# Scenario Score Boost & Comparison View Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let scenarios be flagged `boosted` (a flat +0.2 score bonus applied only when picking a job's best-match scenario, never shown as the displayed score) and add a per-job tab strip comparing every scenario's raw score and reasoning side by side.

**Architecture:** A new `scenarios.boosted` column (replacing the dead `active` column) feeds a `CASE WHEN` into the existing `ROW_NUMBER() OVER (...)` best-match window function in `app/db/queries.py`. A new `get_job_scores()` query surfaces every (job, scenario) score row so the job detail template can render one CSS-only tab per scenario instead of a single hardcoded "best" block.

**Tech Stack:** FastAPI + Jinja2 + SQLite (existing stack, no new dependencies). CSS-only radio-button tabs (no JS).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-04-scenario-score-boost-design.md` — follow it exactly for SQL, template structure, and CSS.
- `BOOST_BONUS = 0.2` is a hardcoded Python constant (not configurable) per the spec.
- The **displayed** score (`best_score`, tab card badges) is always the raw LLM score — the bonus only ever affects which scenario is selected as `rn = 1` / `best_scenario_id`.
- Migration is a hard-downtime rebuild (drop `active`, add `boosted`) — no dual-schema handling, per project convention (`CLAUDE.md`).
- Run `pytest` after every task; all prior tests must stay green.

---

### Task 1: Schema — replace `active` with `boosted`

**Files:**
- Modify: `app/db/schema.py` (DDL `scenarios` table block, add `_migrate_scenarios_boosted_flag`, register it in `init_db`)
- Test: `tests/test_schema.py`

**Interfaces:**
- Produces: `scenarios.boosted` column (`INTEGER NOT NULL DEFAULT 0`), no `active` column, on both fresh and migrated databases.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_schema.py`:

```python
def test_scenarios_table_has_boosted_column_not_active(conn):
    init_db(conn)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(scenarios)").fetchall()}
    assert "boosted" in cols
    assert "active" not in cols


def test_init_db_migrates_scenarios_replaces_active_with_boosted(conn):
    conn.executescript(
        """
        CREATE TABLE scenarios (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            active INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    conn.execute("INSERT INTO scenarios (name, description, active) VALUES ('ai_expert', 'fallback', 1)")
    conn.commit()

    init_db(conn)

    row = conn.execute("SELECT name, description, boosted FROM scenarios WHERE name = 'ai_expert'").fetchone()
    assert row["name"] == "ai_expert"
    assert row["description"] == "fallback"
    assert row["boosted"] == 0  # migration doesn't guess which scenarios should be boosted

    cols = {r[1] for r in conn.execute("PRAGMA table_info(scenarios)").fetchall()}
    assert "boosted" in cols
    assert "active" not in cols

    # Idempotent: running init_db again doesn't error or duplicate columns.
    init_db(conn)
    cols_list = [r[1] for r in conn.execute("PRAGMA table_info(scenarios)").fetchall()]
    assert cols_list.count("boosted") == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_schema.py -k boosted -v`
Expected: FAIL — `boosted` column doesn't exist yet (fresh-DB test fails on `assert "boosted" in cols`; migration test fails because `init_db` doesn't touch `active`/`boosted` at all).

- [ ] **Step 3: Implement the migration**

In `app/db/schema.py`, change the `scenarios` table in `_DDL`:

```sql
CREATE TABLE IF NOT EXISTS scenarios (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    boosted INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

Add a new migration function (near the other `_migrate_*` functions):

```python
def _migrate_scenarios_boosted_flag(conn: sqlite3.Connection) -> None:
    # Replaces the vestigial "active" column (unused since the active-scenario
    # concept was removed) with "boosted", which drives the fallback-scoring
    # bonus in app/db/queries.py.
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='scenarios'"
    ).fetchone()
    if row is None or "boosted" in row[0]:
        return
    conn.execute("ALTER TABLE scenarios DROP COLUMN active")
    conn.execute("ALTER TABLE scenarios ADD COLUMN boosted INTEGER NOT NULL DEFAULT 0")
    conn.commit()
