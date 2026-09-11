import json
import sqlite3

_DDL = """
CREATE TABLE IF NOT EXISTS profile (
    id INTEGER PRIMARY KEY,
    content TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS cv_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    base_cv TEXT NOT NULL DEFAULT '',
    base_instruction TEXT NOT NULL DEFAULT '',
    base_guardrails TEXT NOT NULL DEFAULT '',
    css TEXT NOT NULL DEFAULT '',
    default_scope TEXT NOT NULL DEFAULT '["select","reorder"]',
    directives_template TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS cv_scope_options (
    id INTEGER PRIMARY KEY,
    description TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    default_enabled INTEGER NOT NULL DEFAULT 0,
    sort_order INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS job_cv (
    job_id INTEGER PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    scope TEXT NOT NULL DEFAULT '[]',
    tuning_directives TEXT NOT NULL DEFAULT '',
    plan TEXT NOT NULL DEFAULT '[]',
    handled_suggestions TEXT NOT NULL DEFAULT '[]',
    tailored_cv TEXT NOT NULL DEFAULT '',
    guardrail_findings TEXT NOT NULL DEFAULT '[]',
    change_report TEXT NOT NULL DEFAULT '{}',
    base_hash TEXT NOT NULL DEFAULT '',
    base_cv_snapshot TEXT NOT NULL DEFAULT '',
    plan_generated_at TEXT,
    directives_edited_at TEXT,
    generated_at TEXT,
    scope_edited_at TEXT,
    edited_at TEXT,
    guardrails_checked_at TEXT,
    plan_context_hash TEXT,
    finalized_at TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    fetcher_type TEXT NOT NULL CHECK(fetcher_type IN ('slack', 'finn_listing', 'manual', 'generic_listing', 'eawork_listing')),
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
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
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
    evaluation_completed_at TEXT,
    status_changed_at TEXT
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
    auth_error INTEGER NOT NULL DEFAULT 0,
    task_id INTEGER REFERENCES tasks(id)
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
    parent_task_id INTEGER REFERENCES tasks(id),
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK(status IN ('queued', 'running', 'needs_action', 'done', 'failed', 'dismissed', 'cancelled')),
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
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS job_events (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    kind TEXT NOT NULL,
    message TEXT NOT NULL
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


def _migrate_sources_fetcher_type_eawork_listing(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='sources'"
    ).fetchone()
    if row is None or "'eawork_listing'" in row[0]:
        return
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript(
        """
        CREATE TABLE sources_new (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            fetcher_type TEXT NOT NULL CHECK(fetcher_type IN ('slack', 'finn_listing', 'manual', 'generic_listing', 'eawork_listing')),
            enabled INTEGER NOT NULL DEFAULT 1,
            d_cookie TEXT NOT NULL DEFAULT ''
        );
        INSERT INTO sources_new SELECT * FROM sources;
        DROP TABLE sources;
        ALTER TABLE sources_new RENAME TO sources;
        UPDATE sources SET fetcher_type = 'eawork_listing'
        WHERE fetcher_type = 'generic_listing'
          AND url LIKE 'https://jobs.80000hours.org/%';
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


def _migrate_job_cv_drop_preview_pages(conn: sqlite3.Connection) -> None:
    # Previews are rendered on demand as HTML now (doc-write-cli --html) — the
    # task no longer stores PNG page paths. Direct DROP COLUMN.
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='job_cv'"
    ).fetchone()
    if row is None or "preview_pages" not in row[0]:
        return
    conn.execute("ALTER TABLE job_cv DROP COLUMN preview_pages")
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


def _migrate_fetch_runs_add_task_id(conn: sqlite3.Connection) -> None:
    # Purely additive. Links a fetch run to the task that produced it so the
    # Fetch page's "Last run" health column can deep-link to that task.
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='fetch_runs'"
    ).fetchone()
    if row is None or "task_id" in row[0]:
        return
    conn.execute("ALTER TABLE fetch_runs ADD COLUMN task_id INTEGER REFERENCES tasks(id)")
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


def _migrate_jobs_add_status_changed_at(conn: sqlite3.Connection) -> None:
    # Purely additive. Records when a job's status last changed (feedback
    # decision or gate override), for the Jobs-list "newest changes" sort.
    # No backfill: an unset value falls through to the row's creation time via
    # the sort's MAX(...) over the (later-renamed) fetched_at/created_at column.
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='jobs'"
    ).fetchone()
    if row is None or "status_changed_at" in row[0]:
        return
    conn.execute("ALTER TABLE jobs ADD COLUMN status_changed_at TEXT")
    conn.commit()


