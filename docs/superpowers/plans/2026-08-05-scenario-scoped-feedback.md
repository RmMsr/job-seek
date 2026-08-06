# Scenario-Scoped Feedback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split job feedback into per-`(job, scenario)` gate feedback (an optional, explicit higher/lower direction + note, feeding `propose_criteria`) and a scenario-agnostic personal note on `jobs`, removing `feedback_scenario_id` and the accept/reject-coupled interpretation of feedback notes that no longer maps onto the two-stage scoring split.

**Architecture:** New `scenario_feedback` table (one row per `(job, scenario)`, nullable `direction`) replaces `jobs.feedback_scenario_id`. `propose_criteria` reads directed rows only, tagged `[SHOULD SCORE HIGHER]`/`[SHOULD SCORE LOWER]` instead of `[ACCEPTED]`/`[REJECTED]`. The score-comparison tab strip (already showing one panel per scored scenario) gains an inline optional feedback form per panel; the main feedback form drops its scenario picker entirely; bulk feedback drops its scenario dropdown entirely.

**Tech Stack:** Python, FastAPI, SQLite (stdlib `sqlite3`), Jinja2 templates, HTMX, pytest.

## Global Constraints

- Hard-downtime migrations only — direct `ALTER TABLE ... DROP COLUMN`, no dual-schema compatibility shims (per project CLAUDE.md).
- Commit after each task goes green (per project CLAUDE.md) — one commit per task, not a batch at the end.
- No placeholders, no `TODO`s — every step below has complete, runnable code.
- This is a personal single-user app; migrations run synchronously in `init_db()`, matching every existing migration in `app/db/schema.py`.

---

### Task 1: Schema — `scenario_feedback` table, drop `jobs.feedback_scenario_id`

**Files:**
- Modify: `app/db/schema.py`
- Test: `tests/test_schema.py`

**Interfaces:**
- Produces: `scenario_feedback(id, job_id, scenario_id, note, direction, created_at, handled_at)`, `UNIQUE(job_id, scenario_id)`. `jobs` loses `feedback_scenario_id`. Consumed by every later task.

- [ ] **Step 1: Write the failing tests**

In `tests/test_schema.py`, delete `test_jobs_table_has_feedback_scenario_id_column` and `test_init_db_migrates_jobs_adds_feedback_scenario_id_with_backfill` (both test a migration this task removes — see Step 3). Add:

```python
def test_scenario_feedback_table_created(conn):
    init_db(conn)
    assert "scenario_feedback" in _tables(conn)


def test_scenario_feedback_unique_per_job_and_scenario(conn):
    init_db(conn)
    conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s', 'http://x', 'http')")
    conn.execute("INSERT INTO jobs (source_id, url) VALUES (1, 'http://job/1')")
    conn.execute("INSERT INTO scenarios (name) VALUES ('A')")
    conn.execute("INSERT INTO scenario_feedback (job_id, scenario_id, note) VALUES (1, 1, 'note')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO scenario_feedback (job_id, scenario_id, note) VALUES (1, 1, 'other')")


def test_scenario_feedback_direction_constrained(conn):
    init_db(conn)
    conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s', 'http://x', 'http')")
    conn.execute("INSERT INTO jobs (source_id, url) VALUES (1, 'http://job/1')")
    conn.execute("INSERT INTO scenarios (name) VALUES ('A')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO scenario_feedback (job_id, scenario_id, note, direction) VALUES (1, 1, '', 'sideways')"
        )


def test_jobs_table_has_no_feedback_scenario_id_column(conn):
    init_db(conn)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    assert "feedback_scenario_id" not in cols


def test_init_db_migrates_jobs_drops_feedback_scenario_id_with_backfill(conn):
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
            gate_threshold REAL NOT NULL DEFAULT 0.7,
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
            headline TEXT NOT NULL DEFAULT '',
            published_at TEXT,
            content_type TEXT CHECK(content_type IN ('job_posting', 'lead', 'irrelevant', 'error')),
            fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
            status TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('new', 'accepted', 'rejected', 'invalid')),
            feedback_note TEXT,
            feedback_scenario_id INTEGER REFERENCES scenarios(id),
            feedback_handled_at TEXT,
            interest_score REAL,
            interest_reasoning TEXT,
            attainability_score REAL,
            attainability_reasoning TEXT,
            fit_score REAL,
            profile_version_hash TEXT
        );
        """
    )
    conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s', 'http://x', 'http')")
    conn.execute("INSERT INTO scenarios (name) VALUES ('Remote ML')")  # id 1
    conn.execute(
        "INSERT INTO jobs (source_id, url, status, feedback_note, feedback_scenario_id) "
        "VALUES (1, 'http://job/1', 'accepted', 'good fit', 1)"
    )  # id 1: has feedback, should backfill
    conn.execute("INSERT INTO jobs (source_id, url) VALUES (1, 'http://job/2')")  # id 2: no feedback, nothing to backfill
    conn.commit()

    init_db(conn)

    cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    assert "feedback_scenario_id" not in cols

    rows = conn.execute("SELECT job_id, scenario_id, note, direction FROM scenario_feedback").fetchall()
    assert [dict(r) for r in rows] == [{"job_id": 1, "scenario_id": 1, "note": "good fit", "direction": None}]

    # Idempotent: running init_db again doesn't duplicate or error.
    init_db(conn)
    assert conn.execute("SELECT COUNT(*) FROM scenario_feedback").fetchone()[0] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_schema.py -k "scenario_feedback or feedback_scenario_id" -v`
Expected: FAIL — `scenario_feedback` table doesn't exist yet, `jobs` still has `feedback_scenario_id`.

- [ ] **Step 3: Implement**

In `app/db/schema.py`, add to `_DDL` (after the `job_scores` table):

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

Remove `feedback_scenario_id INTEGER REFERENCES scenarios(id),` from the `jobs` table in `_DDL`.

