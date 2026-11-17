#!/usr/bin/env python3
"""agent-status: what the local coding agent is doing, its job queue, and the model lease.

See status/SPEC.md. One file, Python 3.11 standard library only. Run: python3 status/app.py
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import html
import json
import os
import re
import select
import socket
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

KINDS = {"run_started", "task_started", "task_done", "task_skipped", "push_failed", "ci_waiting", "ci",
          "run_finished", "paused", "resumed"}
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
create table if not exists requests(id integer primary key, client text, lane text, path text, queued real,
  started real, finished real, status int, prompt_n int, gen_n int, prompt_ms real, gen_ms real, error text);
create index if not exists requests_finished on requests(finished);
create table if not exists client_tally(client text primary key, requests int default 0, prompt int default 0,
  gen int default 0, prompt_ms real default 0, gen_ms real default 0, errors int default 0);
create table if not exists jobs(id integer primary key, repo text, ref text, note text, max_hours real,
  ci int, state text, pos real, created real, started real, finished real, worker text, seen real,
  cancel int default 0, detail text, exit_code int);
create table if not exists run_control(run_id text primary key, pause int);
"""


def iso(t: float | None) -> str | None:
    return None if t is None else dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat(timespec="seconds")


def day_of(t: float) -> str:
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%d")


def num(v: object, default: float = 0) -> float:
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else default


def clean_repos(value: list[object] | None, owners: set[str]) -> list[str]:
    if not value:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for v in value:
        s = str(v).strip()
        if not s:
            continue
        if s in seen:
            continue
        if not REPO_RE.fullmatch(s):
            continue
        owner = s.split("/")[0].lower()
        if owner and owners and owner not in owners:
            continue
        seen.add(s)
        out.append(s)
    return sorted(out, key=str.lower)


class App:
    def __init__(self, *, db_path: str, token: str, llama_url: str | None, llama_key: str | None,
                 lease_ttl: float, probe_interval: float, job_ttl: float, clock: Callable[[], float],
                 job_owners: list[str] | None = None) -> None:
        self.job_owners = {o.lower() for o in job_owners or []}
        self.gateway: Gateway | None = None
        self.workers: dict[str, dict] = {}
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
        self.db.execute("delete from requests where finished < ?", (cutoff,))
        self.db.commit()
        self.holder: dict | None = None
        self.waiters: list[dict] = []
        self.probe = {"t": None, "state": "unknown"}
        self.metrics_error: str | None = None
        self.n_ctx: int | None = None
        self.props_t: float | None = None

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
            if kind == "run_finished":
                self.db.execute("delete from run_control where run_id=?", (body["run_id"],))
            if self.meta("since") is None:
                self.set_meta("since", now)
            self.db.commit()

    # --------------------------------------------------------------- run_control
    def run_control(self, run_id: str, act: str) -> tuple[int, dict]:
        with self.lock:
            row = self.db.execute("select pause from run_control where run_id=?", (run_id,)).fetchone()
            if act == "control":
                return 200, {"pause": bool(row[0]) if row else False}
            # pause or resume
            if not self.db.execute("select 1 from events where run_id=? and kind='run_started'", (run_id,)).fetchone():
                return 404, {"error": "run not found"}
            finished = self.db.execute("select count(*) from events where run_id=? and kind='run_finished'",
                                       (run_id,)).fetchone()[0]
            if finished:
                return 409, {"error": "run already finished"}
            if act == "pause":
                self.db.execute("insert or replace into run_control(run_id, pause) values (?, 1)", (run_id,))
            else:
                self.db.execute("insert or replace into run_control(run_id, pause) values (?, 0)", (run_id,))
            self.db.commit()
            return 200, {"run_id": run_id, "pause": act == "pause"}

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
        if self.job_owners and repo.strip().split("/", 1)[0].lower() not in self.job_owners:
            return 400, {"error": f"only repos of {', '.join(sorted(self.job_owners))} may be queued"}
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

    def ping(self, body: dict) -> dict:
        worker = str(body.get("worker") or "worker")[:100]
        with self.lock:
            existing = self.workers.get(worker)
            seen = self.clock()
            paused = bool(body.get("paused"))
            raw_repos = body.get("repos")
            if raw_repos is not None:
                repos = clean_repos(raw_repos, self.job_owners)
                if repos:
                    pass
                elif existing and existing.get("repos"):
                    repos = existing["repos"]
                else:
                    repos = []
            else:
                repos = existing.get("repos", []) if existing else []
            self.workers[worker] = {"seen": seen, "paused": paused, "repos": repos}
        return {"ok": True}

    def claim(self, body: dict) -> dict:
        worker = str(body.get("worker") or "worker")[:100]
        with self.lock:
            existing = self.workers.get(worker)
            raw_repos = body.get("repos")
            if raw_repos is not None:
                repos = clean_repos(raw_repos, self.job_owners)
                if repos:
                    pass
                elif existing and existing.get("repos"):
                    repos = existing["repos"]
                else:
                    repos = []
            else:
                repos = existing.get("repos", []) if existing else []
            self.workers[worker] = {"seen": self.clock(), "paused": False, "repos": repos}
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

    # ------------------------------------------------------------------ n_ctx
    def context_size(self) -> int | None:
        if self.n_ctx is not None:
            return self.n_ctx
        if not self.root:
            return None
        now = self.clock()
        if self.props_t is not None and now - self.props_t < self.probe_interval:
            return None
        req = urllib.request.Request(self.root + "/props")
        if self.llama_key:
            req.add_header("Authorization", f"Bearer {self.llama_key}")
        try:
            with urllib.request.urlopen(req, timeout=2) as r:
                data = json.loads(r.read().decode())
        except Exception:
            self.props_t = now
            return None
        d = data.get("default_generation_settings")
        if isinstance(d, dict):
            ctx = d.get("n_ctx")
        else:
            ctx = data.get("n_ctx")
        if isinstance(ctx, int) and not isinstance(ctx, bool):
            self.n_ctx = ctx
        self.props_t = now
        return self.n_ctx

    # ------------------------------------------------------------------ status
    def status(self) -> dict:
        model = self.model()                              # network: outside the lock
        self.context_size()                               # network: outside the lock
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
            pause_map = {r[0]: bool(r[1]) for r in self.db.execute(
                f"select run_id, pause from run_control where run_id in ({','.join('?' * len(ids))})", ids).fetchall()
                } if ids else {}
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
                                                "plan": [], "fin": None, "job_id": None, "max_hours": None,
                                                "paused_evt": None})
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
            elif kind in ("paused", "resumed"):
                run["paused_evt"] = kind
        out_runs = []
        for run in sorted(runs.values(), key=lambda r: r["last"], reverse=True):
            fin = run["fin"]
            is_paused_evt = run["paused_evt"] == "paused" and not fin
            pause_req = pause_map.get(run["run_id"], False)
            state = "finished" if fin else ("paused" if is_paused_evt else (
                "running" if now - run["last"] <= STALE_AFTER or run["run_id"] == holder_run else "stale"))
            out_runs.append({
                "run_id": run["run_id"], "repo": run["repo"], "branch": run["branch"], "state": state,
                "started": iso(run["first"]), "last_event": iso(run["last"]),
                "current": None if fin else run["current"], "done": run["done"], "skipped": run["skipped"],
                "plan": run["plan"], "ended": fin.get("ended") if fin else None,
                "pushed": fin.get("pushed") if fin else None, "pr": fin.get("pr") if fin else None,
                "job_id": run["job_id"], "max_hours": run["max_hours"],
                "task_since": None if fin or not run["current"] else run.get("task_since"),
                "retries": run.get("retries"), "pause": not fin and pause_req})

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
        worker_repos: set[str] = set()
        for w in self.workers.values():
            if now - w["seen"] < 600:
                for r in w.get("repos", []):
                    worker_repos.add(r)
        job_repos: set[str] = {j["repo"] for j, p in pub_jobs if j["repo"]}
        all_repos = sorted(set(list(worker_repos) + list(job_repos)), key=str.lower)
        job_out = {
            "running": [p for j, p in pub_jobs if j["state"] == "running"],
            "queued": [p for j, p in sorted(pub_jobs, key=lambda x: (x[0]["pos"] or 0, x[0]["id"]))
                       if j["state"] == "queued"],
            "recent": [p for j, p in pub_jobs if j["state"] not in ("queued", "running")][:10],
            "workers": [{"name": n, "seen": iso(w["seen"]), "paused": w["paused"], "repos": w.get("repos", [])}
                        for n, w in sorted(self.workers.items()) if now - w["seen"] < 600],
            "repos": all_repos,
        }
        events = [{**json.loads(b), "received": iso(r)} for r, b in ev_rows]
        gateway = self.gateway.snapshot(now) if self.gateway else None
        return {"now": iso(now), "model": model, "lease": {"holder": holder, "queue": queue}, "gateway": gateway,
                "jobs": job_out, "runs": out_runs, "events": events, "stats": stats, "totals": totals,
                "tokens": tokens, "daily": daily}