```

Register it in `init_db`, after `conn.executescript(_DDL)` and alongside the other migration calls:

```python
def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL)
    _migrate_sources_fetcher_type(conn)
    _migrate_jobs_scores_to_table(conn)
    _migrate_jobs_add_feedback_scenario_id(conn)
    _migrate_jobs_add_headline(conn)
    _migrate_jobs_add_published_at(conn)
    _migrate_scenarios_boosted_flag(conn)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_schema.py -v`
Expected: PASS (all tests, including the two new ones and every pre-existing one — `_DDL`'s `CREATE TABLE IF NOT EXISTS` means fresh in-memory test DBs get `boosted` directly from the DDL, so the migration function is a no-op for them; the migration only fires for the hand-built pre-migration schema in the new test).

- [ ] **Step 5: Commit**

```bash
git add app/db/schema.py tests/test_schema.py
git commit -m "feat: replace vestigial scenarios.active with scenarios.boosted"
```

---

### Task 2: Query layer — boost bonus in best-match selection, `get_job_scores`

**Files:**
- Modify: `app/db/queries.py` (`_BEST_SCORE_JOIN`, `update_scenario`, add `BOOST_BONUS` constant and `get_job_scores`)
- Test: `tests/test_queries.py`

**Interfaces:**
- Consumes: `scenarios.boosted` column from Task 1.
- Produces:
  - `BOOST_BONUS: float` module constant in `app/db/queries.py`, value `0.2`.
  - `update_scenario(conn, scenario_id: int, name: str, description: str, boosted: bool = False) -> None` — extended signature (backward compatible, existing callers omitting `boosted` keep working).
  - `get_job_scores(conn: sqlite3.Connection, job_id: int) -> list[dict]` — each dict has `job_id`, `scenario_id`, `relevance_score`, `score_reasoning`, `scenario_version_hash`, `evaluated_at`, `scenario_name`, `scenario_boosted`, ordered by `relevance_score` descending.
  - `get_job`/`get_jobs`: `best_scenario_id`/`best_score`/`best_score_reasoning` now reflect the boost-adjusted winner, but `best_score`/`best_score_reasoning` values themselves stay the winner's **raw** score/reasoning (never `raw + BOOST_BONUS`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_queries.py`:

```python
def test_update_scenario_persists_boosted(conn):
    sid = q.insert_scenario(conn, "ai_expert", "fallback")
    q.update_scenario(conn, sid, name="ai_expert", description="fallback", boosted=True)
    scenario = {s["id"]: s for s in q.get_scenarios(conn)}[sid]
    assert scenario["boosted"] == 1


def test_update_scenario_boosted_defaults_false(conn):
    sid = q.insert_scenario(conn, "A", "")
    q.update_scenario(conn, sid, name="A", description="")
    scenario = {s["id"]: s for s in q.get_scenarios(conn)}[sid]
    assert scenario["boosted"] == 0


def test_boosted_scenario_wins_within_bonus_margin(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    boosted_id = q.insert_scenario(conn, "ai_expert", "")
    q.update_scenario(conn, boosted_id, name="ai_expert", description="", boosted=True)
    specific_id = q.insert_scenario(conn, "Backend Roles", "")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.upsert_job_score(conn, jid, boosted_id, 0.5, "generic fit", "h1")
    q.upsert_job_score(conn, jid, specific_id, 0.6, "decent fit", "h2")  # 0.6 < 0.5 + 0.2

    job = q.get_job(conn, jid)

    assert job["best_scenario_id"] == boosted_id
    assert job["best_score"] == pytest.approx(0.5)  # raw score, not 0.5 + bonus
    assert job["best_score_reasoning"] == "generic fit"


def test_specific_scenario_wins_when_it_clears_bonus_margin(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    boosted_id = q.insert_scenario(conn, "ai_expert", "")
    q.update_scenario(conn, boosted_id, name="ai_expert", description="", boosted=True)
    specific_id = q.insert_scenario(conn, "Backend Roles", "")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.upsert_job_score(conn, jid, boosted_id, 0.5, "generic fit", "h1")
    q.upsert_job_score(conn, jid, specific_id, 0.75, "strong fit", "h2")  # 0.75 > 0.5 + 0.2

    job = q.get_job(conn, jid)

    assert job["best_scenario_id"] == specific_id
    assert job["best_score"] == pytest.approx(0.75)


def test_get_job_scores_returns_all_scenarios_ordered_by_raw_score(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_a = q.insert_scenario(conn, "A", "")
    scenario_b = q.insert_scenario(conn, "B", "")
    q.update_scenario(conn, scenario_b, name="B", description="", boosted=True)
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.upsert_job_score(conn, jid, scenario_a, 0.7, "a reasoning", "h1")
    q.upsert_job_score(conn, jid, scenario_b, 0.4, "b reasoning", "h2")

    scores = q.get_job_scores(conn, jid)

    assert [s["scenario_id"] for s in scores] == [scenario_a, scenario_b]  # raw score order, not boosted order
    assert scores[0]["scenario_name"] == "A"
    assert scores[0]["scenario_boosted"] == 0
    assert scores[0]["score_reasoning"] == "a reasoning"
    assert scores[1]["scenario_name"] == "B"
    assert scores[1]["scenario_boosted"] == 1


def test_get_job_scores_empty_for_unscored_job(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    assert q.get_job_scores(conn, jid) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_queries.py -k "boosted or get_job_scores" -v`
Expected: FAIL — `update_scenario()` raises `TypeError: update_scenario() got an unexpected keyword argument 'boosted'`; `get_job_scores` doesn't exist (`AttributeError`).

