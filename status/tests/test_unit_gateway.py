"""Tests for the model gateway, owners and workers (SPEC §8, §11). Run: make check"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from test_contract import TOKEN, Base, app, free_port

KEYS = {"loop-key-123": ("agent-loop", "batch"), "scrum-key-123": ("scrum-agent", "interactive")}


class FakeLlama:
    """/v1/chat/completions: records the order requests arrive in (by body "tag"); a tag in
    `hold` blocks until released. stream=true answers with three SSE chunks."""

    def __init__(self) -> None:
        self.order: list[str] = []
        self.hold: dict[str, threading.Event] = {}
        self.auth = None
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                outer.auth = self.headers.get("Authorization")
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                tag = body.get("tag", "")
                outer.order.append(tag)
                if tag in outer.hold:
                    outer.hold[tag].wait(10)
                timings = {"prompt_n": 10, "prompt_ms": 20.0, "predicted_n": 3, "predicted_ms": 75.0}
                if body.get("stream"):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    for i, word in enumerate(["Hel", "lo", "!"]):
                        chunk = {"choices": [{"delta": {"content": word}}]}
                        if i == 2:
                            chunk["timings"] = timings
                        self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                        self.wfile.flush()
                    self.wfile.write(b"data: [DONE]\n\n")
                    return
                data = json.dumps({"choices": [{"message": {"content": "hi " + tag}}], "timings": timings}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:  # noqa: N802
                data = b'{"data":[{"id":"coder"}]}' if self.path == "/v1/models" else b""
                self.send_response(200 if data else 404)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *_: object) -> None:
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/v1"

    def close(self) -> None:
        for ev in self.hold.values():
            ev.set()
        self.srv.shutdown()
        self.srv.server_close()


class GatewayBase(Base):
    def setUp(self) -> None:
        super().setUp()
        self.fake = FakeLlama()
        self.addCleanup(self.fake.close)
        self.stop()
        self.start_gw(self.fake.url)

    def start_gw(self, url: str | None) -> None:
        srv = app.make_server("127.0.0.1", 0, db_path=self.db, token=TOKEN, llama_url=url, llama_key="llk",
                              clock=self.clock, gateway_clients=KEYS, job_owners=["cecedabro"])
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.servers.append(srv)
        self.base = f"http://127.0.0.1:{srv.server_address[1]}"

    def chat(self, key: str, tag: str = "", stream: bool = False) -> tuple[int, bytes]:
        code, _, raw = self.req("POST", "/v1/chat/completions",
                                {"model": "coder", "tag": tag, "stream": stream,
                                 "messages": [{"role": "user", "content": "hi"}]}, token=key)
        return code, raw


class GatewayTest(GatewayBase):
    def test_auth(self) -> None:
        self.assertEqual(self.chat("nope")[0], 401)
        self.assertEqual(self.chat("loop-key-123")[0], 200)
        self.assertEqual(self.chat(TOKEN)[0], 200)                         # the page's chat
        self.assertEqual(self.fake.auth, "Bearer llk")                      # upstream gets the llama key
        clients = {c["client"]: c["requests"] for c in self.status()["gateway"]["by_client"]}
        self.assertEqual(clients, {"agent-loop": 1, "chat": 1})

    def test_passthrough_and_record(self) -> None:
        code, raw = self.chat("scrum-key-123", tag="x")
        self.assertEqual(json.loads(raw)["choices"][0]["message"]["content"], "hi x")
        gw = self.status()["gateway"]
        self.assertEqual(gw["recent"][0]["client"], "scrum-agent")
        self.assertEqual(gw["recent"][0]["generated"], 3)
        self.assertEqual(gw["by_client"][0]["gen_tps"], 40.0)
        self.assertEqual(self.req("GET", "/v1/models", token="loop-key-123")[0], 200)
        self.assertEqual(self.req("GET", "/v1/models", token=None)[0], 401)
        self.assertEqual(self.req("POST", "/v1/embeddings", {}, token="loop-key-123")[0], 404)

    def test_stream(self) -> None:
        code, raw = self.chat("loop-key-123", stream=True)
        self.assertEqual(code, 200)
        text = raw.decode()
        self.assertIn('"Hel"', text)
        self.assertIn("[DONE]", text)
        rec = self.status()["gateway"]["recent"][0]
        self.assertEqual((rec["generated"], rec["prompt"], rec["status"]), (3, 10, 200))

    def test_interactive_jumps_the_queue(self) -> None:
        self.fake.hold["A"] = threading.Event()
        results: dict[str, int] = {}

        def call(key: str, tag: str) -> None:
            results[tag] = self.chat(key, tag)[0]

        threads = [threading.Thread(target=call, args=("loop-key-123", "A"))]
        threads[0].start()
        for _ in range(100):
            if self.fake.order == ["A"]:
                break
            time.sleep(0.02)
        for key, tag in (("loop-key-123", "B"), ("scrum-key-123", "C")):
            t = threading.Thread(target=call, args=(key, tag))
            t.start()
            threads.append(t)
            time.sleep(0.2)
        gw = self.status()["gateway"]
        self.assertEqual([r["client"] for r in gw["running"]], ["agent-loop"])
        self.assertEqual([r["client"] for r in gw["waiting"]], ["scrum-agent", "agent-loop"])   # priority order
        self.fake.hold["A"].set()
        for t in threads:
            t.join(10)
        self.assertEqual(self.fake.order, ["A", "C", "B"])
        self.assertEqual(results, {"A": 200, "B": 200, "C": 200})

    def test_upstream_down(self) -> None:
        self.stop()
        self.start_gw(f"http://127.0.0.1:{free_port()}/v1")
        code, raw = self.chat("loop-key-123")
        self.assertEqual(code, 502)
        self.assertIn("unreachable", json.loads(raw)["error"]["message"])
        self.assertEqual(self.status()["gateway"]["by_client"][0]["errors"], 1)

    def test_off_without_llama(self) -> None:
        self.stop()
        self.start_gw(None)
        self.assertEqual(self.chat("loop-key-123")[0], 404)

    def test_pages(self) -> None:
        self.chat("loop-key-123")
        page = self.req("GET", "/", token=None)[2].decode()
        self.assertIn("Model activity", page)
        self.assertIn('id="chat"', page)
        chat = self.req("GET", "/chat", token=None)
        self.assertEqual(chat[0], 200)
        self.assertIn("/v1/chat/completions", chat[2].decode())

    def test_bad_clients_config(self) -> None:
        with self.assertRaises(SystemExit):
            app.parse_clients("loop:fast:key12345")
        with self.assertRaises(SystemExit):
            app.parse_clients("loop:batch:short")
        self.assertEqual(app.parse_clients(" a:batch:12345678 , b:interactive:abcdefgh "),
                         {"12345678": ("a", "batch"), "abcdefgh": ("b", "interactive")})


class OwnersWorkersTest(GatewayBase):
    def test_owner_allowlist(self) -> None:
        self.assertEqual(self.req("POST", "/api/jobs", {"repo": "someone/else"})[0], 400)
        self.assertEqual(self.req("POST", "/api/jobs", {"repo": "CecedaBro/woodshed"})[0], 201)

    def test_workers(self) -> None:
        self.assertEqual(self.status()["jobs"]["workers"], [])
        self.req("POST", "/api/jobs/claim", {"worker": "devbox-0"})
        self.assertEqual(self.status()["jobs"]["workers"][0]["paused"], False)
        self.req("POST", "/api/workers/ping", {"worker": "devbox-0", "paused": True})
        self.assertTrue(self.status()["jobs"]["workers"][0]["paused"])
        self.assertIn("paused", self.req("GET", "/", token=None)[2].decode())
        self.clock.t += 601
        self.assertEqual(self.status()["jobs"]["workers"], [])
