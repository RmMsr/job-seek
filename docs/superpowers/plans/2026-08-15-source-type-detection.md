# Source-type auto-detection & add-source unfold Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Sources page's manual `fetcher_type` dropdown with automatic detection (Slack/finn.no by URL pattern, everything else by fetch + AI listing-check), behind an unfolding "+ Add source" control that mirrors the Jobs page's minimal "paste a URL" UX — and fix two related pre-existing bugs found while reading the affected files.

**Architecture:** A new fast-path classifier (`classify_known_source`) recognizes Slack and finn.no URLs instantly from the URL string. Everything else goes through the same fetch → Playwright-fallback → `detect_listing` AI pipeline the Jobs page's add-by-url flow already has, refactored so both flows share the underlying fetch helpers instead of duplicating them. The Sources page gets two new streaming endpoints (`/sources/detect`, `/sources/detect/confirm`) that reuse the app's existing `data-progress-url` streaming-progress JS and OOB multi-chunk-refresh mechanism — no new JavaScript is needed anywhere in this plan.

**Tech Stack:** FastAPI (Python), Jinja2 templates, htmx + a small shared vanilla-JS streaming helper in `base.html`, pytest + `respx` (httpx mocking) + `unittest.mock.patch`, SQLite.

## Global Constraints

- Only `slack`, `finn_listing`, `generic_listing` are ever assigned by detection. `http` and `playwright` stay legal in the DB `CHECK` constraint and the per-row edit dropdown (existing rows must remain editable) but are never chosen by the new add flow.
- `fetcher_type` values arriving at either confirm endpoint (`/sources/detect/confirm`, `/jobs/add-listing-source`) must be validated against `{"slack", "finn_listing", "generic_listing"}` server-side (400 otherwise) — both are hidden form fields round-tripped from our own templates, but neither endpoint should trust that blindly.
- No new JavaScript: every new interactive element uses the existing `data-progress-url` / `data-progress-target` / `data-progress-oob` / `data-progress-body-*` attributes already implemented in `app/templates/base.html`.
- Every task must leave the full test suite green (`uv run pytest`) before its commit.

---

## File Structure

**Create:**
- `app/ai/classify_known_source.py` — `classify_known_source(url) -> str | None`, `DETECTABLE_FETCHER_TYPES` constant
- `app/templates/sources/_table.html` — the sources `<table>`/"no sources" block, extracted verbatim from `index.html`, wrapped in `id="sources-table"`
- `app/templates/sources/_add_form.html` — the "paste a URL" mini-form, wrapped in `id="add-source-panel"`
- `app/templates/sources/_detect_confirm.html` — detected-type confirm panel content
- `app/templates/sources/_detect_mismatch.html` — "looks like a single job" warning panel content
- `tests/test_classify_known_source.py`

**Modify:**
- `app/fetchers/slack.py` — rename private `_URL_RE` to public `SLACK_URL_RE`
- `app/fetchers/content.py` — add `FetchError`, `NoContentError`, `fetch_url_html`, `extract_text_or_raise` (moved out of `app/routes/jobs.py`)
- `app/routes/jobs.py` — use the moved helpers; wire `classify_known_source` into `job_add_by_url` and `job_add_listing_source`
- `app/templates/jobs/_listing_confirm.html` — carry the detected `fetcher_type` through as a hidden field
- `app/routes/sources.py` — remove `create_source`; add `_visible_sources`/`_sources_context` helpers; add `/sources/detect` and `/sources/detect/confirm`
- `app/templates/sources/index.html` — replace the static add form with the `<details>` unfold + `_table.html` include
- `app/templates/sources/_row_edit.html` — add the missing `generic_listing` `<option>`
- `tests/test_routes_sources.py` — remove/replace the `POST /sources` tests; add detect/confirm/edit-dropdown tests
- `tests/test_routes_jobs.py` — update `add-listing-source` tests for the new `fetcher_type` field; add finn/slack passthrough + validation tests
- `tests/test_fetcher_content.py` — add tests for the moved `fetch_url_html`/`extract_text_or_raise`

---

## Task 1: `classify_known_source` — the fast-path classifier

**Files:**
- Modify: `app/fetchers/slack.py:10,45`
- Create: `app/ai/classify_known_source.py`
- Create: `tests/test_classify_known_source.py`

**Interfaces:**
- Produces: `classify_known_source(url: str) -> str | None` (returns `"slack"`, `"finn_listing"`, or `None`), `DETECTABLE_FETCHER_TYPES: set[str] = {"slack", "finn_listing", "generic_listing"}` — both consumed by Tasks 5, 6, 8.

- [ ] **Step 1: Rename `_URL_RE` to `SLACK_URL_RE` in `app/fetchers/slack.py`**

In `app/fetchers/slack.py`, line 10 currently reads:
```python
_URL_RE = re.compile(r"^https://([a-zA-Z0-9-]+)\.slack\.com/(?:archives|messages)/([A-Za-z0-9]+)/?$")
```
Change to:
```python
SLACK_URL_RE = re.compile(r"^https://([a-zA-Z0-9-]+)\.slack\.com/(?:archives|messages)/([A-Za-z0-9]+)/?$")
```
And line 45 (inside `_resolve_target`), currently:
```python
    match = _URL_RE.match(source_url)
```
Change to:
```python
    match = SLACK_URL_RE.match(source_url)
```

- [ ] **Step 2: Run the existing Slack fetcher tests to confirm the rename didn't break anything**

Run: `uv run pytest tests/test_fetcher_slack.py -v`
Expected: PASS (all existing tests, unaffected — they exercise behavior, not the private name).

- [ ] **Step 3: Write the failing tests for `classify_known_source`**

Create `tests/test_classify_known_source.py`:
```python
from app.ai.classify_known_source import classify_known_source, DETECTABLE_FETCHER_TYPES


def test_classify_known_source_recognizes_slack_archive_url():
    assert classify_known_source("https://example-workspace.slack.com/archives/C0EXAMPLE1") == "slack"


def test_classify_known_source_recognizes_slack_messages_url():
    assert classify_known_source("https://example-workspace.slack.com/messages/C0EXAMPLE1") == "slack"


def test_classify_known_source_recognizes_finn_no_bare_host():
    assert classify_known_source("https://finn.no/job/browse.html") == "finn_listing"


def test_classify_known_source_recognizes_finn_no_www_subdomain():
    assert classify_known_source("https://www.finn.no/job/search?occupation=1.23") == "finn_listing"


def test_classify_known_source_returns_none_for_unrelated_url():
    assert classify_known_source("https://careers.example.com/jobs") is None


def test_classify_known_source_does_not_match_lookalike_host():
    # "notfinn.no" must not be treated as finn.no via a naive substring/endswith check
    assert classify_known_source("https://notfinn.no/jobs") is None


def test_detectable_fetcher_types_constant():
    assert DETECTABLE_FETCHER_TYPES == {"slack", "finn_listing", "generic_listing"}
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `uv run pytest tests/test_classify_known_source.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.ai.classify_known_source'`

