"""setup.sh "Your computers (intelio node)": relay MCP entry + token per profile.

A stub `hermes` on PATH stands in for the real CLI (the CI hermes-contract job
covers real Hermes). It stores `config set` writes per profile home, expands
${VAR} from that profile's .env on `config get` and masks the way Hermes does.
"""
from pathlib import Path
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SETUP = ROOT / "setup.sh"
COMPUTERS = ROOT / "scripts" / "intelio-computers.sh"
FAKE_RELAY = ROOT / "scripts" / "fake_nodes_mcp.py"
SH = shutil.which("sh") or "/bin/sh"
TOKEN = "ab" * 32
KEY = "mcp_servers.intelio_computers"

HERMES_STUB = r'''#!/usr/bin/env python3
import json, os, re, sys
args = sys.argv[1:]
with open(os.environ["HERMES_STUB_LOG"], "a") as log:
    log.write(" ".join(args) + "\n")
home = os.environ.get("HERMES_HOME") or os.path.join(os.environ["HOME"], ".hermes")
if args[:1] == ["-p"]:
    home = os.path.join(home, "profiles", args[1])
    args = args[2:]
store = os.path.join(home, "config.yaml")
def load():
    try:
        return json.load(open(store))
    except (OSError, ValueError):
        return {}
def env_value(name):
    try:
        for line in open(os.path.join(home, ".env")):
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return None
if args[:2] == ["config", "set"]:
    data = load()
    value = args[3]
    data[args[2]] = int(value) if value.isdigit() else value
    with open(store, "w") as fh:
        json.dump(data, fh, sort_keys=True, indent=1)
    print("Set %s" % args[2])
elif args[:2] == ["config", "get"]:
    value = load().get(args[2])
    if isinstance(value, str):
        value = re.sub(r"\$\{([^}]+)\}", lambda m: env_value(m.group(1)) or m.group(0), value)
        if "Authorization" in args[2]:
            value = value[:4] + "..." + value[-4:]
    print("null" if value is None else value)
elif args[:2] == ["plugins", "list"]:
    print("")
'''


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Env:
    def __init__(self, directory, profiles=("intelio", "prc", "alignment", "hhp"), token=True):
        self.root = Path(directory)
        self.home = self.root / "home"
        self.hermes_home = self.home / ".hermes"
        for name in profiles:
            (self.hermes_home / "profiles" / name).mkdir(parents=True)
        self.token_file = self.home / ".config" / "intelio" / "nodes-mcp.token"
        if token:
            self.token_file.parent.mkdir(parents=True)
            self.token_file.write_text(TOKEN + "\n")
            self.token_file.chmod(0o600)
        bindir = self.root / "stub-bin"
        bindir.mkdir()
        for name, body in (("hermes", HERMES_STUB),
                           ("systemctl", "#!/bin/sh\nexit 3\n"),
                           ("pgrep", "#!/bin/sh\nexit 1\n")):
            path = bindir / name
            path.write_text(body)
            path.chmod(0o755)
        self.log = self.root / "calls.log"
        self.env = dict(os.environ)
        for name in ("HERMES_HOME", "INTELIO_NODES_MCP_TOKEN_FILE", "INTELIO_NODES_MCP_URL",
                     "INTELIO_NODES_MCP_TOKEN"):
            self.env.pop(name, None)
        self.env.update({
            "HOME": str(self.home),
            "PATH": os.pathsep.join([str(bindir), self.env.get("PATH", "/usr/bin:/bin")]),
            "HERMES_STUB_LOG": str(self.log),
            # Nothing listens here unless a test starts the fake relay.
            "INTELIO_NODES_MCP_URL": "http://127.0.0.1:%d/mcp" % free_port(),
        })

    def profile(self, name):
        return self.hermes_home if name == "default" else self.hermes_home / "profiles" / name

    def run(self, *args, script=COMPUTERS):
        return subprocess.run([SH, str(script), *args], capture_output=True, text=True, env=self.env)

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []


@unittest.skipIf(os.name == "nt", "POSIX shell scripts")
class ComputersApplyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def env(self, **kwargs):
        return Env(self._tmp.name, **kwargs)

    def test_default_profile_list_is_intelio_only(self):
        env = self.env()
        result = env.run("apply", str(env.hermes_home))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("changed=1", result.stdout)
        intelio = env.profile("intelio")
        self.assertIn("INTELIO_NODES_MCP_TOKEN=" + TOKEN + "\n", (intelio / ".env").read_text())
        self.assertEqual(stat.S_IMODE((intelio / ".env").stat().st_mode), 0o600)
        for client in ("prc", "alignment", "hhp"):
            self.assertFalse((env.profile(client) / ".env").exists(), client)
            self.assertFalse((env.profile(client) / "config.yaml").exists(), client)
        self.assertFalse((env.hermes_home / ".env").exists())
        sets = [c for c in env.calls() if " config set " in " " + c]
        self.assertEqual(sorted(sets), sorted([
            "-p intelio config set %s.url %s" % (KEY, env.env["INTELIO_NODES_MCP_URL"]),
            "-p intelio config set %s.headers.Authorization Bearer ${INTELIO_NODES_MCP_TOKEN}" % KEY,
            "-p intelio config set %s.timeout 300" % KEY,
            "-p intelio config set %s.connect_timeout 30" % KEY,
            "-p intelio config set %s.elicitation.timeout 120" % KEY]))

    def test_contract_url_is_the_default(self):
        env = self.env()
        env.env.pop("INTELIO_NODES_MCP_URL")
        result = env.run("apply", str(env.hermes_home))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("-p intelio config set %s.url http://127.0.0.1:8645/mcp" % KEY, env.calls())

    def test_second_run_is_idempotent(self):
        env = self.env()
        first = env.run("apply", str(env.hermes_home), "intelio,default")
        self.assertIn("changed=1", first.stdout)
        intelio = env.profile("intelio")
        snapshot = {p: p.read_bytes() for p in (intelio / ".env", intelio / "config.yaml",
                                                 env.hermes_home / ".env", env.hermes_home / "config.yaml")}
        second = env.run("apply", str(env.hermes_home), "intelio,default")
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertIn("changed=0", second.stdout)
        self.assertIn("unchanged", second.stdout)
        for path, data in snapshot.items():
            self.assertEqual(path.read_bytes(), data, path)
        self.assertIn("config set %s.timeout 300" % KEY, env.calls())  # bare default home: no -p

    def test_existing_env_lines_are_kept_and_a_stale_token_replaced(self):
        env = self.env()
        dotenv = env.profile("intelio") / ".env"
        dotenv.write_text("TELEGRAM_BOT_TOKEN=keep-me\nexport INTELIO_NODES_MCP_TOKEN=old\n"
                          "INTELIO_NODES_MCP_TOKEN=older\nOTHER=1")
        dotenv.chmod(0o644)
        result = env.run("apply", str(env.hermes_home))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(dotenv.read_text(),
                         "TELEGRAM_BOT_TOKEN=keep-me\nINTELIO_NODES_MCP_TOKEN=%s\nOTHER=1" % TOKEN)
        self.assertEqual(stat.S_IMODE(dotenv.stat().st_mode), 0o600)

    def test_listed_client_profiles_are_wired(self):
        env = self.env()
        result = env.run("apply", str(env.hermes_home), "intelio, prc")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for name in ("intelio", "prc"):
            self.assertIn(TOKEN, (env.profile(name) / ".env").read_text())
        for name in ("alignment", "hhp"):
            self.assertFalse((env.profile(name) / ".env").exists())

    def test_missing_profile_home_is_skipped_not_created(self):
        env = self.env(profiles=("intelio",))
        result = env.run("apply", str(env.hermes_home), "intelio,ghost")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("profile ghost has no home", result.stdout)
        self.assertFalse(env.profile("ghost").exists())

    def test_bad_profile_name_fails(self):
        env = self.env()
        result = env.run("apply", str(env.hermes_home), "../x")
        self.assertEqual(result.returncode, 1)
        self.assertIn("bad profile name", result.stdout)

    def test_no_token_file_is_one_note_and_no_writes(self):
        env = self.env(token=False)
        result = env.run("apply", str(env.hermes_home))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        notes = [line for line in result.stdout.splitlines() if "no relay token" in line]
        self.assertEqual(len(notes), 1, result.stdout)
        self.assertIn("changed=0", result.stdout)
        self.assertEqual(env.calls(), [])
        self.assertFalse((env.profile("intelio") / ".env").exists())

    def test_token_never_reaches_output_or_argv(self):
        env = self.env()
        outputs = []
        for args in (("apply", str(env.hermes_home), "intelio,default"),
                     ("verify", str(env.hermes_home), "intelio,default")):
            result = env.run(*args)
            outputs.append(result.stdout + result.stderr)
        for text in outputs + ["\n".join(env.calls())]:
            self.assertNotIn(TOKEN, text)
            self.assertNotIn(TOKEN[:16], text)


