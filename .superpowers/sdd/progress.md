# Progress ledger — pending-job-status

Plan: docs/superpowers/plans/2026-09-14-pending-job-status.md
Base before implementation: b7bc692

- [x] Task 1: Widen jobs.status to allow 'pending' (commits b7bc692..afff8d1, review clean; Minor note on substring idempotency check, no fix needed)
- [x] Task 2: Add pending to the jobs tab list (commits afff8d1..8a4f6d8, review clean after 1 fix round)
      - fix 8a4f6d8: plan's test wrongly asserted from_params sorts by VALID_TABS order; reverted to input-order preservation (pre-existing, deliberate behavior), corrected the test, left with_status_toggled untouched
- [x] Task 3: pending in tab predicates and counts (queries.py) (commits 8a4f6d8..5020467, review clean)
- [x] Task 4: Freeze pending jobs out of per-job re-evaluation (commits 5020467..e42caf4, review clean)
- [x] Task 5: pending in stale badges, search seeding, and the status-change revisit trigger (commits e42caf4..a57fbc3, review clean)
      - implementer caught a 2nd plan bug: test_search_tab_bar_shows_seeded_scope active-count can't bump 4->5 yet (tab_defs has no "pending" entry until Task 7); kept it at 4 with explanatory comment, only fixed the toggle-URL sub-assertion. Plan's Task 7 section updated to bump this test 4->5 once tab_defs gets its "pending" entry.
- [x] Task 6: pending in the onboarding checklist (routes/home.py) (commits a57fbc3..65715ff, review clean)
- [x] Task 7: "Mark pending" button, status pill, and tab in the UI (commits 65715ff..bc0b572, review clean)

All 7 tasks complete. Final whole-branch review (commits 07e2165..bc0b572): 2 Important findings.
- [x] Fix 1: _counts_oob.html was missing a count-pending span (stale tab count after htmx organize actions) (commit fd3e84e)
- [x] Fix 2: marking pending fired the one-off status-change revisit check, which could auto-trash a job once its posting naturally disappeared post-application. User decided: drop pending from that trigger entirely, for full freeze uniformity (commit 5e8019e)
Both fixes re-reviewed clean (commits bc0b572..5e8019e, ready to merge).
Next: update spec doc for the fix-2 design correction, manual UI verification, then finishing-a-development-branch.
