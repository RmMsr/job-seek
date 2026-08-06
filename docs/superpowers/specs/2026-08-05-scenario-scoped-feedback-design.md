# Scenario-Scoped Feedback — Design Spec

**Date:** 2026-08-05
**Status:** Approved

## Overview

The two-stage scoring pipeline (2026-08-05, `two-stage-scoring-pipeline-design.md`) split scoring into a scenario-only gate (stage 1) and a scenario-agnostic profile fit scorecard (stage 2). Feedback didn't follow that split: today, a single `(status, feedback_note, feedback_scenario_id)` triple on `jobs` conflates "does this job belong in this scenario's search space" with "do I personally want this job" — and `propose_criteria` (the criteria-refinement LLM call) interprets every note through an accept/reject lens (`[REJECTED]`/`[ACCEPTED]` tags) that no longer reliably indicates which of those two questions the note is actually answering. A rejection note like "cool company but too senior for me" is pure attainability noise if it leaks into gate-criteria suggestions.

This spec splits feedback the same way scoring was split:

1. **Gate feedback** — per `(job, scenario)`, given inline on that scenario's score-comparison tab, feeding `propose_criteria`. Its polarity is an explicit, optional choice ("this should score higher" / "this should score lower"), not derived from `status` and not even derived from the tab's own current ✓/✗ — a job can already pass a gate and still get "should score higher" feedback (a weak match the user wants strengthened), or already fail and get "should score lower" (wrong for even stronger reasons than the current score implies). Leaving the direction unset keeps a note purely as commentary, with no effect on criteria refinement.
2. **Personal note** — per job, scenario-agnostic, freeform. No LLM consumer today; a plausible future input for profile-improvement suggestions (out of scope here).
3. **Status** (`accepted`/`rejected`/`invalid`) — stays exactly as it is today, except it drops all scenario coupling. It's purely a personal decision tracker now.

Bulk feedback's scenario dropdown is removed entirely — it only ever existed to set `feedback_scenario_id`, which no longer exists.

---

## 1. Data model

### New table: `scenario_feedback`

```sql
CREATE TABLE IF NOT EXISTS scenario_feedback (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    scenario_id INTEGER NOT NULL REFERENCES scenarios(id) ON DELETE CASCADE,
    note TEXT NOT NULL DEFAULT '',
    direction TEXT CHECK(direction IN ('higher', 'lower')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    handled_at TEXT,
    UNIQUE(job_id, scenario_id)
);
```

One row per `(job, scenario)` — at most one gate-feedback entry per scenario tab per job, upserted on save. `direction` is nullable and optional: `'higher'` means "this scenario should have scored this job higher than it did," `'lower'` means the opposite, `NULL` means no directional stance (a bare comment). A row exists whenever either `note` is non-blank or `direction` is set; clearing both back to blank/unset deletes the row, so "has feedback" is just "row exists."

### `jobs` — drop `feedback_scenario_id`, repurpose `feedback_note`

```python
def _migrate_jobs_drop_feedback_scenario_id(conn: sqlite3.Connection) -> None:
    # feedback_scenario_id is superseded by the scenario_feedback table —
    # status is now fully scenario-agnostic. Direct DROP COLUMN, same
    # pattern already used for scenarios.boosted.
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='jobs'"
    ).fetchone()
    if row is None or "feedback_scenario_id" not in row[0]:
        return
    # Best-effort backfill: an existing (feedback_note, feedback_scenario_id)
    # pair looks exactly like what a gate-feedback note is today, so carry
    # it into scenario_feedback before the column disappears — better than
    # silently discarding real signal that's already been given.
    conn.execute(
        """
        INSERT OR IGNORE INTO scenario_feedback (job_id, scenario_id, note)
        SELECT id, feedback_scenario_id, feedback_note
        FROM jobs
        WHERE feedback_scenario_id IS NOT NULL
          AND feedback_note IS NOT NULL AND feedback_note != ''
        """
    )
    conn.execute("ALTER TABLE jobs DROP COLUMN feedback_scenario_id")
    conn.commit()
```

