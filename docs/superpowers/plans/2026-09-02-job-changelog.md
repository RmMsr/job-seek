# Per-job Changelog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give each job a reverse-chronological list of timestamped one-line notes recording what changed about it — status changes, score moves on re-evaluation, and revisit-detected offer changes — shown on the expanded job view.

**Architecture:** A new `job_events` table (`job_id`, `created_at`, `kind`, `message`). Entries are written by the existing low-level query functions that already perform the relevant `UPDATE` (`update_job_feedback`, `upsert_job_score`, `update_job_fit`, `mark_job_closed`, `mark_job_gate_override`, `reset_job`), plus two touch-points in `pipeline.py` (revisit "changed" branch and the reset path's before/after score snapshot). The expanded job template (`jobs/_feedback.html`) renders a collapsed `History (N)` block and a "Fetched N days ago" data-age line.

**Tech Stack:** Python 3, FastAPI, SQLite (stdlib `sqlite3`), Jinja2 templates, pytest. Tests run with `python -m pytest` (per the sandbox note — `uv run` fails on the read-only cache).

## Global Constraints

- **Migrations:** personal single-instance app — no backwards-compat shims. New table goes straight into the `_DDL` block in `app/db/schema.py` as `CREATE TABLE IF NOT EXISTS` (the pattern every non-rebuilt table uses). No `_migrate_*` function. No data backfill.
- **FTS:** `job_events` does not touch the `jobs` table, so `jobs_fts` triggers are unaffected — nothing extra to do.
- **Commit cadence:** commit after each task's tests pass. Explicit paths only when staging (`git add path/...`) — `git add -A`/`.` fails in this repo due to untracked special files in the worktree root.
- **Score-change rule (verbatim):** emit a `score` entry when `abs(new - old) >= 0.05`, OR when the relevance score crosses the scenario's `gate_threshold` in either direction (passing = `score >= threshold`). Never emit when `old` is `None`.
- **Test runner:** `python -m pytest tests/<file> -v` from the worktree root.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `app/db/schema.py` | `_DDL` for `job_events` | modify |
| `app/db/queries.py` | `add_job_event`, `get_job_events`, `log_score_change`; event writes wired into `update_job_feedback`, `mark_job_closed`, `mark_job_gate_override`, `reset_job`, `upsert_job_score`, `update_job_fit` | modify |
| `app/pipeline.py` | revisit "changed" → `revisit` entry; `run_reprocess_job` before/after score snapshot | modify |
| `app/routes/jobs.py` | `_insert_error_job` passes `record_event=False`; `job_detail` / `job_expand` / `_render_updated_job_html` load `job_events` into context | modify |
| `app/templates/jobs/_feedback.html` | data-age line + `History (N)` block | modify |
| `app/templates/base.html` | `.job-data-age` and `.job-history*` CSS | modify |
| `tests/test_schema.py` | table-set assertions + `job_events` creation/cascade | modify |
| `tests/test_queries.py` | events CRUD, status entries, score entries, `mark_job_closed` rewrite | modify |
| `tests/test_revisit.py` | revisit "changed" entry; reset score entries | modify |
| `tests/test_routes_jobs.py` | History block + data-age line render | modify |

---

## Task 1: `job_events` table

**Files:**
- Modify: `app/db/schema.py` (the `_DDL` string, ~line 112–119, right after `inbox_items`)
- Test: `tests/test_schema.py`

**Interfaces:**
- Produces: table `job_events(id INTEGER PK, job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE, created_at TEXT NOT NULL DEFAULT (datetime('now')), kind TEXT NOT NULL, message TEXT NOT NULL)`

- [ ] **Step 1: Update the two table-set assertions**

In `tests/test_schema.py`, both `test_init_db_creates_all_tables` and `test_init_db_is_idempotent` assert an exact set. Add `"job_events"` to each:

```python
    assert _tables(conn) == {
        "profile", "sources", "scenarios", "criteria", "jobs", "job_scores", "fetch_runs", "scenario_feedback",
        "tasks", "inbox_items", "job_events",
    }
```

- [ ] **Step 2: Write the failing tests**

Add to `tests/test_schema.py`:

```python
def test_job_events_table_created(conn):
    init_db(conn)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(job_events)").fetchall()}
    assert cols == {"id", "job_id", "created_at", "kind", "message"}


def test_job_events_cascade_delete_with_job(conn):
    init_db(conn)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s', 'http://x', 'slack')")
    conn.execute("INSERT INTO jobs (source_id, url) VALUES (1, 'http://job/1')")
    conn.execute("INSERT INTO job_events (job_id, kind, message) VALUES (1, 'status', 'x')")
    conn.commit()
    conn.execute("DELETE FROM jobs WHERE id = 1")
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM job_events").fetchone()[0] == 0
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_schema.py -v -k "job_events or all_tables or idempotent"`
Expected: FAIL — `no such table: job_events`, and the set assertions fail.

- [ ] **Step 4: Add the table to `_DDL`**

In `app/db/schema.py`, inside the `_DDL` triple-quoted string, after the `CREATE TABLE IF NOT EXISTS inbox_items (...)` block and before the closing `"""`:

```sql
CREATE TABLE IF NOT EXISTS job_events (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    kind TEXT NOT NULL,
    message TEXT NOT NULL
);
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_schema.py -v`
Expected: PASS (all).

- [ ] **Step 6: Commit**

```bash
git add app/db/schema.py tests/test_schema.py
git commit -m "feat: add job_events table"
```

---

## Task 2: `add_job_event` and `get_job_events`

**Files:**
- Modify: `app/db/queries.py` (add near the other job helpers, e.g. just after `delete_job`, ~line 237)
- Test: `tests/test_queries.py`

**Interfaces:**
- Consumes: `job_events` table (Task 1)
- Produces:
  - `add_job_event(conn: sqlite3.Connection, job_id: int, kind: str, message: str) -> None` — inserts one row, commits.
  - `get_job_events(conn: sqlite3.Connection, job_id: int) -> list[dict]` — all events for the job, newest first (`ORDER BY created_at DESC, id DESC`).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_queries.py`:

```python
def test_add_and_get_job_events_newest_first(conn):
    sid = q.get_or_create_manual_source(conn)
    jid = q.insert_job(conn, source_id=sid, url="https://x.test/ev", title="A", company="", raw_text="")
    q.add_job_event(conn, jid, "status", "first")
    conn.execute("UPDATE job_events SET created_at = '2020-01-01T00:00:00' WHERE message = 'first'")
    q.add_job_event(conn, jid, "score", "second")
    conn.commit()
    events = q.get_job_events(conn, jid)
    assert [e["message"] for e in events] == ["second", "first"]
    assert events[0]["kind"] == "score"


def test_get_job_events_empty_for_new_job(conn):
    sid = q.get_or_create_manual_source(conn)
    jid = q.insert_job(conn, source_id=sid, url="https://x.test/ev2", title="A", company="", raw_text="")
    assert q.get_job_events(conn, jid) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_queries.py -v -k job_events`
Expected: FAIL — `AttributeError: module 'app.db.queries' has no attribute 'add_job_event'`.

- [ ] **Step 3: Implement**

In `app/db/queries.py`, after `delete_job`:

```python
def add_job_event(conn: sqlite3.Connection, job_id: int, kind: str, message: str) -> None:
    conn.execute(
        "INSERT INTO job_events (job_id, kind, message) VALUES (?, ?, ?)",
        (job_id, kind, message),
    )
    conn.commit()


def get_job_events(conn: sqlite3.Connection, job_id: int) -> list[dict]:
    return _rows_to_dicts(
        conn.execute(
            "SELECT * FROM job_events WHERE job_id = ? ORDER BY created_at DESC, id DESC",
            (job_id,),
        ).fetchall()
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_queries.py -v -k job_events`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/db/queries.py tests/test_queries.py
git commit -m "feat: add_job_event / get_job_events queries"
```

---

## Task 3: Status entries on `update_job_feedback` (+ `record_event` kwarg, `_insert_error_job`)

**Files:**
- Modify: `app/db/queries.py` — `update_job_feedback` (~line 430)
- Modify: `app/routes/jobs.py` — `_insert_error_job` (line 32)
- Test: `tests/test_queries.py`, `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: `add_job_event` (Task 2)
- Produces: `update_job_feedback(conn, job_id: int, status: str, note: str | None, *, record_event: bool = True) -> None` — unchanged behavior plus: when `record_event` and the stored status actually differs from `status`, writes a `job_event` of kind `"status"` with message `Status: <old> → <new>` (append ` — "<note>"` when `note` is a non-empty string).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_queries.py`:

```python
def test_update_job_feedback_logs_status_change_with_note(conn):
    sid = q.get_or_create_manual_source(conn)
    jid = q.insert_job(conn, source_id=sid, url="https://x.test/fb1", title="A", company="", raw_text="")
    q.update_job_feedback(conn, jid, "rejected", "role moved to London")
    events = q.get_job_events(conn, jid)
    assert len(events) == 1
    assert events[0]["kind"] == "status"
    assert events[0]["message"] == 'Status: new → rejected — "role moved to London"'


def test_update_job_feedback_no_event_when_status_unchanged(conn):
    sid = q.get_or_create_manual_source(conn)
    jid = q.insert_job(conn, source_id=sid, url="https://x.test/fb2", title="A", company="", raw_text="")
    q.update_job_feedback(conn, jid, "new", "just a note")
    assert q.get_job_events(conn, jid) == []


def test_update_job_feedback_record_event_false_suppresses(conn):
    sid = q.get_or_create_manual_source(conn)
    jid = q.insert_job(conn, source_id=sid, url="https://x.test/fb3", title="A", company="", raw_text="")
    q.update_job_feedback(conn, jid, "trash", "err", record_event=False)
    assert q.get_job_events(conn, jid) == []


def test_update_job_feedback_logs_without_note(conn):
    sid = q.get_or_create_manual_source(conn)
    jid = q.insert_job(conn, source_id=sid, url="https://x.test/fb4", title="A", company="", raw_text="")
    q.update_job_feedback(conn, jid, "accepted", None)
    assert q.get_job_events(conn, jid)[0]["message"] == "Status: new → accepted"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_queries.py -v -k "update_job_feedback_logs or update_job_feedback_no_event or record_event_false"`
Expected: FAIL — `TypeError: update_job_feedback() got an unexpected keyword argument 'record_event'` / no events recorded.

- [ ] **Step 3: Implement**

Replace `update_job_feedback` in `app/db/queries.py` with:

```python
def update_job_feedback(
    conn: sqlite3.Connection, job_id: int, status: str, note: str, *, record_event: bool = True
) -> None:
    row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
    old_status = row["status"] if row is not None else None
    conn.execute(
        "UPDATE jobs SET status = ?, feedback_note = ?, feedback_handled_at = NULL, "
        "status_changed_at = datetime('now') WHERE id = ?",
        (status, note, job_id),
    )
    conn.commit()
    if record_event and old_status is not None and old_status != status:
        message = f"Status: {old_status} → {status}"
        if isinstance(note, str) and note.strip():
            message += f' — "{note.strip()}"'
        add_job_event(conn, job_id, "status", message)
```

- [ ] **Step 4: Update `_insert_error_job`**

In `app/routes/jobs.py` line 32, change:

```python
    q.update_job_feedback(conn, job_id, "trash", reason)
```

to:

```python
    q.update_job_feedback(conn, job_id, "trash", reason, record_event=False)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_queries.py -v -k "update_job_feedback"`
Expected: PASS.

- [ ] **Step 6: Run the broader suites for fallout**

Run: `python -m pytest tests/test_queries.py tests/test_routes_jobs.py -q`
Expected: PASS. (Setup code across route tests calls `update_job_feedback`; none assert on `job_events` absence, so only genuine regressions would show.)

- [ ] **Step 7: Commit**

```bash
git add app/db/queries.py app/routes/jobs.py tests/test_queries.py
git commit -m "feat: log status changes to job changelog"
```

---

## Task 4: `mark_job_closed` writes a changelog entry instead of appending to `feedback_note`

**Files:**
- Modify: `app/db/queries.py` — `mark_job_closed` (~line 413)
- Test: `tests/test_queries.py` (rewrite `test_mark_job_closed_appends_note_and_trashes` and `test_mark_job_closed_with_no_existing_note`)

**Interfaces:**
- Consumes: `add_job_event` (Task 2)
- Produces: `mark_job_closed(conn, job_id: int, reason: str) -> None` — sets `status='trash'` and `status_changed_at`; writes a `status` `job_event` `Moved to Trash on revisit — <reason>`. **No longer touches `feedback_note` or `feedback_handled_at`.**

- [ ] **Step 1: Rewrite the two existing tests**

In `tests/test_queries.py`, replace `test_mark_job_closed_appends_note_and_trashes` and `test_mark_job_closed_with_no_existing_note` with:

```python
def test_mark_job_closed_trashes_and_logs_event(conn):
    sid = q.insert_source(conn, "board", "http://example.com", "generic_listing")
    jid = q.insert_job(conn, source_id=sid, url="http://example.com/a", title="A",
                       company="", raw_text="x")
    q.update_job_feedback(conn, jid, "accepted", "Loved the mission")
    q.mark_job_closed(conn, jid, "this job can no longer be found")
    row = q.get_job(conn, jid)
    assert row["status"] == "trash"
    assert row["status_changed_at"] is not None
    # user's own note is left untouched
    assert row["feedback_note"] == "Loved the mission"
    messages = [e["message"] for e in q.get_job_events(conn, jid)]
    assert "Moved to Trash on revisit — this job can no longer be found" in messages


def test_mark_job_closed_with_no_existing_note(conn):
    sid = q.insert_source(conn, "board", "http://example.com", "generic_listing")
    jid = q.insert_job(conn, source_id=sid, url="http://example.com/a", title="A",
                       company="", raw_text="x")
    q.mark_job_closed(conn, jid, "this page is no longer a job posting")
    assert q.get_job(conn, jid)["feedback_note"] is None
    assert q.get_job_events(conn, jid)[0]["message"] == (
        "Moved to Trash on revisit — this page is no longer a job posting"
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_queries.py -v -k mark_job_closed`
Expected: FAIL — old implementation still appends to `feedback_note` and writes no event.

- [ ] **Step 3: Implement**

Replace `mark_job_closed` in `app/db/queries.py` with:

```python
def mark_job_closed(conn: sqlite3.Connection, job_id: int, reason: str) -> None:
    row = conn.execute("SELECT 1 FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        return
    conn.execute(
        "UPDATE jobs SET status = 'trash', status_changed_at = datetime('now') WHERE id = ?",
        (job_id,),
    )
    conn.commit()
    add_job_event(conn, job_id, "status", f"Moved to Trash on revisit — {reason}")
```

The `datetime`/`timezone` imports at the top of `queries.py` may now be unused elsewhere — leave them; other functions still use `datetime.now(timezone.utc)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_queries.py -v -k mark_job_closed`
Expected: PASS.

- [ ] **Step 5: Check revisit + route fallout**

Run: `python -m pytest tests/test_revisit.py tests/test_routes_jobs.py -q`
Expected: PASS. (`test_revisit.py` close tests assert only `status == "trash"`; `test_routes_jobs.py:2634` asserts status trash and `"Trash" in html_chunks`.) If any test asserts on the old `feedback_note` "Moved to trash on revisit" text, update it to check `q.get_job_events` instead.

- [ ] **Step 6: Commit**

```bash
git add app/db/queries.py tests/test_queries.py
git commit -m "feat: revisit-close writes changelog entry, not feedback_note"
```

---

## Task 5: `mark_job_gate_override` and `reset_job` status entries

**Files:**
- Modify: `app/db/queries.py` — `mark_job_gate_override` (~line 392), `reset_job` (~line 360)
- Test: `tests/test_queries.py`

**Interfaces:**
- Consumes: `add_job_event` (Task 2)
- Produces:
  - `mark_job_gate_override(conn, job_id)` — unchanged behavior plus a `status` event `Filed as New — gate threshold bypassed`.
  - `reset_job(conn, job_id)` — unchanged behavior plus, when the stored status was not already `new`, a `status` event `Status: <old> → new`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_queries.py`:

```python
def test_mark_job_gate_override_logs_event(conn):
    sid = q.get_or_create_manual_source(conn)
    jid = q.insert_job(conn, source_id=sid, url="https://x.test/go", title="A", company="", raw_text="")
    q.mark_job_gate_override(conn, jid)
    assert q.get_job_events(conn, jid)[0]["message"] == "Filed as New — gate threshold bypassed"


def test_reset_job_logs_status_change_when_not_new(conn):
    sid = q.get_or_create_manual_source(conn)
    jid = q.insert_job(conn, source_id=sid, url="https://x.test/rs", title="A", company="", raw_text="")
    q.update_job_feedback(conn, jid, "rejected", None)
    q.reset_job(conn, jid)
    messages = [e["message"] for e in q.get_job_events(conn, jid)]
    assert "Status: rejected → new" in messages


def test_reset_job_no_status_event_when_already_new(conn):
    sid = q.get_or_create_manual_source(conn)
    jid = q.insert_job(conn, source_id=sid, url="https://x.test/rs2", title="A", company="", raw_text="")
    q.reset_job(conn, jid)
    assert [e for e in q.get_job_events(conn, jid) if e["kind"] == "status"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_queries.py -v -k "gate_override_logs or reset_job_logs or reset_job_no_status"`
Expected: FAIL — no events recorded.

- [ ] **Step 3: Implement `mark_job_gate_override`**

Replace it in `app/db/queries.py` with:

```python
def mark_job_gate_override(conn: sqlite3.Connection, job_id: int) -> None:
    conn.execute(
        "UPDATE jobs SET gate_override = 1, status_changed_at = datetime('now') WHERE id = ?",
        (job_id,),
    )
    conn.commit()
    add_job_event(conn, job_id, "status", "Filed as New — gate threshold bypassed")
```

- [ ] **Step 4: Implement `reset_job`**

In `app/db/queries.py`, change the start of `reset_job` to capture the old status, and append an event after the commit:

```python
def reset_job(conn: sqlite3.Connection, job_id: int) -> None:
    row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
    old_status = row["status"] if row is not None else None
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
    if old_status is not None and old_status != "new":
        add_job_event(conn, job_id, "status", f"Status: {old_status} → new")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_queries.py -v -k "gate_override or reset_job"`
Expected: PASS (including the pre-existing `test_reset_job_*` tests).

- [ ] **Step 6: Commit**

```bash
git add app/db/queries.py tests/test_queries.py
git commit -m "feat: log gate-override and reset status changes to changelog"
```

---

## Task 6: Score entries — `log_score_change`, `upsert_job_score`, `update_job_fit`

**Files:**
- Modify: `app/db/queries.py` — new `log_score_change`; wire into `upsert_job_score` (~line 266) and `update_job_fit` (~line 287)
- Test: `tests/test_queries.py`

**Interfaces:**
- Consumes: `add_job_event` (Task 2)
- Produces:
  - `log_score_change(conn, job_id: int, *, label: str, old: float | None, new: float | None, threshold: float | None = None) -> None` — inserts a `score` event `"<label> <old:.2f> → <new:.2f>"` when `abs(new - old) >= 0.05` OR (`threshold` given and `(old >= threshold) != (new >= threshold)`). When it crossed, append ` — now passes gate` (if `new >= threshold`) or ` — no longer passes gate`. No-op when `old` or `new` is `None`.
  - `upsert_job_score(...)` — unchanged signature/behavior; now also calls `log_score_change` with `label=f'Re-scored "{scenario_name}"'` and the scenario's `gate_threshold`.
  - `update_job_fit(...)` — unchanged signature/behavior; now also calls `log_score_change` with `label="Fit re-assessed"`, no threshold.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_queries.py`:

```python
def _score_job(conn, threshold=0.7):
    sid = q.get_or_create_manual_source(conn)
    scn = q.insert_scenario(conn, "AI Safety", "")
    conn.execute("UPDATE scenarios SET gate_threshold = ? WHERE id = ?", (threshold, scn))
    jid = q.insert_job(conn, source_id=sid, url=f"https://x.test/s{scn}", title="A", company="", raw_text="")
    conn.commit()
    return jid, scn


def test_upsert_job_score_no_event_on_first_score(conn):
    jid, scn = _score_job(conn)
    q.upsert_job_score(conn, jid, scn, 0.4, "r", "h")
    assert q.get_job_events(conn, jid) == []


def test_upsert_job_score_logs_meaningful_move(conn):
    jid, scn = _score_job(conn)
    q.upsert_job_score(conn, jid, scn, 0.40, "r", "h")
    q.upsert_job_score(conn, jid, scn, 0.55, "r", "h")
    msgs = [e["message"] for e in q.get_job_events(conn, jid)]
    assert msgs == ['Re-scored "AI Safety" 0.40 → 0.55']


def test_upsert_job_score_ignores_tiny_move(conn):
    jid, scn = _score_job(conn)
    q.upsert_job_score(conn, jid, scn, 0.40, "r", "h")
    q.upsert_job_score(conn, jid, scn, 0.43, "r", "h")
    assert q.get_job_events(conn, jid) == []


def test_upsert_job_score_logs_gate_crossing_even_if_tiny(conn):
    jid, scn = _score_job(conn, threshold=0.7)
    q.upsert_job_score(conn, jid, scn, 0.69, "r", "h")
    q.upsert_job_score(conn, jid, scn, 0.71, "r", "h")
    msgs = [e["message"] for e in q.get_job_events(conn, jid)]
    assert msgs == ['Re-scored "AI Safety" 0.69 → 0.71 — now passes gate']


def test_upsert_job_score_logs_gate_drop(conn):
    jid, scn = _score_job(conn, threshold=0.7)
    q.upsert_job_score(conn, jid, scn, 0.72, "r", "h")
    q.upsert_job_score(conn, jid, scn, 0.68, "r", "h")
    msgs = [e["message"] for e in q.get_job_events(conn, jid)]
    assert msgs == ['Re-scored "AI Safety" 0.72 → 0.68 — no longer passes gate']


def test_update_job_fit_no_event_first_time(conn):
    jid, scn = _score_job(conn)
    q.update_job_fit(conn, jid, 0.6, "i", 0.6, "a", "p")
    assert q.get_job_events(conn, jid) == []


def test_update_job_fit_logs_meaningful_move(conn):
    jid, scn = _score_job(conn)
    q.update_job_fit(conn, jid, 0.6, "i", 0.6, "a", "p")   # fit_score = 0.60
    q.update_job_fit(conn, jid, 0.8, "i", 0.8, "a", "p")   # fit_score = 0.80
    msgs = [e["message"] for e in q.get_job_events(conn, jid)]
    assert msgs == ["Fit re-assessed 0.60 → 0.80"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_queries.py -v -k "upsert_job_score_ or update_job_fit_logs or update_job_fit_no_event"`
Expected: FAIL — no events; `log_score_change` missing.

- [ ] **Step 3: Implement `log_score_change`**

In `app/db/queries.py`, immediately after `get_job_events`:

```python
def log_score_change(
    conn: sqlite3.Connection,
    job_id: int,
    *,
    label: str,
    old: float | None,
    new: float | None,
    threshold: float | None = None,
) -> None:
    if old is None or new is None:
        return
    crossed = threshold is not None and (old >= threshold) != (new >= threshold)
    if abs(new - old) < 0.05 and not crossed:
        return
    message = f"{label} {old:.2f} → {new:.2f}"
    if crossed:
        message += " — now passes gate" if new >= threshold else " — no longer passes gate"
    add_job_event(conn, job_id, "score", message)
```

- [ ] **Step 4: Wire into `upsert_job_score`**

Replace `upsert_job_score` with:

```python
def upsert_job_score(
    conn: sqlite3.Connection,
    job_id: int,
    scenario_id: int,
    score: float,
    reasoning: str,
    version_hash: str,
) -> None:
    prev = conn.execute(
        "SELECT relevance_score FROM job_scores WHERE job_id = ? AND scenario_id = ?",
        (job_id, scenario_id),
    ).fetchone()
    old_score = prev["relevance_score"] if prev is not None else None
    scn = conn.execute(
        "SELECT name, gate_threshold FROM scenarios WHERE id = ?", (scenario_id,)
    ).fetchone()
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
    if scn is not None:
        log_score_change(
            conn, job_id,
            label=f'Re-scored "{scn["name"]}"',
            old=old_score, new=score, threshold=scn["gate_threshold"],
        )
```

- [ ] **Step 5: Wire into `update_job_fit`**

Replace `update_job_fit` with:

```python
def update_job_fit(
    conn: sqlite3.Connection,
    job_id: int,
    interest_score: float,
    interest_reasoning: str,
    attainability_score: float,
    attainability_reasoning: str,
    profile_version_hash: str,
) -> None:
    prev = conn.execute("SELECT fit_score FROM jobs WHERE id = ?", (job_id,)).fetchone()
    old_fit = prev["fit_score"] if prev is not None else None
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
    log_score_change(conn, job_id, label="Fit re-assessed", old=old_fit, new=fit_score)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_queries.py -v -k "score or fit"`
Expected: PASS.

- [ ] **Step 7: Check pipeline fallout**

Run: `python -m pytest tests/test_pipeline.py tests/test_revisit.py -q`
Expected: PASS. Initial-ingest paths score from `None` (no event). Any test that asserts an exact event list is one this plan adds; existing pipeline tests do not inspect `job_events`.

- [ ] **Step 8: Commit**

```bash
git add app/db/queries.py tests/test_queries.py
git commit -m "feat: log score and fit changes to job changelog"
```

---

## Task 7: Reset path records score changes vs. pre-reset snapshot

**Files:**
- Modify: `app/pipeline.py` — `run_reprocess_job` (~line 323)
- Test: `tests/test_revisit.py` (has ready-made mocked-client helpers) — add a reset test here, or `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `q.get_job_scores`, `q.reset_job`, `q.log_score_change` (Task 6), `q.job_exists`, `q.get_job`
- Produces: after a reprocess run, one `score` event per scenario whose new relevance score differs meaningfully (or crosses gate) from its pre-reset value, plus one for fit — using `q.log_score_change`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_revisit.py` (reuses `board`, `_mock_client`, `_drain` already imported there; add `run_reprocess_job` to the pipeline import at the top of the file):

```python
from app.pipeline import run_revisit_job, RevisitOutcome, run_reprocess_job  # extend existing import


def test_reprocess_logs_score_change_vs_pre_reset(conn, board):
    jid = q.insert_job(conn, source_id=board["id"], url="http://example.com/rp",
                       title="ML Engineer", company="Acme", raw_text="original posting body text " * 20)
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="job_posting",
                          summary="An ML role.")
    scn = q.get_scenarios(conn)[0]["id"]
    q.upsert_job_score(conn, jid, scn, 0.30, "old", "h")   # pre-reset baseline
    job = q.get_job(conn, jid)

    client = _mock_client(
        '{"type": "job_posting", "reason": "ok"}',
        '{"title": "ML Engineer", "headline": "h", "summary": "s", "company": "Acme", "posted_date": ""}',
        '{"score": 0.90, "reasoning": "much better now"}')
    _drain(run_reprocess_job(conn, client, "llama3.2", job, q.get_scenarios(conn), q.get_profile(conn)))

    msgs = [e["message"] for e in q.get_job_events(conn, jid)]
    assert any(m.startswith('Re-scored "Remote ML" 0.30 → 0.90') for m in msgs)
```

(The `board` fixture creates scenario `"Remote ML"`. Adjust the scenario name in the assertion if the fixture differs.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_revisit.py -v -k reprocess_logs_score_change`
Expected: FAIL — no `Re-scored` event (reset deleted the baseline; `upsert_job_score` saw `old=None`).

- [ ] **Step 3: Implement**

In `app/pipeline.py`, edit `run_reprocess_job`:

```python
def run_reprocess_job(
    conn: sqlite3.Connection,
    client: openai.OpenAI,
    model: str,
    job: dict,
    scenarios: list[dict],
    profile: str,
    *,
    progress_prefix: str = "",
) -> Generator[str, None, None]:
    source = q.get_source(conn, job["source_id"])
    is_slack = bool(source and source["fetcher_type"] == "slack")
    before_scores = {s["scenario_id"]: s["relevance_score"] for s in q.get_job_scores(conn, job["id"])}
    before_fit = job["fit_score"]
    q.reset_job(conn, job["id"])
    yield _progress(f"{progress_prefix}Reprocessing: {job['url']}")
    yield from _ingest_posting(
        conn, client, model, job["id"], job["raw_text"], job["title"], is_slack, profile, scenarios,
        url=job["url"], progress_prefix=progress_prefix,
        preserve_existing_metadata=job["published_at"] is not None,
    )
    if q.job_exists(conn, job["id"]):
        for s in q.get_job_scores(conn, job["id"]):
            q.log_score_change(
                conn, job["id"],
                label=f'Re-scored "{s["scenario_name"]}"',
                old=before_scores.get(s["scenario_id"]),
                new=s["relevance_score"],
                threshold=s["scenario_gate_threshold"],
            )
        q.log_score_change(
            conn, job["id"], label="Fit re-assessed",
            old=before_fit, new=q.get_job(conn, job["id"])["fit_score"],
        )
        yield _progress(f"{progress_prefix}Reset complete: {job['url']}")
    else:
        yield _progress(f"{progress_prefix}Removed as not job-related: {job['url']}")
```

Note: `q.get_job_scores` rows expose `scenario_id`, `scenario_name`, `scenario_gate_threshold`, `relevance_score` (see its `SELECT`).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_revisit.py -v -k reprocess_logs_score_change`
Expected: PASS.

- [ ] **Step 5: Full pipeline suite**

Run: `python -m pytest tests/test_pipeline.py tests/test_revisit.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/pipeline.py tests/test_revisit.py
git commit -m "feat: reprocess records score changes vs pre-reset values"
```

---

## Task 8: Revisit "posting changed" changelog entry

**Files:**
- Modify: `app/pipeline.py` — `run_revisit_job`, the `if state == "changed":` branch (~line 230)
- Test: `tests/test_revisit.py`

**Interfaces:**
- Consumes: `q.add_job_event` (Task 2)
- Produces: on a `"changed"` revisit outcome, a `revisit` `job_event` `Revisit: posting changed — <reason_detail>` (or `Revisit: posting changed` when `reason_detail` is falsy), written before the re-evaluation runs.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_revisit.py`:

```python
def test_changed_revisit_logs_event(conn, board):
    job = _job(conn, board, status="accepted")
    client = _mock_client(
        '{"type": "job_posting", "reason": "ok"}',
        '{"title": "ML Engineer", "headline": "h2", "summary": "s2", "company": "Acme", "posted_date": ""}',
        '{"score": 0.95, "reasoning": "now great"}')
    with patch("app.pipeline.fetch_url_html", return_value=_LONG), \
         patch("app.pipeline.revisit_check", return_value=("changed", "comp band moved")):
        _run(conn, job, client)
    msgs = [e["message"] for e in q.get_job_events(conn, job["id"])]
    assert "Revisit: posting changed — comp band moved" in msgs
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_revisit.py -v -k changed_revisit_logs_event`
Expected: FAIL — event not present.

- [ ] **Step 3: Implement**

In `app/pipeline.py`, in the `if state == "changed":` branch of `run_revisit_job`, right after the existing `yield _progress(...)` line and before `q.update_job_raw_text(...)`:

```python
    if state == "changed":
        yield _progress(
            f"{progress_prefix}Still open, posting changed ({reason_detail or 'no detail'}) — re-evaluating: {url}"
        )
        detail = f" — {reason_detail}" if reason_detail else ""
        q.add_job_event(conn, job["id"], "revisit", f"Revisit: posting changed{detail}")
        q.update_job_raw_text(conn, job["id"], text)
        ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_revisit.py -v -k changed_revisit_logs_event`
Expected: PASS.

- [ ] **Step 5: Full revisit suite**

Run: `python -m pytest tests/test_revisit.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/pipeline.py tests/test_revisit.py
git commit -m "feat: revisit records 'posting changed' changelog entry"
```

---

## Task 9: Display — data-age line, History block, route wiring, CSS

**Files:**
- Modify: `app/routes/jobs.py` — `job_detail` (~line 228), `job_expand` (~line 244), `_render_updated_job_html` (~line 178)
- Modify: `app/templates/jobs/_feedback.html`
- Modify: `app/templates/base.html` (CSS in the `<style>` block, near `.job-advanced` ~line 340 and `.job-hook` ~line 240)
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: `q.get_job_events` (Task 2)
- Produces: `jobs/_feedback.html` renders, when `job_events` is truthy, a `<details class="job-history">` above the `Advanced…` block; and always a `<p class="job-data-age">` under the summary.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_routes_jobs.py` (uses the existing `_seed` helper — inspect its return signature in that file; it returns `(sid, jid, scenario_id)`):

```python
def test_job_detail_shows_history_block(client, conn):
    sid, jid, scenario_id = _seed(conn)
    q.update_job_feedback(conn, jid, "rejected", "not remote")
    resp = client.get(f"/jobs/{jid}")
    assert resp.status_code == 200
    assert "History (1)" in resp.text
    assert 'Status: new → rejected — &#34;not remote&#34;' in resp.text or "Status: new → rejected" in resp.text


def test_job_detail_no_history_block_when_empty(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}")
    assert resp.status_code == 200
    assert 'class="job-history"' not in resp.text


def test_job_expand_shows_data_age(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert "job-data-age" in resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_routes_jobs.py -v -k "history_block or data_age"`
Expected: FAIL — markup absent.

- [ ] **Step 3: Load `job_events` in the three routes**

In `app/routes/jobs.py`:

`job_detail` — after `job_scores = q.get_job_scores(conn, job_id)`:
```python
    job_events = q.get_job_events(conn, job_id)
```
and add `"job_events": job_events,` to the `TemplateResponse` context dict.

`job_expand` — after `job_scores = q.get_job_scores(conn, job_id)`:
```python
    job_events = q.get_job_events(conn, job_id)
```
and add `"job_events": job_events,` to the `context` dict.

`_render_updated_job_html` — in the branch that renders `_feedback.html` (after `job_scores = q.get_job_scores(conn, job_id)`):
```python
    job_events = q.get_job_events(conn, job_id)
    return templates.get_template("jobs/_feedback.html").render(
        request=request, job=job, scenarios=scenarios, job_scores=job_scores,
        job_events=job_events, filter=f, is_detail_page=detail,
    )
```

- [ ] **Step 4: Edit `app/templates/jobs/_feedback.html`**

After the summary `<section>` block (the `{% if job.summary %}...{% endif %}`, ~line 43) add:

```html
  <p class="job-data-age">Fetched {{ job.fetched_at | time_ago }}</p>
```

Immediately before `<details class="job-advanced">` (~line 88) add:

```html
  {% if job_events %}
  <details class="job-history">
    <summary>History ({{ job_events | length }})</summary>
    <ul class="job-history-list">
      {% for e in job_events %}
      <li><time datetime="{{ e.created_at }}" title="{{ e.created_at }}">{{ e.created_at[:10] }}</time> {{ e.message }}</li>
      {% endfor %}
    </ul>
  </details>
  {% endif %}
```

`job_events` is undefined in any other include path of this template; guard already handles that (`{% if job_events %}` is falsy for undefined in Jinja2 with default settings).

- [ ] **Step 5: Add CSS to `app/templates/base.html`**

Near `.job-hook` (~line 240):
```css
    .job-data-age { margin: 0.5rem 0 0; font-size: 0.8rem; color: var(--text-muted); }
```

Near `.job-advanced` (~line 342):
```css
    .job-history { margin-top: 0.5rem; }
    .job-history summary { cursor: pointer; color: var(--text-muted); font-size: 0.85em; user-select: none; }
    .job-history-list { list-style: none; padding: 0; margin: 0.4rem 0 0; font-size: 0.85em; color: var(--text-secondary); }
    .job-history-list li { padding: 0.15rem 0; }
    .job-history-list time { color: var(--text-muted); margin-right: 0.5rem; font-variant-numeric: tabular-nums; }
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_routes_jobs.py -v -k "history_block or data_age"`
Expected: PASS. (If the HTML-entity assertion for the quoted note is brittle, keep only the `"Status: new → rejected"` substring check.)

- [ ] **Step 7: Full route suite**

Run: `python -m pytest tests/test_routes_jobs.py -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add app/routes/jobs.py app/templates/jobs/_feedback.html app/templates/base.html tests/test_routes_jobs.py
git commit -m "feat: render job changelog and data-age on expanded job view"
```

---

## Task 10: Full suite + manual smoke

**Files:** none (verification only)

- [ ] **Step 1: Run the whole test suite**

Run: `python -m pytest -q`
Expected: PASS. Investigate and fix any failure referencing `feedback_note`, `job_events`, `mark_job_closed`, or event counts.

- [ ] **Step 2: Manual smoke via the dev server**

Use the `run-dev-server` skill (throwaway DB copy). Then:
- Open a job, expand it → confirm the "Fetched N days ago" line shows and there is no History block on a job with no events.
- Accept the job with a note → reload → History block shows `Status: new → rejected/accepted — "<note>"`.
- Use **Advanced → Re-evaluate** on a job whose scores will shift → History gains a `Re-scored "<scenario>" x → y` and/or `Fit re-assessed` line.
- Use **Advanced → Revisit** on a job whose posting is gone → job moves to Trash, History shows `Moved to Trash on revisit — …`, and `feedback_note` is NOT polluted.

- [ ] **Step 3: Leave the dev server running and hand the URL to the user** for their own check before offering to merge (per CLAUDE.md UI-handoff rule).

---

## Self-Review Notes

- **Spec coverage:** table (T1); `add`/`get` (T2); status entries — feedback (T3), close (T4), gate-override + reset (T5); score entries — upsert/fit (T6), reset snapshot (T7); revisit-changed (T8); display + data-age + route wiring (T9); verification (T10). All spec sections mapped.
- **`kind` values used:** `"status"`, `"score"`, `"revisit"` — consistent across T3–T8 and matches the spec.
- **Signature consistency:** `log_score_change(conn, job_id, *, label, old, new, threshold=None)` defined in T6, called identically in T6 and T7. `update_job_feedback(..., *, record_event=True)` defined T3, used T3.
- **No placeholder steps:** every code step contains full code.
- **Known test edits (not new behavior gaps):** T1 edits 2 table-set assertions; T4 rewrites 2 `mark_job_closed` tests. Both are spec-mandated behavior changes, called out explicitly.
