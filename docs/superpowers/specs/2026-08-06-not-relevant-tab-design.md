# "Not Relevant" Tab — Design Spec

**Date:** 2026-08-06
**Status:** Approved

## Overview

The job list's "New (N)" tab badge currently counts every `status='new'` job (`get_job_counts`, `app/db/queries.py:368-375` — a plain `GROUP BY status`), but the New list itself only *shows* `status='new'` jobs that cleared at least one scenario's `gate_threshold` (`_get_filtered_jobs` → `get_jobs(..., gate_passed_only=True)`, `app/routes/jobs.py:20-27`). Gate-failed jobs are hidden by default and only reachable through a `show_filtered=1` toggle. The badge count and the visible list disagree, which reads as a bug.

Rather than just fixing the count to match the hidden default, this splits `status='new'` into two persistent tabs:

- **New** — jobs awaiting triage that cleared (or haven't yet been scored against) at least one scenario's gate.
- **Not relevant** — job postings that have been scored and failed every scenario's gate, but haven't been manually triaged into Accepted/Rejected/Invalid yet.

These two buckets are a strict partition of `status='new'`: every `status='new'` row lands in exactly one of them, so their counts always sum to the raw `status='new'` total. No new column or migration is needed — "Not relevant" is a derived filter over existing data (`job_scores` + `scenarios.gate_threshold`), computed live at read time, same as gate-passing is today.

Gate/threshold filtering is removed everywhere else it currently applies by default. Accepted, Rejected, Invalid, and Leads all show everything matching their filter regardless of score — the gate only matters for deciding which side of the New/Not-relevant split a still-untriaged posting falls on. This removes the `show_filtered` toggle entirely; nothing needs it once no other view filters on the gate.

Leads are excluded from "Not relevant": a `content_type='lead'` job that fails the gate is only ever reachable via the Leads tab (which now shows all leads, unfiltered by score), never routed into "Not relevant". Since only `content_type='job_posting'` and `content_type='lead'` jobs ever get scored (`app/pipeline.py:66`), "Not relevant" is implicitly `content_type='job_posting'` only — `irrelevant`/`error`/`NULL` content types are never gate-evaluated and stay in New (as unscored jobs already do today).

---

## 1. Data model

No schema change. "Not relevant" is a query-time filter, not a stored status — `jobs.status` remains `'new'` for these rows.

---

## 2. Query layer (`app/db/queries.py`)

### `get_jobs()`

Replace the `gate_passed_only: bool = False` parameter with `gate_status: str | None = None`:

- `None` (default) — no gate filtering. Used for Accepted, Rejected, Invalid, Leads.
- `"passed"` — excludes gate-failed `job_posting`s; leads/irrelevant/error/unscored jobs always pass through untouched. Used for the New tab.
- `"failed"` — includes only scored `job_posting`s that failed every scenario's gate. Used (together with `content_type="job_posting"`) for the Not-relevant tab.

```python
def get_jobs(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    content_type: str | None = None,
    gate_status: str | None = None,
) -> list[dict]:
    clauses, params = [], []
    if status is not None:
        clauses.append("jobs.status = ?")
        params.append(status)
    if content_type is not None:
        clauses.append("jobs.content_type = ?")
        params.append(content_type)
    if gate_status == "passed":
        clauses.append(
            "(jobs.content_type != 'job_posting' OR jobs.content_type IS NULL "
            "OR scored.scored_count IS NULL OR gate.passed_count > 0)"
        )
    elif gate_status == "failed":
        clauses.append(
            "scored.scored_count IS NOT NULL AND (gate.passed_count IS NULL OR gate.passed_count = 0)"
        )
    sql = f"SELECT {_GATE_SELECT} {_GATE_JOIN}"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY jobs.fit_score DESC NULLS LAST, jobs.fetched_at DESC"
    return _rows_to_dicts(conn.execute(sql, params).fetchall())
```

(`_GATE_SELECT`/`_GATE_JOIN` are unchanged — same gate/scored subqueries already used today.)

### `get_job_counts()`

Add a `not_relevant` key. Compute gate-failed `status='new'` postings with one extra query, then subtract that from the raw `status='new'` group-by count so `new` reflects only what the New tab actually shows:

```python
def get_job_counts(conn: sqlite3.Connection) -> dict[str, int]:
    counts = {"new": 0, "accepted": 0, "rejected": 0, "invalid": 0, "lead": 0, "not_relevant": 0}
    for status, n in conn.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status").fetchall():
        counts[status] = n
    counts["lead"] = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE content_type = ?", ("lead",)
    ).fetchone()[0]
    not_relevant = conn.execute(
        f"""
        SELECT COUNT(*) {_GATE_JOIN}
        WHERE jobs.status = 'new' AND jobs.content_type = 'job_posting'
          AND scored.scored_count IS NOT NULL
          AND (gate.passed_count IS NULL OR gate.passed_count = 0)
        """
    ).fetchone()[0]
    counts["not_relevant"] = not_relevant
    counts["new"] -= not_relevant
    return counts
```

---

## 3. Route layer (`app/routes/jobs.py`)

`status=not_relevant` becomes a recognized pseudo-value on the existing `status` query param, alongside the real DB values (`accepted`/`rejected`/`invalid`) it already handles — no new query param, and existing tab-active-state comparisons (`status == "..."`) keep working unchanged.

```python
def _get_filtered_jobs(
    conn: sqlite3.Connection, status: str | None, content_type: str | None
) -> list[dict]:
    if status == "not_relevant":
        return q.get_jobs(conn, status="new", content_type="job_posting", gate_status="failed")
    if status is None and content_type is None:
        return q.get_jobs(conn, status="new", gate_status="passed")
    return q.get_jobs(conn, status=status, content_type=content_type)
```

`show_filtered` is removed from:
- `job_list` (`GET /jobs`) — drop the query param and the `show_filtered` context value.
- `job_bulk_feedback` (`POST /jobs/bulk-feedback`) — drop the `show_filtered_filter` form field.

`effective_status` logic in both routes is unchanged (`status=not_relevant` flows through it like any other status value).

---

## 4. Templates

`app/templates/jobs/_content.html`:
- Remove the "Show filtered" / "Show filtered ✓" link block (lines 8-12).
- Add a tab between Leads and Invalid:
  ```html
  <a href="/jobs?status=not_relevant" {% if status == "not_relevant" %}class="active"{% endif %}>Not relevant ({{ counts.not_relevant }})</a>
  ```
- Resulting tab order: New, Accepted, Rejected, Leads, Not relevant, Invalid.

`app/templates/jobs/list.html`:
- Remove the `show_filtered_filter` hidden input (line 8).

No changes needed to `_row.html` or `_feedback.html` — rows render the same regardless of which tab query produced them.

---

## 5. Testing

- `test_queries.py`: replace `test_get_jobs_gate_passed_only_excludes_below_threshold` and `test_get_jobs_gate_passed_only_keeps_never_scored_jobs` with equivalents against `gate_status="passed"`; add a `gate_status="failed"` case; extend `test_get_job_counts` to cover `not_relevant` and confirm `new + not_relevant == raw status='new' count`; add a case confirming a gate-failed lead is excluded from `gate_status="failed"` (content_type filter already handles this, but worth asserting explicitly).
- `test_routes_jobs.py`: replace the `show_filtered=1` case (line 562) with a `status=not_relevant` case; add coverage that Accepted/Rejected/Invalid/Leads include gate-failed jobs by default now.
- No changes needed to pipeline or scenario tests — `gate_threshold` itself is untouched.

---

## Out of scope

- No change to how gate thresholds are set or scored (`app/ai/evaluate.py`, scenario editing UI).
- No change to the Leads tab's own filtering beyond removing the gate filter it inherited by default.
- No bulk-action changes — "Not relevant" jobs get Accept/Reject/Invalid/Reset through the same bulk bar as any other tab, unchanged.
