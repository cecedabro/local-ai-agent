# Documentation

| Read this | To learn |
|---|---|
| [STATUS.md](STATUS.md) | Where things stand: what's live, in progress, known issues, next steps |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Every moving part, how they talk, deploying, security boundaries, troubleshooting |
| [DECISIONS.md](DECISIONS.md) | The choices behind the project and why (model, hardware, agent, apps, Clear Source, look) |
| [DEWDROP.md](DEWDROP.md) | How the chat app works, from a message to an answer; the GPU and why it can be slow |
| [CLEAR-SOURCE.md](CLEAR-SOURCE.md) | The research profile: the flow, why it is strict, limits, tuning |
| [BACKEND.md](BACKEND.md) | Server side of Dewdrop and agent-status: modules, data model, API, the answer stream, config |
| [FRONTEND.md](FRONTEND.md) | Pages, JavaScript modules, the look (tokens, themes, the orb), frontend tests |
| [AGENT-LOOP.md](AGENT-LOOP.md) | How work is built by the local agent: preparing, running and reviewing a loop |
| [PLAN.md](PLAN.md) | The original setup plan (model choice, PC, power, devbox) |
| [design/](design/README.md) | Every design round of the status page and Dewdrop, as shown |
| [dewdrop-talk.html](dewdrop-talk.html) | A presentation: the Dewdrop setup from the hardware up to the prompt (open in a browser; arrows, F, P) |
| [local-ai-setup-talk.html](local-ai-setup-talk.html) | The earlier presentation on the local agent setup and the theory behind it |

Specs (the contracts the tests check) live with the code: `chat/SPEC.md`, `status/SPEC.md`. Rules for coding agents:
`AGENTS.md` and `CLAUDE.md` in the repo root.
