# Backlog

Known gaps, not yet scheduled.

- ux: folding a job note that has been moved into another view, removed the link to the new location.
- ux: changing the job note or scenario note should re-enable the job for the feedback processing.
- ux: if the job headline could flow around the link/checkbox it would save some space on smaller screens.
- ux: on small screens the fetch page takes unnecessary much space vertically. also the navigation does not stick.
- ux: when Get suggestions finds something there should be a note pointing to the specific scenario. on multiple scenarios, there can be multiple notes
- ux: The start typing to select the detected model is a strange interface. lets have a simple input, but it can be fed by a list that appears after the model detection
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
