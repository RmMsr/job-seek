# Finn Listing Pagination & Age Cutoff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `FinnListingFetcher` walk finn.no's pagination, stop at a 30-day age cutoff, and stop re-fetching ad detail pages already stored in the database — then persist and display each job's published date.

**Architecture:** `FinnListingFetcher` walks `page=1..MAX_LISTING_PAGES` of the listing, reading each ad card's `<time datetime>` alongside its link. A source-agnostic `is_recent()` helper (in `app/fetchers/base.py`, reusable by future JS-listing fetchers) decides both the per-page pagination stop condition and which discovered ads are eligible to fetch. `pipeline.py` passes the set of already-known job URLs into the fetcher so detail pages already in the DB are never re-fetched, which also gives "resume after failure" behavior for free — no checkpoint state needed. `RawJob.published_at` flows through to a new nullable `jobs.published_at` column and is shown in the UI via a new `time_ago` Jinja filter.

**Tech Stack:** Python 3.12+, Playwright (sync API), SQLite, FastAPI/Jinja2, pytest + respx for HTTP mocking.

## Global Constraints

- `MAX_AGE_DAYS = 30` — ads published before this many days ago are ignored (per approved spec).
- `MAX_LISTING_PAGES = 5` — hard cap on paginated listing pages walked per run.
- `MAX_DETAIL_FETCHES = 50` — cap on new ad detail pages fetched per run.
- These three are plain module-level constants in `app/fetchers/finn.py`, not config-driven — this is a personal, single-instance app (see `CLAUDE.md`); adjusting them means editing the constant and redeploying.
- No backfill for existing rows when adding `jobs.published_at` — additive, nullable column only (see `CLAUDE.md` migration philosophy).
- Follow the existing `_migrate_jobs_add_headline` pattern exactly for the new migration (plain `ALTER TABLE`, no rebuild).

---

### Task 1: `RawJob.published_at` + source-agnostic `is_recent()` helper

**Files:**
- Modify: `app/fetchers/base.py`
- Test: `tests/test_fetcher_base.py` (new)

**Interfaces:**
- Produces: `RawJob.published_at: str | None = None` (new field, default preserves all existing keyword-arg call sites). `is_recent(published_at: str | None, max_age_days: int) -> bool`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_fetcher_base.py`:

```python
from datetime import datetime, timezone, timedelta
from app.fetchers.base import RawJob, is_recent


def test_raw_job_published_at_defaults_to_none():
    job = RawJob(url="http://x", title="T", company="C", raw_text="r")
    assert job.published_at is None


def test_raw_job_published_at_can_be_set():
    job = RawJob(url="http://x", title="T", company="C", raw_text="r", published_at="2026-07-01T00:00:00+00:00")
    assert job.published_at == "2026-07-01T00:00:00+00:00"


def test_is_recent_none_is_always_recent():
    assert is_recent(None, max_age_days=30) is True


def test_is_recent_within_cutoff():
    published = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    assert is_recent(published, max_age_days=30) is True


def test_is_recent_older_than_cutoff():
    published = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
    assert is_recent(published, max_age_days=30) is False


def test_is_recent_at_exact_boundary_is_recent():
    published = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    assert is_recent(published, max_age_days=30) is True


def test_is_recent_handles_z_suffix():
    published = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    assert is_recent(published, max_age_days=30) is True


def test_is_recent_handles_naive_datetime():
    published = (datetime.now(timezone.utc) - timedelta(days=1)).replace(tzinfo=None).isoformat()
    assert is_recent(published, max_age_days=30) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_fetcher_base.py -v`
Expected: FAIL — `ImportError: cannot import name 'is_recent'` and `TypeError: RawJob.__init__() got an unexpected keyword argument 'published_at'`.

- [ ] **Step 3: Implement**

Replace the full contents of `app/fetchers/base.py`:

```python
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol


@dataclass
class RawJob:
    url: str
    title: str
    company: str
    raw_text: str
    published_at: str | None = None


class Fetcher(Protocol):
    def fetch(self) -> list[RawJob]: ...