- [ ] **Step 5: Implement `classify_known_source`**

Create `app/ai/classify_known_source.py`:
```python
from __future__ import annotations
from urllib.parse import urlsplit
from app.fetchers.slack import SLACK_URL_RE

DETECTABLE_FETCHER_TYPES = {"slack", "finn_listing", "generic_listing"}


def classify_known_source(url: str) -> str | None:
    if SLACK_URL_RE.match(url):
        return "slack"
    host = urlsplit(url).netloc.lower()
    if host == "finn.no" or host.endswith(".finn.no"):
        return "finn_listing"
    return None
```

Note on Step 3's lookalike-host test: `"notfinn.no"` does not equal `"finn.no"` and does not end with `".finn.no"` (it ends with `"tfinn.no"`), so this passes with the implementation above — no special-casing needed.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_classify_known_source.py -v`
Expected: PASS (7 tests)

- [ ] **Step 7: Commit**

```bash
git add app/fetchers/slack.py app/ai/classify_known_source.py tests/test_classify_known_source.py
git commit -m "feat: add classify_known_source fast-path fetcher_type detector"
```

---

## Task 2: Move fetch/content helpers out of `jobs.py` into `app/fetchers/content.py`

**Files:**
- Modify: `app/fetchers/content.py`
- Modify: `app/routes/jobs.py:1-55,420-449`
- Modify: `tests/test_fetcher_content.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `app.fetchers.content.FetchError`, `NoContentError(FetchError)`, `fetch_url_html(url: str) -> str` (raises `FetchError`), `extract_text_or_raise(html: str) -> str` (raises `NoContentError`) — consumed by Task 5 (`app/routes/sources.py`'s new detect endpoint) and continue to be used by `app/routes/jobs.py`.

This is a pure refactor (same behavior, new home) so both flows can share one implementation instead of Task 5 duplicating jobs.py's private helpers.

- [ ] **Step 1: Write the failing tests for the moved helpers**

Append to `tests/test_fetcher_content.py` (it currently imports `MIN_CONTENT_LENGTH, extract_text, has_enough_content, has_enough_text` — add to that import line and add these tests):
```python
import httpx
import pytest
import respx
from app.fetchers.content import (
    MIN_CONTENT_LENGTH, extract_text, has_enough_content, has_enough_text,
    FetchError, NoContentError, fetch_url_html, extract_text_or_raise,
)


@respx.mock
def test_fetch_url_html_returns_body_on_200():
    respx.get("http://example.com/page").mock(return_value=httpx.Response(200, text="<p>hi</p>"))
    assert fetch_url_html("http://example.com/page") == "<p>hi</p>"


@respx.mock
def test_fetch_url_html_raises_on_non_200():
    respx.get("http://example.com/missing").mock(return_value=httpx.Response(404))
    with pytest.raises(FetchError, match="HTTP 404"):
        fetch_url_html("http://example.com/missing")


@respx.mock
def test_fetch_url_html_raises_fetch_error_on_connection_failure():
    respx.get("http://example.com/down").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(FetchError):
        fetch_url_html("http://example.com/down")


def test_extract_text_or_raise_returns_text_when_enough_content():
    html = "<html><body><p>" + ("word " * 50) + "</p></body></html>"
    assert "word" in extract_text_or_raise(html)


def test_extract_text_or_raise_raises_no_content_error_when_thin():
    html = "<html><body><noscript>Enable JavaScript</noscript></body></html>"
    with pytest.raises(NoContentError):
        extract_text_or_raise(html)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_fetcher_content.py -v`
Expected: FAIL with `ImportError: cannot import name 'FetchError' from 'app.fetchers.content'`

- [ ] **Step 3: Add the helpers to `app/fetchers/content.py`**

Replace the full contents of `app/fetchers/content.py` with:
```python
from __future__ import annotations
import httpx
from bs4 import BeautifulSoup

MIN_CONTENT_LENGTH = 200  # below this, treat as no real content (e.g. a JS-only page's noscript shell)


class FetchError(Exception):
    pass


class NoContentError(FetchError):
    pass


def extract_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    return soup.get_text(separator="\n")


def has_enough_content(html: str) -> bool:
    return has_enough_text(extract_text(html))


def has_enough_text(text: str) -> bool:
    return len(text.strip()) >= MIN_CONTENT_LENGTH


def fetch_url_html(url: str) -> str:
    try:
        resp = httpx.get(url, timeout=30, follow_redirects=True)
    except httpx.HTTPError as exc:
        raise FetchError(str(exc)) from exc
    if resp.status_code != 200:
        raise FetchError(f"HTTP {resp.status_code}")
    return resp.text


def extract_text_or_raise(html: str) -> str:
    if not has_enough_content(html):
        raise NoContentError("page had little to no extractable text")
    return extract_text(html)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_fetcher_content.py -v`
Expected: PASS (all tests, old and new)

- [ ] **Step 5: Update `app/routes/jobs.py` to use the moved helpers instead of its own copies**

In `app/routes/jobs.py`, the import block (lines 1-15) currently includes:
```python
from app.fetchers.content import has_enough_content, extract_text as _extract_text_raw
```
Change that one line to:
```python
from app.fetchers.content import has_enough_content, FetchError, NoContentError, fetch_url_html, extract_text_or_raise
```

Then delete the now-redundant local definitions (lines 20-41 in the original file):
```python
class _FetchError(Exception):
    pass


class _NoContentError(_FetchError):
    pass


def _fetch_url_html(url: str) -> str:
    try:
        resp = httpx.get(url, timeout=30, follow_redirects=True)
    except httpx.HTTPError as exc:
        raise _FetchError(str(exc)) from exc
    if resp.status_code != 200:
        raise _FetchError(f"HTTP {resp.status_code}")
    return resp.text


def _extract_text(html: str) -> str:
    if not has_enough_content(html):
        raise _NoContentError("page had little to no extractable text")
    return _extract_text_raw(html)
```
(`_insert_error_job` and `_notice_line`, which follow immediately after, stay as-is.)

Finally update the four call sites inside `job_add_by_url`'s `stream()` function:
```python
                try:
                    html = _fetch_url_html(url)
                except _FetchError as exc:
```
becomes:
```python
                try:
                    html = fetch_url_html(url)
                except FetchError as exc:
```
and:
```python
                    try:
                        raw_text = _extract_text(html)
                    except _NoContentError:
```
becomes:
```python
                    try:
                        raw_text = extract_text_or_raise(html)
                    except NoContentError:
