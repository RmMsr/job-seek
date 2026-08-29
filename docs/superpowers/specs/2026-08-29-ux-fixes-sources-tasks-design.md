# UX fixes: sources add-flow, status bar, task links, tasks list

Five independent backlog items, one worktree, one commit each.

1. Source add-flow: the confirm/suggestion panel renders cramped and doesn't
   clear after the source is added.
2. The fixed bottom status bar overlaps the bulk-action bar and can't be
   dismissed.
3. A single-job task (reevaluate / reset / pass-as-new) gives no way back to the
   job from the status bar or the task log page.
4. The Fetch page's Source-name column isn't a link to that source on the
   Sources page.
5. The home "Tasks" list uses a Dismiss button; a checkbox checklist fits better.

---

## 1. Source add-flow: full-width panel + auto-clear

### Current behaviour

`/sources` renders `_add_form.html` (a `display:flex; flex-wrap:wrap` `<form
id="add-source-panel">`) inside the collapsible `.add-panel`. Its **Add** button
has `data-progress-target="#add-source-panel"`, so `detect`'s result panel is
`innerHTML`-swapped **into the form itself**. When the LinkedIn rewrite path
fires, `_rewrite_panel.html` lands in the form; its buttons then swap
`_detect_confirm.html` into `#rewrite-panel` (a bordered `flex-direction:column`
`max-width:640px` div). Either way the confirm panel ends up inside a
shrink-wrapped flex item and renders as a ~300px column.

After **Add source** succeeds, `_task_source_confirm` returns OOB chunks that
replace `#sources-table` and `#add-source-panel`. The `.add-panel` stays
`add-panel-open`, showing an empty URL field; if the confirm panel was nested in
`#rewrite-panel` it also isn't cleared.

The Jobs "Add" flow already does this right — it targets `#jobs-content`,
*outside* the panel, and `finishWatched`'s needs-action branch auto-collapses the
panel because the result landed outside it.

### Design

Give Sources the same shape: a dedicated full-width result container.

- **`app/templates/sources/index.html`** — add `<div id="sources-add-result"></div>`
  as a sibling immediately after the `{% call add_panel("Add") %}` block and
  before `_table.html`.
- **`app/templates/sources/_add_form.html`** — the **Add** button's
  `data-progress-target` becomes `#sources-add-result` (was `#add-source-panel`).
  The `id="add-source-panel"` stays on the `<form>` (still the OOB reset target).
- **`app/templates/_rewrite_panel.html`** — add a `target` parameter; both action
  buttons use `data-progress-target="{{ target }}"`.
  - `app/routes/sources.py` passes `target="#sources-add-result"`.
  - `app/routes/jobs.py` passes `target="#rewrite-panel"` — unchanged behaviour
    for the Jobs flow, which is out of scope here.
- **`app/templates/sources/_detect_confirm.html`** — wrap its fields in
  `<div id="detect-confirm" style="display:flex; gap:0.5rem; align-items:center;
  flex-wrap:wrap;">…</div>`. It currently has no wrapper of its own and relied on
  the flex form it was injected into. (`_detect_mismatch.html` and
  `_rewrite_panel.html` already carry their own `width:100%` wrappers — leave
  them, they render fine in a full-width block.)
- **`app/routes/sources.py` `_task_source_confirm`** — every return path (the
  `already_tracked` early return and the normal success path) appends an empty
  `<div id="sources-add-result"></div>` to `html_chunks`, so the OOB pass clears
  the confirm panel. Factor the shared chunk list into a small helper, e.g.
  `_post_confirm_chunks(conn) -> list[str]` returning `[table, add_form,
  '<div id="sources-add-result"></div>']`.
- **`app/templates/base.html`** — in the `.add-panel-trigger` click handler, when
  a panel is opened, clear a stale result block: if
  `trigger.closest('.add-panel').nextElementSibling` has `id === "sources-add-result"`,
  set its `innerHTML = ""`. (One line; keeps a pending confirm form from
  co-existing with a freshly reopened URL form.)

### Resulting flow

