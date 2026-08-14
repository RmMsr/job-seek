# Playwright fallback for JS-rendered pages — design

**Date:** 2026-08-14
**Status:** Approved

## Problem

Add-by-url and `generic_listing` sources both fetch pages with plain
`httpx`/BeautifulSoup. Sites that render their content client-side (e.g. an
Ashby.io job posting or a React-based careers listing page) come back as a
near-empty HTML shell. Today that's handled by treating "too little
extracted text" as a hard failure:

- **add-by-url**: lands the URL in Trash as a `content_type=error` job with a
  "page likely requires JavaScript to render" note.
- **`GenericListingFetcher`**: the listing page or a job-detail page silently
  returns no jobs; a detail page stuck behind JS never gets picked up on any
  future periodic re-fetch either, since `HttpFetcher` always fails the same
  way.

The codebase already has Playwright as a dependency (`PlaywrightFetcher`,
`FinnListingFetcher`, `SlackFetcher`), just not wired into either of these two
paths. This adds a JS-rendering fallback: try plain HTTP first (cheap), and
only pay for a headless-Chromium render when the plain fetch comes back too
thin.

## Goals

- Add-by-url: a JS-rendered single posting or JS-rendered listing page
  succeeds instead of landing in Trash.
- `generic_listing` sources: both the listing page itself and individual job
  detail pages get the same fallback, so a JS-heavy site keeps working across
  periodic re-fetches, not just at add-time.
- Keep the plain-HTTP path exactly as fast as it is today for the common
  case — Playwright only runs when content is actually too thin.
- Amortize the cost of spinning up a headless browser across nearby calls
  (same request, back-to-back requests, a single fetch run) instead of
  launching a fresh Chromium process per page.

## Non-goals

- `fetcher_type='http'` sources in general, or `HttpFetcher` itself. The
  fallback only applies to add-by-url and `generic_listing`; `HttpFetcher`'s
  behavior (and its own, looser "any non-empty text" check) is unchanged.
- Any UI-visible indication of *how* a job was fetched (plain HTTP vs.
  rendered). The result looks identical either way.
- Encrypting/persisting anything new. No new stored state — the browser pool
  is purely an in-process runtime detail.

## Design

### Shared content-threshold check (`app/fetchers/content.py`, new)

Pulls the existing `_MIN_CONTENT_LENGTH = 200` / text-extraction logic out of
`app/routes/jobs.py` into one shared place:

```python
MIN_CONTENT_LENGTH = 200

def extract_text(html: str) -> str: ...      # BeautifulSoup get_text()
def has_enough_content(html: str) -> bool: ...  # len(extract_text(html).strip()) >= MIN_CONTENT_LENGTH
```

`app/routes/jobs.py`'s `_extract_text`/`_NoContentError` become a thin
wrapper around these, so add-by-url and `GenericListingFetcher` agree on
exactly what "too thin, probably needs JS" means.

### Browser pool (`app/fetchers/playwright_pool.py`, new)

Playwright's sync API is thread-affine: a browser/page object may only be
touched from the OS thread that started `sync_playwright()`. FastAPI runs
sync route handlers on a thread pool, so a naive shared browser object would
break the moment two different request threads touched it. Every existing
fetcher in this codebase sidesteps this by opening and closing
`sync_playwright()` fresh, inside whichever single thread runs that one call.

To get real reuse (the ask: keep a browser warm for ~10s of idleness instead
of relaunching per call) without violating thread-affinity, a `BrowserPool`
owns **one dedicated background thread** — the only thread that ever touches
Playwright objects:

```python
class BrowserPool:
    def __init__(self, idle_timeout_seconds: float = 10.0): ...
    def render(self, url: str, timeout_ms: int = 30000) -> str | None: ...
```

- `render()` (callable from any thread) puts a `(url, timeout_ms,
  result_queue)` job on an internal `queue.Queue` and blocks on
  `result_queue.get()`.