```

`httpx` (line 4: `import httpx`) is no longer used anywhere else in `jobs.py` after this change (it was only used by the local `_fetch_url_html` definition just removed) — delete that import line too.

- [ ] **Step 6: Run the full jobs route test suite to confirm no regression**

Run: `uv run pytest tests/test_routes_jobs.py -v`
Expected: PASS (all existing tests — this was a pure rename/relocation, `respx` mocks the transport layer so they don't care which module owns the function)

- [ ] **Step 7: Run the full test suite**

Run: `uv run pytest`
Expected: PASS, 0 failures

- [ ] **Step 8: Commit**

```bash
git add app/fetchers/content.py app/routes/jobs.py tests/test_fetcher_content.py
git commit -m "refactor: move URL-fetch/content helpers from jobs.py into app.fetchers.content"
```

---

## Task 3: Remove the old `POST /sources` add-with-dropdown path

**Files:**
- Modify: `app/routes/sources.py:1-53`
- Modify: `tests/test_routes_sources.py`

**Interfaces:**
- Produces: `_visible_sources(conn: sqlite3.Connection) -> list[dict]`, `_sources_context(conn: sqlite3.Connection, config) -> dict` (returns `{"sources": ..., "needs_login_by_id": ...}`) — consumed by Task 6's `/sources/detect/confirm`.

This task only removes the old dropdown-based create path and introduces the two helpers; the new add flow (Tasks 5-6) and UI (index.html rewrite) come after, so between this task and Task 6 the Sources page temporarily has no way to add a source from the UI — that's fine, each task's own test suite stays green throughout, and the page is still fully functional for viewing/editing/fetching existing sources.

- [ ] **Step 1: Delete the obsolete tests that exercise `POST /sources`**

In `tests/test_routes_sources.py`, delete these four tests entirely (they test a route this task removes):
- `test_create_source` (posts to `/sources` with a `fetcher_type` dropdown value)
- `test_create_slack_source_shows_login_prompt_when_needed`
- `test_create_slack_source_no_login_prompt_when_already_logged_in`
- `test_create_http_source_does_not_attempt_login_check`

Also delete `test_sources_page_add_source_form_includes_generic_listing_option` — it asserts the *add* form's dropdown has a `generic_listing` option, but the add form is losing its dropdown entirely in this plan (Task 7 fixes the real bug: the same missing option on the *edit* form).

- [ ] **Step 2: Run the suite to confirm those tests are gone and nothing else references the removed route**

Run: `uv run pytest tests/test_routes_sources.py -v`
Expected: PASS (remaining tests unaffected — `create_source` hasn't been touched in `sources.py` yet, this step just confirms the deletions were clean)

- [ ] **Step 3: Write a test confirming `POST /sources` no longer exists**

Add to `tests/test_routes_sources.py`:
```python
def test_post_sources_route_removed(client, conn):
    resp = client.post(
        "/sources",
        data={"name": "finn.no", "url": "https://finn.no", "fetcher_type": "http"},
    )
    assert resp.status_code == 405
```

- [ ] **Step 4: Run it to verify it fails**

Run: `uv run pytest tests/test_routes_sources.py::test_post_sources_route_removed -v`
Expected: FAIL (route still exists, returns 200)

- [ ] **Step 5: Remove `create_source` and add the `_visible_sources`/`_sources_context` helpers**

In `app/routes/sources.py`, the current top of the file (lines 1-52) is:
```python
from __future__ import annotations
import sqlite3
from typing import Optional
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from app.deps import get_db, get_config
from app.db import queries as q
from app.fetchers.slack import SlackFetcher
from app.pipeline import _make_fetcher
from app.template_env import templates

router = APIRouter()


def _check_needs_login(source: dict, config, conn: sqlite3.Connection) -> bool | None:
    if source["fetcher_type"] != "slack":
        return None
    try:
        fetcher = _make_fetcher(source, config.browser_profile_dir, conn)
        return fetcher.check_needs_login()
    except Exception:
        return None


@router.get("/sources", response_class=HTMLResponse)
def sources_page(request: Request, conn: sqlite3.Connection = Depends(get_db), config=Depends(get_config)):
    sources = [s for s in q.get_sources(conn) if s["fetcher_type"] != "manual"]
    needs_login_by_id = {
        s["id"]: _check_needs_login(s, config, conn) for s in sources if s["fetcher_type"] == "slack"
    }
    return templates.TemplateResponse(
        request, "sources/index.html", {"sources": sources, "needs_login_by_id": needs_login_by_id}
    )


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
        {"sources": q.get_sources(conn), "needs_login_by_id": {source_id: needs_login}},
    )
```

Replace it with:
```python
from __future__ import annotations
import sqlite3
from typing import Optional
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from app.deps import get_db, get_config
from app.db import queries as q
from app.fetchers.slack import SlackFetcher
from app.pipeline import _make_fetcher
from app.template_env import templates

router = APIRouter()


def _check_needs_login(source: dict, config, conn: sqlite3.Connection) -> bool | None:
    if source["fetcher_type"] != "slack":
        return None
    try:
        fetcher = _make_fetcher(source, config.browser_profile_dir, conn)
        return fetcher.check_needs_login()
    except Exception:
        return None


def _visible_sources(conn: sqlite3.Connection) -> list[dict]:
    return [s for s in q.get_sources(conn) if s["fetcher_type"] != "manual"]


def _sources_context(conn: sqlite3.Connection, config) -> dict:
    sources = _visible_sources(conn)
    needs_login_by_id = {
        s["id"]: _check_needs_login(s, config, conn) for s in sources if s["fetcher_type"] == "slack"
    }
    return {"sources": sources, "needs_login_by_id": needs_login_by_id}


@router.get("/sources", response_class=HTMLResponse)
def sources_page(request: Request, conn: sqlite3.Connection = Depends(get_db), config=Depends(get_config)):
    return templates.TemplateResponse(request, "sources/index.html", _sources_context(conn, config))
