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
