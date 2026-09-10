# Supported job boards

What the fetchers in `app/fetchers/` currently handle. Add a source by URL — Job
Seek picks the fetcher automatically (host-based for the known boards, otherwise
an LLM look at the page's links; see `app/ai/classify_known_source.py` and
`app/fetchers/listing_detect.py`).

| Board / site | Fetcher | Example URL | Notes |
|---|---|---|---|
| finn.no | `finn_listing` | `https://www.finn.no/job/search?occupation=1.23` | Server-rendered search/listing page; individual `finn.no/job/ad/…` pages are scraped directly. Hyperlink pagers are followed. |
| 80,000 Hours | `eawork_listing` | `https://jobs.80000hours.org/?query=ml&salary-limit=100000` | Hosts `jobs.80000hours.org`, `eawork.org`. Reads the board's JSON API, so query/refinement params in the URL are honoured. |
| Generic listing pages | `generic_listing` | any careers page / board search with ≥2 job links | Links are classified by the LLM. Thin detail pages fall back to a Playwright render (max 10 per run). **JS-paginated sites (Sopra Steria, Tieto) cap at the first page — about 10 postings** (no hyperlink pager to follow — see `BACKLOG.md`). |
| Slack community channels | `slack` | a channel/message URL, plus the `d` cookie | Cookie-only, no browser needed (`docs`/memory: xoxc token is in the messages-page HTML). Lead messages are kept verbatim. |
| LinkedIn | URL rewrite → `generic_listing` | `https://www.linkedin.com/jobs/search/?keywords=ai&location=Oslo` | Search URLs are rewritten to the guest `seeMoreJobPostings` endpoint; individual postings rewrite to the guest `jobPosting` view. See `app/url_rewrite.py`. |
| Any single posting | add-job-by-URL | any job posting URL | One-off: fetches and processes that page only (Playwright render if the static HTML is thin). |
