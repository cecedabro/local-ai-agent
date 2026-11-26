"""Tests for gate.py against a fake swap helper and a dummy upstream (stands in for the AI-Engineers stack).

The fake helper writes "in"/"out"/"beat" to a log and flips a flag file; the dummy upstream's /healthz is 503
until the flag exists, so the gate has to wait for it. Run: python3 -m unittest discover -s pc/gpu-gate/tests
"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import gate  # noqa: E402

FAKE_SWAP = """import sys, time, pathlib
d = pathlib.Path(sys.argv[1]); verb = sys.argv[2]
with (d / "swap.log").open("a") as fh: fh.write(verb + "\\n")
if (d / "fail").exists() and verb == "in": print("images not pulled: x"); sys.exit(3)
time.sleep(0.2)
if verb == "in": (d / "up").touch()
if verb == "out": (d / "up").unlink(missing_ok=True)
"""


class Upstream(BaseHTTPRequestHandler):
    flag: Path
    seen: list

    def log_message(self, *_: object) -> None:
        pass

    def _reply(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        self._reply(200 if self.flag.exists() else 503, {"ok": self.flag.exists()})

    def do_POST(self) -> None:  # noqa: N802
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.seen.append({"auth": self.headers.get("Authorization"), "who": self.headers.get("X-Requester"),
                          "path": self.path})
        time.sleep(float(self.headers.get("X-Slow", "0")))
        self._reply(200, {"skills": ["python"], "echo": json.loads(body or b"{}")})


class GateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "swap.py").write_text(FAKE_SWAP)
        self.key = "person-key-123"
        (self.dir / "keys").write_text(f"# name hash\nalice {gate.hash_key(self.key)}\n")
        (self.dir / "internal").write_text("internal-key\n")
        handler = type("U", (Upstream,), {"flag": self.dir / "up", "seen": []})
        self.seen = handler.seen
        self.up = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.up.serve_forever, daemon=True).start()
        self.cfg = gate.Config(port=0, auth="keys", upstream=f"http://127.0.0.1:{self.up.server_port}", keys_file=self.dir / "keys",
                               internal_key_file=self.dir / "internal",
                               swap_cmd=[sys.executable, str(self.dir / "swap.py"), str(self.dir)],
                               idle_s=1, load_timeout_s=10, tick_s=0.1, beat_s=0.2)
        self.server, self.gate = gate.make_server(self.cfg)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.gate.stop.set()
        self.server.shutdown()
        self.up.shutdown()

    def call(self, method: str, path: str, body: dict | None = None, key: str | None = "default",
             headers: dict | None = None) -> tuple[int, dict, dict]:
        h = {"Content-Type": "application/json", **(headers or {})}
        if key == "default":
            key = self.key
        if key:
            h["Authorization"] = f"Bearer {key}"
        req = urllib.request.Request(self.base + path, method=method, headers=h,
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read()), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read()), dict(e.headers)

    def wait_state(self, state: str, timeout: float = 8) -> dict:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            st = self.gate.status()
            if st["state"] == state:
                return st
            time.sleep(0.05)
        self.fail(f"never reached {state}: {self.gate.status()}")

    def swaps(self) -> list[str]:
        p = self.dir / "swap.log"
        return [v for v in p.read_text().split()] if p.exists() else []

    # ------------------------------------------------------------------ tests
    def test_keys_are_required_and_checked(self) -> None:
        self.assertEqual(self.call("GET", "/v1/status", key=None)[0], 401)
        self.assertEqual(self.call("GET", "/v1/status", key="wrong")[0], 401)
        code, st, _ = self.call("GET", "/v1/status", headers={"X-API-Key": self.key}, key=None)
        self.assertEqual(code, 200)
        self.assertEqual(st["state"], "idle")
        self.assertEqual(self.call("GET", "/healthz", key=None)[0], 200)
        self.assertEqual(self.call("GET", "/other", key=None)[0], 404)

    def test_tailnet_mode_needs_no_key_and_keeps_the_label_clean(self) -> None:
        self.cfg.auth = "tailnet"
        code, st, _ = self.call("GET", "/v1/status", key=None)
        self.assertEqual(code, 200)
        self.assertEqual(st["state"], "idle")
        self.gate.want()
        self.wait_state("active")
        code, _, _ = self.call("POST", "/v1/x", {}, key="ignored", headers={"X-Requester": "kind-dev <evil> log"})
        self.assertEqual(code, 200)
        self.assertEqual(self.seen[-1], {"auth": "Bearer internal-key", "who": "kind-devevillog", "path": "/v1/x"})
        self.call("POST", "/v1/x", {}, key=None)
        self.assertEqual(self.seen[-1]["who"], "tailnet")

    def test_status_shape(self) -> None:
        _, st, _ = self.call("GET", "/v1/status")
        self.assertEqual(set(st), {"state", "detail", "retry_after", "since", "idle_timeout_s", "active_until"})
        self.assertIsNone(st["retry_after"])
        self.assertIsNone(st["active_until"])
        self.assertEqual(st["idle_timeout_s"], 1)

    def test_first_request_swaps_in_then_is_proxied_then_swaps_back(self) -> None:
        self.cfg.idle_s = 3
        code, st, headers = self.call("POST", "/v1/skills-profile", {"cv": "fake"})
        self.assertEqual(code, 503)
        self.assertEqual(headers.get("Retry-After"), "15")
        self.assertEqual(st["state"], "loading")
        self.assertEqual(st["retry_after"], 15)
        self.assertEqual(self.call("POST", "/v1/skills-profile", {"cv": "fake"})[0], 503)   # still loading
        self.wait_state("active")
        code, body, _ = self.call("POST", "/v1/skills-profile", {"cv": "fake"})
        self.assertEqual(code, 200)
        self.assertEqual(body["echo"], {"cv": "fake"})
        self.assertEqual(self.seen[-1], {"auth": "Bearer internal-key", "who": "alice", "path": "/v1/skills-profile"})
        _, st, _ = self.call("GET", "/v1/status")
        self.assertIsNotNone(st["active_until"])
        self.wait_state("idle")                                   # after idle_s without requests
        self.assertEqual(self.swaps()[0], "in")
        self.assertEqual(self.swaps()[-1], "out")
        self.assertIn("beat", self.swaps())

    def test_status_alone_does_not_swap(self) -> None:
        for _ in range(3):
            self.call("GET", "/v1/status")
        self.assertEqual(self.swaps(), [])

    def test_warmup_and_cooldown(self) -> None:
        self.cfg.idle_s = 60
        code, st, _ = self.call("POST", "/v1/warmup")
        self.assertEqual((code, st["state"]), (202, "loading"))
        self.wait_state("active")
        code, st, _ = self.call("POST", "/v1/cooldown")
        self.assertEqual((code, st["state"]), (202, "unloading"))
        self.assertEqual(self.call("POST", "/v1/skills-profile", {})[0], 503)   # during unloading: swap in again
        self.wait_state("active")
        self.assertEqual(self.swaps().count("in"), 2)

    def test_pinned_warmup_ignores_the_idle_timer_until_cooldown(self) -> None:
        self.cfg.idle_s = 1
        code, _, _ = self.call("POST", "/v1/warmup?pin=1")
        self.assertEqual(code, 202)
        st = self.wait_state("active")
        self.assertIsNone(st["active_until"])
        self.assertIn("held on", st["detail"])
        time.sleep(2.5)                                           # well past idle_s
        self.assertEqual(self.call("GET", "/v1/status")[1]["state"], "active")
        self.assertNotIn("out", self.swaps())
        self.call("POST", "/v1/cooldown")
        self.wait_state("idle")
        self.call("POST", "/v1/warmup")                           # a plain warmup after that has the timer again
        self.wait_state("active")
        self.wait_state("idle")

    def test_long_request_is_not_unloaded_midway(self) -> None:
        self.cfg.idle_s = 1
        self.call("POST", "/v1/warmup")
        self.wait_state("active")
        code, _, _ = self.call("POST", "/v1/skills-profile", {}, headers={"X-Slow": "2"})
        self.assertEqual(code, 200)
        self.assertNotIn("out", self.swaps())

    def test_failed_swap_in_goes_back_to_idle_with_the_reason(self) -> None:
        (self.dir / "fail").touch()
        self.call("POST", "/v1/warmup")
        st = self.wait_state("idle")
        self.assertIn("images not pulled", st["detail"])
        self.assertEqual(self.swaps(), ["in", "out"])

    def test_keys_file_is_reread_so_revoking_works(self) -> None:
        self.assertEqual(self.call("GET", "/v1/status")[0], 200)
        (self.dir / "keys").write_text("")
        self.assertEqual(self.call("GET", "/v1/status")[0], 401)

    def test_logs_hold_no_body_and_no_key(self) -> None:
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.call("POST", "/v1/skills-profile", {"cv": "Jan Jansen, secret CV text"})
            self.call("GET", "/v1/status", key="wrong")
            time.sleep(0.3)                                       # the log line comes after the reply
        text = out.getvalue()
        self.assertIn("POST /v1/skills-profile 503", text)
        self.assertIn("who=alice", text)
        self.assertNotIn("Jan Jansen", text)
        self.assertNotIn(self.key, text)
        self.assertNotIn("wrong", text)


if __name__ == "__main__":
    unittest.main()
