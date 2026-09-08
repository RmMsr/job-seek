# CV directives template, scope names, advanced-form fixes

Polish pass on the tailored-CV feature. Four independent changes.

## A. Scope short names

`cv_scope_options` gains a `name` column — a short identifier (`correct`, `choose`, …).
It shows in the advanced-page editor and is prepended to the scope line in both
prompts as `- **{name}**: {description}` (plain `- {description}` when `name` is blank).

New default set (replaces the current 4 in `DEFAULT_SCOPE_OPTIONS`), wording
lightly copy-edited from the user's text, UK spelling:

| name | description | default_enabled | is_baseline |
|---|---|---|---|
| `correct` | Fix spelling errors, incorrect grammar, inconsistent naming or typography. | yes | yes |
| `choose` | Include or omit existing bullets and whole sections by relevance to this job. | yes | yes |
| `organize` | Reorder bullets and sections to emphasise key skills and requirements for this job. | yes | yes |
| `phrase` | Reword existing bullets toward the job's terminology, without introducing a claim the base CV does not already support or upgrading the scope or seniority of one. | no | no |
| `introduce` | Write a leading paragraph or professional summary conveying the personal alignment and mutual interests relevant to both applicant and organisation — without directly addressing the opportunity; leave that for a cover letter. | no | no |

**Migration** (`_migrate_cv_scope_options_add_name`):
1. `ALTER TABLE cv_scope_options ADD COLUMN name TEXT NOT NULL DEFAULT ''` (guard on `PRAGMA table_info`).
2. If the table still holds *exactly* the old 4 default descriptions (untouched):
   wipe and reseed from the new 5-set, then remap ids in `cv_settings.default_scope`
   and every `job_cv.scope` with `{1:2, 2:3, 3:4, 4:5}` (old `select/reorder/rephrase/summary`
   → new `choose/organize/phrase/introduce`; the new `correct` becomes id 1, enabled).
   If the table was customised, stop after step 1 — the user runs "Reset to defaults"
   in the UI to pick up the new set.

**Editing UI:** `_scope_options.html` gets a small `name` `<input>` per row and in
the add-row form. `insert_scope_option` / `update_scope_option` and the
`/cv/scope-options` + `/cv/scope-options/save-all` routes take `name`.

## B. Headed directives + configurable template

### Directives document format

The tuning-directives text becomes a headed markdown outline:

```
## Role relevance
- <directive bullet>
- <directive bullet>

## Skills match
- <directive bullet>
```

The *template* is just the headings, no bullets.

### `cv_settings.directives_template`

New `TEXT NOT NULL DEFAULT ''` column. `DEFAULT_DIRECTIVES_TEMPLATE` constant in
`app/cv/instruction.py`:

```
## Role relevance
## Skills match
## Content hierarchy and placement
## Requirement coverage and gaps
## Achievement evidence
## Experience and seniority
## Personal traits and transparency
## Wording and typography
```

Migration `_migrate_cv_settings_add_directives_template`: `ADD COLUMN`, then
`UPDATE cv_settings SET directives_template = <default> WHERE id = 1 AND directives_template = ''`.
`get_cv_settings` seeds it on new rows; `save_cv_settings` gains the kwarg;
the passthrough call sites in `app/routes/cv.py` forward `current["directives_template"]`.

### Evaluation pass

`plan_tailoring` + `_PLAN_SYSTEM` rewrite: the model is given the job's current
directives outline and walks it heading by heading. Under each heading it proposes:

- `add` — a directive the job calls for and the base CV can honestly support, not
  already covered by an existing bullet in that section;
- `replace` / `remove` — for an existing bullet that is vague, overreaching, or stale.

Every proposal carries `section` — the heading text it belongs under (verbatim,
without the `##`).

`DirectiveProposal`: **drop `category`** (`strengthen`/`trim`/`reframe`) from the
dataclass, the prompt JSON schema, `resolve_directive_proposals`,
`apply_directive_proposals`'s resolved dicts, and the plan-pane UI. **Add `section: str`.**

### Apply logic — section aware

`resolve_directive_proposals(proposals, directives_text)`:
- `add`: kept unless an identical bullet already exists *anywhere* in the text
  (same dedupe as today); resolved dict carries `section`.
- `replace` / `remove`: kept only if `target` matches a bullet anywhere (unchanged).