`jobs.feedback_note` and `jobs.feedback_handled_at` stay as columns — `feedback_note` now means "personal note" only, `feedback_handled_at` becomes dead (see below) rather than removed, since it's a nullable column with no cleanup cost and removing it would be pure churn for this change.

`feedback_handled_at`'s old purpose (skip already-reviewed feedback in `propose_criteria`) moves entirely to `scenario_feedback.handled_at` — `jobs.feedback_handled_at` is no longer read or written by any code path after this change. It is not dropped in this pass (no functional reason to, and dropping it is a one-line follow-up whenever someone next touches this migration file) but is worth noting as now-vestigial, unlike `feedback_scenario_id` which is actively wrong to keep since it implies a scenario coupling that no longer exists.

---

## 2. Query layer (`app/db/queries.py`)

`update_job_feedback` drops scenario coupling:

```python
def update_job_feedback(conn: sqlite3.Connection, job_id: int, status: str, note: str) -> None:
    conn.execute(
        "UPDATE jobs SET status = ?, feedback_note = ? WHERE id = ?",
        (status, note, job_id),
    )
    conn.commit()
```

New gate-feedback functions:

```python
def upsert_scenario_feedback(
    conn: sqlite3.Connection, job_id: int, scenario_id: int, note: str, direction: str | None = None
) -> None:
    note = note.strip()
    if direction not in ("higher", "lower"):
        direction = None
    if not note and direction is None:
        conn.execute(
            "DELETE FROM scenario_feedback WHERE job_id = ? AND scenario_id = ?", (job_id, scenario_id)
        )
    else:
        conn.execute(
            """INSERT INTO scenario_feedback (job_id, scenario_id, note, direction) VALUES (?, ?, ?, ?)
            ON CONFLICT(job_id, scenario_id) DO UPDATE SET note = excluded.note, direction = excluded.direction, handled_at = NULL""",
            (job_id, scenario_id, note, direction),
        )
    conn.commit()
```

Re-saving clears `handled_at` — fresh input the LLM hasn't seen yet, same "resets handled state" behavior the old `update_job_feedback` had.

`get_job_scores` (used by the score-comparison tabs) gains the note and direction so each tab's form can pre-fill:

```python
def get_job_scores(conn: sqlite3.Connection, job_id: int) -> list[dict]:
    return _rows_to_dicts(
        conn.execute(
            """
            SELECT job_scores.*, scenarios.name AS scenario_name, scenarios.gate_threshold AS scenario_gate_threshold,
                   scenario_feedback.note AS feedback_note, scenario_feedback.direction AS feedback_direction
            FROM job_scores
            JOIN scenarios ON scenarios.id = job_scores.scenario_id
            LEFT JOIN scenario_feedback
                ON scenario_feedback.job_id = job_scores.job_id AND scenario_feedback.scenario_id = job_scores.scenario_id
            WHERE job_scores.job_id = ?
            ORDER BY job_scores.relevance_score DESC
            """,
            (job_id,),
        ).fetchall()
    )
```

Refine-flow reads, rewritten against `scenario_feedback`, scoped to rows that actually carry a direction — an undirected (comment-only) row has no polarity for `propose_criteria` to act on, so it's excluded from both the notes fed to the LLM and the job-id list used to mark feedback handled:

```python
def _recent_scenario_feedback_rows(conn: sqlite3.Connection, scenario_id: int, limit: int) -> list[dict]:
    return _rows_to_dicts(
        conn.execute(
            """
            SELECT job_id, note, direction FROM scenario_feedback
            WHERE scenario_id = ? AND direction IS NOT NULL AND handled_at IS NULL
            ORDER BY created_at DESC LIMIT ?
            """,
            (scenario_id, limit),
        ).fetchall()
    )


def get_recent_feedback_notes(conn: sqlite3.Connection, scenario_id: int, limit: int = 20) -> list[dict]:
    return [
        {"direction": r["direction"], "note": r["note"]}
        for r in _recent_scenario_feedback_rows(conn, scenario_id, limit)
    ]


def get_recent_feedback_job_ids(conn: sqlite3.Connection, scenario_id: int, limit: int = 20) -> list[int]:
    return [r["job_id"] for r in _recent_scenario_feedback_rows(conn, scenario_id, limit)]


def mark_feedback_handled(conn: sqlite3.Connection, scenario_id: int, job_ids: list[int]) -> None:
    if not job_ids:
        return
    placeholders = ",".join("?" * len(job_ids))
    conn.execute(
        f"""UPDATE scenario_feedback SET handled_at = datetime('now')
        WHERE scenario_id = ? AND job_id IN ({placeholders})""",
        [scenario_id, *job_ids],
    )
    conn.commit()
```

