# Multiple Named Base CVs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single hardcoded base CV with a named, listable, creatable, renamable, deletable collection of base CVs, shown as tabs on `/cv`, each job picking which one it tailors from.

**Architecture:** A new `base_cvs` table replaces `cv_settings` as the holder of "which CV version is current" for base-CV content; `cv_settings` shrinks to global tailoring settings only. `cv_versions.entity_id` (already generic) now points at `base_cvs.id` instead of a hardcoded `1`. `job_cv.base_cv_id` records which base CV a job tailors from. All base-CV routes move from `/cv/...` to `/cv/{base_cv_id}/...`, mirroring the existing `/jobs/{job_id}/cv/...` convention.

**Tech Stack:** FastAPI, SQLite (stdlib `sqlite3`), Jinja2, htmx, pytest.

## Global Constraints

- Migrations are hard-downtime rebuilds/ALTERs — no dual-shape compatibility handling (see spec's Migration section and this project's migration philosophy).
- FK enforcement (`PRAGMA foreign_keys = ON`) is already active on the app connection (`app/deps.py:16`) and the test `conn` fixture (`tests/conftest.py:13`) — `ON DELETE SET NULL` on `job_cv.base_cv_id` is real, not decorative.
- Global tailoring settings (`base_instruction`, `base_guardrails`, `css`, `default_scope`, `directives_template`) stay shared across all base CVs — only CV content and its version history become per-base-CV.
- Spec: `docs/superpowers/specs/2026-09-19-multiple-base-cvs-design.md`.

---

### Task 1: Schema — `base_cvs` table, `cv_settings`/`job_cv` column changes

**Files:**
- Modify: `app/db/schema.py`
- Test: `tests/test_schema.py`

**Interfaces:**
- Produces: table `base_cvs(id, name, current_version_id, created_at)`; `cv_settings` without `current_version_id`; `job_cv.base_cv_id INTEGER REFERENCES base_cvs(id) ON DELETE SET NULL`.

- [ ] **Step 1: Write the failing schema tests**

Add to `tests/test_schema.py` (near the other CV migration tests, after `test_init_db_migrates_existing_base_cv_and_tailored_cv_into_versions`):

```python
def test_base_cvs_table_and_updated_column_shapes():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(base_cvs)")}
    assert cols == {"id", "name", "current_version_id", "created_at"}
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(cv_settings)")}
    assert "current_version_id" not in cols
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(job_cv)")}
    assert "base_cv_id" in cols
    conn.close()


def test_init_db_migrates_singleton_cv_settings_into_base_cvs():
    # Simulate a pre-migration DB: cv_settings still has current_version_id,
    # no base_cvs table, no job_cv.base_cv_id.
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    # Roll cv_settings back to the pre-migration shape by dropping base_cvs
    # and re-adding current_version_id, so this test exercises the migration
    # itself rather than a hand-built fixture drifting from the real one.
    conn.execute("DROP TABLE base_cvs")
    conn.execute(
        "ALTER TABLE cv_settings ADD COLUMN current_version_id INTEGER REFERENCES cv_versions(id)"
    )
    conn.execute(
        "INSERT INTO cv_versions (hash, entity_type, entity_id, content, action) "
        "VALUES ('abc12345', 'base', 1, '# My CV', 'manual_edit')"
    )
    version_id = conn.execute(
        "SELECT id FROM cv_versions WHERE hash = 'abc12345'"
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO cv_settings (id, current_version_id, base_instruction, base_guardrails, "
        "css, default_scope, directives_template) VALUES (1, ?, '', '', '', '[]', '')",
        (version_id,),
    )
    conn.commit()

    init_db(conn)

    base = conn.execute("SELECT * FROM base_cvs WHERE id = 1").fetchone()
    assert base["name"] == "Default"
    assert base["current_version_id"] == version_id
    cols = {r[1] for r in conn.execute("PRAGMA table_info(cv_settings)")}
    assert "current_version_id" not in cols

    # Idempotent: running again must not raise or duplicate the row.
    init_db(conn)
    count = conn.execute("SELECT COUNT(*) FROM base_cvs").fetchone()[0]
    assert count == 1
    conn.close()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_schema.py -k "base_cvs" -v`
Expected: FAIL (`base_cvs` table doesn't exist / `current_version_id` still on `cv_settings` / migration function doesn't exist).

- [ ] **Step 3: Add `base_cvs` to `_DDL` and update `cv_settings`/`job_cv`**

In `app/db/schema.py`, add a new table right before `CREATE TABLE IF NOT EXISTS cv_settings` in `_DDL`:

```sql
CREATE TABLE IF NOT EXISTS base_cvs (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    current_version_id INTEGER REFERENCES cv_versions(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

Remove the `current_version_id INTEGER REFERENCES cv_versions(id),` line from the `cv_settings` definition in `_DDL` (schema.py:26-35).

Add `base_cv_id INTEGER REFERENCES base_cvs(id) ON DELETE SET NULL,` to the `job_cv` definition in `_DDL` (schema.py:46-66), right after `current_version_id INTEGER REFERENCES cv_versions(id),`.

- [ ] **Step 4: Add the two migration functions**

Add to `app/db/schema.py`, right after `_migrate_cv_versions_add_reset_and_note`:

```python
def _migrate_base_cvs_from_singleton(conn: sqlite3.Connection) -> None:
    """The base CV moves from the cv_settings singleton (current_version_id)
    into its own base_cvs row, named 'Default'. cv_versions rows for
    entity_type='base' already use entity_id=1, so they need no rewrite —
    they simply now belong to base_cvs.id=1. See the 2026-09-19 multiple
    base CVs spec."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(cv_settings)")}
    if "current_version_id" not in cols:
        return  # already migrated
    row = conn.execute("SELECT id, current_version_id FROM cv_settings WHERE id = 1").fetchone()
    if row is not None:
        conn.execute(
            "INSERT INTO base_cvs (id, name, current_version_id) VALUES (1, 'Default', ?)",
            (row[1],),
        )
    conn.execute("ALTER TABLE cv_settings DROP COLUMN current_version_id")
    conn.commit()


def _migrate_job_cv_add_base_cv_id(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(job_cv)")}
    if "base_cv_id" not in cols:
        conn.execute(
            "ALTER TABLE job_cv ADD COLUMN base_cv_id INTEGER REFERENCES base_cvs(id) ON DELETE SET NULL"
        )
        conn.commit()
```

Register both at the end of `init_db`, after `_migrate_cv_versions_add_reset_and_note(conn)`:

```python
    _migrate_base_cvs_from_singleton(conn)
    _migrate_job_cv_add_base_cv_id(conn)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_schema.py -k "base_cvs" -v`
Expected: PASS

- [ ] **Step 6: Run the full suite and commit**

Run: `uv run pytest -q 2>&1 | tail -40`
Expected: many failures elsewhere (every base-CV query function still assumes `cv_settings.current_version_id` and `entity_id=1`) — that's Task 2's job. Confirm the two new tests pass and no *new* schema-level errors appear beyond what's expected from the now-missing column.

```bash
git add app/db/schema.py tests/test_schema.py
git commit -m "feat(cv): base_cvs table, drop cv_settings.current_version_id, add job_cv.base_cv_id"
```

---

### Task 2: Query layer — `base_cvs` CRUD and re-scoping every base-CV function

**Files:**
- Modify: `app/db/queries.py`
- Test: `tests/test_cv_versions.py`, `tests/test_queries.py`

**Interfaces:**
- Consumes: `base_cvs` table, `job_cv.base_cv_id` (Task 1).
- Produces: `create_base_cv(conn, name, content="") -> int`, `rename_base_cv(conn, base_cv_id, name) -> None`, `list_base_cvs(conn) -> list[dict]`, `get_base_cv(conn, base_cv_id) -> dict | None`, `count_jobs_using_base_cv(conn, base_cv_id) -> int`, `delete_base_cv(conn, base_cv_id, force=False) -> bool` (raises `ValueError` on last-remaining), `resolve_job_base_cv_id(conn, job_id) -> int`. Re-scoped: `resolve_base_version_id(conn, base_cv_id)`, `resolve_base_diff_target(conn, base_cv_id, from_version_id=None)`, `get_accepted_base_version(conn, base_cv_id)`, `get_accepted_base_cv(conn, base_cv_id)`, `resolve_base_cv(conn, base_cv_id)`, `revert_base_cv_version(conn, base_cv_id, version_id)`, `accept_base_cv(conn, base_cv_id)`, `accept_base_cv_version(conn, base_cv_id, version_id)`, `set_base_cv(conn, base_cv_id, markdown)`. `save_cv_settings(...)` drops the `base_cv` parameter. `get_cv_settings(conn)` no longer returns `base_cv`/`accepted_at`/`current_version_id`/`current_version_hash`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cv_versions.py` (new tests, alongside the existing base-CV version tests — keep the module's `_age_base_versions` helper but repoint it at `entity_id = ?` with the base CV id under test):

```python
def test_create_list_rename_base_cv(conn):
    default_id = q.list_base_cvs(conn)[0]["id"]   # lazy-seeds "Default"
    assert q.list_base_cvs(conn) == [{"id": default_id, "name": "Default",
                                       "current_version_id": q.list_base_cvs(conn)[0]["current_version_id"],
                                       "created_at": q.list_base_cvs(conn)[0]["created_at"]}]

    second_id = q.create_base_cv(conn, "Backend", content="# Backend CV")
    names = {b["id"]: b["name"] for b in q.list_base_cvs(conn)}
    assert names == {default_id: "Default", second_id: "Backend"}
    assert q.get_base_cv(conn, second_id)["base_cv"] == "# Backend CV"

    q.rename_base_cv(conn, second_id, "Backend Engineer")
    assert q.get_base_cv(conn, second_id)["name"] == "Backend Engineer"


def test_create_base_cv_duplicate_name_raises(conn):
    q.list_base_cvs(conn)  # seeds "Default"
    with pytest.raises(sqlite3.IntegrityError):
        q.create_base_cv(conn, "Default")


def test_delete_base_cv_unreferenced_removes_row_and_versions(conn):
    default_id = q.list_base_cvs(conn)[0]["id"]
    second_id = q.create_base_cv(conn, "Backend", content="# Backend CV")
    q.set_base_cv(conn, second_id, "# Backend CV v2")  # two versions

    assert q.delete_base_cv(conn, second_id) is True
    assert q.get_base_cv(conn, second_id) is None
    assert q.get_versions(conn, "base", second_id) == []
    assert [b["id"] for b in q.list_base_cvs(conn)] == [default_id]


def test_delete_base_cv_referenced_without_force_is_refused(conn):
    default_id = q.list_base_cvs(conn)[0]["id"]
    second_id = q.create_base_cv(conn, "Backend")
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, base_cv_id=second_id)

    assert q.count_jobs_using_base_cv(conn, second_id) == 1
    assert q.delete_base_cv(conn, second_id) is False
    assert q.get_base_cv(conn, second_id) is not None  # untouched


def test_delete_base_cv_referenced_with_force_deletes_and_nulls_job_link(conn):
    second_id = q.create_base_cv(conn, "Backend")
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, base_cv_id=second_id)

    assert q.delete_base_cv(conn, second_id, force=True) is True
    assert q.get_base_cv(conn, second_id) is None
    assert q.get_job_cv(conn, jid)["base_cv_id"] is None  # ON DELETE SET NULL


def test_delete_base_cv_refuses_last_remaining(conn):
    default_id = q.list_base_cvs(conn)[0]["id"]
    with pytest.raises(ValueError):
        q.delete_base_cv(conn, default_id, force=True)
    assert q.get_base_cv(conn, default_id) is not None


def test_resolve_job_base_cv_id_falls_back_to_default(conn):
    default_id = q.list_base_cvs(conn)[0]["id"]
    jid = _job(conn)
    assert q.resolve_job_base_cv_id(conn, jid) == default_id  # no job_cv row yet

    second_id = q.create_base_cv(conn, "Backend")
    q.upsert_job_cv(conn, jid, base_cv_id=second_id)
    assert q.resolve_job_base_cv_id(conn, jid) == second_id
```

`pytest` needs importing in `tests/test_cv_versions.py` for `pytest.raises` — add `import pytest` and `import sqlite3` at the top if not already present (check first).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cv_versions.py -k "base_cv" -v`
Expected: FAIL (`list_base_cvs`/`create_base_cv`/etc. don't exist yet).

- [ ] **Step 3: Add `base_cvs` CRUD to `app/db/queries.py`**

Add near `set_base_cv` (queries.py:347), replacing the old singleton-only `set_base_cv`:

```python
def list_base_cvs(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM base_cvs ORDER BY id").fetchall()
    if not rows:
        create_base_cv(conn, "Default", DEFAULT_BASE_CV)
        rows = conn.execute("SELECT * FROM base_cvs ORDER BY id").fetchall()
    return [dict(r) for r in rows]


_BASE_CV_SELECT = """
    SELECT base_cvs.*, cv_versions.content AS base_cv,
           cv_versions.accepted_at AS accepted_at,
           cv_versions.hash AS current_version_hash
    FROM base_cvs LEFT JOIN cv_versions ON cv_versions.id = base_cvs.current_version_id
    WHERE base_cvs.id = ?
