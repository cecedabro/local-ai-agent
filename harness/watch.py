#!/usr/bin/env python3
"""agent-watch: start a CI-fix run when a workflow fails on main.

Every --interval seconds it asks GitHub for the workflow runs of the newest commit
on main. When one has failed and that commit was not handled before, it checks out
a fresh main and runs `agent-loop --fix-ci <run id>`: a fix task on a new agent/
branch, CI re-checked, and a PR for the human. One attempt per failing commit.

Run it in its own tmux window, inside the project repo:
    agent-watch                  # poll every 10 minutes
    agent-watch --once           # one check, for testing

It skips a cycle while an agent-loop holds .agent/lock or the tree is dirty, and
stops when .agent/WATCH-STOP exists. `ai pause` on the PC also holds it back,
because agent-loop waits for `ai resume` before using the model.
Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from loop import CI_OK, STATE_DIR, git, notify, now, sh  # noqa: E402


def latest_main_failure(repo: Path, branch: str) -> tuple[str, dict] | None:
    """(sha, failed run) for the newest commit on the branch if any of its runs failed."""
    res = sh(["gh", "run", "list", "--branch", branch, "--limit", "30",
              "--json", "databaseId,name,status,conclusion,headSha,event"], cwd=repo, timeout=60)
    if res.returncode != 0:
        print(f"[{now()}] gh run list failed: {res.stdout.strip()[-300:]}", file=sys.stderr)
        return None
    runs = json.loads(res.stdout or "[]")
    if not runs:
        return None
    sha = runs[0]["headSha"]                      # newest first
    mine = [r for r in runs if r["headSha"] == sha]
    if any(r["status"] != "completed" for r in mine):
        return None                               # still running: look again next cycle
    failed = [r for r in mine if r["conclusion"] not in CI_OK]
    return (sha, failed[0]) if failed else None


def busy(repo: Path, branch: str) -> str | None:
    lock = repo / STATE_DIR / "lock"
    if lock.exists():
        try:
            os.kill(int(lock.read_text().strip()), 0)
            return "an agent-loop is running"
        except (ValueError, ProcessLookupError):
            pass
        except PermissionError:
            return "an agent-loop is running"
    if git(repo, "status", "--porcelain"):
        return "working tree not clean"
    git(repo, "fetch", "-q", "origin", branch, check=False)
    if git(repo, "rev-list", f"origin/{branch}..{branch}", check=False):
        return f"local {branch} has unpushed commits"
    return None


def cycle(repo: Path, args: argparse.Namespace) -> None:
    found = latest_main_failure(repo, args.branch)
    if not found:
        return
    sha, run = found
    handled_file = repo / STATE_DIR / "watch-handled"
    handled = set(handled_file.read_text().split()) if handled_file.exists() else set()
    if sha in handled:
        return
    reason = busy(repo, args.branch)
    if reason:
        print(f"[{now()}] {args.branch} {sha[:7]} failed ({run['name']}), but {reason}; later")
        return
    print(f"[{now()}] {args.branch} {sha[:7]}: {run['name']} failed (run {run['databaseId']}); fixing")
    notify("CI failed on main", f"{repo.name} {sha[:7]}: {run['name']}. Starting a fix run.", "rotating_light")
    with handled_file.open("a") as fh:                 # before the run: one attempt per commit
        fh.write(sha + "\n")
    git(repo, "fetch", "-q", "origin", args.branch)
    git(repo, "checkout", "-q", "-B", args.branch, f"origin/{args.branch}")
    cmd = [sys.executable, str(HERE / "loop.py"), "--fix-ci", str(run["databaseId"]), *args.loop_args]
    subprocess.run(cmd, cwd=repo, check=False)
    git(repo, "checkout", "-q", args.branch, check=False)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--repo", type=Path, default=Path.cwd())
    p.add_argument("--branch", default="main")
    p.add_argument("--interval", type=int, default=600)
    p.add_argument("--once", action="store_true")
    p.add_argument("loop_args", nargs="*", help="extra agent-loop arguments, after --")
    args = p.parse_args()
    repo = args.repo.resolve()
    (repo / STATE_DIR).mkdir(exist_ok=True)
    print(f"[{now()}] watching {args.branch} of {repo.name} every {args.interval}s")
    while True:
        if (repo / STATE_DIR / "WATCH-STOP").exists():
            print(f"[{now()}] WATCH-STOP present; exiting")
            return 0
        try:
            cycle(repo, args)
        except (RuntimeError, json.JSONDecodeError, KeyError) as exc:
            print(f"[{now()}] watch cycle error: {exc}", file=sys.stderr)
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
