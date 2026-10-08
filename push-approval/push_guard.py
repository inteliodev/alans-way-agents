#!/usr/bin/env python3
"""intelio push guard: every push an agent makes needs Hayden's approval.

One stdlib-only module, used three ways:

* imported by the ``push-approval`` Hermes plugin (``__init__.py`` next to this file) to spot a
  push in a tool call and, once Hayden approves it, mint a one-time grant;
* run by git as the global ``core.hooksPath`` dispatcher (``push_guard.py hook pre-push ...``):
  in an agent process a push without a valid grant is refused, whatever started it (the Hermes
  terminal tool, Claude Code, Codex, a script);
* run as the ``gh`` / ``git`` shims on the agents' PATH (``push_guard.py gh ...``) for the pushes
  git hooks never see (``gh pr merge``, ``gh repo sync``, ``git push --no-verify``).

Who counts as an agent (``agent_context``): a process with ``INTELIO_AGENT`` set (the plugin
exports it inside Hermes) or ``HERMES_SUPERVISED_CHILD`` (the hermes-gateway unit sets it), or
any process the systemd *user manager* started (hermes-gateway.service, intelio-pwa.service,
``systemd-run --user`` scopes ...). An SSH login lives in its own ``session-N.scope`` and is
never an agent, so Hayden pushing from his own terminal is untouched. There is deliberately no
environment switch that turns the guard off: an agent could set it.

A grant is a 0600 JSON file under ``~/.local/state/intelio/push-grants`` named by 128 random
bits. It covers the pushes inside one approved command (``uses``), expires after 15 minutes,
and is deleted when used up. The id travels in ``INTELIO_PUSH_GRANT``.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import secrets
import shlex
import subprocess
import sys
import time
from pathlib import Path

GRANT_ENV = "INTELIO_PUSH_GRANT"
AGENT_ENV = "INTELIO_AGENT"
GRANT_TTL_S = 15 * 60
GRANT_ID_RE = re.compile(r"^[0-9a-f]{32}$")
# Read in-process only; tests patch the module attribute. Never taken from the environment.
CGROUP_FILE = "/proc/self/cgroup"

HOOK_NAMES = (
    "applypatch-msg", "pre-applypatch", "post-applypatch", "pre-commit", "pre-merge-commit",
    "prepare-commit-msg", "commit-msg", "post-commit", "pre-rebase", "post-checkout", "post-merge",
    "pre-push", "pre-auto-gc", "post-rewrite", "sendemail-validate", "post-index-change",
    "push-to-checkout", "reference-transaction",
)


# --- locations -------------------------------------------------------------------------------------------------------

def home() -> Path:
    return Path(os.path.expanduser("~"))


def guard_dir() -> Path:
    """Installed guard: hooks/, bin/, push_guard.py, previous-hooks-path."""
    base = os.environ.get("XDG_CONFIG_HOME") or str(home() / ".config")
    return Path(base) / "intelio" / "push-guard"


def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(home() / ".local" / "state")
    return Path(base) / "intelio"


def grants_dir() -> Path:
    return state_dir() / "push-grants"


def log_file() -> Path:
    return state_dir() / "push-guard.jsonl"


# --- push detection ----------------------------------------------------------------------------------------------------

_WRAPPERS = {"sudo", "doas", "env", "command", "builtin", "exec", "nohup", "time", "nice", "ionice", "stdbuf",
             "timeout", "xargs", "then", "do", "else", "!", "eval", "chronic", "unbuffer", "caffeinate"}
_SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "fish", "pwsh", "powershell", "powershell.exe", "pwsh.exe", "cmd",
           "cmd.exe"}
_GIT_OPTS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--super-prefix",
                        "--config-env", "--list-cmds", "--attr-source"}
_GH_API_PUSH_PATH = re.compile(
    r"/git/(?:refs|commits|trees|blobs|tags)\b|/merges\b|/merge-upstream\b|/pulls/[^/\s]+/merge\b"
    r"|/contents/|/releases\b", re.I)
_GH_API_WRITE_FLAGS = {"-f", "-F", "--field", "--raw-field", "--input"}
_SEPARATORS = re.compile(r"\|\||&&|[;&|\n\r(){}`]|\$\(")
TAMPER_RE = re.compile(
    r"core\.hookspath|intelio/push-guard|intelio/push-grants|INTELIO_PUSH_GRANT|"
    r"GIT_CONFIG_(?:COUNT|KEY_\d+|VALUE_\d+|PARAMETERS|GLOBAL|NOSYSTEM|SYSTEM)\b", re.I)


def _split_simple_commands(text: str) -> list[str]:
    """Shell text -> simple-command strings, splitting on separators outside single/double quotes.
    Quoted ``$(...)``/backticks are split too (they still run); the inner script of ``sh -c '...'``
    is handled by recursion in :func:`_pushes_in_tokens`."""
    out, buf, quote, i = [], [], None, 0
    while i < len(text):
        ch = text[i]
        if quote == "'":
            buf.append(ch)
            if ch == "'":
                quote = None
            i += 1
            continue
        if ch == "\\" and i + 1 < len(text):
            buf.append(text[i:i + 2])
            i += 2
            continue
        if ch in "'\"":
            quote = None if quote == ch else (ch if quote is None else quote)
            buf.append(ch)
            i += 1
            continue
        m = _SEPARATORS.match(text, i)
        if m and (quote is None or m.group() in ("$(", "`")):
            out.append("".join(buf))
            buf = []
            i = m.end()
            continue
        buf.append(ch)
        i += 1
    out.append("".join(buf))
    return [part.strip() for part in out if part.strip()]


def _strip_prefix(tokens: list[str]) -> list[str]:
    """Drop env assignments and wrappers (sudo, env -i, timeout 30, nice -n 5 ...)."""
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tok):
            i += 1
            continue
        base = os.path.basename(tok).lower()
        if base in _WRAPPERS:
            i += 1
            # wrapper options and their numeric/duration args (timeout 30, nice -n 5, sudo -u x)
            while i < len(tokens) and (tokens[i].startswith("-") or re.match(r"^\d+[smhd]?$", tokens[i])):
                if tokens[i] in ("-u", "-g", "-n", "-s", "-k", "-C", "--user", "--group", "--signal"):
                    i += 1
                i += 1
            continue
        break
    return tokens[i:]


def _git_push(tokens: list[str]) -> str | None:
    i = 1
    tamper = False
    while i < len(tokens) and tokens[i].startswith("-"):
        opt = tokens[i]
        if opt == "-c" and i + 1 < len(tokens) and tokens[i + 1].lower().startswith("core.hookspath"):
            tamper = True
        if "=" not in opt and opt in _GIT_OPTS_WITH_VALUE:
            i += 1
        i += 1
    if i >= len(tokens):
        return None
    sub = tokens[i]
    rest = tokens[i + 1:]
    if sub == "push":
        extra = " (skipping hooks)" if tamper or "--no-verify" in rest else ""
        return f"git push{extra}"
    if sub in ("subtree", "lfs") and rest[:1] == ["push"]:
        return f"git {sub} push"
    return None


def _gh_push(tokens: list[str]) -> str | None:
    args = [t for t in tokens[1:]]
    # skip global flags such as -R owner/repo / --repo
    words = []
    skip = False
    for t in args:
        if skip:
            skip = False
            continue
        if t in ("-R", "--repo", "--hostname"):
            skip = True
            continue
        words.append(t)
    if len(words) < 2 and not (words and words[0] == "api"):
        return None
    group, sub = (words + ["", ""])[:2]
    if group == "pr" and sub == "merge":
        return "gh pr merge"
    if group == "repo" and sub == "sync":
        return "gh repo sync"
    if group == "repo" and sub == "create" and "--push" in words:
        return "gh repo create --push"
    if group == "release" and sub == "create":
        return "gh release create (creates a tag on GitHub)"
    if group == "api":
        method = "GET"
        for idx, t in enumerate(words):
            if t in ("-X", "--method") and idx + 1 < len(words):
                method = words[idx + 1].upper()
            elif t.startswith("--method="):
                method = t.split("=", 1)[1].upper()
            elif t.startswith("-X") and len(t) > 2:
                method = t[2:].upper()
        if method == "GET" and any(t in _GH_API_WRITE_FLAGS or t.startswith(("--field=", "--raw-field=", "--input="))
                                   for t in words):
            method = "POST"
        if method != "GET" and any(_GH_API_PUSH_PATH.search(t) for t in words[1:]):
            return f"gh api {method} (writes to a repository)"
    return None


def _pushes_in_tokens(tokens: list[str], depth: int) -> list[str]:
    tokens = _strip_prefix(tokens)
    if not tokens:
        return []
    prog = os.path.basename(tokens[0]).lower()
    if prog.endswith(".exe"):
        prog = prog[:-4]
    if prog == "git":
        hit = _git_push(tokens)
        return [hit] if hit else []
    if prog == "gh":
        hit = _gh_push(tokens)
        return [hit] if hit else []
    if prog in ("hub",) and len(tokens) > 1 and tokens[1] == "push":
        return ["hub push"]
    # Inner scripts: sh -c '...', ssh host 'git push', pwsh -Command '...'
    if depth < 4 and (prog in _SHELLS or prog in ("ssh", "su", "runuser", "script")):
        found = []
        for tok in tokens[1:]:
            if " " in tok or ";" in tok or "\n" in tok or tok in ("push",):
                found.extend(find_pushes(tok, _depth=depth + 1))
        if prog == "ssh":
            # ssh host git push origin main (unquoted remote command)
            for start in range(1, len(tokens)):
                if os.path.basename(tokens[start]).lower() in ("git", "gh"):
                    found.extend(_pushes_in_tokens(tokens[start:], depth + 1))
                    break
        return found
    return []


def find_pushes(command: str, *, _depth: int = 0) -> list[str]:
    """Descriptions of every push in shell text (``[]`` when there is none). Errs toward asking:
    unparseable text that merely looks like a push counts as one."""
    text = str(command or "")
    if not text.strip():
        return []
    found: list[str] = []
    for part in _split_simple_commands(text):
        try:
            tokens = shlex.split(part, comments=False, posix=True)
        except ValueError:
            if re.search(r"\bgit\b[^;&|]*\bpush\b|\bgh\s+(?:pr\s+merge|repo\s+sync)\b", part):
                found.append("git push (could not parse the command)")
            continue
        found.extend(_pushes_in_tokens(tokens, _depth))
    return found


def find_pushes_in_code(code: str) -> list[str]:
    """execute_code: Python that shells out. Looks for a push in string literals / argv lists."""
    text = str(code or "")
    hits = []
    if re.search(r"""["']git["']\s*,\s*(?:["'][^"']*["']\s*,\s*)*["']push["']""", text):
        hits.append("git push (from Python)")
    for literal in re.findall(r"""(?s)(?:'''(.*?)'''|\"\"\"(.*?)\"\"\"|'([^'\n]*)'|"([^"\n]*)")""", text):
        chunk = next((s for s in literal if s), "")
        if ("git" in chunk and "push" in chunk) or chunk.startswith("gh "):
            hits.extend(find_pushes(chunk))
    if re.search(r"\b(?:push|Repo|Remote)\b.*\.push\(", text) and re.search(r"\b(?:git|dulwich|pygit2)\b", text):
        hits.append("git push (library call from Python)")
    return hits


