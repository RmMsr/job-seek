import sqlite3
import pytest
from app.db.schema import init_db


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    yield c
    c.close()


def _tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {r["name"] for r in rows}


def test_init_db_creates_all_tables(conn):
    init_db(conn)
    assert _tables(conn) == {
        "profile", "sources", "scenarios", "criteria", "jobs", "job_scores", "fetch_runs", "scenario_feedback",
        "tasks", "inbox_items",
    }


def test_init_db_is_idempotent(conn):
    init_db(conn)
    init_db(conn)  # should not raise
    assert _tables(conn) == {
        "profile", "sources", "scenarios", "criteria", "jobs", "job_scores", "fetch_runs", "scenario_feedback",
        "tasks", "inbox_items",
    }


def test_jobs_url_is_unique(conn):
    init_db(conn)
    conn.execute(
        "INSERT INTO sources (name, url, fetcher_type) VALUES ('s', 'http://x', 'slack')"
    )
    conn.execute(
        "INSERT INTO jobs (source_id, url, title, company, raw_text) VALUES (1, 'http://job/1', 'T', 'C', 'r')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO jobs (source_id, url, title, company, raw_text) VALUES (1, 'http://job/1', 'T2', 'C2', 'r2')"
        )


def test_sources_accepts_finn_listing_fetcher_type(conn):
    init_db(conn)
    conn.execute(
        "INSERT INTO sources (name, url, fetcher_type) VALUES ('finn.no', 'http://x', 'finn_listing')"
    )


def test_init_db_migration_preserves_referencing_jobs_with_fk_enforced(conn):
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(
        """
        CREATE TABLE sources (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            fetcher_type TEXT NOT NULL CHECK(fetcher_type IN ('http', 'playwright', 'slack')),
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
            content_type TEXT CHECK(content_type IN ('job_posting', 'lead', 'irrelevant', 'error')),
            fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
            status TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('new', 'accepted', 'rejected', 'invalid')),
            feedback_note TEXT
        );
        """
    )
    conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s', 'http://x', 'slack')")
    conn.execute("INSERT INTO jobs (source_id, url) VALUES (1, 'http://job/1')")
    conn.commit()

    init_db(conn)

    conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('finn.no', 'http://y', 'finn_listing')")
    jobs = conn.execute("SELECT source_id, url FROM jobs").fetchall()
    assert [dict(j) for j in jobs] == [{"source_id": 1, "url": "http://job/1"}]
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO jobs (source_id, url) VALUES (999, 'http://job/2')")


def test_init_db_migrates_sources_table_missing_finn_listing_type(conn):
    conn.executescript(
        """
        CREATE TABLE sources (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            fetcher_type TEXT NOT NULL CHECK(fetcher_type IN ('http', 'playwright', 'slack')),
            enabled INTEGER NOT NULL DEFAULT 1
        );
        """
    )
    conn.execute(
        "INSERT INTO sources (name, url, fetcher_type) VALUES ('s', 'http://x', 'slack')"
    )
    conn.commit()

    init_db(conn)

    conn.execute(
        "INSERT INTO sources (name, url, fetcher_type) VALUES ('finn.no', 'http://y', 'finn_listing')"
    )
    rows = conn.execute("SELECT name, url, fetcher_type FROM sources ORDER BY id").fetchall()
    assert [dict(r) for r in rows] == [
        {"name": "s", "url": "http://x", "fetcher_type": "slack"},
        {"name": "finn.no", "url": "http://y", "fetcher_type": "finn_listing"},
    ]


