# Source management usability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a source be deleted (cascading to its fetch runs, jobs, and — transitively — job scores/scenario feedback) with a job-count confirmation, add a "View all jobs" link per source on `/fetch` that shows every status/content-type for that source unfiltered, and reword the Lifetime column on `/fetch` from "N new" to "N added".

**Architecture:** Two independent slices sharing no code: (1) a cascading `DELETE /sources/{id}` route plus a Delete button, backed by two new counting queries and one new deleting query; (2) an optional `source_id` query filter added to the existing `/jobs` filtering machinery (`_get_filtered_jobs`, `_filter_context`, `_stale_badge`, `_content_context`), threaded through row/bulk action URLs exactly the way `status`/`content_type` already are, so it survives htmx round-trips. A one-line copy change closes out the Lifetime wording.

**Tech Stack:** FastAPI, Jinja2 templates, htmx, sqlite3 (stdlib), pytest + `fastapi.testclient.TestClient`.

## Global Constraints

- No schema migration: `jobs.source_id` and `fetch_runs.source_id` have no `ON DELETE CASCADE`, but `job_scores`/`scenario_feedback` already cascade off `jobs.id`. Source deletion must explicitly delete `fetch_runs` then `jobs` (letting the existing FK cascade clean up scores/feedback) before deleting the `sources` row. Both `app/deps.py` and the test `conn` fixture run with `PRAGMA foreign_keys = ON`, so this order matters — deleting `sources` before `jobs` will raise `sqlite3.IntegrityError`.
- Delete confirmation copy (exact, case-sensitive): `Delete '<name>' and its <count> job(s)? This cannot be undone.` with `job` singular when count == 1, `jobs` otherwise.
- Lifetime column copy: `{{ stats.total_new }} added` (was `{{ stats.total_new }} new`) — same underlying value, label only.
- The `source_id` jobs-filter is exclusive with `status`/`content_type` — when present it always shows every status and content type for that source; it is never combined with the status tabs.
- Follow the existing dual convention for per-row template data (see `needs_login_by_id` / `needs_login` in `app/routes/sources.py`): bulk dict for loop-rendered pages (`sources/index.html`), single scalar for single-row re-renders (`create_source`, `update_source`, `set_cookie`, `forget_cookie`, `source_row`).
- `source_id` must never be emitted as an empty-string query param anywhere `/jobs`'s `source_id: int | None` route parameter could receive it — FastAPI will 422 trying to coerce `""` to `int` (unlike `status`/`content_type`, which are `str | None` and tolerate `""`). Templates must gate `&source_id=...` behind `{% if filter_source_id %}`, never emit it unconditionally.

---

### Task 1: Query layer — job counts by source and cascading source delete

**Files:**
- Modify: `app/db/queries.py` (add after `set_source_cookie`, currently ending at line 74, in the `# --- Sources ---` section)
- Test: `tests/test_queries.py`

**Interfaces:**
- Produces: `get_job_counts_by_source(conn) -> dict[int, int]`, `count_jobs_by_source(conn, source_id: int) -> int`, `delete_source(conn, source_id: int) -> None`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_queries.py` (near the other source/job tests, e.g. after `test_get_or_create_manual_source_creates_once`):

```python
def test_get_job_counts_by_source(conn):
    s1 = q.insert_source(conn, "s1", "http://x", "http")
    s2 = q.insert_source(conn, "s2", "http://y", "http")
    q.insert_job(conn, source_id=s1, url="http://job/1", title="T1", company="C", raw_text="r")
    q.insert_job(conn, source_id=s1, url="http://job/2", title="T2", company="C", raw_text="r")
    q.insert_job(conn, source_id=s2, url="http://job/3", title="T3", company="C", raw_text="r")
    counts = q.get_job_counts_by_source(conn)
    assert counts == {s1: 2, s2: 1}


def test_get_job_counts_by_source_omits_sources_with_no_jobs(conn):
    s1 = q.insert_source(conn, "s1", "http://x", "http")
    counts = q.get_job_counts_by_source(conn)
    assert counts == {}


def test_count_jobs_by_source(conn):
    s1 = q.insert_source(conn, "s1", "http://x", "http")
    q.insert_job(conn, source_id=s1, url="http://job/1", title="T1", company="C", raw_text="r")
    assert q.count_jobs_by_source(conn, s1) == 1


def test_count_jobs_by_source_zero_when_no_jobs(conn):
    s1 = q.insert_source(conn, "s1", "http://x", "http")
    assert q.count_jobs_by_source(conn, s1) == 0


def test_delete_source_removes_source_and_its_jobs(conn):
    s1 = q.insert_source(conn, "s1", "http://x", "http")
    jid = q.insert_job(conn, source_id=s1, url="http://job/1", title="T1", company="C", raw_text="r")
    q.delete_source(conn, s1)
    assert q.get_source(conn, s1) is None
    assert q.get_job(conn, jid) is None


def test_delete_source_removes_its_fetch_runs(conn):
    s1 = q.insert_source(conn, "s1", "http://x", "http")
    run_id = q.start_fetch_run(conn, s1)
    q.complete_fetch_run(conn, run_id, jobs_found=1, jobs_new=1)
    q.delete_source(conn, s1)
    assert q.get_recent_fetch_runs(conn) == []