def is_recent(published_at: str | None, max_age_days: int) -> bool:
    """True if published_at (ISO-8601) is within max_age_days of now, or unknown."""
    if published_at is None:
        return True
    parsed = datetime.fromisoformat(published_at)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    return parsed >= cutoff
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_fetcher_base.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Run the full existing fetcher test suite to check nothing else broke**

Run: `uv run pytest tests/test_fetcher_http.py tests/test_fetcher_slack.py -v`
Expected: PASS (the `published_at=None` default keeps every existing `RawJob(...)` call site working unchanged)

- [ ] **Step 6: Commit**

```bash
git add app/fetchers/base.py tests/test_fetcher_base.py
git commit -m "feat: add RawJob.published_at and is_recent() age-cutoff helper"
```

---

### Task 2: Persist `published_at` — schema migration, `insert_job`, `get_all_job_urls`

**Files:**
- Modify: `app/db/schema.py`
- Modify: `app/db/queries.py`
- Test: `tests/test_schema.py`
- Test: `tests/test_queries.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `jobs.published_at` nullable TEXT column. `insert_job(..., published_at: str | None = None)` (new keyword arg, default preserves all existing call sites). `get_all_job_urls(conn: sqlite3.Connection) -> frozenset[str]`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_schema.py` (after `test_init_db_migrates_jobs_adds_headline_column`, matching its exact style):

```python
def test_jobs_table_has_published_at_column(conn):
    init_db(conn)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    assert "published_at" in cols


def test_init_db_migrates_jobs_adds_published_at_column(conn):
    conn.executescript(
        """
        CREATE TABLE sources (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            fetcher_type TEXT NOT NULL CHECK(fetcher_type IN ('http', 'playwright', 'slack', 'finn_listing')),
            enabled INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE jobs (
            id INTEGER PRIMARY KEY,
            source_id INTEGER NOT NULL REFERENCES sources(id),
            url TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL DEFAULT '',
            company TEXT NOT NULL DEFAULT '',
            raw_text TEXT NOT NULL DEFAULT '',
            simplified_content TEXT NOT NULL DEFAULT '',
            summary TEXT NOT NULL DEFAULT '',
            headline TEXT NOT NULL DEFAULT '',
            content_type TEXT CHECK(content_type IN ('job_posting', 'lead', 'irrelevant', 'error')),
            fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
            status TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('new', 'accepted', 'rejected', 'invalid')),
            feedback_note TEXT,
            feedback_scenario_id INTEGER REFERENCES scenarios(id)
        );
        """
    )
    conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s', 'http://x', 'http')")
    conn.execute("INSERT INTO jobs (source_id, url, title) VALUES (1, 'http://job/1', 'Existing Title')")
    conn.commit()

    init_db(conn)

    row = conn.execute("SELECT title, published_at FROM jobs WHERE url = 'http://job/1'").fetchone()
    assert row["title"] == "Existing Title"
    assert row["published_at"] is None

    # Idempotent: running init_db again doesn't error or duplicate columns.
    init_db(conn)
    cols = [row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()]
    assert cols.count("published_at") == 1
```

Add to `tests/test_queries.py` (near `test_insert_and_get_job`):

```python
def test_insert_job_stores_published_at(conn):
    source_id = q.insert_source(conn, "s", "http://x", "finn_listing")
    jid = q.insert_job(
        conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r",
        published_at="2026-07-01T00:00:00+00:00",
    )
    job = q.get_job(conn, jid)
    assert job["published_at"] == "2026-07-01T00:00:00+00:00"


def test_insert_job_published_at_defaults_to_none(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    job = q.get_job(conn, jid)
    assert job["published_at"] is None


def test_get_all_job_urls_empty(conn):
    assert q.get_all_job_urls(conn) == frozenset()


def test_get_all_job_urls_returns_all_urls(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.insert_job(conn, source_id=source_id, url="http://job/2", title="T2", company="C2", raw_text="r2")
    assert q.get_all_job_urls(conn) == frozenset({"http://job/1", "http://job/2"})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_schema.py tests/test_queries.py -v`
