from __future__ import annotations
import sqlite3


def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def _rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict]:
    return [dict(r) for r in rows]


# --- Profile ---

def get_profile(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT content FROM profile WHERE id = 1").fetchone()
    return row["content"] if row else ""


def upsert_profile(conn: sqlite3.Connection, content: str) -> None:
    conn.execute(
        "INSERT INTO profile (id, content) VALUES (1, ?) "
        "ON CONFLICT(id) DO UPDATE SET content = excluded.content, updated_at = datetime('now')",
        (content,),
    )
    conn.commit()


# --- Sources ---

def get_sources(conn: sqlite3.Connection, enabled_only: bool = False) -> list[dict]:
    sql = "SELECT * FROM sources"
    if enabled_only:
        sql += " WHERE enabled = 1"
    return _rows_to_dicts(conn.execute(sql).fetchall())


def get_source(conn: sqlite3.Connection, source_id: int) -> dict | None:
    return _row_to_dict(conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone())


def insert_source(conn: sqlite3.Connection, name: str, url: str, fetcher_type: str) -> int:
    cur = conn.execute(
        "INSERT INTO sources (name, url, fetcher_type) VALUES (?, ?, ?)",
        (name, url, fetcher_type),
    )
    conn.commit()
    return cur.lastrowid


def update_source(
    conn: sqlite3.Connection, source_id: int, url: str, fetcher_type: str, enabled: bool
) -> None:
    conn.execute(
        "UPDATE sources SET url = ?, fetcher_type = ?, enabled = ? WHERE id = ?",
        (url, fetcher_type, int(enabled), source_id),
    )
    conn.commit()


# --- Scenarios ---

def get_scenarios(conn: sqlite3.Connection) -> list[dict]:
    return _rows_to_dicts(conn.execute("SELECT * FROM scenarios ORDER BY created_at DESC").fetchall())


def insert_scenario(conn: sqlite3.Connection, name: str, description: str) -> int:
    cur = conn.execute(
        "INSERT INTO scenarios (name, description) VALUES (?, ?)", (name, description)
    )
    conn.commit()
    return cur.lastrowid


def update_scenario(conn: sqlite3.Connection, scenario_id: int, name: str, description: str) -> None:
    conn.execute(
        "UPDATE scenarios SET name = ?, description = ? WHERE id = ?",
        (name, description, scenario_id),
    )
    conn.commit()


def get_scenario(conn: sqlite3.Connection, scenario_id: int) -> dict | None:
    return _row_to_dict(conn.execute("SELECT * FROM scenarios WHERE id = ?", (scenario_id,)).fetchone())


# --- Criteria ---

def get_criteria(conn: sqlite3.Connection, scenario_id: int) -> list[dict]:
    return _rows_to_dicts(
        conn.execute(
            "SELECT * FROM criteria WHERE scenario_id = ? ORDER BY created_at", (scenario_id,)
        ).fetchall()
    )


def insert_criterion(
    conn: sqlite3.Connection, scenario_id: int, text: str, weight: str, source: str = "manual"
) -> int:
    cur = conn.execute(
        "INSERT INTO criteria (scenario_id, text, weight, source) VALUES (?, ?, ?, ?)",
        (scenario_id, text, weight, source),
    )
    conn.commit()
    return cur.lastrowid


def get_criterion(conn: sqlite3.Connection, criterion_id: int) -> dict | None:
    return _row_to_dict(conn.execute("SELECT * FROM criteria WHERE id = ?", (criterion_id,)).fetchone())


def update_criterion(conn: sqlite3.Connection, criterion_id: int, text: str, weight: str) -> None:
    conn.execute(
        "UPDATE criteria SET text = ?, weight = ? WHERE id = ?",
        (text, weight, criterion_id),
    )
    conn.commit()


def delete_criterion(conn: sqlite3.Connection, criterion_id: int) -> None:
    conn.execute("DELETE FROM criteria WHERE id = ?", (criterion_id,))
    conn.commit()


# --- Jobs ---

def url_exists(conn: sqlite3.Connection, url: str) -> bool:
    return conn.execute("SELECT 1 FROM jobs WHERE url = ?", (url,)).fetchone() is not None


def insert_job(
    conn: sqlite3.Connection,
    *,
    source_id: int,
    url: str,
    title: str,
    company: str,
    raw_text: str,
) -> int:
    cur = conn.execute(
        "INSERT INTO jobs (source_id, url, title, company, raw_text) VALUES (?, ?, ?, ?, ?)",
        (source_id, url, title, company, raw_text),
    )
    conn.commit()
    return cur.lastrowid


def update_job_pipeline(
    conn: sqlite3.Connection,
    job_id: int,
    *,
    simplified_content: str,
    content_type: str,
    title: str | None = None,
    summary: str = "",
    headline: str = "",
) -> None:
    conn.execute(
        """UPDATE jobs SET
            simplified_content = ?,
            content_type = ?,
            title = COALESCE(?, title),
            summary = ?,
            headline = ?
        WHERE id = ?""",
        (simplified_content, content_type, title, summary, headline, job_id),
    )
    conn.commit()


def upsert_job_score(
    conn: sqlite3.Connection,
    job_id: int,
    scenario_id: int,
    score: float,
    reasoning: str,
    version_hash: str,
) -> None:
    conn.execute(
        """INSERT INTO job_scores (job_id, scenario_id, relevance_score, score_reasoning, scenario_version_hash)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(job_id, scenario_id) DO UPDATE SET
            relevance_score = excluded.relevance_score,
            score_reasoning = excluded.score_reasoning,
            scenario_version_hash = excluded.scenario_version_hash,
            evaluated_at = datetime('now')""",
        (job_id, scenario_id, score, reasoning, version_hash),
    )
    conn.commit()


def get_job_score(conn: sqlite3.Connection, job_id: int, scenario_id: int) -> dict | None:
    return _row_to_dict(
        conn.execute(
            "SELECT * FROM job_scores WHERE job_id = ? AND scenario_id = ?", (job_id, scenario_id)
        ).fetchone()
    )


def get_job_score_hashes(conn: sqlite3.Connection, scenario_id: int) -> dict[int, str]:
    rows = conn.execute(
        "SELECT job_id, scenario_version_hash FROM job_scores WHERE scenario_id = ?", (scenario_id,)
    ).fetchall()
    return {r["job_id"]: r["scenario_version_hash"] for r in rows}


def update_job_feedback(
    conn: sqlite3.Connection,
    job_id: int,
    status: str,
    note: str,
    feedback_scenario_id: int | None = None,
) -> None:
    conn.execute(
        "UPDATE jobs SET status = ?, feedback_note = ?, feedback_scenario_id = ? WHERE id = ?",
        (status, note, feedback_scenario_id, job_id),
    )
    conn.commit()


_BEST_SCORE_SELECT = """
    jobs.*,
    best.scenario_id AS best_scenario_id,
    best.relevance_score AS best_score,
    best.score_reasoning AS best_score_reasoning,
    scenarios.name AS best_scenario_name
"""

_BEST_SCORE_JOIN = """
    FROM jobs
    LEFT JOIN (
        SELECT job_id, scenario_id, relevance_score, score_reasoning,
               ROW_NUMBER() OVER (PARTITION BY job_id ORDER BY relevance_score DESC, scenario_id ASC) AS rn
        FROM job_scores
    ) best ON best.job_id = jobs.id AND best.rn = 1
    LEFT JOIN scenarios ON scenarios.id = best.scenario_id
"""


def get_jobs(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    content_type: str | None = None,
) -> list[dict]:
    clauses, params = [], []
    if status is not None:
        clauses.append("jobs.status = ?")
        params.append(status)
    if content_type is not None:
        clauses.append("jobs.content_type = ?")
        params.append(content_type)
    sql = f"SELECT {_BEST_SCORE_SELECT} {_BEST_SCORE_JOIN}"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY best.relevance_score DESC NULLS LAST, jobs.fetched_at DESC"
    return _rows_to_dicts(conn.execute(sql, params).fetchall())


def get_jobs_missing_headline(conn: sqlite3.Connection) -> list[dict]:
    sql = """
        SELECT * FROM jobs
        WHERE headline = ''
          AND content_type IN ('job_posting', 'lead')
          AND simplified_content != ''
        ORDER BY fetched_at
    """
    return _rows_to_dicts(conn.execute(sql).fetchall())


def get_job_counts(conn: sqlite3.Connection) -> dict[str, int]:
    counts = {"new": 0, "accepted": 0, "rejected": 0, "invalid": 0, "lead": 0}
    for status, n in conn.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status").fetchall():
        counts[status] = n
    counts["lead"] = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE content_type = ?", ("lead",)
    ).fetchone()[0]
    return counts


