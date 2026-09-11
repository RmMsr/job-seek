# Editable tailored CV (ink-mde) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a light CodeMirror-6 markdown editor (ink-mde) to the tailored CV, base CV, and profile, with the tailored CV autosaving and its guardrail check re-runnable after a hand edit.

**Architecture:** A shared Jinja macro + a `window.initInkEditors` ES-module helper in `base.html` mount ink-mde onto any `textarea[data-ink]`, mirroring the value back into the hidden textarea so existing form posts and dirty-checks keep working. The tailored CV gets a fourth "Edit" preview tab whose editor debounce-autosaves to a new route; that route recomputes the pure-Python diff summary and marks guardrails stale via out-of-band swaps. A new `cv_tailor` task mode re-runs only `check_guardrails` and re-renders only `#cv-findings`.

**Tech Stack:** Python 3.12, FastAPI, SQLite, Jinja2, HTMX (no JS build step), ink-mde 0.34.0 loaded from esm.sh at runtime.

## Global Constraints

- No JS build step. Third-party JS loads from a CDN at runtime (matches htmx / Google Fonts already in `base.html`). No `StaticFiles` mount is added.
- ink-mde pinned to exactly `0.34.0`, imported from `https://esm.sh/ink-mde@0.34.0?bundle`.
- Migrations: personal single-instance app — plain `ALTER TABLE ADD COLUMN`, no backfill, no dual-schema shims (`docs` philosophy). Follow the existing `_migrate_job_cv_add_*` pattern in `app/db/schema.py`.
- `job_cv.tailored_cv` is still overwritten wholesale by the `generate` task — the manual-edit ↔ Update collision is a **later** change, explicitly not handled here.
- Do NOT add ink to the directives, guardrails, or CSS textareas.
- Run tests with `python -m pytest` (not `uv run` — read-only cache in the sandbox). Dev server: `python -m uvicorn app.main:app --reload` against a throwaway DB copy.
- Commit after every task.

## File Structure

**New:**
- `app/templates/_ink_editor.html` — the `ink_editor()` macro (hidden textarea + mount div).
- `app/templates/cv/_save_tailored_oob.html` — the three OOB fragments the autosave route returns.

**Modified:**
- `app/db/schema.py` — `edited_at`, `guardrails_checked_at` columns on `job_cv` + two migration fns.
- `app/db/queries.py` — `set_job_cv_tailored()`.
- `app/routes/cv.py` — `_guardrail_status` staleness clause; `_persist_draft` stamp; `save-tailored` + `recheck-guardrails` routes; `recheck` task branch; `_rendered_chunk` / `_tailor_result` `findings` case.
- `app/templates/base.html` — ink ES-module, `initInkEditors`, autosave wiring, `htmx:afterSwap` hook, `--ink-*` theme CSS, editor/tab CSS.
- `app/templates/cv/_cv_diff_summary.html` — (no change to body; wrapper added by callers).
- `app/templates/cv/_preview_pane.html` — `#cv-diff-summary` wrapper, `#cv-editor-status` span, no-doc-write branch uses the macro.
- `app/templates/cv/_preview_tabs.html` — fourth "Edit" tab + editor mount.
- `app/templates/cv/_findings.html` — "Re-check" button, repointed stale copy.
- `app/templates/cv/index.html` — base CV textarea → macro.
- `app/templates/profile/_editor.html` — content textarea → macro + adapt inline script.
- `tests/test_schema.py` — extend the `job_cv` column-set assertion + migration test.

---

### Task 1: Schema — two new `job_cv` columns

**Files:**
- Modify: `app/db/schema.py` (DDL near line 31–48; migration fns near line 990; `init_db` list near line 1045)
- Test: `tests/test_schema.py` (`test_job_cv_table_columns_and_cascade` ~line 1260; new test near line 1508)

**Interfaces:**
- Produces: `job_cv.edited_at TEXT` (nullable), `job_cv.guardrails_checked_at TEXT` (nullable); migration fns `_migrate_job_cv_add_edited_at`, `_migrate_job_cv_add_guardrails_checked_at`.

- [ ] **Step 1: Update the column-set test to expect the new columns**

In `tests/test_schema.py`, `test_job_cv_table_columns_and_cascade`, add `"edited_at"` and `"guardrails_checked_at"` to the expected `cols` set:

```python
    assert cols == {
        "job_id", "scope", "tuning_directives", "plan", "handled_suggestions", "tailored_cv",
        "guardrail_findings", "change_report", "base_hash", "base_cv_snapshot",
        "plan_generated_at", "directives_edited_at", "generated_at",
        "scope_edited_at", "plan_context_hash", "edited_at", "guardrails_checked_at",
        "finalized_at", "updated_at",
    }
```

- [ ] **Step 2: Run it, expect failure**

Run: `python -m pytest tests/test_schema.py::test_job_cv_table_columns_and_cascade -v`
Expected: FAIL — `assert cols == {...}` mismatch (missing `edited_at`, `guardrails_checked_at`).

- [ ] **Step 3: Add the columns to the DDL**

In `app/db/schema.py`, in the `CREATE TABLE IF NOT EXISTS job_cv (...)` block, add after `scope_edited_at TEXT,`:

```sql
    edited_at TEXT,
    guardrails_checked_at TEXT,
```

- [ ] **Step 4: Add the migration functions**

In `app/db/schema.py`, after `_migrate_job_cv_add_plan_context_hash`:

```python
def _migrate_job_cv_add_edited_at(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(job_cv)")}
    if "edited_at" not in cols:
        conn.execute("ALTER TABLE job_cv ADD COLUMN edited_at TEXT")
        conn.commit()


def _migrate_job_cv_add_guardrails_checked_at(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(job_cv)")}
    if "guardrails_checked_at" not in cols:
        conn.execute("ALTER TABLE job_cv ADD COLUMN guardrails_checked_at TEXT")
        conn.commit()
```