def test_delete_source_cascades_to_job_scores_and_scenario_feedback(conn):
    s1 = q.insert_source(conn, "s1", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")
    jid = q.insert_job(conn, source_id=s1, url="http://job/1", title="T1", company="C", raw_text="r")
    q.upsert_job_score(conn, jid, scenario_id, 0.5, "reasoning", "hash1")
    q.upsert_scenario_feedback(conn, jid, scenario_id, "note", "higher")

    q.delete_source(conn, s1)

    assert q.get_job_scores(conn, jid) == []
    row = conn.execute(
        "SELECT 1 FROM scenario_feedback WHERE job_id = ?", (jid,)
    ).fetchone()
    assert row is None


def test_delete_source_leaves_other_sources_and_jobs_intact(conn):
    s1 = q.insert_source(conn, "s1", "http://x", "http")
    s2 = q.insert_source(conn, "s2", "http://y", "http")
    q.insert_job(conn, source_id=s1, url="http://job/1", title="T1", company="C", raw_text="r")
    j2 = q.insert_job(conn, source_id=s2, url="http://job/2", title="T2", company="C", raw_text="r")

    q.delete_source(conn, s1)

    assert q.get_source(conn, s2) is not None
    assert q.get_job(conn, j2) is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_queries.py -k "job_counts_by_source or count_jobs_by_source or delete_source" -v`
Expected: FAIL with `AttributeError: module 'app.db.queries' has no attribute 'get_job_counts_by_source'` (and similarly for the other two functions once each preceding one is added).

- [ ] **Step 3: Implement the three functions**

In `app/db/queries.py`, insert immediately after `set_source_cookie` (currently lines 72-74):

```python
def get_job_counts_by_source(conn: sqlite3.Connection) -> dict[int, int]:
    rows = conn.execute("SELECT source_id, COUNT(*) AS n FROM jobs GROUP BY source_id").fetchall()
    return {row["source_id"]: row["n"] for row in rows}


def count_jobs_by_source(conn: sqlite3.Connection, source_id: int) -> int:
    row = conn.execute("SELECT COUNT(*) FROM jobs WHERE source_id = ?", (source_id,)).fetchone()
    return row[0]


def delete_source(conn: sqlite3.Connection, source_id: int) -> None:
    conn.execute("DELETE FROM fetch_runs WHERE source_id = ?", (source_id,))
    conn.execute("DELETE FROM jobs WHERE source_id = ?", (source_id,))
    conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))
    conn.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_queries.py -k "job_counts_by_source or count_jobs_by_source or delete_source" -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Run full query test suite and commit**

Run: `uv run pytest tests/test_queries.py -v`
Expected: all PASS

```bash
git add app/db/queries.py tests/test_queries.py
git commit -m "feat: add job-count-by-source and cascading source delete queries"
```

---

### Task 2: Delete a source — route and template

**Files:**
- Modify: `app/routes/sources.py`
- Modify: `app/templates/sources/index.html`
- Modify: `app/templates/sources/_row.html`
- Test: `tests/test_routes_sources.py`

**Interfaces:**
- Consumes: `q.get_job_counts_by_source(conn) -> dict[int, int]`, `q.count_jobs_by_source(conn, source_id) -> int`, `q.delete_source(conn, source_id) -> None` (Task 1)
- Produces: `DELETE /sources/{source_id}` route; `job_count` template variable available wherever `sources/_row.html` renders; `job_counts_by_source` dict available in `sources/index.html`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_routes_sources.py`:

```python
def test_delete_source_removes_row_and_returns_empty_body(client, conn):
    sid = _seed(conn)
    resp = client.delete(f"/sources/{sid}")
    assert resp.status_code == 200
    assert resp.text == ""
    assert q.get_source(conn, sid) is None


def test_delete_source_removes_its_jobs(client, conn):
    sid = _seed(conn)
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/1", title="T", company="C", raw_text="r")
    resp = client.delete(f"/sources/{sid}")
    assert resp.status_code == 200
    assert q.get_job(conn, jid) is None


def test_delete_source_404_for_missing_source(client, conn):
    resp = client.delete("/sources/999")
    assert resp.status_code == 404


def test_sources_page_delete_button_confirm_text_includes_job_count(client, conn):
    sid = _seed(conn)
    q.insert_job(conn, source_id=sid, url="http://finn.no/job/1", title="T1", company="C", raw_text="r")
    q.insert_job(conn, source_id=sid, url="http://finn.no/job/2", title="T2", company="C", raw_text="r")
    resp = client.get("/sources")
    assert resp.status_code == 200
    assert "Delete 'finn.no' and its 2 jobs? This cannot be undone." in resp.text


def test_sources_page_delete_button_confirm_text_singular_for_one_job(client, conn):
    sid = _seed(conn)
    q.insert_job(conn, source_id=sid, url="http://finn.no/job/1", title="T1", company="C", raw_text="r")
    resp = client.get("/sources")
    assert resp.status_code == 200
    assert "Delete 'finn.no' and its 1 job? This cannot be undone." in resp.text


def test_sources_page_delete_button_confirm_text_zero_jobs(client, conn):
    _seed(conn)
    resp = client.get("/sources")
    assert resp.status_code == 200
    assert "Delete 'finn.no' and its 0 jobs? This cannot be undone." in resp.text