| Step | Action | Result |
|---|---|---|
| 1 | Click **+ Add** | Panel expands, URL field shown. Stale `#sources-add-result` cleared. |
| 2 | Paste URL, click **Add** | Status bar: "Add: started". Panel open, button locked against double-submit. |
| 3 | detection finishes | Panel auto-collapses to `+ Add`. Full-width block in `#sources-add-result`: *Detected type · Name · Cancel · Add source*. |
| 3' | LinkedIn variant | Full-width suggestion card instead; picking an option replaces it **in place** with the step-3 block (no nesting, no reload). |
| 4 | Edit Name, click **Add source** | Status bar shows the first fetch. Confirm block stays, button locked. |
| 5 | fetch finishes | Confirm block disappears, table refreshes with the new row, add area back to `+ Add`. Warning notice only if the first fetch found nothing. |

Nav, the table, and every row's Edit/Delete/Fetch stay interactive at every
step — nothing is modal or removed. Cancel on the confirm/suggestion block keeps
its current `<a href="/sources">` full reload.

### Tests (`tests/test_routes_sources.py`)

- `_task_source_confirm` success result's `html_chunks` includes an empty
  `<div id="sources-add-result"></div>`.
- Same for the `already_tracked` return path.
- `_detect_confirm.html` output contains the `id="detect-confirm"` flex wrapper.
- `_rewrite_panel.html` rendered from the sources task targets
  `#sources-add-result` (existing LinkedIn detect test, extended).
- `_add_form.html` **Add** button targets `#sources-add-result`.

---

## 2. Status bar vs. bulk-action bar: no overlap + dismissable

`#status-bar` and `.bulk-bar` are both `position:fixed; bottom:0`. When a task
runs while jobs are selected they overlap.

### No overlap

- **`app/templates/base.html` CSS** — `#status-bar` and `#notice-stack` offset
  their `bottom` by a `--bulk-bar-h` custom property:
  - `#status-bar { bottom: var(--bulk-bar-h, 0px); }`
  - `#notice-stack { bottom: calc(3rem + var(--bulk-bar-h, 0px)); }`
- **`app/templates/base.html` JS** — a small block keeps `--bulk-bar-h` current
  on `document.body`:
  - measure `.bulk-bar` via `getBoundingClientRect().height` when it is visible
    (`offsetParent !== null`), else `0px`;
  - re-measure on: a `ResizeObserver` on `.bulk-bar` when present, `htmx:afterSwap`
    (the `#jobs-content` swap replaces the bar), `change` on `input[name="job_ids"]`
    / `.select-all-checkbox` (toggles visibility via `:has()`), and `resize`.
  - Hook into the existing bulk-selection IIFE (already listens to those events).

### Dismissable

- **`app/templates/base.html` JS `renderStatusBar`** — build a signature from the
  active task ids (`tasks.map(t => t.id).join(',')`). Keep a module-level
  `dismissedSignature`. If `signature === dismissedSignature` **and**
  `!data.inbox_count`, render the bar empty and return.
- Append a dismiss `<button class="status-bar-dismiss" aria-label="Dismiss">×</button>`
  to the rendered parts; a delegated click handler sets
  `dismissedSignature = <current signature>` and clears the bar.
- Session-only, no persistence. A new or changed task set produces a new
  signature and the bar returns.
- CSS: `.status-bar-dismiss { margin-left:auto; background:none; border:none;
  color:inherit; opacity:0.6; cursor:pointer; font-size:1rem; line-height:1; }`
  `:hover { opacity:1; }`.

### Tests

CSS/JS-only. Manual dev-server check: start a fetch, select jobs, confirm the
status bar sits above the bulk bar and the × hides it until the next task.

---

## 3. Single-job task links back to the job

`job_reset` / `job_pass_as_new` / `job_reevaluate` tasks carry `params["job_id"]`
but surface no link. Bulk kinds (`jobs_bulk_*`) span many jobs — no single link.

- **`app/routes/tasks.py` `_task_summary`** — add `"link"`: for a kind in
  `{"job_reset", "job_pass_as_new", "job_reevaluate"}` with a `job_id` param,
  `f"/jobs/{job_id}"`; otherwise omitted / `None`. `/tasks/active` and
  `/tasks/{id}` both return it (they share `_task_summary`).
- **`app/templates/base.html` `renderStatusBar`** — when `current.link` is set,
  render `<a href="{link}">view job</a>` next to the existing
  `<a href="/tasks/{id}/log">details</a>`.
