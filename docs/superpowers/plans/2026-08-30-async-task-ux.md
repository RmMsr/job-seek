# Async task UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every async task a durable, followable representation — a lifecycle-stated card on the Start page, a summary+results detail page, a history list — and make action triggers and action-spawned forms show honest pending/decided states.

**Architecture:** Extend the existing `tasks` table with a `group_id` and a `needs_action` / `dismissed` status instead of the parallel `inbox_items` follow-up mirror. A single `task_presentation(conn, task)` helper derives every user-facing string (title / goal / next-step / result links) from `kind` + `params` + `status` + log + `result`. The Start page renders task cards (grouped by `group_id`) from a new `get_dashboard_tasks` query. The status-bar polling and page-blend-in behaviour are preserved; only the status vocabulary and a few endpoint paths change.

**Tech Stack:** Python 3, FastAPI, Jinja2 templates, htmx 1.9, vanilla JS in `base.html`, SQLite (WAL), pytest + `fastapi.testclient`.

## Global Constraints

- Execute Python via `python -m pytest …` from the worktree root (the sandbox's `uv run` fails on a read-only cache). The dev server needs `run_in_background` + `dangerouslyDisableSandbox`.
- Migrations: this is a personal single-instance app. Prefer one hard-downtime migration that assumes the current known shape — no dual-schema shims, no "which old shape is this" branching. (`CLAUDE.md` → Database migrations.)
- Migration functions live in `app/db/schema.py`, are individually idempotent (guard on `PRAGMA`/`sqlite_master` inspection), and are appended to the `init_db` call list in order.
- Never hardcode real Slack workspace/team/channel IDs in source or tests.
- Docs stay terse and human-targeted — no unrequested sections.
- Commit after each task (each task ends green). Commit messages: `feat:` / `fix:` / `refactor:` / `test:` prefix, imperative mood.
- Keep the suite green at every task boundary. Existing tests that assert old behaviour are updated *within the task that changes that behaviour*, never left broken.
- Spec: `docs/superpowers/specs/2026-08-30-async-task-ux-design.md`. Re-read it before starting.

---

## File Structure

**Schema / queries**
- `app/db/schema.py` — MODIFY: `tasks` DDL (add `group_id`, widen `status` CHECK), `inbox_items` DDL (drop `task_id`), three new migration functions.
- `app/db/queries.py` — MODIFY: `enqueue_task` gains `group_id`; new `set_task_needs_action`, `resolve_task`, `dismiss_task`, `retry_task_params`, `get_task_group`, `get_dashboard_tasks`, `get_recent_terminal_tasks`; rewrite `resolve_source_prompts_for_url` to act on `tasks`; delete `get_recent_resolved_inbox_items`, `get_inbox_item_by_task_id`, `create_inbox_item`'s `task_id` arg.

**Task engine**
- `app/task_engine.py` — MODIFY: `execute_task` sets `needs_action` status instead of creating a `task_followup` inbox item; propagate `group_id` into `result`.

**Routes**
- `app/routes/tasks.py` — MODIFY: `task_presentation` (replaces `_task_label`/`_task_summary` internals); `GET /tasks/{id}` → HTML detail page; new `GET /tasks/{id}/state` (JSON); `GET /tasks` history page; `/tasks/{id}/log` + `/tasks/{id}/resume` → 301; `POST /tasks/{id}/dismiss` → `dismiss_task`.
- `app/routes/fetch.py` — MODIFY: `POST /fetch/all` stamps one `group_id`.
- `app/routes/sources.py`, `app/routes/jobs.py` — MODIFY: `needs_action` follow-up enqueues inherit `origin_group_id`; `resolve_source_prompts_for_url` call sites unchanged (function rewritten).
- `app/routes/inbox.py` — MODIFY: keep only the `browser_missing` resolve path (still `POST /inbox/{id}/resolve`).
- `app/main.py` — unchanged (inbox router still mounted).

**Templates**
- `app/templates/home/index.html` — MODIFY: "Tasks" section renders cards + groups.
- `app/templates/home/_task_card.html` — CREATE: one task card (all states).
- `app/templates/home/_task_group.html` — CREATE: grouped parent card with expandable children.
- `app/templates/home/_task_item.html` — MODIFY: now only the standalone-`inbox_items` row (browser_missing).
- `app/templates/tasks/detail.html` — CREATE: summary + timeline + results + inline needs-you panel + collapsed log. Replaces `tasks/log.html` + `tasks/resume.html` (both deleted).
- `app/templates/tasks/list.html` — CREATE: history table.
- `app/templates/tasks/_resolved_panel.html` — CREATE: the collapsed "decided" summary for a resolved needs-you panel.
- `app/templates/base.html` — MODIFY: `finishWatched` / `pollWatched` adapt to `needs_action`; B1 trigger disabled/reset; B2 panel-collapse; status-bar "details" link → `/tasks/{id}`; optional pulse CSS.
- `app/templates/scenarios/_proposals.html`, `app/templates/profile/_proposals.html` — MODIFY: decided-summary confirmation after apply/cancel.

**Tests** — `tests/test_schema.py`, `tests/test_queries.py`, `tests/test_task_engine.py`, `tests/test_routes_tasks.py`, `tests/test_routes_home.py`, `tests/test_routes_fetch.py`, `tests/test_routes_sources.py`, `tests/test_routes_scenarios.py`, `tests/test_routes_profile.py`.

---

## Task 1: Schema — `group_id`, status vocabulary, inbox de-mirror

**Files:**
- Modify: `app/db/schema.py` (`_DDL` `tasks` + `inbox_items`; add `_migrate_tasks_group_and_status`, `_migrate_inbox_drop_task_id`; append both to `init_db`)
- Test: `tests/test_schema.py`

**Interfaces:**
- Produces: `tasks.group_id TEXT` (nullable); `tasks.status` CHECK now `('queued','running','needs_action','done','failed','dismissed')`; `inbox_items` has no `task_id` column.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_schema.py — add

def test_tasks_table_has_group_id_and_wide_status(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}
    assert "group_id" in cols
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='tasks'"
    ).fetchone()[0]
    for s in ("queued", "running", "needs_action", "done", "failed", "dismissed"):
        assert f"'{s}'" in sql

def test_inbox_items_has_no_task_id(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(inbox_items)")}
    assert "task_id" not in cols

def test_migration_backfills_needs_action_from_open_followup(conn):
    # Simulate a pre-migration DB: a done task with needs_action result + open followup.
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        "INSERT INTO tasks (id, kind, params, status, result, finished_at) "
        "VALUES (1, 'source_detect', '{}', 'done', ?, datetime('now'))",
        ('{"needs_action": true, "resume_html": "<p>x</p>"}',),
    )
    conn.execute(
        "INSERT INTO inbox_items (kind, message, link) VALUES ('browser_missing', 'm', '/sources')"
    )
    conn.commit()
    from app.db.schema import _migrate_tasks_group_and_status, _migrate_inbox_drop_task_id
    # Re-run migrations (idempotent) — but the interesting path already ran in init_db.
    # Assert end state:
    assert conn.execute("SELECT status FROM tasks WHERE id = 1").fetchone()[0] == "needs_action"
    assert conn.execute(
        "SELECT COUNT(*) FROM inbox_items WHERE kind = 'browser_missing'"
    ).fetchone()[0] == 1
```

Note: `conftest.py`'s `conn` fixture runs `init_db` on a fresh `:memory:` DB, so the backfill branch needs pre-seeded legacy rows *before* `init_db`. Add a dedicated fixture-free test:

```python
def test_migration_from_legacy_shape():
    import sqlite3
    from app.db.schema import init_db
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    # Build the PRE-migration shape by hand (old DDL subset).
    c.executescript("""
        CREATE TABLE tasks (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, params TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','done','failed')),
            log TEXT NOT NULL DEFAULT '', result TEXT, error TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')), started_at TEXT, finished_at TEXT);
        CREATE TABLE inbox_items (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, message TEXT NOT NULL,
            link TEXT NOT NULL, task_id INTEGER REFERENCES tasks(id),
            created_at TEXT NOT NULL DEFAULT (datetime('now')), resolved_at TEXT);
        INSERT INTO tasks (id, kind, params, status, result, finished_at)
            VALUES (1, 'source_detect', '{}', 'done', '{"needs_action": true}', datetime('now'));
        INSERT INTO tasks (id, kind, params, status, result, finished_at)
            VALUES (2, 'source_detect', '{}', 'done', '{"needs_action": true}', datetime('now'));
        INSERT INTO inbox_items (kind, message, link, task_id) VALUES ('task_followup', 'm', '/t/1', 1);
        INSERT INTO inbox_items (kind, message, link, task_id, resolved_at)
            VALUES ('task_followup', 'm', '/t/2', 2, datetime('now'));
        INSERT INTO inbox_items (kind, message, link) VALUES ('browser_missing', 'b', '/sources');
    """)
    c.commit()
    init_db(c)
    # Task 1: open followup -> needs_action. Task 2: followup already resolved -> stays done.
    assert c.execute("SELECT status FROM tasks WHERE id=1").fetchone()[0] == "needs_action"
    assert c.execute("SELECT status FROM tasks WHERE id=2").fetchone()[0] == "done"
    # task_followup rows gone, task_id column dropped, browser_missing kept.
    assert c.execute("SELECT COUNT(*) FROM inbox_items WHERE kind='task_followup'").fetchone()[0] == 0
    assert "task_id" not in {r[1] for r in c.execute("PRAGMA table_info(inbox_items)")}
    assert c.execute("SELECT COUNT(*) FROM inbox_items WHERE kind='browser_missing'").fetchone()[0] == 1
    c.close()
