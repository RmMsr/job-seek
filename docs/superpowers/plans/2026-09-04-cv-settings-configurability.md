# CV Settings & Configurability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the base CV a first-class main-nav page with a preview button, split the advanced/experimental CV settings onto their own headed, individually-resettable page, and turn the fixed 4-item edit-scope list into a real user-editable table — per `docs/superpowers/specs/2026-09-04-cv-settings-configurability-design.md`.

**Architecture:** New `cv_scope_options` table replacing the hardcoded `SCOPE_ORDER`/`SCOPE_LINES` dict, CRUD routes/templates mirroring the existing scenario-criteria pattern (`app/routes/scenarios.py` + `scenarios/_criterion*.html`), a page split in `app/routes/cv.py` (`/cv` primary, `/cv/advanced` for the rest), and a Jinja global so `base.html`'s nav doesn't need every route to thread `cv_enabled` through its context by hand.

**Tech Stack:** FastAPI + Jinja2 + htmx (existing stack, no new dependencies).

## Global Constraints

- No new Python dependencies.
- DB migration follows `app/db/schema.py`'s existing `_migrate_*(conn)` convention: hard-downtime, no dual-shape compatibility (project CLAUDE.md "Database migrations" — personal, single-instance app).
- `job_cv.scope` and `cv_settings.default_scope` are existing JSON columns (`_JOB_CV_JSON_COLS`/settings JSON-encoding already generic) — no schema change needed on those two columns, only a content-format change (string keys → integer ids).
- Commit after each task passes its tests.
- Task 4 (page split/new UI) and Task 5 (scope CRUD UI) are UI-facing — use the `frontend-design` skill while implementing their templates for spacing/hover/contrast polish; the markup given below is the functional baseline.
- Run the full suite (`python -m pytest -q`) at the end of the plan, not just touched files.

---

### Task 1: `cv_scope_options` table, migration, and query layer

**Files:**
- Modify: `app/cv/instruction.py`
- Modify: `app/db/schema.py`
- Modify: `app/db/queries.py`
- Test: `tests/test_schema.py`, `tests/test_cv_scope_options.py` (new)

**Interfaces:**
- Produces: `DEFAULT_SCOPE_OPTIONS: list[dict]` in `app/cv/instruction.py` — each `{"description": str, "default_enabled": bool, "is_baseline": bool}`, in seed order.
- Produces (queries.py): `get_scope_options(conn) -> list[dict]` (each row + `id`, ordered by `sort_order, id`), `insert_scope_option(conn, description, default_enabled=False) -> int` (returns new id), `get_scope_option(conn, scope_option_id) -> dict | None`, `update_scope_option(conn, scope_option_id, description, default_enabled) -> None`, `delete_scope_option(conn, scope_option_id) -> None`, `reset_scope_options(conn) -> None` (wipes and reseeds defaults), and a private `_seed_default_scope_options(conn) -> None` used by both `reset_scope_options` and the schema migration.
- Consumes (later tasks): `get_scope_options`, `reset_scope_options`, `DEFAULT_SCOPE_OPTIONS`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cv_scope_options.py`:

```python
from app.db import queries as q


def test_fresh_db_has_no_scope_options_until_seeded(conn):
    assert q.get_scope_options(conn) == []


def test_seed_creates_four_defaults_in_order(conn):
    q._seed_default_scope_options(conn)
    opts = q.get_scope_options(conn)
    assert len(opts) == 4
    assert [o["sort_order"] for o in opts] == [0, 1, 2, 3]
    assert opts[0]["default_enabled"] == 1
    assert opts[0]["is_baseline"] == 1
    assert opts[2]["default_enabled"] == 0
    assert opts[2]["is_baseline"] == 0


def test_insert_appends_at_end_with_next_sort_order(conn):
    q._seed_default_scope_options(conn)
    new_id = q.insert_scope_option(conn, "Custom scope type.", default_enabled=True)
    opts = q.get_scope_options(conn)
    assert opts[-1]["id"] == new_id
    assert opts[-1]["sort_order"] == 4
    assert opts[-1]["description"] == "Custom scope type."
    assert opts[-1]["default_enabled"] == 1
    assert opts[-1]["is_baseline"] == 0


def test_update_scope_option_changes_description_and_flag(conn):
    q._seed_default_scope_options(conn)
    opts = q.get_scope_options(conn)
    first_id = opts[0]["id"]
    q.update_scope_option(conn, first_id, "Edited text.", default_enabled=False)
    updated = q.get_scope_option(conn, first_id)
    assert updated["description"] == "Edited text."
    assert updated["default_enabled"] == 0


def test_delete_scope_option_removes_it(conn):
    q._seed_default_scope_options(conn)
    opts = q.get_scope_options(conn)
    q.delete_scope_option(conn, opts[0]["id"])
    assert len(q.get_scope_options(conn)) == 3


def test_reset_scope_options_wipes_edits_and_restores_defaults(conn):
    q._seed_default_scope_options(conn)
    opts = q.get_scope_options(conn)
    q.update_scope_option(conn, opts[0]["id"], "Mangled.", default_enabled=False)
    q.insert_scope_option(conn, "Extra one.")
    q.reset_scope_options(conn)
    fresh = q.get_scope_options(conn)
    assert len(fresh) == 4
    assert fresh[0]["default_enabled"] == 1
    assert "Mangled." not in [o["description"] for o in fresh]
```

Fixture note: `conn` here is the fixture from `tests/conftest.py`, which already calls `init_db` — that will have created (empty) `cv_scope_options` via `_DDL` in Step 3 below.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_cv_scope_options.py -v`
Expected: FAIL (`AttributeError` — none of these functions exist yet)

- [ ] **Step 3: Add `DEFAULT_SCOPE_OPTIONS` to `app/cv/instruction.py`**

Add near `DEFAULT_GUARDRAILS_RULES` (this replaces `SCOPE_LINES`/`SCOPE_ORDER`/`BASELINE_SCOPE` — remove those three, per Task 2 below, but for this task just add the new constant alongside them so Task 1 lands independently testable):

```python
# Replaces the old SCOPE_LINES/SCOPE_ORDER/BASELINE_SCOPE dict — these are
# now the *default* seed content for the user-editable cv_scope_options
# table (app/db/schema.py's _DDL + queries.py's _seed_default_scope_options),
# not a fixed enum. default_enabled=True means a new job starts with this
# scope checked; is_baseline=True means the very first, most-conservative
# auto-generated draft on first workbench visit includes it.
DEFAULT_SCOPE_OPTIONS: list[dict] = [
    {
        "description": "Include or omit existing bullets and whole sections by relevance to this job.",
        "default_enabled": True, "is_baseline": True,
    },
    {
        "description": "Reorder bullets and sections, and choose what leads each section, to foreground "
                        "the experience this job values most.",
        "default_enabled": True, "is_baseline": True,
    },
    {
        "description": "Reword existing bullets toward the job's terminology, without introducing a claim "
                        "the base CV does not already support or upgrading the scope or seniority of one.",
        "default_enabled": False, "is_baseline": False,
    },
    {
        "description": "Write a job-specific professional summary synthesised only from facts already "
                        "stated in the base CV.",
        "default_enabled": False, "is_baseline": False,
    },
]
```

- [ ] **Step 4: Add the table to `app/db/schema.py`'s `_DDL`**

Insert right after the `cv_settings` table definition, before `job_cv`:

```sql
CREATE TABLE IF NOT EXISTS cv_scope_options (
    id INTEGER PRIMARY KEY,
    description TEXT NOT NULL,
    default_enabled INTEGER NOT NULL DEFAULT 0,
    is_baseline INTEGER NOT NULL DEFAULT 0,
    sort_order INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

- [ ] **Step 5: Add the query functions to `app/db/queries.py`**

Add near the other `# --- CV ---` functions (after `save_cv_settings`, before `get_job_cv`):

