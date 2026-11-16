"""Acceptance tests for the history import, the repo dropdown and the context size
(SPEC §8 repo list, §11 n_ctx, §12). Human-owned: the agent may not edit this file.
Run: python3 -m unittest -q status/tests/test_accept_history.py (or make status-test)
"""
from __future__ import annotations

import datetime as dt
import http.client
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_contract import TOKEN, T0, Base, app  # noqa: E402


def iso_day(t: float) -> str:
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%d")


class FakeLlama:
    """/health 200, /props with n_ctx (or 500 while `broken`), /metrics with fixed counters."""

    def __init__(self) -> None:
        self.props_calls = 0
        self.broken = False
        self.auth: str | None = None
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                code, data = 404, b""
                if self.path == "/health":
                    code, data = 200, b'{"status":"ok"}'
                elif self.path == "/props":
                    outer.props_calls += 1
                    outer.auth = self.headers.get("Authorization")
                    if outer.broken:
                        code, data = 500, b"{}"
                    else:
                        code, data = 200, json.dumps({"default_generation_settings": {"n_ctx": 49152}}).encode()
                elif self.path == "/metrics":
                    code, data = 200, (
                        "llamacpp:tokens_predicted_total 100\n"
                        "llamacpp:tokens_predicted_seconds_total 2\n"
                        "llamacpp:prompt_tokens_total 1000\n"
                        "llamacpp:prompt_seconds_total 1\n"
                        "llamacpp:requests_processing 0\n").encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *_: object) -> None:
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/v1"

    def close(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()


class LlamaBase(Base):
    owners: list[str] | None = None

    def setUp(self) -> None:
        super().setUp()
        self.fake = FakeLlama()
        self.addCleanup(self.fake.close)
        self.stop()
        self.srv = app.make_server("127.0.0.1", 0, db_path=self.db, token=TOKEN, llama_url=self.fake.url,
                                   llama_key="llk", clock=self.clock, job_owners=self.owners)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.servers.append(self.srv)
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"


# --------------------------------------------------------------------- §12.1 import
EV = {"repo": "woodshed", "kind": "task_done", "ts": "2026-11-02T10:00:00+00:00", "task_id": "T01",
      "title": "First", "commit": "abc1234", "insertions": 10, "deletions": 2, "attempts": 1}


class ImportTest(Base):
    def imp(self, body: dict, expect: int = 200, token: str | None = TOKEN) -> dict:
        code, _, raw = self.req("POST", "/api/import", body, token=token)
        self.assertEqual(code, expect, raw)
        return json.loads(raw) if raw else {}

    def announce_big_body(self) -> int:
        """Send only the headers of a 9 MB body; the server must answer 413 without reading it."""
        host, port = self.base.removeprefix("http://").split(":")
        con = http.client.HTTPConnection(host, int(port), timeout=10)
        con.putrequest("POST", "/api/import")
        con.putheader("Authorization", f"Bearer {TOKEN}")
        con.putheader("Content-Type", "application/json")
        con.putheader("Content-Length", str(9 * 1024 * 1024))
        con.endheaders()
        status = con.getresponse().status
        con.close()
        return status

    def test_events_count_once(self) -> None:
        body = {"events": [
            {**EV, "key": "woodshed:a"},
            {**EV, "key": "woodshed:b", "ts": "2026-11-03T09:00:00Z", "insertions": 5, "deletions": 0,
             "attempts": 2},
            {"key": "woodshed:run:x", "repo": "woodshed", "kind": "run_started", "ts": "2026-11-02T09:00:00+00:00"},
            {"key": "woodshed:pr:4", "repo": "woodshed", "kind": "run_finished", "ts": "2026-11-03T12:00:00+00:00",
             "pr": "https://github.com/cecedabro/woodshed/pull/4"},
            {**EV, "key": "scrum:a", "repo": "scrum-agent", "kind": "task_skipped"},
        ]}
        self.assertEqual(self.imp(body), {"events_added": 5, "events_skipped": 0,
                                          "token_days": 0, "token_days_ignored": 0})
        self.assertEqual(self.imp(body), {"events_added": 0, "events_skipped": 5,
                                          "token_days": 0, "token_days_ignored": 0})
        st = self.status()
        tot = st["totals"]
        self.assertEqual((tot["commits"], tot["lines_added"], tot["lines_removed"]), (2, 15, 2))
        self.assertEqual((tot["runs"], tot["prs"], tot["tasks_skipped"]), (1, 1, 1))
        self.assertEqual(tot["first_try_rate"], 0.5)
        self.assertTrue(tot["since"].startswith("2026-11-02T09:00:00"))
        self.assertEqual({r["repo"] for r in tot["repos"]}, {"woodshed", "scrum-agent"})
        # not live events
        self.assertEqual(st["events"], [])
        self.assertEqual(st["runs"], [])
        self.assertEqual(st["stats"]["tasks_done"], 0)

    def test_daily_uses_event_day(self) -> None:
        # the fake clock is in 2027; a recent ts lands on its own day in the 14-day chart
        day = iso_day(T0 - 3 * 86400)
        ts = f"{day}T08:00:00+00:00"
        self.imp({"events": [{**EV, "key": "k1", "ts": ts}, {**EV, "key": "k2", "ts": ts}]})
        daily = {d["day"]: d for d in self.status()["daily"]}
        self.assertEqual(daily[day]["commits"], 2)
        self.assertEqual(daily[day]["lines_added"], 20)
        self.assertEqual(daily[iso_day(T0)]["commits"], 0)

    def test_since_keeps_earliest(self) -> None:
        self.event("run_started")                               # live, at T0 (2027)
        self.imp({"events": [{**EV, "key": "old"}]})
        self.assertTrue(self.status()["totals"]["since"].startswith("2026-11-02T10:00:00"))

    def test_validation_stores_nothing(self) -> None:
        good = {**EV, "key": "good"}
        bad_bodies = [
            {"events": [good, {**EV}]},                                    # no key
            {"events": [good, {**EV, "key": ""}]},
            {"events": [good, {**EV, "key": "x", "kind": "ci"}]},
            {"events": [good, {**EV, "key": "x", "ts": "yesterday"}]},
            {"events": [good, {**EV, "key": "x", "ts": "2026-11-02T10:00:00"}]},   # no zone
            {"events": [good, {**EV, "key": "x", "repo": ""}]},
            {"events": "nope"},
            {"tokens": [{"day": "2026-13-01", "source": "opencode", "generated": 1, "prompt": 1}]},
            {"tokens": [{"day": "2026-11-01", "source": "", "generated": 1, "prompt": 1}]},
            {"tokens": [{"day": "2026-11-01", "source": "o", "generated": -1, "prompt": 1}]},
            {"tokens": [{"day": "2026-11-01", "source": "o", "generated": 1.5, "prompt": 1}]},
            {"events": [good] * 5001},
        ]
        for body in bad_bodies:
            code, _, raw = self.req("POST", "/api/import", body)
            self.assertEqual(code, 400, (str(body)[:200], raw))
            self.assertIn("error", json.loads(raw))
        self.assertEqual(self.req("POST", "/api/import", raw=b"[1]")[0], 400)
        self.assertEqual(self.status()["totals"]["commits"], 0)
        self.imp({"events": [good]}, expect=401, token="wrong")
        self.assertEqual(self.announce_big_body(), 413)
        self.assertEqual(self.imp({}), {"events_added": 0, "events_skipped": 0,
                                        "token_days": 0, "token_days_ignored": 0})

    def test_keys_survive_restart(self) -> None:
        self.imp({"events": [{**EV, "key": "k"}]})
        self.stop()
        self.start()
        self.assertEqual(self.imp({"events": [{**EV, "key": "k"}]})["events_skipped"], 1)
        self.assertEqual(self.status()["totals"]["commits"], 1)

    def test_tokens_replace_per_day_and_source(self) -> None:
        day = iso_day(T0 - 2 * 86400)
        rows = [{"day": day, "source": "opencode", "generated": 100, "prompt": 1000},
                {"day": "2026-11-01", "source": "opencode", "generated": 50, "prompt": 500}]
        self.assertEqual(self.imp({"tokens": rows})["token_days"], 2)
        rows[0]["generated"] = 300
        self.imp({"tokens": rows})
        self.imp({"tokens": [{"day": day, "source": "other", "generated": 1, "prompt": 0}]})
        tok = self.status()["tokens"]
        self.assertEqual((tok["generated_total"], tok["prompt_total"]), (351, 1500))
        self.assertIsNone(tok["gen_tps"])
        self.assertEqual(tok["generated_24h"], 0)
        daily = {d["day"]: d for d in self.status()["daily"]}
        self.assertEqual(daily[day]["generated"], 301)


class ImportWithLiveTokensTest(LlamaBase):
    def test_days_from_first_live_sample_are_ignored(self) -> None:
        self.assertTrue(self.srv.app.sample_metrics())          # first live sample at T0
        today, before = iso_day(T0), iso_day(T0 - 86400)
        code, _, raw = self.req("POST", "/api/import", {"tokens": [
            {"day": before, "source": "opencode", "generated": 7, "prompt": 70},
            {"day": today, "source": "opencode", "generated": 1000, "prompt": 1000},
            {"day": iso_day(T0 + 86400), "source": "opencode", "generated": 1000, "prompt": 1000}]})
        self.assertEqual(code, 200, raw)
        self.assertEqual(json.loads(raw)["token_days"], 1)
        self.assertEqual(json.loads(raw)["token_days_ignored"], 2)
        tok = self.status()["tokens"]
        self.assertEqual((tok["generated_total"], tok["prompt_total"]), (107, 1070))
        self.assertEqual(tok["gen_tps"], 50.0)                   # live only: 100 tokens / 2 s
        # the first-sample day is remembered after the samples are pruned (restart 40 days on)
        self.clock.t += 40 * 86400
        self.stop()
        srv = app.make_server("127.0.0.1", 0, db_path=self.db, token=TOKEN, llama_url=self.fake.url,
                              clock=self.clock)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.servers.append(srv)
        self.base = f"http://127.0.0.1:{srv.server_address[1]}"
        code, _, raw = self.req("POST", "/api/import", {"tokens": [
            {"day": today, "source": "opencode", "generated": 1, "prompt": 1}]})
        self.assertEqual(json.loads(raw)["token_days_ignored"], 1)


# --------------------------------------------------------------------- §8 repo list
class ReposTest(LlamaBase):
    owners = ["cecedabro"]

    def ping(self, worker: str, repos: object, path: str = "/api/workers/ping") -> None:
        code, _, raw = self.req("POST", path, {"worker": worker, "paused": False, "repos": repos})
        self.assertEqual(code, 200, raw)

    def test_union_filtered_and_sorted(self) -> None:
        self.assertEqual(self.status()["jobs"]["repos"], [])
        self.ping("devbox-0", ["cecedabro/woodshed", "cecedabro/Scrum-agent", "someone/else", "bad", 7,
                               "cecedabro/woodshed", "a/b/c"])
        self.ping("devbox-1", ["cecedabro/alpha"], path="/api/jobs/claim")
        self.req("POST", "/api/jobs", {"repo": "cecedabro/zeta"})
        self.assertEqual(self.status()["jobs"]["repos"],
                         ["cecedabro/alpha", "cecedabro/Scrum-agent", "cecedabro/woodshed", "cecedabro/zeta"])

    def test_empty_list_keeps_previous_and_workers_expire(self) -> None:
        self.ping("devbox-0", ["cecedabro/woodshed"])
        self.ping("devbox-0", [])
        self.req("POST", "/api/workers/ping", {"worker": "devbox-0", "paused": True})
        self.assertEqual(self.status()["jobs"]["repos"], ["cecedabro/woodshed"])
        self.clock.t += 601
        self.assertEqual(self.status()["jobs"]["repos"], [])

    def test_page_select(self) -> None:
        page = self.req("GET", "/", token=None)[2].decode()
        self.assertTrue('<input name="repo"' in page, "no repos known: expected <input name=\"repo\">")
        self.assertFalse('<select name="repo"' in page, "no repos known: expected no <select>")
        self.ping("devbox-0", ["cecedabro/woodshed", "cecedabro/alpha"])
        page = self.req("GET", "/", token=None)[2].decode()
        self.assertTrue('<select name="repo"' in page, "repos known: expected <select name=\"repo\">")
        self.assertFalse('<input name="repo"' in page, "repos known: expected no <input name=\"repo\">")
        a = page.find('<option value="cecedabro/alpha">cecedabro/alpha</option>')
        w = page.find('<option value="cecedabro/woodshed">cecedabro/woodshed</option>')
        self.assertTrue(0 <= a < w, "expected the two <option>s, alpha before woodshed")


class ReposNoOwnersTest(Base):
    def test_without_job_owners_all_owners_kept(self) -> None:
        self.req("POST", "/api/workers/ping", {"worker": "w", "paused": False, "repos": ["x/y", "cecedabro/a"]})
        self.assertEqual(self.status()["jobs"]["repos"], ["cecedabro/a", "x/y"])


# --------------------------------------------------------------------- §11 n_ctx
class NctxTest(LlamaBase):
    def test_from_props_cached_and_kept(self) -> None:
        gw = self.status()["gateway"]
        self.assertEqual(gw["n_ctx"], 49152)
        self.assertEqual(self.fake.auth, "Bearer llk")
        calls = self.fake.props_calls
        self.fake.broken = True
        self.clock.t += 100
        self.assertEqual(self.status()["gateway"]["n_ctx"], 49152)
        self.assertEqual(self.fake.props_calls, calls)              # known: not asked again

    def test_retry_while_unknown(self) -> None:
        self.fake.broken = True
        self.assertIsNone(self.status()["gateway"]["n_ctx"])
        self.status()
        self.assertEqual(self.fake.props_calls, 1)                 # once per probe_interval
        self.fake.broken = False
        self.clock.t += 31
        self.assertEqual(self.status()["gateway"]["n_ctx"], 49152)


class NctxOffTest(Base):
    def test_null_without_gateway(self) -> None:
        self.assertIsNone(self.status()["gateway"]["n_ctx"])


# --------------------------------------------------------------------- §12.2 backfill.py
def load_backfill():
    import importlib
    return importlib.import_module("backfill")


def sh(cwd: Path, *args: str, env: dict | None = None) -> str:
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True,
                          env={**os.environ, **(env or {})}).stdout


