# Feedback-Scenario Link Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user explicitly tag which scenario a job's feedback note is about, instead of implicitly attributing it to every scenario the job happens to have been scored against.

**Architecture:** Add a nullable `feedback_scenario_id` column to `jobs`, backfilled from each job's best-matching scenario for a smooth upgrade. The feedback form gets a scenario `<select>` (all scenarios, defaulting to the job's best match), and `get_recent_feedback_notes` switches from joining through `job_scores` to filtering directly on the new column.

**Tech Stack:** Python, FastAPI, SQLite (raw SQL, no ORM), Jinja2 templates, htmx, pytest.

## Global Constraints

- `job.status` stays a single global value per job — untouched by this work (per spec).
- Feedback stays one note per job (no per-scenario accumulation) — resubmitting overwrites note, status, and scenario tag together.
- The scenario `<select>` lists **all** scenarios (active or not).
- Default selection: job's best-matching scenario if scored; otherwise no option is force-selected (browser defaults to the first `<option>` in DOM order).
- Spec: `docs/superpowers/specs/2026-08-02-feedback-scenario-link-design.md`

---

### Task 1: Schema — `feedback_scenario_id` column + migration + backfill

**Files:**
- Modify: `app/db/schema.py:35-48` (jobs table DDL), `app/db/schema.py:145-149` (`init_db`)
- Test: `tests/test_schema.py`

**Interfaces:**
- Produces: `jobs.feedback_scenario_id` column (nullable `INTEGER REFERENCES scenarios(id)`), present on every DB `init_db` touches (fresh or migrated).

- [ ] **Step 1: Write the failing migration test**

Add to `tests/test_schema.py`:

```python
def test_jobs_table_has_feedback_scenario_id_column(conn):
    init_db(conn)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    assert "feedback_scenario_id" in cols


def test_init_db_migrates_jobs_adds_feedback_scenario_id_with_backfill(conn):
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(
        """
        CREATE TABLE sources (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            fetcher_type TEXT NOT NULL CHECK(fetcher_type IN ('http', 'playwright', 'slack', 'finn_listing')),
            enabled INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE scenarios (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            active INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE jobs (
            id INTEGER PRIMARY KEY,
            source_id INTEGER NOT NULL REFERENCES sources(id),
            url TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL DEFAULT '',
            company TEXT NOT NULL DEFAULT '',
            raw_text TEXT NOT NULL DEFAULT '',
            simplified_content TEXT NOT NULL DEFAULT '',
            summary TEXT NOT NULL DEFAULT '',
            content_type TEXT CHECK(content_type IN ('job_posting', 'lead', 'irrelevant', 'error')),
            fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
            status TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('new', 'accepted', 'rejected', 'invalid')),
            feedback_note TEXT
        );
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
        """
    )
    conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s', 'http://x', 'http')")
    conn.execute("INSERT INTO scenarios (name) VALUES ('Scenario A')")  # id 1
    conn.execute("INSERT INTO scenarios (name) VALUES ('Scenario B')")  # id 2
    conn.execute(
        "INSERT INTO jobs (source_id, url, status, feedback_note) "
        "VALUES (1, 'http://job/1', 'accepted', 'good fit')"
    )  # id 1, has feedback, scored against both scenarios
    conn.execute("INSERT INTO jobs (source_id, url) VALUES (1, 'http://job/2')")  # id 2, no feedback
    conn.execute(
        "INSERT INTO job_scores (job_id, scenario_id, relevance_score, scenario_version_hash) "
        "VALUES (1, 1, 0.4, 'h1')"
    )
    conn.execute(
        "INSERT INTO job_scores (job_id, scenario_id, relevance_score, scenario_version_hash) "
        "VALUES (1, 2, 0.9, 'h2')"
    )
    conn.commit()

    init_db(conn)

    rows = {r["id"]: r["feedback_scenario_id"] for r in conn.execute("SELECT id, feedback_scenario_id FROM jobs")}
    assert rows[1] == 2  # backfilled to the higher-scoring scenario
    assert rows[2] is None  # no feedback, stays untagged

    # Idempotent: running init_db again doesn't change the backfilled value.
    init_db(conn)
    assert conn.execute("SELECT feedback_scenario_id FROM jobs WHERE id = 1").fetchone()[0] == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_schema.py -k feedback_scenario_id -v`
