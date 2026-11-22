"""Unit tests for loop.py: a human can push to the run's branch while it runs (pause, hotfix, resume).

Real git in a temp dir: a bare remote, the run's checkout and a "human" clone. Run with
`python3 -m unittest discover -s harness/tests -p 'test_*.py'` (make test does).
"""
from __future__ import annotations

import argparse
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import loop  # noqa: E402

BRANCH = "agent/test"
IDENT = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
         "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}


def g(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def commit(repo: Path, name: str, text: str, msg: str) -> None:
    (repo / name).write_text(text)
    g(repo, "add", "-A")
    g(repo, "commit", "-q", "-m", msg)


class Base(unittest.TestCase):
    def setUp(self) -> None:
        mock_env = dict(os.environ, **IDENT, NTFY_URL="")
        self._old_env = os.environ.copy()
        os.environ.clear()
        os.environ.update(mock_env)
        self.tmp = Path(tempfile.mkdtemp())
        self.remote = self.tmp / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", str(self.remote)], check=True)
        self.repo = self.tmp / "run"
        self.repo.mkdir()
        g(self.repo, "init", "-q", "-b", BRANCH)
        (self.repo / "PLAN.md").write_text("# PLAN: t\n- [ ] T01: one\n  accept: true\n")
        commit(self.repo, "app.txt", "line 1\nline 2\n", "init")
        g(self.repo, "remote", "add", "origin", str(self.remote))
        g(self.repo, "push", "-q", "-u", "origin", BRANCH)
        self.human = self.tmp / "human"
        subprocess.run(["git", "clone", "-q", "-b", BRANCH, str(self.remote), str(self.human)], check=True)
        args = argparse.Namespace(push=True, ci=True, ci_rounds=2, ci_appear=0, ci_timeout=30, ci_poll=0,
                                  max_hours=24, retries=1, max_skips=3, fix_ci=None)
        self.run_ = loop.Run(repo=self.repo, args=args, pc=loop.Pc(False), status=loop.Status(False))
        self.run_.branch = BRANCH
        (self.repo / loop.STATE_DIR).mkdir()
        self.out = io.StringIO()
        self._quiet = contextlib.ExitStack()
        self._quiet.enter_context(contextlib.redirect_stdout(self.out))
        self._quiet.enter_context(contextlib.redirect_stderr(self.out))

    def tearDown(self) -> None:
        self._quiet.close()
        os.environ.clear()
        os.environ.update(self._old_env)
        subprocess.run(["rm", "-rf", str(self.tmp)], check=False)

    def human_push(self, name: str, text: str, msg: str) -> None:
        g(self.human, "pull", "-q", "--rebase")
        commit(self.human, name, text, msg)
        g(self.human, "push", "-q")

    def remote_head(self) -> str:
        return g(self.remote, "rev-parse", BRANCH)


class Sync(Base):
    def test_nothing_new_is_a_no_op(self) -> None:
        head = g(self.repo, "rev-parse", "HEAD")
        self.assertTrue(self.run_.sync())
        self.assertEqual(g(self.repo, "rev-parse", "HEAD"), head)

    def test_human_commit_is_taken_in_and_push_fast_forwards(self) -> None:
        self.human_push("fix.txt", "hotfix\n", "human hotfix")
        commit(self.repo, "app.txt", "line 1\nline 2\nline 3\n", "T01: one")
        self.assertTrue(self.run_.push())
        log = g(self.remote, "log", "--format=%s", BRANCH).splitlines()
        self.assertEqual(log[:3], ["T01: one", "human hotfix", "init"])
        self.assertEqual(self.remote_head(), g(self.repo, "rev-parse", "HEAD"))
        self.assertTrue((self.repo / "fix.txt").exists())
        self.assertIsNone(self.run_.stop_reason())

    def test_branch_not_on_remote_yet_still_pushes(self) -> None:
        g(self.repo, "checkout", "-q", "-b", "agent/new")
        self.run_.branch = "agent/new"
        commit(self.repo, "x.txt", "x\n", "T01: one")
        self.assertTrue(self.run_.push())
        self.assertEqual(g(self.remote, "rev-parse", "agent/new"), g(self.repo, "rev-parse", "HEAD"))

    def test_unreachable_remote_is_not_a_conflict(self) -> None:
        g(self.repo, "remote", "set-url", "origin", str(self.tmp / "missing.git"))
        self.assertTrue(self.run_.sync())
        self.assertEqual(self.run_.sync_error, "")

    def test_conflict_aborts_stops_and_never_force_pushes(self) -> None:
        self.human_push("app.txt", "line 1\nHUMAN\n", "human edit")
        human = self.remote_head()
        commit(self.repo, "app.txt", "line 1\nAGENT\n", "T01: one")
        ours = g(self.repo, "rev-parse", "HEAD")
        self.assertFalse(self.run_.push())
        self.assertEqual(self.remote_head(), human)                       # remote untouched
        self.assertEqual(g(self.repo, "rev-parse", "HEAD"), ours)         # our commit kept, rebase aborted
        self.assertFalse((self.repo / ".git" / "rebase-merge").exists())
        self.assertEqual(g(self.repo, "status", "--porcelain"), "")
        self.assertIn("conflict", self.run_.stop_reason() or "")
        self.assertIn("[notify] Branch conflict", self.out.getvalue())
        self.assertIn("## Branch conflict", self.run_.report("x"))
        self.assertFalse(self.run_.push())                               # stays stopped, no second notify
        self.assertEqual(self.out.getvalue().count("[notify] Branch conflict"), 1)


class Pauses(Base):
    def green(self) -> bool:
        return (self.repo / "ci-fixed.txt").exists()

    def test_pause_before_ci_fix_task_lets_a_human_fix_it(self) -> None:
        paused = []

        def pause_point() -> bool:
            if paused:
                return False
            paused.append(1)
            self.human_push("ci-fixed.txt", "ok\n", "human CI fix")      # pushed while paused
            return True
        self.run_.pause_point = pause_point
        self.run_.wait_ci = lambda: [] if self.green() else [("ci", "log")]
        self.run_.run_task = lambda task: self.fail(f"no CI fix task expected, got {task.id}")
        self.assertFalse(self.run_.ci_gate())
        self.assertTrue(paused)
        self.assertTrue(any("CI green" in n for n in self.run_.ci_notes), self.run_.ci_notes)
        self.assertEqual(self.run_.results, [])

    def test_pause_while_waiting_for_ci_rechecks_the_new_head(self) -> None:
        seen = []

        def pause_point() -> bool:
            if seen:
                return False
            seen.append(1)
            self.human_push("ci-fixed.txt", "ok\n", "human CI fix")
            return True
        self.run_.pause_point = pause_point
        checked = []

        def ci_runs(repo: Path, branch: str, sha: str) -> list[dict]:
            checked.append(sha)
            ok = subprocess.run(["git", "cat-file", "-e", f"{sha}:ci-fixed.txt"], cwd=repo).returncode == 0
            return [{"databaseId": 1, "name": "ci", "status": "completed",
                     "conclusion": "success" if ok else "failure"}]
        old = loop.ci_runs
        loop.ci_runs = ci_runs
        try:
            self.assertEqual(self.run_.wait_ci(), [])
        finally:
            loop.ci_runs = old
        self.assertEqual(checked, [g(self.repo, "rev-parse", "HEAD")])   # only the new HEAD was checked
        self.assertTrue(self.green())

    def test_tasks_added_during_a_ci_pause_send_it_back_to_the_plan(self) -> None:
        paused = []

        def pause_point() -> bool:
            if paused:
                return False
            paused.append(1)
            self.human_push("PLAN.md", "# PLAN: t\n- [ ] T01: one\n  accept: true\n- [ ] T02: two\n", "add T02")
            return True
        self.run_.pause_point = pause_point
        self.run_.wait_ci = lambda: [("ci", "log")]
        self.assertTrue(self.run_.ci_gate())

    def test_loop_rereads_the_plan_after_a_pause(self) -> None:
        paused = []

        def pause_point() -> bool:
            if paused:
                return False
            paused.append(1)
            self.human_push("PLAN.md", "# PLAN: t\n- [ ] T01: one\n  accept: true\n- [ ] T02: two\n", "add T02")
            return True
        ran = []

        def run_task(task: loop.Task) -> loop.Result:
            ran.append(task.id)
            loop.mark_task(self.repo / "PLAN.md", task, "x")
            g(self.repo, "commit", "-q", "-am", task.id)
            return loop.Result(task, "done", 1)
        self.run_.pause_point = pause_point
        self.run_.run_task = run_task
        self.assertEqual(self.run_.loop(), "plan complete")
        self.assertEqual(ran, ["T01", "T02"])


if __name__ == "__main__":
    unittest.main()
