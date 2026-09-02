# Task Stop Action Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user stop a queued or running task (and its whole subtree) from the task detail page, landing it in a new terminal `cancelled` status.

**Architecture:** Queued tasks are cancelled with a direct SQL `UPDATE` (the worker's `claim_next_task` only sees `queued`, so it never picks them up). The single running task is stopped cooperatively: a `POST /tasks/{id}/stop` route sets an in-memory cancel flag in `task_engine`; the worker's `execute_task` generator loop checks it between yielded lines and finalizes the task as `cancelled`. Stopping any task in a subtree stops the whole subtree.

**Tech Stack:** Python 3.14, FastAPI, SQLite (stdlib `sqlite3`), Jinja2 templates, pytest. Run tests with `python -m pytest` (never `uv run` — read-only cache in this environment).

## Global Constraints

- Personal single-instance app: use a hard-downtime table-rebuild migration, no dual-schema / backwards-compat branching.
- `tasks` has no FTS triggers — no `INSERT INTO ..._fts VALUES('rebuild')` fixup needed.
- New status string is exactly `cancelled` everywhere.
- UI surface for the Stop control is the **task detail page only** — no status-bar / home-card Stop button.
- Match existing code style: terse comments, no docstrings on trivial helpers, follow the patterns in the files you touch.
- Commit after every task (each task = one commit).

---

### Task 1: `cancelled` status — schema migration + `q.cancel_task` + status icon

**Files:**
- Modify: `app/db/schema.py:103` (main `tasks` DDL CHECK), and add migration `_migrate_tasks_add_cancelled` + register it in `init_db`
- Modify: `app/db/queries.py` (add `cancel_task`, near `dismiss_task` ~line 943)
- Modify: `app/template_env.py:35-42` (`_STATUS_ICONS`)
- Test: `tests/test_schema.py`, `tests/test_task_engine.py`

**Interfaces:**
- Produces:
  - status value `'cancelled'` accepted by the `tasks.status` CHECK constraint
  - `q.cancel_task(conn: sqlite3.Connection, task_id: int) -> None` — sets `status='cancelled'`, `finished_at=datetime('now')` (COALESCE-preserving like `dismiss_task`), leaves `log` / `error` untouched
  - `_STATUS_ICONS['cancelled'] == "⊘"`

- [ ] **Step 1: Write the failing schema test**

In `tests/test_schema.py`, add next to `test_migrate_group_id_to_parent_task_id_hard_cutover`:

```python
def test_migrate_tasks_add_cancelled_status(conn):
    # DB left on the pre-cancelled shape.
    conn.executescript(
        """
        CREATE TABLE tasks (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, params TEXT NOT NULL DEFAULT '{}',
            parent_task_id INTEGER REFERENCES tasks(id),
            status TEXT NOT NULL DEFAULT 'queued'
                CHECK(status IN ('queued','running','needs_action','done','failed','dismissed')),
            log TEXT NOT NULL DEFAULT '', result TEXT, error TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')), started_at TEXT, finished_at TEXT);
        INSERT INTO tasks (id, kind, status) VALUES (1, 'fetch_source', 'done');
        INSERT INTO tasks (id, kind, status) VALUES (2, 'fetch_source', 'queued');
        """
    )
    conn.commit()
    init_db(conn)
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='tasks'"
    ).fetchone()[0]
    assert "'cancelled'" in sql
    assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 2
    conn.execute("UPDATE tasks SET status='cancelled' WHERE id=2")  # must not raise
```

Also extend `test_tasks_table_has_parent_pointer_and_wide_status`: add `"cancelled"` to the tuple `("queued", "running", "needs_action", "done", "failed", "dismissed")`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_schema.py::test_migrate_tasks_add_cancelled_status tests/test_schema.py::test_tasks_table_has_parent_pointer_and_wide_status -v`
Expected: FAIL — `'cancelled'` not in the CHECK SQL.

- [ ] **Step 3: Update the main DDL**

`app/db/schema.py:103`, change:

```python
        CHECK(status IN ('queued', 'running', 'needs_action', 'done', 'failed', 'dismissed')),
```

to:

```python
        CHECK(status IN ('queued', 'running', 'needs_action', 'done', 'failed', 'dismissed', 'cancelled')),
```

- [ ] **Step 4: Add the migration**

In `app/db/schema.py`, after `_migrate_tasks_group_to_parent` (ends ~line 705, just before `_migrate_add_jobs_fts`):

```python
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
```

Then in `init_db`, add the call right after `_migrate_tasks_group_to_parent(conn)` and before `_migrate_add_jobs_fts(conn)`:

```python
    _migrate_tasks_group_to_parent(conn)
    _migrate_tasks_add_cancelled(conn)
    _migrate_add_jobs_fts(conn)
```

- [ ] **Step 5: Add `q.cancel_task`**

In `app/db/queries.py`, immediately after `dismiss_task` (~line 950):

```python
def cancel_task(conn: sqlite3.Connection, task_id: int) -> None:
    conn.execute(
        "UPDATE tasks SET status = 'cancelled', "
        "finished_at = COALESCE(finished_at, datetime('now')) WHERE id = ?",
        (task_id,),
    )
    conn.commit()
```

- [ ] **Step 6: Add the status icon**

`app/template_env.py`, in `_STATUS_ICONS` add after `"dismissed": "–",`:

```python
    "cancelled": "⊘",
```

- [ ] **Step 7: Write the `cancel_task` test**

In `tests/test_task_engine.py`, after the imports-based tests near the top:

```python
def test_cancel_task_sets_cancelled_and_finished(conn):
    t = q.enqueue_task(conn, kind="fetch_all", params={})
    q.cancel_task(conn, t["id"])
    got = q.get_task(conn, t["id"])
    assert got["status"] == "cancelled"
    assert got["finished_at"] is not None
```

- [ ] **Step 8: Run the full affected tests**

Run: `python -m pytest tests/test_schema.py tests/test_task_engine.py -q`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add app/db/schema.py app/db/queries.py app/template_env.py tests/test_schema.py tests/test_task_engine.py
git commit -m "feat: add cancelled task status + migration + cancel_task query"
```

---

### Task 2: Cooperative cancellation in `task_engine`

**Files:**
- Modify: `app/task_engine.py` (module-level cancel registry, `_TaskCancelled`, `execute_task` loop + except branch + `finally`)
- Test: `tests/test_task_engine.py`

**Interfaces:**
- Consumes: `q.cancel_task` (Task 1)
- Produces:
  - `task_engine.request_cancel(task_id: int) -> None`
  - `task_engine.cancel_requested(task_id: int) -> bool`
  - `task_engine._clear_cancel(task_id: int) -> None`
  - `execute_task` finalizes a task as `cancelled` (via `q.cancel_task`) when `cancel_requested(task["id"])` is true at a yield boundary; partial `log` retained, `error` stays NULL

- [ ] **Step 1: Write the failing test**

In `tests/test_task_engine.py`:

```python
def test_execute_task_stops_when_cancel_requested(conn):
    @te.register_task_kind("test_execute_cancel")
    def fn(conn, client, model, config, params):
        for i in range(10):
            yield f"step {i}"
        return {"html_chunks": []}

    task = q.enqueue_task(conn, kind="test_execute_cancel", params={})
    q.claim_next_task(conn)  # mark running, like the worker would

    real_append = q.append_task_log

    def append_and_maybe_cancel(c, tid, line):
        real_append(c, tid, line)
        if line == "step 0":
            te.request_cancel(tid)

    with patch("app.task_engine.q.append_task_log", side_effect=append_and_maybe_cancel):
        te.execute_task(conn, None, None, None, q.get_task(conn, task["id"]))

    got = q.get_task(conn, task["id"])
    assert got["status"] == "cancelled"
    assert got["error"] is None
    assert "step 0" in got["log"]
    assert "step 9" not in got["log"]
    assert not te.cancel_requested(task["id"])  # cleared in finally
    del te.TASK_KINDS["test_execute_cancel"]


def test_execute_task_honors_pre_run_cancel_flag(conn):
    @te.register_task_kind("test_execute_cancel_pre")
    def fn(conn, client, model, config, params):
        yield "only step"
        return {}

    task = q.enqueue_task(conn, kind="test_execute_cancel_pre", params={})
    te.request_cancel(task["id"])  # request lands before the worker starts the run
    te.execute_task(conn, None, None, None, q.get_task(conn, task["id"]))
    assert q.get_task(conn, task["id"])["status"] == "cancelled"
    assert not te.cancel_requested(task["id"])  # finally clears it
    del te.TASK_KINDS["test_execute_cancel_pre"]


def test_execute_task_clears_cancel_flag_on_normal_completion(conn):
    @te.register_task_kind("test_execute_normal_clear")
    def fn(conn, client, model, config, params):
        yield "step"
        return {}

    task = q.enqueue_task(conn, kind="test_execute_normal_clear", params={})
    te.execute_task(conn, None, None, None, task)
    assert q.get_task(conn, task["id"])["status"] == "done"
    assert not te.cancel_requested(task["id"])
    del te.TASK_KINDS["test_execute_normal_clear"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_task_engine.py::test_execute_task_stops_when_cancel_requested -v`
Expected: FAIL — `AttributeError: module 'app.task_engine' has no attribute 'request_cancel'`.

- [ ] **Step 3: Add the cancel registry + exception**

`app/task_engine.py`, after the `logger = ...` line and `TASK_KINDS` block:

```python
class _TaskCancelled(Exception):
    """Raised inside execute_task's loop when a stop was requested for the
    running task."""


_cancel_lock = threading.Lock()
_cancel_requested: set[int] = set()


def request_cancel(task_id: int) -> None:
    with _cancel_lock:
        _cancel_requested.add(task_id)


def cancel_requested(task_id: int) -> bool:
    with _cancel_lock:
        return task_id in _cancel_requested


def _clear_cancel(task_id: int) -> None:
    with _cancel_lock:
        _cancel_requested.discard(task_id)
```

- [ ] **Step 4: Wire it into `execute_task`**

In `execute_task`, the current inner loop is:

```python
    try:
        gen = kind_fn(conn, client, model, config, call_params)
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
    finally:
        _sync_browser_missing_inbox(conn)
```

Replace with:

```python
    try:
        gen = kind_fn(conn, client, model, config, call_params)
        result: dict = {}
        try:
            if cancel_requested(task["id"]):
                raise _TaskCancelled
            while True:
                line = next(gen)
                q.append_task_log(conn, task["id"], line)
                if cancel_requested(task["id"]):
                    raise _TaskCancelled
        except StopIteration as stop:
            result = stop.value or {}
    except _TaskCancelled:
        logger.info("Task %s (%s) cancelled by user", task["id"], task["kind"])
        q.cancel_task(conn, task["id"])
        return
    except Exception as exc:
        logger.exception("Task %s (%s) failed", task["id"], task["kind"])
        q.fail_task(conn, task["id"], str(exc))
        return
    finally:
        _clear_cancel(task["id"])
        _sync_browser_missing_inbox(conn)
```

(The `return` inside the `except _TaskCancelled` still runs the `finally`, which clears the flag — good.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_task_engine.py -q`
Expected: PASS (all, including the pre-existing ones).

- [ ] **Step 6: Commit**

```bash
git add app/task_engine.py tests/test_task_engine.py
git commit -m "feat: cooperative task cancellation in task_engine"
```

---

### Task 3: Subtree stop query + route + presentation

**Files:**
- Modify: `app/db/queries.py` (add `cancel_queued_tasks`; add `'cancelled'` to `get_dashboard_tasks` recent-window `status IN`)
- Modify: `app/routes/tasks.py` (`_next_step`, `_subtree_status`, `root_presentation` cancelled branch, new `POST /tasks/{task_id}/stop`)
- Test: `tests/test_routes_tasks.py`

**Interfaces:**
- Consumes: `q.cancel_task` (Task 1), `task_engine.request_cancel` (Task 2), `q.get_task`, `q.get_task_children`
- Produces:
  - `q.cancel_queued_tasks(conn, task_ids: list[int]) -> int` — sets every id currently `queued` to `cancelled` + `finished_at`; returns rowcount
  - `POST /tasks/{task_id}/stop` — 404 if task missing; stops the whole subtree of the resolved root; 303 redirect to referer-or-`/`
  - `_subtree_status` returns `"cancelled"` when the subtree has a cancelled task and nothing queued/running/needs_action and no later failure
  - `_next_step` returns `"Cancelled"` for a `cancelled` task

- [ ] **Step 1: Write the failing query test**

In `tests/test_routes_tasks.py` (it already imports `q`):

```python
def test_cancel_queued_tasks_only_touches_queued(conn):
    a = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1})
    b = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 2})
    q.claim_next_task(conn)  # a -> running
    n = q.cancel_queued_tasks(conn, [a["id"], b["id"]])
    assert n == 1
    assert q.get_task(conn, a["id"])["status"] == "running"
    assert q.get_task(conn, b["id"])["status"] == "cancelled"
    assert q.get_task(conn, b["id"])["finished_at"] is not None
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_routes_tasks.py::test_cancel_queued_tasks_only_touches_queued -v`
Expected: FAIL — `AttributeError: ... has no attribute 'cancel_queued_tasks'`.

- [ ] **Step 3: Add `q.cancel_queued_tasks`**

`app/db/queries.py`, after `cancel_task`:

```python
def cancel_queued_tasks(conn: sqlite3.Connection, task_ids: list[int]) -> int:
    if not task_ids:
        return 0
    placeholders = ",".join("?" * len(task_ids))
    cur = conn.execute(
        f"UPDATE tasks SET status = 'cancelled', finished_at = datetime('now') "
        f"WHERE status = 'queued' AND id IN ({placeholders})",
        task_ids,
    )
    conn.commit()
    return cur.rowcount
