# Generic listing source — design

**Problem:** [Add job by URL](2026-08-11-add-job-by-url-design.md) only handles a URL that points at a single job posting. Pasting a career page that *lists* many postings (e.g. `https://careers.pmm.tech/jobs`) either mis-fires the classify/summarize/score pipeline against listing-page HTML, or the user has to hand-paste every individual posting URL from the page one at a time — and even then, nothing revisits that listing later to pick up new postings.

**Fix:** teach `POST /jobs/add-by-url` to notice when a fetched page is itself a listing of job postings (via an LLM link-classification step, not per-site heuristics) and, on confirmation, turn it into a proper `sources` row with a new `fetcher_type` — `generic_listing` — that re-discovers and re-fetches postings from that page on every future `/fetch` run, the same way `finn_listing` does for finn.no today, but without finn.no-specific selectors or a Playwright dependency.

## 1. Detecting a listing

A new LLM step, `app/ai/detect_listing.py::detect_listing(client, model, links, page_url) -> dict`, modeled directly on `app/ai/classify.py`: given a list of `(href, anchor_text)` pairs extracted from the fetched page, it asks the LLM whether the page is a listing of multiple job postings and, if so, which of those links are individual postings. Returns `{"is_listing": bool, "job_links": list[str]}`.

- Links are extracted by a new pure function, `app/fetchers/links.py::extract_links(html, base_url) -> list[tuple[str, str]]` — BeautifulSoup `find_all("a", href=True)`, `href` resolved to an absolute URL via `urllib.parse.urljoin(base_url, href)`, anchor text stripped of whitespace, deduplicated by href, capped at 200 links (bounds prompt size; a page with more distinct links than that is already an edge case this feature doesn't need to handle perfectly).
- `detect_listing` formats the (capped) link list as `"<href> — <anchor text>"` lines in the user message, asks for `{"is_listing": bool, "job_links": ["<href>", ...]}`, and — like `classify()` validating against a fixed label set — filters the returned `job_links` down to hrefs that were actually present in the input list, dropping any the model hallucinated.
- On any exception (network already succeeded by this point, so this only covers LLM-call failures), `detect_listing` catches and returns `{"is_listing": False, "job_links": []}` — failing open into the existing single-job path rather than blocking the add-by-url flow.
- A result only counts as a listing if `is_listing` is true **and** `len(job_links) >= 2` — guards against a single job-posting page with a "similar jobs" sidebar tripping a false positive on one stray link.

## 2. Where this plugs into `POST /jobs/add-by-url`

`app/routes/jobs.py`'s existing `_fetch_url_text(url) -> str` (which fetches and immediately reduces to `BeautifulSoup(...).get_text()`) is split into `_fetch_url_html(url) -> str` (fetch only, raises `_FetchError` exactly as before) plus a small `_extract_text(html) -> str` used for the existing single-job path. This is needed because link extraction (§1) requires the raw HTML, which the old function threw away.

After a successful fetch, the route now runs listing detection before deciding what to do:

- **Not a listing** (`is_listing` false, or fewer than 2 job links): unchanged behavior — `_extract_text(html)` feeds `run_add_job` exactly as today.
- **Listing detected**: nothing is inserted into `jobs` or `sources` yet. The stream ends with a confirmation panel (`jobs/_listing_confirm.html`) swapped into `#jobs-content` instead of the refreshed job list: "Detected *N* job posting links at *`<domain>`*", an editable name field pre-filled with the URL's domain (`urllib.parse.urlsplit(url).netloc`), a hidden field carrying the URL, a "Keep as source & fetch" button, and a "Cancel" link back to `/jobs` (plain navigation — simplest way to discard the panel, consistent with the shared progress-button JS's own default fallback of `location.reload()` when nothing more specific applies).

## 3. Confirming: `POST /jobs/add-listing-source`

