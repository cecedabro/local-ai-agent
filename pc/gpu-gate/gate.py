#!/usr/bin/env python3
"""cv-skills GPU gatekeeper: the PC answers cv-skills requests on demand.

Always on in WSL, no GPU, listening on 127.0.0.1:8088 behind `tailscale serve --tcp=80` (Windows side). The first request
swaps the GPU over from the agent's model (`coder`) to the AI-Engineers stack; after GATE_IDLE_S without requests
it swaps back. Python 3.11+, standard library only.

Contract (agreed with the capaciteitsmanager side):
  GET  /v1/status    -> 200 {"state", "detail", "retry_after", "since", "idle_timeout_s", "active_until"}
                        state: idle | loading | active | unloading
  POST /v1/warmup    -> 202 + status; starts the swap in. With ?pin=1 (`ai gpu on`) the stack stays on: no idle
                        timer until /v1/cooldown
  POST /v1/cooldown  -> 202 + status; starts the swap back (`ai gpu off`)
  any other /v1/...  -> active: proxied to the stack (127.0.0.1:8089) with the internal key + X-Requester;
                        idle: starts the swap in; idle/loading/unloading: 503 + Retry-After + status
  GET  /healthz      -> 200 {"ok": true}, no key (local liveness only)
Access (GATE_AUTH):
  tailnet (default) the tailnet policy is the lock: only tag:cv-skills-client nodes (and the owner's own devices) can
                    reach cv-skills-gpu:80, and the gatekeeper listens on loopback only. No key; the optional
                    X-Requester header (a label, sanitised) is only used for the logs.
  keys              every /v1 call needs a personal key (Authorization: Bearer <key> or X-API-Key), stored as
                    sha256 hashes, one "name hash" per line in GATE_KEYS_FILE.
Logs hold method, path, status, duration and requester: never bodies, never keys.

The swap itself is one root helper (`cv-gpu-swap in|out|beat`, via sudo -n): the gatekeeper runs as its own
unprivileged user and cannot run anything else.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import http.client
import json
import os
import re
import secrets
import shlex
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MAX_BODY = 2 * 1024 * 1024
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
       "transfer-encoding", "upgrade", "content-length", "host", "authorization", "x-api-key", "x-requester"}


def iso(t: float | None) -> str | None:
    return None if t is None else dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat(timespec="seconds")


def log(msg: str) -> None:
    print(f"{iso(time.time())} {msg}", flush=True)


@dataclass
class Config:
    host: str = "127.0.0.1"
    port: int = 8088
    upstream: str = "http://127.0.0.1:8089"
    auth: str = "tailnet"           # tailnet | keys
    keys_file: Path = Path("/etc/cv-gate/keys")
    internal_key_file: Path = Path("/etc/cv-gate/internal-key")
    swap_cmd: list[str] = field(default_factory=lambda: ["sudo", "-n", "/usr/local/sbin/cv-gpu-swap"])
    idle_s: int = 600
    load_timeout_s: int = 300
    retry_after: int = 15
    tick_s: float = 5
    beat_s: float = 60
    status_url: str = ""            # agent-status service: the model lease (optional)
    status_token: str = ""

    @classmethod
    def from_env(cls) -> Config:
        e = os.environ.get
        return cls(host=e("GATE_HOST", "127.0.0.1"), port=int(e("GATE_PORT", "8088")),
                   upstream=e("GATE_UPSTREAM", "http://127.0.0.1:8089").rstrip("/"),
                   auth=e("GATE_AUTH", "tailnet"),
                   keys_file=Path(e("GATE_KEYS_FILE", "/etc/cv-gate/keys")),
                   internal_key_file=Path(e("GATE_INTERNAL_KEY_FILE", "/etc/cv-gate/internal-key")),
                   swap_cmd=shlex.split(e("GATE_SWAP_CMD", "sudo -n /usr/local/sbin/cv-gpu-swap")),
                   idle_s=int(e("GATE_IDLE_S", "600")), load_timeout_s=int(e("GATE_LOAD_TIMEOUT_S", "300")),
                   retry_after=int(e("GATE_RETRY_AFTER", "15")),
                   status_url=e("STATUS_URL", "").rstrip("/"), status_token=e("STATUS_TOKEN", ""))


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def load_keys(path: Path) -> dict[str, str]:
    """{sha256 hex: name}. Lines: "name sha256hex"; '#' comments. Read on every request, so revoking is one edit."""
    keys = {}
    try:
        for line in path.read_text().splitlines():
            parts = line.split("#", 1)[0].split()
            if len(parts) == 2 and len(parts[1]) == 64:
                keys[parts[1].lower()] = parts[0]
    except OSError:
        pass
    return keys


class Lease:
    """The model lease on the agent-status service, so agent runs and the page know the GPU is taken. Best effort:
    the swap goes ahead without it (Cédric wants a cv-skills request to win over whatever runs)."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.ident = {"run_id": f"gpu-gate-{os.getpid()}", "repo": "cv-skills", "branch": "gpu-gate"}
        self.held = False

    def _post(self, body: dict) -> dict | None:
        if not self.cfg.status_url:
            return None
        req = urllib.request.Request(self.cfg.status_url + "/api/lease", data=json.dumps({**self.ident, **body}).encode(),
                                     method="POST", headers={"Content-Type": "application/json",
                                                             "Authorization": f"Bearer {self.cfg.status_token}"})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return json.loads(resp.read() or b"{}")
        except (urllib.error.URLError, OSError, ValueError):
            return None

    def take(self) -> None:
        res = self._post({"action": "preempt"})
        if res is None:                                   # an older status service: take it the normal way
            res = self._post({"action": "acquire"})
        self.held = bool(res and res.get("granted"))

    def renew(self) -> None:
        if self.held:
            self._post({"action": "renew"})

    def release(self) -> None:
        if self.cfg.status_url:
            self._post({"action": "release"})
        self.held = False