- The worker thread loop: `queue.get(timeout=idle_timeout_seconds)`.
  - On a job: launch the browser if not already running, then
    `browser.new_context()` → `new_page()` →
    `page.goto(url, wait_until="networkidle", timeout=timeout_ms)` →
    `page.content()`, close the context, push the result back. Any
    exception (nav timeout, DNS failure, crash) is caught and reported as a
    `None` result — same "swallow and report failure" behavior every other
    fetcher already has.
  - On the timeout with no job (idle): close the browser if one is open,
    loop back to waiting. The thread itself stays alive indefinitely (a
    blocked `queue.get` is nearly free); only the browser process is
    torn down and lazily relaunched on the next call.
- A fresh `new_context()` per render is the isolation boundary: even though
  the browser *process* is shared across calls/domains, no cookies or
  storage carry over between them. Process-level reuse is safe; only
  context-level reuse would be a leakage risk, and this never does that.
- `render_html(url, timeout_ms=30000) -> str | None` — module-level function
  wrapping a lazily-created singleton `BrowserPool`. This is the only public
  entry point callers use.
- The worker thread is a daemon thread, so it never blocks process exit; a
  `--reload` dev-server restart just spins up a fresh pool in the new
  process.

### add-by-url (`app/routes/jobs.py`)

The thin-content check moves to run **before** listing-detection instead of
only gating job-content extraction afterward — today, a JS-rendered listing
page fails link-extraction/listing-detection silently on the thin raw HTML
and only then hits the (also failing) content check. After this change:

```python
html = _fetch_url_html(url)
if not has_enough_content(html):
    rendered = render_html(url)
    if rendered and has_enough_content(rendered):
        html = rendered
# existing flow unchanged from here: extract_links, detect_listing,
# extract_text/run_add_job, all operating on (possibly rendered) html
```

If the Playwright render also comes back thin or `None`, behavior falls
through exactly as today: `_insert_error_job` + `_no_content_notice.html`,
landing in Trash.

### `GenericListingFetcher` (`app/fetchers/generic_listing.py`)

**Listing page:** same thin-check applied to the HTML it already fetches via
`httpx.get`, same fallback via `render_html`.

**Detail pages:** keeps delegating to `HttpFetcher(...).fetch()` unchanged
(this is the one place `HttpFetcher` is used but not modified — its own
empty-text check stays as-is). If that returns `[]`, retry that one URL via
`render_html`, up to a cap:

```python
MAX_PLAYWRIGHT_FALLBACKS = 10  # per fetch run, bounds worst-case run time
```

Once the cap is hit, remaining thin/failed detail pages are skipped exactly
as today (missing this run, retried on the next periodic re-fetch — same
"stays in `known_urls`-exclusion limbo until it succeeds" behavior that
already exists for any `HttpFetcher` failure).

## Error handling

No new failure modes. Every path that previously failed silently or landed
in Trash still does, just after one extra (bounded, swallowed-exception)
attempt. `render_html` never raises to its caller — a failed render is
indistinguishable from "Playwright wasn't tried."

## Testing

- `app/fetchers/content.py`: plain unit tests for `has_enough_content`/
  `extract_text` at and around the threshold.
- `app/fetchers/playwright_pool.py`: `patch("app.fetchers.playwright_pool.sync_playwright", ...)`
  with a mocked context-manager tree (same pattern as
  `test_fetcher_finn.py`/`test_fetcher_playwright_base.py`). Cases: launches
  once and reuses across two calls made before the idle timeout; closes and
  relaunches after `idle_timeout_seconds` (construct `BrowserPool` directly
  with a short timeout, e.g. `0.05`, for fast tests); a `new_context()` per
  call, not a shared context; render exceptions surface as `None`, not a
  raised error.
- `app/routes/jobs.py`: extend the existing JS-only-page test — thin HTML
  now triggers a mocked `render_html` before falling back to the error path;
  add a case where the mocked render succeeds and the job is added
  normally.
- `app/fetchers/generic_listing.py`: cases for listing-page fallback success,
  detail-page fallback success, and the `MAX_PLAYWRIGHT_FALLBACKS` cap being
  respected (mock `render_html` directly rather than the pool internals —
  the fetcher only depends on the public function).
