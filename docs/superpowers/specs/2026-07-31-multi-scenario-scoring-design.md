# Multi-Scenario Scoring — Design Spec

**Date:** 2026-07-31
**Status:** Approved

## Overview

Today a job holds exactly one score: `jobs.relevance_score`/`score_reasoning`/`scenario_id`, set to whichever scenario was active when the job was fetched (or last re-evaluated). There's no way for the same job to carry independent scores for two different scenarios, and nothing in the UI shows which scenario a displayed score even belongs to.

This adds a proper per-(job, scenario) score, evaluated automatically for every scenario as new jobs are fetched, with an explicit (never automatic) re-evaluate action to backfill or refresh scores for existing jobs — smart enough to skip pairs that are already up to date. The job list and detail view show the best score across all scenarios a job has been evaluated against, tagged with which scenario produced it.

---

## Data model

New table:

```sql
CREATE TABLE job_scores (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    scenario_id INTEGER NOT NULL REFERENCES scenarios(id) ON DELETE CASCADE,
    relevance_score REAL NOT NULL,
    score_reasoning TEXT NOT NULL DEFAULT '',
    scenario_version_hash TEXT NOT NULL,
    evaluated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(job_id, scenario_id)
);
```

One row per (job, scenario) pair; re-evaluating upserts rather than accumulating a history. `jobs` loses `relevance_score`, `score_reasoning`, `scenario_id` — those live entirely in `job_scores` now. `content_type`, `summary`, `simplified_content` stay on `jobs`: classification and summarization are scenario-agnostic, only fit-evaluation is per-scenario.

### Scenario versioning

A scenario's fit-relevant content is its `description` plus its current `criteria` (text + weight per row). Neither `scenarios` nor `criteria` tracks an edit timestamp, and criteria rows are only ever inserted/deleted (never edited in place), so rather than adding `updated_at` bookkeeping that has to be remembered on every mutation path, a version is a content hash computed on demand:

```python
# app/scenario_version.py
def compute_version_hash(scenario: dict, criteria: list[dict]) -> str:
    parts = [scenario.get("description", "")]
    for c in sorted(criteria, key=lambda c: (c["weight"], c["text"])):
        parts.append(f"{c['weight']}:{c['text']}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
```

Criteria are sorted before hashing so reordering (e.g. delete + re-add) doesn't count as a change. A `job_scores` row's `scenario_version_hash` records the hash *at evaluation time*; comparing it to the scenario's current hash is how staleness is detected, with no separate timestamp field to keep in sync.

---

## When evaluation happens

| Trigger | Which jobs | Which scenarios |
|---|---|---|
| **Fetch** | Newly inserted jobs this run (not URL-deduped) that classify as `job_posting`/`lead` | *All* scenarios that exist at that moment. Zero scenarios → classified/summarized, no score. |
| **Re-evaluate (single)** `POST /scenarios/{id}/reevaluate` | Every `status="new"` job with `content_type in (job_posting, lead)`, **skipping any whose stored `job_scores` row already matches the scenario's current hash** | Just that one scenario |
| **Re-evaluate (all)** `POST /scenarios/reevaluate` | Same eligibility/skip logic, applied per scenario | Every scenario, one after another |

There is no automatic action that re-scores everything against everything — getting a job scored against a scenario it's missing requires an explicit Re-evaluate click (single or combined). Jobs already `accepted`/`rejected`/`invalid` are never touched by either path, matching today's behavior.

### Fetch (`app/pipeline.py::run_fetch`)

Loops over `q.get_scenarios(conn)` (was: `get_active_scenario()`) for each newly classified job:

```python
for scenario in scenarios:
    criteria = q.get_criteria(conn, scenario["id"])
    score, reasoning = evaluate(client, model, profile, scenario, criteria, job_summary)
    version_hash = compute_version_hash(scenario, criteria)
    q.upsert_job_score(conn, job_id, scenario["id"], score, reasoning, version_hash)
    yield _progress(f"[{i}/{n}] Scored {score} for '{scenario['name']}': {raw.url}")
```

Every pair is first-time for a brand-new job, so no staleness check is needed here — it always evaluates and stores the current hash.

