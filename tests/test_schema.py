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
    assert _tables(conn) == {"profile", "sources", "scenarios", "criteria", "jobs", "fetch_runs"}


def test_init_db_is_idempotent(conn):
    init_db(conn)
    init_db(conn)  # should not raise
    assert _tables(conn) == {"profile", "sources", "scenarios", "criteria", "jobs", "fetch_runs"}


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
