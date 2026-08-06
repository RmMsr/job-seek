# Two-Stage Scoring Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split job scoring into a scenario-only gate (stage 1, unchanged shape but profile-free) and a scenario-agnostic profile fit scorecard (stage 2, new: interest + attainability), replacing the boosted/best-match-selection mechanism that couldn't produce real discrimination between jobs.

**Architecture:** Stage 1 (`evaluate()`) scores every `(job, scenario)` pair on theme + must/prefer/avoid only; a job is visible once it clears at least one scenario's `gate_threshold`. Stage 2 (`assess_fit()`) runs once per job, only for gate-passed jobs, scoring profile-vs-job interest and attainability; their average (`fit_score`) drives the default sort. Both are plain OpenAI-compatible chat completions against a local model, matching the existing `evaluate()`/`classify()`/`summarize()` pattern.

**Tech Stack:** Python, FastAPI, SQLite (stdlib `sqlite3`), Jinja2 templates, HTMX, pytest.

## Global Constraints

- Hard-downtime migrations only — rebuild/ALTER in place, no dual-schema compatibility shims (per project CLAUDE.md).
- Commit after each task goes green (per project CLAUDE.md) — do not batch multiple tasks into one commit.
- No placeholders, no `TODO`s — every step below has complete, runnable code.
- This is a personal single-user app; migrations run synchronously in `init_db()`, same as all existing migrations in `app/db/schema.py`.

---

### Task 1: Profile version hash helper

**Files:**
- Modify: `app/scenario_version.py`
- Test: `tests/test_scenario_version.py`