### Re-evaluate (`app/pipeline.py::run_reevaluate`, new)

The per-scenario re-evaluate logic moves out of `routes/scenarios.py` into `pipeline.py` (matching where `run_fetch` lives), so both the single-scenario route and the new combined route share it instead of duplicating the skip logic:

```python
def run_reevaluate(conn, client, model, scenario) -> Generator[str, None, int]:
    profile = q.get_profile(conn)
    criteria = q.get_criteria(conn, scenario["id"])
    current_hash = compute_version_hash(scenario, criteria)
    eligible = [j for j in q.get_jobs(conn, status="new") if j["content_type"] in ("job_posting", "lead")]
    existing = q.get_job_score_hashes(conn, scenario["id"])
    to_evaluate = [j for j in eligible if existing.get(j["id"]) != current_hash]
    skipped = len(eligible) - len(to_evaluate)

    msg = f"Re-evaluating {len(to_evaluate)} job(s) for scenario '{scenario['name']}'"
    if skipped:
        msg += f", skipping {skipped} already current"
    yield _progress(msg)

    for i, job in enumerate(to_evaluate, start=1):
        new_summary = summarize(client, model, job["simplified_content"]) if job["simplified_content"] else job["summary"]
        score, reasoning = evaluate(client, model, profile, scenario, criteria, new_summary)
        q.update_job_pipeline(conn, job["id"], simplified_content=job["simplified_content"],
                               content_type=job["content_type"], summary=new_summary)
        q.upsert_job_score(conn, job["id"], scenario["id"], score, reasoning, current_hash)
        yield _progress(f"[{i}/{len(to_evaluate)}] Re-scored {score}: {job['title'] or job['url']}")

    yield _progress(f"Re-evaluation complete for '{scenario['name']}': {len(to_evaluate)} job(s) updated")
    return len(to_evaluate)
```

`update_job_pipeline()`'s signature drops the `relevance_score`/`score_reasoning`/`scenario_id` params (no longer columns on `jobs`); scoring writes go through the new `upsert_job_score()`.

### Routes (`app/routes/scenarios.py`)

Both routes become thin wrappers around `run_reevaluate`:

- `POST /scenarios/{scenario_id}/reevaluate` (existing): calls it once, streams its lines.
- `POST /scenarios/reevaluate` (new, combined): loops over every scenario, calling `run_reevaluate` for each and streaming all lines in sequence, ending with a combined total, e.g. `"All scenarios re-evaluated: 8 job(s) updated across 2 scenario(s)"`. A new "Re-evaluate all scenarios" button at the top of the Scenarios page (not tied to one scenario) drives it, using the same `data-progress-url` streaming helper as everything else. Safe from colliding with the parameterized route above since `scenario_id` is typed `int` throughout this router — FastAPI's path converter won't match the literal segment `reevaluate` as an int, so it falls through to this route regardless of registration order.

### Query layer additions

- `upsert_job_score(conn, job_id, scenario_id, score, reasoning, version_hash)` — insert-or-update on the `(job_id, scenario_id)` unique constraint.
- `get_job_score_hashes(conn, scenario_id) -> dict[int, str]` — `{job_id: scenario_version_hash}` for one scenario, used to compute the skip set.

---

## Reading & displaying the best score

`get_jobs()` and `get_job()` both need a "best score across scenarios" join, via one shared SQL fragment:

```sql
FROM jobs
LEFT JOIN (
    SELECT job_id, scenario_id, relevance_score, score_reasoning,
           ROW_NUMBER() OVER (PARTITION BY job_id ORDER BY relevance_score DESC, scenario_id ASC) AS rn
    FROM job_scores
) best ON best.job_id = jobs.id AND best.rn = 1
LEFT JOIN scenarios ON scenarios.id = best.scenario_id
```

Selected as `best.relevance_score AS best_score`, `best.score_reasoning AS best_score_reasoning`, `scenarios.name AS best_scenario_name`. Jobs with no `job_scores` rows at all (e.g. `irrelevant` content, or fetched before any scenario existed) get `NULL` for all three — the same "no score yet" case the UI already handles. Sort order becomes `ORDER BY best.relevance_score DESC NULLS LAST, jobs.fetched_at DESC`. Ties on `relevance_score` across scenarios break on `scenario_id ASC` — arbitrary but deterministic, so the same job doesn't flicker between tagged scenarios across requests.