Register them in `init_db`, right after `_migrate_job_cv_add_plan_context_hash(conn)`:

```python
    _migrate_job_cv_add_edited_at(conn)
    _migrate_job_cv_add_guardrails_checked_at(conn)
```

- [ ] **Step 5: Add a migration test**

In `tests/test_schema.py`, after `test_job_cv_migrations_are_idempotent` (or near the other `job_cv` migration tests ~line 1508):

```python
def test_job_cv_has_edited_at_and_guardrails_checked_at(conn):
    init_db(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(job_cv)")}
    assert {"edited_at", "guardrails_checked_at"} <= cols


def test_job_cv_add_edited_at_migrations_are_idempotent(conn):
    from app.db.schema import (
        _migrate_job_cv_add_edited_at, _migrate_job_cv_add_guardrails_checked_at,
    )
    init_db(conn)
    _migrate_job_cv_add_edited_at(conn)
    _migrate_job_cv_add_edited_at(conn)
    _migrate_job_cv_add_guardrails_checked_at(conn)
    _migrate_job_cv_add_guardrails_checked_at(conn)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(job_cv)")]
    assert cols.count("edited_at") == 1
    assert cols.count("guardrails_checked_at") == 1
```

- [ ] **Step 6: Run the schema tests**

Run: `python -m pytest tests/test_schema.py -v`
Expected: PASS (all, including the two new tests and the updated column-set test).

- [ ] **Step 7: Commit**

```bash
git add app/db/schema.py tests/test_schema.py
git commit -m "schema: add job_cv.edited_at and job_cv.guardrails_checked_at"
```

---

### Task 2: Query — `set_job_cv_tailored`

**Files:**
- Modify: `app/db/queries.py` (near `set_job_cv_directives` ~line 168)
- Test: `tests/test_queries.py`

**Interfaces:**
- Consumes: `job_cv.edited_at` (Task 1).
- Produces: `set_job_cv_tailored(conn: sqlite3.Connection, job_id: int, markdown: str) -> None` — sets `tailored_cv`, stamps `edited_at` and `updated_at`.

- [ ] **Step 1: Write the failing test**

In `tests/test_queries.py`:

```python
def test_set_job_cv_tailored_persists_and_stamps_edited_at(conn):
    conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s','http://x','manual')")
    conn.execute("INSERT INTO jobs (source_id, url, title) VALUES (1,'http://x/1','R')")
    conn.commit()
    q.set_job_cv_tailored(conn, 1, "# Edited by hand\n")
    row = q.get_job_cv(conn, 1)
    assert row["tailored_cv"] == "# Edited by hand\n"
    assert row["edited_at"] is not None
```

- [ ] **Step 2: Run it, expect failure**

Run: `python -m pytest tests/test_queries.py::test_set_job_cv_tailored_persists_and_stamps_edited_at -v`
Expected: FAIL — `AttributeError: module 'app.db.queries' has no attribute 'set_job_cv_tailored'`.

- [ ] **Step 3: Implement**

In `app/db/queries.py`, after `set_job_cv_directives`:

```python
def set_job_cv_tailored(conn: sqlite3.Connection, job_id: int, markdown: str) -> None:
    conn.execute("INSERT OR IGNORE INTO job_cv (job_id) VALUES (?)", (job_id,))
    conn.execute(
        "UPDATE job_cv SET tailored_cv = ?, edited_at = datetime('now'), "
        "updated_at = datetime('now') WHERE job_id = ?",
        (markdown, job_id),
    )
    conn.commit()
```

- [ ] **Step 4: Run it, expect pass**

Run: `python -m pytest tests/test_queries.py::test_set_job_cv_tailored_persists_and_stamps_edited_at -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/db/queries.py tests/test_queries.py
git commit -m "queries: add set_job_cv_tailored (stamps edited_at)"
```

---

### Task 3: Guardrail staleness after a hand edit

**Files:**
- Modify: `app/routes/cv.py` (`_persist_draft` ~line 72–89; `_guardrail_status` ~line 128–133)
- Test: `tests/test_routes_cv_workbench.py` (or a new `tests/test_cv_guardrail_status.py`)

**Interfaces:**
- Consumes: `job_cv.edited_at`, `job_cv.guardrails_checked_at` (Task 1).
- Produces: `_guardrail_status(job_cv, settings, running)` returns `"stale"` when the CV was hand-edited after its last guardrail check. `_persist_draft` now also stamps `guardrails_checked_at`.

- [ ] **Step 1: Write the failing tests**

New file `tests/test_cv_guardrail_status.py`:

```python
from app.routes.cv import _guardrail_status

_SETTINGS = {"base_cv": "# Me", "base_instruction": "", "base_guardrails": "no lies"}
_FINDINGS = [{"rule": "no lies", "verdict": "ok", "explanation": ""}]


def _base_hash_of(settings):
    from app.routes.cv import _base_hash
    return _base_hash(settings)


def test_guardrail_status_stale_when_edited_after_check():
    jc = {
        "tailored_cv": "# Draft", "guardrail_findings": _FINDINGS,
        "base_hash": _base_hash_of(_SETTINGS),
        "generated_at": "2026-09-10 10:00:00",
        "guardrails_checked_at": "2026-09-10 10:00:00",
        "edited_at": "2026-09-10 11:00:00",
    }
    assert _guardrail_status(jc, _SETTINGS, running=False) == "stale"


def test_guardrail_status_fresh_when_check_after_edit():
    jc = {
        "tailored_cv": "# Draft", "guardrail_findings": _FINDINGS,
        "base_hash": _base_hash_of(_SETTINGS),
        "generated_at": "2026-09-10 10:00:00",
        "edited_at": "2026-09-10 11:00:00",
        "guardrails_checked_at": "2026-09-10 11:30:00",
    }
    assert _guardrail_status(jc, _SETTINGS, running=False) == "fresh"


def test_guardrail_status_fresh_when_never_edited():
    jc = {
        "tailored_cv": "# Draft", "guardrail_findings": _FINDINGS,
        "base_hash": _base_hash_of(_SETTINGS),
        "generated_at": "2026-09-10 10:00:00",
        "guardrails_checked_at": "2026-09-10 10:00:00",
        "edited_at": None,
    }
    assert _guardrail_status(jc, _SETTINGS, running=False) == "fresh"
```