Expected: FAIL — `KeyError: 'published_at'` and `AttributeError: module 'app.db.queries' has no attribute 'get_all_job_urls'` and `TypeError: insert_job() got an unexpected keyword argument 'published_at'`.

- [ ] **Step 3: Implement schema changes**

In `app/db/schema.py`, edit the `jobs` table DDL — change:

```python
    headline TEXT NOT NULL DEFAULT '',
    content_type TEXT CHECK(content_type IN ('job_posting', 'lead', 'irrelevant', 'error')),
```

to:

```python
    headline TEXT NOT NULL DEFAULT '',
    published_at TEXT,
    content_type TEXT CHECK(content_type IN ('job_posting', 'lead', 'irrelevant', 'error')),
```

Add a new migration function after `_migrate_jobs_add_headline`:

```python
def _migrate_jobs_add_published_at(conn: sqlite3.Connection) -> None:
    # Purely additive column, same shape as the headline migration above.
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='jobs'"
    ).fetchone()
    if row is None or "published_at" in row[0]:
        return
    conn.execute("ALTER TABLE jobs ADD COLUMN published_at TEXT")
    conn.commit()
```

Update `init_db` to call it:

```python
def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL)
    _migrate_sources_fetcher_type(conn)
    _migrate_jobs_scores_to_table(conn)
    _migrate_jobs_add_feedback_scenario_id(conn)
    _migrate_jobs_add_headline(conn)
    _migrate_jobs_add_published_at(conn)
```

- [ ] **Step 4: Implement query changes**

In `app/db/queries.py`, change `insert_job`:

```python
def insert_job(
    conn: sqlite3.Connection,
    *,
    source_id: int,
    url: str,
    title: str,
    company: str,
    raw_text: str,
    published_at: str | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO jobs (source_id, url, title, company, raw_text, published_at) VALUES (?, ?, ?, ?, ?, ?)",
        (source_id, url, title, company, raw_text, published_at),
    )
    conn.commit()
    return cur.lastrowid
```

Add a new query function after `url_exists`:

```python
def get_all_job_urls(conn: sqlite3.Connection) -> frozenset[str]:
    rows = conn.execute("SELECT url FROM jobs").fetchall()
    return frozenset(r["url"] for r in rows)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_schema.py tests/test_queries.py -v`
Expected: PASS

- [ ] **Step 6: Run the full test suite to check nothing else broke**

