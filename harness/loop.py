#!/usr/bin/env python3
"""agent-loop: Ralph-style autonomous coding loop for a local model.

One task per fresh agent session. PLAN.md is the only state between tasks.
The harness, not the model, decides when a task is done: it runs the task's
acceptance command plus a project-wide check. Pass -> commit. Fail -> reset
and retry with the failure output. Too many failures -> skip and move on.

Run from inside a project repo (see harness/templates/):
    agent-loop                  # full run: wake PC, work through PLAN.md, open a PR
    agent-loop --no-pc --no-push --retries 1   # local dry run

Environment (set by the devbox ConfigMap/Secret):
    LLAMA_BASE_URL  e.g. http://192.168.1.50:8080/v1
    LLAMA_API_KEY   llama-server API key
    WAKER_URL       e.g. http://waker.ai-waker.svc.cluster.local
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
FAIL_TAIL_LINES = 80


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
    line: int


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
        current = git(self.repo, "rev-parse", "--abbrev-ref", "HEAD")
        if current.startswith("agent/"):
            self.branch = current                         # resume an interrupted run
        else:
            self.branch = f"agent/{dt.datetime.now():%Y-%m-%d-%H%M}"
            git(self.repo, "checkout", "-b", self.branch)

    # -- one attempt
    def prompt(self, task: Task, failure: str) -> str:
        template = (HERE / "prompt.md").read_text()
        progress_file = self.repo / "PROGRESS.md"
        progress = tail(progress_file.read_text(), 40) if progress_file.exists() else "(none yet)"
        values = {
            "task_id": task.id, "task": task.title,
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
    args = p.parse_args()
    args.agent_cmd = shlex.split(env("AGENT_CMD", "opencode run --model pc/coder {prompt}"))
    args.check_cmd = env("CHECK_CMD", "make check")
    args.protected = [x.strip() for x in env("PROTECTED", "tests/,contract/,PLAN.md,SPEC.md,AGENTS.md").split(",") if x.strip()]

    run = Run(repo=args.repo.resolve(), args=args, pc=Pc(args.pc))
    run.prepare()
    notify("Agent run started", f"{run.repo.name} on {run.branch}", "rocket")
    ended = "error"
    try:
        run.pc.up()
        run.pc.hold()
        ended = run.loop()
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