def _migrate_jobs_split_created_fetched(conn: sqlite3.Connection) -> None:
    # Split the old `fetched_at` (which was only ever a row-creation timestamp)
    # into an immutable `created_at` — carrying every existing sort's semantics
    # unchanged — and a new mutable `fetched_at` meaning "last time we pulled
    # this job's live page", bumped by the revisit task. FTS triggers touch only
    # text columns, so RENAME COLUMN leaves them intact — no rebuild needed.
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='jobs'"
    ).fetchone()
    if row is None or "created_at" in row[0]:
        return
    conn.execute("ALTER TABLE jobs RENAME COLUMN fetched_at TO created_at")
    # SQLite forbids a non-constant DEFAULT on ADD COLUMN, so add it nullable and
    # backfill; insert_job writes fetched_at explicitly for new rows.
    conn.execute("ALTER TABLE jobs ADD COLUMN fetched_at TEXT")
    conn.execute("UPDATE jobs SET fetched_at = created_at")
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


def _migrate_tasks_group_and_status(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='tasks'"
    ).fetchone()
    if row is None or "'needs_action'" in row[0]:
        return
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript(
        """
        CREATE TABLE tasks_new (
            id INTEGER PRIMARY KEY,
            kind TEXT NOT NULL,
            params TEXT NOT NULL DEFAULT '{}',
            group_id TEXT,
            status TEXT NOT NULL DEFAULT 'queued'
                CHECK(status IN ('queued','running','needs_action','done','failed','dismissed')),
            log TEXT NOT NULL DEFAULT '',
            result TEXT,
            error TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            started_at TEXT,
            finished_at TEXT
        );
        INSERT INTO tasks_new (id, kind, params, status, log, result, error, created_at, started_at, finished_at)
            SELECT id, kind, params, status, log, result, error, created_at, started_at, finished_at FROM tasks;
        DROP TABLE tasks;
        ALTER TABLE tasks_new RENAME TO tasks;
        """
    )
    # Backfill: a done task whose result asked for follow-up and whose inbox
    # item is still open becomes needs_action. (inbox_items still has task_id
    # at this point — _migrate_inbox_drop_task_id runs after this.)
    if "task_id" in {r[1] for r in conn.execute("PRAGMA table_info(inbox_items)")}:
        conn.execute(
            """
            UPDATE tasks SET status = 'needs_action'
            WHERE status = 'done'
              AND id IN (
                SELECT task_id FROM inbox_items
                WHERE kind = 'task_followup' AND resolved_at IS NULL AND task_id IS NOT NULL
              )
            """
        )
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")


def _migrate_inbox_drop_task_id(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='inbox_items'"
    ).fetchone()
    if row is None or "task_id" not in row[0]:
        return
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript(
        """
        DELETE FROM inbox_items WHERE kind = 'task_followup';
        CREATE TABLE inbox_items_new (
            id INTEGER PRIMARY KEY,
            kind TEXT NOT NULL,
            message TEXT NOT NULL,
            link TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            resolved_at TEXT
        );
        INSERT INTO inbox_items_new (id, kind, message, link, created_at, resolved_at)
            SELECT id, kind, message, link, created_at, resolved_at FROM inbox_items;
        DROP TABLE inbox_items;
        ALTER TABLE inbox_items_new RENAME TO inbox_items;
        """
    )
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")


def _migrate_tasks_group_to_parent(conn: sqlite3.Connection) -> None:
    # Hard cutover (personal single-instance app): the opaque group_id token
    # is replaced by a real self-referential parent pointer. group_id carried
    # no data worth keeping — every pre-cutover row just gets parent_task_id
    # NULL.
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='tasks'"
    ).fetchone()
    if row is None or "parent_task_id" in row[0]:
        return
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript(
        """
        CREATE TABLE tasks_new (
            id INTEGER PRIMARY KEY,
            kind TEXT NOT NULL,
            params TEXT NOT NULL DEFAULT '{}',
            parent_task_id INTEGER REFERENCES tasks(id),
            status TEXT NOT NULL DEFAULT 'queued'
                CHECK(status IN ('queued','running','needs_action','done','failed','dismissed')),
            log TEXT NOT NULL DEFAULT '',
            result TEXT,
            error TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            started_at TEXT,
            finished_at TEXT
        );
        INSERT INTO tasks_new (id, kind, params, status, log, result, error, created_at, started_at, finished_at)
            SELECT id, kind, params, status, log, result, error, created_at, started_at, finished_at FROM tasks;
        DROP TABLE tasks;
        ALTER TABLE tasks_new RENAME TO tasks;
        """
    )
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")