Delete `_migrate_jobs_add_feedback_scenario_id` entirely — it exists only to carry an old DB from "never had the column" to "has it," and every DB currently in use already has it (from prior deploys), so keeping this migration around would fight the new drop-migration on a fresh install (a freshly `_DDL`-created `jobs` table has no `feedback_scenario_id` in its CREATE SQL, so the old migration's `"feedback_scenario_id" in row[0]` guard would read `False` and re-add the column it was just supposed to skip). Matches the project's "no defensive handling for hypothetical shapes that don't exist in practice" migration philosophy — the only real-world shapes are "already has the column" (handled below) and "fresh install" (never had it, nothing to do).

Add the replacement migration:

```python
def _migrate_jobs_drop_feedback_scenario_id(conn: sqlite3.Connection) -> None:
    # feedback_scenario_id is superseded by scenario_feedback — status is
    # now fully scenario-agnostic. Direct DROP COLUMN, same pattern already
    # used for scenarios.boosted.
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='jobs'"
    ).fetchone()
    if row is None or "feedback_scenario_id" not in row[0]:
        return
    # Best-effort backfill: an existing (feedback_note, feedback_scenario_id)
    # pair looks exactly like what a gate-feedback note is today, so carry
    # it into scenario_feedback before the column disappears.
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

Update `init_db`, removing the deleted migration's call and adding the new one (after `_migrate_jobs_add_fit_scorecard`, before `_migrate_scenarios_gate_threshold`):

```python
def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL)
    _migrate_sources_fetcher_type(conn)
    _migrate_jobs_scores_to_table(conn)
    _migrate_jobs_add_headline(conn)
    _migrate_jobs_add_published_at(conn)
    _migrate_jobs_add_feedback_handled_at(conn)
    _migrate_jobs_add_fit_scorecard(conn)
    _migrate_jobs_drop_feedback_scenario_id(conn)
    _migrate_scenarios_gate_threshold(conn)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_schema.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add app/db/schema.py tests/test_schema.py
git commit -m "feat: add scenario_feedback table, drop jobs.feedback_scenario_id"
```

---

### Task 2: `update_job_feedback`/`reset_job`/gate-join scenario-agnostic

**Files:**
- Modify: `app/db/queries.py`
- Test: `tests/test_queries.py`

**Interfaces:**
- Consumes: schema from Task 1.
- Produces: `update_job_feedback(conn, job_id, status, note)` (drops `feedback_scenario_id`). `reset_job` also clears `scenario_feedback`. `_GATE_JOIN`/`_GATE_SELECT` no longer reference `feedback_scenario_id`/`feedback_scenario_name`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_queries.py`, delete `test_update_job_feedback_persists_scenario_id` (tests a parameter this task removes). Update `test_reset_job_clears_pipeline_output_and_scores`:

```python
def test_reset_job_clears_pipeline_output_and_scores(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_pipeline(
        conn, jid, simplified_content="clean", content_type="job_posting",
        title="AI Title", headline="hook", summary="summary text",
    )
    q.upsert_job_score(conn, jid, scenario_id, 0.8, "great", "h1")
    q.update_job_fit(conn, jid, 0.7, "good interest", 0.6, "some gaps", "phash1")
    q.upsert_scenario_feedback(conn, jid, scenario_id, "shouldn't count", "lower")
    q.update_job_feedback(conn, jid, "accepted", "note")

    q.reset_job(conn, jid)

    job = q.get_job(conn, jid)
    assert job["status"] == "new"
    assert job["content_type"] is None
    assert job["simplified_content"] == ""
    assert job["summary"] == ""
    assert job["headline"] == ""
    assert job["feedback_note"] is None
    assert job["raw_text"] == "r"
    assert job["interest_score"] is None
    assert job["fit_score"] is None
    assert job["profile_version_hash"] is None
    assert q.get_job_scores(conn, jid) == []
    assert q.get_recent_feedback_job_ids(conn, scenario_id) == []
```

