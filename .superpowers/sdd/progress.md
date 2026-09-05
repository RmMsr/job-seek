# Progress ledger — targeted-reeval-tasks

Plan: docs/superpowers/plans/2026-09-02-targeted-reeval-tasks.md
Base before implementation: ab91820

- [x] Task 1: drop gate_threshold from scenario version hash  (commits ab91820..f40b5ae, review clean)
- [x] Task 2: run_reevaluate score-only  (commits f40b5ae..8d2c547, review clean + 1 char. test added)
- [x] Task 3: scenario_reevaluate_one task + manual per-scenario button  (commits 7c3e54d..17607d7, review clean)
- [x] Task 4: profile_reassess_fit task + profile-save triggers  (commits 100c768..1ea6283, review clean)
- [x] Task 5: scenarios_reevaluate_all fan-out root  (commits d08a114..ad0a63b, review clean)

All 5 tasks complete. Whole-branch review (opus) done — "merge with fixes".

## Whole-branch review findings → fix wave (commit after)
- IMPORTANT 1: run_reassess_fit (pipeline.py:489) missing summary-less guard — sibling of run_reevaluate, now independently reachable; feeds "" to assess_fit + stamps current hash. Add same guard.
- IMPORTANT 2: profile save during a RUNNING profile_reassess_fit is swallowed by find_active_task (queued OR running) dedupe → profile B never re-fit. Fix: _task_profile_reassess_fit re-checks profile hash after run, re-enqueues if changed.
- IMPORTANT 3: textarea CRLF — after refine-accept (writes \n), a genuine no-op Save sends \r\n → changed=True + different hash → full re-fit. Fix: normalize content.replace("\r\n","\n") at top of profile_save.
- IMPORTANT 4: fan-out children render as raw "scenario reevaluate one" ×N in task UI + ambient bar. Add _title + _goal cases in app/routes/tasks.py for scenario_reevaluate_one / profile_reassess_fit (mirror fetch_source "Fetch: {name}").
- MINOR 5: test_reevaluate_all_scenarios_counts_scenarios_and_jobs_independently (test_routes_scenarios.py:724) is now a near-dup of test_reevaluate_all_children_rescore_every_scenario — delete or rename.
- MINOR 6: unused `task =` locals at test_routes_scenarios.py:595 and :733 — drop.
- MINOR 7: summary-less jobs never converge their hash (re-listed+re-skipped every run). Accept; add a one-line comment at the skip in run_reevaluate AND run_reassess_fit.
- MINOR 10: no direct generator-level unit test for run_reevaluate; "skipping N already current" string unasserted for it. Add one to test_pipeline.py.
- SPEC (controller does): note in design.md that the dedupe wrinkle also applies to scenario_reevaluate_one children, not just the fit child.
- Recommendation (controller does): one-line comment at run_reevaluate pointing at run_reevaluate_job as the deliberate re-summarize path.
- NOT bugs: #8 already covered above; #9 double-click empty root matches fetch_all precedent (leave).

## Fix wave applied — commit 14c388b (suite 1390 passing)
- All 4 IMPORTANT + MINOR 5/6/7/10 fixed.
- IMPORTANT 2: brief's naive fix was a no-op (follow-up deduped against the running task itself). Fixer added `exclude_task_id` kwarg to find_active_task/enqueue_task in app/db/queries.py (backward-compat, default None) + handler passes its own params["_task_id"]. ONE extra file beyond brief.
- Spec doc dedupe-wrinkle note broadened — commit aac55ab (controller).
- Re-review of 14c388b: ✅ Ready to merge (sonnet). exclude_task_id proven neutral for all ~30 callers; race fix verified real; no infinite loop. One defensive-only Minor (params.get("_task_id") None path — can't happen, execute_task always injects). Left as-is; comment already explains it.

## Status: implementation + reviews complete. Suite 1390 green.
Next: manual smoke test (dev server handoff to user), then squash-merge to main + worktree cleanup.

## Minor findings (for final review triage)
- Task 1: test_scenario_version.py:47 — `test_hash_ignores_gate_threshold` could add a one-line comment explaining why threshold is excluded (not fed to evaluate()).
- Task 2: scenarios.py:109 — inert `return {"notices": [], "html_chunks": []}` kept in interim `_task_scenarios_reevaluate_all` (Task 5 replaces the function).
- Task 2: test_routes_scenarios.py Step-6 tests carry a now-dead `patch("app.pipeline.summarize")` (Task 5 cleanup).
- Note: commit 8d2c547 bundled a ledger bump into a test commit (fixer used `git commit -am`). Harmless; squash-merge flattens it.
- Task 5: test_routes_scenarios.py:~596 — dead `task =` local in test_reevaluate_task_execution_updates_jobs after switch to _drain_all_tasks. Trivial.
- Task 5 reviewer Minor #1 (accepted-job fit coverage "lost") is a FALSE ALARM — tests/test_pipeline.py:781 test_run_reassess_fit_includes_accepted_and_gate_failed_jobs still covers it. No action.
