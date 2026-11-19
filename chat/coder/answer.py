"""Producing one answer as server-sent events (chat/SPEC.md §5).

Fill in the functions marked TODO. Tests: chat/tests/test_step_answer.py
The route POST /api/chats/{id}/messages validates the request, stores the user message, and then
returns StreamingResponse(run_answer(...), media_type="text/event-stream").
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, AsyncIterator, Callable

import httpx

from . import context, gateway, profiles, tools

MAX_ROUNDS = 4

LABELS = {
    "waiting": "Waiting for the model… you're next",
    "compressing": "Compressing older messages…",
    "thinking": "Thinking…",
    "writing": "Writing the answer…",
    "reading": "Reading your message…",
}


def sse(event: str, data: dict) -> str:
    """One event in text/event-stream format."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def phase(name: str) -> str:
    """Yield the phase SSE event."""
    return sse("phase", {"phase": name, "label": LABELS[name]})


def make_title(text: str) -> str:
    """The chat title from the first user message."""
    import re
    title = re.sub(r'\s+', ' ', text).strip()
    if len(title) > 60:
        title = title[:59].rstrip() + "…"
    return title


async def run_answer(db: Any, settings: Any, chat: dict, think: bool, *,
                     stream: Callable = gateway.stream_chat,
                     complete: Callable = gateway.complete,
                     sleep: Callable = asyncio.sleep,
                     clock: Callable[[], float] = time.monotonic,
                     stop: asyncio.Event | None = None,
                     run_tool: Callable = tools.run_tool) -> AsyncIterator[str]:
    """Yield the SSE text of one answer. The user's message is already stored.

    Implements tool rounds (up to MAX_ROUNDS=4) per SPEC §12.3, with:
      - compress phase, reading phase, tool loop (searching/reading), writing phase
      - stop support (task R04), client disconnect (task R05), phases/chunks/done (task R03).
    """
    # Step 1
    if context.used(db, chat) >= context.window(settings)["compress_at"]:
        yield phase("compressing")
        await context.compress(db, chat, settings, complete)
        chat = db.get_chat(chat["id"], chat["username"])

    # Step 2
    messages = context.build_messages(db, chat)
    cfg = profiles.config(db, chat["username"], chat["profile"])
    specs = tools.tool_specs(cfg)
    yield phase("reading")

    # Step 3-4: Tool rounds loop
    sources: list[dict] = []
    rounds = 0
    client = httpx.AsyncClient()
    text = ""
    count = 0
    t0 = None
    usage = None
    stopped = False
    msg_stored = False

    try:
        while True:
            start = clock()
            waited = False
            offer = specs if specs and rounds < MAX_ROUNDS else None

            # Busy retry loop
            while True:
                if offer is not None:
                    agen = stream(settings, messages, think, tools=offer)
                else:
                    agen = stream(settings, messages, think)
                try:
                    first = await agen.__anext__()
                except gateway.Busy:
                    if not waited:
                        yield phase("waiting")
                        waited = True
                    if clock() - start >= settings.WAIT_LIMIT:
                        yield sse("error", {"error": "The model is still busy, try again in a minute.", "status": 503})
                        return
                    await sleep(2)
                    continue
                except gateway.GatewayError as e:
                    yield sse("error", {"error": "The model could not answer.", "status": e.status})
                    return
                except StopAsyncIteration:
                    first = None
                break

            # Process events for this round
            said = ""
            calls_dict: dict[int, dict] = {}
            content_started = False

            async def events():
                if first is not None:
                    yield first
                async for ev in agen:
                    yield ev

            async for tag, val in events():
                if tag == "reasoning":
                    if not hasattr(events, "_thinking_sent") or not events._thinking_sent:
                        yield phase("thinking")
                        events._thinking_sent = True
                elif tag == "content":
                    if not content_started:
                        content_started = True
                        yield phase("writing")
                        t0 = clock()
                    said += val
                    text += val
                    count += 1
                    yield sse("delta", {"text": val})
                elif tag == "usage":
                    usage = val
                elif tag in ("tool_call", "tool_calls"):
                    gateway.merge_tool_calls(calls_dict, val)
                elif tag == "done":
                    stopped = True
                    break
                if stop is not None and stop.is_set():
                    stopped = True
                    break

            calls = [v for _, v in sorted(calls_dict.items())]

            # Handle tool calls for this round
            if calls and offer:
                rounds += 1
                ids = [c["id"] or f"call_{rounds}_{i}" for i, c in enumerate(calls)]
                assistant_msg = {
                    "role": "assistant",
                    "content": said,
                    "tool_calls": [
                        {"id": ids[i], "type": "function",
                         "function": {"name": c["name"], "arguments": c["arguments"]}}
                        for i, c in enumerate(calls)
                    ],
                }
                messages = messages + [assistant_msg]

                for i, call in enumerate(calls):
                    name = call["name"]
                    arguments = call["arguments"]
                    ph, label = tools.describe(name, arguments)
                    yield sse("phase", {"phase": ph, "label": label})

                    result, found = await run_tool(name, arguments, cfg, client, settings.SEARXNG_URL)
                    if isinstance(found, dict):
                        found = [found] if found else []
                    if not isinstance(found, list):
                        found = []
                    for src in found:
                        src_url = src.get("url", "")
                        existing = next((s for s in sources if s.get("url") == src_url), None)
                        if existing:
                            src["n"] = existing["n"]
                        else:
                            src = {**src, "n": len(sources) + 1}
                            sources.append(src)
                    if found:
                        new_urls = {s.get("url") for s in found}
                        result += "\n\nCite these as:\n" + "\n".join(
                            f"[{s['n']}] {s['url']}" for s in sources if s.get("url") in new_urls
                        )
                    messages.append({"role": "tool", "tool_call_id": ids[i], "content": result})

                if stop is not None and stop.is_set():
                    stopped = True
                    break

                # Continue the loop if the model has already written text
                if said:
                    yield phase("writing")
                    continue
                continue
            else:
                break

        msg_stored = True
    except gateway.GatewayError as e:
        yield sse("error", {"error": "The model could not answer.", "status": e.status})
        return
    finally:
        if not msg_stored and text:
            db.add_message(chat["id"], "assistant", text, tokens=count, tps=None, stopped=True, sources=sources)
        try:
            await agen.aclose()
        except Exception:
            pass
        await client.aclose()

    # Step 5
    tokens = usage["completion_tokens"] if usage else count
    tps = round(tokens / (clock() - t0), 1) if t0 else None

    if text or not stopped:
        msg_id = db.add_message(chat["id"], "assistant", text, tokens=tokens, tps=tps, stopped=stopped, sources=sources)
        msg_stored = True
    else:
        msg_id = None
    if msg_id is not None and usage:
        db.update_chat_usage(chat["id"], usage["prompt_tokens"], usage["completion_tokens"])

    # Step 6
    if chat["title"] == "New chat":
        msgs = db.get_messages(chat["id"], chat["username"])
        first_user = next((m for m in msgs if m["role"] == "user"), None)
        if first_user:
            db.update_chat_title(chat["id"], chat["username"], make_title(first_user["content"]))

    # Step 7
    chat = db.get_chat(chat["id"], chat["username"])
    yield sse("done", {"message_id": msg_id, "tokens": tokens, "tps": tps,
                        "context": context.info(db, chat, settings),
                        "title": chat["title"], "stopped": stopped,
                        "sources": sources})


def _unused() -> None:
    json.dumps({})
