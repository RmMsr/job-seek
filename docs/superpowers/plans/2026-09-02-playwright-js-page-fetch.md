# Playwright escalation for JS-rendered job pages — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop JavaScript-rendered single job postings (e.g. jobbnorge) from being stored as `error` jobs by keeping the Playwright render when it yields substantially more text, and by loosening the render pool's page-load wait.

**Architecture:** Four small, independent changes in the fetch layer, upstream of `classify()`. (1) New content-comparison helpers in `content.py`. (2) `detect_listing_page` keeps a render when it's a richer *non-listing* page, not only when it has more job links. (3) The Playwright pool waits on `domcontentloaded` + a fixed settle delay instead of `networkidle`. (4) `generic_listing`'s per-posting render triggers on "shorter than a real posting", not just "under 200 chars".

**Tech Stack:** Python, pytest, `respx` (httpx mocking), `unittest.mock`, BeautifulSoup, Playwright (sync API).

## Global Constraints

- `MIN_CONTENT_LENGTH = 200` stays as-is and keeps all its current callers. New behaviour is layered on top, never by changing this constant.
- No change to `HttpFetcher`'s public shape, to `classify()`, or to any pipeline/route code.
- Storing the post-redirect URL is explicitly out of scope.
- Commit after each task (all steps green).
- Run the full fetcher test suite before the final commit: `python -m pytest tests/test_fetcher_content.py tests/test_listing_detect.py tests/test_fetcher_playwright_pool.py tests/test_fetcher_generic_listing.py -q`

---

### Task 1: content.py content-comparison helpers

**Files:**
- Modify: `app/fetchers/content.py`
- Test: `tests/test_fetcher_content.py`

**Interfaces:**
- Consumes: `extract_text(html: str) -> str` (existing in this module).
- Produces:
  - `MIN_ARTICLE_LENGTH: int` (module constant, value `1200`)
  - `text_length(html: str) -> int`
  - `is_substantially_richer(candidate_html: str, baseline_html: str) -> bool`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_fetcher_content.py`. Update the existing import block at the top of the file to add the three new names:

```python
from app.fetchers.content import (
    MIN_CONTENT_LENGTH, MIN_ARTICLE_LENGTH, extract_text, has_enough_content,
    has_enough_text, FetchError, NoContentError, fetch_url_html,
    extract_text_or_raise, extract_page_title, text_length, is_substantially_richer,
)
```

Append these tests at the end of the file:

```python
def test_min_article_length_is_1200():
    assert MIN_ARTICLE_LENGTH == 1200


def test_text_length_counts_stripped_extracted_text():
    html = "<html><body><h1>Hi</h1><p>  there  </p></body></html>"
    # "Hi\nthere" -> whitespace between is kept by the separator, ends stripped
    assert text_length(html) == len(extract_text(html).strip())
    assert text_length("<html><body>   </body></html>") == 0


def test_is_substantially_richer_true_when_candidate_has_min_content_length_more_text():
    baseline = "<html><body><p>short shell</p></body></html>"
    richer = "<html><body><p>" + ("real description text " * 40) + "</p></body></html>"
    assert is_substantially_richer(richer, baseline)


def test_is_substantially_richer_false_when_similar_length():
    a = "<html><body><p>" + ("some text here " * 30) + "</p></body></html>"
    b = "<html><body><p>" + ("other text now " * 30) + "</p></body></html>"
    assert not is_substantially_richer(a, b)


def test_is_substantially_richer_boundary_at_min_content_length():
    baseline_html = "<html><body><p>" + ("x" * 100) + "</p></body></html>"
    at = "<html><body><p>" + ("y" * (100 + MIN_CONTENT_LENGTH)) + "</p></body></html>"
    below = "<html><body><p>" + ("y" * (100 + MIN_CONTENT_LENGTH - 1)) + "</p></body></html>"
    assert is_substantially_richer(at, baseline_html)
    assert not is_substantially_richer(below, baseline_html)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_fetcher_content.py -q`
Expected: FAIL — `ImportError: cannot import name 'MIN_ARTICLE_LENGTH'` (collection error).

- [ ] **Step 3: Implement the helpers**

In `app/fetchers/content.py`, add the constant next to `MIN_CONTENT_LENGTH`:

```python
MIN_CONTENT_LENGTH = 200  # below this, treat as no real content (e.g. a JS-only page's noscript shell)
MIN_ARTICLE_LENGTH = 1200  # a real job posting's extracted text clears this; a JS nav-shell
                           # ("<site> needs JavaScript" + menu) usually does not
