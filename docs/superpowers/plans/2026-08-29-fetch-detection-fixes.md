# Fetch Detection Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Fix three defects in the `generic_listing` / site-detection path — `detect_listing` output truncation, missing Playwright escalation for JS-rendered listings, and job-URL dedup defeated by rotating tracking params.

**Architecture:** Extract the duplicated "fetch → thin-check → render → extract links → detect" sequence into one shared helper (`detect_listing_page`) that also escalates to a headless render when the raw HTML yields no usable listing. Switch `detect_listing`'s wire format from full URLs to list indices so responses stop overflowing the token cap. Add a domain-agnostic `canonicalize_url` applied at every point a job URL is dedup'd or stored, plus a one-shot migration for existing rows.

**Tech Stack:** Python 3.12+, FastAPI, SQLite (stdlib `sqlite3`), BeautifulSoup, Playwright (sync API via a single-thread pool), OpenAI-compatible client, pytest + respx.

## Global Constraints

- Python `>=3.12`. No new runtime or dev dependencies.
- Work entirely in the worktree `/home/roman/projects/job-seek/.claude/worktrees/fetch-detection-fixes` (branch `worktree-fetch-detection-fixes`).
- Run tests with `/home/roman/projects/job-seek/.venv/bin/python -m pytest` from the worktree root.
- TDD: write the failing test first, watch it fail, implement, watch it pass, commit. One task = one commit (or a few small ones within the task).
- Commit messages: end with the two trailer lines
  `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_01NU3Mqr4axxsNEVK5oMTARm`.
- Migrations: single hard-downtime migration that assumes the **current** schema shape. No dual-schema shims, no defensive handling of hypothetical older shapes.
- Keep docstrings/comments terse and to the point.
- `detect_listing`'s public return value stays `{"is_listing": bool, "job_links": list[str]}` — only the LLM wire format and internal parsing change.

---

## Task 1: `canonicalize_url` utility

**Files:**
- Create: `app/url_canon.py`
- Test: `tests/test_url_canon.py`

**Interfaces:**
- Consumes: nothing (stdlib `urllib.parse` only).
- Produces: `canonicalize_url(url: str) -> str` — lowercases host, drops fragment, removes tracking query params (any `utm_*` prefix + a fixed exact-name set, matched case-insensitively), keeps all other params in original order, drops an empty query string. Idempotent. Non-`http(s)` or netloc-less input returned unchanged.

- [x] **Step 1: Write the failing tests**

Create `tests/test_url_canon.py`:

```python
from app.url_canon import canonicalize_url


def test_strips_utm_and_click_ids():
    assert canonicalize_url(
        "https://ex.com/jobs/5?utm_source=x&utm_medium=y&gclid=abc&fbclid=def"
    ) == "https://ex.com/jobs/5"


def test_strips_linkedin_tracking_params_case_insensitively():
    url = ("https://no.linkedin.com/jobs/view/ai-engineer-at-acme-123"
           "?position=12&pageNum=0&refId=aB%2Fc&trackingId=xYz&trk=public_jobs")
    assert canonicalize_url(url) == "https://no.linkedin.com/jobs/view/ai-engineer-at-acme-123"


def test_keeps_functional_params_in_original_order():
    url = "https://web106.reachmee.com/ext/I002/1338/job?site=6&lang=NO&validator=abc&job_id=1124"
    assert canonicalize_url(url) == url


def test_keeps_functional_params_when_mixed_with_tracking():
    assert canonicalize_url(
        "https://ex.com/j?job_id=9&utm_campaign=q&page=2"
    ) == "https://ex.com/j?job_id=9&page=2"


def test_lowercases_host_but_not_path():
    assert canonicalize_url("https://Careers.EXAMPLE.com/Jobs/Senior-Dev") == \
        "https://careers.example.com/Jobs/Senior-Dev"


def test_drops_fragment():
    assert canonicalize_url("https://ex.com/jobs#section") == "https://ex.com/jobs"


def test_drops_empty_query():
    assert canonicalize_url("https://ex.com/jobs?") == "https://ex.com/jobs"


def test_idempotent():
    url = "https://ex.com/j?job_id=9&utm_campaign=q&refId=z#frag"
    once = canonicalize_url(url)
    assert canonicalize_url(once) == once


def test_passes_through_non_http():
    assert canonicalize_url("mailto:jobs@ex.com") == "mailto:jobs@ex.com"
    assert canonicalize_url("/relative/path?refId=x") == "/relative/path?refId=x"
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `/home/roman/projects/job-seek/.venv/bin/python -m pytest tests/test_url_canon.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.url_canon'`

- [x] **Step 3: Implement `app/url_canon.py`**

```python
from __future__ import annotations
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

_STRIP_EXACT = frozenset({
    "gclid", "fbclid", "msclkid", "mc_cid", "mc_eid", "_hsenc", "_hsmi",
    "trk", "trackingid", "refid", "position", "pagenum",
    "origintolandingjobpostings", "lipi", "licu", "recommendedflavor",
})


def _keep(name: str) -> bool:
    lower = name.lower()
    return not lower.startswith("utm_") and lower not in _STRIP_EXACT


def canonicalize_url(url: str) -> str:
    """Return a stable form of a job URL for dedup and storage: lowercased host,
    no fragment, tracking query params removed, other params kept in order.
    Non-http(s) or netloc-less input is returned unchanged. Idempotent."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return url
    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if _keep(k)]
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path, urlencode(kept), ""))
```

- [x] **Step 4: Run the tests to verify they pass**

Run: `/home/roman/projects/job-seek/.venv/bin/python -m pytest tests/test_url_canon.py -q`
Expected: PASS (9 passed)

- [x] **Step 5: Commit**

```bash
git add app/url_canon.py tests/test_url_canon.py
git commit -m "feat: add canonicalize_url for job URL dedup"
```

---

## Task 2: `detect_listing_page` shared helper

**Files:**
- Create: `app/fetchers/listing_detect.py`
- Test: `tests/test_listing_detect.py`

**Interfaces:**
- Consumes: `app.fetchers.content.fetch_url_html`, `has_enough_content`, `FetchError`; `app.fetchers.links.extract_links`; `app.fetchers.playwright_pool.render_html`; `app.ai.detect_listing.detect_listing` (current signature: `detect_listing(client, model, links: list[tuple[str,str]], page_url) -> {"is_listing": bool, "job_links": list[str]}`).
- Produces:
  - `ListingDetection` dataclass: `html: str`, `is_listing: bool`, `job_links: list[str]`, `rendered: bool`.
  - `detect_listing_page(client, model, url: str) -> ListingDetection`. Raises `FetchError` if the initial HTTP fetch fails. Escalates to one Playwright render when the raw HTML is thin, or when the raw-HTML detection is not a listing / has `< 2` job links; keeps the rendered result only if it yields strictly more job links.

