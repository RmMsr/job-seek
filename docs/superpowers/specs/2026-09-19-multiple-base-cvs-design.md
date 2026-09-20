# Multiple named base CVs

## Problem

Base CV is a hardcoded singleton (`cv_settings` row `id=1`, `cv_versions` rows
pinned to `entity_id=1`). Real use wants several base CVs for different
roles/tracks (e.g. "Backend", "Data Science"), each with its own content and
version history, selectable per job when tailoring, and deletable when no
longer wanted.

## Goals

- Base CVs become a named, listable, creatable, deletable, renamable
  collection — no longer a singleton.
- Shown as tabs on `/cv`, one row per base CV, above the existing
  Preview/Edit/Differences tab row.
- Shared tailoring-generation settings (instruction, guardrails, css, default
  scope, directives template) stay global — not per-base-CV.
- Each job tailors from a specific base CV, chosen by the user and persisted
  on the job (not just "whatever tab happens to be open").
- Deleting a base CV: if no job has ever been tailored from it, delete
  outright (the row and all its versions). If some job has, confirm first
  (surfacing how many), then delete exactly the same way — referencing jobs
  keep their tailored CV and snapshot, they just lose the live link.
- The last remaining base CV can't be deleted.

## Data model

```sql
CREATE TABLE base_cvs (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    current_version_id INTEGER REFERENCES cv_versions(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

`cv_versions` needs no schema change — `entity_type='base'` rows use
`entity_id = base_cvs.id` instead of the hardcoded `1`.

`cv_settings` drops `current_version_id` (that lives on `base_cvs` now) and
becomes purely the global settings row: `base_instruction`,
`base_guardrails`, `css`, `default_scope`, `directives_template`.

`job_cv` gains:

```sql
base_cv_id INTEGER REFERENCES base_cvs(id) ON DELETE SET NULL
```

Set whenever tailoring is (re-)run for a job (see "Routes" below); `NULL`
until then. This column is the sole "is this base CV referenced" signal for
delete — not `base_hash`/`base_cv_snapshot`, which stay untouched, purely
historical, and unrelated to it. FK enforcement is already on for the app
connection (`app/deps.py`), so `ON DELETE SET NULL` fires for free.

## Query-layer refactor

About a dozen functions in `app/db/queries.py` hardcode `entity_id = 1` /
`cv_settings id = 1` for the base CV: `resolve_base_version_id`,
`resolve_base_diff_target`, `get_accepted_base_version`,
`get_accepted_base_cv`, `resolve_base_cv`, `revert_base_cv_version`,
`accept_base_cv`, `accept_base_cv_version`, `set_base_cv`, and the
`_CV_SETTINGS_SELECT` join inside `get_cv_settings`. Each gains a
`base_cv_id` parameter and operates against `base_cvs`/`cv_versions` instead
of `cv_settings`.

`get_cv_settings` stops joining `cv_versions` — it becomes a plain read of
the (still-singleton) global settings row. A new
`get_base_cv(conn, base_cv_id) -> dict` takes over what
`_CV_SETTINGS_SELECT` used to return for the CV-content side: `name`,
`current_version_id`, `base_cv` (content), `accepted_at`,
`current_version_hash`.

New:

- `create_base_cv(conn, name) -> int` — inserts a `base_cvs` row, seeds one
  `cv_versions` row (empty content, `action='manual_edit'`) as current.
- `rename_base_cv(conn, base_cv_id, name) -> None`
- `list_base_cvs(conn) -> list[dict]` — for the tab strip (`id`, `name`,
  ordered by `id`).
- `delete_base_cv(conn, base_cv_id, force=False) -> bool` — returns `False`
  (nothing deleted) if referencing `job_cv` rows exist and `force` is not
  set. Otherwise deletes the `base_cvs` row first, then
  `cv_versions WHERE entity_type='base' AND entity_id=base_cv_id` (same
  ordering as `delete_job`: the FK-holding row goes first). Raises if this
  is the last remaining base CV (caller turns that into a 400).

## Routes

`app/routes/cv.py` currently has un-scoped `/cv`, `/cv/save-base`,
`/cv/preview.html`, `/cv/diff.html`, `/cv.pdf`, plus base-CV accept/revert in
`cv_versions.py` — all implicitly `entity_id=1`. These become base-CV-scoped,
mirroring the existing `/jobs/{job_id}/cv/...` convention:

- `GET /cv` → redirect to `/cv/{lowest-id base_cv_id}`
- `GET /cv/{base_cv_id}`, `POST /cv/{base_cv_id}/save-base`,
  `GET /cv/{base_cv_id}/preview.html`, `GET /cv/{base_cv_id}/diff.html`,
  `GET /cv/{base_cv_id}.pdf`
- `POST /cv/{base_cv_id}/versions/{version_id}/revert`,
  `POST /cv/{base_cv_id}/versions/{version_id}/accept` (renamed from today's
  `/cv/versions/{version_id}/...`), `POST /cv/{base_cv_id}/accept`
- New: `POST /cv` (create — `name` form field, redirects to the new tab),
  `POST /cv/{base_cv_id}/rename` (`name` form field),
  `DELETE /cv/{base_cv_id}` (optional `?force=true`)

Global-settings routes (`/cv/advanced`, `/cv/save-style`,
`/cv/save-guardrails`, `/cv/save-css`, the `reset-*` routes,
`/cv/save-directives-template`, `/cv/scope-options*`) are untouched — they
aren't per-base-CV.

For jobs: `_task_cv_tailor` and `_resolved_settings` need a `base_cv_id` to
resolve `settings["base_cv"]` against, sourced from `job_cv.base_cv_id`,
defaulting to the lowest-id base CV if unset (this is exactly the old
singleton's id, so existing jobs behave identically until a user explicitly
picks something else). `POST /jobs/{job_id}/cv/plan` and `/generate` don't
change shape; the workbench gains a base-CV picker that writes
`job_cv.base_cv_id` via a new `POST /jobs/{job_id}/cv/set-base`
(`base_cv_id` form field) before either is triggered.

## Delete flow

Click delete on a base CV's tab → `DELETE /cv/{base_cv_id}`:

- Backend counts `job_cv` rows with `base_cv_id = X`.
- **0** → deletes immediately (the `base_cvs` row and its `cv_versions`
  rows). Tab strip re-renders; if the deleted tab was active, switch to
  another one.
- **≥1** → doesn't delete; swaps in an inline confirm (same htmx in-place
  pattern used for job/source delete-confirm) naming how many jobs were
  tailored from it. "Confirm delete" issues
  `DELETE /cv/{base_cv_id}?force=true` — the exact same deletion as the
  0-referencing case. Referencing `job_cv` rows are untouched in content;
  they just lose the `base_cv_id` link via `ON DELETE SET NULL`.
- Last remaining base CV: the delete control is disabled/hidden (only one
  `base_cvs` row exists).

## Frontend

- New tab row (base CV names) in `cv/index.html`, above the existing
  Preview/Edit/Differences row, built the same way as
  `_base_preview_tabs.html` — same htmx-driven tab conventions. Selecting a
  tab loads the inner Preview/Edit/Differences row scoped to that
  `base_cv_id`; each tab is its own URL (`/cv/{base_cv_id}`), so browser
  back/forward and bookmarks already track "last viewed" without extra
  server state.
- "+" tab opens an inline name input; submitting creates a blank base CV and
  switches to it.
- Each tab's name is editable inline (double-click, or a small edit
  affordance) via `POST /cv/{base_cv_id}/rename`.
- Each tab gets a small delete control driving the flow above.
- The job tailoring workbench gains a base-CV `<select>` (names from
  `list_base_cvs`), defaulting to the job's `base_cv_id` if set, else the
  lowest-id base CV; changing it posts to `/jobs/{job_id}/cv/set-base`.

## Migration

Hard-downtime rebuild, consistent with project convention:

1. Create `base_cvs`; insert one row `id=1, name='Default'`,
   `current_version_id` copied from the old `cv_settings.current_version_id`.
   (`cv_versions` rows for `entity_type='base'` already use `entity_id=1` —
   no rewrite needed, they now belong to `base_cvs.id=1`.)
2. Rebuild `cv_settings` without `current_version_id`.
3. Add `job_cv.base_cv_id` (nullable, no backfill — existing jobs simply fall
   back to the default base CV at tailor time, same as any job that hasn't
   picked one yet).

## Testing

- Query layer: `create/rename/list/delete_base_cv`; `delete_base_cv`
  refusing on last-remaining and on referenced-without-force, succeeding
  with `force=True`; `ON DELETE SET NULL` behavior on `job_cv.base_cv_id`.
- Routes: tab strip renders all base CVs; create/rename/delete round-trip;
  delete-confirm vs. force-delete flow (0 vs. N referencing jobs); redirect
  from bare `/cv`; global-settings routes unaffected by which base CV is
  active.
- Tailoring: `job_cv.base_cv_id` picker persists and is what
  `_task_cv_tailor` resolves against; unset falls back to the default base
  CV.
