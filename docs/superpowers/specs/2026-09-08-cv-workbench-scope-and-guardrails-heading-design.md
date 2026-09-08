# CV workbench: always-on guardrails heading + condensed scope selector

## Problem

Two workbench polish items, both in the left "Tailoring plan" pane / findings panel:

1. The **Guardrails** heading only appears once a draft has been checked
   (`_findings.html` wraps the whole section in `{% if findings %}`). Before the
   first Update there's no sign the guardrail check exists.
2. The **Edit scope** selector shows every option's full description on its own
   line at all times. Once the user has a draft and knows what the options mean,
   that block is just noise above the tuning directives.

## Changes

### A. Guardrails heading always visible

`app/templates/cv/_findings.html`: move `<section class="guardrail-summary">` and
its `<h3>` heading out of the `{% if findings %}` guard.

- The `.guardrail-bar` span and the Failed / Unclear / OK `<details>` blocks stay
  inside `{% if findings %}` — the bar's `aria-label` is built from the
  `failed|length` / `unclear|length` / `ok|length` counts, which only exist there.
- Add an `{% else %}` branch: a single muted line under the heading.
  - `job_cv` has a `tailored_cv` → "No guardrail findings recorded — run Update to re-check."
  - otherwise → "Checked against your guardrails after you generate a draft."
- The `.cv-findings-stale` note stays first; the existing
  `.cv-findings-stale:not([hidden]) ~ .guardrail-summary { opacity: .45 }` rule is
  unchanged and now always has a sibling to dim.

No CSS change needed for A (`.guardrail-summary` / `.guardrail-summary h3` rules
already exist in `base.html`).

### B. Condensed scope selector — always

> **Revised after the first pass (same day, pre-merge):** dropped the
> `has_draft` two-mode branch. The condensed row is now the *only* view; the
> full descriptive fieldset is gone. Rationale: simpler template, and the
> descriptions are one click away in the foldout regardless of state.

`app/templates/cv/_plan_pane.html`: replace the scope `<fieldset>` with a single
always-rendered block inside `#cv-directives-form` — an inline `Edit scope:`
label, a wrapping row of checkbox + name chips, then a closed `<details>` foldout
holding the descriptions:

```html
<div class="cv-scope-row" role="group" aria-label="Edit scope">
  <span class="cv-scope-label">Edit scope:</span>
  {% for opt in scope_options %}
  <label class="cv-scope-chip">
    <input type="checkbox" name="scope" value="{{ opt.id }}"{% if job_cv and opt.id in job_cv.scope %} checked{% elif not job_cv and opt.default_enabled %} checked{% endif %}>
    {{ opt.name or opt.description }}
  </label>
  {% endfor %}
</div>
<details class="cv-scope-help">
  <summary>Descriptions</summary>
  <dl>
    {% for opt in scope_options %}
    <dt>{{ opt.name or "—" }}</dt><dd>{{ opt.description }}</dd>
    {% endfor %}
  </dl>
</details>
```

Constraints:
- Checkboxes are real `name="scope"` inputs inside `#cv-directives-form`, so the
  existing debounced autosave (`form.addEventListener("input"/"change")` in
  `_plan_pane.html`) persists changes with no new JS.
- Checkboxes are never placed inside `<summary>` — that would toggle the
  `<details>` on every check. The `<details>` holds only the read-only `<dl>`.
- `<details>` defaults closed — the user reveals the descriptions when wanted.
- The checked state: `job_cv.scope` when a `job_cv` row exists, else the option's
  `default_enabled` seed (covers the pre-first-plan visit).

New CSS in `app/templates/base.html`, next to the `.guardrail-*` rules:

```css
.cv-scope-row { display: flex; flex-wrap: wrap; align-items: center;
  gap: 0.15rem 0.85rem; font-size: 0.85em; margin-bottom: 0.3rem; }
.cv-scope-chip { display: inline-flex; align-items: center; gap: 0.25rem;
  white-space: nowrap; }
.cv-scope-help { font-size: 0.9em; margin-bottom: 0.25rem; }
.cv-scope-help summary { cursor: pointer; color: var(--text-muted); }
.cv-scope-help dl { margin: 0.4rem 0 0; }
.cv-scope-help dt { font-weight: 600; font-size: 0.85em; }
.cv-scope-help dd { margin: 0 0 0.3rem; font-size: 0.85em; color: var(--text-muted); }
.cv-scope-label { font-weight: 600; }
```

## Tests (`tests/test_routes_cv_workbench.py`)

- Guardrails heading (`>Guardrails<` inside a `guardrail-summary` section) present
  on a first visit with no `job_cv`; still present with a draft that has findings;
  muted placeholder line before the first check, suppressed while a re-check runs.
- Scope selector: `class="cv-scope-row"`, the `Edit scope:` label, and a closed
  `<details class="cv-scope-help"><summary>Descriptions</summary>` always render;
  no `<legend>Edit scope</legend>` anywhere.
- A scope checkbox is `checked` iff its option id is in `job_cv.scope` (seed
  `job_cv` with `scope=[<one id>]`, assert that id's checkbox is checked and
  another is not); the row sits inside `#cv-directives-form`.

## Out of scope

- The tuning-directives textarea, the "Evaluate directives" / "Reset to template"
  buttons, and the suggestion-review form are unchanged.
- `/cv/advanced` scope-option editing UI is unaffected.
- No change to `cv_save_directives` or any route — the form field names and POST
  target are identical.
