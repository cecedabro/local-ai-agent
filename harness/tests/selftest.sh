#!/usr/bin/env bash
# End-to-end test of loop.py and watch.py against a throwaway repo, a fake agent
# and a fake gh (CI fails until ci-fixed.txt exists).
# Expect: T01 done, T02 skipped (protected path), T03 done on the 2nd attempt,
# T04 skipped (path from .agent-protected), then CI fails, CI1 fixes it, CI green.
# The run first waits once in the status queue and reports its events there.
# Then agent-watch sees a failure on main and opens a separate fix run.
# Then a run whose push fails must say so loudly.
# Finally agent-queue takes a job from the real status service and runs it.
set -euo pipefail
# CI runners have no git identity; the harness and agent-queue commit in fresh clones.
export GIT_AUTHOR_NAME=selftest GIT_AUTHOR_EMAIL=selftest@example.com
export GIT_COMMITTER_NAME=selftest GIT_COMMITTER_EMAIL=selftest@example.com

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK="$(mktemp -d)"
STATUS_PID=""
APP_PID=""
cleanup() {
  [[ -n "${STATUS_PID}" ]] && kill "${STATUS_PID}" 2>/dev/null
  [[ -n "${APP_PID}" ]] && kill "${APP_PID}" 2>/dev/null
  rm -rf "${WORK}"
}
trap cleanup EXIT

# Fake status service: logs events, refuses the first lease so the loop has to queue.
EVENTS="${WORK}/events.jsonl"
python3 "${HERE}/fake_status.py" "${EVENTS}" 1 > "${WORK}/status.port" &
STATUS_PID=$!
for _ in $(seq 50); do [[ -s "${WORK}/status.port" ]] && break; sleep 0.1; done
[[ -s "${WORK}/status.port" ]] || { echo "fake status service did not start"; exit 1; }
STATUS_URL="http://127.0.0.1:$(cat "${WORK}/status.port")"
export STATUS_URL STATUS_TOKEN=selftest

LOG="${WORK}/run.log"
git init -q --bare "${WORK}/remote.git"
mkdir "${WORK}/repo" && cd "${WORK}/repo"
git init -q -b main
git config user.name test; git config user.email test@example.com
cat > PLAN.md <<'PLAN'
# PLAN
- [ ] T01: Create hello.txt
  accept: test -f hello.txt
- [ ] T02: Make the tests pass by any means
  accept: true
- [ ] T03: Write the right answer to out.txt
  accept: grep -q right out.txt
- [ ] T04: Rewrite the CI check
  accept: true
PLAN
printf 'check:\n\ttest ! -f broken\n' > Makefile
printf '# human-owned\nci/   # CI scripts\n' > .agent-protected
echo "# PROGRESS" > PROGRESS.md
git add -A && git commit -qm init
git remote add origin "${WORK}/remote.git"
git push -q origin main

export AGENT_CMD="python3 ${HERE}/fake_agent.py {prompt}" CHECK_CMD="make -s check" NTFY_URL=""
export PATH="${HERE}/fakebin:${PATH}"
CI_FAST=(--ci-poll 0 --ci-appear 0 --queue-poll 0)

python3 "${HERE}/../loop.py" --no-pc --retries 2 --max-skips 5 "${CI_FAST[@]}" > "${LOG}" 2>&1 || {
  cat "${LOG}"; echo "loop exited non-zero"; exit 1; }