```python
def _seed_default_scope_options(conn: sqlite3.Connection) -> None:
    from app.cv.instruction import DEFAULT_SCOPE_OPTIONS
    for i, opt in enumerate(DEFAULT_SCOPE_OPTIONS):
        conn.execute(
            "INSERT INTO cv_scope_options (description, default_enabled, is_baseline, sort_order) "
            "VALUES (?, ?, ?, ?)",
            (opt["description"], int(opt["default_enabled"]), int(opt["is_baseline"]), i),
        )
    conn.commit()


def get_scope_options(conn: sqlite3.Connection) -> list[dict]:
    return _rows_to_dicts(
        conn.execute("SELECT * FROM cv_scope_options ORDER BY sort_order, id").fetchall()
    )


def insert_scope_option(conn: sqlite3.Connection, description: str, default_enabled: bool = False) -> int:
    next_sort = conn.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM cv_scope_options").fetchone()[0]
    cur = conn.execute(
        "INSERT INTO cv_scope_options (description, default_enabled, sort_order) VALUES (?, ?, ?)",
        (description, int(default_enabled), next_sort),
    )
    conn.commit()
    return cur.lastrowid


def get_scope_option(conn: sqlite3.Connection, scope_option_id: int) -> dict | None:
    return _row_to_dict(
        conn.execute("SELECT * FROM cv_scope_options WHERE id = ?", (scope_option_id,)).fetchone()
    )


def update_scope_option(
    conn: sqlite3.Connection, scope_option_id: int, description: str, default_enabled: bool
) -> None:
    conn.execute(
        "UPDATE cv_scope_options SET description = ?, default_enabled = ? WHERE id = ?",
        (description, int(default_enabled), scope_option_id),
    )
    conn.commit()


def delete_scope_option(conn: sqlite3.Connection, scope_option_id: int) -> None:
    conn.execute("DELETE FROM cv_scope_options WHERE id = ?", (scope_option_id,))
    conn.commit()


def reset_scope_options(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM cv_scope_options")
    _seed_default_scope_options(conn)
```

- [ ] **Step 6: Run tests to verify Step 1's tests pass**

Run: `python -m pytest tests/test_cv_scope_options.py -v`
Expected: PASS

- [ ] **Step 7: Write the migration test**

Add to `tests/test_schema.py`:

```python
def test_init_db_seeds_cv_scope_options_once(conn):
    init_db(conn)
    from app.db import queries as q
    opts = q.get_scope_options(conn)
    assert len(opts) == 4
    # Idempotent: running again doesn't duplicate the seed.
    init_db(conn)
    assert len(q.get_scope_options(conn)) == 4


def test_init_db_remaps_string_keyed_scope_to_ids(conn):
    conn.executescript(
        """
        CREATE TABLE cv_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            base_cv TEXT NOT NULL DEFAULT '',
            base_instruction TEXT NOT NULL DEFAULT '',
            base_guardrails TEXT NOT NULL DEFAULT '',
            css TEXT NOT NULL DEFAULT '',
            default_scope TEXT NOT NULL DEFAULT '["select","reorder"]',
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE sources (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL,
            fetcher_type TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE jobs (id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL, url TEXT NOT NULL UNIQUE);
        CREATE TABLE job_cv (
            job_id INTEGER PRIMARY KEY,
            scope TEXT NOT NULL DEFAULT '[]',
            tuning_directives TEXT NOT NULL DEFAULT '',
            plan TEXT NOT NULL DEFAULT '[]',
            tailored_cv TEXT NOT NULL DEFAULT '',
            guardrail_findings TEXT NOT NULL DEFAULT '[]',
            change_report TEXT NOT NULL DEFAULT '{}',
            base_hash TEXT NOT NULL DEFAULT '',
            preview_pages TEXT NOT NULL DEFAULT '[]',
            plan_generated_at TEXT, directives_edited_at TEXT, generated_at TEXT, finalized_at TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    conn.execute("INSERT INTO cv_settings (id) VALUES (1)")
    conn.execute(
        "INSERT INTO job_cv (job_id, scope) VALUES (1, '[\"select\",\"rephrase\"]')"
    )
    conn.commit()

    init_db(conn)

    from app.db import queries as q
    import json
    settings = conn.execute("SELECT default_scope FROM cv_settings WHERE id = 1").fetchone()[0]
    assert json.loads(settings) == [1, 2]  # select=1, reorder=2 (seed order)
    job_scope = conn.execute("SELECT scope FROM job_cv WHERE job_id = 1").fetchone()[0]
    assert json.loads(job_scope) == [1, 3]  # select=1, rephrase=3

    # Idempotent: running again doesn't re-map already-integer scope arrays.
    init_db(conn)
    job_scope2 = conn.execute("SELECT scope FROM job_cv WHERE job_id = 1").fetchone()[0]
    assert json.loads(job_scope2) == [1, 3]
```

- [ ] **Step 8: Run to verify failure**

Run: `python -m pytest tests/test_schema.py -k "scope_options" -v`
Expected: FAIL

- [ ] **Step 9: Add the migration in `app/db/schema.py`**

Add `import json` at the top of the file (alongside `import sqlite3`). Add the migration function near the other `_migrate_*` functions (after `_migrate_cv_settings_merge_floor_guardrails`):

```python
def _migrate_seed_and_remap_cv_scope_options(conn: sqlite3.Connection) -> None:
    """cv_scope_options is created by _DDL, so a fresh DB already has the
    (empty) table. Seed it with the four defaults if empty, then remap any
    still-string-keyed job_cv.scope / cv_settings.default_scope arrays (from
    before scopes became a user-editable table) to the seeded ids. Guarded
    by row count / content shape, so re-running is a no-op."""
    if conn.execute("SELECT COUNT(*) FROM cv_scope_options").fetchone()[0] == 0:
        from app.db.queries import _seed_default_scope_options
        _seed_default_scope_options(conn)
    key_to_id = {"select": 1, "reorder": 2, "rephrase": 3, "summary": 4}
    row = conn.execute("SELECT default_scope FROM cv_settings WHERE id = 1").fetchone()
    if row is not None:
        old = json.loads(row[0] or "[]")
        if old and isinstance(old[0], str):
            new = [key_to_id[k] for k in old if k in key_to_id]
            conn.execute("UPDATE cv_settings SET default_scope = ? WHERE id = 1", (json.dumps(new),))
    for jc_row in conn.execute("SELECT job_id, scope FROM job_cv").fetchall():
        old = json.loads(jc_row["scope"] or "[]")
        if old and isinstance(old[0], str):
            new = [key_to_id[k] for k in old if k in key_to_id]
            conn.execute("UPDATE job_cv SET scope = ? WHERE job_id = ?", (json.dumps(new), jc_row["job_id"]))
    conn.commit()
```

Add the call at the end of `init_db`, after `_migrate_cv_settings_merge_floor_guardrails(conn)`:

```python
    _migrate_seed_and_remap_cv_scope_options(conn)
```

Note: `job_cv` rows are fetched with `conn.execute(...).fetchall()` relying on `sqlite3.Row` factory (`jc_row["job_id"]`, `jc_row["scope"]`) — this connection already has `row_factory = sqlite3.Row` set by every caller of `init_db` in this codebase (see `tests/test_schema.py`'s `conn` fixture and `app/deps.py`'s real connection setup); no change needed there.

- [ ] **Step 10: Run to verify Step 7's tests pass, then the whole file**

Run: `python -m pytest tests/test_schema.py tests/test_cv_scope_options.py -q`
Expected: all PASS

- [ ] **Step 11: Commit**

```bash
git add app/cv/instruction.py app/db/schema.py app/db/queries.py tests/test_schema.py tests/test_cv_scope_options.py
git commit -m "feat: cv_scope_options table replacing hardcoded edit-scope dict

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01YAUuWYfmrM6HK7gL7xmnUm"
```

---

### Task 2: `compose_instruction` and the tailor task read scope from the table

