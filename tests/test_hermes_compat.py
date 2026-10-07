"""Hermes CLI compatibility: live-gateway plugin reinstall and injection permission.

A stub `hermes` on PATH records every call; nothing here runs real Hermes.
"""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMPAT = ROOT / "scripts" / "hermes-compat.sh"
SETUP = ROOT / "setup.sh"
SH = shutil.which("sh") or "/bin/sh"

HERMES_STUB = """#!/bin/sh
printf '%s\\n' "$*" >> "$HERMES_STUB_LOG"
case "$*" in
  "--version") echo "Hermes Agent v0.21.0"; exit 0;;
  "plugins install --help")
    echo "usage: hermes plugins install [--force]"
    [ "${STUB_LIVE_FLAG:-0}" = 1 ] && echo "  --allow-live-gateway  DANGEROUS: allow a forced reinstall"
    exit 0;;
  "plugins list") echo "alans-way  enabled"; exit 0;;
  plugins\\ install*)
    if [ -n "${STUB_INSTALL_FAIL:-}" ]; then
      echo "Error: Cannot reinstall plugin files while the messaging gateway is running"
      echo "stub stderr: refused" >&2
      exit 1
    fi
    exit 0;;
  "proactivity status") echo '{}'; exit 0;;
  # The flag lives only in the default home, as Hermes reads it.
  "config get plugins.entries.alans-way.allow_gateway_injection") echo true; exit 0;;
  -p\ *\ config\ get*) echo false; exit 0;;
esac
exit 0
"""


def stub_bin(directory, live):
    bindir = Path(directory) / "stub-bin"
    bindir.mkdir()
    for name, body in (
            ("hermes", HERMES_STUB),
            # Liveness comes only from these stubs, never the host's units.
            ("systemctl", "#!/bin/sh\nexit 3\n"),
            ("pgrep", "#!/bin/sh\nexit %d\n" % (0 if live else 1))):
        path = bindir / name
        path.write_text(body)
        path.chmod(0o755)
    return bindir


