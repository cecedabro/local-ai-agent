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
}


def sse(event: str, data: dict) -> str:
    """TODO (task S09). One event in text/event-stream format:
    f"event: {event}\\ndata: {json.dumps(data, ensure_ascii=False)}\\n\\n"."""
    raise NotImplementedError


def phase(name: str) -> str:
    """TODO (task S09). sse("phase", {"phase": name, "label": LABELS[name]})."""
    raise NotImplementedError


def make_title(text: str) -> str:
    """TODO (task S09). The chat title from the first user message: collapse all whitespace to single
    spaces and strip; if longer than 60 characters: the first 60, trailing spaces removed, plus "…"."""
    raise NotImplementedError


async def run_answer(db: Any, settings: Any, chat: dict, think: bool, *,
                     stream: Callable = gateway.stream_chat,
                     complete: Callable = gateway.complete,
                     sleep: Callable = asyncio.sleep,
                     clock: Callable[[], float] = time.monotonic) -> AsyncIterator[str]:
    """Yield the SSE text of one answer. The user's message is already stored.

    TODO (task S10: steps 2–5; task S11: steps 1 and 6–7).
      1. (S11) if context.used(db, chat) >= context.window(settings)["compress_at"]:
             yield phase("compressing"); await context.compress(db, chat, settings, complete);
             chat = db.get_chat(chat["id"], chat["username"])
      2. messages = context.build_messages(db, chat)
      3. Start the stream, retrying while busy:
             start = clock(); waited = False
             loop:
               agen = stream(settings, messages, think)
               try: first = await agen.__anext__()        # Busy/GatewayError come from here
               except gateway.Busy:
                   if not waited: yield phase("waiting"); waited = True
                   if clock() - start >= settings.WAIT_LIMIT:
                       yield sse("error", {"error": "The model is still busy, try again in a minute.", "status": 503}); return
                   await sleep(2); continue
               except gateway.GatewayError as e:
                   yield sse("error", {"error": "The model could not answer.", "status": e.status}); return
               except StopAsyncIteration: first = None
               break
      4. Handle `first` and then every event from `agen` the same way:
             ("reasoning", _) -> the first time: yield phase("thinking")
             ("content", t)  -> the first time: yield phase("writing") and note t0 = clock();
                                 then yield sse("delta", {"text": t}); text += t; count += 1
             ("usage", u)    -> keep u
         A GatewayError while reading -> yield the same error event and return.
      5. tokens = usage["completion_tokens"] if usage else count;
         tps = round(tokens / (clock() - t0), 1) if content came and clock() > t0 else None
         msg_id = db.add_message(chat["id"], "assistant", text, tokens=tokens, tps=tps)
         if usage: db.update_chat_usage(chat["id"], usage["prompt_tokens"], usage["completion_tokens"])
      6. (S11) if chat["title"] == "New chat": the first user message of the chat ->
             db.update_chat_title(chat["id"], chat["username"], make_title(it))
      7. (S11) chat = db.get_chat(...) again; yield sse("done", {"message_id": msg_id, "tokens": tokens,
             "tps": tps, "context": context.info(db, chat, settings)})
         (S10 already yields a done event with the same keys; S11 makes "context" real.
          Until then use {"used": 0, "max": 0, "compress_at": 0}.)
    """
    raise NotImplementedError
    yield  # pragma: no cover


def _unused() -> None:
    json.dumps({})
