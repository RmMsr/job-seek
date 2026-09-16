# CV tailoring: three-way iteration + labeled history

## Problem

The per-job CV workbench has two ways to change a tailored CV — the "Update"
button (LLM regenerate, scoped by the edit-scope chips + directives +
guardrails) and manual edits in the Edit tab — but no way to discard
accumulated changes and start over from the base CV. History (added in the
2026-09-14 version-history work) shows *when* something changed and what it
descended from, but not *what kind* of change it was, and carries no record
of which edit-scope levels an LLM update was allowed to use.

Separately, every "Update" today always regenerates from the base CV, so
repeated updates can't build on each other — each one re-derives the whole
CV from base + the full accumulated instruction. That makes iterating
awkward: growing the tuning directives is the only way to change output, and
a long tuning-directives outline gets harder to apply consistently in one
shot the more it accumulates.

## Goals

- Three explicit ways to iterate on a job's tailored CV: apply the tailoring
  plan/directives via LLM (existing "Update", relabeled), manual edits
  (unchanged), and reset to the base CV (new).
- Every one of the three records a `cv_versions` history entry labeled with
  what kind of change it was — "Applied plan" / "Manual edit" / "Reset" —
  and the correct parent: the base CV version for a reset, the previous
  tailored version otherwise.
- An LLM "Applied plan" entry additionally records which edit-scope levels
  were in effect, shown as the last part of its history line.
- "Apply tailoring plan" iterates from the job's current tailored CV (not
  always from base), so repeated applies compound instead of each
  re-deriving the whole document from scratch. "Reset to base CV" becomes
  the explicit way to discard that accumulated drift and restart clean.

## Non-goals

- Not resetting tuning directives/plan/handled-suggestions as part of
  "Reset to base CV" — reset only touches CV content. (Whether repeated
  "Apply tailoring plan" runs should eventually also offer to reset the
  plan is a possible follow-up, out of scope here.)
- Not changing how manual edits or plan-proposal acceptance work.
- Not changing guardrail-checking semantics — guardrails keep comparing
  against the true base CV regardless of what source `tailor_cv()` iterated
  from.

## Data model

`cv_versions.action` widens from `('update','manual_edit')` to
`('update','manual_edit','reset')`, and gains a `note` column:

```sql
CREATE TABLE cv_versions (
    id INTEGER PRIMARY KEY,
    hash TEXT NOT NULL UNIQUE,
    entity_type TEXT NOT NULL CHECK (entity_type IN ('base','tailored')),
    entity_id INTEGER NOT NULL,
    parent_version_id INTEGER REFERENCES cv_versions(id) ON DELETE SET NULL,
    content TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('update','manual_edit','reset')),
    note TEXT NOT NULL DEFAULT '',
    accepted_at TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

`note` is a free-text field, populated only on `action='update'` rows for a
tailored entity: the comma-joined names of the edit-scope options that were
enabled when that version was generated (e.g. `"Correct, Choose,
Rephrase"`), snapshotted at generation time rather than referencing the live
`cv_scope_options` rows — a scope option can later be renamed or deleted,
and the history should keep showing what was actually applied. It stays
empty for `manual_edit` and `reset` rows (neither has an edit-scope
concept), and for the base CV (which never has scope options or
`action='update'` rows — base-CV saves are always `manual_edit`).

