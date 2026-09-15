# Job Seek — Agent Instructions

## Commit frequently

Commit after each completed implementation increment (e.g. each task in a plan going green), not in one large batch at the end of a session. Small, incremental commits are preferred over large diffs.

## Always work in a worktree

Every change — however small — happens in an isolated git worktree, never directly on `main`. Use the `superpowers:using-git-worktrees` skill (native `EnterWorktree`/`ExitWorktree` tools) to set it up. This applies regardless of size: a one-line fix gets a worktree just like a multi-day feature. Only the *process* around the change (brainstorm → spec → plan vs. just diving in) scales with size — see "Default feature workflow" below.

A fresh worktree won't have `config.toml` or `job-seek.db` (both gitignored) — copy `config.toml` from the main checkout before running the app there. See "Manual testing / dev server" for `job-seek.db`.

## Manual testing / dev server

If a change benefits from being exercised by hand rather than just the test suite, run the dev server **against a throwaway copy of `job-seek.db`**, never the live one — manual test actions (accepting/rejecting jobs, resetting/reprocessing, anything that calls the LLM) shouldn't mutate real job data. Use the `run-dev-server` skill for the exact recipe — it copies the DB into the worktree (which is already an isolated directory, so this is cheap and safe) and starts the server with `--reload`.

Stop the dev server once manual testing is done; the throwaway DB copy is gitignored and gets discarded with the worktree at cleanup.

When a change is UI-facing, after implementation leave the dev server running and hand the URL to the user so *they* can try it, rather than only exercising it yourself and moving straight to "finishing a development branch" options. Wait for their go-ahead before offering to merge/clean up.

If you seed sample data into the throwaway DB so the user has something to look at, create it through the real code path (enqueue the real task, run the real fetch) — not hand-written rows straight into SQLite. A malformed fixture row reads as a real bug. If you must insert rows directly, tell the user exactly which ones are seeded.

## Running tests

Run tests with `uv run pytest`, not a bare `python -m pytest` / system interpreter — `uv run` resolves this project's pinned dependency set (matching CI), while the system interpreter's globally installed packages can silently diverge (e.g. missing `httpx2`, causing Starlette's `TestClient` to fall back to a different, broken transport) and mask real failures.

## Finishing a change

Once tests are green (and manual testing passed, if applicable), squash-merge the worktree branch back into local `main`. This repo has `github` and `gitlab` remotes, but the integration path is still a **local** `git merge --squash` into `main` — never a PR, and **do not push** unless the user explicitly asks. Expect local `main` to have moved on since the worktree was created (parallel worktrees land commits); the squash merges onto whatever `main` is at now, so re-run the full test suite on `main` after the merge commit.

If a background subagent drove the implementation and left a dev server running for UI handoff, `TaskStop` that subagent **before** killing the dev server or merging — while it's still resumable it will relaunch the server each time you kill it, and you'll chase it in circles.

The merge has to run from the main checkout, and a worktree-isolated session refuses any git command that `cd`s (or `-C`s) out of its worktree. So leave the worktree *first*, keeping it on disk, then merge:

```bash
# 1. in the worktree: make sure everything is committed
git status

# 2. leave the worktree, keeping it (ExitWorktree action: "keep") —
#    the session's cwd is now the main checkout

# 3. from the main checkout (already the cwd — no cd needed)
git merge --squash <branch>
git commit

# 4. clean up: remove the worktree and delete the branch
git worktree remove <worktree path>
git branch -D <branch>
```

Cleanup mirrors what `finishing-a-development-branch` does for its "merge locally" option — just `git merge --squash` instead of a plain merge. If `git worktree remove` reports "device or resource busy" (the harness keeps config files mounted into a live worktree), run `git worktree prune` to clear git's tracking; the leftover directory is swept when the session exits.

## Default feature workflow

For non-trivial feature work, default to this pipeline unless told otherwise:

1. **Brainstorm** (`superpowers:brainstorming`) — explore the codebase, ask clarifying questions one at a time, present the design in sections, get approval.
2. **Write the spec** to `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md`, commit it.
3. **Write the plan** (`superpowers:writing-plans`) to `docs/superpowers/plans/YYYY-MM-DD-<feature>.md`, commit it.
4. **Implement via background subagent(s)** working through the plan task-by-task (TDD, commit after each task per "Commit frequently" above) inside the same worktree, reporting back only once — when the whole plan is done or it's stuck. Default to a single agent; only split into parallel agents when the plan has genuinely independent tasks where that would clearly help. If it hits a genuine design question the spec/plan doesn't answer, it should ask directly (e.g. via AskUserQuestion) rather than guessing.

For substantially UI-facing work, the implementing subagent should use the `frontend-design` skill from the start — establishing spacing, button hierarchy, alignment and state styling up front is far cheaper than converging on them through many one-tweak review rounds with the user.

This is the default; skip steps only when the user explicitly asks for something lighter-weight.

## Database migrations

This is a personal app running on only one or a very few instances, all kept up to date with development. Backwards compatibility and uninterrupted operation are **not** priorities.

Prefer a simple hard-downtime migration (drop/rebuild, accept a brief outage, edit the row data directly) over conditional upgrade paths, dual-schema compatibility shims, or migration chains that branch on "which old shape is this DB in." If a schema change needs data carried forward, write the one migration that assumes the current known shape — don't build in defensive handling for hypothetical older shapes that don't exist in practice.

The `jobs_fts` FTS5 index is kept in sync by triggers on the `jobs` table. Any
migration that rebuilds `jobs` (drop/recreate) drops those triggers with it —
follow such a migration with `INSERT INTO jobs_fts(jobs_fts) VALUES('rebuild')`
and recreate the triggers (see `_migrate_add_jobs_fts`).
