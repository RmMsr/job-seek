# Task stop action

Give a running or queued task a user-triggered "Stop", landing it in a new
terminal `cancelled` status. Surfaced on the task detail page only.

## Problem

The async task engine has no way to abort work. A `fetch_all` that fans out
to a dozen slow sources, a bulk re-evaluate over the wrong selection, a stuck
fetch — all you can do is wait it out or restart the server (which fails every
running task as "interrupted by restart"). The only existing per-task action is
`dismiss`, and that only applies to `needs_action`.

## 1. `cancelled` status + cancellation mechanism

New terminal status `cancelled`, added to the `tasks.status` CHECK constraint
via a table-rebuild migration `_migrate_tasks_add_cancelled` (same shape as
`_migrate_tasks_group_to_parent`: `PRAGMA foreign_keys=OFF`, create
`tasks_new` with the widened constraint, copy every column, drop, rename,
`PRAGMA foreign_keys=ON`). `tasks` has no FTS triggers, so no rebuild fixup.
Guard: return early if `"'cancelled'"` already in the stored `CREATE TABLE`
SQL. Append the call to `init_db` after `_migrate_tasks_group_to_parent`.

Full status set becomes:
`queued`, `running`, `needs_action`, `done`, `failed`, `dismissed`, `cancelled`.

**Queued tasks** (including queued children): cancelled purely in SQL —
`UPDATE tasks SET status='cancelled', finished_at=datetime('now')
WHERE id IN (...) AND status='queued'`. `claim_next_task` only selects
`queued`, so the worker never picks them up. No signalling.

**The running task**: cooperative cancellation.

- `task_engine` gets a module-level `set[int]` + `threading.Lock`, with
  `request_cancel(task_id)`, `cancel_requested(task_id) -> bool`, and
  `_clear_cancel(task_id)`.
- `execute_task`'s `while True: line = next(gen)` loop checks
  `cancel_requested(task["id"])` after appending each yielded line; if set it
  raises a private `_TaskCancelled`.
- `_TaskCancelled` is caught (a branch before the generic `except Exception`)
  and finalized with a new `q.cancel_task(conn, task_id)` — sets
  `status='cancelled'`, `finished_at=datetime('now')`, leaves `log` (the
  partial progress) and `error` (NULL) as-is.
- The existing `finally` block also calls `_clear_cancel(task["id"])` so a
  request that arrived a beat too late (task already finishing) doesn't sit in
  the set forever. Task ids are monotonic, so this is hygiene, not correctness.

**Known limitation** (state it in the plan, not worth guarding): cancellation
only lands at the next `yield`. A task blocked inside one long step — an LLM
call, a Playwright navigation — keeps running until that step yields a line.
The fetch generators yield progress lines frequently; the LLM-heavy kinds
(`*_reevaluate`, `job_reset`) yield per job. On a server restart an unlanded
in-memory request is moot: `recover_interrupted_tasks` already fails running
tasks.

## 2. Subtree semantics

`POST /tasks/{task_id}/stop` resolves the root (`parent_task_id or id`) and
acts on the whole subtree (`root` + `get_task_children(root_id)`):

- every `queued` task in the subtree → `cancelled` (one SQL UPDATE over the id
  list)
- every `running` task in the subtree → `request_cancel(id)` (the worker
  finalizes it to `cancelled` at its next yield)
- `needs_action` and already-terminal tasks (`done` / `failed` / `dismissed` /
  `cancelled`) → left untouched
- a `fetch_all` root that's already `done` (it completes immediately after
  fan-out) stays `done`; the run's displayed state comes from `_subtree_status`

New query `q.cancel_queued_in_subtree(conn, ids: list[int]) -> None` for the
bulk UPDATE, plus `q.cancel_task` from §1 for the single-task finalize. The
route reads the subtree, partitions by status, calls both.

`_subtree_status` (in `routes/tasks.py`) gains one rule. New order:

1. any `queued` / `running` → `running`
2. else any `needs_action` → `needs_action`
3. else latest-finished task is `failed` → `failed` (unchanged — a real error
   in a later step still wins over an earlier cancel)
4. **else any subtree task is `cancelled` → `cancelled`**
5. else → `done`

## 3. Route