```

- [ ] **Step 2: Run — verify fail**

Run: `python -m pytest tests/test_schema.py -k "group_id or task_id or legacy or backfill" -v`
Expected: FAIL (no `group_id` column / `task_id` still present).

- [ ] **Step 3: Update `_DDL`**

In `app/db/schema.py` `_DDL`, `tasks` table: add `group_id TEXT` after `params`, and change the CHECK to
`CHECK(status IN ('queued', 'running', 'needs_action', 'done', 'failed', 'dismissed'))`.
`inbox_items` table: remove the `task_id INTEGER REFERENCES tasks(id),` line.

- [ ] **Step 4: Add migrations**

```python
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
```

Append to `init_db` (after `_migrate_jobs_canonicalize_urls`):
```python
    _migrate_tasks_group_and_status(conn)
    _migrate_inbox_drop_task_id(conn)
```

- [ ] **Step 5: Run — verify pass**

Run: `python -m pytest tests/test_schema.py -v`
Expected: PASS. Then `python -m pytest tests/test_queries.py tests/test_task_engine.py -v` — some FAIL (expected; fixed in Tasks 2–3). Note which.

- [ ] **Step 6: Commit**

```bash
git add app/db/schema.py tests/test_schema.py
git commit -m "feat: tasks.group_id + needs_action/dismissed status; drop inbox task_id mirror"
```

---

## Task 2: Queries — task lifecycle + dashboard

**Files:**
- Modify: `app/db/queries.py`
- Test: `tests/test_queries.py`

**Interfaces:**
- Consumes: schema from Task 1.
- Produces:
  - `enqueue_task(conn, kind, params, group_id=None) -> dict` (unchanged return shape + `group_id`)
  - `set_task_needs_action(conn, task_id) -> None` — status `running`→`needs_action`, sets `finished_at`
  - `resolve_task(conn, task_id) -> None` — any→`done`, sets `finished_at` if unset
  - `dismiss_task(conn, task_id) -> None` — any→`dismissed`, sets `finished_at` if unset
  - `get_task_group(conn, group_id) -> list[dict]` — all tasks with that `group_id`, `created_at ASC`
  - `get_dashboard_tasks(conn) -> list[dict]` — see below
  - `resolve_source_prompts_for_url(conn, url) -> int` — rewritten to dismiss `needs_action` tasks
- `create_inbox_item(conn, kind, message, link) -> int` (drop the `task_id` param)
- Deleted: `get_recent_resolved_inbox_items`, `get_inbox_item_by_task_id`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_queries.py — add a section

def test_enqueue_task_stores_group_id(conn):
    t = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1}, group_id="g1")
    assert q.get_task(conn, t["id"])["group_id"] == "g1"

def test_set_task_needs_action(conn):
    t = q.enqueue_task(conn, kind="source_detect", params={})
    q.claim_next_task(conn)
    q.set_task_needs_action(conn, t["id"])
    row = q.get_task(conn, t["id"])
    assert row["status"] == "needs_action"
    assert row["finished_at"] is not None

def test_resolve_and_dismiss_task(conn):
    a = q.enqueue_task(conn, kind="source_detect", params={})
    q.set_task_needs_action(conn, a["id"])
    q.resolve_task(conn, a["id"])
    assert q.get_task(conn, a["id"])["status"] == "done"
    b = q.enqueue_task(conn, kind="source_detect", params={})
    q.set_task_needs_action(conn, b["id"])
    q.dismiss_task(conn, b["id"])
    assert q.get_task(conn, b["id"])["status"] == "dismissed"

def test_get_task_group_ordered(conn):
    q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1}, group_id="g")
    q.enqueue_task(conn, kind="fetch_source", params={"source_id": 2}, group_id="g")
    grp = q.get_task_group(conn, "g")
    assert [t["params"]["source_id"] for t in grp] == [1, 2]

def test_get_dashboard_tasks_active_always_terminal_recent_only(conn):
    active = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1})
    old = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 2})
    q.complete_task(conn, old["id"], {})
    conn.execute("UPDATE tasks SET finished_at = datetime('now', '-2 days') WHERE id = ?", (old["id"],))
    recent = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 3})
    q.complete_task(conn, recent["id"], {})
    conn.commit()
    entries = q.get_dashboard_tasks(conn)
    ids = {t["id"] for e in entries for t in e["tasks"]}
    assert active["id"] in ids
    assert recent["id"] in ids
    assert old["id"] not in ids

def test_get_dashboard_tasks_groups_by_group_id(conn):
    q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1}, group_id="g")
    q.enqueue_task(conn, kind="fetch_source", params={"source_id": 2}, group_id="g")
    solo = q.enqueue_task(conn, kind="job_reset", params={"job_id": 5})
    entries = q.get_dashboard_tasks(conn)
    groups = [e for e in entries if e["is_group"]]
    singles = [e for e in entries if not e["is_group"]]
    assert len(groups) == 1 and len(groups[0]["tasks"]) == 2
    assert any(e["tasks"][0]["id"] == solo["id"] for e in singles)

def test_resolve_source_prompts_for_url_dismisses_needs_action_task(conn):
    t = q.enqueue_task(conn, kind="source_detect", params={"url": "https://x.com/careers"})
    q.set_task_needs_action(conn, t["id"])
    n = q.resolve_source_prompts_for_url(conn, "https://x.com/careers")
    assert n == 1
    assert q.get_task(conn, t["id"])["status"] == "dismissed"
```

- [ ] **Step 2: Run — verify fail**

Run: `python -m pytest tests/test_queries.py -k "group_id or needs_action or dismiss or dashboard or source_prompts" -v`
Expected: FAIL (`enqueue_task() got unexpected kwarg` / `AttributeError`).

- [ ] **Step 3: Implement**

In `app/db/queries.py`:

```python
def enqueue_task(conn, kind, params, group_id=None):
    existing = find_active_task(conn, kind, params)
    if existing is not None:
        existing["already_active"] = True
        return existing
    params_json = json.dumps(params, sort_keys=True)
    cur = conn.execute(
        "INSERT INTO tasks (kind, params, group_id) VALUES (?, ?, ?)",
        (kind, params_json, group_id),
    )
    conn.commit()
    task = get_task(conn, cur.lastrowid)
    task["already_active"] = False
    return task


def set_task_needs_action(conn, task_id):
    conn.execute(
        "UPDATE tasks SET status = 'needs_action', "
        "finished_at = COALESCE(finished_at, datetime('now')) WHERE id = ?",
        (task_id,),
    )
    conn.commit()


def resolve_task(conn, task_id):
    conn.execute(
        "UPDATE tasks SET status = 'done', "
        "finished_at = COALESCE(finished_at, datetime('now')) WHERE id = ?",
        (task_id,),
    )
    conn.commit()


def dismiss_task(conn, task_id):
    conn.execute(
        "UPDATE tasks SET status = 'dismissed', "
        "finished_at = COALESCE(finished_at, datetime('now')) WHERE id = ?",
        (task_id,),
    )
    conn.commit()


def get_task_group(conn, group_id):
    rows = conn.execute(
        "SELECT * FROM tasks WHERE group_id = ? ORDER BY created_at ASC, id ASC", (group_id,)
    ).fetchall()
    return [_decode_task(d) for d in _rows_to_dicts(rows)]


_DASHBOARD_WINDOW_HOURS = 24

def get_dashboard_tasks(conn):
    """Entries for the Start-page Tasks section, newest-activity first.
    Each entry: {is_group: bool, group_id: str|None, tasks: [task dict, ...]}.
    Active tasks (queued/running/needs_action) always included; terminal ones
    only if finished within the last 24h."""
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
    # Bucket: a group_id shared by >1 row is a group; everything else is solo.
    by_gid = {}
    for t in tasks:
        by_gid.setdefault(t["group_id"], []).append(t)
    entries = []
    for t in tasks:
        gid = t["group_id"]
        members = by_gid.get(gid, [])
        if gid is not None and len(members) > 1:
            if members[0]["id"] == t["id"]:      # emit once, at the first member
                entries.append({"is_group": True, "group_id": gid, "tasks": members})
        else:
            entries.append({"is_group": False, "group_id": gid, "tasks": [t]})
    # newest-activity first
    entries.sort(key=lambda e: max(x["created_at"] for x in e["tasks"]), reverse=True)
    return entries


def get_recent_terminal_tasks(conn, limit=50, offset=0, status=None):
    """History-page listing. status=None -> all; else exact match."""
    where = "1=1" if status is None else "status = :status"
    rows = conn.execute(
        f"SELECT * FROM tasks WHERE {where} ORDER BY created_at DESC, id DESC "
        "LIMIT :limit OFFSET :offset",
        {"status": status, "limit": limit, "offset": offset},
    ).fetchall()
    return [_decode_task(d) for d in _rows_to_dicts(rows)]
```

Rewrite `resolve_source_prompts_for_url`:
```python
def resolve_source_prompts_for_url(conn, url):
    """Dismiss any needs_action source_detect / job_add_by_url task for `url`,
    so a stale prompt doesn't linger once that URL has been dealt with.
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
```

Delete `get_recent_resolved_inbox_items` and `get_inbox_item_by_task_id`. Change `create_inbox_item` signature to `(conn, kind, message, link)` and its INSERT to 3 columns.

- [ ] **Step 4: Run — verify pass**

Run: `python -m pytest tests/test_queries.py -v`
Expected: PASS (fix any remaining old-signature test call sites in `test_queries.py` inline).