- [x] **Step 1: Write the failing tests**

Create `tests/test_listing_detect.py`:

```python
from unittest.mock import patch
import pytest
from app.fetchers.content import FetchError
from app.fetchers.listing_detect import detect_listing_page, ListingDetection

_LISTING = "<html><body>" + ("<a href='/j/1'>Dev</a><a href='/j/2'>Ops</a>" * 1) + \
    "<p>" + ("Plenty of real page text here to clear the threshold. " * 6) + "</p></body></html>"
_THIN = "<html><body><noscript>Enable JS</noscript></body></html>"
_BOILERPLATE = "<html><body><nav>Home About Contact</nav><p>" + \
    ("Company blurb with lots of words but no job links at all. " * 6) + "</p></body></html>"


def _dl(is_listing, job_links):
    return {"is_listing": is_listing, "job_links": list(job_links)}


def test_listing_in_raw_html_no_render():
    with patch("app.fetchers.listing_detect.fetch_url_html", return_value=_LISTING), \
         patch("app.fetchers.listing_detect.render_html") as render, \
         patch("app.fetchers.listing_detect.detect_listing",
               return_value=_dl(True, ["https://ex.com/j/1", "https://ex.com/j/2"])):
        out = detect_listing_page(object(), "m", "https://ex.com/careers")
    render.assert_not_called()
    assert out == ListingDetection(_LISTING, True, ["https://ex.com/j/1", "https://ex.com/j/2"], False)


def test_thin_raw_html_escalates_to_render():
    with patch("app.fetchers.listing_detect.fetch_url_html", return_value=_THIN), \
         patch("app.fetchers.listing_detect.render_html", return_value=_LISTING) as render, \
         patch("app.fetchers.listing_detect.detect_listing",
               return_value=_dl(True, ["https://ex.com/j/1", "https://ex.com/j/2"])):
        out = detect_listing_page(object(), "m", "https://ex.com/careers")
    render.assert_called_once_with("https://ex.com/careers")
    assert out.rendered is True and out.html == _LISTING and len(out.job_links) == 2


def test_nonthin_but_no_links_escalates_and_keeps_better_result():
    detect = [_dl(False, []), _dl(True, ["https://ex.com/j/1", "https://ex.com/j/2", "https://ex.com/j/3"])]
    with patch("app.fetchers.listing_detect.fetch_url_html", return_value=_BOILERPLATE), \
         patch("app.fetchers.listing_detect.render_html", return_value=_LISTING) as render, \
         patch("app.fetchers.listing_detect.detect_listing", side_effect=detect):
        out = detect_listing_page(object(), "m", "https://ex.com/careers")
    render.assert_called_once_with("https://ex.com/careers")
    assert out.rendered is True and out.is_listing is True and len(out.job_links) == 3


def test_render_not_better_keeps_raw_result():
    detect = [_dl(True, ["https://ex.com/j/1"]), _dl(True, ["https://ex.com/j/9"])]
    with patch("app.fetchers.listing_detect.fetch_url_html", return_value=_BOILERPLATE), \
         patch("app.fetchers.listing_detect.render_html", return_value=_LISTING), \
         patch("app.fetchers.listing_detect.detect_listing", side_effect=detect):
        out = detect_listing_page(object(), "m", "https://ex.com/careers")
    assert out.rendered is False and out.job_links == ["https://ex.com/j/1"]


def test_fetch_error_propagates():
    with patch("app.fetchers.listing_detect.fetch_url_html", side_effect=FetchError("HTTP 503")):
        with pytest.raises(FetchError):
            detect_listing_page(object(), "m", "https://ex.com/careers")
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `/home/roman/projects/job-seek/.venv/bin/python -m pytest tests/test_listing_detect.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.fetchers.listing_detect'`

- [x] **Step 3: Implement `app/fetchers/listing_detect.py`**

```python
from __future__ import annotations
import logging
from dataclasses import dataclass
import openai
from app.fetchers.content import fetch_url_html, has_enough_content
from app.fetchers.links import extract_links
from app.fetchers.playwright_pool import render_html
from app.ai.detect_listing import detect_listing

logger = logging.getLogger("job_seek")

MIN_JOB_LINKS = 2  # a raw-HTML "listing" with fewer links than this is worth a render


@dataclass
class ListingDetection:
    html: str
    is_listing: bool
    job_links: list[str]
    rendered: bool


def _detect(client: openai.OpenAI, model: str, html: str, url: str) -> dict:
    return detect_listing(client, model, extract_links(html, url), url)


def detect_listing_page(client: openai.OpenAI, model: str, url: str) -> ListingDetection:
    """Fetch ``url`` and classify it as a job listing (or not), returning the
    individual job-posting links found.

    Escalates to one headless-browser render when the raw HTML is thin, or when
    the raw-HTML detection is not a listing or has fewer than ``MIN_JOB_LINKS``
    links — many careers pages are JS-rendered and their raw HTML carries enough
    boilerplate to look non-thin while containing no job links. The rendered
    result is kept only if it yields strictly more job links.

    Raises ``FetchError`` if the initial HTTP fetch fails.
    """
    html = fetch_url_html(url)
    render_tried = False

    if not has_enough_content(html):
        rendered = render_html(url)
        render_tried = True
        if rendered and has_enough_content(rendered):
            r = _detect(client, model, rendered, url)
            return ListingDetection(rendered, r["is_listing"], r["job_links"], True)

    result = _detect(client, model, html, url)

    if not result["is_listing"] or len(result["job_links"]) < MIN_JOB_LINKS:
        rendered = None if render_tried else render_html(url)
        if rendered and has_enough_content(rendered):
            alt = _detect(client, model, rendered, url)
            if len(alt["job_links"]) > len(result["job_links"]):
                return ListingDetection(rendered, alt["is_listing"], alt["job_links"], True)

    return ListingDetection(html, result["is_listing"], result["job_links"], False)
```

- [x] **Step 4: Run the tests to verify they pass**

Run: `/home/roman/projects/job-seek/.venv/bin/python -m pytest tests/test_listing_detect.py -q`
Expected: PASS (5 passed)

- [x] **Step 5: Commit**

```bash
git add app/fetchers/listing_detect.py tests/test_listing_detect.py
git commit -m "feat: add detect_listing_page helper with render escalation"
```

---

## Task 3: Rewire `GenericListingFetcher` onto the helper

**Files:**
- Modify: `app/fetchers/generic_listing.py`
- Test: `tests/test_fetcher_generic_listing.py`

