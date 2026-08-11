# Generic listing source Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a pasted URL turns out to be a page listing multiple job postings, let the user turn it into a proper `sources` row (`fetcher_type='generic_listing'`) that gets re-discovered and re-fetched on every future `/fetch` run — using an LLM link-classification step instead of per-site scraping selectors.

**Architecture:** A new LLM step (`app/ai/detect_listing.py`) classifies a page's extracted links as "individual job postings" or not. `POST /jobs/add-by-url` runs this after fetching and, if it looks like a listing, streams back a confirmation panel instead of processing a single job. Confirming (`POST /jobs/add-listing-source`) creates the source and immediately triggers the existing `run_fetch` pipeline on it. A new `GenericListingFetcher` (mirroring `FinnListingFetcher`'s shape, minus Playwright and finn.no-specific selectors) handles all subsequent re-fetches.

**Tech Stack:** FastAPI, Jinja2 + HTMX, sqlite3, httpx + BeautifulSoup, openai client (existing local LLM), pytest + respx + unittest.mock.

## Global Constraints

- Plain HTTP only for both detection and re-fetching — no Playwright (matches the `add-by-url` feature's existing scope decision).
- A page only counts as a listing if `is_listing` is true **and** at least 2 job links are detected — guards against false positives from a single job page's "related jobs" sidebar.
- Link extraction is capped at 200 links per page (bounds LLM prompt size).
- `detect_listing` must fail open (`{"is_listing": False, "job_links": []}`) on any LLM-call exception, never raise into the route.
- `detect_listing`'s returned `job_links` must be filtered to hrefs that were actually present in the input link list (drop hallucinated URLs).
- Migrations follow the existing hard-downtime, rebuild-in-place pattern in `app/db/schema.py`.
- Multi-page/paginated listings, JS-rendered listing pages, and adopting a general crawler framework (Scrapy/Crawlee) are explicitly out of scope for this plan.

---

### Task 1: `extract_links` — pure link-extraction helper

**Files:**
- Create: `app/fetchers/links.py`
- Test: `tests/test_fetcher_links.py`

**Interfaces:**
- Produces: `extract_links(html: str, base_url: str) -> list[tuple[str, str]]` — list of `(absolute_href, anchor_text)` pairs, deduplicated by href, capped at 200. Task 2 (`detect_listing`) and Task 4 (`GenericListingFetcher`) both import this exact function.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_fetcher_links.py`:

```python
from app.fetchers.links import extract_links

_HTML = """
<html><body>
  <nav><a href="/about">About</a></nav>
  <article>
    <a href="/jobs/1">Senior Engineer</a>
    <a href="/jobs/2">  Staff Engineer  </a>
    <a href="https://other.example.com/jobs/3">External Posting</a>
    <a href="/jobs/1">Senior Engineer (duplicate link)</a>
  </article>
</body></html>
"""


def test_extract_links_resolves_relative_hrefs_absolute():
    links = extract_links(_HTML, "https://example.com/careers")
    hrefs = [href for href, _ in links]
    assert "https://example.com/jobs/1" in hrefs
    assert "https://example.com/about" in hrefs
    assert "https://other.example.com/jobs/3" in hrefs


def test_extract_links_dedupes_by_href():
    links = extract_links(_HTML, "https://example.com/careers")
    hrefs = [href for href, _ in links]
    assert hrefs.count("https://example.com/jobs/1") == 1


def test_extract_links_strips_anchor_text_whitespace():
    links = extract_links(_HTML, "https://example.com/careers")
    by_href = dict(links)
    assert by_href["https://example.com/jobs/2"] == "Staff Engineer"


def test_extract_links_caps_at_200():
    html = "<html><body>" + "".join(f'<a href="/jobs/{i}">Job {i}</a>' for i in range(250)) + "</body></html>"
    links = extract_links(html, "https://example.com/careers")
    assert len(links) == 200


def test_extract_links_ignores_hrefless_anchors():
    html = '<html><body><a name="top">Top</a><a href="/jobs/1">Job</a></body></html>'
    links = extract_links(html, "https://example.com/careers")
    assert [href for href, _ in links] == ["https://example.com/jobs/1"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_fetcher_links.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.fetchers.links'`

- [ ] **Step 3: Implement `extract_links`**

Create `app/fetchers/links.py`:

```python
from __future__ import annotations
from urllib.parse import urljoin
from bs4 import BeautifulSoup

_MAX_LINKS = 200


def extract_links(html: str, base_url: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    links: list[tuple[str, str]] = []
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a["href"])
        if href in seen:
            continue
        seen.add(href)
        links.append((href, a.get_text(strip=True)))
        if len(links) >= _MAX_LINKS:
            break
    return links
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_fetcher_links.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/fetchers/links.py tests/test_fetcher_links.py
git commit -m "feat: add extract_links helper for listing-page link extraction"
```

---

### Task 2: `detect_listing` — LLM listing-classification step

**Files:**
- Create: `app/ai/detect_listing.py`
- Test: `tests/test_detect_listing.py`

**Interfaces:**
- Consumes: nothing new (takes a plain `list[tuple[str, str]]` — Task 1's `extract_links` output shape, but doesn't import Task 1's module).
- Produces: `detect_listing(client: openai.OpenAI, model: str, links: list[tuple[str, str]], page_url: str) -> dict` returning `{"is_listing": bool, "job_links": list[str]}`. Task 3 (route) and Task 4 (`GenericListingFetcher`) both import and call this exact function/signature.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_detect_listing.py` (model the mock-client style on `tests/test_pipeline.py`'s `_mock_client` helper):

```python
import json
from unittest.mock import MagicMock
from app.ai.detect_listing import detect_listing


def _client_with_response(content: str) -> MagicMock:
    client = MagicMock()
    choice = MagicMock()
    choice.message.content = content
    client.chat.completions.create.return_value = MagicMock(choices=[choice])
    return client


def test_detect_listing_returns_is_listing_and_job_links():
    links = [
        ("https://example.com/jobs/1", "Senior Engineer"),
        ("https://example.com/jobs/2", "Staff Engineer"),
        ("https://example.com/about", "About us"),
    ]
    client = _client_with_response(json.dumps({
        "is_listing": True,
        "job_links": ["https://example.com/jobs/1", "https://example.com/jobs/2"],
    }))
    result = detect_listing(client, "llama3.2", links, "https://example.com/careers")
    assert result == {
        "is_listing": True,
        "job_links": ["https://example.com/jobs/1", "https://example.com/jobs/2"],
    }


def test_detect_listing_filters_hallucinated_links():
    links = [("https://example.com/jobs/1", "Senior Engineer")]
    client = _client_with_response(json.dumps({
        "is_listing": True,
        "job_links": ["https://example.com/jobs/1", "https://example.com/jobs/999"],
    }))
    result = detect_listing(client, "llama3.2", links, "https://example.com/careers")
    assert result == {"is_listing": True, "job_links": ["https://example.com/jobs/1"]}


def test_detect_listing_not_a_listing():
    links = [("https://example.com/apply", "Apply now")]
    client = _client_with_response(json.dumps({"is_listing": False, "job_links": []}))
    result = detect_listing(client, "llama3.2", links, "https://example.com/jobs/1")
    assert result == {"is_listing": False, "job_links": []}


def test_detect_listing_fails_open_on_exception():
    client = MagicMock()
    client.chat.completions.create.side_effect = RuntimeError("connection refused")
    result = detect_listing(client, "llama3.2", [("https://example.com/x", "X")], "https://example.com")
    assert result == {"is_listing": False, "job_links": []}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_detect_listing.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.ai.detect_listing'`

- [ ] **Step 3: Implement `detect_listing`**

Create `app/ai/detect_listing.py` (modeled on `app/ai/classify.py`):

```python
from __future__ import annotations
import json
import openai
from app.ai.json_utils import extract_json

_SYSTEM = (
    "You look at a list of links extracted from a web page and decide whether the "
    "page is a listing of multiple individual job postings (e.g. a careers page or "
    "job board search results), as opposed to a single job posting, an article, or "
    "an unrelated page. Each link is given as \"<url> — <anchor text>\". "
    "If it is a listing, identify which links point to individual job postings "
    "(not navigation, filters, pagination, login, or unrelated content). "
    "Respond with exactly: "
    "{\"is_listing\": <true|false>, \"job_links\": [\"<url>\", ...]}"
)


def detect_listing(
    client: openai.OpenAI,
    model: str,
    links: list[tuple[str, str]],
    page_url: str,
) -> dict:
    valid_hrefs = {href for href, _ in links}
    link_lines = "\n".join(f"{href} — {text}" for href, text in links)
    user_message = f"Page: {page_url}\n\nLinks:\n{link_lines}"
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user_message[:8000]},
            ],
            temperature=0,
            max_tokens=2000,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        data = json.loads(extract_json(resp.choices[0].message.content))
        is_listing = bool(data.get("is_listing", False))
        job_links = [href for href in data.get("job_links", []) if href in valid_hrefs]
        return {"is_listing": is_listing, "job_links": job_links}
    except Exception:
        return {"is_listing": False, "job_links": []}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_detect_listing.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/ai/detect_listing.py tests/test_detect_listing.py
git commit -m "feat: add detect_listing LLM step for listing-page classification"
```

---

### Task 3: Widen `sources.fetcher_type` to accept `'generic_listing'`

**Files:**
- Modify: `app/db/schema.py` (the `_DDL` string's `sources` table at line 14, and the migration function/registration added right after `_migrate_sources_fetcher_type_manual`)
- Test: `tests/test_schema.py`

**Interfaces:**
- Produces: `sources.fetcher_type` CHECK constraint now includes `'generic_listing'`, for both fresh DBs and existing ones. Task 6 (`_make_fetcher`) and Task 7 (routes) depend on being able to `INSERT INTO sources (..., fetcher_type) VALUES (..., 'generic_listing')` without an `IntegrityError`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_schema.py`:

```python
def test_sources_accepts_generic_listing_fetcher_type(conn):
    init_db(conn)
    conn.execute(
        "INSERT INTO sources (name, url, fetcher_type) VALUES ('Careers Page', 'http://x', 'generic_listing')"
    )


def test_init_db_migrates_sources_table_missing_generic_listing_type(conn):
    conn.executescript(
        """
        CREATE TABLE sources (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            fetcher_type TEXT NOT NULL CHECK(fetcher_type IN ('http', 'playwright', 'slack', 'finn_listing', 'manual')),
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
        "INSERT INTO sources (name, url, fetcher_type) VALUES ('Careers Page', 'http://y', 'generic_listing')"
    )
    rows = conn.execute("SELECT name, url, fetcher_type FROM sources ORDER BY id").fetchall()
    assert [dict(r) for r in rows] == [
        {"name": "s", "url": "http://x", "fetcher_type": "http"},
        {"name": "Careers Page", "url": "http://y", "fetcher_type": "generic_listing"},
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_schema.py -k generic_listing -v`
Expected: both FAIL — `sqlite3.IntegrityError: CHECK constraint failed`.

- [ ] **Step 3: Widen the CHECK constraint in `_DDL`**

In `app/db/schema.py`, change line 14 from:

```python
    fetcher_type TEXT NOT NULL CHECK(fetcher_type IN ('http', 'playwright', 'slack', 'finn_listing', 'manual')),
```

to:

```python
    fetcher_type TEXT NOT NULL CHECK(fetcher_type IN ('http', 'playwright', 'slack', 'finn_listing', 'manual', 'generic_listing')),
```

- [ ] **Step 4: Add the migration function**

Add this function to `app/db/schema.py` directly after `_migrate_sources_fetcher_type_manual`:

```python
def _migrate_sources_fetcher_type_generic_listing(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='sources'"
    ).fetchone()
    if row is None or "'generic_listing'" in row[0]:
        return
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript(
        """
        CREATE TABLE sources_new (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            fetcher_type TEXT NOT NULL CHECK(fetcher_type IN ('http', 'playwright', 'slack', 'finn_listing', 'manual', 'generic_listing')),
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

In `app/db/schema.py`, find `init_db` and add the new call right after `_migrate_sources_fetcher_type_manual(conn)`:

```python
def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL)
    _migrate_sources_fetcher_type(conn)
    _migrate_sources_fetcher_type_manual(conn)
    _migrate_sources_fetcher_type_generic_listing(conn)
    _migrate_fetch_runs_source_fk(conn)
    ...  # (rest unchanged)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_schema.py -v`
Expected: all PASS, including every pre-existing test (confirms the migration doesn't disturb other rows).

- [ ] **Step 7: Commit**

```bash
git add app/db/schema.py tests/test_schema.py
git commit -m "feat: widen sources.fetcher_type to allow 'generic_listing'"
```

---

### Task 4: `GenericListingFetcher`

**Files:**
- Create: `app/fetchers/generic_listing.py`
- Test: `tests/test_fetcher_generic_listing.py`

**Interfaces:**
- Consumes: `extract_links` (Task 1), `detect_listing` (Task 2), `app.fetchers.base.RawJob`, `app.fetchers.http.HttpFetcher` (existing).
- Produces: `GenericListingFetcher(source: dict, client: openai.OpenAI, model: str, known_urls: frozenset[str] = frozenset())` with `.fetch() -> list[RawJob]`. Task 6 (`_make_fetcher`) constructs this exact class with these exact constructor arguments.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_fetcher_generic_listing.py`:

```python
import json
from unittest.mock import MagicMock
import httpx
import respx
from app.fetchers.generic_listing import GenericListingFetcher

_SOURCE = {"id": 1, "name": "Careers", "url": "https://example.com/careers", "fetcher_type": "generic_listing"}

_LISTING_HTML = """<html><body>
<a href="/jobs/1">Senior Engineer</a>
<a href="/jobs/2">Staff Engineer</a>
<a href="/about">About</a>
</body></html>"""


def _client_returning(is_listing: bool, job_links: list[str]) -> MagicMock:
    client = MagicMock()
    choice = MagicMock()
    choice.message.content = json.dumps({"is_listing": is_listing, "job_links": job_links})
    client.chat.completions.create.return_value = MagicMock(choices=[choice])
    return client


@respx.mock
def test_generic_listing_fetcher_returns_raw_jobs_for_detected_links():
    respx.get("https://example.com/careers").mock(return_value=httpx.Response(200, text=_LISTING_HTML))
    respx.get("https://example.com/jobs/1").mock(
        return_value=httpx.Response(200, text="<html><body>Senior Engineer role</body></html>")
    )
    respx.get("https://example.com/jobs/2").mock(
        return_value=httpx.Response(200, text="<html><body>Staff Engineer role</body></html>")
    )
    client = _client_returning(True, ["https://example.com/jobs/1", "https://example.com/jobs/2"])

    fetcher = GenericListingFetcher(_SOURCE, client, "llama3.2")
    jobs = fetcher.fetch()

    urls = {job.url for job in jobs}
    assert urls == {"https://example.com/jobs/1", "https://example.com/jobs/2"}


@respx.mock
def test_generic_listing_fetcher_skips_known_urls():
    respx.get("https://example.com/careers").mock(return_value=httpx.Response(200, text=_LISTING_HTML))
    respx.get("https://example.com/jobs/2").mock(
        return_value=httpx.Response(200, text="<html><body>Staff Engineer role</body></html>")
    )
    client = _client_returning(True, ["https://example.com/jobs/1", "https://example.com/jobs/2"])

    fetcher = GenericListingFetcher(
        _SOURCE, client, "llama3.2", known_urls=frozenset({"https://example.com/jobs/1"})
    )
    jobs = fetcher.fetch()

    assert {job.url for job in jobs} == {"https://example.com/jobs/2"}


@respx.mock
def test_generic_listing_fetcher_returns_empty_when_not_a_listing():
    respx.get("https://example.com/careers").mock(return_value=httpx.Response(200, text=_LISTING_HTML))
    client = _client_returning(False, [])

    fetcher = GenericListingFetcher(_SOURCE, client, "llama3.2")
    jobs = fetcher.fetch()

    assert jobs == []


@respx.mock
def test_generic_listing_fetcher_handles_fetch_failure():
    respx.get("https://example.com/careers").mock(side_effect=httpx.ConnectError("boom"))
    client = _client_returning(True, [])

    fetcher = GenericListingFetcher(_SOURCE, client, "llama3.2")
    jobs = fetcher.fetch()

    assert jobs == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_fetcher_generic_listing.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.fetchers.generic_listing'`

- [ ] **Step 3: Implement `GenericListingFetcher`**

Create `app/fetchers/generic_listing.py`:

```python
from __future__ import annotations
import httpx
import openai
from app.fetchers.base import Fetcher, RawJob
from app.fetchers.http import HttpFetcher
from app.fetchers.links import extract_links
from app.ai.detect_listing import detect_listing

MAX_DETAIL_FETCHES = 50  # cap on new posting detail pages fetched per run


class GenericListingFetcher:
    def __init__(
        self,
        source: dict,
        client: openai.OpenAI,
        model: str,
        known_urls: frozenset[str] = frozenset(),
    ) -> None:
        self._source = source
        self._client = client
        self._model = model
        self._known_urls = known_urls

    def fetch(self) -> list[RawJob]:
        try:
            resp = httpx.get(self._source["url"], timeout=30, follow_redirects=True)
            if resp.status_code != 200:
                return []
            html = resp.text
        except Exception:
            return []

        links = extract_links(html, self._source["url"])
        result = detect_listing(self._client, self._model, links, self._source["url"])
        job_links = [href for href in result["job_links"] if href not in self._known_urls]

        jobs: list[RawJob] = []
        for href in job_links[:MAX_DETAIL_FETCHES]:
            jobs.extend(HttpFetcher({"url": href}).fetch())
        return jobs
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_fetcher_generic_listing.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/fetchers/generic_listing.py tests/test_fetcher_generic_listing.py
git commit -m "feat: add GenericListingFetcher for generic-listing sources"
```

---

### Task 5: `_make_fetcher` dispatch for `generic_listing`

**Files:**
- Modify: `app/pipeline.py:32-40` (`_make_fetcher`) and `app/pipeline.py:102-148` (`run_fetch`, to pass `client`/`model` through)
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `GenericListingFetcher` (Task 4).
- Produces: `_make_fetcher(source: dict, profile_dir: str, conn: sqlite3.Connection, client: openai.OpenAI | None = None, model: str | None = None)` — the two new parameters are optional and default to `None`; every existing call site (`run_fetch`, and `app/routes/sources.py::_check_needs_login`) keeps working unchanged. Task 7 (routes) relies on `run_fetch` now threading its own `client`/`model` into `_make_fetcher` automatically — routes don't call `_make_fetcher` directly.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_pipeline.py` (it already imports `_make_fetcher`; add an import for the new class):

```python
from app.fetchers.generic_listing import GenericListingFetcher  # add to the existing import block


def test_make_fetcher_dispatches_generic_listing(conn):
    source = {"id": 1, "name": "Careers", "url": "http://x", "fetcher_type": "generic_listing"}
    client = MagicMock()
    fetcher = _make_fetcher(source, "browser-profile", conn, client, "llama3.2")
    assert isinstance(fetcher, GenericListingFetcher)


def test_make_fetcher_generic_listing_passes_known_urls_and_client(conn):
    sid = q.insert_source(conn, "test", "http://example.com", "http")
    q.insert_job(conn, source_id=sid, url="http://known/1", title="T", company="C", raw_text="r")
    source = {"id": 1, "name": "Careers", "url": "http://x", "fetcher_type": "generic_listing"}
    client = MagicMock()

    fetcher = _make_fetcher(source, "browser-profile", conn, client, "llama3.2")

    assert fetcher._known_urls == frozenset({"http://known/1"})
    assert fetcher._client is client
    assert fetcher._model == "llama3.2"


def test_make_fetcher_slack_still_works_without_client_or_model(conn):
    source = {
        "id": 1,
        "name": "Example Slack",
        "url": "https://example-workspace.slack.com/archives/C0EXAMPLE1",
        "fetcher_type": "slack",
    }
    fetcher = _make_fetcher(source, "browser-profile", conn)
    assert type(fetcher).__name__ == "SlackFetcher"
```

`MagicMock` is already imported at the top of `tests/test_pipeline.py` (`from unittest.mock import MagicMock, patch`) — no new import needed for it.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_pipeline.py -k generic_listing -v`
Expected: FAIL — `_make_fetcher` doesn't recognize `fetcher_type='generic_listing'` and falls through to `PlaywrightFetcher`, so `isinstance(fetcher, GenericListingFetcher)` is False.

- [ ] **Step 3: Update `_make_fetcher`**

In `app/pipeline.py`, change:

```python
def _make_fetcher(source: dict, profile_dir: str, conn: sqlite3.Connection):
    ft = source["fetcher_type"]
    if ft == "http":
        return HttpFetcher(source)
    if ft == "slack":
        return SlackFetcher(source, profile_dir, known_urls=q.get_all_job_urls(conn))
    if ft == "finn_listing":
        return FinnListingFetcher(source, known_urls=q.get_all_job_urls(conn))
    return PlaywrightFetcher(source, profile_dir)
```

to:

```python
def _make_fetcher(
    source: dict,
    profile_dir: str,
    conn: sqlite3.Connection,
    client: openai.OpenAI | None = None,
    model: str | None = None,
):
    ft = source["fetcher_type"]
    if ft == "http":
        return HttpFetcher(source)
    if ft == "slack":
        return SlackFetcher(source, profile_dir, known_urls=q.get_all_job_urls(conn))
    if ft == "finn_listing":
        return FinnListingFetcher(source, known_urls=q.get_all_job_urls(conn))
    if ft == "generic_listing":
        return GenericListingFetcher(source, client, model, known_urls=q.get_all_job_urls(conn))
    return PlaywrightFetcher(source, profile_dir)
```

Add the import at the top of `app/pipeline.py`, alongside the other fetcher imports:

```python
from app.fetchers.generic_listing import GenericListingFetcher
```

- [ ] **Step 4: Thread `client`/`model` through `run_fetch`**

In `app/pipeline.py`, find `run_fetch` and change the line:

```python
        fetcher = _make_fetcher(source, profile_dir, conn)
```

to:

```python
        fetcher = _make_fetcher(source, profile_dir, conn, client, model)
```

(`run_fetch` already receives `client`/`model` as its own parameters — this just forwards them.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_pipeline.py -v`
Expected: all PASS, including every pre-existing `_make_fetcher`/`run_fetch` test (confirms the new optional parameters don't break existing call sites).

- [ ] **Step 6: Commit**

```bash
git add app/pipeline.py tests/test_pipeline.py
git commit -m "feat: dispatch generic_listing sources to GenericListingFetcher"
```

---

### Task 6: Split `_fetch_url_html`/`_extract_text` and wire listing detection into `POST /jobs/add-by-url`

**Files:**
- Modify: `app/routes/jobs.py` (the `_fetch_url_text` helper at lines 16-31, and `job_add_by_url` at line ~364)
- Create: `app/templates/jobs/_listing_confirm.html`
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: `extract_links` (Task 1), `detect_listing` (Task 2).
- Produces: `_fetch_url_html(url: str) -> str` (replaces `_fetch_url_text`, same `_FetchError`-raising contract, now returns raw HTML instead of extracted text), `_extract_text(html: str) -> str` (new, wraps BeautifulSoup `get_text()`). `job_add_by_url` now branches into a listing-confirmation response when `detect_listing` reports a listing. Task 7's new route (`job_add_listing_source`) reads the same `url`/`name` field naming the confirm panel posts.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_routes_jobs.py`, near the existing `add_job_by_url` tests. First, the two existing success-path tests need `detect_listing` mocked to report "not a listing" (otherwise they'd hit the real, unmocked LLM client) — update them, then add the new listing-detected test:

Change:

```python
@respx.mock
def test_add_job_by_url_success_inserts_job_and_streams_progress(client, conn):
    respx.get("http://example.com/job/1").mock(
        return_value=httpx.Response(200, text="<html><body><p>We are hiring</p></body></html>")
    )
    with patch("app.routes.jobs.run_add_job", side_effect=_fake_run_add_job):
        resp = client.post("/jobs/add-by-url", data={"url": "http://example.com/job/1"})
```

to:

```python
_NOT_A_LISTING = {"is_listing": False, "job_links": []}


@respx.mock
def test_add_job_by_url_success_inserts_job_and_streams_progress(client, conn):
    respx.get("http://example.com/job/1").mock(
        return_value=httpx.Response(200, text="<html><body><p>We are hiring</p></body></html>")
    )
    with patch("app.routes.jobs.run_add_job", side_effect=_fake_run_add_job), \
         patch("app.routes.jobs.detect_listing", return_value=_NOT_A_LISTING):
        resp = client.post("/jobs/add-by-url", data={"url": "http://example.com/job/1"})
```

Similarly change `test_add_job_by_url_stream_ends_with_single_html_chunk`'s `with patch("app.routes.jobs.run_add_job", side_effect=_fake_run_add_job):` block to also patch `detect_listing` the same way:

```python
@respx.mock
def test_add_job_by_url_stream_ends_with_single_html_chunk(client, conn):
    respx.get("http://example.com/job/1").mock(
        return_value=httpx.Response(200, text="<html><body><p>We are hiring</p></body></html>")
    )
    with patch("app.routes.jobs.run_add_job", side_effect=_fake_run_add_job), \
         patch("app.routes.jobs.detect_listing", return_value=_NOT_A_LISTING):
        resp = client.post("/jobs/add-by-url", data={"url": "http://example.com/job/1"})
```

(`test_add_job_by_url_duplicate_url_does_not_insert`, `test_add_job_by_url_fetch_failure_inserts_error_job`, and `test_add_job_by_url_fetch_network_error_inserts_error_job` are unaffected — they return before the fetch succeeds, so `detect_listing` is never reached.)

Then add the new tests:

```python
_IS_A_LISTING = {
    "is_listing": True,
    "job_links": ["https://careers.example.com/jobs/1", "https://careers.example.com/jobs/2"],
}


@respx.mock
def test_add_job_by_url_listing_detected_shows_confirm_panel(client, conn):
    respx.get("https://careers.example.com/jobs").mock(
        return_value=httpx.Response(200, text="<html><body><a href='/jobs/1'>A</a><a href='/jobs/2'>B</a></body></html>")
    )
    with patch("app.routes.jobs.detect_listing", return_value=_IS_A_LISTING):
        resp = client.post("/jobs/add-by-url", data={"url": "https://careers.example.com/jobs"})

    assert resp.status_code == 200
    assert "Detected 2 job posting" in resp.text
    assert "careers.example.com" in resp.text
    assert 'data-progress-url="/jobs/add-listing-source"' in resp.text
    assert q.get_jobs(conn) == []
    assert [s for s in q.get_sources(conn) if s["fetcher_type"] == "generic_listing"] == []


@respx.mock
def test_add_job_by_url_single_job_link_does_not_trigger_listing_flow(client, conn):
    respx.get("http://example.com/job/1").mock(
        return_value=httpx.Response(200, text="<html><body><p>We are hiring</p><a href='/apply'>Apply</a></body></html>")
    )
    one_link_listing = {"is_listing": True, "job_links": ["http://example.com/job/1"]}
    with patch("app.routes.jobs.run_add_job", side_effect=_fake_run_add_job), \
         patch("app.routes.jobs.detect_listing", return_value=one_link_listing):
        resp = client.post("/jobs/add-by-url", data={"url": "http://example.com/job/1"})

    assert resp.status_code == 200
    assert "Classified as job_posting" in resp.text
    assert len(q.get_jobs(conn)) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_routes_jobs.py -k "add_job_by_url or add_by_url" -v`
Expected: the two updated existing tests still PASS (patching `detect_listing` that doesn't exist yet would itself fail) — actually they'll FAIL at this point with `AttributeError: <module 'app.routes.jobs'> does not have the attribute 'detect_listing'`, since the patch target doesn't exist until Step 3. The two new tests FAIL the same way.

- [ ] **Step 3: Split `_fetch_url_text` and wire in detection**

In `app/routes/jobs.py`, change the imports at the top from:

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

to:

```python
from __future__ import annotations
import sqlite3
from urllib.parse import urlsplit
import httpx
import openai
from bs4 import BeautifulSoup
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from app.deps import get_db, get_ai_client, get_model
from app.db import queries as q
from app.pipeline import run_reprocess_job, run_pass_as_new, run_add_job
from app.fetchers.links import extract_links
from app.ai.detect_listing import detect_listing
from app.template_env import templates
```

Then change:

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

to:

```python
class _FetchError(Exception):
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
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator="\n")
    if not text.strip():
        raise _FetchError("page had no extractable text")
    return text
```

- [ ] **Step 4: Update `job_add_by_url` to branch on listing detection**

Find `job_add_by_url` and change:

```python
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

    return StreamingResponse(stream(), media_type="text/plain")
```

to:

```python
        else:
            try:
                html = _fetch_url_html(url)
            except _FetchError as exc:
                source_id = q.get_or_create_manual_source(conn)
                job_id = q.insert_job(conn, source_id=source_id, url=url, title=url, company="", raw_text="")
                q.update_job_pipeline(conn, job_id, simplified_content="", content_type="error")
                yield f"Failed to fetch: {exc}\n"
            else:
                links = extract_links(html, url)
                detection = detect_listing(client, model, links, url)
                if detection["is_listing"] and len(detection["job_links"]) >= 2:
                    panel = templates.get_template("jobs/_listing_confirm.html").render(
                        request=request,
                        url=url,
                        link_count=len(detection["job_links"]),
                        domain=urlsplit(url).netloc,
                        default_name=urlsplit(url).netloc,
                        filter_status=status,
                        filter_content_type=content_type,
                    )
                    yield "HTML:" + panel.replace("\n", "") + "\n"
                    return
                source_id = q.get_or_create_manual_source(conn)
                raw_text = _extract_text(html)
                gen = run_add_job(conn, client, model, source_id, url, raw_text)
                try:
                    while True:
                        yield next(gen) + "\n"
                except StopIteration:
                    pass

        html_chunk = templates.get_template("jobs/_content.html").render(
            request=request, **_content_context(conn, status, content_type)
        )
        yield "HTML:" + html_chunk.replace("\n", "") + "\n"

    return StreamingResponse(stream(), media_type="text/plain")
```

(the local variable was renamed `html_chunk` in the final block to avoid shadowing the `html` fetched a few lines above — the generator function's `html` from `_fetch_url_html` is still in scope at that point, so a plain `html =` reassignment would only be a style smell, not a bug, but the rename avoids the confusion). Note `_extract_text(html)` can itself raise `_FetchError` (empty page) — this is a pre-existing edge case (the old `_fetch_url_text` raised for the same reason) not newly introduced by this task, so no additional handling is added here.

- [ ] **Step 5: Create the confirmation panel template**

Create `app/templates/jobs/_listing_confirm.html`:

```html
<div class="listing-confirm">
  <p>Detected {{ link_count }} job posting link{{ 's' if link_count != 1 else '' }} at <strong>{{ domain }}</strong>.</p>
  <div style="display:flex; gap:0.5rem; align-items:center; flex-wrap:wrap;">
    <input type="hidden" id="listing-url" value="{{ url }}">
    <input type="text" id="listing-name" value="{{ default_name }}" style="flex:1; min-width:160px;">
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

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_routes_jobs.py -v`
Expected: all PASS. (`data-progress-body-url`/`data-progress-body-name` don't have JS support yet — that's Task 8 — but these are route/template tests asserting on the streamed HTML text, not exercising the browser JS, so they pass regardless.)

- [ ] **Step 7: Commit**

```bash
git add app/routes/jobs.py app/templates/jobs/_listing_confirm.html tests/test_routes_jobs.py
git commit -m "feat: detect listing pages in add-by-url and show a confirm panel"
```

---

### Task 7: `POST /jobs/add-listing-source`

**Files:**
- Modify: `app/routes/jobs.py`
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: `run_fetch` (existing, imported fresh in this task), `q.insert_source`, `q.get_source` (existing).
- Produces: `POST /jobs/add-listing-source` accepting form fields `url` and `name`, optional `status`/`content_type` query params (same filter-forwarding convention as `/jobs/add-by-url`). Creates a `generic_listing` source and streams a `run_fetch` on it, ending with the same refreshed-`_content.html` `HTML:` chunk convention.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_routes_jobs.py`:

```python
def _fake_run_fetch(source, conn, client, model, profile_dir):
    yield f"Starting fetch for '{source['name']}'"
    q.insert_job(conn, source_id=source["id"], url="https://careers.example.com/jobs/1", title="T", company="C", raw_text="r")
    yield "Fetch complete"


def test_add_listing_source_creates_source_and_streams_fetch(client, conn):
    with patch("app.routes.jobs.run_fetch", side_effect=_fake_run_fetch):
        resp = client.post(
            "/jobs/add-listing-source",
            data={"url": "https://careers.example.com/jobs", "name": "careers.example.com"},
        )

    assert resp.status_code == 200
    assert "Fetch complete" in resp.text
    sources = [s for s in q.get_sources(conn) if s["fetcher_type"] == "generic_listing"]
    assert len(sources) == 1
    assert sources[0]["name"] == "careers.example.com"
    assert sources[0]["url"] == "https://careers.example.com/jobs"


def test_add_listing_source_stream_ends_with_html_chunk(client, conn):
    with patch("app.routes.jobs.run_fetch", side_effect=_fake_run_fetch):
        resp = client.post(
            "/jobs/add-listing-source",
            data={"url": "https://careers.example.com/jobs", "name": "careers.example.com"},
        )

    assert resp.status_code == 200
    assert 'HTML:<div class="filter-bar">' in resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_routes_jobs.py -k add_listing_source -v`
Expected: FAIL with 404 (route doesn't exist yet).

- [ ] **Step 3: Add the route**

In `app/routes/jobs.py`, add `run_fetch` and `get_config` to the imports:

```python
from app.pipeline import run_reprocess_job, run_pass_as_new, run_add_job, run_fetch
from app.deps import get_db, get_ai_client, get_model, get_config
```

(this replaces the existing `from app.pipeline import ...` and `from app.deps import ...` lines from Task 6 — same lines, extended with one more name each.)

Then add at the end of `app/routes/jobs.py`:

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
        source = q.get_source(conn, source_id)
        gen = run_fetch(source, conn, client, model, config.browser_profile_dir)
        try:
            while True:
                yield next(gen) + "\n"
        except StopIteration:
            pass

        html_chunk = templates.get_template("jobs/_content.html").render(
            request=request, **_content_context(conn, status, content_type)
        )
        yield "HTML:" + html_chunk.replace("\n", "") + "\n"

    return StreamingResponse(stream(), media_type="text/plain")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_routes_jobs.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/routes/jobs.py tests/test_routes_jobs.py
git commit -m "feat: add POST /jobs/add-listing-source route"
```

---

### Task 8: Generalize the progress-button body mechanism in `base.html`

**Files:**
- Modify: `app/templates/base.html` (lines 130-142 and 176-179), `app/templates/jobs/list.html`
- Test: `tests/test_routes_jobs.py` (markup assertions)

**Interfaces:**
- Consumes: nothing new.
- Produces: `data-progress-body-<fieldname>="<selector>"` — one attribute per POST body field, replacing the single-purpose `data-progress-url-input` attribute. `app/templates/jobs/_listing_confirm.html` (Task 6) already uses this convention; this task makes the JS actually support it and migrates the one existing user (`jobs/list.html`'s add-by-url button).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_routes_jobs.py`:

```python
def test_job_list_add_by_url_button_uses_generalized_body_attribute(client, conn):
    resp = client.get("/jobs")
    assert resp.status_code == 200
    assert 'data-progress-body-url="#add-job-url"' in resp.text
    assert 'data-progress-url-input' not in resp.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_routes_jobs.py -k generalized_body -v`
Expected: FAIL — `jobs/list.html` still uses `data-progress-url-input`.

- [ ] **Step 3: Generalize the JS in `base.html`**

Find the body-construction block:

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

Replace with:

```javascript
        var body = null;
        if (el.hasAttribute("data-progress-jobs")) {
          var checked = document.querySelectorAll('input[name="job_ids"]:checked');
          if (!checked.length) return;
          body = new URLSearchParams();
          checked.forEach(function (cb) { body.append("job_ids", cb.value); });
        }
        var bodyFieldNames = [];
        for (var i = 0; i < el.attributes.length; i++) {
          var attrName = el.attributes[i].name;
          if (attrName.indexOf("data-progress-body-") === 0) {
            bodyFieldNames.push(attrName.slice("data-progress-body-".length));
          }
        }
        if (bodyFieldNames.length) {
          body = new URLSearchParams();
          for (var j = 0; j < bodyFieldNames.length; j++) {
            var fieldName = bodyFieldNames[j];
            var fieldEl = document.querySelector(el.getAttribute("data-progress-body-" + fieldName));
            if (!fieldEl || !fieldEl.value) return;
            body.append(fieldName, fieldEl.value);
          }
        }
```

Then find the clear-on-success block in `finish()`:

```javascript
          progressEl.remove();
          if (el.hasAttribute("data-progress-url-input")) {
            var clearInputEl = document.querySelector(el.getAttribute("data-progress-url-input"));
            if (clearInputEl) clearInputEl.value = "";
          }
          if (oobMode) {
```

Replace with:

```javascript
          progressEl.remove();
          for (var k = 0; k < bodyFieldNames.length; k++) {
            var clearFieldEl = document.querySelector(el.getAttribute("data-progress-body-" + bodyFieldNames[k]));
            if (clearFieldEl) clearFieldEl.value = "";
          }
          if (oobMode) {
```

(`bodyFieldNames` is declared with `var` earlier in the same `onClick` function scope, so it's still accessible here via JS's function-scoping — no need to redeclare or pass it through.)

- [ ] **Step 4: Migrate `jobs/list.html`'s add-by-url button**

In `app/templates/jobs/list.html`, change:

```html
  <button type="button" class="btn"
    data-progress-url="/jobs/add-by-url"
    data-progress-url-input="#add-job-url"
    data-progress-target="#jobs-content"
    data-progress-display="#add-job-progress">Add</button>
```

to:

```html
  <button type="button" class="btn"
    data-progress-url="/jobs/add-by-url"
    data-progress-body-url="#add-job-url"
    data-progress-target="#jobs-content"
    data-progress-display="#add-job-progress">Add</button>
```

- [ ] **Step 5: Update the pre-existing form-markup test**

In `tests/test_routes_jobs.py`, find `test_job_list_has_add_by_url_form` and change:

```python
    assert 'data-progress-url-input="#add-job-url"' in resp.text
```

to:

```python
    assert 'data-progress-body-url="#add-job-url"' in resp.text
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_routes_jobs.py -v`
Expected: all PASS.

- [ ] **Step 7: Run the full test suite**

Run: `uv run pytest -v`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add app/templates/base.html app/templates/jobs/list.html tests/test_routes_jobs.py
git commit -m "feat: generalize progress-button POST body to multiple named fields"
```

---

### Task 9: `generic_listing` in the manual "Add source" dropdown

**Files:**
- Modify: `app/templates/sources/index.html`
- Test: `tests/test_routes_sources.py`

**Interfaces:** none new — pure template change.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_routes_sources.py`:

```python
def test_sources_page_add_source_form_includes_generic_listing_option(client, conn):
    resp = client.get("/sources")
    assert resp.status_code == 200
    assert '<option value="generic_listing">generic_listing</option>' in resp.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_routes_sources.py -k generic_listing_option -v`
Expected: FAIL — option not present.

- [ ] **Step 3: Add the option**

In `app/templates/sources/index.html`, change:

```html
  <select name="fetcher_type">
    <option value="http">http</option>
    <option value="playwright">playwright</option>
    <option value="slack">slack</option>
    <option value="finn_listing">finn_listing</option>
  </select>
```

to:

```html
  <select name="fetcher_type">
    <option value="http">http</option>
    <option value="playwright">playwright</option>
    <option value="slack">slack</option>
    <option value="finn_listing">finn_listing</option>
    <option value="generic_listing">generic_listing</option>
  </select>
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_routes_sources.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/templates/sources/index.html tests/test_routes_sources.py
git commit -m "feat: add generic_listing to the manual Add source dropdown"
```

---

### Task 10: Manual verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `uv run pytest -v`
Expected: all PASS.

- [ ] **Step 2: Launch the dev server**

Use the `run-dev-server` skill (throwaway copy of `job-seek.db`, never the live one).

- [ ] **Step 3: Exercise the golden path**

In a browser, go to `/jobs`, paste a real career-listing URL (a page with multiple job links, e.g. a company's `/careers` page) into the add-by-url field, click Add. Confirm: a "Detected N job posting links…" panel appears with an editable name pre-filled from the domain. Click "Keep as source & fetch" and confirm: progress streams, the source is created, and discovered postings appear in the job list.

- [ ] **Step 4: Exercise edge cases**

- Paste a single job-posting URL (not a listing) → confirm the existing single-job flow still runs unchanged (no confirm panel).
- On the confirm panel, click "Cancel" → confirm it returns to the normal job list without creating a source.
- Go to `/sources` and `/fetch` → confirm the newly created listing source is visible (unlike the hidden "Manual" source) and re-triggering its fetch from `/fetch` works.
- Go to `/sources` → confirm `generic_listing` is a selectable option in the manual "Add source" form.

- [ ] **Step 5: Stop the dev server**

Per `CLAUDE.md`, stop the dev server once manual testing is done.

---

## Self-Review Notes

- **Spec coverage:** §1 (detection) → Tasks 1, 2. §2 (add-by-url wiring) → Task 6. §3 (confirm route) → Task 7. §4 (`GenericListingFetcher`) → Task 4, and `_make_fetcher`/`run_fetch` threading → Task 5. §5 (schema + source UI) → Task 3 (schema), Task 9 (dropdown) — the Manual-source-exclusion note in §5 required no code change (already filters by `fetcher_type != "manual"` specifically, not an allowlist, so `generic_listing` sources pass through unaffected — verified by reading the existing filters in `app/routes/sources.py`/`app/routes/fetch.py`). §6 (frontend generalization) → Task 8. Testing items 1-11 in the spec are each covered: 1→Task 1, 2→Task 2, 3→Task 6, 4→Task 6, 5→Task 6 (regression), 6→Task 7, 7→Task 4, 8→Task 5, 9→Task 3, 10→Task 9, 11→Task 10.
- **Placeholder scan:** none found — every step has complete code or an exact command.
- **Type consistency checked:** `detect_listing(client, model, links, page_url) -> dict` signature identical across Task 2's implementation, Task 4's `GenericListingFetcher.fetch()`, and Task 6's `job_add_by_url`. `extract_links(html, base_url) -> list[tuple[str, str]]` identical across Task 1, Task 4, Task 6. `GenericListingFetcher(source, client, model, known_urls=...)` constructor identical across Task 4 and Task 5's `_make_fetcher` call. `_make_fetcher`'s new `client`/`model` optional params flow consistently from Task 5 into Task 7 (via `run_fetch`, not called directly).
- **One cross-task ordering note:** Task 6 introduces `app/templates/jobs/_listing_confirm.html` using `data-progress-body-url`/`data-progress-body-name` (Task 8's convention) before Task 8 adds JS support for it. This is intentional and non-blocking — Task 6's own tests only assert on the streamed HTML text (the attributes are present in the markup), not on JS behavior, so the tasks are independently green in order. A human clicking the confirm button in a browser between Task 6 and Task 8 would find it inert, but that's fine since these are same-session, back-to-back tasks with no deploy in between.
