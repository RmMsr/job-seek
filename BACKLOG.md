# Backlog

Known gaps, not yet scheduled.

- fetch: **`generic_listing` doesn't escalate to Playwright when it should.**
  Accenture's listing is JS-rendered; the raw HTML has enough boilerplate
  text to fool the "is this page thin?" check, so it never renders. Confirmed
  a Playwright render of the same URL does surface the real job links.
- fetch: linkedin
- integration: mcp server
- doc: Bsd 2-clause license
- fetch: **`generic_listing` has no pagination.** Sopra Steria and Tieto both cap
  at exactly 10 postings (their page-1 size) — later pages are never fetched.
- fetch: **Bouvet's ReachMee "job news feed" page** (`web106.reachmee.com/...`)
  returns 0 postings, same JS-rendering issue. Using
  `bouvet.no/ledige-stillinger` instead for now (works fine, static HTML).
  A dedicated ReachMee adapter might be worth it later since other Norwegian
  employers use the same ATS.
- bug: Add profile entry is broken for adding an list item to a non final ## heading with newline
- ux: Rework Tasks. use checkbox list style instead of dismiss.
- fix: Bottom Statusbar overlaps with bulk update form. ensure no overlap and make status line dismissable
- ux: Link via the name column from fetch page to souces with highlight on target page
- ux: A single job task like reevaluate should link back the the job on the status and on the task details page