@unittest.skipIf(os.name == "nt", "POSIX shell scripts")
class ComputersVerifyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_verify_reports_the_entry_and_tolerates_an_absent_relay(self):
        env = Env(self._tmp.name)
        env.run("apply", str(env.hermes_home))
        result = env.run("verify", str(env.hermes_home))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Authorization resolves from this profile's .env", result.stdout)
        self.assertIn("not reachable", result.stdout)

    def test_verify_fails_on_a_missing_entry(self):
        env = Env(self._tmp.name)
        result = env.run("verify", str(env.hermes_home))
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("missing from", result.stdout)
        self.assertIn("incomplete", result.stdout)

    def test_verify_sees_the_relay_refuse_an_unauthenticated_post(self):
        env = Env(self._tmp.name)
        port = free_port()
        env.env["INTELIO_NODES_MCP_URL"] = "http://127.0.0.1:%d/mcp" % port
        relay = subprocess.Popen(
            [sys.executable, str(FAKE_RELAY), "--token-file", str(env.token_file), "--port", str(port)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(relay.wait)
        self.addCleanup(relay.terminate)
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        env.run("apply", str(env.hermes_home))
        result = env.run("verify", str(env.hermes_home))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("refuses an unauthenticated POST (401)", result.stdout)


@unittest.skipIf(os.name == "nt", "POSIX shell scripts")
class SetupComputersTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.env = Env(self._tmp.name)

    def setup_run(self, *args):
        return self.env.run("--hermes-home", str(self.env.hermes_home), "--non-interactive",
                            "--skip-browser", "--skip-services", *args, script=SETUP)

    def test_dry_run_prints_the_plan(self):
        result = self.setup_run("--dry-run")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("computers: mcp_servers.intelio_computers -> http://127.0.0.1:8645/mcp for profiles: intelio",
                      result.stdout)
        self.assertFalse((self.env.profile("intelio") / ".env").exists())

    def test_dry_run_without_token_says_skip(self):
        self.env.token_file.unlink()
        result = self.setup_run("--dry-run", "--computers-profiles", "intelio,prc")
        self.assertIn("no relay token", result.stdout)

    def test_bad_computers_profile_is_refused(self):
        result = self.setup_run("--dry-run", "--computers-profiles", "intelio,bad/name")
        self.assertEqual(result.returncode, 2)
        self.assertIn("bad --computers-profiles", result.stderr)

    def test_full_run_wires_intelio_and_restarts_once(self):
        result = self.setup_run()
        out = result.stdout + result.stderr
        self.assertIn("== Your computers (intelio node)", out)
        self.assertIn("profile intelio: intelio_computers ->", out)
        self.assertIn(TOKEN, (self.env.profile("intelio") / ".env").read_text())
        self.assertFalse((self.env.profile("prc") / ".env").exists())
        self.assertEqual(self.env.calls().count("gateway restart"), 1, self.env.calls())
        self.assertNotIn(TOKEN, out)
        # Second run: nothing changed, so no restart is forced.
        before = len(self.env.calls())
        again = self.setup_run()
        self.assertEqual(self.env.calls()[before:].count("gateway restart"), 0, again.stdout)

    def test_no_computers_touches_nothing(self):
        result = self.setup_run("--no-computers")
        self.assertIn("skipped (--no-computers)", result.stdout)
        self.assertFalse((self.env.profile("intelio") / ".env").exists())
        self.assertFalse(any(KEY in call for call in self.env.calls()))
        self.assertEqual(self.env.calls().count("gateway restart"), 0)


if __name__ == "__main__":
    unittest.main()