- [ ] **Step 5: Commit**

```bash
git add app/db/queries.py tests/test_queries.py
git commit -m "feat: task lifecycle queries (needs_action/dismiss/resolve) + get_dashboard_tasks"
```

---

## Task 3: Task engine — needs_action status, group propagation

**Files:**
- Modify: `app/task_engine.py`
- Test: `tests/test_task_engine.py`

**Interfaces:**
- Consumes: `q.set_task_needs_action`, `q.enqueue_task(..., group_id=)`.
- Produces: a task whose kind-fn returns `result["needs_action"]` ends in `status='needs_action'` (not `done`), with **no** `inbox_items` row created. `result["group_id"]` is set to the task's `group_id` (or `None`) for any task, so panels rendered from the result can thread it into the follow-up POST.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_task_engine.py — add

def test_needs_action_result_sets_needs_action_status(conn, monkeypatch):
    from app import task_engine

    @task_engine.register_task_kind("_t_needs")
    def _gen(conn, client, model, config, params):
        yield "working"
        return {"needs_action": True, "resume_html": "<p>confirm</p>"}

    t = q.enqueue_task(conn, kind="_t_needs", params={})
    task_engine.execute_task(conn, None, None, None, q.get_task(conn, t["id"]))
    row = q.get_task(conn, t["id"])
    assert row["status"] == "needs_action"
    assert conn.execute("SELECT COUNT(*) FROM inbox_items").fetchone()[0] == 0

def test_plain_result_still_completes(conn):
    from app import task_engine

    @task_engine.register_task_kind("_t_plain")
    def _gen(conn, client, model, config, params):
        yield "x"
        return {"html_chunks": []}

    t = q.enqueue_task(conn, kind="_t_plain", params={})
    task_engine.execute_task(conn, None, None, None, q.get_task(conn, t["id"]))
    assert q.get_task(conn, t["id"])["status"] == "done"

def test_result_carries_group_id(conn):
    from app import task_engine

    @task_engine.register_task_kind("_t_grp")
    def _gen(conn, client, model, config, params):
        yield "x"
        return {"needs_action": True}

    t = q.enqueue_task(conn, kind="_t_grp", params={}, group_id="gg")
    task_engine.execute_task(conn, None, None, None, q.get_task(conn, t["id"]))
    assert q.get_task(conn, t["id"])["result"]["group_id"] == "gg"
```

- [ ] **Step 2: Run — verify fail**

Run: `python -m pytest tests/test_task_engine.py -k "needs_action or group_id or plain" -v`
Expected: FAIL (status is `done`; inbox row created).

- [ ] **Step 3: Implement**

In `app/task_engine.py` `execute_task`, replace the tail (from `q.complete_task(...)` onward):

```python
    finally:
        _sync_browser_missing_inbox(conn)
    result.setdefault("group_id", task.get("group_id"))
    if result.get("needs_action"):
        q.complete_task(conn, task["id"], result)      # persist result JSON
        q.set_task_needs_action(conn, task["id"])       # then flip status
    else:
        q.complete_task(conn, task["id"], result)
```

(`complete_task` sets `status='done'`; the `set_task_needs_action` call immediately after overrides it to `needs_action` and keeps `finished_at`. Simpler than adding a result-persisting variant.)

Delete the `if result.get("needs_action"): q.create_inbox_item(...)` block.

- [ ] **Step 4: Run — verify pass**

Run: `python -m pytest tests/test_task_engine.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/task_engine.py tests/test_task_engine.py
git commit -m "feat: task engine lands needs_action tasks in needs_action status, no inbox mirror"
```

---

## Task 4: `task_presentation` helper

**Files:**
- Modify: `app/routes/tasks.py` (add `task_presentation`, `group_presentation`; keep `_progress_from_line`)
- Test: `tests/test_routes_tasks.py` (unit-test the helper directly)

**Interfaces:**
- Consumes: `q.get_job`, `q.get_source`, `q.get_active_tasks` (for "N ahead").
- Produces:
  ```python
  def task_presentation(conn, task) -> dict:
      # {
      #   "title": str,          # short label
      #   "goal": str,           # one-line intent, may be ""
      #   "next_step": str,      # state-dependent line
      #   "results": list[dict], # [{"label": str, "href": str}, ...]
      #   "link": str | None,    # primary related entity (back-compat with old _task_summary["link"])
      # }
  def group_presentation(conn, tasks) -> dict:
      # {"title": str, "goal": str, "next_step": str, "results": [...], "link": None}
  ```

- [ ] **Step 1: Write failing tests**

```python
# tests/test_routes_tasks.py — add

from app.routes.tasks import task_presentation, group_presentation

def test_presentation_fetch_source(conn):
    sid = q.insert_source(conn, "Cord", "https://cord.co", "generic_listing")
    t = q.enqueue_task(conn, kind="fetch_source", params={"source_id": sid})
    p = task_presentation(conn, t)
    assert p["title"] == "Fetch: Cord"
    assert "Cord" in p["goal"]
    assert p["next_step"].startswith("Queued")

def test_presentation_running_uses_progress(conn):
    t = q.enqueue_task(conn, kind="fetch_source", params={})
    q.claim_next_task(conn)
    q.append_task_log(conn, t["id"], "[3/6] Classified as job_posting: http://x")
    p = task_presentation(conn, q.get_task(conn, t["id"]))
    assert p["next_step"] == "Step 3 of 6"

def test_presentation_needs_action(conn):
    t = q.enqueue_task(conn, kind="source_detect", params={"url": "https://x.com"})
    q.complete_task(conn, t["id"], {"needs_action": True, "action_message": "Confirm the detected source"})
    q.set_task_needs_action(conn, t["id"])
    p = task_presentation(conn, q.get_task(conn, t["id"]))
    assert p["next_step"] == "Confirm the detected source"

def test_presentation_done_results_link_for_add_by_url(conn):
    sid = q.insert_source(conn, "S", "https://e.com", "generic_listing")
    jid = q.insert_job(conn, source_id=sid, url="https://e.com/j", title="Dev", company="", raw_text="")
    t = q.enqueue_task(conn, kind="job_add_by_url", params={"url": "https://e.com/j"})
    q.complete_task(conn, t["id"], {"job_id": jid})
    p = task_presentation(conn, q.get_task(conn, t["id"]))
    assert {"label": "View job", "href": f"/jobs/{jid}"} in p["results"]

def test_presentation_failed_shows_error(conn):
    t = q.enqueue_task(conn, kind="fetch_source", params={})
    q.fail_task(conn, t["id"], "Slack auth expired\nstacktrace line\nmore")
    p = task_presentation(conn, q.get_task(conn, t["id"]))
    assert p["next_step"] == "Slack auth expired"

def test_group_presentation_aggregates(conn):
    q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1}, group_id="g")
    b = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 2}, group_id="g")
    q.fail_task(conn, b["id"], "boom")
    grp = q.get_task_group(conn, "g")
    p = group_presentation(conn, grp)
    assert "1" in p["next_step"] and "fail" in p["next_step"].lower()
```

- [ ] **Step 2: Run — verify fail**

Run: `python -m pytest tests/test_routes_tasks.py -k presentation -v`
Expected: FAIL (`ImportError`).

- [ ] **Step 3: Implement**

Rewrite the label/summary section of `app/routes/tasks.py`. Keep `_progress_from_line`, `_job_title`, `_JOB_ACTION_LABELS`. Add:

```python
_QUEUED_AHEAD_CACHE = None  # (not needed; compute per call)

def _queued_ahead(conn, task):
    row = conn.execute(
        "SELECT COUNT(*) FROM tasks WHERE status = 'queued' AND created_at < ?",
        (task["created_at"],),
    ).fetchone()
    return row[0]

def _title(conn, task):
    # exactly today's _task_label body — keep it, it is already tested via
    # /tasks/active label assertions. (Move the function here, rename to _title.)
    ...

def _goal(conn, task):
    kind, params = task["kind"], task["params"]
    if kind == "fetch_source":
        src = q.get_source(conn, params.get("source_id"))
        return f"Pull new postings from {src['name']}" if src else "Pull new postings from a source"
    if kind == "job_add_by_url":
        url = params.get("url", "")
        return f"Add the job at {url[:60]}{'…' if len(url) > 60 else ''}"
    if kind in ("job_reevaluate", "jobs_bulk_reevaluate", "scenarios_reevaluate_all"):
        return "Re-score against your scenarios and profile"
    if kind in ("job_reset", "jobs_bulk_reset"):
        return "Re-run the full pipeline for the selected job(s)"
    if kind == "job_pass_as_new":
        return "Bypass the gate and re-score this job"
    if kind in ("scenarios_refine_all", "scenario_refine_one", "profile_refine"):
        return "Generate improvement suggestions from your feedback"
    if kind in ("source_detect", "source_confirm", "job_add_listing_source"):
        return f"Add {params.get('name') or params.get('url', 'a source')} as a source"
    return ""

def _next_step(conn, task):
    status = task["status"]
    if status == "queued":
        n = _queued_ahead(conn, task)
        return f"Queued — {n} ahead" if n else "Queued"
    if status == "running":
        last = (task["log"].strip().split("\n") or [""])[-1]
        prog = _progress_from_line(last)
        if prog:
            return f"Step {prog['current']} of {prog['total']}"
        return last or "Running"
    if status == "needs_action":
        return (task.get("result") or {}).get("action_message") or "Needs your input"
    if status == "failed":
        return (task["error"] or "Failed").strip().split("\n")[0]
    if status == "dismissed":
        return "Dismissed"
    # done
    return _done_summary(conn, task)

