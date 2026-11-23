"""agent-loop and a preempted lease (status SPEC §4): an attempt cut short because the cv-skills gatekeeper took the
GPU does not count; the loop waits for the lease and retries the same task."""
from __future__ import annotations

import argparse
import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import loop  # noqa: E402


class FakeStatus(loop.Status):
    """lost_lease answers from a script: True while the gatekeeper holds the GPU."""

    def __init__(self, lost: list[bool]) -> None:
        super().__init__(False)
        self.lost = lost
        self.events: list[str] = []
        self.rejoined = 0

    def lost_lease(self) -> bool:
        return self.lost.pop(0) if self.lost else False

    def rejoin(self, should_stop) -> None:
        self.rejoined += 1

    def event(self, kind: str, **fields: object) -> None:
        self.events.append(kind)


class Preempt(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Path(tempfile.mkdtemp())
        for cmd in (["init", "-q", "-b", "agent/t"], ["config", "user.name", "t"], ["config", "user.email", "t@e"]):
            subprocess.run(["git", *cmd], cwd=self.repo, check=True)
        (self.repo / "PLAN.md").write_text("# PLAN: t\n- [ ] T01: one\n  accept: true\n")
        subprocess.run(["git", "add", "-A"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=self.repo, check=True)
        (self.repo / loop.STATE_DIR).mkdir()
        self.args = argparse.Namespace(push=False, retries=2, max_hours=24, ci=False)
        self.out = io.StringIO()
        self.quiet = contextlib.ExitStack()
        self.quiet.enter_context(contextlib.redirect_stdout(self.out))

    def tearDown(self) -> None:
        self.quiet.close()

    def make(self, lost: list[bool], outcomes: list[bool]) -> tuple[loop.Run, list[int]]:
        status = FakeStatus(lost)
        run = loop.Run(repo=self.repo, args=self.args, pc=loop.Pc(False), status=status)
        run.branch = "agent/t"
        tries: list[int] = []

        def attempt(task: loop.Task, n: int, failure: str) -> tuple[bool, str]:
            tries.append(n)
            ok = outcomes.pop(0)
            if ok:
                (self.repo / "out.txt").write_text("x")
            return ok, "" if ok else "model unreachable"
        run.attempt = attempt
        return run, tries

    def task(self) -> loop.Task:
        return loop.parse_plan((self.repo / "PLAN.md").read_text())[0]

    def test_interrupted_attempt_does_not_count(self) -> None:
        # before attempt 1: free; after it fails: lost (interrupted); wait; then attempt 1 again succeeds
        run, tries = self.make(lost=[False, True, True, False], outcomes=[False, True])
        res = run.run_task(self.task())
        self.assertEqual(res.status, "done")
        self.assertEqual(tries, [1, 1])
        self.assertEqual(run.status.rejoined, 1)
        self.assertIn("paused", run.status.events)
        self.assertIn("resumed", run.status.events)
        self.assertIn("interrupted: the GPU was taken", self.out.getvalue())

    def test_ordinary_failures_still_count(self) -> None:
        run, tries = self.make(lost=[], outcomes=[False, False])
        res = run.run_task(self.task())
        self.assertEqual((res.status, tries), ("skipped", [1, 2]))
        self.assertEqual(run.status.rejoined, 0)

    def test_waits_before_starting_when_the_gpu_is_taken(self) -> None:
        run, tries = self.make(lost=[True], outcomes=[True])
        self.assertEqual(run.run_task(self.task()).status, "done")
        self.assertEqual((tries, run.status.rejoined), ([1], 1))

    def test_stop_while_waiting_is_not_a_skip(self) -> None:
        run, tries = self.make(lost=[True], outcomes=[])
        (self.repo / loop.STATE_DIR / "STOP").touch()
        res = run.run_task(self.task())
        self.assertEqual((res.status, tries), ("stopped", []))
        self.assertIn("- [ ] T01", (self.repo / "PLAN.md").read_text())

    def test_lost_lease_reads_the_renew_answer(self) -> None:
        st = loop.Status(False)
        for answer, lost in ((None, False), ({"ok": True}, False), ({"granted": True}, False),
                             ({"granted": False, "position": 1}, True)):
            st._post = lambda path, body, a=answer: a
            self.assertEqual(st.lost_lease(), lost, answer)


if __name__ == "__main__":
    unittest.main()