def commit(repo: Path, author: str, subject: str, date: str, files: dict[str, str]) -> str:
    for name, text in files.items():
        (repo / name).write_text(text)
    sh(repo, "git", "add", "-A")
    env = {"GIT_AUTHOR_NAME": author, "GIT_AUTHOR_EMAIL": "a@example.invalid", "GIT_AUTHOR_DATE": date,
           "GIT_COMMITTER_NAME": author, "GIT_COMMITTER_EMAIL": "a@example.invalid", "GIT_COMMITTER_DATE": date}
    sh(repo, "git", "commit", "-q", "--allow-empty", "-m", subject, env=env)
    return sh(repo, "git", "rev-parse", "HEAD").strip()


class BackfillGitTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "woodshed"
        self.repo.mkdir()
        sh(self.repo, "git", "init", "-q", "-b", "main")
        self.human = commit(self.repo, "Cedric", "Initial", "2026-11-01T08:00:00+01:00", {"a.txt": "x\n"})
        self.t1 = commit(self.repo, "local-agent", "T01: Add the thing " + "x" * 300,
                         "2026-11-02T10:00:00+01:00", {"b.txt": "1\n2\n3\n"})
        self.t2 = commit(self.repo, "local-agent", "T02: Remove a line",
                         "2026-11-02T11:00:00+00:00", {"b.txt": "1\n2\n", "c.txt": "z\n"})
        self.rep = commit(self.repo, "local-agent", "agent run report", "2026-11-02T12:00:00+00:00",
                          {"PROGRESS.md": "done\n"})
        self.fix = commit(self.repo, "local-agent", "Fix the build by hand", "2026-11-02T12:30:00+00:00",
                          {"d.txt": "d\n"})
        sh(self.repo, "git", "checkout", "-q", "-b", "agent/side")
        self.side = commit(self.repo, "local-agent", "T03: On another branch", "2026-11-03T09:00:00+00:00",
                           {"e.txt": "e\n"})
        sh(self.repo, "git", "checkout", "-q", "main")
        logs = self.repo / ".agent" / "logs"
        logs.mkdir(parents=True)
        for n in ("T02-a1.log", "T02-a2.log", "T01-a1.log", "T10-a5.log"):
            (logs / n).write_text("log")
        old = self.repo / ".agent" / "logs-run1"
        old.mkdir()
        (old / "T02-a3.log").write_text("log")
        self.gh_calls: list[list[str]] = []

    def gh(self, args: list[str], cwd: Path) -> str:
        self.gh_calls.append(args)
        return json.dumps([
            {"number": 4, "headRefName": "agent/2026-11-02-1000", "createdAt": "2026-11-02T13:00:00Z",
             "url": "https://github.com/cecedabro/woodshed/pull/4"},
            {"number": 5, "headRefName": "release-deploy", "createdAt": "2026-11-02T14:00:00Z",
             "url": "https://github.com/cecedabro/woodshed/pull/5"}])

    def test_git_events(self) -> None:
        bf = load_backfill()
        evs = bf.git_events(self.repo, "woodshed", gh=self.gh)
        self.assertEqual(self.gh_calls, [["pr", "list", "--state", "all", "--limit", "500",
                                          "--json", "number,headRefName,createdAt,url"]])
        self.assertEqual([e["key"] for e in evs], [
            f"woodshed:{self.t1}", f"woodshed:{self.t2}", f"woodshed:run:{self.rep}", "woodshed:pr:4",
            f"woodshed:{self.side}"])
        t1, t2, run, pr, side = evs
        self.assertEqual(t1["kind"], "task_done")
        self.assertEqual(t1["repo"], "woodshed")
        self.assertEqual(t1["ts"], "2026-11-02T09:00:00+00:00")
        self.assertEqual((t1["task_id"], t1["commit"]), ("T01", self.t1[:7]))
        self.assertEqual(len(t1["title"]), 200)
        self.assertTrue(t1["title"].startswith("Add the thing x"))
        self.assertEqual((t1["insertions"], t1["deletions"], t1["attempts"]), (3, 0, 1))
        self.assertEqual((t2["insertions"], t2["deletions"], t2["attempts"]), (1, 1, 3))
        self.assertEqual((side["task_id"], side["attempts"]), ("T03", 1))
        self.assertEqual((run["kind"], run["ts"]), ("run_started", "2026-11-02T12:00:00+00:00"))
        self.assertEqual((pr["kind"], pr["pr"]), ("run_finished", "https://github.com/cecedabro/woodshed/pull/4"))
        self.assertEqual(pr["ts"], "2026-11-02T13:00:00+00:00")

    def test_gh_failure_means_no_prs(self) -> None:
        bf = load_backfill()
        evs = bf.git_events(self.repo, "woodshed", gh=lambda args, cwd: "[]")
        self.assertEqual([e["kind"] for e in evs], ["task_done", "task_done", "run_started", "task_done"])


