#!/usr/bin/env python3
"""agent-status: what the local coding agent is doing, its job queue, and the model lease.

See status/SPEC.md. One file, Python 3.11 standard library only. Run: python3 status/app.py
"""
from __future__ import annotations

import datetime as dt
import hmac
import html
import json
import os
import re
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

KINDS = {"run_started", "task_started", "task_done", "task_skipped", "push_failed", "ci_waiting", "ci",
         "run_finished"}
MAX_BODY = 256 * 1024
RETENTION = 30 * 86400
STALE_AFTER = 3600
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
REF_RE = re.compile(r"^[A-Za-z0-9._/-]{1,100}$")
JOB_ACTIONS = {"heartbeat", "finish", "cancel", "move", "requeue"}
METRICS = {  # llama-server /metrics (needs --metrics) -> our names
    "llamacpp:tokens_predicted_total": "gen",
    "llamacpp:tokens_predicted_seconds_total": "gen_s",
    "llamacpp:prompt_tokens_total": "prompt",
    "llamacpp:prompt_seconds_total": "prompt_s",
}

SCHEMA = """
create table if not exists events(id integer primary key, received real, run_id text, kind text, body text);
create index if not exists events_received on events(received);
create table if not exists tally(repo text primary key, commits int default 0, skipped int default 0,
  added int default 0, removed int default 0, runs int default 0, prs int default 0,
  push_failed int default 0, seconds real default 0, first_try int default 0, last real);
create table if not exists daily(day text primary key, commits int default 0, skipped int default 0,
  added int default 0, removed int default 0, gen int default 0, prompt int default 0);
create table if not exists samples(t real, gen int, gen_s real, prompt int, prompt_s real, busy int);
create table if not exists meta(key text primary key, value text);
create table if not exists jobs(id integer primary key, repo text, ref text, note text, max_hours real,
  ci int, state text, pos real, created real, started real, finished real, worker text, seen real,
  cancel int default 0, detail text, exit_code int);
"""


def iso(t: float | None) -> str | None:
    return None if t is None else dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat(timespec="seconds")


def day_of(t: float) -> str:
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%d")


def num(v: object, default: float = 0) -> float:
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else default