def test_init_db_migrates_fetch_runs_off_stale_sources_old_fk(conn):
    # Reproduces a real historical corruption: an old migration renamed
    # "sources" to "sources_old" (which auto-rewrites dependent tables'
    # REFERENCES clauses to follow the rename) before creating a fresh
    # "sources" table -- leaving fetch_runs permanently pointing at the
    # now-stale "sources_old" snapshot instead of the live "sources" table.
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
        CREATE TABLE sources_old (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            fetcher_type TEXT NOT NULL CHECK(fetcher_type IN ('http', 'playwright', 'slack')),
            enabled INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE fetch_runs (
            id INTEGER PRIMARY KEY,
            source_id INTEGER NOT NULL REFERENCES "sources_old"(id),
            started_at TEXT NOT NULL DEFAULT (datetime('now')),
            completed_at TEXT,
            jobs_found INTEGER NOT NULL DEFAULT 0,
            jobs_new INTEGER NOT NULL DEFAULT 0,
            error TEXT
        );
        """
    )
    conn.execute("INSERT INTO sources_old (id, name, url, fetcher_type) VALUES (1, 'old', 'http://x', 'slack')")
    conn.execute("INSERT INTO sources (id, name, url, fetcher_type) VALUES (1, 'old', 'http://x', 'slack')")
    conn.execute("INSERT INTO sources (id, name, url, fetcher_type) VALUES (2, 'new', 'http://y', 'slack')")
    conn.execute("INSERT INTO fetch_runs (id, source_id, jobs_found) VALUES (1, 1, 3)")
    conn.commit()

    init_db(conn)

    # A source that only exists in the current "sources" table (not in the
    # stale sources_old snapshot) must be usable for a fetch run.
    conn.execute("INSERT INTO fetch_runs (source_id) VALUES (2)")
    runs = conn.execute("SELECT id, source_id, jobs_found FROM fetch_runs ORDER BY id").fetchall()
    assert [dict(r) for r in runs] == [
        {"id": 1, "source_id": 1, "jobs_found": 3},
        {"id": 2, "source_id": 2, "jobs_found": 0},
    ]
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO fetch_runs (source_id) VALUES (999)")
    assert "sources_old" not in _tables(conn)


def test_job_scores_unique_per_job_and_scenario(conn):
    init_db(conn)
    conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s', 'http://x', 'slack')")
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
            boosted INTEGER NOT NULL DEFAULT 0,
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
    conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s', 'http://x', 'slack')")
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


def test_scenario_feedback_table_created(conn):
    init_db(conn)
    assert "scenario_feedback" in _tables(conn)


def test_scenario_feedback_unique_per_job_and_scenario(conn):
    init_db(conn)
    conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s', 'http://x', 'slack')")
    conn.execute("INSERT INTO jobs (source_id, url) VALUES (1, 'http://job/1')")
    conn.execute("INSERT INTO scenarios (name) VALUES ('A')")
    conn.execute("INSERT INTO scenario_feedback (job_id, scenario_id, note) VALUES (1, 1, 'note')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO scenario_feedback (job_id, scenario_id, note) VALUES (1, 1, 'other')")


def test_scenario_feedback_direction_constrained(conn):
    init_db(conn)
    conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s', 'http://x', 'slack')")
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
    conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s', 'http://x', 'slack')")
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


def test_jobs_table_has_headline_column(conn):
    init_db(conn)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    assert "headline" in cols


def test_init_db_migrates_jobs_adds_headline_column(conn):
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
            content_type TEXT CHECK(content_type IN ('job_posting', 'lead', 'irrelevant', 'error')),
            fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
            status TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('new', 'accepted', 'rejected', 'invalid')),
            feedback_note TEXT,
            feedback_scenario_id INTEGER REFERENCES scenarios(id)
        );
        """
    )
    conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s', 'http://x', 'slack')")
    conn.execute("INSERT INTO jobs (source_id, url, title) VALUES (1, 'http://job/1', 'Existing Title')")
    conn.commit()

    init_db(conn)

    row = conn.execute("SELECT title, headline FROM jobs WHERE url = 'http://job/1'").fetchone()
    assert row["title"] == "Existing Title"
    assert row["headline"] == ""

    # Idempotent: running init_db again doesn't error or duplicate columns.
    init_db(conn)
    cols = [row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()]
    assert cols.count("headline") == 1


def test_jobs_table_has_published_at_column(conn):
    init_db(conn)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    assert "published_at" in cols


