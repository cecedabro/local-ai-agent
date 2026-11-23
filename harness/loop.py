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
    STATUS_URL      agent-status service (status/ in this repo), optional
    STATUS_TOKEN    bearer token for it
    AGENT_JOB_ID    set by agent-queue (worker.py): the job this run belongs to

Status and queue: with STATUS_URL set, the run reports its progress there and first
takes the service's lease on the model: one GPU, so one run at a time; others wait in
line. If the service is unreachable the run goes on without it (fail-open).
Pause: before each task the run asks the service whether Pause was pressed on the page.
If so it releases the lease and the PC, waits for Resume (or a STOP), then queues again.
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
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
TASK_RE = re.compile(r"^- \[(?P<mark>[ x~])\] (?P<id>[A-Z]{1,3}\d+): (?P<title>.+?)\s*$")
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


def _kill_group(pgid: int) -> None:
    """Kill a process group; ignore it if it is already gone."""
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def sh(cmd: str | list[str], *, cwd: Path, timeout: int | None = None,
       check: bool = False) -> subprocess.CompletedProcess[str]:
    """Run a command, merge stderr into stdout, never raise on timeout.

    The command runs in its own process group, and the whole group is killed when it
    finishes or times out. Killing only the shell left test runs (and the agent's own
    tool calls) running in the background, where they held locks and hung later runs.
    """
    with subprocess.Popen(
        cmd, cwd=cwd, shell=isinstance(cmd, str), text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True,
    ) as child:
        try:
            out, _ = child.communicate(timeout=timeout)
            code = child.returncode
        except subprocess.TimeoutExpired:
            _kill_group(child.pid)
            try:
                out, _ = child.communicate(timeout=10)
            except subprocess.TimeoutExpired:  # a process outside the group holds the pipe
                child.kill()
                out = ""
            out = (out or "") + f"\n[timed out after {timeout}s]\n"
            code = 124
        finally:
            _kill_group(child.pid)
    proc = subprocess.CompletedProcess(cmd, code, out or "")
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


# --------------------------------------------------------------------------- status service

def commit_stats(repo: Path) -> dict:
    """Short sha and size of HEAD, for the status page's all-time numbers."""
    out = git(repo, "show", "--shortstat", "--format=%h", "HEAD", check=False).splitlines()
    stats: dict = {"commit": out[0].strip() if out else ""}
    text = " ".join(out[1:])
    for key, word in (("files", "file"), ("insertions", "insertion"), ("deletions", "deletion")):
        m = re.search(rf"(\d+) {word}", text)
        stats[key] = int(m.group(1)) if m else 0
    return stats


def plan_title(repo: Path) -> str:
    """The run's name: the text after "# PLAN:" on PLAN.md's first heading ("" if none)."""
    plan = repo / "PLAN.md"
    for line in plan.read_text().splitlines() if plan.exists() else []:
        if line.startswith("#"):
            m = re.match(r"#\s*PLAN\s*:?\s*(.*)$", line.strip())
            return m.group(1).strip() if m else ""
    return ""


def slugify(title: str, limit: int = 40) -> str:
    """"Accounts & login (v2)" -> "accounts-login-v2": lowercase words joined by '-', cut at a word."""
    words = re.findall(r"[a-z0-9]+", title.lower())
    out = ""
    for w in words:
        if len(out) + len(w) + (1 if out else 0) > limit:
            break
        out = f"{out}-{w}" if out else w
    return out


def free_branch(repo: Path, base: str) -> str:
    """base, or base-2, base-3, ... if that branch already exists here or on origin."""
    taken = set(git(repo, "branch", "-a", "--format=%(refname:short)", check=False).splitlines())
    name, n = base, 1
    while name in taken or f"origin/{name}" in taken:
        n += 1
        name = f"{base}-{n}"
    return name


def plan_snapshot(repo: Path) -> list[dict]:
    """Every task in PLAN.md with its state, for the status page."""
    plan = repo / "PLAN.md"
    if not plan.exists():
        return []
    states = {" ": "todo", "x": "done", "~": "skipped"}
    return [{"id": m["id"], "title": m["title"].split(" — ")[0], "state": states[m["mark"]]}
            for m in map(TASK_RE.match, plan.read_text().splitlines()) if m]