ACTIVE = "stack up; requests are proxied"
ACTIVE_PINNED = "stack up and held on (ai gpu on); requests are proxied; ai gpu off switches back to coder"


class Gate:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.lock = threading.Lock()
        self.state = "idle"
        self.detail = "stack not loaded"
        self.since = time.time()
        self.last_request = 0.0
        self.inflight = 0
        self.want_in = False                              # a request came in while unloading
        self.pinned = False                               # `ai gpu on`: stay loaded until cooldown
        self.lease = Lease(cfg)
        self.stop = threading.Event()

    # ---------------------------------------------------------------- state
    def _set(self, state: str, detail: str) -> None:      # caller holds the lock
        self.state, self.detail, self.since = state, detail, time.time()
        log(f"state {state}: {detail}")

    def status(self) -> dict:
        with self.lock:
            busy = self.state in ("loading", "unloading")
            return {"state": self.state, "detail": self.detail,
                    "retry_after": self.cfg.retry_after if busy else None, "since": iso(self.since),
                    "idle_timeout_s": self.cfg.idle_s,
                    "active_until": iso(self.last_request + self.cfg.idle_s)
                    if self.state == "active" and not self.pinned else None}

    def want(self, pin: bool = False) -> None:
        """Make sure the stack is (getting) loaded; *pin* keeps it loaded until cooldown."""
        with self.lock:
            if pin:
                self.pinned = True
                if self.state == "active":
                    self._set("active", ACTIVE_PINNED)
            if self.state == "idle":
                self._set("loading", "swapping in: stopping the agent model, starting the stack")
                threading.Thread(target=self._swap_in, daemon=True).start()
            elif self.state == "unloading":
                self.want_in = True

    def cooldown(self) -> None:
        with self.lock:
            self.pinned = False
            if self.state != "active":
                self.want_in = False
                return
            self._set("unloading", "swapping back (asked)")
        threading.Thread(target=self._swap_out, daemon=True).start()

    # ---------------------------------------------------------------- swapping
    def _swap(self, verb: str, timeout: int) -> tuple[bool, str]:
        try:
            res = subprocess.run([*self.cfg.swap_cmd, verb], capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, str(exc)[:200]
        out = (res.stdout + res.stderr).strip().splitlines()
        return res.returncode == 0, (out[-1] if out else f"exit {res.returncode}")[:200]

    def _healthy(self) -> bool:
        try:
            with urllib.request.urlopen(self.cfg.upstream + "/healthz", timeout=5) as resp:
                return resp.status == 200
        except (urllib.error.URLError, OSError):
            return False

    def _swap_in(self) -> None:
        self.lease.take()
        deadline = time.monotonic() + self.cfg.load_timeout_s
        ok, msg = self._swap("in", self.cfg.load_timeout_s)
        while ok and not self._healthy():
            if time.monotonic() > deadline:
                ok, msg = False, f"stack not healthy after {self.cfg.load_timeout_s} s"
                break
            time.sleep(1)
        if ok:
            with self.lock:
                self.last_request = time.time()
                self._set("active", ACTIVE_PINNED if self.pinned else ACTIVE)
            return
        log(f"swap in failed: {msg}")
        self._swap("out", self.cfg.load_timeout_s)        # leave the PC as it was
        self.lease.release()
        with self.lock:
            self.pinned = False
            self._set("idle", f"swap in failed: {msg}")

    def _swap_out(self) -> None:
        ok, msg = self._swap("out", self.cfg.load_timeout_s)
        self.lease.release()
        with self.lock:
            self._set("idle", "stack not loaded" if ok else f"swap back had trouble: {msg}")
            again, self.want_in = self.want_in, False
        if again:
            self.want()

    def run_timers(self) -> None:
        """Swap back after idle_s without requests; keep the PC awake and the lease alive while loaded."""
        next_beat = 0.0
        while not self.stop.wait(self.cfg.tick_s):
            with self.lock:
                state, idle = self.state, time.time() - self.last_request
                expired = state == "active" and not self.pinned and self.inflight == 0 and idle > self.cfg.idle_s
                if expired:
                    self._set("unloading", f"swapping back after {self.cfg.idle_s} s without requests")
            if expired:
                threading.Thread(target=self._swap_out, daemon=True).start()
            elif state == "active" and time.monotonic() > next_beat:   # "in" sets the flag itself
                next_beat = time.monotonic() + self.cfg.beat_s
                self._swap("beat", 30)
                self.lease.renew()

    # ---------------------------------------------------------------- proxy
    def begin(self) -> bool:
        """Count a request in if the stack is active (so the idle timer can't unload it mid-request)."""
        with self.lock:
            if self.state != "active":
                return False
            self.inflight += 1
            self.last_request = time.time()
            return True

    def end(self) -> None:
        with self.lock:
            self.inflight -= 1
            self.last_request = time.time()


class Handler(BaseHTTPRequestHandler):
    gate: Gate
    protocol_version = "HTTP/1.1"
    server_version = "cv-gpu-gate"
    sys_version = ""

    def log_message(self, *_: object) -> None:           # our own metadata-only line instead
        pass

    def _send(self, code: int, body: dict | bytes, headers: dict | None = None, ctype: str = "application/json") -> None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)
        self._code = code

    def _who(self) -> str | None:
        if self.gate.cfg.auth != "keys":
            label = re.sub(r"[^A-Za-z0-9._@-]", "", self.headers.get("X-Requester", ""))[:64]
            return label or "tailnet"
        auth = self.headers.get("Authorization", "")
        key = auth[7:].strip() if auth.lower().startswith("bearer ") else self.headers.get("X-API-Key", "").strip()
        if not key:
            return None
        digest = hash_key(key)
        for known, name in load_keys(self.gate.cfg.keys_file).items():
            if hmac.compare_digest(known, digest):
                return name
        return None

    def _handle(self) -> None:
        t0, self._code, who = time.monotonic(), 0, "-"
        try:
            path = urllib.parse.urlsplit(self.path).path
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                self.close_connection = True
                return self._send(413, {"error": "body too large"})
            body = self.rfile.read(length) if length else b""
            if path == "/healthz" and self.command == "GET":
                return self._send(200, {"ok": True})
            if not path.startswith("/v1/"):
                return self._send(404, {"error": "not found"})
            name = self._who()
            if name is None:
                return self._send(401, {"error": "missing or unknown API key"})  # keys mode only
            who = name
            if path == "/v1/status" and self.command == "GET":
                return self._send(200, self.gate.status())
            if path == "/v1/warmup" and self.command == "POST":
                pin = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("pin") == ["1"]
                self.gate.want(pin=pin)
                return self._send(202, self.gate.status())
            if path == "/v1/cooldown" and self.command == "POST":
                self.gate.cooldown()
                return self._send(202, self.gate.status())
            if not self.gate.begin():
                self.gate.want()
                st = self.gate.status()
                return self._send(503, st, {"Retry-After": str(st["retry_after"] or self.gate.cfg.retry_after)})
            try:
                self._proxy(body, name)
            finally:
                self.gate.end()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            log(f"{self.command} {urllib.parse.urlsplit(self.path).path} {self._code or '-'} "
                f"{(time.monotonic() - t0) * 1000:.0f}ms who={who}")

    def _proxy(self, body: bytes, name: str) -> None:
        up = urllib.parse.urlsplit(self.gate.cfg.upstream)
        try:
            internal = self.gate.cfg.internal_key_file.read_text().strip()
        except OSError:
            return self._send(500, {"error": "gatekeeper misconfigured (internal key)"})
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
        headers.update({"Authorization": f"Bearer {internal}", "X-API-Key": internal, "X-Requester": name,
                        "Content-Length": str(len(body))})
        conn = http.client.HTTPConnection(up.hostname, up.port or 80, timeout=self.gate.cfg.load_timeout_s)
        try:
            conn.request(self.command, self.path, body=body, headers=headers)
            resp = conn.getresponse()
            data = resp.read()
        except (OSError, http.client.HTTPException):
            return self._send(502, {"error": "the cv-skills stack did not answer"})
        finally:
            conn.close()
        extra = {k: v for k, v in resp.getheaders() if k.lower() not in HOP and k.lower() not in
                 ("content-type", "cache-control", "date", "server")}
        self._send(resp.status, data, extra, resp.getheader("Content-Type", "application/octet-stream"))

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _handle


def make_server(cfg: Config) -> tuple[ThreadingHTTPServer, Gate]:
    gate = Gate(cfg)
    handler = type("GateHandler", (Handler,), {"gate": gate})
    server = ThreadingHTTPServer((cfg.host, cfg.port), handler)
    server.daemon_threads = True
    threading.Thread(target=gate.run_timers, daemon=True).start()
    return server, gate


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "add-key":     # gate.py add-key <name>: new key, shown once
        name = sys.argv[2]
        if not name.replace("-", "").replace("_", "").isalnum():
            sys.exit("name: letters, digits, - and _ only")
        cfg = Config.from_env()
        key = secrets.token_urlsafe(32)
        with cfg.keys_file.open("a") as fh:
            fh.write(f"{name} {hash_key(key)}\n")
        print(f"key for {name} (shown once; only its hash is stored in {cfg.keys_file}):\n{key}")
        return 0
    cfg = Config.from_env()
    server, _ = make_server(cfg)
    log(f"cv-gpu-gate on {cfg.host}:{cfg.port} -> {cfg.upstream}, idle {cfg.idle_s} s")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
