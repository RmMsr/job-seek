# Playwright Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When plain-HTTP fetching comes back with too little real content (a JS-rendered page), retry with a shared, idle-timeout-bounded headless-Chromium pool instead of giving up — wired into add-by-url and `GenericListingFetcher`'s periodic re-fetch.

**Architecture:** A `BrowserPool` owns one dedicated background thread (Playwright's sync API is thread-affine) that lazily launches a headless Chromium browser, serves render requests from any calling thread via a queue, and closes the browser after 10s of no requests. A shared `has_enough_content`/`extract_text` threshold check (moved out of `app/routes/jobs.py` into `app/fetchers/content.py`) decides when to bother calling it.

**Tech Stack:** Python, `playwright.sync_api`, `threading`/`queue` (stdlib), pytest + `unittest.mock` + `respx` (existing test stack).

## Global Constraints

- Content threshold: `MIN_CONTENT_LENGTH = 200` characters of extracted text (existing value, moved not changed).
- Browser pool idle timeout: `10.0` seconds default (constructor-overridable for tests).
- Page render timeout: `30000` ms (matches existing `PlaywrightFetcher`/`FinnListingFetcher`).
- Page load wait strategy: `wait_until="networkidle"` (matches `FinnListingFetcher`, needed for SPA content to populate before reading `page.content()`).
- Per-fetch-run cap on Playwright fallbacks inside `GenericListingFetcher`: `MAX_PLAYWRIGHT_FALLBACKS = 10`.
- `HttpFetcher` (`app/fetchers/http.py`) is **not** modified — the fallback applies only to add-by-url and `GenericListingFetcher`.
- No new dependencies — Playwright is already in `pyproject.toml`.

---

### Task 1: Shared content-threshold helper

**Files:**
- Create: `app/fetchers/content.py`
- Test: `tests/test_fetcher_content.py`
- Modify: `app/routes/jobs.py:1-45` (imports + `_extract_text`/`_MIN_CONTENT_LENGTH`)

**Interfaces:**
- Produces: `app.fetchers.content.MIN_CONTENT_LENGTH: int`, `app.fetchers.content.extract_text(html: str) -> str`, `app.fetchers.content.has_enough_content(html: str) -> bool`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_fetcher_content.py`:

```python
from app.fetchers.content import MIN_CONTENT_LENGTH, extract_text, has_enough_content


def test_has_enough_content_true_for_long_text():
    html = "<html><body><p>" + ("word " * 50) + "</p></body></html>"
    assert has_enough_content(html)


def test_has_enough_content_false_for_short_text():
    html = "<html><body><noscript>Enable JavaScript</noscript></body></html>"
    assert not has_enough_content(html)


def test_has_enough_content_boundary_at_min_length():
    text = "a" * MIN_CONTENT_LENGTH
    assert has_enough_content(f"<html><body><p>{text}</p></body></html>")
    assert not has_enough_content(f"<html><body><p>{text[:-1]}</p></body></html>")


def test_extract_text_strips_tags():
    html = "<html><body><h1>Title</h1><p>Body text</p></body></html>"
    text = extract_text(html)
    assert "Title" in text
    assert "Body text" in text
    assert "<h1>" not in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_fetcher_content.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.fetchers.content'`

- [ ] **Step 3: Create `app/fetchers/content.py`**

```python
from __future__ import annotations
from bs4 import BeautifulSoup

MIN_CONTENT_LENGTH = 200  # below this, treat as no real content (e.g. a JS-only page's noscript shell)


def extract_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    return soup.get_text(separator="\n")


def has_enough_content(html: str) -> bool:
    return len(extract_text(html).strip()) >= MIN_CONTENT_LENGTH
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_fetcher_content.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Point `app/routes/jobs.py` at the shared helper**

In `app/routes/jobs.py`, remove the `from bs4 import BeautifulSoup` import (line 6) — it will no longer be used directly in this file. Add:

```python
from app.fetchers.content import has_enough_content, extract_text as _extract_text_raw
```

Replace the existing block (currently lines 37-45):

```python
_MIN_CONTENT_LENGTH = 200  # below this, treat as no real content (e.g. a JS-only page's noscript shell)


def _extract_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator="\n")
    if len(text.strip()) < _MIN_CONTENT_LENGTH:
        raise _NoContentError("page had little to no extractable text")
    return text