def touches_guard(text: str) -> bool:
    """A command or write that would switch the guard off (hooksPath, grants, git env overrides)."""
    return bool(TAMPER_RE.search(str(text or "")))


# --- agent context ------------------------------------------------------------------------------------------------------

def _read_cgroup() -> str:
    try:
        return Path(CGROUP_FILE).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def agent_context(env: dict | None = None, cgroup_text: str | None = None) -> str:
    """Why this process counts as an agent, or ``""`` for a person's own terminal."""
    env = os.environ if env is None else env
    if env.get(AGENT_ENV):
        return f"env:{AGENT_ENV}"
    if env.get("HERMES_SUPERVISED_CHILD"):
        return "env:HERMES_SUPERVISED_CHILD"
    text = _read_cgroup() if cgroup_text is None else cgroup_text
    for line in text.splitlines():
        path = line.split(":", 2)[-1].strip()
        if re.search(r"/session-[^/]+\.scope$", path):
            return ""  # an SSH / console login: a person
        m = re.search(r"/user@\d+\.service/(?:.*/)?([^/]+)$", path)
        if m:
            return f"cgroup:{m.group(1)}"
    return ""


# --- grants -------------------------------------------------------------------------------------------------------------

@contextlib.contextmanager
def _locked(directory: Path):
    import fcntl
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with contextlib.suppress(OSError):
        os.chmod(directory, 0o700)
    fd = os.open(directory / ".lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def mint_grant(command: str, *, uses: int = 1, meta: dict | None = None, ttl_s: int = GRANT_TTL_S,
               now: float | None = None) -> str:
    """Write a one-time grant after Hayden approved ``command``; returns its id."""
    now = time.time() if now is None else now
    grant_id = secrets.token_hex(16)
    record = {
        "id": grant_id, "created": now, "expires": now + ttl_s, "uses": max(1, min(int(uses), 20)),
        "command_sha256": hashlib.sha256(str(command).encode("utf-8")).hexdigest(), **(meta or {}),
    }
    directory = grants_dir()
    with _locked(directory):
        path = directory / f"{grant_id}.json"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(record, fh)
        _sweep(directory, now)
    return grant_id


