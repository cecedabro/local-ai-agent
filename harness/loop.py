#!/usr/bin/env python3
"""agent-loop: Ralph-style autonomous coding loop for a local model.

One task per fresh agent session. PLAN.md is the only state between tasks.
The harness, not the model, decides when a task is done: it runs the task's
acceptance command plus a project-wide check. Pass -> commit. Fail -> reset
and retry with the failure output. Too many failures -> skip and move on.

After the tasks, the harness waits for CI on the pushed branch. A failed check
becomes a fix task (with the failed log) and the branch is pushed again, up to
--ci-rounds times. `agent-watch` (watch.py) does the same for failures on main.

Run from inside a project repo (see harness/templates/):
    agent-loop                  # full run: wake PC, work through PLAN.md, wait for CI, open a PR
    agent-loop --fix-ci 123456  # only fix the failed GitHub Actions run 123456 (used by agent-watch)
    agent-loop --no-pc --no-push --retries 1   # local dry run

Protected paths: the PROTECTED defaults plus every line of `.agent-protected` in
the repo root (one path per line, `#` comments; the file protects itself).

Environment (set by the devbox ConfigMap/Secret):
    LLAMA_BASE_URL  e.g. http://192.168.50.50:8080/v1
    LLAMA_API_KEY   llama-server API key
    WAKER_URL       e.g. http://192.168.50.11:9009 (waker on k3s-worker-01)
    WAKER_TOKEN     shared token for the waker
    NTFY_URL, NTFY_TOPIC, NTFY_TOKEN   notifications (optional)
    AGENT_CMD       agent command; "{prompt}" is replaced by the prompt
                    default: opencode run --model pc/coder {prompt}
    CHECK_CMD       project-wide check (default: make check)
    PROTECTED       comma-separated paths the agent may not change
                    default: tests/,contract/,PLAN.md,SPEC.md,AGENTS.md
Stdlib only: this runs in a minimal image.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
TASK_RE = re.compile(r"^- \[(?P<mark>[ x~])\] (?P<id>T\d+): (?P<title>.+?)\s*$")
ACCEPT_RE = re.compile(r"^\s+accept:\s*`?(?P<cmd>.+?)`?\s*$")
STATE_DIR = ".agent"          # logs + STOP file; kept out of git via .git/info/exclude
PROTECT_FILE = ".agent-protected"
FAIL_TAIL_LINES = 80
CI_OK = {"success", "skipped", "neutral"}


# --------------------------------------------------------------------------- utils

def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None:
        sys.exit(f"missing environment variable {name}")
    return value


def sh(cmd: str | list[str], *, cwd: Path, timeout: int | None = None,
       check: bool = False) -> subprocess.CompletedProcess[str]:
    """Run a command, merge stderr into stdout, never raise on timeout."""
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, shell=isinstance(cmd, str), text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode(errors="replace")
        proc = subprocess.CompletedProcess(cmd, 124, out + f"\n[timed out after {timeout}s]\n")
    if check and proc.returncode != 0:
        raise RuntimeError(f"command failed ({proc.returncode}): {cmd}\n{proc.stdout}")
    return proc


def git(repo: Path, *args: str, check: bool = True) -> str:
    return sh(["git", *args], cwd=repo, check=check).stdout.strip()


def tail(text: str, n: int = FAIL_TAIL_LINES) -> str:
    return "\n".join(text.rstrip().splitlines()[-n:])


def now() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M")


# --------------------------------------------------------------------------- notify

def notify(title: str, message: str, tags: str = "robot") -> None:
    url = os.environ.get("NTFY_URL")
    if not url:
        print(f"[notify] {title}: {message}")
        return
    topic = os.environ.get("NTFY_TOPIC", "agent")
    headers = {"Title": title, "Tags": tags}
    if os.environ.get("NTFY_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['NTFY_TOKEN']}"
    req = urllib.request.Request(f"{url.rstrip('/')}/{topic}", data=message.encode(),
                                 headers=headers, method="POST")
    try:
        urllib.request.urlopen(req, timeout=10).close()
    except (urllib.error.URLError, OSError) as exc:
        print(f"[notify failed] {exc}: {title}: {message}", file=sys.stderr)


# --------------------------------------------------------------------------- PC power

class PcUnavailable(RuntimeError):
    pass


class Pc:
    """Wakes the PC, keeps it awake while working, restarts llama-server if needed."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled
        self.base = os.environ.get("LLAMA_BASE_URL", "").rstrip("/")
        self.key = os.environ.get("LLAMA_API_KEY", "")
        self._stop = threading.Event()
        self._beat: threading.Thread | None = None

    # -- probes
    def _http(self, url: str, body: dict | None = None, timeout: int = 60) -> int:
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}
        req = urllib.request.Request(url, data=data, headers=headers,
                                     method="POST" if data else "GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status
        except urllib.error.HTTPError as exc:
            return exc.code
        except (urllib.error.URLError, OSError):
            return 0

    def completion_ok(self) -> bool:
        body = {"model": "coder", "max_tokens": 1,
                "messages": [{"role": "user", "content": "ping"}]}
        return self._http(f"{self.base}/chat/completions", body, timeout=90) == 200

    def health_ok(self) -> bool:
        root = self.base[:-3] if self.base.endswith("/v1") else self.base
        return self._http(f"{root}/health", timeout=10) == 200

    def ssh(self, verb: str) -> str | None:
        proc = subprocess.run(["ssh", "-o", "BatchMode=yes", "pc", verb], text=True,
                              capture_output=True, timeout=30, check=False)
        return proc.stdout.strip() if proc.returncode == 0 else None

    def wake(self) -> None:
        url = os.environ.get("WAKER_URL")
        if not url:
            return
        req = urllib.request.Request(f"{url.rstrip('/')}/wake", data=b"", method="POST",
                                     headers={"X-Waker-Token": os.environ.get("WAKER_TOKEN", "")})
        try:
            urllib.request.urlopen(req, timeout=10).close()
        except (urllib.error.URLError, OSError) as exc:
            print(f"[wake failed] {exc}", file=sys.stderr)

    # -- lifecycle
    def up(self, timeout: int = 240) -> None:
        """Return when the model answers, or raise PcUnavailable."""
        if not self.enabled or self.completion_ok():
            return
        print(f"[{now()}] PC not answering; waking")
        self.wake()
        deadline = time.monotonic() + timeout
        restarted = paused_notified = False
        last_wake = time.monotonic()
        while time.monotonic() < deadline:
            status = self.ssh("status")
            if status == "paused":
                if not paused_notified:
                    notify("PC paused", "Waiting for `ai resume` on the PC.", "pause_button")
                    paused_notified = True
                time.sleep(300)
                deadline = time.monotonic() + timeout   # waiting on you, not a fault
                continue
            if status is None:                          # still asleep / WSL not up
                if time.monotonic() - last_wake > 30:
                    self.wake()
                    last_wake = time.monotonic()
            else:
                if self.completion_ok():
                    print(f"[{now()}] PC ready")
                    return
                # Service down, or up but completions fail (CUDA lost on resume).
                if not restarted and (status == "down" or self.health_ok()):
                    print(f"[{now()}] restarting llama-server")
                    self.ssh("restart")
                    restarted = True
                    deadline = max(deadline, time.monotonic() + 180)
            time.sleep(5)
        raise PcUnavailable("PC did not become ready")

    def hold(self) -> None:
        """Keep the PC awake: touch the busy flag now and every 5 minutes."""
        if not self.enabled:
            return
        self.ssh("busy on")

        def beat() -> None:
            while not self._stop.wait(300):
                self.ssh("busy on")

        self._beat = threading.Thread(target=beat, daemon=True)
        self._beat.start()

    def release(self) -> None:
        if not self.enabled:
            return
        self._stop.set()
        self.ssh("busy off")


# --------------------------------------------------------------------------- plan

@dataclass
class Task:
    id: str
    title: str
    accept: str
    line: int                  # -1: not in PLAN.md (a CI fix task)
    context: str = ""          # extra text for the prompt, e.g. a failed CI log


def parse_plan(text: str) -> list[Task]:
    """Unchecked tasks only, in order. Format:
        - [ ] T03: Add POST /readings
          accept: pytest backend/tests/test_readings.py
    """
    lines = text.splitlines()
    tasks: list[Task] = []
    for i, line in enumerate(lines):
        m = TASK_RE.match(line)
        if not m or m["mark"] != " ":
            continue
        accept = ""
        for nxt in lines[i + 1:i + 4]:
            if TASK_RE.match(nxt):
                break
            a = ACCEPT_RE.match(nxt)
            if a:
                accept = a["cmd"]
                break
        tasks.append(Task(m["id"], m["title"], accept, i))
    return tasks


def mark_task(plan: Path, task: Task, mark: str, note: str = "") -> None:
    lines = plan.read_text().splitlines()
    line = lines[task.line]
    assert TASK_RE.match(line), f"PLAN.md changed under us at line {task.line + 1}"
    new = line.replace("- [ ]", f"- [{mark}]", 1)
    if note:
        new += f" — {note}"
    lines[task.line] = new
    plan.write_text("\n".join(lines) + "\n")


def load_protected(repo: Path, defaults: list[str]) -> list[str]:
    """Defaults plus the repo's .agent-protected lines; the file always protects itself."""
    extra: list[str] = []
    path = repo / PROTECT_FILE
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                extra.append(line)
    out: list[str] = []
    for p in [*defaults, *extra, PROTECT_FILE]:
        if p not in out:
            out.append(p)
    return out


# --------------------------------------------------------------------------- CI (GitHub Actions via gh)

def ci_runs(repo: Path, branch: str, sha: str) -> list[dict] | None:
    """Workflow runs for one commit on a branch, or None if gh failed."""
    res = sh(["gh", "run", "list", "--branch", branch, "--commit", sha, "--limit", "50",
              "--json", "databaseId,name,status,conclusion"], cwd=repo, timeout=60)
    if res.returncode != 0:
        print(f"[ci] gh run list failed: {tail(res.stdout, 5)}", file=sys.stderr)
        return None
    try:
        return json.loads(res.stdout or "[]")
    except json.JSONDecodeError:
        return None


def ci_failure_log(repo: Path, run_id: int | str, lines: int = 60) -> str:
    """The tail of a failed run's log, without gh's job/step/timestamp prefix columns."""
    res = sh(["gh", "run", "view", str(run_id), "--log-failed"], cwd=repo, timeout=120)
    out = []
    for line in res.stdout.splitlines():
        parts = line.split("\t", 2)
        text = parts[-1]
        text = re.sub(r"^﻿?\d{4}-\d\d-\d\dT[\d:.]+Z ", "", text)
        if text.strip():
            out.append(text)
    return "\n".join(out[-lines:]) or tail(res.stdout, lines)


def ci_fix_task(n: int, failed: list[tuple[str, str]]) -> Task:
    names = ", ".join(name for name, _ in failed)
    logs = "\n\n".join(f"### CI job `{name}` failed. Log tail:\n```\n{log}\n```" for name, log in failed)
    title = (f"CI failed on this branch ({names}). Find the cause in the log below, reproduce it "
             "locally if you can (most CI steps are `make check` or a script in the repo), and fix "
             "the code or config that is wrong. Never weaken or skip a check to make it pass.")
    return Task(f"CI{n}", title, "", -1, logs)


# --------------------------------------------------------------------------- loop

@dataclass
class Result:
    task: Task
    status: str            # done | skipped
    attempts: int
    reason: str = ""
    seconds: float = 0.0


@dataclass
class Run:
    repo: Path
    args: argparse.Namespace
    pc: Pc
    branch: str = ""
    results: list[Result] = field(default_factory=list)
    ci_notes: list[str] = field(default_factory=list)
    started: float = field(default_factory=time.monotonic)

    @property
    def state(self) -> Path:
        return self.repo / STATE_DIR

    def stop_reason(self) -> str | None:
        if (self.state / "STOP").exists():
            return "STOP file present"
        if time.monotonic() - self.started > self.args.max_hours * 3600:
            return f"wall-clock cap of {self.args.max_hours} h reached"
        return None

    # -- setup
    def prepare(self) -> None:
        if not (self.repo / "PLAN.md").exists():
            sys.exit("PLAN.md not found; run from a project repo")
        self.state.mkdir(exist_ok=True)
        (self.state / "logs").mkdir(exist_ok=True)
        exclude = self.repo / ".git" / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        existing = exclude.read_text(errors="ignore") if exclude.exists() else ""
        if f"{STATE_DIR}/" not in existing.split():
            with exclude.open("a") as fh:
                fh.write(f"\n{STATE_DIR}/\n")
        if git(self.repo, "status", "--porcelain"):
            sys.exit("working tree not clean; commit or stash first")
        lock = self.state / "lock"
        if lock.exists():
            try:
                os.kill(int(lock.read_text().strip()), 0)
                sys.exit(f"another agent-loop is running here (pid in {lock})")
            except (ValueError, ProcessLookupError):
                pass                                      # stale lock from a crashed run
            except PermissionError:
                sys.exit(f"another agent-loop is running here (pid in {lock})")
        lock.write_text(str(os.getpid()))
        self.args.protected = load_protected(self.repo, self.args.protected)
        current = git(self.repo, "rev-parse", "--abbrev-ref", "HEAD")
        if current.startswith("agent/"):
            self.branch = current                         # resume an interrupted run
        else:
            stamp = f"ci-fix-{self.args.fix_ci}" if self.args.fix_ci else f"{dt.datetime.now():%Y-%m-%d-%H%M}"
            self.branch = f"agent/{stamp}"
            git(self.repo, "checkout", "-b", self.branch)

    # -- one attempt
    def prompt(self, task: Task, failure: str) -> str:
        template = (HERE / "prompt.md").read_text()
        progress_file = self.repo / "PROGRESS.md"
        progress = tail(progress_file.read_text(), 40) if progress_file.exists() else "(none yet)"
        values = {
            "task_id": task.id, "task": task.title + (f"\n\n{task.context}" if task.context else ""),
            "accept": task.accept or "(none; the project check must pass)",
            "check": self.args.check_cmd, "protected": ", ".join(self.args.protected),
            "progress": progress,
            "failure": f"The previous attempt failed. Last output:\n```\n{failure}\n```" if failure else "",
        }
        for key, value in values.items():
            template = template.replace("{{" + key + "}}", value)
        return template

    def changed_paths(self) -> list[str]:
        out = git(self.repo, "status", "--porcelain", "--untracked-files=all")
        paths = []
        for line in out.splitlines():
            path = line[3:]
            if " -> " in path:                            # renames: check both sides
                paths.extend(path.split(" -> "))
            else:
                paths.append(path)
        return [p.strip('"') for p in paths]

    def attempt(self, task: Task, n: int, failure: str) -> tuple[bool, str]:
        self.pc.up()
        argv = [a.replace("{prompt}", self.prompt(task, failure)) for a in self.args.agent_cmd]
        log = self.state / "logs" / f"{task.id}-a{n}.log"
        agent = sh(argv, cwd=self.repo, timeout=self.args.attempt_timeout)
        log.write_text(agent.stdout)

        changed = self.changed_paths()
        if not changed:
            return False, "agent made no changes\n" + tail(agent.stdout, 30)
        bad = [p for p in changed if any(p == q.rstrip("/") or p.startswith(q) for q in self.args.protected)]
        if bad:
            return False, f"changed protected paths: {', '.join(bad)} (fix the code, not the tests/spec)"

        checks = [c for c in (task.accept, self.args.check_cmd) if c]
        for cmd in checks:
            res = sh(cmd, cwd=self.repo, timeout=self.args.check_timeout)
            with log.open("a") as fh:
                fh.write(f"\n$ {cmd}\n{res.stdout}")
            if res.returncode != 0:
                return False, f"$ {cmd}  (exit {res.returncode})\n{tail(res.stdout)}"
        return True, ""

    def reset(self, head: str) -> None:
        git(self.repo, "reset", "--hard", head)
        git(self.repo, "clean", "-fd")                    # .agent/ survives via exclude

    def push(self) -> None:
        if self.args.push:
            res = sh(["git", "push", "-u", "origin", self.branch], cwd=self.repo, timeout=120)
            if res.returncode != 0:
                print(f"[push failed] {tail(res.stdout, 10)}", file=sys.stderr)

    # -- one task
    def run_task(self, task: Task) -> Result:
        t0 = time.monotonic()
        failure = ""
        for n in range(1, self.args.retries + 1):
            head = git(self.repo, "rev-parse", "HEAD")
            print(f"[{now()}] {task.id} attempt {n}/{self.args.retries}: {task.title}")
            ok, failure = self.attempt(task, n, failure)
            if ok:
                if task.line >= 0:
                    mark_task(self.repo / "PLAN.md", task, "x")
                git(self.repo, "add", "-A")
                git(self.repo, "commit", "-q", "-m", f"{task.id}: {task.title}")
                self.push()
                notify(f"{task.id} done", task.title, "white_check_mark")
                return Result(task, "done", n, seconds=time.monotonic() - t0)
            print(f"[{now()}] {task.id} attempt {n} failed: {failure.splitlines()[0] if failure else ''}")
            self.reset(head)
            if self.stop_reason():
                break
        reason = failure.splitlines()[0][:160] if failure else "unknown"
        if task.line >= 0:
            mark_task(self.repo / "PLAN.md", task, "~", f"skipped: {reason}")
            git(self.repo, "commit", "-q", "-am", f"{task.id}: skipped")
        notify(f"{task.id} skipped", f"{task.title}\n{reason}", "warning")
        return Result(task, "skipped", self.args.retries, reason, time.monotonic() - t0)

    # -- whole run
    def loop(self) -> str:
        consecutive_skips = 0
        while True:
            reason = self.stop_reason()
            if reason:
                return reason
            tasks = parse_plan((self.repo / "PLAN.md").read_text())
            if not tasks:
                return "plan complete"
            result = self.run_task(tasks[0])
            self.results.append(result)
            consecutive_skips = consecutive_skips + 1 if result.status == "skipped" else 0
            if consecutive_skips >= self.args.max_skips:
                return f"{consecutive_skips} skips in a row (spec or environment problem?)"

    # -- CI feedback
    def wait_ci(self) -> list[tuple[str, str]] | None:
        """Wait for every workflow run on the pushed HEAD. [] = green, None = no CI / unknown."""
        sha = git(self.repo, "rev-parse", "HEAD")
        appear_by = time.monotonic() + self.args.ci_appear
        deadline = time.monotonic() + self.args.ci_timeout
        print(f"[{now()}] waiting for CI on {sha[:7]}")
        while True:
            runs = ci_runs(self.repo, self.branch, sha)
            if runs is None:
                return None
            if not runs and time.monotonic() > appear_by:
                return None
            if runs and all(r.get("status") == "completed" for r in runs):
                failed = [r for r in runs if r.get("conclusion") not in CI_OK]
                return [(r["name"], ci_failure_log(self.repo, r["databaseId"])) for r in failed]
            if time.monotonic() > deadline:
                return [("timeout", f"CI did not finish within {self.args.ci_timeout} s")]
            time.sleep(self.args.ci_poll)

    def ci_gate(self, first: list[tuple[str, str]] | None = None) -> None:
        """Wait for CI; turn failures into fix tasks until green or out of rounds.
        `first` skips the initial wait (agent-watch passes the failure it saw on main)."""
        if not (self.args.ci and self.args.push):
            return
        if first is None and self.stop_reason():
            self.ci_notes.append(f"CI not checked: {self.stop_reason()}.")
            return
        self.push()                                       # a trailing "skipped" commit is not pushed yet
        failed = first
        for n in range(1, self.args.ci_rounds + 1):
            if failed is None:
                failed = self.wait_ci()
            if failed is None:
                self.ci_notes.append("No CI runs found for the last commit (or gh failed).")
                return
            if not failed:
                self.ci_notes.append(f"CI green on {git(self.repo, 'rev-parse', '--short', 'HEAD')}.")
                notify("CI green", self.branch, "white_check_mark")
                return
            names = ", ".join(name for name, _ in failed)
            self.ci_notes.append(f"CI round {n}: failed ({names}).")
            notify("CI failed", f"{self.branch}: {names}; starting fix task CI{n}", "rotating_light")
            if self.stop_reason():
                return
            result = self.run_task(ci_fix_task(n, failed))
            self.results.append(result)
            if result.status != "done":
                self.ci_notes.append(f"CI{n} could not fix it; needs a human.")
                return
            failed = None
        self.ci_notes.append(f"CI still failing after {self.args.ci_rounds} fix rounds; needs a human.")

    def fix_ci_run(self, run_id: str) -> str:
        """agent-watch mode: one failed run (on main) becomes the first fix task."""
        res = sh(["gh", "run", "view", run_id, "--json", "name,conclusion,headSha"], cwd=self.repo, timeout=60)
        info = json.loads(res.stdout) if res.returncode == 0 else {"name": f"run {run_id}"}
        self.ci_notes.append(f"Started for failed run {run_id} ({info.get('name')}) on main.")
        self.ci_gate(first=[(info.get("name", run_id), ci_failure_log(self.repo, run_id))])
        return "CI fix run finished"

    def report(self, ended: str) -> str:
        done = [r for r in self.results if r.status == "done"]
        skipped = [r for r in self.results if r.status == "skipped"]
        hours = (time.monotonic() - self.started) / 3600
        lines = [
            f"# Agent run {self.branch}",
            "",
            f"Ended {now()}: {ended}. {len(done)} done, {len(skipped)} skipped, {hours:.1f} h.",
            "",
            "| Task | Result | Attempts | Minutes | Note |",
            "| --- | --- | --- | --- | --- |",
        ]
        for r in self.results:
            note = r.reason.replace("|", "\\|")
            lines.append(f"| {r.task.id} {r.task.title} | {r.status} | {r.attempts} | {r.seconds / 60:.0f} | {note} |")
        remaining = parse_plan((self.repo / "PLAN.md").read_text())
        if remaining:
            lines += ["", f"{len(remaining)} task(s) not started: " + ", ".join(t.id for t in remaining)]
        if self.ci_notes:
            lines += ["", "## CI", "", *[f"- {note}" for note in self.ci_notes]]
        lines += ["", f"Logs: `{STATE_DIR}/logs/` in the devbox checkout."]
        return "\n".join(lines) + "\n"

    def finish(self, ended: str) -> None:
        report = self.report(ended)
        (self.repo / "REPORT.md").write_text(report)
        git(self.repo, "add", "REPORT.md")
        git(self.repo, "commit", "-q", "-m", "agent run report", check=False)
        self.push()
        pr = ""
        if self.args.push and self.args.pr:
            res = sh(["gh", "pr", "create", "--head", self.branch,
                      "--title", f"Agent run {self.branch}", "--body-file", "REPORT.md"],
                     cwd=self.repo, timeout=60)
            pr = res.stdout.strip().splitlines()[-1] if res.returncode == 0 and res.stdout.strip() else ""
        done = sum(r.status == "done" for r in self.results)
        notify("Agent run finished", f"{ended}: {done}/{len(self.results)} done. {pr}".strip(), "checkered_flag")
        print(report)
        (self.state / "lock").unlink(missing_ok=True)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--repo", type=Path, default=Path.cwd())
    p.add_argument("--retries", type=int, default=3)
    p.add_argument("--max-skips", type=int, default=3, help="stop after this many skips in a row")
    p.add_argument("--max-hours", type=float, default=10)
    p.add_argument("--attempt-timeout", type=int, default=1800, help="seconds per agent session")
    p.add_argument("--check-timeout", type=int, default=900)
    p.add_argument("--no-pc", dest="pc", action="store_false", help="skip wake/keep-awake")
    p.add_argument("--no-push", dest="push", action="store_false")
    p.add_argument("--no-pr", dest="pr", action="store_false")
    p.add_argument("--no-ci", dest="ci", action="store_false", help="don't wait for CI after the tasks")
    p.add_argument("--ci-rounds", type=int, default=3, help="max CI fix tasks per run")
    p.add_argument("--ci-timeout", type=int, default=1800, help="seconds to wait for CI to finish")
    p.add_argument("--ci-appear", type=int, default=180, help="seconds to wait for CI runs to show up")
    p.add_argument("--ci-poll", type=int, default=20)
    p.add_argument("--fix-ci", metavar="RUN_ID", help="skip PLAN.md; fix this failed GitHub Actions run")
    args = p.parse_args()
    args.agent_cmd = shlex.split(env("AGENT_CMD", "opencode run --model pc/coder {prompt}"))
    args.check_cmd = env("CHECK_CMD", "make check")
    args.protected = [x.strip() for x in env("PROTECTED", "tests/,contract/,PLAN.md,SPEC.md,AGENTS.md").split(",") if x.strip()]

    if args.ci and args.push and not shutil.which("gh"):
        print("[ci] gh not found; not waiting for CI", file=sys.stderr)
        args.ci = False

    run = Run(repo=args.repo.resolve(), args=args, pc=Pc(args.pc))
    run.prepare()
    notify("Agent run started", f"{run.repo.name} on {run.branch}", "rocket")
    ended = "error"
    try:
        run.pc.up()
        run.pc.hold()
        if args.fix_ci:
            ended = run.fix_ci_run(args.fix_ci)
        else:
            ended = run.loop()
            run.ci_gate()
    except PcUnavailable as exc:
        ended = str(exc)
    except KeyboardInterrupt:
        ended = "interrupted"
    finally:
        run.finish(ended)
        run.pc.release()
    return 0


if __name__ == "__main__":
    sys.exit(main())
