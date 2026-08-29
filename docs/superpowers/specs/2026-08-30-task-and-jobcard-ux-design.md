# Task & job-card UX pass

Small UX cleanups around the task pages, plus one unrelated job-card fix pulled
from the backlog. One worktree, one plan.

## 1. `age` template filter

New `age(ts)` in `app/dates.py`, sibling to `time_ago`, registered as the `age`
Jinja filter in `app/template_env.py`.

- `""`/`None` → `""`
- `< 60s` (incl. future / clock skew) → `"just now"`
- `< 60m` → `"{n}m ago"`
- `< 24h` → `"{n}h ago"`
- else → `"{n}d ago"`

Kept separate from `time_ago` (day-granularity: "today" / "N days ago") so job
row ages are unchanged.

## 2. Task age display

A task shows how old it is (from `created_at`) in three places:

| Where | Template | Value | Placement |
|---|---|---|---|
| Start-page task list | `home/_task_item.html` | `item.created_at` | muted `<span class="task-age">` trailing the message, both pending and done rows |
| Action-needed page | `tasks/resume.html` | `task.created_at` | muted subtitle line under the heading |
| Task log page | `tasks/log.html` | `task.created_at` | in the metadata block (§4) |

`GET /tasks/{id}/resume` starts passing `task` to the template (today it passes
only `resume_html` / `action_message` / `inbox_item_id`).

`.task-age`: `font-size: 0.8rem; color: var(--text-muted);` in `base.html`.

## 3. Action-needed page: honest exit + actions navigate away

The four resume panels — `_rewrite_panel.html`, `jobs/_listing_confirm.html`,
`sources/_detect_confirm.html`, `sources/_detect_mismatch.html` — share a shape:
one `<a class="btn btn-subtle">Cancel</a>` plus `data-progress-*` action
`<button>`s. On the resume page today, Cancel navigates away without resolving
the inbox item (task lingers), and pressing an action swaps into a target
selector that doesn't exist on that page (dead-end).

### 3a. Dismiss

- New route `POST /tasks/{task_id}/dismiss`: resolve the task's inbox item
  (`q.get_inbox_item_by_task_id`), then `303` redirect to the `Referer` header
  when same-origin, else `/`. No open item → still redirects.
- `tasks/resume.html`: render its own **Dismiss** button — a `<form method="post"
  action="/tasks/{{ task.id }}/dismiss">`. Scoped CSS `#resume-page a.btn-subtle
  { display: none; }` hides each panel's built-in Cancel. The four panel
  templates are **not** touched (their inline use on Jobs/Sources is unchanged).

### 3b. Actions navigate away ("back, with reload")

`base.html` `finishWatched`: add a branch at the top, before the oob/swap logic.
When `#resume-page` is in the DOM:

- task **failed** → show the error notice, stay on the page (retry possible).
- task **done** → resolve the inbox item (`data-inbox-item-id` on `#resume-page`),
  then hard-navigate: `document.referrer` if same-origin, else `/`. A full
  `window.location` assignment, so the destination reloads.

This replaces the current fire-and-forget resolve at the end of `finishWatched`
and covers every action button generically, including the OOB-mode source
confirms. Chained steps still work: the follow-up task's new inbox item is in the
Start-page list after the reload.

## 4. Task log page reorg (`tasks/log.html`)

Backlog: *"the task detail page needs to be reorganised. What does the title
mean. The form needs structure."*

- **Title / label**: replace `Task: {{ task.kind.replace('_', ' ') }}` (renders
  "Task: job add by url") with a human label that names the entity where one is
  known. Extend `_task_label` in `routes/tasks.py` (it already has `conn`):

  | kind | label |
  |---|---|
  | `fetch_source` | `Fetch: {source name}` (unchanged) |
  | `job_add_by_url` | `Add job by URL` |
  | `job_add_listing_source` | `Add listing source: {params.name}` |
  | `source_detect` | `Detect source` |
  | `source_confirm` | `Add source: {params.name}` |
  | `job_reset` | `Reset job: {job title}` (lookup `q.get_job(params.job_id)`; bare `Reset job` if gone) |
  | `job_reevaluate` | `Re-evaluate job: {job title}` |
  | `job_pass_as_new` | `Pass job as new: {job title}` |
  | `jobs_bulk_reset` / `jobs_bulk_reevaluate` | `Reset N jobs` / `Re-evaluate N jobs` (`len(params.job_ids)`) |

  Fallback stays `kind.replace("_", " ")`. This label is what the status bar's
  active-task line and the log page both show, so the status line names the
  source/job explicitly too. Pass `label` from the `task_log` route.