def _sweep(directory: Path, now: float) -> None:
    for old in directory.glob("*.json"):
        with contextlib.suppress(Exception):
            if json.loads(old.read_text(encoding="utf-8")).get("expires", 0) < now:
                old.unlink()


def consume_grant(grant_id: str | None, *, now: float | None = None) -> tuple[bool, str]:
    """Use one push of a grant. ``(True, "")`` or ``(False, why)``."""
    now = time.time() if now is None else now
    grant_id = str(grant_id or "").strip()
    if not grant_id:
        return False, "no approval grant"
    if not GRANT_ID_RE.match(grant_id):
        return False, "malformed approval grant"
    directory = grants_dir()
    with _locked(directory):
        path = directory / f"{grant_id}.json"
        try:
            st = path.lstat()
            if not path.is_file() or path.is_symlink() or (st.st_mode & 0o077) or st.st_uid != os.getuid():
                return False, "approval grant has unsafe permissions"
            record = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return False, "approval grant not found (already used or expired)"
        except (OSError, ValueError):
            return False, "approval grant unreadable"
        if float(record.get("expires", 0)) < now:
            path.unlink(missing_ok=True)
            return False, "approval grant expired"
        uses = int(record.get("uses", 1)) - 1
        if uses <= 0:
            path.unlink(missing_ok=True)
        else:
            record["uses"] = uses
            tmp = path.with_suffix(".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(record, fh)
            os.replace(tmp, path)
    return True, ""


# --- audit log ----------------------------------------------------------------------------------------------------------

def _redact_url(url: str) -> str:
    return re.sub(r"(?<=://)[^/@\s]+@", "***@", str(url or ""))[:300]


def log_event(**fields) -> None:
    """One JSON line per decision. Never the grant id, never credentials in remote URLs."""
    try:
        path = log_file()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fields = {"time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **fields}
        if "url" in fields:
            fields["url"] = _redact_url(fields["url"])
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(fields, sort_keys=True) + "\n")
    except Exception:
        pass  # logging never changes a decision


REFUSAL = (
    "intelio: push refused. Agents may not push without Hayden's approval ({why}).\n"
    "Do not retry, and do not work around this (no --no-verify, no other remote, no API call).\n"
    "Ask for the push instead: run it with the Hermes terminal tool, which asks Hayden in the\n"
    "intelio app or Telegram and lets the push through once he allows it. If you are a coding\n"
    "tool (Claude Code, Codex) started by an agent, stop and report that the push needs approval."
)


def check_push(kind: str, *, env: dict | None = None, **log_fields) -> tuple[bool, str]:
    """Shared decision for the hook and the shims: ``(allowed, message)``."""
    env = os.environ if env is None else env
    who = agent_context(env)
    if not who:
        log_event(decision="allowed", reason="person", kind=kind, **log_fields)
        return True, ""
    ok, why = consume_grant(env.get(GRANT_ENV))
    log_event(decision="allowed" if ok else "refused", reason="grant" if ok else why, agent=who, kind=kind,
              **log_fields)
    return (True, "") if ok else (False, REFUSAL.format(why=why))


# --- git hook dispatcher -------------------------------------------------------------------------------------------------

def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        return ""


def _chain(name: str, argv: list[str], stdin: bytes) -> int:
    """Run the repo's own hook, then the global hooks path that was set before the guard."""
    candidates = []
    common = _git("rev-parse", "--git-common-dir")
    if common:
        candidates.append(Path(common) / "hooks" / name)
    with contextlib.suppress(OSError):
        prev = (guard_dir() / "previous-hooks-path").read_text(encoding="utf-8").strip()
        if prev:
            candidates.append(Path(os.path.expanduser(prev)) / name)
    mine = (guard_dir() / "hooks").resolve()
    for hook in candidates:
        try:
            if hook.parent.resolve() == mine or not (hook.is_file() and os.access(hook, os.X_OK)):
                continue
        except OSError:
            continue
        code = subprocess.run([str(hook), *argv], input=stdin).returncode
        if code != 0:
            return code
    return 0


def run_hook(name: str, argv: list[str]) -> int:
    stdin = b""
    if not sys.stdin.isatty():
        with contextlib.suppress(Exception):
            stdin = sys.stdin.buffer.read()
    if name == "pre-push":
        refs = [line.split()[2] for line in stdin.decode("utf-8", "replace").splitlines() if len(line.split()) == 4]
        ok, message = check_push("git push", remote=argv[0] if argv else "", url=argv[1] if len(argv) > 1 else "",
                                 refs=refs[:20], cwd=os.getcwd())
        if not ok:
            sys.stderr.write(message + "\n")
            return 1
    return _chain(name, argv, stdin)


# --- PATH shims (gh, git) ------------------------------------------------------------------------------------------------

def _real(prog: str) -> str:
    mine = (guard_dir() / "bin").resolve()
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(entry or ".") / prog
        try:
            if candidate.resolve().parent == mine or candidate.resolve() == Path(__file__).resolve():
                continue
        except OSError:
            continue
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return f"/usr/bin/{prog}"


def run_shim(prog: str, argv: list[str]) -> int:
    command = shlex.join([prog, *argv])
    pushes = find_pushes(command)
    if prog == "git":
        # Plain pushes are left to the pre-push hook (it consumes the grant). Only the forms that skip
        # hooks are decided here.
        pushes = [p for p in pushes if "skipping hooks" in p] or (
            ["git push (git config override)"] if pushes and touches_guard(command) else [])
    if pushes:
        ok, message = check_push(pushes[0], cwd=os.getcwd(), program=prog)
        if not ok:
            sys.stderr.write(message + "\n")
            return 1
    real = _real(prog)
    os.execv(real, [real, *argv])
    return 127  # pragma: no cover


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[0] == "hook":
        return run_hook(argv[1], argv[2:])
    if argv and argv[0] in ("gh", "git"):
        return run_shim(argv[0], argv[1:])
    if argv and argv[0] == "detect":  # debugging aid: prints what would be treated as a push
        print(json.dumps(find_pushes(" ".join(argv[1:]))))
        return 0
    if argv and argv[0] == "context":
        print(agent_context() or "person")
        return 0
    sys.stderr.write("usage: push_guard.py hook <name> [args] | gh|git [args] | detect <command> | context\n")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
