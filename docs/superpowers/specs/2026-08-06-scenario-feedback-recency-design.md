# Scenario Feedback Recency & Handled/Unhandled Counts — Design Spec

**Date:** 2026-08-06
**Status:** Approved

## Overview

`propose_criteria` (the criteria-refinement LLM call, `app/ai/refine.py`) is fed the "recent" directional gate-feedback notes for a scenario via `_recent_scenario_feedback_rows` (`app/db/queries.py`) — today "recent" just means "the most recent 20 unhandled rows," with no time bound. Since preferences drift as a job search progresses, feedback given months ago shouldn't carry the same weight as feedback given last week, and a slow-moving scenario could reach back arbitrarily far in time to fill its 20-row cap. Separately, there's no visibility anywhere into how many "should score higher" vs. "should score lower" votes exist before you trigger a refine.

This spec:

1. Bounds "recent" by both a time window (30 days) and the existing row cap (20) — whichever is tighter wins.
2. Surfaces higher/lower vote counts on the scenarios page, split into **unhandled** ("new" — will feed the next refine) and **handled** (already incorporated into criteria, still within the 30-day window) counts.
3. Changes how feedback gets marked handled: from "mark exactly the job_ids that were sampled into this batch" to "sweep every unhandled directed vote at or before the newest timestamp in this batch." This closes a gap where, once unhandled rows count exceeds the cap or ages past the window, straggler rows could never be marked handled and would sit invisibly forever.

No schema change — `scenario_feedback` already has `created_at`, `direction`, `handled_at`.

---

## 1. Query layer (`app/db/queries.py`)

`_recent_scenario_feedback_rows` gains a `days` window and now selects `created_at` (needed as the sweep anchor):

```python
def _recent_scenario_feedback_rows(conn: sqlite3.Connection, scenario_id: int, limit: int, days: int = 30) -> list[dict]:
    return _rows_to_dicts(
        conn.execute(
            """
            SELECT job_id, note, direction, created_at FROM scenario_feedback
            WHERE scenario_id = ? AND direction IS NOT NULL AND handled_at IS NULL
              AND created_at >= datetime('now', ? || ' days')
            ORDER BY created_at DESC LIMIT ?
            """,
            (scenario_id, f'-{days}', limit),
        ).fetchall()
    )
```

`get_recent_feedback_notes(conn, scenario_id, limit=20, days=30)` unchanged in shape, just passes `days` through.

`get_recent_feedback_job_ids` is **deleted** — nothing needs a job-id list anymore (see §2 on handled-marking). Replaced by:

```python
def get_recent_feedback_anchor(conn: sqlite3.Connection, scenario_id: int, limit: int = 20, days: int = 30) -> str | None:
    rows = _recent_scenario_feedback_rows(conn, scenario_id, limit, days)
    return rows[0]["created_at"] if rows else None
```

Rows are ordered `created_at DESC`, so `rows[0]` is the newest row in the batch — the anchor timestamp for the sweep in §2. `None` when there's no recent feedback to refine from.

`mark_feedback_handled` changes from a job-id list to an anchor-based sweep:

```python
def mark_feedback_handled(conn: sqlite3.Connection, scenario_id: int, anchor: str | None) -> None:
    if not anchor:
        return
    conn.execute(
        """UPDATE scenario_feedback SET handled_at = datetime('now')
        WHERE scenario_id = ? AND direction IS NOT NULL AND handled_at IS NULL AND created_at <= ?""",
        (scenario_id, anchor),
    )
    conn.commit()
```

