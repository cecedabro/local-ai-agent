"""Wake-on-LAN relay. Runs with hostNetwork so the broadcast reaches the LAN.

POST /wake  (header X-Waker-Token)  -> sends the magic packet to PC_MAC
GET  /healthz                        -> ok
"""
import hmac
import os
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAC = os.environ["PC_MAC"].replace(":", "").replace("-", "").lower()
BROADCAST = os.environ.get("BROADCAST", "255.255.255.255")
TOKEN = os.environ["WAKER_TOKEN"]
PORT = int(os.environ.get("PORT", "9009"))

if len(MAC) != 12:
    raise SystemExit(f"bad PC_MAC: {os.environ['PC_MAC']!r}")
PACKET = b"\xff" * 6 + bytes.fromhex(MAC) * 16


def send_magic() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for port in (9, 7):
            s.sendto(PACKET, (BROADCAST, port))


class Handler(BaseHTTPRequestHandler):
    def _reply(self, code: int, body: str) -> None:
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        self._reply(200, "ok\n") if self.path == "/healthz" else self._reply(404, "not found\n")

    def do_POST(self) -> None:
        if self.path != "/wake":
            return self._reply(404, "not found\n")
        if not hmac.compare_digest(self.headers.get("X-Waker-Token", ""), TOKEN):
            return self._reply(403, "forbidden\n")
        send_magic()
        self._reply(202, "sent\n")


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