Expected: FAIL — `feedback_scenario_id` column doesn't exist yet.

- [ ] **Step 3: Add the column to the DDL and write the migration**

In `app/db/schema.py`, update the `jobs` table in `_DDL` (around line 35-48) to add the new column:

```python
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES sources(id),
    url TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL DEFAULT '',
    company TEXT NOT NULL DEFAULT '',
    raw_text TEXT NOT NULL DEFAULT '',
    simplified_content TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    content_type TEXT CHECK(content_type IN ('job_posting', 'lead', 'irrelevant', 'error')),
    fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
    status TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('new', 'accepted', 'rejected', 'invalid')),
    feedback_note TEXT,
    feedback_scenario_id INTEGER REFERENCES scenarios(id)
);
```

Add a new migration function right after `_migrate_jobs_scores_to_table` (which ends around line 142):

```python
def _migrate_jobs_add_feedback_scenario_id(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='jobs'"
    ).fetchone()
    if row is None or "feedback_scenario_id" in row[0]:
        return
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript(
        """
        CREATE TABLE jobs_new (
            id INTEGER PRIMARY KEY,
            source_id INTEGER NOT NULL REFERENCES sources(id),
            url TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL DEFAULT '',
            company TEXT NOT NULL DEFAULT '',
            raw_text TEXT NOT NULL DEFAULT '',
            simplified_content TEXT NOT NULL DEFAULT '',
            summary TEXT NOT NULL DEFAULT '',
            content_type TEXT CHECK(content_type IN ('job_posting', 'lead', 'irrelevant', 'error')),
            fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
            status TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('new', 'accepted', 'rejected', 'invalid')),
            feedback_note TEXT,
            feedback_scenario_id INTEGER REFERENCES scenarios(id)
        );
        INSERT INTO jobs_new (id, source_id, url, title, company, raw_text, simplified_content, summary, content_type, fetched_at, status, feedback_note)
        SELECT id, source_id, url, title, company, raw_text, simplified_content, summary, content_type, fetched_at, status, feedback_note
        FROM jobs;
        UPDATE jobs_new
        SET feedback_scenario_id = (
            SELECT scenario_id FROM job_scores
            WHERE job_scores.job_id = jobs_new.id
            ORDER BY relevance_score DESC, scenario_id ASC
            LIMIT 1
        )
        WHERE feedback_note IS NOT NULL AND feedback_note != '';
        DROP TABLE jobs;
        ALTER TABLE jobs_new RENAME TO jobs;
        """
    )
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
```

Wire it into `init_db` (around line 145-149):

```python
def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL)
    _migrate_sources_fetcher_type(conn)
    _migrate_jobs_scores_to_table(conn)
    _migrate_jobs_add_feedback_scenario_id(conn)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_schema.py -v`
Expected: PASS (all tests, including the two new ones and the existing `test_init_db_creates_all_tables` / idempotency tests).

- [ ] **Step 5: Commit**

```bash
git add app/db/schema.py tests/test_schema.py
git commit -m "feat: add feedback_scenario_id column with backfill migration"
```

---

### Task 2: Query layer — scenario-scoped feedback notes

**Files:**
- Modify: `app/db/queries.py:203-207` (`update_job_feedback`), `app/db/queries.py:210-225` (`_BEST_SCORE_SELECT`/`_BEST_SCORE_JOIN`), `app/db/queries.py:263-273` (`get_recent_feedback_notes`)
- Test: `tests/test_queries.py`

**Interfaces:**
- Consumes: `jobs.feedback_scenario_id` column from Task 1.
- Produces: `update_job_feedback(conn, job_id, status, note, feedback_scenario_id=None)`; `get_job`/`get_jobs` rows now include `best_scenario_id`; `get_recent_feedback_notes(conn, scenario_id, limit=20)` scoped by explicit tag, not `job_scores` membership.

- [ ] **Step 1: Write the failing tests**

