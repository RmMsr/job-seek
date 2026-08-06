# Two-Stage Scoring Pipeline — Design Spec

**Date:** 2026-08-05
**Status:** Approved

## Overview

Today, `evaluate()` (`app/ai/evaluate.py`) makes one LLM call per `(job, scenario)` pair and asks it to reason about scenario theme, `must`/`prefer`/`avoid` criteria, *and* candidate-profile fit all at once, collapsing everything into a single 0.0–1.0 float. In practice this produces poor discrimination: measured against the live DB, 23% of all scored jobs land at 0.8+, and of the jobs still awaiting review, 39% already score 0.8+. The sources are pre-filtered to be topically relevant, so clearing "is this roughly the right kind of job" is common — but the score doesn't distinguish that from "is this actually an exciting, attainable opportunity for me," which should be rare.

The existing `boosted`/`BOOST_BONUS` best-match-selection mechanism (2026-08-04 spec) papered over this by picking one "winning" scenario per job, but a boost bonus doesn't fix discrimination — it just changes which of several similarly-clustered scores gets shown.

This spec splits scoring into two independent stages with different inputs and different jobs to do:

1. **Stage 1 — scenario gate.** Per `(job, scenario)`, unchanged in shape from today's `job_scores` table, but with the candidate profile removed from the prompt entirely. Answers only "is this job in my search space" (theme + must/prefer/avoid). A job becomes visible once it clears at least one scenario's configurable gate threshold.
2. **Stage 2 — profile fit scorecard.** Once per job (not per scenario), for any job that passed at least one gate. Answers "how good is this specific opportunity for me" via two independent scores — **interest** (domain/mission, culture/stage, trajectory alignment) and **attainability** (how realistic landing it is, with gaps called out in the reasoning) — averaged into a `fit_score` used for the default sort.

Explicitly not addressed here: building any calibration loop against historical feedback data — the existing `feedback_note`/accepted/rejected rows were given during early experimentation with the app and are not reliable ground truth (see project memory). A real calibration loop needs feedback collected under stable usage after this redesign ships, and is deferred to a future milestone.

---

## 1. Data model

### `scenarios` — replace `boosted` with `gate_threshold`

Hard-downtime rebuild, per project convention (same pattern as the existing `active` → `boosted` migration this supersedes):

```sql
CREATE TABLE IF NOT EXISTS scenarios (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    gate_threshold REAL NOT NULL DEFAULT 0.7,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

Migration (`app/db/schema.py`, replaces `_migrate_scenarios_boosted_flag`):

```python
def _migrate_scenarios_gate_threshold(conn: sqlite3.Connection) -> None:
    # Replaces "boosted" (best-match tie-break bonus) with "gate_threshold"
    # (per-scenario cutoff for stage-1 visibility) — see two-stage-scoring-pipeline spec.
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='scenarios'"
    ).fetchone()
    if row is None or "gate_threshold" in row[0]:
        return
    conn.execute("ALTER TABLE scenarios DROP COLUMN boosted")
    conn.execute("ALTER TABLE scenarios ADD COLUMN gate_threshold REAL NOT NULL DEFAULT 0.7")
    conn.commit()
```

A scenario "passes" for a job when `job_scores.relevance_score >= scenarios.gate_threshold`, evaluated live at read time (not cached) — tightening or loosening a scenario's threshold changes list visibility immediately, no re-evaluate needed.

### `job_scores` — unchanged shape, reinterpreted as the gate result

No schema change. `relevance_score`/`score_reasoning` now represent stage-1 gate strength (theme + must/prefer/avoid only, no profile) rather than overall fit. `scenario_version_hash` staleness detection (2026-07-31 spec) is unaffected — it still hashes `description` + `criteria`.

### `jobs` — new stage-2 columns

Pure additive migration (no rebuild needed — these are new nullable columns, nothing is removed from `jobs`):

```python
def _migrate_jobs_add_fit_scorecard(conn: sqlite3.Connection) -> None:
    # Purely additive columns, same shape as the feedback_handled_at migration above.
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='jobs'"
    ).fetchone()
    if row is None or "interest_score" in row[0]:
        return
    conn.execute("ALTER TABLE jobs ADD COLUMN interest_score REAL")
    conn.execute("ALTER TABLE jobs ADD COLUMN interest_reasoning TEXT")
    conn.execute("ALTER TABLE jobs ADD COLUMN attainability_score REAL")
    conn.execute("ALTER TABLE jobs ADD COLUMN attainability_reasoning TEXT")
    conn.execute("ALTER TABLE jobs ADD COLUMN fit_score REAL")
    conn.execute("ALTER TABLE jobs ADD COLUMN profile_version_hash TEXT")
    conn.commit()
