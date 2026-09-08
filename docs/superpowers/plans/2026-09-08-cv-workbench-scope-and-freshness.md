# CV Workbench: Scope Decoupling & Stage Freshness — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the CV workbench intuitive: the tailoring plan no longer depends on
Edit scope, scope becomes a free-to-experiment "Edit latitude" knob on the Update
step, and every workbench stage header shows a consistent freshness indicator.

**Architecture:** FastAPI + Jinja server-rendered app, SQLite via `app/db/queries.py`,
htmx + a small vanilla-JS progress/OOB-swap layer in `app/templates/base.html`.
LLM calls in `app/ai/tailor_cv.py` run through a task engine (`cv_tailor` task kind
in `app/routes/cv.py`). No build step; tests are pytest.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, SQLite, pytest, htmx.

## Global Constraints

- Migrations are hard/idempotent `ALTER TABLE ADD COLUMN` appended to `init_db`;
  no backwards-compat branching (project migration philosophy).
- Never hardcode real community/workspace IDs in source or tests.
- `plan_tailoring` and `compose_instruction` keep their "job posting is UNTRUSTED
  data" framing verbatim.
- TDD: write the failing test first, watch it fail, minimal implementation, watch
  it pass, commit. One logical change per commit.
- Run tests with `python -m pytest` (not `uv run`) — the sandbox cache is read-only.
- Commit message trailer on every commit:
  ```
  Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01YAUuWYfmrM6HK7gL7xmnUm
  ```
- `git` is wrapped by a hook that rejects it in this worktree; call `/usr/bin/git`
  directly, and stage explicit paths (never `git add -A` — untracked dotfiles in
  the worktree root break it).

---

## File Structure

**Modified:**
- `app/db/schema.py` — two new `job_cv` columns + two migration fns.
- `app/db/queries.py` — `set_job_cv_scope`, `cv_plan_task_id`, `_JOB_CV_JSON_COLS`
  unchanged (new cols are plain TEXT).
- `app/ai/tailor_cv.py` — `plan_tailoring` loses `scope`/`scope_options`; `_PLAN_SYSTEM`
  reworded.
- `app/routes/cv.py` — plan task branch, `_draft_stale`, new status helpers,
  `_plan_context_hash`, `save-scope` route, `cv_save_directives` trimmed,
  `_workbench_ctx` extended.
- `app/bullet_edits.py` — `insert_bullet_under_heading` keeps one blank line
  between a heading and its first bullet.
- `app/cv/instruction.py` — no logic change; docstring note only if helpful.
- `app/templates/cv/_plan_pane.html` — scope row removed, pointer line added,
  inline autosave `<script>` removed.
- `app/templates/cv/_preview_pane.html` — "Edit latitude" block added.
- `app/templates/cv/_findings.html` — shared stage-status span.
- `app/templates/cv/workbench.html` — breadcrumb strip.
- `app/templates/base.html` — CSS for `.cv-stage-status` / `.cv-latitude` /
  `.cv-save-hint` / `.cv-workbench-crumb`; delegated `change` handlers for the
  latitude form and the directives autosave; regen JS updates `data-state`.

**Tests touched:** `tests/test_tailor_cv_plan.py` (rewrite scope-coupled cases),
`tests/test_bullet_edits.py`, `tests/test_cv_instruction.py`,
`tests/test_routes_cv_actions.py`, `tests/test_routes_cv_workbench.py`,
`tests/test_schema.py`, `tests/test_cv_task.py`.

---

## Task 1: DB columns, `set_job_cv_scope`, `cv_plan_task_id`

**Files:**
- Modify: `app/db/schema.py` (job_cv DDL ~line 31-47; migration fns ~line 974-988;
  `init_db` call list ~line 991-1030)
- Modify: `app/db/queries.py` (`set_job_cv_directives` is at ~line 168 — add
  `set_job_cv_scope` next to it; `cv_generate_task_id` at ~line 1379 — add
  `cv_plan_task_id` next to it)
- Test: `tests/test_schema.py`, `tests/test_queries.py`

**Interfaces:**
- Produces:
  - `job_cv.scope_edited_at TEXT` (nullable), `job_cv.plan_context_hash TEXT` (nullable)
  - `queries.set_job_cv_scope(conn: sqlite3.Connection, job_id: int, scope: list[int]) -> None`
    — upserts the row, writes `scope` as JSON, stamps `scope_edited_at` and
    `updated_at` to `datetime('now')`.
  - `queries.cv_plan_task_id(conn: sqlite3.Connection, job_id: int) -> int | None`
    — id of a queued/running `cv_tailor` task with `params.mode == "plan"` for this
    job, else None.

- [ ] **Step 1: Write the failing schema test**

In `tests/test_schema.py`, add:

```python
def test_job_cv_has_scope_edited_at_and_plan_context_hash(conn):
    init_db(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(job_cv)").fetchall()}
    assert {"scope_edited_at", "plan_context_hash"} <= cols


def test_job_cv_new_columns_migration_is_idempotent(conn):
    # Simulate a pre-existing DB without the columns, then migrate twice.
    conn.executescript(
        "CREATE TABLE job_cv (job_id INTEGER PRIMARY KEY, scope TEXT NOT NULL DEFAULT '[]');"
    )
    from app.db.schema import (
        _migrate_job_cv_add_scope_edited_at, _migrate_job_cv_add_plan_context_hash,
    )
    _migrate_job_cv_add_scope_edited_at(conn)
    _migrate_job_cv_add_scope_edited_at(conn)
    _migrate_job_cv_add_plan_context_hash(conn)
    _migrate_job_cv_add_plan_context_hash(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(job_cv)").fetchall()}
    assert {"scope_edited_at", "plan_context_hash"} <= cols
```

- [ ] **Step 2: Run it, expect failure**

Run: `python -m pytest tests/test_schema.py::test_job_cv_has_scope_edited_at_and_plan_context_hash tests/test_schema.py::test_job_cv_new_columns_migration_is_idempotent -v`
Expected: FAIL — columns missing / migration fns not defined.

- [ ] **Step 3: Add the columns and migrations**

In `app/db/schema.py`, in the `CREATE TABLE IF NOT EXISTS job_cv (...)` block, add
after `generated_at TEXT,`:

```sql
    scope_edited_at TEXT,
    plan_context_hash TEXT,
```

Add two migration functions next to `_migrate_job_cv_add_base_cv_snapshot`:

```python
def _migrate_job_cv_add_scope_edited_at(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(job_cv)")}
    if "scope_edited_at" not in cols:
        conn.execute("ALTER TABLE job_cv ADD COLUMN scope_edited_at TEXT")
        conn.commit()


def _migrate_job_cv_add_plan_context_hash(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(job_cv)")}
    if "plan_context_hash" not in cols:
        conn.execute("ALTER TABLE job_cv ADD COLUMN plan_context_hash TEXT")
        conn.commit()
```