`mark_feedback_handled` gains a `scenario_id` parameter — handling is now scoped to one scenario's feedback, not a whole job, since the same job can carry independent, independently-reviewed gate feedback for multiple scenarios.

`reset_job` (unchanged by the two-stage-scoring-pipeline spec, but affected here) must also clear `scenario_feedback` for that job:

```python
def reset_job(conn: sqlite3.Connection, job_id: int) -> None:
    conn.execute(
        """UPDATE jobs SET ... """,  # unchanged body
        (job_id,),
    )
    conn.execute("DELETE FROM job_scores WHERE job_id = ?", (job_id,))
    conn.execute("DELETE FROM scenario_feedback WHERE job_id = ?", (job_id,))
    conn.commit()
```

Without this, a "should score higher/lower" note given against one evaluation of a job (a specific summary, a specific gate score) would silently survive a full reprocess into a completely different scored state — the direction stays attached to the job, but the job it was actually about no longer exists in the same form. Clearing it on reset keeps gate feedback honest about which version of the job it was given against, the same reasoning that already justifies wiping `job_scores` itself on reset.

---

## 3. `propose_criteria` — direction tags replace accept/reject tags

`app/ai/refine.py`'s `_SYSTEM` prompt and note-formatting change from `[REJECTED]`/`[ACCEPTED]` (job status) to `[SHOULD SCORE HIGHER]`/`[SHOULD SCORE LOWER]` (the explicit `direction` the user chose). Framing it as a score direction rather than a hard match/no-match boundary means the same tag covers both a full flip (a failing job that should pass) and a within-band nudge (a passing job that should have scored even higher) — `propose_criteria` doesn't need to know or care which case it was, only which way the criteria should move:

```python
_SYSTEM = """You refine job search criteria based on feedback about how a scenario's gate scored
specific jobs.

Each criterion has a weight describing how it affects a job's desirability:
- "must": a hard requirement — jobs lacking this should be rejected outright.
- "prefer": a positive trait — its presence makes a job more desirable, but its absence isn't disqualifying.
- "avoid": a negative trait / red flag — its presence makes a job less desirable or should disqualify it.
A criterion about wanting more of something good (higher pay, better title, more autonomy, etc.) is "must" or "prefer", never "avoid" — "avoid" is only for traits that make a job worse.

Each feedback note says which direction a specific job's score should have moved:
- "[SHOULD SCORE HIGHER] <note>": usually supports loosening a "must" that's too strict, adding
  or strengthening a "prefer" for a trait the job has, or narrowing an "avoid" that's wrongly
  triggering on it.
- "[SHOULD SCORE LOWER] <note>": usually supports adding a "must" or "avoid" criterion for
  whatever the job is missing or has that current criteria don't catch, or narrowing a "prefer"
  that's too generously matching it.

Given a scenario, existing criteria, and feedback notes, propose changes as a JSON array.
Each item: {"text": "<criterion>", "weight": "must|prefer|avoid", "action": "add|remove"}.
Only propose changes clearly supported by the feedback. Return [] if no changes needed.
Respond with a valid JSON array only."""


def propose_criteria(
    client: openai.OpenAI,
    model: str,
    scenario: dict,
    existing_criteria: list[dict],
    feedback_notes: list[dict],
) -> list[CriterionProposal]:
    existing_text = "\n".join(f"[{c['weight'].upper()}] {c['text']}" for c in existing_criteria)
    notes_text = "\n".join(
        f"- [{'SHOULD SCORE HIGHER' if n['direction'] == 'higher' else 'SHOULD SCORE LOWER'}] {n['note']}"
        for n in feedback_notes
    )
    user_content = (
        f"## Scenario: {scenario['name']}\n{scenario.get('description', '')}\n\n"
        f"## Existing Criteria\n{existing_text}\n\n"
        f"## Recent Feedback Notes\n{notes_text}"
    )
    ...  # rest unchanged
```

