# Delete Trash jobs — design

**Date:** 2026-08-15
**Status:** Approved

## Problem

There's no way to permanently remove a job — mainly error-case jobs (failed
fetch, no extractable content) that get auto-filed into Trash by
`_insert_error_job`, plus anything a user has manually trashed. They just
accumulate in the Trash view forever.

## Goals

- Delete a job, permanently, from the Trash view — both one at a time and via
  multi-select.
- Reuse the existing Accept/Reject/Trash action slot rather than adding new
  UI surface: since "Trash" is meaningless on an already-trashed job, that
  button becomes **Delete** whenever the job (or, in bulk, the active filter)
  is already `trash`.
- Guard against accidental data loss with an inline confirm step before the
  delete actually happens, consistent with the existing source-deletion
  pattern (`sources/_row_delete_confirm.html`).

## Non-goals

- No delete for any other status — jobs must be moved to Trash first (via
  the existing Trash action) before they're deletable. Enforced both by only
  ever showing Delete where the job/filter is already `trash`, and by the
  routes re-checking `status == "trash"` server-side.
- No "soft delete" / undo — deletion is permanent, consistent with this
  project's migration/data philosophy (personal, single-instance app).
- No changes to how jobs get *into* Trash, or to any other status's actions.

## 1. Data layer (`app/db/queries.py`)

- `delete_job(conn, job_id) -> None`: `DELETE FROM jobs WHERE id = ?` +
  commit. Cascades to `job_scores` and `scenario_feedback` via their existing
  `ON DELETE CASCADE` FKs — the runtime connection already sets `PRAGMA
  foreign_keys = ON` (`app/deps.py`), so no manual cleanup or schema change
  is needed.
- `delete_jobs(conn, job_ids: list[int]) -> None`: same, batched — deletes
  only rows whose `status = 'trash'` (`DELETE FROM jobs WHERE status =
  'trash' AND id IN (...)`), so a caller passing a mixed/unexpected id list
  can never delete a non-trash job. One commit for the batch.

## 2. Routes (`app/routes/jobs.py`)

### Direct (single job) delete

```
GET    /jobs/{job_id}/delete-confirm
DELETE /jobs/{job_id}
```

- `delete_confirm`: 404 via existing `get_job`-not-found check (mirrors
  `job_detail`/`job_reset`). Renders a new `jobs/_row_delete_confirm.html`
  partial swapped into `#job-{{ job.id }}`.
- `DELETE /jobs/{job_id}`: 404 if missing, 400 if `status != "trash"`.
  Otherwise `q.delete_job`, returns empty `HTMLResponse` (row vanishes on
  `hx-swap="outerHTML"`), plus an out-of-band `_counts_oob.html` chunk so the
  Trash count in the nav updates without a full reload (same technique
  `job_feedback` already uses for its counts response).
- Cancel returns to the normal row: reuses the existing `GET
  /jobs/{job_id}/collapse` (already renders `_row.html` with filter context)
  — no new "cancel" route needed.

### Bulk delete

```
POST /jobs/bulk-delete-confirm
POST /jobs/bulk-delete
POST /jobs/bulk-actions-cancel
```

