# The agent loop: how work gets built

Most code in this repo is written by the local model through `agent-loop`, overnight, one small task at a time.
This page explains how a loop is prepared, how it runs, and how its result is reviewed. The mechanics of the
harness are in `docs/ARCHITECTURE.md` (Path 1); the code is `harness/loop.py`, `harness/worker.py` and
`harness/prompt.md`.

## Roles

| Who | Does |
|---|---|
| **You** | Decide what to build, try the result, merge PRs |
| **Claude** (Claude Code) | Writes the spec section, the judge tests, a private reference solution (to prove the tests can be passed), the stubs and the plan; does the look, prompts and security-sensitive code; reviews every run |
| **The local agent** (OpenCode + Qwen) | Fills in the stubs task by task until the tests pass |

## Preparing a loop

1. **Spec**: a new section in `chat/SPEC.md` (or `status/SPEC.md`) with the exact behaviour: routes, messages,
   limits, error texts.
2. **Judge tests**, written before the code: `test_step_*.py` per function, `test_accept_*.py` over HTTP, browser
   checks in `chat/tests/browser/run.mjs`. All are listed in `.agent-protected`.
3. **Reference solution**, kept out of the repo: proves every test can be passed and that the tests don't
   contradict each other.
4. **Stubs**: each function the agent must write keeps its signature and a docstring that starts with
   `TODO (task Dxx).` and lists the steps; the body is `raise NotImplementedError`. Everything human-owned stays in
   place.
5. **`PLAN.md`** on a branch `agent/<plan-title>`: a short intro, rules, and tasks like
   ```
   - [ ] D03: In chat/coder/files.py fill in `runs` and `blocks` (Markdown to runs and blocks)
     accept: make chat-test K="test_step_files and (runs or blocks)"
   ```
   One file and a few functions per task; the accept command selects only that task's tests (and those of earlier
   tasks that must keep passing). Bigger tasks get a "Dxx details" block under the list.
6. Check that only the new tests fail, squash the preparation into one commit (so the reference never appears in
   the history the agent can read), push, and queue the job (status page "Start an agent loop", or `POST
   /api/jobs`).

## How a run goes

`agent-queue` claims the job, checks out the branch and starts `agent-loop`, which takes the model lease, wakes the
PC and works through `PLAN.md`:

- each task gets a **fresh OpenCode session** with only the task line and `harness/prompt.md`;
- after the agent stops, the harness runs the task's `accept:` command and `make check`;
- pass → commit and push; fail → retry with the failure output (max 3 attempts), then mark it skipped;
- an attempt that touches a protected path is rejected;
- every step is reported to the status page;
- at the end it waits for CI, writes `REPORT.md` and opens a PR. **An unfinished run opens a draft PR titled
  "Unfinished, don't merge yet".**

## Reviewing a run

1. Read `REPORT.md` (done / skipped / attempts per task).
2. Run everything: `make lint && make test && make chat-test && make chat-browser-test` and the JS tests.
3. Read the diff of the agent's code, with extra care for anything touching input, files, the network or auth.
4. Fix what the tests didn't catch (small commits on the same branch), merge main in if needed.
5. For features that use the model: try them with the real model (see `docs/CLEAR-SOURCE.md`, "Testing it for
   real").
6. Tell the user it's ready; they merge.

## What makes tasks succeed

- Small: one or two functions, steps spelled out, one test. The model has 48k tokens of context.
- Name the file and the functions in the task title; the harness only passes the title line.
- Tests that show the exact expected value give the agent a clear target.
- Never ask it to read a large file whole (`grep -n`, then `sed -n 'a,bp'`); the rules in `AGENTS.md` say so.
- Keep the look, prompts and security code out of its tasks.

## Loops so far (chat)

| Loop | Branch | Result |
|---|---|---|
| Chat foundation (S01–S26) | `agent/chat-app-foundation` | 23/23 first try |
| Passkeys (P01–P10) | `agent/dewdrop-passkeys` | done |
| Round 2: phases, stop, folders, code (R01–R16) | `agent/dewdrop-round-2` | done |
| Profiles, sources, tools (B01–B19) | `agent/dewdrop-profiles` | 19/19 |
| Clear Source v2 (E01–E19) | `agent/evidencehunt-v2` | 19/19 first try |
| Clear Source rounds 2–3 (F01–F20) | `agent/clear-source-2` | 17/17 (one already done by another task) |
| Files in and out (D01–D13) | `agent/files-in-and-out` | 13/13 (job #15), in review |
