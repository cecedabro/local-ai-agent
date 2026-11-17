#!/usr/bin/env python3
"""backfill.py [--repos DIR] [--opencode-db FILE] [--dry-run]

Import historical events from git logs and OpenCode's SQLite database into the
agent-status service via POST /api/import (SPEC §12.2).
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob as glob_mod
import json
import os
import re
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

SEP = "\x1e"
RECORD_SEP = "\x1f"

_TASK_RE = re.compile(r"^(T\d+): (.*)")
_STAT_RE = re.compile(r",\s*(\d+)\s+insertion\(s?\),?\s*(\d+)\s+deletion\(s?\),?")
_LOG_RE = re.compile(r"-a(\d+)\.log$")
_SHORTSTAT_RE = re.compile(r"(\d+) file(s)? changed(?:,\s+(\d+) insertion(s)?,?\s+(\d+) deletion(s)?)?")


def _parse_shortstat(text: str) -> tuple[int, int]:
    """Return (insertions, deletions) from a shortstat string."""
    ins = 0
    del_ = 0
    for line in text.strip().splitlines():
        m = re.match(r'\s*(\d+)\s+files? changed', line)
        if m:
            rest = line[m.end():]
            im = re.search(r'(\d+)\s+insertions?\b', rest)
            dm = re.search(r'(\d+)\s+deletions?\b', rest)
            if im:
                ins = int(im.group(1))
            if dm:
                del_ = int(dm.group(1))
    return ins, del_


def _gh_default(args: list[str], cwd: Path) -> str:
    """Run the `gh` CLI; return \"[]\" on any failure."""
    try:
        r = subprocess.run(
            ["gh"] + args,
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        return r.stdout
    except Exception:
        return "[]"


def _highest_attempts(repo_dir: Path, task_id: str) -> int:
    """Find the highest attempt number N from .agent/logs/<id>-a<N>.log and .agent/logs-*/<id>-a<N>.log."""
    best = 0
    for pattern in (".agent/logs/{}-a*.log", ".agent/logs-*/{}-a*.log"):
        for p in repo_dir.glob(pattern.format(task_id)):
            m = _LOG_RE.search(p.name)
            if m:
                n = int(m.group(1))
                if n > best:
                    best = n
    return best if best > 0 else 1


def git_events(repo_dir: Path, name: str, gh=None) -> list[dict]:
    """Backfill events from a git repository's history.

    Runs ``git -C <repo_dir> log --all --format=%x1e%H%x1f%an%x1f%aI%x1f%s --shortstat``
    and splits records on ``\\x1e``.  Each record's first line is
    sha/author/date/subject (split on ``\\x1f``), the rest may hold the shortstat
    line.
    """
    if gh is None:
        gh = _gh_default

    proc = subprocess.run(
        ["git", "-C", str(repo_dir), "log", "--all",
         "--format=%x1e%H%x1f%an%x1f%aI%x1f%s", "--shortstat"],
        capture_output=True, text=True, check=True,
    )
    raw = proc.stdout
    records = raw.split(SEP)

    events: list[dict] = []

    for rec in records:
        rec = rec.strip()
        if not rec:
            continue
        lines = rec.splitlines()
        if not lines:
            continue

        parts = lines[0].split(RECORD_SEP)
        if len(parts) < 4:
            continue
        sha, author, adate, subject = parts[0], parts[1], parts[2], parts[3]

        # only local-agent commits
        if author != "local-agent":
            continue

        # shortstat
        stat_text = "\n".join(lines[1:])
        insertions, deletions = _parse_shortstat(stat_text)

        # convert author date to UTC ISO
        adt = dt.datetime.fromisoformat(adate)
        ts = adt.astimezone(dt.timezone.utc).isoformat(timespec="seconds")

        key = f"{name}:{sha}"

        if subject == "agent run report":
            events.append({
                "repo": name,
                "key": f"{name}:run:{sha}",
                "kind": "run_started",
                "ts": ts,
            })
            continue

        m = _TASK_RE.match(subject)
        if not m:
            continue

        task_id = m.group(1)
        title = m.group(2)[:200]
        attempts = _highest_attempts(repo_dir, task_id)

        events.append({
            "repo": name,
            "kind": "task_done",
            "key": key,
            "ts": ts,
            "task_id": task_id,
            "title": title,
            "commit": sha[:7],
            "insertions": insertions,
            "deletions": deletions,
            "attempts": attempts,
        })

    # PRs
    try:
        gh_stdout = gh(
            ["pr", "list", "--state", "all", "--limit", "500",
             "--json", "number,headRefName,createdAt,url"],
            repo_dir,
        )
    except Exception:
        gh_stdout = "[]"

    if not isinstance(gh_stdout, str):
        gh_stdout = str(gh_stdout)

    try:
        prs = json.loads(gh_stdout)
    except (json.JSONDecodeError, ValueError):
        prs = []

    if not isinstance(prs, list):
        prs = []

    for pr in prs:
        head = pr.get("headRefName", "")
        if head.startswith("agent/"):
            created = pr.get("createdAt", "")
            # ensure +00:00 suffix
            if created.endswith("Z"):
                created = created[:-1] + "+00:00"
            elif not created.endswith("+00:00"):
                created += "+00:00"
            events.append({
                "repo": name,
                "kind": "run_finished",
                "key": f"{name}:pr:{pr['number']}",
                "ts": created,
                "pr": pr.get("url", ""),
            })

    events.sort(key=lambda e: (e["ts"], e["key"]))
    return events


def opencode_tokens(db_path: Path) -> list[dict]:
    """Read OpenCode's SQLite DB and return per-day token summaries."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur = con.execute(
        "SELECT data FROM message WHERE json_valid(data) AND json_extract(data, '$.role') = 'assistant'"
    )
    day_sum: dict[str, dict] = {}
    for (raw,) in cur:
        try:
            row = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        tokens = row.get("tokens")
        if not tokens:
            continue
        time_info = row.get("time", {})
        created_ms = time_info.get("created")
        if created_ms is None:
            continue
        created_dt = dt.datetime.fromtimestamp(created_ms / 1000, tz=dt.timezone.utc)
        day = created_dt.strftime("%Y-%m-%d")
        if day not in day_sum:
            day_sum[day] = {"day": day, "source": "opencode", "generated": 0, "prompt": 0}
        out = tokens.get("output", 0) or 0
        reason = tokens.get("reasoning", 0) or 0
        inp = tokens.get("input", 0) or 0
        day_sum[day]["generated"] += out + reason
        day_sum[day]["prompt"] += inp
    con.close()
    return [day_sum[k] for k in sorted(day_sum)]


def send(url: str, token: str, events: list[dict], tokens: list[dict]) -> list[dict]:
    """Send events and tokens to the status service in batches of 1000."""
    answers = []
    batch_size = 1000
    for i in range(0, len(events), batch_size):
        batch = events[i:i + batch_size]
        body = {"events": batch}
        if i == 0 and tokens:
            body["tokens"] = tokens
        payload = json.dumps(body).encode()
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        try:
            resp = urllib.request.urlopen(req)
            answers.append(json.loads(resp.read()))
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"HTTP {e.code}: {e.read().decode()}")
    return answers


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill history into agent-status")
    parser.add_argument("--repos", default=Path.home() / "repos", help="Directory with repos")
    parser.add_argument("--opencode-db", default=Path.home() / ".local" / "share" / "opencode" / "opencode.db")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    status_url = os.environ.get("STATUS_URL", "")
    status_token = os.environ.get("STATUS_TOKEN", "")

    if not args.dry_run and (not status_url or not status_token):
        print("ERROR: STATUS_URL and STATUS_TOKEN must be set (or use --dry-run)", file=sys.stderr)
        sys.exit(2)

    all_events: list[dict] = []
    all_tokens: list[dict] = []

    repos_dir = Path(args.repos)
    if repos_dir.is_dir():
        for d in sorted(repos_dir.iterdir()):
            if d.is_dir() and (d / ".git").exists():
                try:
                    evts = git_events(d, d.name)
                    all_events.extend(evts)
                    print(f"  {d.name}: {len(evts)} events")
                except Exception as exc:
                    print(f"  {d.name}: failed: {exc}", file=sys.stderr)

    opencode_db = Path(args.opencode_db)
    if opencode_db.exists():
        try:
            toks = opencode_tokens(opencode_db)
            all_tokens.extend(toks)
            print(f"  opencode: {len(toks)} token days")
        except Exception as exc:
            print(f"  opencode: failed: {exc}", file=sys.stderr)

    if args.dry_run:
        print(f"DRY RUN: {len(all_events)} events, {len(all_tokens)} token days")
        return

    if all_events:
        answers = send(status_url, status_token, all_events, all_tokens)
        for a in answers:
            print(f"  import: {a.get('events_added', '?')} added, {a.get('events_skipped', '?')} skipped")
    else:
        print("No events to send")


if __name__ == "__main__":
    main()
