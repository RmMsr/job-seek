# Combined "Re-evaluate everything" sweep

## Problem

There are two separate bulk hash-skip sweeps, each on its own page:

- **Scenarios page**: "Re-evaluate all scenarios" (`POST /scenarios/reevaluate`)
  loops every scenario and re-scores jobs whose scenario-criteria version
  hash is stale. Scope: `status in (new, accepted)`. Never touches fit.
- **Scenarios page**, per scenario: "Re-evaluate jobs"
  (`POST /scenarios/{id}/reevaluate`) — same thing, scoped to one scenario.
- **Profile page**: "Recompute fit scores" (`POST /profile/reassess-fit`)
  re-runs fit for jobs whose profile-version hash is stale. Scope:
  `status=new AND gate_status=passed`. Never touches scores.

Refreshing everything after both a criteria edit and a profile edit takes
two clicks on two different pages, and the two sweeps don't even agree on
which jobs are in scope. The buttons don't make sense kept separate.

## Design

One combined sweep, presented as the same button on both pages.

### Pipeline: widen `run_reassess_fit`'s scope

`app/pipeline.py`'s `run_reassess_fit` eligibility changes from
`status=new AND gate_status=passed` to `status in (new, accepted)`, any
`gate_status`, `content_type in (job_posting, lead)` — matching the
scenario sweep's existing scope. Fit is meaningful for accepted jobs too
(it's part of what informed accepting them), and gate status no longer
gates whether fit refreshes.

No other pipeline change: `run_reevaluate` and `_eligible_for_reevaluation`
are unchanged and keep being used internally by the per-scenario loop.

### Route: fold fit-reassess into the scenario sweep

`app/routes/scenarios.py`'s `reevaluate_all_scenarios`
(`POST /scenarios/reevaluate`) gets one more step appended to its stream:
after the existing per-scenario loop finishes, it also drives
`run_reassess_fit(conn, client, model)`, streaming its progress lines the
same way, and folds its returned count into the final summary line (e.g.
`"All scenarios re-evaluated: N job(s) updated across S scenario(s); fit
recomputed for M job(s)"`).

This becomes the one shared endpoint both pages point at. No new route.

### Removed

- `POST /scenarios/{scenario_id}/reevaluate` (`reevaluate_jobs` function)
  and its "Re-evaluate jobs" button on the scenarios page — redundant now
  that the combined sweep covers full refreshes, and per-job/bulk
  "Re-evaluate" (from the prior feature) already covers targeted refresh
  of a single job.
- `POST /profile/reassess-fit` (`reassess_fit` function) in
  `app/routes/profile.py`, and its `run_reassess_fit` import there (import
  moves to `app/routes/scenarios.py`, which now calls it).

### UI

- `app/templates/scenarios/index.html`: button label "Re-evaluate all
  scenarios" → **"Re-evaluate everything"**. Per-scenario "Re-evaluate
  jobs" button block removed entirely.
- `app/templates/profile/index.html`: the "Recompute fit scores"
  button+progress-div block is replaced by the same "Re-evaluate
  everything" button markup, pointing at `/scenarios/reevaluate`, with an
  updated tooltip noting it refreshes both scenario scores and fit for
  every new/accepted job.

### Why this shape

- Reusing `/scenarios/reevaluate` as the one shared endpoint avoids a new
  route for what's conceptually the existing sweep with one more step
  appended — the URL isn't user-facing.
- Widening fit's scope to match the scenario sweep's scope (rather than
  the reverse) keeps the broader, more useful behavior: fit for accepted
  jobs, and gate status no longer suppressing a fit refresh.
- Removing the per-scenario button avoids three overlapping "re-evaluate"
  entry points (per-scenario, all-scenarios, per-job) collapsing into two
  clearly distinct ones: "Re-evaluate everything" (broad, hash-skip sweep)
  and per-job/bulk "Re-evaluate" (targeted, unconditional refresh).

## Testing

- `tests/test_routes_scenarios.py`: remove the `/scenarios/{id}/reevaluate`
  tests (lines ~504-633, per-scenario route). Extend the
  `/scenarios/reevaluate` tests to assert fit also gets recomputed —
  including for an `accepted`-status job and for a job whose
  `gate_status` isn't `passed` — and that the summary line reports both
  counts.
- `tests/test_routes_profile.py`: remove
  `test_reassess_fit_streams_progress` and
  `test_profile_page_has_reassess_fit_button`; add a profile-page render
  test asserting the "Re-evaluate everything" button is present, pointing
  at `/scenarios/reevaluate`.
- `tests/test_pipeline.py`: update `run_reassess_fit`'s three existing
  tests (`test_run_reassess_fit_updates_gate_passed_jobs`,
  `test_run_reassess_fit_skips_jobs_below_gate`,
  `test_run_reassess_fit_skips_already_current_profile_hash`) for the
  widened scope — the "skips jobs below gate" test's premise no longer
  holds (gate status no longer excludes a job), so it gets replaced with
  a test that an `accepted`-status job is now included and a `rejected`/
  `trash`/wrong-content-type job is still excluded.
