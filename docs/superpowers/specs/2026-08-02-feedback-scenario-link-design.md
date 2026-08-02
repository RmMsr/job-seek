# Feedback-Scenario Linking — Design Spec

**Date:** 2026-08-02
**Status:** Approved

## Overview

Job feedback (`status` + `note`) is stored per-job with no explicit link to a scenario. When a scenario's "refine criteria from feedback" flow pulls recent notes (`get_recent_feedback_notes`), it joins through `job_scores` and picks up feedback from *any* job that was scored against that scenario — regardless of what the note was actually about. A job scored against three scenarios has its one feedback note attributed to all three, even though the user was really only commenting on one.

This adds an explicit, user-chosen scenario tag to each job's feedback, defaulting to the job's best-matching scenario but changeable at submission time.

---

## 1. Schema

Add `feedback_scenario_id INTEGER REFERENCES scenarios(id)` to `jobs` (nullable — a job with no feedback yet has no scenario tag either).

Follows the existing rebuild-table migration pattern (`_migrate_sources_fetcher_type` / `_migrate_jobs_scores_to_table` in `app/db/schema.py`): detect the missing column, rebuild `jobs` under a `jobs_new` name with the column added, swap it into place.

**Backfill:** for any existing row where `feedback_note` is non-empty, set `feedback_scenario_id` to that job's best-matching scenario (same `job_scores` best-score logic the `_BEST_SCORE_JOIN` view uses today) so existing feedback keeps counting toward the scenario it currently influences. Rows with no feedback stay `NULL`.

## 2. Feedback form (`app/templates/jobs/_feedback.html`)

Add a required `<select name="feedback_scenario_id">` listing every scenario (active or not — the user may want to tag a job for a scenario it hasn't been scored against yet), styled like the existing weight `<select>` in `_criterion_edit.html`. Pre-selected value:

- `job.best_scenario_id` if the job has been scored against anything, else
- whatever scenario sorts first in the list (natural `<select>` default — no explicit placeholder/blank option).

The user can change the selection before submitting; this is the only new interactive element on the form.

## 3. Route (`app/routes/jobs.py`)

`POST /jobs/{job_id}/feedback` gains `feedback_scenario_id: int = Form(...)`, passed through to `q.update_job_feedback`. No other route changes — `job.status` semantics, the job list filter bar, and job counts are untouched.

## 4. Data layer (`app/db/queries.py`)

- `update_job_feedback(conn, job_id, status, note, feedback_scenario_id)` — now writes all three columns. Still one feedback slot per job: resubmitting overwrites `status`, `note`, and `feedback_scenario_id` together, same overwrite behavior the form already has today.
- `get_recent_feedback_notes(conn, scenario_id, limit=20)` — drops the `job_scores` join entirely; becomes `WHERE jobs.feedback_scenario_id = ? AND jobs.feedback_note IS NOT NULL AND jobs.feedback_note != ''`.
- Need a way to compute "best-matching scenario id" for the default-select and backfill logic. `_BEST_SCORE_JOIN` currently only exposes `best_scenario_name`; add `best.scenario_id AS best_scenario_id` to `_BEST_SCORE_SELECT` so `get_job`/`get_jobs` also return it (used by the form's default-select and reused by the migration's backfill query).

## 5. Testing

- Schema/migration: existing DB with a job that has `feedback_note` set and a `job_scores` row gets `feedback_scenario_id` backfilled to the best-scoring scenario; a job with no feedback stays `NULL`.
- Query-level: `update_job_feedback` persists `feedback_scenario_id`; `get_recent_feedback_notes` only returns notes tagged to the requested scenario, not notes from jobs merely scored against it.
- Route-level: submitting feedback with a `feedback_scenario_id` different from the job's best-matching scenario stores that choice, not the best match.
- Template: form defaults to `job.best_scenario_id` when present, falls back to first scenario in the list when the job has never been scored.