**Interfaces:**
- Consumes: `detect_listing_page` / `ListingDetection` from Task 2.
- Produces: `GenericListingFetcher.fetch()` unchanged signature (`-> list[RawJob]`). Listing-level fetch + render + detection now delegated to `detect_listing_page`; detail-page fetching and the `MAX_PLAYWRIGHT_FALLBACKS` detail-render cap stay in this file.

- [x] **Step 1: Update the tests**

In `tests/test_fetcher_generic_listing.py`:

- Delete `_client_returning` and the `import json` if now unused (keep `from unittest.mock import MagicMock, patch`).
- Add:

```python
from app.fetchers.listing_detect import ListingDetection

def _detection(is_listing, job_links, html="<html><body>ok</body></html>", rendered=False):
    return ListingDetection(html, is_listing, list(job_links), rendered)
```

- Rewrite each test to patch `app.fetchers.generic_listing.detect_listing_page` instead of building an LLM client. The `respx` mocks for the **listing** URL go away (the helper is stubbed); `respx` mocks for **detail** URLs stay. The fetcher no longer takes a real client/model — pass `MagicMock(), "m"` positionally as today (`GenericListingFetcher(_SOURCE, MagicMock(), "m", ...)`), they are just handed to the helper which is stubbed.

Concretely:

```python
@respx.mock
def test_generic_listing_fetcher_returns_raw_jobs_for_detected_links():
    respx.get("https://example.com/jobs/1").mock(return_value=httpx.Response(200, text=_JOB_DETAIL_HTML))
    respx.get("https://example.com/jobs/2").mock(return_value=httpx.Response(200, text=_JOB_DETAIL_HTML))
    with patch("app.fetchers.generic_listing.detect_listing_page",
               return_value=_detection(True, ["https://example.com/jobs/1", "https://example.com/jobs/2"])):
        jobs = GenericListingFetcher(_SOURCE, MagicMock(), "m").fetch()
    assert {j.url for j in jobs} == {"https://example.com/jobs/1", "https://example.com/jobs/2"}


@respx.mock
def test_generic_listing_fetcher_skips_known_urls():
    respx.get("https://example.com/jobs/2").mock(return_value=httpx.Response(200, text=_JOB_DETAIL_HTML))
    with patch("app.fetchers.generic_listing.detect_listing_page",
               return_value=_detection(True, ["https://example.com/jobs/1", "https://example.com/jobs/2"])):
        jobs = GenericListingFetcher(
            _SOURCE, MagicMock(), "m", known_urls=frozenset({"https://example.com/jobs/1"})
        ).fetch()
    assert {j.url for j in jobs} == {"https://example.com/jobs/2"}


def test_generic_listing_fetcher_returns_empty_when_not_a_listing():
    with patch("app.fetchers.generic_listing.detect_listing_page", return_value=_detection(False, [])):
        assert GenericListingFetcher(_SOURCE, MagicMock(), "m").fetch() == []


def test_generic_listing_fetcher_returns_empty_on_listing_fetch_failure(caplog):
    from app.fetchers.content import FetchError
    with patch("app.fetchers.generic_listing.detect_listing_page", side_effect=FetchError("HTTP 503")), \
         caplog.at_level("WARNING", logger="job_seek"):
        jobs = GenericListingFetcher(_SOURCE, MagicMock(), "m").fetch()
    assert jobs == []
    assert any("Careers" in r.message and "https://example.com/careers" in r.message and "503" in r.message
               for r in caplog.records)
```

- Delete `test_generic_listing_fetcher_handles_fetch_failure`, `test_generic_listing_fetcher_logs_exception_on_listing_fetch_failure`, `test_generic_listing_fetcher_logs_warning_on_non_200_listing_response`, and `test_generic_listing_fetcher_falls_back_to_playwright_for_thin_listing_page` — all now covered by `tests/test_listing_detect.py` and the single `..._returns_empty_on_listing_fetch_failure` above.
- Keep, unchanged except for the patch target rewrite to `detect_listing_page`:
  `test_generic_listing_fetcher_falls_back_to_playwright_for_thin_detail_page`,
  `test_generic_listing_fetcher_falls_back_when_detail_page_returns_nonempty_but_thin_content`,
  `test_generic_listing_fetcher_caps_playwright_fallbacks_per_run` — these patch `app.fetchers.generic_listing.render_html` (the **detail-page** render, still in this file) and now also patch `detect_listing_page` to return the detail URLs, e.g.:

```python
@respx.mock
def test_generic_listing_fetcher_falls_back_to_playwright_for_thin_detail_page():
    respx.get("https://example.com/jobs/1").mock(return_value=httpx.Response(200, text=""))
    rendered_detail = "<html><body><p>" + ("Full job description text. " * 10) + "</p></body></html>"
    with patch("app.fetchers.generic_listing.detect_listing_page",
               return_value=_detection(True, ["https://example.com/jobs/1"])), \
         patch("app.fetchers.generic_listing.render_html", return_value=rendered_detail) as mock_render:
        jobs = GenericListingFetcher(_SOURCE, MagicMock(), "m").fetch()
    mock_render.assert_called_once_with("https://example.com/jobs/1")
    assert len(jobs) == 1 and "Full job description" in jobs[0].raw_text
```

Apply the same shape (patch `detect_listing_page` with the detail URLs, keep the `render_html` patch) to the other two retained tests.

- [x] **Step 2: Run the tests to verify they fail**

Run: `/home/roman/projects/job-seek/.venv/bin/python -m pytest tests/test_fetcher_generic_listing.py -q`
Expected: FAIL — `AttributeError: <module 'app.fetchers.generic_listing'> does not have the attribute 'detect_listing_page'`

- [x] **Step 3: Rewrite `app/fetchers/generic_listing.py`**

Replace the whole file with:

```python
from __future__ import annotations
import logging
import openai
from app.fetchers.base import RawJob
from app.fetchers.content import has_enough_content, has_enough_text, extract_text, FetchError
from app.fetchers.http import HttpFetcher
from app.fetchers.listing_detect import detect_listing_page
from app.fetchers.playwright_pool import render_html

logger = logging.getLogger("job_seek")

MAX_DETAIL_FETCHES = 50        # cap on new posting detail pages fetched per run
MAX_PLAYWRIGHT_FALLBACKS = 10  # cap on detail-page Playwright renders per run


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
            detection = detect_listing_page(self._client, self._model, self._source["url"])
        except FetchError as exc:
            logger.warning(
                "generic_listing: could not fetch listing page %s for '%s': %s",
                self._source["url"], self._source["name"], exc,
            )
            return []
        except Exception:
            logger.exception(
                "generic_listing: detection failed for '%s' (%s)",
                self._source["name"], self._source["url"],
            )
            return []

        job_links = [h for h in detection.job_links if h not in self._known_urls]

        jobs: list[RawJob] = []
        playwright_fallbacks_used = 0
        for href in job_links[:MAX_DETAIL_FETCHES]:
            page_jobs = HttpFetcher({"url": href}).fetch()
            is_thin = not page_jobs or not has_enough_text(page_jobs[0].raw_text)
            if is_thin and playwright_fallbacks_used < MAX_PLAYWRIGHT_FALLBACKS:
                playwright_fallbacks_used += 1
                rendered = render_html(href)
                if rendered and has_enough_content(rendered):
                    page_jobs = [RawJob(url=href, title="", company="", raw_text=extract_text(rendered))]
            jobs.extend(page_jobs)
        return jobs
```

