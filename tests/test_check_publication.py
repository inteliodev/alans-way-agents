"""Publication policy regression tests use synthetic text only."""
import importlib.util
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_publication.py"


class PublicationTests(unittest.TestCase):
    def scan(self, path, text):
        spec = importlib.util.spec_from_file_location("publication_check", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.inspect_text(path, text)

    def test_documentation_examples_are_allowed(self):
        self.assertEqual(
            self.scan("README.md", "macuser@mac-private-host /opt/alans-way-agents/approved-workspace"),
            [],
        )
        self.assertEqual(self.scan("tests/test_example.py", "example_file = 'password.txt'"), [])

    def test_real_home_paths_are_denied(self):
        text = "/" + "Users" + "/someone/private-project"
        self.assertTrue(self.scan("README.md", text))

    def test_private_host_addresses_are_denied(self):
        address = ".".join(["100", "72", "1", "23"])
        self.assertTrue(self.scan("README.md", address))
        self.assertEqual(self.scan("tests/test_local.py", "127.0.0.1"), [])

    def test_unspecified_addresses_are_allowed(self):
        address = ".".join(["0", "0", "0", "0"])
        self.assertEqual(self.scan("docs/intelio-vps.md", "listens on %s:631" % address), [])

    def test_the_vps_runbook_may_record_its_own_tailnet_addresses(self):
        address = ".".join(["100", "102", "67", "114"])
        self.assertEqual(self.scan("docs/intelio-vps.md", "http://%s:1234" % address), [])
        self.assertTrue(self.scan("README.md", address))

    def test_credentials_are_denied_without_printing_the_value(self):
        key = "ghp_" + "a" * 36
        findings = self.scan("config.txt", key)
        self.assertTrue(findings)
        self.assertNotIn(key, str(findings))
        self.assertTrue(self.scan("settings.txt", "-----BEGIN " + "OPENSSH PRIVATE KEY-----"))
        token = "123456789:" + "A" * 35
        self.assertTrue(self.scan("bot.env", token))

    def test_runtime_artifacts_and_patches_are_denied(self):
        for name in ["profile.db", ".env", "state.sqlite3", "credentials.json", "fix.patch", "trace.log"]:
            with self.subTest(name=name):
                self.assertTrue(self.scan(name, "synthetic"))


if __name__ == "__main__":
    unittest.main()
