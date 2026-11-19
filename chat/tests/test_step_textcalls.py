"""Tool calls the model writes as text instead of in tool_calls (chat/SPEC.md §12.3). Protected."""
from __future__ import annotations

import asyncio
import json

from coder import answer, gateway
from coder.db import DB

XML = ("<tool_call>\n<function=open_page>\n<parameter=url>\nhttps://boksa.example/a/\n</parameter>\n</function>\n</tool_call>\n"
       "<tool_call> <function=web_search> <parameter=query> cloud engineer </parameter> </function> </tool_call>")


def test_text_tool_calls():
    calls = gateway.text_tool_calls(XML)
    assert [(c["name"], json.loads(c["arguments"])) for c in calls] == [
        ("open_page", {"url": "https://boksa.example/a/"}), ("web_search", {"query": "cloud engineer"})]
    hermes = '<tool_call>{"name": "web_search", "arguments": {"query": "x"}}</tool_call>'
    assert gateway.text_tool_calls(hermes) == [{"id": "", "name": "web_search", "arguments": '{"query": "x"}'}]
    assert gateway.text_tool_calls("no calls here, a < b") == []


def test_tag_split():
    assert gateway.tag_split("Hello") == ("Hello", "")
    assert gateway.tag_split("Let me look. <tool_call><function=x>") == ("Let me look. ", "<tool_call><function=x>")
    assert gateway.tag_split("Let me look. <to") == ("Let me look. ", "<to")
    assert gateway.tag_split("a < b") == ("a < b", "")


class S:
    STATUS_URL = ""
    CTX_DEFAULT = 32768
    WAIT_LIMIT = 6
    SEARXNG_URL = "http://searx"


def test_answer_runs_calls_written_as_text(tmp_path):
    db = DB(str(tmp_path))
    db.init_schema()
    db.create_user("u", "x", ["chat"])
    c = db.create_chat("u", "general")
    db.add_message(c["id"], "user", "boksa outcomes?")
    chat = db.get_chat(c["id"], "u")
    rounds = []

    async def stream(settings, messages, think, tools=None):
        rounds.append(messages)
        if len(rounds) == 1:                       # the calls arrive as text, split over chunks
            for piece in ("Let me open them. <to", "ol_call> <function=open_page> <parameter=url> https://boksa.example/a/ ",
                          "</parameter> </function> </tool_call>"):
                yield ("content", piece)
        else:
            yield ("content", "Here they are [1].")

    ran = []

    async def run_tool(name, arguments, cfg, client, searxng_url):
        ran.append((name, json.loads(arguments)))
        return "PAGE", [{"title": "A", "url": "https://boksa.example/a/", "kind": "page", "quote": "q"}]

    async def collect():
        return [x async for x in answer.run_answer(db, S(), chat, False, stream=stream, run_tool=run_tool)]
    out = "".join(asyncio.run(collect()))
    assert ran == [("open_page", {"url": "https://boksa.example/a/"})], "the call written as text was run"
    assert "tool_call" not in out and "<to" not in out, "the call text never reaches the page"
    assert '"Let me open them. "' in out and '"Here they are [1]."' in out
    last = db.get_messages(chat["id"], "u")[-1]["content"]
    assert "tool_call" not in last and last.endswith("Here they are [1].")
