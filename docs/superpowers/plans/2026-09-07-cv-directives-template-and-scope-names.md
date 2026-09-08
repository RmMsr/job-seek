# CV directives template + scope names — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a configurable headed directives template, give editing scopes short names that flow into the prompts, and fix three advanced-form quirks (in-place saves, consistent "Saved.", auto-sizing textareas).

**Architecture:** Two new columns (`cv_scope_options.name`, `cv_settings.directives_template`) with hard-downtime migrations. The tuning-directives text becomes a `## heading` + `- bullet` outline; the evaluation pass walks headings and every proposal carries a `section`. The `strengthen/trim/reframe` category is removed. Advanced-page forms move to htmx in-place swaps.

**Tech Stack:** FastAPI, Jinja2, htmx, SQLite, pytest. Tests run with `python -m pytest` (never `uv run`). Use `/usr/bin/git` for all git.

## Global Constraints

- Personal single-instance app: hard-downtime migrations, no backwards-compat shims (`CLAUDE.md`).
- `python -m pytest` for tests; `/usr/bin/git` for git; never `git add -A` / `git add .` — stage explicit paths.
- Commit after each task. Commit trailers:
  ```
  Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01YAUuWYfmrM6HK7gL7xmnUm
  ```
- Do **not** commit `.superpowers/sdd/progress.md` (pre-existing local edit — leave it).
- UK spelling in the new scope/template default copy.
- Work in the existing worktree `/home/roman/projects/job-seek/.claude/worktrees/per-job-cv` on branch `worktree-per-job-cv`. Verify each commit lands there.

---

## Task 1: `cv_scope_options.name` column + new 5-scope default set

**Files:**
- Modify: `app/db/schema.py` (`_DDL` ~line 21-28; new migration; `init_db` ~line 932)
- Modify: `app/cv/instruction.py` (`DEFAULT_SCOPE_OPTIONS` ~line 29-49)
- Modify: `app/db/queries.py` (`_seed_default_scope_options` ~77, `insert_scope_option` ~94, `update_scope_option` ~110)
- Test: `tests/test_cv_scope_options.py`, `tests/test_schema.py`, `tests/test_cv_instruction.py`

**Interfaces:**
- Produces: `cv_scope_options` rows have `name: str`. `DEFAULT_SCOPE_OPTIONS` is a 5-list of `{name, description, default_enabled, is_baseline}`. `q.insert_scope_option(conn, description, name="", default_enabled=False) -> int`. `q.update_scope_option(conn, id, description, default_enabled, name="")`.

- [ ] **Step 1: Write failing tests**

In `tests/test_cv_instruction.py`, replace `test_default_scope_options_has_four_entries_with_expected_flags`:
```python
def test_default_scope_options_has_five_named_entries():
    from app.cv.instruction import DEFAULT_SCOPE_OPTIONS
    assert [o["name"] for o in DEFAULT_SCOPE_OPTIONS] == [
        "correct", "choose", "organize", "phrase", "introduce"]
    assert sum(o["default_enabled"] for o in DEFAULT_SCOPE_OPTIONS) == 3
    assert sum(o["is_baseline"] for o in DEFAULT_SCOPE_OPTIONS) == 3
    assert [o["is_baseline"] for o in DEFAULT_SCOPE_OPTIONS] == [1, 1, 1, 0, 0]
```
(Use `1/0` or `True/False` consistently with how the constant is written — match the existing style, which is `True`/`False`; adjust the asserts to `== 3` sums and a truthiness list.)

In `tests/test_cv_scope_options.py`, update `test_seed_creates_four_defaults_in_order` → five, and add:
```python
def test_seed_writes_scope_names(conn):
    q.reset_scope_options(conn)
    opts = q.get_scope_options(conn)
    assert [o["name"] for o in opts] == ["correct", "choose", "organize", "phrase", "introduce"]

def test_insert_and_update_round_trip_name(conn):
    q.reset_scope_options(conn)
    nid = q.insert_scope_option(conn, "Custom.", name="custom", default_enabled=True)
    assert q.get_scope_option(conn, nid)["name"] == "custom"
    q.update_scope_option(conn, nid, "Custom.", default_enabled=True, name="renamed")
    assert q.get_scope_option(conn, nid)["name"] == "renamed"
```
Update `test_delete_scope_option_removes_it` (expects 3 → now 4) and
`test_reset_scope_options_wipes_edits_and_restores_defaults` (4 → 5).

In `tests/test_schema.py`:
```python
def test_cv_scope_options_has_name_column(conn):
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(cv_scope_options)")}
    assert "name" in cols

def test_migrate_adds_name_and_reseeds_pristine_scope_options(conn):
    # conn already ran init_db: pristine table → new 5-set with names
    opts = q.get_scope_options(conn)
    assert [o["name"] for o in opts] == ["correct", "choose", "organize", "phrase", "introduce"]
```
Update `test_init_db_seeds_cv_scope_options_once` (4 → 5).

- [ ] **Step 2: Run tests, verify they fail**

Run: `python -m pytest tests/test_cv_instruction.py tests/test_cv_scope_options.py tests/test_schema.py -q`
Expected: failures on the new/updated assertions.

- [ ] **Step 3: Implement**

`app/cv/instruction.py` — replace `DEFAULT_SCOPE_OPTIONS`:
```python
DEFAULT_SCOPE_OPTIONS: list[dict] = [
    {"name": "correct", "default_enabled": True, "is_baseline": True,
     "description": "Fix spelling errors, incorrect grammar, inconsistent naming or typography."},
    {"name": "choose", "default_enabled": True, "is_baseline": True,
     "description": "Include or omit existing bullets and whole sections by relevance to this job."},
    {"name": "organize", "default_enabled": True, "is_baseline": True,
     "description": "Reorder bullets and sections to emphasise key skills and requirements for this job."},
    {"name": "phrase", "default_enabled": False, "is_baseline": False,
     "description": "Reword existing bullets toward the job's terminology, without introducing a "
                     "claim the base CV does not already support or upgrading the scope or seniority of one."},
    {"name": "introduce", "default_enabled": False, "is_baseline": False,
     "description": "Write a leading paragraph or professional summary conveying the personal alignment "
                     "and mutual interests relevant to both applicant and organisation — without directly "
                     "addressing the opportunity; leave that for a cover letter."},
]
```

`app/db/schema.py` `_DDL` — add `name TEXT NOT NULL DEFAULT ''` to `cv_scope_options` after `description`.

