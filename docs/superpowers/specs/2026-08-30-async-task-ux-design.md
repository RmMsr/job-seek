# Async task UX

## Problem

The async-task system (spec: `2026-08-21-async-task-architecture-design.md`)
works but the way a task's existence and effects reach the user is thin:

- A background task has **no durable, followable representation**. The
  status bar shows the currently-running task ephemerally; once it clears
  there is nowhere to see "what did that fetch do". The front-page "Tasks"
  section is driven by `inbox_items`, which only exist for tasks that end
  needing a decision — a plain fetch that succeeds leaves no trace there.
- **An action that spawns a task barely changes.** The trigger button sets
  an internal `data-progress-running` flag (no visible state) and a toast
  fires. Nothing says "this is pending, wait".
- **Forms that appeared as the result of an action** (needs-action
  confirm/mismatch panels, generated proposal lists) stay fully actionable
  after you have already acted, or re-render as if nothing happened.
- **`/tasks/{id}/log`** is a bare log dump — no summary of what the task
  was for, no links to its results.

What already works and must be preserved: task effects **blend into the
current page** if it is left open (OOB / target swaps); the **status bar**
shows the running task, `[x/y]` progress and queue length.

## Scope

Front-end and task-presentation changes plus a small `tasks` schema
extension. `app/pipeline.py`'s generator functions are unchanged. The
task engine (`app/task_engine.py`) changes only where noted (status on
`needs_action`, passing the running task's id to the kind fn, and the
`__ORIGIN_TASK__` sentinel substitution).

Out of scope: cancelling a queued/running task; pruning old `tasks` rows
(history grows unbounded — acceptable for a personal single-instance app,
noted here deliberately); the unfolded per-job accept/reject/trash panel
and the scenario feedback form (those are unfolded sections, not
action-spawned forms); real-time push (2s polling stays); new
`inbox_items` producers beyond `browser_missing`.

## Design

### A. Data model

**One root task with child steps.** Every multi-step action is a single
**root task** plus zero or more **child step-tasks**; fan-out (all children
known at once) and chains (children discovered reactively) use the same
mechanism.

**`tasks` table changes:**

- `parent_task_id INTEGER` (nullable, `REFERENCES tasks(id)`) — a child
  always points at the **ultimate root** (flattened — a step never points
  at another step; step 3 of a chain still has `parent_task_id = root.id`).
  Replaces the earlier opaque `group_id` token.