```

Also in `get_dashboard_tasks`, change:

```python
           OR (status IN ('done', 'failed', 'dismissed')
```

to:

```python
           OR (status IN ('done', 'failed', 'dismissed', 'cancelled')
```

- [ ] **Step 4: Write the failing presentation + route tests**

In `tests/test_routes_tasks.py`:

```python
from app.routes.tasks import task_presentation, root_presentation, _subtree_status, _next_step


def test_next_step_cancelled(conn):
    t = q.enqueue_task(conn, kind="fetch_all", params={})
    q.cancel_task(conn, t["id"])
    assert _next_step(conn, q.get_task(conn, t["id"])) == "Cancelled"


def test_subtree_status_cancelled(conn):
    root = q.enqueue_task(conn, kind="fetch_all", params={})
    q.complete_task(conn, root["id"], {})
    child = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1},
                           parent_task_id=root["id"])
    q.cancel_task(conn, child["id"])
    subtree = [q.get_task(conn, root["id"]), q.get_task(conn, child["id"])]
    assert _subtree_status(subtree) == "cancelled"


def test_subtree_status_failed_beats_cancelled_when_later(conn):
    root = q.enqueue_task(conn, kind="fetch_all", params={})
    q.complete_task(conn, root["id"], {})
    c1 = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1}, parent_task_id=root["id"])
    q.cancel_task(conn, c1["id"])
    c2 = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 2}, parent_task_id=root["id"])
    q.fail_task(conn, c2["id"], "boom")
    subtree = [q.get_task(conn, root["id"]), q.get_task(conn, c1["id"]), q.get_task(conn, c2["id"])]
    assert _subtree_status(subtree) == "failed"


