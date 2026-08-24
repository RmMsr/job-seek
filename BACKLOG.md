# Backlog

Known gaps, not yet scheduled.

- fetch: **`generic_listing` has no pagination.** Sopra Steria and Tieto both cap
  at exactly 10 postings (their page-1 size) — later pages are never fetched.
- fetch: **`generic_listing` doesn't escalate to Playwright when it should.**
  Accenture's listing is JS-rendered; the raw HTML has enough boilerplate
  text to fool the "is this page thin?" check, so it never renders. Confirmed
  a Playwright render of the same URL does surface the real job links.
- fetch: **Bouvet's ReachMee "job news feed" page** (`web106.reachmee.com/...`)
  returns 0 postings, same JS-rendering issue. Using
  `bouvet.no/ledige-stillinger` instead for now (works fine, static HTML).
  A dedicated ReachMee adapter might be worth it later since other Norwegian
  employers use the same ATS.
