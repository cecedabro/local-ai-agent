"""Acceptance tests for pausing a run (SPEC §13). Human-owned: the agent may not edit this file.
Run: python3 -m unittest -q status/tests/test_accept_pause.py (or make status-test)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_contract import Base  # noqa: E402

RUN = "devbox-0-123-1794850000"


class PauseApiTest(Base):
    def post(self, path: str, expect: int = 200, token: str | None = None) -> dict:
        kw = {} if token is None else {"token": token}
        code, _, raw = self.req("POST", path, {}, **kw)
        self.assertEqual(code, expect, raw)
        return json.loads(raw) if raw else {}

    def run_entry(self) -> dict:
        return self.run_of(RUN)

    def test_pause_resume_and_control(self) -> None:
        self.event("run_started", run=RUN)
        self.assertEqual(self.post(f"/api/runs/{RUN}/control"), {"pause": False})
        self.assertEqual(self.post(f"/api/runs/{RUN}/pause"), {"run_id": RUN, "pause": True})
        self.assertEqual(self.post(f"/api/runs/{RUN}/control"), {"pause": True})
        self.assertTrue(self.run_entry()["pause"])
        self.assertEqual(self.run_entry()["state"], "running")          # asked, not yet paused
        self.assertEqual(self.post(f"/api/runs/{RUN}/resume"), {"run_id": RUN, "pause": False})
        self.assertEqual(self.post(f"/api/runs/{RUN}/control"), {"pause": False})
        self.assertFalse(self.run_entry()["pause"])

    def test_errors(self) -> None:
        self.post("/api/runs/nope/pause", expect=404)
        self.post("/api/runs/nope/resume", expect=404)
        self.assertEqual(self.post("/api/runs/nope/control"), {"pause": False})
        self.event("run_started", run=RUN)
        self.post(f"/api/runs/{RUN}/pause", expect=401, token="wrong")
        self.post(f"/api/runs/{RUN}/sideways", expect=404)
        self.event("run_finished", run=RUN)
        self.post(f"/api/runs/{RUN}/pause", expect=409)

    def test_paused_state_beats_stale_and_survives_restart(self) -> None:
        self.event("run_started", run=RUN)
        self.post(f"/api/runs/{RUN}/pause")
        self.event("paused", run=RUN)
        self.assertEqual(self.run_entry()["state"], "paused")
        self.clock.t += 5 * 3600                                         # long pause: not stale
        self.assertEqual(self.run_entry()["state"], "paused")
        self.stop()
        self.start()
        self.assertEqual(self.post(f"/api/runs/{RUN}/control"), {"pause": True})
        self.post(f"/api/runs/{RUN}/resume")
        self.event("resumed", run=RUN)
        self.assertEqual(self.run_entry()["state"], "running")

    def test_finished_clears_request(self) -> None:
        self.event("run_started", run=RUN)
        self.post(f"/api/runs/{RUN}/pause")
        self.event("paused", run=RUN)
        self.event("run_finished", run=RUN)
        self.assertEqual(self.run_entry()["state"], "finished")
        self.assertFalse(self.run_entry()["pause"])
        self.assertEqual(self.post(f"/api/runs/{RUN}/control"), {"pause": False})

    def test_url_encoded_run_id(self) -> None:
        rid = "host.local-1-2"
        self.event("run_started", run=rid)
        self.assertEqual(self.post("/api/runs/host%2Elocal-1-2/pause"), {"run_id": rid, "pause": True})


class PausePageTest(Base):
    def page(self) -> str:
        return self.req("GET", "/", token=None)[2].decode()

    def test_buttons_follow_state(self) -> None:
        self.event("run_started", run=RUN)
        self.event("task_started", run=RUN, task_id="T01", title="Build", attempt=1)
        html = self.page()
        self.assertTrue(f'data-run="{RUN}" data-act="pause"' in html, "running: expected the Pause button")
        self.req("POST", f"/api/runs/{RUN}/pause", {})
        html = self.page()
        self.assertTrue("pausing after this task" in html, "requested: expected the 'pausing after this task' pill")
        self.assertTrue(f'data-run="{RUN}" data-act="resume"' in html, "requested: expected the Resume button")
        self.assertFalse(f'data-act="pause"' in html, "requested: the Pause button should be gone")
        self.event("paused", run=RUN)
        html = self.page()
        self.assertTrue('<span class="pill warn">paused</span>' in html, "paused: expected the paused pill")
        self.assertTrue(f'data-run="{RUN}" data-act="resume"' in html, "paused: expected the Resume button")
        self.assertTrue("/api/runs/" in html and "encodeURIComponent" in html, "page script must post to /api/runs/")

    def test_paused_run_still_shown_in_now(self) -> None:
        self.event("run_started", run=RUN, repo="woodshed")
        self.event("paused", run=RUN, repo="woodshed")
        self.clock.t += 5 * 3600
        html = self.page()
        start = html.index('id="now"')
        now = html[start:html.index("</section>", start)]
        self.assertTrue("woodshed" in now and "Idle" not in now, "a paused run must stay in the Now card")