- **`app/routes/tasks.py` `task_log`** — compute the same link and pass it to the
  template.
- **`app/templates/tasks/log.html`** — when the link is present, show
  `<p><a href="{{ job_link }}">← Back to the job</a></p>` under the status line.

### Tests (`tests/test_routes_tasks.py`)

- `/tasks/{id}` for a `job_reevaluate` task includes `"link": "/jobs/<id>"`.
- `/tasks/{id}` for a `fetch_source` task has no `link` (or `None`).
- `/tasks/{id}/log` page for a `job_reset` task renders the job link.

---

## 4. Fetch page Source-name column links to Sources

- **`app/templates/fetch/_table.html`** — wrap the name:
  `<a href="/sources#source-row-{{ source.id }}">{{ source.name }}</a>` (keep the
  `<strong>`).
- No JS change: `base.html`'s hash-highlight script already animates a
  `.source-row` target on arrival, and `sources/_row.html` is
  `<tbody id="source-row-{{ source.id }}" class="source-row">`.

### Tests (`tests/test_routes_fetch.py`)

- The fetch table renders `href="/sources#source-row-<id>"` for a configured
  source.

---

## 5. Home "Tasks" list: checkbox checklist instead of Dismiss button

`home/index.html`'s Tasks section renders each unresolved inbox item as
`<li>` + `<a>` + a **Dismiss** `<button hx-post="/inbox/{id}/resolve"
hx-swap="outerHTML">` that swaps the `<li>` to empty. Resolved items render as
plain struck-through `<li>`.

### Design

- **New `app/templates/home/_task_item.html`** — renders one item as an `<li>`,
  parametrised by `item` and `done` (bool):
  - `done=False`: `<li id="inbox-item-{{ item.id }}">` with a leading
    `<input type="checkbox" hx-post="/inbox/{{ item.id }}/resolve"
    hx-target="#inbox-item-{{ item.id }}" hx-swap="outerHTML">` and
    `<a href="{{ item.link }}">{{ item.message }}</a>`.
  - `done=True`: `<li class="task-completed">` with a checked, `disabled`
    checkbox and the message text (no link).
- **`app/templates/home/index.html`** — the Tasks `<ul>` includes
  `_task_item.html` with `done=False` for each `pending_tasks` item and
  `done=True` for each `recent_completed_tasks` item.
- **`app/routes/inbox.py` `inbox_resolve`** — return the item re-rendered via
  `_task_item.html` with `done=True` instead of `HTMLResponse("")`, so checking
  the box leaves a checked-off entry in place. Needs the resolved item row and a
  `request` (add `Request` param); use `q.get_...` for the single item (add a
  `get_inbox_item(conn, item_id)` query if none exists).
- CSS: the existing `.checklist` / `.task-completed` rules are enough; add
  `.checklist li input[type=checkbox] { margin-right: 0.5rem; }` for spacing.

### Tests

- `tests/test_routes_home.py` — the Tasks section renders a checkbox posting to
  `/inbox/{id}/resolve` for a pending item (replaces the current
  "shows_pending_task_with_dismiss_button" assertion); a resolved item renders a
  checked disabled checkbox.
- `tests/test_routes_inbox.py` — `POST /inbox/{id}/resolve` marks it resolved
  **and** returns an `<li class="task-completed">` containing the message.

---

## Out of scope

- Unifying `/sources/detect` and `/jobs/add-by-url` (separate backlog item).
- The Jobs add-by-url rewrite/listing panel has the same latent nesting as
  Sources did; not touched here — Jobs keeps `target="#rewrite-panel"`.
- The `_detect_confirm` / `_detect_mismatch` **Cancel** link stays a full
  `/sources` reload.
- The already-tracked detect path still `location.reload()`s and loses its
  notice — pre-existing, unchanged.
- Task detail page reorganisation ("what does the title mean", form structure) —
  separate backlog item; item 3 only adds the back-link.
- Reworking `/inbox` beyond the resolve response.

## Manual testing

UI-facing. After implementation, run the dev server against a throwaway DB copy
(`run-dev-server` skill) and hand the URL over: add a source end to end (plain
URL and a LinkedIn search URL), select jobs while a fetch runs to check the
status-bar stacking and dismiss, follow a reevaluate task's "view job" link, and
check off a home Tasks item.
