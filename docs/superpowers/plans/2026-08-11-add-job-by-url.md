# Add job by URL Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user paste a job-posting URL into a small form on the jobs list page; the app fetches it over plain HTTP, inserts a job row against a synthetic "Manual" source, and runs it through the existing classify/summarize/score/fit pipeline, streaming progress the same way `/fetch/{source_id}` does today.

**Architecture:** A lazily-created `sources` row (`fetcher_type='manual'`) satisfies the `jobs.source_id NOT NULL` FK for hand-added jobs and is filtered out of source-management UI. A new `pipeline.run_add_job()` generator mirrors `run_fetch`'s per-job insert-then-`_ingest_posting` flow for a single ad-hoc URL. A new `POST /jobs/add-by-url` route fetches the URL with a small dedicated helper (not `HttpFetcher`, which swallows failures), streams progress, and ends with the same `HTML:`-chunk convention as `job_reset`/`job_bulk_reset` — except it re-renders the whole `jobs/_content.html` list (a brand-new row has no existing DOM element to OOB-swap into). The existing generic `data-progress-url` JS in `base.html` gains one small addition (`data-progress-url-input`) so a button can source its POST body from a paired text input, reused instead of writing bespoke fetch/stream JS.

**Tech Stack:** FastAPI, Jinja2 + HTMX (no JS build step — `base.html`'s inline `<script>` is edited directly), sqlite3, httpx + BeautifulSoup, pytest + respx for HTTP mocking.

## Global Constraints

- Migrations follow the existing hard-downtime, rebuild-in-place pattern in `app/db/schema.py` (see `_migrate_sources_fetcher_type`) — no dual-schema compatibility shims.
- Plain HTTP fetch only (`httpx` + BeautifulSoup `get_text()`), matching `HttpFetcher._parse` — no Playwright fallback.
- One URL per submission, no bulk paste.
- The synthetic "Manual" source must not appear in `sources/index.html` or `fetch/panel.html`.
- A fetch failure still creates a job row (`content_type='error'`); a duplicate URL creates no row.

---

### Task 1: Widen `sources.fetcher_type` to accept `'manual'`

**Files:**
- Modify: `app/db/schema.py:14` (the `_DDL` string's `sources` table), and add a new migration function + register it in `init_db` (currently `app/db/schema.py:358`)
- Test: `tests/test_schema.py`

**Interfaces:**
- Produces: `sources.fetcher_type` CHECK constraint now includes `'manual'`, both for fresh DBs (`_DDL`) and existing ones (new migration). Later tasks (Task 2's `get_or_create_manual_source`) depend on being able to `INSERT INTO sources (..., fetcher_type) VALUES (..., 'manual')` without an `IntegrityError`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_schema.py` (near `test_init_db_migrates_sources_table_missing_finn_listing_type`):

```python
def test_sources_accepts_manual_fetcher_type(conn):
    init_db(conn)
    conn.execute(
        "INSERT INTO sources (name, url, fetcher_type) VALUES ('Manual', '', 'manual')"
    )


def test_init_db_migrates_sources_table_missing_manual_type(conn):
    conn.executescript(
        """
        CREATE TABLE sources (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            fetcher_type TEXT NOT NULL CHECK(fetcher_type IN ('http', 'playwright', 'slack', 'finn_listing')),
            enabled INTEGER NOT NULL DEFAULT 1
        );
        """
    )
    conn.execute(
        "INSERT INTO sources (name, url, fetcher_type) VALUES ('s', 'http://x', 'http')"
    )
    conn.commit()

    init_db(conn)

    conn.execute(
        "INSERT INTO sources (name, url, fetcher_type) VALUES ('Manual', '', 'manual')"
    )
    rows = conn.execute("SELECT name, url, fetcher_type FROM sources ORDER BY id").fetchall()
    assert [dict(r) for r in rows] == [
        {"name": "s", "url": "http://x", "fetcher_type": "http"},
        {"name": "Manual", "url": "", "fetcher_type": "manual"},
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_schema.py -k manual -v`
Expected: both FAIL — `test_sources_accepts_manual_fetcher_type` with `sqlite3.IntegrityError: CHECK constraint failed`, `test_init_db_migrates_sources_table_missing_manual_type` the same.

- [ ] **Step 3: Widen the CHECK constraint in `_DDL`**

In `app/db/schema.py`, change line 14 from:

```python
    fetcher_type TEXT NOT NULL CHECK(fetcher_type IN ('http', 'playwright', 'slack', 'finn_listing')),
```

to:

```python
    fetcher_type TEXT NOT NULL CHECK(fetcher_type IN ('http', 'playwright', 'slack', 'finn_listing', 'manual')),
```

- [ ] **Step 4: Add the migration function**

Add this function to `app/db/schema.py` directly after `_migrate_sources_fetcher_type` (the function ending at line ~120, right before `_migrate_fetch_runs_source_fk`):

```python
def _migrate_sources_fetcher_type_manual(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='sources'"
    ).fetchone()
    if row is None or "'manual'" in row[0]:
        return
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript(
        """
        CREATE TABLE sources_new (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            fetcher_type TEXT NOT NULL CHECK(fetcher_type IN ('http', 'playwright', 'slack', 'finn_listing', 'manual')),
            enabled INTEGER NOT NULL DEFAULT 1
        );
        INSERT INTO sources_new SELECT * FROM sources;
        DROP TABLE sources;
        ALTER TABLE sources_new RENAME TO sources;
        """
    )
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
```

- [ ] **Step 5: Register the migration in `init_db`**

In `app/db/schema.py`, find `init_db` (line 358) and add the new call right after `_migrate_sources_fetcher_type(conn)`:

```python
def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL)
    _migrate_sources_fetcher_type(conn)
    _migrate_sources_fetcher_type_manual(conn)
    _migrate_fetch_runs_source_fk(conn)
    ...  # (rest unchanged)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_schema.py -v`
Expected: all PASS, including the two new tests and every pre-existing one (confirms the migration doesn't disturb `http`/`playwright`/`slack`/`finn_listing` rows).

- [ ] **Step 7: Commit**

```bash
git add app/db/schema.py tests/test_schema.py
git commit -m "feat: widen sources.fetcher_type to allow 'manual'"
```

---

### Task 2: Query helpers — `get_or_create_manual_source` and `get_job_by_url`

**Files:**
- Modify: `app/db/queries.py` (add two functions near the existing `# --- Sources ---` and `# --- Jobs ---` sections)
- Test: `tests/test_queries.py`

**Interfaces:**
- Consumes: nothing new (plain `sqlite3.Connection` + existing table schema from Task 1).
- Produces:
  - `get_or_create_manual_source(conn: sqlite3.Connection) -> int` — returns the `id` of the singleton `fetcher_type='manual'` source, creating it (`name="Manual"`, `url=""`) on first call.
  - `get_job_by_url(conn: sqlite3.Connection, url: str) -> dict | None` — plain lookup by `jobs.url`.
  - Task 4 (the new route) depends on both of these exact names/signatures.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_queries.py`:

```python
def test_get_or_create_manual_source_creates_once(conn):
    first_id = q.get_or_create_manual_source(conn)
    second_id = q.get_or_create_manual_source(conn)
    assert first_id == second_id
    source = q.get_source(conn, first_id)
    assert source["name"] == "Manual"
    assert source["fetcher_type"] == "manual"
    sources = [s for s in q.get_sources(conn) if s["fetcher_type"] == "manual"]
    assert len(sources) == 1


def test_get_job_by_url_returns_job(conn):
    sid = q.insert_source(conn, "s", "http://x", "http")
    jid = q.insert_job(conn, source_id=sid, url="http://job/1", title="T", company="C", raw_text="r")
    job = q.get_job_by_url(conn, "http://job/1")
    assert job["id"] == jid


def test_get_job_by_url_returns_none_when_missing(conn):
    assert q.get_job_by_url(conn, "http://nope") is None
```

Check `tests/test_queries.py`'s top for its `conn` fixture (it likely mirrors `tests/conftest.py`'s in-memory `init_db`'d connection — if the file already imports `from app.db import queries as q` and has a local `conn` fixture, no changes are needed beyond adding the tests above).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_queries.py -k "manual_source or get_job_by_url" -v`
Expected: FAIL with `AttributeError: module 'app.db.queries' has no attribute 'get_or_create_manual_source'` (and similarly for `get_job_by_url`).

- [ ] **Step 3: Implement `get_or_create_manual_source`**

In `app/db/queries.py`, add directly after `insert_source` (line ~50, before `update_source`):

```python
def get_or_create_manual_source(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT id FROM sources WHERE fetcher_type = 'manual'").fetchone()
    if row is not None:
        return row["id"]
    return insert_source(conn, "Manual", "", "manual")
```

- [ ] **Step 4: Implement `get_job_by_url`**

In `app/db/queries.py`, add directly after `url_exists` (line ~134, before `insert_job`):

```python
def get_job_by_url(conn: sqlite3.Connection, url: str) -> dict | None:
    return _row_to_dict(conn.execute("SELECT * FROM jobs WHERE url = ?", (url,)).fetchone())
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_queries.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add app/db/queries.py tests/test_queries.py
git commit -m "feat: add get_or_create_manual_source and get_job_by_url query helpers"
```

---

### Task 3: Pipeline — `run_add_job`

**Files:**
- Modify: `app/pipeline.py` (add a new function near `run_fetch`)
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `q.insert_job` (existing), `_ingest_posting` (existing private function in the same module), `q.get_profile`, `q.get_scenarios` (existing).
- Produces: `run_add_job(conn: sqlite3.Connection, client: openai.OpenAI, model: str, source_id: int, url: str, raw_text: str) -> Generator[str, None, None]` — inserts a job row and runs it through `_ingest_posting`, yielding the same progress strings `run_fetch` yields per job. Task 4 (the route) imports and calls this, and tests patch `app.routes.jobs.run_add_job` the same way they already patch `run_reprocess_job`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_pipeline.py` (it already has `conn`, `source`, `_drain`, and `_mock_client` fixtures/helpers — reuse them):

```python
from app.pipeline import run_add_job  # add to the existing import line at the top of the file


def test_run_add_job_stores_job_against_given_source(conn, source):
    client = _mock_client(
        '{"type": "job_posting", "reason": "full description"}',
        '{"title": "ML Engineer", "headline": "Great role", "summary": "Good ML role"}',
        '{"score": 0.9, "reasoning": "Great match"}',
    )
    messages, _ = _drain(
        run_add_job(conn, client, "llama3.2", source["id"], "http://example.com/job/1", "<p>We are hiring</p>")
    )
    jobs = q.get_jobs(conn)
    assert len(jobs) == 1
    assert jobs[0]["url"] == "http://example.com/job/1"
    assert jobs[0]["source_id"] == source["id"]
    assert jobs[0]["content_type"] == "job_posting"
    assert any("Classified as job_posting" in m for m in messages)


def test_run_add_job_irrelevant_content_not_persisted(conn, source):
    client = _mock_client(
        '{"type": "irrelevant", "reason": "not a job"}', "{}", "{}",
    )
    _drain(run_add_job(conn, client, "llama3.2", source["id"], "http://example.com/job/2", "<p>Buy socks now</p>"))
    assert q.get_jobs(conn) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_pipeline.py -k run_add_job -v`
Expected: FAIL with `ImportError: cannot import name 'run_add_job'`.

- [ ] **Step 3: Implement `run_add_job`**

In `app/pipeline.py`, add directly after `run_fetch` (which ends around line 148, right before `run_reprocess_job`):

```python
def run_add_job(
    conn: sqlite3.Connection,
    client: openai.OpenAI,
    model: str,
    source_id: int,
    url: str,
    raw_text: str,
) -> Generator[str, None, None]:
    job_id = q.insert_job(conn, source_id=source_id, url=url, title="", company="", raw_text=raw_text)
    profile = q.get_profile(conn)
    scenarios = q.get_scenarios(conn)
    yield from _ingest_posting(
        conn, client, model, job_id, raw_text, "", False, profile, scenarios, url=url,
    )
```

(`fallback_title=""` and `is_slack=False` match how `run_fetch` calls `_ingest_posting` for an `HttpFetcher`-sourced job, since `HttpFetcher` also yields `title=""`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_pipeline.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/pipeline.py tests/test_pipeline.py
git commit -m "feat: add run_add_job pipeline function for manually-added jobs"
```

---

### Task 4: Route — `POST /jobs/add-by-url`

**Files:**
- Modify: `app/routes/jobs.py` (add `_FetchError`, `_fetch_url_text`, `_content_context` refactor, and the new route)
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: `q.url_exists`, `q.get_job_by_url`, `q.get_or_create_manual_source`, `q.update_job_pipeline`, `q.insert_job` (all existing/Task 2), `run_add_job` (Task 3), `q.get_job_counts` (existing).
- Produces: `POST /jobs/add-by-url` accepting form field `url`, optionally `status`/`content_type` query params (mirroring how `/jobs/bulk-reset` forwards the active filter). Streams newline-delimited progress, ending with two `HTML:`-prefixed chunks (refreshed `jobs/_content.html`, then `jobs/_counts_oob.html`) — same shape Task 6's template consumes.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_routes_jobs.py`. First, the imports at the top of the file need `respx` and `httpx` — check if they're already imported; if not add:

```python
import httpx
import respx
```

Then add these tests (place near the other reset/bulk-reset tests):

```python
def _fake_run_add_job(conn, client, model, source_id, url, raw_text):
    jid = q.insert_job(conn, source_id=source_id, url=url, title="Fake Title", company="Acme", raw_text=raw_text)
    q.update_job_pipeline(conn, jid, simplified_content=raw_text, content_type="job_posting", summary="A role")
    yield f"Classified as job_posting: {url}"


@respx.mock
def test_add_job_by_url_success_inserts_job_and_streams_progress(client, conn):
    respx.get("http://example.com/job/1").mock(
        return_value=httpx.Response(200, text="<html><body><p>We are hiring</p></body></html>")
    )
    with patch("app.routes.jobs.run_add_job", side_effect=_fake_run_add_job):
        resp = client.post("/jobs/add-by-url", data={"url": "http://example.com/job/1"})
    assert resp.status_code == 200
    assert "Classified as job_posting" in resp.text
    jobs = q.get_jobs(conn)
    assert len(jobs) == 1
    assert jobs[0]["url"] == "http://example.com/job/1"
    source = q.get_source(conn, jobs[0]["source_id"])
    assert source["fetcher_type"] == "manual"


@respx.mock
def test_add_job_by_url_stream_ends_with_html_chunks(client, conn):
    respx.get("http://example.com/job/1").mock(
        return_value=httpx.Response(200, text="<html><body><p>We are hiring</p></body></html>")
    )
    with patch("app.routes.jobs.run_add_job", side_effect=_fake_run_add_job):
        resp = client.post("/jobs/add-by-url", data={"url": "http://example.com/job/1"})
    assert resp.status_code == 200
    assert 'HTML:<div class="filter-bar">' in resp.text
    assert 'HTML:<span id="count-new" hx-swap-oob="true">' in resp.text


@respx.mock
def test_add_job_by_url_duplicate_url_does_not_insert(client, conn):
    sid = q.insert_source(conn, "s", "http://x", "http")
    jid = q.insert_job(conn, source_id=sid, url="http://example.com/job/1", title="T", company="C", raw_text="r")

    resp = client.post("/jobs/add-by-url", data={"url": "http://example.com/job/1"})

    assert resp.status_code == 200
    assert "Already tracked" in resp.text
    assert f"/jobs/{jid}" in resp.text
    assert len(q.get_jobs(conn)) == 1


@respx.mock
def test_add_job_by_url_fetch_failure_inserts_error_job(client, conn):
    respx.get("http://example.com/broken").mock(return_value=httpx.Response(404))

    resp = client.post("/jobs/add-by-url", data={"url": "http://example.com/broken"})

    assert resp.status_code == 200
    assert "Failed to fetch" in resp.text
    jobs = q.get_jobs(conn)
    assert len(jobs) == 1
    assert jobs[0]["content_type"] == "error"
    assert jobs[0]["url"] == "http://example.com/broken"


@respx.mock
def test_add_job_by_url_fetch_network_error_inserts_error_job(client, conn):
    respx.get("http://example.com/unreachable").mock(side_effect=httpx.ConnectError("boom"))

    resp = client.post("/jobs/add-by-url", data={"url": "http://example.com/unreachable"})

    assert resp.status_code == 200
    assert "Failed to fetch" in resp.text
    jobs = q.get_jobs(conn)
    assert len(jobs) == 1
    assert jobs[0]["content_type"] == "error"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_routes_jobs.py -k add_job_by_url -v`
Expected: FAIL with 404 (`POST /jobs/add-by-url` doesn't exist yet).

- [ ] **Step 3: Add imports and the fetch helper to `app/routes/jobs.py`**

Change the top of `app/routes/jobs.py` from:

```python
from __future__ import annotations
import sqlite3
import openai
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from app.deps import get_db, get_ai_client, get_model
from app.db import queries as q
from app.pipeline import run_reprocess_job, run_pass_as_new
from app.template_env import templates
```

to:

```python
from __future__ import annotations
import sqlite3
import httpx
import openai
from bs4 import BeautifulSoup
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from app.deps import get_db, get_ai_client, get_model
from app.db import queries as q
from app.pipeline import run_reprocess_job, run_pass_as_new, run_add_job
from app.template_env import templates
```

Then add, directly after the `router = APIRouter()` line:

```python
class _FetchError(Exception):
    pass


def _fetch_url_text(url: str) -> str:
    try:
        resp = httpx.get(url, timeout=30, follow_redirects=True)
    except httpx.HTTPError as exc:
        raise _FetchError(str(exc)) from exc
    if resp.status_code != 200:
        raise _FetchError(f"HTTP {resp.status_code}")
    soup = BeautifulSoup(resp.text, "html.parser")
    text = soup.get_text(separator="\n")
    if not text.strip():
        raise _FetchError("page had no extractable text")
    return text
```

- [ ] **Step 4: Extract `_content_context` and use it from `job_list`**

Find the existing `job_list` handler (`app/routes/jobs.py:98-116`):

```python
@router.get("/jobs", response_class=HTMLResponse)
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
        {
            "jobs": jobs, "stale_jobs": [], "counts": counts, "scenarios": scenarios,
            "status": effective_status, "content_type": content_type,
            "filter_status": status, "filter_content_type": content_type,
        },
    )
```

Replace it with:

```python
def _content_context(conn: sqlite3.Connection, status: str | None, content_type: str | None) -> dict:
    jobs = _enrich_jobs(conn, _get_filtered_jobs(conn, status, content_type))
    counts = q.get_job_counts(conn)
    scenarios = q.get_scenarios(conn)
    effective_status = status if (status is not None or content_type is not None) else "new"
    return {
        "jobs": jobs, "stale_jobs": [], "counts": counts, "scenarios": scenarios,
        "status": effective_status, "content_type": content_type,
        "filter_status": status, "filter_content_type": content_type,
    }


@router.get("/jobs", response_class=HTMLResponse)
def job_list(
    request: Request,
    status: str | None = None,
    content_type: str | None = None,
    conn: sqlite3.Connection = Depends(get_db),
):
    return templates.TemplateResponse(request, "jobs/list.html", _content_context(conn, status, content_type))
```

This is a pure refactor (identical behavior for `GET /jobs`) — run `uv run pytest tests/test_routes_jobs.py -v` now and confirm every pre-existing test still passes before moving on.

- [ ] **Step 5: Add the `POST /jobs/add-by-url` route**

Add at the end of `app/routes/jobs.py`:

```python
@router.post("/jobs/add-by-url")
def job_add_by_url(
    request: Request,
    url: str = Form(...),
    conn: sqlite3.Connection = Depends(get_db),
    client: openai.OpenAI = Depends(get_ai_client),
    model: str = Depends(get_model),
):
    status = request.query_params.get("status") or None
    content_type = request.query_params.get("content_type") or None

    def stream():
        existing = q.get_job_by_url(conn, url)
        if existing is not None:
            yield f"Already tracked: {url} (see /jobs/{existing['id']})\n"
        else:
            source_id = q.get_or_create_manual_source(conn)
            try:
                raw_text = _fetch_url_text(url)
            except _FetchError as exc:
                job_id = q.insert_job(conn, source_id=source_id, url=url, title=url, company="", raw_text="")
                q.update_job_pipeline(conn, job_id, simplified_content="", content_type="error")
                yield f"Failed to fetch: {exc}\n"
            else:
                gen = run_add_job(conn, client, model, source_id, url, raw_text)
                try:
                    while True:
                        yield next(gen) + "\n"
                except StopIteration:
                    pass

        html = templates.get_template("jobs/_content.html").render(
            request=request, **_content_context(conn, status, content_type)
        )
        yield "HTML:" + html.replace("\n", "") + "\n"
        counts_html = templates.get_template("jobs/_counts_oob.html").render(
            request=request, counts=q.get_job_counts(conn)
        )
        yield "HTML:" + counts_html.replace("\n", "")

    return StreamingResponse(stream(), media_type="text/plain")
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_routes_jobs.py -v`
Expected: all PASS, including the five new `add_job_by_url` tests and every pre-existing test (confirms the `job_list` refactor in Step 4 didn't change behavior).

- [ ] **Step 7: Commit**

```bash
git add app/routes/jobs.py tests/test_routes_jobs.py
git commit -m "feat: add POST /jobs/add-by-url route"
```

---

### Task 5: Hide the Manual source from source-management UI

**Files:**
- Modify: `app/routes/sources.py:25-27` (`sources_page`), `app/routes/fetch.py:15-27` (`fetch_panel`)
- Test: `tests/test_routes_sources.py`, `tests/test_routes_fetch.py`

**Interfaces:**
- Consumes: `q.get_sources` (existing), `q.get_or_create_manual_source` (Task 2).
- Produces: no new functions — just filters the `sources` list passed into `sources/index.html` and `fetch/panel.html` templates.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_routes_sources.py`:

```python
def test_sources_page_excludes_manual_source(client, conn):
    q.get_or_create_manual_source(conn)
    q.insert_source(conn, "Real Source", "http://x", "http")
    resp = client.get("/sources")
    assert resp.status_code == 200
    assert "Manual" not in resp.text
    assert "Real Source" in resp.text
```

Add to `tests/test_routes_fetch.py`:

```python
def test_fetch_panel_excludes_manual_source(client, conn):
    q.get_or_create_manual_source(conn)
    q.insert_source(conn, "Real Source", "http://x", "http")
    resp = client.get("/fetch")
    assert resp.status_code == 200
    assert "Manual" not in resp.text
    assert "Real Source" in resp.text
```

Check both test files already `from app.db import queries as q` at the top — if not, add that import.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_routes_sources.py tests/test_routes_fetch.py -k manual -v`
Expected: both FAIL — "Manual" appears in the response text.

- [ ] **Step 3: Filter in `sources_page`**

In `app/routes/sources.py`, change:

```python
@router.get("/sources", response_class=HTMLResponse)
def sources_page(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    return templates.TemplateResponse(request, "sources/index.html", {"sources": q.get_sources(conn)})
```

to:

```python
@router.get("/sources", response_class=HTMLResponse)
def sources_page(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    sources = [s for s in q.get_sources(conn) if s["fetcher_type"] != "manual"]
    return templates.TemplateResponse(request, "sources/index.html", {"sources": sources})
```

- [ ] **Step 4: Filter in `fetch_panel`**

In `app/routes/fetch.py`, change:

```python
@router.get("/fetch", response_class=HTMLResponse)
def fetch_panel(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    sources = q.get_sources(conn)
    runs = q.get_recent_fetch_runs(conn)
```

to:

```python
@router.get("/fetch", response_class=HTMLResponse)
def fetch_panel(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    sources = [s for s in q.get_sources(conn) if s["fetcher_type"] != "manual"]
    runs = q.get_recent_fetch_runs(conn)
```

(the rest of the function is unchanged — `runs_by_source`/`stats_by_source` are keyed by source id and only ever looked up for sources actually iterated over in the template).

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_routes_sources.py tests/test_routes_fetch.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add app/routes/sources.py app/routes/fetch.py tests/test_routes_sources.py tests/test_routes_fetch.py
git commit -m "feat: hide synthetic Manual source from sources and fetch panels"
```

---

### Task 6: Frontend — add-by-URL form and shared progress-button JS

**Files:**
- Modify: `app/templates/jobs/list.html`, `app/templates/base.html`
- Test: `tests/test_routes_jobs.py` (markup assertions)

**Interfaces:**
- Consumes: `POST /jobs/add-by-url` (Task 4).
- Produces: no new backend interfaces — pure template/JS.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_routes_jobs.py`:

```python
def test_job_list_has_add_by_url_form(client, conn):
    resp = client.get("/jobs")
    assert resp.status_code == 200
    assert 'id="add-job-url"' in resp.text
    assert 'data-progress-url="/jobs/add-by-url"' in resp.text
    assert 'data-progress-url-input="#add-job-url"' in resp.text
    assert 'data-progress-target="#jobs-content"' in resp.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_routes_jobs.py -k add_by_url_form -v`
Expected: FAIL — none of those strings are in the response yet.

- [ ] **Step 3: Add the form to `jobs/list.html`**

Current `app/templates/jobs/list.html`:

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

Replace with:

```html
{% extends "base.html" %}
{% block title %}Jobs — Job Seek{% endblock %}
{% block content %}
<h1>Jobs</h1>
<form id="bulk-form" hx-post="/jobs/bulk-feedback" hx-target="#jobs-content" hx-swap="innerHTML">
  <input type="hidden" name="status_filter" value="{{ status or '' }}">
  <input type="hidden" name="content_type_filter" value="{{ content_type or '' }}">
</form>
<form class="add-job-by-url" style="display:flex; gap:0.5rem; align-items:center; margin-bottom:1rem;">
  <input type="url" id="add-job-url" name="url" placeholder="Paste a job posting URL…" required style="flex:1; min-width:200px;">
  <button type="button" class="btn"
    data-progress-url="/jobs/add-by-url{% if filter_status is defined %}?status={{ filter_status or '' }}&content_type={{ filter_content_type or '' }}{% endif %}"
    data-progress-url-input="#add-job-url"
    data-progress-target="#jobs-content"
    data-progress-display="#add-job-progress">Add</button>
  <span id="add-job-progress" class="reset-progress" aria-live="polite"></span>
</form>
<div id="jobs-content">
  {% include "jobs/_content.html" %}
</div>
{% endblock %}
```

- [ ] **Step 4: Extend `base.html`'s progress-button JS to read a paired input**

In `app/templates/base.html`, find the body-construction block (around line 124):

```javascript
        var body = null;
        if (el.hasAttribute("data-progress-jobs")) {
          var checked = document.querySelectorAll('input[name="job_ids"]:checked');
          if (!checked.length) return;
          body = new URLSearchParams();
          checked.forEach(function (cb) { body.append("job_ids", cb.value); });
        }
```

Replace with:

```javascript
        var body = null;
        if (el.hasAttribute("data-progress-jobs")) {
          var checked = document.querySelectorAll('input[name="job_ids"]:checked');
          if (!checked.length) return;
          body = new URLSearchParams();
          checked.forEach(function (cb) { body.append("job_ids", cb.value); });
        }
        if (el.hasAttribute("data-progress-url-input")) {
          var urlInputEl = document.querySelector(el.getAttribute("data-progress-url-input"));
          if (!urlInputEl || !urlInputEl.value) return;
          body = new URLSearchParams();
          body.append("url", urlInputEl.value);
        }
```

Then find `finish()` (a few lines below), specifically the part right after the failure check:

```javascript
        function finish() {
          if (tickTimer) clearInterval(tickTimer);
          delete el.dataset.progressRunning;
          if (failed) {
            progressEl.style.color = "#dc3545";
            progressEl.textContent = lastLine || "Failed";
            return;
          }
          progressEl.remove();
          if (oobMode) {
```

Change it to clear the input on success:

```javascript
        function finish() {
          if (tickTimer) clearInterval(tickTimer);
          delete el.dataset.progressRunning;
          if (failed) {
            progressEl.style.color = "#dc3545";
            progressEl.textContent = lastLine || "Failed";
            return;
          }
          progressEl.remove();
          if (el.hasAttribute("data-progress-url-input")) {
            var clearInputEl = document.querySelector(el.getAttribute("data-progress-url-input"));
            if (clearInputEl) clearInputEl.value = "";
          }
          if (oobMode) {
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_routes_jobs.py -v`
Expected: all PASS, including `test_job_list_has_add_by_url_form` and every pre-existing test in the file.

- [ ] **Step 6: Run the full test suite**

Run: `uv run pytest -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add app/templates/jobs/list.html app/templates/base.html tests/test_routes_jobs.py
git commit -m "feat: add paste-a-URL form to jobs list page"
```

---

### Task 7: Manual verification

**Files:** none (verification only)

- [ ] **Step 1: Launch the dev server**

Use the `run-dev-server` skill (throwaway copy of `job-seek.db`, never the live one).

- [ ] **Step 2: Exercise the golden path**

In a browser, go to `/jobs`, paste a real static job-posting URL into the new field, click Add, and confirm: progress lines stream, the page updates in place (no reload), the new job appears in the list (if it matches the current filter) with source "Manual".

- [ ] **Step 3: Exercise edge cases**

- Paste the same URL again → confirm the "Already tracked" message with a working link to the existing job.
- Paste an unreachable/404 URL → confirm an error job row appears (`content_type=error`) and an error message streams.
- Confirm `/sources` and `/fetch` do not list "Manual" as a source.
- Confirm the input field clears itself after a successful add.

- [ ] **Step 4: Stop the dev server**

Per `CLAUDE.md`, stop the dev server once manual testing is done.

---

## Self-Review Notes

- **Spec coverage:** §1 (Manual source) → Task 1 + 2 + 5. §2 (fetching) → Task 4 Step 3 (`_fetch_url_text`). §3 (duplicate detection) → Task 4 Step 5 (`q.get_job_by_url` short-circuit). §4 (route) → Task 4. §5 (UI) → Task 6. Testing section items 1–8 in the spec are each covered by a task's tests above (migration test = Task 1; `get_or_create_manual_source` test = Task 2; duplicate/error/success route tests = Task 4; manual-source-hidden tests = Task 5; browser verification = Task 7).
- **Type consistency checked:** `run_add_job(conn, client, model, source_id, url, raw_text)` signature in Task 3 matches the call in Task 4 Step 5 exactly. `_FetchError`/`_fetch_url_text` defined in Task 4 Step 3, used in Step 5 of the same task. `get_or_create_manual_source`/`get_job_by_url` defined in Task 2, used in Task 4 Step 5 and Task 5's tests.