def test_init_db_migrates_jobs_adds_published_at_column(conn):
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
            content_type TEXT CHECK(content_type IN ('job_posting', 'lead', 'irrelevant', 'error')),
            fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
            status TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('new', 'accepted', 'rejected', 'invalid')),
            feedback_note TEXT,
            feedback_scenario_id INTEGER REFERENCES scenarios(id)
        );
        """
    )
    conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s', 'http://x', 'slack')")
    conn.execute("INSERT INTO jobs (source_id, url, title) VALUES (1, 'http://job/1', 'Existing Title')")
    conn.commit()

    init_db(conn)

    row = conn.execute("SELECT title, published_at FROM jobs WHERE url = 'http://job/1'").fetchone()
    assert row["title"] == "Existing Title"
    assert row["published_at"] is None

    # Idempotent: running init_db again doesn't error or duplicate columns.
    init_db(conn)
    cols = [row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()]
    assert cols.count("published_at") == 1


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
    conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s', 'http://x', 'slack')")
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


def test_jobs_table_has_gate_override_column_defaulting_false(conn):
    init_db(conn)
    conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s', 'http://x', 'slack')")
    conn.execute("INSERT INTO jobs (source_id, url, title) VALUES (1, 'http://job/1', 'Title')")
    conn.commit()
    row = conn.execute("SELECT gate_override FROM jobs WHERE url = 'http://job/1'").fetchone()
    assert row["gate_override"] == 0


def test_init_db_migrates_jobs_adds_gate_override_column(conn):
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
    conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s', 'http://x', 'slack')")
    conn.execute("INSERT INTO jobs (source_id, url, title) VALUES (1, 'http://job/1', 'Existing Title')")
    conn.commit()

    init_db(conn)

    row = conn.execute("SELECT title, gate_override FROM jobs WHERE url = 'http://job/1'").fetchone()
    assert row["title"] == "Existing Title"
    assert row["gate_override"] == 0

    # Idempotent: running init_db again doesn't error or duplicate columns.
    init_db(conn)
    cols = [row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()]
    assert cols.count("gate_override") == 1


def test_init_db_migrates_jobs_adds_evaluation_completed_at_backfilled(conn):
    conn.executescript(
        """
        CREATE TABLE sources (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            fetcher_type TEXT NOT NULL CHECK(fetcher_type IN ('slack', 'finn_listing', 'manual', 'generic_listing')),
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
            status TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('new', 'accepted', 'rejected', 'trash')),
            feedback_note TEXT,
            feedback_handled_at TEXT,
            interest_score REAL,
            interest_reasoning TEXT,
            attainability_score REAL,
            attainability_reasoning TEXT,
            fit_score REAL,
            profile_version_hash TEXT,
            gate_override INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s', 'http://x', 'slack')")
    conn.execute(
        "INSERT INTO jobs (source_id, url, content_type) VALUES (1, 'http://job/posting', 'job_posting')"
    )
    conn.execute("INSERT INTO jobs (source_id, url, content_type) VALUES (1, 'http://job/lead', 'lead')")
    conn.execute("INSERT INTO jobs (source_id, url, content_type) VALUES (1, 'http://job/error', 'error')")
    conn.execute("INSERT INTO jobs (source_id, url) VALUES (1, 'http://job/unclassified')")
    conn.commit()

    init_db(conn)

    rows = {
        r["url"]: r["evaluation_completed_at"]
        for r in conn.execute("SELECT url, evaluation_completed_at FROM jobs").fetchall()
    }
    assert rows["http://job/posting"] is not None
    assert rows["http://job/lead"] is not None
    assert rows["http://job/error"] is None
    assert rows["http://job/unclassified"] is None

    # Idempotent: running init_db again doesn't error, duplicate the column, or re-touch existing values.
    init_db(conn)
    cols = [row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()]
    assert cols.count("evaluation_completed_at") == 1


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


def test_jobs_status_check_allows_trash_not_invalid(conn):
    init_db(conn)
    conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s', 'http://x', 'slack')")
    conn.execute(
        "INSERT INTO jobs (source_id, url, title, status) VALUES (1, 'http://job/1', 'Title', 'trash')"
    )
    conn.commit()
    row = conn.execute("SELECT status FROM jobs WHERE url = 'http://job/1'").fetchone()
    assert row["status"] == "trash"

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO jobs (source_id, url, title, status) VALUES (1, 'http://job/2', 'Title', 'invalid')"
        )


def test_sources_accepts_manual_fetcher_type(conn):
    init_db(conn)
    conn.execute(
        "INSERT INTO sources (name, url, fetcher_type) VALUES ('Manual', '', 'manual')"
    )