def test_update_source_response_includes_delete_button_with_current_job_count(client, conn):
    sid = _seed(conn)
    q.insert_job(conn, source_id=sid, url="http://finn.no/job/1", title="T1", company="C", raw_text="r")
    resp = client.post(
        f"/sources/{sid}",
        data={"name": "finn.no", "url": "https://finn.no", "fetcher_type": "http", "enabled": "on"},
    )
    assert resp.status_code == 200
    assert "its 1 job?" in resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_routes_sources.py -k "delete_source or delete_button" -v`
Expected: FAIL — `DELETE /sources/{id}` returns 405 (no such route yet); confirm-text tests fail because the button doesn't exist yet.

- [ ] **Step 3: Implement the route and thread `job_count` through every `_row.html` / `index.html` render**

In `app/routes/sources.py`, replace `sources_page`:

```python
@router.get("/sources", response_class=HTMLResponse)
def sources_page(request: Request, conn: sqlite3.Connection = Depends(get_db), config=Depends(get_config)):
    sources = [s for s in q.get_sources(conn) if s["fetcher_type"] != "manual"]
    needs_login_by_id = {
        s["id"]: _check_needs_login(s, config, conn) for s in sources if s["fetcher_type"] == "slack"
    }
    job_counts_by_source = q.get_job_counts_by_source(conn)
    return templates.TemplateResponse(
        request,
        "sources/index.html",
        {
            "sources": sources,
            "needs_login_by_id": needs_login_by_id,
            "job_counts_by_source": job_counts_by_source,
        },
    )
```

Replace `create_source`:

```python
@router.post("/sources", response_class=HTMLResponse)
def create_source(
    request: Request,
    name: str = Form(...),
    url: str = Form(...),
    fetcher_type: str = Form(...),
    conn: sqlite3.Connection = Depends(get_db),
    config=Depends(get_config),
):
    source_id = q.insert_source(conn, name, url, fetcher_type)
    source = q.get_source(conn, source_id)
    needs_login = _check_needs_login(source, config, conn)
    return templates.TemplateResponse(
        request,
        "sources/index.html",
        {
            "sources": q.get_sources(conn),
            "needs_login_by_id": {source_id: needs_login},
            "job_counts_by_source": q.get_job_counts_by_source(conn),
        },
    )