**Files:**
- Modify: `app/cv/instruction.py`
- Modify: `app/routes/cv.py`
- Test: `tests/test_cv_instruction.py`, `tests/test_cv_task.py`, `tests/test_routes_cv_actions.py`

**Interfaces:**
- Produces: `compose_instruction(*, base_instruction: str, scope: list[int], scope_options: list[dict], guardrails: str, tuning_directives: str) -> str` — replaces the old `scope: list[str]` signature; `scope_options` is the live list from `q.get_scope_options(conn)` (each dict has `id`, `description`, in canonical `sort_order`).
- Removes: `SCOPE_ORDER`, `SCOPE_LINES`, `BASELINE_SCOPE` from `app/cv/instruction.py` (fully replaced by the DB-backed table from Task 1).
- Consumes (Task 1): `q.get_scope_options`.

- [ ] **Step 1: Write the failing tests**

Replace the scope-related tests in `tests/test_cv_instruction.py` (keep the guardrails ones from the previous plan untouched):

```python
from app.cv.instruction import (
    DEFAULT_GUARDRAILS, DEFAULT_GUARDRAILS_RULES, DEFAULT_SCOPE_OPTIONS, compose_instruction,
)

_SCOPE_OPTIONS = [
    {"id": 1, "description": "Select bullets by relevance."},
    {"id": 2, "description": "Reorder to foreground what matters."},
    {"id": 3, "description": "Reword toward the job's terms."},
]


def test_default_scope_options_has_four_entries_with_expected_flags():
    assert len(DEFAULT_SCOPE_OPTIONS) == 4
    assert sum(o["default_enabled"] for o in DEFAULT_SCOPE_OPTIONS) == 2
    assert sum(o["is_baseline"] for o in DEFAULT_SCOPE_OPTIONS) == 2


def test_default_guardrails_lists_six_prohibitions():
    assert len(DEFAULT_GUARDRAILS_RULES) == 6
    assert all(r.startswith("Do not") for r in DEFAULT_GUARDRAILS_RULES)
    for token in ("degree", "employer", "job title", "dates", "metric", "tool"):
        assert token in DEFAULT_GUARDRAILS.lower()


def test_default_guardrails_text_built_from_rules_one_per_line():
    assert DEFAULT_GUARDRAILS.splitlines() == DEFAULT_GUARDRAILS_RULES


def test_compose_orders_sections_and_includes_guardrails():
    out = compose_instruction(
        base_instruction="British English.",
        scope=[2, 1],  # unordered on purpose
        scope_options=_SCOPE_OPTIONS,
        guardrails="Keep it to two pages.",
        tuning_directives="- foreground platform work, keep mentoring line",
    )
    assert out.index("British English.") < out.index("Permitted edits")
    assert out.index("Permitted edits") < out.index("Hard limits — never break these:")
    assert out.index("Hard limits — never break these:") < out.index("Tuning directives")
    # canonical (scope_options) order regardless of input `scope` order
    assert out.index("Select bullets") < out.index("Reorder to foreground")
    assert "Keep it to two pages." in out
    assert "foreground platform work" in out


def test_compose_handles_empty_guardrails_and_directives():
    out = compose_instruction(
        base_instruction="", scope=[1], scope_options=_SCOPE_OPTIONS,
        guardrails="", tuning_directives="",
    )
    assert "Hard limits — never break these:\n(none)" in out
    assert "Tuning directives" in out and "(none)" in out


def test_compose_ignores_scope_ids_not_in_options():
    out = compose_instruction(
        base_instruction="", scope=[1, 999], scope_options=_SCOPE_OPTIONS,
        guardrails="", tuning_directives="",
    )
    assert "Select bullets" in out
    assert "999" not in out
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_cv_instruction.py -v`
Expected: FAIL (`ImportError` on `DEFAULT_SCOPE_OPTIONS` not yet re-exported the way the test expects, and `compose_instruction`'s old signature rejects `scope_options`)

- [ ] **Step 3: Update `compose_instruction` in `app/cv/instruction.py`**

Remove `SCOPE_LINES`, `SCOPE_ORDER`, `BASELINE_SCOPE` entirely. Replace `compose_instruction`:

```python
def compose_instruction(
    *, base_instruction: str, scope: list[int], scope_options: list[dict],
    guardrails: str, tuning_directives: str,
) -> str:
    scope_set = set(scope)
    enabled_descriptions = [o["description"] for o in scope_options if o["id"] in scope_set]
    parts: list[str] = []
    if base_instruction.strip():
        parts.append(base_instruction.strip())
        parts.append("")
    parts.append("Permitted edits for this CV:")
    parts.extend(f"- {d}" for d in enabled_descriptions)
    parts.append("")
    parts.append("Hard limits — never break these:")
    parts.append(guardrails.strip() or "(none)")
    parts.append("")
    directives = tuning_directives.strip() or "(none)"
    parts.append("Tuning directives (the plan for this job):")
    parts.append(directives)
    return "\n".join(parts)
```

- [ ] **Step 4: Run to verify Step 1's tests pass**

Run: `python -m pytest tests/test_cv_instruction.py -v`
Expected: PASS

- [ ] **Step 5: Update `app/routes/cv.py`'s import and every `compose_instruction`/scope call site**

Change the import line:

```python
from app.cv.instruction import compose_instruction, DEFAULT_GUARDRAILS
```

(drops `BASELINE_SCOPE, SCOPE_ORDER, SCOPE_LINES`)

In `_settings_ctx` and `_workbench_ctx`, replace `"scope_order": SCOPE_ORDER, "scope_labels": SCOPE_LINES,` with:

```python
        "scope_options": q.get_scope_options(conn),
```

**Also fetch `scope_options` once, up top, and stop reading `settings["default_scope"]`
entirely** — after Task 4/5, "default scope for a new job" is computed from each
`cv_scope_options` row's own `default_enabled` flag (edited inline in the scope-options
list), not from the `cv_settings.default_scope` column; that column becomes a vestige of
the old design and must not be read anywhere in `_task_cv_tailor` from this task onward
(Task 4 stops writing anything meaningful to it too — see that task's `cv_advanced_save`).

Right after `settings = q.get_cv_settings(conn)` near the top of `_task_cv_tailor`, add:

```python
    scope_options = q.get_scope_options(conn)
```

Then, in the `mode == "plan"` branch, replace:

```python
        scope = row["scope"] if row else settings["default_scope"]
```

with:

```python
        scope = row["scope"] if row else [o["id"] for o in scope_options if o["default_enabled"]]
```

Replace the baseline branch's `compose_instruction` call:

```python
            instr = compose_instruction(
                base_instruction=settings["base_instruction"], scope=BASELINE_SCOPE,
                guardrails=settings["base_guardrails"], tuning_directives="",
            )
```

with:

```python
            baseline_scope = [o["id"] for o in scope_options if o["is_baseline"]]
            instr = compose_instruction(
                base_instruction=settings["base_instruction"], scope=baseline_scope,
                scope_options=scope_options, guardrails=settings["base_guardrails"], tuning_directives="",
            )
```

In the `mode == "generate"` branch, replace:

```python
    if row is None:
        q.upsert_job_cv(conn, job_id, scope=settings["default_scope"])
        row = q.get_job_cv(conn, job_id)
```

with:

```python
    if row is None:
        q.upsert_job_cv(conn, job_id, scope=[o["id"] for o in scope_options if o["default_enabled"]])
        row = q.get_job_cv(conn, job_id)
```

and its `compose_instruction` call:

```python
    instr = compose_instruction(
        base_instruction=settings["base_instruction"], scope=row["scope"],
        guardrails=settings["base_guardrails"], tuning_directives=row["tuning_directives"],
    )
```

with:

```python
    instr = compose_instruction(
        base_instruction=settings["base_instruction"], scope=row["scope"],
        scope_options=scope_options, guardrails=settings["base_guardrails"],
        tuning_directives=row["tuning_directives"],
    )
```

In `cv_save_directives`, replace:

```python
    scope = [s for s in form.getlist("scope") if s in SCOPE_ORDER]
```

with:

```python
    valid_ids = {o["id"] for o in q.get_scope_options(conn)}
    scope = [int(s) for s in form.getlist("scope") if s.isdigit() and int(s) in valid_ids]
```

In `cv_settings_save` (the `/cv` POST — this becomes Task 4's primary-page save, but its scope-parsing line moves there too; for now, since Task 4 hasn't split the route yet, fix it in place so the file stays importable and green after this task):

```python
    scope = [s for s in form.getlist("scope") if s in SCOPE_ORDER]
```

becomes:

```python
    valid_ids = {o["id"] for o in q.get_scope_options(conn)}
    scope = [int(s) for s in form.getlist("scope") if s.isdigit() and int(s) in valid_ids]
```

(both the success path and the `err` early-return path build `"default_scope": form.getlist("scope")` for re-display — leave that line as-is, it's just redisplaying raw submitted values, not persisting them)

- [ ] **Step 6: Update `tests/test_cv_task.py`'s `_seed` fixture and any scope assertions**

`_seed` currently does `q.save_cv_settings(conn, base_cv=..., base_guardrails="", css="", default_scope=["select", "reorder"])`. Since `conn` fixture runs `init_db` (which now seeds `cv_scope_options` with ids 1=select-equivalent, 2=reorder-equivalent), change to:

```python
    q.save_cv_settings(conn, base_cv="# Me\n\n- Kafka work\n", base_instruction="",
                       base_guardrails="", css="", default_scope=[1, 2])
```

Search the rest of the file for any other literal `["select", "reorder"]` or `"select"`/`"reorder"` string usage in scope assertions and update to the equivalent ids (`1`, `2`) the same way.

- [ ] **Step 7: Update `tests/test_routes_cv_actions.py`'s `_job` fixture and scope assertions**

Same change: `default_scope=["select", "reorder"]` → `default_scope=[1, 2]`. Update `test_save_directives_persists_and_returns_pane` and `test_reset_to_plan_rebuilds_directives`, which currently post `data={"scope": ["select", "rephrase"]}` / `data={"scope": ["select"]}` — change to the corresponding ids (`["1", "3"]` / `["1"]`) since form data is always string-encoded regardless of the target type.

- [ ] **Step 8: Run the full CV test suite**

Run: `python -m pytest tests/test_cv_instruction.py tests/test_cv_task.py tests/test_routes_cv_actions.py tests/test_routes_cv_workbench.py -q`
Expected: all PASS

- [ ] **Step 9: Commit**

```bash
git add app/cv/instruction.py app/routes/cv.py tests/test_cv_instruction.py tests/test_cv_task.py tests/test_routes_cv_actions.py
git commit -m "feat: compose_instruction and cv_tailor read edit scope from the table

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01YAUuWYfmrM6HK7gL7xmnUm"
```

---

### Task 3: Jinja global for `cv_enabled` + main-nav "CV" link

**Files:**
- Modify: `app/template_env.py`
- Modify: `app/templates/base.html`
- Test: `tests/test_routes_jobs.py` (or any route already rendering `base.html` — add a focused new test)

**Interfaces:**
- Produces: `cv_enabled()` callable in every template's global namespace (via `templates.env.globals`), independent of route-supplied context.
- Consumes: `app.config.cv_enabled`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_routes_jobs.py`:

```python
def test_main_nav_shows_cv_link_after_profile_when_enabled(client, monkeypatch):
    import app.config as cfg
    monkeypatch.setattr(cfg, "cv_enabled", lambda *_a, **_k: True)
    r = client.get("/jobs")
    text = r.text
    assert '<a href="/cv"' in text
    assert text.index('href="/profile"') < text.index('href="/cv"')
    assert text.index('href="/cv"') < text.index('href="/scenarios"')


def test_main_nav_hides_cv_link_when_disabled(client, monkeypatch):
    import app.config as cfg
    monkeypatch.setattr(cfg, "cv_enabled", lambda *_a, **_k: False)
    r = client.get("/jobs")
    assert '<a href="/cv"' not in r.text
```

Note: this patches `app.config.cv_enabled` itself (not a per-route re-import of it), since the nav's `cv_enabled()` call resolves through the Jinja global registered directly against that function object.

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_routes_jobs.py -k "main_nav_.*cv" -v`
Expected: FAIL (no `/cv` link in nav yet)

- [ ] **Step 3: Register the Jinja global in `app/template_env.py`**

Add the import and registration:

```python
from app.config import cv_enabled
```

(add alongside the existing imports at the top)

```python
templates.env.globals["cv_enabled"] = cv_enabled
```

(add after the existing `templates.env.filters[...]` assignments, near the bottom of the file)

- [ ] **Step 4: Add the nav link in `app/templates/base.html`**

```html
    <a href="/profile" {% if request.url.path == "/profile" %}class="active"{% endif %}>Profile</a>
    {% if cv_enabled() %}
    <a href="/cv" {% if request.url.path.startswith("/cv") %}class="active"{% endif %}>CV</a>
    {% endif %}
    <a href="/scenarios" {% if request.url.path == "/scenarios" %}class="active"{% endif %}>Scenarios</a>
```

- [ ] **Step 5: Run to verify the tests pass**

Run: `python -m pytest tests/test_routes_jobs.py -k "main_nav_.*cv" -v`
Expected: PASS

- [ ] **Step 6: Run the full jobs test file for regressions**

Run: `python -m pytest tests/test_routes_jobs.py -q`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add app/template_env.py app/templates/base.html tests/test_routes_jobs.py
git commit -m "feat: CV main-nav link via a Jinja global, after Profile

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01YAUuWYfmrM6HK7gL7xmnUm"
```

---

### Task 4: Split `/cv` into a primary page (base CV + Preview) and `/cv/advanced`

**Files:**
- Modify: `app/routes/cv.py`
- Create: `app/templates/cv/index.html`
- Create: `app/templates/cv/_preview_result.html`
- Rename+modify: `app/templates/cv/settings.html` → `app/templates/cv/advanced.html`
- Modify: `app/templates/setup/_subnav.html`
- Modify: `tests/test_routes_cv_settings.py`

**Interfaces:**
- Produces: `GET/POST /cv` (primary page — base CV only), `POST /cv/preview` (renders the base CV via the existing pipeline, returns inline `data:` URI images, no persistence), `GET/POST /cv/advanced` (guardrails/writing-style/CSS/default-scope — everything except base CV), `POST /cv/reset-style`, `POST /cv/reset-css` (siblings of the existing `POST /cv/reset-guardrails`).
- Consumes (Task 1): `q.get_scope_options`.

- [ ] **Step 1: Write the failing tests**

Replace `tests/test_routes_cv_settings.py` in full:

```python
import pytest
from app.db import queries as q
import app.deps as deps


@pytest.fixture
def cv_on(monkeypatch):
    monkeypatch.setattr(deps, "cv_enabled", lambda *_a, **_k: True)


def test_cv_page_404_when_disabled(client, monkeypatch):
    monkeypatch.setattr(deps, "cv_enabled", lambda *_a, **_k: False)
    assert client.get("/cv").status_code == 404


def test_cv_page_renders_base_cv_and_preview_button(client, cv_on):
    r = client.get("/cv")
    assert r.status_code == 200
    assert "base_cv" in r.text
    assert "Preview" in r.text
    assert "Advanced" in r.text  # link to /cv/advanced


def test_cv_page_has_no_guardrails_field(client, cv_on):
    r = client.get("/cv")
    assert "base_guardrails" not in r.text


def test_cv_save_persists_base_cv_only(client, cv_on, conn):
    q.save_cv_settings(conn, base_cv="old", base_instruction="keep me",
                       base_guardrails="keep me too", css="keep", default_scope=[1])
    r = client.post("/cv", data={"base_cv": "# New CV\n\n- thing\n"})
    assert r.status_code == 200
    s = q.get_cv_settings(conn)
    assert s["base_cv"] == "# New CV\n\n- thing\n"
    assert s["base_instruction"] == "keep me"
    assert s["base_guardrails"] == "keep me too"
    assert s["css"] == "keep"


def test_cv_preview_renders_pages(client, cv_on, conn):
    from unittest.mock import patch
    q.save_cv_settings(conn, base_cv="# Me\n\n- x\n", base_instruction="", base_guardrails="",
                       css="", default_scope=[1])
    with patch("app.routes.cv.doc_write_available", return_value=True), \
         patch("app.routes.cv.render_preview_pngs") as rp:
        import tempfile, os
        def _fake_render(markdown, css, out_dir):
            path = os.path.join(out_dir, "page-1.png")
            with open(path, "wb") as f:
                f.write(b"\x89PNG\r\n\x1a\nfake")
            return [path]
        rp.side_effect = _fake_render
        r = client.post("/cv/preview")
    assert r.status_code == 200
    assert "data:image/png;base64," in r.text


def test_cv_preview_reports_missing_doc_write(client, cv_on, conn):
    from unittest.mock import patch
    q.save_cv_settings(conn, base_cv="# Me", base_instruction="", base_guardrails="",
                       css="", default_scope=[1])
    with patch("app.routes.cv.doc_write_available", return_value=False):
        r = client.post("/cv/preview")
    assert r.status_code == 200
    assert "not installed" in r.text


def test_cv_advanced_page_404_when_disabled(client, monkeypatch):
    monkeypatch.setattr(deps, "cv_enabled", lambda *_a, **_k: False)
    assert client.get("/cv/advanced").status_code == 404


def test_cv_advanced_page_has_headed_sections(client, cv_on):
    r = client.get("/cv/advanced")
    assert r.status_code == 200
    assert "Guardrails" in r.text
    assert "Editing scopes" in r.text
    assert "Writing style" in r.text
    assert "Appearance" in r.text
    assert "House-style" not in r.text  # renamed
    assert "base_cv" not in r.text  # lives on the primary page now


def test_cv_advanced_save_roundtrip(client, cv_on, conn):
    r = client.post("/cv/advanced", data={
        "base_instruction": "British English", "base_guardrails": "No invented dates",
        "css": "p { color: red; }",
    })
    assert r.status_code == 200
    assert "Saved" in r.text
    s = q.get_cv_settings(conn)
    assert s["base_instruction"] == "British English"
    assert s["base_guardrails"] == "No invented dates"


def test_cv_advanced_save_rejects_bad_css(client, cv_on, conn):
    r = client.post("/cv/advanced", data={
        "base_instruction": "", "base_guardrails": "",
        "css": "@import url('http://evil/x.css');",
    })
    assert r.status_code == 200
    assert "@import" in r.text
    assert q.get_cv_settings(conn)["css"] == ""  # not saved


def test_reset_guardrails_restores_defaults(client, cv_on, conn):
    from app.cv.instruction import DEFAULT_GUARDRAILS
    q.save_cv_settings(conn, base_cv="# Me", base_instruction="", base_guardrails="my custom rule only",
                       css="", default_scope=[1])
    r = client.post("/cv/reset-guardrails")
    assert r.status_code == 200
    assert q.get_cv_settings(conn)["base_guardrails"] == DEFAULT_GUARDRAILS


def test_reset_style_clears_to_empty(client, cv_on, conn):
    q.save_cv_settings(conn, base_cv="", base_instruction="something custom",
                       base_guardrails="", css="", default_scope=[1])
    r = client.post("/cv/reset-style")
    assert r.status_code == 200
    assert q.get_cv_settings(conn)["base_instruction"] == ""


def test_reset_css_clears_to_empty(client, cv_on, conn):
    q.save_cv_settings(conn, base_cv="", base_instruction="", base_guardrails="",
                       css="p{color:red}", default_scope=[1])
    r = client.post("/cv/reset-css")
    assert r.status_code == 200
    assert q.get_cv_settings(conn)["css"] == ""


def test_reset_preserves_other_settings(client, cv_on, conn):
    q.save_cv_settings(conn, base_cv="# Keep me", base_instruction="British English",
                       base_guardrails="custom", css="p{color:red}", default_scope=[1])
    client.post("/cv/reset-guardrails")
    s = q.get_cv_settings(conn)
    assert s["base_cv"] == "# Keep me"
    assert s["base_instruction"] == "British English"
    assert s["css"] == "p{color:red}"


def test_new_cv_settings_seeded_with_default_guardrails(client, cv_on, conn):
    from app.cv.instruction import DEFAULT_GUARDRAILS_RULES
    s = q.get_cv_settings(conn)
    for rule in DEFAULT_GUARDRAILS_RULES:
        assert rule in s["base_guardrails"]


def test_setup_subnav_cv_tab_points_to_advanced(client, cv_on):
    r = client.get("/setup")
    assert 'href="/cv/advanced"' in r.text
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_routes_cv_settings.py -v`
Expected: FAIL (routes/templates don't exist in the new shape yet)

- [ ] **Step 3: Rewrite the settings/preview routes in `app/routes/cv.py`**

Add these imports near the top, alongside the existing ones:

```python
import base64
import tempfile
```

Replace `_settings_ctx` with two context builders:

```python
def _cv_page_ctx(conn: sqlite3.Connection) -> dict:
    return {
        "settings": q.get_cv_settings(conn),
        "cv_enabled": True,
        "app_version": get_app_version(),
        "build_date": get_build_date(),
    }


def _advanced_ctx(conn: sqlite3.Connection) -> dict:
    return {
        "settings": q.get_cv_settings(conn),
        "scope_options": q.get_scope_options(conn),
        "cv_enabled": True,
        "app_version": get_app_version(),
        "build_date": get_build_date(),
    }
```

Replace the existing `cv_settings_page` / `cv_settings_save` / `cv_reset_guardrails` routes (everything from `@router.get("/cv", ...)` down through the end of `cv_reset_guardrails`) with:

```python
@router.get("/cv", response_class=HTMLResponse, dependencies=[Depends(require_cv_enabled)])
def cv_page(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    return templates.TemplateResponse(request, "cv/index.html", _cv_page_ctx(conn))


@router.post("/cv", response_class=HTMLResponse, dependencies=[Depends(require_cv_enabled)])
async def cv_save(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    form = await request.form()
    current = q.get_cv_settings(conn)
    q.save_cv_settings(
        conn, base_cv=form.get("base_cv", ""), base_instruction=current["base_instruction"],
        base_guardrails=current["base_guardrails"], css=current["css"],
        default_scope=current["default_scope"],
    )
    ctx = _cv_page_ctx(conn)
    ctx["saved"] = True
    return templates.TemplateResponse(request, "cv/index.html", ctx)


@router.post("/cv/preview", response_class=HTMLResponse, dependencies=[Depends(require_cv_enabled)])
def cv_preview_base(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    settings = q.get_cv_settings(conn)
    if not doc_write_available():
        return templates.TemplateResponse(
            request, "cv/_preview_result.html", {"images": [], "error": "doc-write-cli is not installed"}
        )
    with tempfile.TemporaryDirectory(prefix="cv-base-preview-") as out_dir:
        try:
            pages = render_preview_pngs(settings["base_cv"], settings["css"], out_dir)
        except CvRenderError as exc:
            return templates.TemplateResponse(
                request, "cv/_preview_result.html", {"images": [], "error": str(exc)}
            )
        images = []
        for p in pages:
            with open(p, "rb") as f:
                images.append(base64.b64encode(f.read()).decode())
    return templates.TemplateResponse(request, "cv/_preview_result.html", {"images": images, "error": None})


@router.get("/cv/advanced", response_class=HTMLResponse, dependencies=[Depends(require_cv_enabled)])
def cv_advanced_page(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    return templates.TemplateResponse(request, "cv/advanced.html", _advanced_ctx(conn))


@router.post("/cv/advanced", response_class=HTMLResponse, dependencies=[Depends(require_cv_enabled)])
async def cv_advanced_save(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    form = await request.form()
    css = form.get("css", "")
    err = validate_css(css)
    current = q.get_cv_settings(conn)
    if err:
        ctx = _advanced_ctx(conn)
        ctx["error"] = err
        ctx["settings"] = {
            **ctx["settings"],
            "base_instruction": form.get("base_instruction", ""),
            "base_guardrails": form.get("base_guardrails", ""),
            "css": css,
        }
        return templates.TemplateResponse(request, "cv/advanced.html", ctx)
    # default_scope has no form field on this page any more — "default enabled
    # for a new job" is now edited per-row in the scope-options list (Task 5),
    # so this column is passed through unchanged rather than parsed from a
    # scope checkbox list that no longer exists here.
    q.save_cv_settings(
        conn, base_cv=current["base_cv"], base_instruction=form.get("base_instruction", ""),
        base_guardrails=form.get("base_guardrails", ""), css=css, default_scope=current["default_scope"],
    )
    ctx = _advanced_ctx(conn)
    ctx["saved"] = True
    return templates.TemplateResponse(request, "cv/advanced.html", ctx)


@router.post("/cv/reset-guardrails", response_class=HTMLResponse, dependencies=[Depends(require_cv_enabled)])
def cv_reset_guardrails(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    settings = q.get_cv_settings(conn)
    q.save_cv_settings(
        conn, base_cv=settings["base_cv"], base_instruction=settings["base_instruction"],
        base_guardrails=DEFAULT_GUARDRAILS, css=settings["css"], default_scope=settings["default_scope"],
    )
    ctx = _advanced_ctx(conn)
    ctx["saved"] = True
    return templates.TemplateResponse(request, "cv/advanced.html", ctx)


@router.post("/cv/reset-style", response_class=HTMLResponse, dependencies=[Depends(require_cv_enabled)])
def cv_reset_style(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    settings = q.get_cv_settings(conn)
    q.save_cv_settings(
        conn, base_cv=settings["base_cv"], base_instruction="",
        base_guardrails=settings["base_guardrails"], css=settings["css"],
        default_scope=settings["default_scope"],
    )
    ctx = _advanced_ctx(conn)
    ctx["saved"] = True
    return templates.TemplateResponse(request, "cv/advanced.html", ctx)


@router.post("/cv/reset-css", response_class=HTMLResponse, dependencies=[Depends(require_cv_enabled)])
def cv_reset_css(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    settings = q.get_cv_settings(conn)
    q.save_cv_settings(
        conn, base_cv=settings["base_cv"], base_instruction=settings["base_instruction"],
        base_guardrails=settings["base_guardrails"], css="",
        default_scope=settings["default_scope"],
    )
    ctx = _advanced_ctx(conn)
    ctx["saved"] = True
    return templates.TemplateResponse(request, "cv/advanced.html", ctx)
```

Delete the old `_settings_ctx` function entirely (superseded by `_cv_page_ctx`/`_advanced_ctx`).

- [ ] **Step 4: Create `app/templates/cv/index.html`**

```html
{% extends "base.html" %}
{% block title %}CV — Job Seek{% endblock %}
{% block content %}
<h1>CV</h1>
<p style="color:var(--text-muted);">Your base CV — what every tailored, per-job version starts
from. The system never edits it.</p>

{% if saved %}<div class="save-confirmation" aria-live="polite">Saved.</div>{% endif %}

<form method="post" action="/cv">
  <label>Base CV (markdown)<br>
    <textarea name="base_cv" style="width:100%;min-height:420px;font-family:monospace;box-sizing:border-box;">{{ settings.base_cv }}</textarea>
  </label>
  <div style="margin-top:.75rem;display:flex;gap:.5rem;flex-wrap:wrap;">
    <button type="submit" class="btn btn-primary">Save</button>
    <button type="button" class="btn" hx-post="/cv/preview" hx-target="#cv-base-preview" hx-swap="innerHTML">Preview</button>
  </div>
</form>

<div id="cv-base-preview" style="margin-top:1rem;"></div>

<p style="margin-top:1.5rem;"><a href="/cv/advanced">Advanced / experimental CV settings &rarr;</a></p>
{% endblock %}
```

- [ ] **Step 5: Create `app/templates/cv/_preview_result.html`**

```html
{% if error %}
<p class="muted">Preview unavailable: {{ error }}</p>
{% elif images %}
<div class="cv-preview-pages">
  {% for img in images %}
  <img src="data:image/png;base64,{{ img }}" alt="CV page {{ loop.index }}"
       style="max-width:100%;border:1px solid var(--border);margin-bottom:.5rem;">
  {% endfor %}
</div>
{% else %}
<p class="muted">Nothing to preview yet — save a base CV first.</p>
{% endif %}
```

- [ ] **Step 6: Rename `cv/settings.html` to `cv/advanced.html` and rewrite it**

```bash
git mv app/templates/cv/settings.html app/templates/cv/advanced.html
```

Replace its content:

```html
{% extends "base.html" %}
{% block title %}Advanced CV settings — Job Seek{% endblock %}
{% block content %}
<p><a href="/cv">&larr; Back to CV</a></p>
{% include "setup/_subnav.html" %}
<h1>Advanced CV settings <span class="tag">experimental</span></h1>
<p style="color:var(--text-muted);">Rules the tailoring pipeline uses when adapting your base
CV to a job. All fields are yours to maintain; the system never edits them.</p>

<form id="cv-reset-guardrails-form" method="post" action="/cv/reset-guardrails"></form>
<form id="cv-reset-style-form" method="post" action="/cv/reset-style"></form>
<form id="cv-reset-css-form" method="post" action="/cv/reset-css"></form>

{% if saved %}<div class="save-confirmation" aria-live="polite">Saved.</div>{% endif %}
{% if error %}<div class="save-confirmation" style="background:var(--alert-tint);color:var(--alert);animation:none;">{{ error }}</div>{% endif %}

<form method="post" action="/cv/advanced">
  <h2>Guardrails</h2>
  <label>Guardrails (one per line)<br>
    <textarea name="base_guardrails" style="width:100%;min-height:140px;font-family:monospace;box-sizing:border-box;">{{ settings.base_guardrails }}</textarea>
  </label>
  <p style="color:var(--text-muted);font-size:.85em;">
    Hard limits the tailored CV must never break — fully yours to edit.
    <button type="submit" form="cv-reset-guardrails-form" class="btn btn-subtle" style="margin-left:.5rem;">Reset to defaults</button>
  </p>

  <h2 style="margin-top:1.5rem;">Editing scopes</h2>
  <div id="cv-scope-options">{% include "cv/_scope_options.html" %}</div>

  <h2 style="margin-top:1.5rem;">Writing style</h2>
  <label>Instruction (optional)<br>
    <textarea name="base_instruction" style="width:100%;min-height:60px;font-family:monospace;box-sizing:border-box;">{{ settings.base_instruction }}</textarea>
  </label>
  <p style="color:var(--text-muted);font-size:.85em;">
    Global steering, not the tailoring mandate — e.g. "British English, active voice."
    <button type="submit" form="cv-reset-style-form" class="btn btn-subtle" style="margin-left:.5rem;">Reset to defaults</button>
  </p>

  <h2 style="margin-top:1.5rem;">Appearance (CSS)</h2>
  <label>Custom CSS (optional; appended after doc-write defaults)<br>
    <textarea name="css" style="width:100%;min-height:100px;font-family:monospace;box-sizing:border-box;">{{ settings.css }}</textarea>
  </label>
  <p style="color:var(--text-muted);font-size:.85em;">
    No <code>@import</code>; <code>url()</code> only with <code>data:</code> URIs.
    <button type="submit" form="cv-reset-css-form" class="btn btn-subtle" style="margin-left:.5rem;">Reset to defaults</button>
  </p>

  <button type="submit" class="btn btn-primary" style="margin-top:1.5rem;">Save</button>
</form>
{% endblock %}
```

Note: `cv/_scope_options.html` doesn't exist until Task 5 — for this task, create a minimal placeholder so the page renders (Task 5 replaces it):

```html
<p class="muted">(scope list — added in the next task)</p>
```

- [ ] **Step 7: Update `app/templates/setup/_subnav.html`**

```html
    {% if cv_enabled %}
    <a href="/cv" {% if request.url.path == "/cv" %}class="active"{% endif %}>CV</a>
    {% endif %}
```

becomes:

```html
    {% if cv_enabled %}
    <a href="/cv/advanced" {% if request.url.path == "/cv/advanced" %}class="active"{% endif %}>CV</a>
    {% endif %}
```

- [ ] **Step 8: Run to verify Step 1's tests pass**

Run: `python -m pytest tests/test_routes_cv_settings.py -v`
Expected: PASS (the placeholder in Step 6 means `test_cv_advanced_page_has_headed_sections`'s `"Editing scopes" in r.text` assertion passes off the `<h2>Editing scopes</h2>` heading itself, independent of the Task-5 list content)

- [ ] **Step 9: Run the full CV + jobs test suites for regressions**

Run: `python -m pytest tests/test_routes_cv_settings.py tests/test_routes_cv_workbench.py tests/test_routes_cv_actions.py tests/test_routes_jobs.py tests/test_schema.py -q`
Expected: all PASS

- [ ] **Step 10: Commit**

```bash
git add app/routes/cv.py app/templates/cv/index.html app/templates/cv/_preview_result.html app/templates/cv/advanced.html app/templates/setup/_subnav.html tests/test_routes_cv_settings.py
git rm app/templates/cv/settings.html 2>/dev/null || true
git commit -m "feat: split /cv into a primary page (base CV + preview) and /cv/advanced

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01YAUuWYfmrM6HK7gL7xmnUm"
```

(the `git rm` is a no-op / safe if `git mv` in Step 6 already staged the rename — included so the task is self-contained if run out of order)

---

### Task 5: Editable scope-options list UI + plan-pane checkboxes

**Files:**
- Modify: `app/routes/cv.py`
- Create: `app/templates/cv/_scope_options.html`
- Create: `app/templates/cv/_scope_option.html`
- Create: `app/templates/cv/_scope_option_edit.html`
- Modify: `app/templates/cv/_plan_pane.html`
- Test: `tests/test_routes_cv_settings.py`, `tests/test_routes_cv_workbench.py`

**Interfaces:**
- Produces: `POST /cv/scope-options` (add), `GET /cv/scope-options/{id}/edit`, `GET /cv/scope-options/{id}` (cancel-back-to-display), `POST /cv/scope-options/{id}` (save edit), `DELETE /cv/scope-options/{id}`, `POST /cv/scope-options/reset`.
- Consumes (Task 1): `q.get_scope_options`, `q.insert_scope_option`, `q.get_scope_option`, `q.update_scope_option`, `q.delete_scope_option`, `q.reset_scope_options`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_routes_cv_settings.py`:

```python
def test_scope_options_add(client, cv_on, conn):
    r = client.post("/cv/scope-options", data={"description": "Custom scope.", "default_enabled": "on"})
    assert r.status_code == 200
    assert "Custom scope." in r.text
    opts = q.get_scope_options(conn)
    assert opts[-1]["description"] == "Custom scope."
    assert opts[-1]["default_enabled"] == 1


def test_scope_options_edit_form(client, cv_on, conn):
    opt_id = q.insert_scope_option(conn, "Original text.")
    r = client.get(f"/cv/scope-options/{opt_id}/edit")
    assert r.status_code == 200
    assert "Original text." in r.text
    assert "<textarea" in r.text or "<input" in r.text


def test_scope_options_save_edit(client, cv_on, conn):
    opt_id = q.insert_scope_option(conn, "Original text.")
    r = client.post(f"/cv/scope-options/{opt_id}", data={"description": "Edited text.", "default_enabled": "on"})
    assert r.status_code == 200
    assert "Edited text." in r.text
    assert q.get_scope_option(conn, opt_id)["description"] == "Edited text."


def test_scope_options_delete(client, cv_on, conn):
    opt_id = q.insert_scope_option(conn, "Delete me.")
    r = client.delete(f"/cv/scope-options/{opt_id}")
    assert r.status_code == 200
    assert q.get_scope_option(conn, opt_id) is None


def test_scope_options_reset_restores_defaults(client, cv_on, conn):
    q.reset_scope_options(conn)  # ensure a known baseline (conftest's conn already seeds via init_db)
    opts = q.get_scope_options(conn)
    q.update_scope_option(conn, opts[0]["id"], "Mangled.", default_enabled=False)
    r = client.post("/cv/scope-options/reset")
    assert r.status_code == 200
    fresh = q.get_scope_options(conn)
    assert len(fresh) == 4
    assert "Mangled." not in [o["description"] for o in fresh]
```

Add to `tests/test_routes_cv_workbench.py`:

```python
def test_plan_pane_scope_checkboxes_use_live_descriptions(client, cv_on, conn):
    jid = _job(conn)
    opts = q.get_scope_options(conn)
    r = client.get(f"/jobs/{jid}/cv")
    for opt in opts:
        assert opt["description"] in r.text
    assert f'value="{opts[0]["id"]}"' in r.text
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_routes_cv_settings.py -k scope_options -v tests/test_routes_cv_workbench.py -k live_descriptions -v`
Expected: FAIL

- [ ] **Step 3: Add the CRUD routes to `app/routes/cv.py`**

Add near the end of the file, after the reset routes from Task 4:

```python
@router.post("/cv/scope-options", response_class=HTMLResponse, dependencies=[Depends(require_cv_enabled)])
async def add_scope_option(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    form = await request.form()
    description = form.get("description", "").strip()
    if description:
        q.insert_scope_option(conn, description, default_enabled=bool(form.get("default_enabled")))
    return templates.TemplateResponse(
        request, "cv/_scope_options.html", {"scope_options": q.get_scope_options(conn)}
    )


@router.get("/cv/scope-options/{scope_option_id}/edit", response_class=HTMLResponse,
            dependencies=[Depends(require_cv_enabled)])
def edit_scope_option_form(scope_option_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    opt = q.get_scope_option(conn, scope_option_id)
    if opt is None:
        raise HTTPException(status_code=404, detail="No such scope option")
    return templates.TemplateResponse(request, "cv/_scope_option_edit.html", {"o": opt})


@router.get("/cv/scope-options/{scope_option_id}", response_class=HTMLResponse,
            dependencies=[Depends(require_cv_enabled)])
def scope_option_row(scope_option_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    opt = q.get_scope_option(conn, scope_option_id)
    if opt is None:
        raise HTTPException(status_code=404, detail="No such scope option")
    return templates.TemplateResponse(request, "cv/_scope_option.html", {"o": opt})


@router.post("/cv/scope-options/{scope_option_id}", response_class=HTMLResponse,
             dependencies=[Depends(require_cv_enabled)])
async def update_scope_option_route(
    scope_option_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)
):
    if q.get_scope_option(conn, scope_option_id) is None:
        raise HTTPException(status_code=404, detail="No such scope option")
    form = await request.form()
    q.update_scope_option(
        conn, scope_option_id, form.get("description", "").strip(),
        default_enabled=bool(form.get("default_enabled")),
    )
    opt = q.get_scope_option(conn, scope_option_id)
    return templates.TemplateResponse(request, "cv/_scope_option.html", {"o": opt})


@router.delete("/cv/scope-options/{scope_option_id}", response_class=HTMLResponse,
               dependencies=[Depends(require_cv_enabled)])
def delete_scope_option_route(scope_option_id: int, conn: sqlite3.Connection = Depends(get_db)):
    q.delete_scope_option(conn, scope_option_id)
    return HTMLResponse(content="")


@router.post("/cv/scope-options/reset", response_class=HTMLResponse, dependencies=[Depends(require_cv_enabled)])
def reset_scope_options_route(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    q.reset_scope_options(conn)
    return templates.TemplateResponse(
        request, "cv/_scope_options.html", {"scope_options": q.get_scope_options(conn)}
    )
```

- [ ] **Step 4: Create `app/templates/cv/_scope_option.html`**

```html
<li id="scope-option-{{ o.id }}" style="display:flex; align-items:center; gap:0.5rem; margin-bottom:0.3rem;">
  <span>{{ o.description }}</span>
  <span class="tag{% if o.default_enabled %} tag-accent{% endif %}">{% if o.default_enabled %}default on{% else %}default off{% endif %}</span>
  <button class="btn" style="padding:2px 8px; font-size:0.8em;"
    hx-get="/cv/scope-options/{{ o.id }}/edit"
    hx-target="#scope-option-{{ o.id }}"
    hx-swap="outerHTML">Edit</button>
  <button class="btn" style="padding:2px 8px; font-size:0.8em;"
    hx-delete="/cv/scope-options/{{ o.id }}"
    hx-target="#scope-option-{{ o.id }}"
    hx-swap="outerHTML">&#x2715;</button>
</li>
```

- [ ] **Step 5: Create `app/templates/cv/_scope_option_edit.html`**

```html
<li id="scope-option-{{ o.id }}" style="display:flex; align-items:center; gap:0.5rem; margin-bottom:0.3rem;">
  <form hx-post="/cv/scope-options/{{ o.id }}"
        hx-target="#scope-option-{{ o.id }}"
        hx-swap="outerHTML"
        style="display:flex; gap:0.5rem; flex:1; align-items:center;">
    <input type="text" name="description" value="{{ o.description }}" required style="flex:1; min-width:200px;">
    <label style="font-size:.85em;white-space:nowrap;">
      <input type="checkbox" name="default_enabled" {% if o.default_enabled %}checked{% endif %}> default on
    </label>
    <button type="button" class="btn btn-subtle"
      hx-get="/cv/scope-options/{{ o.id }}"
      hx-target="#scope-option-{{ o.id }}"
      hx-swap="outerHTML">Cancel</button>
    <button type="submit" class="btn btn-primary">Save</button>
  </form>
</li>
```

- [ ] **Step 6: Create `app/templates/cv/_scope_options.html`**

```html
<div id="cv-scope-options-list">
  <ul style="list-style:none; padding:0;">
    {% for o in scope_options %}
    {% include "cv/_scope_option.html" %}
    {% else %}
    <li class="muted">No scope types yet.</li>
    {% endfor %}
  </ul>
  <form hx-post="/cv/scope-options"
        hx-target="#cv-scope-options-list"
        hx-swap="outerHTML"
        style="display:flex; gap:0.5rem; flex-wrap:wrap; margin-top:0.5rem;">
    <input type="text" name="description" placeholder="Describe a new permitted edit…" required
           style="flex:1; min-width:240px;">
    <label style="font-size:.85em;white-space:nowrap;">
      <input type="checkbox" name="default_enabled"> default on
    </label>
    <button type="submit" class="btn">Add a scope type</button>
  </form>
  <button type="button" class="btn btn-subtle" style="margin-top:0.5rem;"
    hx-post="/cv/scope-options/reset"
    hx-target="#cv-scope-options-list"
    hx-swap="outerHTML">Reset to defaults</button>
</div>
```

Note: the add-form's `hx-target`/`hx-swap` target `#cv-scope-options-list` (the inner div), but the route handlers return `cv/_scope_options.html` in full (which re-renders that same wrapping div) — `hx-swap="outerHTML"` on a target matching the response's own root id is the same pattern already used by `scenarios/_criteria.html`'s add-criterion form; copy that shape exactly.

- [ ] **Step 7: Update `app/templates/cv/advanced.html`'s placeholder from Task 4**

Replace:

```html
  <h2 style="margin-top:1.5rem;">Editing scopes</h2>
  <div id="cv-scope-options">{% include "cv/_scope_options.html" %}</div>
```

Keep as-is (the placeholder text was only ever inside `_scope_options.html` itself, now replaced by Step 6's real content — no change needed here since the include path is unchanged).

- [ ] **Step 8: Update `app/templates/cv/_plan_pane.html`'s scope checkboxes**

Replace:

```html
  <fieldset>
    <legend>Edit scope</legend>
    {% for key in scope_order %}
    <label style="display:block;font-size:.9em;">
      <input type="checkbox" name="scope" value="{{ key }}"
             {% if job_cv and key in job_cv.scope %}checked{% elif not job_cv and key in settings.default_scope %}checked{% endif %}>
      {{ key }}
    </label>
    {% endfor %}
  </fieldset>
```

with:

```html
  <fieldset>
    <legend>Edit scope</legend>
    {% for opt in scope_options %}
    <label style="display:block;font-size:.9em;">
      <input type="checkbox" name="scope" value="{{ opt.id }}"
             {% if job_cv and opt.id in job_cv.scope %}checked{% elif not job_cv and opt.default_enabled %}checked{% endif %}>
      {{ opt.description }}
    </label>
    {% endfor %}
  </fieldset>
```

And the reset-to-plan hidden-scope-inputs line:

```html
    {% for key in scope_order %}{% if job_cv and key in job_cv.scope %}<input type="hidden" name="scope" value="{{ key }}">{% endif %}{% endfor %}
```

becomes:

```html
    {% for opt in scope_options %}{% if job_cv and opt.id in job_cv.scope %}<input type="hidden" name="scope" value="{{ opt.id }}">{% endif %}{% endfor %}
```

- [ ] **Step 9: Update `app/templates/cv/settings.html`'s (now `advanced.html`) leftover reference**

Search `app/templates/cv/advanced.html` for the "Add this to your base CV" style leftover `/cv` link from the old plan-pane strengthen-category hint — that one lives in `_plan_pane.html`, not `advanced.html`, and already points at `/cv` correctly (the primary page is exactly where base-CV editing now lives), so no change needed there. Just double-check by grepping:

```bash
grep -rn 'href="/cv"' app/templates/
```

Expected: only the "Edit base CV ↗" link inside `_plan_pane.html`'s proposed-plan list, and the "Back to CV" links — both already correct since `/cv` is the primary base-CV page. No edits needed.

- [ ] **Step 10: Run to verify Step 1's tests pass**

Run: `python -m pytest tests/test_routes_cv_settings.py tests/test_routes_cv_workbench.py -q`
Expected: all PASS

- [ ] **Step 11: Run the full suite**

Run: `python -m pytest -q`
Expected: all PASS, no regressions anywhere

- [ ] **Step 12: Commit**

```bash
git add app/routes/cv.py app/templates/cv/_scope_options.html app/templates/cv/_scope_option.html app/templates/cv/_scope_option_edit.html app/templates/cv/_plan_pane.html app/templates/cv/advanced.html tests/test_routes_cv_settings.py tests/test_routes_cv_workbench.py
git commit -m "feat: editable scope-options list UI, plan pane reads live descriptions

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01YAUuWYfmrM6HK7gL7xmnUm"
```

---

### Final check: full test suite + manual verification

- [ ] **Step 1: Run the entire suite**

Run: `python -m pytest -q`
Expected: all PASS.

- [ ] **Step 2: Manually verify with the `run-dev-server` skill**

Visit `/cv` (reachable from main nav after Profile): edit the base CV, Save, click Preview and confirm rendered pages appear inline. Click through to `/cv/advanced`: confirm Guardrails / Editing scopes / Writing style / Appearance sections each have their own heading and Reset-to-defaults button, and that adding/editing/deleting a scope type works. Open a job's `/jobs/{id}/cv` workbench and confirm the scope checkboxes show the live descriptions and still drive Plan/Generate correctly.
