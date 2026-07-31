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
    assert _tables(conn) == {"profile", "sources", "scenarios", "criteria", "jobs", "job_scores", "fetch_runs"}


def test_init_db_is_idempotent(conn):
    init_db(conn)
    init_db(conn)  # should not raise
    assert _tables(conn) == {"profile", "sources", "scenarios", "criteria", "jobs", "job_scores", "fetch_runs"}


def test_jobs_url_is_unique(conn):
    init_db(conn)
    conn.execute(
        "INSERT INTO sources (name, url, fetcher_type) VALUES ('s', 'http://x', 'http')"
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
            url TEXT NOT NULL UNIQUE
        );
        """
    )
    conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s', 'http://x', 'http')")
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
        "INSERT INTO sources (name, url, fetcher_type) VALUES ('s', 'http://x', 'http')"
    )
    conn.commit()

    init_db(conn)

    conn.execute(
        "INSERT INTO sources (name, url, fetcher_type) VALUES ('finn.no', 'http://y', 'finn_listing')"
    )
    rows = conn.execute("SELECT name, url, fetcher_type FROM sources ORDER BY id").fetchall()
    assert [dict(r) for r in rows] == [
        {"name": "s", "url": "http://x", "fetcher_type": "http"},
        {"name": "finn.no", "url": "http://y", "fetcher_type": "finn_listing"},
    ]


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