def get_job(conn: sqlite3.Connection, job_id: int) -> dict | None:
    sql = f"SELECT {_BEST_SCORE_SELECT} {_BEST_SCORE_JOIN} WHERE jobs.id = ?"
    return _row_to_dict(conn.execute(sql, (job_id,)).fetchone())


def get_recent_feedback_notes(
    conn: sqlite3.Connection, scenario_id: int, limit: int = 20
) -> list[str]:
    rows = conn.execute(
        """SELECT feedback_note FROM jobs
        WHERE feedback_scenario_id = ?
        AND feedback_note IS NOT NULL AND feedback_note != ''
        ORDER BY fetched_at DESC LIMIT ?""",
        (scenario_id, limit),
    ).fetchall()
    return [r["feedback_note"] for r in rows]


# --- Fetch runs ---

def start_fetch_run(conn: sqlite3.Connection, source_id: int) -> int:
    cur = conn.execute("INSERT INTO fetch_runs (source_id) VALUES (?)", (source_id,))
    conn.commit()
    return cur.lastrowid


def complete_fetch_run(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    jobs_found: int,
    jobs_new: int,
    error: str | None = None,
) -> None:
    conn.execute(
        """UPDATE fetch_runs SET
            completed_at = datetime('now'),
            jobs_found = ?,
            jobs_new = ?,
            error = ?
        WHERE id = ?""",
        (jobs_found, jobs_new, error, run_id),
    )
    conn.commit()


def get_recent_fetch_runs(conn: sqlite3.Connection) -> list[dict]:
    return _rows_to_dicts(
        conn.execute(
            "SELECT fr.*, s.name as source_name FROM fetch_runs fr "
            "JOIN sources s ON s.id = fr.source_id "
            "ORDER BY fr.started_at DESC LIMIT 50"
        ).fetchall()
    )