def test_init_db_migrates_sources_table_missing_manual_type(conn):
    conn.executescript(
        """
        CREATE TABLE sources (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            fetcher_type TEXT NOT NULL CHECK(fetcher_type IN ('http', 'playwright', 'slack', 'finn_listing')),
            enabled INTEGER NOT NULL DEFAULT 1
        );
        """
    )
    conn.execute(
        "INSERT INTO sources (name, url, fetcher_type) VALUES ('s', 'http://x', 'slack')"
    )
    conn.commit()

    init_db(conn)

    conn.execute(
        "INSERT INTO sources (name, url, fetcher_type) VALUES ('Manual', '', 'manual')"
    )
    rows = conn.execute("SELECT name, url, fetcher_type FROM sources ORDER BY id").fetchall()
    assert [dict(r) for r in rows] == [
        {"name": "s", "url": "http://x", "fetcher_type": "slack"},
        {"name": "Manual", "url": "", "fetcher_type": "manual"},
    ]


def test_init_db_migrates_jobs_status_invalid_to_trash(conn):
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
            feedback_handled_at TEXT,
            interest_score REAL,
            interest_reasoning TEXT,
            attainability_score REAL,
            attainability_reasoning TEXT,
            fit_score REAL,
            profile_version_hash TEXT,
            gate_override INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s', 'http://x', 'slack')")
    conn.execute(
        "INSERT INTO jobs (source_id, url, title, status) VALUES (1, 'http://job/1', 'Existing Title', 'invalid')"
    )
    conn.execute(
        "INSERT INTO jobs (source_id, url, title, status) VALUES (1, 'http://job/2', 'Still New', 'new')"
    )
    conn.commit()

    init_db(conn)

    row = conn.execute("SELECT title, status FROM jobs WHERE url = 'http://job/1'").fetchone()
    assert row["title"] == "Existing Title"
    assert row["status"] == "trash"
    row2 = conn.execute("SELECT status FROM jobs WHERE url = 'http://job/2'").fetchone()
    assert row2["status"] == "new"

    # Idempotent: running init_db again doesn't error or lose data.
    init_db(conn)
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 2


def test_sources_accepts_generic_listing_fetcher_type(conn):
    init_db(conn)
    conn.execute(
        "INSERT INTO sources (name, url, fetcher_type) VALUES ('Careers Page', 'http://x', 'generic_listing')"
    )


def test_init_db_migrates_sources_table_missing_generic_listing_type(conn):
    conn.executescript(
        """
        CREATE TABLE sources (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            fetcher_type TEXT NOT NULL CHECK(fetcher_type IN ('http', 'playwright', 'slack', 'finn_listing', 'manual')),
            enabled INTEGER NOT NULL DEFAULT 1
        );
        """
    )
    conn.execute(
        "INSERT INTO sources (name, url, fetcher_type) VALUES ('s', 'http://x', 'slack')"
    )
    conn.commit()

    init_db(conn)

    conn.execute(
        "INSERT INTO sources (name, url, fetcher_type) VALUES ('Careers Page', 'http://y', 'generic_listing')"
    )
    rows = conn.execute("SELECT name, url, fetcher_type FROM sources ORDER BY id").fetchall()
    assert [dict(r) for r in rows] == [
        {"name": "s", "url": "http://x", "fetcher_type": "slack"},
        {"name": "Careers Page", "url": "http://y", "fetcher_type": "generic_listing"},
    ]


def test_sources_url_is_unique(conn):
    init_db(conn)
    conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s1', 'http://x', 'generic_listing')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s2', 'http://x', 'generic_listing')")


def test_init_db_migrates_existing_sources_table_to_unique_url(conn):
    conn.executescript(
        """
        CREATE TABLE sources (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            fetcher_type TEXT NOT NULL CHECK(fetcher_type IN ('http', 'playwright', 'slack', 'finn_listing', 'manual', 'generic_listing')),
            enabled INTEGER NOT NULL DEFAULT 1
        );
        """
    )
    conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s', 'http://x', 'generic_listing')")
    conn.commit()

    init_db(conn)

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s2', 'http://x', 'generic_listing')")


