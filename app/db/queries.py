from __future__ import annotations
import json
import sqlite3
from app.url_canon import canonicalize_url


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


def get_source_by_url(conn: sqlite3.Connection, url: str) -> dict | None:
    return _row_to_dict(conn.execute("SELECT * FROM sources WHERE url = ?", (url,)).fetchone())


def insert_source(conn: sqlite3.Connection, name: str, url: str, fetcher_type: str) -> int:
    cur = conn.execute(
        "INSERT INTO sources (name, url, fetcher_type) VALUES (?, ?, ?)",
        (name, url, fetcher_type),
    )
    conn.commit()
    return cur.lastrowid


def get_or_create_manual_source(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT id FROM sources WHERE fetcher_type = 'manual'").fetchone()
    if row is not None:
        return row["id"]
    return insert_source(conn, "Manual", "", "manual")


def update_source(
    conn: sqlite3.Connection, source_id: int, name: str, url: str, fetcher_type: str, enabled: bool
) -> None:
    conn.execute(
        "UPDATE sources SET name = ?, url = ?, fetcher_type = ?, enabled = ? WHERE id = ?",
        (name, url, fetcher_type, int(enabled), source_id),
    )
    conn.commit()


def set_source_cookie(conn: sqlite3.Connection, source_id: int, cookie: str) -> None:
    conn.execute("UPDATE sources SET d_cookie = ? WHERE id = ?", (cookie, source_id))
    conn.commit()


def get_job_counts_by_source(conn: sqlite3.Connection) -> dict[int, int]:
    rows = conn.execute("SELECT source_id, COUNT(*) AS n FROM jobs GROUP BY source_id").fetchall()
    return {row["source_id"]: row["n"] for row in rows}


def count_jobs_by_source(conn: sqlite3.Connection, source_id: int) -> int:
    row = conn.execute("SELECT COUNT(*) FROM jobs WHERE source_id = ?", (source_id,)).fetchone()
    return row[0]


def delete_source(conn: sqlite3.Connection, source_id: int) -> None:
    conn.execute("DELETE FROM fetch_runs WHERE source_id = ?", (source_id,))
    conn.execute("DELETE FROM jobs WHERE source_id = ?", (source_id,))
    conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))
    conn.commit()


def delete_jobs(conn: sqlite3.Connection, job_ids: list[int]) -> None:
    if not job_ids:
        return
    placeholders = ",".join("?" for _ in job_ids)
    conn.execute(f"DELETE FROM jobs WHERE status = 'trash' AND id IN ({placeholders})", job_ids)
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


def get_job_by_url(conn: sqlite3.Connection, url: str) -> dict | None:
    return _row_to_dict(conn.execute("SELECT * FROM jobs WHERE url = ?", (url,)).fetchone())


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
        "INSERT INTO jobs (source_id, url, title, company, raw_text, published_at) "
        "VALUES (?, ?, ?, ?, ?, COALESCE(?, datetime('now')))",
        (source_id, url, title, company, raw_text, published_at),
    )
    conn.commit()
    return cur.lastrowid


def get_all_job_urls(conn: sqlite3.Connection) -> frozenset[str]:
    rows = conn.execute("SELECT url FROM jobs").fetchall()
    return frozenset(canonicalize_url(r["url"]) for r in rows)


def job_exists(conn: sqlite3.Connection, job_id: int) -> bool:
    return conn.execute("SELECT 1 FROM jobs WHERE id = ?", (job_id,)).fetchone() is not None


def delete_job(conn: sqlite3.Connection, job_id: int) -> None:
    conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
    conn.commit()