- [x] **Step 4: Run the tests to verify they pass**

Run: `/home/roman/projects/job-seek/.venv/bin/python -m pytest tests/test_fetcher_generic_listing.py tests/test_listing_detect.py -q`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add app/fetchers/generic_listing.py tests/test_fetcher_generic_listing.py
git commit -m "refactor: generic_listing uses detect_listing_page helper"
```

---

## Task 4: Rewire the two route detection call sites onto the helper

**Files:**
- Modify: `app/routes/sources.py` (`_task_source_detect`, imports)
- Modify: `app/routes/jobs.py` (`_task_job_add_by_url`, imports)
- Test: `tests/test_routes_sources.py`, `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: `detect_listing_page` / `ListingDetection` from Task 2.
- Produces: no change to route behavior or template context keys. `_task_source_detect` still yields the confirm/mismatch panel; `_task_job_add_by_url` still returns the listing-confirm panel or adds the job.

- [x] **Step 1: Update `app/routes/sources.py`**

Imports — replace lines 10-14:

```python
from app.fetchers.content import extract_page_title, FetchError
from app.ai.classify_known_source import classify_known_source, DETECTABLE_FETCHER_TYPES
from app.ai.generate_source_name import generate_source_name
from app.fetchers.listing_detect import detect_listing_page
```

(Removed: `app.fetchers.links.extract_links`, `has_enough_content`, `fetch_url_html`, `render_html`, `app.ai.detect_listing.detect_listing`. Keep the `detect_listing` import ONLY if still referenced elsewhere — it is not.)

In `_task_source_detect`, replace the `if fetcher_type is None:` body (currently lines ~111-129):

```python
    if fetcher_type is None:
        yield "Checking the page..."
        try:
            detection = detect_listing_page(client, model, url)
        except FetchError:
            fetcher_type = "generic_listing"
        else:
            page_title = extract_page_title(detection.html)
            if page_title:
                generated_name = generate_source_name(client, model, default_name, page_title)
                if generated_name:
                    default_name = generated_name
            if detection.is_listing and len(detection.job_links) >= 2:
                fetcher_type = "generic_listing"
```

- [x] **Step 2: Update `app/routes/jobs.py`**

Imports — replace lines 9-14:

```python
from app.fetchers.content import NoContentError, extract_text_or_raise, extract_page_title, FetchError
from app.fetchers.listing_detect import detect_listing_page
from app.ai.classify_known_source import classify_known_source, DETECTABLE_FETCHER_TYPES
from app.ai.generate_source_name import generate_source_name
```

(Removed: `app.fetchers.links.extract_links`, `has_enough_content`, `fetch_url_html`, `render_html`, `app.ai.detect_listing.detect_listing`.)

In `_task_job_add_by_url`, replace the `try: html = fetch_url_html(url) ... detection = detect_listing(...)` block (currently lines ~617-633) so the `try` calls the helper and the listing branch reads from `detection`:

```python
            try:
                detection = detect_listing_page(client, model, url)
            except FetchError as exc:
                _insert_error_job(conn, url, f"Failed to fetch: {exc}")
                notices.append({
                    "level": "warning",
                    "html": templates.get_template("jobs/_fetch_failed_notice.html").render(
                        request=None, url=url, reason=str(exc),
                    ),
                })
            else:
                html = detection.html
                if detection.is_listing and len(detection.job_links) >= 2:
                    domain = urlsplit(url).netloc
                    default_name = domain
                    page_title = extract_page_title(html)
                    if page_title:
                        generated_name = generate_source_name(client, model, domain, page_title)
                        if generated_name:
                            default_name = generated_name
                    panel_context = {
                        "request": None, "url": url,
                        "fetcher_type": classify_known_source(url) or "generic_listing",
                        "link_count": len(detection.job_links), "domain": domain,
                        "default_name": default_name,
                    }
                    panel_context.update(params.get("filter_ctx", {}))
                    panel = templates.get_template("jobs/_listing_confirm.html").render(**panel_context)
                    html_chunks.append(panel)
                    result["needs_action"] = True
                    result["action_message"] = f"'{default_name}' looks like a job listing — confirm how to add it"
                    result["resume_html"] = panel
                    return result
                try:
                    raw_text = extract_text_or_raise(html)
                except NoContentError:
                    ...
```

(The `except NoContentError:` block and everything after it are unchanged.)

- [x] **Step 3: Update `tests/test_routes_sources.py`**

Add near the top:

```python
from app.fetchers.listing_detect import ListingDetection

def _listing(html="<html><body>ok</body></html>", job_links=("https://careers.example.com/jobs/1",
                                                              "https://careers.example.com/jobs/2")):
    return ListingDetection(html, True, list(job_links), False)

def _not_listing(html="<html><body><p>A single job posting.</p></body></html>"):
    return ListingDetection(html, False, [], False)
```

Then, per test: replace `patch("app.routes.sources.detect_listing", return_value=_IS_A_LISTING)` with
`patch("app.routes.sources.detect_listing_page", return_value=_listing(html=<the HTML that test's respx served>))`,
and `..._NOT_A_LISTING` with `..._not_listing(html=<...>)`. The `respx.get(...)` mocks that only fed the listing URL can be deleted (helper is stubbed). Specifics:

- `test_detect_source_slow_path_listing_detected` → `_listing(html="<html><body><a href='/jobs/1'>A</a><a href='/jobs/2'>B</a></body></html>")`.
- `test_detect_source_mismatch_panel_has_cancel_link`, `test_detect_source_slow_path_not_a_listing_shows_mismatch` → `_not_listing()`.
- `test_detect_source_uses_generated_name_when_page_has_title` → `_listing(html="<html><head><title>Frontend Developer Jobs in Oslo | Careers</title></head><body></body></html>")`; the `generate_source_name` patch and `mock_gen.assert_called_once_with(ANY, ANY, "careers.example.com", "Frontend Developer Jobs in Oslo | Careers")` stay.
- `test_detect_source_falls_back_to_domain_when_name_generation_fails` → `_listing(html="<html><head><title>Frontend Developer Jobs</title></head><body></body></html>")`.
- `test_detect_source_mismatch_panel_also_uses_generated_name` → `_not_listing(html="<html><head><title>Senior Engineer at Acme</title></head><body><p>Role details.</p></body></html>")`.
- `test_detect_source_fetch_failure_falls_back_to_generic_listing` — **unchanged**: it relies on `respx` raising `ConnectError`, which `fetch_url_html` inside the helper turns into `FetchError`, which propagates and the route catches. Keep as-is (do not patch the helper).
- `_IS_A_LISTING` / `_NOT_A_LISTING` module constants: delete once no longer referenced.