- [ ] **Step 3: Implement**

In `app/db/queries.py`, add the constant and rewrite `_BEST_SCORE_JOIN` (near its current definition):

```python
BOOST_BONUS = 0.2  # flat bonus added to a boosted scenario's score when picking a job's best match

_BEST_SCORE_SELECT = """
    jobs.*,
    best.scenario_id AS best_scenario_id,
    best.relevance_score AS best_score,
    best.score_reasoning AS best_score_reasoning,
    scenarios.name AS best_scenario_name,
    feedback_scenarios.name AS feedback_scenario_name
"""

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

Replace `update_scenario`:

```python
def update_scenario(conn: sqlite3.Connection, scenario_id: int, name: str, description: str, boosted: bool = False) -> None:
    conn.execute(
        "UPDATE scenarios SET name = ?, description = ?, boosted = ? WHERE id = ?",
        (name, description, int(boosted), scenario_id),
    )
    conn.commit()
```

Add `get_job_scores`, near `get_job` / `get_recent_feedback_notes`:

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

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_queries.py -v`
Expected: PASS — every test in the file, including pre-existing ones like `test_update_scenario` (calls `update_scenario` without `boosted`, relies on the new default) and `test_get_job_exposes_best_scenario_id` (no boosted scenarios involved, behavior unchanged).

- [ ] **Step 5: Commit**

```bash
git add app/db/queries.py tests/test_queries.py
git commit -m "feat: apply boosted-scenario bonus in best-match selection, add get_job_scores"
```

---

### Task 3: Scenario edit UI — boosted checkbox and tag

**Files:**
- Modify: `app/routes/scenarios.py` (`update_scenario` route), `app/templates/scenarios/_header_edit.html`, `app/templates/scenarios/_header.html`
- Test: `tests/test_routes_scenarios.py`

**Interfaces:**
- Consumes: `q.update_scenario(..., boosted: bool)` and `scenario["boosted"]` from Task 2.
- Produces: no new interfaces for later tasks — this is a leaf UI task.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_routes_scenarios.py`:

```python
def test_update_scenario_route_persists_boosted_checkbox(client, conn):
    sid = q.insert_scenario(conn, "ai_expert", "fallback")
    resp = client.post(
        f"/scenarios/{sid}",
        data={"name": "ai_expert", "description": "fallback", "boosted": "on"},
    )
    assert resp.status_code == 200
    scenario = {s["id"]: s for s in q.get_scenarios(conn)}[sid]
    assert scenario["boosted"] == 1


def test_update_scenario_route_unchecking_boosted_clears_flag(client, conn):
    sid = q.insert_scenario(conn, "ai_expert", "fallback")
    client.post(f"/scenarios/{sid}", data={"name": "ai_expert", "description": "fallback", "boosted": "on"})
    resp = client.post(f"/scenarios/{sid}", data={"name": "ai_expert", "description": "fallback"})
    assert resp.status_code == 200
    scenario = {s["id"]: s for s in q.get_scenarios(conn)}[sid]
    assert scenario["boosted"] == 0