Migration `_migrate_cv_versions_add_reset_and_note`: a single hard-downtime
rebuild of `cv_versions` (SQLite can't widen a CHECK constraint in place),
adding both the widened `action` constraint and the `note` column in one
pass — existing rows carry over with `note = ''`. Registered last in
`init_db`, after `_migrate_cv_content_to_versions`.

### Action → history label

Fixed, unambiguous mapping (no entity-type branching needed — `update` only
ever occurs for the tailored entity; the base CV only ever writes
`manual_edit`):

| `action`      | Label          |
|---------------|----------------|
| `update`      | Applied plan   |
| `manual_edit` | Manual edit    |
| `reset`       | Reset          |

## Parent linkage for reset

`_record_version`'s existing parent logic always chains to whatever was
current (`current_version_id`), falling back to `initial_parent_id` only
when the entity has no version yet. A reset needs to override that: its
parent must be the base CV version it reset *to*, even though it's not the
tailored entity's first version. `_record_version` gains an optional
`parent_id` override parameter that, when given, is used directly instead of
the current-version/initial-parent fallback.

`reset_job_cv_to_base(conn, job_id)` (new query function, modeled on
`set_job_cv_tailored`):

- Resolves the base CV the same way tailoring/diffing already do —
  `resolve_base_cv(conn)` for content, `resolve_base_version_id(conn)` for
  the version id to link as parent (the accepted base version, or the
  current one if nothing's accepted).
- Calls `_record_version(conn, "tailored", job_id, action="reset",
  content=base_content, current_version_id=<job's current>,
  parent_id=<resolved base version id>)`.
- Repoints `job_cv.current_version_id` to the new version, same as
  `set_job_cv_tailored` does.
- Leaves `scope`, `tuning_directives`, `plan`, `handled_suggestions`
  untouched — content-only reset.
- A reset when the tailored CV already equals the resolved base content is a
  no-op, via `_record_version`'s existing identical-content short-circuit —
  consistent with how manual-edit/update saves already behave.

## Routes

New: `POST /jobs/{job_id}/cv/reset-to-base` in `app/routes/cv.py`, modeled
on the revert/accept routes in `app/routes/cv_versions.py`: 404 if the job
doesn't exist, calls `q.reset_job_cv_to_base`, logs a job event ("Reset to
base CV"), redirects to `/jobs/{job_id}/cv/preview`. Plain synchronous route
(no task engine involved — it's a DB-only write, no LLM call).

## "Apply tailoring plan" sources from the current tailored CV

In `_task_cv_tailor`'s `mode == "generate"` branch (`cv.py`), the document
handed to `tailor_cv()` becomes:

```python
source_cv = row["tailored_cv"] or settings["base_cv"]
```

— the job's current tailored draft if one exists, otherwise the base CV (a
fresh job, or one that was just reset).

Because `tailor_cv()`'s prompt currently hardcodes "Base CV" as the
document being rewritten, and it will now sometimes receive an
already-tailored draft instead:

- `tailor_cv()`'s parameter renames `base_cv` → `source_cv`; its prompt's
  `"# Base CV"` section header becomes `"# CV to tailor"`.
- Its system prompt's opening line ("You rewrite a candidate's base CV so a
  busy recruiter...") drops "base": "You rewrite a candidate's CV so a busy
  recruiter...".
- `compose_instruction()`'s "Permitted edits — the ONLY kinds of change you
  may make to **the base CV**" similarly drops "base CV" → "the CV".

`check_guardrails()` is untouched: it keeps receiving the *true*
`settings["base_cv"]` as its base-CV argument (ground truth for "is this
claim actually supported"), independent of what `tailor_cv()` iterated
from — a claim introduced in an earlier apply still has to hold up against
the real base every time.

`plan_tailoring()` (the separate "Analyze and find improvements" LLM call
that proposes tuning-directive changes) is untouched — it already compares
the base CV against the job posting/notes, which is unaffected by what
`tailor_cv()` iterates from.

This means repeated "Apply tailoring plan" runs now compound on top of each
other rather than each cleanly re-deriving from base + the full
instruction. "Reset to base CV" is the explicit way to discard that
accumulated drift. Whether the tuning plan/directives should also ever be
reset automatically is left for later — out of scope here (see Non-goals).

## UI

- `_preview_pane.html`'s "Update" button is relabeled "Apply tailoring
  plan" (same `/jobs/{job_id}/cv/generate` endpoint, same
  progress-tracked button pattern).
- A new "Reset to base CV" button sits next to it — a plain `<form
  method="post" action="/jobs/{job_id}/cv/reset-to-base">`, not
  progress-tracked (synchronous, no LLM call).
- `_version_list.html`'s `version_meta()` macro gains the action label and
  note, and reorders to: age, action label, parent, note — each segment
  omitted when absent/empty. Example: `2h ago · Applied plan · from version
  ab12cd · Correct, Choose, Rephrase`. This one macro is shared by the live
  preview pane, the base CV page, and the read-only version-view pages, so
  one edit covers every history display.

## Testing

- `reset_job_cv_to_base`: parent points at the resolved base version (not
  the previous tailored version), content matches resolved base CV, is a
  no-op when content already matches, leaves scope/directives/plan/
  handled-suggestions untouched.
- `POST /jobs/{job_id}/cv/reset-to-base`: 404 on missing job, job event
  logged, redirects to the preview page.
- Migration test: existing `update`/`manual_edit` rows survive the
  `cv_versions` rebuild with `note = ''`; a `reset` row becomes insertable
  afterward.
- `tailor_cv()`/generate-branch: a job with an existing tailored CV sources
  the LLM call from that tailored CV, not from base; a fresh (or
  just-reset) job sources from base. `check_guardrails()` call still
  receives the true base CV in both cases.
- `note` snapshotting: an "Apply tailoring plan" run with scope levels
  `[correct, rephrase]` enabled records `note = "Correct, Rephrase"` (names
  in `cv_scope_options`' `sort_order` order, not the order stored in
  `job_cv.scope`); a manual edit and a reset both record `note = ''`.
- Template/label spot-check (manual): history line ordering and label text
  for all three actions, on both the live preview pane and the read-only
  version-view page.
