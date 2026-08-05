---
name: run-dev-server
description: Launch job-seek's FastAPI dev server for manual testing, against a throwaway copy of job-seek.db so real job data and LLM-driven reprocessing are never touched.
---

# Run job-seek's dev server

## When to use

Whenever a change benefits from being exercised by hand (not just the automated
test suite) — trying a new route, clicking through a template change, watching
a streaming progress endpoint end-to-end against the real LLM. Per `CLAUDE.md`,
this always happens from inside a git worktree, never on the main checkout.

## Why a DB copy

`job-seek.db` holds real job-search data, and plenty of app actions mutate it
via LLM calls (accept/reject, reset & reprocess, re-evaluate). A manual test
session that runs those actions against the live DB will corrupt real state —
duplicate LLM calls, overwritten summaries/scores, feedback notes on jobs the
user never actually decided on. Always test against a copy.

## Recipe

Run from inside the worktree (this is cheap and safe specifically *because*
a worktree is its own directory — copying files into it can't touch the main
checkout's files of the same name).

```bash
# 1. Find the main checkout (the worktree's "commondir" points at its .git)
MAIN_ROOT=$(git -C "$(git rev-parse --git-common-dir)/.." rev-parse --show-toplevel)

# 2. Bring in the gitignored files a fresh worktree doesn't have.
#    config.toml as-is; job-seek.db as a point-in-time snapshot — this copy
#    is what the dev server will read and write, the real file is untouched.
cp "$MAIN_ROOT/config.toml" .
cp "$MAIN_ROOT/job-seek.db" ./job-seek.db

# 3. Run the server. --reload picks up both route and template changes
#    without a manual restart (route/Python changes need it; Jinja templates
#    are read from disk per-request either way).
uv run uvicorn app.main:app --reload --port 8931 > /tmp/job-seek-dev.log 2>&1 &
disown
sleep 2
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8931/jobs
```

Report the URL (`http://127.0.0.1:8931/jobs` or whichever page is relevant)
to the user. If 8931 is taken, pick another port.

## Cleanup

```bash
pkill -f "uvicorn app.main:app --port 8931"
```

No need to remove the DB/config copies — they're gitignored and get discarded
along with the rest of the worktree when the change is finished (see
"Finishing a change" in `CLAUDE.md`).