Both take the same form inputs as `/jobs/bulk-feedback`: `job_ids:
list[int]`, plus `status_filter`/`content_type_filter`/`source_id_filter`
(read from `#bulk-form`'s hidden fields, same as today).

- `bulk-delete-confirm`: renders a confirm partial
  (`jobs/_bulk_delete_confirm.html`) swapped into the bulk-bar's actions
  area, showing the count ("Delete 5 selected jobs? This cannot be undone.")
  and Confirm/Cancel. (Extract the current `.decision-panel-left` actions
  markup into a small `jobs/_bulk_actions.html` partial, parameterized on
  `status`, so both the default render and the cancel path below share it.)
- `bulk-delete`: `q.delete_jobs(conn, job_ids)` (already filters to
  `status='trash'` rows only — belt-and-suspenders since the button is only
  ever shown in the Trash view). Re-renders `jobs/_content.html` the same
  way `job_bulk_feedback` does today (recomputed `jobs`, `counts`, stale
  handling for any passed-in id no longer present).
- `bulk-actions-cancel`: takes `status_filter` (from `#bulk-form`'s hidden
  field, same as the other bulk routes). Re-renders `jobs/_bulk_actions.html`
  with `status=status_filter` — the confirm partial's Cancel button targets
  this route so it returns to the normal Accept/Reject/Delete row without a
  full `_content.html` re-render.

## 3. Templates

### `jobs/_feedback.html` (per-job actions row)

The primary actions row currently is:

```html
<button ... value="accepted" class="btn btn-accept">Accept</button>
<button ... value="rejected" class="btn btn-reject">Reject</button>
<button ... value="trash" class="btn btn-trash">Trash</button>
```

When `job.status == "trash"`, the third button is replaced:

```html
<button type="button" class="btn btn-delete"
  hx-get="/jobs/{{ job.id }}/delete-confirm{{ ...filter query string... }}"
  hx-target="#job-{{ job.id }}"
  hx-swap="outerHTML">Delete</button>
```

(same filter-query-string suffix pattern already used on every other
per-job action URL in this file, so Cancel/collapse round-trips preserve the
current filter.)

### `jobs/_row_delete_confirm.html` (new)

Mirrors `sources/_row_delete_confirm.html`: swapped into `#job-{{ job.id }}`,
shows "Delete this job? This cannot be undone." with **Confirm delete**
(`hx-delete /jobs/{{ job.id }}`, target `#job-{{ job.id }}`, `outerHTML`) and
**Cancel** (`hx-get /jobs/{{ job.id }}/collapse{{ ...filter qs... }}`, same
target/swap).

### `jobs/_content.html` (bulk actions row)

Extract the current actions block:

```html
<div class="actions" role="group" aria-label="Bulk decision">
  <button type="submit" form="bulk-form" name="status" value="accepted" class="btn btn-accept">Accept</button>
  <button type="submit" form="bulk-form" name="status" value="rejected" class="btn btn-reject">Reject</button>
  <button type="submit" form="bulk-form" name="status" value="trash" class="btn btn-trash">Trash</button>
</div>
```

into `jobs/_bulk_actions.html`, parameterized on `status` (the active
filter, already in scope). When `status == "trash"`, the third button
becomes:

```html
<button type="button" class="btn btn-delete"
  hx-post="/jobs/bulk-delete-confirm"
  hx-include="#bulk-form"
  hx-target=".decision-panel-left .actions"
  hx-swap="outerHTML">Delete</button>
```

`_content.html` includes `jobs/_bulk_actions.html` in place of the inlined
block.

### `jobs/_bulk_delete_confirm.html` (new)

Replaces `.actions` in place: "Delete N selected jobs? This cannot be
undone." + **Confirm delete** (`hx-post /jobs/bulk-delete`, target
`#jobs-content`, `hx-swap="innerHTML"` — same target/swap as `#bulk-form`
itself, since a successful bulk delete changes the job list, not just the
actions row) + **Cancel** (`hx-post /jobs/bulk-actions-cancel`, `hx-include
#bulk-form`, target `.decision-panel-left .actions`, `hx-swap="outerHTML"`).

### `base.html` (styling)

New button style, visually distinct (solid, not pastel) from the existing
Accept/Reject/Trash buttons so it reads as more consequential:

```css
.btn-delete { background: #dc3545; color: #fff; border-color: #a71d2a; }
```

No new JS needed — bulk selection (`select-all`, drag-select, `bulk-clear`,
the `:has()`-driven bulk-bar visibility) is all generic over `job_ids`
checkboxes already.

## Testing

- `tests/test_queries.py`: `delete_job` removes the row and cascades to
  `job_scores`/`scenario_feedback`; `delete_jobs` batch-deletes only rows
  with `status='trash'`, ignoring ids for other statuses in the same call.
- `tests/test_routes_jobs.py`:
  - `DELETE /jobs/{id}` on a trash job removes it (row gone, 200); on a
    non-trash job returns 400 and leaves it intact; on an unknown id, 404.
  - `GET /jobs/{id}/delete-confirm` renders the confirm partial; unknown id
    404s.
  - `POST /jobs/bulk-delete` removes only the trash-status ids in the
    posted list, leaves others untouched, and the response reflects the
    updated job list/counts.
  - `POST /jobs/bulk-delete-confirm` renders the confirm partial with the
    correct count.
  - `POST /jobs/bulk-actions-cancel` re-renders the normal bulk actions row.
  - The per-job feedback panel shows **Delete** (not **Trash**) when
    `status == "trash"`, and the bulk actions row shows **Delete** only when
    `status_filter == "trash"`.
- Manual check via `run-dev-server`: trash a job, delete it directly (confirm
  + cancel paths), trash a few more, multi-select delete them (confirm +
  cancel paths), verify Trash count updates and cascaded rows are gone.