def test_stop_cancels_queued_task(client, conn):
    t = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1})
    r = client.post(f"/tasks/{t['id']}/stop", follow_redirects=False)
    assert r.status_code == 303
    assert q.get_task(conn, t["id"])["status"] == "cancelled"
    assert q.claim_next_task(conn) is None  # worker won't pick it up


def test_stop_on_root_cancels_subtree_and_signals_running(client, conn):
    root = q.enqueue_task(conn, kind="fetch_all", params={})
    q.complete_task(conn, root["id"], {})
    running = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1}, parent_task_id=root["id"])
    queued = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 2}, parent_task_id=root["id"])
    q.claim_next_task(conn)  # running -> running

    from app import task_engine as te
    r = client.post(f"/tasks/{root['id']}/stop", follow_redirects=False)
    assert r.status_code == 303
    assert q.get_task(conn, queued["id"])["status"] == "cancelled"
    assert te.cancel_requested(running["id"])
    assert q.get_task(conn, root["id"])["status"] == "done"  # fetch_all root already done
    te._clear_cancel(running["id"])  # tidy up shared module state


def test_stop_on_child_stops_whole_subtree(client, conn):
    root = q.enqueue_task(conn, kind="fetch_all", params={})
    q.complete_task(conn, root["id"], {})
    queued = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 2}, parent_task_id=root["id"])
    r = client.post(f"/tasks/{queued['id']}/stop", follow_redirects=False)
    assert r.status_code == 303
    assert q.get_task(conn, queued["id"])["status"] == "cancelled"