In `tests/test_queries.py`, replace `test_get_recent_feedback_notes_scoped_to_scenario` (lines 160-169) with a version that proves the `job_scores` heuristic is gone — the job is scored *higher* against scenario B but its feedback is explicitly tagged to scenario A, so it must not show up under B:

```python
def test_get_recent_feedback_notes_scoped_to_scenario(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_a = q.insert_scenario(conn, "A", "")
    scenario_b = q.insert_scenario(conn, "B", "")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_pipeline(conn, j1, simplified_content="", content_type="job_posting")
    q.upsert_job_score(conn, j1, scenario_a, 0.5, "reasoning", "hash1")
    q.upsert_job_score(conn, j1, scenario_b, 0.9, "reasoning", "hash2")  # scores higher for B...
    q.update_job_feedback(conn, j1, "rejected", "too junior", feedback_scenario_id=scenario_a)  # ...but tagged to A
    assert q.get_recent_feedback_notes(conn, scenario_a) == ["too junior"]
    assert q.get_recent_feedback_notes(conn, scenario_b) == []
```

Update `test_get_recent_feedback_notes` (lines 149-157) to pass the explicit tag, since the join-based fallback is gone:

```python
def test_get_recent_feedback_notes(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_pipeline(conn, j1, simplified_content="", content_type="job_posting")
    q.upsert_job_score(conn, j1, scenario_id, 0.5, "reasoning", "hash1")
    q.update_job_feedback(conn, j1, "rejected", "too junior", feedback_scenario_id=scenario_id)
    notes = q.get_recent_feedback_notes(conn, scenario_id)
    assert "too junior" in notes
```

Add two new tests, near `test_update_job_feedback` (line 118) and `test_insert_and_get_job` (line 95):

```python
def test_update_job_feedback_persists_scenario_id(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_feedback(conn, jid, "accepted", "good fit", feedback_scenario_id=scenario_id)
    job = q.get_job(conn, jid)
    assert job["feedback_scenario_id"] == scenario_id


def test_get_job_exposes_best_scenario_id(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_a = q.insert_scenario(conn, "A", "")
    scenario_b = q.insert_scenario(conn, "B", "")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.upsert_job_score(conn, jid, scenario_a, 0.4, "ok", "h1")
    q.upsert_job_score(conn, jid, scenario_b, 0.8, "great", "h2")
    job = q.get_job(conn, jid)
    assert job["best_scenario_id"] == scenario_b
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_queries.py -k "feedback or best_scenario_id" -v`
Expected: FAIL — `update_job_feedback()` doesn't accept `feedback_scenario_id`; `best_scenario_id` key missing from `get_job` result; scoping test fails because notes still leak via the `job_scores` join.

- [ ] **Step 3: Implement the query changes**

In `app/db/queries.py`, update `update_job_feedback` (lines 203-207):

```python
def update_job_feedback(
    conn: sqlite3.Connection,
    job_id: int,
    status: str,
    note: str,
    feedback_scenario_id: int | None = None,
) -> None:
    conn.execute(
        "UPDATE jobs SET status = ?, feedback_note = ?, feedback_scenario_id = ? WHERE id = ?",
        (status, note, feedback_scenario_id, job_id),
    )
    conn.commit()
```

Update `_BEST_SCORE_SELECT` (lines 210-215) to expose the winning scenario's id:

```python
_BEST_SCORE_SELECT = """
    jobs.*,
    best.scenario_id AS best_scenario_id,
    best.relevance_score AS best_score,
    best.score_reasoning AS best_score_reasoning,
    scenarios.name AS best_scenario_name
"""
```

