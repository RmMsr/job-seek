# CV workbench mechanics — design

## Goal

The per-job CV tailoring feature (`docs/superpowers/specs/2026-09-03-per-job-cv-generation-design.md`)
works, but manual testing surfaced UX and correctness problems in its
mechanics: the entry point is buried, state changes are invisible, the
directive-editing flow has a real bug, and guardrail rules are split
between hardcoded and user-editable with no way to reconcile them. This
spec fixes the mechanics. Visual redesign of the preview/change-report
content is a separate, later spec; a full page hierarchy overhaul is
deferred until these mechanics are solid.

## 1. Job-row action groups

Applies identically to the expanded job row on `/jobs` (list) and
`/jobs/{id}` (detail) — both render `jobs/_feedback.html`.

- New **Actions** group, placed before the existing decision buttons:
  contains the "Tailor CV" link, styled prominently (accent color, icon).
  Label logic is unchanged: `Tailor CV` / `Continue tailoring CV` /
  `Tailored CV ✓`, gated on `cv_enabled`.
- **Organize** group: today's Accept / Reject / Trash `.actions` bar,
  visually toned down relative to Actions (muted color, no icon) but
  otherwise unchanged.
- **Advanced…** disclosure: unchanged (Pass as new / Re-evaluate / Reset
  to new / Revisit).

## 2. Waiting-state notes

Scoped to the CV workbench (`/jobs/{id}/cv`) only, and kept deliberately
minimal — this is scaffolding for the experimental phase, not permanent
UX polish.

Two states:
- **"Updating…"** — a `cv_tailor` task is queued or running for this job.
- **"Waiting on you"** — nothing will change until the user edits
  directives or clicks Generate/Accept.

Shown as a small inline note on whichever pane (plan or preview) the
state applies to.

## 3. Guardrail findings summary

Replace the always-expanded findings list (`cv/_findings.html`) with a
one-line summary, collapsed by default:

- A small segmented bar sized by verdict counts (green/red/amber) plus
  text counts, e.g. `[██████░] 6 ok · 1 failed · 1 unclear`.
- Expands (details/summary) to today's per-rule list.
- Moves to the last position on the workbench page, full width, below
  the change report.

## 4. Directives & plan model

Manual testing found a real bug: "Generate" reads `tuning_directives`
from the database, not from the live textarea — editing the box and
clicking Generate without first clicking "Save directives" silently
discards the edit. Fix by removing the save/generate split:

- Drop the manual "Save directives" button.
- The tuning-directives textarea and scope checkboxes autosave on
  change via a silent background request — **no DOM/pane swap on
  autosave**, since swapping the pane containing the textarea would
  steal focus and cursor position out from under active typing. Only
  the persisted row is updated; the visible pane re-renders normally on
  the next explicit action (Plan/Generate/Accept).
- "Generate" always acts on current (just-saved) state.
- "Plan" / "Re-plan" unchanged: (re)computes the suggested plan and
  seeds the textarea only if it's currently empty.
- "Reset editor to proposed plan" stays, under the plan `<details>`.
- The directives hint text changes from an unexplained "(one per line)"
  to something that states why: one point per line keeps the box easy
  to scan and edit. It remains a formatting convention, not a parsed
  delimiter — nothing splits on newlines today and this doesn't change
  that.

## 5. Guardrails → settings

Today `FLOOR_RULES` (`app/cv/instruction.py`) is a hardcoded,
always-on rule list; `base_guardrails` is a separate user-editable
field layered on top. Merge them into one user-controlled field:

- CV settings gets a single "Guardrails" textarea, pre-seeded with
  today's floor text.
- One-time data migration on the existing `cv_settings` row: prepend
  the floor text ahead of any existing custom `base_guardrails`
  content, so nothing the user already wrote is lost.
- A "Reset to defaults" button overwrites the whole field with the
  floor text — explicitly destructive to any custom edits, in exchange
  for being one field and one predictable action.
- `compose_instruction()` collapses to a single "Hard limits — never
  break these" block built from the merged field, instead of a
  floor-block plus an "additional hard limits" block.

## 6. Workflow note

One muted line under the "Tailor CV" heading on the workbench page:
base CV and guardrails live in CV settings; here, Plan suggests edits →
tune them → Generate → review → Accept. Plain text, no dismiss
mechanism — this is temporary scaffolding, removed once the page
provides real guidance on its own.

## 7. Logging

The CV tailoring task path (`_task_cv_tailor` and preview rendering in
`app/routes/cv.py`) has no logging at all today. Add `logger.info`
lines timed with `time.monotonic()` around each slow step: the plan
LLM call, the generate LLM call, the guardrail-check LLM call, and
PNG/PDF rendering.

## Out of scope

- Change-report/diff redesign and preview PNG/PDF simplification —
  next spec, informed by separate research into diff representations.
- Full page information-hierarchy overhaul — deferred until the above
  mechanics are solid.

## Testing

Existing route/task tests (`tests/test_routes_cv_workbench.py`,
`tests/test_routes_cv_actions.py`, `tests/test_cv_task.py`,
`tests/test_routes_cv_settings.py`) cover the routes being changed and
should be extended rather than replaced: autosave endpoint behavior,
the dropped Save-directives route (or its replacement), the guardrails
migration/reset behavior, and the button-group markup on both list and
detail rendering paths.
