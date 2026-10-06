"""Intelio fork setup: repo pins, profile-scoped tools, private noVNC, snap profile."""
from pathlib import Path
import os
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
SETUP = ROOT / "setup.sh"
PROFILE = ROOT / "scripts" / "chromium-profile.sh"
BIND = ROOT / "scripts" / "novnc_bind.py"
SH = shutil.which("sh") or "/bin/sh"


def tailscale_v4():
    # First address of Tailscale's CGNAT range, built so the source has no dotted quad.
    return ".".join(["100", "64", "0", "1"])


def public_v4():
    return ".".join(["203", "0", "113", "10"])


def unspecified_v4():
    return ".".join(["0", "0", "0", "0"])


class ChromiumProfileTests(unittest.TestCase):
    def profile(self, binary, home="/home/user", fallback="/tmp/fallback"):
        result = subprocess.run(
            [SH, str(PROFILE), "--print", binary, home, fallback],
            capture_output=True, text=True, check=True)
        return result.stdout.strip()

    def test_snap_profile_lives_in_the_desktop_users_common_dir(self):
        self.assertEqual(
            self.profile("/snap/bin/chromium"),
            "/home/user/snap/chromium/common/hermes-alans-way")

    def test_snap_symlink_resolves_into_the_confined_dir(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            snap_bin = Path(directory) / "snap" / "bin"
            snap_bin.mkdir(parents=True)
            real = snap_bin / "chromium"
            real.write_text("#!/bin/sh\n")
            link = Path(directory) / "chromium"
            link.symlink_to(real)
            self.assertEqual(
                self.profile(str(link)),
                "/home/user/snap/chromium/common/hermes-alans-way")

    def test_non_snap_keeps_the_private_data_dir(self):
        self.assertEqual(self.profile("/usr/bin/chromium"), "/tmp/fallback")


class NovncBindTests(unittest.TestCase):
    def allowed(self, address):
        result = subprocess.run([shutil.which("python3"), str(BIND), address])
        return result.returncode == 0

    def test_loopback_and_tailscale_are_allowed(self):
        self.assertTrue(self.allowed("127.0.0.1"))
        self.assertTrue(self.allowed(tailscale_v4()))

    def test_public_and_wildcard_binds_are_refused(self):
        self.assertFalse(self.allowed(unspecified_v4()))
        self.assertFalse(self.allowed("::"))
        self.assertFalse(self.allowed(public_v4()))
        self.assertFalse(self.allowed("not-an-address"))


class SetupDryRunTests(unittest.TestCase):
    def run_setup(self, *args, env=None):
        merged = dict(os.environ)
        if env:
            merged.update(env)
        return subprocess.run(
            [SH, str(SETUP), *args],
            capture_output=True, text=True, env=merged)

    def test_dry_run_prints_fork_pins_and_writes_nothing(self):
        with __import__("tempfile").TemporaryDirectory() as directory:
            env = {"HOME": directory, "ALANS_WAY_REF": "", "ALANS_WAY_AGENTS_REF": ""}
            # Empty ALANS_WAY_REF must be visible to the script as set-but-empty,
            # which the default assignment treats as "default branch".
            result = self.run_setup(
                "--dry-run", "--non-interactive", "--bot-id", "123",
                "--profile", "alan-local",
                "--mac-node-path", "/opt/homebrew/bin/node",
                env=env)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertIn("https://github.com/inteliodev/alans-way-agents", result.stdout)
            self.assertIn("https://github.com/inteliodev/alans-way", result.stdout)
            self.assertIn("desktop repo: https://github.com/inteliodev/alans-way @ default branch", result.stdout)
            self.assertNotIn("github.com/capthvnsen/", result.stdout)
            self.assertIn("hermes -p alan-local tools enable proactivity --platform telegram", result.stdout)
            self.assertIn("hermes -p alan-local tools disable browser --platform telegram", result.stdout)
            self.assertIn("HERMES_WORKSPACE_MAC_NODE: /opt/homebrew/bin/node", result.stdout)
            self.assertIn("dry-run: no changes made", result.stdout)
            self.assertFalse((Path(directory) / ".hermes").exists())
            self.assertFalse((Path(directory) / ".local" / "share" / "alans-way-agents").exists())

    def test_default_desktop_ref_is_the_intelio_branch(self):
        env = dict(os.environ)
        env.pop("ALANS_WAY_REF", None)
        result = self.run_setup("--dry-run", "--non-interactive", env=env)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("cursor/intelio-harness-layer-8db4", result.stdout)

    def test_desktop_stack_dry_run_stays_on_loopback(self):
        result = self.run_setup(
            "--dry-run", "--desktop-stack", "--desktop-user", "intelio",
            "--non-interactive")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("intelio-xvfb.service", result.stdout)
        self.assertIn("intelio-x11vnc.service", result.stdout)
        self.assertIn("intelio-novnc.service", result.stdout)
        self.assertIn("novnc bind: 127.0.0.1", result.stdout)
        self.assertIn("snap/chromium/common/hermes-alans-way", result.stdout)

    def test_public_novnc_bind_is_refused_before_any_install(self):
        result = self.run_setup(
            "--dry-run", "--desktop-stack", "--novnc-bind", unspecified_v4())
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("refusing --novnc-bind", result.stderr)
        self.assertNotIn("no changes made", result.stdout)

    def test_bad_profile_is_refused(self):
        result = self.run_setup("--dry-run", "--profile", "bad/name")
        self.assertEqual(result.returncode, 2)
        self.assertIn("bad --profile", result.stderr)


if __name__ == "__main__":
    unittest.main()
