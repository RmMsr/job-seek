# Fix Playwright escalation for JS-rendered job pages

## Problem

`detect_listing_page` (`app/fetchers/listing_detect.py`) is the single fetch +
classify entry point for two jobs:

- **sources** — "is this URL a job listing?" (`routes/sources.py`,
  `generic_listing.py`)
- **add-by-URL** — "get this posting's content" (`routes/jobs.py`
  `_task_job_add_by_url`)

Its render-escalation is built entirely around job-link count: a headless render
is kept only when it yields *strictly more job links* than the raw HTML
(`listing_detect.py:55`). A JavaScript-rendered **single posting** adds zero
links, so its render — the copy that actually contains the description — is
always discarded, and the posting lands as a `content_type = "error"` job (the
raw shell text trips `classify()`'s "garbled / empty / access-denied" case).

The 200-char `MIN_CONTENT_LENGTH` floor does not catch these shells. Example:
`https://www.jobbnorge.no/ledige-stillinger/stilling/304563` 302-redirects to a
slug URL that returns HTTP 200 with ~14 KB of HTML whose only extractable text
(~645 chars) is nav chrome plus "Jobbnorge.no trenger JavaScript ...". That
clears the 200-char floor, so nothing escalates usefully.

Second factor: `playwright_pool.py:_render_one` waits with
`wait_until="networkidle"`. Analytics-heavy pages (Matomo, CookieInformation,
HubSpot, tracking pixels) may never reach network idle within the 30s timeout,
so `render_html` returns `None` and the same discard happens.

## Out of scope

Storing the post-redirect (final) URL for a job — httpx already *follows* the
redirect to fetch content; only the stored URL is still the pre-redirect one.
Tracked as a separate follow-up.

## Design

### 1. `app/fetchers/content.py` — content helpers

- `MIN_ARTICLE_LENGTH = 1200` — a real job posting clears this; a JS nav-shell
  usually does not. Used by the per-posting render trigger in
  `generic_listing.py` (section 4). `MIN_CONTENT_LENGTH` (200) stays as the
  absolute "is there *any* usable text" floor, unchanged, still used by
  `has_enough_text` / `has_enough_content` everywhere they are used today.
- `text_length(html: str) -> int` — `len(extract_text(html).strip())`.
- `is_substantially_richer(candidate_html: str, baseline_html: str) -> bool` —
  `text_length(candidate) >= text_length(baseline) + MIN_CONTENT_LENGTH`.

### 2. `app/fetchers/listing_detect.py` — widen the render-keep condition

The existing structure of `detect_listing_page` is kept as-is. It already
renders whenever the raw HTML is not an already-good listing (thin raw ->
first block; not-a-listing or too-few-links -> second block). The *only* change
is the condition for **keeping** that render (currently line 55, "strictly more
job links"):

```
if len(alt["job_links"]) > len(result["job_links"]) or (
    not alt["is_listing"] and is_substantially_richer(rendered, html)
):
    return ListingDetection(rendered, alt["is_listing"], alt["job_links"], True)
```

Net effect: the render is kept if it is a better listing (unchanged) **or** it
is a non-listing page whose text is substantially richer than the raw HTML — the
single-JS-posting case that link-count can never catch.

For `jobbnorge/304563`: raw HTML is not a listing -> second block renders ->
rendered posting text is much richer than the 645-char shell -> kept ->
`classify()` -> `job_posting`.

Branch (b) "complete single posting in raw HTML, skip render" is deliberately
*not* added: the old code already renders for every non-listing add-by-URL, so
this change adds no new render cost, and skipping renders on a text-length
heuristic would regress JS careers pages whose raw HTML carries a lot of
marketing copy but loads its job links via JS.

### 3. `app/fetchers/playwright_pool.py` — wait strategy

`_render_one`: `wait_until="networkidle"` -> `wait_until="domcontentloaded"`,
then `page.wait_for_timeout(SETTLE_MS)` with a module-level `SETTLE_MS = 2000`.
This matches `playwright_base.py`, which already uses `domcontentloaded`
throughout.

### 4. `app/fetchers/generic_listing.py` — per-posting render (lines 57-65)

Same blind spot: `is_thin = not has_enough_text(raw_text)` misses a 645-char
shell.

- Thinness trigger becomes `len(raw_text.strip()) < MIN_ARTICLE_LENGTH` (which
  subsumes today's `not has_enough_text(raw_text)`, since
  `MIN_ARTICLE_LENGTH > MIN_CONTENT_LENGTH`).
- Keep the render only if `has_enough_content(rendered)` **and** the rendered
  text is at least `MIN_CONTENT_LENGTH` chars longer than `raw_text` (today it
  keeps on `has_enough_content` alone).
- `MAX_PLAYWRIGHT_FALLBACKS = 10` is unchanged, so at most 10 short postings per
  listing run get rendered.

`HttpFetcher.fetch()` returns `raw_text` (already extracted), which is all this
comparison needs — no `HttpFetcher` change, no extra fetch. Rendered text comes
from `extract_text(rendered)`.

Test-fixture impact: `tests/test_fetcher_generic_listing.py`'s `_JOB_DETAIL_HTML`
fixture (~290 chars of text) is below the new 1200 trigger, so tests that don't
patch `render_html` would attempt a real render. Bump that fixture to comfortably
exceed `MIN_ARTICLE_LENGTH` (one constant).

### 5. `app/fetchers/content.py` — strip cookie-consent boilerplate on text extraction

Even with the render kept, a page like `jobbnorge/304563` has **no
`<main>`/`<article>`** and leads with ~4000 chars of CookieInformation consent
text. `classify()` sees only the first 4000 chars of simplified text, so it
classifies the posting `irrelevant` and the job is discarded — `error` traded for
a silent drop.

Add:

```python
_BOILERPLATE_SELECTORS = ",".join((
    "script", "style", "nav", "header", "footer",
    "[id*=cookie i]", "[class*=cookie i]",
    "[id*=consent i]", "[class*=consent i]",
    "#CybotCookiebotDialog", "#onetrust-consent-sdk", "#didomi-host", ".osano-cm-window",
))


def extract_readable_text(html: str) -> str:
    """``extract_text`` with site chrome removed — scripts, nav/header/footer, and
    cookie-consent / CMP banners (CookieInformation, Cookiebot, OneTrust, Didomi,
    Osano, and homegrown ``*cookie*`` / ``*consent*`` containers). Used for the
    text that becomes a job's ``raw_text``."""
    soup = BeautifulSoup(html, "html.parser")
    for el in soup.select(_BOILERPLATE_SELECTORS):
        el.decompose()
    return soup.get_text(separator="\n")
```

Wire it in where fetched HTML becomes a job's `raw_text`:

- `extract_text_or_raise(html)` returns `extract_readable_text(html)` (the
  `has_enough_content` gate is unchanged — it still measures the raw page).
- `generic_listing.py`'s per-posting render path uses `extract_readable_text`
  for the rendered HTML instead of `extract_text`.

`extract_text` itself is left pure (still used by `has_enough_content`,
`text_length`, `extract_page_title`). The raw-HTTP `HttpFetcher._parse` path is
left unchanged — server-rendered pages rarely inject multi-thousand-char consent
modals, and that path is out of scope here.

Coverage is a heuristic: the `*cookie*` / `*consent*` substring match plus the
four named selectors covers CookieInformation, Cookiebot, OneTrust, CookieYes,
Complianz, Didomi, Osano and most homegrown banners. Shadow-DOM CMPs
(Usercentrics) are not selectable but also do not serialize into Playwright's
`page.content()`, so they do not pollute the extracted text. `[role=dialog]` /
`[aria-modal]` are deliberately **not** included — too aggressive, would eat real
content.

Verified end-to-end against the live LLM on `brick`: with stripping,
`jobbnorge/304563` simplifies to 9.4k chars, the description lands well inside the
first 4000, and `classify()` returns `job_posting`.

## Testing

- `tests/test_listing_detect.py` — keep all existing tests passing; add:
  - non-listing raw, render returns a substantially richer non-listing page
    -> rendered kept (`rendered=True`, `is_listing=False`)
  - non-listing raw, render returns similar-length content -> raw kept
    (`rendered=False`)
  - non-listing raw, `render_html` returns `None` -> raw returned
- `tests/test_fetcher_content.py` — `text_length`, `is_substantially_richer`
  (true and false cases), `MIN_ARTICLE_LENGTH` is exported.
- `tests/test_fetcher_playwright_pool.py` — `goto` kwargs now
  `wait_until="domcontentloaded"`, and `wait_for_timeout(2000)` is called.
- `tests/test_fetcher_generic_listing.py` — shell posting (645-char raw) ->
  render kept; rich raw -> no render; render-not-richer -> raw kept.
- `tests/test_fetcher_content.py` — `extract_readable_text` drops a
  `<div class="cookie-banner">` / `<div id="onetrust-consent-sdk">` block and
  keeps the surrounding content; `extract_text` is unchanged by it.
- Manual (verified): add-by-URL
  `https://www.jobbnorge.no/ledige-stillinger/stilling/304563` unsandboxed
  against the live LLM -> `detect_listing_page` returns `rendered=True`, and the
  extracted+simplified text classifies as `job_posting`.