The reinforcing-signal note in the old prompt (a REJECTED note about lacking X and an ACCEPTED note praising X are the same signal) doesn't need a direct replacement — HIGHER/LOWER notes about the same trait are already unambiguous in one direction each, there's no cross-status pairing to reconcile. Since `_recent_scenario_feedback_rows` already filters to `direction IS NOT NULL`, `feedback_notes` here never contains an undirected row — the ternary never needs a third branch.

---

## 4. Routes

**`app/routes/jobs.py`:**

- `job_feedback` (`POST /jobs/{job_id}/feedback`): drops the `feedback_scenario_id` form field entirely — `status: str = Form(...)`, `note: str | None = Form(None)`, calls `q.update_job_feedback(conn, job_id, status, note)`.
- `job_bulk_feedback` (`POST /jobs/bulk-feedback`): drops `feedback_scenario_id` form field and its per-job top-passed-scenario fallback — every job in the batch just gets `q.update_job_feedback(conn, job_id, status, note)`.
- New: `POST /jobs/{job_id}/scenario-feedback/{scenario_id}` — form fields `note: str = Form("")` and `direction: str = Form("")` (empty string means unset — FastAPI can't default an `Optional[str] = Form(None)` cleanly against an absent vs. empty radio group, so the route normalizes `""` to `None` before calling `q.upsert_scenario_feedback(conn, job_id, scenario_id, note, direction or None)`), returns the updated score-tab panel fragment (so the save is a normal HTMX partial swap, consistent with every other in-place edit in this app).

**`app/routes/scenarios.py`:**

- `refine_criteria` / `refine_all_scenarios`: unchanged call shape (`q.get_recent_feedback_job_ids(scenario_id)`, `q.get_recent_feedback_notes(scenario_id)`) — same function names, new underlying meaning, no route code change needed beyond what the query-layer rewrite already provides.
- `accept_proposals` (`POST /scenarios/{scenario_id}/refine/accept`): its `q.mark_feedback_handled(conn, ...)` call gains the scenario_id argument: `q.mark_feedback_handled(conn, scenario_id, _parse_job_ids(form.get("feedback_job_ids")))`.

---

## 5. Templates

**`app/templates/jobs/_feedback.html`:**
- Score-tab panels gain an inline gate-feedback form: a two-way, independently-deselectable direction toggle ("▲ Should score higher" / "▼ Should score lower" — clicking the already-selected one clears it back to neutral, needing a touch of inline JS since plain radio inputs can't natively deselect, in keeping with the small existing scripts already in `base.html` for bulk-select/drag-select), plus the hint-labeled note field, pre-filled from `js.feedback_note`/`js.feedback_direction`:
  ```html
  <div class="score-tab-panel">
    {{ js.score_reasoning | markdown }}
    <form class="scenario-feedback-form" hx-post="/jobs/{{ job.id }}/scenario-feedback/{{ js.scenario_id }}"
          hx-target="closest .score-tab-panel" hx-swap="outerHTML">
      <input type="hidden" name="direction" value="{{ js.feedback_direction or '' }}">
      <div class="direction-toggle" role="group" aria-label="Score direction">
        <button type="button" class="btn direction-btn" data-direction="higher"
                aria-pressed="{{ 'true' if js.feedback_direction == 'higher' else 'false' }}">▲ Should score higher</button>
        <button type="button" class="btn direction-btn" data-direction="lower"
                aria-pressed="{{ 'true' if js.feedback_direction == 'lower' else 'false' }}">▼ Should score lower</button>
      </div>
      <label>Why should this scenario score different?
        <textarea name="note" placeholder="Optional details">{{ js.feedback_note or '' }}</textarea>
      </label>
      <button type="submit" class="btn">Save note</button>
    </form>
  </div>
  ```
  (Exact JS for the toggle-button/hidden-input wiring is an implementation detail for the plan, not fixed here — the behavioral contract is: clicking a direction button sets the hidden `direction` input to that value and marks itself pressed; clicking the already-pressed one clears the hidden input back to `""` and unmarks itself.)
- `get_job_scores` needs a matching `feedback_direction` field alongside the `feedback_note` join added in §2 — same `LEFT JOIN scenario_feedback`, one more selected column.
- The main feedback form drops the scenario `<select>` entirely — just the personal note textarea and the Accept/Reject/Invalid buttons, unchanged otherwise.

**`app/templates/jobs/_content.html`:** the bulk bar's `<label class="bulk-scenario">Scenario: <select name="feedback_scenario_id" ...>` block is deleted outright — nothing replaces it.

**`app/templates/jobs/_macros.html`:** `meta_tags`' feedback-scenario tag (`job.feedback_scenario_id != job.top_passed_scenario_id`) is deleted along with the field it depended on — there's no more "feedback attributed to a different scenario than the top match" concept to flag, since feedback isn't attributed to a single scenario at all anymore.

---

## 6. Testing strategy

- `test_schema.py`: `scenario_feedback` table created with `UNIQUE(job_id, scenario_id)` and a `direction` column constrained to `higher`/`lower`/`NULL`; `jobs.feedback_scenario_id` dropped; migration backfills existing `(feedback_note, feedback_scenario_id)` pairs into `scenario_feedback` (as note-only rows, `direction` left `NULL` since the old data never captured a direction) before dropping the column; idempotent on repeat `init_db()`.
- `test_queries.py`: `upsert_scenario_feedback` inserts, updates in place, and deletes only when both `note` and `direction` are blank/unset (a direction-only save with no note text still creates a row; a note-only save with no direction still creates a row); `get_job_scores` surfaces each row's `feedback_note` and `feedback_direction`; `get_recent_feedback_notes`/`get_recent_feedback_job_ids` only return rows with a non-null `direction`, ordered most-recent-first; `mark_feedback_handled` is scoped to one scenario (a job with pending feedback on two scenarios only has one cleared); `update_job_feedback` no longer accepts/needs a scenario argument; `reset_job` clears `scenario_feedback` rows for that job alongside `job_scores`.
- `test_refine.py`: `test_propose_criteria_tags_notes_with_outcome` (existing) is rewritten for the new `{"direction": "higher"/"lower", "note": str}` shape and `[SHOULD SCORE HIGHER]`/`[SHOULD SCORE LOWER]` tags, replacing the old `{"status": "accepted"/"rejected", "feedback_note": str}` / `[ACCEPTED]`/`[REJECTED]` version.
- `test_routes_jobs.py`: `POST /jobs/{id}/feedback` works without `feedback_scenario_id`; new `POST /jobs/{id}/scenario-feedback/{scenario_id}` round-trips a note+direction and clears the row when both are submitted blank; an empty-string `direction` form value is treated as unset, not the literal string; bulk feedback no longer requires or accepts `feedback_scenario_id`.
- `test_routes_scenarios.py`: `refine`/`refine/accept` flows still work end-to-end against the new note source; `mark_feedback_handled` scoping verified via the accept-proposals route (a job's feedback for a different scenario stays pending after accepting proposals for this one).

## Out of scope

- Automated profile-improvement suggestions from personal notes — a plausible future consumer of `jobs.feedback_note`, not built here.
- Any calibration loop tying `status`/personal notes back into stage-2 fit scoring (already deferred in the two-stage-scoring-pipeline spec, for the same "not reliable ground truth yet" reason).
- Dropping the now-vestigial `jobs.feedback_handled_at` column — noted above, left as harmless dead weight rather than bundled into this migration.
- A UI affordance for reviewing/clearing gate feedback in bulk across many jobs at once — gate feedback stays a single-job, single-scenario-tab action.
