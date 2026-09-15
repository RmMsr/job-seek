# Progress ledger — cv-version-history

Plan: docs/superpowers/plans/2026-09-14-cv-version-history.md
Base before implementation: 8807e6e

- [x] Task 1: Schema — cv_versions table and the content-column migration (commits 8807e6e..1e0e2a7, review clean after 1 fix round — removed defensive guards for an impossible mismatched-schema state, fixed 2 pre-existing tests that manufactured it)
- [x] Task 2: Query layer — version recording, pruning, and rewritten CV accessors (commits 1e0e2a7..1efdfae, review clean)
      - Minor (deferred to final review): revert-then-quick-edit could stack onto a reverted historical row (follows spec letter, untested edge case); no test for update->manual_edit boundary; accept/unaccept fns + current_version_id lookups have some duplication
- [x] Task 3: Drop the accept-freeze gate; resolve base CV for tailoring (commit b7b2f62, review clean)
      - Full-suite sweep also caught: removing `_require_editable` dropped an incidental 404-for-missing-job check on 9 routes (it did double duty); restored via a new `_require_job` helper on all 9 (3 had tests catching the gap; the rest fixed for consistency)
      - Also fixed `tests/test_routes_cv_preview.py::test_accepted_view_has_three_tabs` (uncalled-out by the brief but broken by the freeze removal) — Task 5's plan text independently confirms this exact test/fix
      - Review confirmed clean (no Critical/Important). Minor: preview.html/_accepted.html still reference finalized_at but the branch is dead (always falsy) — harmless now, but verify Task 5 actually deletes/replaces both as its own brief specifies
- [x] Task 4: New cv_versions.py router — revert (both entities), base CV accept/unaccept (commit 5335d46, review clean)
      - Implementer subagent hit a rate-limit mid-task (uncommitted work recovered by controller); found+fixed a plan-bug in test_revert_base_cv_version_repoints_current (2 consecutive save_cv_settings stack within 1h, needs backdating) — same bug pre-emptively fixed in plan doc's Task 7 tests (commit 4851cd6)
- [x] Task 5: ?version= on the tailored-CV preview/diff/PDF routes, and a repurposed read-only view (commit 4f6405b, review clean)
      - Fixed an unsatisfiable plan test assertion ('cv-preview-editor' is global CSS, present on every page) -> switched to 'data-variant="edit"' check
      - Minor (deferred to final review): the two historic-content tests (preview.html/pdf) don't actually discriminate old vs new content (both contain "draft"); PDF test has no content check at all. Plan-inherited, not implementer's fault.
- [x] Task 6: Tailored-CV version list, accepted badge, and Unaccept in the preview pane (commit afb2dd4, review clean)
      - Fixed another plan test-text bug: doc_write_available() defaults False in this env, and a bare 'cv-preview-tabs' substring search collides with base.html's <head> CSS rule; patched doc_write_available=True (existing file convention) and searched 'class="cv-preview-tabs"' instead
      - Minor (deferred): no CSS for .cv-version-accepted yet (unstyled badge text)
- [x] Task 7: Base CV — version viewing, version list, and accept/unaccept UI (commit c6a328a, review clean, no fixes needed)
      - Minor (deferred): no test for viewing_version.accepted_at badge in the read-only branch
- [x] Task 8: Full-suite sweep — clean (1809 passed, 4 skipped before final review; see below)

Final whole-branch review (opus, commits a2a98e7..6faa4ed): found 2 Critical (FK IntegrityError bugs, both plan-inherited: _prune_versions could delete the row current_version_id still referenced; delete_job/delete_jobs deleted cv_versions before the referencing row) + 3 Important (settings-only saves spuriously versioning base_cv; revert-then-quick-edit destroying the reverted-to content; "today ago" broken timestamps) + 5 Minor. Root cause of both Criticals slipping through per-task review: tests/test_cv_versions.py used raw sqlite3 connections without PRAGMA foreign_keys=ON.
- [x] Fix round 1 (commits d79429e, 02671e4): all 2 Critical + 3 Important + 5 Minor fixed — _prune_versions now protects the live current_version_id; delete_job/delete_jobs reordered (jobs first, then cv_versions, with delete_jobs resolving actual-trash ids first); _record_version gained a content-equality short-circuit + MAX(id) stacking guard; time_ago -> age filter (5 sites); test harness switched to conftest's FK-enforcing fixture; CSS added for version list/badge; weakened tests tightened; diff-summary removed from historic version view. Full suite 1818 passed, 4 skipped. Re-reviewed (opus): all confirmed genuinely fixed, one new Important found.
- [x] Fix round 2 (commit b8f5092): delete_source also leaked cv_versions (jobs.id has no AUTOINCREMENT, so a deleted job's rowid can be reused, silently inheriting the deleted job's CV history) — fixed with the same resolve-ids-first shape as delete_job/delete_jobs. Reviewed (sonnet): approved, ready to merge.

Deferred (not fixed, judged acceptable to ship without): no version-list navigation from within a read-only version view (must go back to current first); "Version ... from just now" reads slightly oddly for sub-minute case; accept-path silent no-op if current_version_id is somehow NULL (unreachable via normal usage since base_cv is always seeded); spec's diff mechanism wording is slightly imprecise (says "resolved base CV", code uses base_cv_snapshot which is written from the resolved base so they coincide in practice).

Full suite as of b8f5092: 1819 passed, 4 skipped, pristine.
