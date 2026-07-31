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