```

Add the two functions after `has_enough_text`:

```python
def text_length(html: str) -> int:
    return len(extract_text(html).strip())


def is_substantially_richer(candidate_html: str, baseline_html: str) -> bool:
    """True if ``candidate_html`` yields at least ``MIN_CONTENT_LENGTH`` more
    characters of extractable text than ``baseline_html`` — used to decide
    whether a headless render is worth keeping over the raw HTML for a page that
    is not a job listing (a JS-rendered single posting whose raw HTML is a
    nav-only shell)."""
    return text_length(candidate_html) >= text_length(baseline_html) + MIN_CONTENT_LENGTH
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_fetcher_content.py -q`
Expected: PASS (all tests, old and new).

- [ ] **Step 5: Commit**

```bash
git add app/fetchers/content.py tests/test_fetcher_content.py
git commit -m "feat: text_length / is_substantially_richer content helpers"
```

---

### Task 2: keep a richer non-listing render in detect_listing_page

**Files:**
- Modify: `app/fetchers/listing_detect.py:49-58`
- Test: `tests/test_listing_detect.py`

**Interfaces:**
- Consumes: `is_substantially_richer(candidate_html, baseline_html) -> bool` from Task 1.
- Produces: no new symbols; `detect_listing_page` keeps its signature and `ListingDetection` return type.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_listing_detect.py`. Note the existing fixtures: `_LISTING` (has 2 job links + real text), `_THIN` (noscript only), `_BOILERPLATE` (nav + ~340 chars text, no links). Add a rich non-listing fixture and tests:

```python
_POSTING = "<html><body><p>" + (
    "We are hiring a Staff Engineer. You will own the platform roadmap, mentor "
    "engineers, and ship reliability improvements across the fleet. " * 12
) + "</p></body></html>"


def test_nonlisting_render_kept_when_substantially_richer():
    # raw HTML clears the 200-char floor (so the first block is skipped) but is a
    # thin JS shell; the render is a full, link-free posting.
    detect = [_dl(False, []), _dl(False, [])]
    with patch("app.fetchers.listing_detect.fetch_url_html", return_value=_BOILERPLATE), \
         patch("app.fetchers.listing_detect.render_html", return_value=_POSTING) as render, \
         patch("app.fetchers.listing_detect.detect_listing", side_effect=detect):
        out = detect_listing_page(object(), "m", "https://ex.com/job/1")
    render.assert_called_once_with("https://ex.com/job/1")
    assert out.rendered is True and out.is_listing is False
    assert out.html == _POSTING and out.job_links == []


def test_nonlisting_render_discarded_when_not_richer():
    similar = "<html><body><nav>Home About Contact</nav><p>" + \
        ("Roughly the same amount of words as the raw page here. " * 6) + "</p></body></html>"
    detect = [_dl(False, []), _dl(False, [])]
    with patch("app.fetchers.listing_detect.fetch_url_html", return_value=_BOILERPLATE), \
         patch("app.fetchers.listing_detect.render_html", return_value=similar), \
         patch("app.fetchers.listing_detect.detect_listing", side_effect=detect):
        out = detect_listing_page(object(), "m", "https://ex.com/job/1")
    assert out.rendered is False and out.html == _BOILERPLATE and out.is_listing is False


def test_nonlisting_render_returns_none_keeps_raw():
    with patch("app.fetchers.listing_detect.fetch_url_html", return_value=_BOILERPLATE), \
         patch("app.fetchers.listing_detect.render_html", return_value=None), \
         patch("app.fetchers.listing_detect.detect_listing", return_value=_dl(False, [])):
        out = detect_listing_page(object(), "m", "https://ex.com/job/1")
    assert out.rendered is False and out.html == _BOILERPLATE
```

- [ ] **Step 2: Run the tests to verify the new ones fail**

