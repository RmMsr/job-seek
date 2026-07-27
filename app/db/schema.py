import sqlite3

_DDL = """
CREATE TABLE IF NOT EXISTS profile (
    id INTEGER PRIMARY KEY,
    content TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    fetcher_type TEXT NOT NULL CHECK(fetcher_type IN ('http', 'playwright', 'slack', 'finn_listing')),
    enabled INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS scenarios (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS criteria (
    id INTEGER PRIMARY KEY,
    scenario_id INTEGER NOT NULL REFERENCES scenarios(id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    weight TEXT NOT NULL CHECK(weight IN ('must', 'prefer', 'avoid')),
    source TEXT NOT NULL CHECK(source IN ('manual', 'feedback')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

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

CREATE TABLE IF NOT EXISTS fetch_runs (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES sources(id),
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT,
    jobs_found INTEGER NOT NULL DEFAULT 0,
    jobs_new INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
"""


def _migrate_sources_fetcher_type(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='sources'"
    ).fetchone()
    if row is None or "finn_listing" in row[0]:
        return
    conn.executescript(
        """
        ALTER TABLE sources RENAME TO sources_old;
        CREATE TABLE sources (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            fetcher_type TEXT NOT NULL CHECK(fetcher_type IN ('http', 'playwright', 'slack', 'finn_listing')),
            enabled INTEGER NOT NULL DEFAULT 1
        );
        INSERT INTO sources SELECT * FROM sources_old;
        DROP TABLE sources_old;
        """
    )
    conn.commit()


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL)
    _migrate_sources_fetcher_type(conn)
