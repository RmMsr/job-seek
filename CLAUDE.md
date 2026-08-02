# Job Seek — Agent Instructions

## Commit frequently

Commit after each completed implementation increment (e.g. each task in a plan going green), not in one large batch at the end of a session. Small, incremental commits are preferred over large diffs.

## Default feature workflow

For non-trivial feature work, default to this pipeline unless told otherwise:

1. **Brainstorm** (`superpowers:brainstorming`) in a dedicated git worktree — explore the codebase, ask clarifying questions one at a time, present the design in sections, get approval.
2. **Write the spec** to `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md`, commit it.
3. **Write the plan** (`superpowers:writing-plans`) to `docs/superpowers/plans/YYYY-MM-DD-<feature>.md`, commit it.
4. **Implement via a single background subagent** that works through the plan task-by-task (TDD, commit after each task per "Commit frequently" above) inside the same worktree, and reports back only once — when the whole plan is done or it's stuck. If it hits a genuine design question the spec/plan doesn't answer, it should ask directly (e.g. via AskUserQuestion) rather than guessing.

This is the default; skip steps only when the user explicitly asks for something lighter-weight.

## Database migrations

This is a personal app running on only one or a very few instances, all kept up to date with development. Backwards compatibility and uninterrupted operation are **not** priorities.

Prefer a simple hard-downtime migration (drop/rebuild, accept a brief outage, edit the row data directly) over conditional upgrade paths, dual-schema compatibility shims, or migration chains that branch on "which old shape is this DB in." If a schema change needs data carried forward, write the one migration that assumes the current known shape — don't build in defensive handling for hypothetical older shapes that don't exist in practice.
