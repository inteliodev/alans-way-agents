"""setup-workspace.sh CLI: profile selection, safe YAML quoting, and config editing."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "setup-workspace.sh"
SH = shutil.which("sh")


def run(*args, env=None, check=True):
    cmd = [SH or "/bin/sh", str(SCRIPT)] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if check and result.returncode != 0:
        raise AssertionError(f"exit {result.returncode}: {result.stderr}\n{result.stdout}")
    return result


class PrintModeTests(unittest.TestCase):
    def test_prints_quoted_block_for_manual_paste(self):
        result = run("--bot-id", "bot_123", "--bot-name", 'Scout "The Bot"',
                     "--mac-ssh", "user@mac.local",
                     "--router", "/opt/alans way/router.cjs")
        out = result.stdout
        self.assertIn("workspace_browser:", out)
        # JSON-style double quotes contain spaces and embedded quotes safely.
        self.assertIn('- "/opt/alans way/router.cjs"', out)
        self.assertIn('- "bot_123"', out)
        self.assertIn('- "Scout \\"The Bot\\""', out)
        self.assertIn('HERMES_WORKSPACE_MAC_SSH: "user@mac.local"', out)

    def test_tool_timeout_outlasts_the_connector_action_budget(self):
        out = run("--bot-id", "bot_123").stdout
        # browser-mcp allows 90s for an action; Hermes must not cut it off first.
        self.assertIn("    timeout: 120\n", out)
        self.assertIn("    lazy: true\n", out)

    def test_omits_bot_name_args_when_not_given(self):
        result = run("--bot-id", "bot_123")
        self.assertNotIn("--bot-name", result.stdout)
        self.assertNotIn("HERMES_WORKSPACE_MAC_MCP", result.stdout)
        self.assertNotIn("HERMES_WORKSPACE_MAC_NODE", result.stdout)

    def test_writes_mac_mcp_and_node_when_given(self):
        result = run("--bot-id", "bot_123",
                     "--mac-mcp-path", "/Users/you/code/alans-way-intelio/desktop/scripts/browser-mcp.cjs",
                     "--mac-node-path", "/opt/homebrew/bin/node")
        out = result.stdout
        self.assertIn(
            'HERMES_WORKSPACE_MAC_MCP: "/Users/you/code/alans-way-intelio/desktop/scripts/browser-mcp.cjs"',
            out)
        self.assertIn('HERMES_WORKSPACE_MAC_NODE: "/opt/homebrew/bin/node"', out)


class ConfigEditTests(unittest.TestCase):
    def test_creates_mcp_servers_block_when_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.yaml"
            config.write_text("model:\n  default: gpt-4\n", encoding="utf-8")
            run("--bot-id", "bot123", "--config", str(config))
            text = config.read_text(encoding="utf-8")
            self.assertIn("mcp_servers:\n", text)
            self.assertIn("workspace_browser:", text)
            self.assertTrue((config.parent / "config.yaml.bak-alans-way").exists())

    def test_inserts_under_existing_top_level_mcp_servers(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.yaml"
            config.write_text(
                "model:\n  default: gpt-4\nmcp_servers:\n  other:\n    command: echo\n",
                encoding="utf-8")
            run("--bot-id", "bot123", "--config", str(config))
            text = config.read_text(encoding="utf-8")
            self.assertEqual(text.count("mcp_servers:"), 1)
            self.assertIn("workspace_browser:", text)
            self.assertIn("  other:\n    command: echo", text)

    def test_replaces_managed_block_in_place(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.yaml"
            config.write_text(
                "mcp_servers:\n"
                "# >>> alans-way workspace_browser managed block >>>\n"
                "  workspace_browser:\n"
                "    command: node\n"
                "# <<< alans-way workspace_browser managed block <<<\n"
                "  other:\n    command: echo\n",
                encoding="utf-8")
            run("--bot-id", "newbot", "--config", str(config))
            text = config.read_text(encoding="utf-8")
            self.assertEqual(text.count("workspace_browser:"), 1)
            self.assertIn('- "newbot"', text)
            self.assertIn("  other:\n    command: echo", text)

    def test_special_chars_are_quoted_safely(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.yaml"
            config.write_text("model:\n  default: gpt-4\n", encoding="utf-8")
            run("--bot-id", "bot123", "--bot-name", 'Scout: "Helper"',
                "--mac-ssh", "user@mac #comment",
                "--router", "/path/with\\backslash/router.cjs",
                "--config", str(config))
            text = config.read_text(encoding="utf-8")
            # Values are double-quoted so colons, hashes and backslashes are safe.
            self.assertIn('- "/path/with\\\\backslash/router.cjs"', text)
            self.assertIn('- "bot123"', text)
            self.assertIn('- "Scout: \\"Helper\\""', text)
            self.assertIn('HERMES_WORKSPACE_MAC_SSH: "user@mac #comment"', text)

    def test_ignores_commented_or_indented_mcp_servers(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.yaml"
            config.write_text(
                "# mcp_servers:\n"
                "profiles:\n"
                "  demo:\n"
                "    mcp_servers:\n"
                "      other:\n"
                "        command: echo\n",
                encoding="utf-8")
            run("--bot-id", "bot123", "--config", str(config))
            text = config.read_text(encoding="utf-8")
            # Commented/indented mcp_servers are left alone; a new top-level key is created.
            self.assertEqual(text.count("mcp_servers:"), 3)
            self.assertIn("\nmcp_servers:\n", text)


class ProfileOptionTests(unittest.TestCase):
    def test_profile_resolves_to_hermes_home_profile_config(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "hermes_home"
            profile_dir = home / "profiles" / "alan-local"
            profile_dir.mkdir(parents=True)
            config = profile_dir / "config.yaml"
            config.write_text("model:\n  default: gpt-4\n", encoding="utf-8")
            env = dict(os.environ)
            env["HERMES_HOME"] = str(home)
            run("--bot-id", "bot123", "--profile", "alan-local", env=env)
            text = config.read_text(encoding="utf-8")
            self.assertIn("workspace_browser:", text)
            self.assertIn("alan-local", str(config))

    def test_profile_and_config_are_mutually_exclusive(self):
        result = run("--bot-id", "bot123", "--profile", "p", "--config", "/x.yaml", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("mutually exclusive", result.stderr)

    def test_profile_rejects_bad_characters(self):
        result = run("--bot-id", "bot123", "--profile", "bad/profile", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("bad --profile", result.stderr)


class VerifyTests(unittest.TestCase):
    def test_verify_reports_managed_block_for_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "hermes_home"
            profile_dir = home / "profiles" / "alan-local"
            profile_dir.mkdir(parents=True)
            config = profile_dir / "config.yaml"
            config.write_text(
                "mcp_servers:\n"
                "# >>> alans-way workspace_browser managed block >>>\n"
                "  workspace_browser:\n"
                "    command: node\n"
                "# <<< alans-way workspace_browser managed block <<<\n",
                encoding="utf-8")
            env = dict(os.environ)
            env["HERMES_HOME"] = str(home)
            result = run("--verify", "--profile", "alan-local", env=env)
            self.assertIn("managed workspace_browser block present", result.stdout)
            self.assertIn("all checks passed", result.stdout)

    def test_verify_warns_instead_of_crashing_when_the_mac_app_is_closed(self):
        if not shutil.which("node"):
            self.skipTest("node is required for the router probe")
        with tempfile.TemporaryDirectory() as directory:
            bin_dir = Path(directory) / "bin"
            bin_dir.mkdir()
            ssh = bin_dir / "ssh"
            ssh.write_text("#!/bin/sh\nexit 0\n")
            ssh.chmod(0o755)
            env = dict(os.environ, HOME=directory, PATH=str(bin_dir) + os.pathsep + os.environ["PATH"])
            result = run("--verify", "--mac-ssh", "me@mac", env=env)
            self.assertIn("warn router probe → vps (mac unreachable)", result.stdout)
            self.assertIn("all checks passed", result.stdout)

    def test_verify_fails_a_tool_timeout_shorter_than_the_action_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.yaml"
            config.write_text(
                "mcp_servers:\n"
                "  workspace_browser:\n"
                "    command: node\n"
                "    lazy: true\n"
                "    timeout: 30\n"
                "  other:\n"
                "    timeout: 5\n",
                encoding="utf-8")
            result = run("--verify", "--config", str(config), check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("workspace_browser timeout 30s is below 120s", result.stdout)
            config.write_text(config.read_text().replace("timeout: 30", "timeout: 120"), encoding="utf-8")
            result = run("--verify", "--config", str(config))
            self.assertIn("workspace_browser timeout 120s", result.stdout)


if __name__ == "__main__":
    unittest.main()