- **Metadata block**: replace the two loose `<p>`s with a `<dl>`: Status, Age
  (`task.created_at | age`), Started (`task.started_at | age` if set), Finished
  (`task.finished_at | age` if set), Error (if set). Keep `id="task-log-status"`
  on the status value for the existing poll script.

- **Log**: keep the `<pre>`, move its inline styles to a `.task-log` class in
  `base.html`.

No behaviour change to the 2s poll / reload-on-finish script.

## 5. Resume panel structure

Backlog: *"The form needs structure."* The four panels cram fields into a
horizontal `flex` row with `0.8em` labels. Restructure each to a vertical layout:

- One `.resume-panel` container class (in `base.html`), replacing the per-panel
  inline `style` blocks.
- Label above input, inputs full-width within a `max-width`, `<label>` elements
  properly associated (`for`/`id` already present in most).
- Action buttons on their own row at the bottom, primary action last.
- The explanatory text (`reason`, "Detected N job posting links…", the mismatch
  warning) stays as an intro paragraph.

These partials render both inline (Jobs/Sources add-flow, inside `.add-panel`)
and on the resume page, so the new layout must look right in both. No markup
that depends on the surrounding page.

## 6. Job-card permalink button

Backlog: *"The permalink button of a job card should 1) stay in the same place
folded and unfolded 2) be in the top right corner, left of the checkbox if
present 3) copy the link to the clipboard on a normal click, with fallback to a
normal link."*

Applies to `jobs/_row.html` (collapsed) and `jobs/_feedback.html` (expanded);
`jobs/detail.html` context has no checkbox.

- **Position**: `.job-link-icon` becomes `position: absolute` in both states,
  anchored top-right. When the select checkbox is present it sits to its left
  (`right: ~2.2rem`); with no checkbox (detail page) it takes the edge slot
  (`right: ~0.5rem`). Same coordinates collapsed and expanded so it doesn't jump
  on toggle. Remove it from the inline flow of `.job-row-header` /
  `.job-detail-title-row` (drop their `padding-right` reservation as needed).
- **Copy on click**: add a small delegated handler (in `base.html`). On click of
  `.job-link-icon`: `preventDefault`, `stopPropagation`, write the resolved
  absolute `href` to the clipboard via `navigator.clipboard.writeText`, and flash
  a transient "Copied" state (swap the glyph / add a class for ~1.2s). If
  `navigator.clipboard` is unavailable or the write rejects, fall through to
  normal link navigation. The `<a href="/jobs/{id}">` stays intact as the
  no-JS / fallback path.
- Keep `title` / `aria-label`; update copy to reflect click-to-copy.
- Mobile overrides (`base.html` ~line 415: `.job-link-icon { float: right }`)
  reconciled with the new absolute positioning.

## Testing

- `tests/test_dates.py`: `age` — each threshold, empty, future timestamp.
- `tests/test_routes_tasks.py`: `POST /tasks/{id}/dismiss` resolves the inbox
  item and redirects; redirect target honours same-origin `Referer`; resume page
  renders the age subtitle and the Dismiss form; log page renders the human
  label (incl. named source / job) and the metadata `<dl>`.
- `tests/test_routes_home.py`: start-page task rows render `.task-age`.
- `tests/test_routes_jobs.py`: `.job-link-icon` present with absolute-position
  class and correct `href` in collapsed, expanded, and detail contexts.
- JS behaviour (resume-page redirect, click-to-copy) — manual pass on the dev
  server; note in the plan.

## Out of scope

Other backlog items (mcp server, license, `generic_listing` pagination, profile
add-entry bug).