def test_stop_on_terminal_task_is_noop(client, conn):
    t = q.enqueue_task(conn, kind="fetch_all", params={})
    q.complete_task(conn, t["id"], {})
    r = client.post(f"/tasks/{t['id']}/stop", follow_redirects=False)
    assert r.status_code == 303
    assert q.get_task(conn, t["id"])["status"] == "done"


def test_stop_missing_task_404(client, conn):
    r = client.post("/tasks/999/stop", follow_redirects=False)
    assert r.status_code == 404
```

- [ ] **Step 5: Run to verify they fail**

Run: `python -m pytest tests/test_routes_tasks.py -q -k "cancel or stop or subtree or next_step"`
Expected: FAIL — `_subtree_status` / `_next_step` import errors or missing route (404 vs expected behavior for the noop/subtree cases; import error first).

- [ ] **Step 6: Update `_next_step`**

`app/routes/tasks.py`, in `_next_step`, add before `return _done_summary(conn, task)`:

```python
    if status == "cancelled":
        return "Cancelled"
```

- [ ] **Step 7: Update `_subtree_status`**

Replace the body of `_subtree_status` with (adds the `cancelled` rule between the failed check and the `done` fallback):

```python
def _subtree_status(subtree: list[dict]) -> str:
    """Derived state of a root task from its whole subtree (root + children):
    1) anything still queued/running -> running
    2) else anything needs_action  -> needs you
    3) else the latest-finished task failed -> failed
    4) else anything cancelled -> cancelled
    5) else -> done
    """
    if any(t["status"] in ("queued", "running") for t in subtree):
        return "running"
    if any(t["status"] == "needs_action" for t in subtree):
        return "needs_action"
    finished = [t for t in subtree if t["finished_at"]]
    if finished:
        latest = max(finished, key=lambda t: (t["finished_at"], t["id"]))
        if latest["status"] == "failed":
            return "failed"
    if any(t["status"] == "cancelled" for t in subtree):
        return "cancelled"
    return "done"
