"""Stand-in for the agent-status service in the self-test.

Appends every POST (body plus `_path` and `_auth`) as one JSON line to argv[1] and prints
its port on stdout. The first argv[2] lease acquires are refused, so the loop has to queue.
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = open(sys.argv[1], "a", buffering=1)  # noqa: SIM115 - lives as long as the process
refuse = int(sys.argv[2]) if len(sys.argv) > 2 else 0


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 - http.server naming
        global refuse
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        LOG.write(json.dumps({**body, "_path": self.path, "_auth": self.headers.get("Authorization", "")}) + "\n")
        reply: dict = {"ok": True}
        if self.path == "/api/lease" and body.get("action") == "acquire":
            granted = refuse <= 0
            refuse -= 1
            reply = {"granted": granted, "position": 0 if granted else 1,
                     "holder": None if granted else {"repo": "other-repo", "branch": "agent/x"}}
        data = json.dumps(reply).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_: object) -> None:
        pass


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
print(server.server_port, flush=True)
server.serve_forever()
