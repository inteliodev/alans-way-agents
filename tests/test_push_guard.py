"""intelio push guard: detection, grants, agent scoping, and real git pushes through the hook."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
GUARD_SRC = REPO / "push-approval" / "push_guard.py"
SETUP = REPO / "scripts" / "push-approval.sh"


def load_guard():
    spec = importlib.util.spec_from_file_location("push_guard_under_test", GUARD_SRC)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pg = load_guard()


class DetectTests(unittest.TestCase):
    PUSHES = [
        "git push origin main",
        "cd repo && git push",
        "git -C ~/work/app push origin HEAD",
        "git -c user.name=x push",
        "GIT_TRACE=1 git push --force-with-lease",
        "sudo -u hayden git push",
        "timeout 60 git push",
        "nohup git push > out.txt &",
        "git status; git push",
        "bash -c 'git push --no-verify'",
        "sh -c \"cd x && git push\"",
        "ssh srv git push",
        "ssh srv 'cd app && git push'",
        "/usr/bin/git push",
        "git subtree push --prefix docs origin gh-pages",
        "git lfs push origin main",
        "gh pr merge 29 --squash",
        "gh -R inteliodev/alans-way pr merge 29",
        "gh repo sync inteliodev/hermes-agent",
        "gh repo create x --source . --push",
        "gh release create v1.0",
        "gh api -X PUT repos/o/r/pulls/3/merge",
        "gh api --method PATCH repos/o/r/git/refs/heads/main -f sha=abc",
        "gh api repos/o/r/merges -f base=main -f head=x",
        "echo \"$(git push)\"",
    ]
    NOT_PUSHES = [
        "git status",
        "git commit -m 'push the fix'",
        "git log --grep=push",
        "echo git push",
        "npm run push",
        "git pushx",
        "gh pr create --fill",
        "gh pr view 29",
        "gh api repos/o/r/pulls",
        "gh api -X GET repos/o/r/contents/README.md",
        "docker push image",
        "",
    ]

    def test_pushes(self):
        for command in self.PUSHES:
            with self.subTest(command=command):
                self.assertTrue(pg.find_pushes(command), command)

    def test_not_pushes(self):
        for command in self.NOT_PUSHES:
            with self.subTest(command=command):
                self.assertEqual(pg.find_pushes(command), [], command)

    def test_skipping_hooks_is_labelled(self):
        self.assertIn("skipping hooks", pg.find_pushes("git push --no-verify")[0])
        self.assertIn("skipping hooks", pg.find_pushes("git -c core.hooksPath=/dev/null push")[0])

    def test_push_count(self):
        self.assertEqual(len(pg.find_pushes("git push origin a && git push origin b")), 2)

    def test_python_code(self):
        self.assertTrue(pg.find_pushes_in_code("import subprocess\nsubprocess.run(['git', 'push', 'origin'])"))
        self.assertTrue(pg.find_pushes_in_code("import os\nos.system('git push origin main')"))
        self.assertEqual(pg.find_pushes_in_code("print('hello')\nx = [1, 2]"), [])

    def test_tamper(self):
        for text in ("git config --global --unset core.hooksPath", "rm -rf ~/.config/intelio/push-guard",
                     "export INTELIO_PUSH_GRANT=abc", "GIT_CONFIG_COUNT=1 git status",
                     "ls ~/.local/state/intelio/push-grants"):
            self.assertTrue(pg.touches_guard(text), text)
        self.assertFalse(pg.touches_guard("git config --global user.name x"))


class ContextTests(unittest.TestCase):
    def test_env_markers(self):
        self.assertEqual(pg.agent_context({"INTELIO_AGENT": "hermes"}, ""), "env:INTELIO_AGENT")
        self.assertEqual(pg.agent_context({"HERMES_SUPERVISED_CHILD": "1"}, ""), "env:HERMES_SUPERVISED_CHILD")

    def test_cgroups(self):
        gw = "0::/user.slice/user-1000.slice/user@1000.service/app.slice/hermes-gateway.service\n"
        pwa = "0::/user.slice/user-1000.slice/user@1000.service/app.slice/intelio-pwa.service\n"
        run = "0::/user.slice/user-1000.slice/user@1000.service/app.slice/run-r1234.scope\n"
        ssh = "0::/user.slice/user-1000.slice/session-1664.scope\n"
        self.assertEqual(pg.agent_context({}, gw), "cgroup:hermes-gateway.service")
        self.assertEqual(pg.agent_context({}, pwa), "cgroup:intelio-pwa.service")
        self.assertEqual(pg.agent_context({}, run), "cgroup:run-r1234.scope")
        self.assertEqual(pg.agent_context({}, ssh), "")
        self.assertEqual(pg.agent_context({}, "0::/\n"), "")
        self.assertEqual(pg.agent_context({}, ""), "")
        # an env marker wins even inside an SSH login (hermes chat started from a terminal)
        self.assertEqual(pg.agent_context({"INTELIO_AGENT": "1"}, ssh), "env:INTELIO_AGENT")


class GuardHome(unittest.TestCase):
    """A throwaway HOME/XDG tree for every test that writes grants or git config."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="push-guard-")
        self.home = Path(self.tmp) / "home"
        self.home.mkdir()
        self.env = {k: v for k, v in os.environ.items()
                    if k not in ("INTELIO_AGENT", "HERMES_SUPERVISED_CHILD", "INTELIO_PUSH_GRANT",
                                 "XDG_CONFIG_HOME", "XDG_STATE_HOME", "GIT_CONFIG_GLOBAL")}
        self.env.update(HOME=str(self.home), GIT_CONFIG_NOSYSTEM="1", GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e",
                        GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@e")
        self.patch = mock.patch.dict(os.environ, self.env, clear=True)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)


