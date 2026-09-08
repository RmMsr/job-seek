# CV workbench: plan-only first run, no auto baseline draft

## Problem

On the first workbench visit for a job, `_task_cv_tailor` (mode `plan`) does three
LLM calls: `plan_tailoring`, then a conservative `tailor_cv` baseline draft, then
`check_guardrails` on that draft. The baseline draft is rarely what the user
wants and burns two extra LLM calls before they've even looked at the plan.

Separately, when a draft-producing `tailor_cv` is running, the guardrail findings
panel still shows the *previous* run's findings with no indication they're about
to be replaced.

## Changes

### 1. First run stops after the plan

`_task_cv_tailor`, `mode == "plan"` (`app/routes/cv.py`): remove the entire
baseline-draft block — the `if row is None or not cur["tailored_cv"]:` branch
(~lines 478–504) that calls `tailor_cv` → `check_guardrails` → `_persist_draft`
and sets `baseline_generated`. After persisting the plan and stamping
`plan_generated_at`, the task logs `"CV plan generated"` and returns.

- `_tailor_result` / `_rendered_chunk`: drop the `baseline_generated` parameter
  and the `["preview_pane"] if baseline_generated` branch. A `plan` run now only
  ever re-renders `plan_pane`. A `generate` run is unchanged (renders
  `preview_pane` + `plan_pane`).
- `cv_generate_task_id` (`app/db/queries.py`): drop the
  `or (p.get("mode") == "plan" and not has_draft)` clause and the `has_draft`
  lookup. Only a `mode == "generate"` task counts as "producing a tailored CV",
  so `updating_task_id` is `None` during a plan run. Update the docstring.

Result: the first workbench visit fires one LLM call (`plan_tailoring`) instead
of three. The first tailored draft is produced only when the user hits **Update**
(the existing `mode == "generate"` path, which already uses the job's default
scope + directives).

### 2. Remove `is_baseline` entirely

With the auto baseline gone, `cv_scope_options.is_baseline` has no reader (the
first manual Update already uses the default scope, not the baseline subset).

- **Schema** (`app/db/schema.py`): remove `is_baseline` from the
  `cv_scope_options` DDL. Add `_migrate_cv_scope_options_drop_is_baseline`,
  guarded by a `PRAGMA table_info(cv_scope_options)` check (same shape as
  `_migrate_job_cv_drop_preview_pages`), running
  `ALTER TABLE cv_scope_options DROP COLUMN is_baseline`. Register it in the
  migration list.
- **Defaults / seed**: `DEFAULT_SCOPE_OPTIONS` (`app/cv/instruction.py`) loses the
  `is_baseline` keys; `_seed_default_scope_options` (`app/db/queries.py`) drops
  `is_baseline` from the INSERT column list and values.
- No `/cv/advanced` UI change — `is_baseline` was never surfaced there
  (`insert_scope_option` / `update_scope_option` don't touch it, no template
  references it).

### 3. Preview pane empty state (first run, no draft)

`app/templates/cv/_preview_pane.html`, `not has_draft` branch: today it renders
`settings.base_cv` through the "Tailored" tab / raw-markdown fallback. Replace
with:

- an empty-state line: *"No tailored CV yet — review the plan on the left, then
  hit Update."*
- the **Update** button (already outside the `has_draft` guard — keep it)
- the base CV rendered below for reference, labelled **Base**, as a single view
  (reuse the existing `doc-write` / markdown render path; no tab switcher, since
  there is only one document)

Everything gated on `has_draft` today — the diff summary (`_cv_diff_summary.html`),
the Accept button, export (`_cv_export.html`), the tab switcher — stays gated and
does not render until a draft exists. Once the user hits Update and the draft
lands, the pane swaps to the full tabbed view (Tailored / Base / Differences +
Accept + findings) exactly as now.

### 4. "Guardrails will be re-checked" note

A pre-rendered, initially-hidden note revealed client-side the same way the
"update in progress" banner is.

- **`_preview_pane.html`**: render the `#cv-findings` container unconditionally
  (not gated on `has_draft`), so it exists on the very first generate.
- **`app/templates/cv/_findings.html`**: at the top, before the existing
  `{% if findings %}` block:

  ```
  <p class="cv-findings-stale"{% if not updating_task_id %} hidden{% endif %}>
    ⏳ Re-checking against your guardrails — results will update when the new
    draft is ready.
  </p>
  ```

  When prior findings exist they render below it; add a de-emphasis class to the
  `.guardrail-summary` section while the note is showing. When no prior findings
  exist, the note stands alone.
- **Server-rendered case**: a full page load while a `generate` task runs has
  `updating_task_id` truthy → the note renders visible. The finishing task's own
  re-render already passes `updating_task_id=None` through `_rendered_chunk`, so
  fresh findings show without the note.
- **Client-side reveal** (`app/templates/base.html`): extend
  `showPreviewProgress(on)` to also toggle `.cv-findings-stale`
  (`hidden = !on`) and the de-emphasis class. It is already called on the
  Update-button click and cleared by `__cvPreviewResync` on task completion — no
  new wiring.
- **Autostart**: remove `#cv-autostart` from the `showPreviewProgress` trigger
  selector (`base.html`, ~line 1398). A plan run no longer produces a draft or
  touches guardrails, so neither the preview-progress banner nor the stale note
  should fire for it.

## Tests

- `tests/test_cv_task.py`:
  - `test_plan_mode_first_visit_seeds_template_and_queues_proposals` — drop the
    "baseline still ran" / `generated_at is not None` assertions; assert
    `generated_at is None` and that the fake client saw one chat completion, not
    three.
  - `test_plan_mode_first_visit_empty_template_still_queues_proposals` — same
    adjustment.
  - `test_plan_first_visit_returns_both_pane_chunks` → rename/rework: a first
    plan run returns only the `plan_pane` chunk.
  - `test_replan_existing_draft_returns_only_plan_chunk` — still valid, keep.
- `tests/test_cv_scope_options.py` — drop `is_baseline` assertions.
- `tests/test_schema.py` / `tests/test_queries.py` — update `cv_scope_options`
  column expectations; add a migration test for
  `_migrate_cv_scope_options_drop_is_baseline` if the file has migration tests.
- `tests/test_routes_cv_workbench.py` — first visit renders the empty state
  (no "Tailored" tab, no Accept); add a case: with a `generate` task queued,
  the workbench HTML contains `cv-findings-stale`.
- New (`test_cv_task.py` or `test_queries.py`): `cv_generate_task_id` returns
  `None` for a running first-pass `plan` task.

## Out of scope

- The `2026-09-04-cv-settings-configurability-design.md` spec is historical and
  is not edited; this spec supersedes its `is_baseline` sections.
- No change to the `generate` path, the directive-evaluation flow, or the diff
  engine.
