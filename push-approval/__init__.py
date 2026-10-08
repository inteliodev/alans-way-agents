"""push-approval: Hermes asks Hayden before any agent push; everything else stays approval-free.

Hayden's rule (Oct 8 2026): agents read and act on his computers and the VPS without prompts;
the only thing that asks first is a push. ``approvals.mode`` stays ``off`` in every profile.

Why a plugin and not ``approvals.mode``: at the pinned Hermes (d9ef91e) ``mode: off`` short-
circuits every pattern prompt *and* ``request_tool_approval`` (tools/approval.py
``_run_approval_gate`` returns approved first thing), so neither can ask under ``off``. What
still asks is ``tools.approval_prompt.request_elicitation_consent``: it does not read
``approvals.mode`` and routes to whichever surface owns the session -- the intelio app's
``approval.request`` SSE event (api_server ``/api/sessions/<id>/chat/stream``, answered on
``POST /v1/runs/<run>/approval``), Telegram's approval buttons (gateway run_turn_runner), the
TUI, or the CLI panel. Cron / ``-q`` / nobody-present sessions get an instant decline.

Flow for a ``terminal`` call whose command pushes:
  1. this ``pre_tool_call`` hook asks Hayden (one prompt per command, never remembered);
  2. allow  -> mint a one-time grant and prefix ``export INTELIO_PUSH_GRANT=<id>;`` to the command
     (``modify``); the git pre-push guard / gh shim consume it;
     deny, timeout, no surface, or any error -> ``block`` (fail closed; the tool never runs).
``execute_code`` gets the grant through ``os.environ``; pushes typed into a background process
(``process`` tool) are refused with a pointer to the terminal tool, because no grant can reach an
already-running process. MCP tools are skipped: the intelio computers relay asks on its own
(MCP elicitation) so a connector push is not asked twice.

A change to the guard itself (``core.hooksPath``, the grants folder, git config env overrides)
asks the same way: switching the guard off is a push decision.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import push_guard  # noqa: E402  (stdlib-only sibling module, also installed as the git hook)

logger = logging.getLogger(__name__)

_SHELL_TOOLS = {"terminal": "command", "shell": "command", "bash": "command"}
_WRITE_TOOLS = {"write_file": ("path", "content"), "patch": ("path", "new_string")}
_TITLE = "Allow this push?"


def _profile() -> str:
    home = os.environ.get("HERMES_HOME", "")
    try:
        from hermes_constants import get_hermes_home
        home = str(get_hermes_home())
    except Exception:
        pass
    name = Path(home).name if home else ""
    return "intelio (default)" if name in ("", ".hermes") else name


def _ask(summary: str, command: str, where: str) -> str:
    """Hermes-native prompt; returns accept | decline | cancel. Raises if Hermes is unavailable."""
    from tools.approval_prompt import request_elicitation_consent
    message = f"{summary}\n\n{command[:600]}" + (f"\n\n{where}" if where else "")
    description = ("intelio push approval: nothing is pushed unless you allow it. "
                   "Allow covers this one command, once.")
    return request_elicitation_consent(message, description, surface="push-approval", title=_TITLE)


def _blocked(text: str) -> Dict[str, str]:
    return {"action": "block", "message": text}


def _denied(answer: str) -> Dict[str, str]:
    if answer == "cancel":
        why = "nobody answered the approval prompt in time"
    else:
        why = "Hayden did not allow it"
    return _blocked(
        f"BLOCKED: push not approved ({why}). Do NOT retry, rephrase, or push another way "
        "(no --no-verify, no gh api, no other remote). Tell Hayden the push is waiting for his OK.")


def _with_grant_shell(command: str, grant: str) -> str:
    return f"export {push_guard.GRANT_ENV}={grant}; {command}"


def _with_grant_python(code: str, grant: str) -> str:
    lines = code.splitlines(keepends=True)
    head = 0
    while head < len(lines) and (lines[head].startswith("from __future__") or lines[head].startswith("#!")
                                 or lines[head].strip().startswith("# -*-")):
        head += 1
    inject = f"import os as _ipg_os; _ipg_os.environ[{push_guard.GRANT_ENV!r}] = {grant!r}; del _ipg_os\n"
    return "".join(lines[:head]) + inject + "".join(lines[head:])


def _approve(kind: str, pushes: list[str], text: str, where: str) -> Optional[str]:
    """Ask; on allow return a fresh grant id, on anything else raise _NotApproved."""
    summary = f"{_profile()} wants to {kind}: " + ", ".join(dict.fromkeys(pushes))
    answer = _ask(summary, text, where)
    push_guard.log_event(decision="asked", answer=answer, kind=kind, pushes=pushes[:10], profile=_profile())
    if answer != "accept":
        raise _NotApproved(answer)
    return push_guard.mint_grant(text, uses=max(1, len(pushes)), meta={"profile": _profile(), "via": "hermes"})


class _NotApproved(Exception):
    def __init__(self, answer: str):
        super().__init__(answer)
        self.answer = answer


def _on_pre_tool_call(tool_name: str = "", args: Any = None, **_: Any) -> Optional[Dict[str, Any]]:
    try:
        return _decide(str(tool_name or ""), args if isinstance(args, dict) else {})
    except _NotApproved as exc:
        return _denied(exc.answer)
    except Exception as exc:  # fail closed: a hook that raises would otherwise let the tool run
        logger.warning("push-approval: refusing %s, approval check failed: %s", tool_name, exc)
        return _blocked("BLOCKED: this looks like a push and the approval prompt could not be shown "
                        f"({type(exc).__name__}). Do NOT retry; tell Hayden.")


def _decide(tool: str, args: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if tool.startswith("mcp_") or tool.startswith("mcp__"):
        return None  # the intelio computers relay asks for connector pushes itself
    if tool in _SHELL_TOOLS:
        command = str(args.get(_SHELL_TOOLS[tool]) or "")
        if not command.strip():
            return None
        pushes = push_guard.find_pushes(command)
        tamper = push_guard.touches_guard(command)
        if not pushes and not tamper:
            return None
        where = f"in {args.get('workdir') or args.get('cwd') or 'the agent workspace'}"
        kind = "push" if pushes else "change the push guard"
        grant = _approve(kind, pushes or ["push guard settings"], command, where)
        return {"action": "modify", "args": {_SHELL_TOOLS[tool]: _with_grant_shell(command, grant)}}
    if tool == "execute_code":
        code = str(args.get("code") or "")
        pushes = push_guard.find_pushes_in_code(code)
        if not pushes and not push_guard.touches_guard(code):
            return None
        grant = _approve("push from Python", pushes or ["push guard settings"], code, "")
        return {"action": "modify", "args": {"code": _with_grant_python(code, grant)}}
    if tool == "process":
        data = " ".join(str(args.get(k) or "") for k in ("data", "input", "text", "keys"))
        if push_guard.find_pushes(data) or push_guard.touches_guard(data):
            return _blocked("BLOCKED: pushes cannot be typed into a running process; run the push with the "
                            "terminal tool so Hayden can approve it.")
        return None
    if tool in _WRITE_TOOLS:
        path_key, content_key = _WRITE_TOOLS[tool]
        target = f"{args.get(path_key) or ''}\n{args.get(content_key) or ''}"
        if push_guard.touches_guard(str(args.get(path_key) or "")) or (
                ".gitconfig" in str(args.get(path_key) or "") and push_guard.touches_guard(target)):
            _approve("change the push guard", ["push guard settings"], str(args.get(path_key)), "")
        return None
    return None


def register(ctx) -> None:
    # Agent marker for the git guard (inherited by every terminal child, Claude Code and Codex
    # included). The cgroup check in push_guard.agent_context covers children that drop it.
    os.environ.setdefault(push_guard.AGENT_ENV, "hermes")
    shim_dir = push_guard.guard_dir() / "bin"
    path = os.environ.get("PATH", "")
    if shim_dir.is_dir() and str(shim_dir) not in path.split(os.pathsep):
        os.environ["PATH"] = f"{shim_dir}{os.pathsep}{path}" if path else str(shim_dir)
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)
