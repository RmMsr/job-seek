# Backlog

Known gaps, not yet scheduled.

Medium:
- Allow manual tags and filling missing tags like: Missing organization (just 1 per job) free re-usable tags like "Language: English". Interface should make it easy to find existing tags to redude duplicates
- changes in job status like re-processing of scenarios should lead also in a refresh. In case the user initiated a single action, there should appear a question if the job should be moved and optional the view should follow so the job is still visible.

Triage:
- Generating a job preview repeatedly fails in cutting off the new CV at the correct marker and returning the "End of CV" header or other content of the prompt.

Maybe
- fetch: **`generic_listing` has no pagination.** Sopra Steria and Tieto both cap
  at exactly 10 postings (their page-1 size) — later pages are never fetched.
  (JS-driven pagination, not hyperlink pagers — needs Playwright click/scroll)
- Add MCP server