`apply_directive_proposals(directives_text, resolved)`:
- `add`: insert `- {line}` as the **last bullet under its `## {section}` heading**
  (a heading's block runs to the next `##` or EOF). If the heading is absent,
  append `\n\n## {section}\n- {line}` at the end.
- `replace` / `remove`: operate on the matched bullet in place (unchanged).
- Trailing-newline preservation as today.

New helper in `app/bullet_edits.py`: `insert_bullet_under_heading(lines, section, text)`.

### First-visit behaviour

`_task_cv_tailor` plan mode:

```
current_directives = row["tuning_directives"] if row else settings["directives_template"]
...
plan = plan_tailoring(..., tuning_directives=current_directives)
resolved = resolve_directive_proposals(plan["directives"], current_directives)
if row is None:
    q.upsert_job_cv(conn, job_id, scope=scope)
    if current_directives.strip():
        q.set_job_cv_directives(conn, job_id, current_directives)   # seed the template
q.upsert_job_cv(conn, job_id, plan=resolved, scope=scope)
```

The old `if not current_directives.strip() and resolved: set_job_cv_directives(apply(...))`
auto-apply branch is **deleted**. Proposals always land in `job_cv.plan` for review.
The baseline-draft block that follows is unchanged.

### Plan-pane review UI (`_plan_pane.html`)

The "Suggested directive changes" block groups `job_cv.plan` entries by `section`,
each group headed by the section name, each proposal a checkbox to add/apply.
`cv_accept_plan_proposals` reads `section_{i}` from the form alongside the existing
`action_/line_/target_` fields and passes it into the resolved dicts.
The `category` tag / `{{ d.category }}` rendering is removed.
`unapplied_directive_proposals` rendering drops `category` too.

### Per-job "Reset to template" button

Next to the tuning-directives textarea, a button that replaces this job's
directives with the configurable default (`cv_settings.directives_template`).
New route `POST /jobs/{job_id}/cv/reset-directives` →
`q.set_job_cv_directives(conn, job_id, settings["directives_template"])` → re-renders
the plan pane. Button uses htmx (`hx-post`, `hx-target="#cv-plan-pane"`,
`hx-swap="innerHTML"`) with `hx-confirm` since it discards the current directives.

### Note under the textarea

The muted helper line under the textarea changes from "One point per line…" to
encourage the headed pattern — e.g. "Keep the `## Heading` / `- bullet` structure;
the evaluation pass proposes directives under each heading. Saved automatically as
you type."

## C. Advanced-form quirks

### C1 — saves must not navigate away

The plain-`POST` forms on `/cv/advanced` (`save-style`, `save-guardrails`,
`save-css`, and the three reset forms) and the base-CV form on `/cv` submit via
htmx and swap in place, leaving the URL untouched. No route changes — the routes
already return the full page with `saved` / `error` in context.

- `advanced.html`: wrap the page body in `<div id="cv-advanced">`. Each form/reset-form
  gets `hx-post="<action>" hx-target="#cv-advanced" hx-select="#cv-advanced" hx-swap="outerHTML"`,
  keeping `method="post" action="…"` as the no-JS fallback.
- `index.html`: wrap in `<div id="cv-page">`; the save form gets the equivalent
  `hx-post="/cv" hx-target="#cv-page" hx-select="#cv-page" hx-swap="outerHTML"`.

### C2 — consistent "Saved." confirmation

`_scope_options.html` gets the standard
`{% if saved %}<div class="save-confirmation" aria-live="polite">Saved.</div>{% endif %}`
at the top; `save_all_scope_options_route` passes `saved=True`. The other forms
already render the block — it was just never seen because the page navigated.

### C3 — scope textareas size to content

A small global autosize helper in `base.html`:

```js
function autosize(el){ el.style.height='auto'; el.style.height=el.scrollHeight+'px'; }
document.addEventListener('DOMContentLoaded', ()=>document.querySelectorAll('textarea.autosize').forEach(autosize));
document.body.addEventListener('input', e=>{ if(e.target.matches('textarea.autosize')) autosize(e.target); });
document.body.addEventListener('htmx:load', e=>e.target.querySelectorAll?.('textarea.autosize').forEach(autosize));
```

The scope description textareas in `_scope_options.html` switch from `rows="2"` to
`rows="1" class="autosize"`. (`name` is a plain `<input>`, no autosize needed.)

## E. Preview-pane layout (workbench)

Reorder `app/templates/cv/_preview_pane.html` so the guardrail check sits directly
under the preview and the accept/download controls move below the iframe:

1. `<h2>Preview</h2>`
2. `.cv-preview-actions` — **Update button only** (+ the "doc-write-cli not installed" hint)
3. `.cv-preview-bar` — Base / Tailored tabs + Fullscreen
4. `.cv-preview-stage` — the iframe(s) + notice overlay
5. **new** — accept/download row *below the iframe*: the Accept form (or the
   "Accepted …" / finalized message when `job_cv.finalized_at`) and the Download PDF
   link, shown only when `has_draft`
6. `#cv-findings` — the "Guardrails" check (moved up, was last)
7. `#cv-change-report` — "What tailoring changed" (now last)

Fullscreen still targets `.cv-preview-stage` only — the new controls and the
guardrail section are outside it, unaffected.

Existing test `test_preview_pane_layout_header_then_update_accept_download_inline`
is rewritten: `.cv-preview-actions` now contains only Update; Accept + Download
appear after `class="cv-preview-stage"` and before `id="cv-findings"`; `#cv-findings`
precedes `#cv-change-report`.

## D. Delivery

Same `per-job-cv` worktree/branch. One squash-merge at the end.

## Out of scope

- No migration of existing per-job directives into the headed format — a job
  regenerates its outline on next evaluate. Personal single-instance app.
- No reset/undo for the directives template beyond "Reset to defaults" → the 8 headings.
- Headings and scopes stay separate concepts (confirmed).
