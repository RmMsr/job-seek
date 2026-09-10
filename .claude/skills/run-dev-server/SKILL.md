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

Run from inside the worktree. A worktree is its own directory, so the files
you bring in never touch the main checkout — but they *can* clobber state a
previous session (or a still-running dev server) left in **this** worktree, so
each copy below is guarded.

### 1. Locate the main checkout

```bash
MAIN_ROOT=$(git -C "$(git rev-parse --git-common-dir)/.." rev-parse --show-toplevel)
```

### 2. config.toml — copy only if missing

```bash
[ -f config.toml ] || cp "$MAIN_ROOT/config.toml" .
```

Never overwrite an existing `config.toml`; it may carry local edits (a
different LLM endpoint, a test profile). If it's present, leave it.

### 3. job-seek.db — the fresh-worktree case vs. the existing-state case

The dev server **reads and writes** this file, so it must be a throwaway copy.
Always snapshot it with `sqlite3 .backup`, **never `cp`** — the source may be
open in WAL mode and a plain `cp` of a live WAL database yields a malformed
copy (missing the `-wal` sidecar).

**Run the `sqlite3 .backup` with the Bash sandbox disabled** (`dangerouslyDisableSandbox: true`).
The source DB is in the main checkout, outside the worktree's write-allowlist,
and `.backup` needs to touch its `-wal` sidecar — under the sandbox it fails
with `unable to open database file`. Same for the `rm -f` of stale sidecars
and any `curl` to `127.0.0.1` below.

```bash
# All sqlite3 / rm / curl steps below: run with dangerouslyDisableSandbox: true.
if [ ! -e job-seek.db ]; then
    # Fresh worktree — the common case. Snapshot and proceed, no prompt.
    sqlite3 "$MAIN_ROOT/job-seek.db" ".backup 'job-seek.db'"
else
    # This worktree already has a job-seek.db. It may hold test state from an
    # earlier session, and a dev server may still have it open. DO NOT replace
    # it here. Back it up and STOP:
    sqlite3 job-seek.db ".backup 'job-seek.db.bak.$(date +%s)'" 2>/dev/null \
        || cp job-seek.db "job-seek.db.bak.$(date +%s)"
    echo "job-seek.db already exists in this worktree (backed up)."
    echo "Ask the user whether to replace it before continuing."
fi
```

When `job-seek.db` already exists, **ask the user before replacing it** —
they may be mid-test against that state, or another dev server may be serving
from it. Only once they say yes:

```bash
pkill -f "uvicorn app.main:app.*--port 8931"   # stop any server on this DB
rm -f job-seek.db job-seek.db-wal job-seek.db-shm   # drop stale WAL sidecars too
sqlite3 "$MAIN_ROOT/job-seek.db" ".backup 'job-seek.db'"
```

### 4. Run the server

```bash
# --reload picks up route and template changes without a manual restart.
# Prefer `python -m` over `uv run` — `uv run` fails under the Bash sandbox
# (read-only cache); the dev server needs run_in_background + sandbox disabled.
python -m uvicorn app.main:app --reload --port 8931 > /tmp/job-seek-dev.log 2>&1 &
disown
sleep 2
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8931/jobs
```

Report the URL (`http://127.0.0.1:8931/jobs` or whichever page is relevant)
to the user. If 8931 is taken, pick another port.

## Cleanup

```bash
pkill -f "uvicorn app.main:app.*--port 8931"
```

No need to remove the DB/config copies or any `job-seek.db.bak.*` — they're
gitignored and get discarded along with the rest of the worktree when the
change is finished (see "Finishing a change" in `CLAUDE.md`). Do stop the
server when manual testing is done, so it isn't left holding the DB open for
the next session.