Run: `python -m pytest tests/test_listing_detect.py -q`
Expected: `test_nonlisting_render_kept_when_substantially_richer` FAILS (`out.rendered` is `False` — the render is currently discarded because `len(alt job_links) 0 > 0` is false). The other two new tests may already pass; the existing five tests must still pass.

- [ ] **Step 3: Widen the render-keep condition**

In `app/fetchers/listing_detect.py`, update the import on line 5:

```python
from app.fetchers.content import fetch_url_html, has_enough_content, is_substantially_richer
```

Replace the keep check in the second block (currently lines 54-56):

```python
        if rendered and has_enough_content(rendered):
            alt = _detect(client, model, rendered, url)
            if len(alt["job_links"]) > len(result["job_links"]) or (
                not alt["is_listing"] and is_substantially_richer(rendered, html)
            ):
                return ListingDetection(rendered, alt["is_listing"], alt["job_links"], True)
```

Leave the first block (thin raw HTML → immediate render) and the final
`return ListingDetection(html, ...)` unchanged. Update the docstring's
"kept only if it yields strictly more job links" sentence to note it is also
kept when the page is not a listing and the render has substantially more text.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_listing_detect.py -q`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add app/fetchers/listing_detect.py tests/test_listing_detect.py
git commit -m "feat: keep richer non-listing render in detect_listing_page"
```

---

### Task 3: Playwright pool waits on domcontentloaded + settle

**Files:**
- Modify: `app/fetchers/playwright_pool.py`
- Test: `tests/test_fetcher_playwright_pool.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `SETTLE_MS: int` module constant (value `2000`). No signature changes.

- [ ] **Step 1: Update the failing test**

In `tests/test_fetcher_playwright_pool.py`, replace the assertion in
`test_render_returns_page_content` (currently line 32):

```python
def test_render_returns_page_content():
    sync_playwright_mock, pw, browser, page = _mock_playwright()
    pool = BrowserPool(idle_timeout_seconds=1.0)
    with patch("app.fetchers.playwright_pool.sync_playwright", sync_playwright_mock):
        html = pool.render("https://example.com")
    assert html == "<html>rendered</html>"
    page.goto.assert_called_once_with(
        "https://example.com", wait_until="domcontentloaded", timeout=30000
    )
    page.wait_for_timeout.assert_called_once_with(2000)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_fetcher_playwright_pool.py::test_render_returns_page_content -v`
Expected: FAIL — `goto` was called with `wait_until="networkidle"`.

- [ ] **Step 3: Change the wait strategy**

In `app/fetchers/playwright_pool.py`, add the constant near the other module constants (after `DEFAULT_TIMEOUT_MS`):

```python
SETTLE_MS = 2000  # fixed pause after domcontentloaded for client-side rendering to finish;
                  # networkidle never settles on pages with analytics/consent beacons
```

Update `_render_one` (currently lines 101-108):

```python
    def _render_one(self, browser, url: str, timeout_ms: int) -> str:
        ctx = browser.new_context()
        try:
            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(SETTLE_MS)
            return page.content()
        finally:
            ctx.close()
