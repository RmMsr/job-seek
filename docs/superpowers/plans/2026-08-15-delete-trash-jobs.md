# Delete Trash Jobs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user permanently delete jobs that are already in Trash status, both one at a time and via multi-select, by turning the existing (now-redundant) "Trash" button into "Delete" wherever the job/view is already trash.

**Architecture:** Two new query functions (`delete_job`, `delete_jobs`) backed by the existing `ON DELETE CASCADE` foreign keys on `job_scores`/`scenario_feedback`. Five new FastAPI routes in `app/routes/jobs.py` following the exact htmx confirm-panel pattern already used for source deletion (`sources/_row_delete_confirm.html`). No new JavaScript — reuses the existing generic `job_ids` checkbox selection machinery in `base.html`.

**Tech Stack:** FastAPI, Jinja2, htmx 1.9.12, sqlite3, pytest + httpx `TestClient`.

## Global Constraints

- Only jobs with `status = "trash"` are ever deletable — enforced in the UI (the Delete button only ever replaces Trash where the job/view is already trash) and again server-side in every delete route.
- Deletion is permanent — no soft-delete, no undo (see spec's Non-goals; matches this project's migration/data philosophy of no backwards-compat shims).
- No new JS. All selection/visibility behavior reuses `base.html`'s existing `job_ids` checkbox handling.
- Design source of truth: `docs/superpowers/specs/2026-08-15-delete-trash-jobs-design.md`.

---

## Task 1: Data layer — `delete_job` / `delete_jobs`

**Files:**
- Modify: `app/db/queries.py` (add functions near `delete_source`, `app/db/queries.py:87-91`)
- Test: `tests/test_queries.py` (add near the existing `delete_source` tests, `tests/test_queries.py:81-123`)

**Interfaces:**
- Produces: `delete_job(conn: sqlite3.Connection, job_id: int) -> None` — deletes one job unconditionally (callers are expected to have already checked status).
- Produces: `delete_jobs(conn: sqlite3.Connection, job_ids: list[int]) -> None` — deletes only rows among `job_ids` whose `status = 'trash'`; no-ops on an empty list.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_queries.py` (after `test_delete_source_leaves_other_sources_and_jobs_intact`, i.e. after line 123):

```python
def test_delete_job_removes_job(conn):
    sid = q.insert_source(conn, "s1", "http://x", "http")
    jid = q.insert_job(conn, source_id=sid, url="http://job/1", title="T1", company="C", raw_text="r")
    q.update_job_feedback(conn, jid, "trash", None)

    q.delete_job(conn, jid)

    assert q.get_job(conn, jid) is None