class GrantTests(GuardHome):
    def test_mint_and_consume_once(self):
        gid = pg.mint_grant("git push", uses=1)
        path = pg.grants_dir() / f"{gid}.json"
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(pg.grants_dir().stat().st_mode & 0o777, 0o700)
        self.assertEqual(pg.consume_grant(gid), (True, ""))
        ok, why = pg.consume_grant(gid)
        self.assertFalse(ok)
        self.assertIn("already used", why)

    def test_uses_and_expiry(self):
        gid = pg.mint_grant("git push a && git push b", uses=2)
        self.assertTrue(pg.consume_grant(gid)[0])
        self.assertTrue(pg.consume_grant(gid)[0])
        self.assertFalse(pg.consume_grant(gid)[0])
        old = pg.mint_grant("git push", now=time.time() - 3600)
        ok, why = pg.consume_grant(old)
        self.assertFalse(ok)
        self.assertIn(why, ("approval grant expired", "approval grant not found (already used or expired)"))

    def test_bad_ids(self):
        self.assertEqual(pg.consume_grant(None)[0], False)
        self.assertEqual(pg.consume_grant("../../etc/passwd"), (False, "malformed approval grant"))
        self.assertEqual(pg.consume_grant("0" * 32)[0], False)

    def test_loose_permissions_refused(self):
        gid = pg.mint_grant("git push")
        os.chmod(pg.grants_dir() / f"{gid}.json", 0o644)
        self.assertEqual(pg.consume_grant(gid), (False, "approval grant has unsafe permissions"))

    def test_check_push_person_vs_agent(self):
        with mock.patch.object(pg, "CGROUP_FILE", str(Path(self.tmp) / "cg")):
            Path(self.tmp, "cg").write_text("0::/user.slice/user-1000.slice/session-3.scope\n")
            self.assertEqual(pg.check_push("git push", env={}), (True, ""))
            Path(self.tmp, "cg").write_text(
                "0::/user.slice/user-1000.slice/user@1000.service/app.slice/hermes-gateway.service\n")
            ok, message = pg.check_push("git push", env={})
            self.assertFalse(ok)
            self.assertIn("Hayden's approval", message)
            gid = pg.mint_grant("git push")
            self.assertEqual(pg.check_push("git push", env={"INTELIO_PUSH_GRANT": gid}), (True, ""))
        lines = pg.log_file().read_text().splitlines()
        self.assertEqual([json.loads(line)["decision"] for line in lines], ["allowed", "refused", "allowed"])
        self.assertNotIn(gid, "".join(lines))  # the log never records the grant id

    def test_log_redacts_url_credentials(self):
        pg.log_event(decision="x", url="https://user:secret@github.com/o/r.git")
        self.assertNotIn("secret", pg.log_file().read_text())


