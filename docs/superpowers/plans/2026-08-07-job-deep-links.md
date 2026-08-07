# Job Deep Links Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every job a permalink page (`GET /jobs/{job_id}`), and make Reset/Pass-as-new update the job's row in place instead of reloading the whole page — including flagging when the update moves the job out of the tab the user was viewing.

**Architecture:** Server-rendered Jinja2 + HTMX, no build step, no new JS mechanisms. Reset/Pass-as-new reuse the existing `HTML:`-streaming-chunk + `data-progress-oob` convention already used by `scenarios.py`'s "refine criteria" flow (`applyOob()` in `base.html`, unmodified). The active list filter is threaded through the expand/collapse/reset/pass-as-new round trip as two new context variables, `filter_status`/`filter_content_type` — kept deliberately separate from the pre-existing `status`/`content_type` template variables (which job_list normalizes to a display default of `"new"` and are only used for tab-highlighting) so the raw, un-normalized filter can be fed straight into `_get_filtered_jobs` for an accurate "does this job still belong here" check.

**Tech Stack:** FastAPI, Jinja2 (`app/template_env.py`, default `trim_blocks=False`/`lstrip_blocks=False`), HTMX 1.9.12, sqlite3, pytest + `fastapi.testclient.TestClient`.

## Global Constraints

- No new dependencies, no new JS files/mechanisms — reuse `data-progress-oob`/`applyOob()` exactly as `scenarios.py`/`base.html` already implement it.
- Tests go in `tests/test_routes_jobs.py`, following that file's existing style (`_seed(conn)` helper, `client`/`conn` fixtures from `tests/conftest.py`, `patch("app.routes.jobs.run_reprocess_job", ...)` / `patch("app.routes.jobs.run_pass_as_new", ...)` for stream tests).
- Commit after each task (project convention — see `CLAUDE.md` "Commit frequently").
- Run the full suite (`uv run pytest -q`) at the end of every task, not just the new tests, to catch incidental breakage.

---

### Task 1: Deep link page + permalink icons

**Files:**
- Modify: `app/routes/jobs.py` (add `job_detail` route)
- Create: `app/templates/jobs/detail.html`
- Modify: `app/templates/jobs/_feedback.html` (add `is_detail_page` guard around the bulk-select checkbox, add permalink icon)
- Modify: `app/templates/jobs/_row.html` (add permalink icon)
- Modify: `app/templates/base.html` (add `.permalink-icon` CSS rule)
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Produces: `GET /jobs/{job_id}` — 200 with job detail content, 404 if missing. Template context flag `is_detail_page: bool` (only set `True` by this route; absent/falsy everywhere else, which is what later tasks rely on to detect "not the detail page").

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_routes_jobs.py`:

```python
def test_job_detail_returns_200_with_job_content(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}")
    assert resp.status_code == 200
    assert "ML Eng" in resp.text
    assert "Accept" in resp.text
    assert "Reject" in resp.text


def test_job_detail_unknown_job_returns_404(client, conn):
    resp = client.get("/jobs/999")
    assert resp.status_code == 404


def test_job_detail_has_back_to_list_link(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}")
    assert resp.status_code == 200
    assert 'href="/jobs"' in resp.text


def test_job_detail_omits_bulk_select_checkbox(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}")
    assert resp.status_code == 200
    assert '<label class="job-select-wrap">' not in resp.text
    assert 'name="job_ids"' not in resp.text


def test_job_expand_still_has_bulk_select_checkbox(client, conn):
    # Guards against the is_detail_page flag leaking into the normal list flow.
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert '<label class="job-select-wrap">' in resp.text


