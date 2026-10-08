"""push-approval Hermes plugin: asks through Hermes' elicitation prompt for pushes only, fails closed."""
from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
PLUGIN_DIR = REPO / "push-approval"


def load_plugin():
    spec = importlib.util.spec_from_file_location("push_approval_under_test", PLUGIN_DIR / "__init__.py",
                                                  submodule_search_locations=[str(PLUGIN_DIR)])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeApproval:
    """Stands in for tools.approval_prompt (Hermes) and records what Hayden was asked."""

    def __init__(self, answer="accept", raises=None):
        self.answer, self.raises, self.calls = answer, raises, []

    def request_elicitation_consent(self, message, description, *, timeout_seconds=None, surface="", title=""):
        self.calls.append({"message": message, "description": description, "surface": surface, "title": title})
        if self.raises:
            raise self.raises
        return self.answer


class PluginTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="push-approval-")
        self.env = mock.patch.dict(os.environ, {"HOME": self.tmp, "XDG_STATE_HOME": f"{self.tmp}/state",
                                                "XDG_CONFIG_HOME": f"{self.tmp}/config",
                                                "HERMES_HOME": f"{self.tmp}/.hermes/profiles/prc"})
        self.env.start()
        self.plugin = load_plugin()
        self.fake = FakeApproval()
        tools_pkg = types.ModuleType("tools")
        tools_pkg.__path__ = []
        prompt = types.ModuleType("tools.approval_prompt")
        prompt.request_elicitation_consent = lambda *a, **k: self.fake.request_elicitation_consent(*a, **k)
        self.modules = mock.patch.dict(sys.modules, {"tools": tools_pkg, "tools.approval_prompt": prompt})
        self.modules.start()

    def tearDown(self):
        self.modules.stop()
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def call(self, tool, **args):
        return self.plugin._on_pre_tool_call(tool_name=tool, args=args, session_id="s1")

    def test_non_push_runs_without_asking(self):
        for tool, args in (("terminal", {"command": "ls -la && git status"}),
                           ("terminal", {"command": "git commit -m 'push later'"}),
                           ("write_file", {"path": "/tmp/x.txt", "content": "git push"}),
                           ("read_file", {"path": "~/.ssh/config"}),
                           ("execute_code", {"code": "print(1)"}),
                           ("browser_navigate", {"url": "https://example.com"})):
            with self.subTest(tool=tool):
                self.assertIsNone(self.call(tool, **args))
        self.assertEqual(self.fake.calls, [])

    def test_push_allowed_injects_one_time_grant(self):
        out = self.call("terminal", command="git push origin main", workdir="~/work/app")
        self.assertEqual(out["action"], "modify")
        command = out["args"]["command"]
        self.assertRegex(command, r"^export INTELIO_PUSH_GRANT=[0-9a-f]{32}; git push origin main$")
        gid = command.split("=", 1)[1].split(";", 1)[0]
        grant = Path(self.tmp, "state", "intelio", "push-grants", f"{gid}.json")
        self.assertTrue(grant.is_file())
        asked = self.fake.calls[0]
        self.assertEqual(asked["surface"], "push-approval")
        self.assertIn("prc wants to push", asked["message"])
        self.assertIn("git push origin main", asked["message"])
        self.assertIn("~/work/app", asked["message"])

    def test_two_pushes_one_prompt_two_uses(self):
        out = self.call("terminal", command="git push origin a && git push origin b")
        gid = out["args"]["command"].split("=", 1)[1].split(";", 1)[0]
        import json
        record = json.loads(Path(self.tmp, "state", "intelio", "push-grants", f"{gid}.json").read_text())
        self.assertEqual(record["uses"], 2)
        self.assertEqual(len(self.fake.calls), 1)

    def test_denied_blocks(self):
        self.fake.answer = "decline"
        out = self.call("terminal", command="gh pr merge 29 --squash")
        self.assertEqual(out["action"], "block")
        self.assertIn("did not allow", out["message"])
        self.assertIn("Do NOT retry", out["message"])
        self.assertEqual(list(Path(self.tmp).rglob("*.json")), [])

    def test_timeout_blocks(self):
        self.fake.answer = "cancel"
        out = self.call("terminal", command="git push")
        self.assertEqual(out["action"], "block")
        self.assertIn("nobody answered", out["message"])

    def test_prompt_failure_fails_closed(self):
        self.fake.raises = RuntimeError("no surface")
        out = self.call("terminal", command="git push")
        self.assertEqual(out["action"], "block")
        self.assertIn("could not be shown", out["message"])

    def test_hermes_missing_fails_closed(self):
        self.modules.stop()
        with mock.patch.dict(sys.modules, {"tools": None, "tools.approval_prompt": None}):
            out = self.call("terminal", command="git push")
        self.modules.start()
        self.assertEqual(out["action"], "block")

    def test_guard_tamper_asks(self):
        self.fake.answer = "decline"
        out = self.call("terminal", command="git config --global --unset core.hooksPath")
        self.assertEqual(out["action"], "block")
        self.assertIn("change the push guard", self.fake.calls[0]["message"])

    def test_execute_code_gets_grant_in_environ(self):
        code = "from __future__ import annotations\nimport subprocess\nsubprocess.run(['git', 'push'])\n"
        out = self.call("execute_code", code=code)
        new = out["args"]["code"]
        self.assertTrue(new.startswith("from __future__ import annotations\nimport os as _ipg_os;"))
        self.assertIn("INTELIO_PUSH_GRANT", new)
        compile(new, "<test>", "exec")

    def test_process_input_push_refused(self):
        out = self.call("process", action="submit", session_id="p1", data="git push origin main")
        self.assertEqual(out["action"], "block")
        self.assertIn("terminal tool", out["message"])
        self.assertEqual(self.fake.calls, [])

    def test_connector_mcp_tools_left_to_the_relay(self):
        self.assertIsNone(self.call("mcp_intelio_computers_run_command", computer="laptop", command="git push"))
        self.assertIsNone(self.call("mcp__intelio_computers__run_command", computer="laptop", command="git push"))
        self.assertEqual(self.fake.calls, [])

    def test_register_sets_agent_marker_and_shim_path(self):
        shim = Path(self.tmp, "config", "intelio", "push-guard", "bin")
        shim.mkdir(parents=True)
        hooks = []

        class Ctx:
            def register_hook(self, name, fn):
                hooks.append((name, fn))

        with mock.patch.dict(os.environ, {"PATH": "/usr/bin"}):
            os.environ.pop("INTELIO_AGENT", None)
            self.plugin.register(Ctx())
            self.assertEqual(os.environ["INTELIO_AGENT"], "hermes")
            self.assertTrue(os.environ["PATH"].startswith(str(shim)))
        self.assertEqual([name for name, _ in hooks], ["pre_tool_call"])

    def test_manifest(self):
        text = (PLUGIN_DIR / "plugin.yaml").read_text()
        self.assertIn("name: push-approval", text)
        self.assertIn("pre_tool_call", text)


if __name__ == "__main__":
    unittest.main()
