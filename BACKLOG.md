# Backlog

Known gaps, not yet scheduled.

- feature: Make last run info a link to the correspondingfetch task. Adding a symbol for success/failure
- feature: Allow destructive scenario deletion.
- ux: shorten URLs on sources page. show a "expand" action to reveal the full url and have a "open" link.
- ux: the org filter dropdown should shorten the values so the input stays reasonable short
- fetch: **`generic_listing` has no pagination.** Sopra Steria and Tieto both cap
  at exactly 10 postings (their page-1 size) — later pages are never fetched.
  (JS-driven pagination, not hyperlink pagers — needs Playwright click/scroll)
- ux: task list sub entries entries should be indented
- ux: Add a (None) org filter, rename the Manual source to "(Single / None). Same pattern for (All)
- ux: Add ordering switch between score, age and change. New jobs default to higher score up. Everythin else to newest changes up.
- integration: mcp server
- doc: Create a list of supported job board examples for user documentation and regression testing.
- ux: Full text search on job page. Role and org matches rank higher than description.
- ux: A job card accept/reject should fold it with animation
