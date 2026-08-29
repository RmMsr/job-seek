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
    fetcher_type TEXT NOT NULL CHECK(fetcher_type IN ('slack', 'finn_listing', 'manual', 'generic_listing')),
    enabled INTEGER NOT NULL DEFAULT 1,
    d_cookie TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS scenarios (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    gate_threshold REAL NOT NULL DEFAULT 0.7,
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
    gate_override INTEGER NOT NULL DEFAULT 0,
    evaluation_completed_at TEXT
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
    error TEXT,
    auth_error INTEGER NOT NULL DEFAULT 0
);

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

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    params TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued', 'running', 'done', 'failed')),
    log TEXT NOT NULL DEFAULT '',
    result TEXT,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    started_at TEXT,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS inbox_items (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    link TEXT NOT NULL,
    task_id INTEGER REFERENCES tasks(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at TEXT
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


def _migrate_sources_fetcher_type_manual(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='sources'"
    ).fetchone()
    if row is None or "'manual'" in row[0]:
        return
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript(
        """
        CREATE TABLE sources_new (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            fetcher_type TEXT NOT NULL CHECK(fetcher_type IN ('http', 'playwright', 'slack', 'finn_listing', 'manual')),
            enabled INTEGER NOT NULL DEFAULT 1
        );
        INSERT INTO sources_new SELECT * FROM sources;
        DROP TABLE sources;
        ALTER TABLE sources_new RENAME TO sources;
        """
    )
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")


def _migrate_sources_add_d_cookie(conn: sqlite3.Connection) -> None:
    # Purely additive column (stores the Slack `d` session cookie per source),
    # so a plain ALTER TABLE suffices — no table rebuild.
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='sources'"
    ).fetchone()
    if row is None or "d_cookie" in row[0]:
        return
    conn.execute("ALTER TABLE sources ADD COLUMN d_cookie TEXT NOT NULL DEFAULT ''")
    conn.commit()


def _migrate_sources_fetcher_type_generic_listing(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='sources'"
    ).fetchone()
    if row is None or "'generic_listing'" in row[0]:
        return
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript(
        """
        CREATE TABLE sources_new (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            fetcher_type TEXT NOT NULL CHECK(fetcher_type IN ('http', 'playwright', 'slack', 'finn_listing', 'manual', 'generic_listing')),
            enabled INTEGER NOT NULL DEFAULT 1,
            d_cookie TEXT NOT NULL DEFAULT ''
        );
        INSERT INTO sources_new SELECT * FROM sources;
        DROP TABLE sources;
        ALTER TABLE sources_new RENAME TO sources;
        """
    )
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")


def _migrate_sources_drop_http_playwright_types(conn: sqlite3.Connection) -> None:
    # 'http' and 'playwright' were standalone fetcher_type choices for a
    # manually-configured source; classify_known_source/DETECTABLE_FETCHER_TYPES
    # already stopped offering them, so no source can have either value.
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='sources'"
    ).fetchone()
    if row is None or "'http'" not in row[0]:
        return
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript(
        """
        CREATE TABLE sources_new (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            fetcher_type TEXT NOT NULL CHECK(fetcher_type IN ('slack', 'finn_listing', 'manual', 'generic_listing')),
            enabled INTEGER NOT NULL DEFAULT 1,
            d_cookie TEXT NOT NULL DEFAULT ''
        );
        INSERT INTO sources_new SELECT * FROM sources;
        DROP TABLE sources;
        ALTER TABLE sources_new RENAME TO sources;
        """
    )
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")


def _migrate_sources_url_unique(conn: sqlite3.Connection) -> None:
    # A unique index enforces this without a table rebuild, unlike the CHECK-constraint
    # migrations above.
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_sources_url ON sources(url)")
    conn.commit()