def _done_summary(conn, task):
    r = task.get("result") or {}
    kind = task["kind"]
    if kind == "fetch_source":
        n = r.get("jobs_new")
        if n is not None:
            return f"{n} new job{'s' if n != 1 else ''}" if n else "No new postings"
    if kind in ("source_confirm", "job_add_listing_source", "source_detect") and r.get("source_id"):
        src = q.get_source(conn, r["source_id"])
        return f"Source '{src['name']}' created" if src else "Source created"
    if kind == "job_add_by_url" and r.get("job_id"):
        return "Job added"
    return "Done"

_SINGLE_JOB_KINDS = {"job_reset", "job_pass_as_new", "job_reevaluate"}

def _results(conn, task):
    r = task.get("result") or {}
    kind, params = task["kind"], task["params"]
    out = []
    if kind == "fetch_source":
        sid = params.get("source_id")
        if sid:
            out.append({"label": "Jobs from this source", "href": f"/jobs?source_id={sid}"})
        out.append({"label": "Fetch history", "href": "/fetch"})
    elif kind == "job_add_by_url" and r.get("job_id"):
        out.append({"label": "View job", "href": f"/jobs/{r['job_id']}"})
    elif kind in _SINGLE_JOB_KINDS and params.get("job_id"):
        out.append({"label": "View job", "href": f"/jobs/{params['job_id']}"})
    elif kind in ("jobs_bulk_reset", "jobs_bulk_reevaluate"):
        out.append({"label": "Back to jobs", "href": "/jobs"})
    elif kind in ("source_confirm", "job_add_listing_source", "source_detect") and r.get("source_id"):
        out.append({"label": "View source", "href": f"/sources#source-{r['source_id']}"})
    elif kind in ("scenarios_refine_all", "scenario_refine_one", "scenarios_reevaluate_all"):
        for sid in r.get("affected_scenario_ids", []):
            out.append({"label": "Review suggestions", "href": f"/scenarios#proposals-area-{sid}"})
        if not r.get("affected_scenario_ids"):
            out.append({"label": "Open Scenarios", "href": "/scenarios"})
    elif kind == "profile_refine":
        out.append({"label": "Review profile suggestions", "href": "/profile"})
    return out

def _link(conn, task):
    """Back-compat: the single 'related entity' link the old _task_summary exposed."""
    if task["kind"] in _SINGLE_JOB_KINDS and task["params"].get("job_id") is not None:
        return f"/jobs/{task['params']['job_id']}"
    r = task.get("result") or {}
    if task["kind"] == "job_add_by_url" and r.get("job_id"):
        return f"/jobs/{r['job_id']}"
    return None

def task_presentation(conn, task):
    return {
        "title": _title(conn, task),
        "goal": _goal(conn, task),
        "next_step": _next_step(conn, task),
        "results": _results(conn, task),
        "link": _link(conn, task),
    }

def group_presentation(conn, tasks):
    total = len(tasks)
    done = sum(1 for t in tasks if t["status"] == "done")
    failed = sum(1 for t in tasks if t["status"] == "failed")
    active = [t for t in tasks if t["status"] in ("queued", "running")]
    needs = [t for t in tasks if t["status"] == "needs_action"]
    first = tasks[0]
    # A chain (detect -> confirm -> add) is a group whose members are different
    # kinds; a fan-out (fetch all) is same-kind. Present accordingly.
    same_kind = len({t["kind"] for t in tasks}) == 1
    if same_kind and first["kind"] == "fetch_source":
        title = f"Fetch: {total} sources"
        goal = f"Pull new postings from {total} sources"
    else:
        title = task_presentation(conn, first)["title"]
        goal = task_presentation(conn, first)["goal"]
    if needs:
        next_step = task_presentation(conn, needs[0])["next_step"]
    elif active:
        parts = [f"{done} of {total} done"]
        if failed:
            parts.append(f"{failed} failed")
        next_step = ", ".join(parts)
    else:
        parts = [f"{done} of {total} done"]
        if failed:
            parts.append(f"{failed} failed")
        next_step = ", ".join(parts)
    results = []
    for t in tasks:
        results.extend(task_presentation(conn, t)["results"])
    # dedupe by (label, href)
    seen, deduped = set(), []
    for x in results:
        key = (x["label"], x["href"])
        if key not in seen:
            seen.add(key); deduped.append(x)
    return {"title": title, "goal": goal, "next_step": next_step, "results": deduped, "link": None}
```

Update `_task_summary` (used by `/tasks/active`) to merge in `task_presentation` fields:
```python
def _task_summary(conn, task, *, include_result=False):
    pres = task_presentation(conn, task)
    log = task["log"].strip()
    last_line = log.split("\n")[-1] if log else ""
    summary = {
        "id": task["id"], "kind": task["kind"], "label": pres["title"],
        "goal": pres["goal"], "next_step": pres["next_step"], "results": pres["results"],
        "status": task["status"], "last_line": last_line, "error": task["error"],
        "progress": _progress_from_line(last_line), "group_id": task["group_id"],
    }
    if pres["link"]:
        summary["link"] = pres["link"]
    if include_result:
        summary["result"] = task["result"]; summary["log"] = task["log"]
    return summary
```

Note: existing `/tasks/active` label tests (`test_tasks_active_labels_fetch_source_with_source_name` etc.) still pass — `pres["title"]` == old `_task_label` output. The `_task_label` name can be kept as an alias `_task_label = _title` if other modules import it (grep: none do outside this file).

- [ ] **Step 4: Run — verify pass**

Run: `python -m pytest tests/test_routes_tasks.py -v`
Expected: PASS (adjust any wording assertions to match the strings you chose; keep them consistent with the spec's table).

- [ ] **Step 5: Commit**

```bash
git add app/routes/tasks.py tests/test_routes_tasks.py
git commit -m "feat: task_presentation helper (title/goal/next_step/results) + group_presentation"
```

---

## Task 5: Group stamping — fetch-all + detect chain

**Files:**
- Modify: `app/routes/fetch.py` (`POST /fetch/all`), `app/routes/sources.py` (`/sources/detect`, `/sources/detect/confirm`), `app/routes/jobs.py` (`/jobs/add-by-url`, `/jobs/add-listing-source` + the two `needs_action` panel renders)
- Modify: `app/templates/_rewrite_panel.html`, `app/templates/jobs/_listing_confirm.html`, `app/templates/sources/_detect_confirm.html`, `app/templates/sources/_detect_mismatch.html` (thread `origin_group_id`)
- Test: `tests/test_routes_fetch.py`, `tests/test_routes_sources.py`

**Interfaces:**
- Consumes: `q.enqueue_task(..., group_id=)`, `result["group_id"]` from Task 3.
- Produces: `POST /fetch/all` stamps one shared `group_id` (`"fa-" + secrets.token_hex(4)`) across its `fetch_source` tasks. A `needs_action` panel carries `data-progress-body-origin_group_id="#…"` **or** a hidden field; the confirm/add route reads `origin_group_id` from the form and passes it as `group_id` to `enqueue_task`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_routes_fetch.py — add
def test_fetch_all_stamps_one_group(client, conn):
    q.insert_source(conn, "A", "https://a.com", "generic_listing")
    q.insert_source(conn, "B", "https://b.com", "generic_listing")
    resp = client.post("/fetch/all")
    ids = resp.json()["task_ids"]
    gids = {q.get_task(conn, i)["group_id"] for i in ids}
    assert len(gids) == 1 and next(iter(gids)) is not None

# tests/test_routes_sources.py — add
def test_detect_confirm_inherits_group(client, conn):
    detect = q.enqueue_task(conn, kind="source_detect", params={"url": "https://x.com"}, group_id="gX")
    q.complete_task(conn, detect["id"], {"needs_action": True, "group_id": "gX"})
    q.set_task_needs_action(conn, detect["id"])
    resp = client.post("/sources/detect/confirm", data={
        "url": "https://x.com", "name": "X Careers", "fetcher_type": "generic_listing",
        "origin_group_id": "gX",
    })
    tid = resp.json()["task_id"]
    assert q.get_task(conn, tid)["group_id"] == "gX"
```

- [ ] **Step 2: Run — verify fail**

Run: `python -m pytest tests/test_routes_fetch.py::test_fetch_all_stamps_one_group tests/test_routes_sources.py::test_detect_confirm_inherits_group -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

`app/routes/fetch.py` `POST /fetch/all` (around line 65):
```python
import secrets
...
    gid = "fa-" + secrets.token_hex(4)
    tasks = [q.enqueue_task(conn, kind="fetch_source", params={"source_id": s["id"]}, group_id=gid)
             for s in sources]
    return {"task_ids": [t["id"] for t in tasks]}