```

- [ ] **Step 4: Run the full pool test file to verify nothing else broke**

Run: `python -m pytest tests/test_fetcher_playwright_pool.py -q`
Expected: PASS (all tests). The other tests use `MagicMock` pages, so the extra
`page.wait_for_timeout(...)` call is a harmless no-op there.

- [ ] **Step 5: Commit**

```bash
git add app/fetchers/playwright_pool.py tests/test_fetcher_playwright_pool.py
git commit -m "fix: render pool waits domcontentloaded + settle, not networkidle"
```

---

### Task 4: generic_listing renders short per-posting pages

**Files:**
- Modify: `app/fetchers/generic_listing.py`
- Test: `tests/test_fetcher_generic_listing.py`

**Interfaces:**
- Consumes: `MIN_ARTICLE_LENGTH`, `MIN_CONTENT_LENGTH` from `app.fetchers.content`.
- Produces: no new symbols.

- [ ] **Step 1: Write the failing test and bump the shared fixture**

In `tests/test_fetcher_generic_listing.py`:

(a) Bump `_JOB_DETAIL_HTML` (currently ~290 chars of text) so it clears the new
1200-char trigger and non-fallback tests never attempt a real render:

```python
_JOB_DETAIL_HTML = (
    "<html><body><p>"
    + ("We are hiring a Senior Software Engineer to join our platform team. "
       "You will design services, review code, and mentor other engineers. " * 12)
    + "</p></body></html>"
)
```

(b) Add a test for the JS-shell posting that clears the 200-char floor but is far
under a real posting's length:

```python
@respx.mock
def test_generic_listing_renders_detail_page_that_is_a_js_shell_over_200_chars():
    # ~640 chars of nav boilerplate + "needs JavaScript": clears has_enough_text
    # (the old trigger) but is nowhere near a real posting's length.
    shell = "<html><body><nav>" + ("Home Careers About Contact Privacy Terms " * 12) + \
        "</nav><p>This site needs JavaScript enabled.</p></body></html>"
    respx.get("https://example.com/jobs/1").mock(return_value=httpx.Response(200, text=shell))
    rendered_detail = "<html><body><p>" + ("Full role description and requirements. " * 20) + "</p></body></html>"
    with patch("app.fetchers.generic_listing.detect_listing_page",
               return_value=_detection(True, ["https://example.com/jobs/1"])), \
         patch("app.fetchers.generic_listing.render_html", return_value=rendered_detail) as mock_render:
        jobs = GenericListingFetcher(_SOURCE, MagicMock(), "m").fetch()
    mock_render.assert_called_once_with("https://example.com/jobs/1")
    assert len(jobs) == 1 and "Full role description" in jobs[0].raw_text


@respx.mock
def test_generic_listing_keeps_raw_detail_when_render_not_richer():
    shell = "<html><body><nav>" + ("Home Careers About Contact Privacy Terms " * 12) + \
        "</nav><p>This site needs JavaScript enabled.</p></body></html>"
    respx.get("https://example.com/jobs/1").mock(return_value=httpx.Response(200, text=shell))
    with patch("app.fetchers.generic_listing.detect_listing_page",
               return_value=_detection(True, ["https://example.com/jobs/1"])), \
         patch("app.fetchers.generic_listing.render_html", return_value=shell):
        jobs = GenericListingFetcher(_SOURCE, MagicMock(), "m").fetch()
    # render was no richer than raw -> keep the raw HttpFetcher result
    assert len(jobs) == 1 and "needs JavaScript" in jobs[0].raw_text
```

- [ ] **Step 2: Run the tests to verify the new one fails**

Run: `python -m pytest tests/test_fetcher_generic_listing.py -q`
Expected: `test_generic_listing_renders_detail_page_that_is_a_js_shell_over_200_chars`
FAILS — `mock_render` not called (the ~640-char shell passes the current
`has_enough_text` trigger, so no fallback fires).

- [ ] **Step 3: Change the trigger and keep-guard**

In `app/fetchers/generic_listing.py`, update the `content` import (line 6):

```python
from app.fetchers.content import (
    MIN_CONTENT_LENGTH, MIN_ARTICLE_LENGTH, has_enough_content, extract_text, FetchError,
)
```

(`has_enough_text` is no longer needed here — confirm it has no other use in the
file and drop it from the import.)

Replace the per-posting loop body (currently lines 57-65):

```python
        for href in job_links:
            page_jobs = HttpFetcher({"url": href}).fetch()
            raw_text = page_jobs[0].raw_text if page_jobs else ""
            is_thin = len(raw_text.strip()) < MIN_ARTICLE_LENGTH
            if is_thin and playwright_fallbacks_used < MAX_PLAYWRIGHT_FALLBACKS:
                playwright_fallbacks_used += 1
                rendered = render_html(href)
                if rendered and has_enough_content(rendered):
                    rendered_text = extract_text(rendered)
                    if len(rendered_text.strip()) >= len(raw_text.strip()) + MIN_CONTENT_LENGTH:
                        page_jobs = [RawJob(url=href, title="", company="", raw_text=rendered_text)]
            jobs.extend(page_jobs)
        return jobs
