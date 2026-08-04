# Bulk Job Feedback (Multi-Select) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user select multiple jobs in the list, optionally override the scenario and leave a shared note, then apply Accept/Reject/Invalid to all of them at once — and make the Reject-vs-Invalid distinction real and visible.

**Architecture:** Server-rendered FastAPI + Jinja2 + HTMX, no client-side framework. Selection state lives in the DOM (checkboxes); bulk submission is a normal HTMX form POST that re-renders a shared list partial. A handful of small vanilla-JS listeners (matching the existing pattern in `base.html`) handle the live selection count and "clear selection" — no bundler, no new dependencies.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, HTMX 1.9.12, SQLite (no schema changes), pytest + `fastapi.testclient.TestClient`.

## Global Constraints

- No database schema changes — `feedback_note` and `status` already support everything needed.
- The `note` field becomes fully optional everywhere (single-job and bulk forms), not just in bulk.
- Reject = doesn't match criteria (feeds `get_recent_feedback_notes` / scenario criteria tuning). Invalid = not a usable posting at all (must be excluded from `get_recent_feedback_notes`).
- Bulk scenario override sentinel: empty string `""` means "keep each job's own best-matching scenario"; any other value is a scenario id applied to every selected job.
- No JS build step — only inline `<script>` blocks in `base.html`, following the existing progress-bar script's style.
- Reject/Invalid tooltip copy (used verbatim in both the single-job form and the bulk bar, avoiding contractions so it isn't apostrophe-sensitive in HTML attributes):
  - Reject: `Does not match your criteria — feeds back into scenario tuning.`
  - Invalid: `Not a usable posting (expired, spam, duplicate, wrong content) — does not affect scenario criteria.`

---

### Task 1: Make the feedback note fully optional

**Files:**
- Modify: `app/routes/jobs.py:44-54` (`job_feedback`)
- Modify: `app/templates/jobs/_feedback.html:38` (note label/textarea)
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `POST /jobs/{job_id}/feedback` now accepts a request with no `note` field (was a required field, 422 without it).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_routes_jobs.py`:

```python
def test_job_feedback_without_note_succeeds(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.post(
        f"/jobs/{jid}/feedback",
        data={"status": "accepted", "feedback_scenario_id": scenario_id},
    )
    assert resp.status_code == 200
    job = q.get_job(conn, jid)
    assert job["status"] == "accepted"
    assert job["feedback_note"] is None


def test_job_expand_note_field_is_optional(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert "Note (optional)" in resp.text
    assert '<textarea name="note" required' not in resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_routes_jobs.py -k "note_is_optional or without_note" -v`
Expected: FAIL — `test_job_feedback_without_note_succeeds` fails with a 422 status code; `test_job_expand_note_field_is_optional` fails because the label still reads "Note (required)" and the textarea still has `required`.

- [ ] **Step 3: Implement**

In `app/routes/jobs.py`, change the `job_feedback` signature:

```python
@router.post("/jobs/{job_id}/feedback", response_class=HTMLResponse)
def job_feedback(
    job_id: int,
    request: Request,
    status: str = Form(...),
    note: str | None = Form(None),
    feedback_scenario_id: int = Form(...),
    conn: sqlite3.Connection = Depends(get_db),
):
    q.update_job_feedback(conn, job_id, status, note, feedback_scenario_id)
    return HTMLResponse(content="", status_code=200)
```

In `app/templates/jobs/_feedback.html`, change:

```html
    <label>Note (required):
      <textarea name="note" required placeholder="Why accepting/rejecting? What should change?"></textarea>
    </label>
```

to:

```html
    <label>Note (optional):
      <textarea name="note" placeholder="Why accepting/rejecting? What should change?"></textarea>
    </label>
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_routes_jobs.py -v`
Expected: PASS (all tests, including the two new ones and the existing `test_job_feedback_updates_status`).

- [ ] **Step 5: Commit**

```bash
git add app/routes/jobs.py app/templates/jobs/_feedback.html tests/test_routes_jobs.py
git commit -m "feat: make job feedback note optional"
```

---

### Task 2: Reject/Invalid tooltips on the single-job form

**Files:**
- Modify: `app/templates/jobs/_feedback.html:33-35` (decision buttons)
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: nothing consumed by later tasks (Task 7 will duplicate this same copy verbatim in the bulk bar, per the Global Constraints tooltip text).

- [ ] **Step 1: Write the failing test**

```python
def test_job_expand_reject_invalid_buttons_have_tooltips(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert 'title="Does not match your criteria — feeds back into scenario tuning."' in resp.text
    assert 'title="Not a usable posting (expired, spam, duplicate, wrong content) — does not affect scenario criteria."' in resp.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_routes_jobs.py -k reject_invalid_buttons_have_tooltips -v`
Expected: FAIL — buttons currently have no `title` attribute.

- [ ] **Step 3: Implement**

In `app/templates/jobs/_feedback.html`, change:

```html
    <div class="actions" role="group" aria-label="Decision">
      <button type="submit" name="status" value="accepted" class="btn btn-accept">Accept</button>
      <button type="submit" name="status" value="rejected" class="btn btn-reject">Reject</button>
      <button type="submit" name="status" value="invalid" class="btn btn-invalid">Invalid</button>
    </div>
```

to:

```html
    <div class="actions" role="group" aria-label="Decision">
      <button type="submit" name="status" value="accepted" class="btn btn-accept">Accept</button>
      <button type="submit" name="status" value="rejected" class="btn btn-reject" title="Does not match your criteria — feeds back into scenario tuning.">Reject</button>
      <button type="submit" name="status" value="invalid" class="btn btn-invalid" title="Not a usable posting (expired, spam, duplicate, wrong content) — does not affect scenario criteria.">Invalid</button>
    </div>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_routes_jobs.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/templates/jobs/_feedback.html tests/test_routes_jobs.py
git commit -m "feat: add reject/invalid explanatory tooltips"
```

---

### Task 3: Fold an expanded job back to its row view (collapse)

**Files:**
- Modify: `app/routes/jobs.py` (add `job_collapse`, near `job_expand`)
- Modify: `app/templates/jobs/_feedback.html:1-8` (wrap header block in a toggle target)
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: `jobs/_row.html` (existing template, unchanged), `q.get_job`, `q.get_sources` (existing).
- Produces: `GET /jobs/{job_id}/collapse` — renders `jobs/_row.html` for that job, same shape as what `job_list` produces for one row.

- [ ] **Step 1: Write the failing tests**

```python
def test_job_collapse_returns_row_view(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}/collapse")
    assert resp.status_code == 200
    assert f'hx-get="/jobs/{jid}/expand"' in resp.text
    assert f'id="job-{jid}"' in resp.text


def test_job_expand_header_is_collapsible(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert f'hx-get="/jobs/{jid}/collapse"' in resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_routes_jobs.py -k "collapse" -v`
Expected: FAIL — `/jobs/{id}/collapse` doesn't exist (404); the expand view has no `hx-get=".../collapse"`.

- [ ] **Step 3: Implement**

In `app/routes/jobs.py`, add a new route right after `job_expand`:

```python
@router.get("/jobs/{job_id}/collapse", response_class=HTMLResponse)
def job_collapse(job_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    job = q.get_job(conn, job_id)
    sources = {s["id"]: s for s in q.get_sources(conn)}
    job["source_name"] = sources.get(job["source_id"], {}).get("name", "")
    return templates.TemplateResponse(request, "jobs/_row.html", {"job": job})
```

In `app/templates/jobs/_feedback.html`, change the top of the file from:

```html
{% import "jobs/_macros.html" as macros %}
<article class="job-row" id="job-{{ job.id }}">
  {{ macros.meta_tags(job) }}
  <h3 class="job-title">{{ job.title or "(no title)" }}</h3>
  {% if job.headline %}
    <p class="job-hook">{{ job.headline }}</p>
  {% endif %}
```

to:

```html
{% import "jobs/_macros.html" as macros %}
<article class="job-row" id="job-{{ job.id }}">
  <div role="button" tabindex="0" style="cursor:pointer"
    hx-get="/jobs/{{ job.id }}/collapse"
    hx-target="#job-{{ job.id }}"
    hx-swap="outerHTML"
    hx-trigger="click, keyup[key=='Enter']">
    {{ macros.meta_tags(job) }}
    <h3 class="job-title">{{ job.title or "(no title)" }}</h3>
    {% if job.headline %}
      <p class="job-hook">{{ job.headline }}</p>
    {% endif %}
  </div>
```

(The `<form>` and everything after it stays outside this new `<div>`, so no changes needed below that point — just make sure the existing closing `</article>` tag at the end of the file still balances the new `<div>` opened here. The div only needs to close right before the `<p>` containing the company/original-link line, since that line and everything after — including the form — should stay outside the collapse-click target.)

Concretely, insert a closing `</div>` right before the `<p>` that currently reads:

```html
  <p>
    {% if job.company %}<span>· {{ job.company }}</span>{% endif %}
    <a href="{{ job.url }}" target="_blank" rel="noopener">↗ original</a>
  </p>
```

so that block becomes:

```html
  </div>
  <p>
    {% if job.company %}<span>· {{ job.company }}</span>{% endif %}
    <a href="{{ job.url }}" target="_blank" rel="noopener">↗ original</a>
  </p>
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_routes_jobs.py -v`
Expected: PASS (all tests, including `test_job_expand_has_full_meta_parity_with_card` and `test_job_expand_score_box_repeats_score_scenario_with_reasoning`, which only check substring presence/counts unaffected by the new wrapper div).

- [ ] **Step 5: Commit**

```bash
git add app/routes/jobs.py app/templates/jobs/_feedback.html tests/test_routes_jobs.py
git commit -m "feat: allow folding an expanded job back to its row view"
```

---

### Task 4: Exclude invalid-status jobs from scenario criteria feedback

**Files:**
- Modify: `app/db/queries.py:281-291` (`get_recent_feedback_notes`)
- Test: `tests/test_queries.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `get_recent_feedback_notes(conn, scenario_id, limit=20)` — same signature, now also filters out `status = 'invalid'` rows.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_queries.py`:

```python
def test_get_recent_feedback_notes_excludes_invalid_status(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T1", company="C", raw_text="r")
    q.update_job_pipeline(conn, j1, simplified_content="", content_type="job_posting")
    q.upsert_job_score(conn, j1, scenario_id, 0.5, "reasoning", "hash1")
    q.update_job_feedback(conn, j1, "invalid", "expired listing", feedback_scenario_id=scenario_id)
    j2 = q.insert_job(conn, source_id=source_id, url="http://job/2", title="T2", company="C", raw_text="r")
    q.update_job_pipeline(conn, j2, simplified_content="", content_type="job_posting")
    q.upsert_job_score(conn, j2, scenario_id, 0.5, "reasoning", "hash2")
    q.update_job_feedback(conn, j2, "rejected", "too junior", feedback_scenario_id=scenario_id)
    notes = q.get_recent_feedback_notes(conn, scenario_id)
    assert notes == ["too junior"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_queries.py -k excludes_invalid_status -v`
Expected: FAIL — `notes` currently contains both `"expired listing"` and `"too junior"`.

- [ ] **Step 3: Implement**

In `app/db/queries.py`, change:

```python
def get_recent_feedback_notes(
    conn: sqlite3.Connection, scenario_id: int, limit: int = 20
) -> list[str]:
    rows = conn.execute(
        """SELECT feedback_note FROM jobs
        WHERE feedback_scenario_id = ?
        AND feedback_note IS NOT NULL AND feedback_note != ''
        ORDER BY fetched_at DESC LIMIT ?""",
        (scenario_id, limit),
    ).fetchall()
    return [r["feedback_note"] for r in rows]
```

to:

```python
def get_recent_feedback_notes(
    conn: sqlite3.Connection, scenario_id: int, limit: int = 20
) -> list[str]:
    rows = conn.execute(
        """SELECT feedback_note FROM jobs
        WHERE feedback_scenario_id = ?
        AND status != 'invalid'
        AND feedback_note IS NOT NULL AND feedback_note != ''
        ORDER BY fetched_at DESC LIMIT ?""",
        (scenario_id, limit),
    ).fetchall()
    return [r["feedback_note"] for r in rows]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_queries.py -v`
Expected: PASS (including the pre-existing `test_get_recent_feedback_notes` and `test_get_recent_feedback_notes_scoped_to_scenario`, which use `status="rejected"` and are unaffected).

- [ ] **Step 5: Commit**

```bash
git add app/db/queries.py tests/test_queries.py
git commit -m "fix: exclude invalid-status jobs from scenario feedback notes"
```

---

### Task 5: Extract the job list body into a swappable partial

**Files:**
- Create: `app/templates/jobs/_content.html`
- Modify: `app/templates/jobs/list.html`
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: `jobs`, `counts` template context (already produced by `job_list`, unchanged in this task).
- Produces: `<div id="jobs-content">` wrapper that Task 7's bulk-feedback route will target as its HTMX swap destination. `jobs/_content.html` as a template other routes can render directly.

This is a pure refactor — no behavior changes. Existing tests must continue to pass unmodified; this task only adds one new assertion for the wrapper div.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_routes_jobs.py`:

```python
def test_job_list_has_swappable_content_wrapper(client, conn):
    resp = client.get("/")
    assert resp.status_code == 200
    assert '<div id="jobs-content">' in resp.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_routes_jobs.py -k swappable_content_wrapper -v`
Expected: FAIL — `list.html` has no such wrapper yet.

- [ ] **Step 3: Implement**

Create `app/templates/jobs/_content.html` with exactly the body `list.html` currently renders after the `<h1>`:

```html
<div class="filter-bar">
  <a href="/">New ({{ counts.new }})</a>
  <a href="/?status=accepted">Accepted ({{ counts.accepted }})</a>
  <a href="/?status=rejected">Rejected ({{ counts.rejected }})</a>
  <a href="/?status=invalid">Invalid ({{ counts.invalid }})</a>
  <a href="/?content_type=lead">Leads ({{ counts.lead }})</a>
</div>
{% if jobs %}
  {% for job in jobs %}
    {% include "jobs/_row.html" %}
  {% endfor %}
{% else %}
  <p>No jobs found.</p>
{% endif %}
```

Replace `app/templates/jobs/list.html` with:

```html
{% extends "base.html" %}
{% block title %}Jobs — Job Seek{% endblock %}
{% block content %}
<h1>Jobs</h1>
<div id="jobs-content">
  {% include "jobs/_content.html" %}
</div>
{% endblock %}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_routes_jobs.py -v`
Expected: PASS — every existing test in the file, plus the new one. In particular re-check `test_job_list_filter_bar_shows_counts` and `test_job_list_returns_200` pass unchanged, confirming the extraction preserved output.

- [ ] **Step 5: Commit**

```bash
git add app/templates/jobs/_content.html app/templates/jobs/list.html tests/test_routes_jobs.py
git commit -m "refactor: extract job list body into a swappable _content.html partial"
```

---

### Task 6: Add a bulk-select checkbox to each job row

**Files:**
- Modify: `app/templates/jobs/_row.html`
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: each row's checkbox is `name="job_ids"`, `value="{{ job.id }}"`, associated with `form="bulk-form"` (a form element Task 7 creates in `list.html`, living outside `#jobs-content` so it survives partial swaps).

- [ ] **Step 1: Write the failing test**

```python
def test_job_list_row_has_bulk_select_checkbox(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get("/")
    assert resp.status_code == 200
    assert f'<input type="checkbox" class="job-select" name="job_ids" value="{jid}" form="bulk-form"' in resp.text
    assert 'onclick="event.stopPropagation()"' in resp.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_routes_jobs.py -k bulk_select_checkbox -v`
Expected: FAIL — no checkbox in the row yet.

- [ ] **Step 3: Implement**

In `app/templates/jobs/_row.html`, change:

```html
  {{ macros.meta_tags(job) }}
  <h3 class="job-title">{{ job.title or "(no title)" }}</h3>
```

to:

```html
  <input type="checkbox" class="job-select" name="job_ids" value="{{ job.id }}" form="bulk-form"
    aria-label="Select {{ job.title or 'this job' }} for bulk action" onclick="event.stopPropagation()">
  {{ macros.meta_tags(job) }}
  <h3 class="job-title">{{ job.title or "(no title)" }}</h3>
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_routes_jobs.py -v`
Expected: PASS. Double check `test_job_list_card_is_clickable_and_has_no_details_button` still passes (it doesn't check for absence of checkboxes, only absence of "Details" text and presence of `role="button"`).

- [ ] **Step 5: Commit**

```bash
git add app/templates/jobs/_row.html tests/test_routes_jobs.py
git commit -m "feat: add bulk-select checkbox to job rows"
```

---

### Task 7: Bulk feedback backend route

**Files:**
- Modify: `app/routes/jobs.py` (add `_get_filtered_jobs` helper, refactor `job_list` to use it, add `job_bulk_feedback`)
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: `q.get_job`, `q.get_jobs`, `q.get_job_counts`, `q.get_scenarios`, `q.update_job_feedback` (all existing, unchanged), `_enrich_jobs` (existing helper in this file).
- Produces: `POST /jobs/bulk-feedback` — form fields `job_ids: list[int]`, `status: str`, `note: str | None`, `feedback_scenario_id: str` (`""` = keep each job's own best scenario), `status_filter: str | None`, `content_type_filter: str | None`. Renders `jobs/_content.html` with `{"jobs", "counts", "scenarios"}` — the same partial Task 5 created. `_get_filtered_jobs(conn, status, content_type) -> list[dict]` — shared filtering logic, also used by `job_list`.

This task is backend-only and fully testable via direct HTTP calls — it does not depend on the bulk bar UI (Task 8) existing yet.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_routes_jobs.py`:

```python
def test_job_bulk_feedback_updates_multiple_jobs(client, conn):
    sid, j1, scenario_id = _seed(conn)
    j2 = q.insert_job(conn, source_id=sid, url="http://finn.no/job/2", title="Data Eng", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, j2, simplified_content="clean", content_type="job_posting", summary="Also good")
    q.upsert_job_score(conn, j2, scenario_id, 0.6, "Decent match", "hash2")
    resp = client.post(
        "/jobs/bulk-feedback",
        data={
            "job_ids": [j1, j2],
            "status": "rejected",
            "feedback_scenario_id": "",
            "status_filter": "",
            "content_type_filter": "",
        },
    )
    assert resp.status_code == 200
    assert q.get_job(conn, j1)["status"] == "rejected"
    assert q.get_job(conn, j2)["status"] == "rejected"


def test_job_bulk_feedback_defaults_to_each_jobs_own_best_scenario(client, conn):
    sid, j1, scenario_a = _seed(conn)
    scenario_b = q.insert_scenario(conn, "Other", "")
    j2 = q.insert_job(conn, source_id=sid, url="http://finn.no/job/2", title="Data Eng", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, j2, simplified_content="clean", content_type="job_posting", summary="Also good")
    q.upsert_job_score(conn, j2, scenario_b, 0.6, "Decent match", "hash2")
    client.post(
        "/jobs/bulk-feedback",
        data={"job_ids": [j1, j2], "status": "invalid", "feedback_scenario_id": "", "status_filter": "", "content_type_filter": ""},
    )
    assert q.get_job(conn, j1)["feedback_scenario_id"] == scenario_a
    assert q.get_job(conn, j2)["feedback_scenario_id"] == scenario_b


def test_job_bulk_feedback_explicit_scenario_overrides_all(client, conn):
    sid, j1, scenario_a = _seed(conn)
    scenario_b = q.insert_scenario(conn, "Other", "")
    j2 = q.insert_job(conn, source_id=sid, url="http://finn.no/job/2", title="Data Eng", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, j2, simplified_content="clean", content_type="job_posting", summary="Also good")
    q.upsert_job_score(conn, j2, scenario_b, 0.6, "Decent match", "hash2")
    client.post(
        "/jobs/bulk-feedback",
        data={"job_ids": [j1, j2], "status": "rejected", "feedback_scenario_id": scenario_b, "status_filter": "", "content_type_filter": ""},
    )
    assert q.get_job(conn, j1)["feedback_scenario_id"] == scenario_b
    assert q.get_job(conn, j2)["feedback_scenario_id"] == scenario_b


def test_job_bulk_feedback_note_is_optional(client, conn):
    sid, j1, scenario_id = _seed(conn)
    resp = client.post(
        "/jobs/bulk-feedback",
        data={"job_ids": [j1], "status": "accepted", "feedback_scenario_id": "", "status_filter": "", "content_type_filter": ""},
    )
    assert resp.status_code == 200
    assert q.get_job(conn, j1)["feedback_note"] is None


def test_job_bulk_feedback_returns_filtered_content_reflecting_removed_jobs(client, conn):
    sid, j1, scenario_id = _seed(conn)
    resp = client.post(
        "/jobs/bulk-feedback",
        data={"job_ids": [j1], "status": "rejected", "feedback_scenario_id": "", "status_filter": "", "content_type_filter": ""},
    )
    assert resp.status_code == 200
    assert "ML Eng" not in resp.text
    assert "No jobs found" in resp.text


def test_job_bulk_feedback_respects_status_filter_for_response(client, conn):
    sid, j1, scenario_id = _seed(conn)
    q.update_job_feedback(conn, j1, "accepted", "", feedback_scenario_id=scenario_id)
    j2 = q.insert_job(conn, source_id=sid, url="http://finn.no/job/2", title="Data Eng", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, j2, simplified_content="clean", content_type="job_posting", summary="Also good")
    resp = client.post(
        "/jobs/bulk-feedback",
        data={"job_ids": [j2], "status": "accepted", "feedback_scenario_id": "", "status_filter": "accepted", "content_type_filter": ""},
    )
    assert resp.status_code == 200
    assert "ML Eng" in resp.text
    assert "Data Eng" in resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_routes_jobs.py -k bulk_feedback -v`
Expected: FAIL — `POST /jobs/bulk-feedback` doesn't exist (404 for all six tests).

- [ ] **Step 3: Implement**

In `app/routes/jobs.py`, add the helper right after `_enrich_jobs` and before `job_list`:

```python
def _get_filtered_jobs(conn: sqlite3.Connection, status: str | None, content_type: str | None) -> list[dict]:
    if status is None and content_type is None:
        return q.get_jobs(conn, status="new")
    return q.get_jobs(conn, status=status, content_type=content_type)
```

Refactor `job_list` to use it, pass `scenarios`, and normalize `status` to the *effective* filter (mirroring `_get_filtered_jobs`'s own default-to-"new" branching) so Task 8's hidden filter field always reflects what's actually being viewed:

```python
@router.get("/", response_class=HTMLResponse)
def job_list(
    request: Request,
    status: str | None = None,
    content_type: str | None = None,
    conn: sqlite3.Connection = Depends(get_db),
):
    jobs = _enrich_jobs(conn, _get_filtered_jobs(conn, status, content_type))
    counts = q.get_job_counts(conn)
    scenarios = q.get_scenarios(conn)
    effective_status = status if (status is not None or content_type is not None) else "new"
    return templates.TemplateResponse(
        request, "jobs/list.html",
        {"jobs": jobs, "counts": counts, "scenarios": scenarios, "status": effective_status, "content_type": content_type},
    )
```

Add the bulk route at the end of the file:

```python
@router.post("/jobs/bulk-feedback", response_class=HTMLResponse)
def job_bulk_feedback(
    request: Request,
    job_ids: list[int] = Form(...),
    status: str = Form(...),
    note: str | None = Form(None),
    feedback_scenario_id: str = Form(""),
    status_filter: str | None = Form(None),
    content_type_filter: str | None = Form(None),
    conn: sqlite3.Connection = Depends(get_db),
):
    for job_id in job_ids:
        scenario_id = (
            int(feedback_scenario_id) if feedback_scenario_id
            else q.get_job(conn, job_id)["best_scenario_id"]
        )
        q.update_job_feedback(conn, job_id, status, note, scenario_id)

    status_filter = status_filter or None
    content_type_filter = content_type_filter or None
    jobs = _enrich_jobs(conn, _get_filtered_jobs(conn, status_filter, content_type_filter))
    counts = q.get_job_counts(conn)
    scenarios = q.get_scenarios(conn)
    return templates.TemplateResponse(
        request, "jobs/_content.html",
        {"jobs": jobs, "counts": counts, "scenarios": scenarios},
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_routes_jobs.py -v`
Expected: PASS — all tests in the file, including every pre-existing one (the `job_list` refactor must not change any observable output).

- [ ] **Step 5: Commit**

```bash
git add app/routes/jobs.py tests/test_routes_jobs.py
git commit -m "feat: add bulk job feedback backend route"
```

---

### Task 8: Wire the bulk-form shell and visible bulk bar UI

**Files:**
- Modify: `app/templates/jobs/list.html`
- Modify: `app/templates/jobs/_content.html`
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: `scenarios`, `status`, `content_type` context from `job_list` (Task 7 already added these to the route); `q.get_scenarios` result shape `{"id", "name", ...}`.
- Produces: a persistent `<form id="bulk-form">` in `list.html` (outside `#jobs-content`, survives HTMX swaps of the content partial) carrying the HTMX attributes; a `.bulk-bar` element inside `_content.html` whose controls associate to that form via `form="bulk-form"`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_routes_jobs.py`:

```python
def test_job_list_has_persistent_bulk_form_shell(client, conn):
    resp = client.get("/")
    assert resp.status_code == 200
    assert '<form id="bulk-form" hx-post="/jobs/bulk-feedback" hx-target="#jobs-content" hx-swap="innerHTML">' in resp.text
    assert '<input type="hidden" name="status_filter" value="new">' in resp.text


def test_job_list_bulk_bar_has_scenario_select_and_actions(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Keep each job's own scenario" in resp.text
    assert 'form="bulk-form" name="status" value="accepted"' in resp.text
    assert 'form="bulk-form" name="status" value="rejected"' in resp.text
    assert 'form="bulk-form" name="status" value="invalid"' in resp.text
    assert 'title="Does not match your criteria — feeds back into scenario tuning."' in resp.text
    assert 'title="Not a usable posting (expired, spam, duplicate, wrong content) — does not affect scenario criteria."' in resp.text


def test_job_list_bulk_form_reflects_active_filter(client, conn):
    resp = client.get("/?status=accepted")
    assert resp.status_code == 200
    assert '<input type="hidden" name="status_filter" value="accepted">' in resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_routes_jobs.py -k "bulk_form or bulk_bar" -v`
Expected: FAIL — none of this markup exists yet.

- [ ] **Step 3: Implement**

Replace `app/templates/jobs/list.html` with:

```html
{% extends "base.html" %}
{% block title %}Jobs — Job Seek{% endblock %}
{% block content %}
<h1>Jobs</h1>
<form id="bulk-form" hx-post="/jobs/bulk-feedback" hx-target="#jobs-content" hx-swap="innerHTML">
  <input type="hidden" name="status_filter" value="{{ status or '' }}">
  <input type="hidden" name="content_type_filter" value="{{ content_type or '' }}">
</form>
<div id="jobs-content">
  {% include "jobs/_content.html" %}
</div>
{% endblock %}
```

`status` here is already the *effective* filter computed by `job_list` in Task 7 (defaults to `"new"` on the bare `/` view), so this hidden field just echoes it straight through — no template-side branching needed.

In `app/templates/jobs/_content.html`, add the bulk bar between the filter bar and the job loop:

```html
<div class="filter-bar">
  <a href="/">New ({{ counts.new }})</a>
  <a href="/?status=accepted">Accepted ({{ counts.accepted }})</a>
  <a href="/?status=rejected">Rejected ({{ counts.rejected }})</a>
  <a href="/?status=invalid">Invalid ({{ counts.invalid }})</a>
  <a href="/?content_type=lead">Leads ({{ counts.lead }})</a>
</div>
<div class="bulk-bar">
  <span class="bulk-count"></span>
  <button type="button" class="bulk-clear">Clear selection</button>
  <label>Scenario:
    <select name="feedback_scenario_id" form="bulk-form">
      <option value="">Keep each job's own scenario</option>
      {% for s in scenarios %}
        <option value="{{ s.id }}">{{ s.name }}</option>
      {% endfor %}
    </select>
  </label>
  <label>Note (optional):
    <textarea name="note" form="bulk-form" placeholder="Optional note for all selected jobs"></textarea>
  </label>
  <div class="actions" role="group" aria-label="Bulk decision">
    <button type="submit" form="bulk-form" name="status" value="accepted" class="btn btn-accept">Accept</button>
    <button type="submit" form="bulk-form" name="status" value="rejected" class="btn btn-reject" title="Does not match your criteria — feeds back into scenario tuning.">Reject</button>
    <button type="submit" form="bulk-form" name="status" value="invalid" class="btn btn-invalid" title="Not a usable posting (expired, spam, duplicate, wrong content) — does not affect scenario criteria.">Invalid</button>
  </div>
</div>
{% if jobs %}
  {% for job in jobs %}
    {% include "jobs/_row.html" %}
  {% endfor %}
{% else %}
  <p>No jobs found.</p>
{% endif %}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_routes_jobs.py -v`
Expected: PASS — all tests in the file.

- [ ] **Step 5: Commit**

```bash
git add app/templates/jobs/list.html app/templates/jobs/_content.html tests/test_routes_jobs.py
git commit -m "feat: wire bulk-form shell and bulk action bar into job list"
```

---

### Task 9: Sticky bulk bar visibility, live count, and clear-selection behavior

**Files:**
- Modify: `app/templates/base.html` (CSS in `<style>`, new inline `<script>` block)
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: `.bulk-bar`, `.bulk-count`, `.bulk-clear`, `input[name="job_ids"]` markup produced by Tasks 6 and 8.
- Produces: nothing consumed by other tasks — this is the last task.

- [ ] **Step 1: Write the failing test**

```python
def test_base_page_includes_bulk_bar_visibility_and_count_script(client, conn):
    resp = client.get("/")
    assert resp.status_code == 200
    assert ':has(input[name="job_ids"]:checked)' in resp.text
    assert "bulk-count" in resp.text
    assert "bulk-clear" in resp.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_routes_jobs.py -k bulk_bar_visibility_and_count -v`
Expected: FAIL — none of this CSS/JS exists yet (note: `bulk-count`/`bulk-clear` class names already appear in the bar markup from Task 8, so scope this assertion to the `:has()` rule specifically failing, then re-verify after Step 3 that the CSS/JS additions are what's under test).

- [ ] **Step 3: Implement**

In `app/templates/base.html`, add to the end of the `<style>` block (after `.filter-bar a { margin-right: 0.5rem; }`):

```css
    .bulk-bar { display: none; position: sticky; bottom: 0; gap: 0.75rem; align-items: center;
      background: #fff; border-top: 2px solid #0066cc; padding: 0.75rem; margin: 1rem -1rem 0; }
    #jobs-content:has(input[name="job_ids"]:checked) .bulk-bar { display: flex; flex-wrap: wrap; }
    .bulk-bar select, .bulk-bar textarea { max-width: 240px; }
```

Add a new `<script>` block right after the existing one, before `</body>`:

```html
  <script>
    (function () {
      function updateCount() {
        var checked = document.querySelectorAll('input[name="job_ids"]:checked');
        document.querySelectorAll(".bulk-count").forEach(function (el) {
          el.textContent = checked.length + " selected";
        });
      }
      document.body.addEventListener("change", function (evt) {
        if (evt.target.name === "job_ids") updateCount();
      });
      document.body.addEventListener("click", function (evt) {
        if (!evt.target.classList.contains("bulk-clear")) return;
        document.querySelectorAll('input[name="job_ids"]:checked').forEach(function (cb) {
          cb.checked = false;
        });
        updateCount();
      });
      document.body.addEventListener("htmx:afterSwap", updateCount);
    })();
  </script>
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -v`
Expected: PASS — full suite, no regressions anywhere in the project.

- [ ] **Step 5: Commit**

```bash
git add app/templates/base.html tests/test_routes_jobs.py
git commit -m "feat: sticky bulk bar visibility, live selection count, clear selection"
```

---

## Manual Verification (not a plan task — done after all tasks pass)

Start the dev server (`uv run uvicorn app.main:app --reload --port 8000`) and in a browser:

1. Check 2+ job checkboxes on the "New" list — the bulk bar should appear at the bottom, showing a live "N selected" count.
2. Hover Reject/Invalid in the bulk bar and in a single-job expanded form — tooltips should show the explanatory copy.
3. Submit a bulk Reject with no note and no scenario override — jobs should disappear from the "New" view; check the "Rejected" tab shows them with each job's own best-fit scenario.
4. Bulk-select jobs with different best-fit scenarios, pick an explicit scenario override, submit — verify (e.g. via the Scenarios page's refine-criteria flow, or direct DB check) that all selected jobs got the same `feedback_scenario_id`.
5. Click "Clear selection" — all checkboxes uncheck and the bulk bar hides.
6. Expand a single job (click its row), confirm the note field is optional (submit without typing anything), then re-expand another job and click its header again to fold it back to the row view without submitting.
7. Bulk-mark a job "Invalid" with a note, then check the Scenarios page's criteria-refinement flow does not pick up that note (only "Reject" notes should feed it).
