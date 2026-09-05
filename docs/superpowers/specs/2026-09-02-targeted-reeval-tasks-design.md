# Targeted re-evaluation tasks

## Problem

Editing the profile or a scenario doesn't recompute anything. The only way to
refresh scores is the "Re-evaluate everything" button on the scenarios page,
which runs one long monolithic task: re-summarize + re-score every eligible job
against every scenario, then recompute profile fit once.

`run_reassess_fit` and per-scenario `run_reevaluate` already exist and are
hash-gated (`compute_profile_hash` / `compute_version_hash`), but nothing
triggers them in a targeted way.

## Scope

### 1. Hash: drop `gate_threshold`

`compute_version_hash` currently mixes `gate_threshold` into the hash, but the
threshold never feeds the LLM `evaluate()` call — it's applied live at query
time (`WHERE relevance_score >= gate_threshold`). A threshold-only edit
therefore invalidates every cached score and forces a full re-score that
produces identical numbers.

Change: hash only `name`, `description`, and the sorted `(weight, text)`
criteria list. `compute_profile_hash` is unchanged.

One-time effect: every stored `job_scores.scenario_version_hash` was computed
with the threshold included, so all read as stale on first deploy. The next
re-eval (targeted or "everything") re-scores each eligible job once, then it's
self-healing. No backfill migration.

### 2. `run_reevaluate` becomes score-only

Drop the `summarize()` call and the `update_job_pipeline()` call. Score against
the stored `job["summary"]` via `evaluate()`, then `upsert_job_score()`. The
`_eligible_for_reevaluation` hash gate + status/content-type filter is
unchanged.

Refreshing a stale summary (e.g. after a summarize-prompt change) is handled by
the existing per-job "reset to new" path, not by re-evaluation.

If an eligible job somehow has no `summary`, skip it and log — don't feed an
empty summary to `evaluate()`.

### 3. New task kinds

| kind | params | does |
|---|---|---|
| `scenario_reevaluate_one` | `{"scenario_id": N}` | load scenario (no-op + log if deleted), `run_reevaluate` for that one scenario |
| `profile_reassess_fit` | `{}` | `run_reassess_fit` |

Both dedupe via the existing `find_active_task` on `(kind, params)`.

### 4. `scenarios_reevaluate_all` becomes a fan-out root

Like `fetch_all`: enqueue one `scenario_reevaluate_one` child per scenario plus
one `profile_reassess_fit` child, each with `parent_task_id` set. Yield a
"Queued N" line and return `{}`. Displayed state derives from the children
(`_root_summary` / `get_task_children`), so a failing scenario no longer aborts
the rest, and each scenario's progress is independently visible and cancellable.

The worker is single-threaded, so children still run sequentially — the win is
observability, granular cancel, and failure isolation, not parallelism.

Dedupe wrinkle: if a matching child task is already queued/running when the root
fans out, `find_active_task` returns the existing (parent-less) one and the root
gets one fewer child — that scenario's (or the profile fit's) progress then shows
as a separate top-level task instead of nested under the root. Happens for the
`profile_reassess_fit` child (user just saved their profile) and equally for a
`scenario_reevaluate_one` child (user already pressed that scenario's button).
The work still runs exactly once. Acceptable.

### 5. Triggers

**Profile — auto-enqueue `profile_reassess_fit`** from:
- `profile_save` (`POST /profile`)
- `accept_profile_proposals` (refine-accept)

Profile is a single textarea / single Save button, so no burst problem.
Hash-gated, so a no-op save costs nothing.

**Scenario — manual button only.** New endpoint:
```
POST /scenarios/{scenario_id}/reevaluate  →  enqueue scenario_reevaluate_one {scenario_id}
```
Surfaced as a "Re-evaluate this scenario" button in the scenario tab panel
(same `data-progress-url` pattern as "Refine criteria from feedback").

**No scenario edit endpoint enqueues anything** — not `update_scenario`, not
criterion add/edit/delete, not scenario refine-accept. Re-evaluation is heavy
and editing criteria is a multi-save flow; auto-firing mid-edit would waste LLM
calls on intermediate states.

## Non-goals

- No auto-trigger on scenario edits.
- No debounce / `scheduled_for` column on tasks.
- No change to the per-job "Re-evaluate this job" action (`run_reevaluate_job`).
- No hash backfill migration.
- A job that's `rejected` at edit time then flipped back to `new` later keeps
  its stale hash until the next "Re-evaluate everything" — pre-existing, not
  addressed here.

## Tests

- `compute_version_hash`: `gate_threshold` no longer affects output; name /
  description / criteria still do.
- `run_reevaluate`: no `summarize()` / `update_job_pipeline()` call; scores
  against stored `job["summary"]`; honors hash gate + status/content-type
  filter; writes the new hash; skips + logs a summary-less job.
- `scenario_reevaluate_one`: scopes to one scenario; no-op if deleted; dedupes
  on `{scenario_id}`.
- `profile_reassess_fit`: delegates to `run_reassess_fit`.
- `scenarios_reevaluate_all`: fans out N scenario children + 1 fit child with
  `parent_task_id`; returns immediately; children visible via `/tasks/active`.
- Route triggers: `POST /profile` and refine-accept enqueue
  `profile_reassess_fit`; `POST /scenarios/{id}/reevaluate` enqueues the
  scenario task; no scenario edit endpoint enqueues anything.