This marks handled **every** unhandled directed row at or before the anchor — including rows beyond the 20-row cap that never made it into the LLM's notes (e.g. 25 unhandled votes exist, only 20 feed the batch, but accepting that batch's proposals clears all 25). Anchor is fixed at refine time (the batch's newest row), so a vote cast after refine was triggered but before its proposals were accepted stays unhandled — it wasn't part of what the LLM actually saw.

New `get_recent_feedback_counts`, split by handled/unhandled, both scoped to the same 30-day window:

```python
def get_recent_feedback_counts(conn: sqlite3.Connection, scenario_id: int, limit: int = 20, days: int = 30) -> dict:
    unhandled = _recent_scenario_feedback_rows(conn, scenario_id, limit, days)
    handled = _rows_to_dicts(
        conn.execute(
            """
            SELECT direction FROM scenario_feedback
            WHERE scenario_id = ? AND direction IS NOT NULL AND handled_at IS NOT NULL
              AND created_at >= datetime('now', ? || ' days')
            """,
            (scenario_id, f'-{days}'),
        ).fetchall()
    )
    return {
        "unhandled_higher": sum(1 for r in unhandled if r["direction"] == "higher"),
        "unhandled_lower": sum(1 for r in unhandled if r["direction"] == "lower"),
        "handled_higher": sum(1 for r in handled if r["direction"] == "higher"),
        "handled_lower": sum(1 for r in handled if r["direction"] == "lower"),
    }
```

`unhandled` reuses `_recent_scenario_feedback_rows` directly, so the displayed unhandled count is mechanically guaranteed to match what `propose_criteria` actually receives — no separate query to drift out of sync. `handled` is a plain uncapped count within the window (it's a display total, not an LLM input, so no cap needed).

---

## 2. Routes (`app/routes/scenarios.py`)

- `_scenarios_context`: adds `feedback_counts_by_scenario = {s["id"]: q.get_recent_feedback_counts(conn, s["id"]) for s in scenarios}`, same pattern as the existing `criteria_by_scenario` dict.
- `refine_criteria` / `refine_all_scenarios`: replace `job_ids = q.get_recent_feedback_job_ids(...)` with `anchor = q.get_recent_feedback_anchor(conn, scenario_id)`; pass `feedback_anchor=anchor` to the proposals template instead of `feedback_job_ids`.
- `accept_proposals`: `q.mark_feedback_handled(conn, scenario_id, form.get("feedback_anchor") or None)`.
- `_parse_job_ids` helper is deleted — nothing calls it once job-id-based handling is gone.

---

## 3. Templates

**`app/templates/scenarios/_proposals.html`:** hidden field becomes:
```html
<input type="hidden" name="feedback_anchor" value="{{ feedback_anchor or '' }}">
```

**`app/templates/scenarios/index.html`:** two badges next to the "Refine criteria from feedback" button, each independently hidden when both its counts are zero:

```html
{% set fc = feedback_counts_by_scenario[scenario.id] %}
{% if fc.unhandled_higher or fc.unhandled_lower %}
<span class="tag" title="Since last adjustment — will feed the next refine">▲{{ fc.unhandled_higher }} · ▼{{ fc.unhandled_lower }} new</span>
{% endif %}
{% if fc.handled_higher or fc.handled_lower %}
<span class="tag" title="Already incorporated, last 30 days">▲{{ fc.handled_higher }} · ▼{{ fc.handled_lower }} handled</span>
{% endif %}
```

---

## 4. Testing strategy

- `test_queries.py`:
  - `_recent_scenario_feedback_rows` / `get_recent_feedback_notes`: rows older than the 30-day window are excluded even if under the 20-row cap; the row cap still applies within the window.
  - `get_recent_feedback_anchor`: returns the newest `created_at` among the in-window/in-cap rows; `None` when there's no recent feedback.
  - `mark_feedback_handled`: sweeps *all* unhandled directed rows for the scenario at or before the anchor, including rows beyond the 20-row cap; rows created after the anchor are left unhandled; rows belonging to other scenarios are untouched; a `None`/falsy anchor is a no-op.
  - `get_recent_feedback_counts`: unhandled counts match `get_recent_feedback_notes`'s row set exactly (same rows, split by direction); handled counts only include rows with `handled_at` set, scoped to the 30-day window; both split correctly by direction.
- `test_routes_scenarios.py`:
  - Proposals form round-trips `feedback_anchor` (not `feedback_job_ids`).
  - `refine` → `refine/accept` end-to-end: accepting sweeps all eligible unhandled rows, not just the ones whose notes were shown.
  - Scenarios page renders both badges with correct counts; each badge is omitted independently when its counts are zero.

## Out of scope

- Any UI to configure the 30-day window or 20-row cap.
- Live-updating the badges after a refine/accept without a full page reload.
- Any decay/weighting scheme beyond a hard time cutoff.
- Dropping the now-fully-vestigial `jobs.feedback_handled_at` column (already noted as out of scope in the 2026-08-05 scenario-scoped-feedback spec).

---

## Addendum (same day): "Feedback & Refinement" section, quality indicator

The ▲/▼ badges shipped above tested fine functionally but read as unclear icons with no interpretation attached — just raw counts, no verdict. This addendum replaces them with a dedicated section that leads with a plain-language verdict.

### 1. New query function

`get_feedback_satisfaction_counts(conn, scenario_id, days=30) -> dict` — **all** directed feedback in the window regardless of handled state, **uncapped** (unlike `_recent_scenario_feedback_rows`, which caps at `limit` to match what `propose_criteria` actually sees). This is a broader, longer-range view of the same higher/lower signal:

```python
def get_feedback_satisfaction_counts(conn: sqlite3.Connection, scenario_id: int, days: int = 30) -> dict:
    rows = _rows_to_dicts(
        conn.execute(
            """
            SELECT direction FROM scenario_feedback
            WHERE scenario_id = ? AND direction IS NOT NULL
              AND created_at >= datetime('now', ? || ' days')
            """,
            (scenario_id, f'-{days}'),
        ).fetchall()
    )
    return {
        "higher": sum(1 for r in rows if r["direction"] == "higher"),
        "lower": sum(1 for r in rows if r["direction"] == "lower"),
    }
```

### 2. Quality-indicator verdict (`app/routes/scenarios.py`)

A pure helper — no DB access, so it lives alongside `_resolve_proposals` as a private route-module helper rather than in `queries.py`. Computed from the **unhandled** counts (`get_recent_feedback_counts`'s `unhandled_higher`/`unhandled_lower`) — the set that will actually feed the next refine. No minimum vote count: even a single vote produces a verdict, on the reasoning that the raw counts are always shown alongside it so the user can judge confidence themselves.

- "higher" votes mean good jobs are getting filtered out → criteria too strict → loosen.
- "lower" votes mean bad jobs are getting through → criteria too loose → tighten.
- Ratio within `[0.4, 0.6]` of higher-to-total reads as balanced.

```python
def _feedback_quality_verdict(higher: int, lower: int) -> dict:
    total = higher + lower
    if total == 0:
        return {"label": "No unhandled feedback yet", "css_class": ""}
    ratio = higher / total
    if ratio > 0.6:
        return {"label": "Criteria may be too strict — consider loosening", "css_class": "score-mid"}
    if ratio < 0.4:
        return {"label": "Criteria may be too loose — consider tightening", "css_class": "score-mid"}
    return {"label": "Feedback seems balanced", "css_class": "score-high"}
```

`css_class` reuses the existing `score-badge`/`score-high`/`score-mid`/`score-low` styling already used for job scores (`jobs/_score_tabs.html`, `jobs/_macros.html`) — no new color language introduced. Balanced reads green; either directional verdict reads amber (neither direction is "worse" than the other, both just mean "needs a look").

### 3. Route (`app/routes/scenarios.py`)

`_scenarios_context` gains two more per-scenario dicts, same pattern as `criteria_by_scenario`/`feedback_counts_by_scenario`:

```python
feedback_satisfaction_by_scenario = {s["id"]: q.get_feedback_satisfaction_counts(conn, s["id"]) for s in scenarios}
feedback_verdict_by_scenario = {
    s["id"]: _feedback_quality_verdict(
        feedback_counts_by_scenario[s["id"]]["unhandled_higher"],
        feedback_counts_by_scenario[s["id"]]["unhandled_lower"],
    )
    for s in scenarios
}
```

### 4. Template (`scenarios/index.html`)

Replaces the triangle-badge button row with: a small standalone row for "Re-evaluate jobs" (unrelated to feedback, stays outside), and a new bordered "Feedback & Refinement" section containing a header, the verdict badge with its supporting counts, both stat lines in plain words, the "Refine criteria from feedback" button, and the existing proposals area:

```html
<div style="margin-top:0.75rem; display:flex; gap:0.5rem; flex-wrap:wrap;">
  <button class="btn" data-progress-url="/scenarios/{{ scenario.id }}/reevaluate">Re-evaluate jobs</button>
</div>

<div style="margin-top:0.75rem; border:1px solid #dee2e6; border-radius:6px; padding:0.75rem;">
  <h4 style="margin:0 0 0.5rem;">Feedback & Refinement</h4>
  {% set fc = feedback_counts_by_scenario[scenario.id] %}
  {% set sat = feedback_satisfaction_by_scenario[scenario.id] %}
  {% set verdict = feedback_verdict_by_scenario[scenario.id] %}
  <p style="margin:0 0 0.5rem;">
    <span class="score-badge {{ verdict.css_class }}">{{ verdict.label }}</span>
    {% if fc.unhandled_higher or fc.unhandled_lower %}
    <span style="color:#666; font-size:0.85em;">({{ fc.unhandled_higher }} should score higher, {{ fc.unhandled_lower }} should score lower)</span>
    {% endif %}
  </p>
  <p style="margin:0 0 0.75rem; font-size:0.85em; color:#666;">
    Unhandled, will feed next refine: {{ fc.unhandled_higher }} higher / {{ fc.unhandled_lower }} lower<br>
    Last 30 days, all feedback: {{ sat.higher }} higher / {{ sat.lower }} lower
  </p>
  <button class="btn" data-progress-url="/scenarios/{{ scenario.id }}/refine" data-progress-target="#proposals-area-{{ scenario.id }}">
    Refine criteria from feedback
  </button>
  <div id="proposals-area-{{ scenario.id }}" style="margin-top:0.75rem;"></div>
</div>
```

`get_recent_feedback_counts`'s `handled_higher`/`handled_lower` fields stay in the query (already tested by the base spec) but go unused by the template after this addendum — the "already incorporated" badge they drove is dropped, its information now implicit in the two stat lines (unhandled = pending, 30-day-all = includes handled).

### 5. Testing

- `test_queries.py`: `get_feedback_satisfaction_counts` includes handled and unhandled rows alike within the window; excludes rows older than the window; uncapped (returns more than `limit` if that many exist).
- `test_routes_scenarios.py`: scenarios page renders the verdict label and both stat lines with correct numbers; a scenario with zero unhandled feedback shows the "No unhandled feedback yet" verdict; the old triangle-badge assertions from the base spec's tests are replaced with assertions against the new plain-word stat lines.

### Out of scope (addendum)

- Any DB-level test for `_feedback_quality_verdict` itself (it's pure/no I/O) — covered indirectly through the route test asserting the rendered label.
- Configurable verdict thresholds.

---

## Addendum 2 (same day): copy simplification, drop the satisfaction stat

Manual testing of Addendum 1 showed the section had three near-identical "N higher / M lower" numbers with no explanation of what they meant — and in the common case (nothing refined yet), the "unhandled" and "last 30 days, all feedback" lines were literally identical, reading as pointless duplication.

Changes from Addendum 1:

- `get_feedback_satisfaction_counts` and `feedback_satisfaction_by_scenario` are **removed entirely** (function, route wiring, template usage, tests). At this app's personal, single-user scale, `unhandled_* + handled_*` (both already computed by `get_recent_feedback_counts`) covers the same ground as the uncapped 30-day figure in virtually every real case — keeping a third, separately-computed "satisfaction" number added confusion without adding real information.
- The verdict's counts vocabulary now matches the verdict's own words — "too strict" / "too loose" — instead of raw "higher" / "lower", so the connection between the numbers and the verdict is direct instead of requiring a mental translation.
- The pending-votes line always shows (`N vote(s) said "too strict" · M vote(s) said "too loose" — pending, will be used in the next refine`), correctly pluralized.
- The "Also applied earlier (last 30 days): X too strict, Y too loose" line — sourced from `get_recent_feedback_counts`'s existing `handled_higher`/`handled_lower` — only renders when non-zero, instead of always showing a number that's usually zero or a duplicate of the pending line.
- The verdict badge's own parenthetical count (`(N should score higher, M should score lower)`) is dropped — it duplicated the pending-votes line directly beneath it.

Testing: `test_routes_scenarios.py`'s verdict tests are rewritten to assert the new wording and the "Also applied earlier" line's presence/absence; `test_queries.py`'s `get_feedback_satisfaction_counts` tests are deleted along with the function.