def update_job_pipeline(
    conn: sqlite3.Connection,
    job_id: int,
    *,
    simplified_content: str,
    content_type: str,
    title: str | None = None,
    summary: str = "",
    headline: str = "",
    company: str = "",
    published_at: str = "",
) -> None:
    conn.execute(
        """UPDATE jobs SET
            simplified_content = ?,
            content_type = ?,
            title = COALESCE(?, title),
            summary = ?,
            headline = ?,
            company = COALESCE(NULLIF(?, ''), company),
            published_at = COALESCE(NULLIF(?, ''), published_at)
        WHERE id = ?""",
        (simplified_content, content_type, title, summary, headline, company, published_at, job_id),
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
            feedback_handled_at = NULL,
            interest_score = NULL,
            interest_reasoning = NULL,
            attainability_score = NULL,
            attainability_reasoning = NULL,
            fit_score = NULL,
            profile_version_hash = NULL,
            gate_override = 0,
            evaluation_completed_at = NULL
        WHERE id = ?""",
        (job_id,),
    )
    conn.execute("DELETE FROM job_scores WHERE job_id = ?", (job_id,))
    conn.execute("DELETE FROM scenario_feedback WHERE job_id = ?", (job_id,))
    conn.commit()


def mark_job_evaluation_complete(conn: sqlite3.Connection, job_id: int) -> None:
    conn.execute(
        "UPDATE jobs SET evaluation_completed_at = datetime('now') WHERE id = ?", (job_id,)
    )
    conn.commit()


def mark_job_gate_override(conn: sqlite3.Connection, job_id: int) -> None:
    conn.execute("UPDATE jobs SET gate_override = 1 WHERE id = ?", (job_id,))
    conn.commit()


def update_job_feedback(conn: sqlite3.Connection, job_id: int, status: str, note: str) -> None:
    conn.execute(
        "UPDATE jobs SET status = ?, feedback_note = ?, feedback_handled_at = NULL WHERE id = ?",
        (status, note, job_id),
    )
    conn.commit()


def get_unhandled_profile_notes(conn: sqlite3.Connection, limit: int = 30) -> list[dict]:
    return _rows_to_dicts(
        conn.execute(
            """
            SELECT id, status, feedback_note FROM jobs
            WHERE feedback_note IS NOT NULL AND feedback_note != ''
              AND feedback_handled_at IS NULL
            ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    )