```
(everything below — `_get_source_or_404` and the `/sources/{source_id}/...` routes — is untouched by this step.)

- [ ] **Step 6: Run the full sources test suite**

Run: `uv run pytest tests/test_routes_sources.py -v`
Expected: PASS (all remaining tests, including the new `test_post_sources_route_removed`)

- [ ] **Step 7: Run the full test suite**

Run: `uv run pytest`
Expected: PASS, 0 failures

- [ ] **Step 8: Commit**

```bash
git add app/routes/sources.py tests/test_routes_sources.py
git commit -m "refactor: remove POST /sources dropdown-based add path, extract _visible_sources/_sources_context"
```

---

## Task 4: Split `sources/index.html` — extract `_table.html`

**Files:**
- Create: `app/templates/sources/_table.html`
- Modify: `app/templates/sources/index.html`
- Modify: `tests/test_routes_sources.py`

**Interfaces:**
- Produces: `sources/_table.html`, a standalone template expecting `sources` and `needs_login_by_id` in its render context, rooted in `<div id="sources-table">` — consumed directly (via Jinja `{% include %}`) by `index.html` in this task, and rendered standalone (via `templates.get_template(...).render(...)`) by Task 6's `/sources/detect/confirm`.

- [ ] **Step 1: Write a test asserting the table lives in an `id="sources-table"` wrapper**

Add to `tests/test_routes_sources.py`:
```python
def test_sources_page_table_has_wrapper_id(client, conn):
    _seed(conn)
    resp = client.get("/sources")
    assert resp.status_code == 200
    assert 'id="sources-table"' in resp.text
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_routes_sources.py::test_sources_page_table_has_wrapper_id -v`
Expected: FAIL (no such wrapper exists yet)

- [ ] **Step 3: Create `app/templates/sources/_table.html`**

The current `app/templates/sources/index.html` is:
```html
{% extends "base.html" %}
{% block title %}Sources — Job Seek{% endblock %}
{% block content %}
<h1>Sources</h1>
{% if sources %}
  <table style="width:100%; table-layout:fixed; border-collapse:collapse;">
    <colgroup>
      <col style="width:48%">
      <col style="width:14%">
      <col style="width:20%">
      <col>
    </colgroup>
    <thead>
      <tr>
        <th style="text-align:left; padding:0.5rem; border-bottom:2px solid #dee2e6;">Source</th>
        <th style="text-align:left; padding:0.5rem; border-bottom:2px solid #dee2e6;">Type</th>
        <th style="text-align:left; padding:0.5rem; border-bottom:2px solid #dee2e6;">Status</th>
        <th style="padding:0.5rem; border-bottom:2px solid #dee2e6;"></th>
      </tr>
    </thead>
    {% for source in sources %}
      {% set needs_login = needs_login_by_id.get(source.id) if needs_login_by_id is defined else None %}
      {% include "sources/_row.html" %}
    {% endfor %}
  </table>
{% else %}
  <p>No sources yet. Add one below.</p>
{% endif %}

<form method="post" action="/sources" style="display:flex; gap:0.5rem; flex-wrap:wrap; margin-top:1.5rem;">
  <input type="text" name="name" placeholder="Name" required>
  <input type="text" name="url" placeholder="URL" required style="flex:1; min-width:200px;">
  <select name="fetcher_type">
    <option value="http">http</option>
    <option value="playwright">playwright</option>
    <option value="slack">slack</option>
    <option value="finn_listing">finn_listing</option>
    <option value="generic_listing">generic_listing</option>
  </select>
  <button type="submit" class="btn">Add source</button>
</form>
{% endblock %}
```

Create `app/templates/sources/_table.html` with the table portion extracted and wrapped:
```html
<div id="sources-table">
{% if sources %}
  <table style="width:100%; table-layout:fixed; border-collapse:collapse;">
    <colgroup>
      <col style="width:48%">
      <col style="width:14%">
      <col style="width:20%">
      <col>
    </colgroup>
    <thead>
      <tr>
        <th style="text-align:left; padding:0.5rem; border-bottom:2px solid #dee2e6;">Source</th>
        <th style="text-align:left; padding:0.5rem; border-bottom:2px solid #dee2e6;">Type</th>
        <th style="text-align:left; padding:0.5rem; border-bottom:2px solid #dee2e6;">Status</th>
        <th style="padding:0.5rem; border-bottom:2px solid #dee2e6;"></th>
      </tr>
    </thead>
    {% for source in sources %}
      {% set needs_login = needs_login_by_id.get(source.id) if needs_login_by_id is defined else None %}
      {% include "sources/_row.html" %}
    {% endfor %}
  </table>
{% else %}
  <p>No sources yet. Add one below.</p>
{% endif %}
</div>
```

- [ ] **Step 4: Update `index.html` to include it (leave the old add-form in place for now — Task 6 replaces it)**

Replace `index.html`'s content block with:
```html
{% extends "base.html" %}
{% block title %}Sources — Job Seek{% endblock %}
{% block content %}
<h1>Sources</h1>
{% include "sources/_table.html" %}

<form method="post" action="/sources" style="display:flex; gap:0.5rem; flex-wrap:wrap; margin-top:1.5rem;">
  <input type="text" name="name" placeholder="Name" required>
  <input type="text" name="url" placeholder="URL" required style="flex:1; min-width:200px;">
  <select name="fetcher_type">
    <option value="http">http</option>
    <option value="playwright">playwright</option>
    <option value="slack">slack</option>
    <option value="finn_listing">finn_listing</option>
    <option value="generic_listing">generic_listing</option>
  </select>
  <button type="submit" class="btn">Add source</button>
</form>
{% endblock %}
```
(This stray `<form action="/sources">` now points at a removed route — it's inert HTML at this point since Task 3 already removed `POST /sources`. Task 6 replaces this whole block with the `<details>` unfold. Leaving it here for one task keeps this task's diff focused on the extraction alone.)

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest tests/test_routes_sources.py::test_sources_page_table_has_wrapper_id -v`
Expected: PASS

- [ ] **Step 6: Run the full test suite**

Run: `uv run pytest`
Expected: PASS, 0 failures

- [ ] **Step 7: Commit**

```bash
git add app/templates/sources/_table.html app/templates/sources/index.html tests/test_routes_sources.py
git commit -m "refactor: extract sources/_table.html partial from index.html"
```

---

## Task 5: `POST /sources/detect` — the detection endpoint

**Files:**
- Modify: `app/routes/sources.py`
- Create: `app/templates/sources/_detect_confirm.html`
- Create: `app/templates/sources/_detect_mismatch.html`
- Modify: `tests/test_routes_sources.py`

**Interfaces:**
- Consumes: `classify_known_source` (Task 1), `fetch_url_html`/`FetchError`/`has_enough_content`/`extract_links`/`render_html`/`detect_listing` (Task 2 + existing `app.fetchers.links.extract_links`, `app.fetchers.playwright_pool.render_html`, `app.ai.detect_listing.detect_listing`).
- Produces: `POST /sources/detect` — streams progress lines, ends with one `HTML:` chunk containing either `_detect_confirm.html` (has a `fetcher_type` value) or `_detect_mismatch.html`. Consumed by Task 6's UI wiring (the "Add" button in `_add_form.html` targets this route).

- [ ] **Step 1: Write the failing route tests**

