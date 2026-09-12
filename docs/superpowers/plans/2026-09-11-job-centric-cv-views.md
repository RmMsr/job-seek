# Job-centric CV views Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure the standalone job page into four job-centric views —
Offering, Tailor CV, Preview CV, History — sharing a common header and
subnav, replacing today's combined CV workbench (plan + preview in one page)
and the separate, header-heavy job detail page.

**Architecture:** Four full-page routes (`/jobs/{id}`, `/jobs/{id}/cv`,
`/jobs/{id}/cv/preview` [new], `/jobs/{id}/history` [new]), each rendering a
shared `jobs/_job_header.html` partial (title, organisation, tags, subnav)
followed by view-specific content split out of today's `_feedback.html`
(Offering) and `cv/workbench.html` (Tailor CV / Preview CV). Plain
`<a href>` navigation between views — no hx-boost, matching the existing
`/cv` / `/cv/advanced` subnav pattern. The job list's row-expansion
accordion (`jobs/_feedback.html` via `/jobs/{id}/expand`) is untouched.

**Tech Stack:** FastAPI + Jinja2 (server-rendered HTML), HTMX for in-page
fragment swaps, SQLite. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-11-job-centric-cv-views-design.md`

## Global Constraints

- Work happens in the worktree already set up at
  `/home/roman/projects/job-seek/.claude/worktrees/job-centric-cv-views`
  (branch `worktree-job-centric-cv-views`). Do not `cd` out of it.
- The venv is already set up (`uv sync` was run). Run tests with
  `.venv/bin/python -m pytest -q <path>`. Baseline (confirmed before this
  plan started): **1758 passed, 3 failed** — the 3 failures are in
  `tests/test_routes_home.py` (checklist wording), pre-existing and
  unrelated to this work. Do not try to fix them; if a task's changes touch
  a different test and the failure count doesn't grow beyond those 3, you're
  clean.
- Commit after each task (small, incremental commits — not one batch at the
  end).
- No database schema changes in this plan — it's a pure template/route
  reorganization.
- This is a UI-facing change. After Task 5, start the dev server (per the
  `run-dev-server` skill, against a throwaway copy of `job-seek.db`) and hand
  the URL to the user for manual verification before offering to
  merge/finish. Do not merge without the user's go-ahead.

---

## Task 1: `get_job_with_source_name` query helper

All four routes' shared header needs `job.source_name` (used by the tags
row). Today only `job_detail` computes it inline
(`sources = {s["id"]: s for s in q.get_sources(conn)}; job["source_name"] = ...`).
Factor that into one reusable query function.

**Files:**
- Modify: `app/db/queries.py` (add function after `get_job`, ~line 1090)
- Test: `tests/test_queries.py`

**Interfaces:**
- Produces: `q.get_job_with_source_name(conn: sqlite3.Connection, job_id: int) -> dict | None` — same shape as `q.get_job`, plus a `source_name` key (empty string if the source was deleted). Returns `None` if the job doesn't exist.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_queries.py` (near `test_insert_and_get_job`, ~line 331):

```python
def test_get_job_with_source_name(conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "generic_listing")
    jid = q.insert_job(conn, source_id=sid, url="http://job/1", title="T", company="C", raw_text="r")
    job = q.get_job_with_source_name(conn, jid)
    assert job["title"] == "T"
    assert job["source_name"] == "finn.no"


def test_get_job_with_source_name_missing_job_returns_none(conn):
    assert q.get_job_with_source_name(conn, 999) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_queries.py -k get_job_with_source_name -v`
Expected: FAIL with `AttributeError: module 'app.db.queries' has no attribute 'get_job_with_source_name'`

- [ ] **Step 3: Implement**

In `app/db/queries.py`, immediately after the existing `get_job` function
(~line 1090):

```python
def get_job_with_source_name(conn: sqlite3.Connection, job_id: int) -> dict | None:
    job = get_job(conn, job_id)
    if job is None:
        return None
    sources = {s["id"]: s for s in get_sources(conn)}
    job["source_name"] = sources.get(job["source_id"], {}).get("name", "")
    return job
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_queries.py -k get_job_with_source_name -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add app/db/queries.py tests/test_queries.py
git commit -m "$(cat <<'EOF'
feat(cv): add get_job_with_source_name query helper

Job-centric views need source_name for the shared header's tags row
across four different routes; factor the existing job_detail lookup
into a reusable function.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_014aDeoXumXHmUaUFpb5tx1a
EOF
)"
```

---

## Task 2: Common header + subnav, Offering view restructuring

Create the shared `jobs/_job_header.html` (title, organisation, tags,
subnav) and wire it into the standalone job page. Drop the Tailor CV CTA
button (the subnav is now the only entry point) and the click-to-collapse
behavior on the standalone page's old combined header block (dropped per
design decision — see spec; the list row's own collapse/expand is
untouched). History stays in `_feedback.html` for now — it moves out in
Task 3, paired with the new History page landing, so nothing regresses
mid-plan.

Note: the subnav will link to `/jobs/{id}/cv/preview` and `/jobs/{id}/history`,
which don't exist until Tasks 3–4. That's expected and fine within this
plan's execution — they 404 only if visited before those tasks land.

**Files:**
- Create: `app/templates/jobs/_job_header.html`
- Modify: `app/templates/jobs/detail.html`
- Modify: `app/templates/jobs/_feedback.html`
- Modify: `app/templates/base.html` (CSS)
- Modify: `app/routes/jobs.py:231-246` (`job_detail`)
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: `q.get_job_with_source_name` (Task 1); `jobs/_macros.html`'s `meta_tags(job, filter=none)` macro (existing, unchanged signature).
- Produces: `jobs/_job_header.html` — include-only partial, needs `job` (with `source_name`) and the ambient Jinja `request` in context. No macro/function API.

- [ ] **Step 1: Write the failing tests**

In `tests/test_routes_jobs.py`, add near the other `/jobs/{id}` tests
(after `test_job_detail_omits_bulk_select_checkbox`, ~line 1525):

```python
def test_job_detail_shows_common_header(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}")
    assert resp.status_code == 200
    assert '<h1 class="job-header-title">ML Eng</h1>' in resp.text
    assert '<p class="job-header-org">Acme</p>' in resp.text
    assert '<nav class="job-subnav">' in resp.text


def test_job_detail_subnav_marks_offering_active(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}")
    subnav = resp.text[resp.text.index('<nav class="job-subnav">'):resp.text.index('</nav>')]
    offering_link = subnav[subnav.index(f'href="/jobs/{jid}"'):]
    assert 'class="active"' in offering_link[:60]


def test_job_detail_subnav_links_to_all_four_views(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}")
    assert f'href="/jobs/{jid}"' in resp.text
    assert f'href="/jobs/{jid}/cv"' in resp.text
    assert f'href="/jobs/{jid}/cv/preview"' in resp.text
    assert f'href="/jobs/{jid}/history"' in resp.text
```

Replace the existing `test_job_detail_shows_tailor_cv_link` and
`test_job_detail_tailor_cv_sits_in_actions_group_before_organize` (~lines
2825-2847) with:

```python
def test_job_detail_has_no_tailor_cv_cta_button(client, conn):
    conn.execute("INSERT INTO sources (name,url,fetcher_type) VALUES ('s','http://x','manual')")
    conn.execute("INSERT INTO jobs (source_id,url,title,content_type) VALUES (1,'http://x/1','Role','job_posting')")
    conn.commit()
    r = client.get("/jobs/1")
    text = r.text
    assert "btn-tailor-cv" not in text
    assert 'aria-label="Actions"' not in text
```

Delete these three tests entirely (they test the now-dropped
click-to-collapse-in-place feature on the standalone page — see spec):
`test_job_detail_collapse_link_carries_detail_flag` (~line 1651),
`test_job_detail_collapse_keeps_checkbox_hidden` (~line 1658),
`test_job_detail_collapsed_then_reexpanded_still_hides_checkbox_and_redirect`
(~line 1666). Leave `test_job_collapse_without_detail_flag_shows_checkbox`
(the plain list-collapse test) untouched.