def _migrate_fetch_runs_source_fk(conn: sqlite3.Connection) -> None:
    # A historical migration renamed "sources" to "sources_old" (which
    # auto-rewrites dependent tables' REFERENCES clauses to follow the
    # rename) before creating a fresh "sources" table in its place, leaving
    # fetch_runs permanently pointing at that stale sources_old snapshot --
    # any source created since can never start a fetch run.
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='fetch_runs'"
    ).fetchone()
    if row is None or "sources_old" not in row[0]:
        return
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript(
        """
        CREATE TABLE fetch_runs_new (
            id INTEGER PRIMARY KEY,
            source_id INTEGER NOT NULL REFERENCES sources(id),
            started_at TEXT NOT NULL DEFAULT (datetime('now')),
            completed_at TEXT,
            jobs_found INTEGER NOT NULL DEFAULT 0,
            jobs_new INTEGER NOT NULL DEFAULT 0,
            error TEXT
        );
        INSERT INTO fetch_runs_new SELECT * FROM fetch_runs;
        DROP TABLE fetch_runs;
        ALTER TABLE fetch_runs_new RENAME TO fetch_runs;
        DROP TABLE IF EXISTS sources_old;
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


def _migrate_jobs_add_headline(conn: sqlite3.Connection) -> None:
    # Purely additive column, no CHECK/constraint change and nothing to drop,
    # so a plain ALTER TABLE suffices instead of a full jobs_new/copy/drop/rename cycle.
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='jobs'"
    ).fetchone()
    if row is None or "headline" in row[0]:
        return
    conn.execute("ALTER TABLE jobs ADD COLUMN headline TEXT NOT NULL DEFAULT ''")
    conn.commit()


def _migrate_jobs_add_published_at(conn: sqlite3.Connection) -> None:
    # Purely additive column, same shape as the headline migration above.
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='jobs'"
    ).fetchone()
    if row is None or "published_at" in row[0]:
        return
    conn.execute("ALTER TABLE jobs ADD COLUMN published_at TEXT")
    conn.commit()


def _migrate_jobs_add_feedback_handled_at(conn: sqlite3.Connection) -> None:
    # Purely additive column, same shape as the headline/published_at migrations above.
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='jobs'"
    ).fetchone()
    if row is None or "feedback_handled_at" in row[0]:
        return
    conn.execute("ALTER TABLE jobs ADD COLUMN feedback_handled_at TEXT")
    conn.commit()


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


def _migrate_jobs_add_gate_override(conn: sqlite3.Connection) -> None:
    # Purely additive column, same shape as the headline/published_at migrations above.
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='jobs'"
    ).fetchone()
    if row is None or "gate_override" in row[0]:
        return
    conn.execute("ALTER TABLE jobs ADD COLUMN gate_override INTEGER NOT NULL DEFAULT 0")
    conn.commit()


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


def _migrate_jobs_status_invalid_to_trash(conn: sqlite3.Connection) -> None:
    # Renamed the "invalid" status value to "trash" to match the UI label.
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='jobs'"
    ).fetchone()
    if row is None or "'trash'" in row[0]:
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
    conn.execute(
        """
        INSERT INTO jobs_new (
            id, source_id, url, title, company, raw_text, simplified_content, summary,
            headline, published_at, content_type, fetched_at, status, feedback_note,
            feedback_handled_at, interest_score, interest_reasoning, attainability_score,
            attainability_reasoning, fit_score, profile_version_hash, gate_override
        )
        SELECT
            id, source_id, url, title, company, raw_text, simplified_content, summary,
            headline, published_at, content_type, fetched_at,
            CASE WHEN status = 'invalid' THEN 'trash' ELSE status END,
            feedback_note, feedback_handled_at, interest_score, interest_reasoning,
            attainability_score, attainability_reasoning, fit_score, profile_version_hash, gate_override
        FROM jobs
        """
    )
    conn.execute("DROP TABLE jobs")
    conn.execute("ALTER TABLE jobs_new RENAME TO jobs")
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")


def _migrate_fetch_runs_add_auth_error(conn: sqlite3.Connection) -> None:
    # Purely additive column, same shape as the jobs-table additive migrations
    # above. Lets a fetch failure record whether it was specifically a Slack
    # auth failure, so the sources page can show login state without ever
    # making its own live request to Slack.
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='fetch_runs'"
    ).fetchone()
    if row is None or "auth_error" in row[0]:
        return
    conn.execute("ALTER TABLE fetch_runs ADD COLUMN auth_error INTEGER NOT NULL DEFAULT 0")
    conn.commit()


def _migrate_jobs_add_evaluation_completed_at(conn: sqlite3.Connection) -> None:
    # Purely additive column, same shape as the other jobs-table migrations
    # above. Backfilled to "now" for every already-classified job so existing
    # jobs don't vanish from the list on upgrade — only newly ingested jobs
    # go through the real "not visible until the pipeline finishes" gate.
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='jobs'"
    ).fetchone()
    if row is None or "evaluation_completed_at" in row[0]:
        return
    conn.execute("ALTER TABLE jobs ADD COLUMN evaluation_completed_at TEXT")
    conn.execute(
        "UPDATE jobs SET evaluation_completed_at = datetime('now') "
        "WHERE content_type IN ('job_posting', 'lead')"
    )
    conn.commit()


def _migrate_jobs_canonicalize_urls(conn: sqlite3.Connection) -> None:
    # Idempotent: canonicalizing an already-canonical URL is a no-op, so this
    # runs on every startup. Collapses rows that canonicalize to the same URL,
    # keeping the lowest id (child rows cascade-delete; FK enforcement is on).
    from app.url_canon import canonicalize_url

    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='jobs'"
    ).fetchone()
    if row is None:
        return

    by_canon: dict[str, list[int]] = {}
    for job_id, url in conn.execute("SELECT id, url FROM jobs ORDER BY id"):
        by_canon.setdefault(canonicalize_url(url), []).append(job_id)

    for canon, ids in by_canon.items():
        for drop in ids[1:]:
            conn.execute("DELETE FROM jobs WHERE id = ?", (drop,))
        conn.execute("UPDATE jobs SET url = ? WHERE id = ?", (canon, ids[0]))
    conn.commit()


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL)
    _migrate_sources_fetcher_type(conn)
    _migrate_sources_fetcher_type_manual(conn)
    _migrate_sources_add_d_cookie(conn)
    _migrate_sources_fetcher_type_generic_listing(conn)
    _migrate_sources_drop_http_playwright_types(conn)
    _migrate_sources_url_unique(conn)
    _migrate_fetch_runs_source_fk(conn)
    _migrate_jobs_scores_to_table(conn)
    _migrate_jobs_add_headline(conn)
    _migrate_jobs_add_published_at(conn)
    _migrate_jobs_add_feedback_handled_at(conn)
    _migrate_jobs_add_fit_scorecard(conn)
    _migrate_jobs_drop_feedback_scenario_id(conn)
    _migrate_jobs_add_gate_override(conn)
    _migrate_scenarios_gate_threshold(conn)
    _migrate_jobs_status_invalid_to_trash(conn)
    _migrate_fetch_runs_add_auth_error(conn)
    _migrate_jobs_add_evaluation_completed_at(conn)
    _migrate_jobs_canonicalize_urls(conn)