```

- [ ] **Step 8: Update `root_presentation` next-step for cancelled**

In `root_presentation`, the status branches currently go `needs_action` / `failed` / `fetch_all` / `running` / `else`. Add a `cancelled` branch before `elif kind == "fetch_all":`:

```python
    if status == "needs_action":
        next_step = _next_step(conn, na)
    elif status == "failed":
        failed = [t for t in subtree if t["status"] == "failed"]
        next_step = (failed[-1]["error"] or "Failed").strip().split("\n")[0]
    elif status == "cancelled":
        next_step = "Cancelled"
    elif kind == "fetch_all":
```

- [ ] **Step 9: Add the stop route**

`app/routes/tasks.py`, after `task_dismiss` (end of file). Add `from app import task_engine` to the imports at the top (there's currently no such import — add it next to `from app.db import queries as q`):

```python
@router.post("/tasks/{task_id}/stop")
def task_stop(task_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    task = q.get_task(conn, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    root_id = task["parent_task_id"] or task["id"]
    root = q.get_task(conn, root_id)
    subtree = [root, *q.get_task_children(conn, root_id)] if root else [task]
    q.cancel_queued_tasks(conn, [t["id"] for t in subtree if t["status"] == "queued"])
    for t in subtree:
        if t["status"] == "running":
            task_engine.request_cancel(t["id"])
    ref = request.headers.get("referer") or "/"
    dest = ref if urlparse(ref).netloc == urlparse(str(request.url)).netloc else "/"
    return RedirectResponse(dest, status_code=303)
```

- [ ] **Step 10: Run the affected tests**

Run: `python -m pytest tests/test_routes_tasks.py -q`
Expected: PASS.

- [ ] **Step 11: Commit**

```bash
git add app/db/queries.py app/routes/tasks.py tests/test_routes_tasks.py
git commit -m "feat: POST /tasks/{id}/stop cancels the task subtree"
```

---

### Task 4: Detail-page Stop button + cancelled rendering

**Files:**
- Modify: `app/templates/tasks/detail.html` (Stop actions block, poll JS terminal list, `resolved_panel` gate)
- Modify: `app/templates/tasks/_resolved_panel.html` (cancelled branch)
- Modify: `app/templates/tasks/list.html` (filter-bar tab)
- Test: `tests/test_routes_tasks.py`

**Interfaces:**
- Consumes: `POST /tasks/{id}/stop` (Task 3), `_next_step` "Cancelled", `_subtree_status`
- Produces: detail page shows a Stop `<form>` posting to `/tasks/{{ task.id }}/stop` when the viewed task is `queued`/`running`; `cancelled` tasks render a "⊘ Cancelled" resolved panel

- [ ] **Step 1: Write the failing template tests**

In `tests/test_routes_tasks.py`:

```python
def test_detail_shows_stop_button_for_running(client, conn):
    t = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1})
    q.claim_next_task(conn)
    html = client.get(f"/tasks/{t['id']}").text
    assert f'action="/tasks/{t["id"]}/stop"' in html
    assert ">Stop<" in html