class BackfillOpencodeTest(unittest.TestCase):
    def test_tokens_per_day(self) -> None:
        bf = load_backfill()
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "opencode.db"
            con = sqlite3.connect(db)
            con.execute("create table message(id text, session_id text, time_created int, time_updated int, data text)")

            def ms(s: str) -> int:
                return int(dt.datetime.fromisoformat(s).timestamp() * 1000)

            rows = [
                {"role": "assistant", "time": {"created": ms("2026-11-02T23:30:00+00:00")},
                 "tokens": {"input": 1000, "output": 10, "reasoning": 5, "cache": {"read": 99}}},
                {"role": "assistant", "time": {"created": ms("2026-11-02T01:00:00+00:00")},
                 "tokens": {"input": 500, "output": 20}},
                {"role": "assistant", "time": {"created": ms("2026-11-03T00:30:00+01:00")},   # = 2 Nov UTC
                 "tokens": {"input": 1, "output": 1, "reasoning": 0}},
                {"role": "assistant", "time": {"created": ms("2026-11-04T10:00:00+00:00")},
                 "tokens": {"input": 7, "output": 3}},
                {"role": "user", "time": {"created": ms("2026-11-04T10:00:00+00:00")},
                 "tokens": {"input": 999, "output": 999}},
                {"role": "assistant", "time": {"created": ms("2026-11-04T10:00:00+00:00")}},
            ]
            for i, r in enumerate(rows):
                con.execute("insert into message values (?,?,?,?,?)", (str(i), "s", 0, 0, json.dumps(r)))
            con.execute("insert into message values ('bad','s',0,0,'{not json')")
            con.commit()
            con.close()
            self.assertEqual(bf.opencode_tokens(db), [
                {"day": "2026-11-02", "source": "opencode", "generated": 36, "prompt": 1501},
                {"day": "2026-11-04", "source": "opencode", "generated": 3, "prompt": 7}])