```

with:

```python
def _extract_text(html: str) -> str:
    if not has_enough_content(html):
        raise _NoContentError("page had little to no extractable text")
    return _extract_text_raw(html)
```

- [ ] **Step 6: Run the full jobs test suite to confirm no regression**

Run: `.venv/bin/python -m pytest tests/test_routes_jobs.py -v`
Expected: PASS (all existing tests, unchanged behavior)

- [ ] **Step 7: Commit**

```bash
git add app/fetchers/content.py tests/test_fetcher_content.py app/routes/jobs.py
git commit -m "refactor: extract shared content-threshold check into app/fetchers/content.py"
```

---

### Task 2: Browser pool

**Files:**
- Create: `app/fetchers/playwright_pool.py`
- Test: `tests/test_fetcher_playwright_pool.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `app.fetchers.playwright_pool.BrowserPool(idle_timeout_seconds: float = 10.0)` with method `.render(url: str, timeout_ms: int = 30000) -> str | None`; module-level `app.fetchers.playwright_pool.render_html(url: str, timeout_ms: int = 30000) -> str | None`. Tasks 3 and 4 import and call `render_html`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_fetcher_playwright_pool.py`:

```python
import time
from unittest.mock import MagicMock, patch
from app.fetchers.playwright_pool import BrowserPool


def _mock_playwright():
    """Returns (sync_playwright_mock, pw, browser, page). Mirrors how a real
    `sync_playwright().start()` -> `pw.chromium.launch()` -> `browser.new_context()`
    -> `ctx.new_page()` chain is shaped, minus the `with` block since BrowserPool
    calls `.start()`/`.stop()` directly instead of using `sync_playwright()` as a
    context manager (it needs to hold the Playwright instance open across calls)."""
    sync_playwright_mock = MagicMock()
    pw = MagicMock()
    sync_playwright_mock.return_value.start.return_value = pw
    browser = MagicMock()
    pw.chromium.launch.return_value = browser
    ctx = MagicMock()
    browser.new_context.return_value = ctx
    page = MagicMock()
    ctx.new_page.return_value = page
    page.content.return_value = "<html>rendered</html>"
    return sync_playwright_mock, pw, browser, page


def test_render_returns_page_content():
    sync_playwright_mock, pw, browser, page = _mock_playwright()
    pool = BrowserPool(idle_timeout_seconds=1.0)
    with patch("app.fetchers.playwright_pool.sync_playwright", sync_playwright_mock):
        html = pool.render("https://example.com")
    assert html == "<html>rendered</html>"
    page.goto.assert_called_once_with("https://example.com", wait_until="networkidle", timeout=30000)


def test_render_reuses_browser_across_calls_within_idle_window():
    sync_playwright_mock, pw, browser, page = _mock_playwright()
    pool = BrowserPool(idle_timeout_seconds=1.0)
    with patch("app.fetchers.playwright_pool.sync_playwright", sync_playwright_mock):
        pool.render("https://example.com/a")
        pool.render("https://example.com/b")
    assert pw.chromium.launch.call_count == 1
    assert browser.new_context.call_count == 2


def test_render_closes_browser_after_idle_timeout_and_relaunches():
    sync_playwright_mock, pw, browser, page = _mock_playwright()
    pool = BrowserPool(idle_timeout_seconds=0.05)
    with patch("app.fetchers.playwright_pool.sync_playwright", sync_playwright_mock):
        pool.render("https://example.com")
        time.sleep(0.2)
        assert browser.close.call_count == 1
        assert pw.stop.call_count == 1
        pool.render("https://example.com/again")
    assert pw.chromium.launch.call_count == 2