@unittest.skipUnless(shutil.which("git") and sys.platform != "win32", "needs git on a POSIX system")
class HookEndToEnd(GuardHome):
    """setup install --no-hermes into a temp HOME, then real pushes to a local bare remote."""

    def sh(self, *args, env=None, cwd=None, check=True):
        return subprocess.run(list(args), cwd=cwd, env=env or os.environ.copy(), capture_output=True, text=True,
                              check=check)

    def setUp(self):
        super().setUp()
        out = self.sh("sh", str(SETUP), "install", "--no-hermes")
        self.assertIn("core.hooksPath ->", out.stdout)
        self.guard = self.home / ".config" / "intelio" / "push-guard"
        self.remote = Path(self.tmp) / "remote.git"
        self.work = Path(self.tmp) / "work"
        self.sh("git", "init", "-q", "--bare", str(self.remote))
        self.sh("git", "init", "-q", "-b", "main", str(self.work))
        self.sh("git", "remote", "add", "origin", str(self.remote), cwd=self.work)
        (self.work / "a.txt").write_text("a\n")
        self.sh("git", "add", "a.txt", cwd=self.work)
        self.sh("git", "commit", "-q", "-m", "a", cwd=self.work)

    def push(self, extra_env=None, *args):
        env = os.environ.copy()
        env.update(extra_env or {})
        return self.sh("git", "push", "-q", "origin", "main", *args, env=env, cwd=self.work, check=False)

    def remote_head(self):
        return self.sh("git", "--git-dir", str(self.remote), "rev-parse", "--verify", "-q", "main", check=False).stdout

    def test_install_layout(self):
        self.assertEqual(self.sh("git", "config", "--global", "core.hooksPath").stdout.strip(),
                         str(self.guard / "hooks"))
        for name in ("push_guard.py", "hooks/pre-push", "hooks/pre-commit", "bin/gh", "bin/git"):
            self.assertTrue(os.access(self.guard / name, os.X_OK), name)

    def test_agent_push_refused_without_grant(self):
        result = self.push({"INTELIO_AGENT": "hermes"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("push refused", result.stderr)
        self.assertEqual(self.remote_head(), "")

    def test_agent_push_with_grant_once(self):
        mint = self.sh(sys.executable, "-c",
                       f"import sys; sys.path.insert(0, {str(self.guard)!r}); import push_guard; "
                       "print(push_guard.mint_grant('git push origin main'))")
        gid = mint.stdout.strip()
        result = self.push({"INTELIO_AGENT": "hermes", "INTELIO_PUSH_GRANT": gid})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.remote_head())
        (self.work / "b.txt").write_text("b\n")
        self.sh("git", "add", "b.txt", cwd=self.work)
        self.sh("git", "commit", "-q", "-m", "b", cwd=self.work)
        again = self.push({"INTELIO_AGENT": "hermes", "INTELIO_PUSH_GRANT": gid})
        self.assertNotEqual(again.returncode, 0)
        self.assertIn("already used", again.stderr)

    def test_hermes_service_marker_refused(self):
        self.assertNotEqual(self.push({"HERMES_SUPERVISED_CHILD": "1"}).returncode, 0)

    def test_person_push_allowed(self):
        if pg.agent_context({}):
            self.skipTest("this test process itself runs under a systemd user manager")
        result = self.push()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_repo_hooks_still_run(self):
        hook = self.work / ".git" / "hooks" / "pre-commit"
        hook.write_text("#!/bin/sh\necho repo-hook-ran > \"$(git rev-parse --show-toplevel)/ran.txt\"\n")
        hook.chmod(0o755)
        (self.work / "c.txt").write_text("c\n")
        self.sh("git", "add", "c.txt", cwd=self.work)
        self.sh("git", "commit", "-q", "-m", "c", cwd=self.work)
        self.assertEqual((self.work / "ran.txt").read_text().strip(), "repo-hook-ran")

    def test_repo_pre_push_chained_after_grant(self):
        hook = self.work / ".git" / "hooks" / "pre-push"
        hook.write_text("#!/bin/sh\nexit 3\n")
        hook.chmod(0o755)
        gid = self.sh(sys.executable, "-c",
                      f"import sys; sys.path.insert(0, {str(self.guard)!r}); import push_guard; "
                      "print(push_guard.mint_grant('git push'))").stdout.strip()
        result = self.push({"INTELIO_AGENT": "hermes", "INTELIO_PUSH_GRANT": gid})
        self.assertNotEqual(result.returncode, 0)  # the repo's own pre-push still decides
        self.assertEqual(self.remote_head(), "")

    def test_no_verify_caught_by_git_shim(self):
        env = os.environ.copy()
        env.update(INTELIO_AGENT="hermes", PATH=f"{self.guard / 'bin'}{os.pathsep}{env['PATH']}")
        result = self.sh("git", "push", "-q", "--no-verify", "origin", "main", env=env, cwd=self.work, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("push refused", result.stderr)
        self.assertEqual(self.remote_head(), "")
        ok = self.sh("git", "status", "--short", env=env, cwd=self.work, check=False)
        self.assertEqual(ok.returncode, 0)  # non-push git passes straight through the shim

    def test_gh_shim(self):
        fake = Path(self.tmp) / "fakebin"
        fake.mkdir()
        (fake / "gh").write_text("#!/bin/sh\necho real-gh \"$@\"\n")
        (fake / "gh").chmod(0o755)
        env = os.environ.copy()
        env.update(INTELIO_AGENT="hermes", PATH=f"{self.guard / 'bin'}{os.pathsep}{fake}{os.pathsep}{env['PATH']}")
        merged = self.sh("gh", "pr", "merge", "29", env=env, check=False)
        self.assertNotEqual(merged.returncode, 0)
        self.assertIn("push refused", merged.stderr)
        view = self.sh("gh", "pr", "view", "29", env=env, check=False)
        self.assertEqual(view.stdout.strip(), "real-gh pr view 29")

    def test_uninstall_restores_previous_hooks_path(self):
        self.sh("sh", str(SETUP), "uninstall", "--no-hermes")
        self.assertEqual(self.sh("git", "config", "--global", "core.hooksPath", check=False).stdout.strip(), "")
        self.sh("git", "config", "--global", "core.hooksPath", "/opt/team-hooks")
        self.sh("sh", str(SETUP), "install", "--no-hermes")
        self.assertEqual((self.guard / "previous-hooks-path").read_text().strip(), "/opt/team-hooks")
        self.sh("sh", str(SETUP), "uninstall", "--no-hermes")
        self.assertEqual(self.sh("git", "config", "--global", "core.hooksPath").stdout.strip(), "/opt/team-hooks")

    def test_dry_run_changes_nothing(self):
        self.sh("sh", str(SETUP), "uninstall", "--no-hermes")
        out = self.sh("sh", str(SETUP), "install", "--no-hermes", "--dry-run")
        self.assertIn("plan", out.stdout)
        self.assertEqual(self.sh("git", "config", "--global", "core.hooksPath", check=False).stdout.strip(), "")
        self.assertFalse((self.guard / "hooks" / "pre-push").exists())


if __name__ == "__main__":
    unittest.main()