def mark_profile_feedback_handled(conn: sqlite3.Connection, job_ids: list[int]) -> None:
    if not job_ids:
        return
    placeholders = ",".join("?" for _ in job_ids)
    conn.execute(
        f"UPDATE jobs SET feedback_handled_at = datetime('now') WHERE id IN ({placeholders})",
        job_ids,
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

_GATE_PASSED_CLAUSE = """
    (jobs.gate_override = 1
     OR (jobs.content_type IN ('job_posting', 'lead')
         AND jobs.evaluation_completed_at IS NOT NULL
         AND (scored.scored_count IS NULL OR gate.passed_count > 0)))
"""

_GATE_FAILED_CLAUSE = """
    jobs.evaluation_completed_at IS NOT NULL
    AND scored.scored_count IS NOT NULL AND (gate.passed_count IS NULL OR gate.passed_count = 0)
    AND jobs.gate_override = 0
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
    gate_status: str | None = None,
    source_id: int | None = None,
    scenario_id: int | None = None,
) -> list[dict]:
    clauses, params = [], []
    if status is not None:
        clauses.append("jobs.status = ?")
        params.append(status)
    if content_type is not None:
        clauses.append("jobs.content_type = ?")
        params.append(content_type)
    if source_id is not None:
        clauses.append("jobs.source_id = ?")
        params.append(source_id)
    if scenario_id is not None:
        clauses.append(
            """EXISTS (
                SELECT 1 FROM job_scores js JOIN scenarios s ON s.id = js.scenario_id
                WHERE js.job_id = jobs.id AND js.scenario_id = ? AND js.relevance_score >= s.gate_threshold
            )"""
        )
        params.append(scenario_id)
    if gate_status == "passed":
        clauses.append(_GATE_PASSED_CLAUSE)
    elif gate_status == "failed":
        clauses.append(_GATE_FAILED_CLAUSE)
    sql = f"SELECT {_GATE_SELECT} {_GATE_JOIN}"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY jobs.fit_score DESC NULLS LAST, jobs.fetched_at DESC"
    return _rows_to_dicts(conn.execute(sql, params).fetchall())


def get_job_counts(conn: sqlite3.Connection) -> dict[str, int]:
    counts = {"new": 0, "accepted": 0, "rejected": 0, "trash": 0, "lead": 0, "not_relevant": 0}
    for status, n in conn.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status").fetchall():
        counts[status] = n
    counts["lead"] = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE content_type = ? AND status = 'new'", ("lead",)
    ).fetchone()[0]
    not_relevant = conn.execute(
        f"""
        SELECT COUNT(*) {_GATE_JOIN}
        WHERE jobs.status = 'new' AND jobs.content_type = 'job_posting' AND {_GATE_FAILED_CLAUSE}
        """
    ).fetchone()[0]
    counts["not_relevant"] = not_relevant
    counts["new"] = conn.execute(
        f"SELECT COUNT(*) {_GATE_JOIN} WHERE jobs.status = 'new' AND {_GATE_PASSED_CLAUSE}"
    ).fetchone()[0]
    return counts


def get_job(conn: sqlite3.Connection, job_id: int) -> dict | None:
    sql = f"SELECT {_GATE_SELECT} {_GATE_JOIN} WHERE jobs.id = ?"
    return _row_to_dict(conn.execute(sql, (job_id,)).fetchone())


def _recent_scenario_feedback_rows(conn: sqlite3.Connection, scenario_id: int, limit: int, days: int = 30) -> list[dict]:
    return _rows_to_dicts(
        conn.execute(
            """
            SELECT job_id, note, direction, created_at FROM scenario_feedback
            WHERE scenario_id = ? AND direction IS NOT NULL AND handled_at IS NULL
              AND created_at >= datetime('now', ? || ' days')
            ORDER BY created_at DESC LIMIT ?
            """,
            (scenario_id, f'-{days}', limit),
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


def get_recent_feedback_notes(conn: sqlite3.Connection, scenario_id: int, limit: int = 20, days: int = 30) -> list[dict]:
    return [
        {"direction": r["direction"], "note": r["note"]}
        for r in _recent_scenario_feedback_rows(conn, scenario_id, limit, days)
    ]


def get_recent_feedback_anchor(conn: sqlite3.Connection, scenario_id: int, limit: int = 20, days: int = 30) -> str | None:
    rows = _recent_scenario_feedback_rows(conn, scenario_id, limit, days)
    return rows[0]["created_at"] if rows else None


def get_recent_feedback_counts(conn: sqlite3.Connection, scenario_id: int, limit: int = 20, days: int = 30) -> dict:
    unhandled = _recent_scenario_feedback_rows(conn, scenario_id, limit, days)
    handled = _rows_to_dicts(
        conn.execute(
            """
            SELECT direction FROM scenario_feedback
            WHERE scenario_id = ? AND direction IS NOT NULL AND handled_at IS NOT NULL
              AND created_at >= datetime('now', ? || ' days')
            """,
            (scenario_id, f'-{days}'),
        ).fetchall()
    )
    return {
        "unhandled_higher": sum(1 for r in unhandled if r["direction"] == "higher"),
        "unhandled_lower": sum(1 for r in unhandled if r["direction"] == "lower"),
        "handled_higher": sum(1 for r in handled if r["direction"] == "higher"),
        "handled_lower": sum(1 for r in handled if r["direction"] == "lower"),
    }


def mark_feedback_handled(conn: sqlite3.Connection, scenario_id: int, anchor: str | None) -> None:
    if not anchor:
        return
    conn.execute(
        """UPDATE scenario_feedback SET handled_at = datetime('now')
        WHERE scenario_id = ? AND direction IS NOT NULL AND handled_at IS NULL AND created_at <= ?""",
        (scenario_id, anchor),
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
    auth_error: bool = False,
) -> None:
    conn.execute(
        """UPDATE fetch_runs SET
            completed_at = datetime('now'),
            jobs_found = ?,
            jobs_new = ?,
            error = ?,
            auth_error = ?
        WHERE id = ?""",
        (jobs_found, jobs_new, error, int(auth_error), run_id),
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


def get_fetch_stats_by_source(conn: sqlite3.Connection) -> dict[int, dict]:
    rows = conn.execute(
        """
        SELECT source_id,
               COUNT(*) AS run_count,
               SUM(jobs_new) AS total_new,
               SUM(jobs_found) AS total_found,
               MAX(CASE WHEN error IS NULL THEN completed_at END) AS last_success_at,
               MAX(CASE WHEN auth_error THEN completed_at END) AS last_auth_error_at
        FROM fetch_runs
        GROUP BY source_id
        """
    ).fetchall()
    return {row["source_id"]: dict(row) for row in rows}


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


# --- Tasks ---

def _decode_task(row: dict) -> dict:
    row["params"] = json.loads(row["params"]) if row["params"] else {}
    row["result"] = json.loads(row["result"]) if row["result"] else None
    return row


def find_active_task(conn: sqlite3.Connection, kind: str, params: dict) -> dict | None:
    params_json = json.dumps(params, sort_keys=True)
    row = conn.execute(
        "SELECT * FROM tasks WHERE kind = ? AND params = ? AND status IN ('queued', 'running') "
        "ORDER BY created_at DESC LIMIT 1",
        (kind, params_json),
    ).fetchone()
    return _decode_task(_row_to_dict(row)) if row is not None else None


def enqueue_task(
    conn: sqlite3.Connection, kind: str, params: dict, parent_task_id: int | None = None
) -> dict:
    """Returns the task dict, with an extra (non-persisted) "already_active"
    key: True when an identical queued/running task was found and reused
    instead of a new one being created.

    A child step of a multi-step action passes `parent_task_id` = the id of
    the *root* task (chains are flattened — a step never points at another
    step)."""
    existing = find_active_task(conn, kind, params)
    if existing is not None:
        existing["already_active"] = True
        return existing
    params_json = json.dumps(params, sort_keys=True)
    cur = conn.execute(
        "INSERT INTO tasks (kind, params, parent_task_id) VALUES (?, ?, ?)",
        (kind, params_json, parent_task_id),
    )
    conn.commit()
    task = get_task(conn, cur.lastrowid)
    task["already_active"] = False
    return task


def set_task_needs_action(conn: sqlite3.Connection, task_id: int) -> None:
    conn.execute(
        "UPDATE tasks SET status = 'needs_action', "
        "finished_at = COALESCE(finished_at, datetime('now')) WHERE id = ?",
        (task_id,),
    )
    conn.commit()


def resolve_task(conn: sqlite3.Connection, task_id: int) -> None:
    conn.execute(
        "UPDATE tasks SET status = 'done', "
        "finished_at = COALESCE(finished_at, datetime('now')) WHERE id = ?",
        (task_id,),
    )
    conn.commit()


def dismiss_task(conn: sqlite3.Connection, task_id: int) -> None:
    conn.execute(
        "UPDATE tasks SET status = 'dismissed', "
        "finished_at = COALESCE(finished_at, datetime('now')) WHERE id = ?",
        (task_id,),
    )
    conn.commit()


def get_task_children(conn: sqlite3.Connection, root_id: int) -> list[dict]:
    """Every step-task pointing at this root, oldest first. Empty for a
    childless / non-root task."""
    rows = conn.execute(
        "SELECT * FROM tasks WHERE parent_task_id = ? ORDER BY created_at ASC, id ASC", (root_id,)
    ).fetchall()
    return [_decode_task(d) for d in _rows_to_dicts(rows)]


_DASHBOARD_WINDOW_HOURS = 24


def get_dashboard_tasks(conn: sqlite3.Connection) -> list[dict]:
    """Entries for the Start-page Tasks section, newest-activity first, one per
    root. Each entry: {root: task dict, children: [task dict, ...]}.

    A task counts toward the window if it (or, for a root, any subtree task) is
    active (queued/running/needs_action) or finished within the last 24h.
    Children never appear as their own top-level entry."""
    rows = conn.execute(
        """
        SELECT * FROM tasks
        WHERE status IN ('queued', 'running', 'needs_action')
           OR (status IN ('done', 'failed', 'dismissed')
               AND finished_at IS NOT NULL
               AND finished_at >= datetime('now', ?))
        ORDER BY created_at ASC, id ASC
        """,
        (f"-{_DASHBOARD_WINDOW_HOURS} hours",),
    ).fetchall()
    tasks = [_decode_task(d) for d in _rows_to_dicts(rows)]
    # Distinct roots, in first-seen order.
    root_ids: list[int] = []
    for t in tasks:
        rid = t["parent_task_id"] or t["id"]
        if rid not in root_ids:
            root_ids.append(rid)
    entries = []
    for rid in root_ids:
        root = get_task(conn, rid)
        if root is None:
            continue
        children = get_task_children(conn, rid)
        entries.append({"root": root, "children": children})
    entries.sort(
        key=lambda e: max(x["created_at"] for x in [e["root"], *e["children"]]), reverse=True
    )
    return entries


def get_recent_terminal_tasks(
    conn: sqlite3.Connection, limit: int = 50, offset: int = 0, status: str | None = None
) -> list[dict]:
    """History-page listing. status=None -> all; else exact match."""
    where = "1=1" if status is None else "status = :status"
    rows = conn.execute(
        f"SELECT * FROM tasks WHERE {where} ORDER BY created_at DESC, id DESC "
        "LIMIT :limit OFFSET :offset",
        {"status": status, "limit": limit, "offset": offset},
    ).fetchall()
    return [_decode_task(d) for d in _rows_to_dicts(rows)]


def get_task(conn: sqlite3.Connection, task_id: int) -> dict | None:
    row = _row_to_dict(conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone())
    return _decode_task(row) if row is not None else None


def get_active_tasks(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM tasks WHERE status IN ('queued', 'running') ORDER BY created_at ASC"
    ).fetchall()
    return [_decode_task(d) for d in _rows_to_dicts(rows)]


def claim_next_task(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute(
        "SELECT id FROM tasks WHERE status = 'queued' ORDER BY created_at ASC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    conn.execute(
        "UPDATE tasks SET status = 'running', started_at = datetime('now') WHERE id = ?",
        (row["id"],),
    )
    conn.commit()
    return get_task(conn, row["id"])


def append_task_log(conn: sqlite3.Connection, task_id: int, line: str) -> None:
    conn.execute(
        "UPDATE tasks SET log = log || ? || char(10) WHERE id = ?", (line, task_id)
    )
    conn.commit()


def complete_task(conn: sqlite3.Connection, task_id: int, result: dict) -> None:
    conn.execute(
        "UPDATE tasks SET status = 'done', result = ?, finished_at = datetime('now') WHERE id = ?",
        (json.dumps(result), task_id),
    )
    conn.commit()


def fail_task(conn: sqlite3.Connection, task_id: int, error: str) -> None:
    conn.execute(
        "UPDATE tasks SET status = 'failed', error = ?, finished_at = datetime('now') WHERE id = ?",
        (error, task_id),
    )
    conn.commit()


def recover_interrupted_tasks(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        "UPDATE tasks SET status = 'failed', error = 'interrupted by restart', "
        "finished_at = datetime('now') WHERE status = 'running'"
    )
    conn.commit()
    return cur.rowcount


# --- Inbox ---

def create_inbox_item(conn: sqlite3.Connection, kind: str, message: str, link: str) -> int:
    cur = conn.execute(
        "INSERT INTO inbox_items (kind, message, link) VALUES (?, ?, ?)",
        (kind, message, link),
    )
    conn.commit()
    return cur.lastrowid


def get_unresolved_inbox_items(conn: sqlite3.Connection) -> list[dict]:
    return _rows_to_dicts(
        conn.execute(
            "SELECT * FROM inbox_items WHERE resolved_at IS NULL ORDER BY created_at DESC"
        ).fetchall()
    )


def count_unresolved_inbox_items(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM inbox_items WHERE resolved_at IS NULL").fetchone()[0]


def resolve_inbox_item(conn: sqlite3.Connection, item_id: int) -> None:
    conn.execute("UPDATE inbox_items SET resolved_at = datetime('now') WHERE id = ?", (item_id,))
    conn.commit()


def get_inbox_item(conn: sqlite3.Connection, item_id: int) -> dict | None:
    return _row_to_dict(
        conn.execute("SELECT * FROM inbox_items WHERE id = ?", (item_id,)).fetchone()
    )


def resolve_source_prompts_for_url(conn: sqlite3.Connection, url: str) -> int:
    """Dismiss any needs_action source_detect / job_add_by_url task for `url`,
    so a stale prompt doesn't linger once that URL has been dealt with (source
    added, added as a job, already tracked) or superseded by a fresh detect.
    Returns the count dismissed."""
    rows = conn.execute(
        "SELECT id, params FROM tasks "
        "WHERE status = 'needs_action' AND kind IN ('source_detect', 'job_add_by_url')"
    ).fetchall()
    ids = []
    for row in rows:
        try:
            if json.loads(row["params"]).get("url") == url:
                ids.append(row["id"])
        except (ValueError, TypeError):
            continue
    for tid in ids:
        conn.execute(
            "UPDATE tasks SET status = 'dismissed', "
            "finished_at = COALESCE(finished_at, datetime('now')) WHERE id = ?", (tid,)
        )
    if ids:
        conn.commit()
    return len(ids)


def get_fetch_run(conn: sqlite3.Connection, run_id: int) -> dict | None:
    return _row_to_dict(conn.execute("SELECT * FROM fetch_runs WHERE id = ?", (run_id,)).fetchone())
