# How Dewdrop works

Dewdrop (`chat/`, live at dewdrop.cedricschippers.dev) is the public chat app on top of the home GPU. It is invite
only, runs in its own pod in the homelab (namespace `dewdrop`), and reaches the model through the status service's
`/v1` gateway. The exact rules are in `chat/SPEC.md`; this page explains how the pieces fit. Clear Source, the
research profile, has its own page: `docs/CLEAR-SOURCE.md`.

## The pieces

| File | What it does |
|---|---|
| `chat/coder/app.py` | FastAPI app: pages, the JSON API, sessions, the security headers (CSP, `X-Coder` CSRF header) |
| `chat/coder/auth.py`, `passkeys.py` | Passwords (argon2, rate-limited login), invites, passkeys (WebAuthn) |
| `chat/coder/db.py` | SQLite on the pod's volume: users, sessions, chats, folders, messages, files, profile settings, sources |
| `chat/coder/answer.py` | One answer: builds the messages, streams from the gateway, runs tools, stores the result |
| `chat/coder/gateway.py` | Talks to `<status>/v1/chat/completions`: streaming, tool calls (also when Qwen writes them as text), busy/retry |
| `chat/coder/context.py` | The context window: counts tokens, summarises older messages at 75 % ("compressing") |
| `chat/coder/profiles.py` | The two profiles (General, Clear Source), their sources and settings, the system prompt |
| `chat/coder/tools.py` | Web search (self-hosted SearXNG), opening pages safely, PubMed, MeSH, PubMed Central |
| `chat/coder/research.py`, `verify.py` | Clear Source's research flow and the quote checker |
| `chat/coder/files.py` | Files in and out (being built by agent loop D, SPEC §16) |
| `chat/static/js/stream.js` | The live answer: steady typing, the dewdrop orb and its phases, the research trail, sources |
| `chat/static/js/app.js` | The chat page: sidebar, folders, sending, stop, files |
| `chat/static/css/` | The look (Aero / Liquid Glass, Blossom theme); human-owned, design history in `docs/design/` |

## A message, step by step

1. The browser posts `POST /api/chats/<id>/messages` with the session cookie and the `X-Coder` header. Attached
   files are uploaded first (`POST /api/files`); their text is added to the message.
2. `answer.run_answer` checks the context: past 75 % of the model's window it first summarises older messages.
3. The profile decides what happens next:
   - **General**: the model gets the system prompt, the chat and the tools that are switched on (web search, open a
     page, the profile's websites, PubMed). It may call tools up to 4 rounds; each call shows as a step in the trail
     and its results become numbered sources `[n]`.
   - **Clear Source**: the app runs the research itself (`research.run_evidence`); the model only plans, reads and
     writes. See `docs/CLEAR-SOURCE.md`.
4. The answer streams back as server-sent events: `phase` (reading, thinking, searching, writing…), `thinking`,
   `trail`, `delta` (text), `verify` (Clear Source's check) and finally `done` (tokens, speed, sources, context).
5. The page writes the text at a steady pace and the orb shows the phase: clouds while thinking, radar while
   searching, lines while reading, water filling while writing.
6. The answer, its sources, trail and check are stored with the message, so a reload shows the same thing.

## The model and the GPU

There is one GPU (RTX 3080 Ti) running Qwen3.6-35B-A3B as `coder`, and it handles one request at a time. The
gateway puts chat in the *interactive* lane, ahead of agent loops, but it cannot interrupt a request that is already
running. So:

- With nothing else running, answers start within seconds (a sleeping PC first needs ~2 minutes to wake and load).
- While an agent loop runs, every chat request waits for the agent's current request to finish (often 10–60 s). A
  General answer makes 1–3 requests, so it feels a bit slow. A Clear Source answer makes ~10 (plan, one read per
  source, maybe a second round, the answer), so it can take several minutes. It is not stuck; it is queueing.

## Accounts and safety

- Invite-only: an admin makes an invite link; the person sets their own password, and can add a passkey.
- Rights per user: `chat`, `evidence` (Clear Source), `admin`.
- Every write needs the `X-Coder` header (CSRF); the CSP allows only the app's own scripts and styles.
- Fetching web pages for the model goes through `tools.fetch_page`: public addresses only (no LAN, no cluster),
  size and time limits, bot-check pages are refused.
- Downloads are always attachments with `nosniff` and a sandboxing CSP.

## Deploying

Merge to main → CI runs `make chat-test` and the browser tests → image `chat-vX.Y.Z` → Flux rolls the `dewdrop`
pod in a few minutes. A merge whose tests fail builds nothing, so the live app stays on the last good version.