# ---------------------------------------------------------------------------- gateway

LANES = {"interactive": 0, "batch": 1}
GATEWAY_PATHS = {"/v1/chat/completions", "/v1/completions"}
GATEWAY_MAX_BODY = 16 * 1024 * 1024


def parse_clients(spec: str) -> dict[str, tuple[str, str]]:
    """GATEWAY_CLIENTS: comma-separated name:lane:key -> {key: (name, lane)}."""
    out: dict[str, tuple[str, str]] = {}
    for item in spec.split(","):
        item = item.strip()
        if not item:
            continue
        name, lane, key = (item.split(":", 2) + ["", ""])[:3]
        if not name or lane not in LANES or len(key) < 8:
            raise SystemExit(f"GATEWAY_CLIENTS: bad entry for {name or '?'} (need name:interactive|batch:key, "
                             "key of 8+ characters)")
        out[key] = (name, lane)
    return out


class Gateway:
    """OpenAI-compatible front door for llama-server: per-client keys, a priority queue
    (interactive before batch, FIFO within a lane, `concurrency` requests upstream at a time)
    and a record of every request. Prompts and answers are never stored."""

    def __init__(self, app: App, clients: dict[str, tuple[str, str]], concurrency: int = 1) -> None:
        self.app, self.clients, self.concurrency = app, clients, max(1, concurrency)
        self.cond = threading.Condition()
        self.waiting: list[dict] = []
        self.running: list[dict] = []
        self.seq = 0

    @property
    def enabled(self) -> bool:
        return self.app.root is not None

    def client_for(self, header: str) -> tuple[str, str] | None:
        got = header.removeprefix("Bearer ").strip().encode()
        found = None
        for key, client in self.clients.items():         # compare against all keys: constant-ish time
            if hmac.compare_digest(got, key.encode()):
                found = client
        if self.app.token and hmac.compare_digest(got, self.app.token.encode()):
            found = found or ("chat", "interactive")
        return found

    def _next(self) -> dict | None:
        return min(self.waiting, key=lambda r: (LANES[r["lane"]], r["seq"]), default=None)

    def enter(self, client: tuple[str, str], path: str, stream: bool, gone: Callable[[], bool]) -> dict | None:
        """Wait for a turn. None if the caller hung up while waiting."""
        with self.cond:
            self.seq += 1
            req = {"seq": self.seq, "client": client[0], "lane": client[1], "path": path, "stream": stream,
                   "queued": self.app.clock(), "started": None, "gen_live": 0}
            self.waiting.append(req)
            while True:
                if len(self.running) < self.concurrency and self._next() is req:
                    self.waiting.remove(req)
                    req["started"] = self.app.clock()
                    self.running.append(req)
                    return req
                if gone():
                    self.waiting.remove(req)
                    self.cond.notify_all()
                    self.record(req, 499, error="caller left while waiting")
                    return None
                self.cond.wait(0.5)

    def leave(self, req: dict) -> None:
        with self.cond:
            if req in self.running:
                self.running.remove(req)
            self.cond.notify_all()

    def record(self, req: dict, status: int, timings: dict | None = None, error: str = "") -> None:
        t = timings or {}
        prompt_n, gen_n = int(num(t.get("prompt_n"))), int(num(t.get("predicted_n"), req["gen_live"]))
        prompt_ms, gen_ms = num(t.get("prompt_ms")), num(t.get("predicted_ms"))
        now = self.app.clock()
        with self.app.lock:
            self.app.db.execute(
                "insert into requests(client, lane, path, queued, started, finished, status, prompt_n, gen_n, "
                "prompt_ms, gen_ms, error) values (?,?,?,?,?,?,?,?,?,?,?,?)",
                (req["client"], req["lane"], req["path"], req["queued"], req["started"], now, status, prompt_n,
                 gen_n, prompt_ms, gen_ms, error[:300]))
            self.app.db.execute("insert or ignore into client_tally(client) values (?)", (req["client"],))
            self.app.db.execute("update client_tally set requests=requests+1, prompt=prompt+?, gen=gen+?, "
                                "prompt_ms=prompt_ms+?, gen_ms=gen_ms+?, errors=errors+? where client=?",
                                (prompt_n, gen_n, prompt_ms, gen_ms, int(status >= 400), req["client"]))
            self.app.db.commit()

    @staticmethod
    def timings_of(obj: object) -> dict | None:
        if not isinstance(obj, dict):
            return None
        if isinstance(obj.get("timings"), dict):
            return obj["timings"]
        u = obj.get("usage")
        if isinstance(u, dict) and "completion_tokens" in u:
            return {"prompt_n": u.get("prompt_tokens"), "predicted_n": u.get("completion_tokens")}
        return None

    def upstream(self, method: str, path: str, data: bytes | None) -> urllib.request.Request:
        req = urllib.request.Request(self.app.root + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        if self.app.llama_key:
            req.add_header("Authorization", f"Bearer {self.app.llama_key}")
        return req

    def forward(self, handler: BaseHTTPRequestHandler, req: dict, raw: bytes) -> None:
        """Send the request upstream and copy the answer back, streaming as it comes."""
        try:
            resp = urllib.request.urlopen(self.upstream("POST", req["path"], raw), timeout=900)
        except urllib.error.HTTPError as exc:
            data = exc.read()
            handler.send(exc.code, data, exc.headers.get("Content-Type", "application/json"))
            self.record(req, exc.code, error=data[:200].decode(errors="replace"))
            return
        except (OSError, ValueError) as exc:
            handler.send(502, {"error": {"message": "model unreachable (PC asleep?)", "type": "gateway"}})
            self.record(req, 502, error=str(exc))
            return
        timings, status, error, recorded = None, resp.status, "", False
        with resp:
            ctype = resp.headers.get("Content-Type", "application/json")
            try:
                if ctype.startswith("text/event-stream"):
                    handler.close_connection = True
                    handler.send_response(resp.status)
                    handler.send_header("Content-Type", ctype)
                    handler.send_header("Cache-Control", "no-store")
                    handler.end_headers()
                    for line in iter(resp.readline, b""):
                        handler.wfile.write(line)
                        if line.startswith(b"data: ") and line[6:].strip() != b"[DONE]":
                            try:
                                obj = json.loads(line[6:])
                            except ValueError:
                                continue
                            req["gen_live"] += 1
                            timings = self.timings_of(obj) or timings
                        elif line.strip() == b"":
                            handler.wfile.flush()
                else:
                    data = resp.read()
                    try:
                        timings = self.timings_of(json.loads(data))
                    except ValueError:
                        pass
                    # Record before answering, so the caller sees its request on the page at once.
                    self.record(req, status, timings)
                    recorded = True
                    handler.send(resp.status, data, ctype)
            except (BrokenPipeError, ConnectionResetError) as exc:
                status, error = 499, f"caller left during the answer ({exc.__class__.__name__})"
            except (OSError, ValueError) as exc:
                status, error = 502, f"upstream broke off: {exc}"
        if not recorded:
            self.record(req, status, timings, error)

    def snapshot(self, now: float) -> dict:
        with self.cond:
            order = sorted(self.waiting, key=lambda r: (LANES[r["lane"]], r["seq"]))
            waiting = [{"id": r["seq"], "client": r["client"], "lane": r["lane"], "waited_s": round(now - r["queued"])}
                       for r in order]
            running = [{"id": r["seq"], "client": r["client"], "lane": r["lane"], "stream": r["stream"],
                        "for_s": round(now - r["started"]), "tokens": r["gen_live"]} for r in self.running]
        with self.app.lock:
            recent = self.app.db.execute(
                "select client, lane, queued, started, finished, status, prompt_n, gen_n, gen_ms, error "
                "from requests order by id desc limit 15").fetchall()
            tally = self.app.db.execute(
                "select client, requests, prompt, gen, gen_ms, errors from client_tally order by requests desc"
            ).fetchall()
            day = self.app.db.execute(
                "select client, count(*), sum(gen_n) from requests where finished >= ? group by client",
                (now - 86400,)).fetchall()
        last24 = {c: (n, g or 0) for c, n, g in day}
        return {
            "enabled": self.enabled,
            "n_ctx": self.app.n_ctx,
            "clients": sorted({n for n, _ in self.clients.values()} | {"chat"}),
            "waiting": waiting,
            "running": running,
            "recent": [{"client": c, "lane": ln, "finished": iso(f), "waited_s": round((s or f) - q),
                        "took_s": round(f - (s or f)), "status": st, "prompt": p, "generated": g,
                        "gen_tps": round(g / (ms / 1000), 1) if ms else None, "error": err}
                       for c, ln, q, s, f, st, p, g, ms, err in recent],
            "by_client": [{"client": c, "requests": n, "prompt": p, "generated": g, "errors": er,
                           "gen_tps": round(g / (ms / 1000), 1) if ms else None,
                           "requests_24h": last24.get(c, (0, 0))[0], "generated_24h": last24.get(c, (0, 0))[1]}
                          for c, n, p, g, ms, er in tally],
        }


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
    run = next((r for r in st["runs"] if r["state"] in ("running", "paused")), None)
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
        if run["state"] == "running":
            if run.get("pause"):
                state_cls, state_label, run_actions = "warn", "pausing after this task", f'<button class="ctl" data-run="now" data-act="resume" title="Resume this run">Resume</button>'
            else:
                state_cls, state_label, run_actions = "ok", "running", f'<button class="ctl" data-run="{e(run["run_id"])}" data-act="pause" title="Pause this run">Pause</button>'
        else:
            state_cls, state_label, run_actions = "warn", "paused", '<span class="pill warn">paused</span><button class="ctl" data-run="now" data-act="resume" title="Resume this run">Resume</button>'
        parts.append(f'<div class="now-head"><span class="repo">{e(run["repo"])}</span>'
                     f'<code>{e(run["branch"])}</code><span class="pill {state_cls}">{e(state_label)}</span>'
                     f'<span class="muted">running {e(dur(run["started"], now))}'
                     f'{" · job #" + e(run["job_id"]) if run["job_id"] else ""}</span>{run_actions}</div>')
        if c:
            att = f'attempt {e(str(c.get("attempt")))}' + (f'/{e(str(run["retries"]))}' if run.get("retries") else "")
            parts.append(f'<p class="task"><b>{e(str(c.get("task_id")))}</b> {e(str(c.get("title")))}</p>'
                         f'<p class="muted small">{att} · for {e(dur(run["task_since"], now))}</p>')
        else:
            between = ('Between tasks (checking, pushing or waiting for CI).' if run["state"] == "running"
                       else 'Pausing after this task.')
            parts.append(f'<p class="muted small">{between}</p>')
        parts.append(f'<div class="progress live" title="{finished} of {total} tasks"><i style="width:{pct}%"></i></div>'
                     f'<p class="muted small">{finished} of {total} plan tasks · {run["done"]} done, '
                     f'{run["skipped"]} skipped this run · last event {e(ago(run["last_event"], now))}</p>')
        if tk.get("busy") is not None:
            gen = '<span class="dot ok"></span> model generating' if tk["busy"] else '<span class="dot off"></span> model idle'
            parts.append(f'<p class="small">{gen}'
                         f'{" · " + e(str(tk["gen_tps_recent"])) + " tok/s" if tk["gen_tps_recent"] else ""}</p>')
    else:
        parts.append('<p class="idle">Idle</p><p class="muted small">No run is active. Add a job below, or start '
                     '<code>agent-loop</code> in a repo on the devbox.'
                     '</p>')
    parts.append("</section>")

    # -- Model activity (gateway)
    gw = st.get("gateway")
    if gw and gw["enabled"]:
        lane_pill = {"interactive": "ok", "batch": "off"}
        parts.append('<section class="card wide" id="activity-live"><h2>Model activity</h2><div class="cols"><div>')
        rows = []
        for r in gw["running"]:
            rate = f' · ~{r["tokens"] / r["for_s"]:.0f} tok/s' if r["stream"] and r["for_s"] and r["tokens"] else ""
            rows.append(f'<li class="job running"><span class="pos">▶</span><div><b>{e(r["client"])}</b> '
                        f'<span class="pill {lane_pill[r["lane"]]}">{e(r["lane"])}</span><div class="muted small">'
                        f'answering for {r["for_s"]} s{" · " + str(r["tokens"]) + " tokens so far" if r["stream"] else ""}'
                        f'{rate}</div></div></li>')
        for i, r in enumerate(gw["waiting"], 1):
            rows.append(f'<li class="job"><span class="pos">{i}</span><div><b>{e(r["client"])}</b> '
                        f'<span class="pill {lane_pill[r["lane"]]}">{e(r["lane"])}</span>'
                        f'<div class="muted small">waiting {r["waited_s"]} s</div></div></li>')
        parts.append(f'<ol class="jobs">{"".join(rows)}</ol>' if rows else
                     '<p class="muted">No requests right now. Chat and scrum requests go before agent-loop ones.</p>')
        if gw["recent"]:
            parts.append('<h3>Last requests</h3><div class="table-wrap"><table class="small"><tbody>')
            for r in gw["recent"][:8]:
                ok = r["status"] and r["status"] < 400
                what = f'{big(r["generated"])} tok' if ok else f'<span class="pill bad">{e(str(r["status"]))}</span>'
                parts.append(f'<tr><td class="muted">{clock_tag(r["finished"])}</td><td>{e(r["client"])}</td>'
                             f'<td>{what}</td>'
                             f'<td class="muted">{str(r["gen_tps"]) + " tok/s" if r["gen_tps"] else ""}</td>'
                             f'<td class="muted">{"waited " + str(r["waited_s"]) + " s" if r["waited_s"] else ""}</td></tr>')
            parts.append("</tbody></table></div>")
        parts.append('</div><div><h3>By client</h3>')
        if gw["by_client"]:
            parts.append('<div class="table-wrap"><table class="small"><thead><tr><th>Client</th><th>Requests</th>'
                         '<th>Written</th><th>tok/s</th><th>24 h</th></tr></thead><tbody>')
            parts += [f'<tr><td>{e(c["client"])}</td><td>{c["requests"]}</td><td>{big(c["generated"])}</td>'
                      f'<td>{c["gen_tps"] or "–"}</td><td>{c["requests_24h"]} req · {big(c["generated_24h"])} tok</td></tr>'
                      for c in gw["by_client"]]
            parts.append("</tbody></table></div>")
        else:
            parts.append('<p class="muted small">Nothing has gone through the gateway yet.</p>')
        parts.append("</div></div></section>")

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
    ws = jobs["workers"]
    if not ws:
        parts.append('<p class="small"><span class="pill warn">no worker</span> <span class="muted">agent-queue '
                     'is not polling; queued jobs wait.</span></p>')
    for w in ws:
        pill = '<span class="pill warn">paused</span>' if w["paused"] else '<span class="pill ok">worker</span>'
        parts.append(f'<p class="small">{pill} {e(w["name"])} <span class="muted">seen {e(ago(w["seen"], now))}'
                     f'{" · rm ~/.agent-queue-STOP to resume" if w["paused"] else ""}</span></p>')
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
                     '<th>Skipped</th><th>Started</th><th>PR</th><th>Pause</th></tr></thead><tbody>')
        for r in st["runs"]:
            cls = {"running": "ok", "finished": "off", "stale": "warn", "paused": "warn"}.get(r["state"], "off")
            pr = r["pr"] or ""
            link = (f'<a href="{e(pr)}">#{e(pr.rsplit("/", 1)[-1])}</a>' if pr.startswith("https://github.com/")
                    else e(pr))
            pause_cell = ""
            if r["state"] in ("running", "paused"):
                if r["state"] == "running":
                    if r.get("pause"):
                        pause_cell = '<span class="pill warn">pausing after this task</span>' \
                                     f'<button class="ctl" data-run="{e(r["run_id"])}" data-act="resume">Resume</button>'
                    else:
                        pause_cell = f'<button class="ctl" data-run="{e(r["run_id"])}" data-act="pause">Pause</button>'
                else:
                    pause_cell = '<span class="pill warn">paused</span>' \
                                 f'<button class="ctl" data-run="{e(r["run_id"])}" data-act="resume">Resume</button>'
            parts.append(f'<tr><td><span class="pill {cls}">{e(r["state"])}</span>'
                         f'{" " + e(str(r["ended"])) if r["ended"] and r["ended"] != "done" else ""}</td>'
                         f'<td>{e(r["repo"])}</td><td><code>{e(r["branch"])}</code></td><td>{r["done"]}</td>'
                         f'<td>{r["skipped"]}</td><td>{e(ago(r["started"], now))}</td><td>{link}</td><td>{pause_cell}</td></tr>')
        parts.append("</tbody></table></div>")
    else:
        parts.append('<p class="muted">No runs yet.</p>')
    parts.append("</section>")
    return "".join(parts)