def test_detail_shows_stop_button_for_queued(client, conn):
    t = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1})
    html = client.get(f"/tasks/{t['id']}").text
    assert f'action="/tasks/{t["id"]}/stop"' in html


def test_detail_no_stop_button_for_done(client, conn):
    t = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1})
    q.complete_task(conn, t["id"], {})
    html = client.get(f"/tasks/{t['id']}").text
    assert "/stop" not in html


def test_detail_cancelled_task_says_cancelled(client, conn):
    t = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1})
    q.cancel_task(conn, t["id"])
    html = client.get(f"/tasks/{t['id']}").text
    assert "Cancelled" in html
    assert "/stop" not in html
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_routes_tasks.py -q -k "stop_button or cancelled_task_says"`
Expected: FAIL — no `/stop` markup, no "Cancelled" resolved panel.

- [ ] **Step 3: Add the Stop actions block to `detail.html`**

In `app/templates/tasks/detail.html`, right after the closing `</dl>` of `<dl class="task-meta">` (before the `{% if resume_html %}` block):

```html
{% set _stoppable = (pres.status if is_group else task.status) in ("queued", "running") %}
{% if _stoppable %}
<form method="post" action="/tasks/{{ task.id }}/stop" style="margin:0.75rem 0;">
  <button type="submit" class="btn">Stop</button>
</form>
{% endif %}
```

- [ ] **Step 4: Add `cancelled` to the poll-reload list + resolved_panel gate**

In `detail.html`, the non-group poll JS line:

```javascript
      if (["done", "failed", "needs_action", "dismissed"].indexOf(t.status) !== -1) location.reload();
```

becomes:

```javascript
      if (["done", "failed", "needs_action", "dismissed", "cancelled"].indexOf(t.status) !== -1) location.reload();
```

And the non-group `resolved_panel` assignment in `app/routes/tasks.py` `task_detail`:

```python
    resolved_panel = task["status"] in ("done", "dismissed") and _was_needs_action(task)
```

Change to:

```python
    resolved_panel = task["status"] in ("done", "dismissed", "cancelled")
```

Wait — the `_was_needs_action` guard means a plain `done` fetch with no needs_action currently shows no panel. Keep that behavior for done/dismissed but always show for cancelled:

```python
    resolved_panel = (
        task["status"] == "cancelled"
        or (task["status"] in ("done", "dismissed") and _was_needs_action(task))
    )
```

- [ ] **Step 5: Add the cancelled branch to `_resolved_panel.html`**

`app/templates/tasks/_resolved_panel.html`, change the `{% if task.status == "dismissed" %}` chain to include cancelled:

```html
<div class="resolved-panel">
  {% if task.status == "dismissed" %}
    <p>✓ Dismissed <span class="task-age">{{ task.finished_at | age }}</span></p>
  {% elif task.status == "cancelled" %}
    <p>⊘ Cancelled <span class="task-age">{{ task.finished_at | age }}</span></p>
  {% else %}
    <p>✓ {{ pres.next_step }}</p>
    {% if pres.results %}
    <p>{% for r in pres.results %}<a href="{{ r.href }}">{{ r.label }}</a>{% if not loop.last %} · {% endif %}{% endfor %}</p>
    {% endif %}
  {% endif %}
