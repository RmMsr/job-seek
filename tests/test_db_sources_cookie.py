import sqlite3
import pytest
from app.db.schema import init_db
from app.db import queries as q


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:", check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    init_db(c)
    yield c
    c.close()


def test_new_source_defaults_to_empty_cookie(conn):
    sid = q.insert_source(conn, "Example Slack", "https://example-workspace.slack.com/archives/C0EXAMPLE1", "slack")
    assert q.get_source(conn, sid)["d_cookie"] == ""


def test_set_source_cookie_persists_value(conn):
    sid = q.insert_source(conn, "Example Slack", "https://example-workspace.slack.com/archives/C0EXAMPLE1", "slack")
    q.set_source_cookie(conn, sid, "xoxd-fake-cookie")
    assert q.get_source(conn, sid)["d_cookie"] == "xoxd-fake-cookie"


def test_migration_adds_cookie_column_to_preexisting_sources_table(conn):
    # Simulate an older DB whose sources table predates d_cookie.
    conn.execute("DROP TABLE sources")
    conn.execute(
        "CREATE TABLE sources (id INTEGER PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL, "
        "fetcher_type TEXT NOT NULL CHECK(fetcher_type IN ('http','playwright','slack','finn_listing','manual')), "
        "enabled INTEGER NOT NULL DEFAULT 1)"
    )
    conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('Old', 'http://x', 'http')")
    conn.commit()
    init_db(conn)  # idempotent; must add the column
    cols = [r[1] for r in conn.execute("PRAGMA table_info(sources)").fetchall()]
    assert "d_cookie" in cols
    assert conn.execute("SELECT d_cookie FROM sources").fetchone()[0] == ""