- [x] **Step 4: Update `tests/test_routes_jobs.py`**

Add near the existing `_NOT_A_LISTING` (line ~1637):

```python
from app.fetchers.listing_detect import ListingDetection

def _not_listing(html=_JOB_POSTING_HTML):
    return ListingDetection(html, False, [], False)

def _listing(job_links, html="<html><body>listing</body></html>"):
    return ListingDetection(html, True, list(job_links), False)
```

Transformation rule for every test in this file that patches `app.routes.jobs.detect_listing`:
- `patch("app.routes.jobs.detect_listing", return_value=_NOT_A_LISTING)` → `patch("app.routes.jobs.detect_listing_page", return_value=_not_listing())`
- `patch("app.routes.jobs.detect_listing", return_value=_IS_A_LISTING)` → `patch("app.routes.jobs.detect_listing_page", return_value=_listing(_IS_A_LISTING["job_links"]))`
- `patch("app.routes.jobs.detect_listing", return_value={"is_listing": True, "job_links": [...]})` → `_listing([...])`
- Any accompanying `patch("app.routes.jobs.render_html", ...)` is removed (the render now happens inside the stubbed helper) **unless** the test's intent is specifically the route-level no-content path, in which case pass the HTML through `_not_listing(html=...)` instead.
- `patch("app.routes.jobs.detect_listing", return_value={"is_listing": True, "job_links": ["one"]})` for the "only one link → not treated as listing" test → `_listing(["http://example.com/job/1"])` (the route's own `len(...) >= 2` check still applies).

Affected test functions (apply the rule to each; run the suite to confirm none missed):
`test_add_job_by_url_success_inserts_job_and_streams_progress`,
`test_add_job_by_url_job_posting_passed_gate_shows_notice`,
`test_add_job_by_url_error_shows_persistent_notice`,
`test_add_job_by_url_irrelevant_shows_discarded_notice`,
and every other test between lines ~1660 and ~2100 that patches `app.routes.jobs.detect_listing` or `app.routes.jobs.render_html` (the grep list: 1670, 1710, 1723, 1738, 1751, 1765, 1872-73, 1898-99, 1917-18, 1937, 1956-58, 2066, 2076-77, 2091). For the two that assert render behavior at the route (`...render_html", return_value=_JOB_POSTING_HTML`) and (`...render_html") as mock_render` + `mock_render.assert_not_called()`), delete the render assertion — that behavior is now `tests/test_listing_detect.py`'s; keep only the job-add outcome assertions with `_not_listing(html=_JOB_POSTING_HTML)`.

- [x] **Step 5: Run the tests to verify they pass**

Run: `/home/roman/projects/job-seek/.venv/bin/python -m pytest tests/test_routes_sources.py tests/test_routes_jobs.py -q`
Expected: PASS. Investigate any residual failure — most likely a missed `detect_listing` → `detect_listing_page` patch, or an HTML fixture that needs to move into the `ListingDetection.html`.

- [x] **Step 6: Full suite check**

Run: `/home/roman/projects/job-seek/.venv/bin/python -m pytest -q`
Expected: PASS (Task 5 has not landed yet, so `detect_listing` still uses the old format and `tests/test_detect_listing.py` still passes).

- [x] **Step 7: Commit**

```bash
git add app/routes/sources.py app/routes/jobs.py tests/test_routes_sources.py tests/test_routes_jobs.py
git commit -m "refactor: route detection uses detect_listing_page helper"
```

---

## Task 5: Index-based `detect_listing` output (Fix A)

**Files:**
- Modify: `app/ai/detect_listing.py`
- Test: `tests/test_detect_listing.py`

**Interfaces:**
- Consumes: `app.ai.json_utils.extract_json` (unchanged).
- Produces: `detect_listing(client, model, links, page_url) -> {"is_listing": bool, "job_links": list[str]}` — **same return shape**. Internally: prompt now sends a numbered list `N. <anchor> [<url>]`; model returns `{"is_listing": bool, "job_ids": [int, ...]}`; indices are mapped back to `links[n][0]`, out-of-range ignored. `max_tokens` 2000→4000, input cap `[:8000]`→`[:16000]`. A parse failure logs `logger.warning` with the raw content and returns the empty result.

- [x] **Step 1: Update the tests**

Rewrite `tests/test_detect_listing.py`:

```python
import json
from unittest.mock import MagicMock
from app.ai.detect_listing import _SYSTEM, detect_listing


def _client_with_response(content) -> MagicMock:
    client = MagicMock()
    choice = MagicMock()
    choice.message.content = content
    client.chat.completions.create.return_value = MagicMock(choices=[choice])
    return client


_LINKS = [
    ("https://example.com/jobs/1", "Senior Engineer"),
    ("https://example.com/jobs/2", "Staff Engineer"),
    ("https://example.com/about", "About us"),
]


def test_detect_listing_maps_job_ids_to_hrefs():
    client = _client_with_response(json.dumps({"is_listing": True, "job_ids": [0, 1]}))
    result = detect_listing(client, "m", _LINKS, "https://example.com/careers")
    assert result == {
        "is_listing": True,
        "job_links": ["https://example.com/jobs/1", "https://example.com/jobs/2"],
    }


def test_detect_listing_ignores_out_of_range_indices():
    client = _client_with_response(json.dumps({"is_listing": True, "job_ids": [0, 99]}))
    result = detect_listing(client, "m", _LINKS[:1], "https://example.com/careers")
    assert result == {"is_listing": True, "job_links": ["https://example.com/jobs/1"]}


def test_detect_listing_not_a_listing():
    client = _client_with_response(json.dumps({"is_listing": False, "job_ids": []}))
    result = detect_listing(client, "m", _LINKS, "https://example.com/jobs/1")
    assert result == {"is_listing": False, "job_links": []}


def test_detect_listing_sends_numbered_list_with_urls():
    client = _client_with_response(json.dumps({"is_listing": False, "job_ids": []}))
    detect_listing(client, "m", _LINKS, "https://example.com/careers")
    user_msg = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    assert "0. Senior Engineer [https://example.com/jobs/1]" in user_msg
    assert "1. Staff Engineer [https://example.com/jobs/2]" in user_msg


def test_detect_listing_fails_open_on_llm_exception():
    client = MagicMock()
    client.chat.completions.create.side_effect = RuntimeError("connection refused")
    result = detect_listing(client, "m", _LINKS, "https://example.com")
    assert result == {"is_listing": False, "job_links": []}


def test_detect_listing_logs_warning_on_unparseable_response(caplog):
    client = _client_with_response("this is not json")
    with caplog.at_level("WARNING", logger="job_seek"):
        result = detect_listing(client, "m", _LINKS, "https://example.com/careers")
    assert result == {"is_listing": False, "job_links": []}
    assert any("unparseable" in r.message and "https://example.com/careers" in r.message
               for r in caplog.records)


def test_detect_listing_prompt_warns_against_role_location_category_pages():
    client = _client_with_response(json.dumps({"is_listing": False, "job_ids": []}))
    detect_listing(client, "m", _LINKS, "https://example.com")
    sent_system = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert sent_system == _SYSTEM
    assert "role" in _SYSTEM and "location" in _SYSTEM
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `/home/roman/projects/job-seek/.venv/bin/python -m pytest tests/test_detect_listing.py -q`
Expected: FAIL (`test_detect_listing_maps_job_ids_to_hrefs`, `..._sends_numbered_list_with_urls`, `..._logs_warning_on_unparseable_response` fail against the current URL-based implementation).

- [x] **Step 3: Rewrite `app/ai/detect_listing.py`**

```python
from __future__ import annotations
import json
import logging
import openai
from app.ai.json_utils import extract_json

logger = logging.getLogger("job_seek")

_SYSTEM = (
    "You look at a numbered list of links extracted from a web page and decide "
    "whether the page is a listing of multiple individual job postings (e.g. a "
    "careers page or job board search results), as opposed to a single job "
    "posting, an article, or an unrelated page. Each line is "
    "\"<n>. <anchor text> [<url>]\". "
    "If it is a listing, identify which links point to individual job postings "
    "(not navigation, filters, pagination, login, or unrelated content). "
    "A link to an individual job posting names a specific role at a specific "
    "employer. Be wary of links that only name a role and a location (e.g. "
    "\"Machine Learning Engineer Jobs in Oslo\", or a URL like "
    "\"/jobs/ai-engineer-jobs-in-oslo\") with no employer attached — these are "
    "category or search-filter pages that themselves list postings for that "
    "role, not a specific posting, even though they look job-like. A big block "
    "of near-identical links, one per job title or seniority level, is a strong "
    "sign of a role/category filter list rather than individual postings. "
    "Respond with exactly: "
    "{\"is_listing\": <true|false>, \"job_ids\": [<n>, ...]} "
    "where each <n> is the leading number of a link that points to an "
    "individual job posting."
)


def detect_listing(
    client: openai.OpenAI,
    model: str,
    links: list[tuple[str, str]],
    page_url: str,
) -> dict:
    link_lines = "\n".join(f"{i}. {text} [{href}]" for i, (href, text) in enumerate(links))
    user_message = f"Page: {page_url}\n\nLinks:\n{link_lines}"
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user_message[:16000]},
            ],
            temperature=0,
            max_tokens=4000,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
    except Exception:
        logger.warning("detect_listing: LLM call failed for %s", page_url, exc_info=True)
        return {"is_listing": False, "job_links": []}

    content = resp.choices[0].message.content
    try:
        data = json.loads(extract_json(content))
    except (ValueError, TypeError):
        logger.warning("detect_listing: unparseable response for %s: %r", page_url, content)
        return {"is_listing": False, "job_links": []}

    is_listing = bool(data.get("is_listing", False))
    job_links = [
        links[n][0]
        for n in data.get("job_ids", [])
        if isinstance(n, int) and not isinstance(n, bool) and 0 <= n < len(links)
    ]
    return {"is_listing": is_listing, "job_links": job_links}
