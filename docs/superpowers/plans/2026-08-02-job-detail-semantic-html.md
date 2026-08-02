# Job Detail Semantic HTML Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the ad hoc `<div>`/`<span>` markup in the job card and job-detail/feedback views with semantic HTML (`<article>`, `<h3>`, `<dl>`, `<section>`) via a shared Jinja macro, fixing readability gaps: the detail view loses the score, the score-reasoning block is unlabeled, the card title doesn't read as a heading, and the feedback form's scenario selector doesn't visibly indicate the best-fit scenario.

**Architecture:** A new `app/templates/jobs/_macros.html` defines `score_scenario_fields`, `score_scenario`, and `meta_tags` Jinja macros, imported by both `_row.html` (card) and `_feedback.html` (detail), guaranteeing the two views can't drift out of sync on what tags/badges they show. The detail view additionally repeats the score/scenario pair directly above the reasoning text inside a bordered `.score-box` section, and marks the best-fit option in the scenario `<select>`. No backend/query changes — `get_job()` already returns everything needed.

**Tech Stack:** FastAPI + Jinja2 (htmx partial swaps), pytest + `TestClient`, sqlite3.

## Global Constraints

- No backend/route/query changes — this is templates + CSS only (spec §3: `get_job()` already selects everything needed).
- All existing tests in `tests/test_routes_jobs.py` must continue to pass unchanged (no intentional behavior regressions); run the full file after each task.
- Commit after each task, per project CLAUDE.md "Commit frequently".
- CSS lives in the single `<style>` block in `app/templates/base.html`, matching the existing convention (no new stylesheet files).
- The `<option value="{{ s.id }}" {% if ... %}selected{% endif %}>` opening-tag format must stay byte-identical (existing test asserts the exact substring `<option value="{id}" selected>`); only the option's inner text changes.

---

### Task 1: Shared macros + card template

**Files:**
- Create: `app/templates/jobs/_macros.html`
- Modify: `app/templates/jobs/_row.html`
- Modify: `app/templates/base.html` (CSS additions)
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Produces: `app/templates/jobs/_macros.html` exports three macros, importable via `{% import "jobs/_macros.html" as macros %}`:
  - `score_scenario_fields(job)` — renders `<dt>`/`<dd>` pairs for score badge + scenario tag (no enclosing `<dl>`); renders nothing if `job.best_score` is `none`.
  - `score_scenario(job)` — wraps `score_scenario_fields(job)` in its own `<dl class="job-tags">`; renders nothing if `job.best_score` is `none`.
  - `meta_tags(job)` — one `<dl class="job-tags">` containing `score_scenario_fields(job)` plus content-type and source `<dt>`/`<dd>` pairs. This is what both `_row.html` and `_feedback.html` call for their top tag row.
- Consumes (Task 2): `score_scenario(job)` and `meta_tags(job)` from this file.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_routes_jobs.py`:

```python
def test_job_list_title_is_heading_in_its_own_block(client, conn):
    _seed(conn)
    resp = client.get("/")
    assert resp.status_code == 200
    assert '<h3 class="job-title">ML Eng</h3>' in resp.text


def test_job_list_card_is_article(client, conn):
    _seed(conn)
    resp = client.get("/")
    assert resp.status_code == 200
    assert '<article class="job-row"' in resp.text


def test_job_list_tags_are_semantic_definition_list(client, conn):
    _seed(conn)
    resp = client.get("/")
    assert resp.status_code == 200
    assert '<dl class="job-tags">' in resp.text
    assert '<dt class="sr-only">Score</dt>' in resp.text
    assert '<dt class="sr-only">Scenario</dt>' in resp.text
    assert '<dt class="sr-only">Content type</dt>' in resp.text
    assert '<dt class="sr-only">Source</dt>' in resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_routes_jobs.py -k "title_is_heading or card_is_article or semantic_definition_list" -v`
Expected: FAIL (no `<h3 class="job-title">`, no `<article`, no `<dl class="job-tags">` in current output)

- [ ] **Step 3: Create the macros file**

Create `app/templates/jobs/_macros.html`:

```jinja
{% macro score_scenario_fields(job) %}
{% if job.best_score is not none %}
{% set score = job.best_score %}
<dt class="sr-only">Score</dt>
<dd>
  {% if score >= 0.7 %}<span class="score-badge score-high">{{ "%.0f"|format(score * 100) }}%</span>
  {% elif score >= 0.4 %}<span class="score-badge score-mid">{{ "%.0f"|format(score * 100) }}%</span>
  {% else %}<span class="score-badge score-low">{{ "%.0f"|format(score * 100) }}%</span>
  {% endif %}
