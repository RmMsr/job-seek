# Scenario Score Boost & Comparison View — Design Spec

**Date:** 2026-08-04
**Status:** Approved

## Overview

Scoring is currently one independent LLM call per (job, scenario) pair (`app/ai/evaluate.py`), and the "best scenario" for a job is whichever got the numerically highest raw `relevance_score` (`_BEST_SCORE_JOIN` in `app/db/queries.py`). There's no way to designate a scenario (e.g. `ai_expert`) as a catch-all fallback — it only wins by accident when its score happens to be highest, and there's no visibility into how a job scored across scenarios to understand *why* a particular one won.

This spec adds two things:

1. A `boosted` flag on scenarios. A boosted scenario gets a flat `+0.2` added to its raw score *only* when picking a job's best-match scenario — never shown as the displayed score. Any number of scenarios can be boosted (e.g. a generic `ai_expert` fallback, plus separate boosted scenarios for structurally different job types like a side-job search or remote-only search), so specific scenarios have to clear a real bar to beat them.
2. A per-job scenario score comparison view: a tab strip in the job detail view, one tab per scored scenario, tab header = small score card (name, raw score, boosted indicator), body = that scenario's reasoning.

Explicitly out of scope: any change to how `evaluate()` computes a score (that's a separate, deferred question — the comparison view exists partly so there's real data to look at before deciding if structured per-criterion scoring is worth it). Also out of scope: a `boosted` indicator in the bulk-feedback scenario dropdown, and making `BOOST_BONUS` configurable — it's a hardcoded constant for now, with a tooltip in the UI documenting its value.

---

## 1. Data model (`app/db/schema.py`)

Replace the vestigial `active` column (unused since the active-scenario concept was removed — see `.superpowers/sdd/fix-active-scenario-report.md`) with `boosted`. Hard-downtime rebuild, per project convention — no dual-schema handling.

DDL block, `scenarios` table:

```sql
CREATE TABLE IF NOT EXISTS scenarios (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    boosted INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

New migration, registered in `init_db` alongside the others:

```python
def _migrate_scenarios_boosted_flag(conn: sqlite3.Connection) -> None:
    # Replaces the vestigial "active" column (unused since the active-scenario
    # concept was removed) with "boosted", which drives the fallback-scoring
    # bonus below.
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='scenarios'"
    ).fetchone()
    if row is None or "boosted" in row[0]:
        return
    conn.execute("ALTER TABLE scenarios DROP COLUMN active")
    conn.execute("ALTER TABLE scenarios ADD COLUMN boosted INTEGER NOT NULL DEFAULT 0")
    conn.commit()
```

After this migration, `ai_expert` (and any other intended fallback) needs `boosted` set to `1` by hand (via the new UI checkbox, §3) — the migration itself doesn't guess which scenario(s) should be boosted.

## 2. Selection algorithm (`app/db/queries.py`)

A module-level constant next to `_BEST_SCORE_JOIN`:

```python
BOOST_BONUS = 0.2  # flat bonus added to a boosted scenario's score when picking a job's best match
```

`_BEST_SCORE_JOIN` changes to join `scenarios` inside the window-function subquery so the bonus can be applied before ranking:

```python
_BEST_SCORE_JOIN = f"""
    FROM jobs
    LEFT JOIN (
        SELECT job_scores.job_id, job_scores.scenario_id, job_scores.relevance_score, job_scores.score_reasoning,
               ROW_NUMBER() OVER (
                   PARTITION BY job_scores.job_id
                   ORDER BY job_scores.relevance_score + CASE WHEN s.boosted THEN {BOOST_BONUS} ELSE 0 END DESC,
                            job_scores.scenario_id ASC
               ) AS rn
        FROM job_scores
        JOIN scenarios s ON s.id = job_scores.scenario_id
    ) best ON best.job_id = jobs.id AND best.rn = 1
    LEFT JOIN scenarios ON scenarios.id = best.scenario_id
    LEFT JOIN scenarios AS feedback_scenarios ON feedback_scenarios.id = jobs.feedback_scenario_id
"""
```

`best.relevance_score` (exposed as `best_score`/used for `ORDER BY` in `get_jobs`) stays the **raw** score of whichever scenario won — the bonus only affects which row gets `rn = 1`, it's never added to a value that's displayed or used to compare jobs against each other. This matches the earlier decision that the boost is a tie-break mechanism, not a quality signal.

`get_scenario`/`get_scenarios` need no changes (`SELECT *` already picks up `boosted`).

`update_scenario` gains a `boosted` parameter:

```python
def update_scenario(conn: sqlite3.Connection, scenario_id: int, name: str, description: str, boosted: bool = False) -> None:
    conn.execute(
        "UPDATE scenarios SET name = ?, description = ?, boosted = ? WHERE id = ?",
        (name, description, int(boosted), scenario_id),
    )
    conn.commit()