class BackfillSendTest(Base):
    def test_send_batches_and_counts_once(self) -> None:
        bf = load_backfill()
        events = [{**EV, "key": f"k{i}"} for i in range(2500)]
        tokens = [{"day": "2026-11-01", "source": "opencode", "generated": 5, "prompt": 6}]
        answers = bf.send(self.base, TOKEN, events, tokens)
        self.assertEqual([a["events_added"] for a in answers], [1000, 1000, 500])
        self.assertEqual([a["token_days"] for a in answers], [1, 0, 0])
        again = bf.send(self.base, TOKEN, events, tokens)
        self.assertEqual(sum(a["events_skipped"] for a in again), 2500)
        st = self.status()
        self.assertEqual(st["totals"]["commits"], 2500)
        self.assertEqual(st["tokens"]["generated_total"], 5)
        with self.assertRaises(Exception):
            bf.send(self.base, "wrong", events[:1], [])

    def test_cli_dry_run_and_missing_env(self) -> None:
        script = Path(__file__).resolve().parents[1] / "backfill.py"
        with tempfile.TemporaryDirectory() as d:
            env = {k: v for k, v in os.environ.items() if k not in ("STATUS_URL", "STATUS_TOKEN")}
            args = [sys.executable, str(script), "--repos", d, "--opencode-db", str(Path(d) / "none.db")]
            res = subprocess.run(args, capture_output=True, text=True, env=env, timeout=60)
            self.assertEqual(res.returncode, 2, res.stderr)
            res = subprocess.run([*args, "--dry-run"], capture_output=True, text=True, env=env, timeout=60)
            self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self.status()["totals"]["commits"], 0)


if __name__ == "__main__":
    unittest.main()