**Interfaces:**
- Produces: `compute_profile_hash(profile: str) -> str` — used by Task 9 (pipeline stage-2 call) and Task 10 (`run_reassess_fit`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_scenario_version.py`:

```python
from app.scenario_version import compute_version_hash, compute_profile_hash


def test_profile_hash_is_deterministic():
    assert compute_profile_hash("Senior ML engineer") == compute_profile_hash("Senior ML engineer")


def test_profile_hash_changes_with_content():
    assert compute_profile_hash("A") != compute_profile_hash("B")


def test_profile_hash_handles_empty_string():
    assert compute_profile_hash("") == compute_profile_hash("")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_scenario_version.py -k profile_hash -v`
Expected: FAIL with `ImportError: cannot import name 'compute_profile_hash'`

- [ ] **Step 3: Implement**

In `app/scenario_version.py`, add below `compute_version_hash`:

```python
def compute_profile_hash(profile: str) -> str:
    return hashlib.sha256(profile.encode("utf-8")).hexdigest()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_scenario_version.py -v`
Expected: PASS (all tests, old and new)

- [ ] **Step 5: Commit**

```bash
git add app/scenario_version.py tests/test_scenario_version.py
git commit -m "feat: add compute_profile_hash for stage-2 staleness tracking"
```

---

### Task 2: Schema migration — `scenarios.boosted` → `gate_threshold`

**Files:**
- Modify: `app/db/schema.py`
- Test: `tests/test_schema.py`

**Interfaces:**
- Produces: `scenarios.gate_threshold REAL NOT NULL DEFAULT 0.7` column, replacing `boosted`. Consumed by Task 4 (`update_scenario`), Task 5 (gate join).

- [ ] **Step 1: Write the failing tests**

Replace `test_scenarios_table_has_boosted_column_not_active` and `test_init_db_migrates_scenarios_replaces_active_with_boosted` in `tests/test_schema.py` with:

```python
def test_scenarios_table_has_gate_threshold_column_not_boosted(conn):
    init_db(conn)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(scenarios)").fetchall()}
    assert "gate_threshold" in cols
    assert "boosted" not in cols
    assert "active" not in cols


def test_scenarios_gate_threshold_defaults_to_0_7(conn):
    init_db(conn)
    conn.execute("INSERT INTO scenarios (name) VALUES ('A')")
    row = conn.execute("SELECT gate_threshold FROM scenarios WHERE name = 'A'").fetchone()
    assert row["gate_threshold"] == pytest.approx(0.7)


def test_init_db_migrates_scenarios_replaces_boosted_with_gate_threshold(conn):
    conn.executescript(
        """
        CREATE TABLE scenarios (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            boosted INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    conn.execute("INSERT INTO scenarios (name, description, boosted) VALUES ('ai_expert', 'fallback', 1)")
    conn.commit()

    init_db(conn)

    row = conn.execute("SELECT name, description, gate_threshold FROM scenarios WHERE name = 'ai_expert'").fetchone()
    assert row["name"] == "ai_expert"
    assert row["description"] == "fallback"
    assert row["gate_threshold"] == pytest.approx(0.7)  # migration doesn't preserve the old boost as a threshold

    cols = {r[1] for r in conn.execute("PRAGMA table_info(scenarios)").fetchall()}
    assert "gate_threshold" in cols
    assert "boosted" not in cols

    # Idempotent: running init_db again doesn't error or duplicate columns.
    init_db(conn)
    cols_list = [r[1] for r in conn.execute("PRAGMA table_info(scenarios)").fetchall()]
    assert cols_list.count("gate_threshold") == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_schema.py -k "gate_threshold" -v`
Expected: FAIL — `gate_threshold` column doesn't exist yet.

- [ ] **Step 3: Implement**

In `app/db/schema.py`, change the `scenarios` table in `_DDL`:

```python
CREATE TABLE IF NOT EXISTS scenarios (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    gate_threshold REAL NOT NULL DEFAULT 0.7,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

Replace `_migrate_scenarios_boosted_flag` with:

```python
def _migrate_scenarios_gate_threshold(conn: sqlite3.Connection) -> None:
    # Replaces "boosted" (best-match tie-break bonus, now removed entirely)
    # with "gate_threshold" (per-scenario cutoff for stage-1 visibility) —
    # see two-stage-scoring-pipeline spec.
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='scenarios'"
    ).fetchone()
    if row is None or "gate_threshold" in row[0]:
        return
    conn.execute("ALTER TABLE scenarios DROP COLUMN boosted")
    conn.execute("ALTER TABLE scenarios ADD COLUMN gate_threshold REAL NOT NULL DEFAULT 0.7")
    conn.commit()
```

Update `init_db`:

```python
def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL)
    _migrate_sources_fetcher_type(conn)
    _migrate_jobs_scores_to_table(conn)
    _migrate_jobs_add_feedback_scenario_id(conn)
    _migrate_jobs_add_headline(conn)
    _migrate_jobs_add_published_at(conn)
    _migrate_jobs_add_feedback_handled_at(conn)
    _migrate_scenarios_gate_threshold(conn)
```

Note: this migration only runs against a pre-existing DB that still has `boosted` (not `active` — `active` was already migrated away in a prior release). The old `test_init_db_migrates_scenarios_replaces_active_with_boosted` test (which seeded an `active`-only table) is being replaced, not kept, because that migration path (`active` → `boosted`) is now dead: any real DB has already gone through it.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_schema.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add app/db/schema.py tests/test_schema.py
git commit -m "feat: replace scenarios.boosted with configurable gate_threshold"
```

---

### Task 3: Schema migration — `jobs` stage-2 columns

**Files:**
- Modify: `app/db/schema.py`
- Test: `tests/test_schema.py`

**Interfaces:**
- Produces: `jobs.interest_score`, `jobs.interest_reasoning`, `jobs.attainability_score`, `jobs.attainability_reasoning`, `jobs.fit_score`, `jobs.profile_version_hash` (all nullable). Consumed by Task 5 (sort), Task 6 (`update_job_fit`), Task 10 (`run_reassess_fit`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_schema.py`:

```python
def test_jobs_table_has_fit_scorecard_columns(conn):
    init_db(conn)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    for col in ("interest_score", "interest_reasoning", "attainability_score",
                "attainability_reasoning", "fit_score", "profile_version_hash"):
        assert col in cols


def test_init_db_migrates_jobs_adds_fit_scorecard_columns(conn):
    conn.executescript(
        """
        CREATE TABLE sources (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            fetcher_type TEXT NOT NULL CHECK(fetcher_type IN ('http', 'playwright', 'slack', 'finn_listing')),
            enabled INTEGER NOT NULL DEFAULT 1
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
            feedback_handled_at TEXT
        );
        """
    )
    conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s', 'http://x', 'http')")
    conn.execute("INSERT INTO jobs (source_id, url, title) VALUES (1, 'http://job/1', 'Existing Title')")
    conn.commit()

    init_db(conn)

    row = conn.execute(
        "SELECT title, interest_score, fit_score, profile_version_hash FROM jobs WHERE url = 'http://job/1'"
    ).fetchone()
    assert row["title"] == "Existing Title"
    assert row["interest_score"] is None
    assert row["fit_score"] is None
    assert row["profile_version_hash"] is None

    # Idempotent: running init_db again doesn't error or duplicate columns.
    init_db(conn)
    cols = [row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()]
    assert cols.count("interest_score") == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_schema.py -k fit_scorecard -v`
Expected: FAIL — columns don't exist yet.

- [ ] **Step 3: Implement**

In `app/db/schema.py`, add to the `jobs` table in `_DDL` (after `feedback_handled_at TEXT`):

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
```

Add a new migration function, right after `_migrate_jobs_add_feedback_handled_at`:

```python
def _migrate_jobs_add_fit_scorecard(conn: sqlite3.Connection) -> None:
    # Purely additive columns, same shape as the feedback_handled_at migration above.
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='jobs'"
    ).fetchone()
    if row is None or "interest_score" in row[0]:
        return
    conn.execute("ALTER TABLE jobs ADD COLUMN interest_score REAL")
    conn.execute("ALTER TABLE jobs ADD COLUMN interest_reasoning TEXT")
    conn.execute("ALTER TABLE jobs ADD COLUMN attainability_score REAL")
    conn.execute("ALTER TABLE jobs ADD COLUMN attainability_reasoning TEXT")
    conn.execute("ALTER TABLE jobs ADD COLUMN fit_score REAL")
    conn.execute("ALTER TABLE jobs ADD COLUMN profile_version_hash TEXT")
    conn.commit()
```

Register it in `init_db`, after `_migrate_jobs_add_feedback_handled_at(conn)` and before `_migrate_scenarios_gate_threshold(conn)`:

```python
def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL)
    _migrate_sources_fetcher_type(conn)
    _migrate_jobs_scores_to_table(conn)
    _migrate_jobs_add_feedback_scenario_id(conn)
    _migrate_jobs_add_headline(conn)
    _migrate_jobs_add_published_at(conn)
    _migrate_jobs_add_feedback_handled_at(conn)
    _migrate_jobs_add_fit_scorecard(conn)
    _migrate_scenarios_gate_threshold(conn)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_schema.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add app/db/schema.py tests/test_schema.py
git commit -m "feat: add jobs fit-scorecard columns for stage-2 scoring"
```

---

### Task 4: `update_scenario` gate_threshold + `reset_job` clears stage-2 fields

**Files:**
- Modify: `app/db/queries.py`
- Test: `tests/test_queries.py`

**Interfaces:**
- Consumes: `scenarios.gate_threshold` (Task 2), `jobs` fit columns (Task 3).
- Produces: `update_scenario(conn, scenario_id, name, description, gate_threshold=0.7)`. `reset_job` now also clears fit columns.

- [ ] **Step 1: Write the failing tests**

In `tests/test_queries.py`, replace `test_update_scenario_persists_boosted` and `test_update_scenario_boosted_defaults_false` with:

```python
def test_update_scenario_persists_gate_threshold(conn):
    sid = q.insert_scenario(conn, "ai_expert", "fallback")
    q.update_scenario(conn, sid, name="ai_expert", description="fallback", gate_threshold=0.5)
    scenario = {s["id"]: s for s in q.get_scenarios(conn)}[sid]
    assert scenario["gate_threshold"] == pytest.approx(0.5)


def test_update_scenario_gate_threshold_defaults_to_0_7(conn):
    sid = q.insert_scenario(conn, "A", "")
    q.update_scenario(conn, sid, name="A", description="")
    scenario = {s["id"]: s for s in q.get_scenarios(conn)}[sid]
    assert scenario["gate_threshold"] == pytest.approx(0.7)
```

Delete `test_boosted_scenario_wins_within_bonus_margin`, `test_specific_scenario_wins_when_it_clears_bonus_margin`, and `test_get_job_scores_returns_all_scenarios_ordered_by_raw_score` — these test the boost mechanism and `scenario_boosted` field, both removed in Task 5. (Task 5 adds their replacements.)

In `test_reset_job_clears_pipeline_output_and_scores`, add stage-2 setup and assertions:

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
    q.update_job_feedback(conn, jid, "accepted", "note", feedback_scenario_id=scenario_id)

    q.reset_job(conn, jid)

    job = q.get_job(conn, jid)
    assert job["status"] == "new"
    assert job["content_type"] is None
    assert job["simplified_content"] == ""
    assert job["summary"] == ""
    assert job["headline"] == ""
    assert job["feedback_note"] is None
    assert job["feedback_scenario_id"] is None
    assert job["raw_text"] == "r"
    assert job["interest_score"] is None
    assert job["fit_score"] is None
    assert job["profile_version_hash"] is None
    assert q.get_job_scores(conn, jid) == []
```

(This references `q.update_job_fit`, added in Task 6 — Task 4's tests will only fully pass once Task 6 lands. Since both are small and sequential, run Task 4's own new tests scoped by `-k` in Step 2/4 below to avoid a false failure from the not-yet-written `update_job_fit`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_queries.py -k "gate_threshold" -v`
Expected: FAIL with `TypeError: update_scenario() got an unexpected keyword argument 'gate_threshold'`

- [ ] **Step 3: Implement**

In `app/db/queries.py`, replace `update_scenario`:

```python
def update_scenario(
    conn: sqlite3.Connection, scenario_id: int, name: str, description: str, gate_threshold: float = 0.7
) -> None:
    conn.execute(
        "UPDATE scenarios SET name = ?, description = ?, gate_threshold = ? WHERE id = ?",
        (name, description, gate_threshold, scenario_id),
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
            feedback_scenario_id = NULL,
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
    conn.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_queries.py -k "gate_threshold" -v`
Expected: PASS

(Leave `test_reset_job_clears_pipeline_output_and_scores` red for now — it needs `update_job_fit` from Task 6. Do not commit it broken; stash that one edit mentally and re-verify at the end of Task 6.)

- [ ] **Step 5: Commit**

```bash
git add app/db/queries.py tests/test_queries.py
git commit -m "feat: replace boosted param with gate_threshold on update_scenario"
```

---

### Task 5: Replace best-match join with gate-passed join

**Files:**
- Modify: `app/db/queries.py`
- Modify: `tests/test_routes_scenarios.py` (one field-rename fix, unrelated to that file's main Task 11 changes)
- Test: `tests/test_queries.py`

**Interfaces:**
- Consumes: `scenarios.gate_threshold` (Task 2).
- Produces: `get_jobs(conn, *, status=None, content_type=None, gate_passed_only=False)`, `get_job(conn, job_id)` — both now select `passed_gate_count`, `passed_scenario_names`, `top_passed_scenario_id`, `top_passed_scenario_name` instead of `best_score`/`best_scenario_id`/`best_scenario_name`/`best_score_reasoning`. `get_job_scores` drops `scenario_boosted`, gains `scenario_gate_threshold`. Consumed by Task 9 (pipeline), Task 11/12 (routes+templates).

- [ ] **Step 1: Write the failing tests**

In `tests/test_queries.py`, replace `test_get_job_scores_returns_all_scenarios_ordered_by_raw_score` (deleted in Task 4) with:

```python
def test_get_job_scores_returns_all_scenarios_ordered_by_raw_score(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_a = q.insert_scenario(conn, "A", "")
    scenario_b = q.insert_scenario(conn, "B", "")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.upsert_job_score(conn, jid, scenario_a, 0.7, "a reasoning", "h1")
    q.upsert_job_score(conn, jid, scenario_b, 0.4, "b reasoning", "h2")

    scores = q.get_job_scores(conn, jid)

    assert [s["scenario_id"] for s in scores] == [scenario_a, scenario_b]  # raw score order
    assert scores[0]["scenario_name"] == "A"
    assert scores[0]["scenario_gate_threshold"] == pytest.approx(0.7)
    assert scores[0]["score_reasoning"] == "a reasoning"
    assert scores[1]["scenario_name"] == "B"
```

Replace `test_get_job_exposes_best_scenario_id`:

```python
def test_get_job_exposes_top_passed_scenario_id(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_a = q.insert_scenario(conn, "A", "")
    scenario_b = q.insert_scenario(conn, "B", "")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.upsert_job_score(conn, jid, scenario_a, 0.4, "ok", "h1")
    q.upsert_job_score(conn, jid, scenario_b, 0.8, "great", "h2")
    job = q.get_job(conn, jid)
    assert job["top_passed_scenario_id"] == scenario_b
    assert job["top_passed_scenario_name"] == "B"
```

Replace `test_get_jobs_shows_best_score_across_scenarios` and `test_get_job_shows_best_score`:

```python
def test_get_jobs_reports_all_passed_scenario_names(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_a = q.insert_scenario(conn, "A", "")
    scenario_b = q.insert_scenario(conn, "B", "")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="", content_type="job_posting")
    q.upsert_job_score(conn, jid, scenario_a, 0.8, "great fit", "hash1")
    q.upsert_job_score(conn, jid, scenario_b, 0.9, "even better", "hash2")
    job = q.get_jobs(conn)[0]
    assert job["passed_gate_count"] == 2
    assert set(job["passed_scenario_names"].split(", ")) == {"A", "B"}


def test_get_jobs_excludes_scenario_below_its_own_gate_threshold(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    strict = q.insert_scenario(conn, "Strict", "")
    q.update_scenario(conn, strict, name="Strict", description="", gate_threshold=0.9)
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.upsert_job_score(conn, jid, strict, 0.8, "close but no", "hash1")  # below 0.9
    job = q.get_jobs(conn)[0]
    assert job["passed_gate_count"] is None
```

Replace `test_get_jobs_orders_by_best_score_desc` and `test_get_jobs_job_with_no_score_has_none`:

```python
def test_get_jobs_gate_passed_only_excludes_below_threshold(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")  # default gate_threshold 0.7
    below = q.insert_job(conn, source_id=source_id, url="http://job/below", title="Below", company="C", raw_text="r")
    above = q.insert_job(conn, source_id=source_id, url="http://job/above", title="Above", company="C", raw_text="r")
    q.upsert_job_score(conn, below, scenario_id, 0.5, "", "hash1")
    q.upsert_job_score(conn, above, scenario_id, 0.9, "", "hash2")

    all_jobs = q.get_jobs(conn)
    assert {j["title"] for j in all_jobs} == {"Below", "Above"}

    passed_only = q.get_jobs(conn, gate_passed_only=True)
    assert [j["title"] for j in passed_only] == ["Above"]


def test_get_jobs_gate_passed_only_keeps_never_scored_jobs(conn):
    # A job with zero job_scores rows (e.g. no scenarios existed at fetch
    # time) hasn't failed a gate — it was never gated at all — so it must
    # stay visible, unlike a job that was scored and failed every scenario.
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")
    scored_and_failed = q.insert_job(conn, source_id=source_id, url="http://job/failed", title="Failed", company="C", raw_text="r")
    never_scored = q.insert_job(conn, source_id=source_id, url="http://job/unscored", title="Unscored", company="C", raw_text="r")
    q.upsert_job_score(conn, scored_and_failed, scenario_id, 0.5, "", "hash1")  # below default 0.7

    passed_only = q.get_jobs(conn, gate_passed_only=True)

    assert [j["title"] for j in passed_only] == ["Unscored"]


def test_get_jobs_job_with_no_score_has_no_passed_scenarios(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    job = q.get_jobs(conn)[0]
    assert job["passed_gate_count"] is None
    assert job["passed_scenario_names"] is None
    assert job["top_passed_scenario_id"] is None
```

Update `test_update_job_pipeline`: change `assert job["best_score"] is None` to `assert job["fit_score"] is None`.

Also fix `tests/test_routes_scenarios.py::test_reevaluate_streams_progress_and_updates_jobs` (fails once `best_score` is gone, independent of that file's gate_threshold renames handled in Task 11):

```python
def test_reevaluate_streams_progress_and_updates_jobs(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Must be remote", "must")
    source_id = q.insert_source(conn, "s", "http://x", "http")
    job_id = q.insert_job(conn, source_id=source_id, url="http://job/1", title="ML Eng", company="C", raw_text="r")
    q.update_job_pipeline(conn, job_id, simplified_content="clean", content_type="job_posting")

    with patch("app.pipeline.summarize", return_value=("ML Engineer - Remote @ Acme", "Great hook", "Updated summary")), \
         patch("app.pipeline.evaluate", return_value=(0.75, "Good match")):
        resp = client.post(f"/scenarios/{sid}/reevaluate")

    assert resp.status_code == 200
    assert "Re-evaluating 1 job(s)" in resp.text
    assert "Re-evaluation complete" in resp.text
    score = q.get_job_score(conn, job_id, sid)
    assert score["relevance_score"] == pytest.approx(0.75)
    job = q.get_job(conn, job_id)
    assert job["summary"] == "Updated summary"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_queries.py -k "passed_scenario or gate_passed or top_passed or job_with_no_score" -v`
Expected: FAIL — `passed_gate_count`/`top_passed_scenario_id`/etc. don't exist; `get_jobs` has no `gate_passed_only` param.

- [ ] **Step 3: Implement**

In `app/db/queries.py`, replace the `_BEST_SCORE_SELECT` / `BOOST_BONUS` / `_BEST_SCORE_JOIN` block (and `get_jobs`/`get_job`) with:

A job that has never been scored against anything (e.g. no scenarios existed at fetch time) must stay visible by default — only a job that *was* scored and failed every scenario's gate should be hidden. That needs a second join to distinguish "never scored" from "scored and failed," since `passed_count` alone can't tell them apart (both are `NULL`/0):

```python
_GATE_SELECT = """
    jobs.*,
    gate.passed_count AS passed_gate_count,
    gate.passed_scenario_names AS passed_scenario_names,
    gate.top_passed_scenario_id AS top_passed_scenario_id,
    gate.top_passed_scenario_name AS top_passed_scenario_name,
    scored.scored_count AS scored_gate_count,
    feedback_scenarios.name AS feedback_scenario_name
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
    LEFT JOIN scenarios AS feedback_scenarios ON feedback_scenarios.id = jobs.feedback_scenario_id
"""


def get_jobs(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    content_type: str | None = None,
    gate_passed_only: bool = False,
) -> list[dict]:
    clauses, params = [], []
    if status is not None:
        clauses.append("jobs.status = ?")
        params.append(status)
    if content_type is not None:
        clauses.append("jobs.content_type = ?")
        params.append(content_type)
    if gate_passed_only:
        clauses.append("(scored.scored_count IS NULL OR gate.passed_count > 0)")
    sql = f"SELECT {_GATE_SELECT} {_GATE_JOIN}"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY jobs.fit_score DESC NULLS LAST, jobs.fetched_at DESC"
    return _rows_to_dicts(conn.execute(sql, params).fetchall())


def get_job(conn: sqlite3.Connection, job_id: int) -> dict | None:
    sql = f"SELECT {_GATE_SELECT} {_GATE_JOIN} WHERE jobs.id = ?"
    return _row_to_dict(conn.execute(sql, (job_id,)).fetchone())
```

Update `get_job_scores` (drop `scenario_boosted`, add `scenario_gate_threshold`):

```python
def get_job_scores(conn: sqlite3.Connection, job_id: int) -> list[dict]:
    return _rows_to_dicts(
        conn.execute(
            """
            SELECT job_scores.*, scenarios.name AS scenario_name, scenarios.gate_threshold AS scenario_gate_threshold
            FROM job_scores
            JOIN scenarios ON scenarios.id = job_scores.scenario_id
            WHERE job_scores.job_id = ?
            ORDER BY job_scores.relevance_score DESC
            """,
            (job_id,),
        ).fetchall()
    )
```

`gate_passed_only=False` (the default) preserves today's behavior everywhere `get_jobs` is already called without it — no other call site needs to change in this task.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_queries.py tests/test_routes_scenarios.py -v`
Expected: PASS (all tests in both files, including the ones deferred from Task 4 now that `best_score`/`best_scenario_id`/`scenario_boosted` are fully gone — except `test_reset_job_clears_pipeline_output_and_scores`, still pending Task 6's `update_job_fit`)

- [ ] **Step 5: Commit**

```bash
git add app/db/queries.py tests/test_queries.py tests/test_routes_scenarios.py
git commit -m "feat: replace best-match join with gate-passed visibility join"
```

---

### Task 6: `update_job_fit`

**Files:**
- Modify: `app/db/queries.py`
- Test: `tests/test_queries.py`

**Interfaces:**
- Produces: `update_job_fit(conn, job_id, interest_score, interest_reasoning, attainability_score, attainability_reasoning, profile_version_hash)`. Consumed by Task 9 (pipeline), Task 10 (`run_reassess_fit`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_queries.py`:

```python
def test_update_job_fit_sets_scores_and_computed_fit_score(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_fit(conn, jid, 0.8, "Strong domain fit", 0.6, "Slightly junior", "phash1")
    job = q.get_job(conn, jid)
    assert job["interest_score"] == pytest.approx(0.8)
    assert job["interest_reasoning"] == "Strong domain fit"
    assert job["attainability_score"] == pytest.approx(0.6)
    assert job["attainability_reasoning"] == "Slightly junior"
    assert job["fit_score"] == pytest.approx(0.7)  # average
    assert job["profile_version_hash"] == "phash1"


def test_get_jobs_orders_by_fit_score_desc(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    low = q.insert_job(conn, source_id=source_id, url="http://job/low", title="Low", company="C", raw_text="r")
    high = q.insert_job(conn, source_id=source_id, url="http://job/high", title="High", company="C", raw_text="r")
    q.update_job_fit(conn, low, 0.2, "", 0.2, "", "h")
    q.update_job_fit(conn, high, 0.9, "", 0.9, "", "h")
    jobs = q.get_jobs(conn)
    assert [j["title"] for j in jobs] == ["High", "Low"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_queries.py -k "update_job_fit or orders_by_fit_score" -v`
Expected: FAIL with `AttributeError: module 'app.db.queries' has no attribute 'update_job_fit'`

- [ ] **Step 3: Implement**

In `app/db/queries.py`, add near `upsert_job_score`:

```python
def update_job_fit(
    conn: sqlite3.Connection,
    job_id: int,
    interest_score: float,
    interest_reasoning: str,
    attainability_score: float,
    attainability_reasoning: str,
    profile_version_hash: str,
) -> None:
    fit_score = (interest_score + attainability_score) / 2
    conn.execute(
        """UPDATE jobs SET
            interest_score = ?,
            interest_reasoning = ?,
            attainability_score = ?,
            attainability_reasoning = ?,
            fit_score = ?,
            profile_version_hash = ?
        WHERE id = ?""",
        (interest_score, interest_reasoning, attainability_score, attainability_reasoning, fit_score, profile_version_hash, job_id),
    )
    conn.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_queries.py -v`
Expected: PASS (all tests, including the previously-deferred `test_reset_job_clears_pipeline_output_and_scores` from Task 4)

- [ ] **Step 5: Commit**

```bash
git add app/db/queries.py tests/test_queries.py
git commit -m "feat: add update_job_fit for stage-2 scorecard persistence"
```

---

### Task 7: `evaluate()` drops profile, trims prompt to a pure gate

**Files:**
- Modify: `app/ai/evaluate.py`
- Test: `tests/test_evaluate.py`

**Interfaces:**
- Produces: `evaluate(client, model, scenario, criteria, summary) -> tuple[float, str]` (was: `evaluate(client, model, profile, scenario, criteria, summary)`). Consumed by Task 9 (pipeline).

- [ ] **Step 1: Write the failing tests**

Replace `tests/test_evaluate.py` in full:

```python
import pytest
from unittest.mock import MagicMock
from app.ai.evaluate import evaluate


def _mock_client(response_text: str) -> MagicMock:
    client = MagicMock()
    choice = MagicMock()
    choice.message.content = response_text
    client.chat.completions.create.return_value = MagicMock(choices=[choice])
    return client


_SCENARIO = {"name": "Remote ML", "description": "Looking for remote ML roles"}
_CRITERIA = [
    {"text": "Must be remote", "weight": "must"},
    {"text": "Prefer Python", "weight": "prefer"},
]


def test_evaluate_returns_score_and_reasoning():
    client = _mock_client('{"score": 0.85, "reasoning": "Matches remote and Python criteria"}')
    score, reasoning = evaluate(client, "llama3.2", _SCENARIO, _CRITERIA, "Good role summary")
    assert score == pytest.approx(0.85)
    assert "remote" in reasoning.lower() or "Python" in reasoning


def test_evaluate_clamps_score():
    client = _mock_client('{"score": 1.5, "reasoning": "Perfect"}')
    score, _ = evaluate(client, "llama3.2", _SCENARIO, _CRITERIA, "summary")
    assert 0.0 <= score <= 1.0


def test_evaluate_invalid_json_returns_zero():
    client = _mock_client("not json")
    score, reasoning = evaluate(client, "llama3.2", _SCENARIO, _CRITERIA, "summary")
    assert score == 0.0
    assert "error" in reasoning.lower()


def test_evaluate_strips_markdown_code_fence():
    client = _mock_client('```json\n{"score": 0.6, "reasoning": "Decent match"}\n```')
    score, reasoning = evaluate(client, "llama3.2", _SCENARIO, _CRITERIA, "summary")
    assert score == pytest.approx(0.6)
    assert reasoning == "Decent match"


def test_evaluate_disables_model_thinking():
    client = _mock_client('{"score": 0.6, "reasoning": "Decent match"}')
    evaluate(client, "llama3.2", _SCENARIO, _CRITERIA, "summary")
    call_args = client.chat.completions.create.call_args
    assert call_args.kwargs["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}


def test_evaluate_does_not_mention_profile_in_prompt():
    # This call is a pure topical gate now — no profile/candidate-fit language
    # should leak into the system prompt.
    client = _mock_client('{"score": 0.6, "reasoning": "Decent match"}')
    evaluate(client, "llama3.2", _SCENARIO, _CRITERIA, "summary")
    system_content = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert "profile" not in system_content.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_evaluate.py -v`
Expected: FAIL — `evaluate()` still requires a `profile` positional argument (`TypeError`), and the prompt still mentions "profile".

- [ ] **Step 3: Implement**

Replace `app/ai/evaluate.py` in full:

```python
from __future__ import annotations
import json
import openai
from app.ai.json_utils import extract_json

_SYSTEM = """You screen whether a job posting is even in the right search space for a scenario.
Given a search scenario and a job summary, return a relevance score from 0.0 to 1.0 and a brief
reasoning — this is a topical gate, not a judgment of whether the candidate should take the job.

Weigh these signals in priority order, each one narrowing the one before it:

1. Scenario name — the single strongest signal of what this search is about. A job that doesn't
   fit the name's theme at all should score low no matter what else matches.
2. Scenario description — elaborates and refines the name's theme. Use it to interpret borderline
   cases, not to override a job that clearly does or doesn't match the name.
3. 'must' criteria — hard qualifiers, not fine-tuning: a job missing a 'must' criterion should
   score low even if it fits the scenario's theme well.
4. 'prefer' / 'avoid' criteria — fine-tune the score within everything above. A missing 'prefer'
   or a triggered 'avoid' should nudge the score, not sink or save it on their own.

Respond with exactly: {"score": <float>, "reasoning": "<2-3 sentences>"}"""


def evaluate(
    client: openai.OpenAI,
    model: str,
    scenario: dict,
    criteria: list[dict],
    summary: str,
) -> tuple[float, str]:
    criteria_text = "\n".join(
        f"[{c['weight'].upper()}] {c['text']}" for c in criteria
    )
    user_content = (
        f"## Search Scenario: {scenario['name']}\n{scenario.get('description', '')}\n\n"
        f"## Criteria\n{criteria_text}\n\n"
        f"## Job Summary\n{summary}"
    )
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user_content[:8000]},
            ],
            temperature=0,
            # This model emits a hidden chain-of-thought by default, which is slow and,
            # per A/B testing against real postings, sometimes runs long enough to exhaust
            # the response budget before ever emitting a score. Disabling it was faster
            # and at least as reliable/accurate for this text-transformation task.
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        data = json.loads(extract_json(resp.choices[0].message.content))
        score = max(0.0, min(1.0, float(data.get("score", 0.0))))
        return score, data.get("reasoning", "")
    except Exception as exc:
        return 0.0, f"error: {exc}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_evaluate.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/ai/evaluate.py tests/test_evaluate.py
git commit -m "feat: strip profile from evaluate(), reduce to a pure scenario gate"
```

---

### Task 8: New `assess_fit()` stage-2 module

**Files:**
- Create: `app/ai/assess_fit.py`
- Test: `tests/test_assess_fit.py`

**Interfaces:**
- Produces: `assess_fit(client, model, profile, summary) -> dict` with keys `interest`, `interest_reasoning`, `attainability`, `attainability_reasoning`. Consumed by Task 9 (pipeline), Task 10 (`run_reassess_fit`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_assess_fit.py`:

```python
import pytest
from unittest.mock import MagicMock
from app.ai.assess_fit import assess_fit


def _mock_client(response_text: str) -> MagicMock:
    client = MagicMock()
    choice = MagicMock()
    choice.message.content = response_text
    client.chat.completions.create.return_value = MagicMock(choices=[choice])
    return client


_PROFILE = "Senior ML engineer, 8 years, interested in applied research roles at mission-driven startups."


def test_assess_fit_returns_both_scores_and_reasonings():
    client = _mock_client(
        '{"interest": 0.85, "interest_reasoning": "Strong domain match", '
        '"attainability": 0.6, "attainability_reasoning": "Slightly more senior than typical hires"}'
    )
    result = assess_fit(client, "llama3.2", _PROFILE, "Good role summary")
    assert result["interest"] == pytest.approx(0.85)
    assert result["interest_reasoning"] == "Strong domain match"
    assert result["attainability"] == pytest.approx(0.6)
    assert result["attainability_reasoning"] == "Slightly more senior than typical hires"


def test_assess_fit_clamps_scores():
    client = _mock_client(
        '{"interest": 1.5, "interest_reasoning": "x", "attainability": -0.2, "attainability_reasoning": "y"}'
    )
    result = assess_fit(client, "llama3.2", _PROFILE, "summary")
    assert 0.0 <= result["interest"] <= 1.0
    assert 0.0 <= result["attainability"] <= 1.0


def test_assess_fit_invalid_json_returns_zeros_with_error():
    client = _mock_client("not json")
    result = assess_fit(client, "llama3.2", _PROFILE, "summary")
    assert result["interest"] == 0.0
    assert result["attainability"] == 0.0
    assert "error" in result["interest_reasoning"].lower()
    assert "error" in result["attainability_reasoning"].lower()


def test_assess_fit_strips_markdown_code_fence():
    client = _mock_client(
        '```json\n{"interest": 0.5, "interest_reasoning": "a", "attainability": 0.5, "attainability_reasoning": "b"}\n```'
    )
    result = assess_fit(client, "llama3.2", _PROFILE, "summary")
    assert result["interest"] == pytest.approx(0.5)
    assert result["interest_reasoning"] == "a"


def test_assess_fit_disables_model_thinking():
    client = _mock_client(
        '{"interest": 0.5, "interest_reasoning": "a", "attainability": 0.5, "attainability_reasoning": "b"}'
    )
    assess_fit(client, "llama3.2", _PROFILE, "summary")
    call_args = client.chat.completions.create.call_args
    assert call_args.kwargs["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_assess_fit.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.ai.assess_fit'`

- [ ] **Step 3: Implement**

Create `app/ai/assess_fit.py`:

```python
from __future__ import annotations
import json
import openai
from app.ai.json_utils import extract_json

_SYSTEM = """You assess how well a job posting fits a candidate's profile and career stage.
Given a candidate profile and a job summary, return two independent 0.0-1.0 scores.

1. Interest — how well the role aligns with the candidate's domain/mission interests, the
   company/team's stage and culture, and where this sits on the candidate's career trajectory
   (a stretch role, a lateral move, a dead end). Score high only for a role that stands out on
   these dimensions, not merely one that doesn't clash with them — most roles that merely don't
   conflict with the profile should score in the middle or below, not high.
2. Attainability — how realistic landing this role is, given the candidate's actual experience,
   skills, and seniority against what the posting asks for. Its reasoning must name concrete
   gaps (e.g. missing years of experience, a skill the posting requires that the profile doesn't
   show) whenever the score is below 1.0, not vague hedging.

Respond with exactly: {"interest": <float>, "interest_reasoning": "<1-2 sentences>",
"attainability": <float>, "attainability_reasoning": "<1-2 sentences>"}"""


def assess_fit(
    client: openai.OpenAI,
    model: str,
    profile: str,
    summary: str,
) -> dict:
    user_content = f"## Candidate Profile\n{profile}\n\n## Job Summary\n{summary}"
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user_content[:8000]},
            ],
            temperature=0,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        data = json.loads(extract_json(resp.choices[0].message.content))
        interest = max(0.0, min(1.0, float(data.get("interest", 0.0))))
        attainability = max(0.0, min(1.0, float(data.get("attainability", 0.0))))
        return {
            "interest": interest,
            "interest_reasoning": data.get("interest_reasoning", ""),
            "attainability": attainability,
            "attainability_reasoning": data.get("attainability_reasoning", ""),
        }
    except Exception as exc:
        return {
            "interest": 0.0,
            "interest_reasoning": f"error: {exc}",
            "attainability": 0.0,
            "attainability_reasoning": f"error: {exc}",
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_assess_fit.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/ai/assess_fit.py tests/test_assess_fit.py
git commit -m "feat: add assess_fit() for stage-2 profile fit scoring"
```

---

### Task 9: Wire stage 1 + stage 2 into `run_fetch`/`run_reevaluate`

**Files:**
- Modify: `app/pipeline.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `evaluate()` (Task 7, new signature), `assess_fit()` (Task 8), `q.update_job_fit` (Task 6), `compute_profile_hash` (Task 1).
- Produces: `_ingest_posting` now calls `assess_fit` once per job when any scenario passes its gate. `run_reevaluate` no longer fetches/passes `profile` to `evaluate()`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_pipeline.py`, update the `_mock_client` helper to add a 4th, defaulted response for the new stage-2 call, and switch the evaluate-dispatch keyword to match the trimmed prompt from Task 7:

```python
def _mock_client(classify_resp, summarize_resp, evaluate_resp,
                  assess_fit_resp='{"interest": 0.8, "interest_reasoning": "Good fit", '
                                  '"attainability": 0.7, "attainability_reasoning": "Close match"}'):
    client = MagicMock()
    def create(**kwargs):
        system = kwargs["messages"][0]["content"].lower()
        choice = MagicMock()
        if "you classify" in system:
            choice.message.content = classify_resp
        elif "you screen" in system:
            choice.message.content = evaluate_resp
        elif "you assess" in system:
            choice.message.content = assess_fit_resp
        else:
            choice.message.content = summarize_resp
        return MagicMock(choices=[choice])
    client.chat.completions.create.side_effect = create
    return client
```

Fix `test_run_fetch_with_no_scenarios_still_summarizes` (field rename):

```python
def test_run_fetch_with_no_scenarios_still_summarizes(conn):
    source_id = q.insert_source(conn, "test", "http://example.com", "http")
    source_dict = q.get_source(conn, source_id)
    raw_jobs = [RawJob(url="http://example.com/job/1", title="ML Eng", company="Acme", raw_text="<p>We are hiring</p>")]
    client = _mock_client(
        '{"type": "job_posting", "reason": "full description"}',
        '{"title": "ML Engineer - Remote @ Acme", "headline": "Great remote ML role", "summary": "Good ML role"}',
        '{"score": 0.9, "reasoning": "Great match"}',
    )

    with patch("app.pipeline.HttpFetcher") as MockFetcher:
        MockFetcher.return_value.fetch.return_value = raw_jobs
        messages, result = _drain(run_fetch(source_dict, conn, client, "llama3.2", "browser-profile"))

    job = q.get_jobs(conn)[0]
    assert job["content_type"] == "job_posting"
    assert job["summary"] == "Good ML role"
    assert job["fit_score"] is None
    assert not any("Scored" in m for m in messages)
```

Add new tests covering stage-2 wiring:

```python
def test_run_fetch_runs_stage_two_once_when_gate_passes(conn, source):
    raw_jobs = [RawJob(url="http://example.com/job/1", title="ML Eng", company="Acme", raw_text="<p>hi</p>")]
    client = _mock_client(
        '{"type": "job_posting", "reason": "full description"}',
        '{"title": "ML Engineer - Remote @ Acme", "headline": "Great remote ML role", "summary": "Good ML role"}',
        '{"score": 0.9, "reasoning": "Great match"}',  # 0.9 >= default gate_threshold 0.7
    )

    with patch("app.pipeline.HttpFetcher") as MockFetcher:
        MockFetcher.return_value.fetch.return_value = raw_jobs
        messages, _ = _drain(run_fetch(source, conn, client, "llama3.2", "browser-profile"))

    job = q.get_jobs(conn)[0]
    assert job["interest_score"] == pytest.approx(0.8)
    assert job["attainability_score"] == pytest.approx(0.7)
    assert job["fit_score"] == pytest.approx(0.75)
    assert job["profile_version_hash"]
    assert sum("Fit" in m for m in messages) == 1


def test_run_fetch_skips_stage_two_when_gate_not_passed(conn, source):
    q.update_scenario(conn, q.get_scenarios(conn)[0]["id"], name="Remote ML", description="", gate_threshold=0.95)
    raw_jobs = [RawJob(url="http://example.com/job/1", title="ML Eng", company="Acme", raw_text="<p>hi</p>")]
    client = _mock_client(
        '{"type": "job_posting", "reason": "full description"}',
        '{"title": "ML Engineer - Remote @ Acme", "headline": "Great remote ML role", "summary": "Good ML role"}',
        '{"score": 0.9, "reasoning": "Great match"}',  # 0.9 < 0.95, gate fails
    )

    with patch("app.pipeline.HttpFetcher") as MockFetcher:
        MockFetcher.return_value.fetch.return_value = raw_jobs
        messages, _ = _drain(run_fetch(source, conn, client, "llama3.2", "browser-profile"))

    job = q.get_jobs(conn)[0]
    assert job["fit_score"] is None
    assert not any("Fit" in m for m in messages)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_pipeline.py -k "stage_two" -v`
Expected: FAIL — stage 2 isn't wired up yet, `fit_score` stays `None` in both cases.

- [ ] **Step 3: Implement**

In `app/pipeline.py`, add imports:

```python
from app.ai.evaluate import evaluate
from app.ai.assess_fit import assess_fit
from app.scenario_version import compute_version_hash, compute_profile_hash
```

Replace `_ingest_posting`:

```python
def _ingest_posting(
    conn: sqlite3.Connection,
    client: openai.OpenAI,
    model: str,
    job_id: int,
    raw_text: str,
    fallback_title: str,
    is_slack: bool,
    profile: str,
    scenarios: list[dict],
    *,
    url: str,
    progress_prefix: str = "",
) -> Generator[str, None, None]:
    simplified = simplify(raw_text)
    content_type, _ = classify(client, model, simplified, is_slack=is_slack)
    yield _progress(f"{progress_prefix}Classified as {content_type}: {url}")

    if content_type in ("job_posting", "lead"):
        ai_title, headline, job_summary = summarize(client, model, simplified)
        q.update_job_pipeline(
            conn, job_id,
            simplified_content=simplified,
            content_type=content_type,
            title=ai_title or fallback_title,
            headline=headline,
            summary=job_summary,
        )
        passed_gate = False
        for scenario in scenarios:
            criteria = q.get_criteria(conn, scenario["id"])
            score, reasoning = evaluate(client, model, scenario, criteria, job_summary)
            version_hash = compute_version_hash(scenario, criteria)
            q.upsert_job_score(conn, job_id, scenario["id"], score, reasoning, version_hash)
            if score >= scenario["gate_threshold"]:
                passed_gate = True
            yield _progress(f"{progress_prefix}Scored {score} for '{scenario['name']}': {url}")
        if passed_gate:
            result = assess_fit(client, model, profile, job_summary)
            q.update_job_fit(
                conn, job_id,
                result["interest"], result["interest_reasoning"],
                result["attainability"], result["attainability_reasoning"],
                compute_profile_hash(profile),
            )
            yield _progress(
                f"{progress_prefix}Fit {result['interest']:.2f}/{result['attainability']:.2f}: {url}"
            )
    else:
        q.update_job_pipeline(conn, job_id, simplified_content=simplified, content_type=content_type)
```

In `run_reevaluate`, remove the now-unused profile fetch and pass-through (stage 1 no longer needs it):

```python
def run_reevaluate(
    conn: sqlite3.Connection,
    client: openai.OpenAI,
    model: str,
    scenario: dict,
    *,
    job_offset: int = 0,
    job_total: int | None = None,
    scenario_label: str = "",
) -> Generator[str, None, int]:
    to_evaluate, skipped, criteria, current_hash = _eligible_for_reevaluation(conn, scenario)
    total = job_total if job_total is not None else len(to_evaluate)

    msg = f"Re-evaluating {len(to_evaluate)} job(s) for scenario '{scenario['name']}'"
    if skipped:
        msg += f", skipping {skipped} already current"
    yield _progress(scenario_label + msg)

    for i, job in enumerate(to_evaluate, start=1):
        if job["simplified_content"]:
            ai_title, headline, new_summary = summarize(client, model, job["simplified_content"])
        else:
            ai_title, headline, new_summary = job["title"], job["headline"], job["summary"]
        score, reasoning = evaluate(client, model, scenario, criteria, new_summary)
        q.update_job_pipeline(
            conn, job["id"],
            simplified_content=job["simplified_content"],
            content_type=job["content_type"],
            title=ai_title or job["title"],
            headline=headline,
            summary=new_summary,
        )
        q.upsert_job_score(conn, job["id"], scenario["id"], score, reasoning, current_hash)
        yield _progress(f"{scenario_label}[{job_offset + i}/{total}] Re-scored {score}: {job['title'] or job['url']}")

    yield _progress(f"{scenario_label}Re-evaluation complete for '{scenario['name']}': {len(to_evaluate)} job(s) updated")
    return len(to_evaluate)
```

(`run_fetch`, `run_reprocess_job`, `_eligible_for_reevaluation` are unchanged — `run_fetch` already fetches `profile` and passes it to `_ingest_posting`, which is now where it's used.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_pipeline.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add app/pipeline.py tests/test_pipeline.py
git commit -m "feat: wire stage-2 fit assessment into the fetch pipeline"
```

---

### Task 10: `run_reassess_fit` — explicit profile-only re-eval

**Files:**
- Modify: `app/pipeline.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `q.get_jobs(gate_passed_only=True)` (Task 5), `assess_fit()` (Task 8), `compute_profile_hash` (Task 1).
- Produces: `run_reassess_fit(conn, client, model) -> Generator[str, None, int]`. Consumed by Task 13 (profile route).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pipeline.py`:

```python
from app.pipeline import run_reassess_fit


def test_run_reassess_fit_updates_gate_passed_jobs(conn, source):
    scenario_id = q.get_scenarios(conn)[0]["id"]
    jid = q.insert_job(conn, source_id=source["id"], url="http://example.com/job/1", title="T", company="C", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="job_posting", summary="Good role")
    q.upsert_job_score(conn, jid, scenario_id, 0.9, "great", "hash1")  # passes default 0.7 gate

    client = MagicMock()
    choice = MagicMock()
    choice.message.content = '{"interest": 0.8, "interest_reasoning": "a", "attainability": 0.6, "attainability_reasoning": "b"}'
    client.chat.completions.create.return_value = MagicMock(choices=[choice])

    messages, updated_count = _drain(run_reassess_fit(conn, client, "llama3.2"))

    assert updated_count == 1
    job = q.get_job(conn, jid)
    assert job["interest_score"] == pytest.approx(0.8)
    assert job["fit_score"] == pytest.approx(0.7)
    assert any("Recomputing fit scores for 1 job" in m for m in messages)


def test_run_reassess_fit_skips_jobs_below_gate(conn, source):
    scenario_id = q.get_scenarios(conn)[0]["id"]
    jid = q.insert_job(conn, source_id=source["id"], url="http://example.com/job/1", title="T", company="C", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="job_posting", summary="Good role")
    q.upsert_job_score(conn, jid, scenario_id, 0.3, "weak", "hash1")  # below default 0.7 gate

    client = MagicMock()
    _drain(run_reassess_fit(conn, client, "llama3.2"))

    assert client.chat.completions.create.call_count == 0
    assert q.get_job(conn, jid)["fit_score"] is None


def test_run_reassess_fit_skips_already_current_profile_hash(conn, source):
    scenario_id = q.get_scenarios(conn)[0]["id"]
    jid = q.insert_job(conn, source_id=source["id"], url="http://example.com/job/1", title="T", company="C", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="job_posting", summary="Good role")
    q.upsert_job_score(conn, jid, scenario_id, 0.9, "great", "hash1")

    client = MagicMock()
    choice = MagicMock()
    choice.message.content = '{"interest": 0.8, "interest_reasoning": "a", "attainability": 0.6, "attainability_reasoning": "b"}'
    client.chat.completions.create.return_value = MagicMock(choices=[choice])

    _drain(run_reassess_fit(conn, client, "llama3.2"))  # first pass: assesses
    messages, updated_count = _drain(run_reassess_fit(conn, client, "llama3.2"))  # second pass: profile unchanged

    assert updated_count == 0
    assert "skipping 1 already current" in "".join(messages)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_pipeline.py -k reassess_fit -v`
Expected: FAIL with `ImportError: cannot import name 'run_reassess_fit'`

- [ ] **Step 3: Implement**

In `app/pipeline.py`, add after `run_reevaluate`:

```python
def run_reassess_fit(
    conn: sqlite3.Connection,
    client: openai.OpenAI,
    model: str,
) -> Generator[str, None, int]:
    profile = q.get_profile(conn)
    current_hash = compute_profile_hash(profile)
    eligible = [
        j for j in q.get_jobs(conn, status="new", gate_passed_only=True)
        if j["content_type"] in ("job_posting", "lead")
    ]
    to_assess = [j for j in eligible if j["profile_version_hash"] != current_hash]
    skipped = len(eligible) - len(to_assess)

    msg = f"Recomputing fit scores for {len(to_assess)} job(s)"
    if skipped:
        msg += f", skipping {skipped} already current"
    yield _progress(msg)

    for i, job in enumerate(to_assess, start=1):
        result = assess_fit(client, model, profile, job["summary"])
        q.update_job_fit(
            conn, job["id"],
            result["interest"], result["interest_reasoning"],
            result["attainability"], result["attainability_reasoning"],
            current_hash,
        )
        yield _progress(
            f"[{i}/{len(to_assess)}] Fit {result['interest']:.2f}/{result['attainability']:.2f}: "
            f"{job['title'] or job['url']}"
        )

    yield _progress(f"Fit recompute complete: {len(to_assess)} job(s) updated")
    return len(to_assess)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_pipeline.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add app/pipeline.py tests/test_pipeline.py
git commit -m "feat: add run_reassess_fit for explicit profile-only stage-2 refresh"
```

---

### Task 11: Scenario route + templates — gate_threshold

**Files:**
- Modify: `app/routes/scenarios.py`
- Modify: `app/templates/scenarios/_header.html`, `app/templates/scenarios/_header_edit.html`
- Test: `tests/test_routes_scenarios.py`

**Interfaces:**
- Consumes: `q.update_scenario(..., gate_threshold=...)` (Task 4).

- [ ] **Step 1: Write the failing tests**

In `tests/test_routes_scenarios.py`, replace the six `boosted`-related tests:

```python
def test_update_scenario_route_persists_gate_threshold(client, conn):
    sid = q.insert_scenario(conn, "ai_expert", "fallback")
    resp = client.post(
        f"/scenarios/{sid}",
        data={"name": "ai_expert", "description": "fallback", "gate_threshold": "0.5"},
    )
    assert resp.status_code == 200
    scenario = {s["id"]: s for s in q.get_scenarios(conn)}[sid]
    assert scenario["gate_threshold"] == pytest.approx(0.5)


def test_update_scenario_route_gate_threshold_defaults_to_0_7_when_omitted(client, conn):
    sid = q.insert_scenario(conn, "ai_expert", "fallback")
    resp = client.post(f"/scenarios/{sid}", data={"name": "ai_expert", "description": "fallback"})
    assert resp.status_code == 200
    scenario = {s["id"]: s for s in q.get_scenarios(conn)}[sid]
    assert scenario["gate_threshold"] == pytest.approx(0.7)


def test_edit_scenario_form_shows_current_gate_threshold(client, conn):
    sid = q.insert_scenario(conn, "ai_expert", "fallback")
    q.update_scenario(conn, sid, name="ai_expert", description="fallback", gate_threshold=0.55)
    resp = client.get(f"/scenarios/{sid}/edit")
    assert resp.status_code == 200
    assert 'value="0.55"' in resp.text


def test_scenario_header_shows_gate_threshold(client, conn):
    sid = q.insert_scenario(conn, "ai_expert", "fallback")
    resp = client.get(f"/scenarios/{sid}")
    assert resp.status_code == 200
    assert "gate: 70%" in resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_routes_scenarios.py -k gate_threshold -v`
Expected: FAIL — route still expects `boosted`, templates still render the boosted checkbox/tag.

- [ ] **Step 3: Implement**

In `app/routes/scenarios.py`, replace `update_scenario`:

```python
@router.post("/scenarios/{scenario_id}", response_class=HTMLResponse)
def update_scenario(
    scenario_id: int,
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    gate_threshold: float = Form(0.7),
    conn: sqlite3.Connection = Depends(get_db),
):
    _get_scenario_or_404(conn, scenario_id)
    q.update_scenario(conn, scenario_id, name=name, description=description, gate_threshold=gate_threshold)
    scenario = q.get_scenario(conn, scenario_id)
    return templates.TemplateResponse(request, "scenarios/_header.html", {"scenario": scenario})
```

Remove the now-unused `Optional` import if nothing else in the file uses it (check with `grep -n "Optional" app/routes/scenarios.py` — if only this one usage, drop `from typing import Optional`).

Replace `app/templates/scenarios/_header_edit.html`:

```html
<div id="scenario-header-{{ scenario.id }}">
  <form hx-post="/scenarios/{{ scenario.id }}"
        hx-target="#scenario-header-{{ scenario.id }}"
        hx-swap="outerHTML"
        style="display:flex; gap:0.5rem; flex-wrap:wrap; align-items:center;">
    <input type="text" name="name" value="{{ scenario.name }}" required>
    <input type="text" name="description" value="{{ scenario.description }}" style="flex:1; min-width:200px;">
    <label style="display:flex; align-items:center; gap:0.3rem; font-size:0.9em;"
           title="Jobs need at least this raw gate score, from this scenario, to show up in your list">
      Gate threshold:
      <input type="number" name="gate_threshold" value="{{ scenario.gate_threshold }}" min="0" max="1" step="0.05" style="width:5em;">
    </label>
    <button type="submit" class="btn">Save</button>
    <button type="button" class="btn"
      hx-get="/scenarios/{{ scenario.id }}"
      hx-target="#scenario-header-{{ scenario.id }}"
      hx-swap="outerHTML">Cancel</button>
  </form>
</div>
```

Replace `app/templates/scenarios/_header.html`:

```html
<div id="scenario-header-{{ scenario.id }}">
  <div style="display:flex; align-items:center; gap:0.75rem; flex-wrap:wrap;">
    <strong>{{ scenario.name }}</strong>
    <span class="tag" title="Jobs need at least this raw gate score to appear in your list">gate: {{ "%.0f"|format(scenario.gate_threshold * 100) }}%</span>
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
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add app/routes/scenarios.py app/templates/scenarios/_header.html app/templates/scenarios/_header_edit.html tests/test_routes_scenarios.py
git commit -m "feat: expose gate_threshold in scenario edit/display UI"
```

---

### Task 12: Jobs routes + templates — gate visibility, fit scorecard

**Files:**
- Modify: `app/routes/jobs.py`
- Modify: `app/templates/jobs/_macros.html`, `app/templates/jobs/_feedback.html`, `app/templates/jobs/_content.html`, `app/templates/jobs/list.html`, `app/templates/base.html`
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: `q.get_jobs(gate_passed_only=...)`, `top_passed_scenario_id`, `passed_scenario_names`, `fit_score` (Task 5, 6).

- [ ] **Step 1: Write the failing tests**

In `tests/test_routes_jobs.py`, update `_seed` to also assess stage 2 so existing fit-related assertions have data, and fix every test that references removed fields (`best_scenario_id`, `job.best_score` display strings, `boosted`):

```python
def _seed(conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "http")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/1", title="ML Eng", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="job_posting", summary="Great role")
    scenario_id = q.insert_scenario(conn, "Remote ML", "")
    q.upsert_job_score(conn, jid, scenario_id, 0.9, "Good match", "hash1")
    q.update_job_fit(conn, jid, 0.8, "Strong domain fit", 0.7, "Close match", "phash1")
    return sid, jid, scenario_id
```

Replace these tests (all currently reference `best_scenario_id` or a `best_score`-derived "90%" from `_seed`'s gate score, which is now the *stage-1* score, not the displayed fit badge — the displayed badge is `fit_score`, seeded at `(0.8+0.7)/2 = 0.75` → "75%"):

```python
def test_job_feedback_can_target_non_default_scenario(client, conn):
    sid, jid, top_scenario_id = _seed(conn)
    other_scenario_id = q.insert_scenario(conn, "Other Scenario", "")
    resp = client.post(
        f"/jobs/{jid}/feedback",
        data={"status": "rejected", "note": "not a fit here", "feedback_scenario_id": other_scenario_id},
    )
    assert resp.status_code == 200
    job = q.get_job(conn, jid)
    assert job["feedback_scenario_id"] == other_scenario_id
    assert job["feedback_scenario_id"] != top_scenario_id


def test_job_list_shows_feedback_scenario_tag_when_overridden(client, conn):
    sid, jid, top_scenario_id = _seed(conn)
    other_scenario_id = q.insert_scenario(conn, "Other Scenario", "")
    client.post(
        f"/jobs/{jid}/feedback",
        data={"status": "rejected", "note": "not a fit here", "feedback_scenario_id": other_scenario_id},
    )
    resp = client.get("/jobs?status=rejected")
    assert resp.status_code == 200
    assert '<span class="tag tag-feedback">→ Other Scenario</span>' in resp.text


def test_job_list_omits_feedback_scenario_tag_when_matching_top_passed(client, conn):
    sid, jid, top_scenario_id = _seed(conn)
    client.post(
        f"/jobs/{jid}/feedback",
        data={"status": "rejected", "note": "not a fit here", "feedback_scenario_id": top_scenario_id},
    )
    resp = client.get("/jobs?status=rejected")
    assert resp.status_code == 200
    assert '<span class="tag tag-feedback">' not in resp.text


def test_job_expand_feedback_form_defaults_to_top_passed_scenario(client, conn):
    sid, jid, top_scenario_id = _seed(conn)
    q.insert_scenario(conn, "Other Scenario", "")
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert "Other Scenario" in resp.text  # every scenario is listed
    assert f'<option value="{top_scenario_id}" selected>' in resp.text


def test_job_expand_has_full_meta_parity_with_card(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert '<dl class="job-tags">' in resp.text
    assert "75%" in resp.text  # fit-score badge, not just reasoning text
    assert "Remote ML" in resp.text
    assert "job_posting" in resp.text
    assert "finn.no" in resp.text
    assert '<h3 class="job-title">ML Eng</h3>' in resp.text


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
    assert "90%" in resp.text  # scenario_a's gate-score tab card
    assert "40%" in resp.text  # scenario_b's gate-score tab card


def test_job_expand_top_passed_scenario_tab_is_checked(client, conn):
    sid, jid, scenario_a = _seed(conn)
    scenario_b = q.insert_scenario(conn, "Other Scenario", "")
    q.upsert_job_score(conn, jid, scenario_b, 0.4, "weaker fit", "hash2")

    resp = client.get(f"/jobs/{jid}/expand")

    assert resp.status_code == 200
    assert f'id="score-tab-{jid}-{scenario_a}" name="score-tab-{jid}" class="score-tab-input" checked' in resp.text
    assert f'id="score-tab-{jid}-{scenario_b}" name="score-tab-{jid}" class="score-tab-input" checked' not in resp.text


def test_job_expand_shows_gate_pass_indicator(client, conn):
    sid, jid, scenario_a = _seed(conn)
    scenario_b = q.insert_scenario(conn, "Other Scenario", "")
    q.update_scenario(conn, scenario_b, name="Other Scenario", description="", gate_threshold=0.9)
    q.upsert_job_score(conn, jid, scenario_b, 0.4, "weaker fit", "hash2")  # below its own 0.9 gate

    resp = client.get(f"/jobs/{jid}/expand")

    assert resp.status_code == 200
    assert "Passed gate" in resp.text
    assert "Below gate threshold" in resp.text


def test_job_expand_shows_fit_scorecard(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert "Interest" in resp.text
    assert "Attainability" in resp.text
    assert "Strong domain fit" in resp.text
    assert "Close match" in resp.text


def test_job_expand_shows_not_yet_assessed_when_fit_missing(client, conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "http")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/2", title="No Fit Yet", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="job_posting", summary="role")
    scenario_id = q.insert_scenario(conn, "A", "")
    q.upsert_job_score(conn, jid, scenario_id, 0.9, "great", "hash1")  # passes gate, no stage-2 run yet
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert "Not yet assessed" in resp.text


def test_job_expand_no_score_box_when_unscored(client, conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "http")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/2", title="No Score", company="Acme", raw_text="r")
    q.insert_scenario(conn, "First", "")
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert 'class="score-box' not in resp.text


def test_job_expand_scenario_label_indicates_top_fit(client, conn):
    sid, jid, top_scenario_id = _seed(conn)
    other_id = q.insert_scenario(conn, "Other Scenario", "")
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert "fit" in resp.text.lower()
    assert f'<option value="{top_scenario_id}" selected>Remote ML</option>' in resp.text
    assert f'<option value="{other_id}" >Other Scenario</option>' in resp.text
```

Replace `test_job_bulk_feedback_defaults_to_each_jobs_own_best_scenario`:

```python
def test_job_bulk_feedback_defaults_to_each_jobs_own_top_passed_scenario(client, conn):
    sid, j1, scenario_a = _seed(conn)
    scenario_b = q.insert_scenario(conn, "Other", "")
    j2 = q.insert_job(conn, source_id=sid, url="http://finn.no/job/2", title="Data Eng", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, j2, simplified_content="clean", content_type="job_posting", summary="Also good")
    q.upsert_job_score(conn, j2, scenario_b, 0.75, "Decent match", "hash2")  # must clear the default 0.7 gate
    client.post(
        "/jobs/bulk-feedback",
        data={"job_ids": [j1, j2], "status": "invalid", "feedback_scenario_id": "", "status_filter": "", "content_type_filter": ""},
    )
    assert q.get_job(conn, j1)["feedback_scenario_id"] == scenario_a
    assert q.get_job(conn, j2)["feedback_scenario_id"] == scenario_b
```

Fix `test_job_list_tags_are_semantic_definition_list` — the `<dt>` labels change along with the macro rename in this task:

```python
def test_job_list_tags_are_semantic_definition_list(client, conn):
    _seed(conn)
    resp = client.get("/jobs")
    assert resp.status_code == 200
    assert '<dl class="job-tags">' in resp.text
    assert '<dt class="sr-only">Fit score</dt>' in resp.text
    assert '<dt class="sr-only">Matched scenarios</dt>' in resp.text
    assert '<dt class="sr-only">Content type</dt>' in resp.text
    assert '<dt class="sr-only">Source</dt>' in resp.text
```

Add new tests for gate-filtered visibility:

```python
def test_job_list_hides_gate_filtered_jobs_by_default(client, conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "http")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/1", title="Filtered Out", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="job_posting", summary="role")
    scenario_id = q.insert_scenario(conn, "A", "")  # default gate_threshold 0.7
    q.upsert_job_score(conn, jid, scenario_id, 0.3, "not a fit", "hash1")

    resp = client.get("/jobs")
    assert "Filtered Out" not in resp.text

    resp2 = client.get("/jobs?show_filtered=1")
    assert "Filtered Out" in resp2.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_routes_jobs.py -v`
Expected: FAIL — routes/templates still reference `best_scenario_id`/`scenario_boosted`, and there's no `show_filtered` support yet.

- [ ] **Step 3: Implement**

In `app/routes/jobs.py`, thread `show_filtered` through the list and bulk-feedback routes:

```python
def _get_filtered_jobs(
    conn: sqlite3.Connection, status: str | None, content_type: str | None, show_filtered: bool
) -> list[dict]:
    gate_passed_only = not show_filtered
    if status is None and content_type is None:
        return q.get_jobs(conn, status="new", gate_passed_only=gate_passed_only)
    return q.get_jobs(conn, status=status, content_type=content_type, gate_passed_only=gate_passed_only)


@router.get("/jobs", response_class=HTMLResponse)
def job_list(
    request: Request,
    status: str | None = None,
    content_type: str | None = None,
    show_filtered: bool = False,
    conn: sqlite3.Connection = Depends(get_db),
):
    jobs = _enrich_jobs(conn, _get_filtered_jobs(conn, status, content_type, show_filtered))
    counts = q.get_job_counts(conn)
    scenarios = q.get_scenarios(conn)
    effective_status = status if (status is not None or content_type is not None) else "new"
    return templates.TemplateResponse(
        request, "jobs/list.html",
        {
            "jobs": jobs, "counts": counts, "scenarios": scenarios,
            "status": effective_status, "content_type": content_type, "show_filtered": show_filtered,
        },
    )
```

Update `job_bulk_feedback`'s fallback and its own filtering:

```python
@router.post("/jobs/bulk-feedback", response_class=HTMLResponse)
def job_bulk_feedback(
    request: Request,
    job_ids: list[int] = Form(...),
    status: str = Form(...),
    note: str | None = Form(None),
    feedback_scenario_id: str = Form(""),
    status_filter: str | None = Form(None),
    content_type_filter: str | None = Form(None),
    show_filtered_filter: bool = Form(False),
    conn: sqlite3.Connection = Depends(get_db),
):
    for job_id in job_ids:
        scenario_id = (
            int(feedback_scenario_id) if feedback_scenario_id
            else q.get_job(conn, job_id)["top_passed_scenario_id"]
        )
        q.update_job_feedback(conn, job_id, status, note, scenario_id)

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

`job_expand`/`job_collapse` are unchanged (still call `q.get_job`, which already carries the new fields).

Replace `app/templates/jobs/_macros.html`:

```jinja
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
  {% if job.feedback_scenario_id and job.feedback_scenario_id != job.top_passed_scenario_id %}
  <dt class="sr-only">Feedback scenario</dt>
  <dd><span class="tag tag-feedback">→ {{ job.feedback_scenario_name }}</span></dd>
  {% endif %}
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

Replace the score-tabs block and feedback-form scenario select in `app/templates/jobs/_feedback.html` (lines 25-56 from the current file), and add the fit scorecard section:

```html
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
          <div class="score-tab-panel">{{ js.score_reasoning | markdown }}</div>
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
      <textarea name="note" placeholder="Why accepting/rejecting? What should change?"></textarea>
    </label>
    <label>Scenario (top fit shown, change if needed):
      <select name="feedback_scenario_id" required>
        {% for s in scenarios %}
          <option value="{{ s.id }}" {% if s.id == job.top_passed_scenario_id %}selected{% endif %}>{{ s.name }}</option>
        {% endfor %}
      </select>
    </label>
```

(Everything else in `_feedback.html` — the checkbox, header div, summary section, actions, reset button — is unchanged.)

Add a "show filtered" toggle to `app/templates/jobs/_content.html`, in the `.filter-bar` div right after the existing status links:

```html
<div class="filter-bar">
  <div class="filter-links">
    <a href="/jobs" {% if status == "new" and not content_type %}class="active"{% endif %}>New ({{ counts.new }})</a>
    <a href="/jobs?status=accepted" {% if status == "accepted" %}class="active"{% endif %}>Accepted ({{ counts.accepted }})</a>
    <a href="/jobs?status=rejected" {% if status == "rejected" %}class="active"{% endif %}>Rejected ({{ counts.rejected }})</a>
    <a href="/jobs?status=invalid" {% if status == "invalid" %}class="active"{% endif %}>Invalid ({{ counts.invalid }})</a>
    <a href="/jobs?content_type=lead" {% if content_type == "lead" %}class="active"{% endif %}>Leads ({{ counts.lead }})</a>
    {% if show_filtered %}
      <a href="/jobs" class="active" title="Currently showing jobs that didn't clear any scenario's gate">Show filtered ✓</a>
    {% else %}
      <a href="/jobs?show_filtered=1" title="Show jobs that didn't clear any scenario's gate">Show filtered</a>
    {% endif %}
  </div>
  ...
```

Add a hidden `show_filtered_filter` field to the persistent bulk-form shell in `app/templates/jobs/list.html`, mirroring `status_filter`/`content_type_filter`:

```html
{% extends "base.html" %}
{% block title %}Jobs — Job Seek{% endblock %}
{% block content %}
<h1>Jobs</h1>
<form id="bulk-form" hx-post="/jobs/bulk-feedback" hx-target="#jobs-content" hx-swap="innerHTML">
  <input type="hidden" name="status_filter" value="{{ status or '' }}">
  <input type="hidden" name="content_type_filter" value="{{ content_type or '' }}">
  <input type="hidden" name="show_filtered_filter" value="{{ '1' if show_filtered else '' }}">
</form>
<div id="jobs-content">
  {% include "jobs/_content.html" %}
</div>
{% endblock %}
```

Add CSS for the new scorecard section to `app/templates/base.html`, next to the existing `.score-box`/`.score-tab*` rules:

```css
.fit-scorecard-grid { display:grid; grid-template-columns:auto 1fr; gap:0.4rem 0.75rem; align-items:start; margin:0; }
.fit-scorecard-grid dt { font-weight:bold; }
.fit-scorecard-grid dd { margin:0; }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_routes_jobs.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add app/routes/jobs.py app/templates/jobs/_macros.html app/templates/jobs/_feedback.html app/templates/jobs/_content.html app/templates/jobs/list.html app/templates/base.html tests/test_routes_jobs.py
git commit -m "feat: gate-filtered job visibility toggle and fit scorecard UI"
```

---

### Task 13: Profile route — "Recompute fit scores" action

**Files:**
- Modify: `app/routes/profile.py`
- Modify: `app/templates/profile/index.html`
- Test: `tests/test_routes_profile.py`

**Interfaces:**
- Consumes: `run_reassess_fit` (Task 10).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_routes_profile.py`:

```python
from unittest.mock import patch


def test_reassess_fit_streams_progress(client, conn):
    def _fake_run_reassess_fit(conn, client, model):
        yield "Recomputing fit scores for 0 job(s)"
        yield "Fit recompute complete: 0 job(s) updated"
        return 0

    with patch("app.routes.profile.run_reassess_fit", side_effect=_fake_run_reassess_fit):
        resp = client.post("/profile/reassess-fit")

    assert resp.status_code == 200
    assert "Recomputing fit scores" in resp.text
    assert "Fit recompute complete" in resp.text


def test_profile_page_has_reassess_fit_button(client):
    resp = client.get("/profile")
    assert resp.status_code == 200
    assert 'data-progress-url="/profile/reassess-fit"' in resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_routes_profile.py -v`
Expected: FAIL — `/profile/reassess-fit` doesn't exist (404), and the button isn't in the template.

- [ ] **Step 3: Implement**

Replace `app/routes/profile.py` in full:

```python
from __future__ import annotations
import sqlite3
import openai
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from app.deps import get_db, get_ai_client, get_model
from app.db import queries as q
from app.pipeline import run_reassess_fit
from app.template_env import templates

router = APIRouter()


@router.get("/profile", response_class=HTMLResponse)
def profile_page(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    content = q.get_profile(conn)
    return templates.TemplateResponse(request, "profile/index.html", {"content": content})


@router.post("/profile", response_class=HTMLResponse)
def profile_save(
    request: Request,
    content: str = Form(...),
    conn: sqlite3.Connection = Depends(get_db),
):
    q.upsert_profile(conn, content)
    return templates.TemplateResponse(
        request, "profile/index.html", {"content": content, "saved": True}
    )


@router.post("/profile/reassess-fit")
def reassess_fit(
    conn: sqlite3.Connection = Depends(get_db),
    client: openai.OpenAI = Depends(get_ai_client),
    model: str = Depends(get_model),
):
    def stream():
        gen = run_reassess_fit(conn, client, model)
        try:
            while True:
                yield next(gen) + "\n"
        except StopIteration:
            pass

    return StreamingResponse(stream(), media_type="text/plain")
```

Add the button to `app/templates/profile/index.html`, after the existing save form:

```html
{% extends "base.html" %}
{% block title %}Profile — Job Seek{% endblock %}
{% block content %}
<h1>Profile</h1>
{% if saved %}<div style="padding:0.5rem; background:#d4edda; border-radius:4px; margin-bottom:1rem;">Saved.</div>{% endif %}
<form method="post" action="/profile">
  <label>Your skills, interests, and constraints (markdown supported):<br>
    <textarea name="content" style="width:100%; min-height:300px; font-family:monospace;">{{ content }}</textarea>
  </label>
  <br>
  <button type="submit" class="btn">Save</button>
</form>
<div style="margin-top:1.5rem; padding-top:1rem; border-top:1px solid #dee2e6;">
  <button type="button" class="btn" data-progress-url="/profile/reassess-fit"
    data-progress-display="#reassess-progress"
    title="Recompute the interest/attainability scorecard for every job that already passed a scenario gate but has a stale or missing fit score.">
    Recompute fit scores
  </button>
  <div class="reset-progress" id="reassess-progress" aria-live="polite"></div>
</div>
{% endblock %}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_routes_profile.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add app/routes/profile.py app/templates/profile/index.html tests/test_routes_profile.py
git commit -m "feat: add Recompute fit scores action to the profile page"
```

---

### Task 14: Full suite verification + manual smoke test

**Files:** none (verification only)

- [ ] **Step 1: Run the full automated test suite**

Run: `pytest -v`
Expected: PASS — every test in the suite, including all tests untouched by this plan (fetchers, classify, summarize, markdown rendering, sources, home, config, dates, json_utils, refine).

- [ ] **Step 2: Grep for any leftover references to removed concepts**

Run: `grep -rn "best_score\|best_scenario\|BOOST_BONUS\|scenario_boosted\|\bboosted\b" app/ tests/`
Expected: no output (empty). If anything remains, it's a missed call site — fix it and re-run Step 1.

- [ ] **Step 3: Manual smoke test**

Use the `run-dev-server` skill to start the app against a throwaway copy of `job-seek.db` (per project CLAUDE.md — never the live DB). Then, in the browser:

1. Go to `/scenarios`, confirm each scenario shows a "gate: NN%" tag and the edit form has a numeric gate-threshold input (not a boosted checkbox).
2. Trigger a fetch (or use "Re-evaluate all scenarios" against existing throwaway data) and confirm progress lines include both `Scored ...` (stage 1) and, for jobs that pass a gate, `Fit ...` (stage 2) lines.
3. Go to `/jobs`, confirm the list only shows jobs that passed at least one scenario's gate by default, and that `Show filtered` reveals the rest.
4. Expand a passed job, confirm the scenario score-compare tabs show ✓/✗ gate indicators, and a separate Interest/Attainability scorecard section renders below (or "Not yet assessed" if stage 2 hasn't run for it yet).
5. Go to `/profile`, click "Recompute fit scores", confirm it streams progress and updates scorecards.
6. Stop the dev server per the skill's instructions once done.

- [ ] **Step 4: Report results**

If Steps 1-3 all pass cleanly, the implementation is complete. If manual testing surfaces a UX issue not caught by the automated tests (e.g. a confusing label, a layout glitch), fix it directly, re-run the relevant test file, and commit as a small follow-up (`fix: ...`) rather than folding it silently into an earlier task's commit.