STATIC = Path(__file__).resolve().parent / "static"
STATIC_TYPES = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8"}


def static_files() -> dict[str, tuple[bytes, str]]:
    """status/static/*.css|js, read once at start: name -> (bytes, content type)."""
    out: dict[str, tuple[bytes, str]] = {}
    if STATIC.is_dir():
        for f in sorted(STATIC.iterdir()):
            if f.suffix in STATIC_TYPES and f.is_file():
                out[f.name] = (f.read_bytes(), STATIC_TYPES[f.suffix])
    return out


STATIC_FILES = static_files()
STATIC_VERSION = hashlib.sha256(b"".join(b for b, _ in STATIC_FILES.values())).hexdigest()[:10]
# Theme: "auto" (device), "light" or "dark", kept in localStorage; applied before the first paint.
THEME_HEAD = ("<script>(function(){var k='agent-status-theme',m=matchMedia('(prefers-color-scheme: dark)');"
              "function apply(){var p='auto';try{p=localStorage.getItem(k)||'auto'}catch(e){}"
              "var d=document.documentElement;d.dataset.themePref=p;d.dataset.theme=p==='auto'?(m.matches?'dark':'light'):p}"
              "apply();m.addEventListener('change',apply);"
              "window.setTheme=function(p){try{localStorage.setItem(k,p)}catch(e){}apply()}})()</script>")