def test_render_returns_none_on_navigation_error():
    sync_playwright_mock, pw, browser, page = _mock_playwright()
    page.goto.side_effect = RuntimeError("timeout")
    pool = BrowserPool(idle_timeout_seconds=1.0)
    with patch("app.fetchers.playwright_pool.sync_playwright", sync_playwright_mock):
        html = pool.render("https://example.com")
    assert html is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_fetcher_playwright_pool.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.fetchers.playwright_pool'`

- [ ] **Step 3: Create `app/fetchers/playwright_pool.py`**

```python
from __future__ import annotations
import queue
import threading
from playwright.sync_api import sync_playwright

DEFAULT_IDLE_TIMEOUT_SECONDS = 10.0
DEFAULT_TIMEOUT_MS = 30000


class BrowserPool:
    """Owns a single headless Chromium instance on a dedicated background
    thread. Playwright's sync API is thread-affine — a browser/page object
    may only be touched from the thread that started it — so this is the one
    thread that ever calls into Playwright; callers on any other thread
    submit a render job and block for the result. The browser is closed
    after `idle_timeout_seconds` with no jobs and relaunched lazily on the
    next call.
    """

    def __init__(self, idle_timeout_seconds: float = DEFAULT_IDLE_TIMEOUT_SECONDS) -> None:
        self._idle_timeout_seconds = idle_timeout_seconds
        self._jobs: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def render(self, url: str, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> str | None:
        self._ensure_thread()
        result_q: queue.Queue = queue.Queue(maxsize=1)
        self._jobs.put((url, timeout_ms, result_q))
        status, payload = result_q.get()
        return payload if status == "ok" else None

    def _ensure_thread(self) -> None:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._worker_loop, daemon=True)
                self._thread.start()

    def _worker_loop(self) -> None:
        pw = None
        browser = None
        while True:
            try:
                url, timeout_ms, result_q = self._jobs.get(timeout=self._idle_timeout_seconds)
            except queue.Empty:
                if browser is not None:
                    browser.close()
                    pw.stop()
                    browser = None
                    pw = None
                continue
            try:
                if browser is None:
                    pw = sync_playwright().start()
                    browser = pw.chromium.launch(headless=True)
                html = self._render_one(browser, url, timeout_ms)
                result_q.put(("ok", html))
            except Exception:
                result_q.put(("error", None))

    def _render_one(self, browser, url: str, timeout_ms: int) -> str:
        ctx = browser.new_context()
        try:
            page = ctx.new_page()
            page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            return page.content()
        finally:
            ctx.close()


_pool = BrowserPool()


def render_html(url: str, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> str | None:
    return _pool.render(url, timeout_ms)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_fetcher_playwright_pool.py -v`
Expected: PASS (4 tests). Note the third test sleeps 0.2s — that's expected, not a hang.

- [ ] **Step 5: Commit**

```bash
git add app/fetchers/playwright_pool.py tests/test_fetcher_playwright_pool.py
git commit -m "feat: add idle-timeout-bounded shared Playwright browser pool"
```

---

### Task 3: Wire fallback into add-by-url

**Files:**
- Modify: `app/routes/jobs.py` (imports, and the `job_add_by_url` stream body around what is currently lines 423-463)
- Modify: `tests/test_routes_jobs.py` (extend the existing JS-only-page test, add two new tests)

**Interfaces:**
- Consumes: `has_enough_content` (Task 1), `render_html` (Task 2).

- [ ] **Step 1: Update the existing JS-only-page test to mock the fallback as failing**

The route will now call `render_html` when content is thin. Without mocking it, this test would try to launch a real browser. In `tests/test_routes_jobs.py`, find `test_add_job_by_url_js_only_page_shows_no_content_notice` (currently around line 1270) and add a patch on `render_html` returning `None` (fallback attempted, still fails):