class CompatHelperTests(unittest.TestCase):
    def run_helper(self, *args, live=False, live_flag=False, install_fail=False):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "calls.log"
            env = dict(os.environ)
            env.update({
                "PATH": os.pathsep.join([str(stub_bin(directory, live)), env.get("PATH", "/usr/bin:/bin")]),
                "HERMES_STUB_LOG": str(log),
                "STUB_LIVE_FLAG": "1" if live_flag else "0",
                "STUB_INSTALL_FAIL": "1" if install_fail else "",
            })
            result = subprocess.run([SH, str(COMPAT), *args], capture_output=True, text=True, env=env)
            calls = log.read_text().splitlines() if log.exists() else []
            return result, calls

    def test_idle_gateway_reinstalls_without_the_live_flag(self):
        result, calls = self.run_helper("hermes_plugin_reinstall", "file:///repo#alans-way",
                                        live=False, live_flag=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(calls, ["plugins install --force file:///repo#alans-way"])
        self.assertIn("restart_required=0", result.stdout)

    def test_live_gateway_passes_allow_live_gateway_when_supported(self):
        result, calls = self.run_helper("hermes_plugin_reinstall", "file:///repo#alans-way",
                                        live=True, live_flag=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(calls, ["plugins install --help",
                                 "plugins install --force --allow-live-gateway file:///repo#alans-way"])
        self.assertIn("restart_required=1", result.stdout)

    def test_live_gateway_without_the_flag_stops_the_gateway_first(self):
        result, calls = self.run_helper("hermes_plugin_reinstall", "file:///repo#alans-way",
                                        live=True, live_flag=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(calls, ["plugins install --help", "gateway stop",
                                 "plugins install --force file:///repo#alans-way"])
        self.assertIn("restart_required=1", result.stdout)

    def test_install_failure_is_returned_with_hermes_output_visible(self):
        result, _ = self.run_helper("hermes_plugin_reinstall", "file:///repo#alans-way",
                                    live=True, live_flag=True, install_fail=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Cannot reinstall plugin files", result.stdout)
        self.assertIn("stub stderr: refused", result.stderr)

    def test_injection_flag_is_written_to_the_default_home_and_bound_profile(self):
        key = "config set plugins.entries.alans-way.allow_gateway_injection true"
        result, calls = self.run_helper("hermes_allow_gateway_injection", "alans-way", "alan-local")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(calls, [key, "-p alan-local " + key])
        for profile in ("default", ""):
            with self.subTest(profile=profile):
                result, calls = self.run_helper("hermes_allow_gateway_injection", "alans-way", profile)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(calls, [key])

    def test_supports_flag_reads_install_help(self):
        result, _ = self.run_helper("hermes_install_supports_live_gateway", live_flag=True)
        self.assertEqual(result.returncode, 0)
        result, _ = self.run_helper("hermes_install_supports_live_gateway", live_flag=False)
        self.assertNotEqual(result.returncode, 0)


class SetupPluginUpdateTests(unittest.TestCase):
    """setup.sh end to end against a stale installed plugin and a stub Hermes."""

    def run_setup(self, live, live_flag=True, install_fail=False):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            hermes_home = home / ".hermes"
            stale = hermes_home / "plugins" / "alans-way"
            stale.mkdir(parents=True)
            (stale / "plugin.yaml").write_text("name: alans-way\nversion: old\n")
            log = home / "calls.log"
            env = dict(os.environ)
            env.pop("HERMES_HOME", None)
            env.update({
                "HOME": str(home),
                "PATH": os.pathsep.join([str(stub_bin(directory, live)), env.get("PATH", "/usr/bin:/bin")]),
                "HERMES_STUB_LOG": str(log),
                "STUB_LIVE_FLAG": "1" if live_flag else "0",
                "STUB_INSTALL_FAIL": "1" if install_fail else "",
            })
            result = subprocess.run(
                [SH, str(SETUP), "--non-interactive", "--skip-browser", "--skip-services"],
                capture_output=True, text=True, env=env)
            calls = log.read_text().splitlines() if log.exists() else []
            return result, calls

    def test_live_gateway_update_uses_the_flag_and_restarts(self):
        result, calls = self.run_setup(live=True)
        installs = [c for c in calls if c.startswith("plugins install --force")]
        self.assertEqual(len(installs), 1, calls)
        self.assertIn("--allow-live-gateway", installs[0])
        self.assertIn("gateway restart", calls)
        self.assertGreater(calls.index("gateway restart"), calls.index(installs[0]))
        self.assertIn("ok   plugin updated", result.stdout)

    def test_idle_gateway_update_does_not_force_a_restart(self):
        result, calls = self.run_setup(live=False)
        self.assertIn("plugins install --force file://%s#alans-way" % ROOT, calls)
        self.assertNotIn("gateway restart", calls)

    def test_refused_update_fails_loudly(self):
        result, calls = self.run_setup(live=True, install_fail=True)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("Cannot reinstall plugin files", result.stdout)
        self.assertIn("FAIL could not update the installed plugin", result.stdout)
        self.assertNotIn("gateway restart", calls)


class VerifyInjectionTests(unittest.TestCase):
    def test_verify_reads_the_flag_from_the_plugin_home_not_the_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ)
            env.pop("HERMES_HOME", None)
            env.update({
                "HOME": directory,
                "PATH": os.pathsep.join([str(stub_bin(directory, False)), env.get("PATH", "/usr/bin:/bin")]),
                "HERMES_STUB_LOG": str(Path(directory) / "calls.log"),
            })
            result = subprocess.run(
                [SH, str(SETUP), "--verify", "--non-interactive", "--profile", "alan-local"],
                capture_output=True, text=True, env=env)
            self.assertIn("ok   gateway injection allowed for alans-way", result.stdout)
            calls = (Path(directory) / "calls.log").read_text().splitlines()
            self.assertIn("config get plugins.entries.alans-way.allow_gateway_injection", calls)


if __name__ == "__main__":
    unittest.main()