def test_sources_check_rejects_http_and_playwright(conn):
    init_db(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s', 'http://x', 'http')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s', 'http://x', 'playwright')")


def test_init_db_migrates_sources_table_dropping_http_playwright_types(conn):
    conn.executescript(
        """
        CREATE TABLE sources (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            fetcher_type TEXT NOT NULL CHECK(fetcher_type IN ('http', 'playwright', 'slack', 'finn_listing', 'manual', 'generic_listing')),
            enabled INTEGER NOT NULL DEFAULT 1,
            d_cookie TEXT NOT NULL DEFAULT ''
        );
        """
    )
    conn.execute(
        "INSERT INTO sources (name, url, fetcher_type, d_cookie) VALUES ('s', 'http://x', 'generic_listing', 'xoxd-abc')"
    )
    conn.commit()

    init_db(conn)

    row = conn.execute("SELECT name, url, fetcher_type, d_cookie FROM sources WHERE name = 's'").fetchone()
    assert dict(row) == {"name": "s", "url": "http://x", "fetcher_type": "generic_listing", "d_cookie": "xoxd-abc"}

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s2', 'http://y', 'http')")

    # Idempotent: running init_db again doesn't error or duplicate rows.
    init_db(conn)
    assert conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 1


def test_tasks_table_exists():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    conn.execute(
        "INSERT INTO tasks (kind, params) VALUES ('fetch_source', '{}')"
    )
    row = conn.execute("SELECT * FROM tasks").fetchone()
    assert row["status"] == "queued"
    assert row["log"] == ""


def test_inbox_items_table_exists():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    conn.execute(
        "INSERT INTO inbox_items (kind, message, link) VALUES ('browser_missing', 'x', '/y')"
    )
    row = conn.execute("SELECT * FROM inbox_items").fetchone()
    assert row["resolved_at"] is None


def test_tasks_table_has_parent_pointer_and_wide_status(conn):
    init_db(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}
    assert "parent_task_id" in cols
    assert "group_id" not in cols
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='tasks'"
    ).fetchone()[0]
    for s in ("queued", "running", "needs_action", "done", "failed", "dismissed"):
        assert f"'{s}'" in sql
    assert "REFERENCES tasks(id)" in sql


def test_migrate_group_id_to_parent_task_id_hard_cutover(conn):
    # Simulate a DB left on the interim group_id shape.
    conn.executescript(
        """
        CREATE TABLE tasks (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, params TEXT NOT NULL DEFAULT '{}',
            group_id TEXT,
            status TEXT NOT NULL DEFAULT 'queued'
                CHECK(status IN ('queued','running','needs_action','done','failed','dismissed')),
            log TEXT NOT NULL DEFAULT '', result TEXT, error TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')), started_at TEXT, finished_at TEXT);
        INSERT INTO tasks (id, kind, group_id, status) VALUES (1, 'fetch_source', 'fa-abc', 'done');
        INSERT INTO tasks (id, kind, group_id, status) VALUES (2, 'fetch_source', 'fa-abc', 'queued');
        """
    )
    conn.commit()
    init_db(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}
    assert "group_id" not in cols and "parent_task_id" in cols
    assert conn.execute("SELECT parent_task_id FROM tasks WHERE id=1").fetchone()[0] is None
    assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 2