- The `status` CHECK is widened: `queued`, `running`, `needs_action`,
  `done`, `failed`, `dismissed`.
  - A task whose generator returns `result.needs_action` lands in
    **`needs_action`** instead of `done`-plus-an-inbox-item.
  - Dismissing it → `dismissed`. When a follow-up child step is enqueued
    from a `needs_action` root, the **root is `resolve_task`'d → `done`**
    (its step's work is finished); the root's *derived* state then follows
    the new child.

No `goal` column — everything needed to name and describe a task is
already in `kind` + `params` and is reconstructed at render time (see C).

**`inbox_items`** stops mirroring task follow-ups. It keeps only
*task-less* notices — `browser_missing` today, stale-proposal / application
nudges later. `inbox_items.task_id` and the `task_followup` kind are gone.

**Fan-out (fetch all).** `POST /fetch/all` enqueues **one** `fetch_all`
root task and returns its id. The `fetch_all` kind generator enqueues one
`fetch_source` child per enabled source (`parent_task_id = <its own id>`),
logs "Queued N sources" and returns `{}` — it completes immediately; its
displayed state is derived from the children.

**Chain (add source / add job by URL).** `source_detect` and
`job_add_by_url` **are** the root task — no wrapper. Follow-up steps
(`source_confirm`, `job_add_listing_source`, and the mismatch panel's "add
as job instead") enqueue with `parent_task_id = <origin root id>`, threaded
through the needs-you panel via a hidden `origin_task_id` field carrying
the `__ORIGIN_TASK__` sentinel (substituted by the task engine in
`resume_html` + `html_chunks`, same as before).

**Derived state of a root** (walk `WHERE parent_task_id = root.id`, plus
the root itself), priority order:
1. anything queued/running → **running**
2. else anything `needs_action` → **needs you** (surface that task's
   `action_message` / panel)
3. else the latest-finished task `failed` → **failed** (retry)
4. else → **done**

`title` / `next_step` / `results` for a root-with-children aggregate the
subtree (`root_presentation(conn, root, children)`); a `fetch_all` root's
title is *"Fetch all"* (matching the button), the source count lives in the
`next_step` (*"13 sources — 3 done, 1 failed"*), and its result links are
trimmed to just *"Fetch history"*.

**Migration** — hard cutover (personal single-instance app): rebuild the
`tasks` table dropping `group_id` and adding `parent_task_id` (all existing
rows get NULL). No data to carry forward.

### B. Trigger states

Two mechanisms, chosen per trigger.

**B1 — Simple disabled / reset (baseline).** Standalone and per-row
buttons that have no input and must not shift layout: Fetch all, Fetch one
source, Re-evaluate everything, Get suggestions (all / per scenario),
per-job Reset / Re-evaluate / Pass-as-new, bulk Reset / Re-evaluate.

- On click: the button goes visibly inert — dimmed, `aria-disabled="true"`,
  `pointer-events: none` — while the task is `queued` / `running`.
- On `done` **or** `failed`: reset to the normal idle state. (When the
  completion also triggers an OOB/target swap of the surrounding fragment,
  that swap replaces the button anyway — unchanged.)
- The existing `data-progress-running` re-click guard and the failure
  notice are unchanged. All real progress detail stays in the status bar
  and the front-page card — the button only signals "not now".

*Optional enhancement, included in the plan only if cheap:* replace the
dim look with a CSS-only animated accent outline while running and a
~1.5s "✓" flash on done. `prefers-reduced-motion` → static outline. If it
turns fiddly, drop it; the baseline stands alone.

**B2 — Collapse + toast + card.** Triggers inside a panel whose input has
served its purpose: Add job by URL, Add source (detect), and the
resume-panel action buttons.

- On click: the `.add-panel` / panel collapses immediately; a toast fires
  (*"Adding job — following on Start ↗"*); the front-page row is the
  durable trace.
- These triggers get **no** busy/pulse state — the button is about to be
  hidden by the collapse and its glow would bleed a sliver. The busy state
  is B1-only (standalone + per-row triggers that stay on screen).
- Completion still blends into the page if you are still on it (jobs-list
  OOB swap, etc.).

### B3. Collapse forms that appeared from an action

Rule: **any form or panel rendered as the result of an action collapses to
a decided summary once you approve / deny / dismiss** — it never
re-renders still-actionable. (Forms that are just an unfolded section are
out of scope.)

- **Task needs-you panels** (`_rewrite_panel`, `jobs/_listing_confirm`,
  `sources/_detect_confirm`, `sources/_detect_mismatch`): after the action
  → a collapsed summary (*"✓ Source 'Cord' created — view source"*); after
  Dismiss → *"Dismissed"*. The same collapsed summary shows wherever the
  panel appeared — inline on Jobs/Sources, on the task detail page, and on
  the front-page card.
- **Panel-less needs-you tasks.** A task's `result` may set `needs_action`
  with only `action_message` (+ optional `action_link`) and no
  `resume_html` — e.g. the Slack-auth-error path in `fetch.py`
  `_auth_error_result`. Then: the Start-page row's primary button is
  **"Open"** → `action_link` directly (not `/tasks/{id}` — there's no
  panel to review) with Dismiss beside it, or just **Dismiss** when there's
  no `action_link` either; the detail page shows `action_message` (the
  subtitle) + an *"Open"* `btn btn-primary` to `action_link` when present +
  the page Dismiss. `task_presentation` / `root_presentation` expose
  `action_message` / `action_link` / `has_panel` / `needs_action_task_id`
  (a root's pending *child* drives these).
- **Proposal lists** (scenario-criteria proposals in
  `scenarios/_proposals.html`, profile proposals in
  `profile/_proposals.html` — produced by "Get suggestions"): a decided
  proposal greys in place (*"Accepted"* / *"Dismissed"*) instead of the
  list re-rendering without it. When every proposal is decided the block
  shows *"All 5 suggestions reviewed"* with a clear link.

### C. Task presentation helper

`_task_label(conn, task)` in `app/routes/tasks.py` becomes
`task_presentation(conn, task)` returning a dict computed per `kind` from
`params` + `status` + last log line + `result` (+ DB lookups it already
does):

- `title` — short label (today's `_task_label` output): *"Fetch: Cord"*,
  *"Add job by URL"*, *"Re-evaluate: Senior Platform Engineer"*.
- `goal` — one-line sentence of intent: *"Pull new postings from Cord"*,
  *"Add the job at lever.co/acme/… "* (`params.url`, truncated),
  *"Re-score Senior Platform Engineer against your scenarios and profile"*.
- `next_step` — state-dependent:

  | status | `next_step` |
  |---|---|
  | queued | "Queued — {n} ahead" |
  | running | parsed `[x/y]` → "Step 3 of 6", else the last log line |
  | needs_action | per `result` kind: "Confirm the detected source" / "Resolve the URL mismatch" / "Confirm this is a listing page" |
  | done | from `result`: "4 new jobs added" / "No new postings" / "Source 'Cord' created" |
  | failed | `task.error`, trimmed to one line |
  | dismissed | "Dismissed" |

- `results` — list of `{label, href}` derived per kind (see D).

Fallback for an unknown kind: `title = kind.replace("_", " ")`,
`goal = ""`, `next_step` from the table above.

`root_presentation(conn, root, children)` derives a root-with-children's
line from the whole subtree: *"4 of 6 done, 1 failed"* for a fan-out, the
current step for a chain, or the pending needs-you message.

`task_presentation` / `root_presentation` are the single source of task
naming — consumed by the status bar JSON (`/tasks/active`), the front-page
rows, the detail page and the history list.

### D. Front-page "Tasks" section

Rendered from `tasks` (with standalone `inbox_items` merged in), replacing
the current inbox-items-only list. It is a **checklist** (`<ul class="checklist">`,
like onboarding): one `<li>` per root, the bullet replaced by a
status glyph — queued `○`, running `◔`, needs-you `!`, done `✓`,
failed `✗`, dismissed `–` — coloured with `--success` / `--alert` /
`--warning` / `--accent`. Line 1 = glyph + title, with the age pushed to
the right edge (`margin-left:auto`, consistent across every row and the
notice). A second line follows **only when it carries information** —
result links, an error, or an in-progress next-step; a plain `done` task
with no result links is a single line. A needs-you row also gets an action
row (buttons on their own line, standard `.btn` sizing). A dismissed row is
just its struck-through title. The standalone `browser_missing` notice is
its own `<li>` (with a Dismiss button). The header keeps *"all tasks →"*.

**Which rows:** `get_dashboard_tasks` buckets by root (`parent_task_id or
id`) and emits **one entry per root** — `{root, children}`. A root is in
the window if it or any subtree task is active
(queued/running/needs_action) or finished within the last 24h (so a
long-done `fetch_all` root stays while its children run). Children never
appear as their own top-level row. Standalone `inbox_items`
(`resolved_at IS NULL`, e.g. `browser_missing`) are their own `<li>`.

**Root-with-children rows** show the aggregate: derived-state glyph, root
`title` (*"Fetch all"*), aggregate `next_step` (*"6 sources — 4 done, 1
failed"* / the current step / the needs-you message) and result links — no
expander, no child list (the detail page has those). A detect→confirm→add
chain is one row whose derived state advances.

**Link targets: parent→parent, child→child** — every Start-page row (root
or standalone) links to `/tasks/{id}`; child steps, seen only on the root
detail page, link to their own `/tasks/{child_id}`. There is no
`/tasks/group/` route.

**Each row shows:** glyph · `title` · age, then an optional second line —
by state:
- `needs_action` — the `next_step`, then an action row of visually distinct
  focusable buttons: **Review** (primary/accent, → detail page) *when there
  is an inline panel*, else **Open** (primary/accent) → `action_link` *when
  one is set*, else neither; always **Dismiss** (a bordered secondary
  `.btn`, not a link/subtle style). Same pairing wherever a needs-you panel
  offers confirm + cancel/dismiss.
- `done` — the `results` links (and *"✓ {summary}"* if it was a needs-you
  task); nothing if there are no result links.
- `failed` — the error line.
- `queued` / `running` — the `next_step`.
- `dismissed` — nothing (struck-through title).

**Results links** per kind (`task_presentation.results`):

Result links name the entity when the name is known — *"Jobs from Cord"*,
*"View Senior Platform Engineer"* — not *"Jobs from this source"* /
*"View job"*.

| kind | link(s) |
|---|---|
| `fetch_source` | "Jobs from {source name}" → `/jobs?source_id=…`; "Fetch history" → fetch page |
| `fetch_all` root | "Fetch history" only (per-source links would be a wall) |
| `job_add_by_url` | → `/jobs/{id}` |
| `job_add_listing_source`, `source_confirm`, `source_detect` | → `/sources#source-{id}` |
| `job_reset`, `job_reevaluate`, `job_pass_as_new` | → `/jobs/{id}` |
| `jobs_bulk_reset`, `jobs_bulk_reevaluate` | → `/jobs?…` + affected count |
| `scenarios_reevaluate_all`, `scenarios_refine_all`, `scenario_refine_one` | → `/scenarios#proposals-area-{id}` per affected scenario |
| `profile_refine` | → `/profile` |

**History:** new `GET /tasks` page — paginated table (newest first): title
· state (glyph + status word only — for a root, its **derived** state) ·
age. Status filter (all / active / done / failed). Both roots and their
children are listed as their own rows (honest history); every row →
`/tasks/{id}`. Linked from the section header (*"all tasks →"*) and the
status bar. No nav entry.

### E. Task detail page

One route `GET /tasks/{id}` (HTML), absorbing `/tasks/{id}/log` and
`/tasks/{id}/resume`. Both old paths 301 to it.

- **Breadcrumb:** when the task has a `parent_task_id`, a *"Part of:
  {root title}"* link → `/tasks/{root_id}` above the header.
- **Header:** `title` · `goal` · `next_step` · age.
- **Timeline:** created → started → finished, or "→ needs you since …"
  (via the `age` filter).
- **Needs-you panel inline** when the subtree has a `needs_action` task
  (its own, or a child's) with a `resume_html` — the same panel that
  renders on Jobs/Sources, with the B3 collapse-after-decision behaviour.
  When it's panel-less (see B3): an *"Open"* `btn btn-primary` →
  `action_link` if set. Either way the page Dismiss posts to that task's
  id.
- **Results block:** the aggregated `results` links.
- **Log:** a collapsed `<details>`, auto-refreshing (2s poll) while a
  childless task is active.
- **Root with children:** a *"Steps"* list = the root's own step, then
  each child, each linking to its own `/tasks/{child_id}`; results
  aggregated; the page reloads every 3s while the derived state is running.

The poller JSON that `pollWatched` and the log refresh use is
`GET /tasks/{id}/state`; `GET /tasks/{id}` is the HTML page.

### F. Status bar

Behaviour unchanged, except `/tasks/active` **collapses children under
their root**: a running `fetch_source` child surfaces as its `fetch_all`
root (root's goal name + child-completion progress), and the "details"
link is `/tasks/{root_id}` (was `/tasks/{id}/log`).

## Testing

- **`task_presentation` / `root_presentation`** — `title` / `goal` /
  `next_step` / `results` per kind; unknown-kind fallback; `source_detect`
  title names the URL host; subtree aggregation ("4 of 6 done, 1 failed";
  current step) and derived-state priority (running > needs_action >
  latest-failed > done).
- **Status machine** — `queued→running→needs_action→done`, `→dismissed`,
  `→failed`; a stale `running` row at worker startup still → `failed`.
- **Migration** — `group_id` dropped, `parent_task_id` added (NULL for all
  rows), row count and data preserved; widened status CHECK still there.
- **Parenting** — `fetch_all` fans out one `fetch_source` child per enabled
  source, each `parent_task_id = root`; a `source_confirm` / listing-source
  spawned from a needs-you panel attaches under the origin root and
  `resolve_task`s it; `get_dashboard_tasks` emits one entry per root and
  never surfaces a child as its own row.
- **Front-page query** — active subtrees always present; terminal roots
  only within 24h; a stale root with an active child still included;
  standalone `inbox_items` merged; resolved ones absent.
- **Detail page** — childless: summary / results / log, `needs_action`
  panel inline; root-with-children: Steps list linking to each child,
  child's needs-you panel inline (Dismiss → child id); `/log` and
  `/resume` 301 to it; `/tasks/{id}/state` returns the poller JSON;
  `/tasks/group/*` → 404.
- **CTA collapse** — a decided proposal renders in its greyed decided
  state, not as an actionable row; "all reviewed" appears when every
  proposal is decided; a resolved resume panel renders its collapsed
  summary inline, on the card, and on the detail page.
- **History page** — pagination; status filter; default view.
- **JS behaviour** (manual pass on the dev server, noted in the plan) —
  B1 disabled/reset on click and completion; B2 panel-collapse + toast;
  optional pulse + `prefers-reduced-motion` if that enhancement is built.