```

New query for the comparison view:

```python
def get_job_scores(conn: sqlite3.Connection, job_id: int) -> list[dict]:
    return _rows_to_dicts(
        conn.execute(
            """
            SELECT job_scores.*, scenarios.name AS scenario_name, scenarios.boosted AS scenario_boosted
            FROM job_scores
            JOIN scenarios ON scenarios.id = job_scores.scenario_id
            WHERE job_scores.job_id = ?
            ORDER BY job_scores.relevance_score DESC
            """,
            (job_id,),
        ).fetchall()
    )
```

Ordered by raw score (not boosted) — the "which one actually matched best on its own merits" ordering, consistent with the "minimal transparency" decision (a boosted scenario shows its indicator icon but doesn't get any special sort treatment in this list).

## 3. Marking a scenario boosted (`app/routes/scenarios.py`, templates)

`update_scenario` route (`POST /scenarios/{scenario_id}`), following the existing checkbox pattern used for `sources.enabled` (`app/routes/sources.py`):

```python
@router.post("/scenarios/{scenario_id}", response_class=HTMLResponse)
def update_scenario(
    scenario_id: int,
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    boosted: Optional[str] = Form(None),
    conn: sqlite3.Connection = Depends(get_db),
):
    _get_scenario_or_404(conn, scenario_id)
    q.update_scenario(conn, scenario_id, name=name, description=description, boosted=boosted is not None)
    scenario = q.get_scenario(conn, scenario_id)
    return templates.TemplateResponse(request, "scenarios/_header.html", {"scenario": scenario})
```

`_header_edit.html` gains a checkbox with a tooltip documenting the bonus value:

```html
<label style="display:flex; align-items:center; gap:0.3rem; font-size:0.9em;"
       title="Boosted scenarios get a flat +20 point bonus when picking a job's best match">
  <input type="checkbox" name="boosted" {% if scenario.boosted %}checked{% endif %}> Boosted
