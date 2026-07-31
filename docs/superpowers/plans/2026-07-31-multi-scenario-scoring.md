# Multi-Scenario Scoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a job be scored independently against every scenario (not just one), show the best score tagged with which scenario produced it, and add a smart re-evaluate that skips pairs already scored against the scenario's current criteria.

**Architecture:** A new `job_scores` table replaces the single `relevance_score`/`score_reasoning`/`scenario_id` columns on `jobs`, holding one row per (job, scenario) pair keyed by a content hash of that scenario's current criteria. Fetch scores every new job against every scenario automatically; an explicit "Re-evaluate" action (single-scenario or combined-all) backfills/refreshes scores, skipping pairs whose stored hash already matches. Job list/detail read the best score across all a job's scenario rows via a SQL window-function join.

**Tech Stack:** Python 3.12, SQLite (window functions, `ON CONFLICT` upserts), FastAPI, Jinja2 — no new dependencies.

## Global Constraints

- SQLite only, single-user local app — migrations run synchronously and block on startup; no concurrent-access concerns.
- No new Python or JS dependency.
- Criteria rows are immutable once created (only inserted/deleted, never edited) — scenario versioning must not assume an `updated_at` column exists.
- A job's `content_type`, `summary`, and `simplified_content` stay scenario-agnostic; only `relevance_score`/`score_reasoning` are per-scenario.
- Jobs already `accepted`/`rejected`/`invalid` are never touched by fetch or re-evaluate.

**Spec:** `docs/superpowers/specs/2026-07-31-multi-scenario-scoring-design.md`

---

### Task 1: `job_scores` table, `jobs` column removal, and migration

**Files:**
- Modify: `app/db/schema.py`
- Test: `tests/test_schema.py`

**Interfaces:**
- Produces: `job_scores` table — columns `id, job_id, scenario_id, relevance_score, score_reasoning, scenario_version_hash, evaluated_at`, `UNIQUE(job_id, scenario_id)`, both FKs `ON DELETE CASCADE`.
- Produces: `jobs` table without `relevance_score`, `score_reasoning`, `scenario_id`.
- Produces: `init_db(conn)` — same signature, now also runs a migration that backfills `job_scores` (with `scenario_version_hash = 'legacy'`) from any pre-existing scored jobs before dropping those columns.

- [ ] **Step 1: Update the existing table-set assertions and add new failing tests**

In `tests/test_schema.py`, replace both occurrences of the expected table set:

```python
def test_init_db_creates_all_tables(conn):
    init_db(conn)
    assert _tables(conn) == {"profile", "sources", "scenarios", "criteria", "jobs", "fetch_runs"}


def test_init_db_is_idempotent(conn):
    init_db(conn)
    init_db(conn)  # should not raise
    assert _tables(conn) == {"profile", "sources", "scenarios", "criteria", "jobs", "fetch_runs"}
```

with:

```python
def test_init_db_creates_all_tables(conn):
    init_db(conn)
    assert _tables(conn) == {"profile", "sources", "scenarios", "criteria", "jobs", "job_scores", "fetch_runs"}


def test_init_db_is_idempotent(conn):
    init_db(conn)
    init_db(conn)  # should not raise
    assert _tables(conn) == {"profile", "sources", "scenarios", "criteria", "jobs", "job_scores", "fetch_runs"}
```

Then append these new tests at the end of the file:

```python
def test_job_scores_unique_per_job_and_scenario(conn):
    init_db(conn)
    conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s', 'http://x', 'http')")
    conn.execute("INSERT INTO jobs (source_id, url) VALUES (1, 'http://job/1')")
    conn.execute("INSERT INTO scenarios (name) VALUES ('Remote ML')")
    conn.execute(
        "INSERT INTO job_scores (job_id, scenario_id, relevance_score, scenario_version_hash) "
        "VALUES (1, 1, 0.5, 'abc')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO job_scores (job_id, scenario_id, relevance_score, scenario_version_hash) "
            "VALUES (1, 1, 0.9, 'def')"
        )


def test_init_db_migrates_jobs_scores_to_job_scores_table(conn):
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
            relevance_score REAL,
            score_reasoning TEXT NOT NULL DEFAULT '',
            content_type TEXT CHECK(content_type IN ('job_posting', 'lead', 'irrelevant', 'error')),
            scenario_id INTEGER REFERENCES scenarios(id),
            fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
            status TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('new', 'accepted', 'rejected', 'invalid')),
            feedback_note TEXT
        );
        """
    )
    conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s', 'http://x', 'http')")
    conn.execute("INSERT INTO scenarios (name) VALUES ('Remote ML')")
    conn.execute(
        "INSERT INTO jobs (source_id, url, content_type, summary, relevance_score, score_reasoning, scenario_id) "
        "VALUES (1, 'http://job/1', 'job_posting', 'Good role', 0.8, 'Great match', 1)"
    )
    conn.execute(
        "INSERT INTO jobs (source_id, url, content_type) VALUES (1, 'http://job/2', 'irrelevant')"
    )
    conn.commit()

    init_db(conn)

    cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    assert "relevance_score" not in cols
    assert "score_reasoning" not in cols
    assert "scenario_id" not in cols

    scores = conn.execute(
        "SELECT job_id, scenario_id, relevance_score, score_reasoning, scenario_version_hash FROM job_scores"
    ).fetchall()
    assert [dict(s) for s in scores] == [
        {
            "job_id": 1,
            "scenario_id": 1,
            "relevance_score": 0.8,
            "score_reasoning": "Great match",
            "scenario_version_hash": "legacy",
        }
    ]

    jobs = conn.execute("SELECT id, url, content_type FROM jobs ORDER BY id").fetchall()
    assert [dict(j) for j in jobs] == [
        {"id": 1, "url": "http://job/1", "content_type": "job_posting"},
        {"id": 2, "url": "http://job/2", "content_type": "irrelevant"},
    ]

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO job_scores (job_id, scenario_id, relevance_score, scenario_version_hash) "
            "VALUES (999, 1, 0.5, 'x')"
        )

    # Idempotent: running init_db again on the now-migrated DB doesn't duplicate or error.
    init_db(conn)
    assert conn.execute("SELECT COUNT(*) FROM job_scores").fetchone()[0] == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_schema.py -v`