def test_job_list_row_has_permalink_icon(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get("/jobs")
    assert resp.status_code == 200
    assert f'href="/jobs/{jid}" class="permalink-icon"' in resp.text


def test_job_expand_has_permalink_icon(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert f'href="/jobs/{jid}" class="permalink-icon"' in resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_routes_jobs.py -k "job_detail or permalink_icon" -v`
Expected: FAIL — `test_job_detail_*` fail with 404 (no route yet); `test_job_expand_still_has_bulk_select_checkbox` passes already (no regression yet, that's fine); `*_has_permalink_icon` fail (string not found).

- [ ] **Step 3: Add the `job_detail` route**

In `app/routes/jobs.py`, add this route directly after `job_list` (before `job_expand`):

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
    return templates.TemplateResponse(
        request, "jobs/detail.html",
        {"job": job, "scenarios": scenarios, "job_scores": job_scores, "is_detail_page": True},
    )
```

- [ ] **Step 4: Create the detail page template**

Create `app/templates/jobs/detail.html`:

```html
{% extends "base.html" %}
{% block title %}{{ job.title or "Job" }} — Job Seek{% endblock %}
{% block content %}
<p><a href="/jobs">&larr; Back to list</a></p>
{% include "jobs/_feedback.html" %}
{% endblock %}
```

- [ ] **Step 5: Guard the bulk-select checkbox in `_feedback.html`**

In `app/templates/jobs/_feedback.html`, wrap the checkbox `<label>` (currently lines 3-7):

Before:
```html
<article class="job-row" id="job-{{ job.id }}">
  <label class="job-select-wrap">
    <input type="checkbox" class="job-select" name="job_ids" value="{{ job.id }}" form="bulk-form"
      id="job-select-{{ job.id }}" hx-preserve="true"
      aria-label="Select {{ job.title or 'this job' }} for bulk action" onclick="event.stopPropagation()">
  </label>
```

After:
```html
<article class="job-row" id="job-{{ job.id }}">
  {% if not is_detail_page %}
  <label class="job-select-wrap">
    <input type="checkbox" class="job-select" name="job_ids" value="{{ job.id }}" form="bulk-form"
      id="job-select-{{ job.id }}" hx-preserve="true"
      aria-label="Select {{ job.title or 'this job' }} for bulk action" onclick="event.stopPropagation()">
  </label>
  {% endif %}
```

- [ ] **Step 6: Add permalink icons**

In `app/templates/jobs/_row.html`, add the icon right after the opening `<article>` tag's attributes, before `{{ macros.meta_tags(job) }}` (it's inside the whole-article click-to-expand zone, so it needs `stopPropagation`):

Before:
```html
  hx-trigger="click, keyup[key=='Enter']">
  <label class="job-select-wrap">
    <input type="checkbox" class="job-select" name="job_ids" value="{{ job.id }}" form="bulk-form"
      id="job-select-{{ job.id }}" hx-preserve="true"
      aria-label="Select {{ job.title or 'this job' }} for bulk action" onclick="event.stopPropagation()">
  </label>
  {{ macros.meta_tags(job) }}
```

After:
```html
  hx-trigger="click, keyup[key=='Enter']">
  <label class="job-select-wrap">
    <input type="checkbox" class="job-select" name="job_ids" value="{{ job.id }}" form="bulk-form"
      id="job-select-{{ job.id }}" hx-preserve="true"
      aria-label="Select {{ job.title or 'this job' }} for bulk action" onclick="event.stopPropagation()">
  </label>
  <a href="/jobs/{{ job.id }}" class="permalink-icon" title="Direct link to this job" aria-label="Direct link to this job" onclick="event.stopPropagation()">&#128279;</a>
  {{ macros.meta_tags(job) }}
```

In `app/templates/jobs/_feedback.html`, add the icon to the `<p>` line with the company/original-link (this is outside the click-to-collapse `<div>`, so no `stopPropagation` needed):

Before:
```html
  <p>
    {% if job.company %}<span>· {{ job.company }}</span>{% endif %}
    <a href="{{ job.url }}" target="_blank" rel="noopener">↗ original</a>
  </p>
```

After:
```html
  <p>
    {% if job.company %}<span>· {{ job.company }}</span>{% endif %}
    <a href="{{ job.url }}" target="_blank" rel="noopener">↗ original</a>
    <a href="/jobs/{{ job.id }}" class="permalink-icon" title="Direct link to this job" aria-label="Direct link to this job">&#128279;</a>
  </p>
```

In `app/templates/base.html`, add a CSS rule near the other small-element rules (e.g. right after the `.job-hook` rule):

Before:
```css
    .job-hook { margin:0.25rem 0 0; color:#444; }
```

After:
```css
    .job-hook { margin:0.25rem 0 0; color:#444; }
    .permalink-icon { text-decoration:none; font-size:0.85em; }
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/test_routes_jobs.py -k "job_detail or permalink_icon" -v`
Expected: PASS (all 7 new tests)

Run: `uv run pytest -q`
Expected: PASS (full suite, no regressions)

- [ ] **Step 8: Commit**

```bash
git add app/routes/jobs.py app/templates/jobs/detail.html app/templates/jobs/_feedback.html app/templates/jobs/_row.html app/templates/base.html tests/test_routes_jobs.py
git commit -m "feat: add per-job permalink page and row permalink icons"
```

---

### Task 2: Thread the active filter through expand/collapse

**Files:**
- Modify: `app/routes/jobs.py` (add `_filter_context` helper, update `job_list`, `job_expand`, `job_collapse`)
- Modify: `app/templates/jobs/_row.html` (expand link carries filter)
- Modify: `app/templates/jobs/_feedback.html` (collapse link carries filter)
- Test: `tests/test_routes_jobs.py` (add new tests, fix one existing assertion)

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `_filter_context(request: Request) -> dict` — returns `{}` if neither `status` nor `content_type` is present in `request.query_params`, else `{"filter_status": <str|None>, "filter_content_type": <str|None>}` (empty-string query values normalized to `None`). Template variables `filter_status`/`filter_content_type`, distinct from the existing `status`/`content_type` variables — later tasks (3, 4) rely on this exact dict shape and key names.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_routes_jobs.py`:

```python
def test_job_list_default_tab_expand_link_carries_empty_filter_params(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get("/jobs")
    assert resp.status_code == 200
    assert f'hx-get="/jobs/{jid}/expand?status=&content_type="' in resp.text


def test_job_list_filtered_tab_expand_link_carries_filter_params(client, conn):
    sid, jid, scenario_id = _seed(conn)
    q.update_job_feedback(conn, jid, "accepted", "")
    resp = client.get("/jobs?status=accepted")
    assert resp.status_code == 200
    assert f'hx-get="/jobs/{jid}/expand?status=accepted&content_type="' in resp.text


def test_job_expand_forwards_filter_to_collapse_link(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}/expand?status=accepted&content_type=")
    assert resp.status_code == 200
    assert f'hx-get="/jobs/{jid}/collapse?status=accepted&content_type="' in resp.text


def test_job_expand_without_filter_query_omits_collapse_filter_params(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert f'hx-get="/jobs/{jid}/collapse"' in resp.text
    assert "collapse?status=" not in resp.text
```

Fix the now-outdated assertion in `test_job_list_card_is_clickable_and_has_no_details_button` (around line 105-111):

Before:
```python
def test_job_list_card_is_clickable_and_has_no_details_button(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get("/jobs")
    assert resp.status_code == 200
    assert f'hx-get="/jobs/{jid}/expand"' in resp.text
    assert "Details" not in resp.text
    assert 'role="button"' in resp.text
```

After:
```python
def test_job_list_card_is_clickable_and_has_no_details_button(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get("/jobs")
    assert resp.status_code == 200
    assert f'hx-get="/jobs/{jid}/expand?status=&content_type="' in resp.text
    assert "Details" not in resp.text
    assert 'role="button"' in resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_routes_jobs.py -k "filter_params or forwards_filter or omits_collapse or card_is_clickable" -v`
Expected: FAIL (query params not yet emitted anywhere)

- [ ] **Step 3: Add `_filter_context` helper and wire it into the routes**

In `app/routes/jobs.py`, add this helper directly after `_get_filtered_jobs`:

```python
def _filter_context(request: Request) -> dict:
    if "status" not in request.query_params and "content_type" not in request.query_params:
        return {}
    return {
        "filter_status": request.query_params.get("status") or None,
        "filter_content_type": request.query_params.get("content_type") or None,
    }
```

Update `job_list` to also pass the raw filter (kept separate from the existing `status`/`content_type` display variables):

Before:
```python
    effective_status = status if (status is not None or content_type is not None) else "new"
    return templates.TemplateResponse(
        request, "jobs/list.html",
        {
            "jobs": jobs, "counts": counts, "scenarios": scenarios,
            "status": effective_status, "content_type": content_type,
        },
    )
```

After:
```python
    effective_status = status if (status is not None or content_type is not None) else "new"
    return templates.TemplateResponse(
        request, "jobs/list.html",
        {
            "jobs": jobs, "counts": counts, "scenarios": scenarios,
            "status": effective_status, "content_type": content_type,
            "filter_status": status, "filter_content_type": content_type,
        },
    )
```

Update `job_expand`:

Before:
```python
@router.get("/jobs/{job_id}/expand", response_class=HTMLResponse)
def job_expand(job_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    job = q.get_job(conn, job_id)
    sources = {s["id"]: s for s in q.get_sources(conn)}
    job["source_name"] = sources.get(job["source_id"], {}).get("name", "")
    scenarios = q.get_scenarios(conn)
    job_scores = q.get_job_scores(conn, job_id)
    return templates.TemplateResponse(request, "jobs/_feedback.html", {"job": job, "scenarios": scenarios, "job_scores": job_scores})
```

After:
```python
@router.get("/jobs/{job_id}/expand", response_class=HTMLResponse)
def job_expand(job_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    job = q.get_job(conn, job_id)
    sources = {s["id"]: s for s in q.get_sources(conn)}
    job["source_name"] = sources.get(job["source_id"], {}).get("name", "")
    scenarios = q.get_scenarios(conn)
    job_scores = q.get_job_scores(conn, job_id)
    context = {"job": job, "scenarios": scenarios, "job_scores": job_scores}
    context.update(_filter_context(request))
    return templates.TemplateResponse(request, "jobs/_feedback.html", context)
```

Update `job_collapse`:

Before:
```python
@router.get("/jobs/{job_id}/collapse", response_class=HTMLResponse)
def job_collapse(job_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    job = q.get_job(conn, job_id)
    sources = {s["id"]: s for s in q.get_sources(conn)}
    job["source_name"] = sources.get(job["source_id"], {}).get("name", "")
    return templates.TemplateResponse(request, "jobs/_row.html", {"job": job})
```

After:
```python
@router.get("/jobs/{job_id}/collapse", response_class=HTMLResponse)
def job_collapse(job_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    job = q.get_job(conn, job_id)
    sources = {s["id"]: s for s in q.get_sources(conn)}
    job["source_name"] = sources.get(job["source_id"], {}).get("name", "")
    context = {"job": job}
    context.update(_filter_context(request))
    return templates.TemplateResponse(request, "jobs/_row.html", context)
```

- [ ] **Step 4: Update the templates' hx-get URLs**

In `app/templates/jobs/_row.html`:

Before:
```html
  hx-get="/jobs/{{ job.id }}/expand"
```

After:
```html
  hx-get="/jobs/{{ job.id }}/expand{% if filter_status is defined %}?status={{ filter_status or '' }}&content_type={{ filter_content_type or '' }}{% endif %}"
```

In `app/templates/jobs/_feedback.html`:

Before:
```html
    hx-get="/jobs/{{ job.id }}/collapse"
```

After:
```html
    hx-get="/jobs/{{ job.id }}/collapse{% if filter_status is defined %}?status={{ filter_status or '' }}&content_type={{ filter_content_type or '' }}{% endif %}"
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_routes_jobs.py -v`
Expected: PASS (all tests in the file, including the fixed assertion)

Run: `uv run pytest -q`
Expected: PASS (full suite)

- [ ] **Step 6: Commit**

```bash
git add app/routes/jobs.py app/templates/jobs/_row.html app/templates/jobs/_feedback.html tests/test_routes_jobs.py
git commit -m "feat: thread active list filter through job expand/collapse"
```

---

### Task 3: Reset / Pass-as-new update the row in place

**Files:**
- Modify: `app/routes/jobs.py` (add `_render_job_feedback_html` helper, update `job_reset`, `job_pass_as_new`)
- Modify: `app/templates/jobs/_feedback.html` (buttons get `data-progress-oob` + filter query string)
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: `_filter_context(request)` from Task 2, `filter_status`/`filter_content_type` template variables from Task 2.
- Produces: `_render_job_feedback_html(conn, request, job_id, filter_ctx: dict) -> str` — renders `_feedback.html` for the job's current DB state, merging `filter_ctx` into the template context. Task 4 extends this function to also compute `stale_badge`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_routes_jobs.py`:

```python
def test_job_expand_reset_button_has_progress_oob_attribute(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert f'data-progress-url="/jobs/{jid}/reset"' in resp.text
    assert "data-progress-oob" in resp.text


def test_job_reset_stream_ends_with_html_chunk_for_updated_row(client, conn):
    sid, jid, scenario_id = _seed(conn)
    with patch("app.routes.jobs.run_reprocess_job", side_effect=_fake_run_reprocess_job):
        resp = client.post(f"/jobs/{jid}/reset")
    assert resp.status_code == 200
    assert f'HTML:<article class="job-row" id="job-{jid}">' in resp.text


def test_job_pass_as_new_stream_ends_with_html_chunk_for_updated_row(client, conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "http")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/failed", title="Failed Gate", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="job_posting", summary="role")
    scenario_id = q.insert_scenario(conn, "A", "")
    q.upsert_job_score(conn, jid, scenario_id, 0.2, "too junior", "hash1")
    with patch("app.routes.jobs.run_pass_as_new", side_effect=_fake_run_pass_as_new):
        resp = client.post(f"/jobs/{jid}/pass-as-new")
    assert resp.status_code == 200
    assert f'HTML:<article class="job-row" id="job-{jid}">' in resp.text


def test_job_reset_with_filter_query_forwards_it_into_rendered_row(client, conn):
    sid, jid, scenario_id = _seed(conn)
    q.update_job_feedback(conn, jid, "accepted", "")
    with patch("app.routes.jobs.run_reprocess_job", side_effect=_fake_run_reprocess_job):
        resp = client.post(f"/jobs/{jid}/reset?status=accepted&content_type=")
    assert resp.status_code == 200
    # The re-rendered row's own collapse/reset links must keep carrying the filter forward.
    assert f'jobs/{jid}/collapse?status=accepted&content_type=' in resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_routes_jobs.py -k "progress_oob_attribute or ends_with_html_chunk or forwards_it_into_rendered_row" -v`
Expected: FAIL (no `data-progress-oob` attribute, no `HTML:` chunk yet)

- [ ] **Step 3: Add the render helper**

In `app/routes/jobs.py`, add directly after `_filter_context`:

```python
def _render_job_feedback_html(conn: sqlite3.Connection, request: Request, job_id: int, filter_ctx: dict) -> str:
    job = q.get_job(conn, job_id)
    sources = {s["id"]: s for s in q.get_sources(conn)}
    job["source_name"] = sources.get(job["source_id"], {}).get("name", "")
    scenarios = q.get_scenarios(conn)
    job_scores = q.get_job_scores(conn, job_id)
    context = {"job": job, "scenarios": scenarios, "job_scores": job_scores}
    context.update(filter_ctx)
    return templates.get_template("jobs/_feedback.html").render(request=request, **context)
```

- [ ] **Step 4: Update `job_reset` and `job_pass_as_new` to yield the final HTML chunk**

Before (`job_reset`):
```python
@router.post("/jobs/{job_id}/reset")
def job_reset(
    job_id: int,
    conn: sqlite3.Connection = Depends(get_db),
    client: openai.OpenAI = Depends(get_ai_client),
    model: str = Depends(get_model),
):
    job = q.get_job(conn, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    scenarios = q.get_scenarios(conn)
    profile = q.get_profile(conn)

    def stream():
        gen = run_reprocess_job(conn, client, model, job, scenarios, profile)
        try:
            while True:
                yield next(gen) + "\n"
        except StopIteration:
            pass

    return StreamingResponse(stream(), media_type="text/plain")
```

After:
```python
@router.post("/jobs/{job_id}/reset")
def job_reset(
    job_id: int,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    client: openai.OpenAI = Depends(get_ai_client),
    model: str = Depends(get_model),
):
    job = q.get_job(conn, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    scenarios = q.get_scenarios(conn)
    profile = q.get_profile(conn)
    filter_ctx = _filter_context(request)

    def stream():
        gen = run_reprocess_job(conn, client, model, job, scenarios, profile)
        try:
            while True:
                yield next(gen) + "\n"
        except StopIteration:
            pass
        html = _render_job_feedback_html(conn, request, job_id, filter_ctx)
        yield "HTML:" + html.replace("\n", "")

    return StreamingResponse(stream(), media_type="text/plain")
```

Before (`job_pass_as_new`):
```python
@router.post("/jobs/{job_id}/pass-as-new")
def job_pass_as_new(
    job_id: int,
    conn: sqlite3.Connection = Depends(get_db),
    client: openai.OpenAI = Depends(get_ai_client),
    model: str = Depends(get_model),
):
    job = q.get_job(conn, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    profile = q.get_profile(conn)

    def stream():
        gen = run_pass_as_new(conn, client, model, job, profile)
        try:
            while True:
                yield next(gen) + "\n"
        except StopIteration:
            pass

    return StreamingResponse(stream(), media_type="text/plain")
```

After:
```python
@router.post("/jobs/{job_id}/pass-as-new")
def job_pass_as_new(
    job_id: int,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    client: openai.OpenAI = Depends(get_ai_client),
    model: str = Depends(get_model),
):
    job = q.get_job(conn, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    profile = q.get_profile(conn)
    filter_ctx = _filter_context(request)

    def stream():
        gen = run_pass_as_new(conn, client, model, job, profile)
        try:
            while True:
                yield next(gen) + "\n"
        except StopIteration:
            pass
        html = _render_job_feedback_html(conn, request, job_id, filter_ctx)
        yield "HTML:" + html.replace("\n", "")

    return StreamingResponse(stream(), media_type="text/plain")
```

- [ ] **Step 5: Add `data-progress-oob` and the filter query string to the buttons**

In `app/templates/jobs/_feedback.html`, the Advanced section:

Before:
```html
  <details class="job-advanced">
    <summary>Advanced&hellip;</summary>
    {% if job.content_type == "job_posting" and job.scored_gate_count and not job.passed_gate_count and not job.gate_override %}
      <button type="button" class="btn"
        data-progress-url="/jobs/{{ job.id }}/pass-as-new"
        data-progress-display="#reset-progress-{{ job.id }}"
        title="Bypass the gate threshold for this job and file it as New, keeping its existing scores as a record of why it failed.">
        Pass as new
      </button>
    {% endif %}
    <button type="button" class="btn btn-reset"
      data-progress-url="/jobs/{{ job.id }}/reset"
      data-progress-display="#reset-progress-{{ job.id }}"
      title="Wipe the simplified content, classification, summary and scores, then immediately rerun the full pipeline from the original posting text.">
      Reset to new
    </button>
  </details>
```

After:
```html
  <details class="job-advanced">
    <summary>Advanced&hellip;</summary>
    {% if job.content_type == "job_posting" and job.scored_gate_count and not job.passed_gate_count and not job.gate_override %}
      <button type="button" class="btn"
        data-progress-url="/jobs/{{ job.id }}/pass-as-new{% if filter_status is defined %}?status={{ filter_status or '' }}&content_type={{ filter_content_type or '' }}{% endif %}"
        data-progress-oob="1"
        data-progress-display="#reset-progress-{{ job.id }}"
        title="Bypass the gate threshold for this job and file it as New, keeping its existing scores as a record of why it failed.">
        Pass as new
      </button>
    {% endif %}
    <button type="button" class="btn btn-reset"
      data-progress-url="/jobs/{{ job.id }}/reset{% if filter_status is defined %}?status={{ filter_status or '' }}&content_type={{ filter_content_type or '' }}{% endif %}"
      data-progress-oob="1"
      data-progress-display="#reset-progress-{{ job.id }}"
      title="Wipe the simplified content, classification, summary and scores, then immediately rerun the full pipeline from the original posting text.">
      Reset to new
    </button>
  </details>
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_routes_jobs.py -v`
Expected: PASS (all tests in the file)

Run: `uv run pytest -q`
Expected: PASS (full suite)

- [ ] **Step 7: Commit**

```bash
git add app/routes/jobs.py app/templates/jobs/_feedback.html tests/test_routes_jobs.py
git commit -m "feat: reset and pass-as-new update the job row in place instead of reloading"
```

---

### Task 4: "Moved to…" stale badge

**Files:**
- Modify: `app/routes/jobs.py` (add `_stale_badge`, extend `_render_job_feedback_html`)
- Modify: `app/templates/jobs/_feedback.html` (render the badge)
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: `_get_filtered_jobs(conn, status, content_type)` (existing), `filter_ctx` shape from Task 2/3.
- Produces: `_stale_badge(conn, job: dict, status: str | None, content_type: str | None) -> dict | None` — `None` if the job still matches the filter; else `{"label": str, "href": str | None}`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_routes_jobs.py`:

```python
def _fake_run_reprocess_job_to_passing(conn, client, model, job, scenarios, profile, progress_prefix=""):
    yield f"{progress_prefix}Reprocessing: {job['url']}"
    q.reset_job(conn, job["id"])
    q.update_job_pipeline(
        conn, job["id"], simplified_content="clean", content_type="job_posting",
        title=job["title"], headline="", summary="Now a great match",
    )
    scenario = scenarios[0]
    q.upsert_job_score(conn, job["id"], scenario["id"], 0.95, "now passes", "hash-new")
    yield f"{progress_prefix}Reset complete: {job['url']}"


def test_job_reset_from_not_relevant_tab_shows_moved_to_new_badge(client, conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "http")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/1", title="Filtered Out", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="job_posting", summary="role")
    scenario_id = q.insert_scenario(conn, "A", "")  # default gate_threshold 0.7
    q.upsert_job_score(conn, jid, scenario_id, 0.3, "not a fit", "hash1")  # gate-failed -> "Not relevant" tab

    with patch("app.routes.jobs.run_reprocess_job", side_effect=_fake_run_reprocess_job_to_passing):
        resp = client.post(f"/jobs/{jid}/reset?status=not_relevant&content_type=")

    assert resp.status_code == 200
    assert "Moved to New" in resp.text
    assert 'href="/jobs"' in resp.text


def test_job_reset_that_stays_in_current_filter_shows_no_badge(client, conn):
    sid, jid, scenario_id = _seed(conn)  # already gate-passed, status "new"
    with patch("app.routes.jobs.run_reprocess_job", side_effect=_fake_run_reprocess_job):
        resp = client.post(f"/jobs/{jid}/reset?status=&content_type=")
    assert resp.status_code == 200
    assert "Moved to" not in resp.text


def test_job_reset_without_filter_query_shows_no_badge(client, conn):
    # Simulates the standalone /jobs/{id} page, which never sends filter params.
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "http")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/1", title="Filtered Out", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="job_posting", summary="role")
    scenario_id = q.insert_scenario(conn, "A", "")
    q.upsert_job_score(conn, jid, scenario_id, 0.3, "not a fit", "hash1")

    with patch("app.routes.jobs.run_reprocess_job", side_effect=_fake_run_reprocess_job_to_passing):
        resp = client.post(f"/jobs/{jid}/reset")

    assert resp.status_code == 200
    assert "Moved to" not in resp.text


def test_job_pass_as_new_shows_moved_to_new_badge(client, conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "http")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/failed", title="Failed Gate", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="job_posting", summary="role")
    scenario_id = q.insert_scenario(conn, "A", "")
    q.upsert_job_score(conn, jid, scenario_id, 0.2, "too junior", "hash1")

    with patch("app.routes.jobs.run_pass_as_new", side_effect=_fake_run_pass_as_new):
        resp = client.post(f"/jobs/{jid}/pass-as-new?status=not_relevant&content_type=")

    assert resp.status_code == 200
    assert "Moved to New" in resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_routes_jobs.py -k "badge" -v`
Expected: FAIL (no badge markup exists yet)

- [ ] **Step 3: Add `_stale_badge` and wire it into the render helper**

In `app/routes/jobs.py`, add directly after `_filter_context`:

```python
def _stale_badge(conn: sqlite3.Connection, job: dict, status: str | None, content_type: str | None) -> dict | None:
    filtered_ids = {j["id"] for j in _get_filtered_jobs(conn, status, content_type)}
    if job["id"] in filtered_ids:
        return None
    if job["content_type"] == "job_posting":
        if job["passed_gate_count"] or job["gate_override"]:
            return {"label": "Moved to New", "href": "/jobs"}
        return {"label": "Moved to Not relevant", "href": "/jobs?status=not_relevant"}
    if job["content_type"] == "lead":
        return {"label": "Moved to Leads", "href": "/jobs?content_type=lead"}
    return {"label": "No longer shown in this view", "href": None}
```

Update `_render_job_feedback_html`:

Before:
```python
def _render_job_feedback_html(conn: sqlite3.Connection, request: Request, job_id: int, filter_ctx: dict) -> str:
    job = q.get_job(conn, job_id)
    sources = {s["id"]: s for s in q.get_sources(conn)}
    job["source_name"] = sources.get(job["source_id"], {}).get("name", "")
    scenarios = q.get_scenarios(conn)
    job_scores = q.get_job_scores(conn, job_id)
    context = {"job": job, "scenarios": scenarios, "job_scores": job_scores}
    context.update(filter_ctx)
    return templates.get_template("jobs/_feedback.html").render(request=request, **context)
```

After:
```python
def _render_job_feedback_html(conn: sqlite3.Connection, request: Request, job_id: int, filter_ctx: dict) -> str:
    job = q.get_job(conn, job_id)
    sources = {s["id"]: s for s in q.get_sources(conn)}
    job["source_name"] = sources.get(job["source_id"], {}).get("name", "")
    scenarios = q.get_scenarios(conn)
    job_scores = q.get_job_scores(conn, job_id)
    context = {"job": job, "scenarios": scenarios, "job_scores": job_scores}
    context.update(filter_ctx)
    if filter_ctx:
        context["stale_badge"] = _stale_badge(
            conn, job, filter_ctx.get("filter_status"), filter_ctx.get("filter_content_type")
        )
    return templates.get_template("jobs/_feedback.html").render(request=request, **context)
```

- [ ] **Step 4: Render the badge in the template**

In `app/templates/jobs/_feedback.html`, add right after the company/original-link `<p>` block (from Task 1's edit):

Before:
```html
  <p>
    {% if job.company %}<span>· {{ job.company }}</span>{% endif %}
    <a href="{{ job.url }}" target="_blank" rel="noopener">↗ original</a>
    <a href="/jobs/{{ job.id }}" class="permalink-icon" title="Direct link to this job" aria-label="Direct link to this job">&#128279;</a>
  </p>
```

After:
```html
  <p>
    {% if job.company %}<span>· {{ job.company }}</span>{% endif %}
    <a href="{{ job.url }}" target="_blank" rel="noopener">↗ original</a>
    <a href="/jobs/{{ job.id }}" class="permalink-icon" title="Direct link to this job" aria-label="Direct link to this job">&#128279;</a>
  </p>
  {% if stale_badge %}
  <p class="stale-badge">
    {% if stale_badge.href %}
      <a href="{{ stale_badge.href }}" class="tag tag-feedback">{{ stale_badge.label }} &#8635;</a>
    {% else %}
      <span class="tag">{{ stale_badge.label }}</span>
    {% endif %}
  </p>
  {% endif %}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_routes_jobs.py -v`
Expected: PASS (all tests in the file)

Run: `uv run pytest -q`
Expected: PASS (full suite)

- [ ] **Step 6: Commit**

```bash
git add app/routes/jobs.py app/templates/jobs/_feedback.html tests/test_routes_jobs.py
git commit -m "feat: flag jobs that fall out of the active filter after reset/pass-as-new"
```

---

### Task 5: Accept/Reject/Invalid redirect from the detail page

**Files:**
- Modify: `app/routes/jobs.py` (update `job_feedback`)
- Modify: `app/templates/jobs/_feedback.html` (hidden `redirect` field, detail-page only)
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: `is_detail_page` flag from Task 1.
- Produces: `job_feedback` accepts an optional `redirect: str | None = Form(None)`; when present, responds with an empty body and an `HX-Redirect` header set to that value instead of the counts-only OOB fragment.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_routes_jobs.py`:

```python
def test_job_feedback_with_redirect_field_returns_hx_redirect_header(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.post(
        f"/jobs/{jid}/feedback",
        data={"status": "accepted", "note": "", "redirect": "/jobs"},
    )
    assert resp.status_code == 200
    assert resp.headers["HX-Redirect"] == "/jobs"
    job = q.get_job(conn, jid)
    assert job["status"] == "accepted"


def test_job_feedback_without_redirect_field_has_no_redirect_header(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.post(f"/jobs/{jid}/feedback", data={"status": "accepted", "note": ""})
    assert resp.status_code == 200
    assert "HX-Redirect" not in resp.headers


def test_job_detail_feedback_form_includes_redirect_field(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}")
    assert resp.status_code == 200
    assert '<input type="hidden" name="redirect" value="/jobs">' in resp.text


def test_job_expand_feedback_form_has_no_redirect_field(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert 'name="redirect"' not in resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_routes_jobs.py -k "redirect" -v`
Expected: FAIL (no `redirect` field/header handling yet)

- [ ] **Step 3: Update `job_feedback`**

Before:
```python
@router.post("/jobs/{job_id}/feedback", response_class=HTMLResponse)
def job_feedback(
    job_id: int,
    request: Request,
    status: str = Form(...),
    note: str | None = Form(None),
    conn: sqlite3.Connection = Depends(get_db),
):
    q.update_job_feedback(conn, job_id, status, note)
    counts = q.get_job_counts(conn)
    return templates.TemplateResponse(request, "jobs/_counts_oob.html", {"counts": counts})
```

After:
```python
@router.post("/jobs/{job_id}/feedback", response_class=HTMLResponse)
def job_feedback(
    job_id: int,
    request: Request,
    status: str = Form(...),
    note: str | None = Form(None),
    redirect: str | None = Form(None),
    conn: sqlite3.Connection = Depends(get_db),
):
    q.update_job_feedback(conn, job_id, status, note)
    if redirect:
        return HTMLResponse(content="", headers={"HX-Redirect": redirect})
    counts = q.get_job_counts(conn)
    return templates.TemplateResponse(request, "jobs/_counts_oob.html", {"counts": counts})
```

- [ ] **Step 4: Add the hidden field to the template**

In `app/templates/jobs/_feedback.html`, the feedback form:

Before:
```html
  <form class="feedback-form" style="margin-top:0.75rem;"
    hx-post="/jobs/{{ job.id }}/feedback"
    hx-target="#job-{{ job.id }}"
    hx-swap="outerHTML">
    <label>Note (optional):
```

After:
```html
  <form class="feedback-form" style="margin-top:0.75rem;"
    hx-post="/jobs/{{ job.id }}/feedback"
    hx-target="#job-{{ job.id }}"
    hx-swap="outerHTML">
    {% if is_detail_page %}<input type="hidden" name="redirect" value="/jobs">{% endif %}
    <label>Note (optional):
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_routes_jobs.py -v`
Expected: PASS (all tests in the file)

Run: `uv run pytest -q`
Expected: PASS (full suite)

- [ ] **Step 6: Commit**

```bash
git add app/routes/jobs.py app/templates/jobs/_feedback.html tests/test_routes_jobs.py
git commit -m "feat: redirect to job list after accept/reject/invalid on the detail page"
```

---

## Manual verification (after all tasks)

Use the `run-dev-server` skill to start the app against a throwaway DB copy, then:
1. Open `/jobs`, click a row's 🔗 icon — lands on `/jobs/{id}` showing the same detail content, no bulk checkbox.
2. From `/jobs?status=not_relevant`, expand a gate-failed job and click "Pass as new" — row updates in place (no reload, scroll position kept) and shows a "Moved to New ↻" badge linking to `/jobs`.
3. From `/jobs` (New tab), Accept a job — row vanishes immediately as before (unchanged).
4. From `/jobs/{id}`, Accept/Reject/Invalid — browser navigates to `/jobs`.
5. Confirm bulk-reset (`/jobs` bulk bar) still does a full page reload, unchanged.
