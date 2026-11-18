"""Tests for the job queue, all-time totals, token metrics and the page's partial refresh
(SPEC §8–§10). Run: make check
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from test_contract import PLAN, T0, Base


class JobsTest(Base):
    def add(self, repo: str = "cecedabro/woodshed", **fields: object) -> dict:
        code, _, raw = self.req("POST", "/api/jobs", {"repo": repo, **fields})
        self.assertEqual(code, 201, raw)
        return json.loads(raw)

    def act(self, job: int, action: str, body: dict | None = None, expect: int = 200) -> dict:
        code, _, raw = self.req("POST", f"/api/jobs/{job}/{action}", body or {})
        self.assertEqual(code, expect, raw)
        return json.loads(raw)

    def claim(self) -> dict | None:
        code, _, raw = self.req("POST", "/api/jobs/claim", {"worker": "devbox-0"})
        self.assertEqual(code, 200, raw)
        return json.loads(raw)["job"]

    def queued(self) -> list[int]:
        return [j["id"] for j in self.status()["jobs"]["queued"]]

    def test_add_and_list_in_order(self) -> None:
        a = self.add(note="first")
        b = self.add("cecedabro/scrum-agent", ref="dev", max_hours=2, ci=False)
        self.assertEqual(a["state"], "queued")
        self.assertEqual((b["ref"], b["max_hours"], b["ci"]), ("dev", 2.0, False))
        self.assertEqual(self.queued(), [a["id"], b["id"]])

    def test_validation_and_auth(self) -> None:
        for body in ({"repo": "nope"}, {"repo": "a/b; rm -rf /"}, {"repo": "a/b", "ref": "x y"},
                     {"repo": "a/b", "max_hours": 99}, {"repo": "a/b", "ci": "yes"}, {"repo": "a/b", "note": "x" * 201}):
            self.assertEqual(self.req("POST", "/api/jobs", body)[0], 400, body)
        self.assertEqual(self.req("POST", "/api/jobs", {"repo": "a/b"}, token="wrong")[0], 401)
        self.assertEqual(self.req("POST", "/api/auth", {}, token="wrong")[0], 401)
        self.assertEqual(self.req("POST", "/api/auth", {})[0], 200)

    def test_claim_one_at_a_time(self) -> None:
        a, b = self.add(), self.add("cecedabro/other")
        job = self.claim()
        self.assertEqual((job["id"], job["state"], job["worker"]), (a["id"], "running", "devbox-0"))
        self.assertIsNone(self.claim())                                  # one GPU
        self.assertEqual(self.queued(), [b["id"]])
        self.act(a["id"], "finish", {"state": "done", "exit_code": 0})
        self.assertEqual(self.claim()["id"], b["id"])
        self.assertIsNone(self.claim())
        st = self.status()["jobs"]
        self.assertEqual([j["state"] for j in st["recent"]], ["done"])
        self.assertEqual(len(st["running"]), 1)

    def test_move_and_cancel_queued(self) -> None:
        a, b, c = self.add(), self.add(), self.add()
        self.assertEqual(self.act(c["id"], "move", {"to": "top"})["position"], 1)
        self.assertEqual(self.queued(), [c["id"], a["id"], b["id"]])
        self.act(c["id"], "move", {"to": "down"})
        self.assertEqual(self.queued(), [a["id"], c["id"], b["id"]])
        self.act(a["id"], "move", {"to": "bottom"})
        self.assertEqual(self.queued(), [c["id"], b["id"], a["id"]])
        self.act(b["id"], "cancel")
        self.assertEqual(self.queued(), [c["id"], a["id"]])
        self.act(b["id"], "move", {"to": "up"}, expect=409)
        self.act(a["id"], "move", {"to": "sideways"}, expect=400)
        self.act(999, "cancel", expect=404)

    def test_stop_running_via_heartbeat(self) -> None:
        a = self.add()
        self.claim()
        self.assertEqual(self.act(a["id"], "heartbeat"), {"cancel": False})
        self.act(a["id"], "cancel")
        self.assertTrue(self.status()["jobs"]["running"][0]["stopping"])
        self.assertEqual(self.act(a["id"], "heartbeat"), {"cancel": True})
        self.act(a["id"], "finish", {"state": "cancelled"})
        self.act(a["id"], "finish", {"state": "done"}, expect=409)

    def test_stop_now(self) -> None:
        a = self.add()
        self.claim()
        self.act(a["id"], "cancel")                       # graceful first, then "now" on top
        self.assertEqual(self.act(a["id"], "heartbeat"), {"cancel": True})
        self.act(a["id"], "cancel", {"now": True})
        self.assertTrue(self.status()["jobs"]["running"][0]["stopping_now"])
        self.assertEqual(self.act(a["id"], "heartbeat"), {"cancel": True, "now": True})
        self.act(a["id"], "cancel")                       # a later plain Stop doesn't undo "now"
        self.assertEqual(self.act(a["id"], "heartbeat"), {"cancel": True, "now": True})
        page = self.req("GET", "/", token=None)[2].decode()
        self.assertIn("stopping now", page)

    def test_heartbeat_counts_as_worker_seen(self) -> None:
        a = self.add()
        self.claim()
        self.clock.t += 700                               # a worker busy with a job doesn't ping
        self.assertEqual(self.status()["jobs"]["workers"], [])
        self.act(a["id"], "heartbeat")
        self.assertEqual([w["name"] for w in self.status()["jobs"]["workers"]], ["devbox-0"])

    def test_devbox_update_when_idle(self) -> None:
        self.assertEqual(self.req("POST", "/api/devbox", {"pending": True}, token=None)[0], 401)
        self.assertEqual(self.req("POST", "/api/devbox", {"pending": "yes"})[0], 400)
        a = self.add()
        self.claim()
        code, _, raw = self.req("POST", "/api/devbox", {"pending": True, "target": "0.3.0"})
        self.assertEqual((code, json.loads(raw)), (200, {"idle": False}), "a job is running")
        b = self.add()
        self.act(a["id"], "finish", {"state": "done"})
        self.assertIsNone(self.claim(), "no new job while an update is pending")
        st = self.status()["devbox"]
        self.assertEqual((st["pending"], st["target"]), (True, "0.3.0"))
        self.assertIn("devbox update", self.req("GET", "/", token=None)[2].decode())
        self.assertEqual(json.loads(self.req("POST", "/api/devbox", {"pending": True, "target": "0.3.0"})[2]), {"idle": True})
        self.clock.t += 901                               # the CronJob stopped refreshing: the hold expires
        self.assertFalse(self.status()["devbox"]["pending"])
        self.assertEqual(self.claim()["id"], b["id"])
        self.req("POST", "/api/devbox", {"pending": False})
        self.assertFalse(self.status()["devbox"]["pending"])

    def test_lost_worker_frees_the_queue(self) -> None:
        a, b = self.add(), self.add()
        self.claim()
        self.clock.t += 901
        st = self.status()["jobs"]
        self.assertEqual(st["recent"][0]["state"], "lost")
        self.assertEqual(self.claim()["id"], b["id"])
        self.act(a["id"], "finish", {"state": "done"})                   # a late finish still lands

    def test_requeue(self) -> None:
        a = self.add(note="again")
        self.claim()
        self.act(a["id"], "finish", {"state": "failed", "detail": "boom"})
        new = self.act(a["id"], "requeue")["id"]
        self.assertEqual(self.queued(), [new])
        self.assertEqual(self.status()["jobs"]["queued"][0]["note"], "again")
        self.act(new, "requeue", expect=409)

    def test_run_linked_to_job(self) -> None:
        a = self.add()
        self.claim()
        self.event("run_started", job_id=str(a["id"]), plan=PLAN)
        st = self.status()
        self.assertEqual(st["runs"][0]["job_id"], str(a["id"]))
        self.assertEqual(st["jobs"]["running"][0]["run_id"], "r1")

    def test_page_shows_jobs_and_escapes(self) -> None:
        self.add(note="<b>hi</b>")
        page = self.req("GET", "/", token=None)[2].decode()
        self.assertIn("cecedabro/woodshed", page)
        self.assertIn("&lt;b&gt;hi&lt;/b&gt;", page)
        self.assertIn('data-act="move"', page)
        partial = self.req("GET", "/partial", token=None)[2].decode()
        self.assertIn("cecedabro/woodshed", partial)
        self.assertNotIn("<html", partial)


class TotalsTest(Base):
    def test_all_time_survives_retention(self) -> None:
        self.event("run_started", plan=PLAN)
        self.event("task_done", task_id="T01", attempts=1, seconds=600, commit="abc1234", insertions=40, deletions=2)
        self.event("task_done", task_id="T02", attempts=2, seconds=1200, insertions=10, deletions=8)
        self.event("task_skipped", task_id="T03", attempts=3, seconds=300)
        self.event("run_finished", ended="done", pr="https://github.com/o/r/pull/7", pushed=True)
        self.event("task_done", run="r2", repo="scrum-agent", attempts=1, seconds=60, insertions=1)
        t = self.status()["totals"]
        self.assertEqual((t["commits"], t["lines_added"], t["lines_removed"], t["prs"], t["runs"]),
                         (3, 51, 10, 1, 1))
        self.assertEqual(t["tasks_skipped"], 1)
        self.assertEqual(t["agent_hours"], round(2160 / 3600, 1))
        self.assertEqual(t["first_try_rate"], round(2 / 3, 3))
        self.assertEqual({r["repo"]: r["commits"] for r in t["repos"]}, {"woodshed": 2, "scrum-agent": 1})
        self.assertEqual(self.status()["daily"][-1]["commits"], 3)
        self.stop()
        self.clock.t = T0 + 31 * 86400
        self.start()
        st = self.status()
        self.assertEqual(st["events"], [])
        self.assertEqual(st["totals"]["commits"], 3)                    # all-time stays
        self.assertEqual(len(st["daily"]), 14)

    def test_page_shows_commit(self) -> None:
        self.event("task_done", task_id="T01", attempts=1, seconds=60, commit="abc1234", insertions=5, deletions=1)
        page = self.req("GET", "/", token=None)[2].decode()
        self.assertIn("abc1234", page)
        self.assertIn("Commits", page)


class FakeMetrics:
    """llama-server stand-in: /health 200 and /metrics with settable counters."""

    def __init__(self) -> None:
        self.vals = {"gen": 0, "gen_s": 0.0, "prompt": 0, "prompt_s": 0.0, "busy": 0}
        self.code, self.auth = 200, None
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                outer.auth = self.headers.get("Authorization")
                if self.path == "/metrics" and outer.code == 200:
                    v = outer.vals
                    body = ("# HELP llamacpp:prompt_tokens_total x\n# TYPE llamacpp:prompt_tokens_total counter\n"
                            f"llamacpp:prompt_tokens_total {v['prompt']}\nllamacpp:prompt_seconds_total {v['prompt_s']}\n"
                            f"llamacpp:tokens_predicted_total {v['gen']}\n"
                            f"llamacpp:tokens_predicted_seconds_total {v['gen_s']}\n"
                            f"llamacpp:requests_processing {v['busy']}\n").encode()
                    self.send_response(200)
                elif self.path == "/metrics":
                    body = b"{}"
                    self.send_response(outer.code)
                else:
                    body = b""
                    self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_: object) -> None:
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/v1"

    def close(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()


class TokensTest(Base):
    def setUp(self) -> None:
        super().setUp()
        self.fake = FakeMetrics()
        self.addCleanup(self.fake.close)
        self.stop()
        self.llama_url = self.fake.url
        self.start()

    def sample(self) -> bool:
        return self.servers[-1].app.sample_metrics()

    def test_totals_rates_and_restart(self) -> None:
        self.fake.vals.update(gen=1000, gen_s=25.0, prompt=8000, prompt_s=10.0, busy=1)
        self.assertTrue(self.sample())
        self.clock.t += 60
        self.fake.vals.update(gen=3000, gen_s=75.0, prompt=8000, prompt_s=10.0)
        self.sample()
        tk = self.status()["tokens"]
        self.assertEqual((tk["generated_total"], tk["prompt_total"]), (3000, 8000))
        self.assertEqual(tk["gen_tps"], 40.0)
        self.assertEqual(tk["prompt_tps"], 800.0)
        self.assertEqual(tk["gen_tps_recent"], 40.0)
        self.assertTrue(tk["busy"])
        self.assertEqual(sum(h["generated"] for h in tk["hourly"]), 3000)
        self.clock.t += 60
        self.fake.vals.update(gen=500, gen_s=10.0, prompt=100, prompt_s=1.0, busy=0)   # llama-server restarted
        self.sample()
        tk = self.status()["tokens"]
        self.assertEqual((tk["generated_total"], tk["prompt_total"]), (3500, 8100))
        self.assertFalse(tk["busy"])
        self.assertEqual(self.status()["daily"][-1]["generated"], 3500)

    def test_metrics_off(self) -> None:
        self.fake.code = 501
        self.assertFalse(self.sample())
        tk = self.status()["tokens"]
        self.assertFalse(tk["available"])
        self.assertIn("--metrics", tk["error"])
        self.assertIn("--metrics", self.req("GET", "/", token=None)[2].decode())

    def test_sends_api_key(self) -> None:
        self.stop()
        srv = __import__("app").make_server("127.0.0.1", 0, db_path=self.db, token="t", llama_url=self.fake.url,
                                            llama_key="k3y", clock=self.clock)
        self.addCleanup(srv.server_close)
        srv.app.sample_metrics()
        self.assertEqual(self.fake.auth, "Bearer k3y")