- [ ] **Step 2: Run, expect failure**

Run: `python -m pytest tests/test_cv_guardrail_status.py -v`
Expected: FAIL — `test_guardrail_status_stale_when_edited_after_check` returns `"fresh"`.

- [ ] **Step 3: Add the staleness helper + clause**

In `app/routes/cv.py`, add a module-level helper near `_draft_stale`:

```python
def _edited_since_guardrail_check(job_cv: dict | None) -> bool:
    """The CV was hand-edited (autosave stamps edited_at) after its guardrail
    findings were last computed — so the findings no longer describe the shown
    markdown."""
    if not job_cv:
        return False
    ea = job_cv.get("edited_at")
    if not ea:
        return False
    ca = job_cv.get("guardrails_checked_at")
    return not ca or ca < ea
```

Change `_guardrail_status` to:

```python
def _guardrail_status(job_cv: dict | None, settings: dict, running: bool) -> str:
    if running:
        return "running"
    if not job_cv or not job_cv.get("tailored_cv") or not job_cv.get("guardrail_findings"):
        return "none"
    if _edited_since_guardrail_check(job_cv):
        return "stale"
    return "stale" if _draft_stale(job_cv, settings) else "fresh"
```

- [ ] **Step 4: Stamp `guardrails_checked_at` in `_persist_draft`**

In `app/routes/cv.py`, `_persist_draft`, change the post-upsert `UPDATE` from:

```python
    conn.execute("UPDATE job_cv SET generated_at = datetime('now') WHERE job_id = ?", (job_id,))
```

to:

```python
    conn.execute(
        "UPDATE job_cv SET generated_at = datetime('now'), "
        "guardrails_checked_at = datetime('now') WHERE job_id = ?",
        (job_id,),
    )
```

- [ ] **Step 5: Run the new tests + the existing workbench/task suites**

Run: `python -m pytest tests/test_cv_guardrail_status.py tests/test_routes_cv_workbench.py tests/test_cv_task.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/routes/cv.py tests/test_cv_guardrail_status.py
git commit -m "cv: guardrail findings go stale after a hand edit; stamp guardrails_checked_at"
```

---

### Task 4: `save-tailored` autosave route + OOB fragments

**Files:**
- Create: `app/templates/cv/_save_tailored_oob.html`
- Modify: `app/routes/cv.py` (new route near `cv_save_scope` ~line 618); `app/templates/cv/_preview_pane.html`
- Test: `tests/test_routes_cv_actions.py`

**Interfaces:**
- Consumes: `set_job_cv_tailored` (Task 2), `_require_editable` (existing), `_workbench_ctx` (existing).
- Produces: `POST /jobs/{job_id}/cv/save-tailored` form field `markdown` → 200 with OOB HTML (`#cv-diff-summary`, `#cv-findings`, `#cv-editor-status`); 409 if finalized; 404 if job missing.

- [ ] **Step 1: Write the failing tests**

In `tests/test_routes_cv_actions.py`:

```python
def test_save_tailored_persists_and_returns_oob(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Old", base_cv_snapshot="# Me\n")
    r = client.post(f"/jobs/{jid}/cv/save-tailored", data={"markdown": "# New hand-edited\n\n- extra\n"})
    assert r.status_code == 200
    assert q.get_job_cv(conn, jid)["tailored_cv"] == "# New hand-edited\n\n- extra\n"
    assert q.get_job_cv(conn, jid)["edited_at"] is not None
    assert 'id="cv-diff-summary"' in r.text and 'hx-swap-oob="true"' in r.text
    assert 'id="cv-findings"' in r.text
    assert 'id="cv-editor-status"' in r.text


def test_save_tailored_marks_guardrails_stale(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Old",
                    guardrail_findings=[{"rule": "r", "verdict": "ok", "explanation": ""}])
    conn.execute("UPDATE job_cv SET guardrails_checked_at = datetime('now', '-1 hour') WHERE job_id = ?", (jid,))
    conn.commit()
    r = client.post(f"/jobs/{jid}/cv/save-tailored", data={"markdown": "# changed\n"})
    assert 'data-state="stale"' in r.text


def test_save_tailored_404_missing_job(client, conn):
    assert client.post("/jobs/999/cv/save-tailored", data={"markdown": "x"}).status_code == 404


def test_save_tailored_409_when_finalized(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Done")
    q.finalize_job_cv(conn, jid)
    assert client.post(f"/jobs/{jid}/cv/save-tailored", data={"markdown": "x"}).status_code == 409
```

- [ ] **Step 2: Run, expect failure**

Run: `python -m pytest tests/test_routes_cv_actions.py -k save_tailored -v`
Expected: FAIL — 404 for all (route not defined).

- [ ] **Step 3: Create the OOB partial**

`app/templates/cv/_save_tailored_oob.html`:

```jinja
{# Out-of-band fragments returned by POST /jobs/{id}/cv/save-tailored, applied
   by htmx.ajax() after a debounced autosave. Rendered with _workbench_ctx. #}
<div id="cv-diff-summary" hx-swap-oob="true">{% include "cv/_cv_diff_summary.html" %}</div>
<div id="cv-findings" hx-swap-oob="true">{% include "cv/_findings.html" %}</div>
<span id="cv-editor-status" class="cv-editor-status" hx-swap-oob="true" aria-live="polite">Saved</span>
```

- [ ] **Step 4: Add the `#cv-diff-summary` wrapper and `#cv-editor-status` span to the preview pane**

In `app/templates/cv/_preview_pane.html`:

Replace the line `{% include "cv/_cv_diff_summary.html" %}` with:

```jinja
<div id="cv-diff-summary">{% include "cv/_cv_diff_summary.html" %}</div>
```

In the `.cv-preview-actions` block, after the `cv-preview-progress` span, add:

```jinja
  <span id="cv-editor-status" class="cv-editor-status" aria-live="polite"></span>
```

- [ ] **Step 5: Add the route**

In `app/routes/cv.py`, after `cv_save_scope`:

```python
@router.post("/jobs/{job_id}/cv/save-tailored", response_class=HTMLResponse)
async def cv_save_tailored(job_id: int, request: Request,
                          conn: sqlite3.Connection = Depends(get_db)):
    _require_editable(conn, job_id)
    form = await request.form()
    q.set_job_cv_tailored(conn, job_id, form.get("markdown", ""))
    return templates.TemplateResponse(
        request, "cv/_save_tailored_oob.html", _workbench_ctx(conn, job_id),
    )
```

- [ ] **Step 6: Run the tests**

Run: `python -m pytest tests/test_routes_cv_actions.py -k save_tailored -v`
Expected: PASS.

- [ ] **Step 7: Regression — full cv route suites**

Run: `python -m pytest tests/test_routes_cv_actions.py tests/test_routes_cv_workbench.py -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add app/routes/cv.py app/templates/cv/_save_tailored_oob.html app/templates/cv/_preview_pane.html
git commit -m "cv: POST /jobs/{id}/cv/save-tailored — autosave with OOB diff/findings/status"
```

---

### Task 5: `recheck-guardrails` route + task mode + `findings` render

**Files:**
- Modify: `app/routes/cv.py` (`_rendered_chunk` ~line 154; `_tailor_result` ~line 174; `_task_cv_tailor` ~line 498; new route near `cv_generate` ~line 601)
- Modify: `app/templates/cv/_findings.html`
- Test: `tests/test_cv_task.py`, `tests/test_routes_cv_actions.py`

**Interfaces:**
- Consumes: `check_guardrails` (existing import), `_workbench_ctx`, `q.upsert_job_cv`.
- Produces: `POST /jobs/{job_id}/cv/recheck-guardrails` → `{task_id, already_active}`. `cv_tailor` task accepts `params["mode"] == "recheck"` and `params["render"] == "findings"`. `_rendered_chunk(conn, job_id, "findings")` → `<div id="cv-findings">…</div>`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_cv_task.py`:

```python
def test_recheck_mode_updates_findings_and_stamp(conn, cfg):
    jid = _seed(conn)
    q.save_cv_settings(conn, base_cv="# Me\n\n- Kafka work\n", base_instruction="",
                       base_guardrails="no fabrication", css="", default_scope=[1, 2])
    q.upsert_job_cv(conn, jid, tailored_cv="# Tailored\n\n- Kafka\n",
                    guardrail_findings=[{"rule": "old", "verdict": "ok", "explanation": ""}])
    with patch("app.routes.cv.check_guardrails",
               return_value={"findings": [{"rule": "no fabrication", "verdict": "violated",
                                           "explanation": "claim X"}]}):
        task = q.enqueue_task(conn, kind="cv_tailor",
                              params={"job_id": jid, "mode": "recheck", "render": "findings"})
        execute_task(conn, MagicMock(), "m", cfg, task)
    row = q.get_job_cv(conn, jid)
    assert row["guardrail_findings"][0]["rule"] == "no fabrication"
    assert row["guardrails_checked_at"] is not None
    result = q.get_task(conn, task["id"])["result"]
    assert any('id="cv-findings"' in c for c in result["html_chunks"])
    assert not any('id="cv-preview-pane"' in c for c in result["html_chunks"])


def test_recheck_mode_keeps_prior_findings_on_empty_result(conn, cfg):
    jid = _seed(conn)
    q.save_cv_settings(conn, base_cv="# Me\n", base_instruction="",
                       base_guardrails="no fabrication", css="", default_scope=[1, 2])
    q.upsert_job_cv(conn, jid, tailored_cv="# T\n",
                    guardrail_findings=[{"rule": "keep me", "verdict": "ok", "explanation": ""}])
    with patch("app.routes.cv.check_guardrails", return_value={"findings": []}):
        task = q.enqueue_task(conn, kind="cv_tailor",
                              params={"job_id": jid, "mode": "recheck", "render": "findings"})
        execute_task(conn, MagicMock(), "m", cfg, task)
    assert q.get_job_cv(conn, jid)["guardrail_findings"][0]["rule"] == "keep me"


def test_recheck_mode_noop_without_draft(conn, cfg):
    jid = _seed(conn)
    task = q.enqueue_task(conn, kind="cv_tailor",
                          params={"job_id": jid, "mode": "recheck", "render": "findings"})
    execute_task(conn, MagicMock(), "m", cfg, task)  # must not raise
    assert q.get_job_cv(conn, jid) is None or not q.get_job_cv(conn, jid)["tailored_cv"]
