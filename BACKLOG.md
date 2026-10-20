# Backlog

Known gaps, not yet scheduled.

Triage:
- Generating a job preview repeatedly fails in cutting off the new CV at the correct marker and returning the "End of CV" header or other content of the prompt.

Maybe
- fetch: **`generic_listing` has no pagination.** Sopra Steria and Tieto both cap
  at exactly 10 postings (their page-1 size) — later pages are never fetched.
  (JS-driven pagination, not hyperlink pagers — needs Playwright click/scroll)
- Add MCP server
