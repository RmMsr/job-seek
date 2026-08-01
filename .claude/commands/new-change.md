---
description: Start a new change — create an isolated worktree, then brainstorm and write its design spec
---

## Task

Start a new unit of work, following this project's default feature workflow documented in `CLAUDE.md`.

Topic/feature the user wants to work on (may be empty): $ARGUMENTS

1. Create a new git worktree for this change with `EnterWorktree`. If a topic was given above, derive a short kebab-case name from it to pass as the worktree `name`; otherwise ask the user for one before creating it.
2. Once switched into the new worktree, invoke the `superpowers:brainstorming` skill to explore the idea, ask clarifying questions one at a time, and produce an approved design spec written to `docs/superpowers/specs/`.

Stop once the spec is written, committed, and the user has reviewed it — do not continue on to `superpowers:writing-plans` or any implementation. That is a separate, explicit next step.