**Templates:** `jobs/_row.html` and `jobs/_feedback.html` read `job.best_score`/`job.best_score_reasoning` (renamed from `job.relevance_score`/`job.score_reasoning`) and gain a scenario-name tag next to the score badge/reasoning, showing `job.best_scenario_name`. Existing status/content_type filters and the feedback form are unaffected.

---

## Migration

Runs synchronously inside `init_db()` on process startup, same as the existing `fetcher_type` migration — this is a single-user local SQLite app with no concurrent access, so a simple blocking migration is preferred over anything more complex.

1. **Backfill**: before touching `jobs`, copy any existing `(scenario_id, relevance_score, score_reasoning)` into `job_scores` for jobs that have one, with `scenario_version_hash = 'legacy'` — a sentinel no real computed hash can ever equal, so these rows are automatically treated as stale the next time their scenario is re-evaluated. No manual cleanup step required.
2. **Rebuild `jobs`** without the three removed columns: `PRAGMA foreign_keys = OFF`, create a replacement table, copy rows preserving `id` (so the just-backfilled `job_scores` rows stay valid), drop the old table, rename the replacement into place, `PRAGMA foreign_keys = ON` — the same pattern already used for the `fetcher_type` CHECK-constraint migration.

`job_scores` itself needs no migration — it's created via plain `CREATE TABLE IF NOT EXISTS` in the main DDL, which is a no-op on top of an already-migrated database.

---

## Affected files

- `app/db/schema.py` — `job_scores` table, `jobs` column removal + backfill migration
- `app/db/queries.py` — `upsert_job_score`, `get_job_score_hashes`, `update_job_pipeline` signature change, `get_jobs`/`get_job` best-score join
- `app/scenario_version.py` (new) — `compute_version_hash`
- `app/pipeline.py` — `run_fetch` evaluates against all scenarios; `run_reevaluate` (new, moved logic)
- `app/routes/scenarios.py` — `reevaluate_jobs` becomes a thin wrapper; new `POST /scenarios/reevaluate` combined route
- `app/templates/jobs/_row.html`, `app/templates/jobs/_feedback.html` — `best_score`/`best_score_reasoning`/`best_scenario_name`
- `app/templates/scenarios/index.html` — combined "Re-evaluate all scenarios" button

## Testing strategy

- `test_schema.py` — `job_scores` UNIQUE(job_id, scenario_id) enforced; migration backfills with `'legacy'` hash and removes the old columns from `jobs`; FK integrity survives under enforcement; idempotent on repeat `init_db()`.
- `test_scenario_version.py` (new) — hash is deterministic; changes when description or criteria set changes; unaffected by criteria ordering.
- `test_queries.py` — `upsert_job_score` inserts then updates in place on conflict; `get_job_score_hashes` scoped per scenario; `get_jobs`/`get_job` surface the correct max-score row; no-score jobs still return `None`.
- `test_pipeline.py` — `run_fetch` scores a new job against every existing scenario (and against none, gracefully, with zero scenarios); `run_reevaluate` skips already-current pairs and only calls the LLM for missing/stale ones, with correct skip counts in both progress messages and mock call counts.
- `test_routes_scenarios.py` — single-scenario reevaluate streams correctly under the new semantics; new combined route streams across all scenarios with a correct total.
- `test_routes_jobs.py` — scenario-name tag appears when a score exists; list sorts by best score across scenarios.

No template-level test harness exists in this project; template correctness rides along with the route-level `resp.text` assertions above, consistent with existing convention.

## Out of scope

- Showing the full per-scenario score breakdown for a job (only the best score + its scenario is displayed; not a "see all scores" view).
- Filtering the job list by scenario.
- Deleting a scenario (doesn't exist today; if added later, `job_scores` rows would cascade-delete via the existing FK).
- Any automatic trigger that re-scores existing jobs when a scenario is created or edited — always requires an explicit Re-evaluate click (single or combined).
