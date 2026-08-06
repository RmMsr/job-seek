# Detail page polish — design

Four small, independent UX/behavior fixes to the job detail page and the Fetch page.

## 1. Auto-save scenario feedback on action button press

**Problem:** the expanded job row's scenario score tabs (`jobs/_score_tabs.html`) have their own `<form class="scenario-feedback-form">`, posting to `/jobs/{id}/scenario-feedback`, separate from the Accept/Reject/Invalid `<form class="feedback-form">` (`jobs/_feedback.html`) that posts to `/jobs/{id}/feedback`. If the user types scenario-level feedback (direction/note on a scenario tab) but clicks Accept/Reject/Invalid without first clicking "Save all feedback", the job row is swapped out (`hx-target="#job-{id}" hx-swap="outerHTML"` with an empty response body) and the unsaved scenario feedback is lost.

**Fix:** add a delegated click listener in `base.html`, alongside the existing `data-progress-url` handler, that fires when an Accept/Reject/Invalid button (`.feedback-form .actions button[type=submit]`) is clicked. It looks up the sibling `.scenario-feedback-form` within the same `.job-row` and — if found — fires its submission via `htmx.trigger(form, "submit")` before the click's own default action proceeds. This is fire-and-forget: no waiting on its response, since it writes to a different DB table (`scenario_feedback`) than the job's own status update, so there's no ordering dependency between the two requests.

This is safe to fire unconditionally, even when nothing changed in the scenario tabs: `upsert_scenario_feedback` is idempotent when resubmitted with the pre-filled, unmodified values (blank note + no direction only deletes a row when *neither* is set, which matches the "no feedback exists" starting state and is a harmless no-op delete).

Scope: single-job detail view only. The bulk action bar has no per-job score tabs, so this does not apply there.

## 2. Nav count update on action button press

**Problem:** `job_feedback` (single-job Accept/Reject/Invalid) returns an empty response and never refreshes the filter-bar counts (`New (12)`, `Accepted (3)`, etc. in `jobs/_content.html`). They go stale until the next full page load. (Bulk feedback already re-renders the whole `_content.html`, including counts — no change needed there. "Reset to new" reloads the page on completion, which also already refreshes counts — no change needed there either.)

**Fix:** wrap each of the 6 numbers in the filter bar in its own `<span id="count-new">`, `<span id="count-accepted">`, etc. `job_feedback` additionally renders those 6 spans as `hx-swap-oob="true"` fragments, appended to its (still-empty) response body. htmx applies out-of-band swaps by element id regardless of the request's main `hx-target`, so this updates the counts in place with no page reload, and without touching which filter tab is highlighted as active (untouched by this change, since only the number text inside each span is replaced).

## 3. "Reset to new" retains the job-level note

**Problem:** `reset_job` (`app/db/queries.py`) unconditionally clears `feedback_note` along with all the pipeline-derived fields (scores, content, status → `'new'`) and deletes `scenario_feedback` rows. Users want to reset a job to reprocess it without losing the note they wrote.

**Fix:** drop `feedback_note = NULL` from `reset_job`'s `UPDATE jobs SET ...` statement. Everything else `reset_job` currently clears stays as-is (including `feedback_handled_at`, which is unused elsewhere in the codebase today).

## 4. Fetch page: drop repeated URL, add lifetime stats

**Problem:** `fetch/panel.html` shows each source's URL under its name — already shown on the Sources page (`sources/_row.html`) — which is redundant. Separately, the Fetch page only shows the *last* run's new/found counts and error; there's no visibility into a source's track record over time.

**Fix:**
- Remove the `<small>{{ source.url }}</small>` line from `fetch/panel.html`'s source column.
- Add `get_fetch_stats_by_source(conn)` to `app/db/queries.py`: a `GROUP BY source_id` aggregate over the full `fetch_runs` table (not capped at the last-50-runs window that `get_recent_fetch_runs` uses today) returning, per source: `run_count` (`COUNT(*)`), `total_new` (`SUM(jobs_new)`), `total_found` (`SUM(jobs_found)`), and `last_success_at` (`MAX(completed_at) WHERE error IS NULL`).
- Add a "Lifetime" column to the Fetch page table showing total runs, total new jobs found, and last successful run time. The existing "Last run" column is unchanged — it keeps showing the most recent run's timestamp and, if that run failed, its error.

## Testing

Each item gets its own focused test(s):
1. Route/integration test: posting scenario feedback fields is picked up when submitted via the existing `/jobs/{id}/scenario-feedback` endpoint (no new backend behavior — this is a frontend-only change, verified manually in-browser rather than via a new backend test).
2. Route test: `POST /jobs/{id}/feedback` response body contains the 6 `hx-swap-oob` count spans with correct values after a status change.
3. Query test: `reset_job` leaves `feedback_note` untouched when set beforehand.
4. Query test: `get_fetch_stats_by_source` returns correct aggregates across multiple runs for a source, including when a source has zero runs.

Item 1 is markup/JS-only (no new server-side logic), so it's verified via manual browser testing rather than an automated test, per the project's existing pattern for the `data-progress-url` JS.
