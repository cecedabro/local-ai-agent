"""A fake OpenAI-compatible gateway + status endpoint for the chat tests (stdlib only).

    gw = FakeGateway(); gw.start()
    gw.script = {"busy": 1, "reasoning": ["hm"], "content": ["Hel", "lo"], "usage": {...}}
    gw.script["tool_calls"] = [{"name": "web_search", "arguments": '{"query": "x"}'}]
        -> a streamed answer whose last message is not a tool result asks for these tools first
    gw.search_results = [{"title", "url", "content"}]   (GET /search?format=json, a fake SearXNG)
    ... requests land in gw.requests ...
    gw.stop()
"""
from __future__ import annotations

import json
import threading
from urllib.parse import parse_qs, urlsplit
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeGateway:
    def __init__(self) -> None:
        self.requests: list[dict] = []          # {"path", "headers", "body"}
        self.script: dict = {}
        self.n_ctx = 32768
        self.summary = "SUMMARY OF EARLIER MESSAGES"
        self.busy_left = 0
        self.search_results: list[dict] = []
        self.plan = '[{"source": "pubmed", "query": "apixaban renal"}]'   # Clear Source's planning call (SPEC §13.2)
        self.pubmed: list[dict] = []          # fake PubMed: [{"pmid", "title", "abstract", "journal", "year", "types"?}]
        self.mesh: dict[str, list[str]] = {}  # fake MeSH: heading -> its terms (heading first), SPEC §14.1
        self._lock = threading.Lock()
        gw = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a: object) -> None:
                pass

            def do_GET(self) -> None:
                if self.path.startswith(("/esearch.fcgi", "/efetch.fcgi", "/esummary.fcgi")):
                    with gw._lock:
                        gw.requests.append({"path": self.path, "body": None, "headers": dict(self.headers)})
                    q = parse_qs(urlsplit(self.path).query)
                    heads = list(gw.mesh)
                    if q.get("db") == ["mesh"] and self.path.startswith("/esearch"):
                        term = q.get("term", [""])[0].lower()
                        ids = [str(i) for i, h in enumerate(heads) if any(f'"{t.lower()}"' in term for t in gw.mesh[h])]
                        body = json.dumps({"esearchresult": {"idlist": ids}}).encode()
                        ctype = "application/json"
                    elif self.path.startswith("/esummary"):
                        ids = q.get("id", [""])[0].split(",")
                        res = {"uids": ids, **{i: {"ds_meshterms": gw.mesh[heads[int(i)]]} for i in ids if i.isdigit() and int(i) < len(heads)}}
                        body = json.dumps({"result": res}).encode()
                        ctype = "application/json"
                    elif self.path.startswith("/esearch"):
                        body = json.dumps({"esearchresult": {"idlist": [a["pmid"] for a in gw.pubmed], "count": str(len(gw.pubmed)),
                                                             "querytranslation": q.get("term", [""])[0]}}).encode()
                        ctype = "application/json"
                    else:
                        arts = "".join(
                            f"<PubmedArticle><MedlineCitation><PMID>{a['pmid']}</PMID><Article><Journal><Title>{a.get('journal', '')}</Title>"
                            f"<JournalIssue><PubDate><Year>{a.get('year', '')}</Year></PubDate></JournalIssue></Journal>"
                            f"<ArticleTitle>{a['title']}</ArticleTitle><Abstract><AbstractText>{a['abstract']}</AbstractText></Abstract>"
                            f"<PublicationTypeList>{''.join(f'<PublicationType>{t}</PublicationType>' for t in a.get('types', []))}</PublicationTypeList>"
                            f"</Article></MedlineCitation></PubmedArticle>" for a in gw.pubmed)
                        body = f"<?xml version='1.0'?><PubmedArticleSet>{arts}</PubmedArticleSet>".encode()
                        ctype = "text/xml"
                    self.send_response(200)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if self.path.startswith("/search"):
                    with gw._lock:
                        gw.requests.append({"path": self.path, "body": None, "headers": dict(self.headers)})
                    body = json.dumps({"results": gw.search_results}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if self.path == "/api/status":
                    body = json.dumps({"gateway": {"enabled": True, "n_ctx": gw.n_ctx}}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_error(404)

            def do_POST(self) -> None:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                with gw._lock:
                    gw.requests.append({"path": self.path, "body": body,
                                         "headers": {k.title(): v for k, v in self.headers.items()}})
                    busy = gw.busy_left > 0
                    if busy:
                        gw.busy_left -= 1
                if self.path != "/v1/chat/completions":
                    self.send_error(404)
                    return
                if busy:
                    self.send_response(503)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"error":"busy"}')
                    return
                s = gw.script
                if s.get("status"):
                    self.send_response(int(s["status"]))
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"error":"boom"}')
                    return
                if not body.get("stream"):
                    first = ((body.get("messages") or [{}])[0].get("content") or "")
                    reply = gw.plan if first.startswith("You plan literature searches") else gw.summary
                    out = json.dumps({"choices": [{"message": {"role": "assistant", "content": reply}}],
                                      "usage": {"prompt_tokens": 100, "completion_tokens": 20}}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(out)))
                    self.end_headers()
                    self.wfile.write(out)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()

                def chunk(delta: dict | None, usage: dict | None = None) -> None:
                    obj: dict = {"object": "chat.completion.chunk",
                                 "choices": [] if delta is None else [{"index": 0, "delta": delta}]}
                    if usage:
                        obj["usage"] = usage
                    self.wfile.write(b"data: " + json.dumps(obj).encode() + b"\n\n")
                    self.wfile.flush()

                chunk({"role": "assistant"})
                msgs = body.get("messages") or [{}]
                calls = s.get("tool_calls")
                if calls and body.get("tools") and msgs[-1].get("role") != "tool":
                    for i, call in enumerate(calls):
                        args = call["arguments"]
                        chunk({"tool_calls": [{"index": i, "id": f"call_{i}", "type": "function",
                                               "function": {"name": call["name"], "arguments": args[:5]}}]})
                        chunk({"tool_calls": [{"index": i, "function": {"arguments": args[5:]}}]})
                    chunk(None, {"prompt_tokens": 50, "completion_tokens": 9})
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                    return
                for r in s.get("reasoning", []):
                    chunk({"reasoning_content": r})
                for c in s.get("content", ["Hello", " there"]):
                    chunk({"content": c})
                if "usage" in s:
                    if s["usage"]:
                        chunk(None, s["usage"])
                else:
                    chunk(None, {"prompt_tokens": 50, "completion_tokens": 2})
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def start(self) -> "FakeGateway":
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def chat_requests(self) -> list[dict]:
        return [r for r in self.requests if r["path"] == "/v1/chat/completions"]