Delete `test_job_expand_keeps_tailor_cv_link_on_detail` (~line 208) — it
tested the CTA on the (also-dropped) `?detail=1` expand path.

- [ ] **Step 2: Run the new/changed tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_routes_jobs.py -k "common_header or subnav or no_tailor_cv_cta" -v`
Expected: FAIL — `_job_header.html` doesn't exist yet, and the CTA is still present.

- [ ] **Step 3: Create the header partial**

Create `app/templates/jobs/_job_header.html`:

```jinja
{% import "jobs/_macros.html" as macros %}
{% set p = request.url.path %}
<header class="job-header">
  <h1 class="job-header-title">{{ job.title or "(no title)" }}</h1>
  {% if job.company %}<p class="job-header-org">{{ job.company }}</p>{% endif %}
  {{ macros.meta_tags(job) }}
  <nav class="job-subnav">
    <a href="/jobs/{{ job.id }}"{% if p == "/jobs/%d"|format(job.id) %} class="active"{% endif %}>Offering</a>
    <a href="/jobs/{{ job.id }}/cv"{% if p == "/jobs/%d/cv"|format(job.id) %} class="active"{% endif %}>Tailor CV</a>
    <a href="/jobs/{{ job.id }}/cv/preview"{% if p == "/jobs/%d/cv/preview"|format(job.id) %} class="active"{% endif %}>Preview CV</a>
    <a href="/jobs/{{ job.id }}/history"{% if p == "/jobs/%d/history"|format(job.id) %} class="active"{% endif %}>History</a>
  </nav>
</header>
```

- [ ] **Step 4: Add CSS**

In `app/templates/base.html`, immediately after the existing
`.job-detail-header dl.job-tags { margin-top: 0.75rem; }` rule (~line 259),
add:

```css
    .job-header { margin-bottom: 1.25rem; }
    .job-header-title { font-family: var(--font-serif); font-weight: 600; font-size: 1.6rem; margin: 0; }
    .job-header-org { margin: 0.2rem 0 0; color: var(--text-secondary); font-size: 1rem; }
    .job-header dl.job-tags { margin-top: 0.6rem; }
    .job-subnav { display: flex; gap: 1.25rem; margin-top: 0.9rem; padding-bottom: 0.5rem; border-bottom: 1px solid var(--border); }
    .job-subnav a { text-decoration: none; color: var(--text-secondary); font-weight: 500; }
    .job-subnav a:hover { color: var(--accent); }
    .job-subnav a.active { font-weight: 600; color: var(--text-primary); }
    .job-detail-extra { padding-top: 0.9rem; }
    .back-to-list { margin-top: 1.5rem; }
```

- [ ] **Step 5: Wire the header into `jobs/detail.html`, move "Back to list"**

Replace the full contents of `app/templates/jobs/detail.html`:

```jinja
{% extends "base.html" %}
{% block title %}{{ job.title or "Job" }} — Job Seek{% endblock %}
{% block content %}
{% include "jobs/_job_header.html" %}
{% include "jobs/_feedback.html" %}
<p class="back-to-list"><a href="/jobs">&larr; Back to list</a></p>
{% endblock %}
```

- [ ] **Step 6: Split `_feedback.html`'s combined header for `is_detail_page`, drop the CTA**

In `app/templates/jobs/_feedback.html`, replace the block from
`<div class="job-detail-header" role="button" ...>` through its closing
`</div>` (lines 13-40 of the current file — everything from the header div
open tag to `{{ macros.meta_tags(...) }}</div>`) with:

```jinja
  {% if is_detail_page %}
  <div class="job-detail-extra">
    <div class="job-detail-meta">
      {% if job.fit_score is not none %}<span class="sr-only">Fit score</span>{{ macros.fit_score_badge(job) }}{% endif %}
      {% if job.company %}<span>{{ job.company }}</span>{% endif %}
      {% if job.published_at %}<span class="job-age">{{ job.published_at | time_ago }}</span>{% endif %}
      <a href="{{ job.url }}" target="_blank" rel="noopener">&#8599; original</a>
    </div>
    {% if job.stale_badge %}
      <p class="stale-note">
        {% if job.stale_badge.href %}
          <a href="{{ job.stale_badge.href }}">{{ job.stale_badge.label }} &rarr;</a>
        {% else %}
          <span>{{ job.stale_badge.label }}</span>
        {% endif %}
      </p>
    {% endif %}
    {% if job.headline %}
      <p class="job-hook">{{ job.headline }}</p>
    {% endif %}
  </div>
  {% else %}
  <div class="job-detail-header" role="button" tabindex="0" style="cursor:pointer"
    hx-get="/jobs/{{ job.id }}/collapse{{ macros.qsuffix(filter, detail=is_detail_page|default(false)) }}"
    hx-target="#job-{{ job.id }}"
    hx-swap="outerHTML transition:true"
    hx-trigger="click, keyup[key=='Enter']">
    <div class="job-detail-title-row">
      <h2 class="job-detail-title">{{ job.title or "(no title)" }}</h2>
      {% if job.published_at %}<span class="job-age">{{ job.published_at | time_ago }}</span>{% endif %}
    </div>
    <div class="job-detail-meta">
      {% if job.fit_score is not none %}<span class="sr-only">Fit score</span>{{ macros.fit_score_badge(job) }}{% endif %}
      {% if job.company %}<span>{{ job.company }}</span>{% endif %}
      <a href="{{ job.url }}" target="_blank" rel="noopener" onclick="event.stopPropagation()">&#8599; original</a>
    </div>
    {% if job.stale_badge %}
      <p class="stale-note">
        {% if job.stale_badge.href %}
          <a href="{{ job.stale_badge.href }}" onclick="event.stopPropagation()">{{ job.stale_badge.label }} &rarr;</a>
        {% else %}
          <span>{{ job.stale_badge.label }}</span>
        {% endif %}
      </p>
    {% endif %}
    {% if job.headline %}
      <p class="job-hook">{{ job.headline }}</p>
    {% endif %}
    {{ macros.meta_tags(job, filter=filter if filter is defined else none) }}
  </div>
  {% endif %}
```

Then find the Tailor CV action group (still further down the file, inside
the `<form class="feedback-form">`, right after the job-history `<details>`
block):

```jinja
    <div class="job-action-group">
      <span class="job-actions-group-label">Actions</span>
      <div class="cv-actions-group" role="group" aria-label="Actions">
        <a class="btn btn-primary btn-tailor-cv" href="/jobs/{{ job.id }}/cv"
           title="Generate a CV tailored to this job.">
          <span aria-hidden="true">&#128196;</span>
          {% if job_cv and job_cv.finalized_at %}Tailored CV &#10003;{% elif job_cv and job_cv.tailored_cv %}Continue tailoring CV{% else %}Tailor CV{% endif %}
        </a>
      </div>
    </div>
```

Wrap it in `{% if not is_detail_page %}...{% endif %}`:

```jinja
    {% if not is_detail_page %}
    <div class="job-action-group">
      <span class="job-actions-group-label">Actions</span>
      <div class="cv-actions-group" role="group" aria-label="Actions">
        <a class="btn btn-primary btn-tailor-cv" href="/jobs/{{ job.id }}/cv"
           title="Generate a CV tailored to this job.">
          <span aria-hidden="true">&#128196;</span>
          {% if job_cv and job_cv.finalized_at %}Tailored CV &#10003;{% elif job_cv and job_cv.tailored_cv %}Continue tailoring CV{% else %}Tailor CV{% endif %}
        </a>
      </div>
    </div>
    {% endif %}
