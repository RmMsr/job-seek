# Backlog

Known gaps, not yet scheduled.

- fetch: **`generic_listing` has no pagination.** Sopra Steria and Tieto both cap
  at exactly 10 postings (their page-1 size) — later pages are never fetched.
  (JS-driven pagination, not hyperlink pagers — needs Playwright click/scroll)
- integration: mcp server
- doc: Create a list of supported job board examples for user documentation and regression testing.
- ux: Full text search on job page. Role and org matches rank higher than description.