class App:
    def __init__(self, *, db_path: str, token: str, llama_url: str | None, llama_key: str | None,
                 lease_ttl: float, probe_interval: float, job_ttl: float, clock: Callable[[], float]) -> None:
        self.token, self.lease_ttl, self.probe_interval, self.job_ttl = token, lease_ttl, probe_interval, job_ttl
        self.clock, self.llama_key = clock, llama_key
        self.root = None
        if llama_url:
            root = llama_url.rstrip("/")
            self.root = root[:-3] if root.endswith("/v1") else root
        self.lock = threading.Lock()
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.executescript(SCHEMA)
        cutoff = clock() - RETENTION
        self.db.execute("delete from events where received < ?", (cutoff,))
        self.db.execute("delete from samples where t < ?", (cutoff,))
        self.db.commit()
        self.holder: dict | None = None
        self.waiters: list[dict] = []
        self.probe = {"t": None, "state": "unknown"}
        self.metrics_error: str | None = None

    # ------------------------------------------------------------------ helpers
    def meta(self, key: str, default: object = None) -> object:
        row = self.db.execute("select value from meta where key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_meta(self, key: str, value: object) -> None:
        self.db.execute("insert or replace into meta(key, value) values (?, ?)", (key, json.dumps(value)))

    def authorized(self, header: str) -> bool:
        return bool(self.token) and hmac.compare_digest(header.encode(), f"Bearer {self.token}".encode())

    # ------------------------------------------------------------------ events
    def add_event(self, body: dict) -> None:
        now = self.clock()
        repo, kind = body["repo"], body["kind"]
        with self.lock:
            self.db.execute("insert into events(received, run_id, kind, body) values (?,?,?,?)",
                            (now, body["run_id"], kind, json.dumps(body)))
            self.db.execute("insert or ignore into tally(repo) values (?)", (repo,))
            self.db.execute("update tally set last=? where repo=?", (now, repo))
            day = day_of(now)
            self.db.execute("insert or ignore into daily(day) values (?)", (day,))
            if kind == "task_done":
                add, rem = int(num(body.get("insertions"))), int(num(body.get("deletions")))
                self.db.execute("update tally set commits=commits+1, added=added+?, removed=removed+?, "
                                "seconds=seconds+?, first_try=first_try+? where repo=?",
                                (add, rem, num(body.get("seconds")), int(body.get("attempts") == 1), repo))
                self.db.execute("update daily set commits=commits+1, added=added+?, removed=removed+? "
                                "where day=?", (add, rem, day))
            elif kind == "task_skipped":
                self.db.execute("update tally set skipped=skipped+1, seconds=seconds+? where repo=?",
                                (num(body.get("seconds")), repo))
                self.db.execute("update daily set skipped=skipped+1 where day=?", (day,))
            elif kind == "run_started":
                self.db.execute("update tally set runs=runs+1 where repo=?", (repo,))
            elif kind == "run_finished" and body.get("pr"):
                self.db.execute("update tally set prs=prs+1 where repo=?", (repo,))
            elif kind == "push_failed":
                self.db.execute("update tally set push_failed=push_failed+1 where repo=?", (repo,))
            if self.meta("since") is None:
                self.set_meta("since", now)
            self.db.commit()

    # ------------------------------------------------------------------ lease
    @staticmethod
    def pub(e: dict | None) -> dict | None:
        return None if e is None else {k: e[k] for k in ("run_id", "repo", "branch", "since")}

    def expire_lease(self) -> None:
        now = self.clock()
        if self.holder and now - self.holder["seen"] > self.lease_ttl:
            self.holder = None
        self.waiters = [w for w in self.waiters if now - w["seen"] <= self.lease_ttl]

    def lease(self, body: dict) -> dict:
        act, rid = body["action"], body["run_id"]
        with self.lock:
            self.expire_lease()
            now = self.clock()
            if act == "release":
                if self.holder and self.holder["run_id"] == rid:
                    self.holder = None
                self.waiters = [w for w in self.waiters if w["run_id"] != rid]
                return {"granted": False, "position": 0, "holder": self.pub(self.holder)}
            if self.holder and self.holder["run_id"] == rid:
                self.holder["seen"] = now
                return {"granted": True, "position": 0, "holder": self.pub(self.holder)}
            me = next((w for w in self.waiters if w["run_id"] == rid), None)
            if me is None:
                me = {"run_id": rid, "repo": body["repo"], "branch": body.get("branch") or "",
                      "since": iso(now), "seen": now}
            me["seen"] = now
            if self.holder is None and (not self.waiters or self.waiters[0]["run_id"] == rid):
                self.waiters = [w for w in self.waiters if w["run_id"] != rid]
                self.holder = me
                return {"granted": True, "position": 0, "holder": self.pub(me)}
            if me not in self.waiters:
                self.waiters.append(me)
            return {"granted": False, "position": self.waiters.index(me) + 1, "holder": self.pub(self.holder)}

    # ------------------------------------------------------------------ jobs
    JOB_COLS = ("id", "repo", "ref", "note", "max_hours", "ci", "state", "pos", "created", "started",
                "finished", "worker", "seen", "cancel", "detail", "exit_code")

    def job_row(self, job_id: int) -> dict | None:
        row = self.db.execute(f"select {','.join(self.JOB_COLS)} from jobs where id=?", (job_id,)).fetchone()
        return dict(zip(self.JOB_COLS, row)) if row else None

    def job_pub(self, j: dict) -> dict:
        return {"id": j["id"], "repo": j["repo"], "ref": j["ref"] or "", "note": j["note"] or "",
                "max_hours": j["max_hours"], "ci": bool(j["ci"]), "state": j["state"],
                "created": iso(j["created"]), "started": iso(j["started"]), "finished": iso(j["finished"]),
                "worker": j["worker"], "stopping": bool(j["cancel"]) and j["state"] == "running",
                "detail": j["detail"] or "", "exit_code": j["exit_code"]}

    def expire_jobs(self) -> None:
        now = self.clock()
        self.db.execute("update jobs set state='lost', finished=?, detail='worker stopped sending heartbeats' "
                        "where state='running' and seen < ?", (now, now - self.job_ttl))

    def renumber(self, ids: list[int]) -> None:
        for i, jid in enumerate(ids, 1):
            self.db.execute("update jobs set pos=? where id=?", (i, jid))

    def queued_ids(self) -> list[int]:
        return [r[0] for r in self.db.execute("select id from jobs where state='queued' order by pos, id")]

    def add_job(self, body: dict) -> tuple[int, dict]:
        repo = body.get("repo")
        if not isinstance(repo, str) or not REPO_RE.match(repo.strip()):
            return 400, {"error": "repo must look like owner/name"}
        ref = body.get("ref") or ""
        if not isinstance(ref, str) or (ref and not REF_RE.match(ref)):
            return 400, {"error": "bad ref"}
        note = body.get("note") or ""
        if not isinstance(note, str) or len(note) > 200:
            return 400, {"error": "note must be a string of at most 200 characters"}
        hours = body.get("max_hours", 24)
        if not isinstance(hours, (int, float)) or isinstance(hours, bool) or not 0.1 <= hours <= 24:
            return 400, {"error": "max_hours must be between 0.1 and 24"}
        ci = body.get("ci", True)
        if not isinstance(ci, bool):
            return 400, {"error": "ci must be true or false"}
        with self.lock:
            pos = (self.db.execute("select max(pos) from jobs where state='queued'").fetchone()[0] or 0) + 1
            cur = self.db.execute("insert into jobs(repo, ref, note, max_hours, ci, state, pos, created) "
                                  "values (?,?,?,?,?, 'queued', ?, ?)",
                                  (repo.strip(), ref, note, float(hours), int(ci), pos, self.clock()))
            self.db.commit()
            return 201, self.job_pub(self.job_row(cur.lastrowid))

    def claim(self, body: dict) -> dict:
        worker = str(body.get("worker") or "worker")[:100]
        with self.lock:
            self.expire_jobs()
            if self.db.execute("select 1 from jobs where state='running'").fetchone():
                self.db.commit()
                return {"job": None}                      # one GPU: one job at a time
            ids = self.queued_ids()
            if not ids:
                self.db.commit()
                return {"job": None}
            now = self.clock()
            self.db.execute("update jobs set state='running', started=?, seen=?, worker=? where id=?",
                            (now, now, worker, ids[0]))
            self.renumber(ids[1:])
            self.db.commit()
            return {"job": self.job_pub(self.job_row(ids[0]))}

    def job_action(self, job_id: int, act: str, body: dict) -> tuple[int, dict]:
        with self.lock:
            self.expire_jobs()
            j = self.job_row(job_id)
            if j is None:
                return 404, {"error": "no such job"}
            now, st = self.clock(), j["state"]
            if act == "heartbeat":
                if st in ("running", "lost"):
                    self.db.execute("update jobs set state='running', seen=?, finished=null, "
                                    "detail=case when state='lost' then '' else detail end where id=?",
                                    (now, job_id))
                    out = {"cancel": bool(j["cancel"])}
                else:
                    out = {"cancel": True}                # cancelled or finished meanwhile
            elif act == "finish":
                state = body.get("state")
                if state not in ("done", "failed", "cancelled"):
                    return 400, {"error": "state must be done, failed or cancelled"}
                if st not in ("running", "lost"):
                    return 409, {"error": f"job is {st}"}
                code = body.get("exit_code")
                self.db.execute("update jobs set state=?, finished=?, detail=?, exit_code=? where id=?",
                                (state, now, str(body.get("detail") or "")[:2000],
                                 code if isinstance(code, int) else None, job_id))
                out = {"ok": True}
            elif act == "cancel":
                if st == "queued":
                    self.db.execute("update jobs set state='cancelled', finished=?, detail='removed from the "
                                    "queue' where id=?", (now, job_id))
                    self.renumber([i for i in self.queued_ids() if i != job_id])
                elif st == "running":
                    self.db.execute("update jobs set cancel=1 where id=?", (job_id,))
                else:
                    return 409, {"error": f"job is {st}"}
                out = {"ok": True}
            elif act == "move":
                to = body.get("to")
                ids = self.queued_ids()
                if st != "queued" or to not in ("top", "up", "down", "bottom"):
                    return 400 if to not in ("top", "up", "down", "bottom") else 409, {"error": "cannot move"}
                i = ids.index(job_id)
                ids.pop(i)
                j_new = {"top": 0, "up": max(i - 1, 0), "down": min(i + 1, len(ids)), "bottom": len(ids)}[to]
                ids.insert(j_new, job_id)
                self.renumber(ids)
                out = {"ok": True, "position": j_new + 1}
            else:  # requeue
                if st in ("queued", "running"):
                    return 409, {"error": f"job is {st}"}
                pos = (self.db.execute("select max(pos) from jobs where state='queued'").fetchone()[0] or 0) + 1
                cur = self.db.execute("insert into jobs(repo, ref, note, max_hours, ci, state, pos, created) "
                                      "values (?,?,?,?,?, 'queued', ?, ?)",
                                      (j["repo"], j["ref"], j["note"], j["max_hours"], j["ci"], pos, now))
                out = {"ok": True, "id": cur.lastrowid}
            self.db.commit()
            return 200, out

    # ------------------------------------------------------------------ model + metrics
    def model(self) -> dict:
        if not self.root:
            return {"state": "unknown", "checked": None}
        now = self.clock()
        if self.probe["t"] is None or now - self.probe["t"] >= self.probe_interval:
            try:
                with urllib.request.urlopen(self.root + "/health", timeout=2) as r:
                    code = r.status
            except urllib.error.HTTPError as exc:
                code = exc.code
            except (OSError, ValueError):
                code = 0
            self.probe = {"t": now, "state": {200: "ready", 503: "loading"}.get(code, "unreachable")}
        return {"state": self.probe["state"], "checked": iso(self.probe["t"])}

    def sample_metrics(self) -> bool:
        """Read llama-server's /metrics once and add the token counts since the last sample.
        Counters reset when llama-server restarts; a smaller value then counts from zero."""
        if not self.root:
            return False
        req = urllib.request.Request(self.root + "/metrics")
        if self.llama_key:
            req.add_header("Authorization", f"Bearer {self.llama_key}")
        try:
            with urllib.request.urlopen(req, timeout=3) as r:
                text = r.read().decode(errors="replace")
        except urllib.error.HTTPError as exc:
            self.metrics_error = {501: "llama-server runs without --metrics", 401: "LLAMA_API_KEY missing or wrong",
                                  404: "llama-server has no /metrics"}.get(exc.code, f"/metrics answered {exc.code}")
            return False
        except (OSError, ValueError):
            self.metrics_error = "llama-server unreachable (PC asleep?)"
            return False
        vals: dict[str, float] = {}
        busy = 0
        for line in text.splitlines():
            if not line or line.startswith("#"):
                continue
            name, _, value = line.rpartition(" ")
            name = name.split("{")[0].strip()
            try:
                v = float(value)
            except ValueError:
                continue
            if name in METRICS:
                vals[METRICS[name]] = v
            elif name == "llamacpp:requests_processing":
                busy = int(v)
        if set(vals) != set(METRICS.values()):
            self.metrics_error = "llama-server /metrics lacks token counters"
            return False
        self.metrics_error = None
        now = self.clock()
        with self.lock:
            last = self.meta("raw") or {}
            d = {k: (v - last[k] if k in last and v >= last[k] else v) for k, v in vals.items()}
            self.db.execute("insert into samples values (?,?,?,?,?,?)",
                            (now, int(d["gen"]), d["gen_s"], int(d["prompt"]), d["prompt_s"], busy))
            tot = self.meta("tokens") or {"gen": 0, "gen_s": 0, "prompt": 0, "prompt_s": 0}
            self.set_meta("tokens", {k: tot.get(k, 0) + d[k] for k in d})
            self.set_meta("raw", vals)
            self.set_meta("busy", busy)
            day = day_of(now)
            self.db.execute("insert or ignore into daily(day) values (?)", (day,))
            self.db.execute("update daily set gen=gen+?, prompt=prompt+? where day=?",
                            (int(d["gen"]), int(d["prompt"]), day))
            self.db.commit()
        return True

    def tokens(self, now: float) -> dict:
        tot = self.meta("tokens")
        rows = self.db.execute("select t, gen, gen_s, prompt, prompt_s, busy from samples where t > ? order by t",
                               (now - 86400,)).fetchall()
        last_t = rows[-1][0] if rows else None

        def rate(tokens: float, secs: float) -> float | None:
            return round(tokens / secs, 1) if secs > 0 else None

        recent = [r for r in rows if r[0] > now - 900]
        hourly = []
        start = int(now // 3600) * 3600 - 23 * 3600
        for h in range(24):
            lo = start + h * 3600
            sel = [r for r in rows if lo <= r[0] < lo + 3600]
            g, gs = sum(r[1] for r in sel), sum(r[2] for r in sel)
            hourly.append({"hour": iso(lo), "generated": g, "gen_tps": rate(g, gs)})
        return {
            "available": tot is not None,
            "error": self.metrics_error,
            "last_sample": iso(last_t),
            "busy": bool(rows[-1][5]) if rows and now - rows[-1][0] < 300 else None,
            "generated_total": int(tot["gen"]) if tot else 0,
            "prompt_total": int(tot["prompt"]) if tot else 0,
            "gen_tps": rate(tot["gen"], tot["gen_s"]) if tot else None,
            "prompt_tps": rate(tot["prompt"], tot["prompt_s"]) if tot else None,
            "gen_tps_recent": rate(sum(r[1] for r in recent), sum(r[2] for r in recent)),
            "prompt_tps_recent": rate(sum(r[3] for r in recent), sum(r[4] for r in recent)),
            "generated_24h": sum(r[1] for r in rows),
            "hourly": hourly,
        }

    # ------------------------------------------------------------------ status
    def status(self) -> dict:
        model = self.model()                              # network: outside the lock
        with self.lock:
            self.expire_lease()
            self.expire_jobs()
            self.db.commit()
            now = self.clock()
            holder, queue = self.pub(self.holder), [self.pub(w) for w in self.waiters]
            holder_run = self.holder["run_id"] if self.holder else None
            latest = self.db.execute("select run_id, max(received) m from events group by run_id "
                                     "order by m desc limit 10").fetchall()
            ids = [r[0] for r in latest]
            run_rows = self.db.execute(
                f"select received, body from events where run_id in ({','.join('?' * len(ids))}) "
                "order by received, id", ids).fetchall() if ids else []
            ev_rows = self.db.execute("select received, body from events order by received desc, id desc "
                                      "limit 50").fetchall()
            done = [json.loads(b) for (b,) in self.db.execute("select body from events where kind='task_done'")]
            skipped = self.db.execute("select count(*) from events where kind='task_skipped'").fetchone()[0]
            recent = self.db.execute("select count(*) from events where kind='task_done' and received >= ?",
                                     (now - 86400,)).fetchone()[0]
            tally = self.db.execute("select repo, commits, skipped, added, removed, runs, prs, push_failed, "
                                    "seconds, first_try, last from tally order by last desc").fetchall()
            days = {r[0]: r[1:] for r in self.db.execute(
                "select day, commits, skipped, added, removed, gen from daily where day >= ?",
                (day_of(now - 13 * 86400),))}
            jobs = [dict(zip(self.JOB_COLS, r)) for r in self.db.execute(
                f"select {','.join(self.JOB_COLS)} from jobs where state in ('queued','running') "
                "or finished >= ? order by id desc", (now - 7 * 86400,))]
            tokens = self.tokens(now)
            since = self.meta("since")

        runs: dict[str, dict] = {}
        for received, b in run_rows:
            e = json.loads(b)
            run = runs.setdefault(e["run_id"], {"run_id": e["run_id"], "repo": e["repo"], "branch": "",
                                                "first": received, "current": None, "done": 0, "skipped": 0,
                                                "plan": [], "fin": None, "job_id": None, "max_hours": None})
            run["last"] = received
            if e.get("branch"):
                run["branch"] = e["branch"]
            if isinstance(e.get("plan"), list):
                run["plan"] = e["plan"]
            if e.get("job_id"):
                run["job_id"] = str(e["job_id"])
            kind = e["kind"]
            if kind == "run_started":
                run["max_hours"] = e.get("max_hours")
            elif kind == "task_started":
                run["current"] = {"task_id": e.get("task_id"), "title": e.get("title"), "attempt": e.get("attempt")}
                run["task_since"], run["retries"] = iso(received), e.get("retries")
            elif kind in ("task_done", "task_skipped"):
                run["done" if kind == "task_done" else "skipped"] += 1
                if run["current"] and run["current"]["task_id"] == e.get("task_id"):
                    run["current"] = None
            elif kind == "run_finished":
                run["fin"] = e
        out_runs = []
        for run in sorted(runs.values(), key=lambda r: r["last"], reverse=True):
            fin = run["fin"]
            state = "finished" if fin else (
                "running" if now - run["last"] <= STALE_AFTER or run["run_id"] == holder_run else "stale")
            out_runs.append({
                "run_id": run["run_id"], "repo": run["repo"], "branch": run["branch"], "state": state,
                "started": iso(run["first"]), "last_event": iso(run["last"]),
                "current": None if fin else run["current"], "done": run["done"], "skipped": run["skipped"],
                "plan": run["plan"], "ended": fin.get("ended") if fin else None,
                "pushed": fin.get("pushed") if fin else None, "pr": fin.get("pr") if fin else None,
                "job_id": run["job_id"], "max_hours": run["max_hours"],
                "task_since": None if fin or not run["current"] else run.get("task_since"),
                "retries": run.get("retries")})

        n = len(done)
        stats = {"tasks_done": n, "tasks_skipped": skipped,
                 "first_try_rate": (sum(1 for e in done if e.get("attempts") == 1) / n) if n else None,
                 "avg_task_minutes": round(sum(num(e.get("seconds")) for e in done) / n / 60, 1) if n else None,
                 "tasks_per_hour_24h": round(recent / 24, 3)}

        repos = [{"repo": r[0], "commits": r[1], "skipped": r[2], "lines_added": r[3], "lines_removed": r[4],
                  "runs": r[5], "prs": r[6], "push_failed": r[7], "agent_hours": round(r[8] / 3600, 1),
                  "last": iso(r[10])} for r in tally]
        commits = sum(r[1] for r in tally)
        totals = {"since": iso(since), "commits": commits, "tasks_skipped": sum(r[2] for r in tally),
                  "lines_added": sum(r[3] for r in tally), "lines_removed": sum(r[4] for r in tally),
                  "runs": sum(r[5] for r in tally), "prs": sum(r[6] for r in tally),
                  "push_failures": sum(r[7] for r in tally),
                  "agent_hours": round(sum(r[8] for r in tally) / 3600, 1),
                  "first_try_rate": round(sum(r[9] for r in tally) / commits, 3) if commits else None,
                  "repos": repos}
        daily = []
        for i in range(13, -1, -1):
            day = day_of(now - i * 86400)
            c, s, a, rm, g = days.get(day, (0, 0, 0, 0, 0))
            daily.append({"day": day, "commits": c, "skipped": s, "lines_added": a, "lines_removed": rm,
                          "generated": g})

        run_by_job = {}
        for r in out_runs:
            if r["job_id"] and r["job_id"] not in run_by_job:
                run_by_job[r["job_id"]] = r["run_id"]
        pub_jobs = []
        for j in jobs:
            p = self.job_pub(j)
            p["run_id"] = run_by_job.get(str(j["id"]))
            pub_jobs.append((j, p))
        job_out = {
            "running": [p for j, p in pub_jobs if j["state"] == "running"],
            "queued": [p for j, p in sorted(pub_jobs, key=lambda x: (x[0]["pos"] or 0, x[0]["id"]))
                       if j["state"] == "queued"],
            "recent": [p for j, p in pub_jobs if j["state"] not in ("queued", "running")][:10],
        }
        events = [{**json.loads(b), "received": iso(r)} for r, b in ev_rows]
        return {"now": iso(now), "model": model, "lease": {"holder": holder, "queue": queue},
                "jobs": job_out, "runs": out_runs, "events": events, "stats": stats, "totals": totals,
                "tokens": tokens, "daily": daily}


# ---------------------------------------------------------------------------- page

def ago(then: str | None, now: str) -> str:
    if not then:
        return ""
    secs = (dt.datetime.fromisoformat(now) - dt.datetime.fromisoformat(then)).total_seconds()
    if secs < 90:
        return f"{max(int(secs), 0)} s ago"
    if secs < 5400:
        return f"{int(secs // 60)} min ago"
    if secs < 172800:
        return f"{secs / 3600:.1f} h ago"
    return f"{int(secs // 86400)} d ago"


def dur(then: str | None, now: str) -> str:
    if not then:
        return ""
    m = int((dt.datetime.fromisoformat(now) - dt.datetime.fromisoformat(then)).total_seconds() // 60)
    return f"{m} min" if m < 120 else f"{m // 60} h {m % 60:02d} min"


def big(n: float | None) -> str:
    if n is None:
        return "–"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 10_000:
        return f"{n / 1000:.0f}k"
    return f"{n:,.0f}" if float(n).is_integer() else f"{n:,}"


def hhmm(t: str | None) -> str:
    """HH:MM in UTC; the page script rewrites <time> elements to the viewer's time zone."""
    return t[11:16] if t else ""


def clock_tag(t: str | None) -> str:
    return f'<time datetime="{html.escape(t)}">{html.escape(hhmm(t))}</time>' if t else ""


def plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def bars(values: list[float], tips: list[str], labels: tuple[str, str], empty: str) -> str:
    """Single-series bar chart as inline SVG; one <title> tooltip per bar."""
    e = html.escape
    w, h, top, base = 480, 120, 8, 100
    n = len(values)
    peak = max(values) if values else 0
    if not peak:
        return f'<p class="muted small">{e(empty)}</p>'
    slot = w / n
    bw = max(slot - 2, 1)                                  # 2 px surface gap between bars
    out = [f'<svg class="chart" viewBox="0 0 {w} {h}" role="img" aria-label="{e(empty)}">',
           f'<line class="axis" x1="0" y1="{base + .5}" x2="{w}" y2="{base + .5}"/>']
    for i, (v, tip) in enumerate(zip(values, tips)):
        x = i * slot + 1
        bh = (base - top) * v / peak
        r = min(4, bw / 2, bh)
        if v > 0:
            y = base - bh
            out.append(f'<g><path class="bar" d="M{x:.1f},{base} V{y + r:.1f} Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} '
                       f'H{x + bw - r:.1f} Q{x + bw:.1f},{y:.1f} {x + bw:.1f},{y + r:.1f} V{base} Z"/>'
                       f'<rect class="hit" x="{i * slot:.1f}" y="0" width="{slot:.1f}" height="{base}"/>'
                       f'<title>{e(tip)}</title></g>')
        else:
            out.append(f'<g><rect class="hit" x="{i * slot:.1f}" y="0" width="{slot:.1f}" height="{base}"/>'
                       f'<title>{e(tip)}</title></g>')
    out.append(f'<text class="tick" x="0" y="{h - 4}">{e(labels[0])}</text>'
               f'<text class="tick" x="{w}" y="{h - 4}" text-anchor="end">{e(labels[1])}</text>'
               f'<text class="tick" x="{w}" y="{top + 4}" text-anchor="end">max {e(big(peak))}</text></svg>')
    return "".join(out)


BADGE = {"ready": "ok", "loading": "warn", "unreachable": "bad", "unknown": "off"}


def render_live(st: dict) -> str:
    e = html.escape
    now = st["now"]
    run = next((r for r in st["runs"] if r["state"] == "running"), None)
    tk = st["tokens"]
    parts: list[str] = []

    # -- Now
    parts.append('<section class="card wide" id="now"><h2>Now</h2>')
    if run:
        c = run["current"] or {}
        plan = run["plan"]
        total = len(plan) or (run["done"] + run["skipped"])
        finished = sum(1 for t in plan if t.get("state") in ("done", "skipped")) if plan else run["done"] + run["skipped"]
        pct = int(100 * finished / total) if total else 0
        parts.append(f'<div class="now-head"><span class="repo">{e(run["repo"])}</span>'
                     f'<code>{e(run["branch"])}</code><span class="muted">running {e(dur(run["started"], now))}'
                     f'{" · job #" + e(run["job_id"]) if run["job_id"] else ""}</span></div>')
        if c:
            att = f'attempt {e(str(c.get("attempt")))}' + (f'/{e(str(run["retries"]))}' if run.get("retries") else "")
            parts.append(f'<p class="task"><b>{e(str(c.get("task_id")))}</b> {e(str(c.get("title")))}</p>'
                         f'<p class="muted small">{att} · for {e(dur(run["task_since"], now))}</p>')
        else:
            parts.append('<p class="muted">Between tasks (checking, pushing or waiting for CI).</p>')
        parts.append(f'<div class="progress" title="{finished} of {total} tasks"><i style="width:{pct}%"></i></div>'
                     f'<p class="muted small">{finished} of {total} plan tasks · {run["done"]} done, '
                     f'{run["skipped"]} skipped this run · last event {e(ago(run["last_event"], now))}</p>')
        if tk.get("busy") is not None:
            gen = '<span class="dot ok"></span> model generating' if tk["busy"] else '<span class="dot off"></span> model idle'
            parts.append(f'<p class="small">{gen}'
                         f'{" · " + e(str(tk["gen_tps_recent"])) + " tok/s" if tk["gen_tps_recent"] else ""}</p>')
    else:
        parts.append('<p class="idle">Idle</p><p class="muted small">No run is active. Add a job below, or start '
                     '<code>agent-loop</code> in a repo on the devbox.</p>')
    parts.append("</section>")

    # -- Queue
    jobs = st["jobs"]
    parts.append('<section class="card wide" id="queue"><h2>Queue</h2><div class="cols"><div>')
    rows = []
    for j in jobs["running"]:
        stop = ('<span class="pill warn">stopping</span>' if j["stopping"] else
                f'<button class="ctl" data-job="{j["id"]}" data-act="cancel" title="Stop after the current task">Stop</button>')
        rows.append(f'<li class="job running"><span class="pos">▶</span><div><b>{e(j["repo"])}</b>'
                    f'{" <code>" + e(j["ref"]) + "</code>" if j["ref"] else ""}'
                    f'<div class="muted small">#{j["id"]} running {e(dur(j["started"], now))} on {e(j["worker"] or "")}'
                    f'{" · " + e(j["note"]) if j["note"] else ""}</div></div>{stop}</li>')
    for i, j in enumerate(jobs["queued"], 1):
        rows.append(f'<li class="job"><span class="pos">{i}</span><div><b>{e(j["repo"])}</b>'
                    f'{" <code>" + e(j["ref"]) + "</code>" if j["ref"] else ""}'
                    f'<div class="muted small">#{j["id"]} · max {e(str(j["max_hours"]).removesuffix(".0"))} h'
                    f'{"" if j["ci"] else " · no CI wait"} · added {e(ago(j["created"], now))}'
                    f'{" · " + e(j["note"]) if j["note"] else ""}</div></div>'
                    f'<span class="ctl btns"><button data-job="{j["id"]}" data-act="move" data-to="up" title="Move up">↑</button>'
                    f'<button data-job="{j["id"]}" data-act="move" data-to="down" title="Move down">↓</button>'
                    f'<button data-job="{j["id"]}" data-act="cancel" title="Remove">✕</button></span></li>')
    parts.append(f'<ol class="jobs">{"".join(rows)}</ol>' if rows else '<p class="muted">No jobs queued.</p>')
    lease = st["lease"]
    if lease["holder"] or lease["queue"]:
        parts.append('<h3>Model lease</h3><ul class="plain small">')
        if lease["holder"]:
            h = lease["holder"]
            parts.append(f'<li><span class="pill ok">using GPU</span> {e(h["repo"])} <code>{e(h["branch"])}</code></li>')
        for i, w in enumerate(lease["queue"], 1):
            parts.append(f'<li><span class="pill off">waiting {i}</span> {e(w["repo"])} <code>{e(w["branch"])}</code> '
                         f'<span class="muted">since {clock_tag(w["since"])}</span></li>')
        parts.append("</ul>")
    if jobs["recent"]:
        parts.append('<details><summary class="small">Recent jobs</summary><ul class="plain small">')
        for j in jobs["recent"]:
            cls = {"done": "ok", "failed": "bad", "lost": "bad", "cancelled": "off"}.get(j["state"], "off")
            parts.append(f'<li><span class="pill {cls}">{e(j["state"])}</span> #{j["id"]} {e(j["repo"])} '
                         f'<span class="muted">{e(ago(j["finished"], now))} {e(j["detail"][:120])}</span> '
                         f'<button class="ctl link" data-job="{j["id"]}" data-act="requeue">requeue</button></li>')
        parts.append("</ul></details>")
    parts.append("</div><div>")
    if run:
        todo = [t for t in run["plan"] if t.get("state") == "todo"]
        if todo:
            parts.append(f'<h3>Next in {e(run["repo"])} ({len(todo)} tasks)</h3><ol class="plan small">')
            parts += [f'<li><b>{e(str(t.get("id")))}</b> {e(str(t.get("title")))}</li>' for t in todo[:12]]
            if len(todo) > 12:
                parts.append(f'<li class="muted">… and {len(todo) - 12} more</li>')
            parts.append("</ol>")
    else:
        parts.append('<h3>Plan</h3><p class="muted small">The running repo\'s open tasks show here.</p>')
    parts.append("</div></div></section>")

    # -- All time
    t = st["totals"]
    s = st["stats"]
    rate = t["first_try_rate"]
    tiles = [("Commits", big(t["commits"]), plural(t["tasks_skipped"], "task") + " skipped"),
             ("Lines", f'+{big(t["lines_added"])}', f'−{big(t["lines_removed"])} removed'),
             ("Tokens generated", big(tk["generated_total"]), f'{big(tk["prompt_total"])} read'),
             ("PRs opened", big(t["prs"]), plural(t["runs"], "run")),
             ("Agent time", f'{t["agent_hours"]} h', f'avg {s["avg_task_minutes"] or "–"} min per task (30 d)'),
             ("First try", f'{round(rate * 100)}%' if rate is not None else "–", "tasks done on attempt 1")]
    since_tag = f' <span class="muted small">since {e(t["since"][:10])}</span>' if t["since"] else ""
    parts.append(f'<section class="card wide" id="stats"><h2>All time'
                 f'{since_tag}</h2>'
                 '<div class="tiles">')
    parts += [f'<div class="tile"><div class="label">{e(a)}</div><div class="value">{e(b)}</div>'
              f'<div class="muted small">{e(c)}</div></div>' for a, b, c in tiles]
    parts.append(f'</div><p class="muted small">Last 24 h: {s["tasks_per_hour_24h"]} tasks/h · '
                 f'{s["tasks_done"]} done, {s["tasks_skipped"]} skipped in the last 30 days'
                 f'{" · " + plural(t["push_failures"], "push failure") if t["push_failures"] else ""}</p></section>')

    # -- Token speed
    parts.append('<section class="card" id="tokens"><h2>Token speed</h2>')
    speed = [("writing now", tk["gen_tps_recent"]), ("writing avg", tk["gen_tps"]),
             ("reading now", tk["prompt_tps_recent"]), ("reading avg", tk["prompt_tps"])]
    parts.append('<div class="tiles four">' + "".join(
        f'<div class="tile"><div class="label">{e(a)}</div><div class="value">{e(str(v)) if v else "–"}'
        f'<small> tok/s</small></div></div>' for a, v in speed) + "</div>")
    hr = tk["hourly"]
    parts.append('<h3>Tokens written per hour, last 24 h</h3>')
    parts.append(bars([x["generated"] for x in hr],
                      [f'{hhmm(x["hour"])} UTC: {x["generated"]:,} tokens'
                       + (f', {x["gen_tps"]} tok/s' if x["gen_tps"] else "") for x in hr],
                      (hhmm(hr[0]["hour"]) + " UTC", "now"), "No tokens written in the last 24 h."))
    if tk["error"]:
        parts.append(f'<p class="muted small">Metrics: {e(tk["error"])}.</p>')
    elif tk["last_sample"]:
        parts.append(f'<p class="muted small">Sampled from llama-server {e(ago(tk["last_sample"], now))}.</p>')
    parts.append("</section>")

    # -- Activity
    daily = st["daily"]
    parts.append('<section class="card" id="activity"><h2>Commits per day</h2>')
    parts.append(bars([d["commits"] for d in daily],
                      [f'{d["day"]}: {d["commits"]} commits, +{d["lines_added"]}/−{d["lines_removed"]} lines, '
                       f'{d["skipped"]} skipped, {d["generated"]:,} tokens' for d in daily],
                      (daily[0]["day"][5:], "today"), "No commits in the last 14 days."))
    if t["repos"]:
        parts.append('<div class="table-wrap"><table class="small"><thead><tr><th>Repo</th><th>Commits</th><th>Lines</th><th>PRs</th>'
                     '<th>Last</th></tr></thead><tbody>')
        parts += [f'<tr><td>{e(r["repo"])}</td><td>{r["commits"]}</td><td>+{big(r["lines_added"])} '
                  f'−{big(r["lines_removed"])}</td><td>{r["prs"]}</td><td>{e(ago(r["last"], now))}</td></tr>'
                  for r in t["repos"][:8]]
        parts.append("</tbody></table></div>")
    parts.append("</section>")

    # -- Recent
    parts.append('<section class="card wide" id="recent"><h2>Recent</h2>')
    if st["events"]:
        parts.append('<div class="table-wrap"><table class="small events"><tbody>')
        for ev in st["events"][:20]:
            kind = str(ev.get("kind"))
            cls = {"task_done": "ok", "task_skipped": "warn", "push_failed": "bad", "run_finished": "ok"}.get(kind, "off")
            text = ev.get("title") or ev.get("detail") or ev.get("reason") or ev.get("ended") or ""
            extra = ""
            if kind == "task_done" and ev.get("commit"):
                extra = f' <code>{e(str(ev["commit"]))}</code> +{e(str(ev.get("insertions", 0)))}/−{e(str(ev.get("deletions", 0)))}'
            parts.append(f'<tr><td class="muted">{clock_tag(ev.get("received"))}</td><td>{e(str(ev.get("repo")))}</td>'
                         f'<td><span class="pill {cls}">{e(kind)}</span></td><td>{e(str(ev.get("task_id") or ""))}</td>'
                         f'<td class="text">{e(str(text))}{extra}</td></tr>')
        parts.append("</tbody></table></div>")
    else:
        parts.append('<p class="muted">No events yet.</p>')
    parts.append("</section>")

    # -- Runs
    parts.append('<section class="card wide" id="runs"><h2>Runs</h2>')
    if st["runs"]:
        parts.append('<div class="table-wrap"><table class="small runs"><thead><tr><th>State</th><th>Repo</th><th>Branch</th><th>Done</th>'
                     '<th>Skipped</th><th>Started</th><th>PR</th></tr></thead><tbody>')
        for r in st["runs"]:
            cls = {"running": "ok", "finished": "off", "stale": "warn"}[r["state"]]
            pr = r["pr"] or ""
            link = (f'<a href="{e(pr)}">#{e(pr.rsplit("/", 1)[-1])}</a>' if pr.startswith("https://github.com/")
                    else e(pr))
            parts.append(f'<tr><td><span class="pill {cls}">{e(r["state"])}</span>'
                         f'{" " + e(str(r["ended"])) if r["ended"] and r["ended"] != "done" else ""}</td>'
                         f'<td>{e(r["repo"])}</td><td><code>{e(r["branch"])}</code></td><td>{r["done"]}</td>'
                         f'<td>{r["skipped"]}</td><td>{e(ago(r["started"], now))}</td><td>{link}</td></tr>')
        parts.append("</tbody></table></div>")
    else:
        parts.append('<p class="muted">No runs yet.</p>')
    parts.append("</section>")
    return "".join(parts)


CSS = """
:root{--bg:#0f1115;--card:#171a21;--line:#262b36;--ink:#e7e9ee;--muted:#8b93a4;--accent:#6ea8fe;
--ok:#3ecf8e;--warn:#f5b041;--bad:#f0605d;--off:#6b7280;color-scheme:dark}
@media (prefers-color-scheme: light){:root{--bg:#f5f6f8;--card:#fff;--line:#e3e6ec;--ink:#1b1f27;
--muted:#5f6878;--accent:#2f6fde;--ok:#18935d;--warn:#b7791f;--bad:#c93c3a;--off:#6b7280;color-scheme:light}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
header{display:flex;flex-wrap:wrap;align-items:center;gap:10px 16px;padding:14px 16px;border-bottom:1px solid var(--line)}
header h1{font-size:18px;margin:0 auto 0 0}
main,#controls{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;padding:14px 16px;max-width:1200px;margin:0 auto}
#controls{padding-bottom:0}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;min-width:0}
.wide{grid-column:1/-1}
h2{font-size:14px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin:0 0 10px}
h3{font-size:13px;color:var(--muted);margin:14px 0 6px;font-weight:600}
.muted{color:var(--muted)}.small{font-size:13px}
code{font:12.5px ui-monospace,SFMono-Regular,Menlo,monospace;background:var(--line);padding:1px 5px;border-radius:5px;overflow-wrap:anywhere}
.badge,.pill{display:inline-flex;align-items:center;gap:6px;border-radius:999px;font-size:12px;padding:2px 9px;
border:1px solid currentColor;white-space:nowrap}
.badge{font-size:13px;padding:3px 11px}
.badge::before,.dot{content:"";display:inline-block;width:8px;height:8px;border-radius:50%;background:currentColor}
.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}.off{color:var(--off)}
.dot.ok{background:var(--ok)}.dot.off{background:var(--off)}
.now-head{display:flex;flex-wrap:wrap;gap:6px 12px;align-items:baseline}
.repo{font-size:20px;font-weight:650}
.task{font-size:17px;margin:10px 0 2px}
.idle{font-size:20px;font-weight:600;margin:4px 0}
.progress{height:8px;background:var(--line);border-radius:4px;overflow:hidden;margin:12px 0 4px}
.progress i{display:block;height:100%;background:var(--accent);border-radius:4px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.tiles.four{grid-template-columns:repeat(auto-fit,minmax(110px,1fr))}
.tile{border:1px solid var(--line);border-radius:10px;padding:10px 12px}
.tile .label{font-size:12px;color:var(--muted)}
.tile .value{font-size:24px;font-weight:650;font-variant-numeric:tabular-nums}
.tile small{font-size:12px;color:var(--muted);font-weight:400}
.chart{width:100%;height:auto;display:block}
.chart .bar{fill:var(--accent)}.chart .axis{stroke:var(--line)}.chart .hit{fill:transparent}
.chart g:hover .bar{opacity:.75}.chart .tick{fill:var(--muted);font-size:11px}
ol.jobs{list-style:none;margin:0;padding:0}
.job{display:flex;gap:10px;align-items:center;padding:8px 0;border-bottom:1px solid var(--line)}
.job>div{flex:1;min-width:0;overflow-wrap:anywhere}
.job .pos{width:22px;text-align:center;color:var(--muted);font-variant-numeric:tabular-nums}
.job.running .pos{color:var(--ok)}
ul.plain{list-style:none;padding:0;margin:0}ul.plain li{padding:3px 0}
ol.plan{margin:0;padding-left:20px}ol.plan li{padding:2px 0}
table{width:100%;border-collapse:collapse;margin-top:8px}
th{text-align:left;color:var(--muted);font-weight:500}
th,td{padding:5px 6px;border-bottom:1px solid var(--line);vertical-align:top}
td.text{overflow-wrap:anywhere}
.table-wrap{overflow-x:auto}
.cols{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:4px 28px}
.cols h3:first-child{margin-top:0}
a{color:var(--accent)}
button,input,select{font:inherit;color:inherit;background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:5px 10px}
button{cursor:pointer}button:hover{border-color:var(--accent)}
button.link{border:0;background:none;color:var(--accent);padding:0 4px}
.btns{display:flex;gap:4px}.btns button{padding:3px 8px}
form{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
form input[name=repo]{flex:1 1 180px}form input[name=note]{flex:2 1 200px}
form input[name=max_hours]{width:80px}
#msg{min-height:1.2em}
body.locked .ctl,body.locked #add{display:none}
body:not(.locked) #unlock{display:none}
footer{padding:8px 16px 24px;text-align:center}
@media (max-width:700px){main,#controls,.cols{grid-template-columns:minmax(0,1fr)}
.events td:nth-child(2),.runs th:nth-child(5),.runs td:nth-child(5),.runs th:nth-child(6),.runs td:nth-child(6){display:none}
.cols h3:first-child{margin-top:14px}
.tile .value{font-size:20px}}
"""

SCRIPT = """
const TK='agent-status-token';
const tok=()=>{try{return localStorage.getItem(TK)||''}catch(e){return ''}};
const msg=(t)=>{document.getElementById('msg').textContent=t||''};
function localTimes(){document.querySelectorAll('time[datetime]').forEach(t=>{const d=new Date(t.getAttribute('datetime'));
  if(!isNaN(d))t.textContent=d.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'})})}
function lockState(){document.body.classList.toggle('locked',!tok())}
async function post(path,body){
  const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer '+tok()},body:JSON.stringify(body||{})});
  let j={};try{j=await r.json()}catch(e){}
  if(r.status===401){try{localStorage.removeItem(TK)}catch(e){};lockState();msg('Token missing or wrong.')}
  else if(!r.ok){msg(j.error||('Error '+r.status))}else{msg('')}
  return r.ok;
}
async function refresh(){
  try{
    const r=await fetch('/partial',{cache:'no-store'});
    if(!r.ok)throw new Error(r.status);
    const live=document.getElementById('live');
    const open=[...live.querySelectorAll('details[open]')].map(d=>d.closest('section').id);
    live.innerHTML=await r.text();
    open.forEach(id=>{const d=document.querySelector('#'+id+' details');if(d)d.open=true});
    localTimes();
    const s=await fetch('/api/status/head',{cache:'no-store'}).then(r=>r.json());
    const b=document.getElementById('model');b.className='badge '+s.badge;b.textContent='model '+s.model;
    document.getElementById('updated').textContent='updated '+new Date().toLocaleTimeString();
  }catch(e){document.getElementById('updated').textContent='offline, retrying'}
}
setInterval(refresh,5000);  // same data as /api/status, rendered on the server
document.addEventListener('click',async ev=>{
  const b=ev.target.closest('button[data-job]');if(!b)return;
  if(b.dataset.act==='cancel'&&!confirm(b.closest('.running')?'Stop this run after its current task?':'Remove this job from the queue?'))return;
  b.disabled=true;await post('/api/jobs/'+b.dataset.job+'/'+b.dataset.act,b.dataset.to?{to:b.dataset.to}:{});refresh();
});
document.getElementById('unlock').addEventListener('submit',async ev=>{
  ev.preventDefault();const t=ev.target.token.value.trim();
  try{localStorage.setItem(TK,t)}catch(e){}
  if(await post('/api/auth',{})){ev.target.reset();msg('Controls unlocked in this browser.')}
  lockState();
});
document.getElementById('add').addEventListener('submit',async ev=>{
  ev.preventDefault();const f=ev.target;
  const ok=await post('/api/jobs',{repo:f.repo.value.trim(),ref:f.ref.value.trim(),note:f.note.value.trim(),
    max_hours:parseFloat(f.max_hours.value||'24'),ci:f.ci.checked});
  if(ok){f.ref.value='';f.note.value='';msg('Job added.')}
  refresh();
});
document.getElementById('lock').addEventListener('click',()=>{try{localStorage.removeItem(TK)}catch(e){};lockState()});
lockState();localTimes();
"""


def render_page(st: dict, known_repos: list[str]) -> str:
    e = html.escape
    state = st["model"]["state"]
    options = "".join(f'<option value="{e(r)}">' for r in known_repos)
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>Agent status</title><style>{CSS}</style></head><body class="locked">'
        '<header><h1>Agent status</h1>'
        f'<span id="model" class="badge {BADGE.get(state, "off")}">model {e(state)}</span>'
        '<span id="updated" class="muted small"></span></header>'
        '<section id="controls"><div class="card wide">'
        '<form id="add" autocomplete="off">'
        f'<input name="repo" list="repos" placeholder="owner/repo" required aria-label="Repository">'
        f'<datalist id="repos">{options}</datalist>'
        '<input name="ref" placeholder="branch (default)" aria-label="Branch">'
        '<input name="note" placeholder="note" maxlength="200" aria-label="Note">'
        '<label class="small">max h <input name="max_hours" type="number" min="0.1" max="24" step="0.5" value="24">'
        '</label><label class="small"><input name="ci" type="checkbox" checked> wait for CI</label>'
        '<button>Add to queue</button><button type="button" id="lock" class="link">lock</button></form>'
        '<form id="unlock"><span class="small muted">Read-only. Paste the status token to add and manage jobs:</span>'
        '<input name="token" type="password" placeholder="STATUS_TOKEN" aria-label="Token" required>'
        '<button>Unlock</button></form>'
        '<div id="msg" class="small muted"></div></div></section>'
        f'<main id="live">{render_live(st)}</main>'
        '<footer class="small muted">JSON: <a href="/api/status">/api/status</a> · refreshes every 5 s</footer>'
        f'<script>{SCRIPT}</script></body></html>')


# ---------------------------------------------------------------------------- http

def make_server(host: str, port: int, *, db_path: str, token: str, llama_url: str | None = None,
                lease_ttl: float = 180, probe_interval: float = 30, clock=time.time,
                llama_key: str | None = None, job_ttl: float = 900) -> ThreadingHTTPServer:
    app = App(db_path=db_path, token=token, llama_url=llama_url, llama_key=llama_key, lease_ttl=lease_ttl,
              probe_interval=probe_interval, job_ttl=job_ttl, clock=clock)

    class Handler(BaseHTTPRequestHandler):
        server_version = "agent-status"

        def send(self, code: int, body: object, ctype: str = "application/json") -> None:
            if isinstance(body, str):
                data = body.encode()
            else:
                data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802 - http.server naming
            path = self.path.split("?", 1)[0]
            if path == "/healthz":
                return self.send(200, "ok", "text/plain")
            if path == "/api/status":
                return self.send(200, app.status())
            if path == "/api/status/head":
                m = app.model()
                return self.send(200, {"model": m["state"], "badge": BADGE.get(m["state"], "off")})
            if path in ("/", "/partial"):
                st = app.status()
                if path == "/partial":
                    return self.send(200, render_live(st), "text/html; charset=utf-8")
                repos = sorted({j["repo"] for k in ("queued", "running", "recent") for j in st["jobs"][k]})
                return self.send(200, render_page(st, repos), "text/html; charset=utf-8")
            return self.send(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            if not app.authorized(self.headers.get("Authorization", "")):
                return self.send(401, {"error": "unauthorized"})
            try:
                n = int(self.headers.get("Content-Length", 0))
            except ValueError:
                return self.send(400, {"error": "bad length"})
            if n > MAX_BODY:
                return self.send(413, {"error": "too large"})
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self.send(400, {"error": "bad json"})
            if not isinstance(body, dict):
                return self.send(400, {"error": "not an object"})
            path = self.path.split("?", 1)[0]
            if path == "/api/auth":
                return self.send(200, {"ok": True})
            if path == "/api/events":
                if any(not isinstance(body.get(f), str) for f in ("run_id", "repo", "kind", "ts")) \
                        or body["kind"] not in KINDS:
                    return self.send(400, {"error": "need string run_id, repo, kind, ts and a known kind"})
                app.add_event(body)
                return self.send(201, {"ok": True})
            if path == "/api/lease":
                if body.get("action") not in ("acquire", "renew", "release") \
                        or not isinstance(body.get("run_id"), str) or not isinstance(body.get("repo"), str):
                    return self.send(400, {"error": "need action, run_id and repo"})
                return self.send(200, app.lease(body))
            if path == "/api/jobs":
                return self.send(*app.add_job(body))
            if path == "/api/jobs/claim":
                return self.send(200, app.claim(body))
            m = re.fullmatch(r"/api/jobs/(\d+)/([a-z]+)", path)
            if m and m.group(2) in JOB_ACTIONS:
                return self.send(*app.job_action(int(m.group(1)), m.group(2), body))
            return self.send(404, {"error": "not found"})

        def log_message(self, fmt: str, *args: object) -> None:
            if os.environ.get("STATUS_QUIET") != "1":
                sys.stderr.write(f"{self.address_string()} {fmt % args}\n")

    srv = ThreadingHTTPServer((host, port), Handler)
    srv.app = app  # type: ignore[attr-defined]
    return srv


def main() -> None:
    srv = make_server("0.0.0.0", int(os.environ.get("STATUS_PORT", "8080")),
                      db_path=os.environ.get("STATUS_DB", "/data/status.db"),
                      token=os.environ.get("STATUS_TOKEN", ""),
                      llama_url=os.environ.get("LLAMA_BASE_URL") or None,
                      llama_key=os.environ.get("LLAMA_API_KEY") or None,
                      lease_ttl=float(os.environ.get("LEASE_TTL", "180")),
                      probe_interval=float(os.environ.get("PROBE_INTERVAL", "30")),
                      job_ttl=float(os.environ.get("JOB_TTL", "900")))
    interval = float(os.environ.get("METRICS_INTERVAL", "60"))
    app: App = srv.app  # type: ignore[attr-defined]

    def sampler() -> None:
        while True:
            try:
                app.sample_metrics()
            except Exception as exc:  # noqa: BLE001 - never kill the sampler
                print(f"metrics sample failed: {exc}", file=sys.stderr)
            time.sleep(interval)

    if app.root and interval > 0:
        threading.Thread(target=sampler, daemon=True).start()
    print(f"agent-status on :{srv.server_address[1]}", file=sys.stderr, flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