```

- [x] **Step 4: Run the tests to verify they pass**

Run: `/home/roman/projects/job-seek/.venv/bin/python -m pytest tests/test_detect_listing.py -q`
Expected: PASS (7 passed)

- [x] **Step 5: Full suite check**

Run: `/home/roman/projects/job-seek/.venv/bin/python -m pytest -q`
Expected: PASS — Tasks 2-4 insulated their callers/tests from the wire format (helper tests patch `detect_listing`; fetcher and route tests patch `detect_listing_page`).

- [x] **Step 6: Commit**

```bash
git add app/ai/detect_listing.py tests/test_detect_listing.py
git commit -m "fix: detect_listing returns link indices to avoid token-cap truncation"
```

---

## Task 6: Apply `canonicalize_url` at dedup / storage points (Fix C wiring)

**Files:**
- Modify: `app/db/queries.py` (`get_all_job_urls`)
- Modify: `app/pipeline.py` (`run_fetch` ingest loop, `run_add_job`)
- Modify: `app/fetchers/generic_listing.py`, `app/fetchers/finn.py`, `app/fetchers/slack.py` (canonicalize candidate hrefs before the `known_urls` check and on emitted `RawJob.url`)
- Test: `tests/test_pipeline.py`, and one new test in `tests/test_fetcher_generic_listing.py`

**Interfaces:**
- Consumes: `canonicalize_url` from Task 1.
- Produces: every `jobs.url` written by the pipeline, and every URL in the `get_all_job_urls` set, is canonical. Fetchers compare canonical hrefs to `known_urls` and emit canonical `RawJob.url`.

- [x] **Step 1: Write the failing tests**

In `tests/test_pipeline.py`, add (adapt imports/fixtures to the file's existing style — it already builds sources and a fake fetcher):

```python
def test_run_fetch_stores_canonical_job_urls(conn):
    # a fetcher that yields a URL with tracking params
    from app.fetchers.base import RawJob
    from app import pipeline

    src = _make_source(conn, fetcher_type="generic_listing")  # use the file's existing helper

    class _F:
        def fetch(self):
            return [RawJob(url="https://ex.com/jobs/9?refId=abc&utm_source=x",
                           title="", company="", raw_text="x" * 300)]

    with patch.object(pipeline, "_make_fetcher", return_value=_F()), \
         patch.object(pipeline, "_ingest_posting", return_value=iter(())):
        list(pipeline.run_fetch(q.get_source(conn, src), conn, MagicMock(), "m", "/tmp"))

    assert q.get_all_job_urls(conn) == frozenset({"https://ex.com/jobs/9"})


