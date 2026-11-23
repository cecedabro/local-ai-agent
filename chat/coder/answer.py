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

from . import context, files, gateway, profiles, research, tools

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


# Notes (task E12): Clear Source has its own pipeline, and every profile keeps its thinking and trail (SPEC §13.2-13.3).
# Tests: test_step_answer.py, test_step_round2.py, test_step_profiles.py, test_accept_core.py::TestStream,
# test_accept_evidence.py::TestEvidenceV2.
#   1. `from . import research`. In run_answer, right after the optional compress (before build_messages):
#        if chat["profile"] == "evidence":
#            async for chunk in research.run_evidence(db, settings, chat, think, stream=stream, complete=complete, stop=stop):
#                yield chunk
#            return
#   2. Before the rounds: trail = []; thinking_text = ""; th0 = None.
#   3. A ("reasoning", val) event: besides the first-time phase("thinking"), when val is not empty: th0 = th0 or clock();
#      thinking_text += val; yield sse("thinking", {"text": val}).
#   4. After each `result, found = await run_tool(...)`: step = tools.trail_step(name, arguments, found); trail.append(step);
#      yield sse("trail", {"step": step}).
#   5. Both db.add_message calls for the assistant also store trail=trail and thinking=thinking_text; the normal one also
#      thought_s = round((t0 or clock()) - th0, 1) when th0 is not None, else None.
#   6. done also carries "trail": trail.

def make_file(db: Any, username: str, arguments: str) -> tuple[str, dict | None]:
    r"""TODO (task D08). Run the make_file tool (SPEC §16.3): (the tool result for the model, the file for the page or None).
      args = json.loads(arguments or "{}") (not JSON or not a dict -> ("Error: the arguments were not valid JSON.", None))
      data, _, name = files.render(args.get("name") or "file", args.get("format") or "", args.get("content") or "")
        (files.FileError e -> (f"Error: {e}", None))
      fid = db.add_file(username, name, len(data), data, kind=the format (lower case, without "."), text=content, made=True)
      return (f"Made {name} ({len(data)} bytes). The user sees a download card for it; do not repeat its content.",
              {"id": fid, "name": name, "size": len(data), "kind": the format})
    Tests: test_step_files.py::test_make_file
    """
    import json as _json
    try:
        args = _json.loads(arguments or "{}")
        if not isinstance(args, dict):
            raise ValueError
    except (ValueError, TypeError):
        return ("Error: the arguments were not valid JSON.", None)
    name_in = args.get("name") or "file"
    fmt = args.get("format") or ""
    content = args.get("content") or ""
    try:
        data, _, name = files.render(name_in, fmt, content)
    except files.FileError as e:
        return (f"Error: {e}", None)
    kind = fmt.lower().lstrip(".")
    fid = db.add_file(username, name, len(data), data, kind=kind, text=content, made=True)
    return (f"Made {name} ({len(data)} bytes). The user sees a download card for it; do not repeat its content.",
            {"id": fid, "name": name, "size": len(data), "kind": kind})


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

    # Clear Source profile: run its own pipeline
    if chat["profile"] == "evidence":
        # Clear Source has no Quick/Think choice: it always reasons before it writes (one call; searching and reading
        # are the same either way), because a careful answer matters more here than 30 seconds.
        async for chunk in research.run_evidence(db, settings, chat, True, stream=stream, complete=complete, stop=stop):
            yield chunk
        return

    # Step 2
    messages = context.build_messages(db, chat)
    cfg = profiles.config(db, chat["username"], chat["profile"])
    specs = tools.tool_specs(cfg) + [files.TOOL]
    yield phase("thinking")         # the model reads the prompt, then thinks (SPEC §11.3)
    shown = "thinking"              # the phase on screen, so a new round's reasoning doesn't repeat it

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
    trail: list[dict] = []
    thinking_text = ""
    th0 = None
    made: list[dict] = []

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
                except gateway.Busy as e:
                    if e.message:                         # the GPU is lent out: waiting a minute won't help
                        yield sse("error", {"error": e.message, "status": 503})
                        return
                    if not waited:
                        yield phase("waiting")
                        shown = "waiting"
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
            held = ""                  # text that may be a tool call written as text (gateway.tag_split)
            calls_dict: dict[int, dict] = {}
            content_started = False

            async def events():
                if first is not None:
                    yield first
                async for ev in agen:
                    yield ev

            async for tag, val in events():
                if tag == "reasoning":
                    if shown != "thinking":
                        yield phase("thinking")
                        shown = "thinking"
                    if val:
                        th0 = th0 or clock()
                        thinking_text += val
                        yield sse("thinking", {"text": val})
                elif tag == "content":
                    count += 1
                    show, held = gateway.tag_split(held + val)
                    if show:
                        if not content_started:
                            content_started = True
                            yield phase("writing")
                            shown = "writing"
                            t0 = clock()
                        said += show
                        text += show
                        yield sse("delta", {"text": show})
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
            if held:
                written = gateway.text_tool_calls(held)
                if written and not calls:
                    calls = written            # the model wrote its tool calls as text: run them anyway
                elif not written and gateway.TOOL_TAG not in held:
                    said += held               # it was just text after all (e.g. a lone "<")
                    text += held
                    yield sse("delta", {"text": held})

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
                    if name == "make_file":
                        try:
                            fname = str(json.loads(arguments or "{}").get("name") or "a file")[:60]
                        except (ValueError, TypeError, AttributeError):
                            fname = "a file"
                        yield sse("phase", {"phase": "writing", "label": f"Making {fname}…"})
                        shown = "writing"
                        result, made_file = make_file(db, chat["username"], arguments)
                        if made_file:
                            made.append(made_file)
                            yield sse("file", made_file)
                        messages.append({"role": "tool", "tool_call_id": ids[i], "content": result})
                        continue
                    ph, label = tools.describe(name, arguments)
                    yield sse("phase", {"phase": ph, "label": label})
                    shown = ph

                    result, found = await run_tool(name, arguments, cfg, client, settings.SEARXNG_URL)
                    if isinstance(found, dict):
                        found = [found] if found else []
                    if not isinstance(found, list):
                        found = []
                    step = tools.trail_step(name, arguments, found)
                    trail.append(step)
                    yield sse("trail", {"step": step})
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
                    shown = "writing"
                    continue
                continue
            else:
                break

        msg_stored = True
    except gateway.GatewayError as e:
        yield sse("error", {"error": "The model could not answer.", "status": e.status})
        return
    finally:
        if not msg_stored and (text or made):
            mid = db.add_message(chat["id"], "assistant", text, tokens=count, tps=None, stopped=True, sources=sources, trail=trail, thinking=thinking_text)
            if made:
                db.add_message_files(mid, [f["id"] for f in made])
        try:
            await agen.aclose()
        except Exception:
            pass
        await client.aclose()

    # Step 5
    tokens = usage["completion_tokens"] if usage else count
    tps = round(tokens / (clock() - t0), 1) if t0 else None

    if text or made or not stopped:
        msg_id = db.add_message(chat["id"], "assistant", text, tokens=tokens, tps=tps, stopped=stopped, sources=sources, trail=trail, thinking=thinking_text,
                                thought_s=round((t0 or clock()) - th0, 1) if th0 is not None else None)
        msg_stored = True
        if made:
            db.add_message_files(msg_id, [f["id"] for f in made])
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
                        "sources": sources, "trail": trail, "files": made})


def _unused() -> None:
    json.dumps({})