Expected: FAIL — `test_init_db_creates_all_tables`/`test_init_db_is_idempotent` fail because `job_scores` doesn't exist yet; `test_job_scores_unique_per_job_and_scenario` fails with `sqlite3.OperationalError: no such table: job_scores`; `test_init_db_migrates_jobs_scores_to_job_scores_table` fails because the old columns are still present after `init_db`.

- [ ] **Step 3: Update the schema**

In `app/db/schema.py`, replace the `jobs` table definition:

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
    relevance_score REAL,
    score_reasoning TEXT NOT NULL DEFAULT '',
    content_type TEXT CHECK(content_type IN ('job_posting', 'lead', 'irrelevant', 'error')),
    scenario_id INTEGER REFERENCES scenarios(id),
    fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
    status TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('new', 'accepted', 'rejected', 'invalid')),
    feedback_note TEXT
);
```

with:

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
    feedback_note TEXT
);

CREATE TABLE IF NOT EXISTS job_scores (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    scenario_id INTEGER NOT NULL REFERENCES scenarios(id) ON DELETE CASCADE,
    relevance_score REAL NOT NULL,
    score_reasoning TEXT NOT NULL DEFAULT '',
    scenario_version_hash TEXT NOT NULL,
    evaluated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(job_id, scenario_id)
);
```

(Leave `fetch_runs` where it is, after `job_scores`.)

Then add the migration function and wire it into `init_db`. Replace:

```python
def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL)
    _migrate_sources_fetcher_type(conn)
```

with:

```python
def _migrate_jobs_scores_to_table(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='jobs'"
    ).fetchone()
    if row is None or "relevance_score" not in row[0]:
        return
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        """
        INSERT OR IGNORE INTO job_scores (job_id, scenario_id, relevance_score, score_reasoning, scenario_version_hash, evaluated_at)
        SELECT id, scenario_id, relevance_score, score_reasoning, 'legacy', datetime('now')
        FROM jobs
        WHERE scenario_id IS NOT NULL AND relevance_score IS NOT NULL
        """
    )
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
            feedback_note TEXT
        );
        INSERT INTO jobs_new (id, source_id, url, title, company, raw_text, simplified_content, summary, content_type, fetched_at, status, feedback_note)
        SELECT id, source_id, url, title, company, raw_text, simplified_content, summary, content_type, fetched_at, status, feedback_note
        FROM jobs;
        DROP TABLE jobs;
        ALTER TABLE jobs_new RENAME TO jobs;
        """
    )
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL)
    _migrate_sources_fetcher_type(conn)
    _migrate_jobs_scores_to_table(conn)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_schema.py -v`
Expected: PASS — all tests including the new ones.

- [ ] **Step 5: Run the full suite to check for regressions, then commit**

Run: `.venv/bin/python -m pytest`
Expected: many failures elsewhere (queries.py/pipeline.py/routes still reference the removed columns — later tasks fix these); `tests/test_schema.py` itself is fully green.

```bash
git add app/db/schema.py tests/test_schema.py
git commit -m "feat: add job_scores table and migrate scores off jobs"
```

---

### Task 2: Scenario version hashing

**Files:**
- Create: `app/scenario_version.py`
- Test: `tests/test_scenario_version.py`

**Interfaces:**
- Produces: `compute_version_hash(scenario: dict, criteria: list[dict]) -> str` — `scenario` needs a `"description"` key (missing key treated as `""`); each item in `criteria` needs `"text"` and `"weight"` keys. Deterministic, order-independent w.r.t. `criteria` order.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_scenario_version.py`:

```python
from app.scenario_version import compute_version_hash


def test_hash_is_deterministic():
    scenario = {"description": "Remote ML roles"}
    criteria = [{"text": "Must be remote", "weight": "must"}]
    assert compute_version_hash(scenario, criteria) == compute_version_hash(scenario, criteria)


def test_hash_changes_with_description():
    criteria = [{"text": "Must be remote", "weight": "must"}]
    h1 = compute_version_hash({"description": "A"}, criteria)
    h2 = compute_version_hash({"description": "B"}, criteria)
    assert h1 != h2


def test_hash_changes_with_criteria():
    scenario = {"description": "Remote ML roles"}
    h1 = compute_version_hash(scenario, [{"text": "Must be remote", "weight": "must"}])
    h2 = compute_version_hash(scenario, [{"text": "Must be senior", "weight": "must"}])
    assert h1 != h2


def test_hash_unaffected_by_criteria_order():
    scenario = {"description": "Remote ML roles"}
    c1 = {"text": "Must be remote", "weight": "must"}
    c2 = {"text": "Prefer Python", "weight": "prefer"}
    assert compute_version_hash(scenario, [c1, c2]) == compute_version_hash(scenario, [c2, c1])


def test_hash_handles_missing_description_key():
    assert compute_version_hash({}, []) == compute_version_hash({"description": ""}, [])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_scenario_version.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.scenario_version'`

- [ ] **Step 3: Implement**

Create `app/scenario_version.py`:

```python
from __future__ import annotations
import hashlib


def compute_version_hash(scenario: dict, criteria: list[dict]) -> str:
    parts = [scenario.get("description", "")]
    for c in sorted(criteria, key=lambda c: (c["weight"], c["text"])):
        parts.append(f"{c['weight']}:{c['text']}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_scenario_version.py -v`
Expected: PASS — all 5 tests.

- [ ] **Step 5: Commit**

```bash
git add app/scenario_version.py tests/test_scenario_version.py
git commit -m "feat: add scenario content-hash versioning for staleness detection"
```

---

### Task 3: Query layer — job_scores CRUD and best-score joins

**Files:**
- Modify: `app/db/queries.py`
- Test: `tests/test_queries.py`

