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


EH_ANSWER = ("## Kort antwoord\nApixaban lijkt veiliger dan warfarine bij een ernstig verminderde nierfunctie.\n\n## Onderzoek\n"
             '> "apixaban 2.5 mg twice daily caused fewer major bleeds than warfarin" [1]\n\n'
             '> "apixaban is completely safe in every patient on dialysis" [2]\n\n'
             "Bij dialyse zijn er volgens de onderzoekers vooral observationele studies beschikbaar en geen trials.\n\n"
             "## Sterkte van het bewijs\nHet bewijs is beperkt.")
LONG = "\n\n".join(f"Paragraph {i}: " + "the quick brown fox jumps over the lazy dog " * 4 for i in range(1, 15))
CODE = "Here it is:\n\n```html page.html\n<!doctype html>\n<title>Hi</title>\n<p class=\"x\">Hello</p>\n```\n"


class Script(dict):
    """The fake's answer depends on the last message: "long" -> 14 paragraphs, "html" -> a file."""
    def __init__(self, gw):
        super().__init__(usage={"prompt_tokens": 500, "completion_tokens": 40})
        self.gw = gw

    def get(self, key, default=None):
        msgs = self.gw.requests[-1]["body"]["messages"]
        if key == "tool_calls":                   # "search ..." -> one web search first (SPEC §12.3)
            if "word file" in str(msgs[-1]["content"]):                       # SPEC §16.3: the model makes a file
                return [{"name": "make_file", "arguments": '{"name": "Weekplan", "format": "docx", "content": "# Week\\n\\n- rust"}'}]
            return [{"name": "web_search", "arguments": '{"query": "flu shot"}'}] if "search" in msgs[-1]["content"] else None
        if key == "content" and str(msgs[-1]["content"]).startswith("Sources:"):     # Clear Source's answer (SPEC §13)
            return [w + " " for w in EH_ANSWER.split(" ")]
        if key == "content" and msgs[-1]["role"] == "tool":
            return [w + " " for w in "Yes, people over 60 get it every autumn [1].".split(" ")]
        if key == "content":
            last = msgs[-1]["content"]
            text = LONG if "long" in last else CODE if "html" in last else ANSWER
            return [w + " " for w in text.split(" ")]
        if key == "reasoning":
            return ["hmm"]
        return super().get(key, default)


def main() -> None:
    port, data = int(sys.argv[1]), sys.argv[2]
    gw = FakeGateway()
    gw.script = Script(gw)
    handler = gw.server.RequestHandlerClass
    post = handler.do_POST

    def slow_post(self):                          # stream like a real model, ~25 words/s
        write = self.wfile.write

        def slow(b: bytes):
            if b.startswith(b"data: ") and b'"reasoning_content"' in b:
                time.sleep(0.9)                   # the model reads the prompt first
            if b.startswith(b"data: ") and b'"content"' in b:
                time.sleep(0.04)
            return write(b)
        self.wfile.write = slow
        try:
            return post(self)
        except (BrokenPipeError, ConnectionResetError):
            return None                           # the app closed the stream (stop)
    handler.do_POST = slow_post
    import coder.tools as tools_mod
    tools_mod.PUBMED = gw.url                     # PubMed's E-utilities, faked by the gateway
    gw.plan = '[{"source": "pubmed", "query": "apixaban renal", "mesh": ["chronic kidney disease"]}]'   # SPEC §14.1
    gw.mesh = {"Renal Insufficiency, Chronic": ["Renal Insufficiency, Chronic", "Chronic Kidney Disease"]}
    gw.pubmed = [{"pmid": "101", "title": "Apixaban in chronic kidney disease", "journal": "Kidney Int", "year": "2022",
                  "types": ["Journal Article", "Meta-Analysis"],
                  "abstract": "In patients with eGFR 15-29, apixaban 2.5 mg twice daily caused fewer major bleeds than warfarin."},
                 {"pmid": "102", "title": "DOACs in dialysis", "journal": "BMJ", "year": "2021",
                  "abstract": "Evidence in patients on dialysis is limited to observational studies."}]
    gw.search_results = [{"title": "Griepprik", "url": "https://www.rivm.nl/griepprik", "content": "Wie krijgt hem"}]
    gw.start()
    app = create_app({"DATA_DIR": data, "GATEWAY_URL": gw.url + "/v1", "GATEWAY_KEY": "k", "STATUS_URL": gw.url,
                      "ADMIN_USER": "cedric", "ADMIN_PASSWORD": "browser-test-password", "SECURE_COOKIES": "0",
                      "RP_ID": "localhost", "ORIGIN": f"http://localhost:{port}", "SEARXNG_URL": gw.url})
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