```

Update the `MAX_PLAYWRIGHT_FALLBACKS` comment (line 17) from "detail-page
Playwright renders" wording only if needed — the cap semantics are unchanged.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_fetcher_generic_listing.py -q`
Expected: PASS (all tests). Confirm `test_generic_listing_fetcher_falls_back_to_playwright_for_thin_detail_page`
and `..._returns_nonempty_but_thin_content` still pass (empty / 17-char raw is
still under 1200, render text still ≥ raw + 200).

- [ ] **Step 5: Commit**

```bash
git add app/fetchers/generic_listing.py tests/test_fetcher_generic_listing.py
git commit -m "feat: generic_listing renders per-posting pages shorter than a real posting"
```

---

### Task 5: strip cookie-consent boilerplate on text extraction

**Files:**
- Modify: `app/fetchers/content.py`
- Modify: `app/fetchers/generic_listing.py` (rendered-text extraction only)
- Test: `tests/test_fetcher_content.py`, `tests/test_fetcher_generic_listing.py`

**Interfaces:**
- Consumes: `BeautifulSoup` (already imported in `content.py`), `extract_text` (existing).
- Produces:
  - `extract_readable_text(html: str) -> str` in `app.fetchers.content`
  - module-level `_BOILERPLATE_SELECTORS: str` (private)