Run: `uv run pytest -v`
Expected: PASS (no other code references the `jobs` INSERT column list or `insert_job`'s positional shape)

- [ ] **Step 7: Commit**

```bash
git add app/db/schema.py app/db/queries.py tests/test_schema.py tests/test_queries.py
git commit -m "feat: persist job published_at and add get_all_job_urls query"
```

---

### Task 3: `FinnListingFetcher` — pagination, age cutoff, known-URL skip

**Files:**
- Modify: `app/fetchers/finn.py`
- Test: `tests/test_fetcher_finn.py` (full rewrite)

**Interfaces:**
- Consumes: `RawJob.published_at` and `is_recent()` from `app/fetchers/base.py` (Task 1).
- Produces: `FinnListingFetcher.__init__(self, source: dict, known_urls: frozenset[str] = frozenset())`. `FinnListingFetcher.fetch() -> list[RawJob]` (unchanged public shape). Module constants `MAX_AGE_DAYS`, `MAX_LISTING_PAGES`, `MAX_DETAIL_FETCHES` consumed by no other module yet, but Task 4's tests will reference `FinnListingFetcher` and `_make_fetcher`.

- [ ] **Step 1: Write the failing tests**

Replace the full contents of `tests/test_fetcher_finn.py`:

```python
from datetime import datetime, timezone, timedelta
import respx
import httpx
from unittest.mock import MagicMock, patch
from app.fetchers.finn import FinnListingFetcher

_SOURCE = {"id": 1, "name": "finn.no", "url": "https://www.finn.no/job/search?x=1", "fetcher_type": "finn_listing"}


def _dt(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _mock_playwright(pages_cards):
    """pages_cards: list of card-lists (each a list of {"href", "publishedAt"} dicts),
    one entry per simulated page.eval_on_selector_all() call."""
    cm = MagicMock()
    pw = MagicMock()
    cm.__enter__.return_value = pw
    cm.__exit__.return_value = False
    browser = MagicMock()
    pw.chromium.launch.return_value = browser
    page = MagicMock()
    browser.new_page.return_value = page
    page.eval_on_selector_all.side_effect = pages_cards
    return cm, page


@respx.mock
def test_discovers_and_fetches_each_ad():
    page1 = [
        {"href": "https://www.finn.no/job/ad/1", "publishedAt": _dt(1)},
        {"href": "https://www.finn.no/job/ad/2", "publishedAt": _dt(2)},
        {"href": "https://www.finn.no/job/ad/1", "publishedAt": _dt(1)},
    ]
    respx.get("https://www.finn.no/job/ad/1").mock(return_value=httpx.Response(200, text="<p>Job one</p>"))
    respx.get("https://www.finn.no/job/ad/2").mock(return_value=httpx.Response(200, text="<p>Job two</p>"))

    cm, page = _mock_playwright([page1, []])
    with patch("app.fetchers.finn.sync_playwright", return_value=cm):
        jobs = FinnListingFetcher(_SOURCE).fetch()

    urls = [j.url for j in jobs]
    assert urls == ["https://www.finn.no/job/ad/1", "https://www.finn.no/job/ad/2"]
    assert "Job one" in jobs[0].raw_text
    assert "Job two" in jobs[1].raw_text
    assert jobs[0].published_at == page1[0]["publishedAt"]
    assert jobs[1].published_at == page1[1]["publishedAt"]


@respx.mock
def test_caps_number_of_ads_fetched_and_stops_paginating_once_budget_filled():
    cards = [{"href": f"https://www.finn.no/job/ad/{i}", "publishedAt": _dt(1)} for i in range(60)]
    for i in range(60):
        respx.get(f"https://www.finn.no/job/ad/{i}").mock(return_value=httpx.Response(200, text=f"<p>Job {i}</p>"))

    cm, page = _mock_playwright([cards])
    with patch("app.fetchers.finn.sync_playwright", return_value=cm):
        jobs = FinnListingFetcher(_SOURCE).fetch()

    assert len(jobs) == 50
    assert page.eval_on_selector_all.call_count == 1


def test_ignores_non_ad_links():
    cards = [
        {"href": "https://www.finn.no/job/browse.html", "publishedAt": None},
        {"href": "https://www.finn.no/job/ad/1", "publishedAt": _dt(1)},
    ]
    cm, page = _mock_playwright([cards, []])
    with patch("app.fetchers.finn.sync_playwright", return_value=cm), \
         patch("app.fetchers.finn.HttpFetcher") as MockHttp:
        MockHttp.return_value.fetch.return_value = []
        FinnListingFetcher(_SOURCE).fetch()

    called_urls = [c.args[0]["url"] for c in MockHttp.call_args_list]
    assert called_urls == ["https://www.finn.no/job/ad/1"]


def test_returns_empty_list_on_playwright_error():
    with patch("app.fetchers.finn.sync_playwright", side_effect=RuntimeError("boom")):
        jobs = FinnListingFetcher(_SOURCE).fetch()
    assert jobs == []


@respx.mock
def test_paginates_across_multiple_pages_within_cutoff():
    page1 = [{"href": "https://www.finn.no/job/ad/1", "publishedAt": _dt(1)}]
    page2 = [{"href": "https://www.finn.no/job/ad/2", "publishedAt": _dt(2)}]
    respx.get("https://www.finn.no/job/ad/1").mock(return_value=httpx.Response(200, text="<p>Job one</p>"))
    respx.get("https://www.finn.no/job/ad/2").mock(return_value=httpx.Response(200, text="<p>Job two</p>"))

    cm, page = _mock_playwright([page1, page2, []])
    with patch("app.fetchers.finn.sync_playwright", return_value=cm):
        jobs = FinnListingFetcher(_SOURCE).fetch()

    assert [j.url for j in jobs] == [
        "https://www.finn.no/job/ad/1",
        "https://www.finn.no/job/ad/2",
    ]
    goto_urls = [c.args[0] for c in page.goto.call_args_list]
    assert goto_urls[0] == _SOURCE["url"]
    assert "page=2" in goto_urls[1]
    assert "page=3" in goto_urls[2]


@respx.mock
def test_stops_after_max_listing_pages():
    pages = [[{"href": f"https://www.finn.no/job/ad/{i}", "publishedAt": _dt(1)}] for i in range(5)]
    for i in range(5):
        respx.get(f"https://www.finn.no/job/ad/{i}").mock(return_value=httpx.Response(200, text=f"<p>Job {i}</p>"))

    cm, page = _mock_playwright(pages)
    with patch("app.fetchers.finn.sync_playwright", return_value=cm):
        jobs = FinnListingFetcher(_SOURCE).fetch()

    assert page.eval_on_selector_all.call_count == 5
    assert len(jobs) == 5


@respx.mock
def test_stale_ad_interleaved_with_fresh_does_not_halt_pagination():
    page1 = [
        {"href": "https://www.finn.no/job/ad/stale", "publishedAt": _dt(40)},
        {"href": "https://www.finn.no/job/ad/1", "publishedAt": _dt(1)},
    ]
    page2 = [{"href": "https://www.finn.no/job/ad/2", "publishedAt": _dt(2)}]
    respx.get("https://www.finn.no/job/ad/1").mock(return_value=httpx.Response(200, text="<p>Job one</p>"))
    respx.get("https://www.finn.no/job/ad/2").mock(return_value=httpx.Response(200, text="<p>Job two</p>"))

    cm, page = _mock_playwright([page1, page2, []])
    with patch("app.fetchers.finn.sync_playwright", return_value=cm):
        jobs = FinnListingFetcher(_SOURCE).fetch()

    urls = [j.url for j in jobs]
    assert "https://www.finn.no/job/ad/stale" not in urls
    assert urls == ["https://www.finn.no/job/ad/1", "https://www.finn.no/job/ad/2"]


@respx.mock
def test_stops_when_a_full_page_is_past_cutoff():
    page1 = [{"href": "https://www.finn.no/job/ad/1", "publishedAt": _dt(1)}]
    page2 = [{"href": "https://www.finn.no/job/ad/old", "publishedAt": _dt(40)}]
    respx.get("https://www.finn.no/job/ad/1").mock(return_value=httpx.Response(200, text="<p>Job one</p>"))

    cm, page = _mock_playwright([page1, page2])
    with patch("app.fetchers.finn.sync_playwright", return_value=cm):
        jobs = FinnListingFetcher(_SOURCE).fetch()

    assert [j.url for j in jobs] == ["https://www.finn.no/job/ad/1"]
    assert page.eval_on_selector_all.call_count == 2


@respx.mock
def test_skips_already_known_urls():
    page1 = [
        {"href": "https://www.finn.no/job/ad/1", "publishedAt": _dt(1)},
        {"href": "https://www.finn.no/job/ad/2", "publishedAt": _dt(2)},
    ]
    respx.get("https://www.finn.no/job/ad/2").mock(return_value=httpx.Response(200, text="<p>Job two</p>"))
    # ad/1 intentionally has no respx mock: if the fetcher tried to fetch it, respx would raise.

    cm, page = _mock_playwright([page1, []])
    with patch("app.fetchers.finn.sync_playwright", return_value=cm):
        jobs = FinnListingFetcher(_SOURCE, known_urls=frozenset({"https://www.finn.no/job/ad/1"})).fetch()

    assert [j.url for j in jobs] == ["https://www.finn.no/job/ad/2"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_fetcher_finn.py -v`
Expected: FAIL — `TypeError: FinnListingFetcher.fetch() takes 1 positional argument` / `AttributeError` on `.published_at`, and assorted assertion failures against the current single-page, no-cutoff implementation.

- [ ] **Step 3: Implement**

Replace the full contents of `app/fetchers/finn.py`:

```python
from __future__ import annotations
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from playwright.sync_api import sync_playwright
from app.fetchers.base import RawJob, is_recent
from app.fetchers.http import HttpFetcher

MAX_AGE_DAYS = 30          # ads published before this many days ago are ignored
MAX_LISTING_PAGES = 5      # hard cap on paginated listing pages walked per run
MAX_DETAIL_FETCHES = 50    # cap on new ad detail pages fetched per run


class FinnListingFetcher:
    """Discovers individual job ad URLs from a finn.no search/listing page
    (which requires JS to render) via a headless Playwright load, walking
    pagination up to MAX_LISTING_PAGES and stopping once ads fall outside
    MAX_AGE_DAYS, then fetches each ad page as a plain static page —
    finn.no ad pages are server-rendered."""

    def __init__(self, source: dict, known_urls: frozenset[str] = frozenset()) -> None:
        self._source = source
        self._known_urls = known_urls

    def fetch(self) -> list[RawJob]:
        ads = self._discover_ads()
        new_ads = [(url, pub) for url, pub in ads if url not in self._known_urls]
        jobs: list[RawJob] = []
        for url, published_at in new_ads[:MAX_DETAIL_FETCHES]:
            for job in HttpFetcher({"url": url}).fetch():
                job.published_at = published_at
                jobs.append(job)
        return jobs

    def _discover_ads(self) -> list[tuple[str, str | None]]:
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                try:
                    page = browser.new_page()
                    seen: set[str] = set()
                    collected: list[tuple[str, str | None]] = []
                    new_within_cutoff = 0
                    for page_num in range(1, MAX_LISTING_PAGES + 1):
                        page.goto(self._page_url(page_num), wait_until="networkidle", timeout=30000)
                        cards = page.eval_on_selector_all(
                            "a[href*='/job/ad/']",
                            """els => els.map(e => {
                                const container = e.closest('article') || e.closest('section');
                                const time = container ? container.querySelector('time') : null;
                                return {href: e.href, publishedAt: time ? time.getAttribute('datetime') : null};
                            })""",
                        )
                        page_has_fresh_ad = False
                        for card in cards:
                            href = card["href"]
                            if "/job/ad/" not in href or href in seen:
                                continue
                            seen.add(href)
                            published_at = card["publishedAt"]
                            collected.append((href, published_at))
                            if is_recent(published_at, MAX_AGE_DAYS):
                                page_has_fresh_ad = True
                                if href not in self._known_urls:
                                    new_within_cutoff += 1
                        if not page_has_fresh_ad:
                            break
                        if new_within_cutoff >= MAX_DETAIL_FETCHES:
                            break
                finally:
                    browser.close()
        except Exception:
            return []

        return [(url, pub) for url, pub in collected if is_recent(pub, MAX_AGE_DAYS)]

    def _page_url(self, page_num: int) -> str:
        if page_num == 1:
            return self._source["url"]
        parts = urlsplit(self._source["url"])
        query = dict(parse_qsl(parts.query))
        query["page"] = str(page_num)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_fetcher_finn.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add app/fetchers/finn.py tests/test_fetcher_finn.py
git commit -m "feat: paginate finn listing fetcher with age cutoff and known-URL skip"
```

---

### Task 4: Wire known URLs and `published_at` through `pipeline.py`

**Files:**
- Modify: `app/pipeline.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `FinnListingFetcher(source, known_urls=...)` (Task 3), `q.get_all_job_urls(conn)` and `q.insert_job(..., published_at=...)` (Task 2), `RawJob.published_at` (Task 1).
- Produces: `_make_fetcher(source: dict, profile_dir: str, conn: sqlite3.Connection)` (signature change — third positional param added).

- [ ] **Step 1: Write the failing tests**

In `tests/test_pipeline.py`, replace `test_make_fetcher_dispatches_finn_listing`:

```python
def test_make_fetcher_dispatches_finn_listing(conn):
    source = {"id": 1, "name": "finn.no", "url": "http://x", "fetcher_type": "finn_listing"}
    fetcher = _make_fetcher(source, "browser-profile", conn)
    assert isinstance(fetcher, FinnListingFetcher)


def test_make_fetcher_finn_listing_passes_known_urls(conn):
    sid = q.insert_source(conn, "test", "http://example.com", "http")
    q.insert_job(conn, source_id=sid, url="http://known/1", title="T", company="C", raw_text="r")
    source = {"id": 1, "name": "finn.no", "url": "http://x", "fetcher_type": "finn_listing"}

    fetcher = _make_fetcher(source, "browser-profile", conn)

    assert fetcher._known_urls == frozenset({"http://known/1"})


def test_make_fetcher_http_ignores_conn(conn):
    source = {"id": 1, "name": "test", "url": "http://x", "fetcher_type": "http"}
    fetcher = _make_fetcher(source, "browser-profile", conn)
    assert type(fetcher).__name__ == "HttpFetcher"
```

Add a new test after `test_run_fetch_new_job_stored`:

```python
def test_run_fetch_stores_published_at(conn, source):
    raw_jobs = [RawJob(
        url="http://example.com/job/1", title="T", company="C", raw_text="r",
        published_at="2026-07-01T00:00:00+00:00",
    )]
    client = MagicMock()
    choice = MagicMock()
    choice.message.content = '{"type": "irrelevant", "reason": "x"}'
    client.chat.completions.create.return_value = MagicMock(choices=[choice])

    with patch("app.pipeline.HttpFetcher") as MockFetcher:
        MockFetcher.return_value.fetch.return_value = raw_jobs
        _drain(run_fetch(source, conn, client, "llama3.2", "browser-profile"))

    job = q.get_jobs(conn)[0]
    assert job["published_at"] == "2026-07-01T00:00:00+00:00"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_pipeline.py -v`
Expected: FAIL — `TypeError: _make_fetcher() missing 1 required positional argument: 'conn'` and `published_at` assertion failure (currently never stored).

- [ ] **Step 3: Implement**

In `app/pipeline.py`, change `_make_fetcher`:

```python
def _make_fetcher(source: dict, profile_dir: str, conn: sqlite3.Connection):
    ft = source["fetcher_type"]
    if ft == "http":
        return HttpFetcher(source)
    if ft == "slack":
        return SlackFetcher(source, profile_dir)
    if ft == "finn_listing":
        return FinnListingFetcher(source, known_urls=q.get_all_job_urls(conn))
    return PlaywrightFetcher(source, profile_dir)
```

Update its call site inside `run_fetch`:

```python
        fetcher = _make_fetcher(source, profile_dir, conn)
```

Update the `insert_job` call inside `run_fetch`'s loop:

```python
            job_id = q.insert_job(
                conn,
                source_id=source["id"],
                url=raw.url,
                title=raw.title,
                company=raw.company,
                raw_text=raw.raw_text,
                published_at=raw.published_at,
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_pipeline.py -v`
Expected: PASS

- [ ] **Step 5: Run the full test suite**

Run: `uv run pytest -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/pipeline.py tests/test_pipeline.py
git commit -m "feat: pass known job URLs into finn fetcher and persist published_at from run_fetch"
```

---

### Task 5: `time_ago` Jinja filter

**Files:**
- Create: `app/dates.py`
- Modify: `app/template_env.py`
- Test: `tests/test_dates.py` (new)

**Interfaces:**
- Produces: `time_ago(published_at: str | None) -> str`, registered as the Jinja filter `time_ago`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_dates.py`:

```python
from datetime import datetime, timezone, timedelta
from app.dates import time_ago


def test_time_ago_none_returns_empty_string():
    assert time_ago(None) == ""


def test_time_ago_today():
    published = datetime.now(timezone.utc).isoformat()
    assert time_ago(published) == "today"


def test_time_ago_one_day():
    published = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    assert time_ago(published) == "1 day ago"


def test_time_ago_multiple_days():
    published = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    assert time_ago(published) == "5 days ago"


def test_time_ago_handles_z_suffix():
    published = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    assert time_ago(published) == "2 days ago"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_dates.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.dates'`

- [ ] **Step 3: Implement**

Create `app/dates.py`:

```python
from __future__ import annotations
from datetime import datetime, timezone


def time_ago(published_at: str | None) -> str:
    """Render an ISO-8601 timestamp as a short relative string, e.g. '5 days ago'."""
    if not published_at:
        return ""
    parsed = datetime.fromisoformat(published_at)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    days = (datetime.now(timezone.utc) - parsed).days
    if days <= 0:
        return "today"
    if days == 1:
        return "1 day ago"
    return f"{days} days ago"
```

In `app/template_env.py`, add the import and filter registration:

```python
from __future__ import annotations
from fastapi.templating import Jinja2Templates
from app.markdown_render import render_markdown, render_markdown_inline, markdown_to_text
from app.dates import time_ago

templates = Jinja2Templates(directory="app/templates")
templates.env.filters["markdown"] = render_markdown
templates.env.filters["markdown_inline"] = render_markdown_inline
templates.env.filters["markdown_text"] = markdown_to_text
templates.env.filters["time_ago"] = time_ago
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_dates.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/dates.py app/template_env.py tests/test_dates.py
git commit -m "feat: add time_ago Jinja filter for relative published dates"
```

---

### Task 6: Show published date on job card and detail view

**Files:**
- Modify: `app/templates/jobs/_macros.html`
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: `job.published_at` (Task 2 column, already included via `jobs.*` in `_BEST_SCORE_SELECT`), `time_ago` filter (Task 5).

- [ ] **Step 1: Write the failing tests**

At the top of `tests/test_routes_jobs.py`, change:

```python
import pytest
from app.db import queries as q
```

to:

```python
from datetime import datetime, timezone, timedelta
import pytest
from app.db import queries as q
```

Then add these two tests to the file:

```python
def test_job_list_shows_published_date(client, conn):
    published = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "finn_listing")
    q.insert_job(
        conn, source_id=sid, url="http://finn.no/job/1", title="ML Eng", company="Acme", raw_text="r",
        published_at=published,
    )
    resp = client.get("/")
    assert resp.status_code == 200
    assert "5 days ago" in resp.text


def test_job_list_omits_published_date_when_unknown(client, conn):
    _seed(conn)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "days ago" not in resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_routes_jobs.py -v`
Expected: FAIL — `test_job_list_shows_published_date` fails because "5 days ago" isn't in the rendered page yet.

- [ ] **Step 3: Implement**

In `app/templates/jobs/_macros.html`, change the `meta_tags` macro:

```jinja
{% macro meta_tags(job) %}
<dl class="job-tags">
  {{ score_scenario_fields(job) }}
  <dt class="sr-only">Content type</dt>
  <dd><span class="tag">{{ job.content_type or "unknown" }}</span></dd>
  <dt class="sr-only">Source</dt>
  <dd><span class="tag">{{ job.source_name or "" }}</span></dd>
  {% if job.published_at %}
  <dt class="sr-only">Published</dt>
  <dd><span class="tag">{{ job.published_at | time_ago }}</span></dd>
  {% endif %}
</dl>
{% endmacro %}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_routes_jobs.py -v`
Expected: PASS

- [ ] **Step 5: Run the full test suite**

Run: `uv run pytest -v`
Expected: PASS (all tests green)

- [ ] **Step 6: Commit**

```bash
git add app/templates/jobs/_macros.html tests/test_routes_jobs.py
git commit -m "feat: show published date on job card and detail view"
```

---

## Final Verification

- [ ] Run the complete test suite once more: `uv run pytest -v` — expect all tests passing, no warnings about unused fixtures/imports.
- [ ] Manually inspect `app/fetchers/finn.py` for the three named constants (`MAX_AGE_DAYS`, `MAX_LISTING_PAGES`, `MAX_DETAIL_FETCHES`) sitting at module top with their comments, per the Global Constraints section.
- [ ] Confirm `docs/superpowers/specs/2026-08-03-finn-pagination-cutoff-design.md` requirements are all covered: pagination ✓ (Task 3), age cutoff ✓ (Task 1/3), known-URL skip ✓ (Task 3/4), `published_at` persistence ✓ (Task 2/4), UI display ✓ (Task 5/6).