Add to `tests/test_routes_sources.py` (add these imports at the top alongside the existing ones):
```python
import httpx
import respx
```
Then add:
```python
def test_detect_source_fast_path_slack_skips_fetch(client, conn):
    with patch("app.routes.sources.fetch_url_html") as mock_fetch:
        resp = client.post("/sources/detect", data={"url": _SLACK_URL})
    mock_fetch.assert_not_called()
    assert resp.status_code == 200
    assert "Detected type: <strong>slack</strong>" in resp.text
    assert f'value="{_SLACK_URL}"' in resp.text


def test_detect_source_fast_path_finn_skips_fetch(client, conn):
    with patch("app.routes.sources.fetch_url_html") as mock_fetch:
        resp = client.post("/sources/detect", data={"url": "https://www.finn.no/job/search"})
    mock_fetch.assert_not_called()
    assert resp.status_code == 200
    assert "Detected type: <strong>finn_listing</strong>" in resp.text


_IS_A_LISTING = {
    "is_listing": True,
    "job_links": ["https://careers.example.com/jobs/1", "https://careers.example.com/jobs/2"],
}
_NOT_A_LISTING = {"is_listing": False, "job_links": []}


@respx.mock
def test_detect_source_slow_path_listing_detected(client, conn):
    respx.get("https://careers.example.com/jobs").mock(
        return_value=httpx.Response(200, text="<html><body><a href='/jobs/1'>A</a><a href='/jobs/2'>B</a></body></html>")
    )
    with patch("app.routes.sources.detect_listing", return_value=_IS_A_LISTING):
        resp = client.post("/sources/detect", data={"url": "https://careers.example.com/jobs"})
    assert resp.status_code == 200
    assert "Detected type: <strong>generic_listing</strong>" in resp.text
    assert 'value="careers.example.com"' in resp.text  # default name


@respx.mock
def test_detect_source_slow_path_not_a_listing_shows_mismatch(client, conn):
    respx.get("https://example.com/job/1").mock(
        return_value=httpx.Response(200, text="<html><body><p>A single job posting.</p></body></html>")
    )
    with patch("app.routes.sources.detect_listing", return_value=_NOT_A_LISTING):
        resp = client.post("/sources/detect", data={"url": "https://example.com/job/1"})
    assert resp.status_code == 200
    assert "looks like a single job posting" in resp.text
    assert 'data-progress-url="/jobs/add-by-url"' in resp.text
    assert "Add as source anyway" in resp.text


@respx.mock
def test_detect_source_fetch_failure_falls_back_to_generic_listing(client, conn):
    respx.get("https://example.com/unreachable").mock(side_effect=httpx.ConnectError("boom"))
    resp = client.post("/sources/detect", data={"url": "https://example.com/unreachable"})
    assert resp.status_code == 200
    assert "Detected type: <strong>generic_listing</strong>" in resp.text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_routes_sources.py -k detect_source -v`