```python
@respx.mock
def test_add_job_by_url_js_only_page_shows_no_content_notice(client, conn):
    js_shell_html = (
        "<html><body>"
        "<h1>Trener Jobs</h1>"
        "<noscript>You need to enable JavaScript to run this app.</noscript>"
        "</body></html>"
    )
    respx.get("http://example.com/js-app").mock(return_value=httpx.Response(200, text=js_shell_html))

    with patch("app.routes.jobs.detect_listing", return_value=_NOT_A_LISTING), \
         patch("app.routes.jobs.render_html", return_value=None):
        resp = client.post("/jobs/add-by-url", data={"url": "http://example.com/js-app"})

    assert resp.status_code == 200
    assert "NOTICE:warning:" in resp.text
    assert "No job content detected" in resp.text
    assert "load its content dynamically" in resp.text
    assert "JavaScript" in resp.text
    jobs = q.get_jobs(conn)
    assert len(jobs) == 1
    assert jobs[0]["content_type"] == "error"
    assert jobs[0]["status"] == "trash"
    assert jobs[0]["url"] == "http://example.com/js-app"
```

(Only the `with patch(...)` line changed — added `patch("app.routes.jobs.render_html", return_value=None)`.)

Immediately after that test (still before the `_IS_A_LISTING` constant), add two new tests:

```python
@respx.mock
def test_add_job_by_url_js_only_page_playwright_fallback_succeeds(client, conn):
    js_shell_html = (
        "<html><body>"
        "<h1>Trener Jobs</h1>"
        "<noscript>You need to enable JavaScript to run this app.</noscript>"
        "</body></html>"
    )
    respx.get("http://example.com/js-app").mock(return_value=httpx.Response(200, text=js_shell_html))

    with patch("app.routes.jobs.detect_listing", return_value=_NOT_A_LISTING), \
         patch("app.routes.jobs.render_html", return_value=_JOB_POSTING_HTML) as mock_render, \
         patch("app.routes.jobs.run_add_job", side_effect=_fake_run_add_job):
        resp = client.post("/jobs/add-by-url", data={"url": "http://example.com/js-app"})

    mock_render.assert_called_once_with("http://example.com/js-app")
    assert resp.status_code == 200
    assert "Classified as job_posting" in resp.text
    jobs = q.get_jobs(conn)
    assert len(jobs) == 1
    assert jobs[0]["content_type"] == "job_posting"
    assert jobs[0]["url"] == "http://example.com/js-app"


@respx.mock
def test_add_job_by_url_thin_page_not_rendered_when_already_rich(client, conn):
    # Plain HTML already clears the content threshold — render_html must not
    # be called at all, since that's the whole point of trying cheap HTTP first.
    respx.get("http://example.com/job/1").mock(return_value=httpx.Response(200, text=_JOB_POSTING_HTML))

    with patch("app.routes.jobs.detect_listing", return_value=_NOT_A_LISTING), \
         patch("app.routes.jobs.render_html") as mock_render, \
         patch("app.routes.jobs.run_add_job", side_effect=_fake_run_add_job):
        resp = client.post("/jobs/add-by-url", data={"url": "http://example.com/job/1"})

    mock_render.assert_not_called()
    assert resp.status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_routes_jobs.py -k js_only -v`
Expected: FAIL — `AttributeError` / `ImportError`-style failure since `app.routes.jobs.render_html` doesn't exist yet to patch.

- [ ] **Step 3: Wire the fallback into `app/routes/jobs.py`**

Add to the imports near the top (alongside the `app.fetchers.content` import from Task 1):

```python
from app.fetchers.playwright_pool import render_html
```

In `job_add_by_url`'s `stream()`, find the `else:` branch that currently reads (roughly current lines 432-446):

```python
                else:
                    links = extract_links(html, url)
                    detection = detect_listing(client, model, links, url)
                    if detection["is_listing"] and len(detection["job_links"]) >= 2:
```

and insert the fallback check right after `else:`, before `links = extract_links(...)`:

```python
                else:
                    if not has_enough_content(html):
                        rendered = render_html(url)
                        if rendered and has_enough_content(rendered):
                            html = rendered
                    links = extract_links(html, url)
                    detection = detect_listing(client, model, links, url)
                    if detection["is_listing"] and len(detection["job_links"]) >= 2:
```

Nothing else in the function changes — the existing `_extract_text(html)` call further down now operates on the possibly-rendered `html`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_routes_jobs.py -v`
Expected: PASS (all tests, including the 2 new ones)

- [ ] **Step 5: Commit**

```bash
git add app/routes/jobs.py tests/test_routes_jobs.py
git commit -m "feat: fall back to Playwright when add-by-url gets thin HTML"
```

---

### Task 4: Wire fallback into GenericListingFetcher

**Files:**
- Modify: `app/fetchers/generic_listing.py`
- Modify: `tests/test_fetcher_generic_listing.py`

**Interfaces:**
- Consumes: `has_enough_content`, `extract_text` (Task 1), `render_html` (Task 2).

- [ ] **Step 1: Pad the shared `_LISTING_HTML` test fixture so it clears the content threshold**

The existing `_LISTING_HTML` fixture in `tests/test_fetcher_generic_listing.py` is only ~35 characters of extracted text (three short link labels) — under the 200-character threshold this task introduces. Left as-is, every *existing* test in this file would unintentionally trigger the new Playwright fallback with `render_html` unmocked, trying to launch a real browser. Pad it with filler body text (existing anchors unchanged, so `extract_links` still finds the same 3 links):

Replace:

```python
_LISTING_HTML = """<html><body>
<a href="/jobs/1">Senior Engineer</a>
<a href="/jobs/2">Staff Engineer</a>
<a href="/about">About</a>
</body></html>"""
```

with:

```python
_LISTING_HTML = """<html><body>
<a href="/jobs/1">Senior Engineer</a>
<a href="/jobs/2">Staff Engineer</a>
<a href="/about">About</a>
<p>""" + ("We are a fast-growing company building great products. " * 5) + """</p>
</body></html>"""
```

- [ ] **Step 2: Run the existing tests to confirm they still pass with the padded fixture**

Run: `.venv/bin/python -m pytest tests/test_fetcher_generic_listing.py -v`
Expected: PASS (all 4 existing tests — padding the fixture doesn't change link extraction, only its text length)

- [ ] **Step 3: Write the new failing tests**

Add to `tests/test_fetcher_generic_listing.py`, after the existing tests:

```python
from unittest.mock import patch

_THIN_LISTING_HTML = "<html><body><noscript>Enable JavaScript</noscript></body></html>"


@respx.mock
def test_generic_listing_fetcher_falls_back_to_playwright_for_thin_listing_page():
    respx.get("https://example.com/careers").mock(return_value=httpx.Response(200, text=_THIN_LISTING_HTML))
    respx.get("https://example.com/jobs/1").mock(
        return_value=httpx.Response(200, text="<html><body>Senior Engineer role</body></html>")
    )
    client = _client_returning(True, ["https://example.com/jobs/1"])

    with patch("app.fetchers.generic_listing.render_html", return_value=_LISTING_HTML) as mock_render:
        fetcher = GenericListingFetcher(_SOURCE, client, "llama3.2")
        jobs = fetcher.fetch()

    mock_render.assert_called_once_with("https://example.com/careers")
    assert {job.url for job in jobs} == {"https://example.com/jobs/1"}


@respx.mock
def test_generic_listing_fetcher_falls_back_to_playwright_for_thin_detail_page():
    respx.get("https://example.com/careers").mock(return_value=httpx.Response(200, text=_LISTING_HTML))
    respx.get("https://example.com/jobs/1").mock(return_value=httpx.Response(200, text=""))
    client = _client_returning(True, ["https://example.com/jobs/1"])

    rendered_detail_html = "<html><body><p>" + ("Full job description text. " * 10) + "</p></body></html>"
    with patch("app.fetchers.generic_listing.render_html", return_value=rendered_detail_html) as mock_render:
        fetcher = GenericListingFetcher(_SOURCE, client, "llama3.2")
        jobs = fetcher.fetch()

    mock_render.assert_called_once_with("https://example.com/jobs/1")
    assert len(jobs) == 1
    assert jobs[0].url == "https://example.com/jobs/1"
    assert "Full job description" in jobs[0].raw_text


