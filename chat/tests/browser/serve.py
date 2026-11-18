"""Run Dewdrop for the browser tests: a fresh database, the fake gateway streaming a markdown answer
at a realistic pace. Usage: serve.py <port> <data dir>. Protected (human-owned)."""
from __future__ import annotations

import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))      # chat/
sys.path.insert(0, str(HERE.parent))             # chat/tests (fake_gateway)

import uvicorn  # noqa: E402

from coder.app import create_app  # noqa: E402
from fake_gateway import FakeGateway  # noqa: E402

ANSWER = ("Go a bit lower than for search: around **150–200 ms**.\n\n```javascript\nconst onInput = debounce(e => "
          "suggest(e.target.value), 180);\n```\n\n- Cancel the previous request.\n- Cache recent prefixes.\n\n"
          "For the full results page keep **300 ms**.")


def main() -> None:
    port, data = int(sys.argv[1]), sys.argv[2]
    gw = FakeGateway()
    gw.script = {"reasoning": ["hmm"], "content": [w + " " for w in ANSWER.split(" ")],
                 "usage": {"prompt_tokens": 500, "completion_tokens": 40}}
    handler = gw.server.RequestHandlerClass
    post = handler.do_POST

    def slow_post(self):                          # stream like a real model, ~25 words/s
        write = self.wfile.write

        def slow(b: bytes):
            if b.startswith(b"data: ") and b'"content"' in b:
                time.sleep(0.04)
            return write(b)
        self.wfile.write = slow
        return post(self)
    handler.do_POST = slow_post
    gw.start()
    app = create_app({"DATA_DIR": data, "GATEWAY_URL": gw.url + "/v1", "GATEWAY_KEY": "k", "STATUS_URL": gw.url,
                      "ADMIN_USER": "cedric", "ADMIN_PASSWORD": "browser-test-password", "SECURE_COOKIES": "0",
                      "RP_ID": "localhost", "ORIGIN": f"http://localhost:{port}"})
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