"""


def get_base_cv(conn: sqlite3.Connection, base_cv_id: int) -> dict | None:
    row = conn.execute(_BASE_CV_SELECT, (base_cv_id,)).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["base_cv"] = d["base_cv"] or ""
    return d


def create_base_cv(conn: sqlite3.Connection, name: str, content: str = "") -> int:
    cur = conn.execute("INSERT INTO base_cvs (name) VALUES (?)", (name,))
    base_cv_id = cur.lastrowid
    conn.commit()
    if content:
        set_base_cv(conn, base_cv_id, content)
    return base_cv_id


def rename_base_cv(conn: sqlite3.Connection, base_cv_id: int, name: str) -> None:
    conn.execute("UPDATE base_cvs SET name = ? WHERE id = ?", (name, base_cv_id))
    conn.commit()


def count_jobs_using_base_cv(conn: sqlite3.Connection, base_cv_id: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM job_cv WHERE base_cv_id = ?", (base_cv_id,)
    ).fetchone()[0]


def delete_base_cv(conn: sqlite3.Connection, base_cv_id: int, force: bool = False) -> bool:
    """Deletes a base CV and all its versions. Returns False (nothing
    deleted) if jobs were tailored from it and force is not set. Raises
    ValueError if this is the last remaining base CV — never returns False
    for that case, since no amount of force should make it possible."""
    if len(list_base_cvs(conn)) <= 1:
        raise ValueError("Can't delete the last remaining base CV")
    if count_jobs_using_base_cv(conn, base_cv_id) and not force:
        return False
    # base_cvs first: it holds current_version_id, the FK-referencing side —
    # same ordering as delete_job/cv_versions (see delete_job's comment).
    conn.execute("DELETE FROM base_cvs WHERE id = ?", (base_cv_id,))
    conn.execute("DELETE FROM cv_versions WHERE entity_type = 'base' AND entity_id = ?", (base_cv_id,))
    conn.commit()
    return True


def resolve_job_base_cv_id(conn: sqlite3.Connection, job_id: int) -> int:
    """Which base CV a job tailors from — its own choice if set (via
    upsert_job_cv(base_cv_id=...)), else the lowest-id base CV, so a job
    that's never picked one behaves exactly like the old singleton did."""
    row = conn.execute("SELECT base_cv_id FROM job_cv WHERE job_id = ?", (job_id,)).fetchone()
    if row and row[0] is not None:
        return row[0]
    return list_base_cvs(conn)[0]["id"]


def set_base_cv(conn: sqlite3.Connection, base_cv_id: int, markdown: str) -> None:
    """Autosave entry point for a base CV's Edit tab — content only.
    Mirrors set_job_cv_tailored."""
    current_row = conn.execute(
        "SELECT current_version_id FROM base_cvs WHERE id = ?", (base_cv_id,)
    ).fetchone()
    current_version_id = current_row[0] if current_row else None
    new_version_id = _record_version(
        conn, "base", base_cv_id, action="manual_edit", content=markdown,
        current_version_id=current_version_id,
    )
    conn.execute(
        "UPDATE base_cvs SET current_version_id = ?, updated_at = datetime('now') WHERE id = ?",
        (new_version_id, base_cv_id),
    )
    conn.commit()
