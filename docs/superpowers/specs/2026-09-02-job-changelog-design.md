# Per-job changelog

## Goal

Give the user a quick way to see **what has changed** about a job since it was
first fetched: the offer text changed on a revisit, a re-evaluation moved the
scores, the user (or the system) changed the status. A simple reverse-chronological
list of timestamped one-line notes on the expanded job view.

System-generated only. No manual note entry (the existing free-text "Job note"
field stays as the user's scratchpad).

## Data model

New table, purely additive — no `jobs` rebuild, so no `jobs_fts` trigger concerns.

```sql
CREATE TABLE job_events (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    kind TEXT NOT NULL,          -- 'status' | 'score' | 'revisit'
    message TEXT NOT NULL
);
```

`kind` drives nothing functional today; it exists so tests and the template can
group/style entries without matching on message text. Added straight to the
`_DDL` block as a `CREATE TABLE IF NOT EXISTS` (the pattern every other table
except the rebuilt ones uses) — a fresh DB and an existing one both pick it up,
no `_migrate_*` function needed. **No backfill** — existing jobs start with an
empty changelog.

## What writes entries

The actions themselves are never logged — only their **outcomes**. An action that
changes nothing writes nothing.

| Trigger | Entries produced |
|---|---|
| Accept / reject / trash (single or bulk) | `status` entry iff the status value actually changed. User's note appended when given. |
| Auto-trash on revisit / close-detection | `status` entry: `Moved to Trash on revisit — <reason>`. **Replaces** the current `feedback_note` append. |
| Revisit found the posting changed | `revisit` entry with the model's short reason, **plus** a `score` entry if the follow-up re-evaluation then moved scores. |
| "Reset to new" / reprocess | `status` entry iff status changed (e.g. `rejected → new`), **plus** `score` entry if the fresh pipeline run moved scores vs. their pre-reset values. The reset action itself: nothing. |
| "Pass as new" (gate override) | one entry: `Filed as New — gate threshold bypassed`. The first-time fit score is not a change, so no `score` entry. |
| Single-job "Re-evaluate" button | `score` entry if scores moved. |
| Per-scenario bulk re-eval / profile-fit bulk recompute | `score` entry per affected job if its scores moved. |

### Score-change rule

Score entries are produced **inside the low-level write functions**
`upsert_job_score` and `update_job_fit`, which already perform the `UPDATE`. Each
reads the previous value first and, comparing to the new one, may append an event:

- **Relevance score** (`upsert_job_score`): emit when `abs(new - old) >= 0.05`,
  or whenever the score **crosses the scenario's `gate_threshold`** in either
  direction (always called out, regardless of delta size):
  - `Re-scored "AI Safety" 0.40 → 0.55`
  - `Re-scored "AI Safety" 0.68 → 0.71 — now passes gate`
- **Fit score** (`update_job_fit`): emit when `abs(new - old) >= 0.05`:
  - `Fit re-assessed 0.62 → 0.71`
- When `old` is `NULL` (first evaluation, or "pass as new" computing fit for the
  first time), emit nothing — there is no change to report.

Consequence: a re-evaluation that touches three scenarios and the fit score
produces up to four entries sharing one timestamp, rather than a single combined
line. Accepted as the cost of keeping the mechanism almost entirely in the
query layer (see Alternatives).

Both bullets share one helper, `log_score_change(conn, job_id, *, label, old,
new, threshold=None)`, which applies the delta/crossing rule and formats the
message. `upsert_job_score` and `update_job_fit` call it directly.

**Reset** deletes the `job_scores` rows and nulls the fit columns exactly as
today, so `upsert_job_score` / `update_job_fit` see `old = NULL` during the
reprocess run and stay silent. To still record what the reset changed,
`run_reprocess_job` (the only reset path) snapshots
`{scenario_id: relevance_score}` and `fit_score` *before* calling `q.reset_job`,
then after the pipeline finishes calls `q.log_score_change(...)` once per
scenario and once for fit against that snapshot. This is the one place
`pipeline.py` gains score-diffing logic — every other re-eval path keeps its
existing scores in place and is covered by the in-function calls above.

### Error-job edge case

`_insert_error_job` creates a row and immediately trashes it. It passes
`record_event=False` (new optional kwarg on `update_job_feedback`) so no
`new → trash` entry is written for a job the user never saw.

## Display

All changes are in `jobs/_feedback.html` (the expanded / detail view; collapsed
rows are untouched).

1. **Data age** — a small muted line directly below the summary section:
   `Fetched 12 days ago` (`job.fetched_at | time_ago`). Always shown.

2. **History block** — directly above the `Advanced…` `<details>`:

   ```
   ▸ History (4)
      2026-09-02   Status: new → rejected — "role moved to London"
      2026-08-30   Fit re-assessed 0.62 → 0.71
      2026-08-30   Revisit: posting changed — salary band added
      2026-08-21   Status: new → accepted
   ```

   A collapsed `<details class="job-history">`. Rendered only when the job has
   ≥ 1 entry. Newest first. Date shown inline; full ISO timestamp in the row's
   `title` attribute. Minimal CSS — a muted, monospace-ish list consistent with
   the existing `.job-advanced` styling.

The three routes that render `_feedback.html` — `job_detail`, `job_expand`,
`_render_updated_job_html` — each gain a `job_events = q.get_job_events(conn, job_id)`
call and pass it in context, mirroring how `job_scores` is already loaded.

## New / changed query functions

- `add_job_event(conn, job_id, kind, message)` — insert.
- `get_job_events(conn, job_id) -> list[dict]` — newest first.
- `log_score_change(conn, job_id, *, label, old, new, threshold=None)` — shared
  helper: no-op when `old`/`new` is `None` or the move is < 0.05 and no gate
  crossing; otherwise inserts a `score` event.
- `upsert_job_score` — read old `relevance_score` before the upsert; look up the
  scenario `name` + `gate_threshold`; call `log_score_change` after.
- `update_job_fit` — read old `fit_score` before the update; call the helper
  after.
- `update_job_feedback(conn, job_id, status, note, *, record_event=True)` — read
  old status; when it changed and `record_event`, write a `status` entry.
- `mark_job_closed` — write a `status` entry instead of appending to
  `feedback_note`.
- `mark_job_gate_override` — write the `Filed as New — gate threshold bypassed`
  entry.
- `reset_job` — unchanged.

`pipeline.py` gains two things: (1) one line in `run_revisit_job`'s changed
branch — `add_job_event(conn, job["id"], "revisit", f"Revisit: posting changed — {reason}")`;
(2) a before/after score snapshot in `run_reprocess_job` (the reset path), which
after the pipeline finishes calls `q.log_score_change` per scenario and for fit
against the pre-reset values. Everything else flows through the query-layer
helpers above.

## Testing

- **queries**: `add_job_event` / `get_job_events` ordering; `update_job_feedback`
  logs on a real change, stays silent on a no-op and when `record_event=False`;
  `mark_job_closed` writes an event and no longer mutates `feedback_note`;
  `mark_job_gate_override` writes its entry.
- **score logging**: `upsert_job_score` — no entry on first score, entry on a
  ≥ 0.05 move, entry on a sub-threshold move that crosses `gate_threshold` with
  the "now passes gate" / "no longer passes gate" suffix, no entry on a < 0.05
  non-crossing move. `update_job_fit` — same delta rule, no entry from `NULL`.
- **reset**: a reprocess run whose status and scores end up different from before
  yields both a `status` entry and one or more `score` entries (pipeline test
  with a mocked client).
- **revisit**: the "changed" outcome yields a `revisit` entry (pipeline test).
- **route/template**: History block renders with entries and the "Fetched N days
  ago" line; block is absent when there are no entries.

## Alternatives considered

**Snapshot-and-diff in `pipeline.py`.** Capture `{Fit, <scenario>: score}` before
each re-eval, diff after, emit one combined entry per run. Produces tidier single
-line summaries but threads a snapshot dict through five generator functions
(`run_revisit_job`, `run_reevaluate_job`, `run_reevaluate`, `run_reassess_fit`,
`run_reprocess_job`). Rejected: the query-layer approach covers four of those
five paths with two in-function calls, leaving only `run_reprocess_job` (which
wipes its scores) needing an explicit snapshot; the only visible cost is
multiple same-timestamp lines instead of one combined line.

**Manual notes / unified timeline.** Add a note box feeding the same list.
Rejected for now — out of scope; the existing "Job note" field covers freeform
capture.

**Backfilling a "tracked since" entry.** Rejected — noise; the "Fetched N days
ago" line already conveys the job's age.
