"""What the model sees of a chat, how full the context is, and compression (chat/SPEC.md §6).

Fill in the functions marked TODO. Tests: chat/tests/test_step_context.py
`db` is coder.db.DB; `chat` is the dict from db.get_chat(chat_id, username) (keys: id, username,
title, profile, last_prompt_tokens, last_completion_tokens, ...).
"""
from __future__ import annotations

import math
import time
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable

SYSTEM_PROMPTS = {
    "general": ("You are Dewdrop, a helpful assistant running locally on Cédric's PC. "
                "Answer in the user's language. Use Markdown."),
    "evidence": ("You are EvidenceHunt. Answer only from medical evidence and say when the evidence is "
                 "weak or missing. Answer in the user's language. Use Markdown."),
}
SUMMARY_PREFIX = "Summary of the earlier conversation: "
SUMMARISE_PROMPT = ("Summarise this conversation so far in a few short paragraphs, keeping names, numbers, "
                    "decisions and open questions.")
KEEP = 4                      # messages kept verbatim after a compression

_cache: dict[str, tuple[float, int]] = {}    # STATUS_URL -> (time fetched, n_ctx)


def ctx_max(settings: Any, now: Callable[[], float] = time.monotonic) -> int:
    """The model's context size in tokens.

    TODO (task S05).
      - settings.STATUS_URL empty -> settings.CTX_DEFAULT
      - cached value for this STATUS_URL younger than 300 s (by now()) -> return it
      - else httpx.get(STATUS_URL.rstrip("/") + "/api/status", timeout=5).json()["gateway"]["n_ctx"];
        if it is a positive int, cache (now(), value) and return it
      - any exception or bad value -> settings.CTX_DEFAULT (don't cache)
    """
    raise NotImplementedError


def window(settings: Any) -> dict:
    """TODO (task S05). {"max": m, "compress_at": (m * 3) // 4} with m = ctx_max(settings)."""
    raise NotImplementedError


def context_messages(db: Any, chat: dict) -> list[dict]:
    """The chat's history as sent to the model, after the latest summary.

    TODO (task S06). Steps:
      1. msgs = db.get_messages(chat["id"], chat["username"])   (ordered oldest first)
      2. find the index of the LAST message with role "summary" (none -> -1)
      3. out = []; if found: out.append({"role": "system", "content": SUMMARY_PREFIX + that content})
      4. append {"role": m["role"], "content": m["content"]} for every user/assistant message after it
    """
    raise NotImplementedError


def build_messages(db: Any, chat: dict) -> list[dict]:
    """TODO (task S06). [{"role": "system", "content": SYSTEM_PROMPTS[chat["profile"]]}] + context_messages(db, chat)."""
    raise NotImplementedError


def used(db: Any, chat: dict) -> int:
    """Tokens in use.

    TODO (task S07). If chat["last_prompt_tokens"] is not None: last_prompt_tokens +
    (last_completion_tokens or 0). Else math.ceil(total characters of every "content" in
    build_messages(db, chat) / 4).
    """
    raise NotImplementedError


def info(db: Any, chat: dict, settings: Any) -> dict:
    """TODO (task S07). {"used": used(db, chat), "max": ..., "compress_at": ...} (merge window(settings))."""
    raise NotImplementedError


def just_before(iso: str) -> str:
    """TODO (task S08). The ISO-8601 time one microsecond before `iso`, same format.
    datetime.fromisoformat(iso) - timedelta(microseconds=1), then .isoformat()."""
    raise NotImplementedError


async def compress(db: Any, chat: dict, settings: Any,
                   complete: Callable[[Any, list[dict]], Awaitable[str]]) -> bool:
    """Summarise all but the last KEEP messages after the latest summary. True if it did.

    TODO (task S08). Steps:
      1. msgs = db.get_messages(chat["id"], chat["username"]); take the user/assistant messages
         after the last "summary" message (same rule as context_messages)
      2. if len(those) <= KEEP: return False
      3. old, kept = those[:-KEEP], those[-KEEP:]
      4. transcript = "\n\n".join(f"{m['role']}: {m['content']}" for m in old)
         (if there was an earlier summary, put "Earlier summary: <it>\n\n" in front)
      5. text = await complete(settings, [{"role": "system", "content": SUMMARISE_PROMPT},
                                          {"role": "user", "content": transcript}])
      6. db.add_message_at(chat["id"], "summary", text, just_before(kept[0]["created"]))
      7. db.reset_chat_usage(chat["id"])   (so used() estimates again)
      8. return True
    """
    raise NotImplementedError


def _unused() -> None:  # imports you will need
    math.ceil(1.0)
    datetime.now() - timedelta(0)
