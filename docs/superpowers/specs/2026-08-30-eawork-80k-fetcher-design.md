# eawork / 80,000 Hours job board fetcher

## Problem

The 80,000 Hours job board (`jobs.80000hours.org`) is a Nuxt SPA backed by an
Algolia index. `GenericListingFetcher` finds zero jobs for it: the raw HTML has
no job links, and even after a Playwright render each job card is a `<button>`
with JS-driven navigation and no `<a href>` to the posting. The only per-card
anchor points at `app.80000hours.org/job/conversation/?jobId=N` ("Discuss this
opportunity with AI"), which is itself a JS SPA. So the crawler never sees a
real posting URL.

## Approach

A dedicated `eawork_listing` fetcher that queries Algolia directly — the same
requests the board's own frontend makes — and maps the structured records
(which carry the real ATS apply URL) to `RawJob`s. Mirrors the existing
`finn_listing` carve-out: a recognized host gets a fetcher that interprets the
stored human URL.

Applies to `jobs.80000hours.org` and `eawork.org` (same backend).

Scope: the fetcher plus its wiring. No pipeline, scoring, or UI changes.

### Usage-policy note

The Algolia key is a search-only key 80k ships in its public client JS; our
requests are byte-identical to the board's own. `robots.txt` disallows nothing;
the ToS has no anti-automation clause and its "personal, non-commercial, not
providing third parties with their own access" clause describes this use. We
scrape the key from the page at runtime (always the current public config)
rather than committing a copied key. One query per fetch run. No custom
User-Agent — default client behaviour, no added fingerprint.

## URL recognition & schema

- **Stored source URL stays human**: e.g.
  `https://jobs.80000hours.org/?refinementList[tags_area][0]=AI safety & policy&...`
  — bookmarkable, browser-editable, re-pasteable.
- `app/ai/classify_known_source.py`: `host in {"jobs.80000hours.org",
  "eawork.org", "www.eawork.org"}` → `"eawork_listing"`; add `"eawork_listing"`
  to `DETECTABLE_FETCHER_TYPES`. Pasting the URL classifies instantly — no LLM,
  no render.
- `app/pipeline.py` `_make_fetcher`: add the branch (no `client`/`model` needed).
- **Schema**: `sources.fetcher_type` CHECK constraint gains `'eawork_listing'`.
  New idempotent migration `_migrate_sources_fetcher_type_eawork_listing` in
  `schema.py` — table rebuild (the established pattern for this CHECK) — and in
  the same migration flip the existing 80k row (`url LIKE
  'https://jobs.80000hours.org/%'`, currently `generic_listing`) to
  `eawork_listing`. Hard-downtime style per the project's migration philosophy.
  Also update the `_DDL` `CREATE TABLE sources` CHECK list.

## Fetcher — `app/fetchers/eawork.py`

`EaworkListingFetcher(source: dict, known_urls: frozenset[str] = frozenset())`.
No LLM client. `fetch() -> list[RawJob]`:

1. **Scrape Algolia config.** `httpx.get` the source URL's origin
   (`https://jobs.80000hours.org/`). Regex out `algoliaApplicationId`,
   `algoliaApiKey`, `algoliaJobsIndex` (`"jobs_prod"`) from the inline Nuxt
   runtime config (`...algoliaApiKey:"<hex>",algoliaApplicationId:"<id>"...`,
   `algoliaJobsIndex:"jobs_prod"`). Any missing → `FetchError`.
2. **Translate filters.** Parse `refinementList[<facet>][<i>]=<value>` query
   params from the stored URL, group by facet, preserve value order:
   `facetFilters = [[f"{facet}:{v}" for v in values] for facet, values in groups]`
   (OR within a facet, AND across — Algolia semantics). Params that are not
   `refinementList[...]` (`salary-limit`, sort keys, `page`, ...) are ignored.
   No `refinementList` params → omit `facetFilters` (match-all).
3. **Query.** One `httpx.post` to
   `https://{appId}-dsn.algolia.net/1/indexes/{index}/query`, headers
   `x-algolia-application-id`, `x-algolia-api-key`, `content-type:
   application/json`; body `{"params": "query=&hitsPerPage=1000[&facetFilters=<url-encoded JSON>]"}`.
   Default relevance order (the board's own ranking). Non-200 → `FetchError`.
   If `nbHits > 1000`, process the first 1000 and `logger.warning` that results
   were truncated.
4. **Map hits → `RawJob`.** Skip any hit whose canonicalized `url_external` is
   in `known_urls` or is missing. Cap at `MAX_RESULTS = 50` new jobs per run
   (matching `finn`).

Network error / non-200 / JSON parse failure / missing config →
`logger.warning(...)` + return `[]`, consistent with `GenericListingFetcher`.

### `raw_text` composition (no per-job fetch)

Per hit, a plain-text block (HTML stripped with BeautifulSoup); omit any
line whose value is empty:

```
{title} — {company_name}

{description_short or description}

Problem areas: {tags_area joined}
Skills: {tags_skill joined}
Role type: {tags_role_type joined}
Location: {tags_city}, {tags_country} / {tags_location_80k joined}
Experience: {tags_exp_required joined} (min {experience_min} yrs)
Salary: {salary}

About {company_name}: {company_description}

Apply: {url_external}
```

`RawJob(url=url_external, title=title, company=company_name,
raw_text=<block>, published_at=<ISO-8601 from posted_at epoch seconds>)`.
`posted_at <= 0` or non-numeric → `published_at=None`.

Downstream `simplify → classify → summarize → evaluate → assess_fit` run
unchanged; the tag lines carry the scoring signal.

## Edge cases

| Case | Behaviour |
|---|---|
| Config keys absent from page HTML | `FetchError` → `[]` + warning |
| Algolia 4xx/5xx (e.g. rotated key) | `[]` + warning |
| `nbHits > 1000` | first 1000, log truncation |
| Filter matches 0 jobs | `[]`, no error |
| `url_external` missing on a hit | skip that hit |
| `refinementList` params reordered in URL | same `facetFilters` set → same results; dedup on canonical `url_external` regardless |

## Testing — `tests/test_fetcher_eawork.py` (`respx`-mocked)

- Config extraction: happy path from a saved board-HTML fixture; missing key →
  `FetchError` → `[]` + warning.
- Filter translation (pure function, tested directly): single facet, multi-value
  OR, multi-facet AND, non-`refinementList` params ignored, no params →
  match-all.
- Hit → `RawJob`: field mapping, HTML stripping, epoch→ISO, empty fields
  omitted, bad epoch → `None`.
- `known_urls` dedup on canonicalized `url_external`.
- `MAX_RESULTS` cap.
- Algolia non-200 → `[]` + logged warning.
- `classify_known_source` → `eawork_listing` for `jobs.80000hours.org` and
  `eawork.org`.
- Migration: old-shape `sources` row with `generic_listing` for the 80k URL →
  after `init_db`, CHECK allows `eawork_listing` and the row is flipped.

Fixtures: one trimmed Algolia response JSON (~3 hits, varied completeness), one
board-HTML snippet containing the Nuxt config line.
