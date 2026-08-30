# Paginated listing fetch (LinkedIn guest search and friends)

## Problem

A LinkedIn job-search URL is rewritten to the logged-out guest endpoint
`…/jobs-guest/jobs/api/seeMoreJobPostings/search?…&start=0` (`app/url_rewrite.py`)
and stored as a `generic_listing` source. `GenericListingFetcher` fetches that one
URL once. The guest endpoint returns exactly **10 job cards per call**; more
results are reached by paging `start=10`, `start=20`, … up to LinkedIn's ~1000
ceiling. We never page, so every LinkedIn source caps at 10 jobs regardless of how
many the search actually matches.

## Change

All in `app/fetchers/`. No DB, route, or `url_rewrite.py` changes.

### 1. New helper `app/fetchers/pagination.py`

```python
def next_page_url(url: str, page_size: int) -> str | None
```

Finds one pagination param in the query string, first match in this order wins:

| Param                      | Style  | Next value        |
|----------------------------|--------|-------------------|
| `start`, `offset`, `from`  | offset | current `+ page_size` |
| `page`                     | index  | current `+ 1`     |

- Returns `None` when no recognized param is present, or `page_size == 0`.
- Rewrites only that one param; every other query param and the URL fragment is
  preserved. Param value is parsed as an int; non-int value → `None`.
- No LinkedIn-specific branch: `start=0` with a 10-card page becomes `start=10`
  via the generic offset rule.

### 2. Pagination loop in `GenericListingFetcher.fetch()`

New constant `MAX_LISTING_PAGES = 5`.

- **Page 1**: `detect_listing_page(client, model, url)` exactly as today. Not a
  listing → return `[]` (unchanged). `FetchError` / unexpected exception handling
  unchanged.
- `page1_count` = number of canonical job links found on page 1.
- If `page1_count < MIN_JOB_LINKS` (2) → do not paginate; behave as today.
- Otherwise walk pages 2…`MAX_LISTING_PAGES`. For each, compute
  `next_page_url(previous_page_url, page1_count)` and call `detect_listing_page`
  on it. Accumulate job links into an order-preserving dedup structure across all
  pages (a dict keyed by canonical URL, or list + seen-set).

**Stop the walk when any of:**

1. `next_page_url` returns `None`.
2. The new page's canonical links are all already in the seen-set — a
   non-paginated site that ignores the param and keeps returning page 1.
3. The new page yields fewer links than `page1_count` — a partial page is the
   last page. (Process that page's links first, then stop.)
4. The new page contributes zero URLs that aren't already in `known_urls`.
5. Accumulated new-vs-`known_urls` links reach `MAX_DETAIL_FETCHES` (50).
6. `detect_listing_page` on a page-2+ URL raises `FetchError` or the page is not
   a listing — keep what earlier pages produced, stop.

Stride note: `page_size` passed to `next_page_url` is always `page1_count`, fixed
for the whole walk. Later pages' link counts are only compared against it
(stop rule 3), never fed back into the offset — LLM extraction can under-count a
page by a card or two and a per-page stride would compound that into
skips/overlaps.

- After the loop, the existing detail-fetch code runs unchanged over the deduped,
  `known_urls`-filtered link list (still capped at `MAX_DETAIL_FETCHES` detail
  fetches and `MAX_PLAYWRIGHT_FALLBACKS` = 10 Playwright renders per run).

`app/fetchers/listing_detect.py` / `detect_listing_page` are **not** touched. For
the LinkedIn guest fragment the raw-HTML path already classifies as a listing
with no Playwright escalation, so the walk stays at plain httpx + one
`detect_listing` LLM call per page, ≤ 5 per run. A rare last page with 1 link may
cost one wasted render via the existing escalation; acceptable and bounded.

### 3. Inter-page delay + one retry on an empty page (revision)

Manual testing against the real LinkedIn guest endpoint
(`seeMoreJobPostings/search`) showed it intermittently answers a 200 with an
empty body (`<!DOCTYPE html>\n\n<!---->`, 26 bytes, zero links) when hit rapidly
— a soft rate-limit, **not** the end of results (later offsets return full
pages again). Walking pages back-to-back with no delay tripped this on page 3
and stop rule 6 ended the walk at ~20 jobs.

Generic mitigation in the pagination loop:

- **`PAGE_FETCH_DELAY_SECONDS = 2.0`** — sleep this long before every page-2+
  request (not before page 1, which `fetch()` already did).
- **One retry when a page comes back empty** — if `detect_listing_page` raises
  `FetchError`, or returns `not is_listing`, or returns zero job links: wait
  `PAGE_FETCH_DELAY_SECONDS` and try that same URL once more. If the retry is
  also empty, the walk stops (stop rule 6, unchanged).

Encapsulated in a `GenericListingFetcher._fetch_listing_page(url)` helper
returning `ListingDetection | None` (`None` = give up on this page → stop the
walk). No new dependency on the empty-body byte signature — "empty" is defined
by the detection result, so the retry helps any flaky paginated listing, not
just LinkedIn.

Not addressed (accepted limitations, keeping this generic):

- Deep pagination past a *run* of empty pages — the walk still stops after one
  failed retry, so a search with scattered rate-limit gaps yields fewer than its
  full result set. `MAX_LISTING_PAGES = 5` also caps it well before LinkedIn's
  ~1000 ceiling.
- Stop rule 3 (short page = last page) still fires on LinkedIn pages that
  legitimately return < `page1_count` links mid-results.

## Testing

New `tests/test_fetcher_pagination.py` — `next_page_url`:

- `start`/`offset`/`from` incremented by `page_size`; `page` incremented by 1.
- First-match ordering when several are present.
- No recognized param → `None`; `page_size == 0` → `None`; non-int value → `None`.
- Other query params and the `#fragment` preserved.

Added cases in `tests/test_fetcher_generic_listing.py` (mock `detect_listing_page`
per-URL):

- Walks `start=0` → `start=10` → short page at `start=20`, then stops; every
  detail URL across all three pages is fetched.
- Stops at `MAX_LISTING_PAGES = 5` when every page returns a full set of new
  links; page 6 is never requested.
- Mis-page guard: page 2 returns the same links as page 1 → walk stops, no
  duplicate detail fetches.
- URL with no pagination param → single `detect_listing_page` call, behavior
  identical to today.
- `page1_count < MIN_JOB_LINKS` → single page, no pagination.
- Early stop when a whole page is already in `known_urls`.
- Inter-page delay: `time.sleep` is patched; assert it's called with
  `PAGE_FETCH_DELAY_SECONDS` before each page-2+ fetch, never before page 1.
- Empty-page retry: page 2 returns an empty/`not is_listing` detection on the
  first call and a full page on the second → walk continues, links from the
  retry are collected.
- Empty-page retry exhausted: both calls for page 2 come back empty → walk stops,
  earlier pages' links retained.

## Not in this change

- Generic per-site step-size inference beyond "increment offset by page-1 size".
- Pagination for the `finn_listing` or `slack` fetchers (finn already walks its
  own `page` param).
- Extracting `published_at` from listing fragments.
- Raising `MAX_LISTING_PAGES` or `MAX_DETAIL_FETCHES`.
