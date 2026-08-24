# Backlog

Known gaps, not yet scheduled.

- ux: Header "Onboarding" on start page
- ux: Lets organize the scenarios with a tab selector so every scenario gets its own page and can drop a border. the edit forms cancel/save get on a new line
- ux: Give all textareas the same padding as the profile input
- ux: Unified appearance of add source, job and scenario action. Add a Add action button below the header (1, position) with plus sign that reveals (animated) a full width input form with cancel/save actions.
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