`app/db/queries.py`:
```python
def _seed_default_scope_options(conn):
    from app.cv.instruction import DEFAULT_SCOPE_OPTIONS
    for i, opt in enumerate(DEFAULT_SCOPE_OPTIONS):
        conn.execute(
            "INSERT INTO cv_scope_options (name, description, default_enabled, is_baseline, sort_order) "
            "VALUES (?, ?, ?, ?, ?)",
            (opt.get("name", ""), opt["description"], int(opt["default_enabled"]),
             int(opt["is_baseline"]), i),
        )
    conn.commit()

def insert_scope_option(conn, description, name="", default_enabled=False):
    next_sort = conn.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM cv_scope_options").fetchone()[0]
    cur = conn.execute(
        "INSERT INTO cv_scope_options (name, description, default_enabled, sort_order) VALUES (?, ?, ?, ?)",
        (name, description, int(default_enabled), next_sort),
    )
    conn.commit()
    return cur.lastrowid

def update_scope_option(conn, scope_option_id, description, default_enabled, name=""):
    conn.execute(
        "UPDATE cv_scope_options SET name = ?, description = ?, default_enabled = ? WHERE id = ?",
        (name, description, int(default_enabled), scope_option_id),
    )
    conn.commit()
```

`app/db/schema.py` — new migration + register in `init_db` (after `_migrate_seed_and_remap_cv_scope_options`):
```python
def _migrate_cv_scope_options_add_name(conn: sqlite3.Connection) -> None:
    """Add the `name` short-identifier column. If the scope-options table is
    still exactly the old 4-default set (untouched), replace it with the new
    5-set that carries names and remap the integer ids everywhere they're
    referenced. If the user customised it, only add the empty column — they
    run 'Reset to defaults' in the UI to adopt the new set."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(cv_scope_options)")}
    if "name" not in cols:
        conn.execute("ALTER TABLE cv_scope_options ADD COLUMN name TEXT NOT NULL DEFAULT ''")

    from app.cv.instruction import DEFAULT_SCOPE_OPTIONS
    old_defaults = [
        "Include or omit existing bullets and whole sections by relevance to this job.",
        "Reorder bullets and sections, and choose what leads each section, to foreground "
        "the experience this job values most.",
        "Reword existing bullets toward the job's terminology, without introducing a claim "
        "the base CV does not already support or upgrading the scope or seniority of one.",
        "Write a job-specific professional summary synthesised only from facts already "
        "stated in the base CV.",
    ]
    rows = conn.execute(
        "SELECT id, description FROM cv_scope_options ORDER BY sort_order, id"
    ).fetchall()
    if [r[1] for r in rows] != old_defaults:
        conn.commit()
        return  # customised or already migrated — leave it

    # old id order [1,2,3,4] == [select, reorder, rephrase, summary]
    # new id order [1..5]    == [correct, choose, organize, phrase, introduce]
    remap = {rows[0][0]: 2, rows[1][0]: 3, rows[2][0]: 4, rows[3][0]: 5}
    conn.execute("DELETE FROM cv_scope_options")
    from app.db.queries import _seed_default_scope_options
    _seed_default_scope_options(conn)

    srow = conn.execute("SELECT default_scope FROM cv_settings WHERE id = 1").fetchone()
    if srow is not None:
        old = json.loads(srow[0] or "[]")
        new = [remap[i] for i in old if i in remap]
        conn.execute("UPDATE cv_settings SET default_scope = ? WHERE id = 1", (json.dumps(new),))
    for jc in conn.execute("SELECT job_id, scope FROM job_cv").fetchall():
        old = json.loads(jc[1] or "[]")
        new = [remap[i] for i in old if i in remap]
        conn.execute("UPDATE job_cv SET scope = ? WHERE job_id = ?", (json.dumps(new), jc[0]))
    conn.commit()
```
Register: add `_migrate_cv_scope_options_add_name(conn)` to `init_db` right after `_migrate_seed_and_remap_cv_scope_options(conn)`.

> Note: `_migrate_seed_and_remap_cv_scope_options` seeds the *new* 5-set on a
> fresh DB (it calls `_seed_default_scope_options`), so its `key_to_id`
> string-remap dict is now stale but only fires for pre-table string arrays
> that no longer exist in practice — leave it untouched.

- [ ] **Step 4: Run tests, verify pass**

Run: `python -m pytest tests/test_cv_instruction.py tests/test_cv_scope_options.py tests/test_schema.py -q`

- [ ] **Step 5: Commit**

```bash
/usr/bin/git add app/db/schema.py app/db/queries.py app/cv/instruction.py \
  tests/test_cv_instruction.py tests/test_cv_scope_options.py tests/test_schema.py
/usr/bin/git commit -m "feat: scope short names + new 5-scope default set"
```

---

## Task 2: Scope names in the plan + generate prompts

**Files:**
- Modify: `app/ai/tailor_cv.py` (`plan_tailoring` scope_note ~line 68-70)
- Modify: `app/cv/instruction.py` (`compose_instruction` ~line 52-71)
- Test: `tests/test_tailor_cv_plan.py`, `tests/test_cv_instruction.py`

**Interfaces:**
- Consumes: `scope_options` dicts now carry `name` (may be `""`).
- Produces: each enabled scope line reads `- **{name}**: {description}` when `name` is non-empty, else `- {description}`.

- [ ] **Step 1: Write failing tests**

`tests/test_cv_instruction.py` — extend `_SCOPE_OPTIONS` with names and assert:
```python
_SCOPE_OPTIONS = [
    {"id": 1, "name": "choose", "description": "Select bullets by relevance."},
    {"id": 2, "name": "organize", "description": "Reorder to foreground what matters."},
    {"id": 3, "name": "phrase", "description": "Reword toward the job's terms."},
]

def test_compose_prefixes_scope_name():
    out = compose_instruction(base_instruction="", scope=[1, 2], scope_options=_SCOPE_OPTIONS,
                              guardrails="", tuning_directives="")
    assert "- **choose**: Select bullets by relevance." in out
    assert "- **organize**: Reorder to foreground what matters." in out

def test_compose_scope_without_name_has_no_prefix():
    opts = [{"id": 1, "description": "No name here."}]
    out = compose_instruction(base_instruction="", scope=[1], scope_options=opts,
                              guardrails="", tuning_directives="")
    assert "- No name here." in out
    assert "**" not in out.split("Permitted edits")[1].split("Hard limits")[0]
```

`tests/test_tailor_cv_plan.py` — update `_SCOPE_OPTIONS` to include `name` and add:
```python
def test_plan_prompt_prefixes_scope_name():
    client = _mock_client('{"directives": []}')
    plan_tailoring(client, "m", "# CV", "job", [1, 2],
                   scope_options=[{"id": 1, "name": "choose", "description": "Pick bullets."},
                                  {"id": 2, "name": "organize", "description": "Reorder."}])
    user = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    assert "- **choose**: Pick bullets." in user
```

- [ ] **Step 2: Run, verify fail**

Run: `python -m pytest tests/test_cv_instruction.py tests/test_tailor_cv_plan.py -q`

- [ ] **Step 3: Implement**

Shared helper — add to `app/cv/instruction.py`:
```python
def scope_line(opt: dict) -> str:
    name = (opt.get("name") or "").strip()
    return f"- **{name}**: {opt['description']}" if name else f"- {opt['description']}"
```

`compose_instruction` — replace the `enabled_descriptions` list-comp + `parts.extend(f"- {d}" ...)`:
```python
    enabled = [o for o in scope_options if o["id"] in scope_set]
    ...
    parts.extend(scope_line(o) for o in enabled)
```

