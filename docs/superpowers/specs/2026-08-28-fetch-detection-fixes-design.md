# Fetch detection fixes: detect_listing truncation, Playwright escalation, URL canonicalization

## Goal

Three independent defects in the `generic_listing` / site-detection path, all
confirmed against live pages plus the local model:

- **A** — `detect_listing` echoes a full URL per job link; more than ~15 links
  overflow `max_tokens=2000`, `json.loads` throws, and a bare `except` returns
  "not a listing". Silent. Bouvet's ReachMee page (model correctly found 32
  links) and LinkedIn both fail this way.
- **B** — `generic_listing` only escalates to a Playwright render when the raw
  HTML has under 200 chars of text. Accenture's JS-rendered listing ships 7135
  chars of nav/footer boilerplate and zero job links, so it never renders.
- **C** — job URLs are dedup'd by exact string match. LinkedIn (and any ATS with
  rotating `refId` / `trackingId` / `position` query params) re-ingests every
  posting on every fetch.

## Current structure

- `app/ai/detect_listing.py` — `detect_listing(client, model, links, page_url)`.
  Prompt lists links as `"<url> — <anchor>"`, model returns
  `{"is_listing": bool, "job_links": ["<url>", ...]}`. Input truncated `[:8000]`,
  `max_tokens=2000`. Any exception → `{"is_listing": False, "job_links": []}`.
- The fetch → thin-check → render → `extract_links` → `detect_listing` sequence
  is duplicated in three places:
  - `app/fetchers/generic_listing.py` `GenericListingFetcher.fetch()` (lines 31–54)
  - `app/routes/sources.py` `_task_source_detect` (lines 111–129)
  - `app/routes/jobs.py` `_task_add_job` (lines 617–654)
- `app/db/queries.py` `get_all_job_urls` returns raw `jobs.url` values.
  `pipeline.run_fetch` dedup's with `q.url_exists(conn, raw.url)` and stores
  `raw.url` verbatim; fetchers pre-filter candidate hrefs against `known_urls`.

## Design

### A. Index-based `detect_listing` output

`app/ai/detect_listing.py`:

- Prompt presents links as a numbered list: `0. <anchor text> [<url>]`.
- Model returns `{"is_listing": <bool>, "job_ids": [<int>, ...]}` — list indices,
  not URLs.
- Map indices back to hrefs from the caller-supplied `links` list. Ignore any
  index out of range. Drops the current `valid_hrefs` membership filter — an
  in-range index is inherently valid.
- `max_tokens` 2000 → 4000 (trivially covers 200 integer indices).
- Input cap `[:8000]` → `[:16000]` (~100 links with long URLs).
- On `json.loads` / parse failure: `logger.warning("detect_listing: unparseable
  response for %s: %r", page_url, content)` then return
  `{"is_listing": False, "job_links": []}` as today. No silent swallow.

Public return shape (`{"is_listing", "job_links"}`) is unchanged, so callers are
untouched by this fix on its own.

Future extension point (not built now): the same index scheme makes it cheap to
add an optional `next_page_id` for hyperlink pagination later.

### B. Shared detection helper with post-detection escalation

New module `app/fetchers/listing_detect.py`:

```python
@dataclass
class ListingDetection:
    html: str            # final HTML (rendered if escalation happened)
    is_listing: bool
    job_links: list[str]
    rendered: bool        # whether a Playwright render was used

def detect_listing_page(client, model, url) -> ListingDetection: ...
```

Steps:

1. `html = fetch_url_html(url)` — `FetchError` propagates to the caller.
2. Thin-check (`not has_enough_content(html)`) → `render_html(url)` once; keep the
   rendered HTML if it clears the bar. Set `rendered`.
3. `links = extract_links(html, url)`; `result = detect_listing(...)`.
4. If `not result["is_listing"]` or `len(result["job_links"]) < 2`: render (skip
   if step 2 already did), re-run `extract_links` + `detect_listing` on the
   rendered HTML, and keep whichever of the two results has more `job_links`.
   Update `html` / `rendered` if the rendered pass won.
5. Return `ListingDetection`.

Rewire the three call sites onto the helper, removing the triplicated inline
sequence:

- `GenericListingFetcher.fetch()` — replace lines 31–54 with a
  `detect_listing_page` call; keep its own `try/except` that logs and returns
  `[]` on `FetchError` / other failure. Downstream detail-page loop unchanged.
- `_task_source_detect` — use the helper's `html` for `extract_page_title`, and
  `is_listing` / `job_links` for the `>= 2` decision. Keep the existing
  `except FetchError: fetcher_type = "generic_listing"` fallback.
- `_task_add_job` — use the helper's `html` for the non-listing branch
  (`extract_text_or_raise`) and its detection for the listing branch. Keep the
  existing `except FetchError: _insert_error_job(...)` handling.