**Interfaces:**
- Consumes: `job_scores` table from Task 1.
- Produces:
  - `upsert_job_score(conn, job_id: int, scenario_id: int, score: float, reasoning: str, version_hash: str) -> None`
  - `get_job_score(conn, job_id: int, scenario_id: int) -> dict | None`
  - `get_job_score_hashes(conn, scenario_id: int) -> dict[int, str]` — `{job_id: scenario_version_hash}`
  - `update_job_pipeline(conn, job_id: int, *, simplified_content: str, content_type: str, summary: str = "") -> None` — narrowed, no longer takes score/scenario params
  - `get_jobs(...)` / `get_job(...)` — each returned dict gains `best_score: float | None`, `best_score_reasoning: str | None`, `best_scenario_name: str | None`
  - `get_recent_feedback_notes(conn, scenario_id, limit=20) -> list[str]` — now scoped via `job_scores` instead of the removed `jobs.scenario_id`

- [ ] **Step 1: Update existing tests and add new ones**

In `tests/test_queries.py`, replace:

```python
def test_update_job_pipeline(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_pipeline(
        conn, jid,
        simplified_content="clean text",
        content_type="job_posting",
        summary="Good role",
        relevance_score=0.85,
        score_reasoning="Matches profile",
    )
    job = q.get_job(conn, jid)
    assert job["content_type"] == "job_posting"
    assert job["relevance_score"] == pytest.approx(0.85)
```

with:

```python
def test_update_job_pipeline(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_pipeline(
        conn, jid,
        simplified_content="clean text",
        content_type="job_posting",
        summary="Good role",
    )
    job = q.get_job(conn, jid)
    assert job["content_type"] == "job_posting"
    assert job["summary"] == "Good role"
    assert job["best_score"] is None
```

Replace:

```python
def test_get_recent_feedback_notes(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_pipeline(conn, j1, simplified_content="", content_type="job_posting", scenario_id=scenario_id)
    q.update_job_feedback(conn, j1, "rejected", "too junior")
    notes = q.get_recent_feedback_notes(conn, scenario_id)
    assert "too junior" in notes
```

with:

```python
def test_get_recent_feedback_notes(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_pipeline(conn, j1, simplified_content="", content_type="job_posting")
    q.upsert_job_score(conn, j1, scenario_id, 0.5, "reasoning", "hash1")
    q.update_job_feedback(conn, j1, "rejected", "too junior")
    notes = q.get_recent_feedback_notes(conn, scenario_id)
    assert "too junior" in notes


def test_get_recent_feedback_notes_scoped_to_scenario(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_a = q.insert_scenario(conn, "A", "")
    scenario_b = q.insert_scenario(conn, "B", "")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_pipeline(conn, j1, simplified_content="", content_type="job_posting")
    q.upsert_job_score(conn, j1, scenario_a, 0.5, "reasoning", "hash1")
    q.update_job_feedback(conn, j1, "rejected", "too junior")
    assert q.get_recent_feedback_notes(conn, scenario_a) == ["too junior"]
    assert q.get_recent_feedback_notes(conn, scenario_b) == []
```

Then append these new tests at the end of the file:

```python
def test_upsert_job_score_inserts_then_updates(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.upsert_job_score(conn, jid, scenario_id, 0.4, "first pass", "hash1")
    q.upsert_job_score(conn, jid, scenario_id, 0.9, "second pass", "hash2")
    score = q.get_job_score(conn, jid, scenario_id)
    assert score["relevance_score"] == pytest.approx(0.9)
    assert score["score_reasoning"] == "second pass"
    assert score["scenario_version_hash"] == "hash2"


def test_get_job_score_missing_returns_none(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    assert q.get_job_score(conn, jid, scenario_id) is None


def test_get_job_score_hashes_scoped_to_scenario(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_a = q.insert_scenario(conn, "A", "")
    scenario_b = q.insert_scenario(conn, "B", "")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    j2 = q.insert_job(conn, source_id=source_id, url="http://job/2", title="T", company="C", raw_text="r")
    q.upsert_job_score(conn, j1, scenario_a, 0.5, "r1", "hash1")
    q.upsert_job_score(conn, j2, scenario_b, 0.5, "r2", "hash2")
    assert q.get_job_score_hashes(conn, scenario_a) == {j1: "hash1"}


def test_get_jobs_shows_best_score_across_scenarios(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_a = q.insert_scenario(conn, "A", "")
    scenario_b = q.insert_scenario(conn, "B", "")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="", content_type="job_posting")
    q.upsert_job_score(conn, jid, scenario_a, 0.3, "low fit", "hash1")
    q.upsert_job_score(conn, jid, scenario_b, 0.8, "great fit", "hash2")
    job = q.get_jobs(conn)[0]
    assert job["best_score"] == pytest.approx(0.8)
    assert job["best_score_reasoning"] == "great fit"
    assert job["best_scenario_name"] == "B"


def test_get_job_shows_best_score(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.upsert_job_score(conn, jid, scenario_id, 0.6, "decent", "hash1")
    job = q.get_job(conn, jid)
    assert job["best_score"] == pytest.approx(0.6)
    assert job["best_scenario_name"] == "A"


def test_get_jobs_orders_by_best_score_desc(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")
    low = q.insert_job(conn, source_id=source_id, url="http://job/low", title="Low", company="C", raw_text="r")
    high = q.insert_job(conn, source_id=source_id, url="http://job/high", title="High", company="C", raw_text="r")
    q.upsert_job_score(conn, low, scenario_id, 0.2, "", "hash1")
    q.upsert_job_score(conn, high, scenario_id, 0.9, "", "hash1")
    jobs = q.get_jobs(conn)
    assert [j["title"] for j in jobs] == ["High", "Low"]


def test_get_jobs_job_with_no_score_has_none(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    job = q.get_jobs(conn)[0]
    assert job["best_score"] is None
    assert job["best_scenario_name"] is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_queries.py -v`
Expected: FAIL — `update_job_pipeline() got an unexpected keyword argument 'relevance_score'` and similar for the modified tests; `AttributeError`/`KeyError`-style failures for the new tests since `upsert_job_score`, `get_job_score`, `get_job_score_hashes` don't exist yet and `best_score` isn't a key on returned dicts.

- [ ] **Step 3: Implement the query changes**