@respx.mock
def test_generic_listing_fetcher_caps_playwright_fallbacks_per_run():
    respx.get("https://example.com/careers").mock(return_value=httpx.Response(200, text=_LISTING_HTML))
    detail_urls = [f"https://example.com/jobs/{i}" for i in range(15)]
    for url in detail_urls:
        respx.get(url).mock(return_value=httpx.Response(200, text=""))
    client = _client_returning(True, detail_urls)

    with patch("app.fetchers.generic_listing.render_html", return_value=None) as mock_render:
        fetcher = GenericListingFetcher(_SOURCE, client, "llama3.2")
        jobs = fetcher.fetch()

    assert mock_render.call_count == 10
    assert jobs == []
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_fetcher_generic_listing.py -v`
Expected: FAIL on the 3 new tests — `AttributeError` since `app.fetchers.generic_listing.render_html` doesn't exist yet to patch.

- [ ] **Step 5: Wire the fallback into `app/fetchers/generic_listing.py`**

Replace the full file contents:

```python
from __future__ import annotations
import httpx
import openai
from app.fetchers.base import Fetcher, RawJob
from app.fetchers.content import has_enough_content, extract_text
from app.fetchers.http import HttpFetcher
from app.fetchers.links import extract_links
from app.fetchers.playwright_pool import render_html
from app.ai.detect_listing import detect_listing

MAX_DETAIL_FETCHES = 50  # cap on new posting detail pages fetched per run
MAX_PLAYWRIGHT_FALLBACKS = 10  # cap on Playwright renders per run, bounds worst-case run time


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

        if not has_enough_content(html):
            rendered = render_html(self._source["url"])
            if rendered and has_enough_content(rendered):
                html = rendered

        links = extract_links(html, self._source["url"])
        result = detect_listing(self._client, self._model, links, self._source["url"])
        job_links = [href for href in result["job_links"] if href not in self._known_urls]

        jobs: list[RawJob] = []
        playwright_fallbacks_used = 0
        for href in job_links[:MAX_DETAIL_FETCHES]:
            page_jobs = HttpFetcher({"url": href}).fetch()
            if not page_jobs and playwright_fallbacks_used < MAX_PLAYWRIGHT_FALLBACKS:
                playwright_fallbacks_used += 1
                rendered = render_html(href)
                if rendered and has_enough_content(rendered):
                    page_jobs = [RawJob(url=href, title="", company="", raw_text=extract_text(rendered))]
            jobs.extend(page_jobs)
        return jobs
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_fetcher_generic_listing.py -v`
Expected: PASS (7 tests total: 4 existing + 3 new)

- [ ] **Step 7: Run the full test suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS, all tests (existing count + 4 content tests + 4 pool tests + 2 jobs tests + 3 generic_listing tests)

- [ ] **Step 8: Commit**

```bash
git add app/fetchers/generic_listing.py tests/test_fetcher_generic_listing.py
git commit -m "feat: fall back to Playwright for thin listing/detail pages in GenericListingFetcher"
```

---

## Manual Verification (after all tasks)

Use the `run-dev-server` skill to start the app against a throwaway DB copy, then:

1. Paste a known JS-rendered job posting URL (e.g. an Ashby posting) into add-by-url and confirm it now gets classified and added instead of landing in Trash.
2. Check server logs/timing — the request should take a few extra seconds (browser launch + render) compared to a plain-HTML URL, but still complete.
3. Add a JS-rendered listing page as a `generic_listing` source (via the add-by-url listing-confirm flow) and trigger a manual fetch from the Sources page; confirm job postings appear instead of an empty result.
4. Stop the dev server once done — the throwaway DB copy is discarded with the worktree.