```

Replace `source_row`:

```python
@router.get("/sources/{source_id}", response_class=HTMLResponse)
def source_row(source_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    source = _get_source_or_404(conn, source_id)
    job_count = q.count_jobs_by_source(conn, source_id)
    return templates.TemplateResponse(request, "sources/_row.html", {"source": source, "job_count": job_count})
```

Replace `update_source`:

```python
@router.post("/sources/{source_id}", response_class=HTMLResponse)
def update_source(
    source_id: int,
    request: Request,
    name: str = Form(...),
    url: str = Form(...),
    fetcher_type: str = Form(...),
    enabled: Optional[str] = Form(None),
    conn: sqlite3.Connection = Depends(get_db),
    config=Depends(get_config),
):
    _get_source_or_404(conn, source_id)
    q.update_source(
        conn, source_id, name=name, url=url, fetcher_type=fetcher_type, enabled=enabled is not None
    )
    source = q.get_source(conn, source_id)
    needs_login = _check_needs_login(source, config, conn)
    job_count = q.count_jobs_by_source(conn, source_id)
    return templates.TemplateResponse(
        request, "sources/_row.html", {"source": source, "needs_login": needs_login, "job_count": job_count}
    )
```

Replace `set_cookie`:

```python
@router.post("/sources/{source_id}/cookie", response_class=HTMLResponse)
def set_cookie(
    source_id: int,
    request: Request,
    d_cookie: str = Form(...),
    acknowledged: Optional[str] = Form(None),
    conn: sqlite3.Connection = Depends(get_db),
    config=Depends(get_config),
):
    _get_source_or_404(conn, source_id)
    if acknowledged is None:
        raise HTTPException(
            status_code=400,
            detail="You must acknowledge the security warning before saving the cookie.",
        )
    q.set_source_cookie(conn, source_id, d_cookie.strip())
    source = q.get_source(conn, source_id)
    needs_login = _check_needs_login(source, config, conn)
    job_count = q.count_jobs_by_source(conn, source_id)
    return templates.TemplateResponse(
        request, "sources/_row.html", {"source": source, "needs_login": needs_login, "job_count": job_count}
    )
```

Replace `forget_cookie`:

```python
@router.post("/sources/{source_id}/forget-cookie", response_class=HTMLResponse)
def forget_cookie(
    source_id: int,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    config=Depends(get_config),
):
    _get_source_or_404(conn, source_id)
    q.set_source_cookie(conn, source_id, "")
    source = q.get_source(conn, source_id)
    needs_login = _check_needs_login(source, config, conn)
    job_count = q.count_jobs_by_source(conn, source_id)
    return templates.TemplateResponse(
        request, "sources/_row.html", {"source": source, "needs_login": needs_login, "job_count": job_count}
    )
```

Add the new delete route at the end of the file:

```python
@router.delete("/sources/{source_id}", response_class=HTMLResponse)
def delete_source(source_id: int, conn: sqlite3.Connection = Depends(get_db)):
    _get_source_or_404(conn, source_id)
    q.delete_source(conn, source_id)
    return HTMLResponse(content="")
```

In `app/templates/sources/index.html`, update the loop (currently):

```html
    {% for source in sources %}
      {% set needs_login = needs_login_by_id.get(source.id) if needs_login_by_id is defined else None %}
      {% include "sources/_row.html" %}
    {% endfor %}
```

to:

```html
    {% for source in sources %}
      {% set needs_login = needs_login_by_id.get(source.id) if needs_login_by_id is defined else None %}
      {% set job_count = job_counts_by_source.get(source.id, 0) if job_counts_by_source is defined else 0 %}
      {% include "sources/_row.html" %}
    {% endfor %}
```

In `app/templates/sources/_row.html`, the action `<td>` currently reads:

```html
  <td style="padding:0.5rem;">
    <button class="btn" style="font-size:0.85em; padding:3px 10px;"
      hx-get="/sources/{{ source.id }}/edit"
      hx-target="#source-row-{{ source.id }}"
      hx-swap="outerHTML">Edit</button>
  </td>
```

Replace with:

```html
  <td style="padding:0.5rem;">
    <button class="btn" style="font-size:0.85em; padding:3px 10px;"
      hx-get="/sources/{{ source.id }}/edit"
      hx-target="#source-row-{{ source.id }}"
      hx-swap="outerHTML">Edit</button>
    <button class="btn" style="font-size:0.85em; padding:3px 10px; margin-left:0.4rem;"
      hx-delete="/sources/{{ source.id }}"
      hx-target="#source-row-{{ source.id }}"
      hx-swap="outerHTML"
      hx-confirm="Delete '{{ source.name }}' and its {{ job_count }} job{{ 's' if job_count != 1 else '' }}? This cannot be undone.">Delete</button>
  </td>
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_routes_sources.py -v`
Expected: all PASS (existing tests too — `job_count` defaults to `0` via `_row.html`'s Jinja context whenever the variable is present, since every call site above now supplies it)

- [ ] **Step 5: Commit**

```bash
git add app/routes/sources.py app/templates/sources/index.html app/templates/sources/_row.html tests/test_routes_sources.py
git commit -m "feat: add cascading source deletion with job-count confirmation"
```

---

### Task 3: Query layer — `source_id` filter on `get_jobs`

**Files:**
- Modify: `app/db/queries.py:386-411` (`get_jobs`)
- Test: `tests/test_queries.py`

**Interfaces:**
- Produces: `get_jobs(conn, *, status=None, content_type=None, gate_status=None, source_id=None) -> list[dict]` (new `source_id` keyword, additive — existing callers unaffected)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_queries.py` near `test_get_jobs_filter_by_status`:

```python
def test_get_jobs_filter_by_source_id_ignores_status(conn):
    s1 = q.insert_source(conn, "s1", "http://x", "http")
    s2 = q.insert_source(conn, "s2", "http://y", "http")
    j1 = q.insert_job(conn, source_id=s1, url="http://job/1", title="T1", company="C", raw_text="r")
    j2 = q.insert_job(conn, source_id=s1, url="http://job/2", title="T2", company="C", raw_text="r")
    q.insert_job(conn, source_id=s2, url="http://job/3", title="T3", company="C", raw_text="r")
    q.update_job_feedback(conn, j2, "accepted", "note")

    result = q.get_jobs(conn, source_id=s1)

    assert {j["id"] for j in result} == {j1, j2}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_queries.py -k test_get_jobs_filter_by_source_id_ignores_status -v`
Expected: FAIL with `TypeError: get_jobs() got an unexpected keyword argument 'source_id'`

- [ ] **Step 3: Implement**

In `app/db/queries.py`, `get_jobs` currently reads:

```python
def get_jobs(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    content_type: str | None = None,
    gate_status: str | None = None,
) -> list[dict]:
    clauses, params = [], []
    if status is not None:
        clauses.append("jobs.status = ?")
        params.append(status)
    if content_type is not None:
        clauses.append("jobs.content_type = ?")
        params.append(content_type)
    if gate_status == "passed":
```

Change to:

```python
def get_jobs(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    content_type: str | None = None,
    gate_status: str | None = None,
    source_id: int | None = None,
) -> list[dict]:
    clauses, params = [], []
    if status is not None:
        clauses.append("jobs.status = ?")
        params.append(status)
    if content_type is not None:
        clauses.append("jobs.content_type = ?")
        params.append(content_type)
    if source_id is not None:
        clauses.append("jobs.source_id = ?")
        params.append(source_id)
    if gate_status == "passed":
```

(rest of the function body is unchanged)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_queries.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add app/db/queries.py tests/test_queries.py
git commit -m "feat: add source_id filter to get_jobs query"
```

---

### Task 4: `/jobs?source_id=` route — unfiltered-by-status source view

**Files:**
- Modify: `app/routes/jobs.py:58-162` (`_enrich_jobs` through `job_list`) and `342-384` (`job_bulk_feedback`)
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: `q.get_jobs(conn, source_id=...)` (Task 3), `q.get_source(conn, source_id) -> dict | None`
- Produces: `_get_filtered_jobs(conn, status, content_type, source_id=None)`, `_filter_context(request) -> dict` (now may include `filter_source_id`), `_stale_badge(conn, job, status, content_type, source_id=None)`, `_content_context(conn, status, content_type, source_id=None) -> dict` (now may include `filter_source_id` / `filter_source` keys), `GET /jobs` accepts `source_id: int | None`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_routes_jobs.py`:

```python
def test_job_list_source_id_shows_every_status_for_that_source(client, conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "http")
    other_sid = q.insert_source(conn, "other.no", "https://other.no", "http")
    j_new = q.insert_job(conn, source_id=sid, url="http://finn.no/job/1", title="New Job", company="C", raw_text="r")
    j_accepted = q.insert_job(conn, source_id=sid, url="http://finn.no/job/2", title="Accepted Job", company="C", raw_text="r")
    q.update_job_feedback(conn, j_accepted, "accepted", "")
    j_trash = q.insert_job(conn, source_id=sid, url="http://finn.no/job/3", title="Trashed Job", company="C", raw_text="r")
    q.update_job_feedback(conn, j_trash, "trash", "")
    q.insert_job(conn, source_id=other_sid, url="http://other.no/job/1", title="Other Source Job", company="C", raw_text="r")

    resp = client.get(f"/jobs?source_id={sid}")

    assert resp.status_code == 200
    assert "New Job" in resp.text
    assert "Accepted Job" in resp.text
    assert "Trashed Job" in resp.text
    assert "Other Source Job" not in resp.text


def test_job_list_source_id_header_shows_name_and_count(client, conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "http")
    q.insert_job(conn, source_id=sid, url="http://finn.no/job/1", title="Job A", company="C", raw_text="r")
    resp = client.get(f"/jobs?source_id={sid}")
    assert resp.status_code == 200
    assert "All jobs from finn.no (1)" in resp.text
    assert '<a href="/jobs">Clear filter</a>' in resp.text


def test_job_list_source_id_hides_status_tabs(client, conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "http")
    resp = client.get(f"/jobs?source_id={sid}")
    assert resp.status_code == 200
    assert "New Jobs (" not in resp.text


def test_job_feedback_within_source_view_stays_visible_no_stale_badge(client, conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "http")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/1", title="Job A", company="C", raw_text="r")

    resp = client.post(
        f"/jobs/{jid}/feedback?source_id={sid}",
        data={"status": "accepted", "note": ""},
    )

    assert resp.status_code == 200
    assert "Moved to" not in resp.text
    assert "Job A" in resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_routes_jobs.py -k "source_id" -v`
Expected: FAIL — `/jobs?source_id=` returns the default New-jobs view (all four new tests fail on their assertions) since `source_id` isn't wired up yet.

- [ ] **Step 3: Implement**

In `app/routes/jobs.py`, replace `_get_filtered_jobs` (currently lines 65-74):

```python
def _get_filtered_jobs(
    conn: sqlite3.Connection, status: str | None, content_type: str | None, source_id: int | None = None
) -> list[dict]:
    if source_id is not None:
        return q.get_jobs(conn, source_id=source_id)
    if status == "not_relevant":
        return q.get_jobs(conn, status="new", content_type="job_posting", gate_status="failed")
    if status is None and content_type is None:
        return q.get_jobs(conn, status="new", gate_status="passed")
    if status is None and content_type == "lead":
        return q.get_jobs(conn, status="new", content_type="lead")
    return q.get_jobs(conn, status=status, content_type=content_type)
```

Replace `_filter_context` (currently lines 77-83):

```python
def _filter_context(request: Request) -> dict:
    has_status = "status" in request.query_params
    has_content_type = "content_type" in request.query_params
    has_source_id = "source_id" in request.query_params
    if not (has_status or has_content_type or has_source_id):
        return {}
    source_id_param = request.query_params.get("source_id") or None
    return {
        "filter_status": request.query_params.get("status") or None,
        "filter_content_type": request.query_params.get("content_type") or None,
        "filter_source_id": int(source_id_param) if source_id_param else None,
    }
```

Replace `_stale_badge`'s signature and its call to `_get_filtered_jobs` (currently lines 90-93 — keep the rest of the function body, lines 94-107, unchanged):

```python
def _stale_badge(
    conn: sqlite3.Connection, job: dict, status: str | None, content_type: str | None, source_id: int | None = None
) -> dict | None:
    filtered_ids = {j["id"] for j in _get_filtered_jobs(conn, status, content_type, source_id)}
    if job["id"] in filtered_ids:
        return None
```

In `_render_updated_job_html` (currently lines 117-140), update the `_stale_badge` call:

```python
    stale_badge = None
    if filter_ctx:
        stale_badge = _stale_badge(
            conn, job, filter_ctx.get("filter_status"), filter_ctx.get("filter_content_type"),
            filter_ctx.get("filter_source_id"),
        )
```

Replace `_content_context` (currently lines 143-152):

```python
def _content_context(
    conn: sqlite3.Connection, status: str | None, content_type: str | None, source_id: int | None = None
) -> dict:
    jobs = _enrich_jobs(conn, _get_filtered_jobs(conn, status, content_type, source_id))
    counts = q.get_job_counts(conn)
    scenarios = q.get_scenarios(conn)
    effective_status = status if (status is not None or content_type is not None or source_id is not None) else "new"
    filter_source = q.get_source(conn, source_id) if source_id is not None else None
    return {
        "jobs": jobs, "stale_jobs": [], "counts": counts, "scenarios": scenarios,
        "status": effective_status, "content_type": content_type,
        "filter_status": status, "filter_content_type": content_type,
        "filter_source_id": source_id, "filter_source": filter_source,
    }
```

Replace `job_list` (currently lines 155-162):

```python
@router.get("/jobs", response_class=HTMLResponse)
def job_list(
    request: Request,
    status: str | None = None,
    content_type: str | None = None,
    source_id: int | None = None,
    conn: sqlite3.Connection = Depends(get_db),
):
    return templates.TemplateResponse(
        request, "jobs/list.html", _content_context(conn, status, content_type, source_id)
    )
```

In `job_bulk_feedback` (currently lines 342-384), replace the whole function:

```python
@router.post("/jobs/bulk-feedback", response_class=HTMLResponse)
def job_bulk_feedback(
    request: Request,
    job_ids: list[int] = Form(...),
    status: str = Form(...),
    note: str | None = Form(None),
    status_filter: str | None = Form(None),
    content_type_filter: str | None = Form(None),
    source_id_filter: str | None = Form(None),
    conn: sqlite3.Connection = Depends(get_db),
):
    for job_id in job_ids:
        q.update_job_feedback(conn, job_id, status, note)

    status_filter = status_filter or None
    content_type_filter = content_type_filter or None
    source_id_filter = source_id_filter or None
    source_id = int(source_id_filter) if source_id_filter else None
    jobs = _enrich_jobs(conn, _get_filtered_jobs(conn, status_filter, content_type_filter, source_id))
    matched_ids = {j["id"] for j in jobs}

    stale_jobs = []
    for job_id in job_ids:
        if job_id in matched_ids:
            continue
        job = q.get_job(conn, job_id)
        if not job:
            continue
        badge = _stale_badge(conn, job, status_filter, content_type_filter, source_id)
        if not badge:
            continue
        job["stale_badge"] = badge
        stale_jobs.append(job)
    stale_jobs = _enrich_jobs(conn, stale_jobs)

    counts = q.get_job_counts(conn)
    scenarios = q.get_scenarios(conn)
    effective_status = (
        status_filter
        if (status_filter is not None or content_type_filter is not None or source_id is not None)
        else "new"
    )
    filter_source = q.get_source(conn, source_id) if source_id is not None else None
    return templates.TemplateResponse(
        request, "jobs/_content.html",
        {
            "jobs": jobs, "stale_jobs": stale_jobs, "counts": counts, "scenarios": scenarios,
            "status": effective_status, "content_type": content_type_filter,
            "filter_status": status_filter, "filter_content_type": content_type_filter,
            "filter_source_id": source_id, "filter_source": filter_source,
        },
    )
```

Note: `_content.html` (Task 5) is what actually renders the "All jobs from X (N)" header / hides tabs based on `filter_source_id`, `filter_source`, and `jobs`. This task only needs the route to supply correct data — the header text assertions in Step 1 will only pass once Task 5's template change lands, so **run Task 4's tests again after Task 5** as part of that task's own test run; for now, confirm only `test_job_list_source_id_shows_every_status_for_that_source` and `test_job_feedback_within_source_view_stays_visible_no_stale_badge` pass — the header/tabs tests are expected to still fail until Task 5.

- [ ] **Step 4: Run the two data-only tests to verify they pass**

Run: `uv run pytest tests/test_routes_jobs.py -k "shows_every_status_for_that_source or stays_visible_no_stale_badge" -v`
Expected: PASS (2 tests). The header/tabs tests (`test_job_list_source_id_header_shows_name_and_count`, `test_job_list_source_id_hides_status_tabs`) still FAIL — that's expected, Task 5 fixes them.

- [ ] **Step 5: Run the full jobs route suite to check nothing existing broke, then commit**

Run: `uv run pytest tests/test_routes_jobs.py -v`
Expected: all PASS except the two header/tabs tests noted above (those two are allowed to fail here and get fixed in Task 5).

```bash
git add app/routes/jobs.py tests/test_routes_jobs.py
git commit -m "feat: add source_id query filter to /jobs route, ignoring status/content_type"
```

---

### Task 5: Jobs templates — source-scoped header and `source_id` propagation

**Files:**
- Modify: `app/templates/jobs/_content.html`
- Modify: `app/templates/jobs/_row.html`
- Modify: `app/templates/jobs/_feedback.html`
- Modify: `app/templates/jobs/list.html`
- Test: `tests/test_routes_jobs.py` (tests already written in Task 4 Step 1; this task makes them pass)

**Interfaces:**
- Consumes: `filter_source_id`, `filter_source` (dict or `None`), `jobs` (list) — all supplied by `_content_context` / `job_bulk_feedback` from Task 4

- [ ] **Step 1: Confirm the still-failing tests from Task 4**

Run: `uv run pytest tests/test_routes_jobs.py -k "source_id_header or source_id_hides" -v`
Expected: FAIL (these are the two tests Task 4 deferred)

- [ ] **Step 2: Implement the template changes**

In `app/templates/jobs/_content.html`, replace the opening `filter-bar` block (currently lines 1-22):

```html
<div class="filter-bar">
  {% if filter_source_id %}
    <div class="filter-links">
      <span>All jobs from {{ filter_source.name }} ({{ jobs | length }})</span>
      <a href="/jobs">Clear filter</a>
    </div>
  {% else %}
    <div class="filter-links">
      <a href="/jobs" {% if status == "new" and not content_type %}class="active"{% endif %}
        title="Job postings awaiting your decision — passed at least one scenario's relevance gate.">New Jobs (<span id="count-new">{{ counts.new }}</span>)</a>
      <a href="/jobs?content_type=lead" {% if content_type == "lead" %}class="active"{% endif %}
        title="Leads awaiting your decision — mentions of a possible opportunity, not full postings.">New Leads (<span id="count-lead">{{ counts.lead }}</span>)</a>
      <a href="/jobs?status=accepted" {% if status == "accepted" %}class="active"{% endif %}
        title="Jobs and leads you've accepted.">Accepted (<span id="count-accepted">{{ counts.accepted }}</span>)</a>
      <a href="/jobs?status=rejected" {% if status == "rejected" %}class="active"{% endif %}
        title="Jobs and leads you've rejected.">Rejected (<span id="count-rejected">{{ counts.rejected }}</span>)</a>
      <a href="/jobs?status=not_relevant" {% if status == "not_relevant" %}class="active"{% endif %}
        title="Real job postings that didn't pass any scenario's relevance gate — not a match for your current scenarios.">Not relevant (<span id="count-not_relevant">{{ counts.not_relevant }}</span>)</a>
      <a href="/jobs?status=trash" {% if status == "trash" %}class="active"{% endif %}
        title="Unusable postings (expired, spam, duplicate, wrong content) — kept temporarily.">Trash (<span id="count-trash">{{ counts.trash }}</span>)</a>
    </div>
  {% endif %}
  {% if jobs %}
    <label class="select-all">
      <input type="checkbox" class="select-all-checkbox" aria-label="Select all">
      Select all
    </label>
  {% endif %}
</div>
```

(the rest of `_content.html` — bulk bar, job loop, empty state — is unchanged, but the bulk-reset URL inside it needs the `source_id` suffix; find this line, currently:)

```html
        data-progress-url="/jobs/bulk-reset{% if filter_status is defined %}?status={{ filter_status or '' }}&content_type={{ filter_content_type or '' }}{% endif %}"
```

replace with:

```html
        data-progress-url="/jobs/bulk-reset{% if filter_status is defined %}?status={{ filter_status or '' }}&content_type={{ filter_content_type or '' }}{% if filter_source_id %}&source_id={{ filter_source_id }}{% endif %}{% endif %}"
```

In `app/templates/jobs/_row.html`, the single `hx-get` line (currently line 3):

```html
  hx-get="/jobs/{{ job.id }}/expand{% if is_detail_page %}?detail=1{% elif filter_status is defined %}?status={{ filter_status or '' }}&content_type={{ filter_content_type or '' }}{% endif %}"
```

replace with:

```html
  hx-get="/jobs/{{ job.id }}/expand{% if is_detail_page %}?detail=1{% elif filter_status is defined %}?status={{ filter_status or '' }}&content_type={{ filter_content_type or '' }}{% if filter_source_id %}&source_id={{ filter_source_id }}{% endif %}{% endif %}"
```

In `app/templates/jobs/_feedback.html`, four lines follow the same `{% elif filter_status is defined %}...{% endif %}` pattern (currently lines 14, 55, 72, 80). Apply the identical `{% if filter_source_id %}&source_id={{ filter_source_id }}{% endif %}` insertion (immediately before the closing `{% endif %}` of each) to all four:

- Line 14 (`/jobs/{{ job.id }}/collapse`):
  ```html
    hx-get="/jobs/{{ job.id }}/collapse{% if is_detail_page %}?detail=1{% elif filter_status is defined %}?status={{ filter_status or '' }}&content_type={{ filter_content_type or '' }}{% if filter_source_id %}&source_id={{ filter_source_id }}{% endif %}{% endif %}"
  ```
- Line 55 (`/jobs/{{ job.id }}/feedback`):
  ```html
    hx-post="/jobs/{{ job.id }}/feedback{% if filter_status is defined %}?status={{ filter_status or '' }}&content_type={{ filter_content_type or '' }}{% if filter_source_id %}&source_id={{ filter_source_id }}{% endif %}{% endif %}"
  ```
- Line 72 (`/jobs/{{ job.id }}/pass-as-new`):
  ```html
        data-progress-url="/jobs/{{ job.id }}/pass-as-new{% if filter_status is defined %}?status={{ filter_status or '' }}&content_type={{ filter_content_type or '' }}{% if filter_source_id %}&source_id={{ filter_source_id }}{% endif %}{% endif %}"
  ```
- Line 80 (`/jobs/{{ job.id }}/reset`):
  ```html
      data-progress-url="/jobs/{{ job.id }}/reset{% if filter_status is defined %}?status={{ filter_status or '' }}&content_type={{ filter_content_type or '' }}{% if filter_source_id %}&source_id={{ filter_source_id }}{% endif %}{% endif %}"
  ```

In `app/templates/jobs/list.html`, the bulk-form hidden fields currently read:

```html
<form id="bulk-form" hx-post="/jobs/bulk-feedback" hx-target="#jobs-content" hx-swap="innerHTML">
  <input type="hidden" name="status_filter" value="{{ status or '' }}">
  <input type="hidden" name="content_type_filter" value="{{ content_type or '' }}">
</form>
```

add a third hidden field:

```html
<form id="bulk-form" hx-post="/jobs/bulk-feedback" hx-target="#jobs-content" hx-swap="innerHTML">
  <input type="hidden" name="status_filter" value="{{ status or '' }}">
  <input type="hidden" name="content_type_filter" value="{{ content_type or '' }}">
  <input type="hidden" name="source_id_filter" value="{{ filter_source_id or '' }}">
</form>
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `uv run pytest tests/test_routes_jobs.py -v`
Expected: all PASS, including every test added in Task 4 Step 1.

- [ ] **Step 4: Run the full test suite to check for regressions**

Run: `uv run pytest -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add app/templates/jobs/_content.html app/templates/jobs/_row.html app/templates/jobs/_feedback.html app/templates/jobs/list.html
git commit -m "feat: propagate source_id filter through job row/bulk action URLs"
```

---

### Task 6: Fetch page — "View all jobs" link and Lifetime wording

**Files:**
- Modify: `app/templates/fetch/panel.html`
- Test: `tests/test_routes_fetch.py`

**Interfaces:**
- Consumes: nothing new — `source.id` and `stats.total_new` are already in scope in this template

- [ ] **Step 1: Write the failing tests and update the one that breaks**

Add to `tests/test_routes_fetch.py`:

```python
def test_fetch_panel_has_view_all_jobs_link_per_source(client, conn):
    sid = _seed(conn)
    resp = client.get("/fetch")
    assert resp.status_code == 200
    assert f'href="/jobs?source_id={sid}"' in resp.text
```

Update the existing `test_fetch_panel_shows_lifetime_stats` (currently asserting `"2 new" in resp.text`) to match the new wording:

```python
def test_fetch_panel_shows_lifetime_stats(client, conn):
    sid = _seed(conn)
    run = q.start_fetch_run(conn, sid)
    q.complete_fetch_run(conn, run, jobs_found=3, jobs_new=2)
    resp = client.get("/fetch")
    assert resp.status_code == 200
    assert "2 added" in resp.text
    assert "1 run" in resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_routes_fetch.py -k "view_all_jobs_link or shows_lifetime_stats" -v`
Expected: FAIL — no `View all jobs` link yet; `"2 added"` isn't in the response (still says `"2 new"`)

- [ ] **Step 3: Implement**

In `app/templates/fetch/panel.html`, the Lifetime cell currently reads:

```html
        <td style="padding:0.5rem;">
          {% set stats = stats_by_source.get(source.id) %}
          {% if stats %}
            {{ stats.run_count }} run{{ 's' if stats.run_count != 1 else '' }}, {{ stats.total_new }} new
            {% if stats.last_success_at %}<br><small>last success {{ stats.last_success_at }}</small>{% endif %}
          {% else %}
            —
          {% endif %}
        </td>
```

change `{{ stats.total_new }} new` to `{{ stats.total_new }} added`:

```html
        <td style="padding:0.5rem;">
          {% set stats = stats_by_source.get(source.id) %}
          {% if stats %}
            {{ stats.run_count }} run{{ 's' if stats.run_count != 1 else '' }}, {{ stats.total_new }} added
            {% if stats.last_success_at %}<br><small>last success {{ stats.last_success_at }}</small>{% endif %}
          {% else %}
            —
          {% endif %}
        </td>
```

The action cell currently reads:

```html
        <td style="padding:0.5rem;">
          {% if source.enabled %}
            <button class="btn" data-progress-url="/fetch/{{ source.id }}"
              data-progress-display="#fetch-progress-{{ source.id }}">Fetch</button>
          {% else %}
            <span style="color:#999;">disabled</span>
          {% endif %}
        </td>
```

add the link:

```html
        <td style="padding:0.5rem;">
          {% if source.enabled %}
            <button class="btn" data-progress-url="/fetch/{{ source.id }}"
              data-progress-display="#fetch-progress-{{ source.id }}">Fetch</button>
          {% else %}
            <span style="color:#999;">disabled</span>
          {% endif %}
          <a href="/jobs?source_id={{ source.id }}" class="btn" style="margin-left:0.4rem;">View all jobs</a>
        </td>
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_routes_fetch.py -v`
Expected: all PASS

- [ ] **Step 5: Run the full test suite and commit**

Run: `uv run pytest -v`
Expected: all PASS

```bash
git add app/templates/fetch/panel.html tests/test_routes_fetch.py
git commit -m "feat: add View all jobs link and reword Lifetime stats on fetch page"
```

---

## Manual verification (after all tasks land)

Use the `run-dev-server` skill (throwaway DB copy) and check by hand:
1. `/sources`: Delete button confirm text shows the right job count (create a source, add a couple of jobs to it manually via `/jobs` add-by-url pointed at that source if needed, or just eyeball the 0-job case), deleting removes the row and the jobs no longer show up anywhere.
2. `/fetch`: "View all jobs" link for a source with jobs in several statuses shows all of them, unfiltered, with the "All jobs from X (N) · Clear filter" header; accepting/rejecting a job from that view keeps it visible; "Clear filter" returns to the normal `/jobs` view.
3. `/fetch`: Lifetime column reads "N added" instead of "N new".
