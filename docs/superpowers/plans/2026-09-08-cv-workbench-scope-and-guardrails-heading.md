# CV workbench scope + guardrails-heading polish — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Always show the "Guardrails" heading in the CV workbench findings panel, and collapse the per-job edit-scope selector to a one-line checkbox row (with a foldout for descriptions) once a tailored draft exists.

**Architecture:** Two template edits (`_findings.html`, `_plan_pane.html`) plus a small CSS block in `base.html`. No route, model, or JS changes — the condensed scope checkboxes are the same `name="scope"` inputs inside the same `#cv-directives-form`, so the existing debounced autosave covers them.

**Tech Stack:** Jinja2 templates, plain CSS in `app/templates/base.html`, pytest + FastAPI TestClient.

## Global Constraints

- All work stays in the worktree `/home/roman/projects/job-seek/.claude/worktrees/per-job-cv` — never `cd` out.
- Run tests with `python -m pytest` (not `uv run` — sandbox cache is read-only).
- Commit after each task. Append to every commit message:
  ```
  Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_019pKxu33jH8oGbDUe9qWJn9
  ```
- Do NOT stage `.superpowers/` in any commit — use each step's explicit `git add` list.
- Spec: `docs/superpowers/specs/2026-09-08-cv-workbench-scope-and-guardrails-heading-design.md`.
- `_findings.html` and `_plan_pane.html` are rendered with `_workbench_ctx` context: `job_cv` (may be None), `scope_options` (list of dicts with `id`, `name`, `description`, `default_enabled`), `updating_task_id`, `settings`.

---

### Task 1: Guardrails heading always visible

**Files:**
- Modify: `app/templates/cv/_findings.html`
- Test: `tests/test_routes_cv_workbench.py`

**Interfaces:**
- Consumes: `job_cv` (None or a dict with `guardrail_findings` list and `tailored_cv` string), `updating_task_id`.
- Produces: nothing consumed by Task 2.

- [ ] **Step 1: Write the failing tests in `tests/test_routes_cv_workbench.py`**

```python
def test_guardrails_heading_shows_without_a_draft(client, cv_on, conn):
    jid = _job(conn)
    text = client.get(f"/jobs/{jid}/cv").text
    assert 'class="guardrail-summary"' in text
    assert ">Guardrails" in text
    assert "after you generate a draft" in text


def test_guardrails_heading_and_bar_show_with_findings(client, cv_on, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft",
                    guardrail_findings=[{"rule": "No lies", "verdict": "ok", "explanation": ""}])
    text = client.get(f"/jobs/{jid}/cv").text
    assert 'class="guardrail-summary"' in text
    assert ">Guardrails" in text
    assert 'class="guardrail-bar"' in text
    assert "after you generate a draft" not in text


def test_guardrails_placeholder_when_draft_has_no_findings(client, cv_on, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft", guardrail_findings=[])
    text = client.get(f"/jobs/{jid}/cv").text
    assert "No guardrail findings recorded" in text
```

- [ ] **Step 2: Run them, verify they fail**

Run: `python -m pytest tests/test_routes_cv_workbench.py -k guardrails -q`
Expected: FAIL — the section and heading are absent without findings; the placeholder strings don't exist.

- [ ] **Step 3: Rewrite `app/templates/cv/_findings.html`**

Full new file contents:

```html
{% set findings = job_cv.guardrail_findings if job_cv else [] %}
<p class="cv-findings-stale" aria-live="polite"{% if not updating_task_id %} hidden{% endif %}>&#9203; Re-checking
  against your guardrails — results will update when the new draft is ready.</p>
{% set failed = findings | selectattr("verdict", "equalto", "violated") | list %}
{% set unclear = findings | selectattr("verdict", "equalto", "unclear") | list %}
{% set ok = findings | selectattr("verdict", "equalto", "ok") | list %}
<section class="guardrail-summary" aria-label="Guardrail check">
  <h3 style="display:flex;align-items:center;gap:.5rem;">
    Guardrails
    {% if findings %}
    <span class="guardrail-bar" role="img"
          aria-label="{{ ok|length }} ok, {{ failed|length }} failed, {{ unclear|length }} unclear">
      {% if failed %}<span class="guardrail-seg guardrail-seg-fail" style="flex:{{ failed|length }}"></span>{% endif %}
      {% if unclear %}<span class="guardrail-seg guardrail-seg-unclear" style="flex:{{ unclear|length }}"></span>{% endif %}
      {% if ok %}<span class="guardrail-seg guardrail-seg-ok" style="flex:{{ ok|length }}"></span>{% endif %}
    </span>
    {% endif %}
  </h3>

  {% if not findings %}
  <p class="muted" style="font-size:.85em;margin:0;">
    {% if job_cv and job_cv.tailored_cv %}No guardrail findings recorded — run Update to re-check.
    {% else %}Checked against your guardrails after you generate a draft.{% endif %}
  </p>
  {% endif %}

  {% if failed %}
  <details open>
    <summary><strong class="guardrail-fail">Failed</strong> ({{ failed|length }})</summary>
    <ul class="guardrail-list">
      {% for f in failed %}
      <li><strong class="guardrail-fail">FAIL</strong> {{ f.rule }}<br><span class="muted">{{ f.explanation }}</span></li>
      {% endfor %}
    </ul>
  </details>
  {% endif %}

  {% if unclear %}
  <details open>
    <summary><span class="muted">Unclear</span> ({{ unclear|length }})</summary>
    <ul class="guardrail-list">
      {% for f in unclear %}
      <li><span class="muted">??</span> {{ f.rule }}<br><span class="muted">{{ f.explanation }}</span></li>
      {% endfor %}
    </ul>
  </details>
  {% endif %}

  {% if ok %}
  <details>
    <summary><span class="guardrail-ok">OK</span> ({{ ok|length }})</summary>
    <ul class="guardrail-list">
      {% for f in ok %}
      <li><span class="guardrail-ok">ok</span> {{ f.rule }}</li>
      {% endfor %}
    </ul>
  </details>
  {% endif %}
</section>
```

Note: `failed`/`unclear`/`ok` are now computed unconditionally — on an empty
`findings` list each `selectattr` yields `[]`, which is harmless.

- [ ] **Step 4: Run the guardrails tests, verify they pass**

Run: `python -m pytest tests/test_routes_cv_workbench.py -k guardrails -q`
Expected: PASS.

- [ ] **Step 5: Run the full workbench + findings suites**

Run: `python -m pytest tests/test_routes_cv_workbench.py tests/test_cv_task.py -q`
Expected: PASS. (An existing test may assert findings-panel *absence* on a no-draft
page — if one fails because the `guardrail-summary` section now always renders,
update that assertion to check for the pass/fail `guardrail-bar` or a specific
finding row instead of the section wrapper. Name any such change in your report.)

- [ ] **Step 6: Full suite**

Run: `python -m pytest -q`
Expected: PASS (~1670 passed).

- [ ] **Step 7: Commit**

```bash
git add app/templates/cv/_findings.html tests/test_routes_cv_workbench.py
git commit -m "feat(cv): always show the Guardrails heading, with a placeholder before the first check"
```

---

### Task 2: Condensed scope selector once a draft exists

**Files:**
- Modify: `app/templates/cv/_plan_pane.html`
- Modify: `app/templates/base.html` (CSS block after the `.guardrail-list li` rule, ~line 314)
- Test: `tests/test_routes_cv_workbench.py`

**Interfaces:**
- Consumes: `job_cv` (None or dict with `tailored_cv` string and `scope` list of ints), `scope_options`, `settings.directives_template`.
- Produces: nothing.

- [ ] **Step 1: Write the failing tests in `tests/test_routes_cv_workbench.py`**

```python
def test_scope_selector_full_fieldset_without_a_draft(client, cv_on, conn):
    jid = _job(conn)
    text = client.get(f"/jobs/{jid}/cv").text
    assert "<legend>Edit scope</legend>" in text
    assert 'class="cv-scope-row"' not in text


def test_scope_selector_condensed_once_a_draft_exists(client, cv_on, conn):
    jid = _job(conn)
    opts = q.get_scope_options(conn)
    chosen, other = opts[0]["id"], opts[1]["id"]
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft", scope=[chosen])
    text = client.get(f"/jobs/{jid}/cv").text
    assert "<legend>Edit scope</legend>" not in text
    assert 'class="cv-scope-row"' in text
    assert 'class="cv-scope-help"' in text
    # the chosen option is checked in the condensed row, another is not
    assert f'value="{chosen}" checked>' in text
    assert f'value="{other}" checked>' not in text
```

