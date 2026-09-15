# CV Version History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give base CV and tailored (per-job) CV content a recoverable version history — every LLM "update" or manual edit becomes a version, a selector below the preview lets you view and revert to a prior one, and "Accept" becomes a movable marker instead of a read-only freeze.

**Architecture:** One new table, `cv_versions`, holds *every* version of both entity kinds — including the currently-live one. `cv_settings`/`job_cv` drop their `base_cv`/`tailored_cv` content columns entirely and instead carry a single `current_version_id` pointer. `q.get_cv_settings`/`q.get_job_cv` join through that pointer so the many existing call sites reading `row["base_cv"]`/`row["tailored_cv"]` need no changes. Reverting is just repointing `current_version_id` — no content is ever copied.

**Tech Stack:** FastAPI + Jinja2 + htmx, raw `sqlite3` (no ORM), pytest + FastAPI `TestClient`.

Spec: `docs/superpowers/specs/2026-09-14-cv-version-history-design.md`

## Global Constraints

- Retention: keep the most recent 10 versions per `(entity_type, entity_id)`, plus whichever version (if any) is current or holds `accepted_at` — never prune those even if older than the 10 most recent.
- Manual-edit stacking window: 1 hour, resets on every edit (rolling), and only applies when the current version's own `action = 'manual_edit'` **and** `accepted_at IS NULL`. An `action = 'update'` version, or an accepted current version, always opens a new version on the next edit.
- `action` is one of `'update'` (LLM regenerate) or `'manual_edit'` — revert never creates or changes an `action`.
- `hash` is an 8-hex-char random string (`secrets.token_hex(4)`), assigned once at version creation, unique, human-readable — not used for anything but display today.
- Base CV's `base_instruction`, `base_guardrails`, `css`, `default_scope`, `directives_template` are **not** versioned — only `base_cv` (base) and `tailored_cv` (tailored) get history.
- Migration is a hard-downtime rebuild (this project's convention) — no dual-schema compatibility, no fabricated history, just today's content carried into its first version.
- Existing call sites that read `row["base_cv"]` / `row["tailored_cv"]` / `row["finalized_at"]` (aside from the `finalized_at`→`accepted_at` rename) must keep working unchanged — this plan achieves that through the query-layer join and through `upsert_job_cv`/`save_cv_settings` keeping their existing keyword-argument shape.

---

## File Structure

- `app/db/schema.py` — `cv_versions` table, `current_version_id` columns, one migration.
- `app/db/queries.py` — version-recording/pruning helpers, rewritten `get_cv_settings`/`save_cv_settings`/`get_job_cv`/`upsert_job_cv`/`set_job_cv_tailored`, renamed `accept_job_cv`/`unaccept_job_cv`, new `resolve_base_cv`/`get_versions`/`get_version`/`revert_job_cv_version`/`revert_base_cv_version`/`accept_base_cv`/`unaccept_base_cv`, cleanup in `delete_job`/`delete_jobs`.
- `app/routes/cv.py` — drop the accept-freeze gate, resolve base CV for tailoring/diffing/the Base tab, thread `?version=` through the existing preview/diff/pdf routes, rename `finalized_at`→`accepted_at`.
- `app/routes/cv_versions.py` *(new)* — revert (both entities) and base-CV accept/unaccept endpoints.
- `app/main.py` — register the new router.
- `app/templates/cv/_preview_pane.html`, `_preview_tabs.html`, `preview.html`, `workbench.html`, `index.html` — version list, accepted badge, read-only version view.
- `app/templates/cv/_version_list.html` *(new)*, `app/templates/cv/_cv_version_view.html` *(new, replaces `_accepted.html`)*.
- `app/templates/jobs/_feedback.html` — `finalized_at`→`accepted_at` rename.
- Tests: `tests/test_schema.py`, `tests/test_queries.py`, `tests/test_routes_cv_actions.py`, `tests/test_routes_cv_tailor.py`, `tests/test_routes_cv_preview.py`, `tests/test_routes_cv_workbench.py`, `tests/test_cv_task.py` updated in place; `tests/test_cv_versions.py` *(new)*, `tests/test_routes_cv_versions.py` *(new)*.

---

### Task 1: Schema — `cv_versions` table and the content-column migration

**Files:**
- Modify: `app/db/schema.py`
- Test: `tests/test_schema.py`

**Interfaces:**
- Produces: table `cv_versions(id, hash, entity_type, entity_id, parent_version_id, content, action, accepted_at, updated_at)`; `cv_settings.current_version_id`, `job_cv.current_version_id` (both `INTEGER REFERENCES cv_versions(id)`); `cv_settings`/`job_cv` no longer have `base_cv`/`tailored_cv`/`finalized_at`.

- [ ] **Step 1: Write the failing migration test**

Add to `tests/test_schema.py` (near the other `job_cv`/`cv_settings` migration tests):

```python
def test_cv_settings_and_job_cv_columns_after_version_migration():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(cv_settings)")}
    assert cols == {
        "id", "current_version_id", "base_instruction", "base_guardrails",
        "css", "default_scope", "directives_template", "updated_at",
    }
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(job_cv)")}
    assert cols == {
        "job_id", "current_version_id", "scope", "tuning_directives", "plan",
        "handled_suggestions", "guardrail_findings", "change_report", "base_hash",
        "base_cv_snapshot", "plan_generated_at", "directives_edited_at", "generated_at",
        "scope_edited_at", "edited_at", "guardrails_checked_at", "plan_context_hash",
        "updated_at",
    }
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(cv_versions)")}
    assert cols == {
        "id", "hash", "entity_type", "entity_id", "parent_version_id",
        "content", "action", "accepted_at", "updated_at",
    }
    conn.close()


def test_init_db_migrates_existing_base_cv_and_tailored_cv_into_versions(conn):
    # Simulate a pre-migration DB: old-shape cv_settings/job_cv with content
    # columns, populated as a real install would have them.
    conn.executescript(
        """
        DROP TABLE cv_settings;
        CREATE TABLE cv_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            base_cv TEXT NOT NULL DEFAULT '',
            base_instruction TEXT NOT NULL DEFAULT '',
            base_guardrails TEXT NOT NULL DEFAULT '',
            css TEXT NOT NULL DEFAULT '',
            default_scope TEXT NOT NULL DEFAULT '["select","reorder"]',
            directives_template TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        INSERT INTO cv_settings (id, base_cv, base_instruction, base_guardrails, css, default_scope, directives_template)
        VALUES (1, '# My CV', 'instr', 'guard', 'p{}', '[1,2]', 'tmpl');

        DROP TABLE job_cv;
        CREATE TABLE job_cv (
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
            plan_generated_at TEXT, directives_edited_at TEXT, generated_at TEXT,
            scope_edited_at TEXT, edited_at TEXT, guardrails_checked_at TEXT,
            plan_context_hash TEXT, finalized_at TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        INSERT INTO sources (name, url, fetcher_type) VALUES ('s', 'http://x', 'manual');
        INSERT INTO jobs (source_id, url) VALUES (1, 'http://x/1');
        INSERT INTO job_cv (job_id, tailored_cv, finalized_at) VALUES (1, '# Tailored', datetime('now'));
        """
    )
    conn.commit()

    init_db(conn)

    settings = conn.execute(
        "SELECT cv_settings.*, cv_versions.content AS base_cv FROM cv_settings "
        "LEFT JOIN cv_versions ON cv_versions.id = cv_settings.current_version_id WHERE id = 1"
    ).fetchone()
    assert settings["base_cv"] == "# My CV"
    assert settings["base_instruction"] == "instr"

    jc = conn.execute(
        "SELECT job_cv.*, cv_versions.content AS tailored_cv, cv_versions.accepted_at AS accepted_at "
        "FROM job_cv LEFT JOIN cv_versions ON cv_versions.id = job_cv.current_version_id WHERE job_id = 1"
    ).fetchone()
    assert jc["tailored_cv"] == "# Tailored"
    assert jc["accepted_at"] is not None

    # Idempotent: running again must not raise or duplicate versions.
    init_db(conn)
    count = conn.execute(
        "SELECT COUNT(*) FROM cv_versions WHERE entity_type = 'tailored' AND entity_id = 1"
    ).fetchone()[0]
    assert count == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_schema.py -k version -v`
Expected: FAIL (`cv_versions` doesn't exist yet / old columns still present).

- [ ] **Step 3: Update `_DDL` in `app/db/schema.py`**

Add a new table right before `CREATE TABLE IF NOT EXISTS cv_settings` in `_DDL`:

```sql
CREATE TABLE IF NOT EXISTS cv_versions (
    id INTEGER PRIMARY KEY,
    hash TEXT NOT NULL UNIQUE,
    entity_type TEXT NOT NULL CHECK (entity_type IN ('base','tailored')),
    entity_id INTEGER NOT NULL,
    parent_version_id INTEGER REFERENCES cv_versions(id) ON DELETE SET NULL,
    content TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('update','manual_edit')),
    accepted_at TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_cv_versions_entity ON cv_versions(entity_type, entity_id, id);
```

Replace the `cv_settings` definition in `_DDL` with:

```sql
CREATE TABLE IF NOT EXISTS cv_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    current_version_id INTEGER REFERENCES cv_versions(id),
    base_instruction TEXT NOT NULL DEFAULT '',
    base_guardrails TEXT NOT NULL DEFAULT '',
    css TEXT NOT NULL DEFAULT '',
    default_scope TEXT NOT NULL DEFAULT '["select","reorder"]',
    directives_template TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

Replace the `job_cv` definition in `_DDL` with:

```sql
CREATE TABLE IF NOT EXISTS job_cv (
    job_id INTEGER PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    current_version_id INTEGER REFERENCES cv_versions(id),
    scope TEXT NOT NULL DEFAULT '[]',
    tuning_directives TEXT NOT NULL DEFAULT '',
    plan TEXT NOT NULL DEFAULT '[]',
    handled_suggestions TEXT NOT NULL DEFAULT '[]',
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
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

- [ ] **Step 4: Add the migration function**

Add to `app/db/schema.py`, after `_migrate_job_cv_add_guardrails_checked_at`:

```python
def _migrate_cv_content_to_versions(conn: sqlite3.Connection) -> None:
    """base_cv / tailored_cv text and the finalized_at flag move out of
    cv_settings / job_cv into cv_versions, each entity's live content carried
    forward as its first version — not fabricated history. See the
    2026-09-14 CV version-history spec."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='cv_settings'"
    ).fetchone()
    if row is None or "current_version_id" in row[0]:
        return
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript(
        """
        INSERT INTO cv_versions (hash, entity_type, entity_id, parent_version_id, content, action, updated_at)
        SELECT lower(hex(randomblob(4))), 'base', 1, NULL, base_cv, 'manual_edit', updated_at
        FROM cv_settings WHERE id = 1 AND base_cv != '';

        INSERT INTO cv_versions (hash, entity_type, entity_id, parent_version_id, content, action, accepted_at, updated_at)
        SELECT lower(hex(randomblob(4))), 'tailored', job_id, NULL, tailored_cv, 'manual_edit', finalized_at, updated_at
        FROM job_cv WHERE tailored_cv != '';

        CREATE TABLE cv_settings_new (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            current_version_id INTEGER REFERENCES cv_versions(id),
            base_instruction TEXT NOT NULL DEFAULT '',
            base_guardrails TEXT NOT NULL DEFAULT '',
            css TEXT NOT NULL DEFAULT '',
            default_scope TEXT NOT NULL DEFAULT '["select","reorder"]',
            directives_template TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        INSERT INTO cv_settings_new (id, current_version_id, base_instruction, base_guardrails,
                                      css, default_scope, directives_template, updated_at)
        SELECT id,
               (SELECT id FROM cv_versions WHERE entity_type = 'base' AND entity_id = 1
                ORDER BY id DESC LIMIT 1),
               base_instruction, base_guardrails, css, default_scope, directives_template, updated_at
        FROM cv_settings WHERE id = 1;
        DROP TABLE cv_settings;
        ALTER TABLE cv_settings_new RENAME TO cv_settings;

        CREATE TABLE job_cv_new (
            job_id INTEGER PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
            current_version_id INTEGER REFERENCES cv_versions(id),
            scope TEXT NOT NULL DEFAULT '[]',
            tuning_directives TEXT NOT NULL DEFAULT '',
            plan TEXT NOT NULL DEFAULT '[]',
            handled_suggestions TEXT NOT NULL DEFAULT '[]',
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
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        INSERT INTO job_cv_new (job_id, current_version_id, scope, tuning_directives, plan,
                                 handled_suggestions, guardrail_findings, change_report, base_hash,
                                 base_cv_snapshot, plan_generated_at, directives_edited_at, generated_at,
                                 scope_edited_at, edited_at, guardrails_checked_at, plan_context_hash, updated_at)
        SELECT job_id,
               (SELECT id FROM cv_versions WHERE entity_type = 'tailored' AND entity_id = job_cv.job_id
                ORDER BY id DESC LIMIT 1),
               scope, tuning_directives, plan, handled_suggestions, guardrail_findings, change_report,
               base_hash, base_cv_snapshot, plan_generated_at, directives_edited_at, generated_at,
               scope_edited_at, edited_at, guardrails_checked_at, plan_context_hash, updated_at
        FROM job_cv;
        DROP TABLE job_cv;
        ALTER TABLE job_cv_new RENAME TO job_cv;
        """
    )
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
```

Register it in `init_db`, at the end of the function (after `_migrate_jobs_add_pending_status(conn)`):

```python
    _migrate_cv_content_to_versions(conn)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_schema.py -k version -v`
Expected: PASS

- [ ] **Step 6: Update the two existing column-set assertions that this migration invalidates**

In `tests/test_schema.py`, update `test_cv_settings_table_is_singleton` (remove `"base_cv"` from the expected set, add `"current_version_id"`) and `test_job_cv_table_columns_and_cascade` (remove `"tailored_cv"` and `"finalized_at"`, add `"current_version_id"`) to match the new column sets asserted in Step 1's new test — these are now redundant with it, so simplify them to just check table/FK behavior (singleton constraint, cascade-delete) and drop their own column-set assertions, relying on the new test above for that.

- [ ] **Step 7: Run the full schema test file**

Run: `python -m pytest tests/test_schema.py -v`
Expected: PASS (all tests, including the two pre-existing tests updated in Step 6)

- [ ] **Step 8: Commit**

```bash
git add app/db/schema.py tests/test_schema.py
git commit -m "$(cat <<'EOF'
feat(cv): add cv_versions table and migrate base/tailored content into it

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Ry4J3F2w3aCct6MRS1j7Va
EOF
)"
```

---

### Task 2: Query layer — version recording, pruning, and rewritten CV accessors

**Files:**
- Modify: `app/db/queries.py`
- Test: `tests/test_cv_versions.py` (new), `tests/test_queries.py`

**Interfaces:**
- Consumes: `cv_versions` table and `current_version_id` columns from Task 1.
- Produces: `q.get_cv_settings(conn) -> dict` (now includes `accepted_at`, `current_version_hash`, `current_version_id`), `q.save_cv_settings(conn, *, base_cv, base_instruction, base_guardrails, css, default_scope, directives_template="")` (unchanged signature), `q.get_job_cv(conn, job_id) -> dict | None` (now includes `accepted_at`, `current_version_action`, `current_version_hash`), `q.upsert_job_cv(conn, job_id, **fields)` (unchanged signature, `tailored_cv=` now versioned as an `'update'`), `q.set_job_cv_tailored(conn, job_id, markdown)` (unchanged signature, versioned as `'manual_edit'`), `q.accept_job_cv(conn, job_id) -> None`, `q.unaccept_job_cv(conn, job_id) -> None`, `q.accept_base_cv(conn) -> None`, `q.unaccept_base_cv(conn) -> None`, `q.resolve_base_cv(conn) -> str`, `q.get_versions(conn, entity_type, entity_id, exclude_id=None) -> list[dict]`, `q.get_version(conn, entity_type, entity_id, version_id) -> dict | None`, `q.revert_job_cv_version(conn, job_id, version_id) -> bool`, `q.revert_base_cv_version(conn, version_id) -> bool`.

- [ ] **Step 1: Write the failing tests for the version-recording core**

Create `tests/test_cv_versions.py`:

```python
from app.db import queries as q


def test_manual_edit_stacks_within_the_hour_and_opens_new_version_after():
    import sqlite3
    from app.db.schema import init_db
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)

    q.save_cv_settings(conn, base_cv="v1", base_instruction="", base_guardrails="",
                       css="", default_scope=[])
    first_id = q.get_cv_settings(conn)["current_version_id"]

    q.save_cv_settings(conn, base_cv="v2", base_instruction="", base_guardrails="",
                       css="", default_scope=[])
    settings = q.get_cv_settings(conn)
    assert settings["current_version_id"] == first_id   # stacked, same version
    assert settings["base_cv"] == "v2"

    conn.execute(
        "UPDATE cv_versions SET updated_at = datetime('now', '-2 hours') WHERE id = ?",
        (first_id,),
    )
    conn.commit()
    q.save_cv_settings(conn, base_cv="v3", base_instruction="", base_guardrails="",
                       css="", default_scope=[])
    settings = q.get_cv_settings(conn)
    assert settings["current_version_id"] != first_id    # window lapsed -> new version
    assert settings["base_cv"] == "v3"

    versions = q.get_versions(conn, "base", 1)            # all versions, current included
    assert len(versions) == 2
    stacked = next(v for v in versions if v["id"] == first_id)
    assert stacked["content"] == "v2"                     # the stacked v1->v2 version, now history
    current = next(v for v in versions if v["id"] == settings["current_version_id"])
    assert current["content"] == "v3"
    conn.close()


def test_update_action_never_stacks_even_seconds_apart():
    import sqlite3
    from app.db.schema import init_db
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s','http://x','manual')")
    conn.execute("INSERT INTO jobs (source_id, url) VALUES (1, 'http://x/1')")
    conn.commit()

    q.upsert_job_cv(conn, 1, tailored_cv="draft 1")
    first_id = q.get_job_cv(conn, 1)["current_version_id"]
    q.upsert_job_cv(conn, 1, tailored_cv="draft 2")
    second_id = q.get_job_cv(conn, 1)["current_version_id"]
    assert second_id != first_id
    versions = q.get_versions(conn, "tailored", 1)   # all versions, current included
    assert {v["id"] for v in versions} == {first_id, second_id}
    conn.close()


def test_manual_edit_never_mutates_an_accepted_version_in_place():
    import sqlite3
    from app.db.schema import init_db
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s','http://x','manual')")
    conn.execute("INSERT INTO jobs (source_id, url) VALUES (1, 'http://x/1')")
    conn.commit()

    q.set_job_cv_tailored(conn, 1, "draft")
    accepted_id = q.get_job_cv(conn, 1)["current_version_id"]
    q.accept_job_cv(conn, 1)

    q.set_job_cv_tailored(conn, 1, "edited after accept")
    row = q.get_job_cv(conn, 1)
    assert row["current_version_id"] != accepted_id       # new version opened
    assert row["tailored_cv"] == "edited after accept"
    accepted_version = q.get_version(conn, "tailored", 1, accepted_id)
    assert accepted_version["content"] == "draft"          # untouched
    assert accepted_version["accepted_at"] is not None     # badge stayed on it
    assert row["accepted_at"] is None                      # new current isn't accepted
    conn.close()


def test_retention_caps_at_ten_but_keeps_accepted_and_current():
    import sqlite3
    from app.db.schema import init_db
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s','http://x','manual')")
    conn.execute("INSERT INTO jobs (source_id, url) VALUES (1, 'http://x/1')")
    conn.commit()

    q.upsert_job_cv(conn, 1, tailored_cv="draft 0")
    accepted_id = q.get_job_cv(conn, 1)["current_version_id"]
    q.accept_job_cv(conn, 1)
    for i in range(1, 13):
        q.upsert_job_cv(conn, 1, tailored_cv=f"draft {i}")  # each an 'update' -> always new version

    versions = q.get_versions(conn, "tailored", 1)
    ids = {v["id"] for v in versions} | {q.get_job_cv(conn, 1)["current_version_id"]}
    assert accepted_id in ids                              # old accepted version survived pruning
    assert len(versions) <= 11                             # 10 recent (non-current) + accepted
    conn.close()


def test_revert_repoints_without_copying_content():
    import sqlite3
    from app.db.schema import init_db
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s','http://x','manual')")
    conn.execute("INSERT INTO jobs (source_id, url) VALUES (1, 'http://x/1')")
    conn.commit()

    q.upsert_job_cv(conn, 1, tailored_cv="draft 1")
    old_id = q.get_job_cv(conn, 1)["current_version_id"]
    q.upsert_job_cv(conn, 1, tailored_cv="draft 2")

    assert q.revert_job_cv_version(conn, 1, old_id) is True
    row = q.get_job_cv(conn, 1)
    assert row["current_version_id"] == old_id
    assert row["tailored_cv"] == "draft 1"
    # draft 2's version is untouched, still in history
    versions = {v["id"]: v["content"] for v in q.get_versions(conn, "tailored", 1)}
    assert "draft 2" in versions.values()

    assert q.revert_job_cv_version(conn, 1, 99999) is False  # unknown version
    conn.close()


def test_new_version_records_its_parent():
    import sqlite3
    from app.db.schema import init_db
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s','http://x','manual')")
    conn.execute("INSERT INTO jobs (source_id, url) VALUES (1, 'http://x/1')")
    conn.commit()

    q.upsert_job_cv(conn, 1, tailored_cv="draft 1")
    first_id = q.get_job_cv(conn, 1)["current_version_id"]
    first_version = q.get_version(conn, "tailored", 1, first_id)
    assert first_version["parent_version_id"] is None       # nothing preceded it

    q.upsert_job_cv(conn, 1, tailored_cv="draft 2")          # 'update' -> always a new row
    second_id = q.get_job_cv(conn, 1)["current_version_id"]
    second_version = q.get_version(conn, "tailored", 1, second_id)
    assert second_version["parent_version_id"] == first_id
    conn.close()


def test_resolve_base_cv_falls_back_to_current_until_accepted():
    import sqlite3
    from app.db.schema import init_db
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)

    q.save_cv_settings(conn, base_cv="draft base", base_instruction="", base_guardrails="",
                       css="", default_scope=[])
    assert q.resolve_base_cv(conn) == "draft base"   # no accept yet -> current

    q.accept_base_cv(conn)
    assert q.resolve_base_cv(conn) == "draft base"

    q.save_cv_settings(conn, base_cv="wip edit", base_instruction="", base_guardrails="",
                       css="", default_scope=[])
    assert q.resolve_base_cv(conn) == "draft base"   # accepted stays in effect, draft ignored

    q.accept_base_cv(conn)
    assert q.resolve_base_cv(conn) == "wip edit"     # re-accepted -> moves
    conn.close()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_cv_versions.py -v`
Expected: FAIL (functions/columns don't exist yet)

- [ ] **Step 3: Implement the version-recording core in `app/db/queries.py`**

Add near the top of the `# --- CV ---` section, after `_JOB_CV_JSON_COLS`:

```python
import secrets

_VERSION_RETENTION = 10


def _new_version_hash(conn: sqlite3.Connection) -> str:
    while True:
        h = secrets.token_hex(4)
        if not conn.execute("SELECT 1 FROM cv_versions WHERE hash = ?", (h,)).fetchone():
            return h


def _record_version(
    conn: sqlite3.Connection, entity_type: str, entity_id: int, *,
    action: str, content: str, current_version_id: int | None,
) -> int:
    """Returns the version id that should become current. A manual edit
    stacks onto the current row in place when that row is itself an
    unaccepted manual edit less than an hour old; anything else (an update,
    an accepted current version, or a lapsed window) opens a new version."""
    current = None
    if current_version_id is not None:
        current = conn.execute(
            "SELECT action, accepted_at, "
            "(julianday('now') - julianday(updated_at)) * 24 AS age_hours "
            "FROM cv_versions WHERE id = ?",
            (current_version_id,),
        ).fetchone()
    if (
        action == "manual_edit" and current is not None
        and current["action"] == "manual_edit" and current["accepted_at"] is None
        and current["age_hours"] < 1
    ):
        conn.execute(
            "UPDATE cv_versions SET content = ?, updated_at = datetime('now') WHERE id = ?",
            (content, current_version_id),
        )
        return current_version_id
    new_id = conn.execute(
        "INSERT INTO cv_versions (hash, entity_type, entity_id, parent_version_id, content, action, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, datetime('now'))",
        (_new_version_hash(conn), entity_type, entity_id, current_version_id, content, action),
    ).lastrowid
    _prune_versions(conn, entity_type, entity_id)
    return new_id


def _prune_versions(conn: sqlite3.Connection, entity_type: str, entity_id: int) -> None:
    keep = {
        r[0] for r in conn.execute(
            "SELECT id FROM cv_versions WHERE entity_type = ? AND entity_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (entity_type, entity_id, _VERSION_RETENTION),
        )
    }
    accepted = conn.execute(
        "SELECT id FROM cv_versions WHERE entity_type = ? AND entity_id = ? AND accepted_at IS NOT NULL",
        (entity_type, entity_id),
    ).fetchone()
    if accepted:
        keep.add(accepted[0])
    placeholders = ",".join("?" for _ in keep)
    conn.execute(
        f"DELETE FROM cv_versions WHERE entity_type = ? AND entity_id = ? AND id NOT IN ({placeholders})",
        (entity_type, entity_id, *keep),
    )


def resolve_base_cv(conn: sqlite3.Connection) -> str:
    """The base CV as tailoring/diffing see it: the accepted version, or the
    current one if nothing has been accepted yet."""
    row = conn.execute(
        "SELECT content FROM cv_versions WHERE entity_type = 'base' AND entity_id = 1 "
        "AND accepted_at IS NOT NULL ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is not None:
        return row[0]
    return get_cv_settings(conn)["base_cv"]


def get_versions(conn: sqlite3.Connection, entity_type: str, entity_id: int,
                 exclude_id: int | None = None) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM cv_versions WHERE entity_type = ? AND entity_id = ? ORDER BY id DESC",
        (entity_type, entity_id),
    ).fetchall()
    return [dict(r) for r in rows if r["id"] != exclude_id]


def get_version(conn: sqlite3.Connection, entity_type: str, entity_id: int,
                version_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM cv_versions WHERE id = ? AND entity_type = ? AND entity_id = ?",
        (version_id, entity_type, entity_id),
    ).fetchone()
    return dict(row) if row else None


def revert_job_cv_version(conn: sqlite3.Connection, job_id: int, version_id: int) -> bool:
    if get_version(conn, "tailored", job_id, version_id) is None:
        return False
    conn.execute(
        "UPDATE job_cv SET current_version_id = ?, updated_at = datetime('now') WHERE job_id = ?",
        (version_id, job_id),
    )
    conn.commit()
    return True


def revert_base_cv_version(conn: sqlite3.Connection, version_id: int) -> bool:
    if get_version(conn, "base", 1, version_id) is None:
        return False
    conn.execute(
        "UPDATE cv_settings SET current_version_id = ?, updated_at = datetime('now') WHERE id = 1",
        (version_id,),
    )
    conn.commit()
    return True


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


def unaccept_base_cv(conn: sqlite3.Connection) -> None:
    conn.execute(
        "UPDATE cv_versions SET accepted_at = NULL WHERE entity_type = 'base' AND entity_id = 1 "
        "AND accepted_at IS NOT NULL"
    )
    conn.commit()
```

- [ ] **Step 4: Rewrite `get_cv_settings` and `save_cv_settings`**

Replace the existing `get_cv_settings` and `save_cv_settings` functions with:

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

- [ ] **Step 5: Rewrite `get_job_cv`, `upsert_job_cv`, `set_job_cv_tailored`**

Replace the existing functions with:

```python
def get_job_cv(conn: sqlite3.Connection, job_id: int) -> dict | None:
    row = conn.execute(
        """
        SELECT job_cv.*, cv_versions.content AS tailored_cv,
               cv_versions.accepted_at AS accepted_at,
               cv_versions.action AS current_version_action,
               cv_versions.hash AS current_version_hash
        FROM job_cv LEFT JOIN cv_versions ON cv_versions.id = job_cv.current_version_id
        WHERE job_cv.job_id = ?
        """,
        (job_id,),
    ).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["tailored_cv"] = d["tailored_cv"] or ""
    for col in _JOB_CV_JSON_COLS:
        d[col] = json.loads(d[col])
    return d


def upsert_job_cv(conn: sqlite3.Connection, job_id: int, **fields) -> None:
    conn.execute("INSERT OR IGNORE INTO job_cv (job_id) VALUES (?)", (job_id,))
    tailored_cv = fields.pop("tailored_cv", None)
    encoded = {}
    for k, v in fields.items():
        if k in _JOB_CV_JSON_COLS and not isinstance(v, str):
            encoded[k] = json.dumps(v)
        else:
            encoded[k] = v
    if tailored_cv is not None:
        current_version_id = conn.execute(
            "SELECT current_version_id FROM job_cv WHERE job_id = ?", (job_id,)
        ).fetchone()[0]
        encoded["current_version_id"] = _record_version(
            conn, "tailored", job_id, action="update", content=tailored_cv,
            current_version_id=current_version_id,
        )
    if encoded:
        sets = ", ".join(f"{k} = ?" for k in encoded)
        conn.execute(
            f"UPDATE job_cv SET {sets}, updated_at = datetime('now') WHERE job_id = ?",
            (*encoded.values(), job_id),
        )
    else:
        conn.execute("UPDATE job_cv SET updated_at = datetime('now') WHERE job_id = ?", (job_id,))
    conn.commit()


def set_job_cv_tailored(conn: sqlite3.Connection, job_id: int, markdown: str) -> None:
    conn.execute("INSERT OR IGNORE INTO job_cv (job_id) VALUES (?)", (job_id,))
    current_version_id = conn.execute(
        "SELECT current_version_id FROM job_cv WHERE job_id = ?", (job_id,)
    ).fetchone()[0]
    new_version_id = _record_version(
        conn, "tailored", job_id, action="manual_edit", content=markdown,
        current_version_id=current_version_id,
    )
    conn.execute(
        "UPDATE job_cv SET current_version_id = ?, edited_at = datetime('now'), "
        "updated_at = datetime('now') WHERE job_id = ?",
        (new_version_id, job_id),
    )
    conn.commit()
```

- [ ] **Step 6: Rename `finalize_job_cv`/`unfinalize_job_cv` to `accept_job_cv`/`unaccept_job_cv`**

Replace the two functions with:

```python
def accept_job_cv(conn: sqlite3.Connection, job_id: int) -> None:
    conn.execute(
        "UPDATE cv_versions SET accepted_at = NULL WHERE entity_type = 'tailored' AND entity_id = ? "
        "AND accepted_at IS NOT NULL",
        (job_id,),
    )
    conn.execute(
        "UPDATE cv_versions SET accepted_at = datetime('now') WHERE id = "
        "(SELECT current_version_id FROM job_cv WHERE job_id = ?)",
        (job_id,),
    )
    conn.execute("UPDATE job_cv SET updated_at = datetime('now') WHERE job_id = ?", (job_id,))
    conn.commit()


def unaccept_job_cv(conn: sqlite3.Connection, job_id: int) -> None:
    conn.execute(
        "UPDATE cv_versions SET accepted_at = NULL WHERE entity_type = 'tailored' AND entity_id = ? "
        "AND accepted_at IS NOT NULL",
        (job_id,),
    )
    conn.execute("UPDATE job_cv SET updated_at = datetime('now') WHERE job_id = ?", (job_id,))
    conn.commit()
```

- [ ] **Step 7: Clean up orphaned version history on job deletion**

Replace `delete_job` and `delete_jobs`:

```python
def delete_job(conn: sqlite3.Connection, job_id: int) -> None:
    conn.execute("DELETE FROM cv_versions WHERE entity_type = 'tailored' AND entity_id = ?", (job_id,))
    conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
    conn.commit()


def delete_jobs(conn: sqlite3.Connection, job_ids: list[int]) -> None:
    if not job_ids:
        return
    placeholders = ",".join("?" for _ in job_ids)
    conn.execute(
        f"DELETE FROM cv_versions WHERE entity_type = 'tailored' AND entity_id IN "
        f"(SELECT id FROM jobs WHERE status = 'trash' AND id IN ({placeholders}))",
        job_ids,
    )
    conn.execute(f"DELETE FROM jobs WHERE status = 'trash' AND id IN ({placeholders})", job_ids)
    conn.commit()
```

- [ ] **Step 8: Run the new tests to verify they pass**

Run: `python -m pytest tests/test_cv_versions.py -v`
Expected: PASS

- [ ] **Step 9: Update `tests/test_queries.py` for the rename**

Find the test using `finalize_job_cv`/`unfinalize_job_cv`/`finalized_at` (around line 2171) and update it:

```python
def test_accept_and_unaccept_job_cv(client, conn):
    ...  # keep existing job setup
    q.accept_job_cv(conn, jid)
    assert q.get_job_cv(conn, jid)["accepted_at"] is not None
    q.unaccept_job_cv(conn, jid)
    assert q.get_job_cv(conn, jid)["accepted_at"] is None
```

(Rename the test function itself if its old name referenced "finalize"; keep whatever job-setup lines already precede it in the file.)

- [ ] **Step 10: Run the full test file and the whole suite's query tests**

Run: `python -m pytest tests/test_queries.py tests/test_cv_versions.py -v`
Expected: PASS

- [ ] **Step 11: Commit**

```bash
git add app/db/queries.py tests/test_cv_versions.py tests/test_queries.py
git commit -m "$(cat <<'EOF'
feat(cv): version-aware CV read/write query layer

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Ry4J3F2w3aCct6MRS1j7Va
EOF
)"
```

---

### Task 3: Drop the accept-freeze gate; resolve base CV for tailoring

**Files:**
- Modify: `app/routes/cv.py`
- Modify: `app/templates/cv/workbench.html`, `app/templates/jobs/_feedback.html`
- Test: `tests/test_routes_cv_actions.py`, `tests/test_routes_cv_tailor.py`, `tests/test_cv_task.py`

**Interfaces:**
- Consumes: `q.resolve_base_cv`, `q.accept_job_cv`, `q.unaccept_job_cv` from Task 2.
- Produces: `_resolved_settings(conn) -> dict` (new module-level helper in `cv.py`), used by `_workbench_ctx` and `_task_cv_tailor`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_routes_cv_actions.py`, replace `test_save_scope_is_read_only_for_finalized_cv` (accept no longer blocks anything) with:

```python
def test_accept_does_not_block_further_edits(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft")
    client.post(f"/jobs/{jid}/cv/accept")
    r = client.post(f"/jobs/{jid}/cv/save-scope", data={"scope": ["1"]})
    assert r.status_code == 200
    assert q.get_job_cv(conn, jid)["scope"] == [1]
```

Replace `test_accept_finalizes_and_shows_read_only_view` with:

```python
def test_accept_marks_current_version_and_redirects_to_preview(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft")
    r = client.post(f"/jobs/{jid}/cv/accept", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == f"/jobs/{jid}/cv/preview"
    assert q.get_job_cv(conn, jid)["accepted_at"] is not None
    assert "cv" in [e["kind"] for e in q.get_job_events(conn, jid)]


def test_unaccept_clears_the_marker_without_touching_content(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft")
    client.post(f"/jobs/{jid}/cv/accept")
    r = client.post(f"/jobs/{jid}/cv/unaccept", follow_redirects=False)
    assert r.status_code == 303
    row = q.get_job_cv(conn, jid)
    assert row["accepted_at"] is None
    assert row["tailored_cv"] == "# Draft"
```

Search the rest of `tests/test_routes_cv_actions.py`, `tests/test_routes_cv_tailor.py`, and `tests/test_cv_task.py` for `q.finalize_job_cv(conn, jid)` and replace each with `q.accept_job_cv(conn, jid)`; replace any `["finalized_at"]` with `["accepted_at"]`. Any test asserting a 409 for a finalized CV (e.g. in `test_routes_cv_tailor.py`, `test_cv_task.py`) should instead assert the action **succeeds** now — check each one's surrounding context and change the assertion from `status_code == 409` / "still accepted, unchanged" to a success assertion consistent with that test's action (e.g. `status_code == 200` and the content did change).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_routes_cv_actions.py tests/test_routes_cv_tailor.py tests/test_cv_task.py -v`
Expected: FAIL (route still 409s / uses old function names)

- [ ] **Step 3: Remove `_require_editable` and its call sites in `app/routes/cv.py`**

Delete the `_require_editable` function (around line 632) entirely, and delete the `_require_editable(conn, job_id)` line from the start of each of these route handlers: `cv_plan`, `cv_generate`, `cv_recheck_guardrails`, `cv_save_directives`, `cv_save_scope`, `cv_save_tailored`, `cv_reset_directives`, `cv_unhandle_suggestion`, `cv_accept_plan_proposals`.

- [ ] **Step 4: Add `_resolved_settings` and wire it into `_workbench_ctx`/`_task_cv_tailor`**

Add near `_base_hash` in `app/routes/cv.py`:

```python
def _resolved_settings(conn: sqlite3.Connection) -> dict:
    """Settings as tailoring/diffing/the job workbench's Base tab see them —
    base_cv resolved to the accepted version (or current, if nothing's been
    accepted yet). Everything else is live/unversioned."""
    settings = q.get_cv_settings(conn)
    settings["base_cv"] = q.resolve_base_cv(conn)
    return settings
```

In `_workbench_ctx`, change `settings = q.get_cv_settings(conn)` to `settings = _resolved_settings(conn)`.

In `_task_cv_tailor`, change `settings = q.get_cv_settings(conn)` (near the top of the function) to `settings = _resolved_settings(conn)`, and delete the two lines:
```python
    if row and row["finalized_at"]:
        return {"job_id": job_id}  # accepted CV is read-only
```

- [ ] **Step 5: Rewrite `cv_accept` and rename `cv_reopen` to `cv_unaccept`**

Replace:

```python
@router.post("/jobs/{job_id}/cv/accept")
def cv_accept(job_id: int, conn: sqlite3.Connection = Depends(get_db)):
    row = q.get_job_cv(conn, job_id)
    if row is None or not row["tailored_cv"]:
        raise HTTPException(status_code=400, detail="No CV to accept")
    q.accept_job_cv(conn, job_id)
    q.add_job_event(conn, job_id, "cv", "CV accepted")
    return RedirectResponse(f"/jobs/{job_id}/cv/preview", status_code=303)


@router.post("/jobs/{job_id}/cv/unaccept")
def cv_unaccept(job_id: int, conn: sqlite3.Connection = Depends(get_db)):
    if q.get_job(conn, job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")
    q.unaccept_job_cv(conn, job_id)
    q.add_job_event(conn, job_id, "cv", "CV unaccepted")
    return RedirectResponse(f"/jobs/{job_id}/cv/preview", status_code=303)
```

(This drops the old "already finalized, no-op" guard — accepting again just re-marks the current version, which is harmless and simpler than checking first.)

- [ ] **Step 6: Simplify `workbench.html`**

Replace the whole `{% block content %}` body in `app/templates/cv/workbench.html` with:

```html
{% block content %}
{% include "jobs/_job_header.html" %}

<div id="cv-plan-pane">{% include "cv/_plan_pane.html" %}</div>

{% if job_cv is none %}
<button type="button" id="cv-autostart" hidden
        data-progress-url="/jobs/{{ job.id }}/cv/plan" data-progress-oob
        data-progress-label="Analysing the job"></button>
<script>
  document.addEventListener("DOMContentLoaded", function () {
    var b = document.getElementById("cv-autostart");
    if (b) b.click();
  });
</script>
{% endif %}
{% endblock %}
```

- [ ] **Step 7: Rename `finalized_at` to `accepted_at` in `_feedback.html`**

In `app/templates/jobs/_feedback.html` line 119, change `job_cv.finalized_at` to `job_cv.accepted_at`.

- [ ] **Step 8: Run the tests to verify they pass**

Run: `python -m pytest tests/test_routes_cv_actions.py tests/test_routes_cv_tailor.py tests/test_cv_task.py tests/test_routes_cv_workbench.py -v`
Expected: PASS

- [ ] **Step 9: Run the full test suite to catch any other `finalized_at`/`finalize_job_cv` reference**

Run: `python -m pytest -v 2>&1 | tail -60`
Expected: any remaining failures name a file/line with a stale `finalized_at`/`finalize_job_cv`/`unfinalize_job_cv` reference — fix each the same way (rename to `accepted_at`/`accept_job_cv`/`unaccept_job_cv`, and any 409-on-accept assertion becomes a success assertion) until the suite is green.

- [ ] **Step 10: Commit**

```bash
git add app/routes/cv.py app/templates/cv/workbench.html app/templates/jobs/_feedback.html tests/
git commit -m "$(cat <<'EOF'
feat(cv): accept no longer freezes editing; tailoring uses the accepted base CV

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Ry4J3F2w3aCct6MRS1j7Va
EOF
)"
```

---

### Task 4: New `cv_versions.py` router — revert (both entities), base CV accept/unaccept

**Files:**
- Create: `app/routes/cv_versions.py`
- Modify: `app/main.py`
- Test: `tests/test_routes_cv_versions.py` (new)

**Interfaces:**
- Consumes: `q.revert_job_cv_version`, `q.revert_base_cv_version`, `q.accept_base_cv`, `q.unaccept_base_cv`, `q.get_job`, `q.add_job_event` (all existing/from Task 2).
- Produces: `POST /jobs/{job_id}/cv/versions/{version_id}/revert`, `POST /cv/versions/{version_id}/revert`, `POST /cv/accept`, `POST /cv/unaccept`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_routes_cv_versions.py`:

```python
from app.db import queries as q


def _job(conn):
    conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s','http://x','manual')")
    conn.execute("INSERT INTO jobs (source_id, url, title) VALUES (1,'http://x/1','Role')")
    conn.commit()
    return 1


def test_revert_tailored_version_repoints_current(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="draft 1")
    old_id = q.get_job_cv(conn, jid)["current_version_id"]
    q.upsert_job_cv(conn, jid, tailored_cv="draft 2")

    r = client.post(f"/jobs/{jid}/cv/versions/{old_id}/revert", follow_redirects=False)
    assert r.status_code == 303
    row = q.get_job_cv(conn, jid)
    assert row["current_version_id"] == old_id
    assert row["tailored_cv"] == "draft 1"
    assert "cv" in [e["kind"] for e in q.get_job_events(conn, jid)]


def test_revert_unknown_tailored_version_404s(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="draft")
    assert client.post(f"/jobs/{jid}/cv/versions/99999/revert").status_code == 404


def test_revert_rejects_a_version_belonging_to_another_job(client, conn):
    jid1 = _job(conn)
    conn.execute("INSERT INTO jobs (source_id, url, title) VALUES (1,'http://x/2','Role 2')")
    conn.commit()
    jid2 = 2
    q.upsert_job_cv(conn, jid1, tailored_cv="job1 draft")
    other_version_id = q.get_job_cv(conn, jid1)["current_version_id"]
    q.upsert_job_cv(conn, jid2, tailored_cv="job2 draft")

    assert client.post(f"/jobs/{jid2}/cv/versions/{other_version_id}/revert").status_code == 404


def test_revert_base_cv_version_repoints_current(client, conn):
    q.save_cv_settings(conn, base_cv="v1", base_instruction="", base_guardrails="",
                       css="", default_scope=[])
    old_id = q.get_cv_settings(conn)["current_version_id"]
    q.save_cv_settings(conn, base_cv="v2", base_instruction="", base_guardrails="",
                       css="", default_scope=[])

    r = client.post(f"/cv/versions/{old_id}/revert", follow_redirects=False)
    assert r.status_code == 303
    assert q.get_cv_settings(conn)["base_cv"] == "v1"


def test_base_cv_accept_and_unaccept(client, conn):
    q.save_cv_settings(conn, base_cv="v1", base_instruction="", base_guardrails="",
                       css="", default_scope=[])
    r = client.post("/cv/accept", follow_redirects=False)
    assert r.status_code == 303
    assert q.get_cv_settings(conn)["accepted_at"] is not None

    r = client.post("/cv/unaccept", follow_redirects=False)
    assert r.status_code == 303
    assert q.get_cv_settings(conn)["accepted_at"] is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_routes_cv_versions.py -v`
Expected: FAIL (404 — routes don't exist)

- [ ] **Step 3: Create `app/routes/cv_versions.py`**

```python
from __future__ import annotations
import sqlite3
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from app.db import queries as q
from app.deps import get_db

router = APIRouter()


@router.post("/jobs/{job_id}/cv/versions/{version_id}/revert")
def revert_tailored_version(job_id: int, version_id: int, conn: sqlite3.Connection = Depends(get_db)):
    if q.get_job(conn, job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")
    version = q.get_version(conn, "tailored", job_id, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="Version not found")
    q.revert_job_cv_version(conn, job_id, version_id)
    q.add_job_event(conn, job_id, "cv", f"Reverted to version {version['hash']}")
    return RedirectResponse(f"/jobs/{job_id}/cv/preview", status_code=303)


@router.post("/cv/versions/{version_id}/revert")
def revert_base_version(version_id: int, conn: sqlite3.Connection = Depends(get_db)):
    if q.get_version(conn, "base", 1, version_id) is None:
        raise HTTPException(status_code=404, detail="Version not found")
    q.revert_base_cv_version(conn, version_id)
    return RedirectResponse("/cv", status_code=303)


@router.post("/cv/accept")
def accept_base(conn: sqlite3.Connection = Depends(get_db)):
    q.accept_base_cv(conn)
    return RedirectResponse("/cv", status_code=303)


@router.post("/cv/unaccept")
def unaccept_base(conn: sqlite3.Connection = Depends(get_db)):
    q.unaccept_base_cv(conn)
    return RedirectResponse("/cv", status_code=303)
```

- [ ] **Step 4: Register the router in `app/main.py`**

Add `from app.routes import cv_versions` alongside the other route imports, and `app.include_router(cv_versions.router)` alongside the other `include_router` calls.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_routes_cv_versions.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/routes/cv_versions.py app/main.py tests/test_routes_cv_versions.py
git commit -m "$(cat <<'EOF'
feat(cv): revert and base-CV accept/unaccept endpoints

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Ry4J3F2w3aCct6MRS1j7Va
EOF
)"
```

---

### Task 5: `?version=` on the tailored-CV preview/diff/PDF routes, and a repurposed read-only view

**Files:**
- Modify: `app/routes/cv.py`
- Modify: `app/templates/cv/preview.html`, `app/templates/cv/_preview_tabs.html`
- Create: `app/templates/cv/_cv_version_view.html` (replaces `app/templates/cv/_accepted.html`, which is deleted)
- Test: `tests/test_routes_cv_preview.py`

**Interfaces:**
- Consumes: `q.get_version` from Task 2.
- Produces: `cv_preview_page(job_id, version: int | None = None)`, `cv_preview_html(job_id, variant, version: int | None = None)`, `cv_diff_html(job_id, version: int | None = None)`, `cv_pdf(job_id, version: int | None = None)` — all backward compatible when `version` is omitted.

- [ ] **Step 1: Write the failing tests**

In `tests/test_routes_cv_preview.py`, find the test around line 333 that calls `q.finalize_job_cv(conn, jid)` and checks the read-only `_accepted.html` framing — rename the call to `q.accept_job_cv` and update its assertions: accepting no longer changes what `/jobs/{id}/cv/preview` shows by default (it still renders the normal editable pane), so replace assertions like "shows the frozen view" with an assertion that the page still shows the Update button / edit affordances. Add:

```python
def test_preview_page_shows_read_only_historic_version(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="draft 1")
    old_id = q.get_job_cv(conn, jid)["current_version_id"]
    q.upsert_job_cv(conn, jid, tailored_cv="draft 2")

    r = client.get(f"/jobs/{jid}/cv/preview?version={old_id}")
    assert r.status_code == 200
    assert "Revert to this version" in r.text
    assert 'cv-preview-editor' not in r.text   # read-only: no editor mounted


def test_preview_page_404s_for_unknown_version(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="draft")
    assert client.get(f"/jobs/{jid}/cv/preview?version=99999").status_code == 404


def test_preview_html_serves_historic_tailored_content(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="draft 1")
    old_id = q.get_job_cv(conn, jid)["current_version_id"]
    q.upsert_job_cv(conn, jid, tailored_cv="draft 2")

    r = client.get(f"/jobs/{jid}/cv/preview.html?variant=tailored&version={old_id}")
    assert r.status_code in (200, 503)  # 503 only if doc-write-cli isn't installed in CI
    if r.status_code == 200:
        assert "draft 1" in r.text or "draft" in r.text  # rendered HTML contains the old draft


def test_pdf_serves_historic_tailored_content(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="draft 1")
    old_id = q.get_job_cv(conn, jid)["current_version_id"]
    q.upsert_job_cv(conn, jid, tailored_cv="draft 2")

    r = client.get(f"/jobs/{jid}/cv.pdf?version={old_id}")
    assert r.status_code in (200, 503)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_routes_cv_preview.py -v`
Expected: FAIL

- [ ] **Step 3: Thread `version` through `cv_preview_html`, `cv_diff_html`, `cv_pdf`**

In `app/routes/cv.py`, replace `cv_preview_html`:

```python
@router.get(
    "/jobs/{job_id}/cv/preview.html", response_class=HTMLResponse)
def cv_preview_html(job_id: int, variant: str = "tailored", version: int | None = None,
                    conn: sqlite3.Connection = Depends(get_db)):
    job = q.get_job(conn, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    settings = q.get_cv_settings(conn)
    if variant == "base":
        markdown = q.resolve_base_cv(conn)
    elif version is not None:
        v = q.get_version(conn, "tailored", job_id, version)
        if v is None:
            raise HTTPException(status_code=404, detail="Version not found")
        markdown = v["content"]
    else:
        row = q.get_job_cv(conn, job_id)
        if row is None or not row["tailored_cv"]:
            raise HTTPException(status_code=404, detail="No tailored CV")
        markdown = row["tailored_cv"]
    if not doc_write_available():
        raise HTTPException(status_code=503, detail="doc-write-cli is not installed")
    try:
        return HTMLResponse(render_preview_html(markdown, settings["css"]))
    except CvRenderError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
```

Replace `cv_diff_html`:

```python
@router.get(
    "/jobs/{job_id}/cv/diff.html", response_class=HTMLResponse)
def cv_diff_html(job_id: int, request: Request, version: int | None = None,
                 conn: sqlite3.Connection = Depends(get_db)):
    job = q.get_job(conn, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    row = q.get_job_cv(conn, job_id)
    if row is None or not row["tailored_cv"]:
        raise HTTPException(status_code=404, detail="No tailored CV")
    tailored = row["tailored_cv"]
    if version is not None:
        v = q.get_version(conn, "tailored", job_id, version)
        if v is None:
            raise HTTPException(status_code=404, detail="Version not found")
        tailored = v["content"]
    if not row["base_cv_snapshot"]:
        return templates.TemplateResponse(
            request, "cv/_cv_diff_fallback.html", {"predates": True, "body_html": ""},
        )
    settings = q.get_cv_settings(conn)
    try:
        annotated = build_cv_diff(row["base_cv_snapshot"], tailored).annotated_markdown
    except Exception:
        logger.exception("cv_diff build failed for job %s", job_id)
        annotated = tailored
    if doc_write_available():
        try:
            return HTMLResponse(render_diff_html(annotated, settings["css"]))
        except CvRenderError as exc:
            raise HTTPException(status_code=503, detail=str(exc))
    body_html = _markdown.markdown(annotated, extensions=["nl2br"])
    return templates.TemplateResponse(
        request, "cv/_cv_diff_fallback.html", {"predates": False, "body_html": body_html},
    )
```

Replace `cv_pdf`:

```python
@router.get("/jobs/{job_id}/cv.pdf")
def cv_pdf(job_id: int, version: int | None = None, conn: sqlite3.Connection = Depends(get_db)):
    content = None
    row = q.get_job_cv(conn, job_id)
    if version is not None:
        v = q.get_version(conn, "tailored", job_id, version)
        if v is None:
            raise HTTPException(status_code=404, detail="Version not found")
        content = v["content"]
    elif row is not None:
        content = row["tailored_cv"]
    if not content:
        raise HTTPException(status_code=404, detail="No tailored CV")
    settings = q.get_cv_settings(conn)
    try:
        data = render_pdf(content, settings["css"])
    except CvRenderError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return Response(content=data, media_type="application/pdf",
                    headers={"Content-Disposition": 'attachment; filename="cv.pdf"'})
```

- [ ] **Step 4: Rewrite `cv_preview_page` to support `?version=`**

Replace:

```python
@router.get(
    "/jobs/{job_id}/cv/preview", response_class=HTMLResponse)
def cv_preview_page(job_id: int, request: Request, version: int | None = None,
                    conn: sqlite3.Connection = Depends(get_db)):
    if q.get_job(conn, job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")
    ctx = _workbench_ctx(conn, job_id)
    if version is not None:
        v = q.get_version(conn, "tailored", job_id, version)
        if v is None:
            raise HTTPException(status_code=404, detail="Version not found")
        ctx["viewing_version"] = v
    return templates.TemplateResponse(request, "cv/preview.html", ctx)
```

- [ ] **Step 5: Repurpose `_accepted.html` into `_cv_version_view.html`**

Delete `app/templates/cv/_accepted.html`. Create `app/templates/cv/_cv_version_view.html`:

```html
{# Read-only view of one specific tailored-CV version (current or historic),
   shown at /jobs/{id}/cv/preview?version=<id>. `viewing_version` comes from
   cv_preview_page. #}
<p class="muted" style="margin:.25rem 0 .75rem;">
  Version <code>{{ viewing_version.hash }}</code> from {{ viewing_version.updated_at | time_ago }} ago
  ({{ "Update" if viewing_version.action == "update" else "Manual edit" }}){% if viewing_version.accepted_at %} &mdash; &#10003; Accepted{% endif %}.
  This is read-only. <a href="/jobs/{{ job.id }}/cv/preview">Back to current</a>
</p>

<div class="cv-preview-actions">
  <a class="btn" href="/jobs/{{ job.id }}/cv.pdf?version={{ viewing_version.id }}">Download PDF</a>
  <form method="post" action="/jobs/{{ job.id }}/cv/versions/{{ viewing_version.id }}/revert" style="display:inline;">
    <button type="submit" class="btn btn-accept">Revert to this version</button>
  </form>
</div>

{% include "cv/_cv_diff_summary.html" %}

{% if has_doc_write %}
{% with has_draft = true, mid_label = 'Version', title_prefix = 'Version ' ~ viewing_version.hash ~ ' ', active = 'tailored', editable = false, version_id = viewing_version.id %}
  {% include "cv/_preview_tabs.html" %}
{% endwith %}
{% else %}
<p class="muted">doc-write-cli is not installed — showing the raw markdown.</p>
<div class="cv-markdown" style="border:1px solid var(--border);padding:1rem;">{{ viewing_version.content | markdown }}</div>
{% endif %}
```

- [ ] **Step 6: Thread `version_id` through `_preview_tabs.html`**

In `app/templates/cv/_preview_tabs.html`, add after the existing `{% set editable = ... %}` line:

```html
{% set version_id = version_id or None %}
{% set _v = ('&version=' ~ version_id) if version_id else '' %}
```

Change the tailored iframe's `src`/`data-src` line to append `{{ _v }}`:

```html
          {{ 'src' if active == 'tailored' else 'data-src' }}="/jobs/{{ job.id }}/cv/preview.html?variant=tailored{{ _v }}"
```

Change the diff iframe's `src`/`data-src` line similarly (note this one has no existing `?` query string, so build it fresh):

```html
          {{ 'src' if active == 'diff' else 'data-src' }}="/jobs/{{ job.id }}/cv/diff.html{{ ('?version=' ~ version_id) if version_id else '' }}"
```

- [ ] **Step 7: Update `preview.html` to branch on `viewing_version`**

Replace `app/templates/cv/preview.html`'s body:

```html
{% extends "base.html" %}
{% block title %}Preview CV — {{ job.title or "Job" }}{% endblock %}
{% block content %}
{% include "jobs/_job_header.html" %}

{% if viewing_version %}
{% include "cv/_cv_version_view.html" %}
{% else %}
<div id="cv-preview-pane">{% include "cv/_preview_pane.html" %}</div>
{% endif %}

{% if updating_task_id %}
<script>
  document.addEventListener("DOMContentLoaded", function () {
    if (window.__cvWatchGenerate) window.__cvWatchGenerate({{ updating_task_id }});
  });
</script>
{% endif %}
{% endblock %}
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `python -m pytest tests/test_routes_cv_preview.py -v`
Expected: PASS

- [ ] **Step 9: Run the full suite**

Run: `python -m pytest -v 2>&1 | tail -60`
Expected: PASS — fix any remaining reference to the deleted `cv/_accepted.html` template (grep for `_accepted.html` across `app/` and `tests/` if anything fails).

- [ ] **Step 10: Commit**

```bash
git add app/routes/cv.py app/templates/cv/preview.html app/templates/cv/_preview_tabs.html \
        app/templates/cv/_cv_version_view.html tests/test_routes_cv_preview.py
git rm app/templates/cv/_accepted.html
git commit -m "$(cat <<'EOF'
feat(cv): read-only historic-version viewing for the tailored CV

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Ry4J3F2w3aCct6MRS1j7Va
EOF
)"
```

---

### Task 6: Tailored-CV version list, accepted badge, and Unaccept in the preview pane

**Files:**
- Modify: `app/routes/cv.py` (`_workbench_ctx`), `app/templates/cv/_preview_pane.html`
- Create: `app/templates/cv/_version_list.html`
- Test: `tests/test_routes_cv_actions.py`, `tests/test_routes_cv_preview.py`

**Interfaces:**
- Consumes: `q.get_versions` from Task 2.
- Produces: `_version_list.html` partial, parameterized by `versions` (list of dicts) and `version_base_url` (str) — reused by base CV in Task 7.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_routes_cv_preview.py`:

```python
def test_preview_pane_lists_prior_versions_below_the_diff_summary(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="draft 1")
    q.upsert_job_cv(conn, jid, tailored_cv="draft 2")

    r = client.get(f"/jobs/{jid}/cv/preview")
    assert r.status_code == 200
    assert 'class="cv-version-list"' in r.text
    diff_pos = r.text.index('id="cv-diff-summary"')
    tabs_pos = r.text.index('cv-preview-tabs')
    list_pos = r.text.index('cv-version-list')
    assert diff_pos < list_pos < tabs_pos


def test_preview_pane_shows_accepted_badge_and_unaccept_button(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="draft 1")
    client.post(f"/jobs/{jid}/cv/accept")

    r = client.get(f"/jobs/{jid}/cv/preview")
    assert "Accepted" in r.text
    assert f'/jobs/{jid}/cv/unaccept' in r.text
    assert f'/jobs/{jid}/cv/accept' not in r.text  # accept button replaced by unaccept
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_routes_cv_preview.py -k "version_list or accepted_badge" -v`
Expected: FAIL

- [ ] **Step 3: Create `app/templates/cv/_version_list.html`**

Base CV (Task 7) reuses this same partial with `version_base_url = "/cv"`, whose version-view route is `/cv?version=<id>` rather than `/cv/preview?version=<id>` — the link is built accordingly:

```html
{# Version-history list, shown below the diff summary and above the tab bar
   (or below the base-CV form). Params: `versions` (list of dicts with id,
   hash, action, updated_at, accepted_at), `version_base_url` ("/jobs/{id}/cv"
   or "/cv"). #}
{% if versions %}
<div class="cv-version-list">
  <h3>History</h3>
  <ul>
    {% for v in versions %}
    <li>
      <a href="{{ '/cv?version=' ~ v.id if version_base_url == '/cv' else version_base_url ~ '/preview?version=' ~ v.id }}">{{ v.updated_at | time_ago }} ago</a>
      &mdash; {{ "Update" if v.action == "update" else "Manual edit" }}
      <code>{{ v.hash }}</code>
      {% if v.accepted_at %}<span class="cv-version-accepted">&#10003; Accepted</span>{% endif %}
    </li>
    {% endfor %}
  </ul>
</div>
{% endif %}
```

- [ ] **Step 4: Wire the version list and accepted badge into `_workbench_ctx` and `_preview_pane.html`**

In `app/routes/cv.py`, in `_workbench_ctx`, add two keys to the returned dict:

```python
        "versions": q.get_versions(conn, "tailored", job_id,
                                   exclude_id=job_cv["current_version_id"] if job_cv else None),
        "version_base_url": f"/jobs/{job_id}/cv",
```

In `app/templates/cv/_preview_pane.html`, add the include right after the diff-summary div (before the `{% if has_doc_write %}` tab-bar block):

```html
<div id="cv-diff-summary">{% include "cv/_cv_diff_summary.html" %}</div>

{% include "cv/_version_list.html" %}
```

Replace the existing accept-only block at the bottom:

```html
{% if has_draft %}
  <div class="cv-preview-decision">
    <form method="post" action="/jobs/{{ job.id }}/cv/accept" style="display:inline;">
      <button type="submit" class="btn btn-accept">Accept this CV</button>
    </form>
    {% include "cv/_cv_export.html" %}
  </div>
{% endif %}
```

with:

```html
{% if has_draft %}
  {% if job_cv.accepted_at %}
  <p class="muted" style="margin:.25rem 0 .5rem;">&#10003; Accepted {{ job_cv.accepted_at | time_ago }} ago.</p>
  {% endif %}
  <div class="cv-preview-decision">
    {% if job_cv.accepted_at %}
    <form method="post" action="/jobs/{{ job.id }}/cv/unaccept" style="display:inline;">
      <button type="submit" class="btn btn-subtle">Unaccept</button>
    </form>
    {% else %}
    <form method="post" action="/jobs/{{ job.id }}/cv/accept" style="display:inline;">
      <button type="submit" class="btn btn-accept">Accept this CV</button>
    </form>
    {% endif %}
    {% include "cv/_cv_export.html" %}
  </div>
{% endif %}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_routes_cv_preview.py tests/test_routes_cv_actions.py -v`
Expected: PASS

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest -v 2>&1 | tail -40`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add app/routes/cv.py app/templates/cv/_version_list.html app/templates/cv/_preview_pane.html \
        tests/test_routes_cv_preview.py
git commit -m "$(cat <<'EOF'
feat(cv): version-history list and accepted badge in the tailored CV preview

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Ry4J3F2w3aCct6MRS1j7Va
EOF
)"
```

---

### Task 7: Base CV — version viewing, version list, and accept/unaccept UI

**Files:**
- Modify: `app/routes/cv.py` (`cv_page`, `_cv_page_ctx`), `app/templates/cv/index.html`
- Test: `tests/test_routes_cv_settings.py`

**Interfaces:**
- Consumes: `q.get_version`, `q.get_versions` from Task 2; `_version_list.html` from Task 6.
- Produces: `cv_page(version: int | None = None)`; `cv/index.html` read-only branch.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_routes_cv_settings.py`:

```python
def test_cv_page_shows_read_only_historic_version(client, conn):
    q.save_cv_settings(conn, base_cv="v1", base_instruction="", base_guardrails="",
                       css="", default_scope=[])
    old_id = q.get_cv_settings(conn)["current_version_id"]
    # Past the 1h manual-edit stacking window, so v2 opens a distinct version
    # instead of overwriting v1's row in place (see Task 2's stacking rule).
    conn.execute("UPDATE cv_versions SET updated_at = datetime('now', '-2 hours') WHERE id = ?", (old_id,))
    conn.commit()
    q.save_cv_settings(conn, base_cv="v2", base_instruction="", base_guardrails="",
                       css="", default_scope=[])

    r = client.get(f"/cv?version={old_id}")
    assert r.status_code == 200
    assert "Revert to this version" in r.text
    assert "<textarea" not in r.text  # no editor while viewing history


def test_cv_page_404s_for_unknown_version(client, conn):
    assert client.get("/cv?version=99999").status_code == 404


def test_cv_page_lists_history_and_accept_controls(client, conn):
    q.save_cv_settings(conn, base_cv="v1", base_instruction="", base_guardrails="",
                       css="", default_scope=[])
    first_id = q.get_cv_settings(conn)["current_version_id"]
    # Past the 1h stacking window, so the version list below has a non-current
    # entry to show (otherwise both saves collapse into one current version
    # and the list — which excludes current — renders empty).
    conn.execute("UPDATE cv_versions SET updated_at = datetime('now', '-2 hours') WHERE id = ?", (first_id,))
    conn.commit()
    q.save_cv_settings(conn, base_cv="v2", base_instruction="", base_guardrails="",
                       css="", default_scope=[])

    r = client.get("/cv")
    assert 'class="cv-version-list"' in r.text
    assert '/cv/accept' in r.text

    client.post("/cv/accept")
    r = client.get("/cv")
    assert "Accepted" in r.text
    assert "/cv/unaccept" in r.text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_routes_cv_settings.py -k "cv_page" -v`
Expected: FAIL

- [ ] **Step 3: Update `_cv_page_ctx` and `cv_page` in `app/routes/cv.py`**

Replace `_cv_page_ctx`:

```python
def _cv_page_ctx(conn: sqlite3.Connection) -> dict:
    settings = q.get_cv_settings(conn)
    return {
        "settings": settings,
        "has_doc_write": doc_write_available(),
        "app_version": get_app_version(),
        "build_date": get_build_date(),
        "versions": q.get_versions(conn, "base", 1, exclude_id=settings.get("current_version_id")),
        "version_base_url": "/cv",
    }
```

Replace `cv_page`:

```python
@router.get("/cv", response_class=HTMLResponse)
def cv_page(request: Request, version: int | None = None, conn: sqlite3.Connection = Depends(get_db)):
    ctx = _cv_page_ctx(conn)
    if version is not None:
        v = q.get_version(conn, "base", 1, version)
        if v is None:
            raise HTTPException(status_code=404, detail="Version not found")
        ctx["viewing_version"] = v
    return templates.TemplateResponse(request, "cv/index.html", ctx)
```

- [ ] **Step 4: Update `app/templates/cv/index.html`**

Replace the whole file body:

```html
{% extends "base.html" %}
{% block title %}CV — Job Seek{% endblock %}
{% block content %}
{% from "_markdown_editor.html" import markdown_editor %}
<div id="cv-page">
<div style="display:flex;align-items:baseline;justify-content:space-between;gap:1rem;flex-wrap:wrap;">
  <h1 style="margin:0;">CV</h1>
  <a href="/cv/advanced">Advanced CV settings &rarr;</a>
</div>
<p style="color:var(--text-muted);">Your base CV — what every tailored, per-job version starts from.</p>

{% if viewing_version %}
<p class="muted" style="margin:.25rem 0 .75rem;">
  Version <code>{{ viewing_version.hash }}</code> from {{ viewing_version.updated_at | time_ago }} ago
  {% if viewing_version.accepted_at %}&mdash; &#10003; Accepted{% endif %}. This is read-only.
  <a href="/cv">Back to editing</a>
</p>
<div class="cv-preview-actions">
  {% if has_doc_write %}<a class="btn" href="/cv.pdf?version={{ viewing_version.id }}">Download PDF</a>{% endif %}
  <form method="post" action="/cv/versions/{{ viewing_version.id }}/revert" style="display:inline;">
    <button type="submit" class="btn btn-accept">Revert to this version</button>
  </form>
</div>
{% if has_doc_write %}
<div class="cv-preview-stage" style="margin-top:1rem;">
  <iframe class="cv-preview-doc is-active" data-variant="base" title="Version {{ viewing_version.hash }}"
          src="/cv/preview.html?version={{ viewing_version.id }}" sandbox="allow-scripts allow-same-origin"></iframe>
</div>
{% endif %}

{% else %}
{% if saved %}<div class="save-confirmation" aria-live="polite">Saved.</div>{% endif %}

<form method="post" action="/cv"
      hx-post="/cv" hx-target="#cv-page" hx-select="#cv-page" hx-swap="outerHTML">
  <label>Base CV (markdown)<br>
    {{ markdown_editor("base_cv", settings.base_cv, min_height="420px") }}
  </label>
  {% include "cv/_frontmatter_hint.html" %}
  <div style="margin-top:.75rem;display:flex;gap:.5rem;flex-wrap:wrap;">
    <button type="submit" class="btn btn-primary">Save &amp; preview</button>
    {% if has_doc_write %}<a class="btn" href="/cv.pdf">Download PDF</a>{% endif %}
  </div>
</form>

<div class="cv-preview-actions" style="margin-top:.75rem;">
  {% if settings.accepted_at %}
  <p class="muted" style="margin:0;">&#10003; Accepted {{ settings.accepted_at | time_ago }} ago — this is what tailoring uses.</p>
  <form method="post" action="/cv/unaccept" style="display:inline;">
    <button type="submit" class="btn btn-subtle">Unaccept</button>
  </form>
  {% else %}
  <form method="post" action="/cv/accept" style="display:inline;">
    <button type="submit" class="btn btn-accept">Accept this version for tailoring</button>
  </form>
  {% endif %}
</div>

{% include "cv/_version_list.html" %}

<div id="cv-base-preview" style="margin-top:1rem;">
  {% if saved %}{% include "cv/_preview_result.html" %}{% endif %}
</div>
{% endif %}
</div>
{% endblock %}
```

- [ ] **Step 5: Add `version` support to `cv_preview_base_html` and `cv_base_pdf`**

Replace `cv_preview_base_html`:

```python
@router.get("/cv/preview.html", response_class=HTMLResponse)
def cv_preview_base_html(version: int | None = None, conn: sqlite3.Connection = Depends(get_db)):
    settings = q.get_cv_settings(conn)
    content = settings["base_cv"]
    if version is not None:
        v = q.get_version(conn, "base", 1, version)
        if v is None:
            raise HTTPException(status_code=404, detail="Version not found")
        content = v["content"]
    if not doc_write_available():
        raise HTTPException(status_code=503, detail="doc-write-cli is not installed")
    try:
        return HTMLResponse(render_preview_html(content, settings["css"]))
    except CvRenderError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
```

Replace `cv_base_pdf`:

```python
@router.get("/cv.pdf")
def cv_base_pdf(version: int | None = None, conn: sqlite3.Connection = Depends(get_db)):
    settings = q.get_cv_settings(conn)
    content = settings["base_cv"]
    if version is not None:
        v = q.get_version(conn, "base", 1, version)
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

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest tests/test_routes_cv_settings.py -v`
Expected: PASS

- [ ] **Step 7: Run the full suite**

Run: `python -m pytest -v 2>&1 | tail -40`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add app/routes/cv.py app/templates/cv/index.html tests/test_routes_cv_settings.py
git commit -m "$(cat <<'EOF'
feat(cv): version history, viewing, and accept/unaccept for the base CV

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Ry4J3F2w3aCct6MRS1j7Va
EOF
)"
```

---

### Task 8: Full-suite sweep and manual verification

**Files:** none new — this task verifies Tasks 1–7 hang together.

- [ ] **Step 1: Run the entire test suite**

Run: `python -m pytest -v 2>&1 | tail -80`
Expected: PASS, 0 failures. Fix anything still referencing `base_cv`/`tailored_cv` as plain `job_cv`/`cv_settings` columns (grep `PRAGMA table_info` usages and any raw `SELECT base_cv FROM cv_settings` / `SELECT tailored_cv FROM job_cv` outside `queries.py`), or any leftover `finalized_at`/`finalize_job_cv`/`unfinalize_job_cv`/`_require_editable` reference:

```bash
grep -rn "finalized_at\|finalize_job_cv\|unfinalize_job_cv\|_require_editable" app tests
grep -rn "SELECT.*base_cv FROM cv_settings\|SELECT.*tailored_cv FROM job_cv" app
```

Both should return nothing (aside from the `cv_versions`/`_CV_SETTINGS_SELECT` join queries inside `queries.py` itself, which legitimately select `cv_versions.content AS base_cv`/`AS tailored_cv`).

- [ ] **Step 2: Manual smoke test — start the dev server**

Use the `run-dev-server` skill to start the app against a throwaway `job-seek.db` copy. Then, in a browser:
1. Go to `/cv`, save a base CV, edit it again within a minute (should stack — check the version list still shows one entry), then edit it again after manually backdating (skip if not easily reproducible manually; covered by Task 2's automated tests).
2. Click "Accept this version for tailoring" — confirm the badge appears and the form is still editable.
3. Open a job, go to Tailor CV → Preview CV, click Update, then Accept — confirm you can still click Update again afterward (no freeze) and the version list shows both versions with the older one badged "✓ Accepted".
4. Click a historic version's timestamp link — confirm it shows read-only content with a "Revert to this version" button, and that button works (content swaps back, no page crash).
5. Download a PDF from a historic version link and confirm it opens.

Report back to the user with the dev server URL for their own click-through per this project's "UI dev-server handoff" convention — do not merge or clean up until they've said to proceed.

- [ ] **Step 3: Stop the dev server once the user confirms, then proceed to the finishing-a-development-branch flow**

(Per `CLAUDE.md`: squash-merge the worktree branch into local `main`, re-run the full suite on `main`, then remove the worktree and branch.)
