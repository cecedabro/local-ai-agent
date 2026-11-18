"""Talking to the model through the status service's OpenAI-compatible gateway (chat/SPEC.md §5).

Fill in the functions marked TODO. Use httpx (already installed), never `requests`.
Tests: chat/tests/test_step_gateway.py
"""
from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx


class Busy(Exception):
    """The gateway answered 503 or 429: the model is busy, try again later."""


class GatewayError(Exception):
    """The gateway answered another non-200 status, or could not be reached (status 502)."""

    def __init__(self, status: int) -> None:
        super().__init__(f"gateway answered {status}")
        self.status = status


def request_body(messages: list[dict], think: bool, stream: bool = True) -> dict:
    """The JSON body for POST <GATEWAY_URL>/chat/completions.

    TODO (task S02). Return exactly:
      {"model": "coder", "messages": messages, "stream": stream,
       "chat_template_kwargs": {"enable_thinking": think}}
    and when stream is True also the key "stream_options": {"include_usage": True}.
    """
    body: dict[str, Any] = {
        "model": "coder",
        "messages": messages,
        "stream": stream,
        "chat_template_kwargs": {"enable_thinking": think},
    }
    if stream:
        body["stream_options"] = {"include_usage": True}
    return body


def parse_line(line: str) -> list[tuple[str, Any]]:
    """Turn one line of the gateway's event stream into events.

    TODO (task S02). Rules:
      - a line that does not start with "data:" (blank lines, comments) -> []
      - "data: [DONE]" -> [("done", None)]
      - otherwise json.loads the text after "data:" (strip spaces); invalid JSON -> []
      - for the first item of obj["choices"] (if any): its "delta" dict:
          delta["reasoning_content"] non-empty -> ("reasoning", text)
          delta["content"] non-empty           -> ("content", text)
      - obj["usage"] present and not None -> ("usage", obj["usage"])
      Keep that order: reasoning, content, usage.
    Example: 'data: {"choices":[{"delta":{"content":"Hi"}}]}' -> [("content", "Hi")]
    """
    if not line.startswith("data:"):
        return []

    payload = line[len("data:"):].strip()

    if payload == "[DONE]":
        return [("done", None)]

    try:
        obj = json.loads(payload)
    except (json.JSONDecodeError, ValueError):
        return []

    events: list[tuple[str, Any]] = []

    choices = obj.get("choices")
    if choices:
        first = choices[0]
        delta = first.get("delta", {})
        rc = delta.get("reasoning_content")
        if rc:
            events.append(("reasoning", rc))
        c = delta.get("content")
        if c:
            events.append(("content", c))

    usage = obj.get("usage")
    if usage is not None:
        events.append(("usage", usage))

    return events


async def stream_chat(settings: Any, messages: list[dict], think: bool) -> AsyncIterator[tuple[str, Any]]:
    """Stream one answer: yields ("reasoning", str), ("content", str) and ("usage", dict) events.

    TODO (task S03). Steps:
      1. async with httpx.AsyncClient(timeout=600) as client:
      2.   async with client.stream("POST", settings.GATEWAY_URL.rstrip("/") + "/chat/completions",
               json=request_body(messages, think), headers=<Authorization: Bearer settings.GATEWAY_KEY
               when the key is set>) as resp:
      3.     resp.status_code in (503, 429) -> raise Busy(); other != 200 -> raise GatewayError(status)
      4.     async for line in resp.aiter_lines(): for each event in parse_line(line):
               ("done", None) -> return; anything else -> yield it
      httpx.ConnectError / httpx.TimeoutException -> raise GatewayError(502).
    Busy and GatewayError are raised on the first `async for` step, before anything is yielded.
    """
    headers: dict[str, str] | None = None
    if settings.GATEWAY_KEY:
        headers = {"Authorization": f"Bearer {settings.GATEWAY_KEY}"}

    url = settings.GATEWAY_URL.rstrip("/") + "/chat/completions"

    try:
        async with httpx.AsyncClient(timeout=600) as client:
            async with client.stream("POST", url, json=request_body(messages, think), headers=headers) as resp:
                if resp.status_code in (503, 429):
                    raise Busy()
                if resp.status_code != 200:
                    raise GatewayError(resp.status_code)

                async for line in resp.aiter_lines():
                    for event in parse_line(line):
                        if event[0] == "done":
                            return
                        yield event
    except (httpx.ConnectError, httpx.TimeoutException):
        raise GatewayError(502)


async def complete(settings: Any, messages: list[dict]) -> str:
    """One non-streaming answer (used to summarise). Returns choices[0].message.content.

    TODO (task S04). POST the same URL with request_body(messages, think=False, stream=False),
    timeout 600, same Authorization header. 503/429 -> Busy; other != 200 -> GatewayError(status);
    connection errors -> GatewayError(502).
    """
    headers: dict[str, str] | None = None
    if settings.GATEWAY_KEY:
        headers = {"Authorization": f"Bearer {settings.GATEWAY_KEY}"}

    url = settings.GATEWAY_URL.rstrip("/") + "/chat/completions"

    try:
        async with httpx.AsyncClient(timeout=600) as client:
            async with client.stream("POST", url, json=request_body(messages, think=False, stream=False), headers=headers) as resp:
                if resp.status_code in (503, 429):
                    raise Busy()
                if resp.status_code != 200:
                    raise GatewayError(resp.status_code)

                body = json.loads(await resp.aread())
                return body["choices"][0]["message"]["content"]
    except (httpx.ConnectError, httpx.TimeoutException):
        raise GatewayError(502)


def _unused() -> None:  # keeps the json import for the code you will write
    json.dumps({})
