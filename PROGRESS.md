## Task R03 — phases, first chunk, done (2026-11-18)

Modified `chat/coder/answer.py`:
1. Added `"reading": "Reading your message…"` to `LABELS` dict.
2. After `messages = context.build_messages(db, chat)`, yields `phase("reading")` once, before the retry loop.
3. Merged the `first` event and the `agen` stream into a single `events()` async generator, so the first content chunk is sent as a delta (fixes the bug where the first chunk was never emitted).
4. Added `thinking_sent` flag so `phase("thinking")` is yielded only once regardless of how many reasoning chunks arrive.
5. Wrapped the unified loop in `try/except gateway.GatewayError` to properly handle errors mid-stream.
6. The `done` SSE event now includes `"title"` (from the re-read chat) and `"stopped": False` (placeholder for R04).

Tests: `make chat-test K="test_step_answer or (test_step_round2 and reading_phase)"` — 9 passed. `make check` — 36 passed, OK.

Next: Task R04 adds `stop: asyncio.Event | None` parameter and per-event stop checking, setting `stopped=True` on the done event. Task R05 wraps the loop in try/finally for client disconnect handling.
