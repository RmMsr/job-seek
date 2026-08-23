# Scenario Fit Header Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Unify the duplicated scenario-name pills in the job detail page's "Scenario fit" section into a single pill that also acts as the tab selector, rename the save button, and add an explanatory note shown only while the section is expanded.

**Architecture:** Move the radio/label pills out of `<summary>` into a sibling `.score-pill-row` (associated to the form via the `form=""` attribute, since interactive controls inside `<summary>` fight the browser's native open/close toggle). Panel visibility switches from a CSS adjacent-sibling selector to a small delegated `change` listener, since the radios and panels are no longer DOM-adjacent. A delegated `click` listener force-opens the `<details>` on any pill click.

**Tech Stack:** FastAPI + Jinja2 templates, vanilla JS (delegated listeners already used in `base.html`), htmx, pytest + `httpx.TestClient`.

## Global Constraints

- Button label: exactly `Send collected feedback for this job` (from spec).
- Note text: exactly `How well does this job match your search criteria?` (bold) followed by ` Tuning this helps showing you only relevant new jobs.` — shown only while the section is expanded (i.e. rendered as a non-`<summary>` child of `<details>`, natively hidden when collapsed — no extra JS needed for this part).
- No backend/route changes — `routes/jobs.py`'s `scenario-feedback` handler is untouched; it already reads `scenario_id`/`note`/`direction` as lists across every panel regardless of which tab is visible.
- Full spec: `docs/superpowers/specs/2026-08-23-scenario-fit-header-design.md`.

---

### Task 1: Restructure scenario-fit templates (pill/tab unification, note, button rename)

**Files:**
- Modify: `app/templates/jobs/_score_tab_panel.html` (whole file, 14 lines)
- Modify: `app/templates/jobs/_score_tabs.html` (whole file, 30 lines)
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: `job` (has `.id`, `.top_passed_scenario_id`), `job_scores` (list of `js` with `.scenario_id`, `.scenario_name`, `.relevance_score`, `.scenario_gate_threshold`, `.score_reasoning`, `.feedback_direction`, `.feedback_note`), `saved` (bool) — all already provided by the existing `/jobs/{id}/expand` and `/jobs/{id}/scenario-feedback` routes, unchanged.
- Produces: each scenario panel now carries `data-scenario-id="{scenario_id}"` and starts with class `score-tab-panel-active` when `js.scenario_id == job.top_passed_scenario_id` — Task 2's JS relies on both the attribute name and the class name.

- [ ] **Step 1: Write the failing tests**

Open `tests/test_routes_jobs.py`. Replace the three assertions that reference the old markup/button text, and add two new tests for the note and the de-duplicated pill/panel structure.

Replace lines 222-224 (inside `test_job_expand_top_passed_scenario_tab_is_checked`):

```python
    assert resp.status_code == 200
    checked_a = (
        f'id="score-tab-{jid}-{scenario_a}" name="score-tab-{jid}" value="{scenario_a}" '
        f'class="score-tab-input" form="scenario-feedback-form-{jid}" checked'
    )
    checked_b = (
        f'id="score-tab-{jid}-{scenario_b}" name="score-tab-{jid}" value="{scenario_b}" '
        f'class="score-tab-input" form="scenario-feedback-form-{jid}" checked'
    )
    assert checked_a in resp.text
    assert checked_b not in resp.text
```

Replace line 1077 (inside `test_job_expand_save_all_feedback_button_is_visually_primary`):

```python
    assert '<button type="submit" class="btn btn-primary">Send collected feedback for this job</button>' in resp.text
```

Replace line 1108 (inside `test_job_expand_has_one_save_button_for_all_scenario_feedback`):

```python
    assert resp.text.count("Send collected feedback for this job") == 1
```

Add these two new tests right after `test_job_expand_top_passed_scenario_tab_is_checked` (i.e. after the block you just edited, before `test_job_expand_shows_gate_pass_indicator`):

```python
def test_job_expand_shows_explainer_note(client, conn):
    sid, jid, scenario_id = _seed(conn)

    resp = client.get(f"/jobs/{jid}/expand")

    assert resp.status_code == 200
    assert "How well does this job match your search criteria?" in resp.text
    assert "Tuning this helps showing you only relevant new jobs." in resp.text


def test_job_expand_scenario_name_appears_once_per_scenario(client, conn):
    # The scenario-fit header used to render each scenario's name twice: once
    # as a static summary pill, once as a separate tab-selector card. The
    # merged pill/tab element should render it exactly once.
    sid, jid, scenario_a = _seed(conn)
    scenario_b = q.insert_scenario(conn, "Other Scenario", "")
    q.upsert_job_score(conn, jid, scenario_b, 0.4, "weaker fit", "hash2")

    resp = client.get(f"/jobs/{jid}/expand")

    assert resp.status_code == 200
    assert resp.text.count("Remote ML") == 1
    assert resp.text.count("Other Scenario") == 1


def test_job_expand_panel_has_scenario_id_and_default_active_class(client, conn):
    sid, jid, scenario_a = _seed(conn)
    scenario_b = q.insert_scenario(conn, "Other Scenario", "")
    q.upsert_job_score(conn, jid, scenario_b, 0.4, "weaker fit", "hash2")

    resp = client.get(f"/jobs/{jid}/expand")

    assert resp.status_code == 200
    assert f'class="score-tab-panel score-tab-panel-active" data-scenario-id="{scenario_a}"' in resp.text
    assert f'class="score-tab-panel" data-scenario-id="{scenario_b}"' in resp.text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_routes_jobs.py -k "top_passed_scenario_tab_is_checked or save_all_feedback_button_is_visually_primary or one_save_button_for_all_scenario_feedback or explainer_note or scenario_name_appears_once or panel_has_scenario_id" -v`

Expected: all five FAIL with `AssertionError` — the three modified tests assert markup/text that the current templates don't produce yet (old radio attribute order, old button label), and the two new tests assert the note text and `data-scenario-id`/`score-tab-panel-active` attributes, neither of which exist in the current templates.

- [ ] **Step 3: Rewrite `_score_tab_panel.html`**

Replace the entire file content with:

```html
<div class="score-tab-panel{% if js.scenario_id == job.top_passed_scenario_id %} score-tab-panel-active{% endif %}" data-scenario-id="{{ js.scenario_id }}">
  {{ js.score_reasoning | markdown }}
  <input type="hidden" name="scenario_id" value="{{ js.scenario_id }}">
  <input type="hidden" name="direction" value="{{ js.feedback_direction or '' }}" class="direction-hidden">
  <div class="direction-toggle" role="group" aria-label="Score direction">
    <button type="button" class="btn direction-btn" data-direction="higher"
            aria-pressed="{{ 'true' if js.feedback_direction == 'higher' else 'false' }}">▲ Should score higher</button>
    <button type="button" class="btn direction-btn" data-direction="lower"
            aria-pressed="{{ 'true' if js.feedback_direction == 'lower' else 'false' }}">▼ Should score lower</button>
  </div>
  <label>Why should this scenario score different?
    <textarea name="note" placeholder="Optional details">{{ js.feedback_note or '' }}</textarea>
  </label>
</div>
```

(Only change from today: the `class`/`data-scenario-id` on the outer `<div>`.)

- [ ] **Step 4: Rewrite `_score_tabs.html`**

Replace the entire file content with:

```html
<section class="score-box score-compare" id="score-tabs-{{ job.id }}" aria-label="Scenario score comparison">
  {% if saved %}<p class="save-confirmation" aria-live="polite">Feedback saved.</p>{% endif %}
  <details class="score-compare-details"{% if saved %} open{% endif %}>
    <summary><span>Scenario fit</span></summary>
    <div class="score-pill-row">
      {% for js in job_scores %}
        <input type="radio" id="score-tab-{{ job.id }}-{{ js.scenario_id }}" name="score-tab-{{ job.id }}"
               value="{{ js.scenario_id }}" class="score-tab-input" form="scenario-feedback-form-{{ job.id }}"
               {% if js.scenario_id == job.top_passed_scenario_id %}checked{% endif %}>
        <label for="score-tab-{{ job.id }}-{{ js.scenario_id }}" class="score-pill{% if js.relevance_score < js.scenario_gate_threshold %} score-pill-failed{% endif %}">
          <span class="sr-only">{% if js.relevance_score >= js.scenario_gate_threshold %}Passed gate{% else %}Below gate threshold{% endif %}</span>
          <span class="tag">{{ js.scenario_name }}</span>
          <span class="score-badge score-fluid" style="{{ js.relevance_score | score_style }}">{{ "%.0f"|format(js.relevance_score * 100) }}%</span>
        </label>
      {% endfor %}
    </div>
    <p class="score-compare-note"><strong>How well does this job match your search criteria?</strong> Tuning this helps showing you only relevant new jobs.</p>
    <form id="scenario-feedback-form-{{ job.id }}" class="scenario-feedback-form" hx-post="/jobs/{{ job.id }}/scenario-feedback"
          hx-target="#score-tabs-{{ job.id }}" hx-swap="outerHTML">
      {% for js in job_scores %}
        {% include "jobs/_score_tab_panel.html" %}
      {% endfor %}
      <button type="submit" class="btn btn-primary">Send collected feedback for this job</button>
    </form>
  </details>
</section>
```

- [ ] **Step 5: Run the full job-routes test suite**

Run: `uv run pytest tests/test_routes_jobs.py -v`

Expected: PASS (all tests, including the five touched/added in Step 1 and every pre-existing test in the file — `test_job_expand_shows_tab_per_scored_scenario`, `test_job_expand_shows_gate_pass_indicator`, `test_scenario_feedback_*`, `test_job_expand_prefills_note_textarea_from_stored_feedback`, `test_job_expand_shows_scenario_feedback_form_per_tab`, `test_job_expand_has_no_scenario_select`, etc. — none of these reference the removed `.score-tab-card`/`.score-compare-pair` classes by exact string, so they should already pass against the new markup; if any fail, read the failure and fix the template, don't loosen the assertion).

- [ ] **Step 6: Run the full test suite**

Run: `uv run pytest -q`

Expected: PASS, no regressions elsewhere (e.g. `tests/test_routes_home.py` if it renders any job list snippets).

- [ ] **Step 7: Commit**

```bash
git add app/templates/jobs/_score_tab_panel.html app/templates/jobs/_score_tabs.html tests/test_routes_jobs.py
git commit -m "feat: unify scenario pill and tab selector, rename save button, add explainer note"
```

---

### Task 2: Update CSS and add interaction JS in `base.html`

**Files:**
- Modify: `app/templates/base.html:228-257` (CSS block)
- Modify: `app/templates/base.html` (new `<script>` block, inserted after the existing `direction-btn` script)

**Interfaces:**
- Consumes: `.score-pill` (label), `.score-tab-input` (radio, has `.value` = scenario id), `.score-compare-details` (the `<details>`), `.score-tab-panel` / `data-scenario-id` / `.score-tab-panel-active` — all produced by Task 1's templates.
- Produces: visible, correctly-behaving UI. No new interfaces consumed by later tasks (this is the last code task).

- [ ] **Step 1: Replace the CSS block**

In `app/templates/base.html`, find this exact block (currently lines 228-257):

```css
    .score-compare-details summary { cursor:pointer; display:flex; flex-wrap:wrap; align-items:center; gap:0.4rem; list-style:none; }
    .score-compare-details summary::-webkit-details-marker { display:none; }
    .score-compare-details summary::before { content:"▸"; color: var(--text-muted); }
    .score-compare-details[open] summary::before { content:"▾"; }
    .score-compare-details summary > span:first-child { font-weight:600; margin-right:0.2rem; color: var(--text-secondary); }
    .score-compare-details summary .score-compare-summary-scores { display:contents; }
    .score-compare-pair {
      display: inline-flex; align-items: center; gap: 0; border-radius: 999px; overflow: hidden;
      border: 1px solid var(--border);
    }
    .score-compare-pair .tag { border-radius:0; margin:0; font-size:0.85em; background: transparent; }
    .score-compare-pair .score-badge { border-radius:0; margin:0; }
    .score-pill-failed { text-decoration: line-through; text-decoration-color: var(--text-muted); opacity: 0.7; }
    .score-compare-details > form { margin-top:0.75rem; }
    .score-tabs { display:flex; flex-wrap:wrap; gap: 0.4rem; }
    .score-tab-input { position:absolute; opacity:0; width:0; height:0; }
    /* Deliberately not pill-shaped like .score-compare-pair above — this is
       a tab selector, not a second copy of the summary's scenario chips. */
    .score-tab-card {
      margin: 0 0 0.5rem; cursor:pointer; background:var(--surface); display:inline-flex; align-items:center;
      padding:0.25rem 0.7rem; border-radius:6px; border:1px solid var(--border);
      font-size:0.85em; color:var(--text-secondary);
    }
    .score-tab-card .tag { background:transparent; padding:0; border-radius:0; font-size:1em; color:inherit; }
    .score-tab-input:checked + .score-tab-card {
      border-color: var(--accent); color:var(--text-primary); background:var(--accent-tint);
      box-shadow: 0 0 0 1px var(--accent);
    }
    .score-tab-panel { display:none; width:100%; order:1; }
    .score-tab-input:checked + .score-tab-card + .score-tab-panel { display:block; }
```

Replace it with:

```css
    .score-compare-details summary { cursor:pointer; list-style:none; }
    .score-compare-details summary::-webkit-details-marker { display:none; }
    .score-compare-details summary::before { content:"▸"; color: var(--text-muted); margin-right:0.3rem; }
    .score-compare-details[open] summary::before { content:"▾"; }
    .score-compare-details summary > span:first-child { font-weight:600; color: var(--text-secondary); }
    /* Kept visible even when the <details> is closed (author styles beat the
       UA default of hiding non-summary children) — pills stay glanceable
       when folded, and are the tab selector once expanded. */
    .score-compare-details > .score-pill-row { display:flex; flex-wrap:wrap; gap:0.4rem; margin-top:0.4rem; }
    .score-pill {
      cursor:pointer; display: inline-flex; align-items: center; gap: 0; border-radius: 999px; overflow: hidden;
      border: 1px solid var(--border);
    }
    .score-pill .tag { border-radius:0; margin:0; font-size:0.85em; background: transparent; }
    .score-pill .score-badge { border-radius:0; margin:0; }
    .score-pill-failed { text-decoration: line-through; text-decoration-color: var(--text-muted); opacity: 0.7; }
    .score-compare-note { margin:0.5rem 0 0; color: var(--text-muted); font-size:0.9em; }
    .score-compare-details > form { margin-top:0.75rem; }
    .score-tab-input { position:absolute; opacity:0; width:0; height:0; }
    .score-tab-input:checked + .score-pill {
      border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent);
    }
    .score-tab-panel { display:none; }
    .score-tab-panel-active { display:block; }
```

- [ ] **Step 2: Insert the new interaction JS**

In `app/templates/base.html`, find this exact `</script>` / `<script>` boundary (the end of the `direction-btn` handler, currently around lines 735-737):

```html
});
  </script>
  <script>
document.body.addEventListener('click', function (e) {
  var btn = e.target.closest('.feedback-form .actions button[type="submit"]');
```

Replace it with (inserting a new script block between the two existing ones):

```html
});
  </script>
  <script>
document.body.addEventListener('change', function (e) {
  var input = e.target.closest('.score-tab-input');
  if (!input) return;
  var details = input.closest('.score-compare-details');
  if (!details) return;
  details.querySelectorAll('.score-tab-panel').forEach(function (p) {
    p.classList.toggle('score-tab-panel-active', p.dataset.scenarioId === input.value);
  });
});
document.body.addEventListener('click', function (e) {
  var pill = e.target.closest('.score-pill');
  if (!pill) return;
  var details = pill.closest('.score-compare-details');
  if (details) details.open = true;
});
  </script>
  <script>
document.body.addEventListener('click', function (e) {
  var btn = e.target.closest('.feedback-form .actions button[type="submit"]');
```

- [ ] **Step 3: Run the full test suite**

Run: `uv run pytest -q`

Expected: PASS — this step touches no Python-rendered assertions beyond what Task 1 already covers, so this just confirms the CSS/JS edit didn't break template rendering (e.g. no stray unclosed tag).

- [ ] **Step 4: Commit**

```bash
git add app/templates/base.html
git commit -m "style: restyle scenario pill as unified tab selector, add pill-click JS"
```

---

### Task 3: Manual verification in the browser

**Files:** none (verification only)

- [ ] **Step 1: Start the dev server against a throwaway DB copy**

Use the `run-dev-server` skill (copies `job-seek.db` into the worktree, starts with `--reload`). Do not run against the live `job-seek.db`.

- [ ] **Step 2: Open a job detail page with at least one scored scenario**

Navigate to `/jobs`, click into a job that has scenario scores (or seed one via the demo/jumpstart script if the throwaway DB is empty).

- [ ] **Step 3: Verify collapsed state**

Confirm the "Scenario fit" row shows the pills (name + score badge) even while the `<details>` is collapsed, with no second row of scenario names below it, and no explainer note visible.

- [ ] **Step 4: Verify pill click behavior**

While collapsed, click a pill for a *non*-default scenario. Confirm: the section expands, that scenario's panel (reasoning/direction toggle/note) is shown, the clicked pill is visually highlighted, and the explainer note ("How well does this job match your search criteria?...") is now visible. Click a different scenario's pill; confirm the panel and highlight switch accordingly and the section stays open.

- [ ] **Step 5: Verify fold/unfold still works via the summary label**

Click the "Scenario fit" text/chevron (not a pill) to collapse the section again; confirm it collapses and the pills remain visible per Step 3. Click it again to re-expand.

- [ ] **Step 6: Verify the submit button and save behavior**

Confirm the button reads "Send collected feedback for this job". Set a direction/note on one or more scenario panels (switching tabs as needed) and submit; confirm the page shows "Feedback saved." and the section re-expands with the same data intact (matches existing `scenario-feedback` behavior, unchanged).

- [ ] **Step 7: Report back**

Per project convention for UI-facing changes, leave the dev server running and hand the URL to the user for their own check, rather than stopping it immediately after your own pass.
