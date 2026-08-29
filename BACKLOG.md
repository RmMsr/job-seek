# Backlog

Known gaps, not yet scheduled.

- integration: mcp server
- doc: Bsd 2-clause license
- fetch: **`generic_listing` has no pagination.** Sopra Steria and Tieto both cap
  at exactly 10 postings (their page-1 size) — later pages are never fetched.
  (JS-driven pagination, not hyperlink pagers — needs Playwright click/scroll)
- bug: Add profile entry is broken for adding an list item to a non final ## heading with newline
- ux: the task detail page needs to be reorganised. What does the title mean. The form needs structure
- feature: unify the /sources/detect and /jobs/add-by-url add flows — shared
  detection pipeline, panels, and confirm task. Deferred from the LinkedIn
  URL-rewrite change; do it if the duplication starts to bite.