```

Delete the old `set_base_cv(conn, markdown)` definition (queries.py:347-362).

- [ ] **Step 4: Re-scope the remaining base-CV functions to take `base_cv_id`**

In `app/db/queries.py`, replace each of the following (exact old code shown, replace with the new code):

Old (`resolve_base_version_id`, queries.py:152-164):
```python
def resolve_base_version_id(conn: sqlite3.Connection) -> int | None:
    """The id of whichever base-CV version resolve_base_cv() would return the
    content of — the accepted one, or the current one if nothing's accepted
    yet. Used to link a job's first tailored version back to the base
    version it was generated from."""
    row = conn.execute(
        "SELECT id FROM cv_versions WHERE entity_type = 'base' AND entity_id = 1 "
        "AND accepted_at IS NOT NULL ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is not None:
        return row[0]
    current = conn.execute("SELECT current_version_id FROM cv_settings WHERE id = 1").fetchone()
    return current[0] if current else None
```
New:
```python
def resolve_base_version_id(conn: sqlite3.Connection, base_cv_id: int) -> int | None:
    """The id of whichever base-CV version resolve_base_cv() would return the
    content of — the accepted one, or the current one if nothing's accepted
    yet. Used to link a job's first tailored version back to the base
    version it was generated from."""
    row = conn.execute(
        "SELECT id FROM cv_versions WHERE entity_type = 'base' AND entity_id = ? "
        "AND accepted_at IS NOT NULL ORDER BY id DESC LIMIT 1", (base_cv_id,)
    ).fetchone()
    if row is not None:
        return row[0]
    current = conn.execute(
        "SELECT current_version_id FROM base_cvs WHERE id = ?", (base_cv_id,)
    ).fetchone()
    return current[0] if current else None
```

Old (`resolve_base_diff_target`, queries.py:167-186):
```python
def resolve_base_diff_target(conn: sqlite3.Connection, from_version_id: int | None = None) -> int | None:
    """Default diff-against target for the base CV's *own* Differences tab
    (comparing an edit against something, not resolve_base_version_id()'s
    "what does tailoring use" question). The accepted version if one exists;
    otherwise the parent of `from_version_id` (the version currently shown —
    the live current one, if not given), since diffing a version against
    itself is a no-op. None when there's truly nothing to compare against
    yet: a single, never-accepted version."""
    accepted = get_accepted_base_version(conn)
    if accepted is not None:
        return accepted["id"]
    if from_version_id is None:
        current = conn.execute("SELECT current_version_id FROM cv_settings WHERE id = 1").fetchone()
        from_version_id = current[0] if current else None
    if from_version_id is None:
        return None
    row = conn.execute(
        "SELECT parent_version_id FROM cv_versions WHERE id = ?", (from_version_id,)
    ).fetchone()
    return row[0] if row else None
```
New:
```python
def resolve_base_diff_target(
    conn: sqlite3.Connection, base_cv_id: int, from_version_id: int | None = None,
) -> int | None:
    """Default diff-against target for the base CV's *own* Differences tab
    (comparing an edit against something, not resolve_base_version_id()'s
    "what does tailoring use" question). The accepted version if one exists;
    otherwise the parent of `from_version_id` (the version currently shown —
    the live current one, if not given), since diffing a version against
    itself is a no-op. None when there's truly nothing to compare against
    yet: a single, never-accepted version."""
    accepted = get_accepted_base_version(conn, base_cv_id)
    if accepted is not None:
        return accepted["id"]
    if from_version_id is None:
        current = conn.execute(
            "SELECT current_version_id FROM base_cvs WHERE id = ?", (base_cv_id,)
        ).fetchone()
        from_version_id = current[0] if current else None
    if from_version_id is None:
        return None
    row = conn.execute(
        "SELECT parent_version_id FROM cv_versions WHERE id = ?", (from_version_id,)
    ).fetchone()
    return row[0] if row else None
```

Old (`get_accepted_base_version`, queries.py:189-201):
```python
def get_accepted_base_version(conn: sqlite3.Connection) -> dict | None:
    """The accepted base-CV version row (whichever one it is, whether or not
    it's also current), or None if nothing has been accepted yet. Unlike
    cv_settings.accepted_at (joined through current_version_id — see
    _CV_SETTINGS_SELECT below), this reflects acceptance independent of
    what's current, so the UI can tell "nothing accepted" apart from "a
    different, non-current version is accepted"."""
    row = conn.execute(
        _VERSION_SELECT_WITH_PARENT
        + "WHERE v.entity_type = 'base' AND v.entity_id = 1 AND v.accepted_at IS NOT NULL "
        "ORDER BY v.id DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row is not None else None
```
New:
```python
def get_accepted_base_version(conn: sqlite3.Connection, base_cv_id: int) -> dict | None:
    """The accepted base-CV version row (whichever one it is, whether or not
    it's also current), or None if nothing has been accepted yet. Unlike
    get_base_cv's accepted_at (joined through current_version_id), this
    reflects acceptance independent of what's current, so the UI can tell
    "nothing accepted" apart from "a different, non-current version is
    accepted"."""
    row = conn.execute(
        _VERSION_SELECT_WITH_PARENT
        + "WHERE v.entity_type = 'base' AND v.entity_id = ? AND v.accepted_at IS NOT NULL "
        "ORDER BY v.id DESC LIMIT 1", (base_cv_id,)
    ).fetchone()
    return dict(row) if row is not None else None
```

Old (`get_accepted_base_cv`, `resolve_base_cv`, queries.py:204-217):
```python
def get_accepted_base_cv(conn: sqlite3.Connection) -> str | None:
    """The content of the accepted base-CV version, or None if nothing has
    been accepted yet."""
    version = get_accepted_base_version(conn)
    return version["content"] if version is not None else None


def resolve_base_cv(conn: sqlite3.Connection) -> str:
    """The base CV as tailoring/diffing see it: the accepted version, or the
    current one if nothing has been accepted yet."""
    accepted = get_accepted_base_cv(conn)
    if accepted is not None:
        return accepted
    return get_cv_settings(conn)["base_cv"]
```
New:
```python
def get_accepted_base_cv(conn: sqlite3.Connection, base_cv_id: int) -> str | None:
    """The content of the accepted base-CV version, or None if nothing has
    been accepted yet."""
    version = get_accepted_base_version(conn, base_cv_id)
    return version["content"] if version is not None else None


def resolve_base_cv(conn: sqlite3.Connection, base_cv_id: int) -> str:
    """The base CV as tailoring/diffing see it: the accepted version, or the
    current one if nothing has been accepted yet."""
    accepted = get_accepted_base_cv(conn, base_cv_id)
    if accepted is not None:
        return accepted
    base = get_base_cv(conn, base_cv_id)
    return base["base_cv"] if base else ""
```

Old (`revert_base_cv_version`, queries.py:257-265):
```python
def revert_base_cv_version(conn: sqlite3.Connection, version_id: int) -> bool:
    if get_version(conn, "base", 1, version_id) is None:
        return False
    conn.execute(
        "UPDATE cv_settings SET current_version_id = ?, updated_at = datetime('now') WHERE id = 1",
        (version_id,),
    )
    conn.commit()
    return True
```
New:
```python
def revert_base_cv_version(conn: sqlite3.Connection, base_cv_id: int, version_id: int) -> bool:
    if get_version(conn, "base", base_cv_id, version_id) is None:
        return False
    conn.execute(
        "UPDATE base_cvs SET current_version_id = ?, updated_at = datetime('now') WHERE id = ?",
        (version_id, base_cv_id),
    )
    conn.commit()
    return True
```

Old (`accept_base_cv`, `accept_base_cv_version`, queries.py:268-289):
```python
def accept_base_cv(conn: sqlite3.Connection) -> None:
    conn.execute(
        "UPDATE cv_versions SET accepted_at = NULL WHERE entity_type = 'base' AND entity_id = 1 "
        "AND accepted_at IS NOT NULL"
    )
    conn.execute(
        "UPDATE cv_versions SET accepted_at = datetime('now') WHERE id = "
        "(SELECT current_version_id FROM cv_settings WHERE id = 1)"
    )
    conn.commit()


def accept_base_cv_version(conn: sqlite3.Connection, version_id: int) -> None:
    """Accept a specific base-CV version directly, independent of whatever
    current_version_id happens to point at — lets the read-only version view
    accept a historic version without reopening it first."""
    conn.execute(
        "UPDATE cv_versions SET accepted_at = NULL WHERE entity_type = 'base' AND entity_id = 1 "
        "AND accepted_at IS NOT NULL"
    )
    conn.execute("UPDATE cv_versions SET accepted_at = datetime('now') WHERE id = ?", (version_id,))
    conn.commit()
```
New:
```python
def accept_base_cv(conn: sqlite3.Connection, base_cv_id: int) -> None:
    conn.execute(
        "UPDATE cv_versions SET accepted_at = NULL WHERE entity_type = 'base' AND entity_id = ? "
        "AND accepted_at IS NOT NULL", (base_cv_id,)
    )
    conn.execute(
        "UPDATE cv_versions SET accepted_at = datetime('now') WHERE id = "
        "(SELECT current_version_id FROM base_cvs WHERE id = ?)", (base_cv_id,)
    )
    conn.commit()


def accept_base_cv_version(conn: sqlite3.Connection, base_cv_id: int, version_id: int) -> None:
    """Accept a specific base-CV version directly, independent of whatever
    current_version_id happens to point at — lets the read-only version view
    accept a historic version without reopening it first."""
    conn.execute(
        "UPDATE cv_versions SET accepted_at = NULL WHERE entity_type = 'base' AND entity_id = ? "
        "AND accepted_at IS NOT NULL", (base_cv_id,)
    )
    conn.execute("UPDATE cv_versions SET accepted_at = datetime('now') WHERE id = ?", (version_id,))
    conn.commit()
```

- [ ] **Step 5: Split `save_cv_settings` / `get_cv_settings`**

Old (queries.py:292-345, `_CV_SETTINGS_SELECT`, `get_cv_settings`, `save_cv_settings`):
```python
_CV_SETTINGS_SELECT = """
    SELECT cv_settings.*, cv_versions.content AS base_cv,
           cv_versions.accepted_at AS accepted_at,
           cv_versions.hash AS current_version_hash
    FROM cv_settings LEFT JOIN cv_versions ON cv_versions.id = cv_settings.current_version_id
    WHERE cv_settings.id = 1
"""


def get_cv_settings(conn: sqlite3.Connection) -> dict:
    row = conn.execute(_CV_SETTINGS_SELECT).fetchone()
    if row is None:
        save_cv_settings(
            conn, base_cv=DEFAULT_BASE_CV, base_instruction="",
            base_guardrails=DEFAULT_GUARDRAILS, css="", default_scope=["select", "reorder"],
            directives_template=DEFAULT_DIRECTIVES_TEMPLATE,
        )
        row = conn.execute(_CV_SETTINGS_SELECT).fetchone()
    d = dict(row)
    d["base_cv"] = d["base_cv"] or ""
    d["default_scope"] = json.loads(d["default_scope"])
    return d


def save_cv_settings(
    conn: sqlite3.Connection, *, base_cv: str, base_instruction: str,
    base_guardrails: str, css: str, default_scope: list[str],
    directives_template: str = "",
) -> None:
    current_row = conn.execute("SELECT current_version_id FROM cv_settings WHERE id = 1").fetchone()
    current_version_id = current_row[0] if current_row else None
    new_version_id = _record_version(
        conn, "base", 1, action="manual_edit", content=base_cv,
        current_version_id=current_version_id,
    )
    conn.execute(
        """
        INSERT INTO cv_settings
            (id, current_version_id, base_instruction, base_guardrails, css, default_scope, directives_template)
        VALUES (1, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            current_version_id = excluded.current_version_id,
            base_instruction = excluded.base_instruction,
            base_guardrails = excluded.base_guardrails,
            css = excluded.css,
            default_scope = excluded.default_scope,
            directives_template = excluded.directives_template,
            updated_at = datetime('now')
        """,
        (new_version_id, base_instruction, base_guardrails, css, json.dumps(default_scope),
         directives_template),
    )
    conn.commit()
```
New:
```python
_CV_SETTINGS_SELECT = "SELECT * FROM cv_settings WHERE id = 1"


def get_cv_settings(conn: sqlite3.Connection) -> dict:
    row = conn.execute(_CV_SETTINGS_SELECT).fetchone()
    if row is None:
        save_cv_settings(
            conn, base_instruction="", base_guardrails=DEFAULT_GUARDRAILS, css="",
            default_scope=["select", "reorder"], directives_template=DEFAULT_DIRECTIVES_TEMPLATE,
        )
        row = conn.execute(_CV_SETTINGS_SELECT).fetchone()
    d = dict(row)
    d["default_scope"] = json.loads(d["default_scope"])
    return d


def save_cv_settings(
    conn: sqlite3.Connection, *, base_instruction: str, base_guardrails: str, css: str,
    default_scope: list[str], directives_template: str = "",
) -> None:
    conn.execute(
        """
        INSERT INTO cv_settings (id, base_instruction, base_guardrails, css, default_scope, directives_template)
        VALUES (1, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            base_instruction = excluded.base_instruction,
            base_guardrails = excluded.base_guardrails,
            css = excluded.css,
            default_scope = excluded.default_scope,
            directives_template = excluded.directives_template,
            updated_at = datetime('now')
        """,
        (base_instruction, base_guardrails, css, json.dumps(default_scope), directives_template),
    )
    conn.commit()
```

`DEFAULT_BASE_CV` is still imported (now used by `list_base_cvs`'s lazy seed) — leave the import line as-is.

- [ ] **Step 6: Re-scope `upsert_job_cv` and `reset_job_cv_to_base` to use `resolve_job_base_cv_id`**

In `upsert_job_cv` (queries.py:458-486), change:
```python
        encoded["current_version_id"] = _record_version(
            conn, "tailored", job_id, action="update", content=tailored_cv,
            current_version_id=current_version_id,
            initial_parent_id=resolve_base_version_id(conn),
            note=note,
        )
```
to:
```python
        encoded["current_version_id"] = _record_version(
            conn, "tailored", job_id, action="update", content=tailored_cv,
            current_version_id=current_version_id,
            initial_parent_id=resolve_base_version_id(conn, resolve_job_base_cv_id(conn, job_id)),
            note=note,
        )
```

In `reset_job_cv_to_base` (queries.py:517-551), change:
```python
    base_content = resolve_base_cv(conn)
    # resolve_base_cv and resolve_base_version_id independently resolve "the
    # base version" -- they're expected to agree since both run against the
    # same connection with no intervening write (this app is single-instance,
    # non-concurrent).
    new_version_id = _record_version(
        conn, "tailored", job_id, action="reset", content=base_content,
        current_version_id=current_version_id,
        parent_id_override=resolve_base_version_id(conn),
    )
```
to:
```python
    job_base_cv_id = resolve_job_base_cv_id(conn, job_id)
    base_content = resolve_base_cv(conn, job_base_cv_id)
    # resolve_base_cv and resolve_base_version_id independently resolve "the
    # base version" -- they're expected to agree since both run against the
    # same connection with no intervening write (this app is single-instance,
    # non-concurrent).
    new_version_id = _record_version(
        conn, "tailored", job_id, action="reset", content=base_content,
        current_version_id=current_version_id,
        parent_id_override=resolve_base_version_id(conn, job_base_cv_id),
    )
```

- [ ] **Step 7: Run the new tests, then the full query-layer test files**

Run: `uv run pytest tests/test_cv_versions.py tests/test_queries.py -v 2>&1 | tail -80`
Expected: the new Step-1 tests PASS. Many *existing* tests in these files will now FAIL (they call the old `save_cv_settings(..., base_cv=...)` signature or the old unscoped base-CV functions) — that's expected here; Task 7 sweeps the whole test suite. Confirm the failures are all `TypeError: ... unexpected keyword argument 'base_cv'` / `missing 1 required positional argument` (signature mismatches), not something else.

- [ ] **Step 8: Commit**

```bash
git add app/db/queries.py tests/test_cv_versions.py
git commit -m "feat(cv): base_cvs CRUD, re-scope base-CV queries off the cv_settings singleton"
```

---

### Task 3: Routes — `/cv/{base_cv_id}/...`, create/rename/delete, `cv_versions.py`

**Files:**
- Create: `app/templates/cv/_base_cv_tabs.html`
- Modify: `app/routes/cv.py`, `app/routes/cv_versions.py`
- Test: `tests/test_routes_cv_settings.py`, `tests/test_routes_cv_versions.py`

**Interfaces:**
- Consumes: Task 2's `list_base_cvs`, `get_base_cv`, `create_base_cv`, `rename_base_cv`, `delete_base_cv`, `count_jobs_using_base_cv`, and the re-scoped `resolve_base_diff_target`/`get_accepted_base_version`/`resolve_base_cv`/`revert_base_cv_version`/`accept_base_cv`/`accept_base_cv_version`.
- Produces: `GET /cv` (redirect), `GET/POST /cv/{base_cv_id}`, `POST /cv` (create), `POST /cv/{base_cv_id}/rename`, `DELETE /cv/{base_cv_id}`, `GET /cv/{base_cv_id}/preview.html`, `GET /cv/{base_cv_id}/diff.html`, `GET /cv/{base_cv_id}.pdf`, `POST /cv/{base_cv_id}/save-base`. `cv_versions.py`: `POST /cv/{base_cv_id}/versions/{version_id}/revert`, `POST /cv/{base_cv_id}/versions/{version_id}/accept`, `POST /cv/{base_cv_id}/accept`. `cv/_base_cv_tabs.html` partial (rendered by the rename/delete routes; Task 5 wires it into `cv/index.html` and re-scopes the remaining base-CV templates' URLs).

This task is self-contained and independently testable: `cv/_base_cv_tabs.html` (Step 6, below) is written here — not in Task 5 — precisely because the rename/delete routes render it directly, so Task 3's own tests need it to exist. `cv_page`'s full-page render (`cv/index.html`) isn't touched until Task 5, so `test_cv_page_404s_for_unknown_base_cv` and similar checks below only assert on status codes, not on `index.html`'s content.

- [ ] **Step 1: Write the failing route tests**

Add to `tests/test_routes_cv_settings.py` (new tests):

```python
def test_cv_root_redirects_to_the_first_base_cv(client, conn):
    base_cv_id = q.list_base_cvs(conn)[0]["id"]
    r = client.get("/cv", follow_redirects=False)
    assert r.status_code in (302, 303, 307)
    assert r.headers["location"] == f"/cv/{base_cv_id}"


def test_cv_page_404s_for_unknown_base_cv(client):
    r = client.get("/cv/999")
    assert r.status_code == 404


def test_create_base_cv_redirects_to_new_tab(client, conn):
    r = client.post("/cv", data={"name": "Backend"}, follow_redirects=False)
    assert r.status_code == 303
    new_id = q.list_base_cvs(conn)[-1]["id"]
    assert q.list_base_cvs(conn)[-1]["name"] == "Backend"
    assert r.headers["location"] == f"/cv/{new_id}"


def test_create_base_cv_requires_a_name(client):
    r = client.post("/cv", data={"name": "  "})
    assert r.status_code == 400


def test_rename_base_cv(client, conn):
    base_cv_id = q.list_base_cvs(conn)[0]["id"]
    r = client.post(f"/cv/{base_cv_id}/rename", data={"name": "My Main CV"})
    assert r.status_code == 200
    assert q.get_base_cv(conn, base_cv_id)["name"] == "My Main CV"


def test_rename_base_cv_duplicate_name_conflicts(client, conn):
    base_cv_id = q.list_base_cvs(conn)[0]["id"]
    q.create_base_cv(conn, "Backend")
    r = client.post(f"/cv/{base_cv_id}/rename", data={"name": "Backend"})
    assert r.status_code == 409


def test_delete_unreferenced_base_cv_succeeds(client, conn):
    base_cv_id = q.list_base_cvs(conn)[0]["id"]
    second_id = q.create_base_cv(conn, "Backend")
    r = client.delete(f"/cv/{second_id}", params={"active": base_cv_id})
    assert r.status_code == 200
    assert q.get_base_cv(conn, second_id) is None


def test_delete_referenced_base_cv_requires_force(client, conn):
    second_id = q.create_base_cv(conn, "Backend")
    conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s','http://x','manual')")
    cur = conn.execute("INSERT INTO jobs (source_id, url) VALUES (1, 'http://x/1')")
    conn.commit()
    jid = cur.lastrowid
    q.upsert_job_cv(conn, jid, base_cv_id=second_id)

    r = client.delete(f"/cv/{second_id}")
    assert r.status_code == 200  # confirm fragment, not deleted
    assert q.get_base_cv(conn, second_id) is not None

    r = client.delete(f"/cv/{second_id}", params={"force": "true"})
    assert r.status_code == 200
    assert q.get_base_cv(conn, second_id) is None


def test_delete_last_remaining_base_cv_refused(client, conn):
    base_cv_id = q.list_base_cvs(conn)[0]["id"]
    r = client.delete(f"/cv/{base_cv_id}")
    assert r.status_code == 400
    assert q.get_base_cv(conn, base_cv_id) is not None


def test_delete_active_base_cv_redirects_via_hx_redirect(client, conn):
    base_cv_id = q.list_base_cvs(conn)[0]["id"]
    second_id = q.create_base_cv(conn, "Backend")
    r = client.delete(f"/cv/{second_id}", params={"active": second_id})
    assert r.status_code == 200
    assert r.headers.get("HX-Redirect") == f"/cv/{base_cv_id}"
```

Add to `tests/test_routes_cv_versions.py` (new tests, alongside the existing base-CV accept/revert tests — update those existing tests' URLs too, per Task 7):

```python
def test_revert_base_version_is_scoped_to_its_base_cv(client, conn):
    base_cv_id = q.list_base_cvs(conn)[0]["id"]
    q.set_base_cv(conn, base_cv_id, "v1")
    q.accept_base_cv(conn, base_cv_id)
    q.set_base_cv(conn, base_cv_id, "v2")
    v1_id = q.get_accepted_base_version(conn, base_cv_id)["id"]

    r = client.post(f"/cv/{base_cv_id}/versions/{v1_id}/revert", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == f"/cv/{base_cv_id}"
    assert q.get_base_cv(conn, base_cv_id)["base_cv"] == "v1"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_routes_cv_settings.py tests/test_routes_cv_versions.py -k "base_cv or delete or rename or create or revert_base" -v`
Expected: FAIL (routes not yet scoped/created — 404s or old unscoped route shape).

- [ ] **Step 3: Rewrite the base-CV routes in `app/routes/cv.py`**

Replace `_cv_page_ctx` (cv.py:200-216):
```python
def _cv_page_ctx(
    conn: sqlite3.Connection, base_cv_id: int, against: int | None = None,
    viewing_version_id: int | None = None,
) -> dict:
    base = q.get_base_cv(conn, base_cv_id)
    return {
        "settings": q.get_cv_settings(conn),
        "base": base,
        "base_cvs": q.list_base_cvs(conn),
        "has_doc_write": doc_write_available(),
        "app_version": get_app_version(),
        "build_date": get_build_date(),
        "versions": q.get_versions(conn, "base", base_cv_id),
        "current_version_id": base.get("current_version_id") if base else None,
        "accepted_version": q.get_accepted_base_version(conn, base_cv_id),
        "diff_against_id": (
            against if against is not None
            else q.resolve_base_diff_target(conn, base_cv_id, viewing_version_id)
        ),
        "version_base_url": f"/cv/{base_cv_id}",
        "is_base": True,
    }
```

Replace the base-CV route block (cv.py:445-544 — `cv_page` through `cv_base_pdf`) with:
```python
@router.get("/cv", response_class=HTMLResponse)
def cv_page_root(conn: sqlite3.Connection = Depends(get_db)):
    base_cvs = q.list_base_cvs(conn)
    return RedirectResponse(f"/cv/{base_cvs[0]['id']}", status_code=303)


@router.post("/cv", response_class=HTMLResponse)
async def cv_create_base(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    form = await request.form()
    name = form.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")
    try:
        base_cv_id = q.create_base_cv(conn, name)
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="A base CV with that name already exists")
    return RedirectResponse(f"/cv/{base_cv_id}", status_code=303)


@router.get("/cv/{base_cv_id}", response_class=HTMLResponse)
def cv_page(base_cv_id: int, request: Request, version: int | None = None,
           against: int | None = None, conn: sqlite3.Connection = Depends(get_db)):
    if q.get_base_cv(conn, base_cv_id) is None:
        raise HTTPException(status_code=404, detail="Base CV not found")
    if against is not None and q.get_version(conn, "base", base_cv_id, against) is None:
        raise HTTPException(status_code=404, detail="Version not found")
    ctx = _cv_page_ctx(conn, base_cv_id, against=against, viewing_version_id=version)
    # An explicit diff target in the URL means the diff-picker was just used
    # to pick it — land on the Differences tab instead of resetting to
    # Preview underneath a picker that now shows a different target.
    ctx["active"] = "diff" if against is not None else "preview"
    if version is not None:
        v = q.get_version(conn, "base", base_cv_id, version)
        if v is None:
            raise HTTPException(status_code=404, detail="Version not found")
        ctx["viewing_version"] = v
    return templates.TemplateResponse(request, "cv/index.html", ctx)


@router.post("/cv/{base_cv_id}/rename", response_class=HTMLResponse)
async def cv_rename_base(base_cv_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    if q.get_base_cv(conn, base_cv_id) is None:
        raise HTTPException(status_code=404, detail="Base CV not found")
    form = await request.form()
    name = form.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")
    try:
        q.rename_base_cv(conn, base_cv_id, name)
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="A base CV with that name already exists")
    return templates.TemplateResponse(
        request, "cv/_base_cv_tabs.html",
        {"base_cvs": q.list_base_cvs(conn), "active_base_cv_id": base_cv_id, "confirming_delete_id": None},
    )


@router.delete("/cv/{base_cv_id}", response_class=HTMLResponse)
def cv_delete_base(base_cv_id: int, request: Request, force: bool = False,
                   active: int | None = None, conn: sqlite3.Connection = Depends(get_db)):
    if q.get_base_cv(conn, base_cv_id) is None:
        raise HTTPException(status_code=404, detail="Base CV not found")
    try:
        deleted = q.delete_base_cv(conn, base_cv_id, force=force)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not deleted:
        return templates.TemplateResponse(
            request, "cv/_base_cv_tabs.html",
            {"base_cvs": q.list_base_cvs(conn), "active_base_cv_id": active or base_cv_id,
             "confirming_delete_id": base_cv_id,
             "referencing_count": q.count_jobs_using_base_cv(conn, base_cv_id)},
        )
    remaining = q.list_base_cvs(conn)
    if active == base_cv_id:
        return HTMLResponse(content="", headers={"HX-Redirect": f"/cv/{remaining[0]['id']}"})
    return templates.TemplateResponse(
        request, "cv/_base_cv_tabs.html",
        {"base_cvs": remaining, "active_base_cv_id": active, "confirming_delete_id": None},
    )


@router.post("/cv/{base_cv_id}/save-base", response_class=HTMLResponse)
async def cv_save_base(base_cv_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    if q.get_base_cv(conn, base_cv_id) is None:
        raise HTTPException(status_code=404, detail="Base CV not found")
    form = await request.form()
    q.set_base_cv(conn, base_cv_id, form.get("markdown", ""))
    return templates.TemplateResponse(
        request, "cv/_save_base_oob.html", _cv_page_ctx(conn, base_cv_id),
    )


@router.get("/cv/{base_cv_id}/preview.html", response_class=HTMLResponse)
def cv_preview_base_html(base_cv_id: int, version: int | None = None,
                         conn: sqlite3.Connection = Depends(get_db)):
    base = q.get_base_cv(conn, base_cv_id)
    if base is None:
        raise HTTPException(status_code=404, detail="Base CV not found")
    content = base["base_cv"]
    if version is not None:
        v = q.get_version(conn, "base", base_cv_id, version)
        if v is None:
            raise HTTPException(status_code=404, detail="Version not found")
        content = v["content"]
    settings = q.get_cv_settings(conn)
    if not doc_write_available():
        raise HTTPException(status_code=503, detail="doc-write-cli is not installed")
    try:
        return HTMLResponse(render_preview_html(content, settings["css"]))
    except CvRenderError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.get("/cv/{base_cv_id}/diff.html", response_class=HTMLResponse)
def cv_diff_base_html(base_cv_id: int, request: Request, version: int | None = None,
                      against: int | None = None, conn: sqlite3.Connection = Depends(get_db)):
    base = q.get_base_cv(conn, base_cv_id)
    if base is None:
        raise HTTPException(status_code=404, detail="Base CV not found")
    content = base["base_cv"]
    if version is not None:
        v = q.get_version(conn, "base", base_cv_id, version)
        if v is None:
            raise HTTPException(status_code=404, detail="Version not found")
        content = v["content"]
    if against is not None:
        against_v = q.get_version(conn, "base", base_cv_id, against)
        if against_v is None:
            raise HTTPException(status_code=404, detail="Version not found")
        against_content = against_v["content"]
    else:
        target_id = q.resolve_base_diff_target(conn, base_cv_id, version)
        if target_id is None:
            return templates.TemplateResponse(
                request, "cv/_cv_diff_fallback.html",
                {"predates": False, "body_html": "", "message": "Nothing to compare against yet."},
            )
        against_content = q.get_version(conn, "base", base_cv_id, target_id)["content"]
    settings = q.get_cv_settings(conn)
    try:
        annotated = build_cv_diff(against_content, content).annotated_markdown
    except Exception:
        logger.exception("cv_diff build failed for base CV %s", base_cv_id)
        annotated = content
    if doc_write_available():
        try:
            return HTMLResponse(render_diff_html(annotated, settings["css"]))
        except CvRenderError as exc:
            raise HTTPException(status_code=503, detail=str(exc))
    body_html = _markdown.markdown(annotated, extensions=["nl2br"])
    return templates.TemplateResponse(
        request, "cv/_cv_diff_fallback.html", {"predates": False, "body_html": body_html},
    )


@router.get("/cv/{base_cv_id}.pdf")
def cv_base_pdf(base_cv_id: int, version: int | None = None, conn: sqlite3.Connection = Depends(get_db)):
    base = q.get_base_cv(conn, base_cv_id)
    if base is None:
        raise HTTPException(status_code=404, detail="Base CV not found")
    settings = q.get_cv_settings(conn)
    content = base["base_cv"]
    if version is not None:
        v = q.get_version(conn, "base", base_cv_id, version)
        if v is None:
            raise HTTPException(status_code=404, detail="Version not found")
        content = v["content"]
    if not content.strip():
        raise HTTPException(status_code=404, detail="No base CV")
    try:
        data = render_pdf(content, settings["css"])
    except CvRenderError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return Response(content=data, media_type="application/pdf",
                    headers={"Content-Disposition": 'attachment; filename="cv.pdf"'})
```

- [ ] **Step 4: Update the 8 global-settings routes that redundantly round-tripped `base_cv`**

In `app/routes/cv.py`, the `/cv/save-style`, `/cv/save-guardrails`, `/cv/save-css`, `/cv/reset-guardrails`, `/cv/reset-style`, `/cv/reset-css`, `/cv/save-directives-template`, `/cv/reset-directives-template` routes (cv.py:558-659) each call `q.save_cv_settings(conn, base_cv=..., ...)`. Drop the now-gone `base_cv=` argument from all 8 call sites — e.g. `cv_save_style` (cv.py:558-568):

Old:
```python
    current = q.get_cv_settings(conn)
    q.save_cv_settings(
        conn, base_cv=current["base_cv"], base_instruction=form.get("base_instruction", ""),
        base_guardrails=current["base_guardrails"], css=current["css"],
        default_scope=current["default_scope"],
        directives_template=current["directives_template"],
    )
```
New:
```python
    current = q.get_cv_settings(conn)
    q.save_cv_settings(
        conn, base_instruction=form.get("base_instruction", ""),
        base_guardrails=current["base_guardrails"], css=current["css"],
        default_scope=current["default_scope"],
        directives_template=current["directives_template"],
    )
```

Apply the same `base_cv=...,` line removal to the other 7 call sites (`cv_save_guardrails`, `cv_save_css`, `cv_reset_guardrails`, `cv_reset_style`, `cv_reset_css`, `cv_save_directives_template`, `cv_reset_directives_template`) — each has exactly one `base_cv=...` argument to drop.

- [ ] **Step 5: Rewrite `app/routes/cv_versions.py`'s base-CV routes**

Replace:
```python
@router.post("/cv/versions/{version_id}/revert")
def revert_base_version(version_id: int, conn: sqlite3.Connection = Depends(get_db)):
    if q.get_version(conn, "base", 1, version_id) is None:
        raise HTTPException(status_code=404, detail="Version not found")
    q.revert_base_cv_version(conn, version_id)
    return RedirectResponse("/cv", status_code=303)
```
with:
```python
@router.post("/cv/{base_cv_id}/versions/{version_id}/revert")
def revert_base_version(base_cv_id: int, version_id: int, conn: sqlite3.Connection = Depends(get_db)):
    if q.get_version(conn, "base", base_cv_id, version_id) is None:
        raise HTTPException(status_code=404, detail="Version not found")
    q.revert_base_cv_version(conn, base_cv_id, version_id)
    return RedirectResponse(f"/cv/{base_cv_id}", status_code=303)
```

Replace:
```python
@router.post("/cv/versions/{version_id}/accept")
def accept_base_version(version_id: int, conn: sqlite3.Connection = Depends(get_db)):
    if q.get_version(conn, "base", 1, version_id) is None:
        raise HTTPException(status_code=404, detail="Version not found")
    q.accept_base_cv_version(conn, version_id)
    return RedirectResponse(f"/cv?version={version_id}", status_code=303)


@router.post("/cv/accept")
def accept_base(conn: sqlite3.Connection = Depends(get_db)):
    q.accept_base_cv(conn)
    return RedirectResponse("/cv", status_code=303)
```
with:
```python
@router.post("/cv/{base_cv_id}/versions/{version_id}/accept")
def accept_base_version(base_cv_id: int, version_id: int, conn: sqlite3.Connection = Depends(get_db)):
    if q.get_version(conn, "base", base_cv_id, version_id) is None:
        raise HTTPException(status_code=404, detail="Version not found")
    q.accept_base_cv_version(conn, base_cv_id, version_id)
    return RedirectResponse(f"/cv/{base_cv_id}?version={version_id}", status_code=303)


@router.post("/cv/{base_cv_id}/accept")
def accept_base(base_cv_id: int, conn: sqlite3.Connection = Depends(get_db)):
    q.accept_base_cv(conn, base_cv_id)
    return RedirectResponse(f"/cv/{base_cv_id}", status_code=303)
```

- [ ] **Step 6: Write `cv/_base_cv_tabs.html`**

The rename/delete routes above render this partial — it needs to exist for Task 3's own tests to pass, so it's written here rather than in Task 5 (which only wires it into `index.html` and re-scopes the other base-CV templates' URLs).

```html
{# The base-CV tab strip — one tab per named base CV, a "+" tab to create
   one, and (when confirming_delete_id is set) an inline delete-confirm in
   place of that one tab's normal label. Params: base_cvs (list of {id,
   name}), active_base_cv_id, confirming_delete_id (None unless a delete was
   just refused for being referenced), referencing_count (only meaningful
   when confirming_delete_id is set). #}
<div class="cv-base-tabs" id="cv-base-tabs" role="tablist" aria-label="Base CVs">
  {% for b in base_cvs %}
  {% if b.id == confirming_delete_id %}
  <span class="cv-base-tab cv-base-tab-confirm" role="tab">
    <span>Delete "{{ b.name }}"? Tailored by {{ referencing_count }} job{{ 's' if referencing_count != 1 }}.</span>
    <button type="button" class="btn btn-subtle"
      hx-get="/cv/{{ b.id }}" hx-target="#cv-base-tabs" hx-swap="outerHTML"
      hx-vals='{"active": {{ active_base_cv_id or b.id }}}'>Cancel</button>
    <button type="button" class="btn btn-delete"
      hx-delete="/cv/{{ b.id }}?force=true" hx-target="#cv-base-tabs" hx-swap="outerHTML"
      hx-vals='{"active": {{ active_base_cv_id or b.id }}}'>Confirm delete</button>
  </span>
  {% else %}
  <a class="cv-base-tab{{ ' is-active' if b.id == active_base_cv_id else '' }}" role="tab"
     aria-selected="{{ 'true' if b.id == active_base_cv_id else 'false' }}"
     href="/cv/{{ b.id }}">
    <span class="cv-base-tab-name" data-base-cv-id="{{ b.id }}">{{ b.name }}</span>
    {% if base_cvs | length > 1 %}
    <button type="button" class="cv-base-tab-delete" title="Delete this base CV" aria-label="Delete {{ b.name }}"
      hx-delete="/cv/{{ b.id }}" hx-target="#cv-base-tabs" hx-swap="outerHTML"
      hx-vals='{"active": {{ active_base_cv_id or b.id }}}'
      onclick="event.preventDefault(); event.stopPropagation();">&times;</button>
    {% endif %}
  </a>
  {% endif %}
  {% endfor %}
  <button type="button" class="cv-base-tab cv-base-tab-new" id="cv-base-tab-new">+</button>
  <form id="cv-base-tab-create-form" method="post" action="/cv" style="display:none;">
    <input type="text" name="name" placeholder="Base CV name" required>
    <button type="submit" class="btn btn-subtle">Create</button>
  </form>
</div>
<script>
  (function () {
    var btn = document.getElementById("cv-base-tab-new");
    var form = document.getElementById("cv-base-tab-create-form");
    if (btn && form) {
      btn.addEventListener("click", function () {
        form.style.display = form.style.display === "none" ? "flex" : "none";
        var input = form.querySelector("input[name=name]");
        if (form.style.display !== "none" && input) input.focus();
      });
    }
    document.querySelectorAll(".cv-base-tab-name").forEach(function (span) {
      span.addEventListener("dblclick", function () {
        var id = span.getAttribute("data-base-cv-id");
        var current = span.textContent;
        var input = document.createElement("input");
        input.type = "text";
        input.value = current;
        input.className = "cv-base-tab-rename-input";
        span.replaceWith(input);
        input.focus();
        input.select();
        function commit() {
          var name = input.value.trim();
          if (!name || name === current) { input.replaceWith(span); return; }
          htmx.ajax("POST", "/cv/" + id + "/rename", {
            values: { name: name }, target: "#cv-base-tabs", swap: "outerHTML",
          });
        }
        input.addEventListener("blur", commit);
        input.addEventListener("keydown", function (e) {
          if (e.key === "Enter") input.blur();
          if (e.key === "Escape") { input.value = current; input.blur(); }
        });
      });
    });
  })();
</script>
```

(The rename/create JS re-binds on every htmx swap of `#cv-base-tabs` because it's inline in the partial itself and re-executes each time htmx swaps it in — consistent with how other inline `<script>` blocks in this app's partials behave; no separate static JS file needed.)

- [ ] **Step 7: Run the new tests**

Run: `uv run pytest tests/test_routes_cv_settings.py tests/test_routes_cv_versions.py -k "base_cv or delete or rename or create or revert_base" -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add app/routes/cv.py app/routes/cv_versions.py app/templates/cv/_base_cv_tabs.html tests/test_routes_cv_settings.py tests/test_routes_cv_versions.py
git commit -m "feat(cv): scope base CV routes to /cv/{base_cv_id}, add create/rename/delete"
```

---

### Task 4: Job tailoring — `job_cv.base_cv_id`, the base-CV picker's write path, threading through the workbench

**Files:**
- Modify: `app/routes/cv.py`
- Test: `tests/test_routes_cv_workbench.py`, `tests/test_routes_cv_tailor.py`, `tests/test_cv_task.py`

**Interfaces:**
- Consumes: Task 2's `resolve_job_base_cv_id`, re-scoped `resolve_base_cv`/`resolve_base_version_id`.
- Produces: `POST /jobs/{job_id}/cv/set-base`; `_workbench_ctx` gains `base_cvs`, `selected_base_cv_id` keys.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_routes_cv_workbench.py`:

This file already has a `_job(conn)` helper (test_routes_cv_workbench.py:5-11) that inserts a source + job (id 1) and seeds base CV content via `save_cv_settings(conn, base_cv=..., ...)` — reuse it, and note it needs the same Task 7 category-A fix (see Task 7 Step 1) since its `save_cv_settings` call uses the old signature.

```python
def test_set_job_base_cv(client, conn):
    jid = _job(conn)
    second_id = q.create_base_cv(conn, "Backend")

    r = client.post(f"/jobs/{jid}/cv/set-base", data={"base_cv_id": second_id})
    assert r.status_code == 200
    assert q.get_job_cv(conn, jid)["base_cv_id"] == second_id


def test_workbench_ctx_exposes_base_cvs_and_selection(client, conn):
    jid = _job(conn)
    default_id = q.list_base_cvs(conn)[0]["id"]

    r = client.get(f"/jobs/{jid}/cv")
    assert r.status_code == 200
    assert f'value="{default_id}"' in r.text or str(default_id) in r.text
```

Add to `tests/test_cv_task.py` (near `test_generate_sources_from_base_when_no_tailored_cv_yet`, cv_task.py:384-390, which this mirrors):

```python
def test_generate_uses_the_jobs_selected_base_cv_not_the_default(conn, cfg):
    jid = _seed(conn)
    second_id = q.create_base_cv(conn, "Backend", content="# Backend CV\n\n- different content\n")
    q.upsert_job_cv(conn, jid, base_cv_id=second_id)
    with patch("app.routes.cv.tailor_cv", return_value={"markdown": "# first draft"}) as tc, \
         patch("app.routes.cv.check_guardrails", return_value={"findings": []}):
        _run(conn, cfg, jid, "generate")
    source_cv = tc.call_args.args[2]
    assert source_cv == "# Backend CV\n\n- different content\n"
```

This file's `_seed` helper (cv_task.py:16-25) calls the old `save_cv_settings(conn, base_cv=..., ...)` signature — Task 7 fixes it once, which fixes every test in this file at once (see Task 7 Step 1, category A).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_routes_cv_workbench.py -k "base_cv" -v`
Expected: FAIL (`/jobs/{job_id}/cv/set-base` doesn't exist yet).

- [ ] **Step 3: Add a job-scoped base-CV resolution helper and the `set-base` route**

In `app/routes/cv.py`, add near `_resolved_settings` (cv.py:56-62):

```python
def _job_base_cv_id(conn: sqlite3.Connection, job_id: int) -> int:
    return q.resolve_job_base_cv_id(conn, job_id)
```

(A thin route-layer alias kept for symmetry with the other `_job_*` helpers already in this file — call sites below use it directly.)

Replace `_resolved_settings` (cv.py:56-62):
```python
def _resolved_settings(conn: sqlite3.Connection) -> dict:
    """Settings as tailoring/diffing/the job workbench's Base tab see them —
    base_cv resolved to the accepted version (or current, if nothing's been
    accepted yet). Everything else is live/unversioned."""
    settings = q.get_cv_settings(conn)
    settings["base_cv"] = q.resolve_base_cv(conn)
    return settings
```
with:
```python
def _resolved_settings(conn: sqlite3.Connection, base_cv_id: int) -> dict:
    """Settings as tailoring/diffing/the job workbench's Base tab see them —
    base_cv resolved to the accepted version (or current, if nothing's been
    accepted yet) of the given base CV. Everything else is live/unversioned
    and shared across all base CVs."""
    settings = q.get_cv_settings(conn)
    settings["base_cv"] = q.resolve_base_cv(conn, base_cv_id)
    return settings
```

- [ ] **Step 4: Thread `base_cv_id` through the workbench context and diff-target resolution**

Replace `_resolve_diff_target` (cv.py:282-299):
```python
def _resolve_diff_target(
    conn: sqlite3.Connection, job_id: int, against_type: str | None, against_id: int | None,
) -> tuple[str, int] | None:
    """Validates an explicit (against_type, against_id) diff-target pair —
    'base' resolves against the base CV (entity_id 1), 'tailored' against
    this job's own versions. Returns the resolved content, or None when no
    target was given (against_id omitted) so the caller can fall back to its
    own default. Raises 404 for an unknown type or a version id that
    doesn't belong to it."""
    if against_id is None:
        return None
    if against_type not in ("base", "tailored"):
        raise HTTPException(status_code=404, detail="Unknown diff target")
    entity_id = 1 if against_type == "base" else job_id
    version = q.get_version(conn, against_type, entity_id, against_id)
    if version is None:
        raise HTTPException(status_code=404, detail="Version not found")
    return against_type, against_id
```
with:
```python
def _resolve_diff_target(
    conn: sqlite3.Connection, job_id: int, against_type: str | None, against_id: int | None,
) -> tuple[str, int] | None:
    """Validates an explicit (against_type, against_id) diff-target pair —
    'base' resolves against the job's selected base CV, 'tailored' against
    this job's own versions. Returns the resolved content, or None when no
    target was given (against_id omitted) so the caller can fall back to its
    own default. Raises 404 for an unknown type or a version id that
    doesn't belong to it."""
    if against_id is None:
        return None
    if against_type not in ("base", "tailored"):
        raise HTTPException(status_code=404, detail="Unknown diff target")
    entity_id = _job_base_cv_id(conn, job_id) if against_type == "base" else job_id
    version = q.get_version(conn, against_type, entity_id, against_id)
    if version is None:
        raise HTTPException(status_code=404, detail="Version not found")
    return against_type, against_id
```

Replace `_workbench_ctx` (cv.py:302-335):
```python
def _workbench_ctx(
    conn: sqlite3.Connection, job_id: int,
    against_type: str | None = None, against_id: int | None = None,
) -> dict:
    job = q.get_job_with_source_name(conn, job_id)
    job_cv = q.get_job_cv(conn, job_id)
    settings = _resolved_settings(conn)
    updating = q.cv_generate_task_id(conn, job_id)
    planning = q.cv_plan_task_id(conn, job_id)
    return {
        "job": job,
        "job_cv": job_cv,
        "settings": settings,
        "scope_options": q.get_scope_options(conn),
        "updating_task_id": updating,
        "plan_task_id": planning,
        "plan_status": _plan_status(conn, job, job_cv, bool(planning)) if job else "none",
        "draft_status": _draft_status(job_cv, settings, bool(updating)),
        "draft_stale_reason": _draft_staleness_reason(job_cv, settings),
        "guardrail_status": _guardrail_status(job_cv, settings, bool(updating)),
        "has_doc_write": doc_write_available(),
        "cv_diff_view": _cv_diff_view(job_cv),
        "versions": q.get_versions(conn, "tailored", job_id),
        "base_versions": q.get_versions(conn, "base", 1),
        "current_version_id": job_cv["current_version_id"] if job_cv else None,
        "accepted_version": q.get_accepted_job_cv_version(conn, job_id),
        # The Differences tab's default target: whatever's currently accepted
        # for the base CV (or its current version, if nothing's accepted
        # yet) — overridable to any base or own version via the diff-target
        # picker in cv/_preview_tabs.html.
        "diff_against_type": against_type or "base",
        "diff_against_id": against_id if against_id is not None else q.resolve_base_version_id(conn),
        "version_base_url": f"/jobs/{job_id}/cv",
    }
```
with:
```python
def _workbench_ctx(
    conn: sqlite3.Connection, job_id: int,
    against_type: str | None = None, against_id: int | None = None,
) -> dict:
    job = q.get_job_with_source_name(conn, job_id)
    job_cv = q.get_job_cv(conn, job_id)
    selected_base_cv_id = _job_base_cv_id(conn, job_id)
    settings = _resolved_settings(conn, selected_base_cv_id)
    updating = q.cv_generate_task_id(conn, job_id)
    planning = q.cv_plan_task_id(conn, job_id)
    return {
        "job": job,
        "job_cv": job_cv,
        "settings": settings,
        "scope_options": q.get_scope_options(conn),
        "updating_task_id": updating,
        "plan_task_id": planning,
        "plan_status": _plan_status(conn, job, job_cv, bool(planning)) if job else "none",
        "draft_status": _draft_status(job_cv, settings, bool(updating)),
        "draft_stale_reason": _draft_staleness_reason(job_cv, settings),
        "guardrail_status": _guardrail_status(job_cv, settings, bool(updating)),
        "has_doc_write": doc_write_available(),
        "cv_diff_view": _cv_diff_view(job_cv),
        "versions": q.get_versions(conn, "tailored", job_id),
        "base_versions": q.get_versions(conn, "base", selected_base_cv_id),
        "base_cvs": q.list_base_cvs(conn),
        "selected_base_cv_id": selected_base_cv_id,
        "current_version_id": job_cv["current_version_id"] if job_cv else None,
        "accepted_version": q.get_accepted_job_cv_version(conn, job_id),
        # The Differences tab's default target: whatever's currently accepted
        # for the job's selected base CV (or its current version, if nothing's
        # accepted yet) — overridable to any base or own version via the
        # diff-target picker in cv/_preview_tabs.html.
        "diff_against_type": against_type or "base",
        "diff_against_id": (
            against_id if against_id is not None
            else q.resolve_base_version_id(conn, selected_base_cv_id)
        ),
        "version_base_url": f"/jobs/{job_id}/cv",
    }
```

- [ ] **Step 5: Thread `base_cv_id` through `cv_preview_html`, `cv_diff_html`, and `_task_cv_tailor`**

In `cv_preview_html` (cv.py:372-397), change:
```python
    if variant == "base":
        markdown = q.resolve_base_cv(conn)
```
to:
```python
    if variant == "base":
        markdown = q.resolve_base_cv(conn, _job_base_cv_id(conn, job_id))
```

In `cv_diff_html` (cv.py:400-442), change:
```python
    if resolved is not None:
        r_type, r_id = resolved
        entity_id = 1 if r_type == "base" else job_id
        against_content = q.get_version(conn, r_type, entity_id, r_id)["content"]
```
to:
```python
    if resolved is not None:
        r_type, r_id = resolved
        entity_id = _job_base_cv_id(conn, job_id) if r_type == "base" else job_id
        against_content = q.get_version(conn, r_type, entity_id, r_id)["content"]
```

In `_task_cv_tailor` (cv.py:713-719), change:
```python
def _task_cv_tailor(conn, client, model, config, params):
    job_id = params["job_id"]
    mode = params.get("mode", "plan")
    job = q.get_job(conn, job_id)
    if job is None:
        return {"job_id": job_id}
    settings = _resolved_settings(conn)
```
to:
```python
def _task_cv_tailor(conn, client, model, config, params):
    job_id = params["job_id"]
    mode = params.get("mode", "plan")
    job = q.get_job(conn, job_id)
    if job is None:
        return {"job_id": job_id}
    settings = _resolved_settings(conn, _job_base_cv_id(conn, job_id))
```

- [ ] **Step 6: Add the `set-base` route**

Add to `app/routes/cv.py`, near the other `/jobs/{job_id}/cv/...` routes (cv.py:831, right before `cv_plan`):

```python
@router.post("/jobs/{job_id}/cv/set-base", response_class=HTMLResponse)
async def cv_set_base(job_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    _require_job(conn, job_id)
    form = await request.form()
    try:
        base_cv_id = int(form.get("base_cv_id", ""))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid base CV")
    if q.get_base_cv(conn, base_cv_id) is None:
        raise HTTPException(status_code=404, detail="Base CV not found")
    q.upsert_job_cv(conn, job_id, base_cv_id=base_cv_id)
    return _plan_pane(request, conn, job_id)
```

- [ ] **Step 7: Run the tests**

Run: `uv run pytest tests/test_routes_cv_workbench.py tests/test_routes_cv_tailor.py tests/test_cv_task.py -v 2>&1 | tail -100`
Expected: the new tests PASS. Other tests in these files may still fail if they hit `_resolved_settings`/`_workbench_ctx` code paths this task didn't finish scoping, or Task 7's not-yet-done test sweep — re-run after Task 7.

- [ ] **Step 8: Commit**

```bash
git add app/routes/cv.py tests/test_routes_cv_workbench.py tests/test_routes_cv_tailor.py tests/test_cv_task.py
git commit -m "feat(cv): jobs pick which base CV they tailor from"
```

---

### Task 5: Frontend — wire the base CV tab strip into `index.html`, re-scope URLs in the base CV templates

**Files:**
- Modify: `app/templates/cv/index.html`, `app/templates/cv/_base_preview_tabs.html`, `app/templates/cv/_version_list.html`

**Interfaces:**
- Consumes: `_cv_page_ctx`'s `base`, `base_cvs`, `is_base`, `version_base_url` (Task 3), `cv/_base_cv_tabs.html` (Task 3).

- [ ] **Step 1: Wire `cv/_base_cv_tabs.html` (Task 3) into `cv/index.html` and re-point its hardcoded URLs**

At the top of `app/templates/cv/index.html`, right after the `<h1>CV</h1>` header block (index.html:1-9), add:
```html
{% include "cv/_base_cv_tabs.html" %}
```
with template variables `base_cvs=base_cvs, active_base_cv_id=base.id, confirming_delete_id=none` — since `index.html` already receives `base_cvs` and `base` from `_cv_page_ctx` (Task 3), wrap the include:
```html
{% with active_base_cv_id = base.id, confirming_delete_id = none %}
{% include "cv/_base_cv_tabs.html" %}
{% endwith %}
```

Replace every hardcoded `/cv` URL in `index.html` with `/cv/{{ base.id }}`:
- Line 6: `<a href="/cv/advanced">` stays as-is (global settings page, not base-CV-scoped).
- Line 13: `<a href="/cv">Go back to current version</a>` → `<a href="/cv/{{ base.id }}">Go back to current version</a>`
- Line 28: `reopen_url = "/cv/versions/" ~ viewing_version.id ~ "/revert"` → `reopen_url = "/cv/" ~ base.id ~ "/versions/" ~ viewing_version.id ~ "/revert"`
- Line 34: `action="/cv/versions/{{ viewing_version.id }}/accept"` → `action="/cv/{{ base.id }}/versions/{{ viewing_version.id }}/accept"`
- Line 38: `pdf_url = "/cv.pdf?version=" ~ viewing_version.id` → `pdf_url = "/cv/" ~ base.id ~ ".pdf?version=" ~ viewing_version.id`
- Line 53: `autosave_url="/cv/save-base"` → `autosave_url="/cv/" ~ base.id ~ "/save-base"` (Jinja string concatenation inside the macro call — check the exact call syntax at that line since `markdown_editor(...)` takes `autosave_url` as a plain arg, so this becomes `autosave_url=("/cv/" ~ base.id ~ "/save-base")`)
- Line 53: `settings.base_cv` → `base.base_cv` (content now lives on `base`, not `settings`)
- Line 70: `<a href="/cv?version={{ accepted_version.id }}">` → `<a href="/cv/{{ base.id }}?version={{ accepted_version.id }}">`
- Line 72: `action="/cv/accept"` → `action="/cv/{{ base.id }}/accept"`
- Line 76: `pdf_url = "/cv.pdf"` → `pdf_url = "/cv/" ~ base.id ~ ".pdf"`
- Line 76: `markdown_source = settings.base_cv` → `markdown_source = base.base_cv`

- [ ] **Step 2: Re-point `_base_preview_tabs.html`'s hardcoded URLs and `settings.base_cv`**

In `app/templates/cv/_base_preview_tabs.html`:
- Line 51: `href="/cv{{ diff_qs(v.id) }}"` → `href="/cv/{{ base.id }}{{ diff_qs(v.id) }}"`
- Line 69: `src=".../cv/preview.html{{ _v }}"` → `.../cv/{{ base.id }}/preview.html{{ _v }}`
- Line 74: `autosave_url="/cv/save-base"` and `markdown_editor("markdown", settings.base_cv, ...)` → `autosave_url=("/cv/" ~ base.id ~ "/save-base")` and `markdown_editor("markdown", base.base_cv, ...)`
- Line 80: `src=".../cv/diff.html{{ diff_qs(diff_against_id) }}"` → `.../cv/{{ base.id }}/diff.html{{ diff_qs(diff_against_id) }}"`

This partial receives `base` via the same context dict `_cv_page_ctx` builds (already includes it after Task 3) — no new `{% with %}` param needed at either of its two `{% include %}` call sites in `index.html` (they already pass through the full page context).

- [ ] **Step 3: Fix `_version_list.html`'s base-CV URL special-case**

The `version_url` macro (`_version_list.html:28-34`) special-cases the base CV by comparing `version_base_url == '/cv'` — that string no longer occurs (`version_base_url` is now `/cv/{id}`). Add an explicit `is_base` flag instead of relying on string comparison.

Replace:
```python
{% macro version_url(v, current_version_id, version_base_url) -%}
{%- if v.id == current_version_id -%}
{{ version_base_url if version_base_url == '/cv' else version_base_url ~ '/preview' }}
{%- else -%}
{{ ('/cv?version=' ~ v.id) if version_base_url == '/cv' else (version_base_url ~ '/preview?version=' ~ v.id) }}
{%- endif -%}
{%- endmacro %}
```
with:
```python
{% macro version_url(v, current_version_id, version_base_url, is_base=False) -%}
{%- if v.id == current_version_id -%}
{{ version_base_url }}
{%- else -%}
{{ (version_base_url ~ '?version=' ~ v.id) if is_base else (version_base_url ~ '/preview?version=' ~ v.id) }}
{%- endif -%}
{%- endmacro %}
```

Replace:
```python
{% macro version_picker(versions, current_version_id, viewing_id, version_base_url, reopen_url=None, compact=False) %}
```
with:
```python
{% macro version_picker(versions, current_version_id, viewing_id, version_base_url, reopen_url=None, compact=False, is_base=False) %}
```
and inside it, replace the `href="{{ version_url(v, current_version_id, version_base_url) }}"` call with `href="{{ version_url(v, current_version_id, version_base_url, is_base) }}"`.

Replace the bottom-of-file standalone render block:
```python
{{ version_picker(versions, current_version_id, viewing_id if viewing_id is defined else None, version_base_url,
                   reopen_url if reopen_url is defined else None) }}
```
with:
```python
{{ version_picker(versions, current_version_id, viewing_id if viewing_id is defined else None, version_base_url,
                   reopen_url if reopen_url is defined else None, is_base=(is_base if is_base is defined else False)) }}
```

`_cv_page_ctx` (Task 3) already sets `"is_base": True` in the context dict it returns, so `index.html`'s inclusion of `_version_list.html` picks it up automatically; the tailored-CV call sites (job workbench) never set `is_base`, so it defaults to `False` there — unaffected.

- [ ] **Step 4: Add minimal CSS for the new tab strip**

Add to this project's shared stylesheet (locate it: `grep -n "\.cv-preview-tabs" app/static/*.css` or similar — reuse the existing `.cv-preview-tab`/`.cv-preview-tabs` rules as a starting point, adding `.cv-base-tabs`, `.cv-base-tab`, `.cv-base-tab.is-active`, `.cv-base-tab-delete`, `.cv-base-tab-confirm`, `.cv-base-tab-new`, `.cv-base-tab-rename-input` following the same visual language — border-bottom tab styling, active-state underline/background, small subtle delete "×"). This is a visual-craft step; run the `frontend-design` skill's conventions if unsure of spacing/color tokens, and cross-check against `.cv-preview-tab`'s existing rules for consistency (same font-size, padding, border treatment).

- [ ] **Step 5: Manual check (no automated test for pure CSS/JS)**

Run: `uv run pytest tests/test_routes_cv_settings.py tests/test_routes_cv_versions.py -v 2>&1 | tail -60`
Expected: the Task 3 tests that render `cv/_base_cv_tabs.html` now PASS (they previously failed with `TemplateNotFound`).

- [ ] **Step 6: Commit**

```bash
git add app/templates/cv/_base_cv_tabs.html app/templates/cv/index.html app/templates/cv/_base_preview_tabs.html app/templates/cv/_version_list.html app/static/*.css
git commit -m "feat(cv): base CV tab strip — create, rename, delete UI"
```

---

### Task 6: Frontend — job workbench base-CV picker

**Files:**
- Modify: `app/templates/cv/_plan_pane.html`

**Interfaces:**
- Consumes: `_workbench_ctx`'s `base_cvs`, `selected_base_cv_id` (Task 4).

- [ ] **Step 1: Add the picker to `_plan_pane.html`**

In `app/templates/cv/_plan_pane.html`, replace the intro paragraph (lines 7-13):
```html
<form id="cv-directives-form" method="post" action="/jobs/{{ job.id }}/cv/save-directives">
  <p class="muted" style="font-size:.85em;margin:.2rem 0 .6rem;">
    Your <a href="/cv">base CV</a> can be optimized to fit this job. The instructions
    below identify potential improvements and guide the automated CV adjustments.
    You choose how much to apply with <strong>Edit scope</strong>, on the
    <a href="/jobs/{{ job.id }}/cv/preview">Preview CV</a> page.
  </p>
```
with:
```html
<form id="cv-directives-form" method="post" action="/jobs/{{ job.id }}/cv/save-directives">
  <p class="muted" style="font-size:.85em;margin:.2rem 0 .6rem;">
    Your <a href="/cv/{{ selected_base_cv_id }}">base CV</a> can be optimized to fit this job. The instructions
    below identify potential improvements and guide the automated CV adjustments.
    You choose how much to apply with <strong>Edit scope</strong>, on the
    <a href="/jobs/{{ job.id }}/cv/preview">Preview CV</a> page.
  </p>
  {% if base_cvs | length > 1 %}
  <label style="display:block;margin-bottom:.6rem;">Tailoring from
    <select name="base_cv_id"
      hx-post="/jobs/{{ job.id }}/cv/set-base" hx-target="#cv-plan-pane" hx-swap="innerHTML"
      hx-trigger="change">
      {% for b in base_cvs %}
      <option value="{{ b.id }}"{{ ' selected' if b.id == selected_base_cv_id }}>{{ b.name }}</option>
      {% endfor %}
    </select>
  </label>
  {% endif %}
```

(The picker is hidden when there's only one base CV — nothing to choose between, and it would otherwise clutter every job workbench for users who never create a second one.)

- [ ] **Step 2: Write a route test asserting the picker renders and the workbench link points at the selected base CV**

Add to `tests/test_routes_cv_workbench.py`:

```python
def test_plan_pane_shows_base_cv_picker_only_with_multiple_base_cvs(client, conn):
    jid = _job(conn)
    default_id = q.list_base_cvs(conn)[0]["id"]

    r = client.get(f"/jobs/{jid}/cv")
    assert f'href="/cv/{default_id}"' in r.text
    assert "name=\"base_cv_id\"" not in r.text  # only one base CV — no picker

    q.create_base_cv(conn, "Backend")
    r = client.get(f"/jobs/{jid}/cv")
    assert 'name="base_cv_id"' in r.text
```

- [ ] **Step 3: Run the test**

Run: `uv run pytest tests/test_routes_cv_workbench.py -k picker -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add app/templates/cv/_plan_pane.html tests/test_routes_cv_workbench.py
git commit -m "feat(cv): base-CV picker in the job tailoring workbench"
```

---

### Task 7: Full-suite sweep — propagate the `save_cv_settings`/URL changes across the test suite, manual verification

**Files:** all remaining test files that call `save_cv_settings(..., base_cv=...)` or hit an unscoped `/cv/...` route: `tests/test_cv_task.py`, `tests/test_cv_versions.py` (any leftovers), `tests/test_queries.py`, `tests/test_routes_cv_actions.py`, `tests/test_routes_cv_preview.py`, `tests/test_routes_cv_tailor.py`, `tests/test_routes_cv_versions.py`, `tests/test_routes_cv_workbench.py`, `tests/test_routes_cv_settings.py`.

**Interfaces:** none new — this task makes every existing test compile and pass against Tasks 1-6's changes.

- [ ] **Step 1: Run the full suite and triage failures**

Run: `uv run pytest -q 2>&1 | tail -150`

Expected failure categories, each with its fix rule:

**A. `save_cv_settings(conn, base_cv="...", ...)` → `TypeError: unexpected keyword argument 'base_cv'`.**
Fix: replace the single call with a base-CV-id lookup plus a content-only call. Before:
```python
q.save_cv_settings(conn, base_cv="# Me\n\n- x\n", base_instruction="", base_guardrails="",
                   css="", default_scope=[])
```
After:
```python
base_cv_id = q.list_base_cvs(conn)[0]["id"]
q.set_base_cv(conn, base_cv_id, "# Me\n\n- x\n")
q.save_cv_settings(conn, base_instruction="", base_guardrails="", css="", default_scope=[])
```
If the test doesn't actually care about base CV *content* (many settings-only tests pass a throwaway `base_cv="x"` purely because the old signature required it), drop the `set_base_cv` line entirely and just remove `base_cv=...` from the `save_cv_settings` call — no `base_cv_id` lookup needed in that case.

`tests/test_cv_task.py`'s `_seed` helper (cv_task.py:16-25) is called by nearly every test in that file — fix it once, at the source, rather than per call site:
```python
def _seed(conn):
    conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s','http://x','manual')")
    conn.execute(
        "INSERT INTO jobs (source_id, url, title, company, simplified_content) "
        "VALUES (1,'http://x/1','Platform Engineer','Acme','We need Kafka and Terraform.')"
    )
    conn.commit()
    base_cv_id = q.list_base_cvs(conn)[0]["id"]
    q.set_base_cv(conn, base_cv_id, "# Me\n\n- Kafka work\n")
    q.save_cv_settings(conn, base_instruction="", base_guardrails="", css="", default_scope=[1, 2])
    return 1
```
The other `save_cv_settings(conn, base_cv=..., ...)` call in that file (`test_plan_mode_first_visit_is_plan_only_no_draft`, cv_task.py:44-46, which re-saves settings with a `directives_template`) needs the same treatment: drop `base_cv=...`, and drop the redundant `set_base_cv` call entirely since `_seed` already wrote that content and this call doesn't change it.

`tests/test_routes_cv_workbench.py`'s `_job` helper (test_routes_cv_workbench.py:5-11) is the same pattern — fix once at the source:
```python
def _job(conn):
    conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s','http://x','manual')")
    conn.execute("INSERT INTO jobs (source_id, url, title) VALUES (1,'http://x/1','Role')")
    conn.commit()
    base_cv_id = q.list_base_cvs(conn)[0]["id"]
    q.set_base_cv(conn, base_cv_id, "# Me")
    q.save_cv_settings(conn, base_instruction="", base_guardrails="", css="", default_scope=["select", "reorder"])
    return 1
```
Check `tests/test_routes_cv_actions.py`, `tests/test_routes_cv_preview.py`, and `tests/test_routes_cv_tailor.py` for similar per-file `_job`/`_seed`-style helpers before editing individual test bodies — fixing a shared helper once is almost always less work and less error-prone than fixing every call site separately.

**B. Assertions reading `q.get_cv_settings(conn)["base_cv"]` (or `["accepted_at"]`, `["current_version_id"]`, `["current_version_hash"]`) → `KeyError`.**
Fix: read from `q.get_base_cv(conn, base_cv_id)` instead (same keys, that function now returns them).

**C. Direct calls to now-rescoped query functions missing the `base_cv_id` argument** (`resolve_base_version_id`, `resolve_base_diff_target`, `get_accepted_base_version`, `get_accepted_base_cv`, `resolve_base_cv`, `revert_base_cv_version`, `accept_base_cv`, `accept_base_cv_version`, `get_versions(conn, "base", 1)`, `get_version(conn, "base", 1, ...)`) → `TypeError: missing required positional argument` or a version lookup returning `None` unexpectedly.
Fix: pass the test's base CV id (from `q.list_base_cvs(conn)[0]["id"]` or wherever the test created one) instead of the literal `1`.

**D. Route tests hitting unscoped URLs** (`client.get("/cv")` expecting 200 content instead of a redirect, `client.post("/cv/save-base", ...)`, `client.post("/cv/accept")`, `client.post("/cv/versions/{id}/revert")`, `f"/cv.pdf"`) → wrong status code or 404.
Fix: resolve the base CV id first, then hit the scoped URL. Before:
```python
r = client.get("/cv")
assert r.status_code == 200
```
After:
```python
base_cv_id = q.list_base_cvs(conn)[0]["id"]
r = client.get(f"/cv/{base_cv_id}")
assert r.status_code == 200
```
For a bare `client.get("/cv")` that's only used to trigger the redirect and doesn't otherwise care about content, either keep it and assert on the redirect (per Task 3's `test_cv_root_redirects_to_the_first_base_cv`) or switch to `client.get("/cv", follow_redirects=True)` if the test's real assertion is about the destination page's content.

**E. Anything asserting on `settings.base_cv` / `settings["base_cv"]` in a rendered template's HTML** (e.g. checking that saved markdown appears on the page) → assertion no longer finds it, since `settings` no longer carries `base_cv`.
Fix: no test change needed if the content still renders (it does, via `base.base_cv` in the templates from Task 5) — the assertion on raw HTML text (e.g. `"# Me" in r.text`) still passes as long as Task 5's template changes are in. If a test greps for the literal string `data-something="{{ settings.base_cv }}"` that no longer exists in the template source, update the expected string to match the new template output.

Work through the failures file by file, applying the matching rule above, re-running that file's tests after each fix:
```bash
uv run pytest tests/test_cv_task.py -v
uv run pytest tests/test_queries.py -v
uv run pytest tests/test_routes_cv_actions.py -v
uv run pytest tests/test_routes_cv_preview.py -v
uv run pytest tests/test_routes_cv_tailor.py -v
uv run pytest tests/test_routes_cv_versions.py -v
uv run pytest tests/test_routes_cv_workbench.py -v
uv run pytest tests/test_routes_cv_settings.py -v
uv run pytest tests/test_cv_versions.py -v
```

- [ ] **Step 2: Run the entire suite clean**

Run: `uv run pytest -q 2>&1 | tail -40`
Expected: PASS, 0 failures.

Also grep for anything still assuming the old singleton shape outside what's already covered above:
```bash
grep -rn "entity_id = 1\|entity_id=1\|'base', 1\|\"base\", 1" app tests
grep -rn "cv_settings\[.current_version_id.\]\|cv_settings\[.accepted_at.\]" app tests
```
Both should return nothing (aside from `app/db/schema.py`'s migration functions, which legitimately reference the old singleton shape as historical fixture/migration source data).

- [ ] **Step 3: Manual smoke test — start the dev server**

Use the `run-dev-server` skill to start the app against a throwaway `job-seek.db` copy. Then, in a browser:
1. Go to `/cv` — confirm it redirects to `/cv/1` and shows a "Default" tab.
2. Click "+", create a second base CV named "Backend", write some content, save it.
3. Confirm both tabs show, and switching between them shows the right content in Preview/Edit/Differences.
4. Double-click a tab name, rename it, confirm it sticks.
5. Open a job's Tailor CV workbench — confirm the base-CV picker appears (since there are now 2 base CVs) and defaults to whichever one the job was already using (or "Default" for a fresh job). Switch it, click "Generate", confirm the tailored draft reflects the newly selected base CV's content.
6. Try deleting the base CV the job above is using — confirm it shows a "referenced by 1 job" inline confirm instead of deleting immediately; confirm the delete; confirm the tab disappears and the job's workbench still shows its previously generated tailored CV (unaffected).
7. Try deleting down to the last remaining base CV — confirm the delete control is hidden/disabled on the last one.

Report back to the user with the dev server URL for their own click-through per this project's "UI dev-server handoff" convention — do not merge or clean up until they've said to proceed.

- [ ] **Step 4: Stop the dev server once the user confirms, then proceed to the finishing-a-development-branch flow**

(Per `CLAUDE.md`: squash-merge the worktree branch into local `main`, re-run the full suite on `main`, then remove the worktree and branch. Per this project's effort-tracking convention, invoke the `effort-stats` skill afterward since this was done in a worktree.)