New route in `app/routes/jobs.py`, takes `url` + `name` (from the confirm panel's fields), and:

1. `q.insert_source(conn, name, url, "generic_listing")`.
2. Immediately streams a `run_fetch()` on the new source — the exact same call `POST /fetch/{source_id}` already makes — so the user sees postings appear without a separate trip to `/fetch`.
3. Ends the stream with the same refreshed-`_content.html` `HTML:` chunk convention `add-by-url` already uses.

This means confirming re-fetches the page and re-runs LLM link-detection a second time (once to detect at add-by-url time, once inside the `run_fetch` this triggers) rather than threading the first probe's results through the confirm round-trip. Accepted tradeoff: it keeps `run_fetch` completely untouched and avoids passing extracted-link state through the browser, at the cost of one extra fetch + LLM call — and only for a listing source's *first* fetch, not any subsequent one.

## 4. `GenericListingFetcher`

`app/fetchers/generic_listing.py`, same shape as `FinnListingFetcher` (discover → filter against `known_urls` → fetch each new detail page via `HttpFetcher` → cap the count) but generic instead of finn.no-specific:

```python
class GenericListingFetcher:
    def __init__(self, source, client, model, known_urls=frozenset()): ...
    def fetch(self) -> list[RawJob]:
        # 1. plain httpx GET of source["url"] (swallow failures, return [] —
        #    matches HttpFetcher.fetch()'s existing convention for Fetcher
        #    implementations, as opposed to add-by-url's _fetch_url_html
        #    which deliberately raises so failures are visible to the user)
        # 2. extract_links(html, source["url"])
        # 3. detect_listing(client, model, links, source["url"])["job_links"]
        # 4. drop links already in known_urls, cap at MAX_DETAIL_FETCHES
        # 5. HttpFetcher({"url": link}).fetch() per remaining link, collect RawJobs
```

No Playwright — matches the "plain HTTP only" scope decision already made for add-by-url. A listing page that needs JS rendering will simply surface zero links on fetch (same degrade path as `HttpFetcher` hitting a JS-only page today), not an error.

`_make_fetcher` (`app/pipeline.py`) gains a `generic_listing` case and two new optional parameters, `client: openai.OpenAI | None = None` and `model: str | None = None` (only `generic_listing` needs them):

```python
def _make_fetcher(source, profile_dir, conn, client=None, model=None):
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

`run_fetch` (already has `client`/`model` in scope) passes them through. `app/routes/sources.py::_check_needs_login`'s call site is unaffected — it only ever calls `_make_fetcher` for `fetcher_type == "slack"` (early-returns otherwise), so its call keeps omitting `client`/`model` and gets the `None` defaults, which that branch never touches.

## 5. Schema & source-management UI

- `sources.fetcher_type` CHECK widens to include `'manual', 'generic_listing'` — same rebuild-in-place migration pattern as the existing `_migrate_sources_fetcher_type_manual`.
- `sources/index.html`'s "Add source" `<select name="fetcher_type">` gains a `generic_listing` option, for symmetry with the other types (a user who already knows a URL is a listing isn't forced through the add-by-url detection flow to configure one directly) — this does not change the detection-only trigger scope decided earlier; it just means the resulting type is a normal, directly-selectable option like the others once it exists.
- The Manual-source exclusion added by add-by-url (`fetcher_type != "manual"` filters in `sources_page`/`fetch_panel`) is untouched — `generic_listing` sources are real, user-visible, fetchable sources and appear normally in both.

## 6. Frontend: generalizing the progress-button body mechanism

Add-by-url's button uses `data-progress-url-input="#add-job-url"` (added by that feature) to send one field, always named `url`. The confirm panel's button needs to send two fields (`url` and `name`), so this gets generalized to `data-progress-body-<fieldname>="<selector>"` — one attribute per field, the attribute name's suffix becomes the POST field name:

```html
<!-- add-by-url button (migrated) -->
<button ... data-progress-body-url="#add-job-url">Add</button>

<!-- listing-confirm button (new) -->
<button ...
  data-progress-body-url="#listing-url"
  data-progress-body-name="#listing-name">Keep as source &amp; fetch</button>
```

In `base.html`'s shared click handler: scan the clicked element's attributes for any starting with `data-progress-body-`, and for each, read the named selector's input value into a `URLSearchParams` body under that field name (aborting, as today, if any referenced input is empty). The existing single-field `data-progress-url-input` handling is replaced by this general form rather than kept alongside it. The "clear the input on success" step in `finish()` similarly iterates the same `data-progress-body-*` attributes — this is a no-op for the confirm panel's fields, since a successful confirm replaces `#jobs-content` (and thus the panel and its inputs) entirely; it still matters for add-by-url's field, which lives outside `#jobs-content` in `jobs/list.html` and survives the swap.

## Out of scope

- **Multi-page / paginated listings, or a general-purpose crawler** (e.g. adopting Scrapy or Crawlee to walk arbitrary job boards beyond a single listing page). Flagged during brainstorming as a good longer-term direction, but a materially larger change — new dependency, new execution model, pagination-walking logic — than this iteration. Tracked as a follow-up, not built here.
- **JS-rendered listing pages.** Same plain-HTTP-only scope as add-by-url; a listing source that needs Playwright isn't supported (surfaces as "0 postings found" on fetch, not an error).
- **Editing a `generic_listing` source's detection behavior** (e.g. re-running detection, adjusting the job-link count floor) after creation. It's a normal source from that point on — same lifecycle (enable/disable, edit name/url, `/fetch` trigger) as any other.

## Testing

1. `extract_links`: dedup by href, relative hrefs resolved absolute against `base_url`, cap at 200, anchor text whitespace-stripped.
2. `detect_listing`: mocked LLM response with `is_listing=true` and a `job_links` entry not present in the input list — asserts the hallucinated link is filtered out. Mocked LLM exception — asserts `{"is_listing": False, "job_links": []}`.
3. Route test: `POST /jobs/add-by-url` against a page whose extracted links + mocked `detect_listing` report `is_listing=True` with 3 job links — asserts the response contains the confirm panel markup (detected count, domain-derived name field, hidden url field) and that no row was inserted into `jobs` or `sources`.
4. Route test: same page but mocked `detect_listing` returns `is_listing=True` with only 1 job link — asserts the *existing* single-job path still runs (the `>= 2` floor).
5. Route test: existing single-job add-by-url behavior (already covered by `test_add_job_by_url_*` in `tests/test_routes_jobs.py`) still passes unchanged after the `_fetch_url_text` → `_fetch_url_html`/`_extract_text` split.
6. Route test: `POST /jobs/add-listing-source` creates a `sources` row with `fetcher_type='generic_listing'` and the given name/url, and (with `run_fetch` mocked, same pattern as existing reset/pass-as-new tests) streams progress ending in the refreshed job list.
7. `GenericListingFetcher` test (respx-mocked listing page + detail pages, mocked LLM `detect_listing`): returns `RawJob` entries only for links `detect_listing` flagged, skips links already in `known_urls`, caps at `MAX_DETAIL_FETCHES`.
8. `_make_fetcher` test: `fetcher_type='generic_listing'` dispatches to `GenericListingFetcher`; a `_check_needs_login`-style call omitting `client`/`model` still works for `fetcher_type='slack'` (regression check that the new optional params don't break the existing call site).
9. Migration test: existing DB missing `'generic_listing'` from the `sources.fetcher_type` CHECK can, after `init_db`, insert one without a constraint violation; pre-existing rows of every other type survive the rebuild.
10. `sources/index.html` route test: the "Add source" form's `<select>` includes a `generic_listing` `<option>`.
11. Manual/browser verification: paste a real multi-posting career listing URL into add-by-url, confirm the detected-count panel appears with a sensible default name, confirm creates the source and streams in postings; re-visiting `/fetch` later and triggering it again picks up newly-posted jobs without re-adding ones already tracked.