In `init_db`, after the existing `_migrate_job_cv_add_base_cv_snapshot(conn)` line
(order doesn't matter, keep it near the other job_cv migrations):

```python
    _migrate_job_cv_add_scope_edited_at(conn)
    _migrate_job_cv_add_plan_context_hash(conn)
```

- [ ] **Step 4: Run schema tests, expect pass**

Run: `python -m pytest tests/test_schema.py -v`
Expected: PASS (all, including the two new tests).

- [ ] **Step 5: Write the failing queries test**

In `tests/test_queries.py`, add (match the file's existing fixture style — it uses
a `conn` fixture with `init_db` already applied; check an existing test for the
exact job-insert boilerplate and copy it):

```python
def test_set_job_cv_scope_persists_and_stamps(conn):
    conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s','http://x','manual')")
    conn.execute("INSERT INTO jobs (source_id, url, title) VALUES (1,'http://x/1','R')")
    conn.commit()
    q.set_job_cv_scope(conn, 1, [2, 3])
    row = q.get_job_cv(conn, 1)
    assert row["scope"] == [2, 3]
    assert row["scope_edited_at"] is not None


def test_cv_plan_task_id_only_matches_plan_mode(conn):
    conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s','http://x','manual')")
    conn.execute("INSERT INTO jobs (source_id, url, title) VALUES (1,'http://x/1','R')")
    conn.commit()
    q.enqueue_task(conn, kind="cv_tailor", params={"job_id": 1, "mode": "generate"})
    assert q.cv_plan_task_id(conn, 1) is None
    t = q.enqueue_task(conn, kind="cv_tailor", params={"job_id": 1, "mode": "plan"})
    assert q.cv_plan_task_id(conn, 1) == t["id"]
```

- [ ] **Step 6: Run it, expect failure**

Run: `python -m pytest tests/test_queries.py::test_set_job_cv_scope_persists_and_stamps tests/test_queries.py::test_cv_plan_task_id_only_matches_plan_mode -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'set_job_cv_scope'`.

- [ ] **Step 7: Implement the query helpers**

In `app/db/queries.py`, directly after `set_job_cv_directives`:

```python
def set_job_cv_scope(conn: sqlite3.Connection, job_id: int, scope: list[int]) -> None:
    conn.execute("INSERT OR IGNORE INTO job_cv (job_id) VALUES (?)", (job_id,))
    conn.execute(
        "UPDATE job_cv SET scope = ?, scope_edited_at = datetime('now'), "
        "updated_at = datetime('now') WHERE job_id = ?",
        (json.dumps(scope), job_id),
    )
    conn.commit()
```

Directly after `cv_generate_task_id`:

```python
def cv_plan_task_id(conn: sqlite3.Connection, job_id: int) -> int | None:
    """The id of a queued/running cv_tailor 'plan' task for this job — lets the
    workbench show the plan stage as 'working' across a reload."""
    for t in conn.execute(
        "SELECT id, params FROM tasks WHERE kind = 'cv_tailor' AND status IN ('queued', 'running') "
        "ORDER BY created_at DESC"
    ).fetchall():
        p = json.loads(t["params"] or "{}")
        if p.get("job_id") == job_id and p.get("mode") == "plan":
            return t["id"]
    return None
```

- [ ] **Step 8: Run queries tests, expect pass**

Run: `python -m pytest tests/test_queries.py -v`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
/usr/bin/git add app/db/schema.py app/db/queries.py tests/test_schema.py tests/test_queries.py
/usr/bin/git commit -m "feat(cv): job_cv.scope_edited_at + plan_context_hash, set_job_cv_scope, cv_plan_task_id"
```

---

## Task 2: `plan_tailoring` no longer takes scope; plan proposes the full opportunity

**Files:**
- Modify: `app/ai/tailor_cv.py` (`_PLAN_SYSTEM` ~line 11-63; `plan_tailoring` ~line 88-148)
- Test: `tests/test_tailor_cv_plan.py` (rewrite)

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `plan_tailoring(client, model, base_cv, job_context, job_notes="", *,
    tuning_directives="", handled=None) -> dict` — **no `scope`, no `scope_options`**.
    Return shape unchanged: `{"directives": list[DirectiveProposal]}`.

- [ ] **Step 1: Update the tests to the new signature (these are the failing tests)**

In `tests/test_tailor_cv_plan.py`:

1. Delete `_SCOPE_OPTIONS` and every `scope_options=...` kwarg and every positional
   scope-list argument (the `[1, 2]` / `[1]` 5th positional arg) from every
   `plan_tailoring(...)` call. New call shape:
   `plan_tailoring(client, "m", "# CV", "job", tuning_directives=..., handled=...)`
   with `job_notes` as the optional 5th positional where a test passes it.
2. Delete `test_plan_prompt_prefixes_scope_name` entirely.
3. Delete `test_plan_system_binds_proposals_to_permitted_edit_types` entirely.
4. Replace it with:

```python
def test_plan_system_proposes_the_full_opportunity_unbound_by_scope():
    system = _PLAN_SYSTEM.lower()
    assert "permitted edit types" not in system
    assert "full opportunity" in system or "widest" in system
    # it must still forbid claims the base CV can't support
    assert "honestly support" in system or "base cv can honestly" in system


def test_plan_call_has_no_permitted_edit_types_block():
    client = _mock_client('{"directives": []}')
    plan_tailoring(client, "m", "# CV", "job", tuning_directives="- x")
    user = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    assert "Permitted edit types" not in user
```

5. `test_plan_stable_blocks_precede_the_variable_ones`: keep, drop the scope arg;
   its assertions about `# Job posting` ordering are still valid.
6. `test_plan_returns_valid_proposals` and the rest: keep, just drop scope args.

Then, in `tests/test_cv_task.py`, fix the two `job_notes` positional-arg
assertions (lines ~186 and ~206) — after the signature change `job_notes` is the
5th positional (`args[4]`), not `args[5]`:

```python
    notes_arg = pt.call_args.args[4]
```

- [ ] **Step 2: Run the affected files, expect failure**

Run: `python -m pytest tests/test_tailor_cv_plan.py tests/test_cv_task.py -v`
Expected: FAIL — `plan_tailoring()` still requires `scope` / `scope_options`;
`_PLAN_SYSTEM` still contains "permitted edit types".

- [ ] **Step 3: Rewrite `_PLAN_SYSTEM` and `plan_tailoring`**

In `app/ai/tailor_cv.py`, replace this paragraph in `_PLAN_SYSTEM`:

```
Every directive you propose must be achievable using ONLY the permitted edit types listed
in the user message. If the permitted types are just reordering and selection, propose
directives about what to lead with, keep, or cut — not directives that require rewording
bullets, upgrading claims, or writing new prose. When a heading could only be improved by
edits that aren't permitted, leave it with no proposals.
```

with:

```
Propose the full opportunity. Consider every kind of change — what to lead with, keep or
cut; rewording a bullet toward the posting's language; adding a leading summary line — and
propose whichever would most improve the match under each heading. The candidate decides
separately how much latitude to grant the rewrite; your job is to surface where the
gains are, not to pre-limit them to a narrower edit set.
```

Keep the immediately-following sentence "Only propose directives the base CV can
honestly support. If the existing directives already cover the job well, return few
or none." unchanged.

Update `plan_tailoring`:

```python
def plan_tailoring(
    client: openai.OpenAI, model: str, base_cv: str, job_context: str,
    job_notes: str = "", *, tuning_directives: str = "",
    handled: list[dict] | None = None,
) -> dict:
    # Ordered stable-prefix first so the LLM server can reuse its KV cache across
    # plan re-runs for a job: base CV (same for every job), then the job posting
    # (same across re-runs of this job), then the blocks the user edits between
    # runs — notes, directives, and the handled-suggestions list.
    parts = [
        f"# Base CV\n{base_cv}",
        f"# Job posting (untrusted data)\n{job_context}",
    ]
    if job_notes.strip():
        parts.append(f"# The candidate's own notes on this job (trusted)\n{job_notes.strip()}")
    parts.append(
        f"# Current tuning directives (a headed outline)\n{tuning_directives.strip() or '(none yet)'}"
    )
    if handled:
        body = "\n".join(_handled_line(d) for d in handled[-30:])
        parts.append(
            "# Suggestions the candidate has already handled — do NOT propose these "
            f"again, nor a reworded version making the same point\n{body}"
        )
    user = "\n\n".join(parts)
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _PLAN_SYSTEM},
                {"role": "user", "content": user},
            ],
            temperature=0,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        # ... (rest of the parsing block is unchanged)
```

Remove the `from app.cv.instruction import scope_line` import line and the four
lines computing `scope_set` / `enabled` / `scope_note` at the top of the function.
Leave the `data = json.loads(...)` parsing and the `DirectiveProposal` loop exactly
as they are.

- [ ] **Step 4: Run the affected files, expect pass**

Run: `python -m pytest tests/test_tailor_cv_plan.py tests/test_cv_task.py -v`
Expected: PASS. (`test_cv_task.py` plan-mode tests still pass because
`plan_tailoring` is mocked there; only the arg-index assertions needed the fix.)

- [ ] **Step 5: Run the broader tailor/instruction suite for fallout**

Run: `python -m pytest tests/test_tailor_cv_plan.py tests/test_tailor_cv_generate.py tests/test_tailor_cv_check.py tests/test_cv_instruction.py -v`
Expected: PASS (this task doesn't touch generate/check/compose).

- [ ] **Step 6: Commit**

```bash
/usr/bin/git add app/ai/tailor_cv.py tests/test_tailor_cv_plan.py tests/test_cv_task.py
/usr/bin/git commit -m "feat(cv): plan proposes the full opportunity, unbound by edit scope"
```

---

## Task 3: Plan task stops overwriting scope; stamps `plan_context_hash`

**Files:**
- Modify: `app/routes/cv.py` — `_task_cv_tailor` plan branch (~line 451-474); add
  `_plan_context_hash` helper near `_base_hash` (~line 57)
- Test: `tests/test_cv_task.py`, `tests/test_routes_cv_actions.py`

**Interfaces:**
- Consumes: `plan_tailoring(...)` new signature (Task 2); `queries.set_job_cv_scope`
  is *not* used here.
- Produces:
  - `_plan_context_hash(job_context: str, job_notes: str) -> str` (module-level in
    `app/routes/cv.py`) — `sha256` hex of `f"{job_context}\x00{job_notes}"`.
  - After a plan run: `job_cv.plan_context_hash` is set; `job_cv.scope` is
    unchanged for an existing row, seeded to the configured default only when the
    row is created by this run.

- [ ] **Step 1: Write / fix the tests**

`tests/test_cv_task.py` already has `_seed(conn)` and
`_run(conn, cfg, job_id, mode)` and patches `app.routes.cv.plan_tailoring`.

Add:

```python
def test_plan_run_does_not_overwrite_existing_scope(conn, cfg):
    jid = _seed(conn)
    q.upsert_job_cv(conn, jid, scope=[2])          # the user's deliberate choice
    with patch("app.routes.cv.plan_tailoring", return_value={"directives": []}):
        _run(conn, cfg, jid, "plan")
    assert q.get_job_cv(conn, jid)["scope"] == [2]


def test_plan_run_seeds_default_scope_on_a_fresh_row(conn, cfg):
    # No job_cv yet — the run seeds scope from cv_scope_options.default_enabled
    # (the seeded defaults are ids 1,2,3 == correct/choose/organize), exactly as
    # a first generate would.
    jid = _seed(conn)
    with patch("app.routes.cv.plan_tailoring", return_value={"directives": []}):
        _run(conn, cfg, jid, "plan")
    assert q.get_job_cv(conn, jid)["scope"] == [1, 2, 3]


def test_plan_run_stamps_plan_context_hash(conn, cfg):
    jid = _seed(conn)
    with patch("app.routes.cv.plan_tailoring", return_value={"directives": []}):
        _run(conn, cfg, jid, "plan")
    assert q.get_job_cv(conn, jid)["plan_context_hash"]
```

- [ ] **Step 2: Run, expect failure**

Run: `python -m pytest tests/test_cv_task.py -k "plan_run" -v`
Expected: FAIL — scope gets overwritten with the seed on an existing row;
`plan_context_hash` is None.

- [ ] **Step 3: Implement**

Add near `_base_hash` in `app/routes/cv.py`:

```python
def _plan_context_hash(job_context: str, job_notes: str) -> str:
    """Identifies the inputs a plan run saw (posting + notes) so the workbench can
    show the plan stage as stale when they change."""
    return hashlib.sha256(f"{job_context}\x00{job_notes}".encode()).hexdigest()
```

Replace the `if mode == "plan":` block body up to `return _tailor_result(...)` with:

```python
    if mode == "plan":
        current_directives = row["tuning_directives"] if row else settings["directives_template"]
        handled = row["handled_suggestions"] if row else []
        notes = _job_notes(conn, job)
        yield "Evaluating your tuning directives against the job… (LLM call: plan_tailoring)"
        t0 = time.monotonic()
        plan = plan_tailoring(client, model, settings["base_cv"], jc, notes,
                              tuning_directives=current_directives, handled=handled)
        elapsed = time.monotonic() - t0
        logger.info("cv_tailor: plan_tailoring for job %s took %.1fs", job_id, elapsed)
        resolved = resolve_directive_proposals(plan["directives"], current_directives, handled=handled)
        yield f"Evaluated directives — took {elapsed:.1f}s ({len(resolved)} suggestion(s))"
        if row is None:
            # Fresh row: seed scope to the configured default (same as generate).
            q.upsert_job_cv(conn, job_id, scope=[o["id"] for o in scope_options if o["default_enabled"]])
            if current_directives.strip():
                q.set_job_cv_directives(conn, job_id, current_directives)
        q.upsert_job_cv(conn, job_id, plan=resolved)
        conn.execute(
            "UPDATE job_cv SET plan_generated_at = datetime('now'), plan_context_hash = ? "
            "WHERE job_id = ?",
            (_plan_context_hash(jc, notes), job_id),
        )
        conn.commit()
        q.add_job_event(conn, job_id, "cv", "CV plan generated")
        return _tailor_result(conn, job_id, params)
```

(Key diffs: no `scope` local; `plan_tailoring` call has no `scope`/`scope_options`;
the per-run `upsert_job_cv` drops `scope=`; the `UPDATE` also writes
`plan_context_hash`.)

- [ ] **Step 4: Run, expect pass**

Run: `python -m pytest tests/test_cv_task.py -v`
Expected: PASS.

- [ ] **Step 5: Regression sweep of the CV route/action tests**

Run: `python -m pytest tests/test_routes_cv_actions.py tests/test_routes_cv_workbench.py tests/test_routes_tasks.py -v`
Expected: PASS except possibly pre-existing scope-coupled assertions in
`test_routes_cv_actions.py` — if `test_save_directives_persists_and_returns_pane`
or an `accept_plan` test fails here it's expected and fixed in Tasks 4 / 8; note
which failed and move on.

- [ ] **Step 6: Commit**

```bash
/usr/bin/git add app/routes/cv.py tests/test_cv_task.py
/usr/bin/git commit -m "feat(cv): plan run leaves scope untouched, records plan_context_hash"
```

---

## Task 4: `_draft_stale` + stage-status helpers + `save-scope` route + trim `cv_save_directives`

**Files:**
- Modify: `app/routes/cv.py` — `_draft_stale` (~line 86-95), `_workbench_ctx`
  (~line 155-168), `cv_save_directives` (~line 546-556); add status helpers +
  `cv_save_scope` route
- Test: `tests/test_routes_cv_actions.py`, `tests/test_routes_cv_workbench.py`

**Interfaces:**
- Consumes: `queries.set_job_cv_scope`, `queries.cv_plan_task_id`,
  `_plan_context_hash`, `_job_context`, `_job_notes`.
- Produces (all module-level in `app/routes/cv.py`, all return one of
  `"none" | "fresh" | "stale" | "running"`):
  - `_plan_status(conn, job: dict, job_cv: dict | None) -> str`
  - `_draft_status(job_cv: dict | None, settings: dict, running: bool) -> str`
  - `_guardrail_status(job_cv: dict | None, settings: dict, running: bool) -> str`
  - `_workbench_ctx` gains keys: `plan_task_id`, `plan_status`, `draft_status`,
    `guardrail_status`.
  - Route `POST /jobs/{job_id}/cv/save-scope` → re-rendered `cv/_preview_pane.html`.

- [ ] **Step 1: Write failing route tests**

In `tests/test_routes_cv_actions.py`:

```python
def test_save_scope_persists_and_marks_draft_stale(client, cv_on, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft", scope=[1])
    conn.execute("UPDATE job_cv SET generated_at = datetime('now', '-1 hour') WHERE job_id = ?", (jid,))
    conn.commit()
    r = client.post(f"/jobs/{jid}/cv/save-scope", data={"scope": ["1", "2"]})
    assert r.status_code == 200
    assert set(q.get_job_cv(conn, jid)["scope"]) == {1, 2}
    assert q.get_job_cv(conn, jid)["scope_edited_at"] is not None
    assert "Out of date" in r.text  # preview pane reports the draft as stale


def test_save_scope_is_read_only_for_finalized_cv(client, cv_on, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft")
    q.finalize_job_cv(conn, jid)
    assert client.post(f"/jobs/{jid}/cv/save-scope", data={"scope": ["1"]}).status_code == 409
```

Replace `test_save_directives_persists_and_returns_pane` with a version that no
longer sends/expects `scope`:

```python
def test_save_directives_persists_and_returns_pane(client, cv_on, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, scope=[1, 2])  # pre-set; must survive a directives save
    r = client.post(f"/jobs/{jid}/cv/save-directives",
                    data={"tuning_directives": "- foreground Kafka"})
    assert r.status_code == 200
    assert "foreground Kafka" in r.text
    row = q.get_job_cv(conn, jid)
    assert row["tuning_directives"] == "- foreground Kafka"
    assert set(row["scope"]) == {1, 2}          # untouched
    assert row["directives_edited_at"] is not None
```

In `tests/test_routes_cv_workbench.py`:

```python
def test_stage_headers_show_status_indicators(client, cv_on, conn):
    jid = _job(conn)
    page = client.get(f"/jobs/{jid}/cv").text
    # three stage-status elements, all "none" on a blank workbench
    assert page.count('class="cv-stage-status"') >= 3
    assert 'data-state="none"' in page


def test_draft_status_is_stale_after_scope_change(client, cv_on, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft", guardrail_findings=[{"rule": "r", "verdict": "ok", "explanation": ""}])
    conn.execute("UPDATE job_cv SET generated_at = datetime('now', '-1 hour') WHERE job_id = ?", (jid,))
    conn.commit()
    q.set_job_cv_scope(conn, jid, [1])
    page = client.get(f"/jobs/{jid}/cv").text
    # both the draft and the guardrail header track staleness
    assert page.count('data-state="stale"') >= 2
```

- [ ] **Step 2: Run, expect failure**

Run: `python -m pytest tests/test_routes_cv_actions.py tests/test_routes_cv_workbench.py -k "scope or stage or draft_status or save_directives" -v`
Expected: FAIL — no `save-scope` route (404/405), no `cv-stage-status` markup.

- [ ] **Step 3: Extend `_draft_stale`**

```python
def _draft_stale(job_cv: dict | None, settings: dict) -> bool:
    """The shown tailored CV no longer reflects its inputs — base CV / style /
    guardrails changed, or the directives or edit latitude were changed since it
    was generated."""
    if not job_cv or not job_cv.get("tailored_cv"):
        return False
    if job_cv.get("base_hash", "") != _base_hash(settings):
        return True
    ge = job_cv.get("generated_at")
    if not ge:
        return False
    de = job_cv.get("directives_edited_at")
    se = job_cv.get("scope_edited_at")
    return bool((de and de > ge) or (se and se > ge))
```

- [ ] **Step 4: Add the status helpers**

```python
def _plan_status(conn: sqlite3.Connection, job: dict, job_cv: dict | None) -> str:
    if q.cv_plan_task_id(conn, job["id"]):
        return "running"
    if not job_cv or not job_cv.get("plan_generated_at"):
        return "none"
    stored = job_cv.get("plan_context_hash")
    if not stored:                      # plan predates hash tracking — don't nag
        return "fresh"
    current = _plan_context_hash(_job_context(job), _job_notes(conn, job))
    return "fresh" if stored == current else "stale"


def _draft_status(job_cv: dict | None, settings: dict, running: bool) -> str:
    if running:
        return "running"
    if not job_cv or not job_cv.get("tailored_cv"):
        return "none"
    return "stale" if _draft_stale(job_cv, settings) else "fresh"


def _guardrail_status(job_cv: dict | None, settings: dict, running: bool) -> str:
    if running:
        return "running"
    if not job_cv or not job_cv.get("tailored_cv") or not job_cv.get("guardrail_findings"):
        return "none"
    return "stale" if _draft_stale(job_cv, settings) else "fresh"
```

- [ ] **Step 5: Wire `_workbench_ctx`**

```python
def _workbench_ctx(conn: sqlite3.Connection, job_id: int) -> dict:
    job = q.get_job(conn, job_id)
    job_cv = q.get_job_cv(conn, job_id)
    settings = q.get_cv_settings(conn)
    updating = q.cv_generate_task_id(conn, job_id)
    return {
        "job": job,
        "job_cv": job_cv,
        "settings": settings,
        "scope_options": q.get_scope_options(conn),
        "updating_task_id": updating,
        "plan_task_id": q.cv_plan_task_id(conn, job_id),
        "stale_draft": _draft_stale(job_cv, settings),
        "plan_status": _plan_status(conn, job, job_cv) if job else "none",
        "draft_status": _draft_status(job_cv, settings, bool(updating)),
        "guardrail_status": _guardrail_status(job_cv, settings, bool(updating)),
        "has_doc_write": doc_write_available(),
        "cv_diff_view": _cv_diff_view(job_cv),
    }
```

- [ ] **Step 6: Trim `cv_save_directives` and add `cv_save_scope`**

```python
@router.post("/jobs/{job_id}/cv/save-directives", response_class=HTMLResponse,
             dependencies=[Depends(require_cv_enabled)])
async def cv_save_directives(job_id: int, request: Request,
                             conn: sqlite3.Connection = Depends(get_db)):
    _require_editable(conn, job_id)
    form = await request.form()
    q.set_job_cv_directives(conn, job_id, form.get("tuning_directives", ""))
    return _plan_pane(request, conn, job_id)


@router.post("/jobs/{job_id}/cv/save-scope", response_class=HTMLResponse,
             dependencies=[Depends(require_cv_enabled)])
async def cv_save_scope(job_id: int, request: Request,
                        conn: sqlite3.Connection = Depends(get_db)):
    _require_editable(conn, job_id)
    form = await request.form()
    valid_ids = {o["id"] for o in q.get_scope_options(conn)}
    scope = [int(s) for s in form.getlist("scope") if s.isdigit() and int(s) in valid_ids]
    q.set_job_cv_scope(conn, job_id, scope)
    return templates.TemplateResponse(request, "cv/_preview_pane.html",
                                      _workbench_ctx(conn, job_id))
```

- [ ] **Step 7: Add stage-status to templates (minimum to pass the tests)**

In `app/templates/cv/_plan_pane.html`, change `<h2>Tailoring plan</h2>` to:

```html
<h2>Tailoring plan
  <span class="cv-stage-status" data-state="{{ plan_status }}">{{ {
    "none": "Not run yet", "fresh": "Up to date",
    "stale": "Job posting changed — re-evaluate", "running": "Working…",
  }[plan_status] }}</span>
</h2>
```

In `app/templates/cv/_preview_pane.html`, change `<h2>Preview</h2>` to:

```html
<h2>Preview
  <span class="cv-stage-status" data-state="{{ draft_status }}">{{ {
    "none": "No draft yet", "fresh": "Up to date",
    "stale": "Out of date", "running": "Working…",
  }[draft_status] }}</span>
</h2>
```

In `app/templates/cv/_findings.html`, change the `<h3 ...>Guardrails ...` opening so
the heading carries the shared indicator (keep the existing `guardrail-bar` span):

```html
  <h3 style="display:flex;align-items:center;gap:.5rem;flex-wrap:wrap;">Guardrails
    <span class="cv-stage-status" data-state="{{ guardrail_status }}">{{ {
      "none": "Not checked yet", "fresh": "Checked", "stale": "Re-check with Update",
      "running": "Re-checking…",
    }[guardrail_status] }}</span>
    {% if findings %}
    <span class="guardrail-bar" ...>...</span>
    {% endif %}
  </h3>
```

Leave the existing `.cv-findings-stale` and `.cv-preview-stale` elements in place —
they drive the aria-live "in progress / out of date" announcements during a regen
and are covered by existing tests. The new badge is an additive header indicator.

- [ ] **Step 8: Run, expect pass**

Run: `python -m pytest tests/test_routes_cv_actions.py tests/test_routes_cv_workbench.py -v`
Expected: PASS. (`test_editing_routes_are_read_only_for_a_finalized_cv` still
passes; optionally add `/cv/save-scope` to its loop.)

- [ ] **Step 9: Commit**

```bash
/usr/bin/git add app/routes/cv.py app/templates/cv/_plan_pane.html app/templates/cv/_preview_pane.html app/templates/cv/_findings.html tests/test_routes_cv_actions.py tests/test_routes_cv_workbench.py
/usr/bin/git commit -m "feat(cv): save-scope route, scope-aware draft staleness, per-stage status"
```

---

## Task 5: Move the scope control into the preview pane as "Edit latitude"

**Files:**
- Modify: `app/templates/cv/_plan_pane.html` (remove `.cv-scope-row` +
  `.cv-scope-help`, lines ~4-20; remove the inline autosave `<script>` ~line 41-57)
- Modify: `app/templates/cv/_preview_pane.html` (add the latitude block above
  `.cv-preview-actions`)
- Modify: `app/templates/base.html` (add two delegated handlers; `.cv-latitude` CSS)
- Test: `tests/test_routes_cv_actions.py`, `tests/test_routes_cv_workbench.py`

**Interfaces:**
- Consumes: `POST /jobs/{id}/cv/save-scope` (Task 4); `POST /jobs/{id}/cv/save-directives`.
- Produces: the Edit-latitude `<form id="cv-latitude-form">` in the preview pane;
  a body-delegated `change` listener that POSTs it and swaps `#cv-preview-pane`
  innerHTML; a body-delegated `input`/`change` listener that debounce-POSTs
  `#cv-directives-form` (replaces the removed inline script — Task 7 adds the
  visible "Saved" state to it).

- [ ] **Step 1: Write failing tests**

```python
# tests/test_routes_cv_actions.py
def test_scope_control_lives_in_the_preview_pane_not_the_plan_pane(client, cv_on, conn):
    jid = _job(conn)
    page = client.get(f"/jobs/{jid}/cv").text
    plan_pane = page[page.index('id="cv-plan-pane"'):page.index('id="cv-preview-pane"')]
    preview_pane = page[page.index('id="cv-preview-pane"'):]
    assert 'name="scope"' not in plan_pane
    assert "Edit scope:" not in plan_pane
    assert 'id="cv-latitude-form"' in preview_pane
    assert 'name="scope"' in preview_pane
    assert "Edit latitude" in preview_pane


def test_plan_pane_points_at_edit_latitude(client, cv_on, conn):
    jid = _job(conn)
    page = client.get(f"/jobs/{jid}/cv").text
    plan_pane = page[page.index('id="cv-plan-pane"'):page.index('id="cv-preview-pane"')]
    assert "Edit latitude" in plan_pane  # the pointer line lives in the plan pane


def test_directives_still_autosave_without_inline_script(client, cv_on, conn):
    jid = _job(conn)
    page = client.get(f"/jobs/{jid}/cv").text
    assert 'id="cv-directives-form"' in page
    # the autosave is now a delegated handler in base.html, not an inline <script>
    plan_pane = page[page.index('id="cv-plan-pane"'):page.index('id="cv-preview-pane"')]
    assert "<script" not in plan_pane
```

Update `test_plan_pane_autosaves_via_background_script` (currently asserts
`"fetch(form.action"` is in the plan-pane HTML) — the check moves to base.html:

```python
def test_directives_autosave_handler_is_in_base(client, cv_on, conn):
    jid = _job(conn)
    page = client.get(f"/jobs/{jid}/cv").text
    assert "cv-directives-form" in page and "save-directives" in page
    assert 'action="/jobs/{}/cv/save-directives"'.format(jid) in page
```

- [ ] **Step 2: Run, expect failure**

Run: `python -m pytest tests/test_routes_cv_actions.py -k "scope_control or latitude or autosave or directives_still" -v`
Expected: FAIL.

- [ ] **Step 3: Edit `_plan_pane.html`**

Delete the `<div class="cv-scope-row" ...>...</div>` and the
`<details class="cv-scope-help">...</details>` blocks. In their place, right after
the `<form id="cv-directives-form" ...>` open tag:

```html
  <p class="muted" style="font-size:.85em;margin:.2rem 0 .6rem;">
    Suggestions below cover the full opportunity. You choose how much to apply with
    <strong>Edit latitude</strong>, next to the preview.
  </p>
```

Delete the entire trailing `<script> (function () { ... autosave ... })() </script>`
block (lines ~41-57). The `<form>` stays; its `action` attribute is what the
delegated handler in Step 5 reads.

- [ ] **Step 4: Edit `_preview_pane.html`**

Immediately before `<div class="cv-preview-actions">`, insert:

```html
<form id="cv-latitude-form" class="cv-latitude" method="post"
      action="/jobs/{{ job.id }}/cv/save-scope">
  <span class="cv-scope-label">Edit latitude</span>
  <span class="muted" style="font-size:.85em;"> — how far the rewrite may go</span>
  <div class="cv-scope-row" role="group" aria-label="Edit latitude">
    {% for opt in scope_options %}
    <label class="cv-scope-chip">
      <input type="checkbox" name="scope" value="{{ opt.id }}"
        {% if job_cv %}{% if opt.id in job_cv.scope %} checked{% endif %}
        {% elif opt.default_enabled %} checked{% endif %}>
      {{ opt.name or opt.description }}
    </label>
    {% endfor %}
  </div>
  <details class="cv-scope-help">
    <summary>Descriptions</summary>
    <dl>
      {% for opt in scope_options %}
      <dt>{{ opt.name or "—" }}</dt><dd>{{ opt.description }}</dd>
      {% endfor %}
    </dl>
  </details>
</form>
```

- [ ] **Step 5: Add the delegated handlers + CSS in `base.html`**

In the CSS block near `.cv-scope-*` (~line 315), add:

```css
    .cv-latitude { margin-bottom: 0.75rem; }
    .cv-latitude .cv-scope-row { margin-top: 0.3rem; }
```

In the `<script>` region that holds the other CV IIFEs (near the
`window.__cvPreviewResync` / stage code, ~line 1430), add a self-contained IIFE:

```javascript
(function () {
  // Edit-latitude chips: POST on change, swap the preview pane in place so its
  // freshness indicator updates. Delegated so it survives pane re-renders.
  document.body.addEventListener("change", function (e) {
    var form = e.target.closest("#cv-latitude-form");
    if (!form) return;
    fetch(form.action, { method: "POST", body: new FormData(form) })
      .then(function (r) { return r.text(); })
      .then(function (html) {
        var pane = document.getElementById("cv-preview-pane");
        if (pane) { pane.innerHTML = html; if (window.htmx) window.htmx.process(pane); }
      })
      .catch(function () {});
  });

  // Directives autosave (moved out of _plan_pane.html so it survives the OOB
  // plan-pane swap). Task 7 adds the visible "Saving/Saved" hint here.
  var t = null;
  function scheduleSave(form) {
    clearTimeout(t);
    t = setTimeout(function () {
      fetch(form.action, { method: "POST", body: new FormData(form) }).catch(function () {});
    }, 600);
  }
  document.body.addEventListener("input", function (e) {
    var form = e.target.closest("#cv-directives-form");
    if (form) scheduleSave(form);
  });
})();
```

- [ ] **Step 6: Run, expect pass**

Run: `python -m pytest tests/test_routes_cv_actions.py tests/test_routes_cv_workbench.py -v`
Expected: PASS.

- [ ] **Step 7: Manual smoke (dev server)**

Use the `run-dev-server` skill. Open a job's `/cv`, confirm: Edit latitude chips
render by the Update button; toggling one leaves the plan pane untouched and flips
the Preview header to "Out of date"; editing directives still persists across a
reload.

- [ ] **Step 8: Commit**

```bash
/usr/bin/git add app/templates/cv/_plan_pane.html app/templates/cv/_preview_pane.html app/templates/base.html tests/test_routes_cv_actions.py tests/test_routes_cv_workbench.py
/usr/bin/git commit -m "feat(cv): scope becomes an Edit-latitude knob on the Update step"
```

---

## Task 6: Consistent stage-status styling, workbench breadcrumb, regen JS

**Files:**
- Modify: `app/templates/base.html` (CSS for `.cv-stage-status` + `.cv-workbench-crumb`;
  regen JS `data-state` updates ~line 1373-1382, 1429-1434)
- Modify: `app/templates/cv/workbench.html` (breadcrumb strip)
- Test: `tests/test_routes_cv_workbench.py`

**Interfaces:**
- Consumes: `plan_status` / `draft_status` / `guardrail_status` ctx keys (Task 4).
- Produces: `.cv-stage-status[data-state]` visual states; a `.cv-workbench-crumb`
  breadcrumb; regen JS that sets the draft + guardrail spans to `running`/back.

The existing `.cv-findings-stale` / `.cv-preview-stale` aria-live elements stay —
they cover the "in progress" announcement and have tests. Don't touch them.

- [ ] **Step 1: Write failing test**

```python
def test_workbench_has_a_stage_breadcrumb(client, cv_on, conn):
    jid = _job(conn)
    page = client.get(f"/jobs/{jid}/cv").text
    assert 'class="cv-workbench-crumb"' in page
    for label in ("Directives", "Plan", "Draft", "Guardrails"):
        assert label in page
```

- [ ] **Step 2: Run, expect failure**

Run: `python -m pytest tests/test_routes_cv_workbench.py -k "breadcrumb" -v`
Expected: FAIL.

- [ ] **Step 3: Breadcrumb in `workbench.html`**

Inside `<div class="cv-workbench">`, as the first child (before `#cv-plan-pane`):

```html
  <p class="cv-workbench-crumb" aria-hidden="true">
    Directives <span>→</span> Plan <span>→</span> Draft <span>→</span> Guardrails
  </p>
```

- [ ] **Step 4: CSS in `base.html`**

Near `.cv-workbench` (~line 302):

```css
    .cv-workbench-crumb { margin: 0 0 -0.5rem; font-size: 0.8rem; letter-spacing: 0.02em;
      color: var(--text-muted); }
    .cv-workbench-crumb span { opacity: 0.5; margin: 0 0.15rem; }
    .cv-stage-status { font-size: 0.72rem; font-weight: 600; padding: 0.1rem 0.5rem;
      border-radius: 999px; border: 1px solid var(--border); white-space: nowrap;
      color: var(--text-secondary); background: var(--surface); }
    .cv-stage-status[data-state="fresh"] { color: var(--success-strong);
      border-color: var(--success); }
    .cv-stage-status[data-state="stale"] { color: var(--text-primary);
      background: var(--warning-tint); border-color: var(--warning); }
    .cv-stage-status[data-state="running"] { color: var(--text-primary);
      background: var(--warning-tint); border-color: var(--warning); }
    .cv-stage-status[data-state="none"] { opacity: 0.7; }
```

(Use whatever `--success` / `--warning` / `--surface` tokens the file already
defines — grep `:root` in `base.html` to confirm names; the CV guardrail CSS
already uses `--success`, `--warning-tint`, `--alert`.)

- [ ] **Step 5: Regen JS updates the spans**

In `base.html`, in `showPreviewProgress(on)` (~line 1373), after the existing
`.cv-preview-progress` / `.cv-preview-stale` / `.cv-findings-stale` handling, add:

```javascript
    document.querySelectorAll('#cv-preview-pane .cv-stage-status').forEach(function (s) {
      if (on) { s.dataset.prevState = s.dataset.state; s.dataset.state = 'running';
                s.textContent = 'Working…'; }
    });
```

and in `window.__cvPreviewResync` (~line 1431) — the panes get fully re-rendered
from the server on a successful finish, so only handle the failed-regen case:

```javascript
  window.__cvPreviewResync = function () {
    showPreviewProgress(false);
    document.querySelectorAll('#cv-preview-pane .cv-stage-status').forEach(function (s) {
      if (s.dataset.state === 'running' && s.dataset.prevState) {
        s.dataset.state = s.dataset.prevState;   // pane wasn't swapped (regen failed)
      }
    });
    document.querySelectorAll('.cv-preview-stage').forEach(syncNotice);
  };
```

- [ ] **Step 6: Run, expect pass**

Run: `python -m pytest tests/test_routes_cv_workbench.py tests/test_routes_cv_actions.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
/usr/bin/git add app/templates/base.html app/templates/cv/workbench.html tests/test_routes_cv_workbench.py
/usr/bin/git commit -m "feat(cv): consistent stage-freshness badges + workbench breadcrumb"
```

---

## Task 7: Visible "Saved" indicator on the directives editor

**Files:**
- Modify: `app/templates/cv/_plan_pane.html` (wrap the textarea, add the hint span)
- Modify: `app/templates/base.html` (directives autosave handler → set hint states; CSS)
- Test: `tests/test_routes_cv_actions.py`

**Interfaces:**
- Consumes: the delegated directives-autosave handler from Task 5.
- Produces: `<span class="cv-save-hint">` inside a `.cv-editor-wrap`; the handler
  toggles its text through "Saving…" → "Saved" → fades.

- [ ] **Step 1: Write the failing test**

```python
def test_directives_editor_has_a_saved_hint(client, cv_on, conn):
    jid = _job(conn)
    page = client.get(f"/jobs/{jid}/cv").text
    assert 'class="cv-save-hint"' in page
    assert 'class="cv-editor-wrap"' in page
```

- [ ] **Step 2: Run, expect failure**

Run: `python -m pytest tests/test_routes_cv_actions.py::test_directives_editor_has_a_saved_hint -v`
Expected: FAIL.

- [ ] **Step 3: Markup in `_plan_pane.html`**

Wrap the `<textarea name="tuning_directives" ...>` in:

```html
  <label style="display:block;margin-top:.75rem;">Tuning directives<br>
    <span class="cv-editor-wrap">
      <textarea name="tuning_directives" rows="10"
        style="width:100%;font-family:monospace;box-sizing:border-box;">{{ job_cv.tuning_directives if job_cv else settings.directives_template }}</textarea>
      <span class="cv-save-hint" aria-live="polite" hidden></span>
    </span>
  </label>
```

- [ ] **Step 4: CSS in `base.html`** (near `.cv-scope-*`):

```css
    .cv-editor-wrap { position: relative; display: block; }
    .cv-save-hint { position: absolute; right: 8px; bottom: 8px; font-size: 0.72rem;
      color: var(--text-muted); background: var(--surface); padding: 0.05rem 0.4rem;
      border-radius: 4px; pointer-events: none; transition: opacity 0.4s ease; }
    .cv-save-hint[hidden] { display: none; }
    .cv-save-hint.is-fading { opacity: 0; }
```

- [ ] **Step 5: Extend the directives handler in `base.html`**

Replace the Task-5 directives autosave block with:

```javascript
  var t = null;
  function setHint(form, text, fading) {
    var wrap = form.querySelector(".cv-editor-wrap");
    var hint = wrap && wrap.querySelector(".cv-save-hint");
    if (!hint) return;
    hint.hidden = false;
    hint.textContent = text;
    hint.classList.toggle("is-fading", !!fading);
  }
  function scheduleSave(form) {
    clearTimeout(t);
    setHint(form, "Saving…", false);
    t = setTimeout(function () {
      fetch(form.action, { method: "POST", body: new FormData(form) })
        .then(function (r) {
          if (!r.ok) throw new Error();
          setHint(form, "Saved", false);
          setTimeout(function () { setHint(form, "Saved", true); }, 1500);
        })
        .catch(function () { setHint(form, "Not saved — retrying", false); scheduleSave(form); });
    }, 600);
  }
  document.body.addEventListener("input", function (e) {
    var form = e.target.closest("#cv-directives-form");
    if (form) scheduleSave(form);
  });
```

- [ ] **Step 6: Run, expect pass**

Run: `python -m pytest tests/test_routes_cv_actions.py -v`
Expected: PASS.

- [ ] **Step 7: Manual smoke** — type in the directives box on the dev server;
  confirm "Saving…" then "Saved" appears bottom-right and fades.

- [ ] **Step 8: Commit**

```bash
/usr/bin/git add app/templates/cv/_plan_pane.html app/templates/base.html tests/test_routes_cv_actions.py
/usr/bin/git commit -m "feat(cv): visible Saved indicator on the tuning-directives editor"
```

---

## Task 8: Blank line between a directives heading and its first bullet

**Files:**
- Modify: `app/bullet_edits.py` (`insert_bullet_under_heading`, ~line 47-69)
- Test: `tests/test_bullet_edits.py`, `tests/test_cv_instruction.py`,
  `tests/test_routes_cv_actions.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `insert_bullet_under_heading` guarantees exactly one blank line between
  a heading and the first bullet it gains; later bullets append tight to the list;
  `profile_apply.py` is unaffected (separate code path).

- [ ] **Step 1: Write / update the tests (these fail first)**

In `tests/test_bullet_edits.py`:

```python
def test_insert_first_bullet_keeps_blank_line_after_heading():
    lines = "## A\n## B\n- two".split("\n")
    insert_bullet_under_heading(lines, "A", "first")
    assert "\n".join(lines) == "## A\n\n- first\n## B\n- two"


def test_insert_first_bullet_does_not_double_the_blank_line():
    lines = "## A\n\n## B".split("\n")
    insert_bullet_under_heading(lines, "A", "first")
    assert "\n".join(lines) == "## A\n\n- first\n\n## B"


def test_insert_second_bullet_appends_tight_to_the_list():
    lines = "## A\n\n- one\n\n## B".split("\n")
    insert_bullet_under_heading(lines, "A", "two")
    assert "\n".join(lines) == "## A\n\n- one\n- two\n\n## B"
```

Update the two existing cases:

```python
def test_insert_creates_missing_heading_at_end():
    lines = "## A\n- one".split("\n")
    insert_bullet_under_heading(lines, "C", "new")
    assert "\n".join(lines) == "## A\n- one\n\n## C\n\n- new"


def test_insert_into_empty_document():
    lines = [""]
    insert_bullet_under_heading(lines, "A", "new")
    assert "\n".join(lines).strip() == "## A\n\n- new"
```

In `tests/test_cv_instruction.py`, update:

```python
def test_apply_add_inserts_under_named_section():
    ...
    assert out.splitlines() == [
        "## Role relevance", "- lead with platform", "", "## Skills match",
        "", "- name Kubernetes explicitly"]


def test_apply_add_with_unknown_section_appends_heading():
    ...
    assert out.endswith("## New area\n\n- do the thing")


def test_apply_add_to_empty_directives():
    ...
    assert result.strip() == "## Skills match\n\n- foreground platform work"
```

(`test_apply_multiple_proposals_in_one_pass` and the replace/remove tests stay as
they are — they append to an existing list or don't add.)

In `tests/test_routes_cv_actions.py`, update:

```python
def test_accept_plan_proposals_applies_checked_add(client, cv_on, conn):
    ...
    assert row["tuning_directives"].splitlines() == ["## Skills match", "", "- foreground Kafka"]


def test_accept_plan_proposal_inserts_under_its_section(client, cv_on, conn):
    ...
    assert td.splitlines() == ["## Skills match", "", "- name Kubernetes",
                               "## Wording and typography"]
```

- [ ] **Step 2: Run, expect failure**

Run: `python -m pytest tests/test_bullet_edits.py tests/test_cv_instruction.py -k "insert or apply_add" -v`
Expected: FAIL — no blank line inserted.

- [ ] **Step 3: Implement**

In `app/bullet_edits.py`, `insert_bullet_under_heading`:

```python
def insert_bullet_under_heading(lines: list[str], section: str, text: str) -> None:
    """Append `text` as the last bullet of `section` (matched by heading text,
    case-insensitively), mutating `lines` in place. Keeps exactly one blank line
    between the heading and its first bullet. If the heading is absent, create it
    at the end of the document."""
    bullet = format_bullet(text)
    hi = _heading_index(lines, section)
    if hi is None:
        while lines and not lines[-1].strip():
            lines.pop()
        if lines:
            lines.append("")
        lines.append(f"## {section.strip()}")
        lines.append("")
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
    if insert_at == hi + 1:            # this is the section's first bullet
        lines.insert(insert_at, "")
        insert_at += 1
    lines.insert(insert_at, bullet)
```

- [ ] **Step 4: Run, expect pass**

Run: `python -m pytest tests/test_bullet_edits.py tests/test_cv_instruction.py tests/test_routes_cv_actions.py tests/test_profile_apply.py -v`
Expected: PASS (profile tests untouched — different module).

- [ ] **Step 5: Commit**

```bash
/usr/bin/git add app/bullet_edits.py tests/test_bullet_edits.py tests/test_cv_instruction.py tests/test_routes_cv_actions.py
/usr/bin/git commit -m "feat(cv): keep a blank line between a directives heading and its first bullet"
```

---

## Final verification

- [ ] **Step 1: Full suite**

Run: `python -m pytest -q`
Expected: PASS. Investigate any failure before proceeding — do not `-k`-filter past it.

- [ ] **Step 2: Manual walk-through on the dev server** (`run-dev-server` skill)

1. Fresh job → open `/cv`. Plan autostarts; "Tailoring plan" shows "Working…" then
   "Up to date". Preview shows "No draft yet".
2. Edit a directive → "Saving…"/"Saved" bottom-right; "Preview" flips to
   "Out of date".
3. Toggle an Edit-latitude chip → plan pane unchanged, "Preview" stays/flips to
   "Out of date", no scope reset in the chips after the swap.
4. Update → "Preview" and "Guardrails" show "Working…", then both "Up to date" /
   "Checked".
5. Re-run Evaluate → Edit-latitude chips unchanged.

- [ ] **Step 3: Hand the dev server URL to the user** for their own review before
  any merge (per CLAUDE.md "Manual testing" — UI-facing change).

## Self-review notes (spec coverage)

| Spec section | Task |
|---|---|
| §1 decouple plan_tailoring from scope | 2 |
| §1 plan task stops reading/writing scope, seeds on create only | 3 |
| §2 Edit-latitude block in preview pane + save-scope route | 4 (route), 5 (UI) |
| §2 plan-pane pointer line | 5 |
| §3 draft staleness incl. scope_edited_at | 4 |
| §3 consistent stage indicators (plan/draft/guardrail) | 4 (helpers+markup), 6 (style) |
| §3 breadcrumb | 6 |
| §3 guardrails nested / stale-with-draft | 4 + 6 |
| §4 saved indicator | 7 |
| §5 blank line after heading | 8 |
| migration: scope_edited_at + plan_context_hash | 1 |
| queries: set_job_cv_scope, cv_plan_task_id | 1 |
