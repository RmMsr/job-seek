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

## Finishing a change

Once tests are green (and manual testing passed, if applicable), squash-merge the worktree branch back into local `main` — there's no remote configured for this repo, so a local squash merge is the whole integration path, not a PR:

```bash
cd <main checkout>
git merge --squash <branch>
git commit
```

Then clean up: remove the worktree and delete the branch (same cleanup `finishing-a-development-branch` does for its "merge locally" option — just use `git merge --squash` there instead of a plain merge).

## Default feature workflow

For non-trivial feature work, default to this pipeline unless told otherwise:

1. **Brainstorm** (`superpowers:brainstorming`) — explore the codebase, ask clarifying questions one at a time, present the design in sections, get approval.
2. **Write the spec** to `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md`, commit it.
3. **Write the plan** (`superpowers:writing-plans`) to `docs/superpowers/plans/YYYY-MM-DD-<feature>.md`, commit it.
4. **Implement via background subagent(s)** working through the plan task-by-task (TDD, commit after each task per "Commit frequently" above) inside the same worktree, reporting back only once — when the whole plan is done or it's stuck. Default to a single agent; only split into parallel agents when the plan has genuinely independent tasks where that would clearly help. If it hits a genuine design question the spec/plan doesn't answer, it should ask directly (e.g. via AskUserQuestion) rather than guessing.

This is the default; skip steps only when the user explicitly asks for something lighter-weight.

## Database migrations

This is a personal app running on only one or a very few instances, all kept up to date with development. Backwards compatibility and uninterrupted operation are **not** priorities.

Prefer a simple hard-downtime migration (drop/rebuild, accept a brief outage, edit the row data directly) over conditional upgrade paths, dual-schema compatibility shims, or migration chains that branch on "which old shape is this DB in." If a schema change needs data carried forward, write the one migration that assumes the current known shape — don't build in defensive handling for hypothetical older shapes that don't exist in practice.