def test_edit_scenario_form_checkbox_checked_when_boosted(client, conn):
    sid = q.insert_scenario(conn, "ai_expert", "fallback")
    q.update_scenario(conn, sid, name="ai_expert", description="fallback", boosted=True)
    resp = client.get(f"/scenarios/{sid}/edit")
    assert resp.status_code == 200
    assert '<input type="checkbox" name="boosted" checked>' in resp.text


def test_edit_scenario_form_checkbox_unchecked_by_default(client, conn):
    sid = q.insert_scenario(conn, "A", "")
    resp = client.get(f"/scenarios/{sid}/edit")
    assert resp.status_code == 200
    assert '<input type="checkbox" name="boosted" checked>' not in resp.text
    assert 'name="boosted"' in resp.text


def test_scenario_header_shows_boosted_tag(client, conn):
    sid = q.insert_scenario(conn, "ai_expert", "fallback")
    q.update_scenario(conn, sid, name="ai_expert", description="fallback", boosted=True)
    resp = client.get(f"/scenarios/{sid}")
    assert resp.status_code == 200
    assert "Boosted" in resp.text


def test_scenario_header_omits_boosted_tag_when_not_boosted(client, conn):
    sid = q.insert_scenario(conn, "A", "")
    resp = client.get(f"/scenarios/{sid}")
    assert resp.status_code == 200
    assert "Boosted" not in resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_routes_scenarios.py -k boosted -v`
Expected: FAIL — the route doesn't accept/pass `boosted` yet (flag is silently dropped, so persisted value stays `0` and the checked-checkbox test fails), and the templates don't render the checkbox or tag yet.

- [ ] **Step 3: Implement**

In `app/routes/scenarios.py`, update the route (needs `Optional` imported from `typing` if not already):

```python
from typing import Optional
```

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

In `app/templates/scenarios/_header_edit.html`, add the checkbox before the Save button:

```html
<div id="scenario-header-{{ scenario.id }}">
  <form hx-post="/scenarios/{{ scenario.id }}"
        hx-target="#scenario-header-{{ scenario.id }}"
        hx-swap="outerHTML"
        style="display:flex; gap:0.5rem; flex-wrap:wrap; align-items:center;">
    <input type="text" name="name" value="{{ scenario.name }}" required>
    <input type="text" name="description" value="{{ scenario.description }}" style="flex:1; min-width:200px;">
    <label style="display:flex; align-items:center; gap:0.3rem; font-size:0.9em;"
           title="Boosted scenarios get a flat +20 point bonus when picking a job's best match">
      <input type="checkbox" name="boosted" {% if scenario.boosted %}checked{% endif %}> Boosted
    </label>
    <button type="submit" class="btn">Save</button>
    <button type="button" class="btn"
      hx-get="/scenarios/{{ scenario.id }}"
      hx-target="#scenario-header-{{ scenario.id }}"
      hx-swap="outerHTML">Cancel</button>
  </form>
</div>
```

In `app/templates/scenarios/_header.html`, add the tag next to the scenario name:

```html
<div id="scenario-header-{{ scenario.id }}">
  <div style="display:flex; align-items:center; gap:0.75rem; flex-wrap:wrap;">
    <strong>{{ scenario.name }}</strong>
    {% if scenario.boosted %}<span class="tag" title="Gets a +20 point bonus when picking a job's best match">⚡ Boosted</span>{% endif %}
    <button class="btn" style="font-size:0.85em; padding:3px 10px;"
      hx-get="/scenarios/{{ scenario.id }}/edit"
      hx-target="#scenario-header-{{ scenario.id }}"
      hx-swap="outerHTML">Edit</button>
  </div>
  {% if scenario.description %}<div style="color:#555; margin:0.4rem 0 0.75rem;">{{ scenario.description | markdown }}</div>{% endif %}
