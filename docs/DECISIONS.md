# Decisions

The choices that shape this project, why they were made, and what would make us revisit them. Newest
first within each group. Settled decisions are also summarised for coding agents in `AGENTS.md`.

## Model and hardware

| Decision | Why | Revisit when |
|---|---|---|
| **Qwen3.6-35B-A3B, `UD-Q4_K_XL` GGUF with MTP**, served as alias `coder` (2026-10) | A mixture-of-experts model with ~3B active parameters per token: fast enough on a 12 GB GPU with the experts partly in system RAM. Agentic-coding tuned, Apache 2.0. Clients only know `coder`, so the model can be swapped without touching them. | A bigger GPU (24 GB+): a dense 27B model becomes the first choice |
| **llama.cpp `llama-server` in WSL2** on the gaming PC (RTX 3080 Ti 12 GB, 32 GB DDR4) | The GPU is there already; llama.cpp gives fine control over expert offload (`--n-cpu-moe 26`), q8 KV cache, flash attention and MTP speculative decoding. ~40 tok/s generation, ~790 tok/s prompt reading. | — |
| **48k context, one slot (`-np 1`)** | Fits VRAM (11.9 of 12.3 GB). A second slot or more context would spill to shared memory, which is ~10× slower. | More VRAM |
| **The PC sleeps between jobs (S3); waking by magic packet** from a host-network waker pod; keep-awake through a `/run/ai/busy` flag | The PC is a home machine: it should not run all night for nothing. No passwordless login on Windows. | — |
| Not Ollama | Less control over offload and speculative decoding; the earlier Ollama setup was removed. | — |

## Agent and harness

| Decision | Why | Revisit when |
|---|---|---|
| **The agent runs in a devbox pod on k3s, never on the PC**; tests run in the pod | The PC is only a model server. The pod is always on, reproducible (one image), isolated (own namespace, no service-account token, egress policy) and reachable from a phone. | — |
| **OpenCode as the agent CLI**, Pi as fallback; not Aider | OpenCode handles tool calls and long sessions well with Qwen; Aider's edit format fits local models less well. | — |
| **Ralph-style loop: one task per fresh session**; the harness decides "done" by running the task's accept command | Small contexts (48k) and a local model: a fresh session per task keeps it focused; an objective test beats the agent's own judgement. Retries with the failure output, max 3, then skip. | — |
| **Protected paths** (`.agent-protected`): tests, specs, plans, the look, security-sensitive code | The agent must make the code pass the tests, never the other way round. | — |
| **Split of work**: the local agent writes most code in loops; Claude writes specs, judge tests, the look, prompts and security-sensitive parts, and reviews every run | Saves paid tokens; keeps quality and safety where they matter. Each loop is a `PLAN.md` of small tasks (one or two functions, steps in the docstring, one test each). | — |
| **Access through Tailscale SSH** inside the devbox (userspace networking) | No open ports; works from the phone. | — |
| **Harness PRs for unfinished runs are drafts** ("Unfinished, don't merge yet") (2026-11-19) | A stopped run once opened a normal PR that was merged with stubs, turning main red. | — |
| Kubernetes manifests live in the homelab repo (`apps/ai-agent/`), deployed by Flux | One GitOps source for the cluster; the repo the agent works on has no power over cluster objects. | — |
| Secrets in OpenBao, delivered by External Secrets; placeholders are `REPLACE_ME` | Nothing secret in git. | — |

## Status service

| Decision | Why |
|---|---|
| **One stdlib-only Python file** (`status/app.py`) | Minimal image, nothing to patch, easy to read. |
| **Tailscale only** | It controls the queue and the model; it never needs to be public. |
| **The model gateway (`/v1`) lives here**, with lanes: interactive (chat) before batch (agents), and a lease so one agent run uses the GPU at a time | One place that knows who uses the GPU; chat stays responsive. Prompts are never stored. |

## Dewdrop (the chat app)

| Decision | Why | Revisit when |
|---|---|---|
| **Its own app** (`chat/`, FastAPI + Jinja + httpx, SQLite), in this repo, **public** at its own subdomain via Cloudflare Tunnel | The status page stays private; the chat is for family and a few friends. One repo keeps specs, tests and agent loops together. | — |
| **Invite-only accounts**: argon2 passwords, rate-limited login, passkeys, rights per user (`chat`, `evidence`, `admin`) | Public on the internet, but only for invited people. | — |
| **Server-rendered pages + small ES modules, no framework** | Fast on phones, no build step, easy for the agent to work on. | — |
| **The look is human-owned** (`chat/static/css/`, `stream.js`) behind a markup contract (SPEC §9) | Design is iterated in rounds (`docs/design/`); the agent never changes the look by accident. | — |
| Name **Dewdrop**, the dewdrop orb as logo; Aero / Liquid Glass look with a pink **Blossom** theme | Chosen in design rounds 1–3. | — |
| **Web search through a self-hosted SearXNG** | No API keys, no tracking; results stay in the homelab. | — |
| **Page fetching only to public addresses** (`tools.fetch_page`: no LAN, no cluster, size and time limits) | The model must not be able to reach internal services (SSRF). | — |
| **Bot-check pages are refused, never bypassed** | We respect sites that block automated readers (NHG, GGZ). | — |

## Clear Source (the research profile)

| Decision | Why | Revisit when |
|---|---|---|
| **Name Clear Source** (was EvidenceHunt until 2026-11-19; id stays `evidence`) | EvidenceHunt is an existing product. | — |
| **The app runs the research, the model only plans, reads and writes** | A model alone mixes sources with memory; making the app do the searching and checking makes strictness structural. | — |
| **Passages must be verbatim** (`verify.find_quote`), and the answer is written only from them; a checker marks every quote ✓ / ⚠ and flags uncited claims | Trust: every claim traceable to an exact sentence in a source. | — |
| **Read one source at a time** | Small, focused calls work best with a 48k model and keep passages exact. Cost: ~10 calls per answer (slow when the GPU is busy). | Batched reading if speed matters more |
| **Dutch guideline sites are the default sources** (Richtlijnendatabase, Farmacotherapeutisch Kompas, Kinderformularium, RIVM, Lareb, Geneesmiddelenbulletin, Thuisarts) plus PubMed | The main user works in Dutch healthcare. | — |
| **MeSH headings, checked against PubMed's MeSH database** before use | Better PubMed recall; made-up headings are dropped. | — |
| **Always detailed answers** under fixed headings | Requested by the main user. | — |
| NCBI calls spaced out (~3/s) and retried on 429; optional API key | PubMed's rate limit otherwise blocks most searches. | — |

## Frontend look (summary; full history in `docs/design/`)

Status page: glass cards + Aero buttons ("mix"). Dewdrop: open-page layout, frosted-aqua user bubbles, the dewdrop
orb with a phase per animation (clouds = thinking, radar = searching, lines = reading, water filling = writing),
steady writing with a soft fade, Blossom with rose-quartz drops, Clear Source in turquoise (deep rose in Blossom).