</dd>
<dt class="sr-only">Scenario</dt>
<dd><span class="tag">{{ job.best_scenario_name }}</span></dd>
{% endif %}
{% endmacro %}

{% macro score_scenario(job) %}
{% if job.best_score is not none %}
<dl class="job-tags">{{ score_scenario_fields(job) }}</dl>
{% endif %}
{% endmacro %}

{% macro meta_tags(job) %}
<dl class="job-tags">
  {{ score_scenario_fields(job) }}
  <dt class="sr-only">Content type</dt>
  <dd><span class="tag">{{ job.content_type or "unknown" }}</span></dd>
  <dt class="sr-only">Source</dt>
  <dd><span class="tag">{{ job.source_name or "" }}</span></dd>
</dl>
{% endmacro %}
```

- [ ] **Step 4: Rewrite the card template**

Replace the full contents of `app/templates/jobs/_row.html`:

```jinja
{% import "jobs/_macros.html" as macros %}
<article class="job-row" id="job-{{ job.id }}" role="button" tabindex="0" style="cursor:pointer"
  hx-get="/jobs/{{ job.id }}/expand"
  hx-target="#job-{{ job.id }}"
  hx-swap="outerHTML"
  hx-trigger="click, keyup[key=='Enter']">
  {{ macros.meta_tags(job) }}
  <h3 class="job-title">{{ job.title or "(no title)" }}</h3>
  {% if job.headline %}
    <p class="job-hook">{{ job.headline }}</p>
  {% elif job.summary %}
    <p class="job-hook">{{ job.summary | markdown_text | truncate(200) }}</p>
  {% endif %}