Expected: FAIL with 404s (route doesn't exist yet)

- [ ] **Step 3: Create the two panel templates**

Create `app/templates/sources/_detect_confirm.html`:
```html
<p style="margin:0 0 0.5rem;">Detected type: <strong>{{ fetcher_type }}</strong></p>
<div style="display:flex; gap:0.5rem; align-items:center; flex-wrap:wrap;">
  <input type="hidden" id="detect-url" value="{{ url }}">
  <input type="hidden" id="detect-fetcher-type" value="{{ fetcher_type }}">
  <input type="text" id="detect-name" value="{{ name }}" class="text-input" style="flex:1; min-width:160px;">
  <button type="button" class="btn"
    data-progress-url="/sources/detect/confirm"
    data-progress-body-url="#detect-url"
    data-progress-body-name="#detect-name"
    data-progress-body-fetcher_type="#detect-fetcher-type"
    data-progress-oob
    data-progress-display="#detect-confirm-progress">Add source</button>
</div>
<span id="detect-confirm-progress" class="reset-progress" aria-live="polite"></span>
```

Create `app/templates/sources/_detect_mismatch.html`:
```html
<p style="margin:0 0 0.5rem; color:#dc3545;">This looks like a single job posting, not a listing page.</p>
<input type="hidden" id="detect-url" value="{{ url }}">
<input type="hidden" id="detect-name" value="{{ name }}">
<input type="hidden" id="detect-fetcher-type" value="generic_listing">
<div style="display:flex; gap:0.5rem; flex-wrap:wrap;">
  <button type="button" class="btn"
    data-progress-url="/jobs/add-by-url"
    data-progress-body-url="#detect-url"
    data-progress-display="#detect-mismatch-progress">Add as job instead</button>
  <button type="button" class="btn"
    data-progress-url="/sources/detect/confirm"
    data-progress-body-url="#detect-url"
    data-progress-body-name="#detect-name"
    data-progress-body-fetcher_type="#detect-fetcher-type"
    data-progress-oob
    data-progress-display="#detect-mismatch-progress">Add as source anyway</button>
</div>
<span id="detect-mismatch-progress" class="reset-progress" aria-live="polite"></span>
```

- [ ] **Step 4: Implement the route**

In `app/routes/sources.py`, update the import block at the top to add the new dependencies:
```python
from __future__ import annotations
import sqlite3
from typing import Optional
from urllib.parse import urlsplit
import openai
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from app.deps import get_db, get_config, get_ai_client, get_model
from app.db import queries as q
from app.fetchers.slack import SlackFetcher
from app.fetchers.links import extract_links
from app.fetchers.content import has_enough_content, fetch_url_html, FetchError
from app.fetchers.playwright_pool import render_html
from app.ai.classify_known_source import classify_known_source
from app.ai.detect_listing import detect_listing
from app.pipeline import _make_fetcher
from app.template_env import templates

router = APIRouter()
```
(this replaces the current shorter import block — `Optional` and `HTTPException` were already imported and stay in use further down in the file; `HTMLResponse` stays for the existing routes).

Then add the new route after `sources_page` (before `_get_source_or_404`):
```python
@router.post("/sources/detect")
def detect_source(
    request: Request,
    url: str = Form(...),
    client: openai.OpenAI = Depends(get_ai_client),
    model: str = Depends(get_model),
):
    def stream():
        default_name = urlsplit(url).netloc
        fetcher_type = classify_known_source(url)
        if fetcher_type is None:
            yield "Checking the page...\n"
            try:
                html = fetch_url_html(url)
            except FetchError:
                fetcher_type = "generic_listing"
            else:
                if not has_enough_content(html):
                    rendered = render_html(url)
                    if rendered and has_enough_content(rendered):
                        html = rendered
                links = extract_links(html, url)
                detection = detect_listing(client, model, links, url)
                if detection["is_listing"] and len(detection["job_links"]) >= 2:
                    fetcher_type = "generic_listing"

        if fetcher_type is not None:
            panel = templates.get_template("sources/_detect_confirm.html").render(
                request=request, url=url, name=default_name, fetcher_type=fetcher_type,
            )
        else:
            panel = templates.get_template("sources/_detect_mismatch.html").render(
                request=request, url=url, name=default_name,
            )
        yield "HTML:" + panel.replace("\n", "") + "\n"

    return StreamingResponse(stream(), media_type="text/plain")
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_routes_sources.py -k detect_source -v`
Expected: PASS (5 tests)

- [ ] **Step 6: Run the full test suite**

Run: `uv run pytest`
Expected: PASS, 0 failures

- [ ] **Step 7: Commit**

```bash
git add app/routes/sources.py app/templates/sources/_detect_confirm.html app/templates/sources/_detect_mismatch.html tests/test_routes_sources.py
git commit -m "feat: add POST /sources/detect fetcher_type detection endpoint"
```

---

## Task 6: `POST /sources/detect/confirm` + unfold UI wiring

**Files:**
- Modify: `app/routes/sources.py`
- Create: `app/templates/sources/_add_form.html`
- Modify: `app/templates/sources/index.html`
- Modify: `tests/test_routes_sources.py`

**Interfaces:**
- Consumes: `_visible_sources`, `_sources_context` (Task 3), `DETECTABLE_FETCHER_TYPES` (Task 1), `run_fetch` (existing, `app.pipeline`).
- Produces: `POST /sources/detect/confirm` — creates the source, runs its first fetch, returns two OOB `HTML:` chunks (`id="sources-table"`, `id="add-source-panel"`).

- [ ] **Step 1: Write the failing route tests**

Add to `tests/test_routes_sources.py`:
```python
def _fake_run_fetch(source, conn, client, model, profile_dir):
    yield f"Starting fetch for '{source['name']}'"
    yield "Fetch complete"


def test_confirm_source_creates_source_and_streams_fetch(client, conn):
    with patch("app.routes.sources.run_fetch", side_effect=_fake_run_fetch):
        resp = client.post(
            "/sources/detect/confirm",
            data={"url": "https://careers.example.com/jobs", "name": "careers.example.com", "fetcher_type": "generic_listing"},
        )
    assert resp.status_code == 200
    assert "Fetch complete" in resp.text
    sources = q.get_sources(conn)
    assert len(sources) == 1
    assert sources[0]["fetcher_type"] == "generic_listing"
    assert sources[0]["name"] == "careers.example.com"


def test_confirm_source_response_has_both_oob_chunks(client, conn):
    with patch("app.routes.sources.run_fetch", side_effect=_fake_run_fetch):
        resp = client.post(
            "/sources/detect/confirm",
            data={"url": "https://careers.example.com/jobs", "name": "careers.example.com", "fetcher_type": "generic_listing"},
        )
    assert resp.status_code == 200
    assert 'HTML:<div id="sources-table">' in resp.text
    assert 'HTML:<div id="add-source-panel">' in resp.text


def test_confirm_source_rejects_invalid_fetcher_type(client, conn):
    resp = client.post(
        "/sources/detect/confirm",
        data={"url": "https://example.com", "name": "example.com", "fetcher_type": "http"},
    )
    assert resp.status_code == 400
    assert q.get_sources(conn) == []


def test_confirm_source_slack_shows_needs_login(client, conn):
    with patch("app.routes.sources.run_fetch", side_effect=_fake_run_fetch), \
         patch.object(SlackFetcher, "check_needs_login", return_value=True):
        resp = client.post(
            "/sources/detect/confirm",
            data={"url": _SLACK_URL, "name": "Example Slack", "fetcher_type": "slack"},
        )
    assert resp.status_code == 200
    assert "Needs Slack login" in resp.text


def test_sources_page_has_add_source_unfold(client, conn):
    resp = client.get("/sources")
    assert resp.status_code == 200
    assert "+ Add source" in resp.text
    assert 'data-progress-url="/sources/detect"' in resp.text
    assert 'id="add-source-panel"' in resp.text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_routes_sources.py -k confirm_source -v`
Expected: FAIL with 404s (route doesn't exist yet)

- [ ] **Step 3: Create `app/templates/sources/_add_form.html`**

```html
<div id="add-source-panel" style="display:flex; gap:0.5rem; align-items:center; flex-wrap:wrap; margin-top:0.75rem;">
  <input type="url" id="add-source-url" name="url" placeholder="Paste a listing page URL…" required class="text-input" style="flex:1; min-width:200px;">
  <button type="button" class="btn"
    data-progress-url="/sources/detect"
    data-progress-body-url="#add-source-url"
    data-progress-target="#add-source-panel"
    data-progress-display="#add-source-progress">Add</button>
  <span id="add-source-progress" class="reset-progress" aria-live="polite"></span>
</div>
```

- [ ] **Step 4: Implement the route**

In `app/routes/sources.py`, add the `run_fetch` import — change:
```python
from app.pipeline import _make_fetcher
```
to:
```python
from app.pipeline import _make_fetcher, run_fetch
```
and add `DETECTABLE_FETCHER_TYPES` to the existing `classify_known_source` import line:
```python
from app.ai.classify_known_source import classify_known_source, DETECTABLE_FETCHER_TYPES
```

Then add the new route directly after `detect_source` (from Task 5):
```python
@router.post("/sources/detect/confirm")
def confirm_source(
    request: Request,
    url: str = Form(...),
    name: str = Form(...),
    fetcher_type: str = Form(...),
    conn: sqlite3.Connection = Depends(get_db),
    client: openai.OpenAI = Depends(get_ai_client),
    model: str = Depends(get_model),
    config=Depends(get_config),
):
    if fetcher_type not in DETECTABLE_FETCHER_TYPES:
        raise HTTPException(status_code=400, detail="Invalid fetcher_type")

    def stream():
        source_id = q.insert_source(conn, name, url, fetcher_type)
        source = q.get_source(conn, source_id)
        gen = run_fetch(source, conn, client, model, config.browser_profile_dir)
        try:
            while True:
                yield next(gen) + "\n"
        except StopIteration:
            pass

        table = templates.get_template("sources/_table.html").render(
            request=request, **_sources_context(conn, config)
        )
        yield "HTML:" + table.replace("\n", "") + "\n"
        add_form = templates.get_template("sources/_add_form.html").render(request=request)
        yield "HTML:" + add_form.replace("\n", "") + "\n"

    return StreamingResponse(stream(), media_type="text/plain")
```

- [ ] **Step 5: Wire the `<details>` unfold into `index.html`**

Replace `index.html`'s content block (currently ending in the stray `<form action="/sources">` left over from Task 4) with:
```html
{% extends "base.html" %}
{% block title %}Sources — Job Seek{% endblock %}
{% block content %}
<h1>Sources</h1>
{% include "sources/_table.html" %}

<details id="add-source-toggle" style="margin-top:1.5rem;">
  <summary class="btn" style="cursor:pointer; display:inline-block;">+ Add source</summary>
  {% include "sources/_add_form.html" %}
</details>
{% endblock %}
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_routes_sources.py -k "confirm_source or add_source_unfold" -v`
Expected: PASS (6 tests)

- [ ] **Step 7: Run the full test suite**

Run: `uv run pytest`
Expected: PASS, 0 failures

- [ ] **Step 8: Commit**

```bash
git add app/routes/sources.py app/templates/sources/_add_form.html app/templates/sources/index.html tests/test_routes_sources.py
git commit -m "feat: add POST /sources/detect/confirm and unfold add-source UI"
```

---

## Task 7: Fix the missing `generic_listing` option in the edit-row dropdown

**Files:**
- Modify: `app/templates/sources/_row_edit.html`
- Modify: `tests/test_routes_sources.py`

**Interfaces:** none (template-only fix).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_routes_sources.py`:
```python
def test_edit_form_includes_generic_listing_option(client, conn):
    sid = q.insert_source(conn, "mlai.work/norway", "https://mlai.work/l/norway", "generic_listing")
    resp = client.get(f"/sources/{sid}/edit")
    assert resp.status_code == 200
    assert '<option value="generic_listing" selected>generic_listing</option>' in resp.text
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_routes_sources.py::test_edit_form_includes_generic_listing_option -v`
Expected: FAIL (no such option in the dropdown)

- [ ] **Step 3: Add the missing option**

In `app/templates/sources/_row_edit.html`, the `<select>` currently reads:
```html
        <select name="fetcher_type">
          <option value="http" {% if source.fetcher_type == "http" %}selected{% endif %}>http</option>
          <option value="playwright" {% if source.fetcher_type == "playwright" %}selected{% endif %}>playwright</option>
          <option value="slack" {% if source.fetcher_type == "slack" %}selected{% endif %}>slack</option>
          <option value="finn_listing" {% if source.fetcher_type == "finn_listing" %}selected{% endif %}>finn_listing</option>
        </select>
```
Add a `generic_listing` option:
```html
        <select name="fetcher_type">
          <option value="http" {% if source.fetcher_type == "http" %}selected{% endif %}>http</option>
          <option value="playwright" {% if source.fetcher_type == "playwright" %}selected{% endif %}>playwright</option>
          <option value="slack" {% if source.fetcher_type == "slack" %}selected{% endif %}>slack</option>
          <option value="finn_listing" {% if source.fetcher_type == "finn_listing" %}selected{% endif %}>finn_listing</option>
          <option value="generic_listing" {% if source.fetcher_type == "generic_listing" %}selected{% endif %}>generic_listing</option>
        </select>
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_routes_sources.py::test_edit_form_includes_generic_listing_option -v`
Expected: PASS

- [ ] **Step 5: Run the full test suite**

Run: `uv run pytest`
Expected: PASS, 0 failures

- [ ] **Step 6: Commit**

```bash
git add app/templates/sources/_row_edit.html tests/test_routes_sources.py
git commit -m "fix: add missing generic_listing option to source edit-row dropdown"
```

---

## Task 8: Stop hardcoding `generic_listing` in the Jobs page's "keep as source" flow

**Files:**
- Modify: `app/routes/jobs.py`
- Modify: `app/templates/jobs/_listing_confirm.html`
- Modify: `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: `classify_known_source`, `DETECTABLE_FETCHER_TYPES` (Task 1).

- [ ] **Step 1: Update the existing tests that will break from the new required `fetcher_type` field**

In `tests/test_routes_jobs.py`, `test_add_listing_source_creates_source_and_streams_fetch` currently posts without `fetcher_type`:
```python
def test_add_listing_source_creates_source_and_streams_fetch(client, conn):
    with patch("app.routes.jobs.run_fetch", side_effect=_fake_run_fetch):
        resp = client.post(
            "/jobs/add-listing-source",
            data={"url": "https://careers.example.com/jobs", "name": "careers.example.com"},
        )
```
Update the `data` dict in both `test_add_listing_source_creates_source_and_streams_fetch` and `test_add_listing_source_stream_ends_with_html_chunk` to include the new required field:
```python
            data={"url": "https://careers.example.com/jobs", "name": "careers.example.com", "fetcher_type": "generic_listing"},
```

- [ ] **Step 2: Write the new failing tests**

Add to `tests/test_routes_jobs.py`:
```python
def test_add_listing_source_passes_through_finn_listing_type(client, conn):
    with patch("app.routes.jobs.run_fetch", side_effect=_fake_run_fetch):
        resp = client.post(
            "/jobs/add-listing-source",
            data={"url": "https://www.finn.no/job/search", "name": "Finn AI", "fetcher_type": "finn_listing"},
        )
    assert resp.status_code == 200
    sources = q.get_sources(conn)
    assert len(sources) == 1
    assert sources[0]["fetcher_type"] == "finn_listing"


def test_add_listing_source_rejects_invalid_fetcher_type(client, conn):
    resp = client.post(
        "/jobs/add-listing-source",
        data={"url": "https://example.com", "name": "example.com", "fetcher_type": "http"},
    )
    assert resp.status_code == 400
    assert q.get_sources(conn) == []


@respx.mock
def test_add_job_by_url_listing_confirm_panel_carries_detected_fetcher_type(client, conn):
    respx.get("https://careers.example.com/jobs").mock(
        return_value=httpx.Response(200, text="<html><body><a href='/jobs/1'>A</a><a href='/jobs/2'>B</a></body></html>")
    )
    with patch("app.routes.jobs.detect_listing", return_value=_IS_A_LISTING):
        resp = client.post("/jobs/add-by-url", data={"url": "https://careers.example.com/jobs"})
    assert resp.status_code == 200
    assert 'id="listing-fetcher-type" value="generic_listing"' in resp.text


@respx.mock
def test_add_job_by_url_listing_confirm_panel_detects_finn_no(client, conn):
    respx.get("https://www.finn.no/job/search").mock(
        return_value=httpx.Response(200, text="<html><body><a href='/job/1'>A</a><a href='/job/2'>B</a></body></html>")
    )
    finn_listing = {"is_listing": True, "job_links": ["https://www.finn.no/job/1", "https://www.finn.no/job/2"]}
    with patch("app.routes.jobs.detect_listing", return_value=finn_listing):
        resp = client.post("/jobs/add-by-url", data={"url": "https://www.finn.no/job/search"})
    assert resp.status_code == 200
    assert 'id="listing-fetcher-type" value="finn_listing"' in resp.text
```
(These reuse `_IS_A_LISTING` already defined in this file near `test_add_job_by_url_listing_detected_shows_confirm_panel`.)

- [ ] **Step 3: Run the new/updated tests to verify they fail**

Run: `uv run pytest tests/test_routes_jobs.py -k "add_listing_source or listing_confirm_panel" -v`
Expected: FAIL — `test_add_listing_source_creates_source_and_streams_fetch`/`_stream_ends_with_html_chunk` still pass (fetcher_type ignored server-side today), but `test_add_listing_source_passes_through_finn_listing_type`, `test_add_listing_source_rejects_invalid_fetcher_type`, and both `listing_confirm_panel` tests fail (no `fetcher_type` form field consumed yet, no hidden field in the template yet).

- [ ] **Step 4: Add the hidden field to `_listing_confirm.html`**

The current `app/templates/jobs/_listing_confirm.html` is:
```html
<div class="listing-confirm">
  <p>Detected {{ link_count }} job posting link{{ 's' if link_count != 1 else '' }} at <strong>{{ domain }}</strong>.</p>
  <div style="display:flex; gap:0.5rem; align-items:center; flex-wrap:wrap;">
    <input type="hidden" id="listing-url" value="{{ url }}">
    <input type="text" id="listing-name" value="{{ default_name }}" class="text-input" style="flex:1; min-width:160px;">
    <button type="button" class="btn"
      data-progress-url="/jobs/add-listing-source{% if filter_status is defined %}?status={{ filter_status or '' }}&content_type={{ filter_content_type or '' }}{% endif %}"
      data-progress-body-url="#listing-url"
      data-progress-body-name="#listing-name"
      data-progress-target="#jobs-content"
      data-progress-display="#listing-confirm-progress">Keep as source &amp; fetch</button>
    <a href="/jobs" class="btn">Cancel</a>
  </div>
  <span id="listing-confirm-progress" class="reset-progress" aria-live="polite"></span>
</div>
```
Add a hidden `fetcher_type` field and wire it into the button's body fields:
```html
<div class="listing-confirm">
  <p>Detected {{ link_count }} job posting link{{ 's' if link_count != 1 else '' }} at <strong>{{ domain }}</strong>.</p>
  <div style="display:flex; gap:0.5rem; align-items:center; flex-wrap:wrap;">
    <input type="hidden" id="listing-url" value="{{ url }}">
    <input type="hidden" id="listing-fetcher-type" value="{{ fetcher_type }}">
    <input type="text" id="listing-name" value="{{ default_name }}" class="text-input" style="flex:1; min-width:160px;">
    <button type="button" class="btn"
      data-progress-url="/jobs/add-listing-source{% if filter_status is defined %}?status={{ filter_status or '' }}&content_type={{ filter_content_type or '' }}{% endif %}"
      data-progress-body-url="#listing-url"
      data-progress-body-name="#listing-name"
      data-progress-body-fetcher_type="#listing-fetcher-type"
      data-progress-target="#jobs-content"
      data-progress-display="#listing-confirm-progress">Keep as source &amp; fetch</button>
    <a href="/jobs" class="btn">Cancel</a>
  </div>
  <span id="listing-confirm-progress" class="reset-progress" aria-live="polite"></span>
</div>
```

- [ ] **Step 5: Wire `classify_known_source` into `job_add_by_url` and validate `fetcher_type` in `job_add_listing_source`**

In `app/routes/jobs.py`, add the import (alongside the existing `from app.ai.detect_listing import detect_listing` line):
```python
from app.ai.classify_known_source import classify_known_source, DETECTABLE_FETCHER_TYPES
```

In `job_add_by_url`'s `stream()`, the block that builds `panel_context` currently reads:
```python
                    if detection["is_listing"] and len(detection["job_links"]) >= 2:
                        panel_context = {
                            "request": request,
                            "url": url,
                            "link_count": len(detection["job_links"]),
                            "domain": urlsplit(url).netloc,
                            "default_name": urlsplit(url).netloc,
                        }
```
Change to:
```python
                    if detection["is_listing"] and len(detection["job_links"]) >= 2:
                        panel_context = {
                            "request": request,
                            "url": url,
                            "fetcher_type": classify_known_source(url) or "generic_listing",
                            "link_count": len(detection["job_links"]),
                            "domain": urlsplit(url).netloc,
                            "default_name": urlsplit(url).netloc,
                        }
```

Then update `job_add_listing_source`'s signature and body. Currently:
```python
@router.post("/jobs/add-listing-source")
def job_add_listing_source(
    request: Request,
    url: str = Form(...),
    name: str = Form(...),
    conn: sqlite3.Connection = Depends(get_db),
    client: openai.OpenAI = Depends(get_ai_client),
    model: str = Depends(get_model),
    config=Depends(get_config),
):
    status = request.query_params.get("status") or None
    content_type = request.query_params.get("content_type") or None

    def stream():
        source_id = q.insert_source(conn, name, url, "generic_listing")
```
Change to:
```python
@router.post("/jobs/add-listing-source")
def job_add_listing_source(
    request: Request,
    url: str = Form(...),
    name: str = Form(...),
    fetcher_type: str = Form(...),
    conn: sqlite3.Connection = Depends(get_db),
    client: openai.OpenAI = Depends(get_ai_client),
    model: str = Depends(get_model),
    config=Depends(get_config),
):
    if fetcher_type not in DETECTABLE_FETCHER_TYPES:
        raise HTTPException(status_code=400, detail="Invalid fetcher_type")

    status = request.query_params.get("status") or None
    content_type = request.query_params.get("content_type") or None

    def stream():
        source_id = q.insert_source(conn, name, url, fetcher_type)
```
(`HTTPException` is already imported in `jobs.py` — no new import needed for that.)

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_routes_jobs.py -k "add_listing_source or listing_confirm_panel" -v`
Expected: PASS (6 tests)

- [ ] **Step 7: Run the full test suite**

Run: `uv run pytest`
Expected: PASS, 0 failures

- [ ] **Step 8: Commit**

```bash
git add app/routes/jobs.py app/templates/jobs/_listing_confirm.html tests/test_routes_jobs.py
git commit -m "feat: detect finn_listing/slack fetcher_type for Jobs page's keep-as-source flow"
```

---

## Final check

- [ ] Run the full test suite once more: `uv run pytest`
- [ ] Manually verify per the run-dev-server skill: open `/sources`, click "+ Add source", paste a finn.no URL (confirms `finn_listing` instantly), a Slack channel URL (confirms `slack` instantly), a real careers-page listing URL (confirms `generic_listing` after a live AI check and a real fetch), and a single job-posting URL (confirms the mismatch panel and its "Add as job instead" button work). Confirm the "Manual" source never appears in the table at any point, and that editing one of the two pre-existing `generic_listing` sources shows it correctly pre-selected in the dropdown.