```

`app/routes/sources.py`:
- `POST /sources/detect` — stamp a group so a later confirm can join it:
  ```python
  gid = "sd-" + secrets.token_hex(4)
  task = q.enqueue_task(conn, kind="source_detect", params={"url": url, "skip_rewrite": skip_rewrite}, group_id=gid)
  ```
- The `source_detect` task-kind fn already returns a `result` with `resume_html`; the panel template needs `origin_group_id`. Pass it into the template render context: `group_id=params.get("group_id")` isn't in params — instead read it from the task in `task_engine` (already injected as `result["group_id"]` by Task 3). So the panel-rendering code (in the task-kind fn) must include the group id. Simplest: the kind fn builds `resume_html` via `templates.get_template(...).render(..., origin_group_id=??)`. It does not have the task row. **Fix:** have `task_engine.execute_task` post-process `result["resume_html"]` is too hacky. Instead: the kind fn returns `result` WITHOUT rendering the panel's group field, and `resume.html` / inline landing always renders `origin_group_id` from `result.group_id` which IS available wherever the panel is displayed (detail page, `/tasks/{id}/state`, front card). See Task 9 — the panel templates get `origin_group_id` from the rendering route, not the kind fn.
  For the **inline** case (base.html drops `result.resume_html` into a target) the group id must already be inside that HTML. So the kind fn DOES need it. Resolution: kind fns that set `needs_action` also set `result["origin_group_id"]` = the group they were enqueued with — but they don't know it either.
  **Chosen resolution:** `task_engine.execute_task`, after `result.setdefault("group_id", task["group_id"])`, if `result.get("resume_html")` and `task["group_id"]`, string-replace the sentinel `__ORIGIN_GROUP__` in `resume_html` with the group id. Panel templates render `value="__ORIGIN_GROUP__"` for the hidden field. Document this sentinel in `task_engine.py`.

  Add to `execute_task` after the `setdefault`:
  ```python
  if result.get("resume_html") and result.get("group_id"):
      result["resume_html"] = result["resume_html"].replace("__ORIGIN_GROUP__", result["group_id"])
  ```

- `POST /sources/detect/confirm` and `/jobs/add-listing-source` and `/jobs/add-by-url`: accept `origin_group_id: str = Form("")` and pass `group_id=origin_group_id or None` to `enqueue_task`.

Panel templates — add to each `<form>` / button group:
```html
<input type="hidden" name="origin_group_id" value="__ORIGIN_GROUP__">
```
and for the `data-progress` buttons add `data-progress-body-origin_group_id="#<the hidden input selector>"` — or simpler, since base.html's POST already serialises `data-progress-body-*` fields by selector, give the hidden input an id and reference it. (Check `_rewrite_panel.html` etc. for existing hidden-input patterns; `#rw-skip` shows the convention.)

- [ ] **Step 4: Run — verify pass**

Run: `python -m pytest tests/test_routes_fetch.py tests/test_routes_sources.py tests/test_routes_jobs.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/routes/fetch.py app/routes/sources.py app/routes/jobs.py app/task_engine.py app/templates/_rewrite_panel.html app/templates/jobs/_listing_confirm.html app/templates/sources/_detect_confirm.html app/templates/sources/_detect_mismatch.html tests/
git commit -m "feat: stamp a shared group_id across fetch-all fan-out and detect->confirm chains"
```

---

## Task 6: Detail page + `/state` JSON + redirects + dismiss

**Files:**
- Modify: `app/routes/tasks.py`
- Create: `app/templates/tasks/detail.html`
- Delete: `app/templates/tasks/log.html`, `app/templates/tasks/resume.html`
- Test: `tests/test_routes_tasks.py`

**Interfaces:**
- Consumes: `task_presentation`, `group_presentation`, `q.get_task`, `q.get_task_group`, `q.dismiss_task`, `q.resolve_task`.
- Produces:
  - `GET /tasks/{id}` → **HTML** detail page (was JSON).
  - `GET /tasks/{id}/state` → JSON (`_task_summary(conn, task, include_result=True)`) — the poller target.
  - `GET /tasks/{id}/log` → `RedirectResponse(f"/tasks/{id}", 301)`.
  - `GET /tasks/{id}/resume` → `RedirectResponse(f"/tasks/{id}", 301)`.
  - `POST /tasks/{id}/dismiss` → `q.dismiss_task`, 303 to `Referer`/`/`.
  - `GET /tasks/active` unchanged shape (still consumed by the status bar) but each task also carries `goal` / `next_step` / `group_id`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_routes_tasks.py — replace resume/log tests with:

def test_detail_page_renders_summary_and_results(client, conn):
    sid = q.insert_source(conn, "S", "https://e.com", "generic_listing")
    jid = q.insert_job(conn, source_id=sid, url="https://e.com/j", title="Dev", company="", raw_text="")
    t = q.enqueue_task(conn, kind="job_add_by_url", params={"url": "https://e.com/j"})
    q.complete_task(conn, t["id"], {"job_id": jid})
    html = client.get(f"/tasks/{t['id']}").text
    assert "Add job by URL" in html
    assert f'href="/jobs/{jid}"' in html
    assert "task-log" in html  # collapsed <details> present

def test_detail_page_shows_needs_action_panel_inline(client, conn):
    t = q.enqueue_task(conn, kind="source_detect", params={"url": "https://x.com"})
    q.complete_task(conn, t["id"], {"needs_action": True, "resume_html": "<p id='panel'>confirm me</p>",
                                    "action_message": "Confirm the detected source"})
    q.set_task_needs_action(conn, t["id"])
    html = client.get(f"/tasks/{t['id']}").text
    assert "confirm me" in html
    assert "Confirm the detected source" in html

def test_state_endpoint_returns_json(client, conn):
    t = q.enqueue_task(conn, kind="fetch_source", params={})
    q.append_task_log(conn, t["id"], "hello")
    data = client.get(f"/tasks/{t['id']}/state").json()
    assert data["status"] == "queued"
    assert data["log"] == "hello\n"

def test_log_and_resume_redirect(client, conn):
    t = q.enqueue_task(conn, kind="fetch_source", params={})
    for path in (f"/tasks/{t['id']}/log", f"/tasks/{t['id']}/resume"):
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 301
        assert r.headers["location"] == f"/tasks/{t['id']}"

def test_dismiss_sets_dismissed_status(client, conn):
    t = q.enqueue_task(conn, kind="source_detect", params={})
    q.complete_task(conn, t["id"], {"needs_action": True})
    q.set_task_needs_action(conn, t["id"])
    r = client.post(f"/tasks/{t['id']}/dismiss", follow_redirects=False)
    assert r.status_code == 303
    assert q.get_task(conn, t["id"])["status"] == "dismissed"

def test_grouped_detail_lists_children(client, conn):
    q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1}, group_id="g")
    q.enqueue_task(conn, kind="fetch_source", params={"source_id": 2}, group_id="g")
    html = client.get("/tasks/group/g").text
    assert "source" in html.lower()
```

- [ ] **Step 2: Run — verify fail**

Run: `python -m pytest tests/test_routes_tasks.py -v`
Expected: many FAIL.

- [ ] **Step 3: Implement routes**

```python
@router.get("/tasks/active")
def tasks_active(conn=Depends(get_db)):
    return {
        "tasks": [_task_summary(conn, t) for t in q.get_active_tasks(conn)],
        "inbox_count": q.count_unresolved_inbox_items(conn),
    }

@router.get("/tasks/{task_id}/state")
def task_state(task_id: int, conn=Depends(get_db)):
    task = q.get_task(conn, task_id)
    if task is None:
        raise HTTPException(404, "Task not found")
    return _task_summary(conn, task, include_result=True)

@router.get("/tasks/{task_id}/log")
def task_log_redirect(task_id: int):
    return RedirectResponse(f"/tasks/{task_id}", status_code=301)

@router.get("/tasks/{task_id}/resume")
def task_resume_redirect(task_id: int):
    return RedirectResponse(f"/tasks/{task_id}", status_code=301)

@router.get("/tasks/group/{group_id}", response_class=HTMLResponse)
def task_group_detail(group_id: str, request: Request, conn=Depends(get_db)):
    tasks = q.get_task_group(conn, group_id)
    if not tasks:
        raise HTTPException(404, "Group not found")
    pres = group_presentation(conn, tasks)
    children = [{"task": t, "pres": task_presentation(conn, t)} for t in tasks]
    return templates.TemplateResponse(request, "tasks/detail.html", {
        "is_group": True, "group_id": group_id, "pres": pres, "children": children,
        "tasks": tasks,
    })

@router.get("/tasks/{task_id}", response_class=HTMLResponse)
def task_detail(task_id: int, request: Request, conn=Depends(get_db)):
    task = q.get_task(conn, task_id)
    if task is None:
        raise HTTPException(404, "Task not found")
    pres = task_presentation(conn, task)
    lines = task["log"].strip().split("\n") if task["log"].strip() else []
    resume_html = (task.get("result") or {}).get("resume_html") if task["status"] == "needs_action" else None
    return templates.TemplateResponse(request, "tasks/detail.html", {
        "is_group": False, "task": task, "pres": pres, "lines": lines,
        "resume_html": resume_html,
        "active": task["status"] in ("queued", "running"),
    })

@router.post("/tasks/{task_id}/dismiss")
def task_dismiss(task_id: int, request: Request, conn=Depends(get_db)):
    task = q.get_task(conn, task_id)
    if task is not None and task["status"] == "needs_action":
        q.dismiss_task(conn, task_id)
    ref = request.headers.get("referer") or "/"
    from urllib.parse import urlparse
    dest = ref if urlparse(ref).netloc == urlparse(str(request.url)).netloc else "/"
    return RedirectResponse(dest, status_code=303)

@router.get("/tasks", response_class=HTMLResponse)   # placeholder — filled in Task 7
def task_history(request: Request, conn=Depends(get_db)):
    ...