```

`NULL` across all six columns means "not yet assessed" (e.g. a job that hasn't been through stage 2, or was fetched before this migration). `profile_version_hash` is `sha256(profile.content)` — computed the same way `compute_version_hash` works for scenarios (`app/scenario_version.py`), giving stage 2 the same staleness-detection story: if the profile text changes, existing scorecards are flagged stale without needing a separate `updated_at` to keep in sync.

### Removed: `BOOST_BONUS` / best-match join

`_BEST_SCORE_JOIN` and the `BOOST_BONUS` constant in `app/db/queries.py` are deleted. There's no more "one winning scenario" — any number of scenarios can pass independently, and stage 2 (not a scenario's raw score) drives the default sort.

---

## 2. Stage 1 — scenario gate

`app/ai/evaluate.py`, prompt trimmed to two tiers (theme, then must/prefer/avoid) — the "must criteria and candidate's profile" tier is deleted, and the `profile` parameter is removed from `evaluate()`'s signature entirely:

```python
def evaluate(
    client: openai.OpenAI,
    model: str,
    scenario: dict,
    criteria: list[dict],
    summary: str,
) -> tuple[float, str]:
    ...
```

Callers (`app/pipeline.py::run_fetch`, `run_reevaluate`) drop the `profile` argument they currently pass through. Output shape (`{"score": float, "reasoning": str}`) and clamping/error handling are unchanged.

`q.upsert_job_score(...)` is unchanged — still one row per `(job_id, scenario_id)`.

---

## 3. Stage 2 — profile fit scorecard

New module `app/ai/assess_fit.py`:

```python
def assess_fit(
    client: openai.OpenAI,
    model: str,
    profile: str,
    summary: str,
) -> dict:
    """Returns {"interest": float, "interest_reasoning": str,
                "attainability": float, "attainability_reasoning": str}"""
```

System prompt names the three interest sub-dimensions explicitly (domain/mission alignment, company/team stage & culture, career trajectory fit) and instructs attainability's reasoning to call out concrete gaps (missing years of experience, seniority mismatch, skills the posting requires that the profile doesn't show) rather than scoring "challenges" as a separate number. Same JSON-extraction/clamping/error-handling pattern as `evaluate()` (shared helper via `app/ai/json_utils.py` if the shape overlaps enough — otherwise duplicated, matching existing project style of small focused modules over premature sharing).

`q.update_job_fit(conn, job_id, interest, interest_reasoning, attainability, attainability_reasoning, profile_version_hash)` writes the five columns plus a computed `fit_score = (interest + attainability) / 2` on `jobs`.

---

## 4. Pipeline (`app/pipeline.py`)

### `run_fetch`

Unchanged loop over scenarios for stage 1 (now calling `evaluate()` without `profile`). After the scenario loop, if **any** scenario's `job_scores` row cleared its `gate_threshold`, one `assess_fit()` call runs and `update_job_fit` stores the result with the current `profile_version_hash`. If no scenario passes, stage 2 is skipped — the job is filed as gate-filtered (see §5) and never gets the more expensive profile call.

### `run_reevaluate` (existing, per-scenario gate re-eval)

Unchanged behavior — re-runs stage 1 for one scenario against eligible `status="new"` jobs, skipping already-current `(job, scenario)` pairs via `scenario_version_hash`, same as today. Does **not** touch stage 2.

### `run_reassess_fit` (new, profile-only re-eval)

Mirrors `run_reevaluate`'s skip logic but keyed on `profile_version_hash` instead of `scenario_version_hash`:

```python
def run_reassess_fit(conn, client, model) -> Generator[str, None, int]:
    profile = q.get_profile(conn)
    current_hash = compute_profile_hash(profile)
    eligible = [j for j in q.get_jobs(conn, status="new") if q.job_passed_any_gate(conn, j["id"])]
    to_assess = [j for j in eligible if j["profile_version_hash"] != current_hash]
    ...
    for job in to_assess:
        result = assess_fit(client, model, profile, job["summary"])
        q.update_job_fit(conn, job["id"], ..., current_hash)
    ...