class Status:
    """Reports to the agent-status service and holds its lease on the model.
    Best effort: unset STATUS_URL or an unreachable service never stops a run."""

    def __init__(self, enabled: bool, poll: float = 30) -> None:
        self.url = os.environ.get("STATUS_URL", "").rstrip("/") if enabled else ""
        self.token = os.environ.get("STATUS_TOKEN", "")
        self.poll = poll
        self.ident: dict = {"run_id": f"{socket.gethostname()}-{os.getpid()}-{int(time.time())}"}
        if os.environ.get("AGENT_JOB_ID"):
            self.ident["job_id"] = os.environ["AGENT_JOB_ID"]
        self._stop = threading.Event()
        self._warned = False

    def _post(self, path: str, body: dict) -> dict | None:
        if not self.url:
            return None
        req = urllib.request.Request(
            self.url + path, data=json.dumps(body).encode(), method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                raw = resp.read()
            return json.loads(raw) if raw else {}
        except (urllib.error.URLError, OSError, ValueError) as exc:
            if not self._warned:
                print(f"[status] {self.url}{path} failed ({exc}); carrying on without it", file=sys.stderr)
                self._warned = True
            return None

    def bind(self, repo: Path, branch: str) -> None:
        self.ident.update(repo=repo.name, branch=branch)

    def event(self, kind: str, **fields: object) -> None:
        ts = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        self._post("/api/events", {**self.ident, "kind": kind, "ts": ts, **fields})

    def acquire(self, should_stop) -> None:
        """Wait for our turn on the model. Returns early if the service is down or a STOP appears."""
        told = False
        while self.url:
            res = self._post("/api/lease", {**self.ident, "action": "acquire"})
            if res is None:
                return
            if res.get("granted"):
                self._stop = threading.Event()            # fresh after a pause released the lease
                threading.Thread(target=self._renew, args=(self._stop,), daemon=True).start()
                return
            holder = res.get("holder") or {}
            msg = (f"queued (position {res.get('position', '?')}) behind "
                   f"{holder.get('repo', '?')} {holder.get('branch', '')}").strip()
            print(f"[{now()}] {msg}")
            if not told:
                notify("Agent run queued", f"{self.ident.get('repo')}: {msg}", "hourglass")
                told = True
            if should_stop():
                return
            time.sleep(self.poll)

    def _renew(self, stop: threading.Event) -> None:
        while not stop.wait(60):
            self._post("/api/lease", {**self.ident, "action": "renew"})

    def lost_lease(self) -> bool:
        """True when another holder took the lease from us (status SPEC §4 preempt: the cv-skills gatekeeper
        stopped the model on purpose). Fail-open: False when the service is off or unreachable."""
        res = self._post("/api/lease", {**self.ident, "action": "renew"})
        return bool(res) and res.get("granted") is False

    def rejoin(self, should_stop) -> None:
        """Wait (first in line after a preempt) until the lease is ours again."""
        self._stop.set()                                  # the old renew thread
        self.acquire(should_stop)

    def pause_wanted(self) -> bool:
        """True while Pause is pressed for this run on the status page (fail-open: False)."""
        res = self._post(f"/api/runs/{self.ident['run_id']}/control", {})
        return bool(res and res.get("pause"))

    def release(self) -> None:
        self._stop.set()
        self._post("/api/lease", {**self.ident, "action": "release"})


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
        stop = self._stop = threading.Event()             # fresh after a pause released the PC

        def beat() -> None:
            while not stop.wait(300):
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
    status: Status = field(default_factory=lambda: Status(False))
    branch: str = ""
    push_errors: list[str] = field(default_factory=list)
    results: list[Result] = field(default_factory=list)
    ci_notes: list[str] = field(default_factory=list)
    sync_error: str = ""                                  # set when a human's pushed commits conflict with ours
    started: float = field(default_factory=time.monotonic)

    @property
    def state(self) -> Path:
        return self.repo / STATE_DIR

    def stop_reason(self) -> str | None:
        if self.sync_error:
            return self.sync_error
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
            if self.args.fix_ci:
                name = f"ci-fix-{self.args.fix_ci}"
            else:                                         # "# PLAN: Accounts and login" -> accounts-and-login
                name = slugify(plan_title(self.repo)) or f"{dt.datetime.now():%Y-%m-%d-%H%M}"
            self.branch = free_branch(self.repo, f"agent/{name}")
            git(self.repo, "checkout", "-b", self.branch)
        self.status.bind(self.repo, self.branch)

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

    def sync(self) -> bool:
        """Take in commits a human pushed to the branch (say, a hotfix during a pause): fetch it and rebase our
        unpushed commits on top, so the next push is a fast-forward. An unreachable remote or a branch that is not
        on the remote yet is fine (the push reports it). A conflict aborts the rebase, is loud (ntfy, status page,
        report) and stops the run: never force-push over a human's work. Returns False once that happened."""
        if self.sync_error:
            return False
        if not self.args.push:
            return True
        remote = f"refs/remotes/origin/{self.branch}"
        res = sh(["git", "fetch", "-q", "origin", f"+refs/heads/{self.branch}:{remote}"], cwd=self.repo, timeout=120)
        if res.returncode != 0:
            return True
        if sh(["git", "merge-base", "--is-ancestor", remote, "HEAD"], cwd=self.repo).returncode == 0:
            return True                                   # nothing new on the remote
        theirs = git(self.repo, "log", "--format=%h %s", f"HEAD..{remote}", check=False).splitlines()
        res = sh(["git", "rebase", "-q", remote], cwd=self.repo, timeout=120)
        if res.returncode == 0:
            msg = f"took in {len(theirs)} pushed commit(s) on {self.branch}: " + "; ".join(theirs[:3])
            print(f"[{now()}] {msg}")
            self.status.event("ci", detail=msg[:300])     # the status service only knows its own kinds
            return True
        sh(["git", "rebase", "--abort"], cwd=self.repo)
        err = tail(res.stdout, 10)
        self.sync_error = f"commits pushed to {self.branch} conflict with the run's own; stopped (nothing force-pushed)"
        print(f"[sync failed] {err}", file=sys.stderr)
        notify("Branch conflict", f"{self.repo.name} {self.branch}: pushed commits conflict with the run's; "
               "stopped. Rebase by hand on the devbox.", "rotating_light")
        self.status.event("push_failed", detail="branch conflict: " + tail(err, 5))
        return False

    def push(self) -> bool:
        """Push the branch (after taking in commits pushed by someone else). A failure is loud (ntfy, status page,
        report), never silent: unpushed work only exists on the devbox."""
        if not self.args.push:
            return True
        if not self.sync():
            return False
        res = sh(["git", "push", "-u", "origin", self.branch], cwd=self.repo, timeout=120)
        if res.returncode == 0:
            return True
        err = tail(res.stdout, 10)
        print(f"[push failed] {err}", file=sys.stderr)
        if not self.push_errors:
            notify("Push failed", f"{self.repo.name} {self.branch}: commits stay on the devbox.\n{tail(err, 3)}",
                   "rotating_light")
        self.push_errors.append(err)
        self.status.event("push_failed", detail=tail(err, 5))
        return False

    # -- one task
    def wait_for_gpu(self) -> bool:
        """If the GPU was taken from us (lease preempted by the cv-skills gatekeeper), let go of the PC and wait
        until the lease comes back. Paused time does not count toward --max-hours. True if it waited."""
        if not self.status.lost_lease():
            return False
        t0 = time.monotonic()
        print(f"[{now()}] the GPU was taken (lease preempted); waiting for it to come back")
        notify("Agent run waiting", f"{self.repo.name}: the GPU is in use (cv-skills); continuing afterwards",
               "pause_button")
        self.status.event("paused", detail="GPU taken (lease preempted)")
        self.pc.release()
        self.status.rejoin(self.stop_reason)
        self.started += time.monotonic() - t0
        if self.stop_reason():
            return True
        print(f"[{now()}] the GPU is back")
        self.status.event("resumed")
        self.pc.up()
        self.pc.hold()
        return True

    def run_task(self, task: Task) -> Result:
        t0 = time.monotonic()
        failure = ""
        n = 1
        while n <= self.args.retries:
            self.wait_for_gpu()                           # before pc.up(): the model is stopped on purpose
            if self.stop_reason() and not failure:        # stopped while waiting: not tried, not skipped
                return Result(task, "stopped", n - 1, self.stop_reason() or "", time.monotonic() - t0)
            if self.stop_reason():
                break
            head = git(self.repo, "rev-parse", "HEAD")
            print(f"[{now()}] {task.id} attempt {n}/{self.args.retries}: {task.title}")
            self.status.event("task_started", task_id=task.id, title=task.title[:200], attempt=n,
                              retries=self.args.retries)
            try:
                ok, failure = self.attempt(task, n, failure)
            except PcUnavailable:
                if not self.status.lost_lease():
                    raise
                ok, failure = False, "the GPU was taken during the attempt"
            if ok:
                if task.line >= 0:
                    mark_task(self.repo / "PLAN.md", task, "x")
                git(self.repo, "add", "-A")
                git(self.repo, "commit", "-q", "-m", f"{task.id}: {task.title}")
                size = commit_stats(self.repo)
                self.push()
                notify(f"{task.id} done", task.title, "white_check_mark")
                seconds = time.monotonic() - t0
                self.status.event("task_done", task_id=task.id, title=task.title[:200], attempts=n,
                                  seconds=round(seconds), plan=plan_snapshot(self.repo), **size)
                return Result(task, "done", n, seconds=seconds)
            if self.status.lost_lease():                  # the model was taken mid-attempt: this one doesn't count
                print(f"[{now()}] {task.id} attempt {n} interrupted: the GPU was taken; retrying it later")
                self.reset(head)
                failure = ""
                continue
            print(f"[{now()}] {task.id} attempt {n} failed: {failure.splitlines()[0] if failure else ''}")
            self.reset(head)
            n += 1
            if self.stop_reason():
                break
        reason = failure.splitlines()[0][:160] if failure else "unknown"
        if task.line >= 0:
            mark_task(self.repo / "PLAN.md", task, "~", f"skipped: {reason}")
            git(self.repo, "commit", "-q", "-am", f"{task.id}: skipped")
        notify(f"{task.id} skipped", f"{task.title}\n{reason}", "warning")
        seconds = time.monotonic() - t0
        self.status.event("task_skipped", task_id=task.id, title=task.title[:200],
                          attempts=self.args.retries, seconds=round(seconds), reason=reason,
                          plan=plan_snapshot(self.repo))
        return Result(task, "skipped", self.args.retries, reason, seconds)

    # -- pause (status page)
    def pause_point(self) -> bool:
        """Between tasks, before a CI fix task and while waiting for CI: if Pause is pressed on the status page,
        let go of the model and the PC and wait for Resume (or a STOP). Paused time does not count toward
        --max-hours. Returns True if it paused; then the caller takes in what a human pushed meanwhile (sync)."""
        if not self.status.pause_wanted():
            return False
        t0 = time.monotonic()
        print(f"[{now()}] paused from the status page; waiting for Resume")
        notify("Agent run paused", f"{self.repo.name}: press Resume on the status page", "pause_button")
        self.status.event("paused", plan=plan_snapshot(self.repo))
        self.status.release()
        self.pc.release()
        while self.status.pause_wanted() and not (self.state / "STOP").exists():
            time.sleep(self.status.poll)
        self.started += time.monotonic() - t0
        if (self.state / "STOP").exists():
            return True
        print(f"[{now()}] resumed")
        self.status.acquire(self.stop_reason)
        self.pc.up()
        self.pc.hold()
        self.status.event("resumed")
        return True

    # -- whole run
    def loop(self) -> str:
        consecutive_skips = 0
        while True:
            reason = self.stop_reason()
            if reason:
                return reason
            self.pause_point()
            self.sync()                                   # a human may have pushed (and edited PLAN.md)
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
    def note_ci(self, note: str) -> None:
        self.ci_notes.append(note)
        self.status.event("ci", detail=note)

    def resumed(self) -> bool:
        """A pause point for the CI round: True if it paused, a STOP is not set and the branch was brought up to
        date with what a human pushed meanwhile (so CI is checked again on the new HEAD)."""
        if not self.pause_point() or self.stop_reason():
            return False
        if not self.push():                               # sync + push: CI runs on the new HEAD
            return False
        self.note_ci(f"Resumed; checking CI again on {git(self.repo, 'rev-parse', '--short', 'HEAD')}.")
        return True

    def wait_ci(self) -> list[tuple[str, str]] | None:
        """Wait for every workflow run on the pushed HEAD. [] = green, None = no CI / unknown.
        Pause works here too; after a resume it waits for CI on the (maybe new) HEAD."""
        sha = git(self.repo, "rev-parse", "HEAD")
        appear_by = time.monotonic() + self.args.ci_appear
        deadline = time.monotonic() + self.args.ci_timeout
        print(f"[{now()}] waiting for CI on {sha[:7]}")
        self.status.event("ci_waiting", detail=sha[:7])
        while True:
            if self.resumed():
                return self.wait_ci()
            if self.stop_reason():
                return [("stopped", self.stop_reason() or "")]
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

    def ci_gate(self, first: list[tuple[str, str]] | None = None) -> bool:
        """Wait for CI; turn failures into fix tasks until green or out of rounds.
        `first` skips the initial wait (agent-watch passes the failure it saw on main).
        Pause works before each fix task and while waiting; after a resume CI is checked again on the new HEAD
        (a human's fix may have made it green). Returns True if PLAN.md gained new open tasks during a pause, so
        the caller runs the plan again."""
        if not (self.args.ci and self.args.push):
            return False
        if first is None and self.stop_reason():
            self.note_ci(f"CI not checked: {self.stop_reason()}.")
            return False
        known = {t.id for t in parse_plan((self.repo / "PLAN.md").read_text())}
        def new_tasks() -> bool:
            return bool({t.id for t in parse_plan((self.repo / "PLAN.md").read_text())} - known)
        self.push()                                       # a trailing "skipped" commit is not pushed yet
        failed = first
        n = 0
        while True:
            if failed is None:
                failed = self.wait_ci()
            if self.stop_reason():
                self.note_ci(f"CI round stopped: {self.stop_reason()}.")
                return False
            if new_tasks():
                self.note_ci("PLAN.md gained tasks during a pause; running the plan again.")
                return True
            if failed is None:
                self.note_ci("No CI runs found for the last commit (or gh failed).")
                return False
            if not failed:
                self.note_ci(f"CI green on {git(self.repo, 'rev-parse', '--short', 'HEAD')}.")
                notify("CI green", self.branch, "white_check_mark")
                return False
            if n >= self.args.ci_rounds:
                self.note_ci(f"CI still failing after {self.args.ci_rounds} fix rounds; needs a human.")
                return False
            names = ", ".join(name for name, _ in failed)
            if self.resumed():                            # Pause pressed: a human may fix it; look again
                failed = None
                continue
            if self.stop_reason():
                return False
            n += 1
            self.note_ci(f"CI round {n}: failed ({names}).")
            notify("CI failed", f"{self.branch}: {names}; starting fix task CI{n}", "rotating_light")
            result = self.run_task(ci_fix_task(n, failed))
            self.results.append(result)
            if result.status != "done":
                self.note_ci(f"CI{n} could not fix it; needs a human.")
                return False
            failed = None                                 # run_task pushed the fix

    def fix_ci_run(self, run_id: str) -> str:
        """agent-watch mode: one failed run (on main) becomes the first fix task."""
        res = sh(["gh", "run", "view", run_id, "--json", "name,conclusion,headSha"], cwd=self.repo, timeout=60)
        info = json.loads(res.stdout) if res.returncode == 0 else {"name": f"run {run_id}"}
        self.note_ci(f"Started for failed run {run_id} ({info.get('name')}) on main.")
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
        if self.sync_error:
            lines += ["", "## Branch conflict", "",
                      f"{self.sync_error}. Someone pushed commits to `{self.branch}` that conflict with commits the "
                      "run had not pushed yet. Those commits are only in the devbox checkout: rebase them by hand "
                      f"on `origin/{self.branch}` and push."]
        if self.push_errors:
            lines += ["", "## Push failed", "",
                      f"Pushing failed {len(self.push_errors)} time(s); commits may exist only in the "
                      "devbox checkout. Last error:", "", "```", self.push_errors[-1], "```"]
        lines += ["", f"Logs: `{STATE_DIR}/logs/` in the devbox checkout."]
        return "\n".join(lines) + "\n"

    def finish(self, ended: str) -> None:
        self.push()                                       # so the report knows whether pushing works
        report = self.report(ended)
        (self.repo / "REPORT.md").write_text(report)
        git(self.repo, "add", "REPORT.md")
        git(self.repo, "commit", "-q", "-m", "agent run report", check=False)
        pushed = self.push()
        pr = ""
        if self.args.push and self.args.pr and pushed:
            # A run that stopped before its plan was done gets a draft PR: its branch still holds TODO stubs, and
            # merging it would put half-built code on main.
            unfinished = bool(parse_plan((self.repo / "PLAN.md").read_text()))
            title = f"Agent run: {plan_title(self.repo) or self.branch}"
            res = sh(["gh", "pr", "create", "--head", self.branch, *(["--draft"] if unfinished else []),
                      "--title", ("Unfinished, don't merge yet: " + title) if unfinished else title,
                      "--body-file", "REPORT.md"], cwd=self.repo, timeout=60)
            pr = res.stdout.strip().splitlines()[-1] if res.returncode == 0 and res.stdout.strip() else ""
        done = sum(r.status == "done" for r in self.results)
        notify("Agent run finished", f"{ended}: {done}/{len(self.results)} done. {pr}".strip(), "checkered_flag")
        self.status.event("run_finished", ended=ended, done=done, skipped=len(self.results) - done,
                          pushed=pushed, pr=pr or None, plan=plan_snapshot(self.repo))
        print(report)
        (self.state / "lock").unlink(missing_ok=True)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--repo", type=Path, default=Path.cwd())
    p.add_argument("--retries", type=int, default=3)
    p.add_argument("--max-skips", type=int, default=3, help="stop after this many skips in a row")
    p.add_argument("--max-hours", type=float, default=24, help="stop starting new tasks after this many hours")
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
    p.add_argument("--no-status", dest="status", action="store_false",
                   help="don't report to STATUS_URL or wait for its queue")
    p.add_argument("--queue-poll", type=float, default=30, help="seconds between queue checks")
    args = p.parse_args()
    args.agent_cmd = shlex.split(env("AGENT_CMD", "opencode run --model pc/coder {prompt}"))
    args.check_cmd = env("CHECK_CMD", "make check")
    args.protected = [x.strip() for x in env("PROTECTED", "tests/,contract/,PLAN.md,SPEC.md,AGENTS.md").split(",") if x.strip()]

    if args.ci and args.push and not shutil.which("gh"):
        print("[ci] gh not found; not waiting for CI", file=sys.stderr)
        args.ci = False

    run = Run(repo=args.repo.resolve(), args=args, pc=Pc(args.pc), status=Status(args.status, args.queue_poll))
    run.prepare()
    notify("Agent run started", f"{run.repo.name} on {run.branch}", "rocket")
    ended = "error"
    try:
        run.status.acquire(run.stop_reason)
        run.started = time.monotonic()                    # waiting in line doesn't count
        run.status.event("run_started", fix_ci=args.fix_ci, max_hours=args.max_hours,
                         plan=plan_snapshot(run.repo))
        run.pc.up()
        run.pc.hold()
        if args.fix_ci:
            ended = run.fix_ci_run(args.fix_ci)
        else:
            ended = run.loop()
            while run.ci_gate():                          # a pause added tasks to PLAN.md
                ended = run.loop()
    except PcUnavailable as exc:
        ended = str(exc)
    except KeyboardInterrupt:
        ended = "interrupted"
    finally:
        run.finish(ended)
        run.pc.release()
        run.status.release()
    return 0


if __name__ == "__main__":
    sys.exit(main())
