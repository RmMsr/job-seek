# CV workbench: decouple scope from the plan, make stage freshness visible

## Problem

The **Edit scope** selector sits at the top of the "Tailoring plan" pane, next to
*Evaluate directives*. That placement implies it drives the plan. It barely does:
scope's real bite is at **Update** (`compose_instruction` makes it the hard edit
boundary). This mismatch causes three concrete surprises:

1. **Scope resets after a plan action.** `_task_cv_tailor` (`mode="plan"`) reads
   `job_cv.scope` and writes it back (`upsert_job_cv(..., plan=resolved, scope=scope)`).
   If the 600 ms directives autosave hasn't landed when *Evaluate* is clicked, the
   plan re-persists the stale scope and the OOB re-render repaints the chips from it.
2. **Changing scope means starting over.** Because scope feeds the plan prompt
   (`# Permitted edit types`), tightening or relaxing it should really trigger a
   re-Evaluate and often pruning plan items — a heavy loop for what the user wants
   to treat as a knob ("how hard do I want to push this CV?").
3. **What's stale after a change is invisible.** The dependency chain
   (scope → draft, directives → draft, plan → directives, draft → guardrails) is
   only partially surfaced: the draft has a staleness nag, nothing else does.

## Goals

- Scope becomes a knob on the **Update** step ("edit latitude"), free to experiment
  with — spinning it invalidates only the draft, never the plan.
- The **plan is unbound by scope**: it proposes the full opportunity across every
  dimension, surfacing where the biggest gains are even if not yet licensed.
- Every workbench stage header carries a **consistent freshness indicator**.
- No plan action ever mutates `job_cv.scope`.

## Design

### 1. Decouple `plan_tailoring` from scope

- Drop the `scope` and `scope_options` parameters and the `# Permitted edit types`
  block from `plan_tailoring()` (`app/ai/tailor_cv.py`); drop the now-unused
  `scope_line` import.
- `_PLAN_SYSTEM`: remove the paragraph requiring every directive to be "achievable
  using ONLY the permitted edit types". Replace with an instruction to propose the
  full opportunity across all dimensions (lead/keep/cut, reword toward the posting's
  terms, a leading summary) — each still honestly supported by the base CV. Keep the
  handled-suggestions and untrusted-posting rules unchanged.
- `_task_cv_tailor`, `mode="plan"`: stop reading `row["scope"]` and stop passing
  `scope=` on the per-run `upsert_job_cv(..., plan=resolved)`. The plan branch may
  still **seed** `scope` to the configured default when it creates a brand-new
  `job_cv` row (same initialisation `mode="generate"` already does) — what it must
  never do is **overwrite** an existing row's scope. Net: re-evaluating a plan
  leaves a user's scope selection byte-identical.
- `compose_instruction` and `mode="generate"` are unchanged — they still read
  `job_cv.scope` and enforce it as the hard boundary. A directive that needs
  `phrase` while `phrase` is off simply doesn't fire (already handled: "apply what
  you can within the permitted edits and otherwise leave that content as it stands").

### 2. Scope → "Edit latitude" knob in the preview pane

