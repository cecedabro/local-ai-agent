"""Small judge for chat/coder/gateway.py (tasks S02–S04). Protected."""
from __future__ import annotations

import asyncio

import pytest

from coder import gateway
from fake_gateway import FakeGateway


class S:
    def __init__(self, url: str, key: str = "sk-test") -> None:
        self.GATEWAY_URL = url + "/v1"
        self.GATEWAY_KEY = key


@pytest.fixture()
def gw():
    g = FakeGateway().start()
    yield g
    g.stop()


async def collect(agen):
    return [e async for e in agen]


# ---------------------------------------------------------------- S02
def test_request_body():
    msgs = [{"role": "user", "content": "hi"}]
    assert gateway.request_body(msgs, True) == {
        "model": "coder", "messages": msgs, "stream": True,
        "stream_options": {"include_usage": True}, "chat_template_kwargs": {"enable_thinking": True}}
    assert gateway.request_body(msgs, False, stream=False) == {
        "model": "coder", "messages": msgs, "stream": False, "chat_template_kwargs": {"enable_thinking": False}}


def test_parse_line():
    p = gateway.parse_line
    assert p("") == [] and p(": keep-alive") == [] and p("event: x") == []
    assert p("data: [DONE]") == [("done", None)]
    assert p("data: {not json") == []
    assert p('data: {"choices":[{"delta":{"content":"Hi"}}]}') == [("content", "Hi")]
    assert p('data: {"choices":[{"delta":{"reasoning_content":"hm"}}]}') == [("reasoning", "hm")]
    assert p('data: {"choices":[{"delta":{"role":"assistant","content":""}}]}') == []
    assert p('data: {"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":2}}') == [
        ("usage", {"prompt_tokens": 5, "completion_tokens": 2})]
    assert p('data: {"choices":[{"delta":{"content":"a"}}],"usage":null}') == [("content", "a")]


# ---------------------------------------------------------------- S03
def test_stream_chat(gw):
    gw.script = {"reasoning": ["r1"], "content": ["Hel", "lo"], "usage": {"prompt_tokens": 9, "completion_tokens": 2}}
    evs = asyncio.run(collect(gateway.stream_chat(S(gw.url), [{"role": "user", "content": "x"}], True)))
    assert evs == [("reasoning", "r1"), ("content", "Hel"), ("content", "lo"),
                   ("usage", {"prompt_tokens": 9, "completion_tokens": 2})]
    req = gw.chat_requests()[-1]
    assert req["headers"].get("Authorization") == "Bearer sk-test"
    assert req["body"]["stream"] is True and req["body"]["chat_template_kwargs"] == {"enable_thinking": True}


def test_stream_chat_busy_and_errors(gw):
    gw.busy_left = 1
    with pytest.raises(gateway.Busy):
        asyncio.run(collect(gateway.stream_chat(S(gw.url), [], False)))
    gw.script = {"status": 500}
    with pytest.raises(gateway.GatewayError) as e:
        asyncio.run(collect(gateway.stream_chat(S(gw.url), [], False)))
    assert e.value.status == 500
    with pytest.raises(gateway.GatewayError) as e:
        asyncio.run(collect(gateway.stream_chat(S("http://127.0.0.1:9"), [], False)))
    assert e.value.status == 502


def test_no_key_no_header(gw):
    asyncio.run(collect(gateway.stream_chat(S(gw.url, key=""), [], False)))
    assert "Authorization" not in gw.chat_requests()[-1]["headers"]


# ---------------------------------------------------------------- S04
def test_complete(gw):
    assert asyncio.run(gateway.complete(S(gw.url), [{"role": "user", "content": "sum"}])) == gw.summary
    assert gw.chat_requests()[-1]["body"]["stream"] is False
    gw.busy_left = 1
    with pytest.raises(gateway.Busy):
        asyncio.run(gateway.complete(S(gw.url), []))