```

Route-ordering caveat: `/tasks/{task_id}` is a catch-all; declare `/tasks/active`, `/tasks/group/{group_id}`, `/tasks/{task_id}/state`, `/tasks/{task_id}/log`, `/tasks/{task_id}/resume`, `/tasks` **before** it, and keep `task_id: int` so `/tasks/active` never matches the int route. FastAPI matches in declaration order — put the bare `/tasks` and `/tasks/active` first.

- [ ] **Step 4: Implement `tasks/detail.html`**

```html
{% extends "base.html" %}
{% block title %}{{ pres.title }} — Job Seek{% endblock %}
{% block content %}
<h1>{{ pres.title }}</h1>
{% if pres.goal %}<p class="resume-message">{{ pres.goal }}</p>{% endif %}
<p class="resume-subtitle"><strong>{{ pres.next_step }}</strong></p>

{% if not is_group %}
<dl class="task-meta">
  <dt>Status</dt><dd id="task-log-status">{{ task.status }}{% if task.error %} — {{ task.error }}{% endif %}</dd>
  <dt>Created</dt><dd>{{ task.created_at | age }}</dd>
  {% if task.started_at %}<dt>Started</dt><dd>{{ task.started_at | age }}</dd>{% endif %}
  {% if task.finished_at %}<dt>{{ 'Needs you since' if task.status == 'needs_action' else 'Finished' }}</dt><dd>{{ task.finished_at | age }}</dd>{% endif %}
</dl>

{% if resume_html %}
<div id="resume-page" data-task-id="{{ task.id }}" style="margin:1.25rem 0;">{{ resume_html | safe }}</div>
<form method="post" action="/tasks/{{ task.id }}/dismiss" class="resume-dismiss">
  <button type="submit" class="btn btn-subtle">Dismiss</button>
</form>
{% endif %}

{% if pres.results %}
<h2>Results</h2>
<ul>{% for r in pres.results %}<li><a href="{{ r.href }}">{{ r.label }}</a></li>{% endfor %}</ul>
{% endif %}

<details class="task-log-details" {% if active %}open{% endif %}>
  <summary>Log</summary>
  <pre id="task-log-lines" class="task-log">{% for line in lines %}{{ line }}
{% endfor %}</pre>
</details>

{% if active %}
<script>
(function () {
  var pre = document.getElementById("task-log-lines");
  var statusEl = document.getElementById("task-log-status");
  setInterval(function () {
    fetch("/tasks/{{ task.id }}/state").then(function (r) { return r.json(); }).then(function (t) {
      pre.textContent = (t.log || "").trim();
      statusEl.textContent = t.status;
      if (["done", "failed", "needs_action", "dismissed"].indexOf(t.status) !== -1) location.reload();
    });
  }, 2000);
})();
</script>
{% endif %}

{% else %}
<h2>Steps</h2>
<ol>
{% for c in children %}
  <li><a href="/tasks/{{ c.task.id }}">{{ c.pres.title }}</a> — {{ c.pres.next_step }}</li>
{% endfor %}
</ol>
{% if pres.results %}
<h2>Results</h2>
<ul>{% for r in pres.results %}<li><a href="{{ r.href }}">{{ r.label }}</a></li>{% endfor %}</ul>
{% endif %}
{% endif %}

<p style="margin-top:1.5rem;"><a href="/tasks">All tasks</a> · <a href="/">Back to Start</a></p>
{% endblock %}
```

Add `.task-log-details summary { cursor: pointer; color: var(--text-secondary); }` to `base.html`.

Note: the `#resume-page` div keeps the same id the old resume page used, so base.html's `finishWatched` resume-branch (Task 8) still recognises "I'm on a standalone action page". Swap `data-inbox-item-id` → `data-task-id`.

- [ ] **Step 5: Run — verify pass**

Run: `python -m pytest tests/test_routes_tasks.py -v`
Expected: PASS. Update `test_task_log_page_*` / `test_task_resume_*` names+bodies to the new tests above (delete the obsolete ones).

- [ ] **Step 6: Commit**

```bash
git add app/routes/tasks.py app/templates/tasks/ tests/test_routes_tasks.py app/templates/base.html
git commit -m "feat: task detail page (summary+results+log), /state JSON, /log+/resume 301"
```

---

## Task 7: History page `GET /tasks`

**Files:**
- Modify: `app/routes/tasks.py` (fill in `task_history`)
- Create: `app/templates/tasks/list.html`
- Test: `tests/test_routes_tasks.py`

**Interfaces:**
- Consumes: `q.get_recent_terminal_tasks(conn, limit, offset, status)`, `task_presentation`.
- Produces: `GET /tasks?status=<all|active|done|failed>&page=<n>` — paginated table, 50/page.

- [ ] **Step 1: Write failing tests**

```python
def test_history_lists_all_tasks_newest_first(client, conn):
    a = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1})
    b = q.enqueue_task(conn, kind="job_reset", params={"job_id": 2})
    q.fail_task(conn, b["id"], "boom")
    html = client.get("/tasks").text
    assert html.index("job reset" if "job reset" in html else "Reset job") < html.index("fetch source" if "fetch source" in html else "Fetch")
    assert "failed" in html

def test_history_status_filter(client, conn):
    a = q.enqueue_task(conn, kind="fetch_source", params={})
    b = q.enqueue_task(conn, kind="fetch_source", params={})
    q.fail_task(conn, b["id"], "x")
    html = client.get("/tasks?status=failed").text
    assert str(b["id"]) in html and f'/tasks/{a["id"]}"' not in html

def test_history_pagination(client, conn):
    for i in range(55):
        q.enqueue_task(conn, kind="fetch_source", params={"source_id": i})
    page1 = client.get("/tasks").text
    page2 = client.get("/tasks?page=2").text
    assert "page=2" in page1
    assert page1 != page2
```

- [ ] **Step 2: Run — verify fail** — `python -m pytest tests/test_routes_tasks.py -k history -v` → FAIL.

- [ ] **Step 3: Implement**

```python
@router.get("/tasks", response_class=HTMLResponse)
def task_history(request: Request, status: str = "all", page: int = 1, conn=Depends(get_db)):
    per = 50
    page = max(1, page)
    status_filter = None if status in ("all", "active") else status
    rows = q.get_recent_terminal_tasks(conn, limit=per + 1, offset=(page - 1) * per, status=status_filter)
    if status == "active":
        rows = [t for t in rows if t["status"] in ("queued", "running", "needs_action")]
    has_next = len(rows) > per
    rows = rows[:per]
    items = [{"task": t, "pres": task_presentation(conn, t)} for t in rows]
    return templates.TemplateResponse(request, "tasks/list.html", {
        "items": items, "status": status, "page": page, "has_next": has_next,
    })
```

`app/templates/tasks/list.html`:
```html
{% extends "base.html" %}
{% block title %}Tasks — Job Seek{% endblock %}
{% block content %}
<h1>Tasks</h1>
<div class="filter-bar">
  <div>
    {% for s in ["all", "active", "done", "failed"] %}
    <a href="/tasks?status={{ s }}" class="{{ 'active' if status == s else '' }}">{{ s|capitalize }}</a>
    {% endfor %}
  </div>
</div>
<div class="responsive-table-wrap">
<table>
  <thead><tr><th>Task</th><th>State</th><th>Age</th></tr></thead>
  <tbody>
  {% for it in items %}
    <tr>
      <td><a href="{{ ('/tasks/group/' ~ it.task.group_id) if it.task.group_id else ('/tasks/' ~ it.task.id) }}">{{ it.pres.title }}</a></td>
      <td>{{ it.task.status }} — {{ it.pres.next_step }}</td>
      <td>{{ it.task.created_at | age }}</td>
    </tr>
  {% endfor %}
  </tbody>
</table>
</div>
<p>
  {% if page > 1 %}<a href="/tasks?status={{ status }}&page={{ page - 1 }}">← newer</a>{% endif %}
  {% if has_next %}<a href="/tasks?status={{ status }}&page={{ page + 1 }}">older →</a>{% endif %}
</p>
{% endblock %}
```

- [ ] **Step 4: Run — verify pass** — `python -m pytest tests/test_routes_tasks.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add app/routes/tasks.py app/templates/tasks/list.html tests/test_routes_tasks.py
git commit -m "feat: /tasks history page with status filter + pagination"
```

---

## Task 8: Front-page task cards

**Files:**
- Modify: `app/routes/home.py`, `app/templates/home/index.html`, `app/templates/home/_task_item.html`
- Create: `app/templates/home/_task_card.html`, `app/templates/home/_task_group.html`
- Test: `tests/test_routes_home.py`

**Interfaces:**
- Consumes: `q.get_dashboard_tasks(conn)`, `q.get_unresolved_inbox_items(conn)`, `task_presentation`, `group_presentation` (import from `app.routes.tasks`).
- Produces: `home()` context gets `task_entries` (list of `{is_group, group_id, tasks, pres}`) and `standalone_notices` (unresolved `inbox_items`). Drops `pending_tasks` / `recent_completed_tasks`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_routes_home.py — replace the tasks-section tests

def test_home_shows_active_task_card(client, conn):
    sid = q.insert_source(conn, "Cord", "https://cord.co", "generic_listing")
    q.enqueue_task(conn, kind="fetch_source", params={"source_id": sid})
    html = client.get("/").text
    assert "Fetch: Cord" in html
    assert "Queued" in html

def test_home_shows_needs_action_card_with_dismiss(client, conn):
    t = q.enqueue_task(conn, kind="source_detect", params={"url": "https://x.com"})
    q.complete_task(conn, t["id"], {"needs_action": True, "action_message": "Confirm the detected source"})
    q.set_task_needs_action(conn, t["id"])
    html = client.get("/").text
    assert "Confirm the detected source" in html
    assert f'/tasks/{t["id"]}/dismiss' in html

