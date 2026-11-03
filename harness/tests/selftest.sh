#!/usr/bin/env bash
# End-to-end test of loop.py and watch.py against a throwaway repo, a fake agent
# and a fake gh (CI fails until ci-fixed.txt exists).
# Expect: T01 done, T02 skipped (protected path), T03 done on the 2nd attempt,
# T04 skipped (path from .agent-protected), then CI fails, CI1 fixes it, CI green.
# Then agent-watch sees a failure on main and opens a separate fix run.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT

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
CI_FAST=(--ci-poll 0 --ci-appear 0)

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
echo "selftest passed"