def _migrate_tasks_add_cancelled(conn: sqlite3.Connection) -> None:
    # Widen the tasks.status CHECK to allow 'cancelled' (user-stopped task).
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='tasks'"
    ).fetchone()
    if row is None or "'cancelled'" in row[0]:
        return
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript(
        """
        CREATE TABLE tasks_new (
            id INTEGER PRIMARY KEY,
            kind TEXT NOT NULL,
            params TEXT NOT NULL DEFAULT '{}',
            parent_task_id INTEGER REFERENCES tasks(id),
            status TEXT NOT NULL DEFAULT 'queued'
                CHECK(status IN ('queued','running','needs_action','done','failed','dismissed','cancelled')),
            log TEXT NOT NULL DEFAULT '',
            result TEXT,
            error TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            started_at TEXT,
            finished_at TEXT
        );
        INSERT INTO tasks_new (id, kind, params, parent_task_id, status, log, result, error, created_at, started_at, finished_at)
            SELECT id, kind, params, parent_task_id, status, log, result, error, created_at, started_at, finished_at FROM tasks;
        DROP TABLE tasks;
        ALTER TABLE tasks_new RENAME TO tasks;
        """
    )
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")


def _migrate_add_jobs_fts(conn: sqlite3.Connection) -> None:
    # Create-once. Placed last in init_db so it runs after any table rebuild.
    # A future migration that rebuilds the jobs table must follow itself with
    #   INSERT INTO jobs_fts(jobs_fts) VALUES('rebuild')
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='jobs_fts'"
    ).fetchone()
    if row is not None:
        return
    conn.executescript(
        """
        CREATE VIRTUAL TABLE jobs_fts USING fts5(
            title, company, headline, summary, simplified_content,
            content='jobs', content_rowid='id',
            tokenize='porter unicode61'
        );

        CREATE TRIGGER jobs_fts_ai AFTER INSERT ON jobs BEGIN
            INSERT INTO jobs_fts(rowid, title, company, headline, summary, simplified_content)
            VALUES (new.id, new.title, new.company, new.headline, new.summary, new.simplified_content);
        END;

        CREATE TRIGGER jobs_fts_ad AFTER DELETE ON jobs BEGIN
            INSERT INTO jobs_fts(jobs_fts, rowid, title, company, headline, summary, simplified_content)
            VALUES ('delete', old.id, old.title, old.company, old.headline, old.summary, old.simplified_content);
        END;

        CREATE TRIGGER jobs_fts_au AFTER UPDATE ON jobs BEGIN
            INSERT INTO jobs_fts(jobs_fts, rowid, title, company, headline, summary, simplified_content)
            VALUES ('delete', old.id, old.title, old.company, old.headline, old.summary, old.simplified_content);
            INSERT INTO jobs_fts(rowid, title, company, headline, summary, simplified_content)
            VALUES (new.id, new.title, new.company, new.headline, new.summary, new.simplified_content);
        END;

        INSERT INTO jobs_fts(rowid, title, company, headline, summary, simplified_content)
            SELECT id, title, company, headline, summary, simplified_content FROM jobs;
        """
    )
    conn.commit()


def _migrate_cv_settings_merge_floor_guardrails(conn: sqlite3.Connection) -> None:
    """FLOOR_RULES used to be a hardcoded, always-on guardrail block; now the
    whole guardrails field is user-editable. Fold the floor text into
    base_guardrails once, ahead of any existing custom text, so nothing the
    user already wrote is lost. Guarded by checking for a stable floor rule
    so re-running init_db is a no-op."""
    from app.cv.instruction import DEFAULT_GUARDRAILS
    # A rule that has been in the default set since the floor merge; used only
    # as a "have we already merged?" sentinel, so keep it a literal even if
    # DEFAULT_GUARDRAILS_RULES is later reordered or reworded.
    sentinel = "Do not add a degree, certification, school, or field of study that is not in the base CV."
    row = conn.execute("SELECT base_guardrails FROM cv_settings WHERE id = 1").fetchone()
    if row is None:
        return  # no settings row saved yet -- get_cv_settings() seeds new rows itself
    current = row[0] or ""
    if sentinel in current:
        return  # already migrated
    merged = DEFAULT_GUARDRAILS if not current.strip() else f"{DEFAULT_GUARDRAILS}\n{current}"
    conn.execute("UPDATE cv_settings SET base_guardrails = ? WHERE id = 1", (merged,))
    conn.commit()