def test_home_groups_fetch_all(client, conn):
    q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1}, group_id="g")
    q.enqueue_task(conn, kind="fetch_source", params={"source_id": 2}, group_id="g")
    html = client.get("/").text
    assert "2 sources" in html
    assert '/tasks/group/g' in html

def test_home_hides_old_done_task(client, conn):
    t = q.enqueue_task(conn, kind="fetch_source", params={})
    q.complete_task(conn, t["id"], {})
    conn.execute("UPDATE tasks SET finished_at = datetime('now','-2 days') WHERE id = ?", (t["id"],))
    conn.commit()
    assert f'/tasks/{t["id"]}' not in client.get("/").text

def test_home_still_shows_browser_missing_notice(client, conn):
    q.create_inbox_item(conn, kind="browser_missing", message="Install chromium", link="/sources")
    assert "Install chromium" in client.get("/").text
```

- [ ] **Step 2: Run — verify fail** — FAIL.

- [ ] **Step 3: Implement `home.py`**

```python
from app.routes.tasks import task_presentation, group_presentation
...
    entries = q.get_dashboard_tasks(conn)
    for e in entries:
        e["pres"] = group_presentation(conn, e["tasks"]) if e["is_group"] else task_presentation(conn, e["tasks"][0])
    context["task_entries"] = entries
    context["standalone_notices"] = q.get_unresolved_inbox_items(conn)
```

`home/index.html` — replace the `{% if pending_tasks or recent_completed_tasks %}` block:
```html
{% if task_entries or standalone_notices %}
<section class="tasks-section">
  <h2>Tasks <a href="/tasks" style="font-size:0.7em; font-weight:400;">all tasks →</a></h2>
  {% for notice in standalone_notices %}
    {% include "home/_task_item.html" %}
  {% endfor %}
  {% for entry in task_entries %}
    {% if entry.is_group %}{% include "home/_task_group.html" %}
    {% else %}{% set task = entry.tasks[0] %}{% set pres = entry.pres %}{% include "home/_task_card.html" %}
    {% endif %}
  {% endfor %}
</section>
{% endif %}
```

`home/_task_item.html` — simplify to just the standalone-notice row (drop the `done`/checkbox-task branches; keep the `browser_missing` shape):
```html
<div class="task-card task-card-notice" id="inbox-item-{{ notice.id }}">
  <a href="{{ notice.link }}">{{ notice.message }}</a>
  <span class="task-age">{{ notice.created_at | age }}</span>
  <button type="button" class="btn-subtle" hx-post="/inbox/{{ notice.id }}/resolve"
          hx-target="#inbox-item-{{ notice.id }}" hx-swap="outerHTML">Dismiss</button>
</div>
```

`home/_task_card.html`:
```html
<div class="task-card task-card-{{ task.status }}" id="task-card-{{ task.id }}">
  <div class="task-card-head">
    <a class="task-card-title" href="{{ ('/tasks/group/' ~ task.group_id) if task.group_id else ('/tasks/' ~ task.id) }}">{{ pres.title }}</a>
    <span class="task-age">{{ task.created_at | age }}</span>
  </div>
  <p class="task-card-step">{{ pres.next_step }}</p>
  {% if task.status == "needs_action" %}
  <div class="task-card-actions">
    <a class="btn btn-primary" href="/tasks/{{ task.id }}">Review</a>
    <form method="post" action="/tasks/{{ task.id }}/dismiss" style="display:inline;">
      <button type="submit" class="btn btn-subtle">Dismiss</button>
    </form>
  </div>
  {% elif task.status == "failed" %}
  <div class="task-card-actions">
    <span class="task-card-error">{{ task.error|truncate(120) }}</span>
  </div>
  {% elif task.status in ("done",) and pres.results %}
  <div class="task-card-results">
    {% for r in pres.results %}<a href="{{ r.href }}">{{ r.label }}</a>{% endfor %}
  </div>
  {% endif %}
</div>
```

`home/_task_group.html`:
```html
<div class="task-card task-card-group">
  <div class="task-card-head">
    <a class="task-card-title" href="/tasks/group/{{ entry.group_id }}">{{ entry.pres.title }}</a>
    <span class="task-age">{{ entry.tasks[0].created_at | age }}</span>
  </div>
  <p class="task-card-step">{{ entry.pres.next_step }}</p>
  <details>
    <summary>{{ entry.tasks|length }} steps</summary>
    <ul>
    {% for t in entry.tasks %}
      <li><a href="/tasks/{{ t.id }}">{{ t.status }}</a> — {{ t.kind.replace('_', ' ') }}</li>
    {% endfor %}
    </ul>
  </details>
  {% if entry.pres.results %}
  <div class="task-card-results">
    {% for r in entry.pres.results %}<a href="{{ r.href }}">{{ r.label }}</a>{% endfor %}
  </div>
  {% endif %}
</div>
```

Add card CSS to `base.html` (near `.task-*` rules): `.task-card { border: 1px solid var(--border); border-radius: 8px; background: var(--surface); padding: 0.6rem 0.85rem; margin-bottom: 0.6rem; }` plus `.task-card-head { display:flex; justify-content:space-between; align-items:baseline; gap:0.75rem; }`, `.task-card-title { font-family: var(--font-serif); font-weight:600; text-decoration:none; }`, `.task-card-step { margin:0.3rem 0 0; color: var(--text-secondary); font-size:0.9rem; }`, `.task-card-actions, .task-card-results { margin-top:0.5rem; display:flex; gap:0.5rem; flex-wrap:wrap; }`, `.task-card-needs_action { border-color: var(--warning); }`, `.task-card-failed { border-color: var(--alert); }`.

- [ ] **Step 4: Run — verify pass** — `python -m pytest tests/test_routes_home.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add app/routes/home.py app/templates/home/ tests/test_routes_home.py app/templates/base.html
git commit -m "feat: front-page task cards driven by get_dashboard_tasks, grouped by group_id"
```

---

## Task 9: base.html — poller + needs_action + status-bar link + B1 trigger state

**Files:**
- Modify: `app/templates/base.html`
- Test: manual (dev server) — noted; plus `tests/test_routes_tasks.py` already covers `/state`.

**Interfaces:**
- Consumes: `GET /tasks/{id}/state` (renamed from `/tasks/{id}`), task `status` may now be `needs_action`.

- [ ] **Step 1: `pollWatched` → `/state`, treat `needs_action` as terminal-for-watch**

In `base.html`, `pollWatched`:
```js
fetch("/tasks/" + taskId + "/state").then(function (r) { return r.json(); }).then(function (task) {
  if (!watched[taskId]) return;
  if (task.status === "done" || task.status === "failed" || task.status === "needs_action") {
    finishWatched(taskId, task);
  } else {
    setTimeout(function () { pollWatched(taskId); }, 2000);
  }
})...
```
`pollBatch` similarly: `fetch("/tasks/" + id + "/state")`, and `pending` check treats `needs_action` as not-pending.

`finishWatched`: the existing code keys the "landing" behaviour on `task.status === "failed"` vs else, and on `result.needs_action`. Since `needs_action` is now a status, keep `result.needs_action` checks working (the result JSON still has that flag) — no logic change needed there beyond the status list. The `#resume-page` branch: change `resumeInboxItemId` → `data-task-id`, and on `done` POST to `/tasks/<id>/dismiss` is wrong (that dismisses); instead the follow-up action already completed the *origin* — just navigate away. Simplify: when `#resume-page` present and task `done`, hard-navigate to `referrer||"/"`. When `needs_action` (a chained next step), reload the detail page so the new panel shows.

- [ ] **Step 2: Status-bar "details" link → `/tasks/{id}`**

In `renderStatusBar`: `parts.push('<a href="/tasks/' + current.id + '">details</a>');` (was `/tasks/${id}/log`). If `current.group_id`, link `/tasks/group/<group_id>` instead.

- [ ] **Step 3: B1 — trigger disabled/reset**

In `onClick` (the `data-progress-url` handler), after `el.dataset.progressRunning = "1";` add:
```js
el.setAttribute("aria-disabled", "true");
el.classList.add("progress-trigger-busy");
```
Define a helper `function resetTrigger(el){ delete el.dataset.progressRunning; el.removeAttribute("aria-disabled"); el.classList.remove("progress-trigger-busy"); }` and call it everywhere the code currently does `delete el.dataset.progressRunning` (in `finishWatched`, `pollBatch`, the `.catch`, the `skipped` branch, the early `return`s for missing body fields).

