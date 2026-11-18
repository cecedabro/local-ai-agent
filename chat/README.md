# Coder (the chat app)

The public-facing chat interface for the local LLM agent. It lives in `chat/`, runs as its own
pod on k3s, and talks to the model only through the status service's gateway (`/v1`).

Stack: FastAPI, Jinja2, httpx, SQLite. Package list is in `chat/requirements.txt`.

Spec: `chat/SPEC.md`. Tests: `make chat-test` (all) or `make chat-test K="<pytest expr>"`;
JS stream tests: `node --test chat/tests/js/stream.test.mjs`.

## Running locally

1. Install the dependencies into a virtualenv:

   ```
   python3 -m venv chat/.venv
   chat/.venv/bin/pip install -r chat/requirements.txt
   ```

2. Run the server (uses `./data` for the SQLite database by default):

   ```
   PYTHONPATH=chat chat/.venv/bin/uvicorn coder.app:create_app \
       --app-dir chat --factory --reload \
       --port 8080
   ```

3. Set the environment variables the app needs. The minimum useful config:

   | Variable | Example | Purpose |
   |---|---|---|
   | `GATEWAY_URL` | `http://localhost:8081/v1` | Status service gateway URL |
   | `GATEWAY_KEY` | `sk-...` | Bearer token for the gateway |
   | `ADMIN_USER` | `admin` | Initial admin username |
   | `ADMIN_PASSWORD` | `...` | Initial admin password |
   | `SECURE_COOKIES` | `0` | Drop the `Secure` flag for local http |

   Example with env vars:

   ```
   GATEWAY_KEY=sk-xxx ADMIN_USER=admin ADMIN_PASSWORD=secret SECURE_COOKIES=0 \
     PYTHONPATH=chat chat/.venv/bin/uvicorn coder.app:create_app \
     --app-dir chat --factory --reload --port 8080
   ```

4. Open `http://localhost:8080` in a browser. Without a session you are redirected to `/login`.
   Create the first account via the admin env vars above, or create an invite and accept it.

5. The chat app runs behind Cloudflare Tunnel in production (public-facing) and behind Tailscale
   for the status page. Locally both are on `localhost`.

## How it fits together

The chat app is one of several services in the local-ai-agent stack:

- **Status service** (`status/`) — exposes the model's state, job queue, and an OpenAI-compatible
  gateway (`/v1`). The chat app sends every completion through this gateway.
- **Chat app** (`chat/`) — the web UI where users interact with the model. Handles authentication,
  chat management, streaming SSE responses, file uploads, and context compression.
- **Agent loop** (`harness/`) — runs autonomous coding tasks on repos, taking leases from the
  status service. Chat requests go before agent work in the gateway queue.

The data flow for a chat message:

1. The browser sends `POST /api/chats/{id}/messages` with the user text.
2. The app stores the user message in SQLite, then streams an SSE response back to the browser.
3. The app forwards the request (system prompt + chat context + new message) to the status
   service's `/v1/chat/completions` endpoint, authenticated with `GATEWAY_KEY`.
4. The status service sends the request to llama-server on the PC; responses stream back as SSE.
5. The app parses the SSE events (reasoning, content, usage), yields them to the browser,
   and stores the completed assistant message when done.