**Why:** even with the render kept (Task 2), a page like `jobbnorge/304563` has no
`<main>`/`<article>` and leads with ~4000 chars of CookieInformation consent text.
`classify()` only sees the first 4000 chars of simplified text, so it returns
`irrelevant` and the job is silently discarded. Removing consent/CMP containers
before text extraction puts the description back inside the classifier's window
(verified end-to-end: 20.4k → 9.4k simplified chars, `classify()` → `job_posting`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_fetcher_content.py` (extend the import to add `extract_readable_text`):

```python
def test_extract_readable_text_drops_cookie_and_consent_containers():
    html = (
        "<html><body>"
        "<div class='cookie-banner'>We use cookies. " + ("blah " * 200) + "</div>"
        "<div id='onetrust-consent-sdk'>" + ("consent copy " * 200) + "</div>"
        "<div class='job-body'><h1>Staff Engineer</h1><p>Own the platform roadmap.</p></div>"
        "</body></html>"
    )
    text = extract_readable_text(html)
    assert "Staff Engineer" in text and "Own the platform roadmap." in text
    assert "We use cookies" not in text
    assert "consent copy" not in text


def test_extract_readable_text_also_drops_script_nav_footer():
    html = (
        "<html><body><nav>Home About</nav><script>var x=1;</script>"
        "<div class='content'><p>Real posting body here.</p></div>"
        "<footer>© 2026</footer></body></html>"
    )
    text = extract_readable_text(html)
    assert "Real posting body here." in text
    assert "Home About" not in text and "var x=1" not in text and "© 2026" not in text


def test_extract_text_is_unchanged_by_boilerplate_stripping():
    # extract_text stays pure — only extract_readable_text strips.
    html = "<html><body><div class='cookie-banner'>cookies</div><p>body</p></body></html>"
    assert "cookies" in extract_text(html)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_fetcher_content.py -q`
Expected: FAIL — `ImportError: cannot import name 'extract_readable_text'`.

- [ ] **Step 3: Implement `extract_readable_text`**

In `app/fetchers/content.py`, after `extract_text`:

```python
_BOILERPLATE_SELECTORS = ",".join((
    "script", "style", "nav", "header", "footer",
    "[id*=cookie i]", "[class*=cookie i]",
    "[id*=consent i]", "[class*=consent i]",
    "#CybotCookiebotDialog", "#onetrust-consent-sdk", "#didomi-host", ".osano-cm-window",
))


def extract_readable_text(html: str) -> str:
    """``extract_text`` with site chrome removed — scripts/styles, nav/header/footer,
    and cookie-consent / CMP banners (CookieInformation, Cookiebot, OneTrust,
    Didomi, Osano, and homegrown ``*cookie*`` / ``*consent*`` containers). Used for
    the text that becomes a job's ``raw_text``: a rendered page often leads with a
    multi-thousand-character consent notice that would otherwise bury the posting
    past the classifier's input window. ``extract_text`` itself stays pure."""
    soup = BeautifulSoup(html, "html.parser")
    for el in soup.select(_BOILERPLATE_SELECTORS):
        el.decompose()
    return soup.get_text(separator="\n")
```

Then point `extract_text_or_raise` at it:

```python
def extract_text_or_raise(html: str) -> str:
    if not has_enough_content(html):
        raise NoContentError("page had little to no extractable text")
    return extract_readable_text(html)
```

(`has_enough_content` is unchanged — it still measures the raw page via `extract_text`.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_fetcher_content.py -q`
Expected: PASS.

- [ ] **Step 5: Use it for the rendered per-posting page in generic_listing**

In `app/fetchers/generic_listing.py`, add `extract_readable_text` to the
`app.fetchers.content` import, and in the per-posting loop change the rendered
extraction from `extract_text(rendered)` to `extract_readable_text(rendered)`:

```python
                if rendered and has_enough_content(rendered):
                    rendered_text = extract_readable_text(rendered)
                    if len(rendered_text.strip()) >= len(raw_text.strip()) + MIN_CONTENT_LENGTH:
                        page_jobs = [RawJob(url=href, title="", company="", raw_text=rendered_text)]
```

`extract_text` may now be unused in that file — check and drop it from the import
if so. Leave `HttpFetcher._parse` (the raw-HTTP path) unchanged.

- [ ] **Step 6: Run the generic_listing + full fetcher suite**

Run: `python -m pytest tests/test_fetcher_generic_listing.py tests/test_fetcher_content.py tests/test_listing_detect.py -q`
Expected: PASS. The Task 4 fallback tests use link-free rendered fixtures, so
`extract_readable_text` returns the same text as `extract_text` for them.

- [ ] **Step 7: Commit**

```bash
git add app/fetchers/content.py app/fetchers/generic_listing.py tests/test_fetcher_content.py
git commit -m "feat: strip cookie-consent boilerplate from job page text"
```

---

### Task 6: full suite + manual verification note

**Files:** none (verification only).

- [ ] **Step 1: Run the fetcher suite**

Run: `python -m pytest tests/test_fetcher_content.py tests/test_listing_detect.py tests/test_fetcher_playwright_pool.py tests/test_fetcher_generic_listing.py tests/test_fetcher_http.py tests/test_routes_jobs.py -q`
Expected: PASS.

- [ ] **Step 2: Run the whole suite**

Run: `python -m pytest -q`
Expected: PASS (no regressions). Investigate any failure before proceeding.

- [ ] **Step 3: Record the manual check for the handoff**

Playwright's Chromium works from this machine but its network is blocked by the
*command sandbox*; the maintainer verified the full path unsandboxed against the
live LLM (`detect_listing_page` → `rendered=True`; extracted+simplified text →
`classify()` = `job_posting`). Re-confirm on the dev server:

> add-by-URL `https://www.jobbnorge.no/ledige-stillinger/stilling/304563` against
> a throwaway DB → a `job_posting` job with the real description, not `error` and
> not silently discarded. Progress log: `Escalating to headless-browser render`
> then `Classified as job_posting`.

- [ ] **Step 4: No commit** (verification task).

---

## Self-Review

**Spec coverage:**
- content helpers (spec §1) → Task 1.
- `detect_listing_page` keep-condition (spec §2) → Task 2.
- Playwright wait (spec §3) → Task 3.
- `generic_listing` per-posting trigger + fixture bump (spec §4) → Task 4.
- cookie-consent stripping `extract_readable_text` (spec §5) → Task 5.
- Test list (spec "Testing") → Tasks 1-5 steps + Task 6.
- Out-of-scope redirect URL → not planned, correct.

**Placeholder scan:** no TBD/TODO; all code shown in full; all commands have expected output.

**Type consistency:** `text_length(html)->int`, `is_substantially_richer(candidate_html, baseline_html)->bool`, `MIN_ARTICLE_LENGTH=1200`, `SETTLE_MS=2000` — used identically in Tasks 2 and 4 as defined in Task 1 / Task 3. `_dl`, `_detection`, `_JOB_DETAIL_HTML` match the existing test helpers in each file.