def test_run_fetch_dedups_against_canonical_known_url(conn):
    from app.fetchers.base import RawJob
    from app import pipeline

    src = _make_source(conn, fetcher_type="generic_listing")
    q.insert_job(conn, source_id=src, url="https://ex.com/jobs/9", title="", company="", raw_text="x" * 300)

    class _F:
        def fetch(self):
            return [RawJob(url="https://ex.com/jobs/9?trackingId=zzz",
                           title="", company="", raw_text="x" * 300)]

    with patch.object(pipeline, "_make_fetcher", return_value=_F()), \
         patch.object(pipeline, "_ingest_posting", return_value=iter(())) as ingest:
        list(pipeline.run_fetch(q.get_source(conn, src), conn, MagicMock(), "m", "/tmp"))

    ingest.assert_not_called()
```

(If `tests/test_pipeline.py` has no `_make_source` helper, inline `q.insert_source(conn, "S", "https://ex.com", "generic_listing")` and `q.get_source`.)

In `tests/test_fetcher_generic_listing.py` add:

```python
@respx.mock
def test_generic_listing_fetcher_canonicalizes_detail_urls():
    respx.get("https://example.com/jobs/1").mock(return_value=httpx.Response(200, text=_JOB_DETAIL_HTML))
    with patch("app.fetchers.generic_listing.detect_listing_page",
               return_value=_detection(True, ["https://example.com/jobs/1?refId=abc"])):
        jobs = GenericListingFetcher(_SOURCE, MagicMock(), "m").fetch()
    assert [j.url for j in jobs] == ["https://example.com/jobs/1"]
```

- [x] **Step 2: Run to verify they fail**

Run: `/home/roman/projects/job-seek/.venv/bin/python -m pytest tests/test_pipeline.py::test_run_fetch_stores_canonical_job_urls tests/test_fetcher_generic_listing.py::test_generic_listing_fetcher_canonicalizes_detail_urls -q`
Expected: FAIL (URLs stored/returned with the tracking params still attached).

- [x] **Step 3: Implement the wiring**

`app/db/queries.py` — `get_all_job_urls`:

```python
from app.url_canon import canonicalize_url  # add to imports at top of file

def get_all_job_urls(conn: sqlite3.Connection) -> frozenset[str]:
    rows = conn.execute("SELECT url FROM jobs").fetchall()
    return frozenset(canonicalize_url(r["url"]) for r in rows)
```

`app/pipeline.py` — add `from app.url_canon import canonicalize_url` to imports. In `run_fetch`, at the top of the `for i, raw in enumerate(...)` loop:

```python
        for i, raw in enumerate(raw_jobs, start=1):
            raw_url = canonicalize_url(raw.url)
            if q.url_exists(conn, raw_url):
                yield _progress(f"[{i}/{jobs_found}] Skipping duplicate: {raw_url}")
                continue
            job_id = q.insert_job(
                conn,
                source_id=source["id"],
                url=raw_url,
                title=raw.title,
                company=raw.company,
                raw_text=raw.raw_text,
                published_at=raw.published_at,
            )
            ...
            yield from _ingest_posting(
                conn, client, model, job_id, raw.raw_text, raw.title, is_slack, profile, scenarios,
                url=raw_url, progress_prefix=f"[{i}/{jobs_found}] ",
            )
```

In `run_add_job`, first line of the body:

```python
    url = canonicalize_url(url)
    job_id = q.insert_job(conn, source_id=source_id, url=url, title="", company="", raw_text=raw_text)
```

`app/fetchers/generic_listing.py` — add `from app.url_canon import canonicalize_url`, and canonicalize in the filter:

```python
        job_links = [
            c for h in detection.job_links
            if (c := canonicalize_url(h)) not in self._known_urls
        ]
```

(The loop then iterates canonical URLs; `HttpFetcher({"url": href})` fetches the canonical URL and `RawJob.url` is already canonical.)

`app/fetchers/finn.py` — add the import; in `fetch`:

```python
        ads = self._discover_ads()
        new_ads = [
            (c, pub) for url, pub in ads
            if (c := canonicalize_url(url)) not in self._known_urls
        ]
```

`app/fetchers/slack.py` — add the import; at line ~177 change:

```python
                canon = canonicalize_url(job.url)
                if canon in self._known_urls:
                    continue
                job.url = canon
```

- [x] **Step 4: Run the tests to verify they pass**

Run: `/home/roman/projects/job-seek/.venv/bin/python -m pytest tests/test_pipeline.py tests/test_fetcher_generic_listing.py tests/test_fetcher_finn.py tests/test_fetcher_slack.py -q`
Expected: PASS. Fix any incidental URL-string assertions in these files to the canonical form.

- [x] **Step 5: Full suite check**

Run: `/home/roman/projects/job-seek/.venv/bin/python -m pytest -q`
Expected: PASS

- [x] **Step 6: Commit**

```bash
git add app/db/queries.py app/pipeline.py app/fetchers/generic_listing.py app/fetchers/finn.py app/fetchers/slack.py tests/
git commit -m "fix: canonicalize job URLs at dedup and storage points"
```

---

## Task 7: One-shot migration for existing job URLs

**Files:**
- Modify: `app/db/schema.py` (new `_migrate_jobs_canonicalize_urls`, register in `init_db`)
- Test: `tests/test_schema.py`

**Interfaces:**
- Consumes: `canonicalize_url` from Task 1.
- Produces: `_migrate_jobs_canonicalize_urls(conn)` — idempotent. Rewrites every `jobs.url` to canonical form; when several rows canonicalize to the same value, keeps the lowest `id` and deletes the rest (cascades to `job_scores` / `scenario_feedback` via existing `ON DELETE CASCADE`, FK enforcement is already on). Runs on every `init_db`.

- [x] **Step 1: Write the failing test**

In `tests/test_schema.py`:

```python
from app.db.schema import _migrate_jobs_canonicalize_urls


