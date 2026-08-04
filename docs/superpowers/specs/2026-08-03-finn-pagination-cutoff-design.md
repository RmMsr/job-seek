# Finn Listing Pagination & Age Cutoff — Design Spec

**Date:** 2026-08-03
**Status:** Approved

## Overview

`FinnListingFetcher` (`app/fetchers/finn.py`) currently only reads the single listing URL configured on the source — it never follows finn.no's pagination — and caps results at `_MAX_ADS = 20` by slicing whatever ad links happen to be on that first page. It has no notion of how old a posting is, and it re-fetches the detail HTML of every discovered ad on every run, including ads already stored in the database.

This spec adds real pagination, a hardcoded 30-day age cutoff (using the finn ad's actual published timestamp, confirmed present as `<time datetime="...">` on each listing card), and a fix for the redundant detail re-fetching that pagination would otherwise make worse. It also persists the published date so it can be shown in the UI.

Explicitly out of scope: decoupling job fetching from AI classification/summarization/evaluation so the latter can be retried independently per job. That's a separate, cross-cutting pipeline change (affects every fetcher type, not just finn) and will get its own spec.

---

## 1. Age cutoff helper (`app/fetchers/base.py`)

A small, source-agnostic helper next to `RawJob`/`Fetcher` so any future JS-listing-style fetcher can reuse it — the age-cutoff concept isn't finn-specific, only the scraping to extract the date is:

```python
def is_recent(published_at: str | None, max_age_days: int) -> bool:
    """True if published_at (ISO-8601) is within max_age_days of now, or unknown."""
```

- `published_at=None` (unknown date, e.g. other fetcher types) returns `True` — never filters out something we can't date.
- Parses with `datetime.fromisoformat` (handles the `Z` suffix finn.no uses; Python 3.12 is fine here) and compares against `datetime.now(timezone.utc)`.

## 2. `RawJob` gains `published_at`

```python
@dataclass
class RawJob:
    url: str
    title: str
    company: str
    raw_text: str
    published_at: str | None = None
```

Default `None` keeps every other fetcher (`http`, `playwright`, `slack`) unchanged — they simply never set it.

## 3. `FinnListingFetcher` rewrite (`app/fetchers/finn.py`)

### Constants (module level, same spot/style as today's `_MAX_ADS`)

```python
MAX_AGE_DAYS = 30          # ads published before this many days ago are ignored
MAX_LISTING_PAGES = 5      # hard cap on paginated listing pages walked per run
MAX_DETAIL_FETCHES = 50    # cap on new ad detail pages fetched per run
```

Each is a plain top-level constant with a one-line comment on what it controls, so it's obvious what to tune if real-world data shows the values are off. No config.toml/UI plumbing — this is a personal, single-instance app; bumping a constant and redeploying is the intended adjustment path.

### Constructor

```python
def __init__(self, source: dict, known_urls: frozenset[str] = frozenset()) -> None:
    self._source = source
    self._known_urls = known_urls
```

`known_urls` is the set of job URLs already in the database (see §5). Defaults to empty so existing direct construction (e.g. in tests) keeps working without change unless a test cares about the skip behavior.

### Discovery loop

`_discover_ad_urls` becomes `_discover_ads() -> list[tuple[str, str | None]]` returning `(url, published_at)` pairs, walking pages instead of loading one:

1. For `page` in `1..MAX_LISTING_PAGES`:
   - Build the page URL: parse `source["url"]` with `urllib.parse.urlsplit`/`parse_qsl`, set/replace the `page` query param, re-encode. (Page 1 uses the source URL as-is — finn.no accepts `page=1` explicitly too, but avoiding it keeps the first request identical to today's behavior.)
   - Load the page with the existing Playwright single-page-load logic (`networkidle` wait).
   - For each `a[href*='/job/ad/']`, find the closest ad-card container and read its `time` element's `datetime` attribute (mirrors the structure confirmed by manual inspection of the live site). Dedupe by href within and across pages using a running `seen` set (same pattern as today's `seen` list, now checked across the whole run, not per page).
   - Partition this page's newly-seen ads into `within_cutoff` (via `is_recent(published_at, MAX_AGE_DAYS)`) and stale.
2. Stop paginating (return what's been collected so far) at the first of:
   - This page's `within_cutoff` set is empty (only counting ads not already seen on an earlier page) — the listing has aged past the cutoff. A single stale sponsored card mixed in with fresh ones doesn't trigger this, since the rest of the page still has `within_cutoff` ads.
   - `MAX_LISTING_PAGES` pages have been walked.
   - The number of collected `within_cutoff` ads whose URL is *not* in `self._known_urls` reaches `MAX_DETAIL_FETCHES` — no reason to keep paginating once there's enough new work to fill this run's budget.
3. On any Playwright exception, same as today: return `[]` (caught once, at the top level, not per page).

### Fetch

```python
def fetch(self) -> list[RawJob]:
    ads = self._discover_ads()
    new_ads = [(url, pub) for url, pub in ads if url not in self._known_urls]
    jobs: list[RawJob] = []
    for url, published_at in new_ads[:MAX_DETAIL_FETCHES]:
        for job in HttpFetcher({"url": url}).fetch():
            job.published_at = published_at
            jobs.append(job)
    return jobs
```

Ads already in `known_urls` never reach `HttpFetcher` — no wasted request, and the `MAX_DETAIL_FETCHES` budget is spent only on genuinely new ads.

## 4. Persistence (`app/db/schema.py`, `app/db/queries.py`)

Additive migration, following the exact existing pattern for `headline` (`_migrate_jobs_add_headline`):

```python
def _migrate_jobs_add_published_at(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='jobs'"
    ).fetchone()
    if row is None or "published_at" in row[0]:
        return
    conn.execute("ALTER TABLE jobs ADD COLUMN published_at TEXT")
    conn.commit()
```

Called from `init_db` alongside the other `_migrate_*` calls. Also add the column to the `CREATE TABLE IF NOT EXISTS jobs (...)` DDL block (nullable, no default — unlike `headline` this has no sensible non-null default).

`insert_job` gains a `published_at: str | None = None` keyword parameter and includes it in the `INSERT`. No changes needed to `get_jobs`/`get_job`/`_BEST_SCORE_SELECT` — they already `SELECT jobs.*`, which picks up the new column automatically.

## 5. Wiring known URLs through (`app/db/queries.py`, `app/pipeline.py`)

New query:

```python
def get_all_job_urls(conn: sqlite3.Connection) -> frozenset[str]:
    rows = conn.execute("SELECT url FROM jobs").fetchall()
    return frozenset(r["url"] for r in rows)
```

(`jobs.url` is globally unique, not per-source, so this doesn't need a `source_id` filter.)

`_make_fetcher` gains a `conn` parameter, used only for the `finn_listing` branch:

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

`run_fetch`'s call site passes `conn` through (it already has it in scope).

`run_fetch`'s per-job insert call passes the new field through:

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

### Why this is enough for "resume after a failure"

If a run dies partway through processing (e.g. an unexpected exception outside the already-resilient AI calls), everything inserted so far is already committed per-job. The next run's pagination walk rediscovers the same candidates, `known_urls` now includes everything the previous run finished, so those are skipped for free, and the run's `MAX_DETAIL_FETCHES` budget goes straight to whatever's left. No separate checkpoint state is needed. (Making the AI-processing stage itself independently retryable per job is the deferred follow-up spec mentioned above.)

## 6. UI (`app/template_env.py`, `app/templates/jobs/_macros.html`)

New filter, registered next to the existing `markdown`/`markdown_text` ones:

```python
# app/template_env.py
from app.dates import time_ago
templates.env.filters["time_ago"] = time_ago
```

```python
# app/dates.py (new file)
def time_ago(published_at: str | None) -> str:
    """Render an ISO-8601 timestamp as a short relative string, e.g. '5 days ago'."""
```

Handles `None` by returning `""`. Buckets: `"today"`, `"1 day ago"`, `"N days ago"`; no need for week/month granularity given the 30-day cutoff this feeds from.

`meta_tags` macro (`app/templates/jobs/_macros.html`) gains one more `dt`/`dd` pair, rendered only when known:

```jinja
{% if job.published_at %}
<dt class="sr-only">Published</dt>
<dd><span class="tag">{{ job.published_at | time_ago }}</span></dd>
{% endif %}
```

Because both `_row.html` (card) and `_feedback.html` (detail) already call `macros.meta_tags(job)`, this appears in both automatically — no template duplication. Jobs from every non-finn source simply omit the tag, same as they already omit the score tag when unscored.

## 7. Testing

- `is_recent`: within cutoff → `True`; older than cutoff → `False`; `None` → `True`; boundary (exactly `max_age_days` old) — pick and test one consistent inclusive/exclusive behavior.
- `FinnListingFetcher`:
  - Existing tests (`test_discovers_and_fetches_each_ad`, `test_ignores_non_ad_links`, `test_returns_empty_list_on_playwright_error`) updated for the new mock shape (cards need a `time[datetime]` alongside each ad link) but keep asserting the same behaviors.
  - `test_caps_number_of_ads_fetched` updated for `MAX_DETAIL_FETCHES = 50` instead of the old 20.
  - New: pagination walks multiple pages when page 1 is exhausted but still within cutoff, stopping once a page's ads are all past `MAX_AGE_DAYS`.
  - New: stops at `MAX_LISTING_PAGES` even if every page is still within cutoff (mock all 5 pages as fresh).
  - New: stops early once enough new ads are collected to fill `MAX_DETAIL_FETCHES`, without walking further pages.
  - New: ads whose URL is in `known_urls` are excluded from the `HttpFetcher` calls entirely and don't count against `MAX_DETAIL_FETCHES`.
  - New: a stale sponsored-style card interleaved with fresh ones on the same page doesn't halt pagination.
- `q.get_all_job_urls`: returns the right set; empty DB → empty frozenset.
- `q.insert_job` / migration: `published_at` round-trips through insert → `get_job`; fresh DB creates the column via the `CREATE TABLE` DDL; existing DB (pre-migration) gets it added via `ALTER TABLE` without touching existing rows.
- `time_ago`: known buckets plus `None` → `""`.
- Macro/template: `published_at` present → tag renders in both card and detail; absent → no tag, no error.
- `pipeline.run_fetch`: `_make_fetcher` is called with `conn` and constructs `FinnListingFetcher` with `known_urls` populated from the DB for `finn_listing` sources; other fetcher types unaffected.