</div>
```

- [ ] **Step 6: Add the `cancelled` filter tab to `list.html`**

`app/templates/tasks/list.html:7`, change:

```html
    {% for s in ["all", "active", "done", "failed"] %}
```

to:

```html
    {% for s in ["all", "active", "done", "failed", "cancelled"] %}
```

- [ ] **Step 7: Run affected tests**

Run: `python -m pytest tests/test_routes_tasks.py -q`
Expected: PASS.

- [ ] **Step 8: Full suite**

Run: `python -m pytest -q`
Expected: PASS (all green).

- [ ] **Step 9: Commit**

```bash
git add app/templates/tasks/detail.html app/templates/tasks/_resolved_panel.html app/templates/tasks/list.html app/routes/tasks.py tests/test_routes_tasks.py
git commit -m "feat: Stop button on task detail page + cancelled rendering"
```

---

### Task 5: Manual smoke test + spec-parity check

**Files:** none (verification only)

- [ ] **Step 1: Start the dev server against a throwaway DB**

Use the `run-dev-server` skill recipe. Confirm it binds a fresh port (a curl 200 alone doesn't prove it's your server — check the startup log line).

- [ ] **Step 2: Exercise the flow**

- Enqueue a `fetch_all` (POST `/fetch/all` or the UI button) with at least 2 enabled sources.
- Open `/tasks/{root_id}` — confirm the Stop button shows while children run.
- Click Stop. Confirm: queued children flip to `cancelled`, the running child stops at its next progress line and lands `cancelled`, the page reloads and shows "⊘ Cancelled", the Stop button is gone.
- Open `/tasks?status=cancelled` — the run's children are listed.
- Enqueue a single `fetch_source`, Stop it while `queued` — confirm immediate `cancelled`, worker never runs it.

- [ ] **Step 3: Stop the dev server.** Leave it running only if handing off to the user for UI review (see below).

- [ ] **Step 4: Spec-parity check**

Re-read `docs/superpowers/specs/2026-09-01-task-stop-action-design.md` and confirm every section maps to shipped code. Note any intentional deviations in the final report.

---

## Self-Review Notes

- **Spec §1 (migration + mechanism):** Task 1 (migration, `cancel_task`, icon) + Task 2 (in-memory signal, `execute_task` loop). The `_clear_cancel` hygiene note from the spec is implemented in the `finally`.
- **Spec §2 (subtree):** Task 3 — `cancel_queued_tasks` + route loop + `_subtree_status` rule + `root_presentation` branch. Order (running → needs_action → failed → cancelled → done) matches the spec exactly.
- **Spec §3 (route):** Task 3 Step 9. 404 / no-op / referer-redirect all covered.
- **Spec §4 (presentation helpers):** `_next_step` (Task 3 Step 6), `_subtree_status` (Step 7), `root_presentation` (Step 8), `_root_summary` inherits via `root_presentation`.
- **Spec §5 (UI detail page):** Task 4 — Stop block keyed on `task.id`, poll-list `cancelled`, `_resolved_panel` branch + gate.
- **Spec §6 (small bits):** `_STATUS_ICONS` (Task 1), `list.html` tab (Task 4). CSS rule dropped — `.task-li-icon` already defaults to `var(--text-muted)` and `dismissed` has no rule either, so `cancelled` matches `dismissed` with no new CSS. `get_dashboard_tasks` gains `'cancelled'` in the 24h window (Task 3) so a just-cancelled run doesn't vanish from the Start page — consistent with `dismissed`.
- **Spec §7 (tests):** distributed across Tasks 1–4; full suite gate in Task 4 Step 8; manual smoke in Task 5.
- **Type consistency:** query name is `cancel_queued_tasks` everywhere (spec draft said `cancel_queued_in_subtree` — renamed for accuracy since it filters by `status='queued'`, not by subtree membership; the route supplies the subtree ids). `request_cancel` / `cancel_requested` / `_clear_cancel` consistent between Tasks 2 and 3.