def test_delete_job_cascades_to_job_scores_and_scenario_feedback(conn):
    sid = q.insert_source(conn, "s1", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")
    jid = q.insert_job(conn, source_id=sid, url="http://job/1", title="T1", company="C", raw_text="r")
    q.upsert_job_score(conn, jid, scenario_id, 0.5, "reasoning", "hash1")
    q.upsert_scenario_feedback(conn, jid, scenario_id, "note", "higher")
    q.update_job_feedback(conn, jid, "trash", None)

    q.delete_job(conn, jid)

    assert q.get_job_scores(conn, jid) == []
    row = conn.execute("SELECT 1 FROM scenario_feedback WHERE job_id = ?", (jid,)).fetchone()
    assert row is None


def test_delete_jobs_removes_only_trash_status_jobs(conn):
    sid = q.insert_source(conn, "s1", "http://x", "http")
    trash_id = q.insert_job(conn, source_id=sid, url="http://job/1", title="T1", company="C", raw_text="r")
    q.update_job_feedback(conn, trash_id, "trash", None)
    new_id = q.insert_job(conn, source_id=sid, url="http://job/2", title="T2", company="C", raw_text="r")

    q.delete_jobs(conn, [trash_id, new_id])

    assert q.get_job(conn, trash_id) is None
    assert q.get_job(conn, new_id) is not None


def test_delete_jobs_empty_list_is_noop(conn):
    sid = q.insert_source(conn, "s1", "http://x", "http")
    jid = q.insert_job(conn, source_id=sid, url="http://job/1", title="T1", company="C", raw_text="r")
    q.update_job_feedback(conn, jid, "trash", None)

    q.delete_jobs(conn, [])

    assert q.get_job(conn, jid) is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_queries.py -k delete_job -v`
Expected: FAIL with `AttributeError: module 'app.db.queries' has no attribute 'delete_job'` (and `delete_jobs`).

- [ ] **Step 3: Implement**

In `app/db/queries.py`, add directly after `delete_source` (after line 91, before the `# --- Scenarios ---` comment):

```python
def delete_job(conn: sqlite3.Connection, job_id: int) -> None:
    conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
    conn.commit()


def delete_jobs(conn: sqlite3.Connection, job_ids: list[int]) -> None:
    if not job_ids:
        return
    placeholders = ",".join("?" for _ in job_ids)
    conn.execute(f"DELETE FROM jobs WHERE status = 'trash' AND id IN ({placeholders})", job_ids)
    conn.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_queries.py -k delete_job -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add app/db/queries.py tests/test_queries.py
git commit -m "feat: add delete_job/delete_jobs query functions"
```

---

## Task 2: Direct (single-job) delete

**Files:**
- Create: `app/templates/jobs/_row_delete_confirm.html`
- Modify: `app/routes/jobs.py` (add two routes, after `job_collapse`, i.e. after `app/routes/jobs.py:220`)
- Modify: `app/templates/jobs/_feedback.html` (the primary actions row, `app/templates/jobs/_feedback.html:57-61` in current file)
- Modify: `app/templates/base.html` (new `.btn-delete` style, next to `.btn-trash`, `app/templates/base.html:74`)
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: `q.delete_job(conn, job_id)` from Task 1; existing `q.get_job(conn, job_id)`, `q.get_job_counts(conn)`, `_filter_context(request)`, `_is_detail_page_request(request)` (all already in `app/routes/jobs.py`).
- Produces: routes `GET /jobs/{job_id}/delete-confirm` and `DELETE /jobs/{job_id}`, consumed by Task 3 only insofar as they follow the same confirm-panel convention (no shared code).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_routes_jobs.py` (near the other `/jobs/{id}` route tests, e.g. after `test_job_collapse_returns_row_view` around line 331):

```python
def test_job_delete_confirm_renders_confirm_panel(client, conn):
    sid, jid, scenario_id = _seed(conn)
    q.update_job_feedback(conn, jid, "trash", None)
    resp = client.get(f"/jobs/{jid}/delete-confirm")
    assert resp.status_code == 200
    assert "Delete this job?" in resp.text
    assert f'hx-delete="/jobs/{jid}"' in resp.text
    assert f'id="job-{jid}"' in resp.text


def test_job_delete_confirm_404_for_missing_job(client, conn):
    resp = client.get("/jobs/999/delete-confirm")
    assert resp.status_code == 404


def test_job_delete_removes_trash_job(client, conn):
    sid, jid, scenario_id = _seed(conn)
    q.update_job_feedback(conn, jid, "trash", None)
    resp = client.delete(f"/jobs/{jid}")
    assert resp.status_code == 200
    assert q.get_job(conn, jid) is None


def test_job_delete_response_includes_updated_trash_count(client, conn):
    sid, jid, scenario_id = _seed(conn)
    q.update_job_feedback(conn, jid, "trash", None)
    resp = client.delete(f"/jobs/{jid}")
    assert resp.status_code == 200
    assert '<span id="count-trash" hx-swap-oob="true">0</span>' in resp.text


def test_job_delete_rejects_non_trash_job(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.delete(f"/jobs/{jid}")
    assert resp.status_code == 400
    assert q.get_job(conn, jid) is not None


def test_job_delete_404_for_missing_job(client, conn):
    resp = client.delete("/jobs/999")
    assert resp.status_code == 404
```

Add to `tests/test_routes_jobs.py` (near `test_job_expand_reject_trash_buttons_have_tooltips`, around line 323):

```python
def test_job_expand_shows_delete_instead_of_trash_when_already_trash(client, conn):
    sid, jid, scenario_id = _seed(conn)
    q.update_job_feedback(conn, jid, "trash", None)
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert f'hx-get="/jobs/{jid}/delete-confirm"' in resp.text
    assert 'name="status" value="trash"' not in resp.text


def test_job_expand_shows_trash_when_not_trash(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert 'name="status" value="trash"' in resp.text
    assert "delete-confirm" not in resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_routes_jobs.py -v`
Expected: the 8 new tests FAIL — `delete-confirm`/`DELETE /jobs/{id}` routes don't exist yet (404s where 200 expected, 405 where 400/200 expected), and the expand tests fail because the Trash button is unconditional today. All pre-existing tests in the file still PASS.

- [ ] **Step 3: Create the confirm partial**

Create `app/templates/jobs/_row_delete_confirm.html`:

```html
<article class="job-row" id="job-{{ job.id }}">
  <div class="delete-confirm">
    <p>Delete this job? This cannot be undone.</p>
    <div class="actions" role="group" aria-label="Confirm delete">
      <button type="button" class="btn btn-delete"
        hx-delete="/jobs/{{ job.id }}"
        hx-target="#job-{{ job.id }}"
        hx-swap="outerHTML">Confirm delete</button>
      <button type="button" class="btn"
        hx-get="/jobs/{{ job.id }}/collapse{% if is_detail_page %}?detail=1{% elif filter_status is defined %}?status={{ filter_status or '' }}&content_type={{ filter_content_type or '' }}{% if filter_source_id %}&source_id={{ filter_source_id }}{% endif %}{% endif %}"
        hx-target="#job-{{ job.id }}"
        hx-swap="outerHTML">Cancel</button>
    </div>
  </div>
</article>
```

- [ ] **Step 4: Add the routes**

In `app/routes/jobs.py`, add directly after `job_collapse` (after line 220, before `job_feedback`):

```python
@router.get("/jobs/{job_id}/delete-confirm", response_class=HTMLResponse)
def job_delete_confirm(job_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    job = q.get_job(conn, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    context = {"job": job}
    context.update(_filter_context(request))
    if _is_detail_page_request(request):
        context["is_detail_page"] = True
    return templates.TemplateResponse(request, "jobs/_row_delete_confirm.html", context)


@router.delete("/jobs/{job_id}", response_class=HTMLResponse)
def job_delete(job_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    job = q.get_job(conn, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] != "trash":
        raise HTTPException(status_code=400, detail="Only trashed jobs can be deleted")
    q.delete_job(conn, job_id)
    counts_html = templates.get_template("jobs/_counts_oob.html").render(
        request=request, counts=q.get_job_counts(conn)
    )
    return HTMLResponse(content=counts_html)
```

- [ ] **Step 5: Update the feedback panel's actions row**

In `app/templates/jobs/_feedback.html`, replace:

```html
      <button type="submit" name="status" value="trash" class="btn btn-trash" title="Not a usable posting (expired, spam, duplicate, wrong content).">Trash</button>
```

with:

```html
      {% if job.status == "trash" %}
      <button type="button" class="btn btn-delete"
        hx-get="/jobs/{{ job.id }}/delete-confirm{% if is_detail_page %}?detail=1{% elif filter_status is defined %}?status={{ filter_status or '' }}&content_type={{ filter_content_type or '' }}{% if filter_source_id %}&source_id={{ filter_source_id }}{% endif %}{% endif %}"
        hx-target="#job-{{ job.id }}"
        hx-swap="outerHTML">Delete</button>
      {% else %}
      <button type="submit" name="status" value="trash" class="btn btn-trash" title="Not a usable posting (expired, spam, duplicate, wrong content).">Trash</button>
      {% endif %}
```

- [ ] **Step 6: Add the `.btn-delete` style**

In `app/templates/base.html`, directly after the `.btn-trash` rule (line 74):

```css
    .btn-delete { background: #dc3545; color: #fff; border-color: #a71d2a; }
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/test_routes_jobs.py -v`
Expected: all tests PASS, including the 8 new ones from Step 1

- [ ] **Step 8: Run the full test suite to check for regressions**

Run: `pytest -q`
Expected: PASS, no failures (existing Trash-button tests only ever seed non-trash jobs, so they're unaffected)

- [ ] **Step 9: Commit**

```bash
git add app/templates/jobs/_row_delete_confirm.html app/routes/jobs.py app/templates/jobs/_feedback.html app/templates/base.html tests/test_routes_jobs.py
git commit -m "feat: direct delete for trashed jobs"
```

---

## Task 3: Bulk (multi-select) delete

**Files:**
- Create: `app/templates/jobs/_bulk_actions.html` (extracted from `_content.html`)
- Create: `app/templates/jobs/_bulk_delete_confirm.html`
- Modify: `app/templates/jobs/_content.html` (`app/templates/jobs/_content.html:47-51` in current file)
- Modify: `app/routes/jobs.py` (add three routes, after `job_bulk_feedback`, i.e. after `app/routes/jobs.py:410`)
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: `q.delete_jobs(conn, job_ids)` from Task 1; existing `_content_context(conn, status, content_type, source_id)` (`app/routes/jobs.py:153-166`, returns a dict including `"status"`).
- Produces: routes `POST /jobs/bulk-delete-confirm`, `POST /jobs/bulk-delete`, `POST /jobs/bulk-actions-cancel`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_routes_jobs.py` (near the other bulk-feedback tests, after `test_job_bulk_feedback_shows_no_jobs_found_when_nothing_matches_or_moved`, around line 668):

```python
def test_job_bulk_delete_confirm_shows_count(client, conn):
    sid, j1, scenario_id = _seed(conn)
    j2 = q.insert_job(conn, source_id=sid, url="http://finn.no/job/2", title="Data Eng", company="Acme", raw_text="r")
    resp = client.post("/jobs/bulk-delete-confirm", data={"job_ids": [j1, j2]})
    assert resp.status_code == 200
    assert "Delete 2 selected jobs? This cannot be undone." in resp.text


def test_job_bulk_delete_confirm_singular_for_one_job(client, conn):
    sid, j1, scenario_id = _seed(conn)
    resp = client.post("/jobs/bulk-delete-confirm", data={"job_ids": [j1]})
    assert resp.status_code == 200
    assert "Delete 1 selected job? This cannot be undone." in resp.text


def test_job_bulk_delete_removes_only_trash_jobs(client, conn):
    sid, j1, scenario_id = _seed(conn)
    q.update_job_feedback(conn, j1, "trash", None)
    j2 = q.insert_job(conn, source_id=sid, url="http://finn.no/job/2", title="Data Eng", company="Acme", raw_text="r")
    resp = client.post(
        "/jobs/bulk-delete",
        data={"job_ids": [j1, j2], "status_filter": "trash", "content_type_filter": "", "source_id_filter": ""},
    )
    assert resp.status_code == 200
    assert q.get_job(conn, j1) is None
    assert q.get_job(conn, j2) is not None


def test_job_bulk_delete_response_reflects_remaining_trash_jobs(client, conn):
    sid, j1, scenario_id = _seed(conn)
    q.update_job_feedback(conn, j1, "trash", None)
    resp = client.post(
        "/jobs/bulk-delete",
        data={"job_ids": [j1], "status_filter": "trash", "content_type_filter": "", "source_id_filter": ""},
    )
    assert resp.status_code == 200
    assert "No jobs found" in resp.text


def test_job_bulk_actions_cancel_shows_trash_outside_trash_view(client, conn):
    resp = client.post("/jobs/bulk-actions-cancel", data={"status_filter": ""})
    assert resp.status_code == 200
    assert 'name="status" value="trash"' in resp.text
    assert "bulk-delete-confirm" not in resp.text


def test_job_bulk_actions_cancel_shows_delete_in_trash_view(client, conn):
    resp = client.post("/jobs/bulk-actions-cancel", data={"status_filter": "trash"})
    assert resp.status_code == 200
    assert 'hx-post="/jobs/bulk-delete-confirm"' in resp.text
    assert 'name="status" value="trash"' not in resp.text


def test_job_list_bulk_bar_shows_delete_in_trash_view(client, conn):
    sid, jid, scenario_id = _seed(conn)
    q.update_job_feedback(conn, jid, "trash", None)
    resp = client.get("/jobs?status=trash")
    assert resp.status_code == 200
    assert 'hx-post="/jobs/bulk-delete-confirm"' in resp.text


def test_job_list_bulk_bar_shows_trash_outside_trash_view(client, conn):
    resp = client.get("/jobs")
    assert resp.status_code == 200
    assert 'name="status" value="trash"' in resp.text
    assert 'hx-post="/jobs/bulk-delete-confirm"' not in resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_routes_jobs.py -v`
Expected: the 8 new tests FAIL — the three new routes don't exist (404s), and the view-based Trash/Delete swap doesn't exist yet. All pre-existing tests in the file still PASS.

- [ ] **Step 3: Extract `_bulk_actions.html`**

Create `app/templates/jobs/_bulk_actions.html`:

```html
<div class="actions" role="group" aria-label="Bulk decision">
  <button type="submit" form="bulk-form" name="status" value="accepted" class="btn btn-accept">Accept</button>
  <button type="submit" form="bulk-form" name="status" value="rejected" class="btn btn-reject" title="Does not match your criteria.">Reject</button>
  {% if status == "trash" %}
  <button type="button" class="btn btn-delete"
    hx-post="/jobs/bulk-delete-confirm"
    hx-include="#bulk-form"
    hx-target=".decision-panel-left .actions"
    hx-swap="outerHTML">Delete</button>
  {% else %}
  <button type="submit" form="bulk-form" name="status" value="trash" class="btn btn-trash" title="Not a usable posting (expired, spam, duplicate, wrong content).">Trash</button>
  {% endif %}
</div>
```

In `app/templates/jobs/_content.html`, replace:

```html
    <div class="actions" role="group" aria-label="Bulk decision">
      <button type="submit" form="bulk-form" name="status" value="accepted" class="btn btn-accept">Accept</button>
      <button type="submit" form="bulk-form" name="status" value="rejected" class="btn btn-reject" title="Does not match your criteria.">Reject</button>
      <button type="submit" form="bulk-form" name="status" value="trash" class="btn btn-trash" title="Not a usable posting (expired, spam, duplicate, wrong content).">Trash</button>
    </div>
```

with:

```html
    {% include "jobs/_bulk_actions.html" %}
```

(`status` is already in scope in `_content.html`'s own render context wherever it's included — set by `_content_context`/`job_bulk_feedback` today — so no context changes needed at the call sites.)

- [ ] **Step 4: Create the bulk confirm partial**

Create `app/templates/jobs/_bulk_delete_confirm.html`:

```html
<div class="actions bulk-delete-confirm" role="group" aria-label="Confirm bulk delete">
  <span>Delete {{ job_ids | length }} selected job{{ 's' if job_ids | length != 1 else '' }}? This cannot be undone.</span>
  <button type="button" class="btn btn-delete"
    hx-post="/jobs/bulk-delete"
    hx-include="#bulk-form"
    hx-target="#jobs-content"
    hx-swap="innerHTML">Confirm delete</button>
  <button type="button" class="btn"
    hx-post="/jobs/bulk-actions-cancel"
    hx-include="#bulk-form"
    hx-target=".decision-panel-left .actions"
    hx-swap="outerHTML">Cancel</button>
</div>
```

- [ ] **Step 5: Add the routes**

In `app/routes/jobs.py`, add directly after `job_bulk_feedback` (after line 410, before `job_add_by_url`):

```python
@router.post("/jobs/bulk-delete-confirm", response_class=HTMLResponse)
def job_bulk_delete_confirm(
    request: Request,
    job_ids: list[int] = Form(...),
    conn: sqlite3.Connection = Depends(get_db),
):
    return templates.TemplateResponse(request, "jobs/_bulk_delete_confirm.html", {"job_ids": job_ids})


@router.post("/jobs/bulk-delete", response_class=HTMLResponse)
def job_bulk_delete(
    request: Request,
    job_ids: list[int] = Form(...),
    status_filter: str | None = Form(None),
    content_type_filter: str | None = Form(None),
    source_id_filter: str | None = Form(None),
    conn: sqlite3.Connection = Depends(get_db),
):
    q.delete_jobs(conn, job_ids)
    status_filter = status_filter or None
    content_type_filter = content_type_filter or None
    source_id_filter = source_id_filter or None
    source_id = int(source_id_filter) if source_id_filter else None
    return templates.TemplateResponse(
        request, "jobs/_content.html", _content_context(conn, status_filter, content_type_filter, source_id)
    )


@router.post("/jobs/bulk-actions-cancel", response_class=HTMLResponse)
def job_bulk_actions_cancel(
    request: Request,
    status_filter: str | None = Form(None),
    conn: sqlite3.Connection = Depends(get_db),
):
    return templates.TemplateResponse(request, "jobs/_bulk_actions.html", {"status": status_filter or None})
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_routes_jobs.py -v`
Expected: all tests PASS, including the 8 new ones from Step 1

- [ ] **Step 7: Run the full test suite to check for regressions**

Run: `pytest -q`
Expected: PASS, no failures

- [ ] **Step 8: Commit**

```bash
git add app/templates/jobs/_bulk_actions.html app/templates/jobs/_bulk_delete_confirm.html app/templates/jobs/_content.html app/routes/jobs.py tests/test_routes_jobs.py
git commit -m "feat: bulk delete for trashed jobs"
```

---

## Manual verification (after all tasks)

Use the `run-dev-server` skill (throwaway DB copy, never the live one):

1. Add a job by a bad/unreachable URL so it lands in Trash as an error job (or trash any existing job via its Trash button).
2. On `/jobs?status=trash`, expand the job and confirm the primary actions row shows **Delete** (solid red) instead of **Trash**.
3. Click **Delete** → confirm the inline "Delete this job? This cannot be undone." panel appears; click **Cancel** → row returns to normal. Click **Delete** again → **Confirm delete** → row disappears and the Trash count in the nav updates.
4. Trash two or three more jobs. On `/jobs?status=trash`, select several via checkboxes (select-all, drag-select) and confirm the bulk bar shows **Delete** (not Trash) in red.
5. Click bulk **Delete** → confirm the "Delete N selected jobs?" panel; **Cancel** → back to normal bulk actions with selection intact. Click **Delete** → **Confirm delete** → selected rows vanish, counts update.
6. Switch to `/jobs` (New) or another non-Trash tab and confirm the bulk bar still shows **Trash**, not **Delete**.