In `app/db/queries.py`, replace `update_job_pipeline`:

```python
def update_job_pipeline(
    conn: sqlite3.Connection,
    job_id: int,
    *,
    simplified_content: str,
    content_type: str,
    summary: str = "",
    relevance_score: float | None = None,
    score_reasoning: str = "",
    scenario_id: int | None = None,
) -> None:
    conn.execute(
        """UPDATE jobs SET
            simplified_content = ?,
            content_type = ?,
            summary = ?,
            relevance_score = ?,
            score_reasoning = ?,
            scenario_id = ?
        WHERE id = ?""",
        (simplified_content, content_type, summary, relevance_score, score_reasoning, scenario_id, job_id),
    )
    conn.commit()
```

with:

```python
def update_job_pipeline(
    conn: sqlite3.Connection,
    job_id: int,
    *,
    simplified_content: str,
    content_type: str,
    summary: str = "",
) -> None:
    conn.execute(
        """UPDATE jobs SET
            simplified_content = ?,
            content_type = ?,
            summary = ?
        WHERE id = ?""",
        (simplified_content, content_type, summary, job_id),
    )
    conn.commit()


def upsert_job_score(
    conn: sqlite3.Connection,
    job_id: int,
    scenario_id: int,
    score: float,
    reasoning: str,
    version_hash: str,
) -> None:
    conn.execute(
        """INSERT INTO job_scores (job_id, scenario_id, relevance_score, score_reasoning, scenario_version_hash)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(job_id, scenario_id) DO UPDATE SET
            relevance_score = excluded.relevance_score,
            score_reasoning = excluded.score_reasoning,
            scenario_version_hash = excluded.scenario_version_hash,
            evaluated_at = datetime('now')""",
        (job_id, scenario_id, score, reasoning, version_hash),
    )
    conn.commit()


def get_job_score(conn: sqlite3.Connection, job_id: int, scenario_id: int) -> dict | None:
    return _row_to_dict(
        conn.execute(
            "SELECT * FROM job_scores WHERE job_id = ? AND scenario_id = ?", (job_id, scenario_id)
        ).fetchone()
    )


def get_job_score_hashes(conn: sqlite3.Connection, scenario_id: int) -> dict[int, str]:
    rows = conn.execute(
        "SELECT job_id, scenario_version_hash FROM job_scores WHERE scenario_id = ?", (scenario_id,)
    ).fetchall()
    return {r["job_id"]: r["scenario_version_hash"] for r in rows}
```

Then replace `get_jobs` and `get_job`:

```python
def get_jobs(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    content_type: str | None = None,
) -> list[dict]:
    clauses, params = [], []
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    if content_type is not None:
        clauses.append("content_type = ?")
        params.append(content_type)
    sql = "SELECT * FROM jobs"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY relevance_score DESC NULLS LAST, fetched_at DESC"
    return _rows_to_dicts(conn.execute(sql, params).fetchall())


def get_job(conn: sqlite3.Connection, job_id: int) -> dict | None:
    return _row_to_dict(conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone())
```

with:

```python
_BEST_SCORE_SELECT = """
    jobs.*,
    best.relevance_score AS best_score,
    best.score_reasoning AS best_score_reasoning,
    scenarios.name AS best_scenario_name
"""

_BEST_SCORE_JOIN = """
    FROM jobs
    LEFT JOIN (
        SELECT job_id, scenario_id, relevance_score, score_reasoning,
               ROW_NUMBER() OVER (PARTITION BY job_id ORDER BY relevance_score DESC, scenario_id ASC) AS rn
        FROM job_scores
    ) best ON best.job_id = jobs.id AND best.rn = 1
    LEFT JOIN scenarios ON scenarios.id = best.scenario_id
"""


def get_jobs(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    content_type: str | None = None,
) -> list[dict]:
    clauses, params = [], []
    if status is not None:
        clauses.append("jobs.status = ?")
        params.append(status)
    if content_type is not None:
        clauses.append("jobs.content_type = ?")
        params.append(content_type)
    sql = f"SELECT {_BEST_SCORE_SELECT} {_BEST_SCORE_JOIN}"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY best.relevance_score DESC NULLS LAST, jobs.fetched_at DESC"
    return _rows_to_dicts(conn.execute(sql, params).fetchall())


def get_job(conn: sqlite3.Connection, job_id: int) -> dict | None:
    sql = f"SELECT {_BEST_SCORE_SELECT} {_BEST_SCORE_JOIN} WHERE jobs.id = ?"
    return _row_to_dict(conn.execute(sql, (job_id,)).fetchone())
```

Finally, replace `get_recent_feedback_notes`:

```python
def get_recent_feedback_notes(
    conn: sqlite3.Connection, scenario_id: int, limit: int = 20
) -> list[str]:
    rows = conn.execute(
        """SELECT feedback_note FROM jobs
        WHERE scenario_id = ? AND feedback_note IS NOT NULL AND feedback_note != ''
        ORDER BY fetched_at DESC LIMIT ?""",
        (scenario_id, limit),
    ).fetchall()
    return [r["feedback_note"] for r in rows]
```

with:

```python
def get_recent_feedback_notes(
    conn: sqlite3.Connection, scenario_id: int, limit: int = 20
) -> list[str]:
    rows = conn.execute(
        """SELECT jobs.feedback_note FROM jobs
        JOIN job_scores ON job_scores.job_id = jobs.id AND job_scores.scenario_id = ?
        WHERE jobs.feedback_note IS NOT NULL AND jobs.feedback_note != ''
        ORDER BY jobs.fetched_at DESC LIMIT ?""",
        (scenario_id, limit),
    ).fetchall()
    return [r["feedback_note"] for r in rows]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_queries.py -v`
Expected: PASS — all tests including the new ones.

- [ ] **Step 5: Run the full suite, then commit**

Run: `.venv/bin/python -m pytest`
Expected: `test_schema.py` and `test_queries.py` PASS; pipeline/route/template tests still fail (fixed in later tasks).

```bash
git add app/db/queries.py tests/test_queries.py
git commit -m "feat: per-scenario job score queries and best-score joins"
```

