#!/usr/bin/env python3
"""agent-queue: run the jobs queued on the agent-status page, one at a time.

Each job names a GitHub repo (owner/name) and optionally a branch. The worker clones or
updates it under --repos, checks out that branch (default: the repo's default branch) and
runs agent-loop there with AGENT_JOB_ID set, so the run shows up under its job. While the
run is going it sends a heartbeat; a Stop on the page makes it touch .agent/STOP, so the
run ends after its current task.

The devbox starts it in tmux window `queue` of session `agent` (devbox/entrypoint.sh):
    agent-queue                 # until Ctrl-C
    agent-queue --once          # take at most one job, then exit
    touch ~/.agent-queue-STOP   # pause: take no new jobs (the page shows it); rm to resume

Environment: STATUS_URL, STATUS_TOKEN (required), plus everything agent-loop reads.
    QUEUE_OWNERS     comma-separated GitHub owners whose repos may be run (required)
    QUEUE_GIT_BASE   clone URL prefix (default https://github.com/; the job's repo + .git is appended)
    AGENT_LOOP_CMD   the loop command (default: agent-loop)
The worker also tells the page which repos the owners have (`gh repo list`, every 10 min),
so the page can offer them in a dropdown.
Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

STOP_FILE = Path.home() / ".agent-queue-STOP"


def now() -> str:
    return time.strftime("%H:%M:%S")


class Client:
    def __init__(self, url: str, token: str) -> None:
        self.url, self.token = url.rstrip("/"), token

    def post(self, path: str, body: dict | None = None) -> dict | None:
        req = urllib.request.Request(
            self.url + path, data=json.dumps(body or {}).encode(), method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as exc:
            print(f"[{now()}] {path}: HTTP {exc.code} {exc.read()[:200]!r}", file=sys.stderr)
            return None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            print(f"[{now()}] {path}: {exc}", file=sys.stderr)
            return None


REPO_REFRESH = 600


def owner_repos(owners: set[str]) -> list[str]:
    """owner/name of every non-archived repo of the owners, via gh; [] if gh fails."""
    found: list[str] = []
    for owner in sorted(owners):
        try:
            res = subprocess.run(["gh", "repo", "list", owner, "--no-archived", "--limit", "200",
                                  "--json", "nameWithOwner", "-q", ".[].nameWithOwner"],
                                 capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if res.returncode == 0:
            found += [r.strip() for r in res.stdout.splitlines() if r.strip().count("/") == 1]
    return sorted(set(found), key=str.lower)


PLAN_TASK = re.compile(r"^- \[(?P<mark>[ x~])\] (?P<id>[A-Z]{1,3}\d+): (?P<title>.+?)\s*$")


def parse_plan(text: str) -> dict:
    """Title and task counts of a PLAN.md, plus the next three open tasks (short titles)."""
    title, tasks = "", []
    for line in text.splitlines():
        if not title and line.startswith("#"):
            m = re.match(r"#\s*PLAN\s*:?\s*(.*)$", line.strip())
            title = (m.group(1) if m else line.lstrip("# ")).strip()
        m = PLAN_TASK.match(line)
        if m:
            short = re.split(r" \(see | — |\. ", m["title"])[0][:90]
            tasks.append((m["mark"], f'{m["id"]} {short}'))
    open_ = [t for mark, t in tasks if mark == " "]
    return {"title": title[:120], "total": len(tasks), "done": sum(1 for mark, _ in tasks if mark == "x"),
            "skipped": sum(1 for mark, _ in tasks if mark == "~"), "open": len(open_), "next": open_[:3]}


def gh(*args: str) -> str | None:
    try:
        res = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return res.stdout if res.returncode == 0 else None


def repo_plans(repos: list[str], cache: dict) -> list[dict]:
    """Every branch (default branch or agent/*) with a PLAN.md that still has open tasks.

    `cache` maps (repo, branch) -> (commit sha, plan or None), so a branch is only re-read after a
    new commit. Starting a loop on the default branch makes agent-loop create agent/<plan-title>.
    """
    out: list[dict] = []
    for repo in repos:
        default = (gh("api", f"repos/{repo}", "-q", ".default_branch") or "").strip()
        listing = gh("api", "--paginate", f"repos/{repo}/branches", "-q", '.[] | [.name, .commit.sha] | @tsv')
        if not default or listing is None:
            continue
        for line in listing.splitlines():
            name, _, sha = line.partition("\t")
            if name != default and not name.startswith("agent/"):
                continue
            hit = cache.get((repo, name))
            if hit and hit[0] == sha:
                plan = hit[1]
            else:
                text = gh("api", "-H", "Accept: application/vnd.github.raw", f"repos/{repo}/contents/PLAN.md?ref={name}")
                plan = parse_plan(text) if text else None
                cache[(repo, name)] = (sha, plan)
            if plan and plan["open"]:
                out.append({"repo": repo, "branch": name, "default": name == default, **plan})
    return out[:200]


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=600)


def checkout(job: dict, repos: Path, git_base: str) -> tuple[Path | None, str]:
    """Clone or update the job's repo and check out its branch. Returns (path, error)."""
    name = job["repo"].split("/", 1)[1]
    path = repos / name
    if not (path / ".git").exists():
        if path.exists():
            return None, f"{path} exists but is not a git repo"
        res = subprocess.run(["git", "clone", "-q", f"{git_base}{job['repo']}.git", str(path)],
                             capture_output=True, text=True, timeout=600)
        if res.returncode:
            return None, f"clone failed: {res.stderr.strip()[-300:]}"
    lock = path / ".agent" / "lock"
    if lock.exists():
        try:
            os.kill(int(lock.read_text().strip()), 0)
            return None, "another agent-loop is already running in this repo"
        except (ValueError, ProcessLookupError):
            pass
        except PermissionError:
            return None, "another agent-loop is already running in this repo"
    if git(path, "status", "--porcelain").stdout.strip():
        return None, f"{path} has uncommitted changes; clean it up first"
    res = git(path, "fetch", "-q", "--prune", "origin")
    if res.returncode:
        return None, f"fetch failed: {res.stderr.strip()[-300:]}"
    ref = job.get("ref") or ""
    if not ref:
        git(path, "remote", "set-head", "origin", "--auto")
        head = git(path, "symbolic-ref", "--short", "refs/remotes/origin/HEAD").stdout.strip()
        ref = head.removeprefix("origin/") or "main"
    res = git(path, "checkout", "-q", "-B", ref, f"origin/{ref}")
    if res.returncode:
        return None, f"no branch {ref} on origin: {res.stderr.strip()[-200:]}"
    if not (path / "PLAN.md").exists():
        return None, f"no PLAN.md on {ref}"
    return path, ""


def run_job(client: Client, job: dict, args: argparse.Namespace) -> None:
    jid = job["id"]
    print(f"[{now()}] job #{jid}: {job['repo']} {job.get('ref') or ''}".rstrip())
    owner = job["repo"].split("/", 1)[0].lower()
    if owner not in args.owners:
        err = f"owner {owner} is not in QUEUE_OWNERS"
        print(f"[{now()}] job #{jid} refused: {err}")
        client.post(f"/api/jobs/{jid}/finish", {"state": "failed", "detail": err})
        return
    path, err = checkout(job, args.repos, args.git_base)
    if path is None:
        print(f"[{now()}] job #{jid} failed: {err}")
        client.post(f"/api/jobs/{jid}/finish", {"state": "failed", "detail": err})
        return
    cmd = shlex.split(os.environ.get("AGENT_LOOP_CMD", "agent-loop"))
    cmd += ["--max-hours", str(job.get("max_hours") or 24)]
    if not job.get("ci", True):
        cmd.append("--no-ci")
    env = {**os.environ, "AGENT_JOB_ID": str(jid)}
    proc = subprocess.Popen(cmd, cwd=path, env=env)
    stopping = threading.Event()
    killed = threading.Event()

    def beat() -> None:
        while proc.poll() is None:
            res = client.post(f"/api/jobs/{jid}/heartbeat")
            if res and res.get("cancel") and not stopping.is_set():
                print(f"[{now()}] job #{jid}: stop requested; the run ends after its current task")
                (path / ".agent").mkdir(exist_ok=True)
                (path / ".agent" / "STOP").touch()
                stopping.set()
            if res and res.get("now") and not killed.is_set():
                print(f"[{now()}] job #{jid}: stop now requested; interrupting agent-loop")
                killed.set()
                proc.send_signal(signal.SIGINT)   # agent-loop cleans up: kills the agent, frees the model and PC
            for _ in range(int(args.heartbeat * 10)):
                if proc.poll() is not None:
                    return
                time.sleep(0.1)

    t = threading.Thread(target=beat, daemon=True)
    t.start()
    try:
        code = proc.wait()
    except KeyboardInterrupt:
        (path / ".agent").mkdir(exist_ok=True)
        (path / ".agent" / "STOP").touch()
        print(f"[{now()}] interrupted; waiting for agent-loop to finish its current task (Ctrl-C again kills it)")
        try:
            code = proc.wait()
        except KeyboardInterrupt:
            proc.terminate()
            code = proc.wait()
        client.post(f"/api/jobs/{jid}/finish", {"state": "cancelled", "exit_code": code,
                                                 "detail": "worker interrupted"})
        raise
    t.join(timeout=2)
    (path / ".agent" / "STOP").unlink(missing_ok=True)
    state = "cancelled" if stopping.is_set() else ("done" if code == 0 else "failed")
    detail = ("stopped now from the status page" if killed.is_set() else "stopped from the status page") \
        if stopping.is_set() else ("" if code == 0 else f"agent-loop exited {code}")
    print(f"[{now()}] job #{jid} {state} {detail}".rstrip())
    client.post(f"/api/jobs/{jid}/finish", {"state": state, "exit_code": code, "detail": detail})


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--repos", type=Path, default=Path.home() / "repos", help="where repos are cloned")
    p.add_argument("--poll", type=float, default=30, help="seconds between queue checks when idle")
    p.add_argument("--heartbeat", type=float, default=30)
    p.add_argument("--once", action="store_true", help="take at most one job, then exit")
    args = p.parse_args()
    args.git_base = os.environ.get("QUEUE_GIT_BASE", "https://github.com/")
    url, token = os.environ.get("STATUS_URL", ""), os.environ.get("STATUS_TOKEN", "")
    if not url or not token:
        sys.exit("STATUS_URL and STATUS_TOKEN must be set")
    args.owners = {o.strip().lower() for o in os.environ.get("QUEUE_OWNERS", "").split(",") if o.strip()}
    if not args.owners:
        sys.exit("QUEUE_OWNERS must be set (e.g. your GitHub user)")
    args.repos.mkdir(parents=True, exist_ok=True)
    client = Client(url, token)
    worker = socket.gethostname()
    print(f"[{now()}] agent-queue on {worker}: polling {url} (pause: touch {STOP_FILE})")
    told_down = told_paused = False
    repos: list[str] = []
    plans: list[dict] = []
    plan_cache: dict = {}
    repos_at = -REPO_REFRESH
    while True:
        if time.monotonic() - repos_at >= REPO_REFRESH:
            repos, repos_at = owner_repos(args.owners) or repos, time.monotonic()
            plans = repo_plans(repos, plan_cache)
        if STOP_FILE.exists():
            if not told_paused:
                print(f"[{now()}] paused ({STOP_FILE} exists); rm it to resume")
                told_paused = True
            client.post("/api/workers/ping", {"worker": worker, "paused": True, "repos": repos, "plans": plans})
            if args.once:
                return 0
            try:
                time.sleep(args.poll)
            except KeyboardInterrupt:
                return 130
            continue
        if told_paused:
            print(f"[{now()}] resumed")
            told_paused = False
        res = client.post("/api/jobs/claim", {"worker": worker, "repos": repos, "plans": plans})
        if res is None and not told_down:
            print(f"[{now()}] status service unreachable; retrying every {args.poll:g} s")
            told_down = True
        if res is not None:
            told_down = False
        job = (res or {}).get("job")
        if job:
            try:
                run_job(client, job, args)
            except KeyboardInterrupt:
                return 130
            repos_at = -REPO_REFRESH                    # progress changed: re-read the plans
        if args.once:
            return 0
        if not job:
            try:
                time.sleep(args.poll)
            except KeyboardInterrupt:
                return 130


if __name__ == "__main__":
    sys.exit(main())