def test_inbox_items_has_no_task_id(conn):
    init_db(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(inbox_items)")}
    assert "task_id" not in cols


def test_migration_from_legacy_shape():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE tasks (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, params TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','done','failed')),
            log TEXT NOT NULL DEFAULT '', result TEXT, error TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')), started_at TEXT, finished_at TEXT);
        CREATE TABLE inbox_items (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, message TEXT NOT NULL,
            link TEXT NOT NULL, task_id INTEGER REFERENCES tasks(id),
            created_at TEXT NOT NULL DEFAULT (datetime('now')), resolved_at TEXT);
        INSERT INTO tasks (id, kind, params, status, result, finished_at)
            VALUES (1, 'source_detect', '{}', 'done', '{"needs_action": true}', datetime('now'));
        INSERT INTO tasks (id, kind, params, status, result, finished_at)
            VALUES (2, 'source_detect', '{}', 'done', '{"needs_action": true}', datetime('now'));
        INSERT INTO inbox_items (kind, message, link, task_id) VALUES ('task_followup', 'm', '/t/1', 1);
        INSERT INTO inbox_items (kind, message, link, task_id, resolved_at)
            VALUES ('task_followup', 'm', '/t/2', 2, datetime('now'));
        INSERT INTO inbox_items (kind, message, link) VALUES ('browser_missing', 'b', '/sources');
        """
    )
    c.commit()
    init_db(c)
    assert c.execute("SELECT status FROM tasks WHERE id=1").fetchone()[0] == "needs_action"
    assert c.execute("SELECT status FROM tasks WHERE id=2").fetchone()[0] == "done"
    assert c.execute("SELECT COUNT(*) FROM inbox_items WHERE kind='task_followup'").fetchone()[0] == 0
    assert "task_id" not in {r[1] for r in c.execute("PRAGMA table_info(inbox_items)")}
    assert c.execute("SELECT COUNT(*) FROM inbox_items WHERE kind='browser_missing'").fetchone()[0] == 1
    c.close()


def test_migrate_canonicalizes_job_urls_and_collapses_collisions(conn):
    from app.db.schema import _migrate_jobs_canonicalize_urls

    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    sid = conn.execute(
        "INSERT INTO sources (name, url, fetcher_type) VALUES ('S', 'https://ex.com', 'generic_listing')"
    ).lastrowid
    conn.execute("INSERT INTO scenarios (id, name, description) VALUES (1, 'S', '')")
    # row 1: already canonical; row 2: same job with tracking params (higher id -> dropped)
    conn.execute("INSERT INTO jobs (id, source_id, url, title, company, raw_text) "
                 "VALUES (1, ?, 'https://ex.com/j/9', '', '', 'x')", (sid,))
    conn.execute("INSERT INTO jobs (id, source_id, url, title, company, raw_text) "
                 "VALUES (2, ?, 'https://ex.com/j/9?refId=abc', '', '', 'x')", (sid,))
    # row 3: only needs rewriting, no collision
    conn.execute("INSERT INTO jobs (id, source_id, url, title, company, raw_text) "
                 "VALUES (3, ?, 'https://ex.com/j/7?utm_source=x', '', '', 'x')", (sid,))
    conn.execute("INSERT INTO job_scores (job_id, scenario_id, relevance_score, score_reasoning, scenario_version_hash) "
                 "VALUES (2, 1, 0.5, 'r', 'h')")  # child of the row that will be deleted
    conn.commit()

    _migrate_jobs_canonicalize_urls(conn)

    rows = conn.execute("SELECT id, url FROM jobs ORDER BY id").fetchall()
    assert [(r["id"], r["url"]) for r in rows] == [
        (1, "https://ex.com/j/9"),
        (3, "https://ex.com/j/7"),
    ]
    assert conn.execute("SELECT COUNT(*) FROM job_scores").fetchone()[0] == 0

    # idempotent
    _migrate_jobs_canonicalize_urls(conn)
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 2


def test_init_db_accepts_eawork_listing_fetcher_type(conn):
    init_db(conn)
    conn.execute(
        "INSERT INTO sources (name, url, fetcher_type) "
        "VALUES ('80k', 'https://jobs.80000hours.org/', 'eawork_listing')"
    )  # must not raise


def test_init_db_flips_existing_80k_generic_row_to_eawork(conn):
    conn.executescript(
        """
        CREATE TABLE sources (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            fetcher_type TEXT NOT NULL CHECK(fetcher_type IN ('slack', 'finn_listing', 'manual', 'generic_listing')),
            enabled INTEGER NOT NULL DEFAULT 1,
            d_cookie TEXT NOT NULL DEFAULT ''
        );
        """
    )
    conn.execute(
        "INSERT INTO sources (name, url, fetcher_type) VALUES "
        "('80k', 'https://jobs.80000hours.org/?refinementList%5Btags_area%5D%5B0%5D=Technical', 'generic_listing')"
    )
    conn.execute(
        "INSERT INTO sources (name, url, fetcher_type) VALUES ('other', 'https://x.test/', 'generic_listing')"
    )
    init_db(conn)
    rows = dict(conn.execute("SELECT name, fetcher_type FROM sources").fetchall())
    assert rows["80k"] == "eawork_listing"
    assert rows["other"] == "generic_listing"
