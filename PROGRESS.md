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

## Task R04 — stop event, done (2026-11-18)

Modified `chat/coder/answer.py`:
1. Added `stop: asyncio.Event | None = None` parameter to `run_answer` signature.
2. Added `stopped = False` flag before the event loop.
3. After each event in the loop: check `stop.is_set()` and if so, set `stopped = True` and break.
4. Wrapped the loop in `try/finally` that calls `await agen.aclose()` on exit (serves R04 and R05).
5. Store the message only if `text or not stopped`, passing `stopped=stopped` to `db.add_message`.
6. `done` SSE event now carries `"stopped": stopped` (True when stopped, False on normal completion).

Tests: `make chat-test K="test_step_answer or (test_step_round2 and stop_event)"` — 9 passed. `make check` — 36 passed, OK.

Note: The `try/finally` with `agen.aclose()` is already in place, so R05 (client disconnect handling via try/finally) has a head start — it only needs to add the partial-store logic.

## Task R05 — client disconnect partial store, done (2026-11-18)

Modified `chat/coder/answer.py`:
1. Added `msg_stored = False` flag before the try block (line 135).
2. After `db.add_message(...)` in Step 5, set `msg_stored = True`.
3. In the `finally` block, before `agen.aclose()`, added partial-store logic: if `not msg_stored and text`, store accumulated text with `stopped=True`.
(When Starlette calls `aclose()` on the generator due to client disconnect, the finally block catches it and saves the partial answer.)

Tests: `make chat-test K="test_step_answer or test_step_round2"` — 14 passed. `make check` — 36 passed, OK.

## Task R07 — folder routes, done (2026-11-18)

Modified `chat/coder/app.py`:
1. Fixed the PATCH `/api/folders/{folder_id}` response to return only `{"id", "name", "created"}` instead of the full folder dict (which included `username`). The test `TestFolders.test_crud` expects exactly those three keys.

Tests: `make chat-test K="TestFolders"` — 3 passed. `make check` — 36 passed, OK.

## Task R10 — convHtml and loadChats, done (2026-11-18)

Modified `chat/static/js/app.js`:
1. Implemented `convHtml(c)` — renders one sidebar chat item as `<div class="conv[ on]" data-id="ID" draggable="true"><span>TITLE</span>[<em class="tag">EH</em> for evidence profile]<button class="more" data-chat-menu="ID">⋯</button></div>` with "on" class when `c.id === state.chatId`, title escaped via `esc()`, and profile tag shown for "evidence" chats.
2. Implemented `loadChats()` — fetches `/api/chats` and `/api/folders` in parallel via `Promise.all`, stores them in `state.chats` and `state.folders`, filters by `#search` (case-insensitive title substring), then renders `#convs` with folders first (`<details class="folder">` with COUNT) followed by date groups (`<h4>LABEL</h4>`) for unfiled chats using `groupChats()`.

Tests: `node --check chat/static/js/app.js && make chat-test` — 79 passed. `make check` — 36 passed, OK.

## Task R13 — moveChat, newFolder, openMenu, chatMenu, folderMenu, menuAction, done (2026-11-18)

Modified `chat/static/js/app.js`:
1. Implemented `moveChat(chatId, folderId)` — PATCHes `/api/chats/CHAT` with `{folder_id}` (null clears folder), then reloads sidebar via `loadChats()`.
2. Implemented `newFolder()` — prompts for folder name, trims it, POSTs to `/api/folders`, returns the new folder's id or null on failure.
3. Implemented `openMenu(button, html)` — sets `#cmenu` innerHTML to `html`, positions it via `--x`/`--y` CSS variables based on `getBoundingClientRect()` of the button, and shows it.
4. Implemented `chatMenu(button, chatId)` — builds the chat context menu HTML (Rename, Move to per-folder buttons, "No folder" if applicable, New folder, Delete), opens it, and sets `#cmenu.dataset.chat = chatId`.
5. Implemented `folderMenu(button, folderId)` — builds the folder context menu HTML (Rename, Delete folder), opens it, and sets `#cmenu.dataset.folder = folderId`.
6. Implemented `menuAction(act, btn)` — handles all menu actions: rename (PATCH chat title), move (calls `moveChat`), newfolder (creates folder + moves chat), delete (confirms, DELETEs chat, resets open chat UI), rename-folder, delete-folder.
7. Implemented `setupSidebar()` — wires `#convs` click (menu buttons + conv open), drag-and-drop (dragstart/dragover/drop for moving chats into folders), `#cmenu` click (dispatches `menuAction`), outside-click + Escape to close menu, and `#new-folder` click.

Tests: `node --check chat/static/js/app.js && make chat-test` — 79 passed. `make chat-browser-test` — all 7 tests passed including "round 2: folders: new folder, move a chat with the menu, delete a chat". `make check` — 36 passed, OK.

## Task R11 — doneMeta and drawMessage, done (2026-11-18)

Modified `chat/static/js/app.js`:
1. Implemented `doneMeta(meta, content, tokens, tps, stopped, retryText)` — sets `meta.innerHTML` with TINY orb, `metaText(tokens, tps)` span, Copy and Retry buttons. Copy uses `navigator.clipboard.writeText(content)`. Retry skips if `state.busy`, else puts `retryText()` in `#input` and calls `send()`. If stopped, inserts `<div class="note stopped">Stopped</div>` after meta.
2. Implemented `drawMessage(m)` — handles summary (`.note.summary`), user messages (`.u` with file chips and text via `typedText()` when files exist), and assistant messages (`.ai` with ORB + markdown-rendered content + `meta2` initialized with `doneMeta`). Retry callback gets the last `.u .msg.user` text from the DOM.

Tests: `node --check chat/static/js/app.js && make chat-test` — 79 passed. `make check` — 36 passed, OK.

## Task R12 — setBusy, stopAnswer, send, done (2026-11-18)

Modified `chat/static/js/app.js`:
1. Implemented `setBusy(on)` — sets `state.busy = on`, toggles "stop" class on `#send`, updates title and aria-label to "Stop" while busy, "Send" otherwise.
2. Implemented `stopAnswer()` — if `state.busy` and there is an open chat, calls `setBusy(false)` then POSTs to `/api/chats/{id}/stop`.
3. Implemented `send()` — full message send flow: validates input, creates chat if needed, draws user message and assistant placeholder, streams via `readEvents()` with phase/delta/done/error handling, uses `Typewriter` for smooth text rendering, calls `doneMeta` on completion, handles errors and stopped state.

Tests: `node --check chat/static/js/app.js && make chat-test` — 79 passed. `make check` — 36 passed, OK.

## Task R16 — README.md Round 2 section, done (2026-11-18)

Modified `chat/README.md`:
1. Appended a "## Round 2" section (6 lines) covering: reading phase shimmer, stop button with partial answer persistence, folder organisation (the ⋯ menu + drag-and-drop), coloured code blocks with Copy and Download, and scroll-pinning with "↓ Latest".

Tests: `make chat-test` — 79 passed. `grep -q "## Round 2"` and `grep -q "folders"` confirmed.