CSS in `base.html`:
```css
.progress-trigger-busy { opacity: 0.55; pointer-events: none; }
@media (prefers-reduced-motion: no-preference) {
  .progress-trigger-busy { animation: trigger-pulse 1.2s ease-in-out infinite; }
}
@keyframes trigger-pulse { 0%,100% { box-shadow: 0 0 0 0 var(--accent); } 50% { box-shadow: 0 0 0 3px var(--accent-tint); } }
```
(This folds in the "optional pulse" — it's a pure CSS add, cheap. If it looks bad on the dev-server pass, delete the `@media` block and keep just the opacity.)

- [ ] **Step 4: Manual verification (dev server)**

Use the `run-dev-server` skill. Verify: (a) click "Fetch all" → button dims, status bar shows progress, card appears on Start; (b) a `source_detect` on Sources → needs-you card on Start, Review opens detail page with the panel, Confirm chains to the add step, Dismiss greys it; (c) status-bar "details" opens the new detail page. Record results in the task's commit message.

- [ ] **Step 5: Run the full suite + commit**

Run: `python -m pytest -q`
Expected: PASS.

```bash
git add app/templates/base.html
git commit -m "feat: poller uses /state, needs_action-aware watch, busy trigger state, detail-page status-bar link"
```

---

## Task 10: Collapse resolved needs-you panels to a decided summary

**Files:**
- Create: `app/templates/tasks/_resolved_panel.html`
- Modify: `app/routes/tasks.py` (detail page renders the resolved summary when `status in (done, dismissed)` and the task ever had `needs_action`), `app/templates/home/_task_card.html`
- Test: `tests/test_routes_tasks.py`

**Interfaces:**
- Consumes: `task_presentation` `results` + `_done_summary`.
- Produces: a `done`/`dismissed` task that previously needed action renders `tasks/_resolved_panel.html` (a one-line "✓ {summary} — {result link}" / "Dismissed") in place of the old form, on the detail page and the front card.

- [ ] **Step 1: Write failing tests**

```python
def test_resolved_needs_action_detail_shows_summary_not_form(client, conn):
    t = q.enqueue_task(conn, kind="source_detect", params={"url": "https://x.com"})
    q.complete_task(conn, t["id"], {"needs_action": True, "resume_html": "<form id='f'>x</form>"})
    q.set_task_needs_action(conn, t["id"])
    q.resolve_task(conn, t["id"])
    q.complete_task(conn, t["id"], {"source_id": None})  # follow-up wrote the terminal result
    # simulate: mark it done with a source
    sid = q.insert_source(conn, "X Careers", "https://x.com", "generic_listing")
    conn.execute("UPDATE tasks SET result = ? WHERE id = ?", ('{"source_id": %d}' % sid, t["id"]))
    conn.commit()
    html = client.get(f"/tasks/{t['id']}").text
    assert "<form id='f'>" not in html
    assert "Source 'X Careers' created" in html or "View source" in html

def test_dismissed_task_detail_says_dismissed(client, conn):
    t = q.enqueue_task(conn, kind="source_detect", params={})
    q.complete_task(conn, t["id"], {"needs_action": True, "resume_html": "<form>x</form>"})
    q.set_task_needs_action(conn, t["id"])
    q.dismiss_task(conn, t["id"])
    html = client.get(f"/tasks/{t['id']}").text
    assert "Dismissed" in html
    assert "<form>x</form>" not in html
```

- [ ] **Step 2: Run — verify fail** — FAIL.

- [ ] **Step 3: Implement**

`tasks/_resolved_panel.html`:
```html
<div class="resolved-panel">
  {% if task.status == "dismissed" %}
    <p>✓ Dismissed <span class="task-age">{{ task.finished_at | age }}</span></p>
  {% else %}
    <p>✓ {{ pres.next_step }}</p>
    {% if pres.results %}
    <p>{% for r in pres.results %}<a href="{{ r.href }}">{{ r.label }}</a>{% if not loop.last %} · {% endif %}{% endfor %}</p>
    {% endif %}
  {% endif %}
</div>
```

In `task_detail` route: compute `was_needs_action = (task.get("result") or {}).get("needs_action") is not None` (the flag stays in the persisted result even after resolve). Pass `resolved_panel = task["status"] in ("done", "dismissed") and was_needs_action`. In `detail.html`, replace the `{% if resume_html %}` block:
```html
{% if resume_html %}
  ... existing inline panel + Dismiss ...
{% elif resolved_panel %}
  {% include "tasks/_resolved_panel.html" %}
{% endif %}
```

In `home/_task_card.html`, for `task.status in ("done","dismissed")` when `task.result.needs_action` is set, render `_resolved_panel.html` instead of the plain results row.

- [ ] **Step 4: Run — verify pass** — `python -m pytest tests/test_routes_tasks.py tests/test_routes_home.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add app/templates/tasks/_resolved_panel.html app/templates/tasks/detail.html app/templates/home/_task_card.html app/routes/tasks.py tests/
git commit -m "feat: resolved needs-you panels collapse to a decided summary"
```

---

## Task 11: Proposal lists — decided-summary confirmation

**Files:**
- Modify: `app/templates/scenarios/_proposals.html`, `app/templates/profile/_proposals.html`, and the accept routes in `app/routes/scenarios.py` / `app/routes/profile.py` to return a brief confirmation fragment.
- Test: `tests/test_routes_scenarios.py`, `tests/test_routes_profile.py`

**Interfaces:**
- Consumes: existing accept-route return values (`#criteria-{id}` / `#profile-editor` swaps).
- Produces: after "Apply changes" / "Mark reviewed" / "Cancel", the proposals area shows a one-line decided summary (`"✓ 3 criteria added, 1 removed"` / `"✓ Reviewed — no changes"`) instead of just vanishing.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_routes_scenarios.py
def test_apply_criteria_proposals_shows_confirmation(client, conn):
    sid = q.create_scenario(conn, "S", "")
    # seed a proposal apply payload of one "add"
    resp = client.post(f"/scenarios/{sid}/refine/accept", data={
        "apply_0": "on", "kind_0": "add", "weight_0": "must", "text_0": "Remote OK",
        "feedback_anchor": "",
    })
    assert "criteria added" in resp.text or "✓" in resp.text

# tests/test_routes_profile.py
def test_apply_profile_proposals_shows_confirmation(client, conn):
    resp = client.post("/profile/refine/accept", data={"job_ids": "1"})
    assert "✓" in resp.text
```

(Adjust payload keys to the actual route parsing — check `scenarios.py` `refine_accept` / `profile.py` `refine_accept`.)

- [ ] **Step 2: Run — verify fail** — FAIL.

- [ ] **Step 3: Implement**

In each accept route, after applying, prepend a `<p class="save-confirmation">✓ …</p>` line to the returned fragment (the `.save-confirmation` class already exists in `base.html` with a fade-out animation). Compute counts from what was applied. For "Cancel"/"Mark reviewed" (profile no-op path), return `<p class="save-confirmation">✓ Reviewed — no changes</p>` + the editor.

Keep it minimal — no per-row state machine. The proposal `<form>` is already replaced by the target swap; this just makes the outcome legible.

- [ ] **Step 4: Run — verify pass** — `python -m pytest tests/test_routes_scenarios.py tests/test_routes_profile.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add app/templates/scenarios/_proposals.html app/templates/profile/_proposals.html app/routes/scenarios.py app/routes/profile.py tests/
git commit -m "feat: proposal apply/cancel shows a decided-summary confirmation"
```

---

## Task 12: Full-suite sweep + docs

**Files:**
- Modify: any test file with stale assertions; `BACKLOG.md` if it references the old task pages.
- Test: whole suite.

- [ ] **Step 1: Run the whole suite**

Run: `python -m pytest -q`
Expected: PASS. Fix stragglers (most likely: `test_routes_inbox.py`, `test_main.py`, any `test_queries.py` referencing deleted fns, `test_routes_jobs.py`/`test_routes_sources.py` asserting on `inbox` wording).

- [ ] **Step 2: Grep for orphans**

Run: `grep -rn "task_followup\|get_recent_resolved_inbox_items\|get_inbox_item_by_task_id\|/tasks/.*/log\|/tasks/.*/resume\|tasks/log.html\|tasks/resume.html\|pending_tasks\|recent_completed_tasks" app/ tests/`
Expected: no hits in `app/` (tests may keep redirect-assertion references).

- [ ] **Step 3: Dev-server pass**

`run-dev-server` skill. Walk the spec's Testing bullets end to end. Leave the server running and hand the URL to the user (UI change — per `CLAUDE.md`, wait for their go-ahead before offering to merge).

- [ ] **Step 4: Commit**

```bash
git add -A  # NOTE: repo root has untracked special files — if `git add -A` errors, stage explicit paths
git commit -m "test: sweep stale task/inbox assertions after async-task UX pass"
```

---

## Self-Review

**Spec coverage:**
- A. Data model — Task 1 (schema), Task 2 (queries). ✅
- B1 trigger states — Task 9 step 3 (busy/reset + optional pulse). ✅
- B2 collapse+toast+card — panel triggers already collapse via existing `.add-panel` logic; toast fires today; card is Task 8. The explicit "collapse on click" for `add-panel` triggers is **added in Task 9 step 3's `onClick`** — ⚠️ ADD: in Task 9, after marking the trigger busy, `var ap = el.closest(".add-panel"); if (ap) ap.classList.remove("add-panel-open");`. Folded into Task 9.
- B3 collapse action-spawned forms — Task 10 (needs-you panels), Task 11 (proposals). ✅
- C task_presentation — Task 4. ✅
- D front-page list + grouping + results + history — Tasks 5, 7, 8. ✅
- E detail page — Task 6. ✅
- F status bar link — Task 9 step 2. ✅
- Migration — Task 1. ✅
- Testing bullets — distributed; dev-server pass in Tasks 9 & 12.

**Placeholder scan:** `_title` in Task 4 step 3 says "exactly today's `_task_label` body — keep it" — that is a concrete instruction (move the existing function, rename), not a placeholder. The `task_history` route in Task 6 is explicitly a placeholder filled in Task 7 (noted inline). No other gaps.

**Type consistency:** `task_presentation` returns `{title, goal, next_step, results, link}` — consumed consistently in Tasks 5–11. `get_dashboard_tasks` entry shape `{is_group, group_id, tasks}` + `pres` added in `home.py` — consumed by `_task_card.html` / `_task_group.html`. `/tasks/{id}/state` is the poller path everywhere after Task 6. `origin_group_id` form field + `__ORIGIN_GROUP__` sentinel consistent between Task 5 route changes and panel templates.

**Fix applied inline:** B2 `add-panel` collapse-on-click folded into Task 9 step 3.
