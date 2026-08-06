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


def update_scenario(
    conn: sqlite3.Connection, scenario_id: int, name: str, description: str, gate_threshold: float = 0.7
) -> None:
    conn.execute(
        "UPDATE scenarios SET name = ?, description = ?, gate_threshold = ? WHERE id = ?",
        (name, description, gate_threshold, scenario_id),
    )
    conn.commit()


def get_scenario(conn: sqlite3.Connection, scenario_id: int) -> dict | None:
    return _row_to_dict(conn.execute("SELECT * FROM scenarios WHERE id = ?", (scenario_id,)).fetchone())


# --- Criteria ---

def get_criteria(conn: sqlite3.Connection, scenario_id: int) -> list[dict]:
    return _rows_to_dicts(
        conn.execute(
            """
            SELECT * FROM criteria WHERE scenario_id = ?
            ORDER BY CASE weight WHEN 'must' THEN 0 WHEN 'prefer' THEN 1 WHEN 'avoid' THEN 2 ELSE 3 END,
                     created_at
            """,
            (scenario_id,),
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
    published_at: str | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO jobs (source_id, url, title, company, raw_text, published_at) VALUES (?, ?, ?, ?, ?, ?)",
        (source_id, url, title, company, raw_text, published_at),
    )
    conn.commit()
    return cur.lastrowid


def get_all_job_urls(conn: sqlite3.Connection) -> frozenset[str]:
    rows = conn.execute("SELECT url FROM jobs").fetchall()
    return frozenset(r["url"] for r in rows)


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


def update_job_fit(
    conn: sqlite3.Connection,
    job_id: int,
    interest_score: float,
    interest_reasoning: str,
    attainability_score: float,
    attainability_reasoning: str,
    profile_version_hash: str,
) -> None:
    fit_score = (interest_score + attainability_score) / 2
    conn.execute(
        """UPDATE jobs SET
            interest_score = ?,
            interest_reasoning = ?,
            attainability_score = ?,
            attainability_reasoning = ?,
            fit_score = ?,
            profile_version_hash = ?
        WHERE id = ?""",
        (interest_score, interest_reasoning, attainability_score, attainability_reasoning, fit_score, profile_version_hash, job_id),
    )
    conn.commit()


def upsert_scenario_feedback(
    conn: sqlite3.Connection, job_id: int, scenario_id: int, note: str, direction: str | None = None
) -> None:
    note = note.strip()
    if direction not in ("higher", "lower"):
        direction = None
    existing = conn.execute(
        "SELECT note, direction FROM scenario_feedback WHERE job_id = ? AND scenario_id = ?",
        (job_id, scenario_id),
    ).fetchone()
    if not note and direction is None:
        # Explicit retraction: no direction and nothing new to say.
        if existing is not None:
            conn.execute(
                "DELETE FROM scenario_feedback WHERE job_id = ? AND scenario_id = ?", (job_id, scenario_id)
            )
            conn.commit()
        return
    # A blank note means "nothing new to say this round," not "clear the
    # existing note" — the combined feedback form clears its textareas after
    # every save, so a later submit touching only one scenario's fields must
    # not silently wipe another scenario's already-saved note.
    final_note = note or (existing["note"] if existing is not None else "")
    if existing is not None and existing["note"] == final_note and existing["direction"] == direction:
        return  # nothing actually changed — leave handled_at alone
    conn.execute(
        """INSERT INTO scenario_feedback (job_id, scenario_id, note, direction) VALUES (?, ?, ?, ?)
        ON CONFLICT(job_id, scenario_id) DO UPDATE SET
            note = excluded.note, direction = excluded.direction, handled_at = NULL""",
        (job_id, scenario_id, final_note, direction),
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


def reset_job(conn: sqlite3.Connection, job_id: int) -> None:
    conn.execute(
        """UPDATE jobs SET
            status = 'new',
            content_type = NULL,
            simplified_content = '',
            summary = '',
            headline = '',
            feedback_note = NULL,
            feedback_handled_at = NULL,
            interest_score = NULL,
            interest_reasoning = NULL,
            attainability_score = NULL,
            attainability_reasoning = NULL,
            fit_score = NULL,
            profile_version_hash = NULL
        WHERE id = ?""",
        (job_id,),
    )
    conn.execute("DELETE FROM job_scores WHERE job_id = ?", (job_id,))
    conn.execute("DELETE FROM scenario_feedback WHERE job_id = ?", (job_id,))
    conn.commit()


def update_job_feedback(conn: sqlite3.Connection, job_id: int, status: str, note: str) -> None:
    conn.execute(
        "UPDATE jobs SET status = ?, feedback_note = ? WHERE id = ?",
        (status, note, job_id),
    )
    conn.commit()


_GATE_SELECT = """
    jobs.*,
    gate.passed_count AS passed_gate_count,
    gate.passed_scenario_names AS passed_scenario_names,
    gate.top_passed_scenario_id AS top_passed_scenario_id,
    gate.top_passed_scenario_name AS top_passed_scenario_name,
    scored.scored_count AS scored_gate_count
"""

_GATE_JOIN = """
    FROM jobs
    LEFT JOIN (
        SELECT ranked.job_id,
               COUNT(*) AS passed_count,
               GROUP_CONCAT(ranked.scenario_name, ', ') AS passed_scenario_names,
               MAX(CASE WHEN ranked.rn = 1 THEN ranked.scenario_id END) AS top_passed_scenario_id,
               MAX(CASE WHEN ranked.rn = 1 THEN ranked.scenario_name END) AS top_passed_scenario_name
        FROM (
            SELECT js.job_id, js.scenario_id, s.name AS scenario_name,
                   ROW_NUMBER() OVER (
                       PARTITION BY js.job_id
                       ORDER BY js.relevance_score DESC, js.scenario_id ASC
                   ) AS rn
            FROM job_scores js
            JOIN scenarios s ON s.id = js.scenario_id
            WHERE js.relevance_score >= s.gate_threshold
        ) ranked
        GROUP BY ranked.job_id
    ) gate ON gate.job_id = jobs.id
    LEFT JOIN (
        SELECT job_id, COUNT(*) AS scored_count FROM job_scores GROUP BY job_id
    ) scored ON scored.job_id = jobs.id
"""


def get_jobs(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    content_type: str | None = None,
    gate_passed_only: bool = False,
) -> list[dict]:
    clauses, params = [], []
    if status is not None:
        clauses.append("jobs.status = ?")
        params.append(status)
    if content_type is not None:
        clauses.append("jobs.content_type = ?")
        params.append(content_type)
    if gate_passed_only:
        clauses.append("(scored.scored_count IS NULL OR gate.passed_count > 0)")
    sql = f"SELECT {_GATE_SELECT} {_GATE_JOIN}"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY jobs.fit_score DESC NULLS LAST, jobs.fetched_at DESC"
    return _rows_to_dicts(conn.execute(sql, params).fetchall())


def get_job_counts(conn: sqlite3.Connection) -> dict[str, int]:
    counts = {"new": 0, "accepted": 0, "rejected": 0, "invalid": 0, "lead": 0}
    for status, n in conn.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status").fetchall():
        counts[status] = n
    counts["lead"] = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE content_type = ?", ("lead",)
    ).fetchone()[0]
    return counts


def get_job(conn: sqlite3.Connection, job_id: int) -> dict | None:
    sql = f"SELECT {_GATE_SELECT} {_GATE_JOIN} WHERE jobs.id = ?"
    return _row_to_dict(conn.execute(sql, (job_id,)).fetchone())


def _recent_scenario_feedback_rows(conn: sqlite3.Connection, scenario_id: int, limit: int) -> list[dict]:
    return _rows_to_dicts(
        conn.execute(
            """
            SELECT job_id, note, direction FROM scenario_feedback
            WHERE scenario_id = ? AND direction IS NOT NULL AND handled_at IS NULL
            ORDER BY created_at DESC LIMIT ?
            """,
            (scenario_id, limit),
        ).fetchall()
    )


def get_job_scores(conn: sqlite3.Connection, job_id: int) -> list[dict]:
    return _rows_to_dicts(
        conn.execute(
            """
            SELECT job_scores.*, scenarios.name AS scenario_name, scenarios.gate_threshold AS scenario_gate_threshold,
                   scenario_feedback.note AS feedback_note, scenario_feedback.direction AS feedback_direction
            FROM job_scores
            JOIN scenarios ON scenarios.id = job_scores.scenario_id
            LEFT JOIN scenario_feedback
                ON scenario_feedback.job_id = job_scores.job_id AND scenario_feedback.scenario_id = job_scores.scenario_id
            WHERE job_scores.job_id = ?
            ORDER BY job_scores.relevance_score DESC
            """,
            (job_id,),
        ).fetchall()
    )


def get_recent_feedback_notes(conn: sqlite3.Connection, scenario_id: int, limit: int = 20) -> list[dict]:
    return [
        {"direction": r["direction"], "note": r["note"]}
        for r in _recent_scenario_feedback_rows(conn, scenario_id, limit)
    ]


def get_recent_feedback_job_ids(conn: sqlite3.Connection, scenario_id: int, limit: int = 20) -> list[int]:
    return [r["job_id"] for r in _recent_scenario_feedback_rows(conn, scenario_id, limit)]


def mark_feedback_handled(conn: sqlite3.Connection, scenario_id: int, job_ids: list[int]) -> None:
    if not job_ids:
        return
    placeholders = ",".join("?" * len(job_ids))
    conn.execute(
        f"""UPDATE scenario_feedback SET handled_at = datetime('now')
        WHERE scenario_id = ? AND job_id IN ({placeholders})""",
        [scenario_id, *job_ids],
    )
    conn.commit()


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


def has_completed_fetch_run(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM fetch_runs WHERE completed_at IS NOT NULL LIMIT 1"
    ).fetchone()
    return row is not None


def get_last_fetch_completed_at(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT MAX(completed_at) FROM fetch_runs WHERE completed_at IS NOT NULL"
    ).fetchone()
    return row[0] if row and row[0] else None
