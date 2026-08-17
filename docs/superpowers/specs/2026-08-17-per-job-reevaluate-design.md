# Per-job/bulk "Re-evaluate" action

## Problem

Today there are two ways to refresh a job's scenario scores and profile fit:

- **Bulk, hash-skipped, scoped to eligible jobs**: "Re-evaluate all scenarios"
  (`/scenarios/reevaluate`, `/scenarios/{id}/reevaluate`) re-scores jobs
  whose scenario criteria changed; "Recompute fit scores" (`/profile/reassess-fit`)
  re-runs fit for jobs whose profile changed. Both only consider
  `status IN (new, accepted)`, and skip anything whose stored version hash
  already matches current criteria/profile.
- **Reset to new** (`/jobs/{id}/reset`, `/jobs/bulk-reset`): a full pipeline
  replay from the raw posting text — re-simplifies, re-classifies, wipes and
  regenerates everything, and moves the job's `status` back to `new`
  (discarding any accept/reject/trash decision and prior scenario feedback).

There's no way to refresh a specific job's scores and fit in place — without
losing your accept/reject/trash decision — when you just want to see how it
scores after a criteria or profile change, without waiting for (or being
silently skipped by) the bulk hash-based sweep.

## Design

A new "Re-evaluate" action, available both per-job and for a bulk selection,
that always forces a fresh score + fit computation without moving the job
out of its current status. Conceptually a lighter Reset: same LLM calls as
the scoring half of the ingest pipeline, minus the wipe/reclassify/move.

### Pipeline function

`app/pipeline.py`, modeled on the `job_posting`/`lead` branch of
`_ingest_posting`, minus `simplify()`/`classify()` and minus the
gate-conditioned fit call:

```python
def run_reevaluate_job(
    conn: sqlite3.Connection,
    client: openai.OpenAI,
    model: str,
    job: dict,
    scenarios: list[dict],
    profile: str,
    *,
    progress_prefix: str = "",
) -> Generator[str, None, None]:
```

Behavior:

1. If `job["status"] in ("rejected", "trash")` or
   `job["content_type"] not in ("job_posting", "lead")`: yield a "Skipped"
   progress line and return. (Belt-and-suspenders — the per-job button is
   already hidden in these cases — but the bulk route shares this function
   across a multi-select, which could include ineligible jobs.)
2. Regenerate title/headline/summary from the job's existing
   `simplified_content` via `summarize()` (same call bulk `run_reevaluate`
   already makes), persist via `q.update_job_pipeline`.
3. For every scenario in `scenarios`: `evaluate()` against the new summary,
   `q.upsert_job_score(...)` — unconditionally, no version-hash check.
   Yield one progress line per scenario.
4. Always call `assess_fit()` against the new summary and
   `q.update_job_fit(...)` — not gated on any scenario passing its gate this
   round. Yield a progress line.
5. Never touches `job["status"]`, `content_type`, `feedback_handled_at`,
   `gate_override`, or `scenario_feedback` rows.

No new DB queries — reuses `update_job_pipeline`, `upsert_job_score`,
`update_job_fit`, `get_criteria`, `compute_version_hash`,
`compute_profile_hash`, exactly as the existing bulk/ingest paths do.

### Per-job route + UI

`POST /jobs/{job_id}/reevaluate` in `app/routes/jobs.py`, same shape as
`job_reset`/`job_pass_as_new`: fetch job (404 if missing), fetch
`scenarios`/`profile` once, stream `run_reevaluate_job`, re-render the job
row via `_render_updated_job_html`, then the OOB counts partial.

`app/templates/jobs/_feedback.html`: a new "Re-evaluate" button in the
existing `<details class="job-advanced">` block, alongside "Pass as new" and
"Reset to new", gated on `job.status in ("new", "accepted")`. Same
`data-progress-*` wiring as the neighboring buttons. Title tooltip explains
it re-scores in place without moving the job.

### Bulk route + UI

`POST /jobs/bulk-reevaluate` in `app/routes/jobs.py`, same shape as
`job_bulk_reset`: loop over `job_ids` from the multi-select form, call
`run_reevaluate_job` per job with an index-based `progress_prefix`, re-render
each row, then the OOB counts partial at the end.

`app/templates/jobs/_content.html`: a new "Re-evaluate" button in the bulk
bar's `<details class="job-advanced">` block, next to "Reset to new", using
the existing `data-progress-jobs` multi-select wiring. New progress display
target (`#bulk-reevaluate-progress`) separate from `#bulk-reset-progress`.

### Why this shape

- Sharing one `run_reevaluate_job` function between the per-job and bulk
  routes (rather than duplicating logic) matches how `run_reprocess_job`
  already backs both `/jobs/{id}/reset` and `/jobs/bulk-reset`.
- Unconditional recompute (no hash-skip) matches the mental model this was
  designed around: a lighter Reset, not a smaller version of the existing
  bulk hash-skipped sweep. A single button click should never silently
  no-op.
- Fit is always recomputed (not gated on gate-pass) so an already-accepted
  job — which has nothing left to "pass" — still gets a fresh fit read
  alongside its scenario scores.

## Testing

New tests in `tests/test_routes_jobs.py`, modeled on existing
reset/pass-as-new/bulk-reset tests:

- Per-job re-evaluate updates all scenario scores and fit, leaves
  `status` untouched (accepted stays accepted).
- Per-job re-evaluate on a `rejected`/`trash` job: skipped, no LLM calls,
  status/scores untouched.
- Per-job re-evaluate on a non-`job_posting`/`lead` content type: skipped.
- Per-job re-evaluate recomputes even when criteria/profile are unchanged
  (no hash-skip) — evaluate()/assess_fit() are called every time.
- Bulk re-evaluate streams progress across multiple selected jobs and
  updates each one's scores/fit without moving any of their statuses.
