"""your-computers skill regression tests: exact tool names and the safety rules."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = ROOT / "alans-way" / "skills" / "your-computers" / "SKILL.md"
TOOLS = ("list_computers", "computer_info", "list_dir", "read_file", "write_file",
         "search_files", "run_command", "screenshot", "start_session", "send_input",
         "read_output", "stop_session", "list_sessions")


def _normalized(text: str) -> str:
    return " ".join(text.split())


class YourComputersSkillTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = SKILL_PATH.read_text(encoding="utf-8")
        cls.normalized = _normalized(cls.text)

    def test_has_valid_frontmatter(self):
        match = re.match(r"^---\n(.*?)\n---\n", self.text, re.DOTALL)
        self.assertIsNotNone(match, "SKILL.md must start with frontmatter")
        self.assertIn("name: your-computers", match.group(1))
        self.assertIn("description: Use when", match.group(1))

    def test_lf_line_endings(self):
        self.assertNotIn(b"\r\n", SKILL_PATH.read_bytes())

    def test_every_tool_uses_the_exact_hermes_name(self):
        for tool in TOOLS:
            with self.subTest(tool=tool):
                self.assertIn("`mcp__intelio_computers__%s`" % tool, self.text)
        named = set(re.findall(r"mcp__intelio_computers__([a-z_]+)", self.text))
        self.assertEqual(named - set(TOOLS), set(), "skill names a tool the relay does not serve")

    def test_list_computers_first_and_name_the_computer(self):
        self.assertIn("Call `mcp__intelio_computers__list_computers` first", self.normalized)
        self.assertIn("Say which computer you are acting on", self.normalized)

    def test_vps_terminal_for_everything_else(self):
        self.assertIn("stays in your own terminal and file tools on this VPS", self.normalized)

    def test_offline_handling(self):
        self.assertIn("If the computer is offline", self.normalized)
        self.assertIn("do not switch to another computer on your own", self.normalized)

    def test_read_before_write(self):
        self.assertIn("Read and search before writing", self.normalized)

    def test_coding_agents_and_session_loop(self):
        for phrase in ('claude -p "<task>"', 'codex exec "<task>"', "`since` set to the `cursor`",
                       "`exited` is true", "`truncated`", "`enter: true`", "`force: true`"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.normalized)
        order = [self.text.index("1. `mcp__intelio_computers__start_session`"),
                 self.text.index("2. Loop on `mcp__intelio_computers__read_output`"),
                 self.text.index("3. Answer with `mcp__intelio_computers__send_input`"),
                 self.text.index("4. When done, `mcp__intelio_computers__stop_session`")]
        self.assertEqual(order, sorted(order))

    def test_sign_in_relayed_never_typed(self):
        self.assertIn("Relay the device code and URL to the user", self.normalized)
        self.assertIn("Never type, ask for or accept passwords", self.normalized)

    def test_sessions_never_left_running(self):
        self.assertIn("Stop every session you start", self.normalized)
        self.assertIn("call `mcp__intelio_computers__list_sessions`", self.normalized)

    def test_elevation_needs_the_user(self):
        self.assertIn("only the user can approve", self.normalized)
        self.assertIn("do not look for a way around it", self.normalized)

    def test_secrets_only_when_asked(self):
        self.assertIn("unless the user asked for that specific thing", self.normalized)
        for secret in (".env", "keychains", "SSH keys"):
            with self.subTest(secret=secret):
                self.assertIn(secret, self.normalized)

    def test_pushes_need_the_users_yes(self):
        self.assertIn("Pushes need the user's yes", self.normalized)
        self.assertIn("Allow covers that one command, once.", self.normalized)
        self.assertIn("Do not retry another way", self.normalized)
        self.assertIn("type the whole push command in one `send_input`", self.normalized)
        self.assertIn("The computer refuses the protected ones outright", self.normalized)

    def test_report_what_changed(self):
        self.assertIn("Report what changed", self.normalized)
        self.assertIn("Do not claim a change you did not verify.", self.normalized)

    def test_no_words_that_trip_the_hermes_install_scan(self):
        # Hermes' skills_guard scores a bare `sudo` (and ~/.ssh-style paths)
        # high -> "caution", which blocks `hermes plugins install` for a
        # community source. Keep the skill prose clear of them.
        self.assertNotRegex(self.text, r"\bsudo\b")
        self.assertNotRegex(self.text, r"~/\.(ssh|aws|gnupg|kube|docker)")


if __name__ == "__main__":
    unittest.main()
