# LinkedIn detail-page fetch via the guest posting endpoint

## Problem

Jobs sourced from LinkedIn sometimes store a login/consent wall as their content
instead of the job description (e.g. job 85: "Doctolib — Senior ML Engineer",
classified `lead`, body is French cookie-consent + sign-in text).

Two compounding causes:

1. **The `/jobs/view/<slug>-<id>` page is structurally unusable for our pipeline.**
   Even a non-walled fetch is ~19k chars of stripped text where the description
   starts only at ~char 4,500. `classify()` reads the first 4,000 chars and
   `summarize()` the first 6,000 — so the classifier sees nothing but
   cookie/login boilerplate and labels it `lead`.
2. **LinkedIn intermittently serves brick a pure login wall** (bot detection when
   the listing fetcher pulls many detail pages in a row) — no description at all.

`https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/<id>` returns an ~8k-char
fragment with the description in the first ~1k — same logged-out endpoint family
we already rewrite the *search* URL to (`app/url_rewrite.py`).

A second, source-agnostic issue surfaced by this: `summarize()` returns
`simplified_content` **verbatim** as the summary for every `lead`. That is
intentional for Slack (short human messages) but means a bad web scrape dumps raw
page chrome into the job body.

## Change

### 1. Fetch-time LinkedIn detail-URL rewrite

New helper in `app/url_rewrite.py`:

```python
def linkedin_guest_posting_url(url: str) -> str | None
```

- `*.linkedin.com/jobs/view/<slug>-<digits>` → `https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/<digits>`
- `*.linkedin.com/jobs/...?currentJobId=<digits>` → same
- anything else → `None`

The job id is the trailing digit run of the `/jobs/view/` path segment, or the
`currentJobId` query param.

Applied at the two HTTP entry points, rewriting **only the fetch target** — the
stored/displayed URL and `RawJob.url` are unchanged:

- `app/fetchers/http.py` — `HttpFetcher.fetch()` GETs the rewritten URL if the
  helper returns one; `_parse()` still builds `RawJob(url=self._source["url"], …)`.
- `app/fetchers/content.py` — `fetch_url_html()` GETs the rewritten URL (covers
  `detect_listing_page`, i.e. pasting a single LinkedIn job link via add-by-URL).

No change to `canonicalize_url`, stored URLs, dedup, or the "↗ original" link.

### 2. Lead raw-passthrough guard

`app/ai/summarize.py`: add a `raw_passthrough: bool` parameter, passed as
`is_slack` from `_ingest_posting` (`app/pipeline.py`).

- Slack lead → unchanged: summary is the retained original message.
- Non-Slack lead → falls through to the normal `_SYSTEM` summariser; the body is
  an AI summary, never raw page text.

## Testing

- `tests/test_url_rewrite.py` — `linkedin_guest_posting_url`: slug+id,
  `currentJobId`, subdomain host, non-LinkedIn URL, `/jobs/view/` with no id,
  already-guest URL.
- `tests/test_fetcher_http.py` — `HttpFetcher` GETs the rewritten guest URL for a
  LinkedIn view URL while `RawJob.url` stays the original; non-LinkedIn URL
  fetched unchanged.
- `tests/test_fetcher_content.py` (or existing content test) — `fetch_url_html`
  rewrites a LinkedIn view URL before the GET.
- `summarize` test — non-Slack lead returns a summarised body; Slack lead returns
  the raw message.

## Not in this change

- Cleaning up jobs already ingested with wall content (job 85 stays broken until
  its source is re-fetched).
- Extracting the posting date ("1 week ago") from the guest fragment into
  `published_at`.
- Any change to `canonicalize_url` / LinkedIn URL normalisation for storage.
