# Where things stand

A snapshot of the project, updated by hand. Last update: 2026-11-20. For how the parts work see
`docs/ARCHITECTURE.md`, `docs/DEWDROP.md` and `docs/CLEAR-SOURCE.md`.

## Live

Version `chat-v0.1.11` (2026-11-20) includes the Clear Source fixes from the real-model tests: PubMed rate limit,
the second search round, long pages read where it matters, fewer checker false alarms, `[1; 2]` citations.

| Part | State |
|---|---|
| Model | Qwen3.6-35B-A3B (4-bit, MTP) on the PC's RTX 3080 Ti as `coder`; the PC sleeps between jobs and wakes on demand |
| Status page | Tailscale only: queue, run history, model lease, token stats, `/v1` gateway, quick chat |
| Agent loop | `agent-queue` + `agent-loop` in `devbox-0`, OpenCode; one task per fresh session, judged by tests |
| Dewdrop | Public at dewdrop.cedricschippers.dev: invites, passwords + passkeys, chats and folders, General profile with web search and PubMed, Clear Source, Aero / Blossom looks, installable on a phone |
| Clear Source | v3 research flow (plan → search → read one by one → second round → write → check), MeSH, study types, Dutch default sources |

## In progress

| What | Where | Next step |
|---|---|---|
| Loop D: files in and out (SPEC §16): attach PDF / Word / Excel / HTML / text, the model makes files (`make_file`), "Download as" for any answer | branch `agent/files-in-and-out`, agent job #15 | When the job ends: review, browser check, merge after the fixes PR |

## Known issues

- **Model at half speed after the PC woke (2026-11-20 night)**: ~20 instead of ~40 tok/s since llama-server
  restarted on wake. Diagnosed and restarted at 02:23 UTC; see the result in the session notes. If it recurs, a
  speed check after wake that restarts llama-server is planned.

- **Clear Source is slow while an agent loop runs.** Every one of its ~10 model calls queues behind the agent's
  current request (one GPU, one request at a time). Idea: read several sources in one call, or pause the agent
  between tasks while someone uses Clear Source.
- **No "waiting for the model" hint while an agent runs.** The gateway only says "busy" (503) when it can't queue;
  while the agent holds the model, Dewdrop just shows the phase ("Reading your message…") for longer.
- **NHG / GGZ guidelines can't be read** (bot protection). Thuisarts and the Kompas cover part of it.
- **Search quality limits answers** on loosely phrased topics (PubMed's best match can be off-topic).
- The context bar of a new chat shows the previous chat's numbers until the first message.
- Waking the PC can take longer than Dewdrop's 120 s wait (`WAIT_LIMIT`); the first message after a long sleep may
  time out. Planned: a longer wait with a "Waking up the model…" label.

## Next (proposed order)

1. Merge the Clear Source fixes; re-test with real questions (girlfriend's input welcome).
2. Finish loop D (files) after the agent: review, merge.
3. Language switch NL / EN for the whole app (a per-user setting; Clear Source answers in Dutch already).
4. Faster Clear Source under load (batched reading) and a clear "waiting for the GPU" state.
5. Smaller items: model-wake wait, new-chat context bar, rotate the status token, rate-limit by `Cf-Connecting-IP`,
   "log out everywhere", Clear Source logos.

## How the work is split

The local agent does the bulk coding in loops (a `PLAN.md` of small tasks, each with a test it must pass). Claude
(in Claude Code) writes the specs, the tests, the look, prompts and security-sensitive parts, reviews each run and
fixes what the tests didn't catch. Protected paths (`.agent-protected`) keep the agent from changing tests and specs.