---

### Task 4: Fetch scores every new job against every scenario

**Files:**
- Modify: `app/pipeline.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `q.get_scenarios`, `q.get_criteria`, `q.upsert_job_score` (Task 3), `compute_version_hash` (Task 2).
- Produces: `run_fetch` unchanged signature; for each newly classified `job_posting`/`lead`, summarizes once and scores against every scenario present at that moment (zero scenarios → summarized but unscored, no crash).

- [ ] **Step 1: Write the new failing tests**

In `tests/test_pipeline.py`, append at the end of the file:

```python
def test_run_fetch_scores_against_every_scenario(conn, source):
    first_id = q.get_scenarios(conn)[0]["id"]
    second_id = q.insert_scenario(conn, "Robotics", "")
    q.insert_criterion(conn, second_id, "Must involve embedded systems", "must")

    raw_jobs = [RawJob(url="http://example.com/job/1", title="ML Eng", company="Acme", raw_text="<p>We are hiring</p>")]
    client = _mock_client(
        '{"type": "job_posting", "reason": "full description"}',
        "Good ML role",
        '{"score": 0.9, "reasoning": "Great match"}',
    )

    with patch("app.pipeline.HttpFetcher") as MockFetcher:
        MockFetcher.return_value.fetch.return_value = raw_jobs
        messages, result = _drain(run_fetch(source, conn, client, "llama3.2", "browser-profile"))

    job_id = q.get_jobs(conn)[0]["id"]
    assert q.get_job_score(conn, job_id, first_id) is not None
    assert q.get_job_score(conn, job_id, second_id) is not None
    assert sum("Scored" in m for m in messages) == 2


def test_run_fetch_with_no_scenarios_still_summarizes(conn):
    source_id = q.insert_source(conn, "test", "http://example.com", "http")
    source_dict = q.get_source(conn, source_id)
    raw_jobs = [RawJob(url="http://example.com/job/1", title="ML Eng", company="Acme", raw_text="<p>We are hiring</p>")]
    client = _mock_client(
        '{"type": "job_posting", "reason": "full description"}',
        "Good ML role",
        '{"score": 0.9, "reasoning": "Great match"}',
    )

    with patch("app.pipeline.HttpFetcher") as MockFetcher:
        MockFetcher.return_value.fetch.return_value = raw_jobs
        messages, result = _drain(run_fetch(source_dict, conn, client, "llama3.2", "browser-profile"))

    job = q.get_jobs(conn)[0]
    assert job["content_type"] == "job_posting"
    assert job["summary"] == "Good ML role"
    assert job["best_score"] is None
    assert not any("Scored" in m for m in messages)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_pipeline.py -v`
Expected: FAIL — `test_run_fetch_scores_against_every_scenario` fails because only the active scenario gets scored today; `test_run_fetch_with_no_scenarios_still_summarizes` fails because today's code only summarizes/scores when there's an *active* scenario, and this test has none set active (in fact none exist), so `job["summary"]` would be empty.

- [ ] **Step 3: Update `run_fetch`**

In `app/pipeline.py`, add this import alongside the existing ones:

```python
from app.scenario_version import compute_version_hash
```

Then replace:

```python
        profile = q.get_profile(conn)
        scenario = q.get_active_scenario(conn)
        criteria = q.get_criteria(conn, scenario["id"]) if scenario else []

        for i, raw in enumerate(raw_jobs, start=1):
            if q.url_exists(conn, raw.url):
                yield _progress(f"[{i}/{jobs_found}] Skipping duplicate: {raw.url}")
                continue
            job_id = q.insert_job(
                conn,
                source_id=source["id"],
                url=raw.url,
                title=raw.title,
                company=raw.company,
                raw_text=raw.raw_text,
            )
            jobs_new += 1
            simplified = simplify(raw.raw_text)
            is_slack = source["fetcher_type"] == "slack"
            content_type, _ = classify(client, model, simplified, is_slack=is_slack)
            yield _progress(f"[{i}/{jobs_found}] Classified as {content_type}: {raw.url}")

            if content_type in ("job_posting", "lead") and scenario:
                job_summary = summarize(client, model, simplified)
                score, reasoning = evaluate(client, model, profile, scenario, criteria, job_summary)
                q.update_job_pipeline(
                    conn, job_id,
                    simplified_content=simplified,
                    content_type=content_type,
                    summary=job_summary,
                    relevance_score=score,
                    score_reasoning=reasoning,
                    scenario_id=scenario["id"],
                )
                yield _progress(f"[{i}/{jobs_found}] Scored {score}: {raw.url}")
            else:
                q.update_job_pipeline(
                    conn, job_id,
                    simplified_content=simplified,
                    content_type=content_type,
                )
