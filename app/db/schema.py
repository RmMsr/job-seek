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
    content_type TEXT CHECK(content_type IN ('job_posting', 'lead', 'irrelevant', 'error')),
    fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
    status TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('new', 'accepted', 'rejected', 'invalid')),
    feedback_note TEXT,
    feedback_scenario_id INTEGER REFERENCES scenarios(id)
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
    # Rebuild under a new name rather than renaming "sources" away: SQLite
    # auto-rewrites other tables' REFERENCES clauses when the referenced
    # table is renamed, which would leave jobs/fetch_runs pointing at a
    # since-dropped "sources_old". Building the replacement under a fresh
    # name and swapping it into place afterwards avoids that entirely.
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript(
        """
        CREATE TABLE sources_new (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            fetcher_type TEXT NOT NULL CHECK(fetcher_type IN ('http', 'playwright', 'slack', 'finn_listing')),
            enabled INTEGER NOT NULL DEFAULT 1
        );
        INSERT INTO sources_new SELECT * FROM sources;
        DROP TABLE sources;
        ALTER TABLE sources_new RENAME TO sources;
        """
    )
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")


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


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL)
    _migrate_sources_fetcher_type(conn)
    _migrate_jobs_scores_to_table(conn)
    _migrate_jobs_add_feedback_scenario_id(conn)
