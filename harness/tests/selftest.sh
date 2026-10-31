#!/usr/bin/env bash
# End-to-end test of loop.py against a throwaway repo with a fake agent.
# Expect: T01 done, T02 skipped (protected path), T03 done on the 2nd attempt.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT

LOG="${WORK}/run.log"
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
PLAN
printf 'check:\n\ttest ! -f broken\n' > Makefile
echo "# PROGRESS" > PROGRESS.md
git add -A && git commit -qm init

AGENT_CMD="python3 ${HERE}/fake_agent.py {prompt}" CHECK_CMD="make -s check" NTFY_URL="" \
  python3 "${HERE}/../loop.py" --no-pc --no-push --retries 2 --max-skips 5 > "${LOG}" 2>&1 || {
  cat "${LOG}"; echo "loop exited non-zero"; exit 1; }

fail() { echo "FAIL: $*"; cat "${LOG}"; cat PLAN.md; exit 1; }
grep -q '^- \[x\] T01:' PLAN.md || fail "T01 not done"
grep -q '^- \[~\] T02:.*protected' PLAN.md || fail "T02 not skipped for protected path"
grep -q '^- \[x\] T03:' PLAN.md || fail "T03 not done"
[[ ! -e tests/test_cheat.py ]] || fail "protected change left behind"
[[ -f REPORT.md ]] || fail "no REPORT.md"
grep -q '| T03 Write the right answer to out.txt | done | 2 |' REPORT.md || fail "T03 should take 2 attempts"
[[ "$(git rev-parse --abbrev-ref HEAD)" == agent/* ]] || fail "not on an agent/ branch"
[[ -z "$(git status --porcelain)" ]] || fail "tree not clean after run"
# Not `git log | grep -q`: grep -q exits on the first match, git log gets SIGPIPE (141) and
# pipefail turns that into a random failure.
[[ "$(git log --oneline)" == *'T01: Create hello.txt'* ]] || fail "T01 commit missing"
echo "selftest passed"