</div>
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_routes_scenarios.py -v`
Expected: PASS — all tests in the file, including pre-existing ones (`test_update_scenario` doesn't send `boosted`, gets `False`, unaffected).

- [ ] **Step 5: Commit**

```bash
git add app/routes/scenarios.py app/templates/scenarios/_header_edit.html app/templates/scenarios/_header.html tests/test_routes_scenarios.py
git commit -m "feat: add boosted checkbox and tag to scenario edit UI"
```

---

### Task 4: Job detail — scenario score comparison tabs

**Files:**
- Modify: `app/routes/jobs.py` (`job_expand`), `app/templates/jobs/_feedback.html`, `app/templates/base.html` (CSS)
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: `q.get_job_scores(conn, job_id)` from Task 2.
- Produces: no new interfaces — final leaf task.

- [ ] **Step 1: Update the now-stale test and write new failing tests**

The existing `test_job_expand_score_box_repeats_score_scenario_with_reasoning` in `tests/test_routes_jobs.py` (around line 230) asserts the old single-block markup (`macros.score_scenario(job)` repeated inside `.score-box`). Replace it with tab-aware tests. Delete `test_job_expand_score_box_repeats_score_scenario_with_reasoning` and add these in its place:

```python
def test_job_expand_shows_tab_per_scored_scenario(client, conn):
    sid, jid, scenario_a = _seed(conn)
    scenario_b = q.insert_scenario(conn, "Other Scenario", "")
    q.upsert_job_score(conn, jid, scenario_b, 0.4, "weaker fit", "hash2")

    resp = client.get(f"/jobs/{jid}/expand")

    assert resp.status_code == 200
    assert 'class="score-box' in resp.text
    assert "Remote ML" in resp.text
    assert "Other Scenario" in resp.text
    assert "Good match" in resp.text  # scenario_a's reasoning, from _seed
    assert "weaker fit" in resp.text  # scenario_b's reasoning
    assert resp.text.count("90%") == 2  # top meta row + scenario_a's tab card
    assert "40%" in resp.text  # scenario_b's tab card


def test_job_expand_best_scenario_tab_is_checked(client, conn):
    sid, jid, scenario_a = _seed(conn)
    scenario_b = q.insert_scenario(conn, "Other Scenario", "")
    q.upsert_job_score(conn, jid, scenario_b, 0.4, "weaker fit", "hash2")

    resp = client.get(f"/jobs/{jid}/expand")

    assert resp.status_code == 200
    assert f'id="score-tab-{jid}-{scenario_a}"' in resp.text
    assert f'id="score-tab-{jid}-{scenario_a}" name="score-tab-{jid}" class="score-tab-input" checked' in resp.text
    assert f'id="score-tab-{jid}-{scenario_b}" name="score-tab-{jid}" class="score-tab-input" checked' not in resp.text


def test_job_expand_shows_boosted_indicator_for_boosted_scenario(client, conn):
    sid, jid, scenario_a = _seed(conn)
    q.update_scenario(conn, scenario_a, name="Remote ML", description="", boosted=True)

    resp = client.get(f"/jobs/{jid}/expand")

    assert resp.status_code == 200
    assert "Boosted scenario" in resp.text  # title attribute on the indicator icon


def test_job_expand_no_score_box_when_unscored(client, conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "http")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/2", title="No Score", company="Acme", raw_text="r")
    q.insert_scenario(conn, "First", "")
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert 'class="score-box' not in resp.text
```

(`test_job_expand_no_score_box_when_unscored` already exists further down the file with this exact body — remove the duplicate definition so there's only one copy.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_routes_jobs.py -k "tab or boosted_indicator" -v`
Expected: FAIL — `job_expand` doesn't pass `job_scores` to the template yet, and `_feedback.html` still renders the old single-block markup, so none of the tab IDs/badges/reasoning-for-both-scenarios show up.

- [ ] **Step 3: Implement**

In `app/routes/jobs.py`, update `job_expand`:

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

In `app/templates/jobs/_feedback.html`, replace:

```html
  {% if job.best_score_reasoning %}
    <section class="score-box" aria-label="Score reasoning">
      {{ macros.score_scenario(job) }}
      {{ job.best_score_reasoning | markdown }}
    </section>
  {% endif %}
```

with:

```html
  {% if job_scores %}
    <section class="score-box score-compare" aria-label="Scenario score comparison">
      <div class="score-tabs">
        {% for js in job_scores %}
          <input type="radio" id="score-tab-{{ job.id }}-{{ js.scenario_id }}" name="score-tab-{{ job.id }}" class="score-tab-input" {% if js.scenario_id == job.best_scenario_id %}checked{% endif %}>
          <label for="score-tab-{{ job.id }}-{{ js.scenario_id }}" class="score-tab-card">
            {% if js.scenario_boosted %}<span title="Boosted scenario">⚡</span>{% endif %}
            <span class="tag">{{ js.scenario_name }}</span>
            {% if js.relevance_score >= 0.7 %}<span class="score-badge score-high">{{ "%.0f"|format(js.relevance_score * 100) }}%</span>
            {% elif js.relevance_score >= 0.4 %}<span class="score-badge score-mid">{{ "%.0f"|format(js.relevance_score * 100) }}%</span>
            {% else %}<span class="score-badge score-low">{{ "%.0f"|format(js.relevance_score * 100) }}%</span>
            {% endif %}
          </label>
          <div class="score-tab-panel">{{ js.score_reasoning | markdown }}</div>
        {% endfor %}
      </div>
    </section>
  {% endif %}
```

