# Backlog

Known gaps, not yet scheduled.

- bug: Version hash on setup page seems stale. Also it should state UTC build time.
- feature: Make last run info a link to the correspondingfetch task. Adding a symbol for success/failure
- feature: Allow destructive scenario deletion.
- ux: shorten URLs on sources page. show a "expand" action to reveal the full url and have a "open" link.
- fetch: **`generic_listing` has no pagination.** Sopra Steria and Tieto both cap
  at exactly 10 postings (their page-1 size) — later pages are never fetched.
  (JS-driven pagination, not hyperlink pagers — needs Playwright click/scroll)
- integration: mcp server
- doc: Create a list of supported job board examples for user documentation and regression testing.
- ux: Full text search on job page. Role and org matches rank higher than description.
- ux: A job card accept/reject should fold it with animation