</label>
```

`_header.html` shows a tag when boosted, so it's visible at a glance on the scenarios page without entering edit mode:

```html
{% if scenario.boosted %}<span class="tag" title="Gets a +20 point bonus when picking a job's best match">⚡ Boosted</span>{% endif %}
```

## 4. Score comparison view (`app/routes/jobs.py`, `app/templates/jobs/_feedback.html`)

`job_expand` passes the new data through:

```python
@router.get("/jobs/{job_id}/expand", response_class=HTMLResponse)
def job_expand(job_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    job = q.get_job(conn, job_id)
    sources = {s["id"]: s for s in q.get_sources(conn)}
    job["source_name"] = sources.get(job["source_id"], {}).get("name", "")
    scenarios = q.get_scenarios(conn)
    job_scores = q.get_job_scores(conn, job_id)
    return templates.TemplateResponse(request, "jobs/_feedback.html", {"job": job, "scenarios": scenarios, "job_scores": job_scores})
```

`_feedback.html`: replace the current single `score-box` block —

```html
{% if job.best_score_reasoning %}
  <section class="score-box" aria-label="Score reasoning">
    {{ macros.score_scenario(job) }}
    {{ job.best_score_reasoning | markdown }}
  </section>
{% endif %}
```

— with a tab strip covering every scored scenario. Implemented as CSS-only radio-button tabs (no JS), matching the app's existing lightweight server-rendered style:

```html
{% if job_scores %}
  <section class="score-box score-compare" aria-label="Scenario score comparison">
    <div class="score-tabs">
      {% for js in job_scores %}
        <input type="radio" id="score-tab-{{ job.id }}-{{ js.scenario_id }}" name="score-tab-{{ job.id }}"
               class="score-tab-input" {% if js.scenario_id == job.best_scenario_id %}checked{% endif %}>
        <label for="score-tab-{{ job.id }}-{{ js.scenario_id }}" class="score-tab-card">
          {% if js.scenario_boosted %}<span title="Boosted scenario">⚡</span>{% endif %}
          <span class="tag">{{ js.scenario_name }}</span>
          {% if js.relevance_score >= 0.7 %}<span class="score-badge score-high">
          {% elif js.relevance_score >= 0.4 %}<span class="score-badge score-mid">
          {% else %}<span class="score-badge score-low">
          {% endif %}{{ "%.0f"|format(js.relevance_score * 100) }}%</span>
        </label>
        <div class="score-tab-panel">{{ js.score_reasoning | markdown }}</div>
      {% endfor %}
    </div>
  </section>
{% endif %}
```

This drops the old `macros.score_scenario(job)` call from inside the box — that badge/name pair is now redundant with the tab cards (the best-scenario's badge still shows up top via `meta_tags`, unaffected).

CSS (added to the `<style>` block in `app/templates/base.html`, next to the existing `.score-badge`/`.score-box` rules): each scenario's `input`, `label`, and reasoning `div` are three consecutive siblings, letting a single sibling-combinator rule show only the checked one's panel, while `order` reflows all panels below the (possibly wrapping) row of cards:

```css
.score-tabs { display:flex; flex-wrap:wrap; }
.score-tab-input { position:absolute; opacity:0; width:0; height:0; }
.score-tab-card { display:flex; align-items:center; gap:0.3rem; padding:0.3rem 0.6rem; margin:0 0.3rem 0.5rem 0; border:1px solid #dee2e6; border-radius:6px; cursor:pointer; background:#fff; }
.score-tab-input:checked + .score-tab-card { border-color:#0d6efd; background:#eaf2ff; }
.score-tab-panel { display:none; width:100%; order:1; }
.score-tab-input:checked + .score-tab-card + .score-tab-panel { display:block; }
```

Radio inputs give this native keyboard accessibility for free (tab/arrow-key navigation between cards).

## 5. Testing

- **Migration** (`tests/test_schema.py` or equivalent): fresh DB has `boosted` column, defaults to `0`, no `active` column; an existing pre-migration DB gets `active` dropped and `boosted` added (default `0`, existing rows unaffected).
- **`update_scenario`**: persists `boosted` true/false; `get_scenario`/`get_scenarios` return it.
- **`_BEST_SCORE_JOIN` / `get_job` / `get_jobs`**: a job scored against a boosted scenario (lower raw score) and a non-boosted scenario (higher raw score but by less than `BOOST_BONUS`) resolves to the boosted scenario as `best_scenario_id`, with `best_score`/`best_score_reasoning` reflecting that scenario's **raw** values (not raw + bonus). A non-boosted scenario whose raw score exceeds the boosted one's raw + `BOOST_BONUS` still wins. Tie-break on equal effective scores stays `scenario_id ASC`.
- **`get_job_scores`**: returns one row per scored scenario for a job, ordered by raw `relevance_score` descending, including `scenario_name` and `scenario_boosted`; empty list for a job with no scores yet.
- **Routes**: `POST /scenarios/{id}` persists a checked/unchecked `boosted` checkbox correctly (checked → `True`, field absent → `False`); `GET /jobs/{id}/expand` includes `job_scores` in the template context.
- **Templates**: `_header_edit.html` checkbox reflects current `boosted` state; `_header.html` shows the boosted tag only when true; `_feedback.html` renders one tab per `job_scores` row, the row matching `best_scenario_id` has its radio `checked`, and each tab's reasoning markdown renders in its own panel.