- Remove `.cv-scope-row` and `.cv-scope-help` from `cv/_plan_pane.html`.
- Add an **Edit latitude** block to `cv/_preview_pane.html`, directly above the
  *Update* button: a short label ("Edit latitude — how far the rewrite may go"),
  the five scope chips (same `scope_options` loop, checked from `job_cv.scope` or
  `opt.default_enabled` when there's no row), and the folded "Descriptions"
  `<details>`. Reuse the existing `.cv-scope-*` CSS.
- Chips autosave immediately on `change` (no debounce — it's a checkbox) to a new
  route:

  `POST /jobs/{job_id}/cv/save-scope` → `_require_editable`, parse `scope` against
  valid ids (same filter as `cv_save_directives`), `set_job_cv_scope(job_id, scope)`
  (persists + stamps `scope_edited_at`), return `cv/_preview_pane.html` rendered
  from `_workbench_ctx` (same pattern as `cv_save_directives` returning `_plan_pane`).
  The chips are wrapped in a `<form>`; a `change` listener POSTs it and replaces
  `#cv-preview-pane` innerHTML with the response, so the draft freshness indicator
  updates in place.
- Plan pane keeps a one-line pointer where the scope row was:
  *"Suggestions cover the full opportunity — choose how much to apply with Edit
  latitude, next to the preview."*

### 3. Consistent stage freshness indicators

Every stage header renders the same small status element — a `<span class="cv-stage-status" data-state="…">` with states:

| state | meaning | copy |
|-------|---------|------|
| `none` | never run | "Not run yet" |
| `fresh` | up to date with its inputs | "Up to date" |
| `stale` | an input changed since it last ran | stage-specific, see below |
| `running` | task in flight | "Working…" |

Stages and their staleness rules:

- **Tailoring plan** (`_plan_pane` `<h2>`): `none` when `plan_generated_at` is null;
  `stale` when the job posting/notes changed since — computed from a hash of
  `_job_context(job)` + `_job_notes(conn, job)` compared to a stored
  `plan_context_hash`; `fresh` otherwise. Copy when stale: "Job posting changed —
  re-evaluate". `running` while a `mode="plan"` `cv_tailor` task is active.
- **Preview / draft** (`_preview_pane` `<h2>`): reuses `_draft_stale`, extended so it
  also returns true when `scope_edited_at > generated_at`. `none` when no
  `tailored_cv`. Stale copy: "Out of date — Update to refresh (directives or edit
  latitude changed)". `running` from `updating_task_id`.
- **Guardrails** (`_findings.html` `<h3>`): `none` when no `tailored_cv` / no
  findings; `fresh` when findings exist and the draft is fresh; `stale` when the
  draft changed since the check (i.e. the draft is stale, or `tailored_cv` changed
  after `guardrail_findings` were written — in practice the check runs inside the
  same generate task, so `stale` here tracks draft staleness). `running` from
  `updating_task_id`. Replaces the current ad-hoc `.cv-findings-stale` line and the
  "run Update to re-check" paragraph with the shared indicator + a short line.

A one-line breadcrumb at the top of `cv-workbench` (in `workbench.html`):
`Directives → Plan → Draft → Guardrails`, purely orienting (no interactivity).

Implementation notes:
- Add helpers to `_workbench_ctx`: `plan_status`, `draft_status`, `guardrail_status`
  each returning one of the state strings, so the templates stay logic-light.
- The JS that toggles `.cv-preview-progress` / `.cv-findings-stale` during a regen
  (`base.html` ~1373) updates the new `data-state` attributes instead / as well.

### 4. Saved indicator on the directives editor

- Wrap the `tuning_directives` `<textarea>` in `position: relative` container; add
  `<span class="cv-save-hint" aria-live="polite">` pinned bottom-right (inside the
  textarea's bottom padding, `pointer-events: none`).
- The autosave IIFE in `_plan_pane.html`: on `input`/`change` set text "Saving…";
  on the `fetch` resolving set "Saved" with a check, then fade out after ~2 s
  (CSS opacity transition, class toggle). On `fetch` reject: "Not saved — retrying"
  and reschedule.

### 5. Blank line between a directives heading and its bullets

- `insert_bullet_under_heading` (`app/bullet_edits.py`):
  - New-heading branch: after `lines.append(f"## {section}")` append `""` before
    the bullet.
  - Existing-heading branch: after locating `insert_at`, if the line immediately
    below the heading (`lines[hi + 1]`) is not blank, insert `""` at `hi + 1` and
    shift `insert_at` by one. Net effect: exactly one blank line between the
    heading and the first bullet; subsequent bullets append normally.
- `find_bullet` / `_heading_index` unchanged (blank lines are already skipped).

## Data / migration

Add two nullable columns to `job_cv` (hard, idempotent `ALTER TABLE ADD COLUMN`,
appended to `init_db` per project migration philosophy):

- `scope_edited_at TEXT` — stamped by `save-scope`.
- `plan_context_hash TEXT` — stamped alongside `plan_generated_at` in the plan task.

New migration fns `_migrate_job_cv_add_scope_edited_at` and
`_migrate_job_cv_add_plan_context_hash`, both guarded on `PRAGMA table_info`.

`queries.py`: `set_job_cv_directives`-style helper `set_job_cv_scope(conn, job_id,
scope)` that also stamps `scope_edited_at`; the plan task writes `plan_context_hash`
via the existing `conn.execute(... plan_generated_at ...)` statement.

## Testing (TDD)

- `test_tailor_cv_plan.py`: `plan_tailoring` has no `scope` param; the prompt has
  no "Permitted edit types" block; proposals spanning reword/summary dimensions are
  returned and not filtered.
- `test_routes_cv_actions.py`:
  - `POST /cv/plan` (and `/cv/plan/accept`, `/cv/reset-directives`) leave
    `job_cv.scope` byte-identical to its pre-call value.
  - `POST /cv/save-scope` persists scope, stamps `scope_edited_at`, and the
    returned pane shows the draft as stale when a draft already existed.
- `test_cv_instruction.py` / new `test_bullet_edits.py`:
  `insert_bullet_under_heading` puts exactly one blank line between heading and
  first bullet, for both the new-heading and existing-heading paths, and doesn't
  add a second blank line for later bullets.
- `test_routes_cv_workbench.py`: each stage header renders a `cv-stage-status` with
  the expected `data-state` for the no-draft, fresh, stale-directives,
  stale-scope, and running cases.
- `test_schema.py`: the two new columns exist after `init_db`; migration is
  idempotent on a second `init_db`.

## Out of scope

- Reworking the two-pane layout itself (approach B/C from brainstorming).
- Any change to how guardrails are computed or to `check_guardrails`.
- Plan-item "needs wider latitude" tags on individual directives (possible
  follow-up, not needed for this change).
