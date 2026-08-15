# Source management usability — design

**Date:** 2026-08-15
**Status:** Approved

## Problem

Sources currently can't be deleted — a bad or defunct source just sits in
`/sources` forever, and there's no way to see everything a given source has
produced without going through the status/content-type tabs on `/jobs` (New,
Leads, Accepted, Rejected, Not relevant, Trash) one at a time. Separately,
the "Lifetime" column on `/fetch` reads e.g. "3 runs, 12 new", which reads as
if it means "12 unseen jobs right now" rather than its actual meaning: 12
jobs added to the DB across the source's whole history.

## Goals

- Delete a source, cascading to everything that references it (fetch runs,
  jobs, and — transitively — job scores and scenario feedback), with a
  confirmation that states how many jobs will be removed.
- From `/fetch`, jump straight to every job a source has ever produced,
  regardless of status or content type, with one click.
- Reword the Lifetime column so "new" isn't confused with the New-jobs tab.

## Non-goals

- No "soft delete" / undo / archive for sources — deletion is permanent, as
  agreed with the user (personal, single-instance app; see project's
  migration/data philosophy).
- No option to delete a source but keep its jobs (e.g. reassign them to the
  `manual` source). Always cascades.
- No combining `source_id` with the existing status/content-type tabs — the
  source view is intentionally "everything, unfiltered," not a further facet
  to combine with the others.
- No changes to the fetch/pipeline logic itself, only to the UI/routes
  described here.

## 1. Delete a source

### Data layer (`app/db/queries.py`)

- `get_job_counts_by_source(conn) -> dict[int, int]`: grouped query,
  `SELECT source_id, COUNT(*) FROM jobs GROUP BY source_id`, mirroring the
  existing `get_fetch_stats_by_source` shape.
- `count_jobs_by_source(conn, source_id) -> int`: single-source count, for
  routes that only touch one row (`SELECT COUNT(*) FROM jobs WHERE
  source_id = ?`).
- `delete_source(conn, source_id) -> None`: deletes, in order and in one
  transaction, `fetch_runs` for that source, then `jobs` for that source
  (which cascades via the existing `ON DELETE CASCADE` FKs to `job_scores`
  and `scenario_feedback`), then the `sources` row itself. No schema
  migration is needed — the cascades on `jobs` already exist; `fetch_runs`
  and `jobs` just aren't cascaded off of `sources` today, so the app deletes
  them explicitly before the `sources` row.

### Route (`app/routes/sources.py`)

```
DELETE /sources/{source_id}
```

- 404 via the existing `_get_source_or_404`.
- Calls `q.delete_source`, returns an empty `HTMLResponse` (200) — same
  pattern as `DELETE /criteria/{criterion_id}`.

### Template

- `sources/_row.html`: new **Delete** button next to **Edit**, using
  `hx-delete`, target `#source-row-{{ source.id }}`, `hx-swap="outerHTML"`
  (row vanishes on success), and:
  ```
  hx-confirm="Delete '{{ source.name }}' and its {{ job_count }} job{{ 's' if job_count != 1 else '' }}? This cannot be undone."
  ```
- `job_count` must be in scope everywhere `_row.html` is rendered, following
  the existing dual convention used for `needs_login_by_id` / `needs_login`:
  - `sources_page` (the `/sources` list): computes
    `job_counts_by_source = q.get_job_counts_by_source(conn)` once, passes
    the dict to the template; `sources/index.html`'s loop sets
    `job_count = job_counts_by_source.get(source.id, 0)` per iteration
    (alongside its existing `needs_login` `{% set %}`).
  - `create_source`, `update_source`, `set_cookie`, `forget_cookie` (all
    single-row renders): each passes `job_count =
    q.count_jobs_by_source(conn, source_id)` directly into the `_row.html`
    context.

## 2. "View all jobs" from the Fetch page

### Route (`app/routes/jobs.py`)

- `GET /jobs` gains an optional `source_id: int | None = None` query param,
  threaded into `_content_context`.
- `_get_filtered_jobs(conn, status, content_type, source_id=None)`: when
  `source_id` is not `None`, ignores `status`/`content_type` entirely and
  returns `q.get_jobs(conn, source_id=source_id)` — every status and content
  type for that source, same default ordering
  (`fit_score DESC NULLS LAST, fetched_at DESC`).
- `q.get_jobs` gains a `source_id: int | None = None` filter parameter
  (`jobs.source_id = ?` clause), composable with its existing filters (used
  standalone here, but no reason to special-case the query function itself).
- `_filter_context(request)`: also reads `source_id` from query params. When
  present, includes `filter_source_id` in the returned dict (alongside
  `filter_status`/`filter_content_type`, which become `None` in this mode).
  The "any filter present" check that currently gates on
  `status`/`content_type` also gates on `source_id`.
- `_stale_badge` and `_content_context` take and pass through `source_id` /
  `filter_source_id` the same way they already do for status/content_type.
- Because the source view already includes every status, a job that gets
  accepted/rejected/trashed while browsing it never goes stale — it stays
  visible, since it's still that source's job. `_stale_badge` naturally
  returns `None` in this mode as a consequence of `_get_filtered_jobs`
  including all statuses, no special-casing needed there.
- `filter_source_id` needs a source name and total count for the header (see
  template section) — fetch the source via `q.get_source(conn, source_id)`
  in `_content_context` when `source_id` is set.

### Propagating `source_id` through actions

Same mechanism already used for `status`/`content_type`: baked into each
row's action URLs so it survives htmx round-trips (expand/collapse,
feedback, reset, pass-as-new) and into the bulk-form's hidden fields and the
bulk-reset URL. Every place `_row.html` / `_feedback.html` /
`_content.html` currently does:

```
{% if filter_status is defined %}?status={{ filter_status or '' }}&content_type={{ filter_content_type or '' }}{% endif %}
```

gains a `&source_id={{ filter_source_id }}` suffix when `filter_source_id`
is defined (and the bulk-form's hidden `status_filter`/`content_type_filter`
inputs gain a sibling `source_id_filter` input, read back by
`_filter_context` alongside the query-param path used for GET-driven
re-renders).

### Template (`jobs/_content.html`)

- When `filter_source_id` is set, the status tabs
  (New/Leads/Accepted/Rejected/Not relevant/Trash) are replaced with a
  single header line:
  ```
  All jobs from {{ filter_source.name }} ({{ jobs | length }}) · <a href="/jobs">Clear filter</a>
  ```
- Bulk select/accept/reject/trash/reset controls are unchanged and remain
  available in this mode.

### Template (`fetch/panel.html`)

- Each source row gets a **View all jobs** link:
  `<a href="/jobs?source_id={{ source.id }}" class="btn">View all jobs</a>`.
  Works even when the source has 0 jobs (renders the existing "No jobs
  found." empty state).

## 3. Lifetime wording

- `fetch/panel.html`: `{{ stats.total_new }} new` → `{{ stats.total_new }}
  added`. No data-layer change — same `total_new` value, different label.

## Testing

- `tests/test_routes_sources.py`: delete route removes the source, its
  fetch runs, its jobs (and transitively job_scores/scenario_feedback);
  404 on unknown id; confirm-text job count reflects actual job count
  (0, 1, and N cases for pluralization).
- `tests/test_routes_jobs.py` (or wherever `/jobs` route tests live): `source_id`
  filter returns jobs across all statuses/content types for that source and
  none from other sources; combined with a feedback/reset action, the job
  stays visible (no stale badge) and `source_id` survives the round-trip.
- Manual check via `run-dev-server`: delete confirm dialog text, view-all-jobs
  link from `/fetch`, Lifetime column wording.
