# Backlog

Known gaps, not yet scheduled.

- ux: Add a save button for job notes.
- fix: a status change does not need to repeat the note content into history
- ux: A jonb text search should still allow to multi-select jobs
- fix: /fetch page action buttons should be in line with the title
- ux: a finished task should primarily state the duration after that it finished, not the time stamp
- observability: Add tracing of llm calls
- ux: tasks like refresh or fetch should report back links to relevant changed items. Drop the "Open" keyword.
- ux: An unreachable inference endpoint has to count as error. for example on the cv_plan.
- fix: a task on a single job or source like "Add job by URL" should lik to the final result, single job revisit should link to that. Multiple jobs revisits should lik to them also stating the change. Log lines should include job ids.
- fetch: **`generic_listing` has no pagination.** Sopra Steria and Tieto both cap
  at exactly 10 postings (their page-1 size) — later pages are never fetched.
  (JS-driven pagination, not hyperlink pagers — needs Playwright click/scroll)
- integration: mcp server
- doc: Create a list of supported job board examples for user documentation and regression testing.
