# PLAN: history import, repo dropdown, context size

Read `status/SPEC.md` §8 (the "Repo list" bullet and the page paragraph after it), §11 (`n_ctx`)
and §12 first. The judge is `status/tests/test_accept_history.py` (protected: change the code,
never the test). Python 3.11 standard library only. `status/app.py` stays one file;
`status/backfill.py` is a new, separate script. Keep every existing test passing
(`make check` and `make status-test`).

- [ ] T01: In status/app.py add POST /api/import for the "events" list only (SPEC §12.1): validation with 400 and nothing stored on any bad item, an 8 MiB body limit for this path only (other paths keep theirs), a new SQLite table of imported keys, and the all-time updates by the UTC day of each event's ts (refactor the tally/daily part of add_event into a helper that takes the day and time, so live and imported events share it); imported events are not stored in the events table; answer {"events_added","events_skipped","token_days":0,"token_days_ignored":0}
  accept: python3 -m unittest -q status/tests/test_accept_history.py -k test_events_count_once -k test_daily_uses_event_day -k test_since_keeps_earliest -k test_keys_survive_restart
- [ ] T02: Add the "tokens" list to POST /api/import (SPEC §12.1): a new table keyed by (day, source) that each row replaces; remember the UTC day of the first live token sample in the meta table when sample_metrics() stores its first sample, and ignore import rows on or after that day; add imported tokens to tokens.generated_total, tokens.prompt_total and daily generated, but not to any tok/s, generated_24h or hourly; finish the validation of both lists
  accept: python3 -m unittest -q status/tests/test_accept_history.py -k ImportTest -k ImportWithLiveTokensTest
- [ ] T03: Repo list (SPEC §8): /api/workers/ping and /api/jobs/claim keep a worker's newest non-empty, filtered "repos" list with that worker; /api/status jobs.repos is the sorted union described in the spec; the add-job form on the page uses <select name="repo"> with those options when the list is not empty, else the old <input name="repo">
  accept: python3 -m unittest -q status/tests/test_accept_history.py -k Repos
- [ ] T04: gateway.n_ctx (SPEC §11): fetch <root>/props with the llama key and a 2 s timeout, at most once per probe_interval while unknown, keep the value once known; null when the gateway is off
  accept: python3 -m unittest -q status/tests/test_accept_history.py -k Nctx
- [ ] T05: Create status/backfill.py with git_events(repo_dir, name, gh=None) per SPEC §12.2 (git log --all with --shortstat, author local-agent, task commits, run report commits, attempts from .agent/logs*/ file names, PRs from gh with an injectable gh callable)
  accept: python3 -m unittest -q status/tests/test_accept_history.py -k BackfillGitTest
- [ ] T06: Add opencode_tokens(db_path) to status/backfill.py per SPEC §12.2 (read-only sqlite3 URI, sums per UTC day)
  accept: python3 -m unittest -q status/tests/test_accept_history.py -k BackfillOpencodeTest
- [ ] T07: Add send(url, token, events, tokens) and the command line (argparse: --repos, --opencode-db, --dry-run; STATUS_URL and STATUS_TOKEN from the environment, exit 2 when missing) to status/backfill.py per SPEC §12.2
  accept: python3 -m unittest -q status/tests/test_accept_history.py -k BackfillSendTest
- [ ] T08: Everything together: all acceptance and contract tests pass, and status/README.md gains a short "History import" section (what backfill.py reads, how to run it on the devbox, that running it twice is safe) plus POST /api/import in the endpoints list
  accept: make status-test && grep -q backfill.py status/README.md && grep -q /api/import status/README.md