```

with:

```python
        profile = q.get_profile(conn)
        scenarios = q.get_scenarios(conn)

        for i, raw in enumerate(raw_jobs, start=1):
            if q.url_exists(conn, raw.url):
                yield _progress(f"[{i}/{jobs_found}] Skipping duplicate: {raw.url}")
                continue
            job_id = q.insert_job(
                conn,
                source_id=source["id"],
                url=raw.url,
                title=raw.title,
                company=raw.company,
                raw_text=raw.raw_text,
            )
            jobs_new += 1
            simplified = simplify(raw.raw_text)
            is_slack = source["fetcher_type"] == "slack"
            content_type, _ = classify(client, model, simplified, is_slack=is_slack)
            yield _progress(f"[{i}/{jobs_found}] Classified as {content_type}: {raw.url}")

            if content_type in ("job_posting", "lead"):
                job_summary = summarize(client, model, simplified)
                q.update_job_pipeline(
                    conn, job_id,
                    simplified_content=simplified,
                    content_type=content_type,
                    summary=job_summary,
                )
                for scenario in scenarios:
                    criteria = q.get_criteria(conn, scenario["id"])
                    score, reasoning = evaluate(client, model, profile, scenario, criteria, job_summary)
                    version_hash = compute_version_hash(scenario, criteria)
                    q.upsert_job_score(conn, job_id, scenario["id"], score, reasoning, version_hash)
                    yield _progress(f"[{i}/{jobs_found}] Scored {score} for '{scenario['name']}': {raw.url}")
            else:
                q.update_job_pipeline(
                    conn, job_id,
                    simplified_content=simplified,
                    content_type=content_type,
                )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_pipeline.py -v`
Expected: PASS — all tests including the two new ones.

- [ ] **Step 5: Run the full suite, then commit**

Run: `.venv/bin/python -m pytest`
Expected: `test_schema.py`, `test_queries.py`, `test_scenario_version.py`, `test_pipeline.py` PASS; route/template tests still fail (fixed in later tasks).

```bash
git add app/pipeline.py tests/test_pipeline.py
git commit -m "feat: score every new job against every scenario at fetch time"
```

---

### Task 5: Smart re-evaluate (single + combined) via `run_reevaluate`

**Files:**
- Modify: `app/pipeline.py`
- Modify: `app/routes/scenarios.py`
- Modify: `app/templates/scenarios/index.html`
- Test: `tests/test_routes_scenarios.py`

**Interfaces:**
- Produces: `run_reevaluate(conn, client, model, scenario: dict) -> Generator[str, None, int]` in `app/pipeline.py` — yields progress strings, `StopIteration.value` is the count of jobs actually re-scored (skips jobs whose stored `job_scores` hash already matches the scenario's current hash).
- Produces: `POST /scenarios/{scenario_id}/reevaluate` (existing path, new implementation) and `POST /scenarios/reevaluate` (new, combined) — both `StreamingResponse`, `media_type="text/plain"`.

- [ ] **Step 1: Add `run_reevaluate` to `app/pipeline.py`**

Append this function at the end of `app/pipeline.py`:

```python
def run_reevaluate(
    conn: sqlite3.Connection,
    client: openai.OpenAI,
    model: str,
    scenario: dict,
) -> Generator[str, None, int]:
    profile = q.get_profile(conn)
    criteria = q.get_criteria(conn, scenario["id"])
    current_hash = compute_version_hash(scenario, criteria)
    eligible = [j for j in q.get_jobs(conn, status="new") if j["content_type"] in ("job_posting", "lead")]
    existing_hashes = q.get_job_score_hashes(conn, scenario["id"])
    to_evaluate = [j for j in eligible if existing_hashes.get(j["id"]) != current_hash]
    skipped = len(eligible) - len(to_evaluate)

    msg = f"Re-evaluating {len(to_evaluate)} job(s) for scenario '{scenario['name']}'"
    if skipped:
        msg += f", skipping {skipped} already current"
    yield _progress(msg)

    for i, job in enumerate(to_evaluate, start=1):
        new_summary = summarize(client, model, job["simplified_content"]) if job["simplified_content"] else job["summary"]
        score, reasoning = evaluate(client, model, profile, scenario, criteria, new_summary)
        q.update_job_pipeline(
            conn, job["id"],
            simplified_content=job["simplified_content"],
            content_type=job["content_type"],
            summary=new_summary,
        )
        q.upsert_job_score(conn, job["id"], scenario["id"], score, reasoning, current_hash)
        yield _progress(f"[{i}/{len(to_evaluate)}] Re-scored {score}: {job['title'] or job['url']}")

    yield _progress(f"Re-evaluation complete for '{scenario['name']}': {len(to_evaluate)} job(s) updated")
    return len(to_evaluate)