```

Do not touch anything else in this file — the `{% if not is_detail_page %}`
checkbox block, the permalink `<a>`, the summary/score-tabs/scorecard
sections, the note form, the history `<details>` (still gated only by
`job_events`, unchanged until Task 3), the Organize actions, and the
Advanced `<details>` all stay exactly as they are.

- [ ] **Step 7: Update the `job_detail` route**

In `app/routes/jobs.py`, replace the `job_detail` function (~lines
231-246):

```python
@router.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(job_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    job = q.get_job(conn, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    sources = {s["id"]: s for s in q.get_sources(conn)}
    job["source_name"] = sources.get(job["source_id"], {}).get("name", "")
    scenarios = q.get_scenarios(conn)
    job_scores = q.get_job_scores(conn, job_id)
    job_events = q.get_job_events(conn, job_id)
    return templates.TemplateResponse(
        request, "jobs/detail.html",
        {"job": job, "scenarios": scenarios, "job_scores": job_scores,
         "job_events": job_events, "is_detail_page": True, "filter": None,
         "job_cv": q.get_job_cv(conn, job_id)},
    )
```

with:

```python
@router.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(job_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    job = q.get_job_with_source_name(conn, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    scenarios = q.get_scenarios(conn)
    job_scores = q.get_job_scores(conn, job_id)
    job_events = q.get_job_events(conn, job_id)
    return templates.TemplateResponse(
        request, "jobs/detail.html",
        {"job": job, "scenarios": scenarios, "job_scores": job_scores,
         "job_events": job_events, "is_detail_page": True, "filter": None,
         "job_cv": q.get_job_cv(conn, job_id)},
    )
```

(`job_events` stays for now — Task 3 removes it when history moves out.)

- [ ] **Step 8: Run the full jobs test file**

Run: `.venv/bin/python -m pytest tests/test_routes_jobs.py -v`
Expected: PASS (all tests, including the new/changed ones from Step 1; the
3 deleted collapse tests and the 1 deleted CTA-on-expand test are gone from
the count)

- [ ] **Step 9: Run the full suite to check for regressions**

Run: `.venv/bin/python -m pytest -q`
Expected: same 3 pre-existing `test_routes_home.py` failures, nothing new

- [ ] **Step 10: Commit**

```bash
git add app/templates/jobs/_job_header.html app/templates/jobs/detail.html \
        app/templates/jobs/_feedback.html app/templates/base.html \
        app/routes/jobs.py tests/test_routes_jobs.py
git commit -m "$(cat <<'EOF'
feat(cv): shared job header + subnav, drop Tailor CV CTA and detail-page collapse

Title/organisation/tags now render once via jobs/_job_header.html,
shared across the four job-centric views (subnav links to Tailor CV,
Preview CV, History). The standalone job page's click-to-collapse
toggle is dropped — it doesn't fit the new static header — and the
big Tailor CV button is replaced by the subnav tab.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_014aDeoXumXHmUaUFpb5tx1a
EOF
)"
```

---

## Task 3: History view

New standalone page for the job-events list, replacing the `<details>`
block currently nested in the Offering view's feedback form.

**Files:**
- Create: `app/templates/jobs/history.html`
- Modify: `app/routes/jobs.py` (new route, after `job_detail`)
- Modify: `app/templates/jobs/_feedback.html` (gate the history `<details>` to list-row only)
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: `q.get_job_with_source_name` (Task 1), `q.get_job_events(conn, job_id)` (existing).
- Produces: `GET /jobs/{job_id}/history` — 200 with `jobs/history.html`, 404 if job missing.

- [ ] **Step 1: Write the failing tests**

Replace `test_job_detail_shows_history_block` and
`test_job_detail_no_history_block_when_empty` (~lines 2800-2816) in
`tests/test_routes_jobs.py` with:

```python
def test_job_history_page_lists_events(client, conn):
    sid, jid, scenario_id = _seed(conn)
    q.update_job_feedback(conn, jid, "rejected", "not remote")
    resp = client.get(f"/jobs/{jid}/history")
    assert resp.status_code == 200
    assert "Status: new → rejected" in resp.text
    assert 'Status: new → rejected — ' not in resp.text


def test_job_history_page_empty_state(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}/history")
    assert resp.status_code == 200
    assert "No history yet" in resp.text


def test_job_history_page_404_for_missing_job(client, conn):
    assert client.get("/jobs/999/history").status_code == 404


def test_job_detail_offering_page_has_no_history_block(client, conn):
    sid, jid, scenario_id = _seed(conn)
    q.update_job_feedback(conn, jid, "rejected", "not remote")
    resp = client.get(f"/jobs/{jid}")
    assert 'class="job-history"' not in resp.text
    assert "Status: new → rejected" not in resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_routes_jobs.py -k job_history -v`
Expected: FAIL — 404 (no route yet) for the first three, and the fourth
still fails because history is still shown on the Offering page.

- [ ] **Step 3: Create `jobs/history.html`**

```jinja
{% extends "base.html" %}
{% block title %}History — {{ job.title or "Job" }}{% endblock %}
{% block content %}
{% include "jobs/_job_header.html" %}
<h1>History</h1>
{% if job_events %}
<ul class="job-history-list">
  {% for e in job_events %}
  <li><time datetime="{{ e.created_at }}" title="{{ e.created_at }}">{{ e.created_at[:10] }}</time> {{ e.message }}</li>
  {% endfor %}
</ul>
{% else %}
<p class="muted">No history yet.</p>
{% endif %}
{% endblock %}
```

- [ ] **Step 4: Add the route**

In `app/routes/jobs.py`, add immediately after the `job_detail` function:

```python
@router.get("/jobs/{job_id}/history", response_class=HTMLResponse)
def job_history(job_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    job = q.get_job_with_source_name(conn, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    job_events = q.get_job_events(conn, job_id)
    return templates.TemplateResponse(
        request, "jobs/history.html", {"job": job, "job_events": job_events},
    )
```

- [ ] **Step 5: Remove history from the Offering view**

In `app/templates/jobs/_feedback.html`, find:

```jinja
    {% if job_events %}
    <details class="job-history">
      <summary>History ({{ job_events | length }})</summary>
      <ul class="job-history-list">
        {% for e in job_events %}
        <li><time datetime="{{ e.created_at }}" title="{{ e.created_at }}">{{ e.created_at[:10] }}</time> {{ e.message }}</li>
        {% endfor %}
      </ul>
    </details>
    {% endif %}
```

Replace with:

```jinja
    {% if not is_detail_page and job_events %}
    <details class="job-history">
      <summary>History ({{ job_events | length }})</summary>
      <ul class="job-history-list">
        {% for e in job_events %}
        <li><time datetime="{{ e.created_at }}" title="{{ e.created_at }}">{{ e.created_at[:10] }}</time> {{ e.message }}</li>
        {% endfor %}
      </ul>
    </details>
    {% endif %}
```

Now `job_detail`'s `job_events` context value is genuinely unused (it only
ever renders `_feedback.html` with `is_detail_page=True`). In
`app/routes/jobs.py`, simplify `job_detail` (from Task 2's version):
remove the `job_events = q.get_job_events(conn, job_id)` line and the
`"job_events": job_events,` context entry.

- [ ] **Step 6: Run the history tests**

Run: `.venv/bin/python -m pytest tests/test_routes_jobs.py -k "job_history or job_detail" -v`
Expected: PASS

- [ ] **Step 7: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: same 3 pre-existing `test_routes_home.py` failures, nothing new

- [ ] **Step 8: Commit**

```bash
git add app/templates/jobs/history.html app/routes/jobs.py app/templates/jobs/_feedback.html \
        tests/test_routes_jobs.py
git commit -m "$(cat <<'EOF'
feat(cv): add standalone History view for job events

Pulls the job-events list out of the Offering view's collapsed
<details> into its own page at /jobs/{id}/history, reachable from the
shared subnav.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_014aDeoXumXHmUaUFpb5tx1a
EOF
)"
```

---

## Task 4: Preview CV view, trim Tailor CV to plan-only

The biggest task: split `cv/workbench.html` into a plan-only Tailor CV page
and a new Preview CV page carrying the preview pane (which already includes
guardrails) and the accepted/read-only view. Update the two places that
assumed a single combined page (the `cv_accept` redirect target, and
`_tailor_result`'s OOB rendering), then migrate every affected test.

**Files:**
- Create: `app/templates/cv/preview.html`
- Modify: `app/templates/cv/workbench.html`
- Modify: `app/routes/cv.py` (`_workbench_ctx`, `_tailor_result`, `cv_accept`, new `cv_preview_page` route)
- Create: `tests/test_routes_cv_tailor.py`
- Create: `tests/test_routes_cv_preview.py`
- Rewrite (trim): `tests/test_routes_cv_workbench.py`
- Modify: `tests/test_routes_cv_actions.py`
- Modify: `tests/test_cv_task.py`

**Interfaces:**
- Consumes: `_workbench_ctx(conn, job_id)` (existing, now sources `job` via `q.get_job_with_source_name`).
- Produces: `GET /jobs/{job_id}/cv/preview` — 200 with `cv/preview.html`, 404 if job missing. `cv_accept` now redirects to `/jobs/{job_id}/cv/preview` (was `/jobs/{job_id}/cv`).

- [ ] **Step 1: Update `_workbench_ctx` to use the shared job lookup**

In `app/routes/cv.py`, in `_workbench_ctx` (~line 230), change:

```python
    job = q.get_job(conn, job_id)
```

to:

```python
    job = q.get_job_with_source_name(conn, job_id)
```

- [ ] **Step 2: Simplify `_tailor_result` — drop the cross-pane plan_pane OOB**

In `app/routes/cv.py`, replace `_tailor_result` (~line 197):

```python
def _tailor_result(conn: sqlite3.Connection, job_id: int, params: dict) -> dict:
    result: dict = {"job_id": job_id}
    which = params.get("render")
    if which:
        # This can run from inside a finishing cv_tailor task (either the 'generate'
        # or the 'plan' mode) — it's still 'running' in the DB, so _workbench_ctx
        # would see it as an in-flight regen. It isn't: the updating_task_id=None
        # override keeps this render from painting the pane as still-updating.
        panes = ["preview_pane", "plan_pane"] if which == "preview_pane" else [which]
        chunks = []
        for p in panes:
            overrides = {"updating_task_id": None}
            if p == "plan_pane":
                overrides["plan_task_id"] = None
            chunks.append(_rendered_chunk(conn, job_id, p, **overrides))
        result["html_chunks"] = chunks
    return result
```

with:

```python
def _tailor_result(conn: sqlite3.Connection, job_id: int, params: dict) -> dict:
    result: dict = {"job_id": job_id}
    which = params.get("render")
    if which:
        # This can run from inside a finishing cv_tailor task (either the 'generate'
        # or the 'plan' mode) — it's still 'running' in the DB, so _workbench_ctx
        # would see it as an in-flight regen. It isn't: the updating_task_id=None
        # override keeps this render from painting the pane as still-updating.
        overrides = {"updating_task_id": None}
        if which == "plan_pane":
            overrides["plan_task_id"] = None
        result["html_chunks"] = [_rendered_chunk(conn, job_id, which, **overrides)]
    return result
```

(Tailor CV and Preview CV are separate pages now — a generate no longer
needs to also refresh the off-page plan pane.)

- [ ] **Step 3: Create `cv/preview.html`**

```jinja
{% extends "base.html" %}
{% block title %}Preview CV — {{ job.title or "Job" }}{% endblock %}
{% block content %}
{% include "jobs/_job_header.html" %}

{% if job_cv and job_cv.finalized_at %}
{% include "cv/_accepted.html" %}
{% else %}
<div id="cv-preview-pane">{% include "cv/_preview_pane.html" %}</div>
{% endif %}

{% if updating_task_id %}
<script>
  // A regen was already running when this page loaded (e.g. a mid-update
  // reload). Reattach so the preview pane still refreshes when it finishes.
  document.addEventListener("DOMContentLoaded", function () {
    if (window.__cvWatchGenerate) window.__cvWatchGenerate({{ updating_task_id }});
  });
</script>
{% endif %}
{% endblock %}
```

- [ ] **Step 4: Add the `cv_preview_page` route**

In `app/routes/cv.py`, immediately after `cv_workbench` (~line 256), add:

```python
@router.get(
    "/jobs/{job_id}/cv/preview", response_class=HTMLResponse)
def cv_preview_page(job_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    if q.get_job(conn, job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return templates.TemplateResponse(request, "cv/preview.html", _workbench_ctx(conn, job_id))
```

- [ ] **Step 5: Trim `cv/workbench.html` to plan-only + finalized notice**

Replace the full contents of `app/templates/cv/workbench.html`:

```jinja
{% extends "base.html" %}
{% block title %}Tailor CV — {{ job.title or "Job" }}{% endblock %}
{% block content %}
{% include "jobs/_job_header.html" %}

{% if job_cv and job_cv.finalized_at %}
<p class="muted" style="margin:.25rem 0 .75rem;">
  &#10003; Accepted {{ job_cv.finalized_at | time_ago }} — this CV is read-only. See
  <a href="/jobs/{{ job.id }}/cv/preview">Preview CV</a>.
</p>
<form method="post" action="/jobs/{{ job.id }}/cv/reopen" style="display:inline;">
  <button type="submit" class="btn btn-subtle">Start over</button>
</form>
{% else %}
<div id="cv-plan-pane">{% include "cv/_plan_pane.html" %}</div>

{% if job_cv is none %}
<button type="button" id="cv-autostart" hidden
        data-progress-url="/jobs/{{ job.id }}/cv/plan" data-progress-oob
        data-progress-label="Analysing the job"></button>
<script>
  // The base.html body click-listener is registered after this content block is
  // parsed, so defer the synthetic click until the DOM is ready.
  document.addEventListener("DOMContentLoaded", function () {
    var b = document.getElementById("cv-autostart");
    if (b) b.click();
  });
</script>
{% endif %}
{% endif %}
{% endblock %}
```

(This drops the old `cv-workbench-crumb` "Directives → Plan → Draft →
Guardrails" breadcrumb entirely — it described a single combined page that
no longer exists; the subnav is the new wayfinding.)

- [ ] **Step 6: Update `cv_accept`'s redirect target**

In `app/routes/cv.py`, in `cv_accept` (~line 744-752), change:

```python
    return RedirectResponse(f"/jobs/{job_id}/cv", status_code=303)
```

to:

```python
    return RedirectResponse(f"/jobs/{job_id}/cv/preview", status_code=303)
```

(`cv_reopen`'s redirect stays `/jobs/{job_id}/cv` — resuming editing lands
you back on Tailor CV, which is correct.)

- [ ] **Step 7: Run the app-code tests that must now fail, to confirm the diagnosis**

Run: `.venv/bin/python -m pytest tests/test_routes_cv_workbench.py tests/test_routes_cv_actions.py tests/test_cv_task.py -q`
Expected: several FAILs / ERRORs — content that moved off `/jobs/{id}/cv`
(guardrails, scope, tabs, accepted view) is gone from that page, the accept
redirect target changed, and `test_generate_task_returns_oob_preview_chunk`
now gets only 1 chunk instead of 2. This confirms the tests need updating
in the following steps — do not attempt to make app code match old test
expectations.

- [ ] **Step 8: Rewrite `tests/test_routes_cv_workbench.py`, keeping only the iframe/diff machinery tests**

Replace the full file contents:

```python
from unittest.mock import patch
from app.db import queries as q


def _job(conn):
    conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s','http://x','manual')")
    conn.execute("INSERT INTO jobs (source_id, url, title) VALUES (1,'http://x/1','Role')")
    conn.commit()
    q.save_cv_settings(conn, base_cv="# Me", base_instruction="", base_guardrails="",
                       css="", default_scope=["select", "reorder"])
    return 1


def test_workbench_404_for_missing_job(client):
    assert client.get("/jobs/999/cv").status_code == 404


def test_preview_html_renders_base_and_tailored(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Tailored marker\n")
    with patch("app.routes.cv.doc_write_available", return_value=True), \
         patch("app.routes.cv.render_preview_html", side_effect=lambda md, css: f"<!DOCTYPE html>\n{md}"):
        rb = client.get(f"/jobs/{jid}/cv/preview.html?variant=base")
        rt = client.get(f"/jobs/{jid}/cv/preview.html?variant=tailored")
    assert rb.status_code == 200 and "# Me" in rb.text
    assert rt.status_code == 200 and "Tailored marker" in rt.text


def test_preview_html_defaults_to_tailored(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# The draft\n")
    with patch("app.routes.cv.doc_write_available", return_value=True), \
         patch("app.routes.cv.render_preview_html", side_effect=lambda md, css: md):
        r = client.get(f"/jobs/{jid}/cv/preview.html")
    assert "The draft" in r.text


def test_preview_html_tailored_404_without_draft(client, conn):
    jid = _job(conn)
    assert client.get(f"/jobs/{jid}/cv/preview.html?variant=tailored").status_code == 404


def test_preview_html_503_without_doc_write(client, conn):
    jid = _job(conn)
    with patch("app.routes.cv.doc_write_available", return_value=False):
        assert client.get(f"/jobs/{jid}/cv/preview.html?variant=base").status_code == 503


def test_diff_html_renders_with_a_draft(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# CV\n\n- kept\n",
                    base_cv_snapshot="# CV\n\n- kept\n- dropped\n")
    with patch("app.routes.cv.doc_write_available", return_value=True), \
         patch("app.routes.cv.render_diff_html", side_effect=lambda md, css: f"<!doctype html>\n{md}"):
        r = client.get(f"/jobs/{jid}/cv/diff.html")
    assert r.status_code == 200
    assert "cvd-del" in r.text and "dropped" in r.text


def test_diff_html_404_without_draft(client, conn):
    jid = _job(conn)
    assert client.get(f"/jobs/{jid}/cv/diff.html").status_code == 404


def test_diff_html_predates_tracking_when_snapshot_empty(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# CV\n", base_cv_snapshot="")
    r = client.get(f"/jobs/{jid}/cv/diff.html")
    assert r.status_code == 200
    assert "predates change tracking" in r.text


def test_diff_html_fallback_without_doc_write(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# CV\n\n- a\n", base_cv_snapshot="# CV\n\n- a\n- b\n")
    with patch("app.routes.cv.doc_write_available", return_value=False):
        r = client.get(f"/jobs/{jid}/cv/diff.html")
    assert r.status_code == 200
    assert "<del" in r.text


def test_diff_html_falls_back_to_plain_doc_when_the_diff_builder_raises(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# CV marker\n", base_cv_snapshot="# Base\n")
    with patch("app.routes.cv.build_cv_diff", side_effect=RuntimeError("boom")), \
         patch("app.routes.cv.doc_write_available", return_value=True), \
         patch("app.routes.cv.render_diff_html", side_effect=lambda md, css: f"<!doctype html>\n{md}"):
        r = client.get(f"/jobs/{jid}/cv/diff.html")
    assert r.status_code == 200
    assert "CV marker" in r.text
```

- [ ] **Step 9: Create `tests/test_routes_cv_tailor.py`**

```python
from app.db import queries as q


def _job(conn):
    conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s','http://x','manual')")
    conn.execute("INSERT INTO jobs (source_id, url, title) VALUES (1,'http://x/1','Role')")
    conn.commit()
    q.save_cv_settings(conn, base_cv="# Me", base_instruction="", base_guardrails="",
                       css="", default_scope=["select", "reorder"])
    return 1


def test_tailor_cv_renders_first_visit(client, conn):
    jid = _job(conn)
    r = client.get(f"/jobs/{jid}/cv")
    assert r.status_code == 200
    assert "Tailoring plan" in r.text or "plan" in r.text.lower()


def test_plan_pane_has_a_stage_status_indicator(client, conn):
    jid = _job(conn)
    page = client.get(f"/jobs/{jid}/cv").text
    assert page.count('class="cv-stage-status"') == 1
    assert 'data-state="none"' in page


def test_tailor_cv_has_no_waiting_on_you_nags(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft")
    conn.execute("UPDATE job_cv SET generated_at = datetime('now', '-1 hour') WHERE job_id = ?", (jid,))
    q.set_job_cv_directives(conn, jid, "- something new")
    text = client.get(f"/jobs/{jid}/cv").text
    for gone in ("Waiting on you", "is-stale", "n-stale", "n-updating"):
        assert gone not in text


def test_tailor_cv_has_no_stage_breadcrumb(client, conn):
    jid = _job(conn)
    page = client.get(f"/jobs/{jid}/cv").text
    assert 'class="cv-workbench-crumb"' not in page


def test_tailor_cv_has_no_workflow_instructions_line(client, conn):
    jid = _job(conn)
    r = client.get(f"/jobs/{jid}/cv")
    assert "cv-workflow-note" not in r.text
    assert "How this works" not in r.text


def test_directives_note_mentions_heading_and_bullet_structure(client, conn):
    jid = _job(conn)
    r = client.get(f"/jobs/{jid}/cv")
    assert "## Heading" in r.text and "- bullet" in r.text


def test_first_visit_directives_textarea_prefilled_with_template(client, conn):
    jid = _job(conn)
    q.save_cv_settings(conn, base_cv="# Me", base_instruction="", base_guardrails="",
                       css="", default_scope=[1], directives_template="## Role relevance\n## Skills match")
    r = client.get(f"/jobs/{jid}/cv")
    ta = r.text[r.text.index('name="tuning_directives"'):]
    body = ta[ta.index(">") + 1:ta.index("</textarea>")]
    assert "## Role relevance" in body and "## Skills match" in body


def test_tailor_cv_shows_locked_notice_when_finalized(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Final")
    q.finalize_job_cv(conn, jid)
    page = client.get(f"/jobs/{jid}/cv").text
    assert "read-only" in page
    assert 'id="cv-plan-pane"' not in page
    assert "Analyze and find improvements" not in page
    assert 'action="/jobs/{}/cv/reopen"'.format(jid) in page
```

- [ ] **Step 10: Create `tests/test_routes_cv_preview.py`**

```python
import re
from app.db import queries as q


def _job(conn):
    conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s','http://x','manual')")
    conn.execute("INSERT INTO jobs (source_id, url, title) VALUES (1,'http://x/1','Role')")
    conn.commit()
    q.save_cv_settings(conn, base_cv="# Me", base_instruction="", base_guardrails="",
                       css="", default_scope=["select", "reorder"])
    return 1


def test_preview_page_404_for_missing_job(client):
    assert client.get("/jobs/999/cv/preview").status_code == 404


def test_preview_pane_has_stage_status_indicators(client, conn):
    jid = _job(conn)
    page = client.get(f"/jobs/{jid}/cv/preview").text
    assert page.count('class="cv-stage-status"') == 2
    assert 'data-state="none"' in page


def test_preview_draft_and_guardrail_status_stale_after_scope_change(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft", guardrail_findings=[{"rule": "r", "verdict": "ok", "explanation": ""}])
    conn.execute("UPDATE job_cv SET generated_at = datetime('now', '-1 hour') WHERE job_id = ?", (jid,))
    conn.commit()
    q.set_job_cv_scope(conn, jid, [1])
    page = client.get(f"/jobs/{jid}/cv/preview").text
    # both the draft and the guardrail header track staleness
    assert page.count('data-state="stale"') >= 2


def test_preview_marks_draft_out_of_date_when_base_cv_changed(client, conn):
    from app.routes.cv import _base_hash
    jid = _job(conn)
    settings = q.get_cv_settings(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft", base_hash=_base_hash(settings))
    conn.execute("UPDATE job_cv SET generated_at = datetime('now') WHERE job_id = ?", (jid,))
    fresh = client.get(f"/jobs/{jid}/cv/preview").text
    assert 'data-state="fresh"' in fresh and "Outdated" not in fresh
    q.save_cv_settings(conn, base_cv="# a whole new CV", base_instruction=settings["base_instruction"],
                       base_guardrails=settings["base_guardrails"], css=settings["css"],
                       default_scope=settings["default_scope"],
                       directives_template=settings["directives_template"])
    r = client.get(f"/jobs/{jid}/cv/preview")
    assert '<span id="cv-draft-status" class="cv-stage-status" data-state="stale">Outdated</span>' in r.text


def test_preview_badge_shows_running_while_an_update_runs(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft", base_hash="stale")
    conn.execute("UPDATE job_cv SET generated_at = datetime('now') WHERE job_id = ?", (jid,))
    q.enqueue_task(conn, kind="cv_tailor",
                   params={"job_id": jid, "mode": "generate", "render": "preview_pane"})
    r = client.get(f"/jobs/{jid}/cv/preview")
    assert 'data-state="running"' in r.text
    assert 'class="cv-preview-progress" aria-live="polite">' in r.text


def test_preview_progress_note_hidden_when_no_update_running(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft")
    text = client.get(f"/jobs/{jid}/cv/preview").text
    assert "A tailored update is in progress" in text
    assert 'class="cv-preview-progress" aria-live="polite" hidden' in text


def test_preview_cv_hides_progress_when_only_a_plan_task_is_running(client, conn):
    # a plan run produces no draft and doesn't touch guardrails
    jid = _job(conn)
    q.enqueue_task(conn, kind="cv_tailor",
                   params={"job_id": jid, "mode": "plan", "render": "plan_pane"})
    assert q.cv_generate_task_id(conn, jid) is None
    r = client.get(f"/jobs/{jid}/cv/preview")
    assert 'class="cv-preview-progress" aria-live="polite" hidden' in r.text
    assert 'class="cv-findings-stale" aria-live="polite" hidden' in r.text


def test_guardrail_findings_grouped_by_status_with_counts(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft", guardrail_findings=[
        {"rule": "no invented dates", "verdict": "ok", "explanation": ""},
        {"rule": "no invented tools", "verdict": "ok", "explanation": ""},
        {"rule": "no invented degrees", "verdict": "violated", "explanation": "claims a PhD"},
        {"rule": "no invented metrics", "verdict": "unclear", "explanation": "vague number"},
    ])
    r = client.get(f"/jobs/{jid}/cv/preview")
    text = r.text
    assert "<details" in text and "guardrail" in text.lower()
    findings_block = text[text.index('class="guardrail-summary"'):]
    assert findings_block.index("Guardrails") < findings_block.index("guardrail-bar")
    assert "Failed</strong> (1)" in findings_block
    assert "Unclear</span> (1)" in findings_block
    assert "OK</span> (2)" in findings_block
    assert "claims a PhD" in findings_block
    ok_start = findings_block.index("OK</span> (2)")
    failed_start = findings_block.index("Failed</strong> (1)")
    unclear_start = findings_block.index("Unclear</span> (1)")
    ok_details = findings_block[:ok_start][findings_block[:ok_start].rindex("<details"):]
    failed_details = findings_block[:failed_start][findings_block[:failed_start].rindex("<details"):]
    unclear_details = findings_block[:unclear_start][findings_block[:unclear_start].rindex("<details"):]
    assert ok_details.startswith("<details>")
    assert failed_details.startswith("<details open>")
    assert unclear_details.startswith("<details open>")


def test_guardrails_heading_shows_without_a_draft(client, conn):
    jid = _job(conn)
    text = client.get(f"/jobs/{jid}/cv/preview").text
    assert 'class="guardrail-summary"' in text
    assert ">Guardrails" in text
    assert "after you generate a draft" in text


def test_guardrails_heading_and_bar_show_with_findings(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft",
                    guardrail_findings=[{"rule": "No lies", "verdict": "ok", "explanation": ""}])
    text = client.get(f"/jobs/{jid}/cv/preview").text
    assert 'class="guardrail-summary"' in text
    assert ">Guardrails" in text
    assert 'class="guardrail-bar"' in text
    assert "after you generate a draft" not in text


def test_guardrails_placeholder_when_draft_has_no_findings(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft", guardrail_findings=[])
    text = client.get(f"/jobs/{jid}/cv/preview").text
    assert "No guardrail findings recorded" in text
    assert 'class="guardrail-bar"' not in text


def test_guardrails_placeholder_hidden_while_recheck_runs(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft", guardrail_findings=[])
    q.enqueue_task(conn, kind="cv_tailor",
                   params={"job_id": jid, "mode": "generate", "render": "preview_pane"})
    text = client.get(f"/jobs/{jid}/cv/preview").text
    assert "after you generate a draft" not in text
    assert "No guardrail findings recorded" not in text
    assert 'class="cv-findings-stale" aria-live="polite">' in text


def test_scope_selector_is_an_edit_latitude_row_with_descriptions_foldout(client, conn):
    jid = _job(conn)
    text = client.get(f"/jobs/{jid}/cv/preview").text
    assert "<legend>Edit scope</legend>" not in text
    assert 'class="cv-scope-row"' in text
    assert "Edit latitude" in text
    assert 'class="cv-scope-help"' in text
    assert "<summary>Descriptions</summary>" in text
    assert "<details class=\"cv-scope-help\">" in text and "<details class=\"cv-scope-help\" open>" not in text


def test_scope_selector_checkbox_state_reflects_job_cv_scope(client, conn):
    jid = _job(conn)
    opts = q.get_scope_options(conn)
    chosen, other = opts[0]["id"], opts[1]["id"]
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft", scope=[chosen])
    text = client.get(f"/jobs/{jid}/cv/preview").text
    assert 'class="cv-scope-row"' in text
    form_start = text.index('id="cv-latitude-form"')
    form_end = text.index("</form>", form_start)
    assert form_start < text.index('class="cv-scope-row"', form_start) < form_end
    assert f'value="{chosen}" checked>' in text
    assert f'value="{other}" checked>' not in text


def test_scope_checkboxes_use_live_descriptions(client, conn):
    import html
    jid = _job(conn)
    opts = q.get_scope_options(conn)
    r = client.get(f"/jobs/{jid}/cv/preview")
    text = html.unescape(r.text)
    for opt in opts:
        assert opt["description"] in text
    assert f'value="{opts[0]["id"]}"' in r.text
    if opts[0].get("name"):
        assert f'<dt>{opts[0]["name"]}</dt>' in r.text
        assert f'#{opts[0]["id"]} {opts[0]["name"]}' not in text


def test_preview_pane_layout_guardrails_under_preview_controls_below_iframe(client, conn):
    from unittest.mock import patch
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft\n\n- x\n", scope=[1],
                    guardrail_findings=[{"rule": "r", "verdict": "ok", "explanation": ""}])
    with patch("app.routes.cv.doc_write_available", return_value=True):
        text = client.get(f"/jobs/{jid}/cv/preview").text
    actions = text[text.index('class="cv-preview-actions"'):text.index('class="cv-preview-bar"')]
    assert ">Update</button>" in actions
    assert "Accept this CV" not in actions and "Download PDF" not in actions
    stage = text.index('class="cv-preview-stage')
    assert stage < text.index("Accept this CV") < text.index('id="cv-findings"')


def test_preview_pane_has_base_tailored_tabs(client, conn):
    from unittest.mock import patch
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft\n")
    with patch("app.routes.cv.doc_write_available", return_value=True):
        r = client.get(f"/jobs/{jid}/cv/preview")
    assert 'data-variant="base"' in r.text
    assert 'data-variant="tailored"' in r.text
    assert 'class="cv-preview-stage' in r.text
    assert r.text.count('class="cv-preview-doc') == 4
    assert 'class="cv-preview-doc is-active" data-variant="tailored"' in r.text
    assert 'src="/jobs/%d/cv/preview.html?variant=tailored"' % jid in r.text
    assert 'data-src="/jobs/%d/cv/preview.html?variant=base"' % jid in r.text
    assert 'src="/jobs/%d/cv/preview.html?variant=base"' % jid not in r.text.replace("data-src", "xxxx")
    assert 'class="btn btn-subtle cv-preview-fs"' in r.text


def test_preview_pane_tailored_tab_disabled_without_draft(client, conn):
    from unittest.mock import patch
    jid = _job(conn)
    with patch("app.routes.cv.doc_write_available", return_value=True):
        r = client.get(f"/jobs/{jid}/cv/preview")
    m = re.search(r'<button[^>]*class="cv-preview-tab"[^>]*data-variant="tailored"[^>]*>', r.text)
    assert m and "disabled" in m.group(0)
    assert r.text.count('class="cv-preview-doc') == 1
    assert 'class="cv-preview-doc is-active" data-variant="base"' in r.text
    assert 'src="/jobs/%d/cv/preview.html?variant=base"' % jid in r.text


def test_preview_pane_shows_progress_note_while_a_generate_task_runs(client, conn):
    from unittest.mock import patch
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft\n")
    q.enqueue_task(conn, kind="cv_tailor",
                   params={"job_id": jid, "mode": "generate", "render": "preview_pane"})
    tid = q.cv_generate_task_id(conn, jid)
    assert tid is not None
    with patch("app.routes.cv.doc_write_available", return_value=True):
        r = client.get(f"/jobs/{jid}/cv/preview")
    assert 'class="cv-preview-progress" aria-live="polite">' in r.text
    assert "A tailored update is in progress" in r.text
    assert f"__cvWatchGenerate({tid})" in r.text


def test_preview_cv_has_edit_tab_and_mount_when_draft(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft body\n")
    from unittest.mock import patch
    with patch("app.routes.cv.doc_write_available", return_value=True):
        r = client.get(f"/jobs/{jid}/cv/preview")
    assert 'data-variant="edit"' in r.text
    assert 'data-md-editor-autosave-url="/jobs/%d/cv/save-tailored"' % jid in r.text
    assert "# Draft body" in r.text
    assert r.text.index('data-variant="edit"') < r.text.index('data-variant="diff"')


def test_preview_cv_edit_tab_disabled_without_draft(client, conn):
    jid = _job(conn)
    from unittest.mock import patch
    with patch("app.routes.cv.doc_write_available", return_value=True):
        r = client.get(f"/jobs/{jid}/cv/preview")
    assert re.search(r'data-variant="edit"[^>]*disabled', r.text) or 'data-variant="edit"' not in r.text


def test_preview_cv_no_docwrite_uses_editor_not_readonly_div(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft body\n")
    from unittest.mock import patch
    with patch("app.routes.cv.doc_write_available", return_value=False):
        r = client.get(f"/jobs/{jid}/cv/preview")
    assert "data-md-editor" in r.text


def test_preview_pane_markdown_fallback_without_doc_write(client, conn):
    from unittest.mock import patch
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft body\n")
    with patch("app.routes.cv.doc_write_available", return_value=False):
        r = client.get(f"/jobs/{jid}/cv/preview")
    assert 'class="cv-preview-stage"' not in r.text
    assert "Draft body" in r.text


def test_preview_pane_has_differences_tab(client, conn):
    from unittest.mock import patch
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft\n", base_cv_snapshot="# Base\n")
    with patch("app.routes.cv.doc_write_available", return_value=True):
        r = client.get(f"/jobs/{jid}/cv/preview")
    assert 'data-variant="diff"' in r.text
    assert f'data-src="/jobs/{jid}/cv/diff.html"' in r.text
    assert "What tailoring changed" not in r.text
    assert 'id="cv-change-report"' not in r.text


def test_preview_pane_shows_change_digest_above_the_tabs(client, conn):
    from unittest.mock import patch
    jid = _job(conn)
    q.upsert_job_cv(
        conn, jid,
        base_cv_snapshot="# CV\n\n## Summary\n\nEngineer.\n\n## Skills\n\n- kept\n- dropped bullet\n",
        tailored_cv="# CV\n\n## Summary\n\nEngineer.\n\nBrand new intro paragraph.\n\n## Skills\n\n- kept\n",
    )
    with patch("app.routes.cv.doc_write_available", return_value=True):
        r = client.get(f"/jobs/{jid}/cv/preview")
    assert '<p class="cv-diff-summary-line">' in r.text
    assert "1 bullet dropped" in r.text
    assert "Brand new intro paragraph." in r.text
    assert r.text.index('<div class="cv-diff-summary">') < r.text.index('data-variant="diff"')


def test_preview_pane_no_digest_before_first_draft(client, conn):
    from unittest.mock import patch
    jid = _job(conn)
    with patch("app.routes.cv.doc_write_available", return_value=True):
        r = client.get(f"/jobs/{jid}/cv/preview")
    assert '<div class="cv-diff-summary">' not in r.text


def test_differences_tab_disabled_without_draft(client, conn):
    from unittest.mock import patch
    jid = _job(conn)
    with patch("app.routes.cv.doc_write_available", return_value=True):
        r = client.get(f"/jobs/{jid}/cv/preview")
    assert 'data-variant="diff"' in r.text
    assert f'/jobs/{jid}/cv/diff.html' not in r.text


def test_accepted_view_has_three_tabs(client, conn):
    from unittest.mock import patch
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Final\n", base_cv_snapshot="# Base\n")
    q.finalize_job_cv(conn, jid)
    with patch("app.routes.cv.doc_write_available", return_value=True):
        r = client.get(f"/jobs/{jid}/cv/preview")
    assert r.text.count('class="cv-preview-tab"') == 3
    assert 'data-variant="diff"' in r.text


def test_preview_cv_first_visit_shows_empty_preview_state(client, conn):
    jid = _job(conn)
    text = client.get(f"/jobs/{jid}/cv/preview").text
    assert "No tailored CV yet" in text
    assert "Accept this CV" not in text


def test_findings_stale_note_hidden_when_no_generate_running(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft")
    text = client.get(f"/jobs/{jid}/cv/preview").text
    assert 'class="cv-findings-stale" aria-live="polite" hidden' in text


def test_findings_stale_note_visible_while_generate_runs(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft")
    q.enqueue_task(conn, kind="cv_tailor",
                   params={"job_id": jid, "mode": "generate", "render": "preview_pane"})
    text = client.get(f"/jobs/{jid}/cv/preview").text
    assert 'class="cv-findings-stale" aria-live="polite">' in text


def test_findings_container_present_on_first_generate_without_draft(client, conn):
    jid = _job(conn)
    q.enqueue_task(conn, kind="cv_tailor",
                   params={"job_id": jid, "mode": "generate", "render": "preview_pane"})
    text = client.get(f"/jobs/{jid}/cv/preview").text
    assert 'id="cv-findings"' in text
    assert 'class="cv-findings-stale" aria-live="polite">' in text
```

- [ ] **Step 11: Fix the page-content-dependent tests in `tests/test_routes_cv_actions.py`**

Replace `test_accept_finalizes_and_shows_read_only_view`:

```python
def test_accept_finalizes_and_shows_read_only_view(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft")
    r = client.post(f"/jobs/{jid}/cv/accept", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == f"/jobs/{jid}/cv/preview"
    assert q.get_job_cv(conn, jid)["finalized_at"] is not None
    assert "cv" in [e["kind"] for e in q.get_job_events(conn, jid)]
    # Preview CV: the accepted, read-only view — downloads + start over, no tailoring UI
    preview = client.get(f"/jobs/{jid}/cv/preview").text
    assert "read-only" in preview
    assert "Start over" in preview and "Download PDF" in preview
    assert "Accept this CV" not in preview
    assert ">Update</button>" not in preview
    # Tailor CV: a locked notice, no directives form
    tailor = client.get(f"/jobs/{jid}/cv").text
    assert "read-only" in tailor
    assert 'id="cv-plan-pane"' not in tailor
```

Replace `test_copy_markdown_available_in_both_states`:

```python
def test_copy_markdown_available_in_both_states(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Tailored\n\n- a bullet with <special> & chars\n")
    page = client.get(f"/jobs/{jid}/cv/preview").text
    assert "Copy markdown" in page and 'class="cv-md-source"' in page
    assert "a bullet with" in page
    q.finalize_job_cv(conn, jid)
    page = client.get(f"/jobs/{jid}/cv/preview").text
    assert "Copy markdown" in page and "a bullet with" in page
```

Replace `test_reopen_clears_finalized_and_restores_workbench`:

```python
def test_reopen_clears_finalized_and_restores_workbench(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft")
    q.finalize_job_cv(conn, jid)
    r = client.post(f"/jobs/{jid}/cv/reopen", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == f"/jobs/{jid}/cv"
    assert q.get_job_cv(conn, jid)["finalized_at"] is None
    assert 'id="cv-plan-pane"' in client.get(f"/jobs/{jid}/cv").text
    assert 'id="cv-preview-pane"' in client.get(f"/jobs/{jid}/cv/preview").text
```

Replace `test_scope_control_lives_in_the_preview_pane_not_the_plan_pane`:

```python
def test_scope_control_lives_in_the_preview_pane_not_the_plan_pane(client, conn):
    jid = _job(conn)
    plan_page = client.get(f"/jobs/{jid}/cv").text
    preview_page = client.get(f"/jobs/{jid}/cv/preview").text
    assert 'name="scope"' not in plan_page
    assert "Edit scope:" not in plan_page
    assert 'id="cv-latitude-form"' in preview_page
    assert 'name="scope"' in preview_page
    assert "Edit latitude" in preview_page
```

Replace `test_plan_pane_points_at_edit_latitude`:

```python
def test_plan_pane_points_at_edit_latitude(client, conn):
    jid = _job(conn)
    page = client.get(f"/jobs/{jid}/cv").text
    assert "Edit latitude" in page  # the pointer line lives in the plan pane
```

Replace `test_directives_still_autosave_without_inline_script`:

```python
def test_directives_still_autosave_without_inline_script(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid)  # avoids the first-visit autostart <script>
    page = client.get(f"/jobs/{jid}/cv").text
    assert 'id="cv-directives-form"' in page
    assert "<script" not in page
```

Replace `test_accept_button_is_a_plain_post`:

```python
def test_accept_button_is_a_plain_post(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft")
    text = client.get(f"/jobs/{jid}/cv/preview").text
    accept_start = text.index("Accept this CV")
    accept_form = text[max(0, accept_start - 300):accept_start]
    assert 'action="/jobs/{}/cv/accept"'.format(jid) in accept_form
    assert "hx-post" not in accept_form
```

Leave every other test in this file untouched — they exercise POST
endpoints that return fragments directly (plan, generate, save-directives,
save-scope, save-tailored, recheck-guardrails, plan/accept, handled/delete,
reset-directives, pdf) and are unaffected by the page split.

- [ ] **Step 12: Fix `tests/test_cv_task.py`'s two-chunk assumption**

Replace `test_generate_task_returns_oob_preview_chunk` (~line 145):

```python
def test_generate_task_returns_oob_preview_chunk(conn, cfg):
    jid = _seed(conn)
    q.upsert_job_cv(conn, jid, scope=[1])
    q.set_job_cv_directives(conn, jid, "- foreground Kafka")
    with patch("app.routes.cv.tailor_cv", return_value={"markdown": "# Me\n- x\n"}), \
         patch("app.routes.cv.check_guardrails", return_value={"findings": []}):
        task = q.enqueue_task(conn, kind="cv_tailor",
                              params={"job_id": jid, "mode": "generate", "render": "preview_pane"})
        execute_task(conn, MagicMock(), "m", cfg, task)
    res = q.get_task(conn, task["id"])["result"]
    # Tailor CV and Preview CV are separate pages now — a generate no longer
    # needs to also refresh the (off-page) plan pane
    assert len(res["html_chunks"]) == 1
    preview = res["html_chunks"][0]
    assert 'id="cv-preview-pane"' in preview
    # the finishing task is still 'running' in the DB, but its own result render
    # must not paint the preview as still-updating
    assert 'class="cv-preview-progress" aria-live="polite" hidden' in preview
    assert 'class="cv-findings-stale" aria-live="polite" hidden' in preview
    # the stage badges must not stick on "running" once the task's own render lands
    assert 'data-state="running"' not in preview
```

- [ ] **Step 13: Run everything touched by this task**

Run: `.venv/bin/python -m pytest tests/test_routes_cv_workbench.py tests/test_routes_cv_tailor.py tests/test_routes_cv_preview.py tests/test_routes_cv_actions.py tests/test_cv_task.py -v`
Expected: PASS (all)

- [ ] **Step 14: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: same 3 pre-existing `test_routes_home.py` failures, nothing new

- [ ] **Step 15: Commit**

```bash
git add app/templates/cv/preview.html app/templates/cv/workbench.html app/routes/cv.py \
        tests/test_routes_cv_workbench.py tests/test_routes_cv_tailor.py \
        tests/test_routes_cv_preview.py tests/test_routes_cv_actions.py tests/test_cv_task.py
git commit -m "$(cat <<'EOF'
feat(cv): split CV workbench into Tailor CV and Preview CV pages

/jobs/{id}/cv is now plan-only (directives + suggestions). The
preview pane (edit latitude, tabs, guardrails, accept/export) and the
accepted read-only view move to a new /jobs/{id}/cv/preview. Accept
now redirects to Preview CV; a generate's task result no longer also
re-renders the (now off-page) plan pane.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_014aDeoXumXHmUaUFpb5tx1a
EOF
)"
```

---

## Task 5: Full regression + manual verification

- [ ] **Step 1: Run the complete test suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 1758 passed (same count as baseline, since no test was added net-new
beyond the ones this plan adds/removes — check the printed total), 3
pre-existing failures in `test_routes_home.py`, nothing else.

- [ ] **Step 2: Manual dev-server verification**

Use the `run-dev-server` skill (throwaway copy of `job-seek.db`, do not
touch the live DB). Once running, check by hand:

- `/jobs/{id}` (Offering): header shows title/org/tags once, subnav has 4
  tabs with Offering active, no Tailor CV button, "Back to list" is at the
  bottom, Accept/Reject/Trash still work.
- `/jobs/{id}/cv` (Tailor CV): only directives/plan content, subnav shows
  Tailor CV active, first visit on a job with no `job_cv` auto-triggers
  analysis.
- `/jobs/{id}/cv/preview` (Preview CV): edit latitude, Update, tabs
  (Base/Tailored/Edit/Differences), guardrails, Accept this CV — all work;
  accepting redirects here and shows the read-only accepted view.
- `/jobs/{id}/history`: lists job events, empty state when there are none.
- Subnav navigation between all four views works and highlights the active
  tab correctly at each URL.

- [ ] **Step 3: Hand off**

Leave the dev server running and give the user the URL, per this project's
UI-change convention. Wait for their go-ahead before offering to
merge/finish (squash-merge into local `main`, remove the worktree, per
CLAUDE.md's "Finishing a change" section) — do not do this automatically.