`app/ai/tailor_cv.py` `plan_tailoring` — replace lines building `scope_note`:
```python
    from app.cv.instruction import scope_line
    enabled = [o for o in scope_options if o["id"] in scope_set]
    scope_note = "\n".join(scope_line(o) for o in enabled) or "- (reorder and cut only)"
```
(Import at top of the function or module — module-level import is fine, no cycle: `instruction.py` already imports from `tailor_cv`, so import `scope_line` **inside** `plan_tailoring` to avoid the cycle.)

- [ ] **Step 4: Run, verify pass**

Run: `python -m pytest tests/test_cv_instruction.py tests/test_tailor_cv_plan.py tests/test_cv_task.py -q`

- [ ] **Step 5: Commit**

```bash
/usr/bin/git add app/ai/tailor_cv.py app/cv/instruction.py tests/test_cv_instruction.py tests/test_tailor_cv_plan.py
/usr/bin/git commit -m "feat: prefix scope lines with their short name in both prompts"
```

---

## Task 3: Scope editor UI — name field, "Saved." confirmation, autosize

**Files:**
- Modify: `app/templates/cv/_scope_options.html`
- Modify: `app/routes/cv.py` (`add_scope_option` ~304, `save_all_scope_options_route` ~327)
- Modify: `app/templates/base.html` (add autosize helper near the existing inline scripts)
- Test: `tests/test_cv_scope_options.py` or `tests/test_routes_cv_actions.py` (whichever holds the scope-route tests — grep `scope-options`), `tests/test_routes_cv_settings.py`

**Interfaces:**
- Consumes: `scope_options` dicts with `name`; `saved` bool in `_scope_options.html` context.
- Produces: `POST /cv/scope-options` and `/cv/scope-options/save-all` accept a `name` field per row; save-all response includes a "Saved." confirmation.

- [ ] **Step 1: Write failing tests**

Find the scope-route tests (`grep -rn "scope-options" tests/`). Add:
```python
def test_save_all_scope_options_persists_name_and_shows_saved(client, conn):
    q.reset_scope_options(conn)
    opts = q.get_scope_options(conn)
    r = client.post("/cv/scope-options/save-all", data={
        "id": [str(o["id"]) for o in opts],
        "name": ["renamed" if i == 0 else o["name"] for i, o in enumerate(opts)],
        "description": [o["description"] for o in opts],
        "default_enabled": [str(opts[0]["id"])],
    })
    assert r.status_code == 200
    assert "Saved." in r.text
    assert q.get_scope_options(conn)[0]["name"] == "renamed"

def test_add_scope_option_persists_name(client, conn):
    q.reset_scope_options(conn)
    r = client.post("/cv/scope-options", data={"name": "extra", "description": "An extra scope."})
    assert r.status_code == 200
    assert q.get_scope_options(conn)[-1]["name"] == "extra"
```
(Use `data={"field": [..]}` for repeated fields — see the httpx-TestClient memory.)

In `tests/test_routes_cv_settings.py` add:
```python
def test_scope_editor_renders_name_input_and_autosize(client):
    r = client.get("/cv/advanced")
    assert 'name="name"' in r.text
    assert 'class="autosize"' in r.text
```

- [ ] **Step 2: Run, verify fail**

- [ ] **Step 3: Implement**

`_scope_options.html`:
- At the very top, add `{% if saved %}<div class="save-confirmation" aria-live="polite">Saved.</div>{% endif %}`.
- In the save-all row `<li>`: add a `name` input before the description textarea:
  ```html
  <input type="text" name="name" value="{{ o.name }}" placeholder="id"
         style="width:6.5rem; box-sizing:border-box; font:inherit;">
  ```
  Change the description `<textarea ... rows="2" ...>` to `rows="1" class="autosize"`.
- In the add-row form: add `<input type="text" name="name" placeholder="id" style="width:6.5rem;box-sizing:border-box;font:inherit;">` before the description textarea; that textarea also `rows="1" class="autosize"`.

`app/routes/cv.py`:
```python
@router.post("/cv/scope-options", ...)
async def add_scope_option(request, conn=Depends(get_db)):
    form = await request.form()
    description = form.get("description", "").strip()
    if description:
        q.insert_scope_option(conn, description, name=form.get("name", "").strip(),
                              default_enabled=bool(form.get("default_enabled")))
    return templates.TemplateResponse(request, "cv/_scope_options.html",
                                      {"scope_options": q.get_scope_options(conn)})

@router.post("/cv/scope-options/save-all", ...)
async def save_all_scope_options_route(request, conn=Depends(get_db)):
    form = await request.form()
    ids = [int(i) for i in form.getlist("id")]
    names = form.getlist("name")
    descriptions = form.getlist("description")
    enabled_ids = {int(i) for i in form.getlist("default_enabled")}
    for opt_id, name, description in zip(ids, names, descriptions):
        description = description.strip()
        if description:
            q.update_scope_option(conn, opt_id, description, default_enabled=opt_id in enabled_ids,
                                  name=name.strip())
    return templates.TemplateResponse(request, "cv/_scope_options.html",
                                      {"scope_options": q.get_scope_options(conn), "saved": True})
```
(`zip` needs `names` and `descriptions` the same length — the add form and save-all row both emit exactly one `name` + one `description` per row, so alignment holds.)

`app/templates/base.html` — add near the bottom of the main inline `<script>` block (or a new one before `</body>`):
```html
<script>
  (function () {
    function autosize(el){ el.style.height='auto'; el.style.height=el.scrollHeight+'px'; }
    window.__autosize = autosize;
    function all(root){ (root.querySelectorAll ? root.querySelectorAll('textarea.autosize') : []).forEach(autosize); }
    document.addEventListener('DOMContentLoaded', function(){ all(document); });
    document.body.addEventListener('input', function(e){
      if (e.target.matches && e.target.matches('textarea.autosize')) autosize(e.target);
    });
    document.body.addEventListener('htmx:load', function(e){ all(e.target); });
  })();
</script>
```

- [ ] **Step 4: Run, verify pass**

Run: `python -m pytest tests/test_routes_cv_settings.py tests/test_routes_cv_actions.py tests/test_cv_scope_options.py -q`

- [ ] **Step 5: Commit**

```bash
/usr/bin/git add app/templates/cv/_scope_options.html app/templates/base.html app/routes/cv.py tests/
/usr/bin/git commit -m "feat: scope editor name field, Saved. confirmation, autosizing textareas"
```

---

## Task 4: `cv_settings.directives_template` column + default constant