`POST /tasks/{task_id}/stop` in `routes/tasks.py`:

- 404 if `get_task(task_id)` is None.
- Resolve root, load subtree, apply §2.
- If nothing in the subtree was stoppable (all `needs_action` / terminal),
  it's a no-op — no error, same as `task_dismiss` when the task isn't
  `needs_action`.
- Redirect to `referer` when same-origin, else `/` — copy the exact pattern
  from `task_dismiss` (303).

## 4. Presentation helpers (`routes/tasks.py`)

- `_next_step`: add `if status == "cancelled": return "Cancelled"` (before the
  `_done_summary` fallthrough).
- `_subtree_status`: the §2 rule.
- `root_presentation` / `_root_summary`: `cancelled` flows through the existing
  `status == "failed"` / else branches fine — the `else` "done chain" branch
  produces a `_done_summary`; guard it so a `cancelled` subtree shows
  "Cancelled" via `_next_step`, matching the non-group path.

## 5. UI — detail page only (`templates/tasks/detail.html`)

- A **Stop** button in a new actions block placed right after the
  `<dl class="task-meta">`. Shown when `task.status in ("queued","running")`
  (non-group) or `pres.status in ("queued","running")` — in practice
  `pres.status == "running"` (group). POSTs to `/tasks/{{ task.id }}/stop`
  (explicit `task.id` — the root/current task, not `panel_task_id`).
  `class="btn"`, no `btn-primary`.
- Poll JS: add `"cancelled"` to the non-group terminal-status list
  `["done","failed","needs_action","dismissed"]` so the page reloads when the
  worker lands the cancel. The group branch already reloads every 3s.
- `templates/tasks/_resolved_panel.html`: add a `cancelled` branch →
  `<p>⊘ Cancelled <span class="task-age">{{ task.finished_at | age }}</span></p>`,
  parallel to the `dismissed` branch. `detail.html`'s `resolved_panel` gate
  (`task.status in ("done","dismissed")`) must also include `"cancelled"`.

## 6. Small shared bits

- `template_env.py`: `_STATUS_ICONS["cancelled"] = "⊘"`.
- `base.html` style block: `.task-li-icon.icon-cancelled { color: var(--text-dim); }`
  (or the nearest existing muted token — check what `dismissed` rows use;
  `dismissed` has no colour rule, so a muted one is fine for both — scope it
  `.icon-cancelled, .icon-dismissed`).
- `templates/tasks/list.html`: add `"cancelled"` to the filter-bar tab list
  `["all", "active", "done", "failed"]`. `get_recent_terminal_tasks` already
  does exact-match on any status, so no query change.
- Status-bar (`base.html` ~line 954+) and home task cards: out of scope (§UI
  is detail-page only). `cancelled` tasks drop out of `get_active_tasks`
  immediately, so the live status bar just stops showing them — acceptable.

## 7. Tests

**`test_task_engine.py`**

- queued task → `request_cancel` is not the path; route-level, covered below.
- `cancel_requested` / `request_cancel` / `_clear_cancel` round-trip.
- running task: drive `execute_task` with a fake kind whose generator yields N
  lines; call `request_cancel` after the first; assert it stops early, status
  `cancelled`, partial log has the early lines only, `error` is NULL.
- `_clear_cancel` runs even when the task completes normally (no leak).
- migration: an old-shape `tasks` table (no `cancelled` in CHECK) →
  `init_db` → constraint accepts `cancelled`; existing rows preserved.

**`test_routes_tasks.py`**

- `POST /tasks/{id}/stop` on a `queued` task → 303, status `cancelled`,
  worker (via `claim_next_task`) skips it.
- stop on a `fetch_all` root with queued + one running child → queued children
  `cancelled`, `request_cancel` called for the running child, root stays
  `done`, `_subtree_status` → `cancelled` once the child lands.
- stop on an already-`done` / `failed` / `needs_action` task → 303, no state
  change, no 500.
- stop on a missing task id → 404.
- `_subtree_status`: `[done root, cancelled child]` → `cancelled`;
  `[done root, cancelled child, failed later child]` → `failed`.
- detail page renders the Stop button for `queued` / `running` and not for
  terminal states; `_resolved_panel` shows "⊘ Cancelled" for a `cancelled`
  task.

Full suite green.
