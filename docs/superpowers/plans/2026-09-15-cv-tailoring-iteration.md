# CV tailoring: three-way iteration + labeled history Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the per-job CV workbench three explicit iteration actions — Apply tailoring plan (relabeled LLM update), manual edits (unchanged), and a new Reset to base CV — with `cv_versions` history entries labeled Applied plan/Manual edit/Reset, correct parent linkage, and the applied edit-scope recorded as a note. Also: "Apply tailoring plan" now iterates from the job's current tailored CV instead of always regenerating from base.

**Architecture:** One schema change (widen `cv_versions.action`, add a `note` column) underpins everything else. A new query function (`reset_job_cv_to_base`) and route give the reset action its own history-recording path, reusing the existing version-recording machinery (`_record_version`) with one new override (an explicit parent). The generate task gains a source-document swap and a scope-name snapshot into the new `note` column. A single shared Jinja macro (`version_meta`) renders the new label/note everywhere history is shown.

**Tech Stack:** Python (FastAPI, sqlite3), Jinja2 templates, pytest.

## Global Constraints

- `cv_versions.action` is constrained to exactly `('update','manual_edit','reset')` — no other values, and the CHECK constraint must enforce this (not just app-level validation).
- `cv_versions.note` is `TEXT NOT NULL DEFAULT ''` — never NULL, empty string when not applicable.
- Action → history label is a fixed mapping: `update` → "Applied plan", `manual_edit` → "Manual edit", `reset` → "Reset". No entity-type branching (base CV never has `action='update'`).
- `note` is populated only on `action='update'` versions of the **tailored** entity — the comma-joined `name` (falling back to `description`) of the job's enabled `cv_scope_options` at generation time, in `cv_scope_options`' `sort_order` order. Always `''` for `manual_edit`, `reset`, and any base-CV version.
- `version_meta()`'s rendered segment order is: age, action label, parent, note — each segment omitted entirely when empty/absent (no stray separators).
- "Reset to base CV" changes tailored **content only** — `scope`, `tuning_directives`, `plan`, and `handled_suggestions` on `job_cv` are never touched by it.
- A reset's `parent_version_id` is the resolved base CV version (`resolve_base_version_id`), never the tailored version it replaces — even though it is not the job's first tailored version.
- `check_guardrails()` and `plan_tailoring()` always receive the **true** base CV (`settings["base_cv"]` / `resolve_base_cv(conn)`), never whatever document `tailor_cv()` iterated from.
- All new/changed routes and queries follow the existing patterns in `app/routes/cv_versions.py` and `app/db/queries.py` (see each task) — don't introduce a new pattern where an existing one already fits.

---

### Task 1: Widen `cv_versions.action`, add `note` column

**Files:**
- Modify: `app/db/schema.py:11-21` (top-level DDL)
- Modify: `app/db/schema.py:1251` (register new migration call)
- Test: `tests/test_cv_versions.py`

**Interfaces:**
- Produces: `cv_versions.action` accepts `'reset'` in addition to `'update'`/`'manual_edit'`; `cv_versions.note TEXT NOT NULL DEFAULT ''`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cv_versions.py`:

```python
def test_cv_versions_action_allows_reset_and_note_defaults_empty(conn):
    conn.execute(
        "INSERT INTO cv_versions (hash, entity_type, entity_id, content, action) "
        "VALUES ('abcd1234', 'tailored', 1, 'content', 'reset')"
    )
    conn.commit()
    row = conn.execute("SELECT action, note FROM cv_versions WHERE hash = 'abcd1234'").fetchone()
    assert row["action"] == "reset"
    assert row["note"] == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cv_versions.py::test_cv_versions_action_allows_reset_and_note_defaults_empty -v`
Expected: FAIL with `sqlite3.IntegrityError: CHECK constraint failed: action` (or similar — the current schema only allows `'update'`/`'manual_edit'`).

- [ ] **Step 3: Widen the top-level DDL**

In `app/db/schema.py`, replace lines 11-21:

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
```

with:

```sql
CREATE TABLE IF NOT EXISTS cv_versions (
    id INTEGER PRIMARY KEY,
    hash TEXT NOT NULL UNIQUE,
    entity_type TEXT NOT NULL CHECK (entity_type IN ('base','tailored')),
    entity_id INTEGER NOT NULL,
    parent_version_id INTEGER REFERENCES cv_versions(id) ON DELETE SET NULL,
    content TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('update','manual_edit','reset')),
    note TEXT NOT NULL DEFAULT '',
    accepted_at TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

- [ ] **Step 4: Add the migration function**

In `app/db/schema.py`, add this function immediately after `_migrate_cv_content_to_versions` (which ends just before `_migrate_cv_scope_options_drop_is_baseline`, around line 1104):

```python
def _migrate_cv_versions_add_reset_and_note(conn: sqlite3.Connection) -> None:
    """Widens cv_versions.action to allow 'reset' (SQLite can't alter a CHECK
    constraint in place) and adds the note column, in one rebuild. See the
    2026-09-15 CV tailoring iteration spec."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='cv_versions'"
    ).fetchone()
    if row is None or "'reset'" in row[0]:
        return
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript(
        """
        CREATE TABLE cv_versions_new (
            id INTEGER PRIMARY KEY,
            hash TEXT NOT NULL UNIQUE,
            entity_type TEXT NOT NULL CHECK (entity_type IN ('base','tailored')),
            entity_id INTEGER NOT NULL,
            parent_version_id INTEGER REFERENCES cv_versions_new(id) ON DELETE SET NULL,
            content TEXT NOT NULL,
            action TEXT NOT NULL CHECK (action IN ('update','manual_edit','reset')),
            note TEXT NOT NULL DEFAULT '',
            accepted_at TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        INSERT INTO cv_versions_new (id, hash, entity_type, entity_id, parent_version_id,
                                      content, action, note, accepted_at, updated_at)
        SELECT id, hash, entity_type, entity_id, parent_version_id,
               content, action, '', accepted_at, updated_at
        FROM cv_versions;
        DROP TABLE cv_versions;
        ALTER TABLE cv_versions_new RENAME TO cv_versions;
        CREATE INDEX idx_cv_versions_entity ON cv_versions(entity_type, entity_id, id);
        """
    )
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
```

- [ ] **Step 5: Register the migration**

In `app/db/schema.py`, change line 1251 from:

```python
    _migrate_cv_content_to_versions(conn)
```

to:

```python
    _migrate_cv_content_to_versions(conn)
    _migrate_cv_versions_add_reset_and_note(conn)
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/test_cv_versions.py::test_cv_versions_action_allows_reset_and_note_defaults_empty -v`
Expected: PASS

- [ ] **Step 7: Run the full test suite to check for regressions**

Run: `uv run pytest -q`
Expected: All tests pass (this is a schema widen + additive column; nothing existing should break).

- [ ] **Step 8: Commit**

```bash
git add app/db/schema.py tests/test_cv_versions.py
git commit -m "$(cat <<'EOF'
feat(cv): widen cv_versions.action to allow reset, add note column

Lays the schema groundwork for a "Reset to base CV" action and for
recording the applied edit-scope on an LLM-generated version.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_014AGqSLUXcmZFngZmNC65ma
EOF
)"
```

---

### Task 2: `note` support in `_record_version` / `upsert_job_cv`

**Files:**
- Modify: `app/db/queries.py:53-104` (`_record_version`)
- Modify: `app/db/queries.py:448-474` (`upsert_job_cv`)
- Test: `tests/test_cv_versions.py`

**Interfaces:**
- Consumes: Task 1's `cv_versions.note` column.
- Produces: `_record_version(conn, entity_type, entity_id, *, action, content, current_version_id, initial_parent_id=None, parent_id_override=None, note="")` — `parent_id_override`, when given, is used as the new version's parent instead of the current-version/initial-parent chain (consumed by Task 3). `upsert_job_cv(conn, job_id, tailored_cv=..., note=..., **other_fields)` threads `note` through when `tailored_cv` is given; defaults to `""`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cv_versions.py` (uses the existing `_job` helper already in this file):

```python
def test_upsert_job_cv_records_note_on_update_version(conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="draft 1", note="correct, choose")
    version_id = q.get_job_cv(conn, jid)["current_version_id"]
    version = q.get_version(conn, "tailored", jid, version_id)
    assert version["action"] == "update"
    assert version["note"] == "correct, choose"


def test_upsert_job_cv_note_defaults_to_empty(conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="draft 1")
    version_id = q.get_job_cv(conn, jid)["current_version_id"]
    assert q.get_version(conn, "tailored", jid, version_id)["note"] == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cv_versions.py -k note -v`
Expected: FAIL with `TypeError: upsert_job_cv() got an unexpected keyword argument 'note'` (the first test), and the second test should currently pass already (note doesn't exist as a concept yet, so nothing to assert against — actually it will fail with `KeyError: 'note'` since the column exists from Task 1 but nothing ever sets it... it should already pass once Task 1 lands, since the column defaults to `''`. Confirm both fail/pass as stated before moving on; only the first must fail here).

- [ ] **Step 3: Add `note`/`parent_id_override` to `_record_version`**

In `app/db/queries.py`, replace the whole `_record_version` function (lines 53-104) with:

```python
def _record_version(
    conn: sqlite3.Connection, entity_type: str, entity_id: int, *,
    action: str, content: str, current_version_id: int | None,
    initial_parent_id: int | None = None, parent_id_override: int | None = None,
    note: str = "",
) -> int:
    """Returns the version id that should become current. A manual edit
    stacks onto the current row in place when that row is itself an
    unaccepted manual edit less than an hour old AND is still the entity's
    newest version (so a revert followed by a quick edit always opens a
    fresh version instead of overwriting what was just reverted to);
    anything else (an update, an accepted current version, or a lapsed
    window) opens a new version. A no-op save (content unchanged) returns
    the current version untouched without creating or mutating anything —
    this keeps settings-only saves that round-trip an unedited base_cv from
    spuriously versioning it (or clearing its accepted marker).

    `initial_parent_id` only takes effect when this entity has no version at
    all yet (current_version_id is None) — it lets a job's very first
    tailored version record the base-CV version it was generated from as its
    parent, rather than leaving parent_version_id NULL.

    `parent_id_override`, when given, is used as the new version's parent
    instead of the usual current-version/initial-parent chain — used by a
    reset, whose parent is the base CV version it reset to rather than the
    tailored version it replaces, even when that isn't the entity's first
    version."""
    current = None
    if current_version_id is not None:
        current = conn.execute(
            "SELECT action, accepted_at, content, "
            "(julianday('now') - julianday(updated_at)) * 24 AS age_hours "
            "FROM cv_versions WHERE id = ?",
            (current_version_id,),
        ).fetchone()
    if current is not None and current["content"] == content:
        return current_version_id
    if (
        action == "manual_edit" and current is not None
        and current["action"] == "manual_edit" and current["accepted_at"] is None
        and current["age_hours"] < 1
        and current_version_id == conn.execute(
            "SELECT MAX(id) FROM cv_versions WHERE entity_type = ? AND entity_id = ?",
            (entity_type, entity_id),
        ).fetchone()[0]
    ):
        conn.execute(
            "UPDATE cv_versions SET content = ?, updated_at = datetime('now') WHERE id = ?",
            (content, current_version_id),
        )
        return current_version_id
    parent_id = parent_id_override
    if parent_id is None:
        parent_id = current_version_id if current_version_id is not None else initial_parent_id
    new_id = conn.execute(
        "INSERT INTO cv_versions (hash, entity_type, entity_id, parent_version_id, content, action, note, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))",
        (_new_version_hash(conn), entity_type, entity_id, parent_id, content, action, note),
    ).lastrowid
    _prune_versions(conn, entity_type, entity_id)
    return new_id
```

- [ ] **Step 4: Thread `note` through `upsert_job_cv`**

In `app/db/queries.py`, replace `upsert_job_cv` (lines 448-474) with:

```python
def upsert_job_cv(conn: sqlite3.Connection, job_id: int, **fields) -> None:
    conn.execute("INSERT OR IGNORE INTO job_cv (job_id) VALUES (?)", (job_id,))
    tailored_cv = fields.pop("tailored_cv", None)
    note = fields.pop("note", "")
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
            initial_parent_id=resolve_base_version_id(conn),
            note=note,
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_cv_versions.py -k note -v`
Expected: PASS (both tests)

- [ ] **Step 6: Run the full test suite to check for regressions**

Run: `uv run pytest -q`
Expected: All tests pass — `note` defaults to `""` everywhere it isn't explicitly passed, so no existing caller of `upsert_job_cv` or `_record_version` changes behavior.

- [ ] **Step 7: Commit**

```bash
git add app/db/queries.py tests/test_cv_versions.py
git commit -m "$(cat <<'EOF'
feat(cv): thread note and parent override through version recording

_record_version gains parent_id_override (for a reset's parent, which
skips the normal chain) and note (a free-text field snapshotted on
'update' versions). upsert_job_cv threads note through.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_014AGqSLUXcmZFngZmNC65ma
EOF
)"
```

---

### Task 3: `reset_job_cv_to_base` query function

**Files:**
- Modify: `app/db/queries.py` (add after `set_job_cv_tailored`, which ends at line 502)
- Test: `tests/test_cv_versions.py`

**Interfaces:**
- Consumes: `_record_version(..., parent_id_override=...)` from Task 2; existing `resolve_base_cv(conn) -> str` and `resolve_base_version_id(conn) -> int | None`.
- Produces: `reset_job_cv_to_base(conn: sqlite3.Connection, job_id: int) -> None`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cv_versions.py`:

```python
def test_reset_job_cv_to_base_copies_base_content_and_labels_reset(conn):
    _save_base(conn, "base content")
    base_id = q.get_cv_settings(conn)["current_version_id"]
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="tailored draft")

    q.reset_job_cv_to_base(conn, jid)

    row = q.get_job_cv(conn, jid)
    assert row["tailored_cv"] == "base content"
    version = q.get_version(conn, "tailored", jid, row["current_version_id"])
    assert version["action"] == "reset"
    assert version["parent_version_id"] == base_id   # base version, not the prior tailored one


def test_reset_job_cv_to_base_leaves_scope_and_directives_untouched(conn):
    _save_base(conn, "base content")
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="tailored draft", scope=[1, 2])
    q.set_job_cv_directives(conn, jid, "- my directive")

    q.reset_job_cv_to_base(conn, jid)

    row = q.get_job_cv(conn, jid)
    assert row["scope"] == [1, 2]
    assert row["tuning_directives"] == "- my directive"


def test_reset_job_cv_to_base_is_a_noop_when_already_matching_base(conn):
    _save_base(conn, "base content")
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="base content")   # already equals base
    before_id = q.get_job_cv(conn, jid)["current_version_id"]

    q.reset_job_cv_to_base(conn, jid)

    row = q.get_job_cv(conn, jid)
    assert row["current_version_id"] == before_id     # no new version created
    assert len(q.get_versions(conn, "tailored", jid)) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cv_versions.py -k reset_job_cv_to_base -v`
Expected: FAIL with `AttributeError: module 'app.db.queries' has no attribute 'reset_job_cv_to_base'`

- [ ] **Step 3: Implement `reset_job_cv_to_base`**

In `app/db/queries.py`, add this function immediately after `set_job_cv_tailored` (after line 502):

```python
def reset_job_cv_to_base(conn: sqlite3.Connection, job_id: int) -> None:
    """Content-only reset: replaces the tailored CV with the resolved base CV
    (the accepted base version, or the current one if nothing's accepted),
    recording a 'reset' version whose parent is that base version rather than
    the tailored version it replaces. Scope, tuning directives, plan and
    handled suggestions are left untouched — mirrors set_job_cv_tailored's
    write shape otherwise."""
    conn.execute("INSERT OR IGNORE INTO job_cv (job_id) VALUES (?)", (job_id,))
    current_version_id = conn.execute(
        "SELECT current_version_id FROM job_cv WHERE job_id = ?", (job_id,)
    ).fetchone()[0]
    new_version_id = _record_version(
        conn, "tailored", job_id, action="reset", content=resolve_base_cv(conn),
        current_version_id=current_version_id,
        parent_id_override=resolve_base_version_id(conn),
    )
    conn.execute(
        "UPDATE job_cv SET current_version_id = ?, edited_at = datetime('now'), "
        "updated_at = datetime('now') WHERE job_id = ?",
        (new_version_id, job_id),
    )
    conn.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cv_versions.py -k reset_job_cv_to_base -v`
Expected: PASS (all three tests)

- [ ] **Step 5: Run the full test suite to check for regressions**

Run: `uv run pytest -q`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add app/db/queries.py tests/test_cv_versions.py
git commit -m "$(cat <<'EOF'
feat(cv): add reset_job_cv_to_base query function

Content-only reset of a job's tailored CV back to the resolved base
CV, recorded as a 'reset' version parented to the base version rather
than the tailored version it replaces.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_014AGqSLUXcmZFngZmNC65ma
EOF
)"
```

---

### Task 4: `POST /jobs/{job_id}/cv/reset-to-base` route

**Files:**
- Modify: `app/routes/cv.py` (add route after `cv_save_tailored`, which ends at line 831, before `cv_reset_directives`)
- Test: `tests/test_routes_cv_actions.py`

**Interfaces:**
- Consumes: `q.reset_job_cv_to_base(conn, job_id)` from Task 3; `_require_job(conn, job_id)` and `q.add_job_event(conn, job_id, kind, message)` (both already exist in `cv.py`).
- Produces: `POST /jobs/{job_id}/cv/reset-to-base` → 404 if the job doesn't exist, otherwise resets and redirects to `/jobs/{job_id}/cv/preview` (303).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_routes_cv_actions.py` (uses the existing `_job` helper already in this file — it saves `base_cv="# Me"`):

```python
def test_reset_to_base_replaces_tailored_content_and_logs_event(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="tailored draft")

    r = client.post(f"/jobs/{jid}/cv/reset-to-base", follow_redirects=False)

    assert r.status_code == 303
    assert r.headers["location"] == f"/jobs/{jid}/cv/preview"
    row = q.get_job_cv(conn, jid)
    assert row["tailored_cv"] == "# Me"
    version = q.get_version(conn, "tailored", jid, row["current_version_id"])
    assert version["action"] == "reset"
    assert "cv" in [e["kind"] for e in q.get_job_events(conn, jid)]


def test_reset_to_base_404_for_missing_job(client, conn):
    assert client.post("/jobs/999/cv/reset-to-base").status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_routes_cv_actions.py -k reset_to_base -v`
Expected: FAIL with 404 Not Found (route doesn't exist yet) on the first test's assertion, and the second test already passes trivially (any undefined route 404s) — the meaningful failure is the first test.

- [ ] **Step 3: Implement the route**

In `app/routes/cv.py`, add immediately after `cv_save_tailored` (after line 831, before the `cv_reset_directives` route at line 834):

```python
@router.post("/jobs/{job_id}/cv/reset-to-base")
def cv_reset_to_base(job_id: int, conn: sqlite3.Connection = Depends(get_db)):
    _require_job(conn, job_id)
    q.reset_job_cv_to_base(conn, job_id)
    q.add_job_event(conn, job_id, "cv", "Reset to base CV")
    return RedirectResponse(f"/jobs/{job_id}/cv/preview", status_code=303)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_routes_cv_actions.py -k reset_to_base -v`
Expected: PASS (both tests)

- [ ] **Step 5: Run the full test suite to check for regressions**

Run: `uv run pytest -q`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add app/routes/cv.py tests/test_routes_cv_actions.py
git commit -m "$(cat <<'EOF'
feat(cv): add reset-to-base route for the tailored CV workbench

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_014AGqSLUXcmZFngZmNC65ma
EOF
)"
```

---

### Task 5: Relabel "Update" → "Apply tailoring plan", add "Reset to base CV" button

**Files:**
- Modify: `app/templates/cv/_preview_pane.html:30-42`
- Modify: `tests/test_routes_cv_preview.py:195,402` (button-text assertions) and the comment at `:400-401`
- Test: `tests/test_routes_cv_preview.py`

**Interfaces:**
- Consumes: `POST /jobs/{job_id}/cv/reset-to-base` route from Task 4.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_routes_cv_preview.py`:

```python
def test_preview_pane_has_reset_to_base_button(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="draft 1")
    r = client.get(f"/jobs/{jid}/cv/preview")
    assert f'action="/jobs/{jid}/cv/reset-to-base"' in r.text
    assert ">Reset to base CV</button>" in r.text
    assert ">Apply tailoring plan</button>" in r.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_routes_cv_preview.py::test_preview_pane_has_reset_to_base_button -v`
Expected: FAIL — none of the three strings are in the page yet.

- [ ] **Step 3: Update the template**

In `app/templates/cv/_preview_pane.html`, replace lines 30-42:

```html
<div class="cv-preview-actions">
  <button type="button" class="btn btn-primary" data-progress-url="/jobs/{{ job.id }}/cv/generate"
          data-progress-label="Updating the tailored CV" data-progress-oob>Update</button>
  <span class="cv-preview-progress" aria-live="polite"{% if not updating_task_id %} hidden{% endif %}>&#9203; A tailored update is in progress&hellip;</span>
  <span id="cv-editor-status" class="cv-editor-status" aria-live="polite"></span>
  {% if not has_doc_write %}<span class="muted">(doc-write-cli not installed)</span>{% endif %}
</div>

{% if not has_draft %}
<p class="cv-preview-empty muted">No tailored CV yet — review the plan on the
  <a href="/jobs/{{ job.id }}/cv">Tailor CV</a> page, then hit
  <strong>Update</strong>. Your base CV is shown below for reference.</p>
{% endif %}
```

with:

```html
<div class="cv-preview-actions">
  <button type="button" class="btn btn-primary" data-progress-url="/jobs/{{ job.id }}/cv/generate"
          data-progress-label="Applying the tailoring plan" data-progress-oob>Apply tailoring plan</button>
  <form method="post" action="/jobs/{{ job.id }}/cv/reset-to-base" style="display:inline;">
    <button type="submit" class="btn btn-subtle">Reset to base CV</button>
  </form>
  <span class="cv-preview-progress" aria-live="polite"{% if not updating_task_id %} hidden{% endif %}>&#9203; A tailored update is in progress&hellip;</span>
  <span id="cv-editor-status" class="cv-editor-status" aria-live="polite"></span>
  {% if not has_doc_write %}<span class="muted">(doc-write-cli not installed)</span>{% endif %}
</div>

{% if not has_draft %}
<p class="cv-preview-empty muted">No tailored CV yet — review the plan on the
  <a href="/jobs/{{ job.id }}/cv">Tailor CV</a> page, then hit
  <strong>Apply tailoring plan</strong>. Your base CV is shown below for reference.</p>
{% endif %}
```

- [ ] **Step 4: Update the two existing button-text assertions**

In `tests/test_routes_cv_preview.py`, line 195, change:

```python
    assert ">Update</button>" in actions
```

to:

```python
    assert ">Apply tailoring plan</button>" in actions
```

And around lines 400-402, change:

```python
    # Accepting no longer freezes the view — the normal editable preview pane
    # (with its Update button and edit affordances) still renders.
    assert ">Update</button>" in r.text
```

to:

```python
    # Accepting no longer freezes the view — the normal editable preview pane
    # (with its Apply-tailoring-plan button and edit affordances) still renders.
    assert ">Apply tailoring plan</button>" in r.text
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_routes_cv_preview.py -v`
Expected: PASS (all tests in this file, including the new one and the two updated assertions)

- [ ] **Step 6: Run the full test suite to check for regressions**

Run: `uv run pytest -q`
Expected: All tests pass.

- [ ] **Step 7: Commit**

```bash
git add app/templates/cv/_preview_pane.html tests/test_routes_cv_preview.py
git commit -m "$(cat <<'EOF'
feat(cv): relabel Update to Apply tailoring plan, add Reset to base CV button

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_014AGqSLUXcmZFngZmNC65ma
EOF
)"
```

---

### Task 6: History shows action label + note, reordered

**Files:**
- Modify: `app/templates/cv/_version_list.html:44-46` (`version_meta` macro)
- Test: `tests/test_routes_cv_preview.py`

**Interfaces:**
- Consumes: `v.action` and `v.note` fields, present on every row `get_versions`/`get_version` return (from Task 1's schema change; `v.*` is already selected by `_VERSION_SELECT_WITH_PARENT`, so no query change is needed here).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_routes_cv_preview.py`:

```python
def test_history_meta_shows_age_type_parent_note_in_order(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="draft 1", note="scope-alpha")
    first_hash = q.get_job_cv(conn, jid)["current_version_hash"]
    q.upsert_job_cv(conn, jid, tailored_cv="draft 2", note="scope-beta")

    r = client.get(f"/jobs/{jid}/cv/preview")
    list_start = r.text.index('id="cv-version-list"')
    list_end = r.text.index('id="cv-findings"', list_start)
    history = r.text[list_start:list_end]

    assert "Applied plan" in history
    assert f"from version {first_hash[:6]}" in history
    assert "scope-beta" in history

    # The current (second) row's meta line: age, then "Applied plan", then
    # "from version <first_hash>", then its own note — in that order.
    parent_pos = history.index(f"from version {first_hash[:6]}")
    label_pos = history.rindex("Applied plan", 0, parent_pos)
    note_pos = history.index("scope-beta", parent_pos)
    assert label_pos < parent_pos < note_pos
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_routes_cv_preview.py::test_history_meta_shows_age_type_parent_note_in_order -v`
Expected: FAIL — "Applied plan" and the note text aren't rendered anywhere yet.

- [ ] **Step 3: Update `version_meta`**

In `app/templates/cv/_version_list.html`, replace lines 44-46:

```jinja
{% macro version_meta(v) -%}
{{ v.updated_at | age }}{% if v.parent_hash %} &middot; from {{ 'cv' if v.parent_entity_type != v.entity_type else 'version' }} {{ v.parent_hash[:6] }}{% endif %}
{%- endmacro %}
```

with:

```jinja
{% set _ACTION_LABELS = {'update': 'Applied plan', 'manual_edit': 'Manual edit', 'reset': 'Reset'} %}
{% macro version_meta(v) -%}
{{ v.updated_at | age }}{% if v.action in _ACTION_LABELS %} &middot; {{ _ACTION_LABELS[v.action] }}{% endif %}{% if v.parent_hash %} &middot; from {{ 'cv' if v.parent_entity_type != v.entity_type else 'version' }} {{ v.parent_hash[:6] }}{% endif %}{% if v.note %} &middot; {{ v.note }}{% endif %}
{%- endmacro %}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_routes_cv_preview.py::test_history_meta_shows_age_type_parent_note_in_order -v`
Expected: PASS

- [ ] **Step 5: Run the full test suite to check for regressions**

Run: `uv run pytest -q`
Expected: All tests pass — `version_meta` is shared by the base CV page, the live tailored preview pane, and the read-only version-view pages, so a broad pass here also verifies nothing else broke.

- [ ] **Step 6: Commit**

```bash
git add app/templates/cv/_version_list.html tests/test_routes_cv_preview.py
git commit -m "$(cat <<'EOF'
feat(cv): show action label and note in version history, reordered

version_meta() now renders age, action label (Applied plan / Manual
edit / Reset), parent, then note — each segment omitted when absent.
Shared by every history display (base CV, tailored preview, read-only
version views).

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_014AGqSLUXcmZFngZmNC65ma
EOF
)"
```

---

### Task 7: "Apply tailoring plan" iterates from the current tailored CV

**Files:**
- Modify: `app/ai/tailor_cv.py:148-171` (`_tailor_system`), `:183-203` (`tailor_cv`)
- Modify: `app/cv/instruction.py:89-93,108-113` (`compose_instruction` wording)
- Modify: `app/routes/cv.py:728-740` (`_task_cv_tailor`'s generate branch)
- Modify: `tests/test_tailor_cv_generate.py:72-80` (`test_stable_blocks_precede_the_instruction`)
- Test: `tests/test_cv_task.py`

**Interfaces:**
- Produces: `tailor_cv(client, model, source_cv, instruction, job_context, *, guardrails="", ...)` — renamed positional parameter (was `base_cv`), no behavior change to callers using positional args (the only production call site, `app/routes/cv.py`, already calls positionally). `_task_cv_tailor`'s generate branch now computes `source_cv = row["tailored_cv"] or settings["base_cv"]` and passes that to `tailor_cv()` instead of always passing `settings["base_cv"]`. `check_guardrails()`'s call is untouched — still receives `settings["base_cv"]`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cv_task.py` (uses the existing `_seed`/`_run` helpers already in this file — `_seed` saves `base_cv="# Me\n\n- Kafka work\n"` and no tailored CV yet):

```python
def test_generate_sources_from_existing_tailored_cv_not_base(conn, cfg):
    jid = _seed(conn)
    q.upsert_job_cv(conn, jid, scope=[1], tailored_cv="# Existing tailored draft\n- prior edit\n")
    with patch("app.routes.cv.tailor_cv", return_value={"markdown": "# regenerated"}) as tc, \
         patch("app.routes.cv.check_guardrails", return_value={"findings": []}):
        _run(conn, cfg, jid, "generate")
    source_cv = tc.call_args.args[2]
    assert source_cv == "# Existing tailored draft\n- prior edit\n"


def test_generate_sources_from_base_when_no_tailored_cv_yet(conn, cfg):
    jid = _seed(conn)
    with patch("app.routes.cv.tailor_cv", return_value={"markdown": "# first draft"}) as tc, \
         patch("app.routes.cv.check_guardrails", return_value={"findings": []}):
        _run(conn, cfg, jid, "generate")
    source_cv = tc.call_args.args[2]
    assert source_cv == "# Me\n\n- Kafka work\n"


def test_generate_check_guardrails_still_uses_true_base_cv_not_source(conn, cfg):
    jid = _seed(conn)
    q.upsert_job_cv(conn, jid, scope=[1], tailored_cv="# Existing tailored draft\n")
    with patch("app.routes.cv.tailor_cv", return_value={"markdown": "# regenerated"}), \
         patch("app.routes.cv.check_guardrails", return_value={"findings": []}) as cg:
        _run(conn, cfg, jid, "generate")
    # check_guardrails(client, model, base_guardrails, base_cv, tailored_cv)
    assert cg.call_args.args[3] == "# Me\n\n- Kafka work\n"
```

- [ ] **Step 2: Run tests to verify the first two fail**

Run: `uv run pytest tests/test_cv_task.py -k "sources_from" -v`
Expected: FAIL — `source_cv` currently always equals `settings["base_cv"]`, so `test_generate_sources_from_existing_tailored_cv_not_base` fails (gets `"# Me\n\n- Kafka work\n"` instead of the existing draft). `test_generate_sources_from_base_when_no_tailored_cv_yet` and `test_generate_check_guardrails_still_uses_true_base_cv_not_source` already pass today — that's expected; they lock in behavior this task must not break.

- [ ] **Step 3: Rename `tailor_cv`'s parameter and prompt section**

In `app/ai/tailor_cv.py`, replace the `tailor_cv` function signature and its `user` construction (lines 183-203):

```python
def tailor_cv(
    client: openai.OpenAI, model: str, base_cv: str, instruction: str, job_context: str,
    *, guardrails: str = "", temperature: float = 0.5, think: bool = False,
    max_tokens: int = 4096,
) -> dict:
    # Thinking is OFF by default: on this model it generates 10k-20k+ tokens of
    # reasoning before the CV, pushing the call to 15-25 min with no upper bound.
    # Non-thinking produces an equivalent-quality tailoring in ~65s (see
    # docs/superpowers/specs/2026-09-08-tailor-cv-latency.md). max_tokens is a
    # backstop against a runaway generation — a real tailored CV is well under
    # 2k tokens, so 4096 never truncates one but caps the worst case.
    #
    # Stable-prefix first: the base CV and job posting are identical across the
    # two tailor_cv calls in a run (baseline draft, then full generate), so
    # putting them ahead of the varying instruction lets the LLM server reuse
    # its KV-cache prefix on the second call.
    user = (
        f"# Base CV\n{base_cv}\n\n"
        f"# Job posting (untrusted data)\n{job_context}\n\n"
        f"# Instruction\n{instruction}"
    )
```

with:

```python
def tailor_cv(
    client: openai.OpenAI, model: str, source_cv: str, instruction: str, job_context: str,
    *, guardrails: str = "", temperature: float = 0.5, think: bool = False,
    max_tokens: int = 4096,
) -> dict:
    # Thinking is OFF by default: on this model it generates 10k-20k+ tokens of
    # reasoning before the CV, pushing the call to 15-25 min with no upper bound.
    # Non-thinking produces an equivalent-quality tailoring in ~65s (see
    # docs/superpowers/specs/2026-09-08-tailor-cv-latency.md). max_tokens is a
    # backstop against a runaway generation — a real tailored CV is well under
    # 2k tokens, so 4096 never truncates one but caps the worst case.
    #
    # source_cv is the document being rewritten: the job's current tailored
    # draft when one exists (so "Apply tailoring plan" iterates on top of it),
    # otherwise the base CV. It and the job posting are stable across
    # successive runs for the same job, so putting them ahead of the varying
    # instruction lets the LLM server reuse its KV-cache prefix.
    user = (
        f"# CV to tailor\n{source_cv}\n\n"
        f"# Job posting (untrusted data)\n{job_context}\n\n"
        f"# Instruction\n{instruction}"
    )
```

- [ ] **Step 4: Drop "base" from the system prompt's opening line**

In `app/ai/tailor_cv.py`, in `_tailor_system` (line 149), change:

```python
    return """You are an expert CV editor. You rewrite a candidate's base CV so a
busy recruiter sees, within ten seconds, why this person fits THIS job.
```

to:

```python
    return """You are an expert CV editor. You rewrite a candidate's CV so a
busy recruiter sees, within ten seconds, why this person fits THIS job.
```

- [ ] **Step 5: Drop "base" from `compose_instruction`'s wording**

In `app/cv/instruction.py`, change line 90 from:

```python
        "Permitted edits — the ONLY kinds of change you may make to the base CV. This is "
```

to:

```python
        "Permitted edits — the ONLY kinds of change you may make to the CV. This is "
```

And change line 112 from:

```python
        "content as it stands in the base CV."
```

to:

```python
        "content as it stands in the CV."
```

- [ ] **Step 6: Compute and pass `source_cv` in the generate branch**

In `app/routes/cv.py`, in `_task_cv_tailor`, replace (lines 728-740):

```python
    # mode == "generate"
    if row is None:
        q.upsert_job_cv(conn, job_id, scope=[o["id"] for o in scope_options if o["default_enabled"]])
        row = q.get_job_cv(conn, job_id)
    instr = compose_instruction(
        base_instruction=settings["base_instruction"], scope=row["scope"],
        scope_options=scope_options, guardrails=settings["base_guardrails"],
        tuning_directives=row["tuning_directives"],
    )
    yield "Generating the tailored CV… (LLM call: tailor_cv)"
    t0 = time.monotonic()
    result = tailor_cv(client, model, settings["base_cv"], instr, jc,
                       guardrails=settings["base_guardrails"])
```

with:

```python
    # mode == "generate"
    if row is None:
        q.upsert_job_cv(conn, job_id, scope=[o["id"] for o in scope_options if o["default_enabled"]])
        row = q.get_job_cv(conn, job_id)
    instr = compose_instruction(
        base_instruction=settings["base_instruction"], scope=row["scope"],
        scope_options=scope_options, guardrails=settings["base_guardrails"],
        tuning_directives=row["tuning_directives"],
    )
    source_cv = row["tailored_cv"] or settings["base_cv"]
    yield "Generating the tailored CV… (LLM call: tailor_cv)"
    t0 = time.monotonic()
    result = tailor_cv(client, model, source_cv, instr, jc,
                       guardrails=settings["base_guardrails"])
```

(The `check_guardrails(...)` call further down, which already passes `settings["base_cv"]`, is unchanged.)

- [ ] **Step 7: Update the stale test asserting on the old prompt header**

In `tests/test_tailor_cv_generate.py`, replace `test_stable_blocks_precede_the_instruction` (lines 72-80):

```python
def test_stable_blocks_precede_the_instruction():
    # The base CV and job posting are stable across the two tailor_cv calls in a
    # run; the instruction is not. Ordering the stable blocks first lets the LLM
    # server reuse the KV-cache prefix on the second call.
    client = _mock_client("# CV\n- x")
    tailor_cv(client, "m", "# base cv", "the instruction", "the job posting")
    user = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    assert user.index("# Base CV") < user.index("# Instruction")
    assert user.index("# Job posting") < user.index("# Instruction")
```

with:

```python
def test_stable_blocks_precede_the_instruction():
    # The source CV and job posting are stable across successive tailor_cv
    # calls for the same job; the instruction is not. Ordering the stable
    # blocks first lets the LLM server reuse the KV-cache prefix.
    client = _mock_client("# CV\n- x")
    tailor_cv(client, "m", "# source cv", "the instruction", "the job posting")
    user = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    assert user.index("# CV to tailor") < user.index("# Instruction")
    assert user.index("# Job posting") < user.index("# Instruction")
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `uv run pytest tests/test_cv_task.py tests/test_tailor_cv_generate.py -v`
Expected: PASS (all tests in both files)

- [ ] **Step 9: Run the full test suite to check for regressions**

Run: `uv run pytest -q`
Expected: All tests pass.

- [ ] **Step 10: Commit**

```bash
git add app/ai/tailor_cv.py app/cv/instruction.py app/routes/cv.py tests/test_cv_task.py tests/test_tailor_cv_generate.py
git commit -m "$(cat <<'EOF'
feat(cv): apply-tailoring-plan iterates from the current tailored CV

Generate mode now sources tailor_cv() from the job's current tailored
draft when one exists, falling back to the base CV for a fresh (or
just-reset) job — so repeated applies compound instead of each
re-deriving the whole document from base. tailor_cv()'s parameter and
prompt section are renamed source-neutral since it's no longer always
the base CV. check_guardrails() is unchanged: it always validates
against the true base CV regardless of what tailor_cv() iterated from.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_014AGqSLUXcmZFngZmNC65ma
EOF
)"
```

---

### Task 8: Record the applied edit-scope as a note on "Applied plan" versions

**Files:**
- Modify: `app/routes/cv.py:81-102` (`_persist_draft`), `:728-756` (generate branch, building on Task 7's edit)
- Test: `tests/test_cv_task.py`, `tests/test_cv_versions.py`

**Interfaces:**
- Consumes: `upsert_job_cv(..., note=...)` from Task 2; `reset_job_cv_to_base`/`set_job_cv_tailored` from Tasks 3 and the existing codebase (both already pass no `note`, i.e. `""`, by default).
- Produces: `_persist_draft(conn, job_id, *, draft, findings, settings, note="")`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cv_task.py`:

```python
def test_generate_records_applied_scope_note(conn, cfg):
    jid = _seed(conn)
    q.upsert_job_cv(conn, jid, scope=[1, 2])   # correct, choose
    with patch("app.routes.cv.tailor_cv", return_value={"markdown": "# regenerated"}), \
         patch("app.routes.cv.check_guardrails", return_value={"findings": []}):
        _run(conn, cfg, jid, "generate")
    row = q.get_job_cv(conn, jid)
    version = q.get_version(conn, "tailored", jid, row["current_version_id"])
    assert version["note"] == "correct, choose"
```

Add to `tests/test_cv_versions.py` (uses the existing `_job` helper):

```python
def test_manual_edit_and_reset_record_no_note(conn):
    jid = _job(conn)
    q.set_job_cv_tailored(conn, jid, "hand edited")
    row = q.get_job_cv(conn, jid)
    assert q.get_version(conn, "tailored", jid, row["current_version_id"])["note"] == ""

    q.reset_job_cv_to_base(conn, jid)
    row = q.get_job_cv(conn, jid)
    assert q.get_version(conn, "tailored", jid, row["current_version_id"])["note"] == ""
```

- [ ] **Step 2: Run tests to verify the first fails**

Run: `uv run pytest tests/test_cv_task.py::test_generate_records_applied_scope_note tests/test_cv_versions.py::test_manual_edit_and_reset_record_no_note -v`
Expected: `test_generate_records_applied_scope_note` FAILs (`note` is currently always `""` for a generate run — assertion expects `"correct, choose"`). `test_manual_edit_and_reset_record_no_note` already passes today — expected, it locks in behavior this task must not break.

- [ ] **Step 3: Compute the note and thread it through `_persist_draft`**

In `app/routes/cv.py`, replace `_persist_draft` (lines 81-102):

```python
def _persist_draft(conn, job_id: int, *, draft: str, findings: list, settings: dict) -> None:
    """Store a freshly generated draft and stamp generated_at. If the guardrail
    check came back empty while guardrails ARE configured, keep the previous
    findings — an empty result there is almost always a transient LLM/JSON
    failure, and silently blanking the guardrails panel is worse than showing a
    slightly stale check."""
    fields: dict = {
        "tailored_cv": draft,
        "base_hash": _base_hash(settings),
        "base_cv_snapshot": settings.get("base_cv", ""),
    }
    if findings or not settings.get("base_guardrails", "").strip():
        fields["guardrail_findings"] = findings
    else:
        logger.warning("cv_tailor: guardrail check returned nothing for job %s — keeping prior findings", job_id)
    q.upsert_job_cv(conn, job_id, **fields)
    conn.execute(
        "UPDATE job_cv SET generated_at = datetime('now'), "
        "guardrails_checked_at = datetime('now') WHERE job_id = ?",
        (job_id,),
    )
    conn.commit()
```

with:

```python
def _persist_draft(conn, job_id: int, *, draft: str, findings: list, settings: dict, note: str = "") -> None:
    """Store a freshly generated draft and stamp generated_at. If the guardrail
    check came back empty while guardrails ARE configured, keep the previous
    findings — an empty result there is almost always a transient LLM/JSON
    failure, and silently blanking the guardrails panel is worse than showing a
    slightly stale check."""
    fields: dict = {
        "tailored_cv": draft,
        "note": note,
        "base_hash": _base_hash(settings),
        "base_cv_snapshot": settings.get("base_cv", ""),
    }
    if findings or not settings.get("base_guardrails", "").strip():
        fields["guardrail_findings"] = findings
    else:
        logger.warning("cv_tailor: guardrail check returned nothing for job %s — keeping prior findings", job_id)
    q.upsert_job_cv(conn, job_id, **fields)
    conn.execute(
        "UPDATE job_cv SET generated_at = datetime('now'), "
        "guardrails_checked_at = datetime('now') WHERE job_id = ?",
        (job_id,),
    )
    conn.commit()
```

- [ ] **Step 4: Compute `scope_note` and pass it through in the generate branch**

In `app/routes/cv.py`, in `_task_cv_tailor`, replace (this builds on Task 7's edit to the same region):

```python
    instr = compose_instruction(
        base_instruction=settings["base_instruction"], scope=row["scope"],
        scope_options=scope_options, guardrails=settings["base_guardrails"],
        tuning_directives=row["tuning_directives"],
    )
    source_cv = row["tailored_cv"] or settings["base_cv"]
    yield "Generating the tailored CV… (LLM call: tailor_cv)"
```

with:

```python
    instr = compose_instruction(
        base_instruction=settings["base_instruction"], scope=row["scope"],
        scope_options=scope_options, guardrails=settings["base_guardrails"],
        tuning_directives=row["tuning_directives"],
    )
    scope_ids = set(row["scope"])
    scope_note = ", ".join(
        (o["name"] or o["description"]) for o in scope_options if o["id"] in scope_ids
    )
    source_cv = row["tailored_cv"] or settings["base_cv"]
    yield "Generating the tailored CV… (LLM call: tailor_cv)"
```

And further down, replace:

```python
    _persist_draft(conn, job_id, draft=draft, findings=findings, settings=settings)
```

with:

```python
    _persist_draft(conn, job_id, draft=draft, findings=findings, settings=settings, note=scope_note)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_cv_task.py::test_generate_records_applied_scope_note tests/test_cv_versions.py::test_manual_edit_and_reset_record_no_note -v`
Expected: PASS (both)

- [ ] **Step 6: Run the full test suite to check for regressions**

Run: `uv run pytest -q`
Expected: All tests pass.

- [ ] **Step 7: Commit**

```bash
git add app/routes/cv.py tests/test_cv_task.py tests/test_cv_versions.py
git commit -m "$(cat <<'EOF'
feat(cv): record applied edit-scope as a note on Applied plan versions

The comma-joined names of the edit-scope options enabled at generation
time are snapshotted into cv_versions.note, surfaced in history via
Task 6's version_meta() change. Manual edits and resets never carry a
note — no edit-scope concept applies to either.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_014AGqSLUXcmZFngZmNC65ma
EOF
)"
```

---

## Post-plan manual verification

After all tasks are green, do a quick manual pass with the `run-dev-server` skill (throwaway DB, per this project's rules):

1. Open a job's Tailor CV page, run "Apply tailoring plan" twice in a row — confirm the second run's output looks like it built on the first (not a clean re-derivation from base), and that history shows two "Applied plan" entries with the applied scope names as their note.
2. Click "Reset to base CV" — confirm the tailored content becomes the base CV, history shows a "Reset" entry whose parent is a base-CV version (not the previous tailored entry), and scope/directives are unchanged.
3. Manually edit the CV in the Edit tab — confirm history shows "Manual edit" with no note.
4. Check the base CV's own History section still renders correctly (it only ever shows "Manual edit" entries, no "Applied plan"/"Reset").
5. The `cv_versions` rebuild migration (Task 1) has no dedicated pytest coverage — matching every other migration in `app/db/schema.py`, none of which are unit-tested, since the `conn` test fixture always calls `init_db` against a brand-new database that already has the target schema. Its only real exercise is upgrading this project's actual `job-seek.db`. Before treating the merge as done, copy the real `job-seek.db`, run `init_db` against the copy (e.g. via the app's normal startup), and confirm it starts cleanly and `PRAGMA table_info(cv_versions)` shows the new `note` column with existing rows' `action` values intact.
