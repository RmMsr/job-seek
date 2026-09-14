# Pending job status — design

## Problem

There's no way to mark a job as "applied to." Once you've accepted a job and
applied, it just sits in the Accepted tab indistinguishable from jobs you
haven't applied to yet.

## Solution

Add `pending` as a new value of `jobs.status`, alongside `new` / `accepted` /
`rejected` / `trash`. It's a full peer to those statuses — same tab/count/pill
treatment, same "Mark pending" organize-action button available anywhere
Accept/Reject/Trash are, single-job and bulk. The one deliberate difference:
pending jobs are **frozen** for background processing (excluded from the
periodic revisit sweep and from scenario re-evaluation candidates), since
once you've applied there's nothing further to auto-check against the live
posting.

## Data model & migration

Widen the `jobs.status` CHECK constraint:

```sql
CHECK(status IN ('new', 'accepted', 'rejected', 'trash', 'pending'))
```

This is a pure widen, not a rename, so no data transform is needed — but
SQLite can't alter a CHECK constraint in place, so it still requires the
drop/rebuild pattern used by `_migrate_jobs_status_invalid_to_trash`
(`app/db/schema.py`): create `jobs_new` with the widened CHECK, copy rows
across unchanged, drop `jobs`, rename `jobs_new` to `jobs`.

Rebuilding `jobs` drops the `jobs_fts` triggers (`jobs_fts_ai`, `jobs_fts_ad`,
`jobs_fts_au`), since they're defined on `jobs`. Per this repo's migration
note, follow the rebuild with `INSERT INTO jobs_fts(jobs_fts) VALUES('rebuild')`
and recreate all three triggers verbatim.

The canonical `CREATE TABLE jobs` in `schema.py` also gets the widened CHECK,
so fresh installs start with the right shape.

## Backend changes

- `app/job_filter.py`: add `"pending"` to `VALID_TABS`, positioned right
  after `"accepted"`: `("new", "lead", "accepted", "pending", "rejected",
  "not_relevant", "trash")`.
- `app/db/queries.py`:
  - `_TAB_PREDICATE["pending"] = "(jobs.status = 'pending')"`.
  - `get_job_counts`: add `"pending"` to the initial counts dict and to the
    loop that counts `accepted`/`rejected`/`trash` directly by status.
  - `get_revisitable_jobs`: **unchanged** — pending jobs stay excluded
    (frozen), matching rejected/trash today.
- `app/pipeline.py`: the two `status="new"` + `status="accepted"`
  re-evaluation-candidate queries (used by the profile-refine loop) are
  **unchanged** — pending stays excluded, per the freeze decision above.
- `app/routes/jobs.py`:
  - `_stale_badge`: add a `pending` branch — `{"label": "Moved to Pending",
    "href": f"/jobs?status=pending{anchor}"}`.
  - `_SEARCH_SEED_TABS`: add `"pending"` so an unscoped search still surfaces
    pending jobs by default, same as accepted/rejected today.
  - `job_feedback` and `job_bulk_feedback`: `status in ("accepted",
    "rejected")` (unchanged — `"pending"` deliberately excluded). **Revised
    after the final review:** the initial version of this spec had
    `"pending"` also firing the reactive one-off "re-check the live
    posting" task, for uniformity with Accept/Reject. Review caught that
    this inverts the point of "pending": once you've applied, the listing
    disappearing is the *expected* outcome, not a sign something's wrong —
    but the revisit's gone/closed verdict unconditionally moves the job to
    Trash, which would bury a job the user is actively tracking. Marking
    pending no longer triggers this check at all, making the freeze (see
    above) uniform across every background-processing mechanism, not just
    the periodic sweep and re-evaluation.
- `app/routes/home.py`: the onboarding checklist's "Review your first job"
  done-check (`counts["accepted"] + counts["rejected"] + counts["trash"] >
  0`) adds `counts["pending"]`, so a user who marks a job pending directly
  from New (without first hitting Accept — the button is available
  regardless of current status, same as Accept/Reject/Trash) doesn't see
  that checklist item stay stuck incomplete.

## UI changes

- `app/templates/jobs/_feedback.html` (single-job organize actions) and
  `app/templates/jobs/_bulk_actions.html` (bulk actions bar): add
  `<button type="submit" name="status" value="pending" class="btn">Mark
  pending</button>`, positioned between Accept and Reject: **Accept → Mark
  pending → Reject → Trash**. Plain `.btn` styling — no dedicated color,
  matching how Trash looks today.
- `app/templates/jobs/_macros.html`: `status_pill` macro gets a `pending`
  branch: `<span class="status-pill status-pill-pending">Pending</span>`.
- `app/templates/jobs/_content.html`: add a tab entry right after
  `accepted`: `("pending", "Pending", "count-pending", counts.pending,
  "Jobs you've applied to and are waiting to hear back on.")`.
- `app/templates/base.html`: add
  `.status-pill-pending { background: var(--warning-tint); color:
  var(--warning); }`, reusing the existing amber warning tokens. No visual
  collision with the "Lead" pill (also warning-colored) since Lead only ever
  appears on jobs still in `new` status — a job can't be both.

## Testing

- Migration test mirroring the existing invalid→trash migration test: an
  old-shape DB gets the widened CHECK, accepts `'pending'`, and FTS search
  still works after the rebuild.
- Route tests for `POST /jobs/{id}/feedback` and `POST /jobs/bulk-feedback`
  with `status=pending`: status persists, the job gets a stale badge when it
  no longer matches the current tab, counts update, and the reactive
  revisit-on-status-change task is enqueued the same as for accept/reject.
- `job_filter.py` test: `pending` round-trips through `VALID_TABS`,
  `with_status_toggled`, and tab query params like the other statuses.

## Out of scope

- No further sub-states of "applied" (e.g. interview, offer). Just the one
  `pending` bucket.
- No automatic transition out of `pending` — the user manually re-decides
  (Accept/Reject/Trash) if/when they hear back.