```

Triggered by an explicit "Recompute fit scores" action (new button on `app/routes/profile.py`'s page) — never automatic, matching the project's existing re-evaluate convention. `compute_profile_hash` lives alongside `compute_version_hash` in `app/scenario_version.py` (or a sibling `profile_version.py` if that file's name stops fitting — implementer's call, both are one-line hash functions).

---

## 5. Visibility & UI

**Job list (`app/routes/jobs.py`, `get_jobs`):** default filter becomes "has at least one `job_scores` row where `relevance_score >= scenarios.gate_threshold`." A toggle (query param, e.g. `?show_filtered=1`) reveals gate-filtered jobs for quality review — per your answer, these are retained, not deleted, with retention/cleanup policy explicitly out of scope for this spec. Default sort becomes `jobs.fit_score DESC NULLS LAST, jobs.fetched_at DESC` (jobs pending stage 2 — freshly migrated or fetched with zero passing scenarios — sort last).

**Job row/detail (`jobs/_row.html`, `jobs/_feedback.html`):**
- Passed-scenario tags replace the single best-scenario tag — one tag per scenario whose gate the job cleared.
- The existing per-scenario score comparison tab strip (2026-08-04 spec) **stays**, per your steer — it still reads `q.get_job_scores(job_id)`, now showing gate scores/reasoning per scenario. Useful for quality-checking the gate and deciding whether a scenario's threshold or criteria need adjusting. Only change: drop the boosted-indicator icon (⚡) since `boosted` no longer exists — nothing replaces it, a threshold isn't a per-tab indicator.
- New: a two-row scorecard section (Interest / Attainability), each score + its reasoning, rendered above or beside the tab strip. `NULL` scores (stage 2 not yet run) render as "not yet assessed" rather than a 0%/blank badge.

**Scenario edit (`scenarios/_header_edit.html`):** boosted checkbox replaced with a numeric input for `gate_threshold` (default `0.7`, same tooltip-documentation pattern as the old boost bonus). `scenarios/_header.html`'s boosted tag is replaced with a small "gate: 70%"-style label.

**Profile page:** new "Recompute fit scores" button driving `run_reassess_fit` via the existing `data-progress-url` streaming pattern.

---

## 6. Affected files

- `app/db/schema.py` — `scenarios.boosted` → `gate_threshold` migration; new `jobs` stage-2 columns migration
- `app/db/queries.py` — remove `BOOST_BONUS`/`_BEST_SCORE_JOIN`; add gate-threshold-based visibility filter + sort; add `update_job_fit`, `job_passed_any_gate`; `update_scenario` gains `gate_threshold` param
- `app/ai/evaluate.py` — drop `profile` param, trim prompt to two tiers
- `app/ai/assess_fit.py` (new) — stage-2 LLM call
- `app/scenario_version.py` — add `compute_profile_hash` (or new sibling module)
- `app/pipeline.py` — `run_fetch` gains post-gate stage-2 call; new `run_reassess_fit`
- `app/routes/scenarios.py` — `gate_threshold` form field replacing `boosted`
- `app/routes/jobs.py` — visibility filter/toggle, sort change
- `app/routes/profile.py` — new reassess-fit route
- Templates: `jobs/_row.html`, `jobs/_feedback.html`, `scenarios/_header.html`, `scenarios/_header_edit.html`, profile page template

## 7. Testing strategy

- `test_schema.py` — `gate_threshold` migration (default `0.7`, `boosted` column gone); `jobs` stage-2 columns migration (nullable, idempotent on repeat `init_db()`)
- `test_evaluate.py` — update fixtures/signature for `profile`-less `evaluate()`
- `test_assess_fit.py` (new) — mirrors `test_evaluate.py`'s structure for the new call (score parsing, clamping, error handling, JSON fence stripping)
- `test_scenario_version.py` — add `compute_profile_hash` determinism test
- `test_queries.py` — `update_job_fit` writes all five columns + computed `fit_score`; visibility filter includes/excludes jobs correctly around `gate_threshold`; sort order by `fit_score`
- `test_pipeline.py` — `run_fetch`: stage 2 runs exactly once per job when ≥1 scenario passes, zero times when none pass; `run_reassess_fit`: skips jobs whose `profile_version_hash` already matches, only reassesses gate-passed jobs
- `test_routes_jobs.py` — default list excludes gate-filtered jobs; `?show_filtered=1` reveals them; scorecard renders "not yet assessed" for `NULL` stage-2 fields
- `test_routes_scenarios.py` — `gate_threshold` persists via the update-scenario route

## Out of scope

- Any calibration loop against historical `feedback_note`/status data (unreliable ground truth from the app's experimentation phase — see project memory).
- Restructuring the profile into structured fields (stays free text; richer content is a user-authoring concern, not a schema concern).
- Multi-call decomposition of stage 2 into more than two axes (interest/attainability) — start simple, revisit once real usage data exists.
- Retention/cleanup policy for gate-filtered jobs.
- Automatic stage-2 re-run triggered by a profile edit (stays an explicit "Recompute fit scores" click, matching the project's existing re-evaluate convention).