No new render cap needed — the listing-level render is at most one per call.
`MAX_PLAYWRIGHT_FALLBACKS` (detail-page renders) is unrelated and unchanged.

### C. Job URL canonicalization

New module `app/url_canon.py`:

```python
def canonicalize_url(url: str) -> str: ...
```

- Remove query params: any `utm_*` (prefix match), and exact names `gclid`,
  `fbclid`, `msclkid`, `mc_cid`, `mc_eid`, `_hsenc`, `_hsmi`, `trk`,
  `trackingId`, `refId`, `position`, `pageNum`, `originToLandingJobPostings`,
  `lipi`, `licu`, `recommendedFlavor`.
- Keep every other param (`job_id`, `site`, `validator`, `page`, …), preserving
  original order.
- Lowercase the host. Drop an empty/trailing `?`. **Keep the fragment** —
  Slack permalinks (`.../archives/C123#<ts>`) carry the message identity there,
  and tracking junk only ever lives in the query string.
- Idempotent. Domain-agnostic — no per-site branches.
- Non-HTTP(S) or unparseable input returns unchanged.

Applied at:

- **Chokepoint (correctness):** `pipeline.run_fetch` and `pipeline.run_add_job`
  — canonicalize the incoming URL before the `url_exists` check and before
  `insert_job`.
- **Efficiency:** `q.get_all_job_urls` returns a canonicalized `frozenset`, and
  each fetcher (`GenericListingFetcher`, `FinnListingFetcher`, `SlackFetcher`)
  canonicalizes a candidate href before the `known_urls` membership test, so
  detail pages already held are not re-fetched.
- `sources.url` is **out of scope** — user-entered listing pages.

**Migration** (one-shot, hard-downtime per `CLAUDE.md`): rewrite existing
`jobs.url` to canonical form. On collision (two rows canonicalize to the same
value) keep the lowest `id` and delete the rest. Expected to affect ~0 rows today
(LinkedIn is not a registered source), but keeps the invariant true.

## Tests

- `tests/test_url_canon.py` — new. Param stripping (exact + `utm_*` prefix),
  param-order preservation, host lowercasing, fragment drop, empty-query drop,
  idempotency, non-HTTP passthrough.
- `tests/test_detect_listing.py` — add: index→href mapping; out-of-range index
  ignored; unparseable response logs a warning and returns the empty result.
  Adjust existing cases to the numbered-list prompt / `job_ids` output.
- `tests/test_listing_detect.py` — new. With `render_html` and `detect_listing`
  stubbed: raw HTML is a listing (≥2 links) → no render; raw HTML thin → renders;
  raw HTML non-thin but `< 2` job_links → escalates and keeps the better result;
  `FetchError` propagates.
- `tests/test_fetcher_generic_listing.py` — add the Accenture-shaped case
  (raw HTML non-thin, zero job links → render path taken, links from rendered
  HTML).
- Migration test — colliding rows collapse to the lowest `id`.
- Full suite green.

## Follow-ups (added during review)

**Missing Playwright browser.** A missing browser binary (`playwright install`
never run) surfaced only as a stack trace, and every render silently degraded.
Now: `playwright_pool` recognises that specific launch error, sets a
`browser_missing` flag (cleared on the next successful launch), and logs one
concise actionable WARNING instead of a traceback. `task_engine.execute_task`
syncs a single unresolved `browser_missing` inbox item to that flag after every
task — it appears on the home page once a render is actually attempted and
auto-resolves once the browser works.

**Stale / duplicated add-source prompts** (BACKLOG). Completing an add (confirm,
add-as-job, add-as-source) or re-running detect for the same URL left the
"New source detected" follow-up item open, and repeated detects stacked
duplicates. New `queries.resolve_source_prompts_for_url(url)` resolves the
`task_followup` items (from `source_detect` / `job_add_by_url` tasks) for a URL;
called at the end of `_task_source_detect` (supersede), and on every terminal
outcome of `_task_source_confirm`, `_task_job_add_listing_source`,
`_task_job_add_by_url`.

**Mismatch panel layout** (BACKLOG). `sources/_detect_mismatch.html` rendered its
error text and action buttons on one flex line inside the add-source form.
Wrapped in a `flex-direction:column` block so the error sits on its own line
with the buttons beneath.

## Out of scope

- **Pagination.** `generic_listing` still fetches only page 1. The named backlog
  cases (Sopra Steria, Tietoevry) use JS-driven pagination, not hyperlink
  pagers, so the cheap fix would not help them. Stays a separate backlog item.
- **sixrobotics.com/careers** — the page currently has zero openings and is
  JS-rendered; nothing to extract. Not addressed here.
- **LinkedIn as a source type** — cross-posting dedup (same job, different URLs
  across LinkedIn / company site / finn) is a content-level concern, separate
  from URL canonicalization. Not addressed.
- Any HTTP/render response caching or the detect→confirm double-fetch. Discussed
  and deferred.
