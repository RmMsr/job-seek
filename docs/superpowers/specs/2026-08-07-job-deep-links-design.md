# Job deep links & in-place updates — design

Two related changes: every job gets a shareable permalink, and the Reset/Pass-as-new actions stop reloading the whole page — they update the job in place instead, even if the action moves the job out of whatever filtered tab the user is currently looking at. Accept/Reject/Invalid keep their current "move on" behavior (row vanishes from the list immediately) everywhere except the new permalink page, where there's no list to move on within.

## 1. Deep link: `GET /jobs/{job_id}`

**Problem:** there's no URL that opens a single job's detail directly. The only "detail" view today is an inline expand of a row inside the filtered `/jobs` list (`app/routes/jobs.py`'s `job_expand`/`job_collapse`, swapping `jobs/_row.html` ↔ `jobs/_feedback.html` via HTMX). You can't link someone to a specific job, or bookmark one, independent of which tab/filter it currently lives in.

**Fix:** add `GET /jobs/{job_id}` in `app/routes/jobs.py`, rendering a new minimal template `jobs/detail.html` that extends `base.html`, shows a "← Back to list" link, and includes the same job content as the inline expanded view (`jobs/_feedback.html`). 404s if the job doesn't exist. This page has no active filter — it always shows exactly the one job.

The bulk-select checkbox in `_feedback.html` only makes sense paired with the list's bulk-action bar, so it's omitted entirely on this page (a `hide_select` flag passed into the template, defaulting to shown — when true, the `<label class="job-select-wrap">` block is skipped rather than rendered-and-hidden).

A small permalink icon (🔗) is added next to the "↗ original" link on both `_row.html` and `_feedback.html`, pointing to `/jobs/{{ job.id }}`. On `_row.html` the whole `<article>` is the click-to-expand target, so the icon needs `onclick="event.stopPropagation()"`, matching the existing pattern used for the bulk-select checkbox on the same element. On `_feedback.html` the click-to-collapse zone is scoped to the inner header `<div>` only, so the icon (placed in the `<p>` line with the company/original-link, outside that div) needs no such guard.

## 2. Reset / Pass-as-new update in place instead of reloading

**Problem:** `POST /jobs/{id}/reset` and `POST /jobs/{id}/pass-as-new` (`app/routes/jobs.py`) stream progress via the `data-progress-url` JS in `base.html`, then call `location.reload()` on completion (neither button sets `data-progress-target` or `data-progress-oob`, so the JS's `finish()` falls through to the reload branch). This throws away the user's scroll position and view state to show what's usually a one-row change.

**Fix:** reuse the existing `HTML:` streaming-chunk + `data-progress-oob` convention already used by `scenarios.py`'s "refine criteria" flow (`refine_criteria`, `scenarios/index.html`). At the end of each stream, the route renders the job's updated `_feedback.html` and yields it as `"HTML:" + rendered.replace("\n", "")`. The buttons in `_feedback.html` get `data-progress-oob` added. `base.html`'s existing `applyOob()` then replaces the row's `outerHTML` in place by matching the `id="job-{id}"` embedded in the chunk — no changes needed to the shared JS itself.

This applies uniformly whether the buttons are rendered inside the `/jobs` list or on the new `/jobs/{id}` page — the job re-renders as its (now up to date) expanded detail view either way, staying visible with fresh scores/status without a reload.

Failure handling is unaffected: `finish()` already short-circuits on a failed stream before touching the OOB/target branches, so a failed reset/pass-as-new still just shows the red error text next to the button, same as today.

## 3. "Moved to…" badge for rows that fall out of the active filter

**Problem:** once reset/pass-as-new updates a row in place instead of reloading, a job can end up displayed in a tab it no longer belongs to — e.g. resetting a job while viewing "Not relevant" can flip it back to gate-passed "New", but the row would just sit there in the "Not relevant" list with no reload to naturally clear it out.

**Fix:** thread the active filter (`status`/`content_type` query params) through the expand/collapse round trip so `_feedback.html` always knows what filter (if any) it's being viewed under:
- `job_list` already has `status`/`content_type` in its render context; `_content.html`'s `{% include "jobs/_row.html" %}` already passes that context through implicitly (no `without context`).
- `_row.html`'s expand link and `_feedback.html`'s collapse link (`hx-get` to `/jobs/{id}/expand` / `/jobs/{id}/collapse`) append `?status=...&content_type=...` when those are defined, and `job_expand`/`job_collapse` accept and forward them into the template context, so the filter survives repeated toggling.
- The reset/pass-as-new buttons' `data-progress-url` likewise appends the current `status`/`content_type`, and the `/jobs/{id}/reset` and `/jobs/{id}/pass-as-new` routes accept them as optional query params.

After performing the update, if `status`/`content_type` were provided (i.e. this request came from a list context, not the standalone `/jobs/{id}` page), the route checks whether the job is still in `_get_filtered_jobs(conn, status, content_type)`. If not, the re-rendered row includes a small badge, e.g. `Moved to New ↻`, linking to the tab the job now lives in — `/jobs` for gate-passed job postings, `/jobs?status=not_relevant` for gate-failed ones, `/jobs?content_type=lead` for leads. If the job's new `content_type` doesn't correspond to any visible tab (e.g. reclassified as neither a posting nor a lead), the badge reads plainly "No longer shown in this view" with no link. If the job still matches the current filter, or the request came from the standalone `/jobs/{id}` page (no filter params), no badge is shown.

Jinja distinguishes "`status` not passed at all" (the standalone detail page) from "`status` passed as `None`" (the list's default `/jobs` tab, which implicitly means gate-passed New) via `{% if status is defined %}`, so the two cases don't need a separate sentinel flag.

## 4. Accept / Reject / Invalid — unchanged in the list, redirect on the detail page

**Problem:** `job_feedback` (`POST /jobs/{id}/feedback`) returns only the OOB count-update fragment, which — combined with the feedback form's `hx-target="#job-{id}" hx-swap="outerHTML"` — makes the row vanish immediately. That's the desired "move on" behavior in the list. But on the new `/jobs/{id}` page, the same swap would just leave an empty page with no list to fall back to.

**Fix:** the feedback form in `_feedback.html`, when rendered on `/jobs/{id}`, includes a hidden `redirect=/jobs` field. `job_feedback` checks for that field and, if present, responds with an `HX-Redirect: /jobs` header instead of the counts-only body — HTMX's built-in support for that header does a full navigation to `/jobs` client-side. In the list context (no hidden field), behavior is byte-for-byte unchanged.

## Out of scope

- **Bulk-reset** (`POST /jobs/bulk-reset`) keeps its current full-page-reload behavior. It resets many jobs at once with progress reported per-job; giving each affected row independent in-place/stale-badge treatment is a larger change than this covers.
- **Scenario/profile-triggered re-evaluation** (`run_reevaluate`, `run_reassess_fit` in `app/pipeline.py`, invoked from `app/routes/scenarios.py` / `app/routes/profile.py`) is untouched. Those are bulk operations launched from different pages, not the per-job actions this design covers.

## Testing

1. Route test: `GET /jobs/{id}` returns 200 with the job's content for an existing job, 404 for a missing one.
2. Route test: `GET /jobs/{id}` response does not include the bulk-select checkbox markup at all.
3. Route test: `POST /jobs/{id}/reset` and `POST /jobs/{id}/pass-as-new` stream responses end with an `HTML:` chunk containing the job's `id="job-{id}"` and updated field values (e.g. new `gate_status`/scores).
4. Route test: `POST /jobs/{id}/reset` with `status=not_relevant` in the query string, on a job whose reset flips it to gate-passed, includes the "Moved to New" badge/link in the final `HTML:` chunk; the same call with no `status`/`content_type` params (simulating the standalone detail page) does not.
5. Route test: `POST /jobs/{id}/reset` with query params matching the job's filter post-reset (i.e. it still belongs) produces no badge.
6. Route test: `POST /jobs/{id}/feedback` with a `redirect` field set responds with `HX-Redirect` header and no body change to existing behavior when the field is absent.
7. Manual/browser verification of the JS-only pieces: permalink icon click doesn't trigger row expand/collapse; reset/pass-as-new visibly update the row without a page reload; the stale badge is clickable and lands on the right tab.
