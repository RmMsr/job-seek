# Backlog roundup — small UX & feature items

Eight independent backlog items, one worktree, one plan, one commit per item.
Order below is the intended implementation order (schema changes first).

---

## #14 — Jobs list sort switch (score / age / change)

**Goal:** let the user switch the Jobs list ordering between fit score, posting
age, and most-recent change. Default becomes "newest changes first".

- **Migration:** add `jobs.status_changed_at TEXT` (nullable). Set it to
  `datetime('now')` in `q.update_job_feedback` (covers single + bulk feedback,
  which share that function) and in `q.mark_job_gate_override`. No backfill —
  NULL falls back to `fetched_at` in the sort.
- **`JobFilter`:** add `order: str = "change"`. Valid values `change | score | age`
  (anything else coerces to `change`).
  - `query_params()` emits `order` only when it is not the default.
  - `cleared()` preserves `status_tab` **and** `order` (a view preference, not a
    filter).
  - `order` is **not** part of `is_narrowed`.
- **`q.get_jobs(..., order="change")`:** choose ORDER BY by `order`:
  - `change`: `ORDER BY MAX(jobs.status_changed_at, jobs.evaluation_completed_at, jobs.fetched_at) DESC, jobs.id DESC`
    (SQLite scalar `MAX` ignores NULL args; `fetched_at` is NOT NULL so always present).
  - `score`: `ORDER BY jobs.fit_score DESC NULLS LAST, jobs.fetched_at DESC` (today's behaviour).
  - `age`: `ORDER BY COALESCE(jobs.published_at, jobs.fetched_at) DESC, jobs.id DESC`.
- **UI (`jobs/_content.html`):** a `Sort` `<select>` in `.filter-row` with options
  Newest changes / Fit score / Posting age. Same htmx pattern as the existing
  selects: `hx-get="/jobs"`, `hx-target="#jobs-content"`, `hx-push-url="true"`,
  `hx-vals` status, `hx-include` the other three filter controls. The other three
  selects add `[name='order']` to their `hx-include` lists.
- **Bulk feedback:** the manual re-sort in `jobs.py` (`job_bulk_feedback`, the
  `sorted(jobs + stale_jobs, ...)` call) sorts by the active `order` instead of
  always by `fit_score`. Factor the key function so list and bulk agree.

---

## #5 — "Last run" links to its fetch task + health symbol

**Goal:** the Fetch page's "Last run" column is a health signal — make it link to
the exact task that produced the latest run, with a success/failure glyph.

- **Migration:** add `fetch_runs.task_id INTEGER REFERENCES tasks(id)` (nullable).
- **`q.start_fetch_run(conn, source_id, task_id=None)`** writes `task_id`.
- **`pipeline.run_fetch(..., task_id=None)`** new kwarg, passed straight to
  `start_fetch_run`. Thread `task_id=params["_task_id"]` from every call site:
  `_task_fetch_source`, `_task_source_confirm` (sources.py),
  `_task_job_add_listing_source` (jobs.py).
- **`fetch/_table.html`** "Last run" cell:
  - Prefix a glyph from the latest run: `✓` when `not run.error`, `✗` when
    `run.error` (keep the existing `⚠ {{ run.error }}` detail line below).
  - When `run.task_id` is set, wrap the age text in
    `<a href="/tasks/{{ run.task_id }}">`. Pre-migration runs (`task_id` NULL):
    glyph + text, no link.
  - `runs_by_source` already carries the latest run per source; ensure the query
    (`q.get_recent_fetch_runs`) selects `task_id`.

---

## #18 — Accept/reject/trash folds the card

**Goal:** when a decision moves a job out of the current tab, the expanded card
folds down to the compact "Moved to …" stale-badge row instead of snapping.

- **`jobs.py` `job_feedback`:** when the updated job leaves the current filter
  (i.e. `_render_updated_job_html` produced a stale-badge row — detect via the
  same `_stale_badge(...)` check the helper uses), add response header
  `HX-Reswap: outerHTML swap:0.35s`. When the job stays, response unchanged.
- **CSS (`base.html`):**
  ```css
  .job-row-expanded.htmx-swapping { animation: job-fold .35s ease forwards; overflow: hidden; }
  @keyframes job-fold {
    to { max-height: 0; opacity: 0; padding-top: 0; padding-bottom: 0; margin: 0; }
  }
  ```
  Needs a `max-height` start value large enough for an expanded card (e.g.
  `2000px`). Covered by the existing `prefers-reduced-motion: reduce` block —
  add `.job-row-expanded.htmx-swapping { animation: none; }` there.
- No view-transition-name changes (per-id names can't be targeted by a shared
  rule; the htmx-swapping class approach sidesteps that).

---

## #12 — Indent child step-tasks on /tasks, collapsed by default

**Goal:** in the Tasks history table, child step-tasks currently render as loose
rows next to their root. Group them under the root, indented, collapsed by
default.

- **`tasks.py` `task_history`:** after fetching the page of rows, build groups:
  for each row that has `parent_task_id`, attach under its root; fetch any root
  or sibling children not already in the page so a group is never half-shown.
  Keep pagination keyed on the 50 fetched rows (a group straddling a page
  boundary just pulls its stragglers in — acceptable).
  Pass `groups` = list of `{root: {task, pres, state}, children: [{task, pres, state}, ...]}`.
- **`tasks/list.html`:** one `<tbody>` per group.
  - Root row: in the Task cell, a hidden checkbox `#task-grp-{{ root.id }}` +
    `<label>` with a disclosure triangle (CSS-only, mirrors the scenario-tab and
    add-panel patterns already in the codebase). Only rendered when the group has
    children.
  - Child rows: same `<tbody>`, `display: none` until
    `#task-grp-{{ root.id }}:checked ~ .task-child-row` (or an equivalent
    selector that works within a `tbody`), first cell `padding-left` bump for the
    indent.
  - Childless tasks: single-row `<tbody>`, no toggle.
- No JavaScript.

---

## #6 — Destructive scenario deletion

**Goal:** allow deleting a scenario outright.

- **Connection factory:** confirm `PRAGMA foreign_keys = ON` is set on every
  connection (`app/deps.py` / wherever `sqlite3.connect` lives). Add it if
  missing — `job_scores` and `scenario_feedback` declare
  `ON DELETE CASCADE REFERENCES scenarios(id)` but that only fires with the
  pragma on.
- **`q.delete_scenario(conn, scenario_id)`:** explicit `DELETE FROM criteria
  WHERE scenario_id = ?` (criteria has no cascade), then
  `DELETE FROM scenarios WHERE id = ?`. `job_scores` / `scenario_feedback`
  cascade.
- **Route:** `DELETE /scenarios/{scenario_id}` — 404 if missing, delete, return a
  full re-render of `scenarios/index.html` (simplest; the page is a tab strip, an
  OOB removal of one panel + one nav input is fiddlier and not worth it).
- **UI:** a `Delete scenario` button in `scenarios/_header_edit.html`, styled like
  the sources Delete button, with
  `hx-confirm="Delete '<name>'? This removes its criteria and all <N> job scores for it. Job fit scores are not recalculated."`
  and `hx-delete`, `hx-target="#scenario-tabs-or-body"` `hx-swap="outerHTML"`.
  Pass the job-scores count into the template (`q.count_job_scores_for_scenario`
  or reuse an existing count).
- **Scores left stale:** gate status recomputes naturally from surviving
  `job_scores`; `fit_score` catches up on the next re-evaluate. No task queued.

---

## #7 — Shorten source URLs on /sources

**Goal:** long URLs blow out the Sources table. Show a short form, expand on
demand, always offer an explicit open link.

- **`sources/_row.html`:** replace
  `<small style="word-break:break-all;">{{ source.url }}</small>` with:
  - truncated display text: scheme stripped, `host + path` truncated to ~50 chars
    with `…`. A small Jinja helper or an inline `{{ source.url | ... }}` — prefer
    a `short_url` filter in `template_env.py` (reusable, testable).
  - a CSS `<details class="url-expand">`: `<summary>` = the short text, body = the
    full URL (`word-break: break-all`).
  - an always-visible `<a href="{{ source.url }}" target="_blank" rel="noopener">open ↗</a>`.
- **CSS (`base.html`):** minimal `.url-expand` styling (inline marker, muted,
  `font-size: 0.85em`) consistent with the existing Slack `<details>` block.
- Scope: `/sources` only. `/fetch` links the source name to `/sources` and shows
  no URL — unchanged.

---

## #8 / #13 — Jobs filter dropdown polish

All in `jobs/_content.html` (+ small `JobFilter` / query additions for `org_none`).

- **#8 — org option truncation:** Organization `<option>` visible text
  `{{ c | truncate(40, True, '…') }}`; `value="{{ c }}"` stays the full string.
- **#13 — `(All)` / `(None)` standardisation:**
  - `"All"` → `"(All)"` in the Scenario, Source, and Organization dropdowns.
  - Scenario's `"None"` → `"(None)"`.
  - **Organization `(None)`:** new `<option value="none">(None)</option>`.
    - `JobFilter.org_none: bool = False` (mirrors `scenario_none`): `from_params`
      sets it when `org == "none"`; `query_params()` emits `org=none`;
      `is_narrowed` includes it; `cleared()` drops it.
    - `_jobs_for_filter` / `_counts_for_filter` pass an `org_none` flag;
      `q.get_jobs` and `q.get_job_counts` / `q._count_filter_sql` translate it to
      `jobs.company = ''`.
  - **Source `(Single / None)`:** in the Source dropdown, render the option label
    as `"(Single / None)"` when `s.fetcher_type == "manual"`. DB `name` stays
    `"Manual"`; no other call site changes.

---

## Testing

- **#14:** `get_jobs` ordering unit tests for each `order` value incl. NULL
  `status_changed_at` / `published_at` fallbacks; `JobFilter` round-trip
  (`query_params` ↔ `from_params`) for `order`; `update_job_feedback` /
  `mark_job_gate_override` stamp `status_changed_at`.
- **#5:** `start_fetch_run` persists `task_id`; `run_fetch` threads it; a
  `fetch_source` task run leaves `fetch_runs.task_id` set. Template renders glyph
  + link.
- **#18:** route returns `HX-Reswap` header only when the job leaves the filter.
- **#12:** `task_history` groups children under roots; childless tasks render
  standalone; straddling groups pull stragglers.
- **#6:** `delete_scenario` cascades `job_scores` / `scenario_feedback`, removes
  `criteria`; route 404s on missing; FK pragma on.
- **#7:** `short_url` filter cases (long path, query string, short URL, no path).
- **#8/#13:** `JobFilter` `org_none` round-trip; `get_jobs` / `get_job_counts`
  with `org_none` match `company = ''`; dropdown label swap for the manual source.
