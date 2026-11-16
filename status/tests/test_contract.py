"""Contract tests for status/app.py (see status/SPEC.md). Human-owned: the agent may not edit
this file. Run one group with: python3 -m unittest -q status/tests/test_contract.py -k LeaseTest
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

STATUS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(STATUS_DIR))
import app  # noqa: E402

TOKEN = "t0ken"
T0 = 1_800_000_000.0          # a fixed "now" for the fake clock


class Clock:
    def __init__(self) -> None:
        self.t = T0

    def __call__(self) -> float:
        return self.t


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Base(unittest.TestCase):
    llama_url: str | None = None

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "status.db")
        self.clock = Clock()
        self.servers: list[ThreadingHTTPServer] = []
        self.start(token=TOKEN)

    def start(self, token: str = TOKEN) -> None:
        srv = app.make_server("127.0.0.1", 0, db_path=self.db, token=token, llama_url=self.llama_url,
                              lease_ttl=180, probe_interval=30, clock=self.clock)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.servers.append(srv)
        self.base = f"http://127.0.0.1:{srv.server_address[1]}"

    def stop(self) -> None:
        srv = self.servers.pop()
        srv.shutdown()
        srv.server_close()

    def tearDown(self) -> None:
        while self.servers:
            self.stop()
        self.tmp.cleanup()

    # -- http helpers
    def req(self, method: str, path: str, body: object = None, token: str | None = TOKEN,
            raw: bytes | None = None) -> tuple[int, dict, bytes]:
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        r = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def event(self, kind: str, run: str = "r1", repo: str = "woodshed", **fields: object) -> None:
        body = {"run_id": run, "repo": repo, "branch": f"agent/{run}", "kind": kind,
                "ts": "2026-11-16T10:00:00+00:00", **fields}
        code, _, raw = self.req("POST", "/api/events", body)
        self.assertEqual(code, 201, raw)

    def lease(self, action: str, run: str, repo: str = "woodshed") -> dict:
        code, _, raw = self.req("POST", "/api/lease",
                                {"action": action, "run_id": run, "repo": repo, "branch": f"agent/{run}"})
        self.assertEqual(code, 200, raw)
        return json.loads(raw)

    def status(self) -> dict:
        code, _, raw = self.req("GET", "/api/status", token=None)
        self.assertEqual(code, 200, raw)
        return json.loads(raw)

    def run_of(self, run_id: str) -> dict:
        return next(r for r in self.status()["runs"] if r["run_id"] == run_id)


PLAN = [{"id": "T01", "title": "First", "state": "todo"}, {"id": "T02", "title": "Second", "state": "todo"}]


class EventsTest(Base):
    def test_healthz(self) -> None:
        code, _, raw = self.req("GET", "/healthz", token=None)
        self.assertEqual((code, raw.strip()), (200, b"ok"))

    def test_event_accepted(self) -> None:
        code, _, raw = self.req("POST", "/api/events", {"run_id": "r1", "repo": "w", "kind": "run_started",
                                                         "ts": "2026-11-16T10:00:00+00:00"})
        self.assertEqual(code, 201)
        self.assertEqual(json.loads(raw), {"ok": True})

    def test_requires_token(self) -> None:
        body = {"run_id": "r1", "repo": "w", "kind": "run_started", "ts": "x"}
        self.assertEqual(self.req("POST", "/api/events", body, token=None)[0], 401)
        self.assertEqual(self.req("POST", "/api/events", body, token="wrong")[0], 401)

    def test_empty_token_refuses_writes(self) -> None:
        self.stop()
        self.start(token="")
        body = {"run_id": "r1", "repo": "w", "kind": "run_started", "ts": "x"}
        self.assertEqual(self.req("POST", "/api/events", body, token="")[0], 401)

    def test_rejects_bad_bodies(self) -> None:
        self.assertEqual(self.req("POST", "/api/events", raw=b"{not json")[0], 400)
        self.assertEqual(self.req("POST", "/api/events", ["a list"])[0], 400)
        self.assertEqual(self.req("POST", "/api/events", {"repo": "w", "kind": "run_started", "ts": "x"})[0], 400)
        self.assertEqual(self.req("POST", "/api/events", {"run_id": 5, "repo": "w", "kind": "run_started",
                                                          "ts": "x"})[0], 400)
        self.assertEqual(self.req("POST", "/api/events", {"run_id": "r", "repo": "w", "kind": "explode",
                                                          "ts": "x"})[0], 400)

    def test_rejects_huge_body(self) -> None:
        body = {"run_id": "r1", "repo": "w", "kind": "ci", "ts": "x", "detail": "x" * 300_000}
        self.assertEqual(self.req("POST", "/api/events", body)[0], 413)

    def test_events_newest_first_with_received_time(self) -> None:
        self.event("task_started", task_id="T01", title="First", attempt=1)
        self.clock.t += 5
        self.event("task_done", task_id="T01", title="First", attempts=1, seconds=5, extra="kept")
        events = self.status()["events"]
        self.assertEqual([e["kind"] for e in events], ["task_done", "task_started"])
        self.assertEqual(events[0]["extra"], "kept")
        self.assertEqual(events[0]["repo"], "woodshed")
        self.assertTrue(events[0]["received"].startswith("2027-01-15T"), events[0]["received"])

    def test_at_most_50_events(self) -> None:
        for i in range(55):
            self.clock.t += 1
            self.event("ci", detail=f"n{i}")
        events = self.status()["events"]
        self.assertEqual(len(events), 50)
        self.assertEqual(events[0]["detail"], "n54")

    def test_events_survive_restart(self) -> None:
        self.event("run_started", plan=PLAN)
        self.stop()
        self.start()
        self.assertEqual(self.status()["events"][0]["kind"], "run_started")


class LeaseTest(Base):
    def test_first_acquire_granted(self) -> None:
        res = self.lease("acquire", "r1")
        self.assertTrue(res["granted"])
        self.assertEqual(res["position"], 0)
        self.assertEqual(res["holder"]["run_id"], "r1")
        self.assertEqual(res["holder"]["repo"], "woodshed")
        self.assertEqual(res["holder"]["branch"], "agent/r1")
        self.assertIn("since", res["holder"])

    def test_queue_in_order(self) -> None:
        self.lease("acquire", "r1")
        r2 = self.lease("acquire", "r2", repo="scrum-agent")
        self.assertEqual((r2["granted"], r2["position"], r2["holder"]["run_id"]), (False, 1, "r1"))
        r3 = self.lease("acquire", "r3", repo="weatherstation")
        self.assertEqual((r3["granted"], r3["position"]), (False, 2))
        self.assertEqual(self.lease("acquire", "r2")["position"], 1)        # keeps its place
        lease = self.status()["lease"]
        self.assertEqual(lease["holder"]["run_id"], "r1")
        self.assertEqual([w["run_id"] for w in lease["queue"]], ["r2", "r3"])

    def test_release_passes_to_first_waiter(self) -> None:
        self.lease("acquire", "r1")
        self.lease("acquire", "r2")
        self.lease("acquire", "r3")
        rel = self.lease("release", "r1")
        self.assertFalse(rel["granted"])
        self.assertIsNone(rel["holder"])
        r3 = self.lease("acquire", "r3")                                     # not first in line
        self.assertEqual((r3["granted"], r3["position"]), (False, 2))
        self.assertTrue(self.lease("acquire", "r2")["granted"])
        self.assertEqual(self.lease("acquire", "r3")["position"], 1)

    def test_renew_keeps_holder(self) -> None:
        self.lease("acquire", "r1")
        self.clock.t += 120
        self.assertTrue(self.lease("renew", "r1")["granted"])
        self.clock.t += 120                                                  # 240 s after acquire
        self.assertFalse(self.lease("acquire", "r2")["granted"])

    def test_expired_holder_dropped(self) -> None:
        self.lease("acquire", "r1")
        self.lease("acquire", "r2")
        self.clock.t += 181
        self.assertTrue(self.lease("acquire", "r2")["granted"])

    def test_expired_waiter_dropped(self) -> None:
        self.lease("acquire", "r1")
        self.lease("acquire", "r2")
        self.clock.t += 100
        self.lease("acquire", "r3")
        self.lease("renew", "r1")
        self.clock.t += 100                                                  # r2 silent for 200 s
        self.assertEqual(self.lease("acquire", "r3")["position"], 1)

    def test_renew_when_free_grants(self) -> None:
        self.assertTrue(self.lease("renew", "r9")["granted"])

    def test_release_unknown_is_fine(self) -> None:
        res = self.lease("release", "nobody")
        self.assertEqual((res["granted"], res["position"], res["holder"]), (False, 0, None))

    def test_bad_requests(self) -> None:
        self.assertEqual(self.req("POST", "/api/lease", {"action": "steal", "run_id": "r", "repo": "w"})[0], 400)
        self.assertEqual(self.req("POST", "/api/lease", {"action": "acquire", "repo": "w"})[0], 400)
        self.assertEqual(self.req("POST", "/api/lease", {"action": "acquire", "run_id": "r", "repo": "w"},
                                  token=None)[0], 401)

    def test_branch_optional(self) -> None:
        code, _, raw = self.req("POST", "/api/lease", {"action": "acquire", "run_id": "r1", "repo": "w"})
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(raw)["holder"]["branch"], "")


class StatusRunsTest(Base):
    def test_running_run_with_current_task(self) -> None:
        self.event("run_started", plan=PLAN)
        self.clock.t += 10
        self.event("task_started", task_id="T01", title="First", attempt=1)
        self.clock.t += 10
        self.event("task_started", task_id="T01", title="First", attempt=2)
        run = self.run_of("r1")
        self.assertEqual(run["state"], "running")
        self.assertEqual(run["repo"], "woodshed")
        self.assertEqual(run["branch"], "agent/r1")
        self.assertEqual(run["current"], {"task_id": "T01", "title": "First", "attempt": 2})
        self.assertEqual(run["plan"], PLAN)
        self.assertEqual((run["done"], run["skipped"]), (0, 0))
        self.assertEqual((run["ended"], run["pushed"], run["pr"]), (None, None, None))
        self.assertLess(run["started"], run["last_event"])

    def test_done_and_skipped(self) -> None:
        self.event("run_started", plan=PLAN)
        self.event("task_started", task_id="T01", title="First", attempt=1)
        plan2 = [dict(PLAN[0], state="done"), PLAN[1]]
        self.event("task_done", task_id="T01", title="First", attempts=1, seconds=60, plan=plan2)
        run = self.run_of("r1")
        self.assertIsNone(run["current"])
        self.assertEqual(run["done"], 1)
        self.assertEqual(run["plan"], plan2)
        self.event("task_started", task_id="T02", title="Second", attempt=1)
        self.event("ci", detail="no plan here")
        self.assertEqual(self.run_of("r1")["current"]["task_id"], "T02")
        self.event("task_skipped", task_id="T02", title="Second", attempts=3, seconds=90, reason="nope")
        run = self.run_of("r1")
        self.assertEqual((run["done"], run["skipped"], run["current"]), (1, 1, None))
        self.assertEqual(run["plan"], plan2)                                 # latest event *with* a plan

    def test_finished(self) -> None:
        self.event("run_started", plan=PLAN)
        self.event("task_started", task_id="T01", title="First", attempt=1)
        self.event("run_finished", ended="plan complete", done=0, skipped=0, pushed=False,
                   pr="https://github.com/o/r/pull/1")
        run = self.run_of("r1")
        self.assertEqual(run["state"], "finished")
        self.assertIsNone(run["current"])
        self.assertEqual((run["ended"], run["pushed"], run["pr"]),
                         ("plan complete", False, "https://github.com/o/r/pull/1"))

    def test_newest_first_and_capped(self) -> None:
        for i in range(12):
            self.clock.t += 10
            self.event("run_started", run=f"r{i}")
        runs = self.status()["runs"]
        self.assertEqual(len(runs), 10)
        self.assertEqual(runs[0]["run_id"], "r11")
        self.clock.t += 10
        self.event("ci", run="r5", detail="late")
        self.assertEqual(self.status()["runs"][0]["run_id"], "r5")

    def test_stale_unless_holding_lease(self) -> None:
        self.event("run_started", run="old")
        self.event("run_started", run="held")
        self.lease("acquire", "held")
        self.clock.t += 3601
        self.lease("renew", "held")
        self.assertEqual(self.run_of("old")["state"], "stale")
        self.assertEqual(self.run_of("held")["state"], "running")

    def test_empty(self) -> None:
        st = self.status()
        self.assertEqual((st["runs"], st["events"]), ([], []))
        self.assertEqual(st["lease"], {"holder": None, "queue": []})
        self.assertTrue(st["now"].startswith("2027-01-15T"), st["now"])


class StatsTest(Base):
    def test_stats(self) -> None:
        self.event("task_done", task_id="T01", attempts=1, seconds=600)
        self.event("task_done", task_id="T02", attempts=2, seconds=1200)
        self.event("task_skipped", task_id="T03", attempts=3, seconds=50)
        stats = self.status()["stats"]
        self.assertEqual(stats["tasks_done"], 2)
        self.assertEqual(stats["tasks_skipped"], 1)
        self.assertEqual(stats["first_try_rate"], 0.5)
        self.assertEqual(stats["avg_task_minutes"], 15.0)
        self.assertEqual(stats["tasks_per_hour_24h"], round(2 / 24, 3))

    def test_24h_window(self) -> None:
        self.event("task_done", task_id="T01", attempts=1, seconds=60)
        self.clock.t += 25 * 3600
        self.event("task_done", task_id="T02", attempts=1, seconds=60)
        stats = self.status()["stats"]
        self.assertEqual(stats["tasks_done"], 2)
        self.assertEqual(stats["tasks_per_hour_24h"], round(1 / 24, 3))

    def test_no_data(self) -> None:
        stats = self.status()["stats"]
        self.assertEqual(stats, {"tasks_done": 0, "tasks_skipped": 0, "first_try_rate": None,
                                 "avg_task_minutes": None, "tasks_per_hour_24h": 0})


class FakeLlama:
    """A /health endpoint answering `code`; counts hits."""

    def __init__(self, code: int) -> None:
        self.code, self.hits = code, 0
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                if self.path == "/health":
                    outer.hits += 1
                    self.send_response(outer.code)
                else:
                    self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *_: object) -> None:
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/v1"

    def close(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()


class ModelTest(Base):
    def with_llama(self, url: str) -> None:
        self.stop()
        self.llama_url = url
        self.start()

    def test_unknown_without_url(self) -> None:
        self.assertEqual(self.status()["model"], {"state": "unknown", "checked": None})

    def test_unreachable(self) -> None:
        self.with_llama(f"http://127.0.0.1:{free_port()}/v1")
        model = self.status()["model"]
        self.assertEqual(model["state"], "unreachable")
        self.assertTrue(model["checked"].startswith("2027-01-15T"))

    def test_ready_loading_and_cache(self) -> None:
        fake = FakeLlama(200)
        self.addCleanup(fake.close)
        self.with_llama(fake.url)
        self.assertEqual(self.status()["model"]["state"], "ready")
        fake.code = 503
        self.assertEqual(self.status()["model"]["state"], "ready")           # cached
        self.assertEqual(fake.hits, 1)
        self.clock.t += 31
        self.assertEqual(self.status()["model"]["state"], "loading")
        self.assertEqual(fake.hits, 2)


class PageTest(Base):
    def page(self) -> str:
        code, headers, raw = self.req("GET", "/", token=None)
        self.assertEqual(code, 200)
        ctype = {k.lower(): v for k, v in headers.items()}["content-type"]
        self.assertTrue(ctype.startswith("text/html"), ctype)
        return raw.decode()

    def test_shows_running_run_queue_and_model(self) -> None:
        self.event("run_started", plan=PLAN)
        self.event("task_started", task_id="T01", title="Build the widget", attempt=2)
        self.lease("acquire", "r1")
        self.lease("acquire", "r2", repo="scrum-agent")
        html = self.page()
        for text in ("woodshed", "agent/r1", "T01", "Build the widget", "scrum-agent", "T02", "unknown"):
            self.assertIn(text, html)

    def test_idle(self) -> None:
        self.assertIn("Idle", self.page())

    def test_escapes_values(self) -> None:
        self.event("task_started", task_id="T01", title="<script>alert(1)</script>", attempt=1)
        html = self.page()
        self.assertNotIn("<script>alert(1)", html)
        self.assertIn("&lt;script&gt;alert(1)", html)

    def test_no_external_resources(self) -> None:
        self.event("run_started", plan=PLAN)
        html = self.page()
        for bad in ('src="http', "src='http", 'href="http', "href='http", "@import"):
            self.assertNotIn(bad, html)

    def test_404(self) -> None:
        self.assertEqual(self.req("GET", "/nope", token=None)[0], 404)


class PageScriptTest(Base):
    def test_refreshes_from_api(self) -> None:
        html = self.req("GET", "/", token=None)[2].decode()
        self.assertIn("<script", html)
        self.assertIn("/api/status", html)
        self.assertIn("5000", html)
        self.assertIn('name="viewport"', html)


class MainTest(Base):
    def test_retention_30_days(self) -> None:
        self.event("ci", detail="old")
        self.clock.t += 10
        self.event("ci", detail="newer")
        self.stop()
        self.clock.t = T0 + 30 * 86400 + 5                                   # "old" > 30 days, "newer" not
        self.start()
        self.assertEqual([e["detail"] for e in self.status()["events"]], ["newer"])

    def test_runs_as_script(self) -> None:
        port = free_port()
        env = {**os.environ, "STATUS_PORT": str(port), "STATUS_DB": self.db, "STATUS_TOKEN": "x"}
        env.pop("LLAMA_BASE_URL", None)
        proc = subprocess.Popen([sys.executable, str(STATUS_DIR / "app.py")], env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(proc.wait, 10)
        self.addCleanup(proc.terminate)
        self.base = f"http://127.0.0.1:{port}"
        for _ in range(100):
            try:
                if self.req("GET", "/healthz", token=None)[0] == 200:
                    break
            except OSError:
                pass
            time.sleep(0.1)
        else:
            self.fail("app.py did not start")
        code, _, _ = self.req("POST", "/api/events", {"run_id": "r", "repo": "w", "kind": "ci", "ts": "x"},
                              token="x")
        self.assertEqual(code, 201)


if __name__ == "__main__":
    unittest.main()