def _migrate_cv_settings_add_directives_template(conn: sqlite3.Connection) -> None:
    from app.cv.instruction import DEFAULT_DIRECTIVES_TEMPLATE
    cols = {r[1] for r in conn.execute("PRAGMA table_info(cv_settings)")}
    if "directives_template" not in cols:
        conn.execute("ALTER TABLE cv_settings ADD COLUMN directives_template TEXT NOT NULL DEFAULT ''")
    conn.execute(
        "UPDATE cv_settings SET directives_template = ? WHERE id = 1 AND directives_template = ''",
        (DEFAULT_DIRECTIVES_TEMPLATE,),
    )
    conn.commit()


def _migrate_seed_and_remap_cv_scope_options(conn: sqlite3.Connection) -> None:
    """cv_scope_options is created by _DDL, so a fresh DB already has the
    (empty) table. Seed it with the four defaults if empty, then remap any
    still-string-keyed job_cv.scope / cv_settings.default_scope arrays (from
    before scopes became a user-editable table) to the seeded ids. Guarded
    by row count / content shape, so re-running is a no-op."""
    if conn.execute("SELECT COUNT(*) FROM cv_scope_options").fetchone()[0] == 0:
        from app.db.queries import _seed_default_scope_options
        _seed_default_scope_options(conn)
    key_to_id = {"select": 1, "reorder": 2, "rephrase": 3, "summary": 4}
    row = conn.execute("SELECT default_scope FROM cv_settings WHERE id = 1").fetchone()
    if row is not None:
        old = json.loads(row[0] or "[]")
        if old and isinstance(old[0], str):
            new = [key_to_id[k] for k in old if k in key_to_id]
            conn.execute("UPDATE cv_settings SET default_scope = ? WHERE id = 1", (json.dumps(new),))
    for jc_row in conn.execute("SELECT job_id, scope FROM job_cv").fetchall():
        old = json.loads(jc_row["scope"] or "[]")
        if old and isinstance(old[0], str):
            new = [key_to_id[k] for k in old if k in key_to_id]
            conn.execute("UPDATE job_cv SET scope = ? WHERE job_id = ?", (json.dumps(new), jc_row["job_id"]))
    conn.commit()