def test_migrate_canonicalizes_job_urls_and_collapses_collisions(conn):
    init_db(conn)
    sid = conn.execute(
        "INSERT INTO sources (name, url, fetcher_type) VALUES ('S', 'https://ex.com', 'generic_listing')"
    ).lastrowid
    # row 1: clean canonical form; row 2: same job with tracking params (collision, higher id -> dropped)
    conn.execute("INSERT INTO jobs (id, source_id, url, title, company, raw_text) "
                 "VALUES (1, ?, 'https://ex.com/j/9', '', '', 'x')", (sid,))
    conn.execute("INSERT INTO jobs (id, source_id, url, title, company, raw_text) "
                 "VALUES (2, ?, 'https://ex.com/j/9?refId=abc', '', '', 'x')", (sid,))
    # row 3: only needs rewriting, no collision
    conn.execute("INSERT INTO jobs (id, source_id, url, title, company, raw_text) "
                 "VALUES (3, ?, 'https://ex.com/j/7?utm_source=x#top', '', '', 'x')", (sid,))
    conn.execute("INSERT INTO job_scores (job_id, scenario_id, score, reasoning, scenario_version_hash) "
                 "VALUES (2, 1, 0.5, 'r', 'h')")  # child of the row that will be deleted
    conn.commit()

    _migrate_jobs_canonicalize_urls(conn)

    rows = conn.execute("SELECT id, url FROM jobs ORDER BY id").fetchall()
    assert [(r["id"], r["url"]) for r in rows] == [
        (1, "https://ex.com/j/9"),
        (3, "https://ex.com/j/7"),
    ]
    assert conn.execute("SELECT COUNT(*) FROM job_scores").fetchone()[0] == 0

    # idempotent
    _migrate_jobs_canonicalize_urls(conn)
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 2
```

(If `job_scores` requires a real `scenario_id` FK, insert a scenario first: `conn.execute("INSERT INTO scenarios (id, name, description) VALUES (1, 'S', '')")` — check the table's columns in `schema.py` and match.)

- [x] **Step 2: Run to verify it fails**

Run: `/home/roman/projects/job-seek/.venv/bin/python -m pytest tests/test_schema.py::test_migrate_canonicalizes_job_urls_and_collapses_collisions -q`
Expected: FAIL — `ImportError: cannot import name '_migrate_jobs_canonicalize_urls'`

- [x] **Step 3: Implement in `app/db/schema.py`**

Add the function (near the other `_migrate_jobs_*` helpers):

```python
def _migrate_jobs_canonicalize_urls(conn: sqlite3.Connection) -> None:
    # Idempotent: canonicalizing an already-canonical URL is a no-op, so this
    # runs on every startup. Collapses rows that canonicalize to the same URL,
    # keeping the lowest id (child rows cascade-delete).
    from app.url_canon import canonicalize_url

    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='jobs'"
    ).fetchone()
    if row is None:
        return

    by_canon: dict[str, list[int]] = {}
    for job_id, url in conn.execute("SELECT id, url FROM jobs ORDER BY id"):
        by_canon.setdefault(canonicalize_url(url), []).append(job_id)

    for canon, ids in by_canon.items():
        for drop in ids[1:]:
            conn.execute("DELETE FROM jobs WHERE id = ?", (drop,))
        conn.execute("UPDATE jobs SET url = ? WHERE id = ?", (canon, ids[0]))
    conn.commit()
```

Register in `init_db`, after `_migrate_jobs_add_evaluation_completed_at(conn)`:

```python
    _migrate_jobs_add_evaluation_completed_at(conn)
    _migrate_jobs_canonicalize_urls(conn)
```

- [x] **Step 4: Run the test to verify it passes**

Run: `/home/roman/projects/job-seek/.venv/bin/python -m pytest tests/test_schema.py -q`
Expected: PASS

- [x] **Step 5: Full suite check**

Run: `/home/roman/projects/job-seek/.venv/bin/python -m pytest -q`
Expected: PASS

- [x] **Step 6: Commit**

```bash
git add app/db/schema.py tests/test_schema.py
git commit -m "feat: migrate existing job URLs to canonical form"
```

---

## Task 8: Backlog cleanup + full verification

**Files:**
- Modify: `BACKLOG.md`

- [x] **Step 1: Remove the fixed items from `BACKLOG.md`**

Delete these lines:
- `- fetch: **\`generic_listing\` doesn't escalate to Playwright when it should.** ...` (the Accenture item) — fixed by Task 2/3.
- `- fetch: linkedin like: https://no.linkedin.com/jobs/artificial-intelligence-jobs` — fixed by Task 5 + Task 6.
- `- fetch: **Bouvet's ReachMee "job news feed" page** ...` — fixed by Task 5.

Leave `- fetch: https://sixrobotics.com/careers not recognized as job listing` (page has zero openings; out of scope) but append `  (page currently lists no openings; JS-rendered — revisit when it has postings)`.

Leave the `feature:` pagination item; optionally append `  (JS-driven pagination, not hyperlink pagers — needs Playwright click/scroll)`.

- [x] **Step 2: Full suite + quick manual sanity**

Run: `/home/roman/projects/job-seek/.venv/bin/python -m pytest -q`
Expected: PASS, no skips introduced.

Optional live check (needs the configured LLM at `http://brick:7000` reachable and network):

```bash
/home/roman/projects/job-seek/.venv/bin/python -c "
import openai
from app.fetchers.listing_detect import detect_listing_page
c = openai.OpenAI(base_url='http://brick:7000/v1', api_key='x')
m = 'unsloth/Gemma-4-26B-A4B-It-GGUF:Q4_K_M'
for u in ['https://web106.reachmee.com/ext/I002/1338/main?site=6&validator=09af816191d8e8a901a9828c0130f41b&lang=NO',
          'https://no.linkedin.com/jobs/artificial-intelligence-jobs',
          'https://www.accenture.com/no-en/careers/jobsearch?jt=Mid-Level']:
    d = detect_listing_page(c, m, u)
    print(u, '->', d.is_listing, len(d.job_links), 'rendered' if d.rendered else 'raw')
"
```

Expected: all three report `is_listing True` with a non-trivial `job_links` count (Accenture via `rendered`).

- [x] **Step 3: Commit**

```bash
git add BACKLOG.md
git commit -m "chore: clear fixed fetch backlog items"
```

---

## Self-Review

**Spec coverage:**
- A (detect_listing truncation) → Task 5. ✓
- B (Playwright escalation) → Tasks 2 (helper), 3 (generic_listing), 4 (routes). ✓
- C (URL canonicalization) → Tasks 1 (util), 6 (wiring), 7 (migration). ✓
- Tests enumerated in the spec → covered across Tasks 1-7; route-test rewrite in Task 4. ✓
- Out-of-scope items (pagination, sixrobotics, LinkedIn-as-source, caching) → untouched; BACKLOG note in Task 8. ✓

**Type consistency:**
- `detect_listing_page(client, model, url) -> ListingDetection(html, is_listing, job_links, rendered)` — used identically in Tasks 2, 3, 4, and the Task 8 sanity check.
- `detect_listing(...) -> {"is_listing", "job_links"}` return shape unchanged through Task 5; helper (Task 2) and its tests consume that shape; fetcher/route tests (Tasks 3, 4) patch `detect_listing_page` and never see the wire format.
- `canonicalize_url(str) -> str` — used in Tasks 6 and 7 with the same signature defined in Task 1.

**Ordering rationale:** Task 2→3→4 lands the helper before the format change so only `tests/test_detect_listing.py` couples to the `job_ids` wire format (Task 5). Task 1→6→7 keeps the canonicalization util, its wiring, and its migration in dependency order.