(This references `q.upsert_scenario_feedback`, added in Task 3 — run this test's full pass scoped later, at the end of Task 3, same deferred-verification approach as the prior plan.)

Add a test proving the gate join survives the column drop:

```python
def test_get_jobs_works_without_feedback_scenario_id_column(conn):
    # Regression guard: _GATE_JOIN used to join on jobs.feedback_scenario_id,
    # which Task 1 dropped — this must not raise sqlite3.OperationalError.
    source_id = q.insert_source(conn, "s", "http://x", "http")
    q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    assert q.get_jobs(conn) is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_queries.py -k "works_without_feedback_scenario_id" -v`
Expected: FAIL with `sqlite3.OperationalError: no such column: jobs.feedback_scenario_id`

- [ ] **Step 3: Implement**

In `app/db/queries.py`, remove the feedback-scenario join/column from `_GATE_SELECT`/`_GATE_JOIN`:

```python
_GATE_SELECT = """
    jobs.*,
    gate.passed_count AS passed_gate_count,
    gate.passed_scenario_names AS passed_scenario_names,
    gate.top_passed_scenario_id AS top_passed_scenario_id,
    gate.top_passed_scenario_name AS top_passed_scenario_name,
    scored.scored_count AS scored_gate_count
"""

_GATE_JOIN = """
    FROM jobs
    LEFT JOIN (
        SELECT ranked.job_id,
               COUNT(*) AS passed_count,
               GROUP_CONCAT(ranked.scenario_name, ', ') AS passed_scenario_names,
               MAX(CASE WHEN ranked.rn = 1 THEN ranked.scenario_id END) AS top_passed_scenario_id,
               MAX(CASE WHEN ranked.rn = 1 THEN ranked.scenario_name END) AS top_passed_scenario_name
        FROM (
            SELECT js.job_id, js.scenario_id, s.name AS scenario_name,
                   ROW_NUMBER() OVER (
                       PARTITION BY js.job_id
                       ORDER BY js.relevance_score DESC, js.scenario_id ASC
                   ) AS rn
            FROM job_scores js
            JOIN scenarios s ON s.id = js.scenario_id
            WHERE js.relevance_score >= s.gate_threshold
        ) ranked
        GROUP BY ranked.job_id
    ) gate ON gate.job_id = jobs.id
    LEFT JOIN (
        SELECT job_id, COUNT(*) AS scored_count FROM job_scores GROUP BY job_id
    ) scored ON scored.job_id = jobs.id
"""
```

(`get_jobs`/`get_job` function bodies below this are unchanged — only the two SQL fragment constants above lose the `feedback_scenarios` join/column.)

Replace `update_job_feedback`:

```python
def update_job_feedback(conn: sqlite3.Connection, job_id: int, status: str, note: str) -> None:
    conn.execute(
        "UPDATE jobs SET status = ?, feedback_note = ? WHERE id = ?",
        (status, note, job_id),
    )
    conn.commit()
```

Replace `reset_job`:

```python
def reset_job(conn: sqlite3.Connection, job_id: int) -> None:
    conn.execute(
        """UPDATE jobs SET
            status = 'new',
            content_type = NULL,
            simplified_content = '',
            summary = '',
            headline = '',
            feedback_note = NULL,
            feedback_handled_at = NULL,
            interest_score = NULL,
            interest_reasoning = NULL,
            attainability_score = NULL,
            attainability_reasoning = NULL,
            fit_score = NULL,
            profile_version_hash = NULL
        WHERE id = ?""",
        (job_id,),
    )
    conn.execute("DELETE FROM job_scores WHERE job_id = ?", (job_id,))
    conn.execute("DELETE FROM scenario_feedback WHERE job_id = ?", (job_id,))
    conn.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_queries.py -k "works_without_feedback_scenario_id or test_update_job_feedback" -v`
Expected: PASS (leave `test_reset_job_clears_pipeline_output_and_scores` red until Task 3 adds `upsert_scenario_feedback`)

- [ ] **Step 5: Commit**

```bash
git add app/db/queries.py tests/test_queries.py
git commit -m "feat: drop feedback_scenario_id coupling from update_job_feedback/reset_job/gate join"
```

---

### Task 3: `upsert_scenario_feedback`, `get_job_scores` gains note/direction

**Files:**
- Modify: `app/db/queries.py`
- Test: `tests/test_queries.py`

**Interfaces:**
- Produces: `upsert_scenario_feedback(conn, job_id, scenario_id, note, direction=None)`. `get_job_scores` rows gain `feedback_note`/`feedback_direction`. Consumed by Task 6 (route), Task 8 (templates).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_queries.py`:

```python
def test_upsert_scenario_feedback_inserts_and_updates(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, jid, scenario_id, "too broad", "lower")
    scores = q.get_job_scores(conn, jid)
    # No job_scores row exists yet — get_job_scores only surfaces scored scenarios —
    # so verify via get_recent_feedback_notes instead, which reads scenario_feedback directly.
    assert q.get_recent_feedback_notes(conn, scenario_id) == [{"direction": "lower", "note": "too broad"}]

    q.upsert_scenario_feedback(conn, jid, scenario_id, "actually fine", "higher")
    assert q.get_recent_feedback_notes(conn, scenario_id) == [{"direction": "higher", "note": "actually fine"}]


def test_upsert_scenario_feedback_deletes_when_both_blank(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, jid, scenario_id, "note", "higher")
    q.upsert_scenario_feedback(conn, jid, scenario_id, "", None)
    assert q.get_recent_feedback_notes(conn, scenario_id) == []


def test_upsert_scenario_feedback_keeps_row_with_only_note(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, jid, scenario_id, "just a comment", None)
    row = conn.execute(
        "SELECT note, direction FROM scenario_feedback WHERE job_id = ? AND scenario_id = ?", (jid, scenario_id)
    ).fetchone()
    assert row["note"] == "just a comment"
    assert row["direction"] is None


def test_upsert_scenario_feedback_keeps_row_with_only_direction(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, jid, scenario_id, "", "higher")
    row = conn.execute(
        "SELECT note, direction FROM scenario_feedback WHERE job_id = ? AND scenario_id = ?", (jid, scenario_id)
    ).fetchone()
    assert row["note"] == ""
    assert row["direction"] == "higher"


def test_get_job_scores_surfaces_feedback_note_and_direction(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.upsert_job_score(conn, jid, scenario_id, 0.5, "reasoning", "hash1")
    q.upsert_scenario_feedback(conn, jid, scenario_id, "too strict", "higher")
    scores = q.get_job_scores(conn, jid)
    assert scores[0]["feedback_note"] == "too strict"
    assert scores[0]["feedback_direction"] == "higher"


def test_get_job_scores_feedback_fields_none_when_no_feedback(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.upsert_job_score(conn, jid, scenario_id, 0.5, "reasoning", "hash1")
    scores = q.get_job_scores(conn, jid)
    assert scores[0]["feedback_note"] is None
    assert scores[0]["feedback_direction"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_queries.py -k "upsert_scenario_feedback or get_job_scores_surfaces or get_job_scores_feedback" -v`
Expected: FAIL — `upsert_scenario_feedback` doesn't exist; `get_job_scores` doesn't select `feedback_note`/`feedback_direction`.

- [ ] **Step 3: Implement**

In `app/db/queries.py`, add near `upsert_job_score`:

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
            ON CONFLICT(job_id, scenario_id) DO UPDATE SET
                note = excluded.note, direction = excluded.direction, handled_at = NULL""",
            (job_id, scenario_id, note, direction),
        )
    conn.commit()
```

Update `get_job_scores`:

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

Note: `get_recent_feedback_notes`, used by the first new test above, doesn't exist yet in its new form — it's rewritten in Task 4. Since `test_upsert_scenario_feedback_inserts_and_updates`/`test_upsert_scenario_feedback_deletes_when_both_blank` call it, run those two specifically after Task 4 lands; verify only the `get_job_scores`-focused tests and the two `keeps_row_with_only_*` tests (which query the table directly, not through `get_recent_feedback_notes`) at the end of this task.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_queries.py -k "keeps_row_with_only or get_job_scores_surfaces or get_job_scores_feedback" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/db/queries.py tests/test_queries.py
git commit -m "feat: add upsert_scenario_feedback, surface it on get_job_scores"
```

---

### Task 4: Rewrite refine-flow reads against `scenario_feedback`

**Files:**
- Modify: `app/db/queries.py`
- Test: `tests/test_queries.py`

**Interfaces:**
- Produces: `get_recent_feedback_notes(conn, scenario_id, limit=20) -> [{"direction": str, "note": str}]` (was `[{"status": str, "feedback_note": str}]`). `get_recent_feedback_job_ids` unchanged signature, new source. `mark_feedback_handled(conn, scenario_id, job_ids)` (gains `scenario_id`). Consumed by Task 5 (`propose_criteria`), Task 7 (`accept_proposals`).

- [ ] **Step 1: Write the failing tests**

In `tests/test_queries.py`, replace `test_get_recent_feedback_notes`, `test_get_recent_feedback_notes_reports_accepted_status`, `test_get_recent_feedback_notes_scoped_to_scenario`, `test_get_recent_feedback_notes_excludes_invalid_status`, `test_get_recent_feedback_notes_excludes_handled`, `test_get_recent_feedback_job_ids`, `test_mark_feedback_handled_excludes_from_future_calls`, `test_update_job_feedback_resets_handled_state` with:

```python
def test_get_recent_feedback_notes(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, j1, scenario_id, "too junior", "lower")
    notes = q.get_recent_feedback_notes(conn, scenario_id)
    assert {"direction": "lower", "note": "too junior"} in notes


def test_get_recent_feedback_notes_reports_direction(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, j1, scenario_id, "should have counted", "higher")
    notes = q.get_recent_feedback_notes(conn, scenario_id)
    assert notes == [{"direction": "higher", "note": "should have counted"}]


def test_get_recent_feedback_notes_scoped_to_scenario(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_a = q.insert_scenario(conn, "A", "")
    scenario_b = q.insert_scenario(conn, "B", "")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, j1, scenario_a, "too junior", "lower")
    assert q.get_recent_feedback_notes(conn, scenario_a) == [{"direction": "lower", "note": "too junior"}]
    assert q.get_recent_feedback_notes(conn, scenario_b) == []


def test_get_recent_feedback_notes_excludes_undirected_comments(conn):
    # A note left with no direction chosen is pure commentary — propose_criteria
    # has no polarity to act on, so it must not see it.
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, j1, scenario_id, "just a thought", None)
    assert q.get_recent_feedback_notes(conn, scenario_id) == []


def test_get_recent_feedback_notes_excludes_handled(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T1", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, j1, scenario_id, "too junior", "lower")
    q.mark_feedback_handled(conn, scenario_id, [j1])
    assert q.get_recent_feedback_notes(conn, scenario_id) == []


def test_get_recent_feedback_job_ids(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T1", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, j1, scenario_id, "too junior", "lower")
    assert q.get_recent_feedback_job_ids(conn, scenario_id) == [j1]


def test_mark_feedback_handled_scoped_to_one_scenario(conn):
    # A job can carry independent gate feedback for two scenarios — handling
    # one scenario's proposals must not clear the other's pending feedback.
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_a = q.insert_scenario(conn, "A", "")
    scenario_b = q.insert_scenario(conn, "B", "")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T1", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, j1, scenario_a, "too junior", "lower")
    q.upsert_scenario_feedback(conn, j1, scenario_b, "should count here too", "higher")
    q.mark_feedback_handled(conn, scenario_a, [j1])
    assert q.get_recent_feedback_job_ids(conn, scenario_a) == []
    assert q.get_recent_feedback_job_ids(conn, scenario_b) == [j1]


def test_upsert_scenario_feedback_resets_handled_state(conn):
    # Re-saving feedback on a job is fresh input the LLM hasn't seen yet,
    # even if its prior feedback had already been handled.
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T1", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, j1, scenario_id, "too junior", "lower")
    q.mark_feedback_handled(conn, scenario_id, [j1])
    q.upsert_scenario_feedback(conn, j1, scenario_id, "actually, too senior", "lower")
    assert q.get_recent_feedback_notes(conn, scenario_id) == [{"direction": "lower", "note": "actually, too senior"}]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_queries.py -k "get_recent_feedback or mark_feedback_handled or resets_handled_state" -v`
Expected: FAIL — `get_recent_feedback_notes` still returns the old `status`/`feedback_note` shape from `jobs`; `mark_feedback_handled` doesn't accept `scenario_id`.

- [ ] **Step 3: Implement**

In `app/db/queries.py`, replace `_recent_feedback_rows`/`get_recent_feedback_notes`/`get_recent_feedback_job_ids`/`mark_feedback_handled`:

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

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_queries.py -v`
Expected: PASS (all tests, including the ones deferred from Tasks 2 and 3)

- [ ] **Step 5: Commit**

```bash
git add app/db/queries.py tests/test_queries.py
git commit -m "feat: rewrite refine-flow feedback reads against scenario_feedback"
```

---

### Task 5: `propose_criteria` — direction tags

**Files:**
- Modify: `app/ai/refine.py`
- Test: `tests/test_refine.py`

**Interfaces:**
- Consumes: `{"direction": "higher"/"lower", "note": str}` shape from Task 4.
- Produces: same `propose_criteria(client, model, scenario, existing_criteria, feedback_notes)` signature, new tag text and prompt.

- [ ] **Step 1: Write the failing tests**

In `tests/test_refine.py`, replace the module-level `_NOTES` fixture and `test_propose_criteria_tags_notes_with_outcome`:

```python
_NOTES = [
    {"direction": "lower", "note": "required on-site work"},
    {"direction": "lower", "note": "too junior, needs senior level"},
]
```

```python
def test_propose_criteria_tags_notes_with_direction():
    response = "[]"
    client = _mock_client(response)
    notes = [
        {"direction": "lower", "note": "too junior"},
        {"direction": "higher", "note": "great senior role, should have matched"},
    ]
    propose_criteria(client, "llama3.2", _SCENARIO, _EXISTING, notes)
    call_args = client.chat.completions.create.call_args
    user_content = call_args.kwargs["messages"][1]["content"]
    assert "[SHOULD SCORE LOWER] too junior" in user_content
    assert "[SHOULD SCORE HIGHER] great senior role, should have matched" in user_content
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_refine.py -v`
Expected: FAIL — every test using `_NOTES` breaks (`propose_criteria` still does `n['status'].upper()`, a `KeyError` against the new `{"direction", "note"}` shape), and the new test looks for tags the current prompt never emits.

- [ ] **Step 3: Implement**

Replace `app/ai/refine.py`'s `_SYSTEM` and `propose_criteria`:

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


@dataclass
class CriterionProposal:
    text: str
    weight: str
    action: str


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
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user_content},
            ],
            temperature=0,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        items = json.loads(extract_json(resp.choices[0].message.content))
        proposals = []
        for item in items:
            if item.get("weight") not in ("must", "prefer", "avoid"):
                continue
            if item.get("action") not in ("add", "remove"):
                continue
            proposals.append(
                CriterionProposal(
                    text=item["text"],
                    weight=item["weight"],
                    action=item["action"],
                )
            )
        return proposals
    except Exception:
        return []
```

(`match_removal_target` is unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_refine.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add app/ai/refine.py tests/test_refine.py
git commit -m "feat: tag propose_criteria feedback by direction, not accept/reject"
```

---

### Task 6: `jobs.py` routes — drop scenario coupling, new scenario-feedback endpoint

**Files:**
- Modify: `app/routes/jobs.py`
- Create: `app/templates/jobs/_score_tab_panel.html`
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: `q.update_job_feedback` (Task 2), `q.upsert_scenario_feedback`/`q.get_job_scores` (Task 3).
- Produces: `POST /jobs/{job_id}/feedback` and `POST /jobs/bulk-feedback` scenario-agnostic. New `POST /jobs/{job_id}/scenario-feedback/{scenario_id}`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_routes_jobs.py`, delete these tests — they exercise behavior this task removes entirely (the scenario picker on the main feedback form, and bulk feedback's per-job scenario attribution):

- `test_job_feedback_can_target_non_default_scenario`
- `test_job_list_shows_feedback_scenario_tag_when_overridden`
- `test_job_list_omits_feedback_scenario_tag_when_matching_top_passed`
- `test_job_expand_feedback_form_defaults_to_top_passed_scenario`
- `test_job_expand_feedback_form_has_no_forced_selection_when_unscored`
- `test_job_expand_scenario_label_indicates_top_fit`
- `test_job_bulk_feedback_defaults_to_each_jobs_own_top_passed_scenario`
- `test_job_bulk_feedback_explicit_scenario_overrides_all`

Update the remaining feedback tests to drop `feedback_scenario_id`:

```python
def test_job_feedback_updates_status(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.post(
        f"/jobs/{jid}/feedback",
        data={"status": "accepted", "note": "good fit"},
    )
    assert resp.status_code == 200
    job = q.get_job(conn, jid)
    assert job["status"] == "accepted"
    assert job["feedback_note"] == "good fit"


def test_job_feedback_without_note_succeeds(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.post(f"/jobs/{jid}/feedback", data={"status": "accepted"})
    assert resp.status_code == 200
    job = q.get_job(conn, jid)
    assert job["status"] == "accepted"
    assert job["feedback_note"] is None
```

```python
def test_job_bulk_feedback_updates_multiple_jobs(client, conn):
    sid, j1, scenario_id = _seed(conn)
    j2 = q.insert_job(conn, source_id=sid, url="http://finn.no/job/2", title="Data Eng", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, j2, simplified_content="clean", content_type="job_posting", summary="Also good")
    q.upsert_job_score(conn, j2, scenario_id, 0.6, "Decent match", "hash2")
    resp = client.post(
        "/jobs/bulk-feedback",
        data={"job_ids": [j1, j2], "status": "rejected", "status_filter": "", "content_type_filter": ""},
    )
    assert resp.status_code == 200
    assert q.get_job(conn, j1)["status"] == "rejected"
    assert q.get_job(conn, j2)["status"] == "rejected"


def test_job_bulk_feedback_note_is_optional(client, conn):
    sid, j1, scenario_id = _seed(conn)
    resp = client.post(
        "/jobs/bulk-feedback",
        data={"job_ids": [j1], "status": "accepted", "status_filter": "", "content_type_filter": ""},
    )
    assert resp.status_code == 200
    assert q.get_job(conn, j1)["feedback_note"] is None


def test_job_bulk_feedback_returns_filtered_content_reflecting_removed_jobs(client, conn):
    sid, j1, scenario_id = _seed(conn)
    resp = client.post(
        "/jobs/bulk-feedback",
        data={"job_ids": [j1], "status": "rejected", "status_filter": "", "content_type_filter": ""},
    )
    assert resp.status_code == 200
    assert "ML Eng" not in resp.text
    assert "No jobs found" in resp.text


def test_job_bulk_feedback_respects_status_filter_for_response(client, conn):
    sid, j1, scenario_id = _seed(conn)
    q.update_job_feedback(conn, j1, "accepted", "")
    j2 = q.insert_job(conn, source_id=sid, url="http://finn.no/job/2", title="Data Eng", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, j2, simplified_content="clean", content_type="job_posting", summary="Also good")
    resp = client.post(
        "/jobs/bulk-feedback",
        data={"job_ids": [j2], "status": "accepted", "status_filter": "accepted", "content_type_filter": ""},
    )
    assert resp.status_code == 200
    assert "ML Eng" in resp.text
    assert "Data Eng" in resp.text
```

Leave `test_job_list_bulk_bar_has_scenario_select_and_actions` untouched in this task — it asserts against `_content.html`, which this task doesn't modify, so it still passes unchanged. Task 8 replaces it (deleting this version) once the template's scenario `<select>` is actually removed.

Add new tests for the scenario-feedback endpoint:

```python
def test_scenario_feedback_saves_note_and_direction(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.post(
        f"/jobs/{jid}/scenario-feedback/{scenario_id}",
        data={"note": "should have scored lower", "direction": "lower"},
    )
    assert resp.status_code == 200
    scores = q.get_job_scores(conn, jid)
    assert scores[0]["feedback_note"] == "should have scored lower"
    assert scores[0]["feedback_direction"] == "lower"


def test_scenario_feedback_empty_direction_treated_as_unset(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.post(
        f"/jobs/{jid}/scenario-feedback/{scenario_id}",
        data={"note": "just a comment", "direction": ""},
    )
    assert resp.status_code == 200
    scores = q.get_job_scores(conn, jid)
    assert scores[0]["feedback_note"] == "just a comment"
    assert scores[0]["feedback_direction"] is None


def test_scenario_feedback_returns_updated_panel(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.post(
        f"/jobs/{jid}/scenario-feedback/{scenario_id}",
        data={"note": "should count more", "direction": "higher"},
    )
    assert resp.status_code == 200
    assert 'class="score-tab-panel"' in resp.text
    assert "should count more" in resp.text
    assert 'aria-pressed="true"' in resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_routes_jobs.py -k "scenario_feedback" -v`
Expected: FAIL — `/jobs/{job_id}/scenario-feedback/{scenario_id}` doesn't exist (404).

- [ ] **Step 3: Implement**

Create `app/templates/jobs/_score_tab_panel.html`:

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

In `app/routes/jobs.py`, update `job_feedback`:

```python
@router.post("/jobs/{job_id}/feedback", response_class=HTMLResponse)
def job_feedback(
    job_id: int,
    request: Request,
    status: str = Form(...),
    note: str | None = Form(None),
    conn: sqlite3.Connection = Depends(get_db),
):
    q.update_job_feedback(conn, job_id, status, note)
    return HTMLResponse(content="", status_code=200)
```

Update `job_bulk_feedback`:

```python
@router.post("/jobs/bulk-feedback", response_class=HTMLResponse)
def job_bulk_feedback(
    request: Request,
    job_ids: list[int] = Form(...),
    status: str = Form(...),
    note: str | None = Form(None),
    status_filter: str | None = Form(None),
    content_type_filter: str | None = Form(None),
    show_filtered_filter: bool = Form(False),
    conn: sqlite3.Connection = Depends(get_db),
):
    for job_id in job_ids:
        q.update_job_feedback(conn, job_id, status, note)

    status_filter = status_filter or None
    content_type_filter = content_type_filter or None
    jobs = _enrich_jobs(conn, _get_filtered_jobs(conn, status_filter, content_type_filter, show_filtered_filter))
    counts = q.get_job_counts(conn)
    scenarios = q.get_scenarios(conn)
    effective_status = status_filter if (status_filter is not None or content_type_filter is not None) else "new"
    return templates.TemplateResponse(
        request, "jobs/_content.html",
        {
            "jobs": jobs, "counts": counts, "scenarios": scenarios,
            "status": effective_status, "content_type": content_type_filter, "show_filtered": show_filtered_filter,
        },
    )
```

Add the new route, after `job_feedback`:

```python
@router.post("/jobs/{job_id}/scenario-feedback/{scenario_id}", response_class=HTMLResponse)
def job_scenario_feedback(
    job_id: int,
    scenario_id: int,
    request: Request,
    note: str = Form(""),
    direction: str = Form(""),
    conn: sqlite3.Connection = Depends(get_db),
):
    q.upsert_scenario_feedback(conn, job_id, scenario_id, note, direction or None)
    job = q.get_job(conn, job_id)
    js = next(s for s in q.get_job_scores(conn, job_id) if s["scenario_id"] == scenario_id)
    return templates.TemplateResponse(request, "jobs/_score_tab_panel.html", {"job": job, "js": js})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_routes_jobs.py -v`
Expected: PASS (all tests). This task doesn't touch `_feedback.html`/`_macros.html`/`_content.html` — only the route file and the new partial — so every test exercising those templates (score-tab rendering, meta tags, the bulk bar) is untouched and stays green throughout; only the feedback-route tests and the newly-added scenario-feedback tests are new/changed here.

- [ ] **Step 5: Commit**

```bash
git add app/routes/jobs.py app/templates/jobs/_score_tab_panel.html tests/test_routes_jobs.py
git commit -m "feat: drop scenario coupling from job feedback, add per-scenario feedback endpoint"
```

---

### Task 7: `mark_feedback_handled` scenario-scoping in `scenarios.py`

**Files:**
- Modify: `app/routes/scenarios.py`
- Test: `tests/test_routes_scenarios.py`

**Interfaces:**
- Consumes: `q.mark_feedback_handled(conn, scenario_id, job_ids)` (Task 4).

- [ ] **Step 1: Write the failing tests**

In `tests/test_routes_scenarios.py`, update the five tests that seed feedback via the now-removed `update_job_feedback(..., feedback_scenario_id=...)` path — replace their seeding line with `q.upsert_scenario_feedback(conn, job_id, sid, "too junior", "lower")`:

```python
def test_refine_alone_does_not_mark_feedback_handled(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Must be remote", "must")
    source_id = q.insert_source(conn, "s", "http://x", "http")
    job_id = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_pipeline(conn, job_id, simplified_content="", content_type="job_posting")
    q.upsert_scenario_feedback(conn, job_id, sid, "too junior", "lower")

    proposals = [CriterionProposal(text="Must be senior", weight="must", action="add")]
    with patch("app.routes.scenarios.propose_criteria", return_value=proposals):
        client.post(f"/scenarios/{sid}/refine")

    assert q.get_recent_feedback_job_ids(conn, sid) == [job_id]


def test_refine_embeds_feedback_job_ids_in_apply_form(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    source_id = q.insert_source(conn, "s", "http://x", "http")
    job_id = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_pipeline(conn, job_id, simplified_content="", content_type="job_posting")
    q.upsert_scenario_feedback(conn, job_id, sid, "too junior", "lower")

    proposals = [CriterionProposal(text="Must be senior", weight="must", action="add")]
    with patch("app.routes.scenarios.propose_criteria", return_value=proposals):
        resp = client.post(f"/scenarios/{sid}/refine")
    assert f'name="feedback_job_ids" value="{job_id}"' in resp.text


def test_manual_add_criterion_does_not_mark_feedback_handled(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    source_id = q.insert_source(conn, "s", "http://x", "http")
    job_id = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_pipeline(conn, job_id, simplified_content="", content_type="job_posting")
    q.upsert_scenario_feedback(conn, job_id, sid, "too junior", "lower")

    resp = client.post(f"/scenarios/{sid}/criteria", data={"text": "Must be senior", "weight": "must"})
    assert resp.status_code == 200
    assert q.get_recent_feedback_job_ids(conn, sid) == [job_id]


def test_apply_batch_marks_feedback_handled_even_when_all_rows_skipped(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    source_id = q.insert_source(conn, "s", "http://x", "http")
    job_id = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_pipeline(conn, job_id, simplified_content="", content_type="job_posting")
    q.upsert_scenario_feedback(conn, job_id, sid, "too junior", "lower")

    resp = client.post(
        f"/scenarios/{sid}/refine/accept",
        data={"kind_0": "add", "text_0": "Must be senior", "weight_0": "must", "feedback_job_ids": str(job_id)},
    )
    assert resp.status_code == 200
    assert q.get_recent_feedback_job_ids(conn, sid) == []
    assert q.get_criteria(conn, sid) == []


def test_refine_all_scenarios_embeds_feedback_job_ids(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    source_id = q.insert_source(conn, "s", "http://x", "http")
    job_id = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_pipeline(conn, job_id, simplified_content="", content_type="job_posting")
    q.upsert_scenario_feedback(conn, job_id, sid, "too junior", "lower")

    proposals = [CriterionProposal(text="Must be senior", weight="must", action="add")]
    with patch("app.routes.scenarios.propose_criteria", return_value=proposals):
        resp = client.post("/scenarios/refine")
    assert f'name="feedback_job_ids" value="{job_id}"' in resp.text
```

Add a scoping regression test:

```python
def test_apply_batch_only_marks_this_scenarios_feedback_handled(client, conn):
    sid_a = q.insert_scenario(conn, "Remote ML", "")
    sid_b = q.insert_scenario(conn, "Robotics", "")
    source_id = q.insert_source(conn, "s", "http://x", "http")
    job_id = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_pipeline(conn, job_id, simplified_content="", content_type="job_posting")
    q.upsert_scenario_feedback(conn, job_id, sid_a, "too junior", "lower")
    q.upsert_scenario_feedback(conn, job_id, sid_b, "should count here too", "higher")

    client.post(
        f"/scenarios/{sid_a}/refine/accept",
        data={"feedback_job_ids": str(job_id)},
    )

    assert q.get_recent_feedback_job_ids(conn, sid_a) == []
    assert q.get_recent_feedback_job_ids(conn, sid_b) == [job_id]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_routes_scenarios.py -k "feedback" -v`
Expected: FAIL — `q.update_job_feedback(..., feedback_scenario_id=...)` raises `TypeError` (parameter removed in Task 2); `mark_feedback_handled` call in `accept_proposals` doesn't pass `scenario_id` yet.

- [ ] **Step 3: Implement**

In `app/routes/scenarios.py`, update `accept_proposals`'s call:

```python
    q.mark_feedback_handled(conn, scenario_id, _parse_job_ids(form.get("feedback_job_ids")))
```

(This is the only production-code change — `refine_criteria`/`refine_all_scenarios` already call `q.get_recent_feedback_job_ids(conn, scenario["id"])`/`q.get_recent_feedback_notes(conn, scenario["id"])` with the same shape as before; their new meaning comes entirely from Task 4's rewrite underneath them.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_routes_scenarios.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add app/routes/scenarios.py tests/test_routes_scenarios.py
git commit -m "feat: scope mark_feedback_handled to one scenario in accept_proposals"
```

---

### Task 8: Templates — drop scenario pickers, wire the tab-panel feedback form, toggle styling

**Files:**
- Modify: `app/templates/jobs/_feedback.html`, `app/templates/jobs/_macros.html`, `app/templates/jobs/_content.html`, `app/templates/base.html`
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: `_score_tab_panel.html` (Task 6), `js.feedback_note`/`js.feedback_direction` (Task 3).

- [ ] **Step 1: Write the failing tests**

Delete `test_job_list_bulk_bar_has_scenario_select_and_actions` — it asserts `"Keep each job's own scenario" in resp.text`, which this task's template change removes; `test_job_list_bulk_bar_has_actions_and_no_scenario_select` (added below) is its replacement, asserting the opposite.

Nothing else in `tests/test_routes_jobs.py` needs changing: the score-tab-panel content this task adds (the direction toggle, the hint, the note field) doesn't overlap with any existing assertion in the tab-rendering or meta-tag tests (`test_job_expand_shows_tab_per_scored_scenario`, `test_job_expand_shows_gate_pass_indicator`, `test_job_expand_shows_fit_scorecard`, `test_job_list_tags_are_semantic_definition_list`, etc.) — they keep passing unmodified. Add the following new tests:

```python
def test_job_expand_shows_scenario_feedback_form_per_tab(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert f'hx-post="/jobs/{jid}/scenario-feedback/{scenario_id}"' in resp.text
    assert "Why should this scenario score different?" in resp.text
    assert "Should score higher" in resp.text
    assert "Should score lower" in resp.text


def test_job_list_bulk_bar_has_actions_and_no_scenario_select(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get("/jobs")
    assert resp.status_code == 200
    assert "Keep each job's own scenario" not in resp.text
    assert 'name="feedback_scenario_id"' not in resp.text
    assert 'form="bulk-form" name="status" value="accepted"' in resp.text
    assert 'form="bulk-form" name="status" value="rejected"' in resp.text
    assert 'form="bulk-form" name="status" value="invalid"' in resp.text
    assert 'title="Does not match your criteria — feeds back into scenario tuning."' in resp.text
    assert 'title="Not a usable posting (expired, spam, duplicate, wrong content) — does not affect scenario criteria."' in resp.text


def test_job_expand_has_no_scenario_select(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert '<select name="feedback_scenario_id"' not in resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_routes_jobs.py -v`
Expected: The tests added/updated in this step fail against the still-old templates (scenario `<select>` still present, tab panels not yet using `_score_tab_panel.html`).

- [ ] **Step 3: Implement**

Replace `app/templates/jobs/_feedback.html` in full:

```html
{% import "jobs/_macros.html" as macros %}
<article class="job-row" id="job-{{ job.id }}">
  <label class="job-select-wrap">
    <input type="checkbox" class="job-select" name="job_ids" value="{{ job.id }}" form="bulk-form"
      aria-label="Select {{ job.title or 'this job' }} for bulk action" onclick="event.stopPropagation()">
  </label>
  <div role="button" tabindex="0" style="cursor:pointer"
    hx-get="/jobs/{{ job.id }}/collapse"
    hx-target="#job-{{ job.id }}"
    hx-swap="outerHTML"
    hx-trigger="click, keyup[key=='Enter']">
    {{ macros.meta_tags(job) }}
    <h3 class="job-title">{{ job.title or "(no title)" }}</h3>
    {% if job.headline %}
      <p class="job-hook">{{ job.headline }}</p>
    {% endif %}
  </div>
  <p>
    {% if job.company %}<span>· {{ job.company }}</span>{% endif %}
    <a href="{{ job.url }}" target="_blank" rel="noopener">↗ original</a>
  </p>
  {% if job.summary %}
    <section aria-label="Summary">{{ job.summary | markdown }}</section>
  {% endif %}
  {% if job_scores %}
    <section class="score-box score-compare" aria-label="Scenario score comparison">
      <div class="score-tabs">
        {% for js in job_scores %}
          <input type="radio" id="score-tab-{{ job.id }}-{{ js.scenario_id }}" name="score-tab-{{ job.id }}" class="score-tab-input" {% if js.scenario_id == job.top_passed_scenario_id %}checked{% endif %}>
          <label for="score-tab-{{ job.id }}-{{ js.scenario_id }}" class="score-tab-card">
            {% if js.relevance_score >= js.scenario_gate_threshold %}<span title="Passed gate">✓</span>
            {% else %}<span title="Below gate threshold">✗</span>
            {% endif %}
            <span class="tag">{{ js.scenario_name }}</span>
            {% if js.relevance_score >= 0.7 %}<span class="score-badge score-high">{{ "%.0f"|format(js.relevance_score * 100) }}%</span>
            {% elif js.relevance_score >= 0.4 %}<span class="score-badge score-mid">{{ "%.0f"|format(js.relevance_score * 100) }}%</span>
            {% else %}<span class="score-badge score-low">{{ "%.0f"|format(js.relevance_score * 100) }}%</span>
            {% endif %}
          </label>
          {% include "jobs/_score_tab_panel.html" %}
        {% endfor %}
      </div>
    </section>
  {% endif %}
  {% if job.passed_gate_count %}
    <section class="score-box fit-scorecard" aria-label="Fit scorecard">
      {% if job.fit_score is not none %}
        <dl class="fit-scorecard-grid">
          <dt>Interest</dt>
          <dd>
            <span class="score-badge {% if job.interest_score >= 0.7 %}score-high{% elif job.interest_score >= 0.4 %}score-mid{% else %}score-low{% endif %}">{{ "%.0f"|format(job.interest_score * 100) }}%</span>
            {{ job.interest_reasoning | markdown }}
          </dd>
          <dt>Attainability</dt>
          <dd>
            <span class="score-badge {% if job.attainability_score >= 0.7 %}score-high{% elif job.attainability_score >= 0.4 %}score-mid{% else %}score-low{% endif %}">{{ "%.0f"|format(job.attainability_score * 100) }}%</span>
            {{ job.attainability_reasoning | markdown }}
          </dd>
        </dl>
      {% else %}
        <p>Not yet assessed.</p>
      {% endif %}
    </section>
  {% endif %}
  <form class="feedback-form" style="margin-top:0.75rem;"
    hx-post="/jobs/{{ job.id }}/feedback"
    hx-target="#job-{{ job.id }}"
    hx-swap="outerHTML">
    <label>Note (optional):
      <textarea name="note" placeholder="Why accepting/rejecting? What should change?">{{ job.feedback_note or '' }}</textarea>
    </label>
    <div class="actions" role="group" aria-label="Decision">
      <button type="submit" name="status" value="accepted" class="btn btn-accept">Accept</button>
      <button type="submit" name="status" value="rejected" class="btn btn-reject" title="Does not match your criteria — feeds back into scenario tuning.">Reject</button>
      <button type="submit" name="status" value="invalid" class="btn btn-invalid" title="Not a usable posting (expired, spam, duplicate, wrong content) — does not affect scenario criteria.">Invalid</button>
    </div>
  </form>
  <details class="job-advanced">
    <summary>Advanced&hellip;</summary>
    <button type="button" class="btn btn-reset"
      data-progress-url="/jobs/{{ job.id }}/reset"
      data-progress-display="#reset-progress-{{ job.id }}"
      title="Wipe the simplified content, classification, summary and scores, then immediately rerun the full pipeline from the original posting text.">
      Reset to new
    </button>
  </details>
  <div class="reset-progress" id="reset-progress-{{ job.id }}" aria-live="polite"></div>
</article>
```

(Only two changes from the current file: the score-tab loop body's inline `<div class="score-tab-panel">...</div>` is replaced with `{% include "jobs/_score_tab_panel.html" %}`, the feedback form drops its `<label>Scenario...<select>` block and the note `<textarea>` now pre-fills from `job.feedback_note`, matching how every other editable field in this app pre-fills on re-render.)

Update `app/templates/jobs/_macros.html`'s `meta_tags` — drop the feedback-scenario tag block entirely, since `feedback_scenario_id`/`feedback_scenario_name` no longer exist:

```html
{% macro fit_badge_fields(job) %}
{% if job.fit_score is not none %}
<dt class="sr-only">Fit score</dt>
<dd>
  {% if job.fit_score >= 0.7 %}<span class="score-badge score-high">{{ "%.0f"|format(job.fit_score * 100) }}%</span>
  {% elif job.fit_score >= 0.4 %}<span class="score-badge score-mid">{{ "%.0f"|format(job.fit_score * 100) }}%</span>
  {% else %}<span class="score-badge score-low">{{ "%.0f"|format(job.fit_score * 100) }}%</span>
  {% endif %}
</dd>
{% endif %}
{% if job.passed_scenario_names %}
<dt class="sr-only">Matched scenarios</dt>
{% for name in job.passed_scenario_names.split(', ') %}
<dd><span class="tag">{{ name }}</span></dd>
{% endfor %}
{% endif %}
{% endmacro %}

{% macro meta_tags(job) %}
<dl class="job-tags">
  {{ fit_badge_fields(job) }}
  <dt class="sr-only">Content type</dt>
  <dd><span class="tag">{{ job.content_type or "unknown" }}</span></dd>
  <dt class="sr-only">Source</dt>
  <dd><span class="tag">{{ job.source_name or "" }}</span></dd>
  {% if job.published_at %}
  <dt class="sr-only">Published</dt>
  <dd><span class="tag">{{ job.published_at | time_ago }}</span></dd>
  {% endif %}
</dl>
{% endmacro %}
```

In `app/templates/jobs/_content.html`, delete the bulk scenario-select block entirely:

```html
<div class="bulk-bar">
  <div class="bulk-bar-header">
    <span class="bulk-count"></span>
    <button type="button" class="btn bulk-clear">Clear selection</button>
    <details class="job-advanced">
      <summary>Advanced&hellip;</summary>
      <button type="button" class="btn btn-reset" data-progress-url="/jobs/bulk-reset" data-progress-jobs
        data-progress-display="#bulk-reset-progress"
        title="Wipe the simplified content, classification, summary and scores for each selected job, then immediately rerun the full pipeline from the original posting text.">
        Reset to new
      </button>
    </details>
  </div>
  <div class="actions" role="group" aria-label="Bulk decision">
    <button type="submit" form="bulk-form" name="status" value="accepted" class="btn btn-accept">Accept</button>
    <button type="submit" form="bulk-form" name="status" value="rejected" class="btn btn-reject" title="Does not match your criteria — feeds back into scenario tuning.">Reject</button>
    <button type="submit" form="bulk-form" name="status" value="invalid" class="btn btn-invalid" title="Not a usable posting (expired, spam, duplicate, wrong content) — does not affect scenario criteria.">Invalid</button>
  </div>
  <label class="bulk-note">Note (optional):
    <textarea name="note" form="bulk-form" placeholder="Optional note for all selected jobs"></textarea>
  </label>
  <div class="reset-progress" id="bulk-reset-progress" aria-live="polite"></div>
</div>
```

(Everything above and below this block in `_content.html` — the filter bar, select-all, the job loop — is unchanged.)

Add CSS and the toggle-button script to `app/templates/base.html`, next to the existing `.fit-scorecard-grid` rules:

```css
.direction-toggle { display:flex; gap:0.4rem; margin:0.4rem 0; }
.direction-btn[aria-pressed="true"] { border-color:#0d6efd; background:#eaf2ff; }
.scenario-feedback-form textarea { width:100%; min-height:3em; margin:0.3rem 0; }
```

```html
<script>
document.addEventListener('click', function (e) {
  var btn = e.target.closest('.direction-btn');
  if (!btn) return;
  var form = btn.closest('form');
  var hidden = form.querySelector('input[name="direction"]');
  var alreadyPressed = btn.getAttribute('aria-pressed') === 'true';
  form.querySelectorAll('.direction-btn').forEach(function (b) { b.setAttribute('aria-pressed', 'false'); });
  if (alreadyPressed) {
    hidden.value = '';
  } else {
    hidden.value = btn.dataset.direction;
    btn.setAttribute('aria-pressed', 'true');
  }
});
</script>
```

(A single document-level delegated listener, registered once — matches the app's existing pattern for the bulk-select/drag-select scripts already in `base.html`, and keeps working after HTMX swaps `_score_tab_panel.html` back in without needing to re-bind anything.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_routes_jobs.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add app/templates/jobs/_feedback.html app/templates/jobs/_macros.html app/templates/jobs/_content.html app/templates/base.html tests/test_routes_jobs.py
git commit -m "feat: per-scenario feedback UI, drop scenario pickers from feedback forms"
```

---

### Task 9: Full suite verification + manual smoke test

**Files:** none (verification only)

- [ ] **Step 1: Run the full automated test suite**

Run: `pytest -v`
Expected: PASS — every test in the suite.

- [ ] **Step 2: Grep for any leftover references to removed concepts**

Run: `grep -rn "feedback_scenario_id\|feedback_scenario_name\|\[REJECTED\]\|\[ACCEPTED\]" app/ tests/`
Expected: no output. If anything remains, it's a missed call site — fix it and re-run Step 1.

- [ ] **Step 3: Manual smoke test**

Use the `run-dev-server` skill against a throwaway copy of `job-seek.db`. Then, in the browser:

1. Go to `/jobs`, expand a job that's scored against at least two scenarios (or seed one via a test fetch/re-evaluate first).
2. On one scenario's tab, click "▲ Should score higher," type a note, save — confirm the tab panel updates in place and the button stays pressed after the swap.
3. Click the same button again — confirm it deselects (hidden `direction` clears) and the note field is still editable independently.
4. Confirm the main feedback form (bottom of the expanded job) no longer has a scenario dropdown, just the note + Accept/Reject/Invalid.
5. Go to that scenario's page (`/scenarios`), click "Refine criteria" — confirm the request succeeds and (if the LLM proposes anything) the proposal reasoning is plausible given the direction you gave.
6. Select several jobs in the list, open the bulk bar — confirm there's no scenario selector, just note + status buttons.
7. Stop the dev server per the skill's instructions once done.

- [ ] **Step 4: Report results**

If Steps 1–3 all pass cleanly, the implementation is complete. If manual testing surfaces a UX issue not caught by the automated tests, fix it directly, re-run the relevant test file, and commit as a small follow-up (`fix: ...`) rather than folding it silently into an earlier task's commit.