The `<input>` tag must stay on a single line exactly as above — Jinja doesn't collapse literal HTML whitespace, so wrapping it across two lines would insert a newline+indentation between `name="..."` and `class="..."` in the rendered output, which would break the exact-substring `checked` assertions in Step 1's tests.

In `app/templates/base.html`, add to the existing `<style>` block, near the `.score-box`/`.score-badge` rules:

```css
.score-tabs { display:flex; flex-wrap:wrap; }
.score-tab-input { position:absolute; opacity:0; width:0; height:0; }
.score-tab-card { display:flex; align-items:center; gap:0.3rem; padding:0.3rem 0.6rem; margin:0 0.3rem 0.5rem 0; border:1px solid #dee2e6; border-radius:6px; cursor:pointer; background:#fff; }
.score-tab-input:checked + .score-tab-card { border-color:#0d6efd; background:#eaf2ff; }
.score-tab-panel { display:none; width:100%; order:1; }
.score-tab-input:checked + .score-tab-card + .score-tab-panel { display:block; }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_routes_jobs.py -v`
Expected: PASS — every test in the file. In particular, check `test_job_expand_feedback_form_defaults_to_best_scenario` and `test_job_expand_scenario_label_indicates_best_fit` (feedback-form dropdown, untouched by this task) still pass, confirming the tab-strip change didn't disturb the rest of `_feedback.html`.

- [ ] **Step 5: Commit**

```bash
git add app/routes/jobs.py app/templates/jobs/_feedback.html app/templates/base.html tests/test_routes_jobs.py
git commit -m "feat: replace single best-score block with per-scenario comparison tabs"
```

---

### Task 5: Full-suite verification

**Files:** none (verification only)

- [ ] **Step 1: Run the entire test suite**

Run: `pytest -q`
Expected: All tests pass, zero failures/errors. This confirms Tasks 1-4 didn't regress any other area (pipeline, fetchers, other routes).

- [ ] **Step 2: Manual smoke test**

Start the app (`config.toml` must already exist in this worktree — copied from the main checkout):

```bash
.venv/bin/uvicorn app.main:app --port 8734 &
disown
sleep 1
curl -s http://127.0.0.1:8734/scenarios | grep -o "Boosted" || echo "no boosted scenarios yet (expected on a fresh DB)"
```

Then, using the running server:
1. `curl -s http://127.0.0.1:8734/scenarios` — confirm the page loads (200) and includes a "Boosted" checkbox in each scenario's edit form region once expanded (or just trust the automated tests here; this step is about catching template-rendering errors that only show up against a real request, not about re-deriving assertions already covered by tests).
2. Pick any existing scenario, mark it boosted via `curl -s -X POST http://127.0.0.1:8734/scenarios/<id> -d "name=<name>&description=<description>&boosted=on"`, then `curl -s http://127.0.0.1:8734/scenarios/<id>` and confirm `⚡ Boosted` appears in the response.
3. If any job has scores from 2+ scenarios (check with `sqlite3 job-seek.db "SELECT job_id, COUNT(*) FROM job_scores GROUP BY job_id HAVING COUNT(*) > 1 LIMIT 1;"`), hit `curl -s http://127.0.0.1:8734/jobs/<that_job_id>/expand` and confirm the response contains one `score-tab-card` per scenario and all their reasoning texts.
4. `pkill -f "uvicorn app.main:app --port 8734"` to stop the server.

If any step reveals a rendering error (e.g. a Jinja `UndefinedError` visible in a 500 response), fix it and re-run the relevant automated test file before re-attempting the manual check.

- [ ] **Step 3: Report completion**

No commit for this task (verification only). If all steps pass, the plan is complete.
