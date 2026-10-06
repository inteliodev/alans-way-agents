"""Mac availability state: watcher file parsing, observer signatures, router notices."""
from pathlib import Path
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
ROUTER = ROOT / "alans-way" / "scripts" / "workspace-router.cjs"
WATCH = ROOT / "alans-way" / "scripts" / "mac-watch.sh"
NODE = shutil.which("node")
SH = shutil.which("sh")


def plugin():
    name = "companion_workspace_mac_test"
    path = ROOT / "alans-way"
    spec = importlib.util.spec_from_file_location(name, path / "__init__.py", submodule_search_locations=[str(path)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class MacStateEnvTest(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.get("HERMES_MAC_STATE_FILE")

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("HERMES_MAC_STATE_FILE", None)
        else:
            os.environ["HERMES_MAC_STATE_FILE"] = self._saved


class MacStateReaderTests(MacStateEnvTest):
    def test_missing_malformed_and_wrong_shape_are_tolerated(self):
        module = plugin()
        observe = __import__(module.__name__ + ".proactive_observe", fromlist=["mac_state"])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mac-state.json"
            os.environ["HERMES_MAC_STATE_FILE"] = str(path)
            self.assertIsNone(observe.mac_state())
            path.write_text("not json{", encoding="utf-8")
            self.assertIsNone(observe.mac_state())
            path.write_text(json.dumps({"state": "sideways"}), encoding="utf-8")
            self.assertIsNone(observe.mac_state())
            path.write_text(json.dumps(["offline"]), encoding="utf-8")
            self.assertIsNone(observe.mac_state())
            path.write_text(" " * 5000, encoding="utf-8")
            self.assertIsNone(observe.mac_state())
            path.write_text(json.dumps({"state": "offline", "since": "2026-02-01T10:00:00Z",
                                        "lastSeenOnline": "2026-02-01T09:59:00Z", "extra": [1]}),
                            encoding="utf-8")
            self.assertEqual(observe.mac_state(), {"state": "offline",
                                                   "since": "2026-02-01T10:00:00Z",
                                                   "lastSeenOnline": "2026-02-01T09:59:00Z"})
            real = Path(directory) / "real.json"
            real.write_text(json.dumps({"state": "online"}), encoding="utf-8")
            path.unlink()
            path.symlink_to(real)
            self.assertIsNone(observe.mac_state())

    def test_offline_online_flip_records_one_context_change(self):
        module = plugin()
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            path = home / "mac-state.json"
            os.environ["HERMES_MAC_STATE_FILE"] = str(path)
            runtime = module.Runtime(None, home)
            runtime.store.update_policy({"session_key": "agent:main:telegram:dm:123456789"})
            self.assertIsNone(runtime.review_context()["preferences"]["workspace_mac"])
            self.assertEqual(runtime.observe(), 0)
            path.write_text(json.dumps({"state": "offline", "since": "2026-02-01T10:00:00Z"}),
                            encoding="utf-8")
            self.assertEqual(runtime.observe(), 0)
            self.assertEqual(runtime.review_context()["preferences"]["workspace_mac"]["state"], "offline")
            path.write_text(json.dumps({"state": "online", "since": "2026-02-01T10:30:00Z"}),
                            encoding="utf-8")
            self.assertEqual(runtime.observe(), 1)
            self.assertEqual(runtime.observe(), 0)
            path.write_text(json.dumps({"state": "offline", "since": "2026-02-01T11:00:00Z"}),
                            encoding="utf-8")
            self.assertEqual(runtime.observe(), 1)
            self.assertEqual(runtime.store.status()["counts"], {"pending": 2})
            restarted = module.Runtime(None, home)
            self.assertEqual(restarted.observe(), 0)
            path.write_text(json.dumps({"state": "online", "since": "2026-02-01T12:00:00Z"}),
                            encoding="utf-8")
            self.assertEqual(restarted.observe(), 1)
            runtime.close()
            restarted.close()


TOOL_RESULT = json.dumps({"jsonrpc": "2.0", "id": 1,
                          "result": {"content": [{"type": "text", "text": "tabs listed"}]}})

ROUTER_DRIVER = (
    "const fs = require('fs');"
    "const router = require(process.argv[1]);"
    "const annotate = router.makeAnnotator(process.argv[2], process.argv[3] === '1', process.argv[4]);"
    "for (const op of JSON.parse(fs.readFileSync(0, 'utf8'))) {"
    "  if ('rm' in op) fs.rmSync(process.argv[4], { force: true });"
    "  if ('raw' in op) fs.writeFileSync(process.argv[4], op.raw);"
    "  if ('state' in op) fs.writeFileSync(process.argv[4], JSON.stringify(op.state));"
    "  if ('line' in op) process.stdout.write(annotate(op.line) + '\\n');"
    "}"
)


@unittest.skipUnless(NODE, "node is required for router tests")
class RouterNoticeTests(MacStateEnvTest):
    def annotate(self, directory, ops, host="vps", mac_configured=True):
        proc = subprocess.run(
            [NODE, "-e", ROUTER_DRIVER, str(ROUTER), host,
             "1" if mac_configured else "0", str(Path(directory) / "mac-state.json")],
            input=json.dumps(ops), capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout.splitlines()

    def test_offline_result_carries_state_and_notice(self):
        with tempfile.TemporaryDirectory() as directory:
            [line] = self.annotate(directory, [
                {"state": {"state": "offline", "since": "2026-02-01T10:00:00Z",
                           "lastSeenOnline": "2026-02-01T09:59:00Z"},
                 "line": TOOL_RESULT}])
            msg = json.loads(line)
            self.assertEqual(msg["id"], 1)
            self.assertEqual(msg["result"]["content"][0], {"type": "text", "text": "tabs listed"})
            self.assertEqual(msg["result"]["_meta"]["workspace"],
                             {"host": "vps",
                              "mac": {"state": "offline", "since": "2026-02-01T10:00:00Z",
                                      "lastSeenOnline": "2026-02-01T09:59:00Z"}})
            notice = msg["result"]["content"][1]["text"]
            self.assertIn("[workspace] Mac unreachable since 2026-02-01T10:00:00Z", notice)
            self.assertIn("VPS browser", notice)

    def test_back_online_notice_fires_once_per_flip(self):
        with tempfile.TemporaryDirectory() as directory:
            lines = self.annotate(directory, [
                {"state": {"state": "online", "since": "2026-02-01T10:05:00Z"}, "line": TOOL_RESULT},
                {"line": TOOL_RESULT},
                {"state": {"state": "offline", "since": "2026-02-01T10:20:00Z"}, "line": TOOL_RESULT},
                {"state": {"state": "online", "since": "2026-02-01T10:40:00Z"}, "line": TOOL_RESULT},
            ])
            contents = [json.loads(line)["result"]["content"] for line in lines]
            self.assertIn("Mac is back online as of 2026-02-01T10:05:00Z", contents[0][1]["text"])
            self.assertIn("can resume", contents[0][1]["text"])
            self.assertEqual(len(contents[1]), 1)
            self.assertIn("Mac unreachable since 2026-02-01T10:20:00Z", contents[2][1]["text"])
            self.assertIn("Mac is back online as of 2026-02-01T10:40:00Z", contents[3][1]["text"])

    def test_missing_and_malformed_state_files_report_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            lines = self.annotate(directory, [
                {"line": TOOL_RESULT},
                {"raw": "not json{"},
                {"line": TOOL_RESULT},
                {"raw": json.dumps({"state": "sideways"})},
                {"line": TOOL_RESULT},
            ])
            for line in lines:
                msg = json.loads(line)
                self.assertIsNone(msg["result"]["_meta"]["workspace"]["mac"])
                self.assertEqual(len(msg["result"]["content"]), 1)

    def test_non_result_traffic_passes_through(self):
        with tempfile.TemporaryDirectory() as directory:
            lines = self.annotate(directory, [
                {"state": {"state": "offline", "since": "2026-02-01T10:00:00Z"}},
                {"line": "browser log noise"},
                {"line": json.dumps({"jsonrpc": "2.0", "method": "notifications/progress", "params": {}})},
                {"line": json.dumps({"jsonrpc": "2.0", "id": 2, "error": {"code": -1, "message": "x"}})},
                {"line": json.dumps({"jsonrpc": "2.0", "id": 3, "result": {"value": 42}})},
            ])
            self.assertEqual(lines[0], "browser log noise")
            self.assertEqual(json.loads(lines[1]), {"jsonrpc": "2.0", "method": "notifications/progress", "params": {}})
            self.assertEqual(json.loads(lines[2]), {"jsonrpc": "2.0", "id": 2, "error": {"code": -1, "message": "x"}})
            meta = json.loads(lines[3])["result"]["_meta"]["workspace"]
            self.assertEqual(meta["host"], "vps")
            self.assertEqual(meta["mac"]["state"], "offline")

    def test_mac_host_and_unconfigured_mac_emit_no_notice(self):
        with tempfile.TemporaryDirectory() as directory:
            [line] = self.annotate(directory, [
                {"state": {"state": "offline", "since": "2026-02-01T10:00:00Z"}, "line": TOOL_RESULT}],
                host="mac")
            msg = json.loads(line)
            self.assertEqual(msg["result"]["_meta"]["workspace"]["host"], "mac")
            self.assertEqual(len(msg["result"]["content"]), 1)
            [line] = self.annotate(directory, [
                {"state": {"state": "offline", "since": "2026-02-01T10:00:00Z"}, "line": TOOL_RESULT}],
                mac_configured=False)
            msg = json.loads(line)
            self.assertIsNone(msg["result"]["_meta"]["workspace"]["mac"])
            self.assertEqual(len(msg["result"]["content"]), 1)


@unittest.skipUnless(NODE and SH, "node and sh are required for backend command tests")
class MacBackendCommandTests(unittest.TestCase):
    """Non-interactive ssh never loads Homebrew's PATH, so a bare `node` misses."""

    def run_remote(self, home, node="", with_bundle=True):
        bundle = Path(home) / "Apps" / "Open Alan.app"
        script = bundle / "Contents" / "Resources" / "app" / "scripts" / "browser-mcp.cjs"
        script.parent.mkdir(parents=True)
        script.write_text("")
        if with_bundle:
            exe = bundle / "Contents" / "MacOS" / "Open Alan"
            exe.parent.mkdir(parents=True)
            exe.write_text('#!/bin/sh\necho "app-binary run-as-node=$ELECTRON_RUN_AS_NODE $*"\n')
            exe.chmod(0o755)
        bin_dir = Path(home) / "bin"
        bin_dir.mkdir()
        fake_node = bin_dir / "node"
        fake_node.write_text('#!/bin/sh\necho "path-node $*"\n')
        fake_node.chmod(0o755)
        command = subprocess.run(
            [NODE, "-e", "process.stdout.write(require(process.argv[1]).macBackendCommand("
                         "process.argv[2], process.argv[3], 'bot-1', \"Alan's bot\"))",
             str(ROUTER), str(script), node],
            capture_output=True, text=True, check=True).stdout
        env = {"PATH": str(bin_dir) + os.pathsep + "/usr/bin:/bin"}
        return subprocess.run([SH, "-c", command], env=env, capture_output=True, text=True), script

    def test_default_runs_the_app_bundle_binary_as_node(self):
        with tempfile.TemporaryDirectory() as home:
            result, script = self.run_remote(home)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(),
                             f"app-binary run-as-node=1 {script} --bot-id bot-1 --bot-name Alan's bot")

    def test_falls_back_to_path_node_without_a_bundle_binary(self):
        with tempfile.TemporaryDirectory() as home:
            result, script = self.run_remote(home, with_bundle=False)
            self.assertEqual(result.stdout.strip(), f"path-node {script} --bot-id bot-1 --bot-name Alan's bot")

    def test_an_explicit_mac_node_wins(self):
        with tempfile.TemporaryDirectory() as home:
            result, script = self.run_remote(home, node="node")
            self.assertEqual(result.stdout.strip(), f"path-node {script} --bot-id bot-1 --bot-name Alan's bot")

    def test_intelio_bundle_uses_its_own_binary(self):
        script = "/Applications/Intelio.app/Contents/Resources/app/scripts/browser-mcp.cjs"
        command = subprocess.run(
            [NODE, "-e", "process.stdout.write(require(process.argv[1]).macBackendCommand("
                         "process.argv[2], '', 'bot-1', ''))",
             str(ROUTER), script],
            capture_output=True, text=True, check=True).stdout
        self.assertIn("ELECTRON_RUN_AS_NODE=1", command)
        self.assertIn("/Applications/Intelio.app/Contents/MacOS/Intelio", command)

    def test_source_checkout_tries_homebrew_node_before_path_node(self):
        script = "/Users/you/code/alans-way-intelio/desktop/scripts/browser-mcp.cjs"
        command = subprocess.run(
            [NODE, "-e", "process.stdout.write(require(process.argv[1]).macBackendCommand("
                         "process.argv[2], '', 'bot-1', ''))",
             str(ROUTER), script],
            capture_output=True, text=True, check=True).stdout
        self.assertIn("/opt/homebrew/bin/node", command)
        self.assertIn("exec node", command)
        self.assertNotIn("ELECTRON_RUN_AS_NODE", command)


@unittest.skipUnless(NODE, "node is required for router discovery tests")
class MacScriptDiscoveryTests(unittest.TestCase):
    def router(self):
        return subprocess.run(
            [NODE, "-e",
             "const r=require(process.argv[1]);"
             "const alive='true';"
             "const scripts=r.defaultMacScripts();"
             "process.stdout.write(JSON.stringify({"
             "scripts,"
             "clause:r.macScriptProbeClause(scripts[0], alive),"
             "legacy:r.macScriptProbeClause(scripts[3], alive),"
             "home:r.acceptProbedMacScript("
             "'/Users/you/Applications/Intelio.app/Contents/Resources/app/scripts/browser-mcp.cjs',"
             "scripts),"
             "abs:r.acceptProbedMacScript("
             "'/Applications/Intelio.app/Contents/Resources/app/scripts/browser-mcp.cjs',"
             "scripts),"
             "src:r.acceptProbedMacScript("
             "'/home/user/code/alans-way-intelio/desktop/scripts/browser-mcp.cjs',"
             "scripts),"
             "evil:r.acceptProbedMacScript('/tmp/browser-mcp.cjs', scripts)"
             "}))",
             str(ROUTER)],
            capture_output=True, text=True, check=True)

    def test_default_order_prefers_intelio_then_legacy_bundles(self):
        payload = json.loads(self.router().stdout)
        scripts = payload["scripts"]
        self.assertEqual(scripts[0], "~/Applications/Intelio.app/Contents/Resources/app/scripts/browser-mcp.cjs")
        self.assertEqual(scripts[1], "/Applications/Intelio.app/Contents/Resources/app/scripts/browser-mcp.cjs")
        self.assertEqual(scripts[2], "~/code/alans-way-intelio/desktop/scripts/browser-mcp.cjs")
        self.assertIn("/Applications/alans-way-localapp.app/Contents/Resources/app/scripts/browser-mcp.cjs", scripts)
        self.assertTrue(payload["home"])
        self.assertTrue(payload["abs"])
        self.assertTrue(payload["src"])
        self.assertFalse(payload["evil"])
        self.assertIn('"$HOME/Applications/Intelio.app/', payload["clause"])
        self.assertNotIn("'$HOME/", payload["clause"])
        self.assertIn("'/Applications/alans-way-localapp.app/", payload["legacy"])


@unittest.skipUnless(SH, "sh is required for watcher tests")
class MacWatchScriptTests(MacStateEnvTest):
    def run_once(self, home, online, *args):
        bin_dir = Path(home) / "bin"
        bin_dir.mkdir(exist_ok=True)
        stub = bin_dir / "ssh"
        stub.write_text("#!/bin/sh\nexit %d\n" % (0 if online else 1))
        stub.chmod(0o755)
        env = dict(os.environ)
        env["PATH"] = str(bin_dir) + os.pathsep + env["PATH"]
        env["HERMES_WORKSPACE_MAC_SSH"] = "test@mac"
        env["HERMES_MAC_STATE_FILE"] = str(Path(home) / "state" / "mac-state.json")
        return subprocess.run([SH, str(WATCH), "--once", *args],
                              env=env, capture_output=True, text=True)

    def test_once_writes_state_and_logs_transitions(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state" / "mac-state.json"
            events = Path(directory) / "state" / "mac-events.log"
            result = self.run_once(directory, online=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            first = json.loads(state.read_text(encoding="utf-8"))
            self.assertEqual(first["state"], "online")
            self.assertEqual(first["since"], first["lastTransition"])
            self.assertEqual(first["lastSeenOnline"], first["since"])
            self.assertEqual(events.read_text().count("unknown -> online"), 1)
            result = self.run_once(directory, online=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            second = json.loads(state.read_text(encoding="utf-8"))
            self.assertEqual(second["state"], "offline")
            self.assertEqual(second["lastSeenOnline"], first["lastSeenOnline"])
            self.assertEqual(events.read_text().count("online -> offline"), 1)
            result = self.run_once(directory, online=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(events.read_text().splitlines()), 2)

    def test_once_tolerates_a_corrupt_state_file(self):
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory) / "state"
            state_dir.mkdir()
            (state_dir / "mac-state.json").write_text("garbage{{{", encoding="utf-8")
            result = self.run_once(directory, online=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads((state_dir / "mac-state.json").read_text())["state"], "online")


if __name__ == "__main__":
    unittest.main()
