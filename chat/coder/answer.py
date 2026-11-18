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

from . import context, gateway

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
                     stop: asyncio.Event | None = None) -> AsyncIterator[str]:
    """Yield the SSE text of one answer. The user's message is already stored.

    Round 2 (chat/SPEC.md §11.1–11.2). Tests: test_step_answer.py and test_step_round2.py.
    Rewrite the body to this shape (the S10/S11 code below already does steps 1, 3 and most of 5):

    TODO (task R03): phases, the first chunk, done.
      a. LABELS gets "reading": "Reading your message…".
      b. After the optional compress and `messages = context.build_messages(db, chat)`:
         yield phase("reading")   (once, before the busy loop; "waiting" may follow it)
      c. Handle `first` and the rest of `agen` in ONE loop, so the first chunk is treated like every
         other one (today the first content chunk is never sent as a delta: a bug). For example:
             async def events():
                 if first is not None: yield first
                 async for ev in agen: yield ev
             async for tag, val in events(): ...
         ("reasoning", _): only the first time: yield phase("thinking")   (it is sent once, not per chunk)
         ("content", t) with t non-empty: the first time yield phase("writing") and t0 = clock();
                                          then text += t; count += 1; yield sse("delta", {"text": t})
         ("usage", u): usage = u
      d. done gets two more keys: "title": the chat's title after the title step (from the re-read chat),
         and "stopped": False (task R04 makes it True when stopped).

    TODO (task R04): stop. Add the keyword parameter `stop: asyncio.Event | None = None`.
      After handling each event in the loop of c.: if stop is not None and stop.is_set(): stopped = True; break.
      Then: store the answer only if there is text (`if text or not stopped`), with
      db.add_message(..., stopped=stopped); otherwise msg_id = None. done carries "stopped": stopped.
      Close the gateway stream when leaving: `await agen.aclose()` (see R05, in the finally).

    TODO (task R05): the browser went away. Wrap the loop of c. and everything after it in
         try: ... finally:
             if the answer was not stored yet (no add_message and no error event) and text:
                 db.add_message(chat["id"], "assistant", text, tokens=count, tps=None, stopped=True)
             try: await agen.aclose()
             except Exception: pass
      (Starlette closes the generator with aclose() when the client disconnects; the finally runs then.)
      Keep the GatewayError handling: a GatewayError while reading -> yield the error event, store nothing.
    """
    # Step 1
    if context.used(db, chat) >= context.window(settings)["compress_at"]:
        yield phase("compressing")
        await context.compress(db, chat, settings, complete)
        chat = db.get_chat(chat["id"], chat["username"])

    # Step 2
    messages = context.build_messages(db, chat)
    yield phase("reading")

    # Step 3: Start the stream, retrying while busy
    start = clock()
    waited = False
    while True:
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

    # Step 4: Handle first and remaining events in ONE loop
    text = ""
    count = 0
    t0 = None
    usage = None
    content_started = False
    thinking_sent = False

    async def events():
        if first is not None:
            yield first
        async for ev in agen:
            yield ev

    stopped = False
    msg_stored = False
    try:
        async for tag, val in events():
            if tag == "reasoning":
                if not thinking_sent:
                    yield phase("thinking")
                    thinking_sent = True
            elif tag == "content":
                if not content_started:
                    content_started = True
                    yield phase("writing")
                    t0 = clock()
                text += val
                count += 1
                yield sse("delta", {"text": val})
            elif tag == "usage":
                usage = val
            if stop is not None and stop.is_set():
                stopped = True
                break
        msg_stored = True
    except gateway.GatewayError as e:
        yield sse("error", {"error": "The model could not answer.", "status": e.status})
        return
    finally:
        if not msg_stored and text:
            db.add_message(chat["id"], "assistant", text, tokens=count, tps=None, stopped=True)
        try:
            await agen.aclose()
        except Exception:
            pass

    # Step 5
    tokens = usage["completion_tokens"] if usage else count
    tps = round(tokens / (clock() - t0), 1) if t0 else None

    if text or not stopped:
        msg_id = db.add_message(chat["id"], "assistant", text, tokens=tokens, tps=tps, stopped=stopped)
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
                        "title": chat["title"], "stopped": stopped})


def _unused() -> None:
    json.dumps({})