(`_BEST_SCORE_JOIN` is unchanged — `best.scenario_id` is already selected by the inner window-function subquery, it just wasn't exposed in the outer `SELECT` before.)

Replace `get_recent_feedback_notes` (lines 263-273) to filter on the explicit tag instead of joining through `job_scores`:

```python
def get_recent_feedback_notes(
    conn: sqlite3.Connection, scenario_id: int, limit: int = 20
) -> list[str]:
    rows = conn.execute(
        """SELECT feedback_note FROM jobs
        WHERE feedback_scenario_id = ?
        AND feedback_note IS NOT NULL AND feedback_note != ''
        ORDER BY fetched_at DESC LIMIT ?""",
        (scenario_id, limit),
    ).fetchall()
    return [r["feedback_note"] for r in rows]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_queries.py -v`
Expected: PASS (all tests in the file, including `test_update_job_feedback`, `test_get_jobs_filter_by_status`, `test_get_job_counts`, which don't pass `feedback_scenario_id` and rely on its default of `None`).

- [ ] **Step 5: Commit**

```bash
git add app/db/queries.py tests/test_queries.py
git commit -m "feat: scope feedback notes to an explicit scenario tag"
```

---

### Task 3: Feedback route + template — scenario selection UI

**Files:**
- Modify: `app/routes/jobs.py:35-52` (`job_expand`, `job_feedback`), `app/templates/jobs/_feedback.html`
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: `q.get_scenarios(conn)` (existing), `q.update_job_feedback(conn, job_id, status, note, feedback_scenario_id)` and `job["best_scenario_id"]` from Task 2.
- Produces: `GET /jobs/{job_id}/expand` renders a scenario `<select name="feedback_scenario_id">`; `POST /jobs/{job_id}/feedback` requires `feedback_scenario_id` and persists it.

- [ ] **Step 1: Write the failing tests**

In `tests/test_routes_jobs.py`, update `_seed` to also return the scenario id (needed by the new tests), and update every existing call site that unpacks two values:

```python
def _seed(conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "http")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/1", title="ML Eng", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="job_posting", summary="Great role")
    scenario_id = q.insert_scenario(conn, "Remote ML", "")
    q.upsert_job_score(conn, jid, scenario_id, 0.9, "Good match", "hash1")
    return sid, jid, scenario_id


def test_job_list_returns_200(client, conn):
    _seed(conn)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "ML Eng" in resp.text


def test_job_list_empty(client, conn):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "No jobs found" in resp.text


def test_job_list_filter_accepted(client, conn):
    sid, jid, scenario_id = _seed(conn)
    q.update_job_feedback(conn, jid, "accepted", "great")
    resp = client.get("/?status=accepted")
    assert resp.status_code == 200
    assert "ML Eng" in resp.text
    resp2 = client.get("/")
    assert "ML Eng" not in resp2.text


def test_job_list_filter_bar_shows_counts(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "New (1)" in resp.text
    assert "Accepted (0)" in resp.text
    assert "Rejected (0)" in resp.text
    assert "Invalid (0)" in resp.text
    assert "Leads (0)" in resp.text


def test_job_expand(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert "Accept" in resp.text
    assert "Reject" in resp.text


def test_job_feedback_updates_status(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.post(
        f"/jobs/{jid}/feedback",
        data={"status": "accepted", "note": "good fit", "feedback_scenario_id": scenario_id},
    )
    assert resp.status_code == 200
    job = q.get_job(conn, jid)
    assert job["status"] == "accepted"
    assert job["feedback_note"] == "good fit"
    assert job["feedback_scenario_id"] == scenario_id


def test_job_feedback_can_target_non_default_scenario(client, conn):
    sid, jid, best_scenario_id = _seed(conn)
    other_scenario_id = q.insert_scenario(conn, "Other Scenario", "")
    resp = client.post(
        f"/jobs/{jid}/feedback",
        data={"status": "rejected", "note": "not a fit here", "feedback_scenario_id": other_scenario_id},
    )
    assert resp.status_code == 200
    job = q.get_job(conn, jid)
    assert job["feedback_scenario_id"] == other_scenario_id
    assert job["feedback_scenario_id"] != best_scenario_id


def test_job_list_shows_scenario_tag_for_best_score(client, conn):
    _seed(conn)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Remote ML" in resp.text


def test_job_expand_shows_scenario_tag_with_reasoning(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert "Good match" in resp.text
    assert "Remote ML" in resp.text


def test_job_expand_feedback_form_defaults_to_best_scenario(client, conn):
    sid, jid, best_scenario_id = _seed(conn)
    q.insert_scenario(conn, "Other Scenario", "")
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert "Other Scenario" in resp.text  # every scenario is listed
    assert f'<option value="{best_scenario_id}" selected>' in resp.text


def test_job_expand_feedback_form_has_no_forced_selection_when_unscored(client, conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "http")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/2", title="No Score", company="Acme", raw_text="r")
    q.insert_scenario(conn, "First", "")
    q.insert_scenario(conn, "Second", "")
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert "selected" not in resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_routes_jobs.py -v`
Expected: FAIL — `_seed` now returns 3 values (existing unpack sites already updated above, so this alone won't fail); the new scenario-selection tests fail because the form has no `<select>` yet and `POST /jobs/{id}/feedback` doesn't require/store `feedback_scenario_id`.

- [ ] **Step 3: Implement the route and template changes**

In `app/routes/jobs.py`, update `job_expand` (lines 35-40):

```python
@router.get("/jobs/{job_id}/expand", response_class=HTMLResponse)
def job_expand(job_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    job = q.get_job(conn, job_id)
    sources = {s["id"]: s for s in q.get_sources(conn)}
    job["source_name"] = sources.get(job["source_id"], {}).get("name", "")
    scenarios = q.get_scenarios(conn)
    return templates.TemplateResponse(request, "jobs/_feedback.html", {"job": job, "scenarios": scenarios})
```

Update `job_feedback` (lines 43-52):

```python
@router.post("/jobs/{job_id}/feedback", response_class=HTMLResponse)
def job_feedback(
    job_id: int,
    request: Request,
    status: str = Form(...),
    note: str = Form(...),
    feedback_scenario_id: int = Form(...),
    conn: sqlite3.Connection = Depends(get_db),
):
    q.update_job_feedback(conn, job_id, status, note, feedback_scenario_id)
    return HTMLResponse(content="", status_code=200)
```

In `app/templates/jobs/_feedback.html`, add the scenario select inside the form, before the status buttons:

```html
<div class="job-row" id="job-{{ job.id }}">
  <div style="display:flex; gap:0.5rem; flex-wrap:wrap; align-items:center;">
    <strong>{{ job.title or "(no title)" }}</strong>
    {% if job.company %}<span>· {{ job.company }}</span>{% endif %}
    <a href="{{ job.url }}" target="_blank" rel="noopener">↗ original</a>
  </div>
  {% if job.summary %}
    <div style="margin-top:0.5rem;">{{ job.summary | markdown }}</div>
  {% endif %}
  {% if job.best_score_reasoning %}
    <div style="margin-top:0.4rem; font-size:0.9em; color:#666;">
      <span class="tag">{{ job.best_scenario_name }}</span>
      {{ job.best_score_reasoning | markdown }}
    </div>
  {% endif %}
  <form class="feedback-form" style="margin-top:0.75rem;"
    hx-post="/jobs/{{ job.id }}/feedback"
    hx-target="#job-{{ job.id }}"
    hx-swap="outerHTML">
    <label>Scenario:
      <select name="feedback_scenario_id" required>
        {% for s in scenarios %}
          <option value="{{ s.id }}" {% if s.id == job.best_scenario_id %}selected{% endif %}>{{ s.name }}</option>
        {% endfor %}
      </select>
    </label>
    <div style="display:flex; gap:0.5rem; flex-wrap:wrap;">
      <button type="submit" name="status" value="accepted" class="btn btn-accept">Accept</button>
      <button type="submit" name="status" value="rejected" class="btn btn-reject">Reject</button>
      <button type="submit" name="status" value="invalid" class="btn btn-invalid">Invalid</button>
    </div>
    <label>Note (required):
      <textarea name="note" required placeholder="Why accepting/rejecting? What should change?"></textarea>
    </label>
  </form>
</div>
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_routes_jobs.py -v`
Expected: PASS (all tests in the file).

- [ ] **Step 5: Run the full test suite**

Run: `pytest -v`
Expected: PASS (no regressions in `test_routes_scenarios.py` or elsewhere — `refine_criteria` still calls `q.get_recent_feedback_notes(conn, scenario_id)` with the same signature).

- [ ] **Step 6: Commit**

```bash
git add app/routes/jobs.py app/templates/jobs/_feedback.html tests/test_routes_jobs.py
git commit -m "feat: let users pick which scenario a feedback note targets"
```