def _migrate_cv_scope_options_add_name(conn: sqlite3.Connection) -> None:
    """Add the `name` short-identifier column. If the scope-options table is
    still exactly the old 4-default set (untouched), replace it with the new
    5-set that carries names and remap the integer ids everywhere they're
    referenced. If the user customised it, only add the empty column — they
    run 'Reset to defaults' in the UI to adopt the new set."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(cv_scope_options)")}
    if "name" not in cols:
        conn.execute("ALTER TABLE cv_scope_options ADD COLUMN name TEXT NOT NULL DEFAULT ''")

    old_defaults = [
        "Include or omit existing bullets and whole sections by relevance to this job.",
        "Reorder bullets and sections, and choose what leads each section, to foreground "
        "the experience this job values most.",
        "Reword existing bullets toward the job's terminology, without introducing a claim "
        "the base CV does not already support or upgrading the scope or seniority of one.",
        "Write a job-specific professional summary synthesised only from facts already "
        "stated in the base CV.",
    ]
    rows = conn.execute(
        "SELECT id, description FROM cv_scope_options ORDER BY sort_order, id"
    ).fetchall()
    if [r[1] for r in rows] != old_defaults:
        conn.commit()
        return  # customised or already migrated — leave it

    # old id order [1,2,3,4] == [select, reorder, rephrase, summary]
    # new id order [1..5]    == [correct, choose, organize, rephrase, introduce]
    remap = {rows[0][0]: 2, rows[1][0]: 3, rows[2][0]: 4, rows[3][0]: 5}
    conn.execute("DELETE FROM cv_scope_options")
    from app.db.queries import _seed_default_scope_options
    _seed_default_scope_options(conn)

    srow = conn.execute("SELECT default_scope FROM cv_settings WHERE id = 1").fetchone()
    if srow is not None:
        old = json.loads(srow[0] or "[]")
        new = [remap[i] for i in old if i in remap]
        conn.execute("UPDATE cv_settings SET default_scope = ? WHERE id = 1", (json.dumps(new),))
    for jc in conn.execute("SELECT job_id, scope FROM job_cv").fetchall():
        old = json.loads(jc[1] or "[]")
        new = [remap[i] for i in old if i in remap]
        conn.execute("UPDATE job_cv SET scope = ? WHERE job_id = ?", (json.dumps(new), jc[0]))
    conn.commit()


def _migrate_job_cv_add_handled_suggestions(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(job_cv)")}
    if "handled_suggestions" not in cols:
        conn.execute(
            "ALTER TABLE job_cv ADD COLUMN handled_suggestions TEXT NOT NULL DEFAULT '[]'"
        )
        conn.commit()


def _migrate_job_cv_add_base_cv_snapshot(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(job_cv)")}
    if "base_cv_snapshot" not in cols:
        conn.execute("ALTER TABLE job_cv ADD COLUMN base_cv_snapshot TEXT NOT NULL DEFAULT ''")
        conn.commit()


def _migrate_job_cv_add_scope_edited_at(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(job_cv)")}
    if "scope_edited_at" not in cols:
        conn.execute("ALTER TABLE job_cv ADD COLUMN scope_edited_at TEXT")
        conn.commit()


def _migrate_job_cv_add_plan_context_hash(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(job_cv)")}
    if "plan_context_hash" not in cols:
        conn.execute("ALTER TABLE job_cv ADD COLUMN plan_context_hash TEXT")
        conn.commit()


def _migrate_job_cv_add_edited_at(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(job_cv)")}
    if "edited_at" not in cols:
        conn.execute("ALTER TABLE job_cv ADD COLUMN edited_at TEXT")
        conn.commit()


def _migrate_job_cv_add_guardrails_checked_at(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(job_cv)")}
    if "guardrails_checked_at" not in cols:
        conn.execute("ALTER TABLE job_cv ADD COLUMN guardrails_checked_at TEXT")
        conn.commit()


def _migrate_cv_scope_options_drop_is_baseline(conn: sqlite3.Connection) -> None:
    # The auto baseline draft is gone — the first manual Update uses the default
    # scope, so is_baseline has no reader. Direct DROP COLUMN.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(cv_scope_options)")}
    if "is_baseline" not in cols:
        return
    conn.execute("ALTER TABLE cv_scope_options DROP COLUMN is_baseline")
    conn.commit()


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL)
    _migrate_sources_fetcher_type(conn)
    _migrate_sources_fetcher_type_manual(conn)
    _migrate_sources_add_d_cookie(conn)
    _migrate_sources_fetcher_type_generic_listing(conn)
    _migrate_sources_drop_http_playwright_types(conn)
    _migrate_sources_fetcher_type_eawork_listing(conn)
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
    _migrate_fetch_runs_add_task_id(conn)
    _migrate_jobs_add_evaluation_completed_at(conn)
    _migrate_jobs_add_status_changed_at(conn)
    _migrate_jobs_split_created_fetched(conn)
    _migrate_jobs_canonicalize_urls(conn)
    _migrate_tasks_group_and_status(conn)
    _migrate_inbox_drop_task_id(conn)
    _migrate_tasks_group_to_parent(conn)
    _migrate_tasks_add_cancelled(conn)
    _migrate_add_jobs_fts(conn)
    _migrate_cv_settings_merge_floor_guardrails(conn)
    _migrate_cv_settings_add_directives_template(conn)
    _migrate_seed_and_remap_cv_scope_options(conn)
    _migrate_cv_scope_options_add_name(conn)
    _migrate_job_cv_drop_preview_pages(conn)
    _migrate_job_cv_add_handled_suggestions(conn)
    _migrate_job_cv_add_base_cv_snapshot(conn)
    _migrate_job_cv_add_scope_edited_at(conn)
    _migrate_job_cv_add_plan_context_hash(conn)
    _migrate_job_cv_add_edited_at(conn)
    _migrate_job_cv_add_guardrails_checked_at(conn)
    _migrate_cv_scope_options_drop_is_baseline(conn)