</article>
```

- [ ] **Step 5: Add CSS for the new elements**

In `app/templates/base.html`, inside the existing `<style>` block, add after the `.job-row:hover { background: #f8f9fa; }` line:

```css
.sr-only { position:absolute; width:1px; height:1px; padding:0; margin:-1px; overflow:hidden; clip:rect(0,0,0,0); white-space:nowrap; border:0; }
dl.job-tags { display:flex; flex-wrap:wrap; gap:0.5rem; align-items:center; margin:0; }
dl.job-tags dt, dl.job-tags dd { margin:0; }
.job-title { margin:0.5rem 0 0.25rem; font-size:1.1rem; }
.job-hook { margin:0.25rem 0 0; color:#444; }
```

- [ ] **Step 6: Run the new tests and the full file to check for regressions**

Run: `pytest tests/test_routes_jobs.py -v`
Expected: all tests PASS, including the three new ones and every pre-existing test (`test_job_list_returns_200`, `test_job_list_shows_scenario_tag_for_best_score`, `test_job_list_card_is_clickable_and_has_no_details_button`, `test_job_list_shows_headline_when_present`, `test_job_list_falls_back_to_summary_when_no_headline`, `test_job_list_row_omits_company_but_expand_keeps_it`, etc.)

- [ ] **Step 7: Commit**

```bash
git add app/templates/jobs/_macros.html app/templates/jobs/_row.html app/templates/base.html tests/test_routes_jobs.py
git commit -m "feat: semantic HTML for job card (article, h3 title, dl tags)"
```

---

### Task 2: Detail template — score box, parity, best-fit indicator, form spacing

**Files:**
- Modify: `app/templates/jobs/_feedback.html`
- Modify: `app/templates/base.html` (CSS additions)
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: `macros.meta_tags(job)` and `macros.score_scenario(job)` from `app/templates/jobs/_macros.html` (Task 1).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_routes_jobs.py`:

```python
def test_job_expand_has_full_meta_parity_with_card(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert '<dl class="job-tags">' in resp.text
    assert "90%" in resp.text  # score badge, not just reasoning text
    assert "Remote ML" in resp.text
    assert "job_posting" in resp.text
    assert "finn.no" in resp.text
    assert '<h3 class="job-title">ML Eng</h3>' in resp.text


def test_job_expand_score_box_repeats_score_scenario_with_reasoning(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert 'class="score-box"' in resp.text
    assert resp.text.count("90%") == 2  # once in top meta row, once in the score box
    assert resp.text.count("Remote ML") == 2
    assert "Good match" in resp.text


def test_job_expand_no_score_box_when_unscored(client, conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "http")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/2", title="No Score", company="Acme", raw_text="r")
    q.insert_scenario(conn, "First", "")
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert 'class="score-box"' not in resp.text


def test_job_expand_scenario_dropdown_marks_best_fit(client, conn):
    sid, jid, best_scenario_id = _seed(conn)
    other_id = q.insert_scenario(conn, "Other Scenario", "")
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert f'<option value="{best_scenario_id}" selected>Remote ML (Best fit)</option>' in resp.text
    assert f'<option value="{other_id}" >Other Scenario</option>' in resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_routes_jobs.py -k "meta_parity or score_box or scenario_dropdown_marks_best_fit" -v`
Expected: FAIL (no `.score-box`, no `(Best fit)` text, no score badge shown in expand view today)

- [ ] **Step 3: Rewrite the detail template**

Replace the full contents of `app/templates/jobs/_feedback.html`:

```jinja
{% import "jobs/_macros.html" as macros %}
<article class="job-row" id="job-{{ job.id }}">
  {{ macros.meta_tags(job) }}
  <h3 class="job-title">{{ job.title or "(no title)" }}</h3>
  {% if job.headline %}
    <p class="job-hook">{{ job.headline }}</p>
  {% endif %}
  <p>
    {% if job.company %}<span>· {{ job.company }}</span>{% endif %}
    <a href="{{ job.url }}" target="_blank" rel="noopener">↗ original</a>
  </p>
  {% if job.summary %}
    <section aria-label="Summary">{{ job.summary | markdown }}</section>
  {% endif %}
  {% if job.best_score_reasoning %}
    <section class="score-box" aria-label="Score reasoning">
      {{ macros.score_scenario(job) }}
      {{ job.best_score_reasoning | markdown }}
    </section>
  {% endif %}
  <form class="feedback-form" style="margin-top:0.75rem;"
    hx-post="/jobs/{{ job.id }}/feedback"
    hx-target="#job-{{ job.id }}"
    hx-swap="outerHTML">
    <label>Scenario:
      <select name="feedback_scenario_id" required>
        {% for s in scenarios %}
          <option value="{{ s.id }}" {% if s.id == job.best_scenario_id %}selected{% endif %}>{{ s.name }}{% if s.id == job.best_scenario_id %} (Best fit){% endif %}</option>
        {% endfor %}
      </select>
    </label>
    <div class="actions" role="group" aria-label="Decision">
      <button type="submit" name="status" value="accepted" class="btn btn-accept">Accept</button>
      <button type="submit" name="status" value="rejected" class="btn btn-reject">Reject</button>
      <button type="submit" name="status" value="invalid" class="btn btn-invalid">Invalid</button>
    </div>
    <label>Note (required):
      <textarea name="note" required placeholder="Why accepting/rejecting? What should change?"></textarea>
    </label>
  </form>
</article>
```

- [ ] **Step 4: Add CSS for the score box and form spacing**

In `app/templates/base.html`, inside the existing `<style>` block, add after the `.job-hook` rule added in Task 1:

```css
.score-box { margin-top:0.75rem; padding:0.75rem; border:1px solid #dee2e6; border-radius:6px; background:#f8f9fa; }
.score-box dl.job-tags { margin-bottom:0.5rem; }
.feedback-form .actions { margin-top:0.75rem; }
```

- [ ] **Step 5: Run the new tests and the full file to check for regressions**

Run: `pytest tests/test_routes_jobs.py -v`
Expected: all tests PASS, including the four new ones and every pre-existing test — in particular `test_job_expand_feedback_form_defaults_to_best_scenario` (exact `<option value="{id}" selected>` prefix must still match), `test_job_expand_feedback_form_has_no_forced_selection_when_unscored` (`"selected" not in resp.text`), `test_job_expand_shows_scenario_tag_with_reasoning`, and `test_job_list_row_omits_company_but_expand_keeps_it`.

- [ ] **Step 6: Commit**

```bash
git add app/templates/jobs/_feedback.html app/templates/base.html tests/test_routes_jobs.py
git commit -m "feat: semantic detail view with score box, meta parity, best-fit scenario marker"
```