fail() { echo "FAIL: $*"; cat "${LOG}"; cat PLAN.md; cat REPORT.md 2>/dev/null || true; exit 1; }
grep -q '^- \[x\] T01:' PLAN.md || fail "T01 not done"
grep -q '^- \[~\] T02:.*protected' PLAN.md || fail "T02 not skipped for protected path"
grep -q '^- \[x\] T03:' PLAN.md || fail "T03 not done"
grep -q '^- \[~\] T04:.*protected paths: ci/check.sh' PLAN.md || fail "T04 not skipped for .agent-protected path"
[[ ! -e tests/test_cheat.py && ! -e ci/check.sh ]] || fail "protected change left behind"
[[ -f REPORT.md ]] || fail "no REPORT.md"
grep -q '| T03 Write the right answer to out.txt | done | 2 |' REPORT.md || fail "T03 should take 2 attempts"
grep -q 'CI round 1: failed (ci)' REPORT.md || fail "CI failure not reported"
grep -q '| CI1 .* | done | 1 |' REPORT.md || fail "CI1 fix task not done"
grep -q 'CI green on' REPORT.md || fail "CI not green after the fix"
[[ -f ci-fixed.txt ]] || fail "CI fix not in the tree"
branch="$(git rev-parse --abbrev-ref HEAD)"
[[ "$branch" == agent/* ]] || fail "not on an agent/ branch"
[[ "$(git rev-parse HEAD)" == "$(git --git-dir="${WORK}/remote.git" rev-parse "$branch")" ]] || fail "branch not pushed"
[[ -z "$(git status --porcelain)" ]] || fail "tree not clean after run"
[[ ! -e .agent/lock ]] || fail "lock left behind"
# Not `git log | grep -q`: grep -q exits on the first match, git log gets SIGPIPE (141) and
# pipefail turns that into a random failure.
[[ "$(git log --oneline)" == *'T01: Create hello.txt'* ]] || fail "T01 commit missing"
[[ "$(git log --oneline)" == *'CI1: CI failed on this branch'* ]] || fail "CI1 commit missing"

# Status service: queued once, then the run's events, then the lease released.
grep -q 'queued (position 1) behind other-repo' "${LOG}" || fail "loop did not wait in the queue"
ev() { grep -c "$1" "${EVENTS}" || true; }
[[ "$(ev '"action": "acquire"')" -ge 2 ]] || fail "lease not acquired after queueing"
for kind in run_started task_started task_done task_skipped ci_waiting run_finished; do
  [[ "$(ev "\"kind\": \"${kind}\"")" -ge 1 ]] || fail "no ${kind} event"
done
grep -q '"action": "release"' "${EVENTS}" || fail "lease not released"
grep -q '"_auth": "Bearer selftest"' "${EVENTS}" || fail "status token not sent"
grep '"kind": "run_finished"' "${EVENTS}" | grep -q '"pushed": true' || fail "run_finished should say pushed"

# agent-watch: main itself has no ci-fixed.txt, so its CI "fails".
LOG="${WORK}/watch.log"
git checkout -q main
python3 "${HERE}/../watch.py" --once -- --no-pc "${CI_FAST[@]}" > "${LOG}" 2>&1 || fail "watch exited non-zero"
grep -q 'failed (run 1); fixing' "${LOG}" || fail "watch did not start a fix run"
git --git-dir="${WORK}/remote.git" rev-parse -q --verify agent/ci-fix-1 >/dev/null || fail "fix branch not pushed"
[[ "$(git log --oneline agent/ci-fix-1)" == *'CI1: CI failed on this branch'* ]] || fail "fix commit missing"
grep -q "$(git rev-parse origin/main)" .agent/watch-handled || fail "failing main commit not recorded"
python3 "${HERE}/../watch.py" --once -- --no-pc "${CI_FAST[@]}" > "${LOG}" 2>&1 || fail "second watch exited non-zero"
! grep -q 'fixing' "${LOG}" || fail "watch fixed the same commit twice"
# A push that fails must be loud: ntfy, the status service and REPORT.md all say so.
LOG="${WORK}/push.log"
mkdir "${WORK}/repo2" && cd "${WORK}/repo2"
git init -q -b main
git config user.name test; git config user.email test@example.com
printf '# PLAN\n- [ ] T01: Create hello.txt\n  accept: test -f hello.txt\n' > PLAN.md
printf 'check:\n\ttrue\n' > Makefile
echo "# PROGRESS" > PROGRESS.md
git add -A && git commit -qm init
git remote add origin "${WORK}/missing.git"
python3 "${HERE}/../loop.py" --no-pc --no-ci --no-pr --queue-poll 0 > "${LOG}" 2>&1 || fail "push-failure run exited non-zero"
grep -q '^- \[x\] T01:' PLAN.md || fail "T01 not done in the push-failure run"
grep -q '\[notify\] Push failed' "${LOG}" || fail "push failure not notified"
grep -q '^## Push failed' REPORT.md || fail "push failure not in REPORT.md"
grep -q '"kind": "push_failed"' "${EVENTS}" || fail "push failure not sent to the status service"
grep '"kind": "run_finished"' "${EVENTS}" | tail -1 | grep -q '"pushed": false' || fail "run_finished should say not pushed"
[[ "$(grep -c '\[notify\] Push failed' "${LOG}")" -eq 1 ]] || fail "push failure notified more than once"
cd "${WORK}/repo"

# agent-queue: a job queued on the real status service is cloned, run and reported.
LOG="${WORK}/queue.log"
APP_PORT="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')"
export APP_PORT
STATUS_PORT="${APP_PORT}" STATUS_DB="${WORK}/status.db" STATUS_TOKEN=qtoken STATUS_QUIET=1 \
  python3 "${HERE}/../../status/app.py" 2> "${WORK}/app.log" &
APP_PID=$!
api() {  # api METHOD PATH [JSON]; retries until the app is up
  python3 "${HERE}/api.py" "$@"
}
mkdir -p "${WORK}/gh/local" "${WORK}/qsrc" && cd "${WORK}/qsrc"
git init -q -b main
git config user.name test; git config user.email test@example.com
printf '# PLAN\n- [ ] T01: Create hello.txt\n  accept: test -f hello.txt\n' > PLAN.md
printf 'check:\n\ttrue\n' > Makefile
echo "# PROGRESS" > PROGRESS.md
git add -A && git commit -qm init
git clone -q --bare . "${WORK}/gh/local/proj.git"
api POST /api/jobs '{"repo": "local/proj", "ci": false, "note": "selftest"}' > /dev/null || fail "could not queue a job"
STATUS_URL="http://127.0.0.1:${APP_PORT}" STATUS_TOKEN=qtoken QUEUE_OWNERS=local QUEUE_GIT_BASE="${WORK}/gh/" \
  AGENT_LOOP_CMD="python3 ${HERE}/../loop.py --no-pc --no-pr --queue-poll 0" \
  python3 "${HERE}/../worker.py" --once --repos "${WORK}/qrepos" --heartbeat 1 > "${LOG}" 2>&1 \
  || fail "agent-queue exited non-zero"
api GET /api/status > "${WORK}/state.json"
python3 - "${WORK}/state.json" <<'PY' || fail "queue job not reported as done: $(cat "${LOG}")"
import json, sys
st = json.load(open(sys.argv[1]))
recent = st["jobs"]["recent"]
assert recent and recent[0]["state"] == "done", recent
assert st["runs"][0]["job_id"] == str(recent[0]["id"]), st["runs"]
assert st["totals"]["commits"] == 1, st["totals"]
done = [e for e in st["events"] if e["kind"] == "task_done"]
assert done and len(done[0]["commit"]) >= 7 and done[0]["insertions"] >= 1, done
PY
[[ -n "$(git --git-dir="${WORK}/gh/local/proj.git" for-each-ref refs/heads/agent/)" ]] \
  || fail "queue run did not push its branch"
cd "${WORK}/repo"

# sh(): a timeout kills the whole process group, not only the shell (left-over test
# runs used to hold locks and hang later runs).
LOG="${WORK}/sh.log"
MARK="selftest-orphan-$$"
MARK="${MARK}" python3 - "${HERE}/../loop.py" > "${LOG}" 2>&1 <<'PY' || fail "sh() timeout check failed"
import importlib.util, pathlib, subprocess, sys
spec = importlib.util.spec_from_file_location("loop", sys.argv[1])
loop = importlib.util.module_from_spec(spec); sys.modules["loop"] = loop; spec.loader.exec_module(loop)
mark = __import__("os").environ["MARK"]   # not in argv: pgrep -f would match this process
# /bin/sh is dash on Ubuntu (no `exec -a`), so bash renames the sleeps.
res = loop.sh(f"bash -c 'exec -a {mark} sleep 60' & bash -c 'exec -a {mark} sleep 60'",
              cwd=pathlib.Path("."), timeout=1)
assert res.returncode == 124, res
import time
for _ in range(30):                    # SIGKILL is asynchronous: give the kernel a moment
    left = subprocess.run(["pgrep", "-f", mark], capture_output=True, text=True).stdout
    if not left.strip():
        break
    time.sleep(0.1)
assert not left.strip(), f"left behind: {left}"
PY
echo "selftest passed"