```

In `tests/test_routes_cv_actions.py`:

```python
def test_recheck_guardrails_endpoint_enqueues_task(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft")
    r = client.post(f"/jobs/{jid}/cv/recheck-guardrails")
    assert r.status_code == 200
    task = q.get_task(conn, r.json()["task_id"])
    assert task["kind"] == "cv_tailor"
    assert task["params"]["mode"] == "recheck"
    assert task["params"]["render"] == "findings"


def test_recheck_guardrails_409_when_finalized(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Done")
    q.finalize_job_cv(conn, jid)
    assert client.post(f"/jobs/{jid}/cv/recheck-guardrails").status_code == 409
```

- [ ] **Step 2: Run, expect failure**

Run: `python -m pytest tests/test_cv_task.py -k recheck tests/test_routes_cv_actions.py -k recheck -v`
Expected: FAIL — route 404; task treats unknown mode as `generate` fallthrough / KeyErrors.

- [ ] **Step 3: `_rendered_chunk` — add the `findings` case**

In `app/routes/cv.py`, `_rendered_chunk`, replace the `inner`/`wrapper` ternaries:

```python
    _PANE_TEMPLATES = {
        "plan_pane": ("cv/_plan_pane.html", "cv-plan-pane"),
        "preview_pane": ("cv/_preview_pane.html", "cv-preview-pane"),
        "findings": ("cv/_findings.html", "cv-findings"),
    }
    inner, wrapper = _PANE_TEMPLATES.get(which, _PANE_TEMPLATES["preview_pane"])
```

- [ ] **Step 4: `_tailor_result` — map `findings` to a single pane**

In `app/routes/cv.py`, `_tailor_result`, change the `panes` line:

```python
        if which == "preview_pane":
            panes = ["preview_pane", "plan_pane"]
        else:
            panes = [which]
```

(Leaves `plan_pane` and `findings` as single-pane renders; `preview_pane` still also refreshes the plan pane.)

- [ ] **Step 5: Add the `recheck` branch to `_task_cv_tailor`**

In `app/routes/cv.py`, `_task_cv_tailor`, after the `if mode == "plan":` block returns and before `# mode == "generate"`:

```python
    if mode == "recheck":
        if row is None or not row["tailored_cv"]:
            return {"job_id": job_id}
        if not settings["base_guardrails"].strip():
            return {"job_id": job_id}
        yield "Checking against your guardrails… (LLM call: check_guardrails)"
        t0 = time.monotonic()
        findings = check_guardrails(client, model, settings["base_guardrails"],
                                    settings["base_cv"], row["tailored_cv"])["findings"]
        elapsed = time.monotonic() - t0
        logger.info("cv_tailor: recheck check_guardrails for job %s took %.1fs", job_id, elapsed)
        if findings:
            q.upsert_job_cv(conn, job_id, guardrail_findings=findings)
        else:
            logger.warning("cv_tailor: recheck guardrail check returned nothing for job %s — keeping prior findings", job_id)
        conn.execute(
            "UPDATE job_cv SET guardrails_checked_at = datetime('now') WHERE job_id = ?",
            (job_id,),
        )
        conn.commit()
        yield f"Checked guardrails — took {elapsed:.1f}s ({len(findings)} finding(s))"
        q.add_job_event(conn, job_id, "cv", "Guardrails re-checked")
        return _tailor_result(conn, job_id, params)
```

- [ ] **Step 6: Add the route**

In `app/routes/cv.py`, after `cv_generate`:

```python
@router.post("/jobs/{job_id}/cv/recheck-guardrails")
def cv_recheck_guardrails(job_id: int, conn: sqlite3.Connection = Depends(get_db)):
    _require_editable(conn, job_id)
    task = q.enqueue_task(conn, kind="cv_tailor",
                          params={"job_id": job_id, "mode": "recheck", "render": "findings"})
    return {"task_id": task["id"], "already_active": task["already_active"]}
```

- [ ] **Step 7: `_findings.html` — Re-check button + repointed copy**

In `app/templates/cv/_findings.html`, change the stale label text and add the button inside the `<h3>`:

```jinja
    <span class="cv-stage-status" data-state="{{ guardrail_status }}">{{ {
      "none": "Not checked yet", "fresh": "Checked", "stale": "Re-check needed",
      "running": "Re-checking…",
    }.get(guardrail_status, "") }}</span>
    {% if job and job_cv and job_cv.tailored_cv and settings and settings.base_guardrails %}
    <button type="button" class="btn btn-subtle"
            data-progress-url="/jobs/{{ job.id }}/cv/recheck-guardrails"
            data-progress-label="Re-checking guardrails" data-progress-oob>Re-check</button>
    {% endif %}
```

(`job` and `settings` are already in `_workbench_ctx`; `_rendered_chunk` passes the full ctx.)

- [ ] **Step 8: Run the tests**

Run: `python -m pytest tests/test_cv_task.py tests/test_routes_cv_actions.py tests/test_routes_cv_workbench.py -v`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add app/routes/cv.py app/templates/cv/_findings.html
git commit -m "cv: standalone guardrail re-check (cv_tailor mode=recheck, #cv-findings render)"
```

---

### Task 6: Shared ink-mde integration (macro + base.html)

**Files:**
- Create: `app/templates/_ink_editor.html`
- Modify: `app/templates/base.html` (CSS in `<style>`; new `<script type="module">` + a plain `<script>` near the other CV scripts ~line 1406)
- Test: `tests/test_ink_editor_macro.py`

**Interfaces:**
- Produces: Jinja macro `ink_editor(name, value, min_height="300px", autosave_url=None)` importable via `{% from "_ink_editor.html" import ink_editor %}`. Global JS `window.initInkEditors(root)`. Each `.ink-mount` element exposes `_ink` (the instance) and `_inkSetReadonly(bool)`.

- [ ] **Step 1: Write the failing macro test**

`tests/test_ink_editor_macro.py`:

```python
from app.template_env import templates


def _render(**kw):
    tmpl = templates.env.from_string(
        '{% from "_ink_editor.html" import ink_editor %}'
        '{{ ink_editor(name, value, min_height, autosave_url) }}'
    )
    return tmpl.render(**kw)


def test_macro_emits_hidden_textarea_with_data_ink():
    html = _render(name="base_cv", value="# Hi", min_height="420px", autosave_url=None)
    assert 'name="base_cv"' in html
    assert "data-ink" in html
    assert 'data-ink-min-height="420px"' in html
    assert "# Hi" in html
    assert "ink-mount" in html
    assert "data-ink-autosave-url" not in html


def test_macro_includes_autosave_url_when_given():
    html = _render(name="markdown", value="x", min_height="300px",
                   autosave_url="/jobs/7/cv/save-tailored")
    assert 'data-ink-autosave-url="/jobs/7/cv/save-tailored"' in html


def test_macro_escapes_value():
    html = _render(name="c", value="<script>alert(1)</script>", min_height="300px", autosave_url=None)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
```

- [ ] **Step 2: Run, expect failure**

Run: `python -m pytest tests/test_ink_editor_macro.py -v`
Expected: FAIL — `TemplateNotFound: _ink_editor.html`.

- [ ] **Step 3: Create the macro**

`app/templates/_ink_editor.html`:

```jinja
{% macro ink_editor(name, value, min_height="300px", autosave_url=None) -%}
<div class="ink-editor-wrap">
  <textarea class="ink-source" name="{{ name }}" data-ink
    data-ink-min-height="{{ min_height }}"
    {%- if autosave_url %} data-ink-autosave-url="{{ autosave_url }}"{% endif %}
    hidden>{{ value }}</textarea>
  <div class="ink-mount" aria-label="Markdown editor"></div>
</div>
{%- endmacro %}
```

- [ ] **Step 4: Run, expect pass**

Run: `python -m pytest tests/test_ink_editor_macro.py -v`
Expected: PASS.

- [ ] **Step 5: Add the ink-mde ES module + initInkEditors to base.html**

In `app/templates/base.html`, immediately before `</body>` (after the existing `<script>` blocks), add:

```html
  <script type="module">
    import { ink } from 'https://esm.sh/ink-mde@0.34.0?bundle';
    window.__ink = ink;
    // Mount now that the wrapper is defined; DOM is already parsed at </body>.
    if (window.initInkEditors) window.initInkEditors(document);
  </script>
```

And a plain `<script>` (before that module, so `initInkEditors` is defined when the module runs) — place it alongside the other CV scripts:

```html
  <script>
  (function () {
    var DEBOUNCE_MS = 1000;

    function mount(textarea) {
      if (textarea.dataset.inkReady) return;
      var wrap = textarea.closest('.ink-editor-wrap') || textarea.parentNode;
      var mountEl = wrap.querySelector('.ink-mount');
      if (!mountEl || !window.__ink) return;
      textarea.dataset.inkReady = '1';
      mountEl.style.minHeight = textarea.dataset.inkMinHeight || '300px';

      var saveTimer = null, status = document.getElementById('cv-editor-status');
      var autosaveUrl = textarea.dataset.inkAutosaveUrl;

      function setStatus(text) { if (status) status.textContent = text; }

      function invalidateStyledPreviews() {
        document.querySelectorAll('.cv-preview-doc[data-variant="tailored"], .cv-preview-doc[data-variant="diff"]')
          .forEach(function (f) {
            var src = f.getAttribute('src');
            if (src) { f.setAttribute('data-src', src); f.removeAttribute('src'); }
          });
      }

      function doSave(doc) {
        if (!autosaveUrl || !window.htmx) return;
        setStatus('Saving…');
        window.htmx.ajax('POST', autosaveUrl, { values: { markdown: doc }, swap: 'none' })
          .then(function () { setStatus('Saved'); invalidateStyledPreviews(); })
          .catch(function () { setStatus('Save failed — will retry'); });
      }

      var instance = window.__ink(mountEl, {
        doc: textarea.value,
        hooks: {
          afterUpdate: function (doc) {
            textarea.value = doc;
            textarea.dispatchEvent(new Event('input', { bubbles: true }));
            if (autosaveUrl) {
              clearTimeout(saveTimer);
              saveTimer = setTimeout(function () { doSave(doc); }, DEBOUNCE_MS);
            }
          },
        },
      });

      mountEl._ink = instance;
      mountEl._inkSetReadonly = function (ro) {
        try { instance.update({ readonly: !!ro }); } catch (e) {}
      };
    }

    window.initInkEditors = function (root) {
      (root || document).querySelectorAll('textarea[data-ink]:not([data-ink-ready])').forEach(mount);
    };

    document.body.addEventListener('htmx:afterSwap', function (evt) {
      window.initInkEditors(evt.detail && evt.detail.target ? evt.detail.target : document);
    });
  })();
  </script>
```

- [ ] **Step 6: Add ink theming + editor CSS to the base.html `<style>`**

Append inside the main `<style>` block:

```css
    .ink-editor-wrap { display: block; }
    .ink-mount { border: 1px solid var(--border); border-radius: 6px; overflow: hidden; }
    .cv-editor-status { font-size: 0.8rem; color: var(--text-muted); margin-left: 0.5rem; }
    .cv-preview-editor { padding: 0; background: var(--surface); }
    .cv-preview-editor .ink-mount { border: none; border-radius: 0; height: 100%; }
    .ink {
      --ink-block-background-color: var(--ground);
      --ink-border-radius: 6px;
      --ink-color: var(--text-primary);
      --ink-font-family: var(--font-sans);
      --ink-code-font-family: ui-monospace, "JetBrains Mono", monospace;
      --ink-syntax-heading-color: var(--accent);
      --ink-syntax-emphasis-color: var(--text-primary);
      --ink-syntax-link-color: var(--accent);
      --ink-syntax-code-color: var(--neutral-strong);
    }
```

- [ ] **Step 7: Full suite + a render smoke test**

Run: `python -m pytest tests/test_ink_editor_macro.py tests/test_routes_cv_settings.py tests/test_routes_profile.py -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add app/templates/_ink_editor.html app/templates/base.html tests/test_ink_editor_macro.py
git commit -m "ui: shared ink-mde markdown editor (macro + initInkEditors + theming)"
```

---

### Task 7: Tailored CV — Edit tab wiring

**Files:**
- Modify: `app/templates/cv/_preview_tabs.html`, `app/templates/cv/_preview_pane.html`
- Test: `tests/test_routes_cv_workbench.py`

**Interfaces:**
- Consumes: `ink_editor` macro (Task 6), `POST /jobs/{id}/cv/save-tailored` (Task 4).
- Produces: a `data-variant="edit"` tab + `.cv-preview-editor` mount in the workbench preview stage.

- [ ] **Step 1: Write the failing tests**

In `tests/test_routes_cv_workbench.py`:

```python
def test_workbench_has_edit_tab_and_mount_when_draft(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft body\n")
    from unittest.mock import patch
    with patch("app.routes.cv.doc_write_available", return_value=True):
        r = client.get(f"/jobs/{jid}/cv")
    assert 'data-variant="edit"' in r.text
    assert 'data-ink-autosave-url="/jobs/%d/cv/save-tailored"' % jid in r.text
    assert "# Draft body" in r.text


def test_workbench_edit_tab_disabled_without_draft(client, conn):
    jid = _job(conn)
    from unittest.mock import patch
    with patch("app.routes.cv.doc_write_available", return_value=True):
        r = client.get(f"/jobs/{jid}/cv")
    # tab present but disabled (mirrors the Tailored/Differences tabs)
    assert re.search(r'data-variant="edit"[^>]*disabled', r.text) or 'data-variant="edit"' not in r.text


def test_workbench_no_docwrite_uses_editor_not_readonly_div(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft body\n")
    from unittest.mock import patch
    with patch("app.routes.cv.doc_write_available", return_value=False):
        r = client.get(f"/jobs/{jid}/cv")
    assert "data-ink" in r.text
```

- [ ] **Step 2: Run, expect failure**

Run: `python -m pytest tests/test_routes_cv_workbench.py -k "edit_tab or docwrite" -v`
Expected: FAIL.

- [ ] **Step 3: Add the Edit tab + mount to `_preview_tabs.html`**

The Edit tab must appear only in the editable workbench, not the read-only
accepted view (which also includes this partial). Add an `editable` flag.

In `app/templates/cv/_preview_tabs.html`, near the top with the other
`{% set %}`s, add:

```jinja
{% set editable = editable or false %}
```

Add a tab button after the "Differences" tab button:

```jinja
    {% if editable %}
    <button type="button" class="cv-preview-tab" role="tab" data-variant="edit"
            aria-selected="false"
            {% if not has_draft %}disabled title="Update to get a draft to edit"{% endif %}>Edit</button>
    {% endif %}
```

In `.cv-preview-stage`, after the `diff` iframe (inside the `{% if has_draft %}` block), add:

```jinja
  {% if editable %}
  <div class="cv-preview-doc cv-preview-editor" data-variant="edit" title="Edit markdown">
    {% from "_ink_editor.html" import ink_editor %}
    {{ ink_editor("markdown", job_cv.tailored_cv,
                  autosave_url="/jobs/" ~ job.id ~ "/cv/save-tailored") }}
  </div>
  {% endif %}
```

In `app/templates/cv/_preview_pane.html`, the `{% with %}` that includes
`_preview_tabs.html` gains `editable=true`:

```jinja
  {% with mid_label = 'Tailored', title_prefix = '', active = '', editable = true %}
    {% include "cv/_preview_tabs.html" %}
  {% endwith %}
```

`_accepted.html` is left as-is (no `editable` → defaults false).

Note: ink lazy-mounts on first activation via the existing `activate()` path only if we call `initInkEditors` there — add to `base.html`'s CV preview `activate(stage, variant)` function, at the end:

```javascript
    if (variant === 'edit') {
      var m = stage.querySelector('.cv-preview-editor');
      if (m && window.initInkEditors) window.initInkEditors(m);
    }
```

- [ ] **Step 4: No-doc-write branch of `_preview_pane.html`**

Replace:

```jinja
  <div class="cv-markdown" style="border:1px solid var(--border);padding:1rem;">{{ (job_cv.tailored_cv if has_draft else settings.base_cv) | markdown }}</div>
```

with:

```jinja
  {% if has_draft %}
    {% from "_ink_editor.html" import ink_editor %}
    {{ ink_editor("markdown", job_cv.tailored_cv,
                  autosave_url="/jobs/" ~ job.id ~ "/cv/save-tailored") }}
  {% else %}
    <div class="cv-markdown" style="border:1px solid var(--border);padding:1rem;">{{ settings.base_cv | markdown }}</div>
  {% endif %}
```

- [ ] **Step 5: Run the tests + workbench regression**

Run: `python -m pytest tests/test_routes_cv_workbench.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/templates/cv/_preview_tabs.html app/templates/cv/_preview_pane.html app/templates/base.html
git commit -m "cv: Edit tab in the workbench preview pane (ink-mde + autosave)"
```

---

### Task 8: Base CV + profile editors

**Files:**
- Modify: `app/templates/cv/index.html`, `app/templates/profile/_editor.html`
- Test: `tests/test_routes_cv_settings.py`, `tests/test_routes_profile.py`

**Interfaces:**
- Consumes: `ink_editor` macro (Task 6). No route changes — both remain plain form POSTs.

- [ ] **Step 1: Write the failing tests**

In `tests/test_routes_cv_settings.py`:

```python
def test_cv_page_base_cv_uses_ink_editor(client, conn):
    r = client.get("/cv")
    assert 'name="base_cv"' in r.text and "data-ink" in r.text


def test_cv_page_base_cv_still_saves(client, conn):
    r = client.post("/cv", data={"base_cv": "# New base\n"})
    assert r.status_code == 200
    from app.db import queries as q
    assert q.get_cv_settings(conn)["base_cv"] == "# New base\n"
```

In `tests/test_routes_profile.py`:

```python
def test_profile_editor_uses_ink(client, conn):
    r = client.get("/profile")
    assert 'name="content"' in r.text and "data-ink" in r.text


def test_profile_still_saves(client, conn):
    r = client.post("/profile", data={"content": "# Skills\n\n- Python\n"})
    assert r.status_code == 200
    from app.db import queries as q
    assert q.get_profile(conn) == "# Skills\n\n- Python\n"
```

- [ ] **Step 2: Run, expect failure**

Run: `python -m pytest tests/test_routes_cv_settings.py -k ink tests/test_routes_profile.py -k "ink or still_saves" -v`
Expected: FAIL on the `data-ink` assertions.

- [ ] **Step 3: Base CV editor**

In `app/templates/cv/index.html`, at the top add `{% from "_ink_editor.html" import ink_editor %}` (after `{% extends %}`/`{% block %}` opening as the file allows — put it just inside `{% block content %}`), and replace the `base_cv` `<textarea>…</textarea>` with:

```jinja
{{ ink_editor("base_cv", settings.base_cv, min_height="420px") }}
```

- [ ] **Step 4: Profile editor — macro + script adaptation**

In `app/templates/profile/_editor.html`:

- Add `{% from "_ink_editor.html" import ink_editor %}` at the top of the file.
- Replace the `<textarea name="content" …>{{ content }}</textarea>` with `{{ ink_editor("content", content, min_height="300px") }}`.
- In the inline `<script>`, change `setLocked` to also drive the ink instance:

```javascript
  function setLocked(locked) {
    textarea.readOnly = locked;
    textarea.style.background = locked ? "#e9ecef" : "";
    suggestBtn.disabled = locked;
    var mountEl = container.querySelector('.ink-mount');
    if (mountEl && mountEl._inkSetReadonly) mountEl._inkSetReadonly(locked);
  }
```

(The `htmx:afterSwap` hook from Task 6 already re-mounts ink after "accept proposals" swaps this partial. `isDirty()` keeps working via the synthetic `input` event the shared module dispatches.)

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/test_routes_cv_settings.py tests/test_routes_profile.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/templates/cv/index.html app/templates/profile/_editor.html
git commit -m "ui: ink-mde editor for base CV and profile"
```

---

### Task 9: Full regression + manual verification

**Files:** none (verification only)

- [ ] **Step 1: Run the whole suite**

Run: `python -m pytest -q`
Expected: PASS (no regressions).

- [ ] **Step 2: Start the dev server against a throwaway DB**

Follow the `run-dev-server` skill: copy `config.toml` from the main checkout into the worktree, `sqlite3` `.backup` the live `job-seek.db` into the worktree (never `cp` the live file), start `python -m uvicorn app.main:app --reload --port <free>` with `run_in_background` + `dangerouslyDisableSandbox`.

- [ ] **Step 3: Exercise the tailored CV editor**

- Open a job with a tailored draft → `/jobs/<id>/cv`, click the **Edit** tab.
- Confirm ink-mde renders (headings styled inline, monospace fences), edit a line.
- Wait ~1s → `#cv-editor-status` shows "Saved"; the Guardrails badge flips to "Re-check needed"; the change digest above the tabs updates.
- Switch to the **Tailored** / **Differences** tabs → they re-fetch and reflect the edit.
- Click **Re-check** → task runs, findings refresh in place, badge returns to "Checked".
- Accept the CV → the Edit tab is gone / read-only; "Start over" brings it back.

- [ ] **Step 4: Exercise base CV + profile**

- `/cv` → base CV shows ink editor, edit + Save persists.
- `/profile` → ink editor, edit + Save persists; "Suggest profile improvements" while dirty still shows the unsaved-changes warning; after suggestions appear the editor is locked; Accept/Cancel re-renders and re-mounts the editor with the applied text.

- [ ] **Step 5: Stop the dev server, then hand it back to the user**

Per the project convention for UI-facing work: leave a fresh dev server running, give the user the URL, and wait for their go-ahead before offering to merge.

- [ ] **Step 6: Commit any doc touch-ups**

```bash
git add -A ':!config.toml' ':!job-seek.db*'
git commit -m "docs: mark editable-tailored-cv plan complete" || true
```

---

## Self-Review

**Spec coverage:**
- Editor library / esm.sh loading → Task 6 (Global Constraints + Step 5). ✓
- Shared `initInkEditors` / macro / write-back / synthetic `input` / `setReadonly` → Task 6. ✓
- `htmx:afterSwap` re-init → Task 6 Step 5. ✓
- `--ink-*` theming → Task 6 Step 6. ✓
- `edited_at` / `guardrails_checked_at` columns + migrations, no backfill → Task 1. ✓
- `set_job_cv_tailored` → Task 2. ✓
- `_guardrail_status` staleness clause + `_persist_draft` stamp → Task 3. ✓
- Edit tab (4th tab, `cv-preview-doc`, lazy mount, default stays `tailored`) → Task 7. ✓
- `save-tailored` route + OOB diff/findings/status + iframe invalidation → Task 4 (route/OOB) + Task 6 (`invalidateStyledPreviews`). ✓
- `#cv-diff-summary` wrapper → Task 4 Step 4. ✓
- no-doc-write branch uses the editor → Task 7 Step 4. ✓
- `recheck-guardrails` route + `mode=recheck` task + empty-result guard + `#cv-findings`-only render → Task 5. ✓
- `_findings.html` Re-check button + repointed copy → Task 5 Step 7. ✓
- Base CV + profile editors + profile lock adaptation + suggestion machinery untouched → Task 8. ✓
- Testing (routes, task, unit, migration, templates, manual JS) → Tasks 1–9. ✓
- Files list → matches the spec's Files section. ✓

**Placeholder scan:** no TBD/TODO; every code step has literal content. ✓

**Type consistency:** `set_job_cv_tailored(conn, job_id, markdown)` consistent Tasks 2/4. `_edited_since_guardrail_check` / `_guardrail_status` consistent Task 3. `render == "findings"` / `_PANE_TEMPLATES["findings"]` / `params["mode"] == "recheck"` consistent Task 5. Macro signature `ink_editor(name, value, min_height, autosave_url)` consistent Tasks 6/7/8. `.ink-mount._ink` / `._inkSetReadonly` consistent Tasks 6/8. ✓

**Out of scope (unchanged):** `generate` still overwrites `tailored_cv`; no ink on directives/guardrails/CSS. ✓