```

- [ ] **Step 2: Update the existing reevaluate test and add new ones (failing)**

In `tests/test_routes_scenarios.py`, replace:

```python
def test_reevaluate_streams_progress_and_updates_jobs(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.set_active_scenario(conn, sid)
    q.insert_criterion(conn, sid, "Must be remote", "must")
    source_id = q.insert_source(conn, "s", "http://x", "http")
    job_id = q.insert_job(conn, source_id=source_id, url="http://job/1", title="ML Eng", company="C", raw_text="r")
    q.update_job_pipeline(conn, job_id, simplified_content="clean", content_type="job_posting", scenario_id=sid)

    with patch("app.routes.scenarios.summarize", return_value="Updated summary"), \
         patch("app.routes.scenarios.evaluate", return_value=(0.75, "Good match")):
        resp = client.post(f"/scenarios/{sid}/reevaluate")

    assert resp.status_code == 200
    assert "Re-evaluating 1 job(s)" in resp.text
    assert "Re-evaluation complete" in resp.text
    job = q.get_job(conn, job_id)
    assert job["relevance_score"] == pytest.approx(0.75)
    assert job["summary"] == "Updated summary"
```

with:

```python
def test_reevaluate_streams_progress_and_updates_jobs(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Must be remote", "must")
    source_id = q.insert_source(conn, "s", "http://x", "http")
    job_id = q.insert_job(conn, source_id=source_id, url="http://job/1", title="ML Eng", company="C", raw_text="r")
    q.update_job_pipeline(conn, job_id, simplified_content="clean", content_type="job_posting")

    with patch("app.pipeline.summarize", return_value="Updated summary"), \
         patch("app.pipeline.evaluate", return_value=(0.75, "Good match")):
        resp = client.post(f"/scenarios/{sid}/reevaluate")

    assert resp.status_code == 200
    assert "Re-evaluating 1 job(s)" in resp.text
    assert "Re-evaluation complete" in resp.text
    job = q.get_job(conn, job_id)
    assert job["best_score"] == pytest.approx(0.75)
    assert job["summary"] == "Updated summary"


def test_reevaluate_skips_jobs_already_current(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Must be remote", "must")
    source_id = q.insert_source(conn, "s", "http://x", "http")
    q.insert_job(conn, source_id=source_id, url="http://job/1", title="ML Eng", company="C", raw_text="r")
    job_id = q.get_jobs(conn)[0]["id"]
    q.update_job_pipeline(conn, job_id, simplified_content="clean", content_type="job_posting")

    with patch("app.pipeline.summarize", return_value="Updated summary"), \
         patch("app.pipeline.evaluate", return_value=(0.75, "Good match")) as mock_evaluate:
        client.post(f"/scenarios/{sid}/reevaluate")
        resp = client.post(f"/scenarios/{sid}/reevaluate")

    assert mock_evaluate.call_count == 1
    assert "skipping 1 already current" in resp.text
    assert "Re-evaluating 0 job(s)" in resp.text


def test_reevaluate_all_scenarios_streams_combined_progress(client, conn):
    sid_a = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid_a, "Must be remote", "must")
    sid_b = q.insert_scenario(conn, "Robotics", "")
    q.insert_criterion(conn, sid_b, "Must involve embedded systems", "must")
    source_id = q.insert_source(conn, "s", "http://x", "http")
    job_id = q.insert_job(conn, source_id=source_id, url="http://job/1", title="ML Eng", company="C", raw_text="r")
    q.update_job_pipeline(conn, job_id, simplified_content="clean", content_type="job_posting")

    with patch("app.pipeline.summarize", return_value="Updated summary"), \
         patch("app.pipeline.evaluate", return_value=(0.75, "Good match")):
        resp = client.post("/scenarios/reevaluate")

    assert resp.status_code == 200
    assert "Re-evaluating 2 scenario(s)" in resp.text
    assert "All scenarios re-evaluated: 2 job(s) updated across 2 scenario(s)" in resp.text
    assert q.get_job_score(conn, job_id, sid_a) is not None
    assert q.get_job_score(conn, job_id, sid_b) is not None
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_routes_scenarios.py -v`
Expected: FAIL — the reevaluate route still filters on the removed `j["scenario_id"]` field (`KeyError`), and `POST /scenarios/reevaluate` doesn't exist yet (404).

- [ ] **Step 4: Rewrite the routes**

In `app/routes/scenarios.py`, replace the imports:

```python
from app.ai.refine import propose_criteria
from app.ai.summarize import summarize
from app.ai.evaluate import evaluate
from app.template_env import templates
```

with:

```python
from app.ai.refine import propose_criteria
from app.pipeline import run_reevaluate
from app.template_env import templates
```

Then replace the `reevaluate_jobs` function:

```python
@router.post("/scenarios/{scenario_id}/reevaluate")
def reevaluate_jobs(
    scenario_id: int,
    conn: sqlite3.Connection = Depends(get_db),
    client: openai.OpenAI = Depends(get_ai_client),
    model: str = Depends(get_model),
):
    scenario = _get_scenario_or_404(conn, scenario_id)
    criteria = q.get_criteria(conn, scenario_id)
    profile = q.get_profile(conn)
    jobs = q.get_jobs(conn, status="new")
    evaluated = [j for j in jobs if j["scenario_id"] == scenario_id and j["content_type"] in ("job_posting", "lead")]

    def stream():
        total = len(evaluated)
        msg = f"Re-evaluating {total} job(s) for scenario '{scenario['name']}'"
        logger.info(msg)
        yield msg + "\n"
        for i, job in enumerate(evaluated, start=1):
            new_summary = summarize(client, model, job["simplified_content"]) if job["simplified_content"] else job["summary"]
            score, reasoning = evaluate(client, model, profile, scenario, criteria, new_summary)
            q.update_job_pipeline(
                conn, job["id"],
                simplified_content=job["simplified_content"],
                content_type=job["content_type"],
                summary=new_summary,
                relevance_score=score,
                score_reasoning=reasoning,
                scenario_id=scenario_id,
            )
            msg = f"[{i}/{total}] Re-scored {score}: {job['title'] or job['url']}"
            logger.info(msg)
            yield msg + "\n"
        msg = f"Re-evaluation complete: {total} job(s) updated"
        logger.info(msg)
        yield msg + "\n"

    return StreamingResponse(stream(), media_type="text/plain")
```

with:

```python
@router.post("/scenarios/{scenario_id}/reevaluate")
def reevaluate_jobs(
    scenario_id: int,
    conn: sqlite3.Connection = Depends(get_db),
    client: openai.OpenAI = Depends(get_ai_client),
    model: str = Depends(get_model),
):
    scenario = _get_scenario_or_404(conn, scenario_id)

    def stream():
        gen = run_reevaluate(conn, client, model, scenario)
        try:
            while True:
                yield next(gen) + "\n"
        except StopIteration:
            pass

    return StreamingResponse(stream(), media_type="text/plain")


@router.post("/scenarios/reevaluate")
def reevaluate_all_scenarios(
    conn: sqlite3.Connection = Depends(get_db),
    client: openai.OpenAI = Depends(get_ai_client),
    model: str = Depends(get_model),
):
    scenarios = q.get_scenarios(conn)

    def stream():
        yield f"Re-evaluating {len(scenarios)} scenario(s)\n"
        total_updated = 0
        for scenario in scenarios:
            gen = run_reevaluate(conn, client, model, scenario)
            try:
                while True:
                    yield next(gen) + "\n"
            except StopIteration as stop:
                total_updated += stop.value
        yield f"All scenarios re-evaluated: {total_updated} job(s) updated across {len(scenarios)} scenario(s)\n"

    return StreamingResponse(stream(), media_type="text/plain")
```

- [ ] **Step 5: Add the combined button to the Scenarios page**

In `app/templates/scenarios/index.html`, replace:

```html
<h1>Scenarios</h1>
<form method="post" action="/scenarios" style="display:flex; gap:0.5rem; flex-wrap:wrap; margin-bottom:1.5rem;">
  <input type="text" name="name" placeholder="Scenario name" required>
  <input type="text" name="description" placeholder="Description (optional)" style="flex:1;">
  <button type="submit" class="btn">Create</button>
</form>
```

with:

```html
<h1>Scenarios</h1>
<form method="post" action="/scenarios" style="display:flex; gap:0.5rem; flex-wrap:wrap; margin-bottom:1rem;">
  <input type="text" name="name" placeholder="Scenario name" required>
  <input type="text" name="description" placeholder="Description (optional)" style="flex:1;">
  <button type="submit" class="btn">Create</button>
</form>

{% if scenarios %}
<button class="btn" data-progress-url="/scenarios/reevaluate" style="margin-bottom:1.5rem;">Re-evaluate all scenarios</button>
{% endif %}
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_routes_scenarios.py -v`
Expected: PASS — all tests including the three new/updated ones.

- [ ] **Step 7: Run the full suite, then commit**

Run: `.venv/bin/python -m pytest`
Expected: everything passes except `test_routes_jobs.py` (fixed in Task 6).

```bash
git add app/pipeline.py app/routes/scenarios.py app/templates/scenarios/index.html tests/test_routes_scenarios.py
git commit -m "feat: smart re-evaluate that skips already-current scores, plus a combined all-scenarios action"
```

---

### Task 6: Job list/detail show the best score and its scenario

**Files:**
- Modify: `app/templates/jobs/_row.html`
- Modify: `app/templates/jobs/_feedback.html`
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: `job.best_score`, `job.best_score_reasoning`, `job.best_scenario_name` (Task 3).

- [ ] **Step 1: Update the test seed helper and add new tests (failing)**

In `tests/test_routes_jobs.py`, replace:

```python
def _seed(conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "http")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/1", title="ML Eng", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="job_posting",
                          summary="Great role", relevance_score=0.9, score_reasoning="Good match")
    return sid, jid
```

with:

```python
def _seed(conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "http")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/1", title="ML Eng", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="job_posting", summary="Great role")
    scenario_id = q.insert_scenario(conn, "Remote ML", "")
    q.upsert_job_score(conn, jid, scenario_id, 0.9, "Good match", "hash1")
    return sid, jid
```

Then append these new tests at the end of the file:

```python
def test_job_list_shows_scenario_tag_for_best_score(client, conn):
    _seed(conn)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Remote ML" in resp.text


def test_job_expand_shows_scenario_tag_with_reasoning(client, conn):
    sid, jid = _seed(conn)
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert "Good match" in resp.text
    assert "Remote ML" in resp.text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_routes_jobs.py -v`
Expected: FAIL — `_seed` no longer matches `update_job_pipeline`'s old signature until the templates catch up, and the two new tests fail because "Remote ML" doesn't appear anywhere in either rendered view yet.

- [ ] **Step 3: Update the templates**

In `app/templates/jobs/_row.html`, replace:

```html
<div class="job-row" id="job-{{ job.id }}">
  <div style="display:flex; align-items:center; gap:0.5rem; flex-wrap:wrap;">
    {% set score = job.relevance_score %}
    {% if score is not none %}
      {% if score >= 0.7 %}
        <span class="score-badge score-high">{{ "%.0f"|format(score * 100) }}%</span>
      {% elif score >= 0.4 %}
        <span class="score-badge score-mid">{{ "%.0f"|format(score * 100) }}%</span>
      {% else %}
        <span class="score-badge score-low">{{ "%.0f"|format(score * 100) }}%</span>
      {% endif %}
    {% endif %}
    <span class="tag">{{ job.content_type or "unknown" }}</span>
```

with:

```html
<div class="job-row" id="job-{{ job.id }}">
  <div style="display:flex; align-items:center; gap:0.5rem; flex-wrap:wrap;">
    {% set score = job.best_score %}
    {% if score is not none %}
      {% if score >= 0.7 %}
        <span class="score-badge score-high">{{ "%.0f"|format(score * 100) }}%</span>
      {% elif score >= 0.4 %}
        <span class="score-badge score-mid">{{ "%.0f"|format(score * 100) }}%</span>
      {% else %}
        <span class="score-badge score-low">{{ "%.0f"|format(score * 100) }}%</span>
      {% endif %}
      <span class="tag">{{ job.best_scenario_name }}</span>
    {% endif %}
    <span class="tag">{{ job.content_type or "unknown" }}</span>
```

In `app/templates/jobs/_feedback.html`, replace:

```html
  {% if job.score_reasoning %}
    <div style="margin-top:0.4rem; font-size:0.9em; color:#666;">{{ job.score_reasoning | markdown }}</div>
  {% endif %}
```

with:

```html
  {% if job.best_score_reasoning %}
    <div style="margin-top:0.4rem; font-size:0.9em; color:#666;">
      <span class="tag">{{ job.best_scenario_name }}</span>
      {{ job.best_score_reasoning | markdown }}
    </div>
  {% endif %}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_routes_jobs.py -v`
Expected: PASS — all tests including the two new ones.

- [ ] **Step 5: Run the full suite, then commit**

Run: `.venv/bin/python -m pytest`
Expected: PASS, zero failures across the whole suite.

```bash
git add app/templates/jobs/_row.html app/templates/jobs/_feedback.html tests/test_routes_jobs.py
git commit -m "feat: show best score's scenario as a tag in job list and detail views"
```

---

### Task 7: End-to-end verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `.venv/bin/python -m pytest`
Expected: PASS, zero failures.

- [ ] **Step 2: Manually verify the migration against the real database**

Back up first, then let the app migrate it on startup:

```bash
cp job-seek.db job-seek.db.bak
.venv/bin/uvicorn app.main:app --port 8734 &
sleep 2
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8734/
sqlite3 job-seek.db "PRAGMA table_info(jobs);"
sqlite3 job-seek.db "SELECT job_id, scenario_id, relevance_score, scenario_version_hash FROM job_scores;"
pkill -f "uvicorn app.main:app --port 8734"
```

Expected: `jobs` no longer lists `relevance_score`/`score_reasoning`/`scenario_id` columns; `job_scores` has one row per previously-scored job with `scenario_version_hash = 'legacy'`.

- [ ] **Step 3: Manually verify the job list and re-evaluate in a browser**

Start the app, open `/`, confirm existing jobs still show a score badge and now also a scenario-name tag next to it. Open `/scenarios`, confirm the new "Re-evaluate all scenarios" button appears, and that clicking a single scenario's "Re-evaluate jobs" a second time in a row (without changing criteria) completes near-instantly and shows a "skipping N already current" message rather than re-calling the LLM.

- [ ] **Step 4: Commit if any fixups were needed during verification**

```bash
git add -A
git commit -m "fix: address issues found during end-to-end verification"
```

(Skip this step if verification found nothing to fix.)
