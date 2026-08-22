# Async task architecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace every `StreamingResponse`-backed progress endpoint with a background task queue, a polled status tray, and a general-purpose action inbox, so long operations survive tab closes/navigation.

**Architecture:** A `tasks` DB table + a single background worker thread (serialized, one task at a time) replace per-request `StreamingResponse` generators. Routes shrink to "validate → enqueue → return `{task_id}`". The browser polls `GET /tasks/{id}` for a task it just started (applying the same HTML-swap/notice behavior as today once it completes) and `GET /tasks/active` for ambient tray display. Tasks whose outcome needs a decision (source detected, fetch hit an auth error, ambiguous listing) also write a row to a general-purpose `inbox_items` table.

**Tech Stack:** FastAPI, sqlite3 (stdlib), Jinja2 (existing `templates` env), vanilla JS (no new frontend dependencies).

## Global Constraints

- No new runtime dependencies (no Celery/Redis/etc.) — single in-process worker thread, per the spec's "personal, single-instance app" framing.
- `app/pipeline.py`'s generator functions are never modified — only how they're invoked changes.
- The `NOTICE:`/`HTML:`-prefixed-line stream protocol is retired entirely; results travel as plain JSON (no more `.replace("\n", "")` escaping anywhere).
- Every task-kind function, wherever it renders a Jinja template directly (not via `TemplateResponse`), passes `request=None` — confirmed safe: only `base.html` and `sources/_row.html` dereference `request` in a template body, and neither is reachable from worker-rendered fragments in a way that breaks (Jinja's default lenient `Undefined` renders `None.base_url` as blank, not an error).
- Task `params`/`result` are stored as JSON text columns; `app/db/queries.py` functions handle encode/decode so callers work with plain dicts.

---

### Task 1: Schema, WAL mode, and `tasks`/`inbox_items` queries

**Files:**
- Modify: `app/db/schema.py` (`_DDL`, end of file)
- Modify: `app/deps.py` (`_open_db`)
- Modify: `app/db/queries.py` (add `import json` at top; add new functions at end)
- Test: `tests/test_schema.py`, `tests/test_queries.py`

**Interfaces:**
- Produces: `q.enqueue_task(conn, kind: str, params: dict) -> dict`, `q.find_active_task(conn, kind: str, params: dict) -> dict | None`, `q.get_task(conn, task_id: int) -> dict | None`, `q.get_active_tasks(conn) -> list[dict]`, `q.claim_next_task(conn) -> dict | None`, `q.append_task_log(conn, task_id: int, line: str) -> None`, `q.complete_task(conn, task_id: int, result: dict) -> None`, `q.fail_task(conn, task_id: int, error: str) -> None`, `q.recover_interrupted_tasks(conn) -> int`, `q.create_inbox_item(conn, kind: str, message: str, link: str) -> int`, `q.get_unresolved_inbox_items(conn) -> list[dict]`, `q.count_unresolved_inbox_items(conn) -> int`, `q.resolve_inbox_item(conn, item_id: int) -> None`, `q.get_fetch_run(conn, run_id: int) -> dict | None`. Task dicts have `params`/`result` already decoded from JSON into plain dicts (`result` is `None` until the task finishes).

- [ ] **Step 1: Write failing schema tests**

Add to `tests/test_schema.py`:

```python
def test_tasks_table_exists():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    conn.execute(
        "INSERT INTO tasks (kind, params) VALUES ('fetch_source', '{}')"
    )
    row = conn.execute("SELECT * FROM tasks").fetchone()
    assert row["status"] == "queued"
    assert row["log"] == ""


def test_inbox_items_table_exists():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    conn.execute(
        "INSERT INTO inbox_items (kind, message, link) VALUES ('task_followup', 'x', '/y')"
    )
    row = conn.execute("SELECT * FROM inbox_items").fetchone()
    assert row["resolved_at"] is None
```

(Check the top of `tests/test_schema.py` for its existing `sqlite3`/`init_db` imports and reuse them — don't re-import if already present.)

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_schema.py -k "tasks_table_exists or inbox_items_table_exists" -v`
Expected: FAIL — no such table

- [ ] **Step 3: Add the tables to `_DDL`**

In `app/db/schema.py`, append inside the `_DDL` string (after the existing `scenario_feedback` table, before the closing `"""`):

```sql
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
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at TEXT
);
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_schema.py -k "tasks_table_exists or inbox_items_table_exists" -v`
Expected: PASS

- [ ] **Step 5: Enable WAL mode**

In `app/deps.py`, in `_open_db`, after `conn.execute("PRAGMA foreign_keys = ON")` add:

```python
    conn.execute("PRAGMA journal_mode = WAL")
```

(No-ops harmlessly on `:memory:` connections used by tests.)

- [ ] **Step 6: Write failing queries tests**

Add to `tests/test_queries.py`:

```python
def test_enqueue_task_creates_queued_row(conn):
    task = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1})
    assert task["status"] == "queued"
    assert task["kind"] == "fetch_source"
    assert task["params"] == {"source_id": 1}
    assert task["result"] is None


def test_enqueue_task_dedupes_identical_active_task():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    first = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1})
    second = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1})
    assert first["id"] == second["id"]
    assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1


def test_enqueue_task_does_not_dedupe_after_completion():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    first = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1})
    q.complete_task(conn, first["id"], {"html_chunks": []})
    second = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1})
    assert second["id"] != first["id"]


def test_claim_next_task_returns_oldest_queued_and_marks_running(conn):
    q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1})
    q.enqueue_task(conn, kind="fetch_source", params={"source_id": 2})
    claimed = q.claim_next_task(conn)
    assert claimed["params"] == {"source_id": 1}
    assert claimed["status"] == "running"
    assert claimed["started_at"] is not None
    assert q.claim_next_task(conn)["params"] == {"source_id": 2}
    assert q.claim_next_task(conn) is None


def test_append_task_log_accumulates_lines(conn):
    task = q.enqueue_task(conn, kind="fetch_source", params={})
    q.append_task_log(conn, task["id"], "line one")
    q.append_task_log(conn, task["id"], "line two")
    fetched = q.get_task(conn, task["id"])
    assert fetched["log"] == "line one\nline two\n"


def test_complete_task_sets_status_and_result(conn):
    task = q.enqueue_task(conn, kind="fetch_source", params={})
    q.complete_task(conn, task["id"], {"html_chunks": ["<p>ok</p>"]})
    fetched = q.get_task(conn, task["id"])
    assert fetched["status"] == "done"
    assert fetched["result"] == {"html_chunks": ["<p>ok</p>"]}
    assert fetched["finished_at"] is not None


def test_fail_task_sets_status_and_error(conn):
    task = q.enqueue_task(conn, kind="fetch_source", params={})
    q.fail_task(conn, task["id"], "boom")
    fetched = q.get_task(conn, task["id"])
    assert fetched["status"] == "failed"
    assert fetched["error"] == "boom"


def test_recover_interrupted_tasks_fails_running_rows(conn):
    task = q.enqueue_task(conn, kind="fetch_source", params={})
    q.claim_next_task(conn)
    n = q.recover_interrupted_tasks(conn)
    assert n == 1
    fetched = q.get_task(conn, task["id"])
    assert fetched["status"] == "failed"
    assert fetched["error"] == "interrupted by restart"


def test_get_active_tasks_excludes_done_and_failed(conn):
    a = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1})
    b = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 2})
    q.complete_task(conn, a["id"], {})
    active = q.get_active_tasks(conn)
    assert [t["id"] for t in active] == [b["id"]]


def test_inbox_item_lifecycle(conn):
    item_id = q.create_inbox_item(conn, kind="task_followup", message="hi", link="/x")
    assert q.count_unresolved_inbox_items(conn) == 1
    items = q.get_unresolved_inbox_items(conn)
    assert items[0]["message"] == "hi"
    q.resolve_inbox_item(conn, item_id)
    assert q.count_unresolved_inbox_items(conn) == 0


def test_get_fetch_run_returns_row(conn):
    sid = q.insert_source(conn, "s", "http://x", "generic_listing")
    run_id = q.start_fetch_run(conn, sid)
    q.complete_fetch_run(conn, run_id, jobs_found=1, jobs_new=1, auth_error=True)
    run = q.get_fetch_run(conn, run_id)
    assert run["auth_error"] == 1
```

(Check `tests/test_queries.py`'s top for its existing `conn` fixture usage pattern — it's the shared `conftest.py` fixture; reuse it, and add `import sqlite3` / `from app.db.schema import init_db` only if not already imported for the two tests that build their own connection to test dedup-after-completion behavior across enqueue calls without the shared fixture's implicit isolation.)

- [ ] **Step 7: Run to verify failure**

Run: `pytest tests/test_queries.py -k "task or inbox_item or fetch_run" -v`
Expected: FAIL — `AttributeError: module 'app.db.queries' has no attribute 'enqueue_task'` (etc.)

- [ ] **Step 8: Implement the query functions**

In `app/db/queries.py`, add `import json` to the top imports, then append at the end of the file:

```python
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


def enqueue_task(conn: sqlite3.Connection, kind: str, params: dict) -> dict:
    existing = find_active_task(conn, kind, params)
    if existing is not None:
        return existing
    params_json = json.dumps(params, sort_keys=True)
    cur = conn.execute(
        "INSERT INTO tasks (kind, params) VALUES (?, ?)", (kind, params_json)
    )
    conn.commit()
    return get_task(conn, cur.lastrowid)


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
        "INSERT INTO inbox_items (kind, message, link) VALUES (?, ?, ?)", (kind, message, link)
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


def get_fetch_run(conn: sqlite3.Connection, run_id: int) -> dict | None:
    return _row_to_dict(conn.execute("SELECT * FROM fetch_runs WHERE id = ?", (run_id,)).fetchone())
```

- [ ] **Step 9: Run to verify pass**

Run: `pytest tests/test_schema.py tests/test_queries.py -v`
Expected: PASS (all, including pre-existing tests — WAL-mode change shouldn't affect them since `:memory:` DBs ignore it)

- [ ] **Step 10: Commit**

```bash
git add app/db/schema.py app/deps.py app/db/queries.py tests/test_schema.py tests/test_queries.py
git commit -m "feat: add tasks/inbox_items tables and their queries"
```

---

### Task 2: Task engine core (worker thread, registry, execution)

**Files:**
- Create: `app/task_engine.py`
- Test: `tests/test_task_engine.py`

**Interfaces:**
- Consumes: `q.claim_next_task`, `q.append_task_log`, `q.complete_task`, `q.fail_task`, `q.recover_interrupted_tasks`, `q.create_inbox_item` from Task 1. `app.deps._open_db`, `app.deps.get_ai_client`, `app.deps.get_model`, `app.deps.load_config`.
- Produces: `register_task_kind(kind: str) -> Callable` (decorator route modules use to register their task-kind generator functions), `TASK_KINDS: dict[str, Callable]`, `execute_task(conn, client, model, config, task: dict) -> None` (runs one already-claimed task to completion against the given connection — this is what both the real worker loop and tests call), `run_worker_forever(stop_event: threading.Event, poll_interval: float = 1.0) -> None`.

A registered task-kind function has the shape `def fn(conn, client, model, config, params: dict) -> Generator[str, None, dict]` — yields plain progress strings, returns a result dict. Recognized result keys: `notices: list[{level, html}]`, `html_chunks: list[str]`, `needs_action: bool`, `action_message: str`, `action_link: str | None`, `resume_html: str` (used as `/tasks/{id}/resume`'s content when `action_link` is absent).

- [ ] **Step 1: Write failing tests**

Create `tests/test_task_engine.py`:

```python
import sqlite3
import threading
import time
import pytest
from app.db.schema import init_db
from app.db import queries as q
from app import task_engine as te


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:", check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    init_db(c)
    yield c
    c.close()


def test_register_task_kind_adds_to_registry():
    @te.register_task_kind("test_kind_registration")
    def fn(conn, client, model, config, params):
        return {}
        yield  # pragma: no cover
    assert te.TASK_KINDS["test_kind_registration"] is fn
    del te.TASK_KINDS["test_kind_registration"]


def test_execute_task_runs_generator_and_stores_log_and_result(conn):
    @te.register_task_kind("test_execute_success")
    def fn(conn, client, model, config, params):
        yield "step one"
        yield "step two"
        return {"html_chunks": ["<p>done</p>"]}

    task = q.enqueue_task(conn, kind="test_execute_success", params={})
    te.execute_task(conn, None, None, None, task)
    fetched = q.get_task(conn, task["id"])
    assert fetched["status"] == "done"
    assert fetched["log"] == "step one\nstep two\n"
    assert fetched["result"] == {"html_chunks": ["<p>done</p>"]}
    del te.TASK_KINDS["test_execute_success"]


def test_execute_task_marks_failed_on_exception(conn):
    @te.register_task_kind("test_execute_failure")
    def fn(conn, client, model, config, params):
        yield "about to fail"
        raise ValueError("boom")

    task = q.enqueue_task(conn, kind="test_execute_failure", params={})
    te.execute_task(conn, None, None, None, task)
    fetched = q.get_task(conn, task["id"])
    assert fetched["status"] == "failed"
    assert fetched["error"] == "boom"
    del te.TASK_KINDS["test_execute_failure"]


def test_execute_task_unknown_kind_fails_immediately(conn):
    task = q.enqueue_task(conn, kind="no_such_kind", params={})
    te.execute_task(conn, None, None, None, task)
    fetched = q.get_task(conn, task["id"])
    assert fetched["status"] == "failed"
    assert "no_such_kind" in fetched["error"]


def test_execute_task_creates_inbox_item_when_needs_action(conn):
    @te.register_task_kind("test_execute_needs_action")
    def fn(conn, client, model, config, params):
        yield "checking"
        return {"needs_action": True, "action_message": "please decide", "action_link": "/somewhere"}

    task = q.enqueue_task(conn, kind="test_execute_needs_action", params={})
    te.execute_task(conn, None, None, None, task)
    items = q.get_unresolved_inbox_items(conn)
    assert len(items) == 1
    assert items[0]["message"] == "please decide"
    assert items[0]["link"] == "/somewhere"
    del te.TASK_KINDS["test_execute_needs_action"]


def test_execute_task_needs_action_defaults_link_to_resume(conn):
    @te.register_task_kind("test_execute_needs_action_resume")
    def fn(conn, client, model, config, params):
        yield "checking"
        return {"needs_action": True, "action_message": "decide", "resume_html": "<p>panel</p>"}

    task = q.enqueue_task(conn, kind="test_execute_needs_action_resume", params={})
    te.execute_task(conn, None, None, None, task)
    items = q.get_unresolved_inbox_items(conn)
    assert items[0]["link"] == f"/tasks/{task['id']}/resume"
    del te.TASK_KINDS["test_execute_needs_action_resume"]


def test_run_worker_forever_processes_queued_task_then_stops(conn, monkeypatch):
    processed = []

    @te.register_task_kind("test_worker_loop")
    def fn(conn, client, model, config, params):
        processed.append(params["n"])
        return {}
        yield  # pragma: no cover

    monkeypatch.setattr(te, "_open_db", lambda config: conn)
    monkeypatch.setattr(te, "load_config", lambda: object())
    monkeypatch.setattr(te, "get_ai_client", lambda: None)
    monkeypatch.setattr(te, "get_model", lambda: None)

    q.enqueue_task(conn, kind="test_worker_loop", params={"n": 1})
    stop_event = threading.Event()
    thread = threading.Thread(target=te.run_worker_forever, args=(stop_event, 0.05))
    thread.start()
    for _ in range(40):
        if processed:
            break
        time.sleep(0.05)
    stop_event.set()
    thread.join(timeout=2)
    assert processed == [1]
    del te.TASK_KINDS["test_worker_loop"]
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_task_engine.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.task_engine'`

- [ ] **Step 3: Implement `app/task_engine.py`**

```python
from __future__ import annotations
import logging
import threading
from typing import Callable, Generator
import openai
from app.config import Config, load_config
from app.deps import _open_db, get_ai_client, get_model
from app.db import queries as q

logger = logging.getLogger("job_seek")

TaskKindFn = Callable[..., Generator[str, None, dict]]
TASK_KINDS: dict[str, TaskKindFn] = {}


def register_task_kind(kind: str) -> Callable[[TaskKindFn], TaskKindFn]:
    def decorator(fn: TaskKindFn) -> TaskKindFn:
        TASK_KINDS[kind] = fn
        return fn
    return decorator


def execute_task(
    conn, client: openai.OpenAI | None, model: str | None, config: Config | None, task: dict
) -> None:
    kind_fn = TASK_KINDS.get(task["kind"])
    if kind_fn is None:
        q.fail_task(conn, task["id"], f"Unknown task kind: {task['kind']!r}")
        return
    try:
        gen = kind_fn(conn, client, model, config, task["params"])
        result: dict = {}
        try:
            while True:
                line = next(gen)
                q.append_task_log(conn, task["id"], line)
        except StopIteration as stop:
            result = stop.value or {}
    except Exception as exc:
        logger.exception("Task %s (%s) failed", task["id"], task["kind"])
        q.fail_task(conn, task["id"], str(exc))
        return
    q.complete_task(conn, task["id"], result)
    if result.get("needs_action"):
        q.create_inbox_item(
            conn,
            kind="task_followup",
            message=result.get("action_message", "A task needs your attention"),
            link=result.get("action_link") or f"/tasks/{task['id']}/resume",
        )


def run_worker_forever(stop_event: threading.Event, poll_interval: float = 1.0) -> None:
    conn = _open_db(load_config())
    try:
        recovered = q.recover_interrupted_tasks(conn)
        if recovered:
            logger.info("Marked %d interrupted task(s) as failed on startup", recovered)
        while not stop_event.is_set():
            task = q.claim_next_task(conn)
            if task is None:
                stop_event.wait(poll_interval)
                continue
            execute_task(conn, get_ai_client(), get_model(), load_config(), task)
    finally:
        conn.close()
```

Note: `_open_db` and `load_config` are imported as plain names at module scope (not accessed via `app.deps.load_config()` inline) specifically so the test suite's `monkeypatch.setattr(te, "_open_db", ...)` / `monkeypatch.setattr(te, "load_config", ...)` can override them.

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_task_engine.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/task_engine.py tests/test_task_engine.py
git commit -m "feat: add background task engine (registry, execution, worker loop)"
```

---

### Task 3: `/tasks` and `/inbox` HTTP endpoints, lifespan wiring, resume/inbox templates

**Files:**
- Create: `app/routes/tasks.py`
- Create: `app/routes/inbox.py`
- Create: `app/templates/tasks/resume.html`
- Create: `app/templates/inbox/index.html`
- Modify: `app/main.py`
- Test: `tests/test_routes_tasks.py`, `tests/test_routes_inbox.py`

**Interfaces:**
- Consumes: everything from Task 1 (`q.*`) and Task 2 (`run_worker_forever`).
- Produces: `GET /tasks/active` → `{"tasks": [...], "inbox_count": int}`; `GET /tasks/{id}` → task summary incl. `result`; `GET /tasks/{id}/resume` → HTML page; `GET /inbox` → HTML page; `POST /inbox/{id}/resolve` → empty 200.

- [ ] **Step 1: Write failing route tests**

Create `tests/test_routes_tasks.py`:

```python
from app.db import queries as q


def test_tasks_active_lists_queued_and_running(client, conn):
    q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1})
    resp = client.get("/tasks/active")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["tasks"]) == 1
    assert data["tasks"][0]["kind"] == "fetch_source"
    assert data["tasks"][0]["status"] == "queued"
    assert data["inbox_count"] == 0


def test_tasks_active_excludes_done(client, conn):
    task = q.enqueue_task(conn, kind="fetch_source", params={})
    q.complete_task(conn, task["id"], {})
    resp = client.get("/tasks/active")
    assert resp.json()["tasks"] == []


def test_task_detail_includes_result(client, conn):
    task = q.enqueue_task(conn, kind="fetch_source", params={})
    q.complete_task(conn, task["id"], {"html_chunks": ["<p>x</p>"]})
    resp = client.get(f"/tasks/{task['id']}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "done"
    assert data["result"] == {"html_chunks": ["<p>x</p>"]}


def test_task_detail_404_for_missing(client, conn):
    resp = client.get("/tasks/999")
    assert resp.status_code == 404


def test_task_resume_renders_resume_html(client, conn):
    task = q.enqueue_task(conn, kind="fetch_source", params={})
    q.complete_task(conn, task["id"], {"resume_html": "<p>confirm me</p>"})
    resp = client.get(f"/tasks/{task['id']}/resume")
    assert resp.status_code == 200
    assert "confirm me" in resp.text


def test_task_resume_404_without_resume_html(client, conn):
    task = q.enqueue_task(conn, kind="fetch_source", params={})
    q.complete_task(conn, task["id"], {})
    resp = client.get(f"/tasks/{task['id']}/resume")
    assert resp.status_code == 404
```

Create `tests/test_routes_inbox.py`:

```python
from app.db import queries as q


def test_inbox_page_lists_unresolved_items(client, conn):
    q.create_inbox_item(conn, kind="task_followup", message="do this", link="/somewhere")
    resp = client.get("/inbox")
    assert resp.status_code == 200
    assert "do this" in resp.text
    assert 'href="/somewhere"' in resp.text


def test_inbox_page_empty_state(client, conn):
    resp = client.get("/inbox")
    assert resp.status_code == 200
    assert "Nothing needs your attention" in resp.text


def test_inbox_resolve_marks_resolved(client, conn):
    item_id = q.create_inbox_item(conn, kind="task_followup", message="x", link="/y")
    resp = client.post(f"/inbox/{item_id}/resolve")
    assert resp.status_code == 200
    assert q.count_unresolved_inbox_items(conn) == 0
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_routes_tasks.py tests/test_routes_inbox.py -v`
Expected: FAIL — 404s (routes don't exist yet)

- [ ] **Step 3: Implement `app/routes/tasks.py`**

```python
from __future__ import annotations
import sqlite3
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from app.deps import get_db
from app.db import queries as q
from app.template_env import templates

router = APIRouter()


def _task_summary(task: dict, *, include_result: bool = False) -> dict:
    log = task["log"].strip()
    last_line = log.split("\n")[-1] if log else ""
    summary = {
        "id": task["id"], "kind": task["kind"], "status": task["status"],
        "last_line": last_line, "error": task["error"],
    }
    if include_result:
        summary["result"] = task["result"]
    return summary


@router.get("/tasks/active")
def tasks_active(conn: sqlite3.Connection = Depends(get_db)):
    return {
        "tasks": [_task_summary(t) for t in q.get_active_tasks(conn)],
        "inbox_count": q.count_unresolved_inbox_items(conn),
    }


@router.get("/tasks/{task_id}")
def task_detail(task_id: int, conn: sqlite3.Connection = Depends(get_db)):
    task = q.get_task(conn, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return _task_summary(task, include_result=True)


@router.get("/tasks/{task_id}/resume", response_class=HTMLResponse)
def task_resume(task_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    task = q.get_task(conn, task_id)
    if task is None or not task.get("result") or not task["result"].get("resume_html"):
        raise HTTPException(status_code=404, detail="Nothing to resume for this task")
    return templates.TemplateResponse(
        request, "tasks/resume.html", {"resume_html": task["result"]["resume_html"]}
    )
```

- [ ] **Step 4: Implement `app/routes/inbox.py`**

```python
from __future__ import annotations
import sqlite3
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from app.deps import get_db
from app.db import queries as q
from app.template_env import templates

router = APIRouter()


@router.get("/inbox", response_class=HTMLResponse)
def inbox_page(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    return templates.TemplateResponse(
        request, "inbox/index.html", {"items": q.get_unresolved_inbox_items(conn)}
    )


@router.post("/inbox/{item_id}/resolve", response_class=HTMLResponse)
def inbox_resolve(item_id: int, conn: sqlite3.Connection = Depends(get_db)):
    q.resolve_inbox_item(conn, item_id)
    return HTMLResponse(content="")
```

- [ ] **Step 5: Create the templates**

`app/templates/tasks/resume.html`:

```html
{% extends "base.html" %}
{% block title %}Resume — Job Seek{% endblock %}
{% block content %}
<h1>Resume</h1>
{{ resume_html | safe }}
<p><a href="/inbox">Back to Inbox</a></p>
{% endblock %}
```

`app/templates/inbox/index.html`:

```html
{% extends "base.html" %}
{% block title %}Inbox — Job Seek{% endblock %}
{% block content %}
<h1>Inbox</h1>
{% if not items %}
<p>Nothing needs your attention right now.</p>
{% else %}
<ul class="checklist" id="inbox-list">
  {% for item in items %}
  <li id="inbox-item-{{ item.id }}">
    <a href="{{ item.link }}">{{ item.message }}</a>
    <button type="button" class="btn" hx-post="/inbox/{{ item.id }}/resolve"
      hx-target="#inbox-item-{{ item.id }}" hx-swap="outerHTML">Dismiss</button>
  </li>
  {% endfor %}
</ul>
{% endif %}
{% endblock %}
```

- [ ] **Step 6: Wire routers and worker-thread lifespan into `app/main.py`**

Replace the full contents of `app/main.py`:

```python
from __future__ import annotations
import logging
import threading
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.routes import home, jobs, fetch, profile, scenarios, sources, tasks, inbox
from app.task_engine import run_worker_forever

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    stop_event = threading.Event()
    worker_thread = threading.Thread(target=run_worker_forever, args=(stop_event,), daemon=True)
    worker_thread.start()
    yield
    stop_event.set()
    worker_thread.join(timeout=5)


app = FastAPI(title="Job Seek", lifespan=lifespan)
app.include_router(home.router)
app.include_router(jobs.router)
app.include_router(fetch.router)
app.include_router(profile.router)
app.include_router(scenarios.router)
app.include_router(sources.router)
app.include_router(tasks.router)
app.include_router(inbox.router)
```

This is safe under the existing test suite: `tests/conftest.py`'s `client` fixture does `TestClient(app)` without entering it as a context manager, so lifespan (and thus the worker thread) never starts during unit tests — confirmed by reading Starlette's `TestClient`, which only runs `lifespan` handlers on `__enter__`/`__exit__`.

- [ ] **Step 7: Run to verify pass**

Run: `pytest tests/test_routes_tasks.py tests/test_routes_inbox.py tests/test_routes_home.py -v`
Expected: PASS (including the pre-existing home route tests, confirming the `main.py` rewrite didn't break app startup)

- [ ] **Step 8: Run full suite to check nothing else broke**

Run: `pytest -x -q`
Expected: PASS (all still-unconverted routes are unaffected by this task)

- [ ] **Step 9: Commit**

```bash
git add app/routes/tasks.py app/routes/inbox.py app/templates/tasks/resume.html \
  app/templates/inbox/index.html app/main.py tests/test_routes_tasks.py tests/test_routes_inbox.py
git commit -m "feat: add /tasks and /inbox endpoints, start worker thread on app startup"
```

---

### Task 4: Frontend — polling tray, inbox badge, retire the stream-pump JS

**Files:**
- Modify: `app/templates/base.html`

**Interfaces:**
- Consumes: `GET /tasks/active`, `GET /tasks/{id}` (Task 3). Every converted route (Tasks 5–12) returns JSON `{"task_id": <int>}` from its POST endpoint — this task's JS assumes that contract for any element carrying `data-progress-url`.
- Produces: no new interfaces for other tasks to consume — this is the terminal frontend piece routes 5-12 rely on already being in place. Do this task *before* Task 5, so each converted route can be manually smoke-tested against a working tray as it lands.

- [ ] **Step 1: Add tray markup and inbox nav link**

In `app/templates/base.html`, replace:

```html
  <nav>
    <a href="/" {% if request.url.path == "/" %}class="active"{% endif %}>Start</a>
    <a href="/jobs" {% if request.url.path == "/jobs" %}class="active"{% endif %}>Jobs</a>
    <a href="/fetch" {% if request.url.path == "/fetch" %}class="active"{% endif %}>Fetch</a>
    <a href="/sources" {% if request.url.path == "/sources" %}class="active"{% endif %}>Sources</a>
    <a href="/profile" {% if request.url.path == "/profile" %}class="active"{% endif %}>Profile</a>
    <a href="/scenarios" {% if request.url.path == "/scenarios" %}class="active"{% endif %}>Scenarios</a>
  </nav>
  <main>{% block content %}{% endblock %}</main>
```

with:

```html
  <nav>
    <a href="/" {% if request.url.path == "/" %}class="active"{% endif %}>Start</a>
    <a href="/jobs" {% if request.url.path == "/jobs" %}class="active"{% endif %}>Jobs</a>
    <a href="/fetch" {% if request.url.path == "/fetch" %}class="active"{% endif %}>Fetch</a>
    <a href="/sources" {% if request.url.path == "/sources" %}class="active"{% endif %}>Sources</a>
    <a href="/profile" {% if request.url.path == "/profile" %}class="active"{% endif %}>Profile</a>
    <a href="/scenarios" {% if request.url.path == "/scenarios" %}class="active"{% endif %}>Scenarios</a>
    <a href="/inbox" {% if request.url.path == "/inbox" %}class="active"{% endif %}>Inbox<span id="inbox-badge"></span></a>
  </nav>
  <div id="task-tray"></div>
  <main>{% block content %}{% endblock %}</main>
```

- [ ] **Step 2: Add tray CSS**

In the `<style>` block, after the `.notice-dismiss:hover` rule, add:

```css
    #task-tray:empty { display: none; }
    #task-tray { margin-bottom: 1rem; padding: 0.5rem 0.75rem; border: 1px solid #dee2e6;
      border-radius: 6px; background: #f8f9fa; font-size: 0.85em; color: #444; }
    .tray-task { padding: 0.15rem 0; }
    #inbox-badge:empty { display: none; }
```

- [ ] **Step 3: Replace the progress-button script**

Replace the entire first `<script>...</script>` block (the one starting `function showNotice(html, level) {` and ending with the `submit` event listener, i.e. the block currently spanning what was lines 141–339) with:

```html
  <script>
    (function () {
      function showNotice(html, level) {
        var stack = document.getElementById("notice-stack");
        if (!stack) return;
        var wrapper = document.createElement("div");
        wrapper.className = "notice" + (level === "warning" ? " notice-warning" : "");
        var content = document.createElement("div");
        content.innerHTML = html;
        var dismissBtn = document.createElement("button");
        dismissBtn.type = "button";
        dismissBtn.className = "notice-dismiss";
        dismissBtn.setAttribute("aria-label", "Dismiss");
        dismissBtn.textContent = "×";
        dismissBtn.addEventListener("click", function () { wrapper.remove(); });
        wrapper.appendChild(content);
        wrapper.appendChild(dismissBtn);
        stack.appendChild(wrapper);
        setTimeout(function () {
          wrapper.classList.add("notice-fade-out");
          setTimeout(function () {
            if (wrapper.parentNode) wrapper.remove();
          }, 600);
        }, 30000);
      }

      function applyOob(chunk) {
        var container = document.createElement("div");
        container.innerHTML = chunk;
        Array.prototype.forEach.call(container.children, function (node) {
          if (!node.id) return;
          var existing = document.getElementById(node.id);
          if (!existing) return;
          existing.outerHTML = node.outerHTML;
          var replaced = document.getElementById(node.id);
          if (replaced && window.htmx) window.htmx.process(replaced);
        });
      }

      var watched = {}; // task_id -> {el, progressEl, start, targetSelector, oobMode, bodyFieldNames}

      function finishWatched(taskId, task) {
        var w = watched[taskId];
        if (!w) return;
        delete watched[taskId];
        delete w.el.dataset.progressRunning;
        if (task.status === "failed") {
          w.progressEl.style.color = "#dc3545";
          w.progressEl.textContent = task.error || "Failed";
          setTimeout(function () { w.progressEl.remove(); }, 3500);
          return;
        }
        var result = task.result || {};
        (result.notices || []).forEach(function (n) { showNotice(n.html, n.level); });
        var chunks = result.html_chunks || [];
        w.progressEl.remove();
        for (var k = 0; k < w.bodyFieldNames.length; k++) {
          var clearFieldEl = document.querySelector(w.el.getAttribute("data-progress-body-" + w.bodyFieldNames[k]));
          if (clearFieldEl) clearFieldEl.value = "";
        }
        if (w.oobMode) {
          chunks.forEach(applyOob);
        } else if (w.targetSelector && chunks.length) {
          var target = document.querySelector(w.targetSelector);
          if (target) {
            target.innerHTML = chunks[chunks.length - 1];
            if (window.htmx) window.htmx.process(target);
          }
        } else if (!w.oobMode && !chunks.length) {
          location.reload();
        }
      }

      function pollWatched(taskId) {
        var w = watched[taskId];
        if (!w) return;
        fetch("/tasks/" + taskId).then(function (r) { return r.json(); }).then(function (task) {
          if (!watched[taskId]) return;
          if (task.status === "done" || task.status === "failed") {
            finishWatched(taskId, task);
          } else {
            w.progressEl.textContent = Math.round((Date.now() - w.start) / 1000) + "s" +
              (task.last_line ? " — " + task.last_line : "");
            setTimeout(function () { pollWatched(taskId); }, 2000);
          }
        }).catch(function () {
          setTimeout(function () { pollWatched(taskId); }, 2000);
        });
      }

      function onClick(evt) {
        var el = evt.target.closest("[data-progress-url]");
        if (!el) return;
        evt.preventDefault();
        if (el.dataset.progressRunning === "1") return;
        el.dataset.progressRunning = "1";

        var url = el.getAttribute("data-progress-url");
        var targetSelector = el.getAttribute("data-progress-target");
        var oobMode = el.hasAttribute("data-progress-oob");
        var body = null;
        if (el.hasAttribute("data-progress-jobs")) {
          var checked = document.querySelectorAll('input[name="job_ids"]:checked');
          if (!checked.length) { delete el.dataset.progressRunning; return; }
          body = new URLSearchParams();
          checked.forEach(function (cb) { body.append("job_ids", cb.value); });
        }
        var bodyFieldNames = [];
        for (var i = 0; i < el.attributes.length; i++) {
          var attrName = el.attributes[i].name;
          if (attrName.indexOf("data-progress-body-") === 0) {
            bodyFieldNames.push(attrName.slice("data-progress-body-".length));
          }
        }
        if (bodyFieldNames.length) {
          body = new URLSearchParams();
          for (var j = 0; j < bodyFieldNames.length; j++) {
            var fieldName = bodyFieldNames[j];
            var fieldEl = document.querySelector(el.getAttribute("data-progress-body-" + fieldName));
            if (!fieldEl || !fieldEl.value) { delete el.dataset.progressRunning; return; }
            body.append(fieldName, fieldEl.value);
          }
        }
        var displaySelector = el.getAttribute("data-progress-display");
        var displayTarget = displaySelector ? document.querySelector(displaySelector) : null;
        var progressEl = document.createElement("span");
        if (displayTarget) {
          displayTarget.appendChild(progressEl);
        } else {
          progressEl.style.marginLeft = "0.5rem";
          progressEl.style.color = "#666";
          el.insertAdjacentElement("afterend", progressEl);
        }
        progressEl.textContent = "0s";

        fetch(url, { method: "POST", body: body }).then(function (r) {
          if (!r.ok) throw new Error("Request failed: " + r.status);
          return r.json();
        }).then(function (data) {
          watched[data.task_id] = {
            el: el, progressEl: progressEl, start: Date.now(),
            targetSelector: targetSelector, oobMode: oobMode, bodyFieldNames: bodyFieldNames,
          };
          pollWatched(data.task_id);
        }).catch(function (err) {
          delete el.dataset.progressRunning;
          progressEl.style.color = "#dc3545";
          progressEl.textContent = "Error: " + (err && err.message ? err.message : err);
          setTimeout(function () { progressEl.remove(); }, 3500);
        });
      }

      document.body.addEventListener("click", onClick);

      // A <form> wrapping one of these buttons (e.g. a text input + Add
      // button) has no native submit handler of its own — pressing Enter in
      // the input would otherwise just do a default (and useless) form
      // submission. Forward it to the button's own click handling instead.
      document.body.addEventListener("submit", function (evt) {
        var btn = evt.target.querySelector("[data-progress-url]");
        if (!btn) return;
        evt.preventDefault();
        btn.click();
      });

      function renderTray(tasks) {
        var tray = document.getElementById("task-tray");
        if (!tray) return;
        if (!tasks.length) { tray.innerHTML = ""; return; }
        tray.innerHTML = tasks.map(function (t) {
          var label = t.kind.replace(/_/g, " ") + ": " + (t.last_line || t.status);
          return '<div class="tray-task">' + label.replace(/</g, "&lt;") + '</div>';
        }).join("");
      }

      function pollAmbient() {
        fetch("/tasks/active").then(function (r) { return r.json(); }).then(function (data) {
          renderTray(data.tasks);
          var badge = document.getElementById("inbox-badge");
          if (badge) badge.textContent = data.inbox_count ? " (" + data.inbox_count + ")" : "";
        }).catch(function () {});
      }
      pollAmbient();
      setInterval(pollAmbient, 2000);
      document.addEventListener("visibilitychange", function () {
        if (document.visibilityState === "visible") pollAmbient();
      });
    })();
  </script>
```

This drops the old `getReader()`/`TextDecoder` stream pump and `handleLine` line-protocol parser entirely, replacing them with `pollWatched` (per-task, started right after enqueue) and `pollAmbient` (tray + inbox badge, always running). `applyOob`/`showNotice`/the `data-progress-*` attribute contract on buttons are unchanged, so no template markup elsewhere needs to change for this task.

- [ ] **Step 2 (verification, not a code step): Manual smoke test**

Since no route is converted yet, there's nothing live to click through end-to-end. Defer manual verification to the end of Task 5 (the first converted route), where the tray/watch behavior can actually be exercised via the `run-dev-server` skill against a throwaway DB copy.

- [ ] **Step 3: Run full suite**

Run: `pytest -x -q`
Expected: PASS — this task only touches `base.html`, which no current test asserts the internal script contents of beyond the `data-progress-target`/`notice-stack` markup already covered by existing tests (e.g. `test_fetch_panel_has_fetch_all_button`, `test_fetch_panel_has_notice_stack_and_content_wrapper`), which remain intact.

- [ ] **Step 4: Commit**

```bash
git add app/templates/base.html
git commit -m "feat: replace stream-pump JS with polling tray and inbox badge"
```

---

### Task 5: Convert `fetch.py`

**Files:**
- Modify: `app/routes/fetch.py`
- Test: `tests/test_routes_fetch.py`

**Interfaces:**
- Consumes: `register_task_kind`, `execute_task` (Task 2); `q.enqueue_task`, `q.get_fetch_run` (Task 1).
- Produces: task kinds `fetch_source` (`params: {"source_id": int}`), `fetch_all` (`params: {"source_ids": list[int]}`).

- [ ] **Step 1: Replace `app/routes/fetch.py`**

```python
from __future__ import annotations
import sqlite3
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from app.deps import get_db
from app.db import queries as q
from app.pipeline import run_fetch
from app.task_engine import register_task_kind
from app.template_env import templates

router = APIRouter()


def _fetch_panel_context(conn: sqlite3.Connection) -> dict:
    sources = [s for s in q.get_sources(conn) if s["fetcher_type"] != "manual"]
    runs = q.get_recent_fetch_runs(conn)
    runs_by_source = {}
    for run in runs:
        sid = run["source_id"]
        if sid not in runs_by_source:
            runs_by_source[sid] = run
    stats_by_source = q.get_fetch_stats_by_source(conn)
    return {"sources": sources, "runs_by_source": runs_by_source, "stats_by_source": stats_by_source}


def _auth_error_result(conn: sqlite3.Connection, source: dict, run_id: int) -> dict:
    run = q.get_fetch_run(conn, run_id)
    if run and run["auth_error"]:
        return {
            "needs_action": True,
            "action_message": f"'{source['name']}' needs you to reconnect Slack to keep fetching",
            "action_link": f"/sources#source-row-{source['id']}",
        }
    return {}


@register_task_kind("fetch_source")
def _task_fetch_source(conn, client, model, config, params):
    source = q.get_source(conn, params["source_id"])
    if source is None:
        return {"html_chunks": [], "notices": []}
    gen = run_fetch(source, conn, client, model, config.browser_profile_dir)
    fetch_result = None
    try:
        while True:
            yield next(gen)
    except StopIteration as stop:
        fetch_result = stop.value
    result = {"html_chunks": [], "notices": []}
    if fetch_result is not None:
        result.update(_auth_error_result(conn, source, fetch_result.run_id))
    return result


@register_task_kind("fetch_all")
def _task_fetch_all(conn, client, model, config, params):
    source_ids = params["source_ids"]
    total_found = 0
    total_new = 0
    needs_action_extras = {}
    for idx, source_id in enumerate(source_ids, start=1):
        source = q.get_source(conn, source_id)
        if source is None:
            continue
        label = f"[Source {idx}/{len(source_ids)}: {source['name']}] "
        gen = run_fetch(source, conn, client, model, config.browser_profile_dir)
        try:
            while True:
                yield label + next(gen)
        except StopIteration as stop:
            fetch_result = stop.value
            total_found += fetch_result.jobs_found
            total_new += fetch_result.jobs_new
            extra = _auth_error_result(conn, source, fetch_result.run_id)
            if extra:
                needs_action_extras = extra  # last auth error wins if several sources need reconnecting

    notice_html = templates.get_template("fetch/_fetch_all_summary_notice.html").render(
        total_new=total_new, total_found=total_found, source_count=len(source_ids),
    )
    html_chunks = []
    if source_ids:
        html_chunks.append(
            templates.get_template("fetch/_table.html").render(request=None, **_fetch_panel_context(conn))
        )
    result = {"notices": [{"level": "info", "html": notice_html}], "html_chunks": html_chunks}
    result.update(needs_action_extras)
    return result


@router.get("/fetch", response_class=HTMLResponse)
def fetch_panel(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    return templates.TemplateResponse(request, "fetch/panel.html", _fetch_panel_context(conn))


@router.post("/fetch/all")
def trigger_fetch_all(conn: sqlite3.Connection = Depends(get_db)):
    sources = [s for s in q.get_sources(conn) if s["fetcher_type"] != "manual" and s["enabled"]]
    task = q.enqueue_task(conn, kind="fetch_all", params={"source_ids": [s["id"] for s in sources]})
    return {"task_id": task["id"]}


@router.post("/fetch/{source_id}")
def trigger_fetch(source_id: int, conn: sqlite3.Connection = Depends(get_db)):
    source = q.get_source(conn, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    task = q.enqueue_task(conn, kind="fetch_source", params={"source_id": source_id})
    return {"task_id": task["id"]}
```

- [ ] **Step 2: Update `tests/test_routes_fetch.py`**

The streaming-specific tests no longer apply (`test_post_fetch_streams_progress`, `test_post_fetch_all_streams_each_enabled_source`, `test_post_fetch_all_skips_disabled_and_manual_sources`, `test_post_fetch_all_emits_notice_line_with_aggregate_stats`, `test_post_fetch_all_emits_html_line_with_updated_table`, `test_post_fetch_all_with_no_enabled_sources_emits_notice_and_no_html`) — they assert on `resp.text` stream content that no longer exists (POST endpoints now return JSON `{"task_id": ...}` immediately). Delete those six functions and their two `_fake_run_fetch*` helpers, and replace with the following (keep every other existing test in the file as-is — the `GET /fetch` panel tests are unaffected):

```python
from app.task_engine import execute_task


def test_post_fetch_enqueues_task(client, conn):
    sid = _seed(conn)
    resp = client.post(f"/fetch/{sid}")
    assert resp.status_code == 200
    task_id = resp.json()["task_id"]
    task = q.get_task(conn, task_id)
    assert task["kind"] == "fetch_source"
    assert task["params"] == {"source_id": sid}
    assert task["status"] == "queued"


def test_post_fetch_unknown_source_returns_404(client, conn):
    resp = client.post("/fetch/999")
    assert resp.status_code == 404


def _fake_run_fetch(*args, **kwargs):
    yield "Starting fetch for 'finn.no' (http)"
    yield "Fetch complete for 'finn.no': 2 new / 3 found"
    return FetchResult(source_id=1, run_id=1, jobs_found=3, jobs_new=2, error=None)


def test_fetch_source_task_execution_updates_log_and_status(conn):
    sid = _seed(conn)
    run_id = q.start_fetch_run(conn, sid)
    q.complete_fetch_run(conn, run_id, jobs_found=3, jobs_new=2)
    task = q.enqueue_task(conn, kind="fetch_source", params={"source_id": sid})
    with patch("app.routes.fetch.run_fetch", side_effect=_fake_run_fetch):
        execute_task(conn, MagicMock(), "model", MagicMock(browser_profile_dir="/tmp"), task)
    fetched = q.get_task(conn, task["id"])
    assert fetched["status"] == "done"
    assert "Fetch complete for 'finn.no': 2 new / 3 found" in fetched["log"]


def test_fetch_source_task_flags_auth_error_as_needing_action(conn):
    sid = _seed(conn)

    def fake_fetch(*args, **kwargs):
        run_id = q.start_fetch_run(conn, sid)
        q.complete_fetch_run(conn, run_id, jobs_found=0, jobs_new=0, error="auth", auth_error=True)
        yield "Fetch failed"
        return FetchResult(source_id=sid, run_id=run_id, jobs_found=0, jobs_new=0, error="auth")

    task = q.enqueue_task(conn, kind="fetch_source", params={"source_id": sid})
    with patch("app.routes.fetch.run_fetch", side_effect=fake_fetch):
        execute_task(conn, MagicMock(), "model", MagicMock(browser_profile_dir="/tmp"), task)
    fetched = q.get_task(conn, task["id"])
    assert fetched["result"]["needs_action"] is True
    assert "reconnect Slack" in fetched["result"]["action_message"]
    items = q.get_unresolved_inbox_items(conn)
    assert len(items) == 1


def test_fetch_all_task_execution_aggregates_and_renders_table(conn):
    sid1 = q.insert_source(conn, "finn.no", "https://finn.no", "generic_listing")
    sid2 = q.insert_source(conn, "other.no", "https://other.no", "generic_listing")

    def fake_fetch_all(source, conn, *args, **kwargs):
        run_id = q.start_fetch_run(conn, source["id"])
        yield f"Starting fetch for '{source['name']}'"
        q.complete_fetch_run(conn, run_id, jobs_found=1, jobs_new=1)
        return FetchResult(source_id=source["id"], run_id=run_id, jobs_found=1, jobs_new=1, error=None)

    task = q.enqueue_task(conn, kind="fetch_all", params={"source_ids": [sid1, sid2]})
    with patch("app.routes.fetch.run_fetch", side_effect=fake_fetch_all):
        execute_task(conn, MagicMock(), "model", MagicMock(browser_profile_dir="/tmp"), task)
    fetched = q.get_task(conn, task["id"])
    assert fetched["status"] == "done"
    assert fetched["result"]["notices"][0]["html"]
    assert "2" in fetched["result"]["notices"][0]["html"]
    assert f'id="fetch-row-{sid1}"' in fetched["result"]["html_chunks"][0]


def test_fetch_all_with_no_sources_has_no_html_chunk(conn):
    task = q.enqueue_task(conn, kind="fetch_all", params={"source_ids": []})
    execute_task(conn, MagicMock(), "model", MagicMock(browser_profile_dir="/tmp"), task)
    fetched = q.get_task(conn, task["id"])
    assert fetched["result"]["html_chunks"] == []
```

Add `from unittest.mock import MagicMock` to the existing `from unittest.mock import patch, MagicMock` import line if not already present (it already imports `MagicMock` per the file's current top import — confirm and reuse).

- [ ] **Step 2: Run to verify pass**

Run: `pytest tests/test_routes_fetch.py -v`
Expected: PASS

- [ ] **Step 3: Run full suite**

Run: `pytest -x -q`
Expected: PASS

- [ ] **Step 4: Manual smoke test**

Use the `run-dev-server` skill to start the app against a throwaway DB copy. Add a real source (or seed one), click "Fetch" on `/fetch`, and confirm: the button shows a live "Ns — <log line>" progress indicator (via polling, not streaming), the page updates once done (table refresh via `data-progress-target="#fetch-content"`), and the tray under the nav shows the task while running. Stop the dev server when done.

- [ ] **Step 5: Commit**

```bash
git add app/routes/fetch.py tests/test_routes_fetch.py
git commit -m "feat: convert fetch endpoints to background tasks"
```

---

### Task 6: Convert `jobs.py` single-job operations (reset, pass-as-new, reevaluate)

**Files:**
- Modify: `app/routes/jobs.py`
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Produces: task kinds `job_reset`, `job_pass_as_new`, `job_reevaluate`, each with `params: {"job_id": int, "filter_ctx": dict}`.

- [ ] **Step 1: Add task-kind functions to `app/routes/jobs.py`**

Add `from app.task_engine import register_task_kind` to the imports. Replace the three streaming route functions (`job_reset`, `job_pass_as_new`, `job_reevaluate`, currently lines 268–360) with:

```python
@register_task_kind("job_reset")
def _task_job_reset(conn, client, model, config, params):
    job = q.get_job(conn, params["job_id"])
    if job is None:
        return {"html_chunks": [], "notices": []}
    scenarios = q.get_scenarios(conn)
    profile = q.get_profile(conn)
    gen = run_reprocess_job(conn, client, model, job, scenarios, profile)
    try:
        while True:
            yield next(gen)
    except StopIteration:
        pass
    html = _render_updated_job_html(conn, None, params["job_id"], params["filter_ctx"])
    counts_html = templates.get_template("jobs/_counts_oob.html").render(
        request=None, counts=q.get_job_counts(conn)
    )
    return {"html_chunks": [html, counts_html], "notices": []}


@register_task_kind("job_pass_as_new")
def _task_job_pass_as_new(conn, client, model, config, params):
    job = q.get_job(conn, params["job_id"])
    if job is None:
        return {"html_chunks": [], "notices": []}
    profile = q.get_profile(conn)
    gen = run_pass_as_new(conn, client, model, job, profile)
    try:
        while True:
            yield next(gen)
    except StopIteration:
        pass
    html = _render_updated_job_html(conn, None, params["job_id"], params["filter_ctx"])
    counts_html = templates.get_template("jobs/_counts_oob.html").render(
        request=None, counts=q.get_job_counts(conn)
    )
    return {"html_chunks": [html, counts_html], "notices": []}


@register_task_kind("job_reevaluate")
def _task_job_reevaluate(conn, client, model, config, params):
    job = q.get_job(conn, params["job_id"])
    if job is None:
        return {"html_chunks": [], "notices": []}
    scenarios = q.get_scenarios(conn)
    profile = q.get_profile(conn)
    gen = run_reevaluate_job(conn, client, model, job, scenarios, profile)
    try:
        while True:
            yield next(gen)
    except StopIteration:
        pass
    html = _render_updated_job_html(conn, None, params["job_id"], params["filter_ctx"])
    counts_html = templates.get_template("jobs/_counts_oob.html").render(
        request=None, counts=q.get_job_counts(conn)
    )
    return {"html_chunks": [html, counts_html], "notices": []}


@router.post("/jobs/{job_id}/reset")
def job_reset(job_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    job = q.get_job(conn, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    task = q.enqueue_task(
        conn, kind="job_reset", params={"job_id": job_id, "filter_ctx": _filter_context(request)}
    )
    return {"task_id": task["id"]}


@router.post("/jobs/{job_id}/pass-as-new")
def job_pass_as_new(job_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    job = q.get_job(conn, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    task = q.enqueue_task(
        conn, kind="job_pass_as_new", params={"job_id": job_id, "filter_ctx": _filter_context(request)}
    )
    return {"task_id": task["id"]}


@router.post("/jobs/{job_id}/reevaluate")
def job_reevaluate(job_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    job = q.get_job(conn, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    task = q.enqueue_task(
        conn, kind="job_reevaluate", params={"job_id": job_id, "filter_ctx": _filter_context(request)}
    )
    return {"task_id": task["id"]}
```

Note these three routes no longer need `client`/`model` dependencies — drop `client: openai.OpenAI = Depends(get_ai_client), model: str = Depends(get_model)` from their signatures (the `openai` import and `get_ai_client`/`get_model` imports stay in the file since other not-yet-converted endpoints in this same file still use them until Tasks 7–9 land).

- [ ] **Step 2: Update `tests/test_routes_jobs.py`**

Search the file for tests covering `POST /jobs/{job_id}/reset`, `POST /jobs/{job_id}/pass-as-new`, `POST /jobs/{job_id}/reevaluate` (`grep -n "def test.*\(reset\|pass_as_new\|reevaluate\)" tests/test_routes_jobs.py` — exclude `bulk_reset`/`bulk_reevaluate`, those are Task 7's). Each such test currently posts to the endpoint and asserts on `resp.text` stream contents; replace each with two variants following this pattern (shown for `reset`; mirror for the other two kinds):

```python
def test_job_reset_enqueues_task(client, conn):
    job_id = _seed_job(conn)  # reuse whatever existing seed helper the file already has
    resp = client.post(f"/jobs/{job_id}/reset")
    assert resp.status_code == 200
    task = q.get_task(conn, resp.json()["task_id"])
    assert task["kind"] == "job_reset"
    assert task["params"]["job_id"] == job_id


def test_job_reset_unknown_job_returns_404(client, conn):
    resp = client.post("/jobs/999/reset")
    assert resp.status_code == 404


def test_job_reset_task_execution_updates_job_and_renders_fragment(conn):
    job_id = _seed_job(conn)
    task = q.enqueue_task(conn, kind="job_reset", params={"job_id": job_id, "filter_ctx": {}})
    with patch("app.routes.jobs.run_reprocess_job") as mock_gen:
        mock_gen.return_value = iter(["Reset complete: url"])
        execute_task(conn, MagicMock(), "model", MagicMock(), task)
    fetched = q.get_task(conn, task["id"])
    assert fetched["status"] == "done"
    assert len(fetched["result"]["html_chunks"]) == 2
```

Use the file's existing job-seeding helper (grep for `def _seed` / `def _make_job` near the top of the file) rather than inventing a new one — match its exact name and signature. Add `from app.task_engine import execute_task` to the test file's imports if not already present via a prior task's edits to a shared conftest (it isn't — add it here).

- [ ] **Step 3: Run to verify pass**

Run: `pytest tests/test_routes_jobs.py -v -k "reset or pass_as_new or reevaluate and not bulk"`
Expected: PASS

- [ ] **Step 4: Run full suite**

Run: `pytest -x -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/routes/jobs.py tests/test_routes_jobs.py
git commit -m "feat: convert single-job reset/pass-as-new/reevaluate to background tasks"
```

---

### Task 7: Convert `jobs.py` bulk operations (bulk-reset, bulk-reevaluate)

**Files:**
- Modify: `app/routes/jobs.py`
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Produces: task kinds `jobs_bulk_reset`, `jobs_bulk_reevaluate`, each with `params: {"job_ids": list[int], "filter_ctx": dict}`.

- [ ] **Step 1: Add task-kind functions, replace the two bulk routes**

Replace `job_bulk_reset` and `job_bulk_reevaluate` (currently lines 363–432) with:

```python
@register_task_kind("jobs_bulk_reset")
def _task_jobs_bulk_reset(conn, client, model, config, params):
    job_ids = params["job_ids"]
    filter_ctx = params["filter_ctx"]
    scenarios = q.get_scenarios(conn)
    profile = q.get_profile(conn)
    yield f"Resetting {len(job_ids)} job(s)"
    html_chunks = []
    for idx, job_id in enumerate(job_ids, start=1):
        job = q.get_job(conn, job_id)
        if not job:
            continue
        prefix = f"[{idx}/{len(job_ids)}] "
        gen = run_reprocess_job(conn, client, model, job, scenarios, profile, progress_prefix=prefix)
        try:
            while True:
                yield next(gen)
        except StopIteration:
            pass
        html_chunks.append(_render_updated_job_html(conn, None, job_id, filter_ctx))
    html_chunks.append(
        templates.get_template("jobs/_counts_oob.html").render(request=None, counts=q.get_job_counts(conn))
    )
    yield f"Reset complete: {len(job_ids)} job(s) reprocessed"
    return {"html_chunks": html_chunks, "notices": []}


@register_task_kind("jobs_bulk_reevaluate")
def _task_jobs_bulk_reevaluate(conn, client, model, config, params):
    job_ids = params["job_ids"]
    filter_ctx = params["filter_ctx"]
    scenarios = q.get_scenarios(conn)
    profile = q.get_profile(conn)
    yield f"Re-evaluating {len(job_ids)} job(s)"
    html_chunks = []
    for idx, job_id in enumerate(job_ids, start=1):
        job = q.get_job(conn, job_id)
        if not job:
            continue
        prefix = f"[{idx}/{len(job_ids)}] "
        gen = run_reevaluate_job(conn, client, model, job, scenarios, profile, progress_prefix=prefix)
        try:
            while True:
                yield next(gen)
        except StopIteration:
            pass
        html_chunks.append(_render_updated_job_html(conn, None, job_id, filter_ctx))
    html_chunks.append(
        templates.get_template("jobs/_counts_oob.html").render(request=None, counts=q.get_job_counts(conn))
    )
    yield f"Re-evaluation complete: {len(job_ids)} job(s) updated"
    return {"html_chunks": html_chunks, "notices": []}


@router.post("/jobs/bulk-reset")
def job_bulk_reset(request: Request, job_ids: list[int] = Form(...), conn: sqlite3.Connection = Depends(get_db)):
    task = q.enqueue_task(
        conn, kind="jobs_bulk_reset", params={"job_ids": job_ids, "filter_ctx": _filter_context(request)}
    )
    return {"task_id": task["id"]}


@router.post("/jobs/bulk-reevaluate")
def job_bulk_reevaluate(request: Request, job_ids: list[int] = Form(...), conn: sqlite3.Connection = Depends(get_db)):
    task = q.enqueue_task(
        conn, kind="jobs_bulk_reevaluate", params={"job_ids": job_ids, "filter_ctx": _filter_context(request)}
    )
    return {"task_id": task["id"]}
```

Both bulk buttons in `app/templates/jobs/_content.html` already carry `data-progress-oob="1"` (confirmed by reading that template earlier) — the frontend from Task 4 applies every chunk in `html_chunks` via `applyOob` for oob-mode tasks, so the per-job row chunks and the trailing counts chunk are each matched and swapped by their embedded element `id`, exactly matching the old behavior. No template change needed.

- [ ] **Step 2: Update `tests/test_routes_jobs.py`**

Find and replace the existing `bulk-reset`/`bulk-reevaluate` streaming tests (`grep -n "def test.*bulk_re" tests/test_routes_jobs.py`) with the enqueue+execute pattern from Task 6, adapted for list params:

```python
def test_bulk_reset_enqueues_task_with_job_ids(client, conn):
    j1, j2 = _seed_job(conn), _seed_job(conn)
    resp = client.post("/jobs/bulk-reset", data={"job_ids": [j1, j2]})
    assert resp.status_code == 200
    task = q.get_task(conn, resp.json()["task_id"])
    assert task["kind"] == "jobs_bulk_reset"
    assert task["params"]["job_ids"] == [j1, j2]


def test_bulk_reset_task_execution_produces_chunk_per_job_plus_counts(conn):
    j1, j2 = _seed_job(conn), _seed_job(conn)
    task = q.enqueue_task(conn, kind="jobs_bulk_reset", params={"job_ids": [j1, j2], "filter_ctx": {}})
    with patch("app.routes.jobs.run_reprocess_job") as mock_gen:
        mock_gen.return_value = iter(["ok"])
        execute_task(conn, MagicMock(), "model", MagicMock(), task)
    fetched = q.get_task(conn, task["id"])
    assert fetched["status"] == "done"
    assert len(fetched["result"]["html_chunks"]) == 3  # 2 job rows + counts


def test_bulk_reevaluate_enqueues_task_with_job_ids(client, conn):
    j1 = _seed_job(conn)
    resp = client.post("/jobs/bulk-reevaluate", data={"job_ids": [j1]})
    assert resp.status_code == 200
    task = q.get_task(conn, resp.json()["task_id"])
    assert task["kind"] == "jobs_bulk_reevaluate"
```

Use `data={"job_ids": [j1, j2]}` (a list value under one key), not a list of tuples — matches this project's established httpx `TestClient` convention for repeated form fields on `list[X] = Form(...)` routes.

- [ ] **Step 3: Run to verify pass**

Run: `pytest tests/test_routes_jobs.py -v -k "bulk_reset or bulk_reevaluate"`
Expected: PASS

- [ ] **Step 4: Run full suite**

Run: `pytest -x -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/routes/jobs.py tests/test_routes_jobs.py
git commit -m "feat: convert bulk job reset/reevaluate to background tasks"
```

---

### Task 8: Convert `sources.py` (detect, detect/confirm) — first inbox-producing "needs a decision" flow

**Files:**
- Modify: `app/routes/sources.py`
- Test: `tests/test_routes_sources.py`

**Interfaces:**
- Produces: task kinds `source_detect` (`params: {"url": str}`), `source_confirm` (`params: {"url": str, "name": str, "fetcher_type": str}`).
- Changes shared helper `check_already_tracked_notice(conn, request, url) -> str | None` to `check_already_tracked_notice_data(conn, url) -> dict | None` (drops the unused `request` param, returns a `{level, html}` dict instead of a pre-escaped `NOTICE:`-prefixed string). **Task 9 depends on this new signature** — do this task before Task 9.

- [ ] **Step 1: Refactor `check_already_tracked_notice`, add task-kind functions, replace the two routes**

In `app/routes/sources.py`, add `from app.task_engine import register_task_kind` to imports. Replace `_notice_line` and `check_already_tracked_notice`:

```python
def check_already_tracked_notice_data(conn: sqlite3.Connection, url: str) -> dict | None:
    existing_source = q.get_source_by_url(conn, url)
    if existing_source is not None:
        return {
            "level": "info",
            "html": templates.get_template("jobs/_already_tracked.html").render(
                request=None, kind="source",
                link_href=f"/sources#source-row-{existing_source['id']}",
                link_text=f'View "{existing_source["name"]}" in Sources',
            ),
        }
    existing_job = q.get_job_by_url(conn, url)
    if existing_job is not None:
        return {
            "level": "info",
            "html": templates.get_template("jobs/_already_tracked.html").render(
                request=None, kind="job", link_href=f"/jobs/{existing_job['id']}", link_text="View this job",
            ),
        }
    return None
```

(Delete the old `_notice_line` function — nothing in this file needs it anymore once both routes below are converted.)

Replace `detect_source` and `confirm_source` (currently lines 100–199) with:

```python
@register_task_kind("source_detect")
def _task_source_detect(conn, client, model, config, params):
    url = params["url"]
    already_tracked = check_already_tracked_notice_data(conn, url)
    if already_tracked is not None:
        return {"notices": [already_tracked], "html_chunks": []}

    default_name = urlsplit(url).netloc
    fetcher_type = classify_known_source(url)
    if fetcher_type is None:
        yield "Checking the page..."
        try:
            html = fetch_url_html(url)
        except FetchError:
            fetcher_type = "generic_listing"
        else:
            if not has_enough_content(html):
                rendered = render_html(url)
                if rendered and has_enough_content(rendered):
                    html = rendered
            page_title = extract_page_title(html)
            if page_title:
                generated_name = generate_source_name(client, model, default_name, page_title)
                if generated_name:
                    default_name = generated_name
            links = extract_links(html, url)
            detection = detect_listing(client, model, links, url)
            if detection["is_listing"] and len(detection["job_links"]) >= 2:
                fetcher_type = "generic_listing"

    if fetcher_type is not None:
        panel = templates.get_template("sources/_detect_confirm.html").render(
            request=None, url=url, name=default_name, fetcher_type=fetcher_type,
        )
    else:
        panel = templates.get_template("sources/_detect_mismatch.html").render(
            request=None, url=url, name=default_name,
        )
    return {
        "notices": [], "html_chunks": [panel],
        "needs_action": True,
        "action_message": f"New source detected: {default_name}",
        "resume_html": panel,
    }


@register_task_kind("source_confirm")
def _task_source_confirm(conn, client, model, config, params):
    url, name, fetcher_type = params["url"], params["name"], params["fetcher_type"]
    already_tracked = check_already_tracked_notice_data(conn, url)
    if already_tracked is not None:
        table = templates.get_template("sources/_table.html").render(request=None, **_sources_context(conn))
        add_form = templates.get_template("sources/_add_form.html").render(request=None)
        return {"notices": [already_tracked], "html_chunks": [table, add_form]}

    source_id = q.insert_source(conn, name, url, fetcher_type)
    source = q.get_source(conn, source_id)
    gen = run_fetch(source, conn, client, model, config.browser_profile_dir)
    fetch_result = None
    try:
        while True:
            yield next(gen)
    except StopIteration as stop:
        fetch_result = stop.value

    notices = []
    if fetch_result is not None and fetch_result.jobs_found == 0:
        notices.append({
            "level": "warning",
            "html": templates.get_template("sources/_fetch_nothing_found_notice.html").render(
                request=None, name=name, url=url, error=fetch_result.error, source_id=source_id,
            ),
        })

    table = templates.get_template("sources/_table.html").render(request=None, **_sources_context(conn))
    add_form = templates.get_template("sources/_add_form.html").render(request=None)
    return {"notices": notices, "html_chunks": [table, add_form]}


@router.post("/sources/detect")
def detect_source(url: str = Form(...), conn: sqlite3.Connection = Depends(get_db)):
    task = q.enqueue_task(conn, kind="source_detect", params={"url": url})
    return {"task_id": task["id"]}


@router.post("/sources/detect/confirm")
def confirm_source(
    url: str = Form(...), name: str = Form(...), fetcher_type: str = Form(...),
    conn: sqlite3.Connection = Depends(get_db),
):
    if fetcher_type not in DETECTABLE_FETCHER_TYPES:
        raise HTTPException(status_code=400, detail="Invalid fetcher_type")
    task = q.enqueue_task(
        conn, kind="source_confirm", params={"url": url, "name": name, "fetcher_type": fetcher_type}
    )
    return {"task_id": task["id"]}
```

Drop the now-unused `client`/`model`/`config` `Depends(...)` params from both route signatures (kept only inside the task-kind functions, which the worker supplies).

- [ ] **Step 2: Update `tests/test_routes_sources.py`**

`grep -n "def test.*detect" tests/test_routes_sources.py` to find the existing streaming-based tests for `POST /sources/detect` and `POST /sources/detect/confirm`; delete those and add:

```python
from app.task_engine import execute_task
from unittest.mock import MagicMock


def test_detect_source_enqueues_task(client, conn):
    resp = client.post("/sources/detect", data={"url": "https://example.com/jobs"})
    assert resp.status_code == 200
    task = q.get_task(conn, resp.json()["task_id"])
    assert task["kind"] == "source_detect"
    assert task["params"]["url"] == "https://example.com/jobs"


def test_source_detect_task_flags_needs_action_with_resume_html(conn):
    task = q.enqueue_task(conn, kind="source_detect", params={"url": "https://example.com/jobs"})
    with patch("app.routes.sources.classify_known_source", return_value="generic_listing"):
        execute_task(conn, MagicMock(), "model", MagicMock(), task)
    fetched = q.get_task(conn, task["id"])
    assert fetched["result"]["needs_action"] is True
    assert "example.com" in fetched["result"]["action_message"]
    items = q.get_unresolved_inbox_items(conn)
    assert items[0]["link"] == f"/tasks/{task['id']}/resume"


def test_source_detect_already_tracked_short_circuits(conn):
    q.insert_source(conn, "existing", "https://tracked.example.com", "generic_listing")
    task = q.enqueue_task(conn, kind="source_detect", params={"url": "https://tracked.example.com"})
    execute_task(conn, MagicMock(), "model", MagicMock(), task)
    fetched = q.get_task(conn, task["id"])
    assert fetched["result"].get("needs_action") is None
    assert q.get_unresolved_inbox_items(conn) == []


def test_confirm_source_enqueues_task(client, conn):
    resp = client.post(
        "/sources/detect/confirm",
        data={"url": "https://example.com/jobs", "name": "Example", "fetcher_type": "generic_listing"},
    )
    assert resp.status_code == 200
    task = q.get_task(conn, resp.json()["task_id"])
    assert task["kind"] == "source_confirm"


def test_confirm_source_invalid_fetcher_type_returns_400(client, conn):
    resp = client.post(
        "/sources/detect/confirm",
        data={"url": "https://example.com/jobs", "name": "Example", "fetcher_type": "bogus"},
    )
    assert resp.status_code == 400


def test_source_confirm_task_execution_inserts_source_and_renders_table(conn):
    def fake_run_fetch(source, conn, *a, **k):
        run_id = q.start_fetch_run(conn, source["id"])
        yield "Starting fetch"
        q.complete_fetch_run(conn, run_id, jobs_found=1, jobs_new=1)
        return FetchResult(source_id=source["id"], run_id=run_id, jobs_found=1, jobs_new=1, error=None)

    task = q.enqueue_task(
        conn, kind="source_confirm",
        params={"url": "https://example.com/jobs", "name": "Example", "fetcher_type": "generic_listing"},
    )
    with patch("app.routes.sources.run_fetch", side_effect=fake_run_fetch):
        execute_task(conn, MagicMock(), "model", MagicMock(browser_profile_dir="/tmp"), task)
    fetched = q.get_task(conn, task["id"])
    assert fetched["status"] == "done"
    assert q.get_source_by_url(conn, "https://example.com/jobs") is not None
    assert len(fetched["result"]["html_chunks"]) == 2
```

Add `from app.pipeline import FetchResult` to the test file's imports if not already present.

- [ ] **Step 3: Run to verify pass**

Run: `pytest tests/test_routes_sources.py -v`
Expected: PASS

- [ ] **Step 4: Run full suite**

Run: `pytest -x -q`
Expected: PASS — note `app/routes/jobs.py` imports `check_already_tracked_notice` from this module (`from app.routes.sources import check_already_tracked_notice`); that import now points at a function that no longer exists under that name. This will break `app/routes/jobs.py`'s import at collection time. Fix it now: change the import line in `app/routes/jobs.py` from `from app.routes.sources import check_already_tracked_notice` to `from app.routes.sources import check_already_tracked_notice_data`, even though `jobs.py`'s actual call site isn't updated to use it until Task 9 — this keeps the import valid and the full suite green after this task.

- [ ] **Step 5: Commit**

```bash
git add app/routes/sources.py app/routes/jobs.py tests/test_routes_sources.py
git commit -m "feat: convert source detect/confirm to background tasks with inbox follow-up"
```

---

### Task 9: Convert `jobs.py` add flows (add-by-url, add-listing-source)

**Files:**
- Modify: `app/routes/jobs.py`
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: `check_already_tracked_notice_data` from Task 8.
- Produces: task kinds `job_add_by_url` (`params: {"url": str, "status": str|None, "content_type": str|None, "filter_ctx": dict}`), `job_add_listing_source` (`params: {"url": str, "name": str, "fetcher_type": str, "status": str|None, "content_type": str|None}`).

- [ ] **Step 1: Add task-kind functions, replace the two routes**

Replace `job_add_by_url` and `job_add_listing_source` (currently lines 526–695), and the module's `_notice_line` helper usage within them, with:

```python
@register_task_kind("job_add_by_url")
def _task_job_add_by_url(conn, client, model, config, params):
    url = params["url"]
    status = params.get("status")
    content_type = params.get("content_type")
    notices: list[dict] = []
    html_chunks: list[str] = []
    result = {"notices": notices, "html_chunks": html_chunks}

    existing_job = q.get_job_by_url(conn, url)
    if existing_job is not None:
        if existing_job["content_type"] == "error":
            notices.append({
                "level": "warning",
                "html": templates.get_template("jobs/_already_tracked.html").render(
                    request=None, kind="error", link_href=f"/jobs/{existing_job['id']}",
                ),
            })
        else:
            notices.append({
                "level": "info",
                "html": templates.get_template("jobs/_already_tracked.html").render(
                    request=None, kind="job", link_href=f"/jobs/{existing_job['id']}", link_text="View this job",
                ),
            })
    else:
        already_tracked = check_already_tracked_notice_data(conn, url)
        if already_tracked is not None:
            notices.append(already_tracked)
        else:
            try:
                html = fetch_url_html(url)
            except FetchError as exc:
                _insert_error_job(conn, url, f"Failed to fetch: {exc}")
                notices.append({
                    "level": "warning",
                    "html": templates.get_template("jobs/_fetch_failed_notice.html").render(
                        request=None, url=url, reason=str(exc),
                    ),
                })
            else:
                if not has_enough_content(html):
                    rendered = render_html(url)
                    if rendered and has_enough_content(rendered):
                        html = rendered
                links = extract_links(html, url)
                detection = detect_listing(client, model, links, url)
                if detection["is_listing"] and len(detection["job_links"]) >= 2:
                    domain = urlsplit(url).netloc
                    default_name = domain
                    page_title = extract_page_title(html)
                    if page_title:
                        generated_name = generate_source_name(client, model, domain, page_title)
                        if generated_name:
                            default_name = generated_name
                    panel_context = {
                        "request": None, "url": url,
                        "fetcher_type": classify_known_source(url) or "generic_listing",
                        "link_count": len(detection["job_links"]), "domain": domain,
                        "default_name": default_name,
                    }
                    panel_context.update(params.get("filter_ctx", {}))
                    panel = templates.get_template("jobs/_listing_confirm.html").render(**panel_context)
                    html_chunks.append(panel)
                    result["needs_action"] = True
                    result["action_message"] = f"'{default_name}' looks like a job listing — confirm how to add it"
                    result["resume_html"] = panel
                    return result
                try:
                    raw_text = extract_text_or_raise(html)
                except NoContentError:
                    _insert_error_job(
                        conn, url, "No extractable content — page likely requires JavaScript to render"
                    )
                    notices.append({
                        "level": "warning",
                        "html": templates.get_template("jobs/_no_content_notice.html").render(request=None, url=url),
                    })
                else:
                    source_id = q.get_or_create_manual_source(conn)
                    gen = run_add_job(conn, client, model, source_id, url, raw_text)
                    try:
                        while True:
                            yield next(gen)
                    except StopIteration:
                        pass
                    added_job = q.get_job_by_url(conn, url)
                    if added_job is None:
                        notices.append({
                            "level": "info",
                            "html": templates.get_template("jobs/_discarded_notice.html").render(request=None),
                        })
                    else:
                        added_job = q.get_job(conn, added_job["id"])
                        if added_job["content_type"] == "lead":
                            notices.append({
                                "level": "info",
                                "html": templates.get_template("jobs/_lead_added_notice.html").render(
                                    request=None, job_id=added_job["id"],
                                ),
                            })
                        elif added_job["content_type"] == "error":
                            notices.append({
                                "level": "warning",
                                "html": templates.get_template("jobs/_error_added_notice.html").render(
                                    request=None, job_id=added_job["id"],
                                ),
                            })
                        elif added_job["content_type"] == "job_posting":
                            passed_gate = bool(added_job["passed_gate_count"]) or bool(added_job["gate_override"])
                            template_name = (
                                "jobs/_job_added_notice.html" if passed_gate
                                else "jobs/_not_relevant_added_notice.html"
                            )
                            notices.append({
                                "level": "info",
                                "html": templates.get_template(template_name).render(
                                    request=None, job_id=added_job["id"],
                                ),
                            })

    html_chunks.append(
        templates.get_template("jobs/_content.html").render(
            request=None, **_content_context(conn, status, content_type)
        )
    )
    return result


@register_task_kind("job_add_listing_source")
def _task_job_add_listing_source(conn, client, model, config, params):
    url, name, fetcher_type = params["url"], params["name"], params["fetcher_type"]
    status = params.get("status")
    content_type = params.get("content_type")
    notices = []

    already_tracked = check_already_tracked_notice_data(conn, url)
    if already_tracked is not None:
        notices.append(already_tracked)
        html_chunks = [templates.get_template("jobs/_content.html").render(
            request=None, **_content_context(conn, status, content_type)
        )]
        return {"notices": notices, "html_chunks": html_chunks}

    source_id = q.insert_source(conn, name, url, fetcher_type)
    source = q.get_source(conn, source_id)
    gen = run_fetch(source, conn, client, model, config.browser_profile_dir)
    fetch_result = None
    try:
        while True:
            yield next(gen)
    except StopIteration as stop:
        fetch_result = stop.value

    if fetch_result is not None and fetch_result.jobs_found == 0:
        notices.append({
            "level": "warning",
            "html": templates.get_template("sources/_fetch_nothing_found_notice.html").render(
                request=None, name=name, url=url, error=fetch_result.error, source_id=source_id,
            ),
        })
    else:
        notices.append({
            "level": "info",
            "html": templates.get_template("jobs/_source_added_notice.html").render(
                request=None, name=name, source_id=source_id,
            ),
        })

    html_chunks = [templates.get_template("jobs/_content.html").render(
        request=None, **_content_context(conn, status, content_type)
    )]
    return {"notices": notices, "html_chunks": html_chunks}


@router.post("/jobs/add-by-url")
def job_add_by_url(request: Request, url: str = Form(...), conn: sqlite3.Connection = Depends(get_db)):
    task = q.enqueue_task(conn, kind="job_add_by_url", params={
        "url": url,
        "status": request.query_params.get("status") or None,
        "content_type": request.query_params.get("content_type") or None,
        "filter_ctx": _filter_context(request),
    })
    return {"task_id": task["id"]}


@router.post("/jobs/add-listing-source")
def job_add_listing_source(
    request: Request, url: str = Form(...), name: str = Form(...), fetcher_type: str = Form(...),
    conn: sqlite3.Connection = Depends(get_db),
):
    if fetcher_type not in DETECTABLE_FETCHER_TYPES:
        raise HTTPException(status_code=400, detail="Invalid fetcher_type")
    task = q.enqueue_task(conn, kind="job_add_listing_source", params={
        "url": url, "name": name, "fetcher_type": fetcher_type,
        "status": request.query_params.get("status") or None,
        "content_type": request.query_params.get("content_type") or None,
    })
    return {"task_id": task["id"]}
```

Update the module's import line `from app.routes.sources import check_already_tracked_notice` (already changed to `check_already_tracked_notice_data` at the end of Task 8) — verify it reads `from app.routes.sources import check_already_tracked_notice_data` and that this task's new code calls it by that name (both shown above already use the correct name). Drop the now-unused `config: Config = Depends(get_config)` param from `job_add_listing_source`'s old signature (not present in the new one above) — confirm `get_config` is still imported/used elsewhere in the file before removing its import (it likely isn't used elsewhere in `jobs.py`; remove the import if `get_config` is now unused).

- [ ] **Step 2: Update `tests/test_routes_jobs.py`**

`grep -n "def test.*add_by_url\|def test.*add_listing_source" tests/test_routes_jobs.py` to find the existing streaming tests; delete them and add coverage matching Task 5–8's enqueue+execute pattern:

```python
def test_add_by_url_enqueues_task(client, conn):
    resp = client.post("/jobs/add-by-url", data={"url": "https://example.com/job/1"})
    assert resp.status_code == 200
    task = q.get_task(conn, resp.json()["task_id"])
    assert task["kind"] == "job_add_by_url"
    assert task["params"]["url"] == "https://example.com/job/1"


def test_add_by_url_already_tracked_job_short_circuits(conn):
    sid = q.get_or_create_manual_source(conn)
    job_id = q.insert_job(conn, source_id=sid, url="https://example.com/j", title="t", company="", raw_text="x")
    task = q.enqueue_task(conn, kind="job_add_by_url", params={"url": "https://example.com/j"})
    execute_task(conn, MagicMock(), "model", MagicMock(), task)
    fetched = q.get_task(conn, task["id"])
    assert fetched["status"] == "done"
    assert fetched["result"]["notices"][0]["level"] == "info"


def test_add_by_url_listing_page_sets_needs_action(conn):
    task = q.enqueue_task(conn, kind="job_add_by_url", params={"url": "https://example.com/jobs"})
    with patch("app.routes.jobs.fetch_url_html", return_value="<html></html>"), \
         patch("app.routes.jobs.has_enough_content", return_value=True), \
         patch("app.routes.jobs.extract_links", return_value=[]), \
         patch("app.routes.jobs.detect_listing", return_value={"is_listing": True, "job_links": ["a", "b"]}), \
         patch("app.routes.jobs.classify_known_source", return_value="generic_listing"):
        execute_task(conn, MagicMock(), "model", MagicMock(), task)
    fetched = q.get_task(conn, task["id"])
    assert fetched["result"]["needs_action"] is True
    assert q.get_unresolved_inbox_items(conn)


def test_add_listing_source_enqueues_task(client, conn):
    resp = client.post(
        "/jobs/add-listing-source",
        data={"url": "https://example.com/jobs", "name": "Example", "fetcher_type": "generic_listing"},
    )
    assert resp.status_code == 200
    task = q.get_task(conn, resp.json()["task_id"])
    assert task["kind"] == "job_add_listing_source"


def test_add_listing_source_invalid_fetcher_type_returns_400(client, conn):
    resp = client.post(
        "/jobs/add-listing-source",
        data={"url": "https://example.com/jobs", "name": "Example", "fetcher_type": "bogus"},
    )
    assert resp.status_code == 400
```

Keep any pre-existing tests in the file for these two endpoints that assert on *inserted DB rows* rather than stream text (if any) — only remove ones asserting `resp.text` contains stream markers.

- [ ] **Step 3: Run to verify pass**

Run: `pytest tests/test_routes_jobs.py -v`
Expected: PASS (full file, not just the new tests — confirms nothing from Tasks 6–7 regressed)

- [ ] **Step 4: Run full suite**

Run: `pytest -x -q`
Expected: PASS

- [ ] **Step 5: Manual smoke test**

Use `run-dev-server`. On `/jobs`, use "Add job by URL" with a real job posting URL and confirm the row appears once the task completes; then try a listing/index-page URL and confirm the "keep as source" confirm panel appears inline (still watching) — then check `/inbox` after triggering the same listing-URL flow again from a URL you then navigate away from before it completes, confirming the inbox entry appears with a working `/tasks/{id}/resume` link. Stop the dev server when done.

- [ ] **Step 6: Commit**

```bash
git add app/routes/jobs.py tests/test_routes_jobs.py
git commit -m "feat: convert job add-by-url/add-listing-source to background tasks"
```

---

### Task 10: Convert `scenarios.py` (reevaluate-all, refine-all, refine-one)

**Files:**
- Modify: `app/routes/scenarios.py`
- Test: `tests/test_routes_scenarios.py`

**Interfaces:**
- Produces: task kinds `scenarios_reevaluate_all` (`params: {}`), `scenarios_refine_all` (`params: {}`), `scenario_refine_one` (`params: {"scenario_id": int}`).

- [ ] **Step 1: Add task-kind functions, replace the three routes**

Add `from app.task_engine import register_task_kind` to imports. Replace `reevaluate_all_scenarios`, `refine_all_scenarios`, and `refine_criteria` (currently lines 86–152 and 245–275) with:

```python
@register_task_kind("scenarios_reevaluate_all")
def _task_scenarios_reevaluate_all(conn, client, model, config, params):
    scenarios = q.get_scenarios(conn)
    yield f"Re-evaluating {len(scenarios)} scenario(s)"
    total_updated = 0
    for idx, scenario in enumerate(scenarios, start=1):
        label = f"[Scenario {idx}/{len(scenarios)}: {scenario['name']}] "
        gen = run_reevaluate(conn, client, model, scenario, scenario_label=label)
        try:
            while True:
                yield next(gen)
        except StopIteration as stop:
            total_updated += stop.value

    fit_gen = run_reassess_fit(conn, client, model)
    fit_updated = 0
    try:
        while True:
            yield next(fit_gen)
    except StopIteration as stop:
        fit_updated = stop.value

    yield (
        f"All scenarios re-evaluated: {total_updated} job(s) updated across "
        f"{len(scenarios)} scenario(s); fit recomputed for {fit_updated} job(s)"
    )
    return {"notices": [], "html_chunks": []}


@register_task_kind("scenarios_refine_all")
def _task_scenarios_refine_all(conn, client, model, config, params):
    scenarios = q.get_scenarios(conn)
    yield f"Refining criteria for {len(scenarios)} scenario(s)"
    html_chunks = []
    for idx, scenario in enumerate(scenarios, start=1):
        label = f"[Scenario {idx}/{len(scenarios)}: {scenario['name']}] "
        yield label + "Requesting criteria proposals from LLM"
        existing = q.get_criteria(conn, scenario["id"])
        anchor = q.get_recent_feedback_anchor(conn, scenario["id"])
        notes = q.get_recent_feedback_notes(conn, scenario["id"])
        proposals = propose_criteria(client, model, scenario, existing, notes)
        resolved = _resolve_proposals(proposals, existing)
        yield label + f"Received {len(resolved)} proposal(s)"
        html = templates.get_template("scenarios/_proposals.html").render(
            request=None, proposals=resolved, scenario_id=scenario["id"], feedback_anchor=anchor,
        )
        html_chunks.append(f'<div id="proposals-area-{scenario["id"]}" style="margin-top:0.75rem; width:100%;">{html}</div>')
    yield f"Refined criteria proposals for {len(scenarios)} scenario(s)"
    return {"notices": [], "html_chunks": html_chunks}


@register_task_kind("scenario_refine_one")
def _task_scenario_refine_one(conn, client, model, config, params):
    scenario = q.get_scenario(conn, params["scenario_id"])
    if scenario is None:
        return {"notices": [], "html_chunks": []}
    existing = q.get_criteria(conn, scenario["id"])
    anchor = q.get_recent_feedback_anchor(conn, scenario["id"])
    notes = q.get_recent_feedback_notes(conn, scenario["id"])
    yield f"Requesting criteria proposals for '{scenario['name']}' from LLM"
    proposals = propose_criteria(client, model, scenario, existing, notes)
    yield f"Received {len(proposals)} proposal(s)"
    resolved = _resolve_proposals(proposals, existing)
    html = templates.get_template("scenarios/_proposals.html").render(
        request=None, proposals=resolved, scenario_id=scenario["id"], feedback_anchor=anchor,
    )
    return {"notices": [], "html_chunks": [html]}


@router.post("/scenarios/reevaluate")
def reevaluate_all_scenarios(conn: sqlite3.Connection = Depends(get_db)):
    task = q.enqueue_task(conn, kind="scenarios_reevaluate_all", params={})
    return {"task_id": task["id"]}


@router.post("/scenarios/refine")
def refine_all_scenarios(conn: sqlite3.Connection = Depends(get_db)):
    task = q.enqueue_task(conn, kind="scenarios_refine_all", params={})
    return {"task_id": task["id"]}


@router.post("/scenarios/{scenario_id}/refine")
def refine_criteria(scenario_id: int, conn: sqlite3.Connection = Depends(get_db)):
    _get_scenario_or_404(conn, scenario_id)
    task = q.enqueue_task(conn, kind="scenario_refine_one", params={"scenario_id": scenario_id})
    return {"task_id": task["id"]}
```

Drop the now-unused `client`/`model` `Depends(...)` params from these three route signatures, and the `request: Request` param from `refine_all_scenarios`/`refine_criteria` (no longer used at enqueue time — `request=None` is baked into the kind function's own template renders instead). Confirm `logger`/`logging` are still used elsewhere in the file before removing that import (the module-level `logger.info` calls inside the old `refine_criteria` stream body are dropped since progress lines no longer need separate `logger.info` calls — the `_progress`-style double-logging was specific to that one route; check whether `logging`/`logger` become unused and remove the import if so).

- [ ] **Step 2: Update `tests/test_routes_scenarios.py`**

`grep -n "def test" tests/test_routes_scenarios.py | grep -iE "reevaluate|refine"` to find the streaming-based tests for these three endpoints; delete them and add:

```python
from app.task_engine import execute_task
from unittest.mock import MagicMock, patch


def test_scenarios_reevaluate_enqueues_task(client, conn):
    resp = client.post("/scenarios/reevaluate")
    assert resp.status_code == 200
    task = q.get_task(conn, resp.json()["task_id"])
    assert task["kind"] == "scenarios_reevaluate_all"


def test_scenarios_refine_enqueues_task(client, conn):
    resp = client.post("/scenarios/refine")
    assert resp.status_code == 200
    assert q.get_task(conn, resp.json()["task_id"])["kind"] == "scenarios_refine_all"


def test_scenario_refine_one_enqueues_task(client, conn):
    sid = q.insert_scenario(conn, "Backend", "desc")
    resp = client.post(f"/scenarios/{sid}/refine")
    assert resp.status_code == 200
    task = q.get_task(conn, resp.json()["task_id"])
    assert task["kind"] == "scenario_refine_one"
    assert task["params"]["scenario_id"] == sid


def test_scenario_refine_one_unknown_scenario_404s(client, conn):
    resp = client.post("/scenarios/999/refine")
    assert resp.status_code == 404


def test_scenario_refine_one_task_execution_renders_proposals(conn):
    sid = q.insert_scenario(conn, "Backend", "desc")
    task = q.enqueue_task(conn, kind="scenario_refine_one", params={"scenario_id": sid})
    with patch("app.routes.scenarios.propose_criteria", return_value=[]):
        execute_task(conn, MagicMock(), "model", MagicMock(), task)
    fetched = q.get_task(conn, task["id"])
    assert fetched["status"] == "done"
    assert len(fetched["result"]["html_chunks"]) == 1
```

- [ ] **Step 3: Run to verify pass**

Run: `pytest tests/test_routes_scenarios.py -v`
Expected: PASS

- [ ] **Step 4: Run full suite**

Run: `pytest -x -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/routes/scenarios.py tests/test_routes_scenarios.py
git commit -m "feat: convert scenario reevaluate/refine endpoints to background tasks"
```

---

### Task 11: Convert `profile.py` (refine)

**Files:**
- Modify: `app/routes/profile.py`
- Test: `tests/test_routes_profile.py`

**Interfaces:**
- Produces: task kind `profile_refine` (`params: {}`).

- [ ] **Step 1: Add task-kind function, replace the route**

Add `from app.task_engine import register_task_kind` to imports. Replace `refine_profile` (currently lines 39–61) with:

```python
@register_task_kind("profile_refine")
def _task_profile_refine(conn, client, model, config, params):
    profile_text = q.get_profile(conn)
    notes = q.get_unhandled_profile_notes(conn)
    yield "Requesting profile improvement suggestions from LLM"
    proposals = propose_profile_changes(client, model, profile_text, notes)
    resolved = resolve_proposals(proposals, profile_text)
    yield f"Received {len(resolved)} proposal(s)"
    html = templates.get_template("profile/_proposals.html").render(
        request=None, grouped=group_proposals_by_section(resolved), job_ids=[n["id"] for n in notes],
    )
    return {"notices": [], "html_chunks": [html]}


@router.post("/profile/refine")
def refine_profile(conn: sqlite3.Connection = Depends(get_db)):
    task = q.enqueue_task(conn, kind="profile_refine", params={})
    return {"task_id": task["id"]}
```

Drop the now-unused `request: Request`, `client`/`model` `Depends(...)` params from the route signature.

- [ ] **Step 2: Update `tests/test_routes_profile.py`**

`grep -n "def test.*refine" tests/test_routes_profile.py` to find the streaming-based test(s) for `POST /profile/refine`; delete and add:

```python
from app.task_engine import execute_task
from unittest.mock import MagicMock, patch


def test_profile_refine_enqueues_task(client, conn):
    resp = client.post("/profile/refine")
    assert resp.status_code == 200
    task = q.get_task(conn, resp.json()["task_id"])
    assert task["kind"] == "profile_refine"


def test_profile_refine_task_execution_renders_proposals(conn):
    q.upsert_profile(conn, "Some profile text")
    task = q.enqueue_task(conn, kind="profile_refine", params={})
    with patch("app.routes.profile.propose_profile_changes", return_value=[]):
        execute_task(conn, MagicMock(), "model", MagicMock(), task)
    fetched = q.get_task(conn, task["id"])
    assert fetched["status"] == "done"
    assert len(fetched["result"]["html_chunks"]) == 1
```

Add `from app.db import queries as q` to the test file's imports if not already present (check the top of the file first).

- [ ] **Step 3: Run to verify pass**

Run: `pytest tests/test_routes_profile.py -v`
Expected: PASS

- [ ] **Step 4: Run full suite**

Run: `pytest -x -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/routes/profile.py tests/test_routes_profile.py
git commit -m "feat: convert profile refine endpoint to background task"
```

---

### Task 12: Final sweep — dead code, manual verification, full regression pass

**Files:**
- Modify: `app/routes/fetch.py`, `app/routes/jobs.py`, `app/routes/scenarios.py`, `app/routes/sources.py`, `app/routes/profile.py` (import cleanup only)
- No new tests — this task verifies and tidies, it doesn't add behavior.

- [ ] **Step 1: Remove now-dead imports across converted route files**

Every converted file previously imported `StreamingResponse` from `fastapi.responses` — grep to confirm none of the five files use it anymore, then drop it from each file's import line:

Run: `grep -rn "StreamingResponse" app/routes/`
Expected: no matches (all five files' routes now return plain dicts, not `StreamingResponse`)

If any match remains, that route wasn't actually converted — stop and go back to the relevant task rather than deleting the import.

Also grep for now-unused `get_ai_client`/`get_model`/`get_config` imports per file (each converted route dropped these `Depends(...)` uses) and drop any import that's no longer referenced anywhere in that file:

Run: `grep -n "get_ai_client\|get_model\|get_config" app/routes/fetch.py app/routes/jobs.py app/routes/scenarios.py app/routes/sources.py app/routes/profile.py`

Remove any import whose name doesn't appear in a `Depends(...)` call anymore in that file.

- [ ] **Step 2: Run full suite with coverage of the new modules**

Run: `pytest -q`
Expected: PASS, zero failures, zero errors, no import warnings

- [ ] **Step 3: Manual end-to-end smoke test**

Use the `run-dev-server` skill against a throwaway DB copy. Exercise, in order:
1. `/fetch` → "Fetch all" — confirm tray shows progress, page table refreshes on completion.
2. `/jobs` → bulk-select two jobs → "Re-evaluate selected" — confirm oob row swaps + counts update.
3. `/jobs` → "Add job by URL" with a listing-page URL → confirm inline confirm panel, then check `/inbox` shows nothing once you complete the flow (resolved implicitly since the inbox item's underlying decision was already made from the visible panel — if it still shows, that's expected per this task's design: inbox items aren't auto-resolved by taking the action on the *live* panel, only by `POST /inbox/{id}/resolve`; confirm this matches the design in the spec at `docs/superpowers/specs/2026-08-21-async-task-architecture-design.md` and isn't a regression before treating it as a bug).
4. `/scenarios` → "Get suggestions for all scenarios" — confirm per-scenario proposal panels appear.
5. `/profile` → "Suggest improvements" — confirm proposal panel appears.
6. Restart the dev server mid-fetch (Ctrl-C while a fetch-all is running, then restart) — confirm `/tasks/active` no longer lists the interrupted task and re-triggering the same fetch works normally (validates `recover_interrupted_tasks`).

Stop the dev server once done. Report results before proceeding — if any step reveals a real regression, fix it as a follow-up commit in this same task, not a new task.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore: drop dead StreamingResponse/dependency imports from converted routes"
```

(Skip this commit if Step 1 found nothing to remove.)