**Files:**
- Modify: `app/db/schema.py` (`_DDL` `cv_settings` ~11-19; new migration; `init_db`)
- Modify: `app/cv/instruction.py` (new `DEFAULT_DIRECTIVES_TEMPLATE`)
- Modify: `app/db/queries.py` (`get_cv_settings` ~43, `save_cv_settings` ~56)
- Test: `tests/test_schema.py`, `tests/test_routes_cv_settings.py` (or a `tests/test_queries.py` if that's where cv-settings unit tests live — grep)

**Interfaces:**
- Produces: `q.get_cv_settings(conn)["directives_template"]: str`. `q.save_cv_settings(..., directives_template: str = "")` — optional kwarg. `DEFAULT_DIRECTIVES_TEMPLATE` — the 8 `## headings`.

- [ ] **Step 1: Write failing tests**

`tests/test_schema.py`:
```python
def test_cv_settings_has_directives_template_column(conn):
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(cv_settings)")}
    assert "directives_template" in cols

def test_init_db_seeds_directives_template_default(conn):
    from app.cv.instruction import DEFAULT_DIRECTIVES_TEMPLATE
    q.get_cv_settings(conn)  # ensure singleton row
    row = conn.execute("SELECT directives_template FROM cv_settings WHERE id = 1").fetchone()
    assert row[0] == DEFAULT_DIRECTIVES_TEMPLATE
```
Update `test_cv_settings_table_columns` (~line 1250) expected column set: add `"directives_template"`.

`tests/test_cv_instruction.py`:
```python
def test_default_directives_template_is_headings_only():
    from app.cv.instruction import DEFAULT_DIRECTIVES_TEMPLATE
    lines = [l for l in DEFAULT_DIRECTIVES_TEMPLATE.splitlines() if l.strip()]
    assert len(lines) == 8
    assert all(l.startswith("## ") for l in lines)
    assert "## Role relevance" in lines
    assert "## Wording and typography" in lines
```

Round-trip test (in whichever file holds `save_cv_settings` unit tests):
```python
def test_save_cv_settings_round_trips_directives_template(conn):
    q.save_cv_settings(conn, base_cv="", base_instruction="", base_guardrails="",
                       css="", default_scope=[1], directives_template="## Foo\n## Bar")
    assert q.get_cv_settings(conn)["directives_template"] == "## Foo\n## Bar"
```

- [ ] **Step 2: Run, verify fail**

- [ ] **Step 3: Implement**

`app/cv/instruction.py`:
```python
DEFAULT_DIRECTIVES_TEMPLATE = "\n".join(
    "## " + h for h in [
        "Role relevance",
        "Skills match",
        "Content hierarchy and placement",
        "Requirement coverage and gaps",
        "Achievement evidence",
        "Experience and seniority",
        "Personal traits and transparency",
        "Wording and typography",
    ]
)
```

`app/db/schema.py` `_DDL` — add to `cv_settings`: `directives_template TEXT NOT NULL DEFAULT ''`.

`app/db/queries.py`:
- `get_cv_settings`: the seeding `INSERT` stays `(id, base_guardrails)` — the migration
  handles the template default for the singleton row. `SELECT *` already returns the new column.
- `save_cv_settings` — add `directives_template: str = ""` param, add to the `INSERT` column
  list + `VALUES` + `ON CONFLICT DO UPDATE SET directives_template = excluded.directives_template`.

`app/db/schema.py` — new migration, registered in `init_db` after `_migrate_cv_settings_merge_floor_guardrails`:
```python
def _migrate_cv_settings_add_directives_template(conn: sqlite3.Connection) -> None:
    from app.cv.instruction import DEFAULT_DIRECTIVES_TEMPLATE
    cols = {r[1] for r in conn.execute("PRAGMA table_info(cv_settings)")}
    if "directives_template" not in cols:
        conn.execute("ALTER TABLE cv_settings ADD COLUMN directives_template TEXT NOT NULL DEFAULT ''")
    conn.execute(
        "UPDATE cv_settings SET directives_template = ? WHERE id = 1 AND directives_template = ''",
        (DEFAULT_DIRECTIVES_TEMPLATE,),
    )
    conn.commit()
```

> `get_cv_settings` seeds a brand-new singleton row with `directives_template=''`
> (the DDL default), then `init_db`'s migration is *not* re-run mid-request — so
> for a DB created fresh *after* this release, the seed must set it too. Change
> the seed INSERT to `INSERT INTO cv_settings (id, base_guardrails, directives_template) VALUES (1, ?, ?)`
> passing `DEFAULT_DIRECTIVES_TEMPLATE`. Import it in `queries.py` alongside `DEFAULT_GUARDRAILS`.

- [ ] **Step 4: Run, verify pass**

Run: `python -m pytest tests/test_schema.py tests/test_cv_instruction.py tests/test_routes_cv_settings.py -q`

- [ ] **Step 5: Commit**

```bash
/usr/bin/git add app/db/schema.py app/db/queries.py app/cv/instruction.py tests/
/usr/bin/git commit -m "feat: cv_settings.directives_template column, seeded with 8-heading default"
```

---

## Task 5: Directives-template editor on the advanced page

**Files:**
- Modify: `app/templates/cv/advanced.html`
- Modify: `app/routes/cv.py` — new `save_directives_template` + `reset_directives_template` routes; thread `directives_template=current["directives_template"]` through the other `save_cv_settings` passthrough calls (`cv_save`, `cv_save_style`, `cv_save_guardrails`, `cv_save_css`, `cv_reset_guardrails`, `cv_reset_style`, `cv_reset_css`)
- Test: `tests/test_routes_cv_settings.py`

**Interfaces:**
- Produces: `POST /cv/save-directives-template` (field `directives_template`), `POST /cv/reset-directives-template`. Both re-render `advanced.html` with `saved=True`.

- [ ] **Step 1: Write failing tests**

```python
def test_save_directives_template_persists_and_keeps_other_fields(client, conn):
    q.save_cv_settings(conn, base_cv="KEEP", base_instruction="", base_guardrails="G",
                       css="", default_scope=[1], directives_template="## Old")
    r = client.post("/cv/save-directives-template", data={"directives_template": "## New\n## Two"})
    assert r.status_code == 200
    s = q.get_cv_settings(conn)
    assert s["directives_template"] == "## New\n## Two"
    assert s["base_cv"] == "KEEP" and s["base_guardrails"] == "G"

def test_reset_directives_template_restores_default(client, conn):
    from app.cv.instruction import DEFAULT_DIRECTIVES_TEMPLATE
    q.save_cv_settings(conn, base_cv="", base_instruction="", base_guardrails="",
                       css="", default_scope=[1], directives_template="## Mangled")
    r = client.post("/cv/reset-directives-template", data={})
    assert q.get_cv_settings(conn)["directives_template"] == DEFAULT_DIRECTIVES_TEMPLATE

def test_saving_guardrails_preserves_directives_template(client, conn):
    q.save_cv_settings(conn, base_cv="", base_instruction="", base_guardrails="",
                       css="", default_scope=[1], directives_template="## Keep me")
    client.post("/cv/save-guardrails", data={"base_guardrails": "New rule"})
    assert q.get_cv_settings(conn)["directives_template"] == "## Keep me"

def test_advanced_page_renders_directives_template_textarea(client):
    r = client.get("/cv/advanced")
    assert 'name="directives_template"' in r.text
```

- [ ] **Step 2: Run, verify fail**

- [ ] **Step 3: Implement**

Add the two routes in `app/routes/cv.py` (near the other `save-*` routes):
```python
@router.post("/cv/save-directives-template", response_class=HTMLResponse,
             dependencies=[Depends(require_cv_enabled)])
async def cv_save_directives_template(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    form = await request.form()
    c = q.get_cv_settings(conn)
    q.save_cv_settings(conn, base_cv=c["base_cv"], base_instruction=c["base_instruction"],
                       base_guardrails=c["base_guardrails"], css=c["css"],
                       default_scope=c["default_scope"],
                       directives_template=form.get("directives_template", ""))
    ctx = _advanced_ctx(conn); ctx["saved"] = True
    return templates.TemplateResponse(request, "cv/advanced.html", ctx)

@router.post("/cv/reset-directives-template", response_class=HTMLResponse,
             dependencies=[Depends(require_cv_enabled)])
def cv_reset_directives_template(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    from app.cv.instruction import DEFAULT_DIRECTIVES_TEMPLATE
    c = q.get_cv_settings(conn)
    q.save_cv_settings(conn, base_cv=c["base_cv"], base_instruction=c["base_instruction"],
                       base_guardrails=c["base_guardrails"], css=c["css"],
                       default_scope=c["default_scope"],
                       directives_template=DEFAULT_DIRECTIVES_TEMPLATE)
    ctx = _advanced_ctx(conn); ctx["saved"] = True
    return templates.TemplateResponse(request, "cv/advanced.html", ctx)
```

In every *other* `save_cv_settings(...)` call in `app/routes/cv.py`, add
`directives_template=current["directives_template"]` (or `settings[...]` / `c[...]`
matching the local var name in that route). There are 8 such calls
(`cv_save`, `cv_save_style`, `cv_save_guardrails`, `cv_save_css`, `cv_reset_guardrails`,
`cv_reset_style`, `cv_reset_css`, `add_scope_option` has none — it's `insert_scope_option`).
Grep `save_cv_settings(` in the file to get them all.

`advanced.html` — new section after "Writing style", before "Editing scopes":
```html
<form id="cv-reset-directives-template-form" method="post" action="/cv/reset-directives-template"></form>

<h2 style="margin-top:1.5rem;">Tuning directives template</h2>
<form method="post" action="/cv/save-directives-template">
  <label>Headings a new job's tuning-directives outline starts from (one <code>## Heading</code> per line)<br>
    <textarea name="directives_template" rows="10"
      style="width:100%;font-family:monospace;box-sizing:border-box;">{{ settings.directives_template }}</textarea>
  </label>
  <p style="color:var(--text-muted);font-size:.85em;">
    The evaluation pass walks these headings and proposes directives under each.
    <button type="submit" form="cv-reset-directives-template-form" class="btn btn-subtle" style="margin-left:.5rem;">Reset to defaults</button>
  </p>
  <button type="submit" class="btn btn-primary">Save</button>
</form>
```
(htmx attributes for in-place save come in Task 8, which wraps the whole page.)

- [ ] **Step 4: Run, verify pass**

Run: `python -m pytest tests/test_routes_cv_settings.py -q`

- [ ] **Step 5: Commit**

```bash
/usr/bin/git add app/routes/cv.py app/templates/cv/advanced.html tests/test_routes_cv_settings.py
/usr/bin/git commit -m "feat: editable tuning-directives template on the advanced page"
```

---

## Task 6: Headed directives model — `section` replaces `category`

**Files:**
- Modify: `app/ai/tailor_cv.py` (`_PLAN_SYSTEM` ~8-49, `DirectiveProposal` ~55-62, `plan_tailoring` parsing ~90-107)
- Modify: `app/cv/instruction.py` (`resolve_directive_proposals` ~74, `apply_directive_proposals` ~95)
- Modify: `app/bullet_edits.py` (new `insert_bullet_under_heading`)
- Test: `tests/test_tailor_cv_plan.py`, `tests/test_cv_instruction.py`, `tests/test_bullet_edits.py` (grep — create if absent)

**Interfaces:**
- Produces: `DirectiveProposal(action, section, rationale, line, target)` — no `category`. Resolved dicts: `{"action", "section", "rationale", "line", "target"}`. `insert_bullet_under_heading(lines: list[str], section: str, text: str) -> None` mutates `lines` in place.

- [ ] **Step 1: Write failing tests**

`tests/test_bullet_edits.py`:
```python
from app.bullet_edits import insert_bullet_under_heading

def test_insert_appends_as_last_bullet_of_the_section():
    lines = "## A\n- one\n\n## B\n- two".split("\n")
    insert_bullet_under_heading(lines, "A", "one-and-a-half")
    assert "\n".join(lines) == "## A\n- one\n- one-and-a-half\n\n## B\n- two"

def test_insert_creates_missing_heading_at_end():
    lines = "## A\n- one".split("\n")
    insert_bullet_under_heading(lines, "C", "new")
    assert "\n".join(lines) == "## A\n- one\n\n## C\n- new"

def test_insert_into_empty_document():
    lines = [""]
    insert_bullet_under_heading(lines, "A", "new")
    assert "\n".join(lines).strip() == "## A\n- new"
```

`tests/test_cv_instruction.py` — rewrite every `DirectiveProposal(... category=...)` to
`section=...` and every resolved dict `{"category": ...}` to `{"section": ...}`. Add:
```python
def test_apply_add_inserts_under_named_section():
    resolved = [{"action": "add", "section": "Skills match", "rationale": "r",
                 "line": "name Kubernetes explicitly", "target": None}]
    out = apply_directive_proposals("## Role relevance\n- lead with platform\n\n## Skills match", resolved)
    assert out.splitlines() == [
        "## Role relevance", "- lead with platform", "", "## Skills match",
        "- name Kubernetes explicitly"]

def test_apply_add_with_unknown_section_appends_heading():
    resolved = [{"action": "add", "section": "New area", "rationale": "r",
                 "line": "do the thing", "target": None}]
    out = apply_directive_proposals("## Role relevance\n- x", resolved)
    assert out.endswith("## New area\n- do the thing")
```

`tests/test_tailor_cv_plan.py` — replace `category` with `section` in every mock JSON
and assertion; delete `test_plan_drops_bad_category`; add:
```python
def test_plan_requires_section():
    client = _mock_client('{"directives": [{"action": "add", "rationale": "x", "line": "y"}]}')
    out = plan_tailoring(client, "m", "# CV", "job", [1], scope_options=_SCOPE_OPTIONS)
    assert out["directives"] == []

def test_plan_keeps_proposal_with_section():
    client = _mock_client('{"directives": [{"action": "add", "section": "Skills match", '
                          '"rationale": "job leads with k8s", "line": "foreground the platform work"}]}')
    out = plan_tailoring(client, "m", "# CV", "job", [1], scope_options=_SCOPE_OPTIONS)
    assert out["directives"][0].section == "Skills match"

def test_plan_system_prompt_says_walk_the_headings():
    client = _mock_client('{"directives": []}')
    plan_tailoring(client, "m", "# CV", "job", [1], scope_options=_SCOPE_OPTIONS)
    system = client.chat.completions.create.call_args.kwargs["messages"][0]["content"].lower()
    assert "heading" in system and "section" in system
```
Update `test_plan_returns_valid_proposals` and the replace/remove tests to include `"section"`.

- [ ] **Step 2: Run, verify fail**

- [ ] **Step 3: Implement**

`app/bullet_edits.py`:
```python
def _heading_index(lines: list[str], section: str) -> int | None:
    needle = section.strip().casefold()
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("#") and s.lstrip("#").strip().casefold() == needle:
            return i
    return None


def insert_bullet_under_heading(lines: list[str], section: str, text: str) -> None:
    bullet = format_bullet(text)
    hi = _heading_index(lines, section)
    if hi is None:
        while lines and not lines[-1].strip():
            lines.pop()
        if lines:
            lines.append("")
        lines.append(f"## {section.strip()}")
        lines.append(bullet)
        return
    end = len(lines)
    for j in range(hi + 1, len(lines)):
        if lines[j].strip().startswith("#"):
            end = j
            break
    insert_at = end
    while insert_at - 1 > hi and not lines[insert_at - 1].strip():
        insert_at -= 1
    lines.insert(insert_at, bullet)
```

`app/ai/tailor_cv.py`:
- `DirectiveProposal`: replace `category: str` with `section: str`.
- Remove `_VALID_CATEGORIES`. Keep `_VALID_ACTIONS`.
- `_PLAN_SYSTEM` — rewrite. Keep the untrusted-job-posting framing and the
  add/replace/remove definitions. New core: the candidate's directives are a
  **headed outline** (`## Heading` lines with `- bullet` directives). Walk each
  heading; under each, propose `add`s the job calls for and the base CV supports,
  and `replace`/`remove` for existing bullets that are vague, overreaching, or
  stale. **Every proposal must name its `section`** — the exact heading text
  (without `##`) it belongs under; for an `add` under a heading that should exist
  but doesn't, use the natural heading name. Drop all `category` text. New JSON:
  ```
  {"directives": [{"action": "add|replace|remove", "section": "<heading text>",
  "rationale": "<one line>", "line": "<directive; omit for remove>",
  "target": "<exact existing directive text; omit for add>"}]}
  ```
- `plan_tailoring` parsing loop: drop the `cat` checks; require
  `section = (item.get("section") or "").strip()` non-empty else `continue`;
  build `DirectiveProposal(action=action, section=section, rationale=..., line=line, target=target)`.
- The `# Current tuning directives` prompt block: keep, but label it
  `# Current tuning directives (a headed outline)`.

`app/cv/instruction.py`:
- `resolve_directive_proposals`: swap `"category": p.category` → `"section": p.section` in both resolved dicts.
- `apply_directive_proposals`: for `r["action"] == "add"`, call
  `insert_bullet_under_heading(lines, r["section"], r["line"])` instead of `lines.append(format_bullet(...))`.
  Import it from `app.bullet_edits`.
  Keep the trailing-newline preservation.

- [ ] **Step 4: Run, verify pass**

Run: `python -m pytest tests/test_tailor_cv_plan.py tests/test_cv_instruction.py tests/test_bullet_edits.py -q`

- [ ] **Step 5: Commit**

```bash
/usr/bin/git add app/ai/tailor_cv.py app/cv/instruction.py app/bullet_edits.py tests/
/usr/bin/git commit -m "feat: headed directives model — section replaces strengthen/trim/reframe"
```

---

## Task 7: Plan-task seeding + section-grouped review UI + per-job reset

**Files:**
- Modify: `app/routes/cv.py` (`_task_cv_tailor` plan mode ~362-420; `cv_accept_plan_proposals` ~509-536; new `cv_reset_directives` route)
- Modify: `app/templates/cv/_plan_pane.html`
- Test: `tests/test_cv_task.py`, `tests/test_routes_cv_workbench.py`, `tests/test_routes_cv_actions.py`

**Interfaces:**
- Consumes: `settings["directives_template"]`, `DirectiveProposal.section`, resolved dicts with `"section"`.
- Produces: on first visit `job_cv.tuning_directives` is seeded from the template (when non-empty); proposals always go to `job_cv.plan`. `cv_accept_plan_proposals` reads `section_{i}` form fields. `POST /jobs/{job_id}/cv/reset-directives` replaces the job's directives with `settings["directives_template"]` and re-renders the plan pane.

- [ ] **Step 1: Write failing tests**

`tests/test_cv_task.py` — replace `test_plan_mode_first_visit_creates_row_and_baseline`:
```python
def test_plan_mode_first_visit_seeds_template_and_queues_proposals(conn, cfg):
    jid = _seed(conn)
    q.save_cv_settings(conn, base_cv="# Me\n\n- Kafka work\n", base_instruction="",
                       base_guardrails="", css="", default_scope=[1, 2],
                       directives_template="## Skills match\n## Wording and typography")
    with patch("app.routes.cv.plan_tailoring", return_value={"directives": [
            DirectiveProposal(action="add", section="Skills match", rationale="r",
                              line="foreground Kafka", target=None)]}), \
         patch("app.routes.cv.tailor_cv", return_value={"markdown": "# Me\n\n- Kafka work\n"}), \
         patch("app.routes.cv.check_guardrails", return_value={"findings": []}):
        task = _run(conn, cfg, jid, "plan")
    assert task["status"] == "done"
    row = q.get_job_cv(conn, jid)
    assert row["tuning_directives"].strip() == "## Skills match\n## Wording and typography"
    assert row["plan"][0]["line"] == "foreground Kafka"
    assert row["plan"][0]["section"] == "Skills match"
    assert row["generated_at"] is not None  # baseline still ran

def test_plan_mode_first_visit_empty_template_still_queues_proposals(conn, cfg):
    jid = _seed(conn)  # _seed saves settings without a template → ''
    with patch("app.routes.cv.plan_tailoring", return_value={"directives": [
            DirectiveProposal(action="add", section="General", rationale="r",
                              line="foreground Kafka", target=None)]}), \
         patch("app.routes.cv.tailor_cv", return_value={"markdown": "# Me\n\n- Kafka work\n"}), \
         patch("app.routes.cv.check_guardrails", return_value={"findings": []}):
        task = _run(conn, cfg, jid, "plan")
    row = q.get_job_cv(conn, jid)
    assert row["tuning_directives"] == ""          # nothing seeded, nothing auto-applied
    assert row["plan"][0]["line"] == "foreground Kafka"
```
Update `test_plan_first_visit_returns_both_pane_chunks` and any other plan test using
`DirectiveProposal(category=...)` → `section=...`.

`tests/test_routes_cv_actions.py` (grep for `plan/accept` tests) — update the accept
POST to send `section_0` and assert the applied text lands under that heading. Add:
```python
def test_accept_plan_proposal_inserts_under_its_section(client, conn):
    jid = _make_job(conn)  # match the file's existing helper
    q.upsert_job_cv(conn, jid, scope=[1])
    q.set_job_cv_directives(conn, jid, "## Skills match\n## Wording and typography")
    r = client.post(f"/jobs/{jid}/cv/plan/accept", data={
        "action_0": "add", "section_0": "Skills match", "rationale_0": "r",
        "line_0": "name Kubernetes", "apply_0": "on"})
    assert r.status_code == 200
    td = q.get_job_cv(conn, jid)["tuning_directives"]
    assert td.splitlines() == ["## Skills match", "- name Kubernetes", "## Wording and typography"]

def test_reset_directives_replaces_with_configured_template(client, conn):
    jid = _make_job(conn)
    q.save_cv_settings(conn, base_cv="", base_instruction="", base_guardrails="",
                       css="", default_scope=[1], directives_template="## A\n## B")
    q.upsert_job_cv(conn, jid, scope=[1])
    q.set_job_cv_directives(conn, jid, "## A\n- something the user wrote")
    r = client.post(f"/jobs/{jid}/cv/reset-directives", data={})
    assert r.status_code == 200
    assert q.get_job_cv(conn, jid)["tuning_directives"] == "## A\n## B"
```

- [ ] **Step 2: Run, verify fail**

- [ ] **Step 3: Implement**

`_task_cv_tailor` plan mode — replace the block from `current_directives = ...` through the
`if not current_directives.strip() and resolved:` / `else:` branch:
```python
        scope = row["scope"] if row else [o["id"] for o in scope_options if o["default_enabled"]]
        current_directives = row["tuning_directives"] if row else settings["directives_template"]
        yield "Evaluating your tuning directives against the job… (LLM call: plan_tailoring)"
        t0 = time.monotonic()
        plan = plan_tailoring(client, model, settings["base_cv"], jc, scope, _job_notes(conn, job),
                              scope_options=scope_options, tuning_directives=current_directives)
        elapsed = time.monotonic() - t0
        logger.info("cv_tailor: plan_tailoring for job %s took %.1fs", job_id, elapsed)
        resolved = resolve_directive_proposals(plan["directives"], current_directives)
        yield f"Evaluated directives — took {elapsed:.1f}s ({len(resolved)} suggestion(s))"
        if row is None:
            q.upsert_job_cv(conn, job_id, scope=scope)
            if current_directives.strip():
                q.set_job_cv_directives(conn, job_id, current_directives)
        q.upsert_job_cv(conn, job_id, plan=resolved, scope=scope)
        conn.execute("UPDATE job_cv SET plan_generated_at = datetime('now') WHERE job_id = ?", (job_id,))
        conn.commit()
```
The `cur = q.get_job_cv(...)` / baseline-draft block below stays as-is (it already
guards on `row is None or not cur["tailored_cv"]`).

Remove the now-unused `apply_directive_proposals` import if nothing else in the file
uses it — but `cv_accept_plan_proposals` still does, so keep it.

`cv_accept_plan_proposals` — in the `while f"action_{i}" in form:` loop, add
`"section": form.get(f"section_{i}", "")` to the `row` dict. `apply_directive_proposals`
already consumes `section` (Task 6).

`_plan_pane.html` — the `{% if job_cv and job_cv.plan %}` block:
- Group `job_cv.plan` by `d.section`. Simplest in Jinja: `{% for section, items in job_cv.plan | groupby("section") %}` with a `<p class="muted"><strong>{{ section }}</strong></p>` header per group, then the existing per-proposal row markup inside an inner loop.
- **Important:** `loop.index0` must stay unique across the whole form. Use an outer
  `{% set ns = namespace(i=0) %}` and inside the inner loop use `ns.i` for the field
  suffixes, incrementing `{% set ns.i = ns.i + 1 %}` after each. Add
  `<input type="hidden" name="section_{{ ns.i }}" value="{{ d.section }}">`.
- Delete the `<span class="tag">{{ d.category }}</span>` line.

`_plan_pane.html` `unapplied_directive_proposals` block — delete `<span class="tag">{{ u.category }}</span>`.

`cv_accept_plan_proposals` builds `unapplied` dicts too — they no longer need `category`;
leave the dict as-is minus `category`, or keep a harmless extra key. Grep the template
for `.category` to be sure none remain.

**New route** in `app/routes/cv.py` (near `cv_save_directives`):
```python
@router.post("/jobs/{job_id}/cv/reset-directives", response_class=HTMLResponse,
             dependencies=[Depends(require_cv_enabled)])
def cv_reset_directives(job_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    if q.get_job(conn, job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")
    q.set_job_cv_directives(conn, job_id, q.get_cv_settings(conn)["directives_template"])
    return _plan_pane(request, conn, job_id)
```

`_plan_pane.html` — in the `<div style="margin-top:.5rem;display:flex;gap:.5rem;flex-wrap:wrap;">`
row that holds "Evaluate directives", add a second button:
```html
<button type="button" class="btn btn-subtle"
        hx-post="/jobs/{{ job.id }}/cv/reset-directives"
        hx-target="#cv-plan-pane" hx-swap="innerHTML"
        hx-confirm="Replace the current tuning directives with your template? This discards edits here.">
  Reset to template
</button>
```

`_plan_pane.html` — the muted note under the textarea (currently "One point per line
keeps this easy to scan and edit. Saved automatically as you type.") becomes:
```html
<p class="muted" style="font-size:.85em;">Keep the <code>## Heading</code> / <code>- bullet</code>
  structure — the evaluation pass proposes directives under each heading. Saved automatically as you type.</p>
```

Add to `tests/test_routes_cv_workbench.py`:
```python
def test_directives_note_mentions_heading_and_bullet_structure(client, cv_on, conn):
    jid = _job(conn)  # match the file's helper
    r = client.get(f"/jobs/{jid}/cv")
    assert "## Heading" in r.text and "- bullet" in r.text
```

- [ ] **Step 4: Run, verify pass**

Run: `python -m pytest tests/test_cv_task.py tests/test_routes_cv_workbench.py tests/test_routes_cv_actions.py -q`

- [ ] **Step 5: Commit**

```bash
/usr/bin/git add app/routes/cv.py app/templates/cv/_plan_pane.html tests/
/usr/bin/git commit -m "feat: seed directives template on first visit; section-grouped review; per-job reset"
```

---

## Task 8: Advanced + base-CV forms save in place (htmx)

**Files:**
- Modify: `app/templates/cv/advanced.html`, `app/templates/cv/index.html`
- Test: `tests/test_routes_cv_settings.py`

**Interfaces:**
- No route or Python changes. Forms gain `hx-post` / `hx-target` / `hx-select` / `hx-swap`; the page body is wrapped in a stable id.

- [ ] **Step 1: Write failing tests**

```python
def test_advanced_forms_post_in_place():
    # every save form targets the wrapper and selects it back out
    from pathlib import Path
    html = Path("app/templates/cv/advanced.html").read_text()
    assert 'id="cv-advanced"' in html
    assert html.count('hx-select="#cv-advanced"') >= 4  # style, guardrails, css, directives-template

def test_base_cv_form_posts_in_place():
    from pathlib import Path
    html = Path("app/templates/cv/index.html").read_text()
    assert 'id="cv-page"' in html
    assert 'hx-post="/cv"' in html and 'hx-select="#cv-page"' in html
```
(These are white-box template assertions — acceptable here since the swap behaviour
can't be exercised without a browser. If the file already has a richer route test
that renders `/cv/advanced` and posts, prefer extending that.)

Also add a behaviour check that the save response still carries the confirmation
(already covered for `/cv/save-guardrails` etc. — verify `"Saved."` in `r.text`
in an existing test, add if missing).

- [ ] **Step 2: Run, verify fail**

- [ ] **Step 3: Implement**

`advanced.html`:
- Wrap everything inside `{% block content %}` (from `{% include "setup/_subnav.html" %}`
  onward, or just the `<h1>` onward — keep the subnav outside if it's shared) in
  `<div id="cv-advanced">…</div>`.
- Each real save `<form>` (`save-style`, `save-guardrails`, `save-css`,
  `save-directives-template`): add
  `hx-post="<same action>" hx-target="#cv-advanced" hx-select="#cv-advanced" hx-swap="outerHTML"`.
  Keep `method="post" action="…"` for no-JS.
- Each hidden reset `<form>` (`cv-reset-guardrails-form`, `cv-reset-style-form`,
  `cv-reset-css-form`, `cv-reset-directives-template-form`): same four `hx-*` attrs.
- The scope-options block already swaps its own `#cv-scope-options-list` — leave it,
  it works inside the wrapper.

`index.html`:
- Wrap the body (`<h1>CV</h1>` onward) in `<div id="cv-page">`.
- The save `<form method="post" action="/cv">`: add
  `hx-post="/cv" hx-target="#cv-page" hx-select="#cv-page" hx-swap="outerHTML"`.
- Leave the Preview button (`hx-post="/cv/preview"`) alone.

Verify `htmx.org` is loaded on these pages (it is — `base.html`). `hx-select` extracts
the wrapper from the full `TemplateResponse`, so the `saved` / `error` confirmation
inside the wrapper swaps in and the URL never changes.

- [ ] **Step 4: Run suite**

Run: `python -m pytest tests/test_routes_cv_settings.py tests/test_routes_cv_workbench.py -q`

- [ ] **Step 5: Commit**

```bash
/usr/bin/git add app/templates/cv/advanced.html app/templates/cv/index.html tests/test_routes_cv_settings.py
/usr/bin/git commit -m "feat: advanced + base-CV settings forms save in place without navigating"
```

---

## Task 9: Workbench preview-pane layout — guardrails under the preview, controls below the iframe

**Files:**
- Modify: `app/templates/cv/_preview_pane.html`
- Modify: `app/templates/base.html` only if a `.cv-preview-*` style needs adjusting for the relocated controls (likely a small `.cv-preview-decision` rule)
- Test: `tests/test_routes_cv_workbench.py`

**Interfaces:**
- No route/Python changes. New DOM order inside `#cv-preview-pane`.

New order:
1. `<h2>Preview</h2>`
2. `.cv-preview-actions` — **Update button only** (keep the `data-progress-*` attrs; keep the `{% if not has_doc_write %}(doc-write-cli not installed){% endif %}` hint here)
3. `.cv-preview-bar` — tabs + Fullscreen (unchanged)
4. `.cv-preview-stage` — iframe(s) + `.cv-preview-notice` (unchanged)
5. **new** `.cv-preview-decision` `<div>` — rendered only `{% if has_draft %}`:
   the `{% if job_cv.finalized_at %}` "Accepted … Updating will un-accept." span,
   `{% else %}` the Accept `<form>` (unchanged markup), and the `Download PDF` `<a>`.
6. `<div id="cv-findings">{% include "cv/_findings.html" %}</div>` — `{% if has_draft %}`
7. `<div id="cv-change-report">{% include "cv/_change_report.html" %}</div>` — `{% if has_draft %}`

The markdown-fallback branch (`{% else %}` of `has_doc_write`) keeps its current
content; put items 5-7 after the whole `{% if has_doc_write %}…{% else %}…{% endif %}`
so they show in both branches (as today).

`base.html` — add near the other `.cv-preview-*` rules (~line 590):
```css
.cv-preview-decision { display:flex; align-items:center; gap:0.5rem; flex-wrap:wrap; margin-top:0.6rem; }
```

- [ ] **Step 1: Rewrite the layout test**

Replace `test_preview_pane_layout_header_then_update_accept_download_inline` in
`tests/test_routes_cv_workbench.py`:
```python
def test_preview_pane_layout_guardrails_under_preview_controls_below_iframe(client, cv_on, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft\n\n- x\n", scope=[1],
                    guardrail_findings=[{"rule": "r", "verdict": "ok", "explanation": ""}])
    text = client.get(f"/jobs/{jid}/cv").text
    actions = text[text.index('class="cv-preview-actions"'):text.index('class="cv-preview-bar"')]
    assert ">Update</button>" in actions
    assert "Accept this CV" not in actions and "Download PDF" not in actions
    stage = text.index('class="cv-preview-stage')
    assert stage < text.index("Accept this CV") < text.index('id="cv-findings"')
    assert text.index('id="cv-findings"') < text.index('id="cv-change-report"')
```
(Match `_job` / `cv_on` to the fixtures the file already uses.)

- [ ] **Step 2: Run, verify fail**

Run: `python -m pytest tests/test_routes_cv_workbench.py -q`

- [ ] **Step 3: Implement** the reorder in `_preview_pane.html` + the CSS rule.

- [ ] **Step 4: Run full suite**

Run: `python -m pytest -q`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
/usr/bin/git add app/templates/cv/_preview_pane.html app/templates/base.html tests/test_routes_cv_workbench.py
/usr/bin/git commit -m "feat: preview pane — guardrails under the iframe, accept/download below it"
```

---

## Self-review notes

- **Spec coverage:** A → Tasks 1-3; B → Tasks 4-7; C1 → Task 8; C2 → Task 3 (verified in Task 8); C3 → Task 3; E → Task 9; D → the worktree/commit constraints.
- **Type consistency:** `DirectiveProposal(action, section, rationale, line, target)` — Tasks 6 & 7. `scope_line(opt)` — Task 2. `insert_bullet_under_heading(lines, section, text)` — Task 6, consumed by `apply_directive_proposals`.
- **`save_cv_settings`** gains `directives_template=""` as an *optional* kwarg (Task 4) so the ~8 existing `save_cv_settings` calls in tests keep working; routes pass it explicitly.
- **Manual test after Task 9:** per `CLAUDE.md`, make sure the dev server is running against the throwaway DB (background task `buw8w8zap`, `--reload`) and hand the user `/cv`, `/cv/advanced`, and a job workbench `/jobs/<id>/cv` to try the directives flow and the new preview layout. Wait for their go-ahead before merging.
