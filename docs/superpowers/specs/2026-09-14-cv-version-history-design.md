# CV version history

## Problem

Base CV and tailored (per-job) CV content are each stored as a single mutable
column that gets overwritten on every save. There's no way to see what a CV
looked like before the last edit or LLM regenerate, and no way to recover a
prior draft. Separately, "Accept" on a tailored CV currently freezes it
read-only until "Start over" un-freezes it — a blunt, all-or-nothing gate that
doesn't fit well once versions exist (you'd rather mark a version as accepted
and keep going).

## Goals

- Every save of base CV or tailored CV content — whether a manual edit or an
  LLM "update" — becomes a recoverable version.
- A version selector below the preview lists prior versions by timestamp;
  selecting one loads it read-only. An explicit "Revert" swaps it in as
  current.
- Rapid manual edits (autosave) within a rolling 1-hour window collapse into
  one version instead of spamming history.
- "Accept" becomes a movable marker on a specific version, not a read-only
  freeze — editing remains possible after accepting.
- The base CV gains its own accept step, and tailoring/diffing use the
  *accepted* base CV version (not whatever's currently being drafted) as
  their reference.

## Data model

One new table holds every version — including the currently-live one — for
both entity kinds:

```sql
CREATE TABLE cv_versions (
    id INTEGER PRIMARY KEY,
    hash TEXT NOT NULL UNIQUE,              -- short random hex label (e.g. 8 hex chars),
                                             -- assigned at creation; human-readable identifier,
                                             -- reserved for future use tracing an exported
                                             -- document back to the version that produced it
    entity_type TEXT NOT NULL CHECK (entity_type IN ('base','tailored')),
    entity_id INTEGER NOT NULL,             -- 1 for base (cv_settings is a singleton), job_id for tailored
    parent_version_id INTEGER REFERENCES cv_versions(id) ON DELETE SET NULL,
    content TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('update','manual_edit')),
    accepted_at TEXT,                       -- set on Accept; moves when the row is superseded
    updated_at TEXT NOT NULL                -- last write to this row; becomes its "archived at"
                                             -- timestamp once it stops being current
);
CREATE INDEX idx_cv_versions_entity ON cv_versions(entity_type, entity_id, id);
```

`cv_settings` and `job_cv` each drop their content column (`base_cv` /
`tailored_cv`) and, for `job_cv`, `finalized_at`. Both gain:

```sql
current_version_id INTEGER REFERENCES cv_versions(id)
```

`current_version_id` is the *only* pointer to "what's current" — there is no
separate live content column. `q.get_cv_settings` / `q.get_job_cv` do a
`LEFT JOIN cv_versions ON cv_versions.id = current_version_id`, aliasing
`content AS base_cv` / `AS tailored_cv` and exposing `accepted_at` and `hash`
on the returned row. This means the many existing call sites that read
`row["tailored_cv"]` / `row["base_cv"]` need no changes — only the *write*
paths change.

`base_cv_snapshot` / `base_hash` on `job_cv` (used by the existing
Differences tab and staleness checks) are untouched and unrelated to
`parent_version_id` — they keep working exactly as today, except that what
they snapshot changes (see "Base CV accept" below).

## Version-creation rules

A single helper, conceptually `record_version(conn, entity_type, entity_id, action, content)`,
backs every write:

- **`action='update'`** (LLM regenerate; tailored CV only): always inserts a
  new `cv_versions` row with `parent_version_id` = the previous
  `current_version_id`, and repoints `current_version_id` to it. Never
  stacks, regardless of timing.
- **`action='manual_edit'`**: look at the row `current_version_id` points to.
  - If its `action = 'manual_edit'`, its `accepted_at` **is NULL**, and
    `now - updated_at < 1h`: update that row's `content` and `updated_at` in
    place. No new row. This is the "stacking" case — the window rolls
    forward on every edit (each edit resets the 1h clock, so a continuous
    editing session never splits).
  - Otherwise: insert a new row (as with `update`) and repoint.

  The `accepted_at IS NULL` condition is deliberate: an accepted version
  must never be mutated in place, or "accepted" would silently stop meaning
  what it says. Editing on top of an accepted current version always opens
  a new version (with `accepted_at` unset), same as editing after the 1h
  window has lapsed.
- **Revert**: `UPDATE cv_settings/job_cv SET current_version_id = <target>`.
  Nothing else happens — no content is copied, no row is created or
  modified. The version being reverted *away* from simply stops being
  pointed to and remains in `cv_versions` unchanged, alongside whatever else
  is already there.

`parent_version_id` records the version that was current immediately before
this one became current (same entity only — a tailored version's parent is
always another tailored version, or `NULL` for the first one; same for
base). It's for lineage display ("edited from `<hash>`"), not tied to the
base/tailored diffing mechanism.

**Retention:** after every insert, delete rows for that
`(entity_type, entity_id)` beyond the most recent 10 (by `id`), excluding
whichever is current *and* whichever (if any) holds `accepted_at` — so an
older accepted version isn't silently pruned just because editing has moved
on past it. `parent_version_id` uses `ON DELETE SET NULL` so a pruned
ancestor doesn't break the surviving chain — it just shows no parent.

## Accept, and dropping the read-only freeze

Tailored CV:
- `POST /jobs/{job_id}/cv/accept`: clears `accepted_at` on any other version
  row for that job (there should be at most one), then sets it on the
  current row. Still logs the "CV accepted" job event.
- "Start over" is repurposed as **Unaccept**: clears `accepted_at` on
  whichever row holds it. It no longer touches editability.
- `_require_editable` and its 9 call sites in `app/routes/cv.py` are
  removed. Every mutating route works regardless of accept state.
- The frozen `cv/_accepted.html` branch in `workbench.html` is replaced by a
  non-blocking inline "✓ Accepted `<time>`" badge (shown whenever the
  *current* version's `accepted_at` is set) alongside an "Unaccept" button.
  The plan pane and preview pane are always shown.
- The version list highlights whichever row (current or historic) has
  `accepted_at` set — the badge stays on that specific version even after
  later edits move `current_version_id` elsewhere, since `accepted_at`
  belongs to the row, not to a flag that has to be carried over manually.

Base CV gets the same accept/unaccept mechanism (`accepted_at` is not
tailored-only). Its own editor page shows an Accept/Unaccept control and
version list identical in spirit to the tailored CV's.

**No accepted base CV version yet** (fresh install, or before the first
explicit accept): tailoring and diffing fall back to treating the current
version as accepted. Once the base CV has been accepted at least once, only
the explicit accepted version counts — an in-progress edit to the base CV
no longer silently changes what tailoring uses.

## Base CV accept feeds tailoring and diffing

A resolver, `resolve_base_cv(conn) -> (content, version_id)`, picks the
accepted base version if one exists, else the current one (per the fallback
above). This resolved content/version replaces the live
`cv_settings["base_cv"]` read in three places:

1. The LLM calls in `_task_cv_tailor` (`plan_tailoring`, `tailor_cv`) —
   tailoring is generated against the accepted base, not an in-progress
   draft.
2. `base_cv_snapshot` / `base_hash`, captured on each `generate` — now
   snapshot the resolved (accepted) base version, keeping the Differences
   tab consistent with what tailoring actually used.
3. The "Base" tab within a job's tailoring workbench
   (`cv_preview_html(variant="base")`) — renders the resolved (accepted)
   base version, so what you see there matches what tailoring used or will
   use. (The base CV's *own* editor/preview/PDF pages, at `/cv`, continue to
   show the live/current draft — that's the editing surface.)

## UI

- New partial `cv/_cv_version_list.html`, included right after the diff
  summary and before the tab bar in `_preview_pane.html` (tailored) and the
  equivalent spot on the base CV page. Lists non-current versions newest
  first: timestamp, action ("Update" / "Manual edit"), hash, and an
  "✓ Accepted" tag when applicable. Each entry links to a read-only view.
- Viewing a historic version reuses the existing `editable=false` tab-bar
  path (today's `_accepted.html`/`_preview_tabs.html` machinery) — no new
  rendering code. `GET /jobs/{job_id}/cv/preview` (and the base CV
  equivalent) becomes a general read-only full view taking an optional
  `?version=<id>`; when given, content resolves from `cv_versions` instead
  of the live row. Not persisted anywhere — purely a query param.
- `cv_preview_html`, `cv_diff_html`, and `cv_pdf` / `cv_base_pdf` all gain
  the same optional `version` query param. Diff still compares against the
  resolved (accepted) base CV, not a per-version snapshot — no new snapshot
  storage is introduced by this feature.
- "Revert to this version" appears only when viewing a non-current version;
  it posts to `/jobs/{job_id}/cv/versions/{version_id}/revert` (and the base
  CV equivalent), which repoints `current_version_id` and logs "Reverted to
  version `<hash>`" as a job event (tailored CV only — base CV has no job to
  log against).

## Migration

Hard-downtime rebuild of `cv_settings` and `job_cv` (dropping columns),
consistent with this project's migration philosophy. For each existing row,
carry its current content forward as a single new `cv_versions` row
(`action='manual_edit'`, `parent_version_id=NULL`, `accepted_at` copied from
the old `finalized_at` if present) and point `current_version_id` at it —
this moves today's value into the new structure, it does not fabricate
history. No prior versions are backfilled.