THEME_SCRIPT = """
document.querySelectorAll('.theme input').forEach(i=>{i.checked=i.value===(document.documentElement.dataset.themePref||'auto');
  i.addEventListener('change',()=>window.setTheme(i.value))});
"""
HEAD = ('<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
        f'{THEME_HEAD}<link rel="stylesheet" href="/static/style.css?v={STATIC_VERSION}">')
ICON = {
    "auto": ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="8"/>'
             '<path d="M12 4a8 8 0 0 1 0 16z" fill="currentColor"/></svg>'),
    "light": ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">'
              '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2'
              'M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>'),
    "dark": '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/></svg>',
}


def theme_switch() -> str:
    """Auto / Light / Dark in the header."""
    opts = "".join(f'<input type="radio" name="theme" value="{v}" id="theme-{v}"{" checked" if v == "auto" else ""}>'
                   f'<label for="theme-{v}" title="{t}" aria-label="{t}">{ICON[v]}</label>'
                   for v, t in (("auto", "Theme: follow the device"), ("light", "Light theme"), ("dark", "Dark theme")))
    return f'<div class="seg three icons theme" role="radiogroup" aria-label="Theme">{opts}<span class="thumb"></span></div>'
# Gradient for the chart bars; outside #live so the 5 s refresh keeps it.
DEFS = ('<svg class="defs" aria-hidden="true" focusable="false"><defs><linearGradient id="bar-fill" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0" stop-color="var(--bar-top)"/><stop offset="1" stop-color="var(--bar-bottom)"/></linearGradient></defs></svg>')

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
if(document.getElementById('live'))setInterval(refresh,5000);  // same data as /api/status, rendered on the server
document.addEventListener('click',async ev=>{
  const b=ev.target.closest('button[data-job]');if(!b)return;
  if(b.dataset.act==='cancel'&&!confirm(b.closest('.running')?'Stop this run after its current task?':'Remove this job from the queue?'))return;
  b.disabled=true;await post('/api/jobs/'+b.dataset.job+'/'+b.dataset.act,b.dataset.to?{to:b.dataset.to}:{});refresh();
});
document.getElementById('unlock')?.addEventListener('submit',async ev=>{
  ev.preventDefault();const t=ev.target.token.value.trim();
  try{localStorage.setItem(TK,t)}catch(e){}
  if(await post('/api/auth',{})){ev.target.reset();msg('Controls unlocked in this browser.')}
  lockState();
});
document.getElementById('add')?.addEventListener('submit',async ev=>{
  ev.preventDefault();const f=ev.target;
  const ok=await post('/api/jobs',{repo:f.repo.value.trim(),ref:f.ref.value.trim(),note:f.note.value.trim(),
    max_hours:parseFloat(f.max_hours.value||'24'),ci:f.ci.checked});
  if(ok){f.ref.value='';f.note.value='';msg('Job added.')}
  refresh();
});
document.getElementById('lock')?.addEventListener('click',()=>{try{localStorage.removeItem(TK)}catch(e){};lockState()});
lockState();localTimes();
"""

CHAT_SCRIPT = r"""
(function(){
const box=document.getElementById('chat');if(!box)return;
const KEY='agent-status-chat';
const log=box.querySelector('.log'),form=box.querySelector('form'),ta=form.q,stopB=box.querySelector('.stop');
let hist=[];try{hist=JSON.parse(localStorage.getItem(KEY)||'[]')}catch(e){}
let ctl=null;
const sys=()=>'You are a helpful assistant running on a local model in a home lab. Today is '+
  new Date().toDateString()+'. Answer concisely; use plain text or simple Markdown.';
function save(){try{localStorage.setItem(KEY,JSON.stringify(hist.slice(-40)))}catch(e){}}
function bubble(role,text){const d=document.createElement('div');d.className='msg '+role;d.textContent=text;
  log.appendChild(d);log.scrollTop=log.scrollHeight;return d}
function draw(){log.textContent='';hist.forEach(m=>bubble(m.role,m.content));
  if(!hist.length)bubble('note','Ask the local model anything. Chat requests go before agent work; the history stays in this browser.')}
draw();
box.querySelector('.clear').addEventListener('click',()=>{if(ctl)ctl.abort();hist=[];save();draw()});
stopB.addEventListener('click',()=>{if(ctl)ctl.abort()});
const MODE='agent-status-chat-mode';
try{const m=localStorage.getItem(MODE);if(m&&form.mode)form.mode.value=m}catch(e){}
form.addEventListener('change',ev=>{if(ev.target.name==='mode'){try{localStorage.setItem(MODE,ev.target.value)}catch(e){}}});
ta.addEventListener('keydown',ev=>{if(ev.key==='Enter'&&!ev.shiftKey){ev.preventDefault();form.requestSubmit()}});
form.addEventListener('submit',async ev=>{
  ev.preventDefault();const q=ta.value.trim();if(!q||ctl)return;
  if(!hist.length)log.textContent='';
  hist.push({role:'user',content:q});bubble('user',q);ta.value='';save();
  const out=bubble('assistant','');const meta=document.createElement('div');meta.className='meta';
  log.appendChild(meta);
  const load=document.createElement('div');load.className='loading';
  load.innerHTML='<span class="orb"><i></i></span><span class="shimmer"></span>';out.appendChild(load);
  const say=(t,fast)=>{load.lastChild.textContent=t;load.classList.toggle('fast',!!fast)};
  say('Waiting for the model…');log.scrollTop=log.scrollHeight;
  ctl=new AbortController();stopB.hidden=false;let text='',n=0,tps='';
  const think=form.mode.value==='think';
  try{
    const r=await fetch('/v1/chat/completions',{method:'POST',signal:ctl.signal,
      headers:{'Content-Type':'application/json','Authorization':'Bearer '+tok()},
      body:JSON.stringify({model:'coder',stream:true,max_tokens:think?8192:2048,
        messages:[{role:'system',content:sys()},...hist.slice(-20)],chat_template_kwargs:{enable_thinking:think}})});
    if(!r.ok){let j={};try{j=await r.json()}catch(e){}
      if(r.status===401){try{localStorage.removeItem(TK)}catch(e){};lockState()}
      throw new Error((j.error&&j.error.message)||('HTTP '+r.status))}
    say('Reading your message…');
    const rd=r.body.getReader(),dec=new TextDecoder();let buf='';
    for(;;){const {done,value}=await rd.read();if(done)break;buf+=dec.decode(value,{stream:true});
      let i;while((i=buf.indexOf('\n'))>=0){const line=buf.slice(0,i).trim();buf=buf.slice(i+1);
        if(!line.startsWith('data: ')||line==='data: [DONE]')continue;
        let o;try{o=JSON.parse(line.slice(6))}catch(e){continue}
        const d=(o.choices&&o.choices[0]&&o.choices[0].delta)||{};
        if(d.reasoning_content&&!text)say('Thinking…',true);
        if(d.content){text+=d.content;n++;out.textContent=text;log.scrollTop=log.scrollHeight}
        if(o.timings&&o.timings.predicted_per_second)tps=' · '+o.timings.predicted_per_second.toFixed(1)+' tok/s';
      }
      if(text)meta.textContent=n+' tokens'+tps;
    }
    if(!text){load.remove();out.remove();meta.textContent='(empty answer)'}
  }catch(e){meta.textContent=e.name==='AbortError'?'stopped':'error: '+e.message;if(!text)out.remove()}
  ctl=null;stopB.hidden=true;
  if(text){hist.push({role:'assistant',content:text});save()}
});
})();
"""


BOLT = '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M13 2 4 14h7l-1 8 9-12h-7z"/></svg>'
SPARKLE = ('<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 2l1.8 5.2L19 9l-5.2 1.8L12 16l-1.8-5.2L5 9'
           'l5.2-1.8zM19 14l.9 2.1L22 17l-2.1.9L19 20l-.9-2.1L16 17l2.1-.9zM5 15l.7 1.6L7.3 17l-1.6.7L5 19.3l-.7-1.6L2.7 17'
           'l1.6-.4z"/></svg>')


def chat_box(full: bool) -> str:
    link = '' if full else '<a class="small" href="/chat">full chat →</a>'
    return (f'<div id="chat" class="card wide{" full" if full else ""}"><div class="chat-head"><h2>Chat</h2>{link}'
            '<button type="button" class="clear link small">clear</button></div>'
            '<div class="log" aria-live="polite"></div>'
            '<form autocomplete="off"><textarea name="q" rows="2" placeholder="Message the local model (Enter to send, '
            'Shift+Enter for a new line)" aria-label="Message" required></textarea>'
            '<div class="composer-bar"><div class="seg" role="radiogroup" aria-label="Answer mode">'
            f'<input type="radio" name="mode" value="quick" id="mode-quick" checked><label for="mode-quick" '
            f'title="Answer straight away">{BOLT}Quick</label>'
            f'<input type="radio" name="mode" value="think" id="mode-think"><label for="mode-think" '
            f'title="Reason first: slower, better for hard questions">{SPARKLE}Think</label><span class="thumb"></span></div>'
            '<span class="spacer"></span>'
            '<button type="button" class="stop" hidden>Stop</button><button class="primary">Send</button></div></form></div>')


def render_page(st: dict, known_repos: list[str], chat: bool = True) -> str:
    e = html.escape
    state = st["model"]["state"]
    if known_repos:
        options = "".join(f'<option value="{e(r)}">{e(r)}</option>' for r in known_repos)
        repo_input = f'<select name="repo" required aria-label="Repository">{options}</select>'
    else:
        repo_input = f'<input name="repo" list="repos" placeholder="owner/repo" required aria-label="Repository">'
    datalist = f'<datalist id="repos">{"".join("<option value=" + e(r) + ">" + e(r) + "</option>" for r in known_repos)}</datalist>' if known_repos else ""
    return (
        f'<!doctype html><html lang="en"><head>{HEAD}'
        f'<title>Agent status</title></head><body class="locked">{DEFS}'
        '<header><h1>Agent status</h1>'
        f'<span id="model" class="badge {BADGE.get(state, "off")}">model {e(state)}</span>'
        f'<span id="updated" class="muted small"></span>{theme_switch()}</header>'
        '<section id="controls"><div class="card wide">'
        '<div class="card-head ctl"><h2>Queue a job</h2>'
        '<button type="button" id="lock" class="link small">lock</button></div>'
        '<form id="add" autocomplete="off"><div class="fields">'
        f'{repo_input}{datalist}'
        '<input name="ref" placeholder="branch (default)" aria-label="Branch">'
        '<input name="note" placeholder="note" maxlength="200" aria-label="Note"></div>'
        '<div class="opts"><label class="chip">max <input name="max_hours" type="number" min="0.1" max="24" step="0.5" '
        'value="24" aria-label="Max hours"> h</label>'
        '<label class="switch"><input name="ci" type="checkbox" checked><span class="track"></span>wait for CI</label>'
        '<span class="spacer"></span><button class="primary">Add to queue</button></div></form>'
        '<form id="unlock"><span class="small muted">Read-only. Paste the status token to add and manage jobs:</span>'
        '<input name="token" type="password" placeholder="STATUS_TOKEN" aria-label="Token" required>'
        '<button>Unlock</button></form>'
        '<div id="msg" class="small muted"></div></div>'
        f'{chat_box(False) if chat else ""}</section>'
        f'<main id="live">{render_live(st)}</main>'
        '<footer class="small muted">JSON: <a href="/api/status">/api/status</a> · refreshes every 5 s</footer>'
        f'<script>{SCRIPT}{CHAT_SCRIPT}{THEME_SCRIPT}</script></body></html>')


def render_chat_page() -> str:
    return (
        f'<!doctype html><html lang="en"><head>{HEAD}'
        '<title>Agent chat</title></head><body class="locked">'
        f'<header><h1>Chat</h1><a class="small" href="/">← status</a>{theme_switch()}</header>'
        '<section id="controls"><div class="card wide" id="unlock-card">'
        '<form id="unlock"><span class="small muted">Paste the status token to chat:</span>'
        '<input name="token" type="password" placeholder="STATUS_TOKEN" aria-label="Token" required>'
        '<button>Unlock</button></form><div id="msg" class="small muted"></div></div>'
        f'{chat_box(True)}</section>'
        f'<script>{SCRIPT}{CHAT_SCRIPT}{THEME_SCRIPT}</script></body></html>')


# ---------------------------------------------------------------------------- http

def make_server(host: str, port: int, *, db_path: str, token: str, llama_url: str | None = None,
                lease_ttl: float = 180, probe_interval: float = 30, clock=time.time,
                llama_key: str | None = None, job_ttl: float = 900,
                job_owners: list[str] | None = None, gateway_clients: dict[str, tuple[str, str]] | None = None,
                gateway_concurrency: int = 1) -> ThreadingHTTPServer:
    app = App(db_path=db_path, token=token, llama_url=llama_url, llama_key=llama_key, lease_ttl=lease_ttl,
              probe_interval=probe_interval, job_ttl=job_ttl, clock=clock, job_owners=job_owners)
    gw = Gateway(app, gateway_clients or {}, gateway_concurrency)
    app.gateway = gw

    class Handler(BaseHTTPRequestHandler):
        server_version = "agent-status"

        def send(self, code: int, body: object, ctype: str = "application/json") -> None:
            if isinstance(body, bytes):
                data = body
            elif isinstance(body, str):
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

        def client_gone(self) -> bool:
            try:
                readable, _, _ = select.select([self.connection], [], [], 0)
                return bool(readable) and self.connection.recv(1, socket.MSG_PEEK) == b""
            except OSError:
                return True

        def gateway(self, method: str) -> None:
            path = self.path.split("?", 1)[0]
            if not gw.enabled:
                return self.send(404, {"error": {"message": "gateway off: LLAMA_BASE_URL not set"}})
            client = gw.client_for(self.headers.get("Authorization", ""))
            if client is None:
                return self.send(401, {"error": {"message": "unknown key"}})
            if method == "GET":
                if path != "/v1/models":
                    return self.send(404, {"error": {"message": "not found"}})
                try:
                    with urllib.request.urlopen(gw.upstream("GET", path, None), timeout=10) as r:
                        return self.send(r.status, r.read(), r.headers.get("Content-Type", "application/json"))
                except urllib.error.HTTPError as exc:
                    return self.send(exc.code, exc.read())
                except (OSError, ValueError):
                    return self.send(502, {"error": {"message": "model unreachable (PC asleep?)"}})
            if path not in GATEWAY_PATHS:
                return self.send(404, {"error": {"message": "not found"}})
            try:
                n = int(self.headers.get("Content-Length", 0))
            except ValueError:
                return self.send(400, {"error": {"message": "bad length"}})
            if n > GATEWAY_MAX_BODY:
                return self.send(413, {"error": {"message": "too large"}})
            raw = self.rfile.read(n)
            try:
                body = json.loads(raw)
            except ValueError:
                return self.send(400, {"error": {"message": "bad json"}})
            if not isinstance(body, dict):
                return self.send(400, {"error": {"message": "not an object"}})
            req = gw.enter(client, path, bool(body.get("stream")), self.client_gone)
            if req is None:
                return
            try:
                gw.forward(self, req, raw)
            finally:
                gw.leave(req)

        def do_GET(self) -> None:  # noqa: N802 - http.server naming
            path = self.path.split("?", 1)[0]
            if path.startswith("/v1/"):
                return self.gateway("GET")
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
                return self.send(200, render_page(st, st["jobs"]["repos"], gw.enabled), "text/html; charset=utf-8")
            if path == "/chat":
                return self.send(200, render_chat_page(), "text/html; charset=utf-8")
            if path.startswith("/static/") and path[8:] in STATIC_FILES:
                body, ctype = STATIC_FILES[path[8:]]
                return self.send(200, body, ctype)
            return self.send(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            if self.path.startswith("/v1/"):
                return self.gateway("POST")
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
            if path == "/api/workers/ping":
                return self.send(200, app.ping(body))
            m = re.fullmatch(r"/api/jobs/(\d+)/([a-z]+)", path)
            if m and m.group(2) in JOB_ACTIONS:
                return self.send(*app.job_action(int(m.group(1)), m.group(2), body))
            m = re.fullmatch(r"/api/runs/([^/]+)/(pause|resume|control)", path)
            if m:
                return self.send(*app.run_control(urllib.parse.unquote(m.group(1)), m.group(2)))
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
                      job_ttl=float(os.environ.get("JOB_TTL", "900")),
                      job_owners=[o.strip() for o in os.environ.get("JOB_OWNERS", "").split(",") if o.strip()],
                      gateway_clients=parse_clients(os.environ.get("GATEWAY_CLIENTS", "")),
                      gateway_concurrency=int(os.environ.get("GATEWAY_CONCURRENCY", "1")))
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
