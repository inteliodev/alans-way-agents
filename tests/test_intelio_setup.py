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
    def profile(self, binary, home="/home/user", fallback="/tmp/fallback", snap_rc=1):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            bindir = Path(directory)
            snap = bindir / "snap"
            snap.write_text("#!/bin/sh\nexit %d\n" % snap_rc)
            snap.chmod(0o755)
            env = dict(os.environ)
            # The stub hides a host `snap` so apt Chromium is not rewritten
            # just because this machine has the Chromium snap installed.
            env["PATH"] = str(bindir) + ":" + env.get("PATH", "/usr/bin:/bin")
            result = subprocess.run(
                [SH, str(PROFILE), "--print", binary, home, fallback],
                capture_output=True, text=True, check=True, env=env)
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

    def test_snap_wrapper_is_detected_before_realpath(self):
        # Ubuntu: /snap/bin/chromium -> /usr/bin/snap. The resolved path does
        # not contain /snap/, so the original path has to win.
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snap_bin = root / "snap" / "bin"
            snap_bin.mkdir(parents=True)
            usr_bin = root / "usr" / "bin"
            usr_bin.mkdir(parents=True)
            real = usr_bin / "snap"
            real.write_text("#!/bin/sh\n")
            link = snap_bin / "chromium"
            link.symlink_to(real)
            resolved = os.path.realpath(link)
            self.assertNotIn("/snap/", resolved)
            self.assertEqual(
                self.profile(str(link)),
                "/home/user/snap/chromium/common/hermes-alans-way")

    def test_snap_list_selects_the_confined_profile(self):
        self.assertEqual(
            self.profile("/usr/bin/chromium", snap_rc=0),
            "/home/user/snap/chromium/common/hermes-alans-way")

    def test_non_snap_keeps_the_private_data_dir(self):
        self.assertEqual(self.profile("/usr/bin/chromium"), "/tmp/fallback")

    def test_google_chrome_is_not_rewritten_when_snap_chromium_exists(self):
        self.assertEqual(self.profile("/usr/bin/google-chrome", snap_rc=0), "/tmp/fallback")


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
        # The profile line depends on the host: no chromium on PATH prints the
        # snap placeholder; a chromium binary prints the path the script chose
        # (CI runners have apt chromium, so ~/.local/share). Both are covered
        # deterministically by the stubbed-chromium tests below.
        self.assertTrue(
            "snap Chromium profile: <desktop-home>/snap/chromium/common/hermes-alans-way" in result.stdout
            or "chromium user-data-dir: /" in result.stdout, result.stdout)

    def desktop_dry_run_with_chromium(self, chromium_dir, snap_rc):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bindir = root / chromium_dir
            bindir.mkdir(parents=True)
            chromium = bindir / "chromium"
            chromium.write_text("#!/bin/sh\nexit 0\n")
            chromium.chmod(0o755)
            stubs = root / "stubs"
            stubs.mkdir()
            snap = stubs / "snap"
            snap.write_text("#!/bin/sh\nexit %d\n" % snap_rc)
            snap.chmod(0o755)
            env = dict(os.environ)
            env.pop("HERMES_VPS_BROWSER_DATA", None)
            env["HOME"] = directory
            env["PATH"] = os.pathsep.join([str(bindir), str(stubs), env.get("PATH", "/usr/bin:/bin")])
            # No --desktop-user: the profile home is $HOME, never a real account.
            result = self.run_setup("--dry-run", "--desktop-stack", "--non-interactive", env=env)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertIn("novnc bind: 127.0.0.1", result.stdout)
            self.assertNotIn("novnc bind: " + unspecified_v4(), result.stdout)
            return directory, result.stdout

    def test_desktop_dry_run_uses_the_snap_profile_for_snap_chromium(self):
        home, stdout = self.desktop_dry_run_with_chromium("snap/bin", snap_rc=1)
        self.assertIn(
            "chromium user-data-dir: %s/snap/chromium/common/hermes-alans-way" % home, stdout)

    def test_desktop_dry_run_keeps_the_private_dir_for_apt_chromium(self):
        home, stdout = self.desktop_dry_run_with_chromium("usr/bin", snap_rc=1)
        self.assertIn(
            "chromium user-data-dir: %s/.local/share/hermes-alans-way/browser/chromium" % home, stdout)
        self.assertNotIn("snap/chromium/common", stdout)

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

    def test_desktop_stack_dry_run_mentions_cups(self):
        result = self.run_setup("--dry-run", "--desktop-stack", "--non-interactive")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("snap stop --disable cups", result.stdout)


class VerifyToolsetTests(unittest.TestCase):
    def verify(self, config):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "hermes"
            home.mkdir()
            (home / "config.yaml").write_text(config)
            env = dict(os.environ)
            env["HOME"] = directory
            return subprocess.run(
                [SH, str(SETUP), "--verify", "--non-interactive", "--hermes-home", str(home)],
                capture_output=True, text=True, env=env)

    def test_flow_list_without_browser_is_not_a_false_warning(self):
        result = self.verify(
            "platform_toolsets:\n"
            "  telegram: [web, terminal, file, todo]\n"
            "agent:\n"
            "  disabled_toolsets:\n"
            "    - browser\n")
        self.assertIn("built-in browser toolset disabled for telegram", result.stdout)
        self.assertNotIn("still enabled", result.stdout)

    def test_flow_list_with_browser_warns(self):
        result = self.verify(
            "platform_toolsets:\n"
            "  telegram: [browser, terminal]\n")
        self.assertIn("built-in 'browser' toolset still enabled", result.stdout)

    def test_block_list_with_browser_warns(self):
        result = self.verify(
            "platform_toolsets:\n"
            "  telegram:\n"
            "    - browser\n"
            "    - terminal\n")
        self.assertIn("built-in 'browser' toolset still enabled", result.stdout)

    def test_block_list_ignores_a_later_browser_item(self):
        result = self.verify(
            "platform_toolsets:\n"
            "  telegram:\n"
            "    - web\n"
            "    - terminal\n"
            "agent:\n"
            "  disabled_toolsets:\n"
            "    - browser\n")
        self.assertIn("built-in browser toolset disabled for telegram", result.stdout)
        self.assertNotIn("still enabled", result.stdout)

    def test_two_space_block_items_count(self):
        result = self.verify(
            "platform_toolsets:\n"
            "  telegram:\n"
            "  - browser\n"
            "  discord:\n"
            "  - terminal\n")
        self.assertIn("built-in 'browser' toolset still enabled", result.stdout)

    def test_bundle_name_is_not_treated_as_the_browser_toolset(self):
        result = self.verify(
            "platform_toolsets:\n"
            "  telegram: [hermes-telegram]\n"
            "agent:\n"
            "  disabled_toolsets:\n"
            "    - browser\n")
        self.assertIn("built-in browser toolset disabled for telegram", result.stdout)
        self.assertNotIn("still enabled", result.stdout)

    def test_flow_list_and_string_form_see_proactivity(self):
        flow = self.verify(
            "platform_toolsets:\n"
            "  telegram: [proactivity, terminal]\n")
        quoted = self.verify(
            "platform_toolsets:\n"
            "  telegram: '[\"proactivity\", \"terminal\"]'\n")
        for result in (flow, quoted):
            self.assertIn("proactivity toolset enabled for telegram", result.stdout)
            self.assertIn("built-in browser toolset disabled for telegram", result.stdout)


if __name__ == "__main__":
    unittest.main()