The condensed `<input>` in Step 3 is written as
`... value="{{ opt.id }}"{% if opt.id in job_cv.scope %} checked{% endif %}>` so a
checked box renders exactly `value="3" checked>` — the assertion above matches
that verbatim.

- [ ] **Step 2: Run them, verify they fail**

Run: `python -m pytest tests/test_routes_cv_workbench.py -k scope_selector -q`
Expected: FAIL — `cv-scope-row` doesn't exist; the fieldset always renders.

- [ ] **Step 3: Edit `app/templates/cv/_plan_pane.html`**

At the very top of the file, before `<h2>Tailoring plan</h2>`, add:

```html
{% set has_draft = job_cv and job_cv.tailored_cv %}
```

Replace the current scope `<fieldset>` block (lines ~4-13, the whole
`<fieldset> … </fieldset>`) with:

```html
  {% if not has_draft %}
  <fieldset>
    <legend>Edit scope</legend>
    {% for opt in scope_options %}
    <label style="display:block;font-size:.9em;">
      <input type="checkbox" name="scope" value="{{ opt.id }}"
             {% if job_cv and opt.id in job_cv.scope %}checked{% elif not job_cv and opt.default_enabled %}checked{% endif %}>
      {% if opt.name %}<strong>{{ opt.name }}:</strong> {% endif %}{{ opt.description }}
    </label>
    {% endfor %}
  </fieldset>
  {% else %}
  <div class="cv-scope-row">
    {% for opt in scope_options %}
    <label class="cv-scope-chip">
      <input type="checkbox" name="scope" value="{{ opt.id }}"{% if opt.id in job_cv.scope %} checked{% endif %}>
      {{ opt.name or opt.description }}
    </label>
    {% endfor %}
  </div>
  <details class="cv-scope-help">
    <summary>Edit scope — what these mean</summary>
    <dl>
      {% for opt in scope_options %}
      <dt>{{ opt.name or "—" }}</dt><dd>{{ opt.description }}</dd>
      {% endfor %}
    </dl>
  </details>
  {% endif %}
```

Everything else in the form (the tuning-directives textarea, the help text, the
button row, the autosave `<script>`, the suggestion form, the handled-suggestions
`<details>`) is unchanged.

- [ ] **Step 4: Add CSS to `app/templates/base.html`**

Immediately after the `.guardrail-list li { margin-bottom: .3rem; }` line (~line 314):

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
```

- [ ] **Step 5: Run the scope tests, verify they pass**

Run: `python -m pytest tests/test_routes_cv_workbench.py -k scope_selector -q`
Expected: PASS. If the `checked`-token assertion is brittle, adjust it to the
actual rendered whitespace (see Step 1 note).

- [ ] **Step 6: Run the workbench + plan-pane related suites**

Run: `python -m pytest tests/test_routes_cv_workbench.py tests/test_routes_cv_actions.py tests/test_cv_task.py -q`
Expected: PASS. An existing test that posts to `/jobs/{id}/cv/save-directives` or
asserts scope-checkbox descriptions on a *drafted* job may now see the condensed
markup — if one fails, update it to match the condensed row (checkbox + name) or
seed the `job_cv` without `tailored_cv` so it stays in full mode. Name any such
change in your report.

- [ ] **Step 7: Full suite**

Run: `python -m pytest -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add app/templates/cv/_plan_pane.html app/templates/base.html tests/test_routes_cv_workbench.py
git commit -m "feat(cv): condense the edit-scope selector to a one-line row once a draft exists"
```

---

## Manual check (after both tasks)

Restart the dev server (`run-dev-server` skill, throwaway DB). On a fresh job's
Tailor CV page:
1. Before any Update: full "Edit scope" fieldset with descriptions; "Guardrails"
   heading visible with "Checked against your guardrails after you generate a draft."
2. After Update: scope collapses to a single wrapping row of `[✓] name` chips with
   an "Edit scope — what these mean" foldout; toggling a checkbox still autosaves
   (reload and confirm it stuck); Guardrails heading now shows the pass/fail bar
   and detail rows.
Hand the URL to the user.
