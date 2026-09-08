# CV plan-only first run — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On the first CV workbench visit, run only the tailoring plan (one LLM call) — no auto baseline draft, no guardrail check — and show a "re-checking your guardrails" note whenever a draft-producing `tailor_cv` is running.

**Architecture:** Delete the baseline-draft branch from `_task_cv_tailor`'s `plan` mode; the first real draft is produced by the existing `generate` mode when the user hits Update. Drop the now-unread `cv_scope_options.is_baseline` column via a `DROP COLUMN` migration. Add a pre-rendered, initially-hidden guardrail-stale note revealed by the same client-side hook that shows the "update in progress" banner.

**Tech Stack:** Python 3.14, FastAPI, SQLite (raw `sqlite3`), Jinja2 templates, vanilla JS in `base.html`, pytest.

## Global Constraints

- Every change happens in the existing worktree at `/home/roman/projects/job-seek/.claude/worktrees/per-job-cv` — never `cd` out of it.
- Commit after each task (each task green = one commit). End commit messages with:
  ```
  Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_019pKxu33jH8oGbDUe9qWJn9
  ```
- Run tests with `python -m pytest` (not `uv run` — the sandbox cache is read-only).
- Migrations: single-instance app, hard cutover is fine — direct `ALTER TABLE ... DROP COLUMN`, guarded only by a column-existence check.
- Spec: `docs/superpowers/specs/2026-09-07-cv-plan-only-first-run-design.md`.

---

### Task 1: Plan mode stops after the plan

**Files:**
- Modify: `app/routes/cv.py` — `_task_cv_tailor` (`mode == "plan"` branch, ~lines 455–506), `_tailor_result` (~lines 126–142)
- Modify: `app/db/queries.py` — `cv_generate_task_id` (~lines 1380–1397)
- Test: `tests/test_cv_task.py`, `tests/test_queries.py`, `tests/test_routes_cv_workbench.py`

**Interfaces:**
- Produces: `_tailor_result(conn, job_id, params) -> dict` — the `baseline_generated` keyword parameter is removed. Return shape unchanged (`{"job_id": int, "html_chunks"?: list[str]}`).
- Produces: `cv_generate_task_id(conn, job_id) -> int | None` — now matches only `mode == "generate"` cv_tailor tasks.

- [ ] **Step 1: Update the failing tests in `tests/test_cv_task.py`**

Replace `test_plan_mode_first_visit_seeds_template_and_queues_proposals` (lines ~42–60) with:

```python
def test_plan_mode_first_visit_is_plan_only_no_draft(conn, cfg):
    jid = _seed(conn)
    q.save_cv_settings(conn, base_cv="# Me\n\n- Kafka work\n", base_instruction="",
                       base_guardrails="", css="", default_scope=[1, 2],
                       directives_template="## Skills match\n## Wording and typography")
    with patch("app.routes.cv.plan_tailoring", return_value={"directives": [
            DirectiveProposal(action="add", section="Skills match", rationale="r",
                              line="foreground Kafka", target=None)]}), \
         patch("app.routes.cv.tailor_cv") as mock_tailor, \
         patch("app.routes.cv.check_guardrails") as mock_check:
        task = _run(conn, cfg, jid, "plan")
    assert task["status"] == "done"
    row = q.get_job_cv(conn, jid)
    assert row["tuning_directives"].strip() == "## Skills match\n## Wording and typography"
    assert row["plan"][0]["line"] == "foreground Kafka"
    assert row["plan"][0]["section"] == "Skills match"
    assert row["plan_generated_at"] is not None
    assert row["generated_at"] is None       # no auto baseline draft
    assert not row["tailored_cv"]
    mock_tailor.assert_not_called()
    mock_check.assert_not_called()
    assert "cv" in [e["kind"] for e in q.get_job_events(conn, jid)]
```

In `test_plan_mode_first_visit_empty_template_still_queues_proposals` (lines ~63–73) remove the
`patch("app.routes.cv.tailor_cv", ...)` and `patch("app.routes.cv.check_guardrails", ...)`
context managers (leave only the `plan_tailoring` patch). Assertions stay as-is.

Replace `test_plan_first_visit_returns_both_pane_chunks` (lines ~220–232) with:

```python
def test_plan_first_visit_returns_only_plan_chunk(conn, cfg):
    jid = _seed(conn)
    with patch("app.routes.cv.plan_tailoring", return_value={"directives": [
            DirectiveProposal(action="add", section="Skills match", rationale="r",
                               line="foreground Kafka", target=None)]}):
        task = q.enqueue_task(conn, kind="cv_tailor",
                              params={"job_id": jid, "mode": "plan", "render": "plan_pane"})
        execute_task(conn, MagicMock(), "m", cfg, task)
    res = q.get_task(conn, task["id"])["result"]
    assert len(res["html_chunks"]) == 1
    assert 'id="cv-plan-pane"' in res["html_chunks"][0]
```

In `test_plan_mode_logs_timed_steps` (lines ~253–266): remove the `tailor_cv` and
`check_guardrails` patch context managers, and delete the two assertions:
```python
    assert any("tailor_cv" in m and str(jid) in m for m in messages)
    assert any("check_guardrails" in m and str(jid) in m for m in messages)
```
Keep the `plan_tailoring` assertion.

- [ ] **Step 2: Update the failing test in `tests/test_queries.py`**

Replace the body of `test_cv_generate_task_id_matches_tasks_producing_a_draft` (lines ~1352–1372):

```python
def test_cv_generate_task_id_matches_only_generate_tasks(conn):
    conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s','http://s','manual')")
    j7 = conn.execute("INSERT INTO jobs (source_id, url, title) VALUES (1,'http://s/7','A')").lastrowid
    j8 = conn.execute("INSERT INTO jobs (source_id, url, title) VALUES (1,'http://s/8','B')").lastrowid
    conn.commit()
    assert q.cv_generate_task_id(conn, j7) is None
    # a plan task never counts — it produces no draft
    plan = q.enqueue_task(conn, kind="cv_tailor",
                          params={"job_id": j7, "mode": "plan", "render": "plan_pane"})
    assert q.cv_generate_task_id(conn, j7) is None
    q.complete_task(conn, plan["id"], {})
    gen = q.enqueue_task(conn, kind="cv_tailor",
                         params={"job_id": j7, "mode": "generate", "render": "preview_pane"})
    assert q.cv_generate_task_id(conn, j7) == gen["id"]
    assert q.cv_generate_task_id(conn, j8) is None  # other job
    q.complete_task(conn, gen["id"], {})
    assert q.cv_generate_task_id(conn, j7) is None  # finished
```

- [ ] **Step 3: Update the failing test in `tests/test_routes_cv_workbench.py`**

Replace `test_first_pass_plan_task_also_shows_progress_note` (lines ~215–223):

```python
def test_first_pass_plan_task_does_not_show_preview_progress_note(client, cv_on, conn):
    # a plan run produces no draft and doesn't touch guardrails
    jid = _job(conn)
    q.enqueue_task(conn, kind="cv_tailor",
                   params={"job_id": jid, "mode": "plan", "render": "plan_pane"})
    assert q.cv_generate_task_id(conn, jid) is None
    r = client.get(f"/jobs/{jid}/cv")
    assert 'class="cv-preview-progress" aria-live="polite" hidden' in r.text
```

- [ ] **Step 4: Run the tests, verify they fail**

Run: `python -m pytest tests/test_cv_task.py tests/test_queries.py::test_cv_generate_task_id_matches_only_generate_tasks tests/test_routes_cv_workbench.py::test_first_pass_plan_task_does_not_show_preview_progress_note -q`
Expected: FAIL — `generated_at` is still set, `cv_generate_task_id` still matches the plan task, chunk count is 2.

- [ ] **Step 5: Trim the baseline block from `_task_cv_tailor` in `app/routes/cv.py`**

In the `if mode == "plan":` branch, replace everything from `conn.commit()` (right after the
`UPDATE job_cv SET plan_generated_at` execute) through the `return _tailor_result(...)` line
with:

```python
        conn.commit()
        q.add_job_event(conn, job_id, "cv", "CV plan generated")
        return _tailor_result(conn, job_id, params)
```

That deletes: the `cur = q.get_job_cv(...)` line, `baseline_generated = False`, the entire
`if row is None or not cur["tailored_cv"]:` block (the baseline `tailor_cv` + `check_guardrails`
+ `_persist_draft` calls and their `yield`/`logger.info` lines), and the `baseline_generated=`
argument. The `generate` branch below is untouched.

- [ ] **Step 6: Simplify `_tailor_result` in `app/routes/cv.py`**

```python
def _tailor_result(conn: sqlite3.Connection, job_id: int, params: dict) -> dict:
    result: dict = {"job_id": job_id}
    which = params.get("render")
    if which:
        # This can run from inside the finishing cv_tailor 'generate' task — it's
        # still 'running' in the DB, so _workbench_ctx would see it as an in-flight
        # regen. It isn't: force the "update in progress" note off for this render.
        panes = [which] + (["plan_pane"] if which == "preview_pane" else [])
        result["html_chunks"] = [
            _rendered_chunk(conn, job_id, p, updating_task_id=None) for p in panes
        ]
    return result
```

(Removes the `baseline_generated` parameter and its `["preview_pane"] if baseline_generated` branch.)

- [ ] **Step 7: Narrow `cv_generate_task_id` in `app/db/queries.py`**

```python
def cv_generate_task_id(conn: sqlite3.Connection, job_id: int) -> int | None:
    """The id of a queued/running cv_tailor 'generate' task for this job. Lets the
    workbench show the "update in progress" note across a reload and reattach so
    the preview pane still refreshes when the task finishes. Returns None otherwise
    — a 'plan' task produces no draft and doesn't count."""
    for t in conn.execute(
        "SELECT id, params FROM tasks WHERE kind = 'cv_tailor' AND status IN ('queued', 'running') "
        "ORDER BY created_at DESC"
    ).fetchall():
        p = json.loads(t["params"] or "{}")
        if p.get("job_id") == job_id and p.get("mode") == "generate":
            return t["id"]
    return None
```

- [ ] **Step 8: Run the tests, verify they pass**

Run: `python -m pytest tests/test_cv_task.py tests/test_queries.py tests/test_routes_cv_workbench.py -q`
Expected: PASS.

- [ ] **Step 9: Full suite**

Run: `python -m pytest -q`
Expected: PASS. (`tests/test_routes_tasks.py` only checks task-presentation strings, not the
baseline behaviour — no change needed there.)

- [ ] **Step 10: Commit**

```bash
git add app/routes/cv.py app/db/queries.py tests/
git commit -m "feat(cv): first workbench visit runs the plan only, no auto baseline draft"
```

---

### Task 2: Remove the `is_baseline` column

**Files:**
- Modify: `app/db/schema.py` — `_DDL` `cv_scope_options` block (line ~27), new `_migrate_cv_scope_options_drop_is_baseline`, `init_db` migration list (line ~1018)
- Modify: `app/db/queries.py` — `_seed_default_scope_options` (lines ~83–92)
- Modify: `app/cv/instruction.py` — `DEFAULT_SCOPE_OPTIONS` (lines ~54–71)
- Test: `tests/test_schema.py`, `tests/test_cv_scope_options.py`, `tests/test_cv_instruction.py`

**Interfaces:**
- Consumes: nothing from Task 1 (independent, but Task 1 removed the last reader of `is_baseline`).
- Produces: `cv_scope_options` rows no longer have an `is_baseline` key. `DEFAULT_SCOPE_OPTIONS` dicts have keys `name`, `default_enabled`, `description` only.

- [ ] **Step 1: Write the failing migration test in `tests/test_schema.py`**

Add after `test_init_db_drops_job_cv_preview_pages` (~line 1306):

```python
def test_init_db_drops_cv_scope_options_is_baseline(conn):
    conn.executescript(
        """
        CREATE TABLE cv_scope_options (
            id INTEGER PRIMARY KEY,
            description TEXT NOT NULL,
            name TEXT NOT NULL DEFAULT '',
            default_enabled INTEGER NOT NULL DEFAULT 0,
            is_baseline INTEGER NOT NULL DEFAULT 0,
            sort_order INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    conn.execute(
        "INSERT INTO cv_scope_options (description, name, default_enabled, is_baseline, sort_order) "
        "VALUES ('keep me', 'correct', 1, 1, 0)"
    )
    conn.commit()

    init_db(conn)

    cols = {r["name"] for r in conn.execute("PRAGMA table_info(cv_scope_options)")}
    assert "is_baseline" not in cols
    # the pre-existing row survives
    assert conn.execute("SELECT description FROM cv_scope_options WHERE name='correct'").fetchone()[0] == "keep me"
```

- [ ] **Step 2: Update `tests/test_cv_scope_options.py`**

Line ~14: change the hand-written DDL string to drop `is_baseline`:
```python
        "default_enabled INTEGER NOT NULL DEFAULT 0, "
```
(remove the `is_baseline INTEGER NOT NULL DEFAULT 0, ` fragment).

Delete these assertions:
- line ~26: `assert opts[0]["is_baseline"] == 1`
- line ~28: `assert opts[3]["is_baseline"] == 0`
- line ~53: `assert opts[-1]["is_baseline"] == 0`

- [ ] **Step 3: Update `tests/test_cv_instruction.py`**

In `test_default_scope_options_has_five_named_entries` (~line 29) delete:
```python
    assert sum(o["is_baseline"] for o in DEFAULT_SCOPE_OPTIONS) == 3
    assert [bool(o["is_baseline"]) for o in DEFAULT_SCOPE_OPTIONS] == [
        True, True, True, False, False]
```

- [ ] **Step 4: Run the tests, verify they fail**

Run: `python -m pytest tests/test_schema.py::test_init_db_drops_cv_scope_options_is_baseline tests/test_cv_scope_options.py tests/test_cv_instruction.py -q`
Expected: FAIL — migration function doesn't exist (`AttributeError` / column still present); `_seed_default_scope_options` still does `int(opt["is_baseline"])` raising `KeyError` once `DEFAULT_SCOPE_OPTIONS` loses the key.

- [ ] **Step 5: Drop `is_baseline` from the DDL in `app/db/schema.py`**

In `_DDL`, delete this line from the `CREATE TABLE IF NOT EXISTS cv_scope_options` block:
```
    is_baseline INTEGER NOT NULL DEFAULT 0,
```

- [ ] **Step 6: Add the migration in `app/db/schema.py`**

Add next to the other `job_cv` / scope-option migrations (e.g. after `_migrate_job_cv_add_base_cv_snapshot`, ~line 980):

```python
def _migrate_cv_scope_options_drop_is_baseline(conn: sqlite3.Connection) -> None:
    # The auto baseline draft is gone — the first manual Update uses the default
    # scope, so is_baseline has no reader. Direct DROP COLUMN.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(cv_scope_options)")}
    if "is_baseline" not in cols:
        return
    conn.execute("ALTER TABLE cv_scope_options DROP COLUMN is_baseline")
    conn.commit()
```

Register it in `init_db`, on the line after `_migrate_job_cv_add_base_cv_snapshot(conn)`:
```python
    _migrate_cv_scope_options_drop_is_baseline(conn)
```

- [ ] **Step 7: Update `_seed_default_scope_options` in `app/db/queries.py`**

```python
def _seed_default_scope_options(conn: sqlite3.Connection) -> None:
    from app.cv.instruction import DEFAULT_SCOPE_OPTIONS
    for i, opt in enumerate(DEFAULT_SCOPE_OPTIONS):
        conn.execute(
            "INSERT INTO cv_scope_options (name, description, default_enabled, sort_order) "
            "VALUES (?, ?, ?, ?)",
            (opt.get("name", ""), opt["description"], int(opt["default_enabled"]), i),
        )
    conn.commit()
```

- [ ] **Step 8: Update `DEFAULT_SCOPE_OPTIONS` in `app/cv/instruction.py`**

Remove every `"is_baseline": True/False,` key from the five dicts, and rewrite the
comment above the list (lines ~52–56) to:

```python
# Replaces the old SCOPE_LINES/SCOPE_ORDER dict — these are now the *default*
# seed content for the user-editable cv_scope_options table (app/db/schema.py's
# _DDL + queries.py's _seed_default_scope_options), not a fixed enum.
# default_enabled=True means a new job starts with this scope checked.
```

Result, e.g.:
```python
    {"name": "correct", "default_enabled": True,
     "description": "Fix spelling errors, incorrect grammar, inconsistent naming or typography."},
```

- [ ] **Step 9: Run the tests, verify they pass**

Run: `python -m pytest tests/test_schema.py tests/test_cv_scope_options.py tests/test_cv_instruction.py tests/test_cv_task.py -q`
Expected: PASS.

- [ ] **Step 10: Full suite**

Run: `python -m pytest -q`
Expected: PASS.

- [ ] **Step 11: Commit**

```bash
git add app/db/schema.py app/db/queries.py app/cv/instruction.py tests/
git commit -m "refactor(cv): drop the now-unused is_baseline scope-option flag"
```

---

### Task 3: Preview-pane empty state on first run

**Files:**
- Modify: `app/templates/cv/_preview_pane.html`
- Modify: `app/templates/base.html` — add `.cv-preview-empty` style near the `.cv-diff-summary` rules (~line 630)
- Test: `tests/test_routes_cv_workbench.py`

**Interfaces:**
- Consumes: `has_draft` (already computed at the top of `_preview_pane.html` as `job_cv and job_cv.tailored_cv`).
- Produces: nothing consumed by later tasks.

**Context:** `cv/_preview_tabs.html` already handles `has_draft = False` — it disables the
Tailored/Differences tabs (tooltip "Update to see…") and defaults the active tab to Base. So
only an explanatory line is missing.

- [ ] **Step 1: Write the failing test in `tests/test_routes_cv_workbench.py`**

```python
def test_workbench_first_visit_shows_empty_preview_state(client, cv_on, conn):
    jid = _job(conn)
    text = client.get(f"/jobs/{jid}/cv").text
    assert "No tailored CV yet" in text
    assert "Accept this CV" not in text           # gated on has_draft
```

- [ ] **Step 2: Run it, verify it fails**

Run: `python -m pytest tests/test_routes_cv_workbench.py::test_workbench_first_visit_shows_empty_preview_state -q`
Expected: FAIL — "No tailored CV yet" not in the page.

- [ ] **Step 3: Add the empty-state line in `app/templates/cv/_preview_pane.html`**

Immediately before `{% include "cv/_cv_diff_summary.html" %}`:

```html
{% if not has_draft %}
<p class="cv-preview-empty muted">No tailored CV yet — review the plan on the left, then hit
  <strong>Update</strong>. Your base CV is shown below for reference.</p>
{% endif %}
```

- [ ] **Step 4: Add the style in `app/templates/base.html`**

Next to the `.cv-diff-summary` rules (~line 630):

```css
    .cv-preview-empty { margin: 0 0 0.6rem; font-size: 0.9rem; }
```

- [ ] **Step 5: Run the test, verify it passes**

Run: `python -m pytest tests/test_routes_cv_workbench.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/templates/cv/_preview_pane.html app/templates/base.html tests/test_routes_cv_workbench.py
git commit -m "feat(cv): explicit empty-state in the preview pane before the first draft"
```

---

### Task 4: "Re-checking your guardrails" note

**Files:**
- Modify: `app/templates/cv/_findings.html` — prepend the stale note
- Modify: `app/templates/cv/_preview_pane.html` — render `#cv-findings` unconditionally
- Modify: `app/templates/base.html` — `showPreviewProgress` (~line 1358), the autostart trigger selector + comment (~line 1396), CSS near `.cv-preview-progress` (~line 626)
- Test: `tests/test_routes_cv_workbench.py`

**Interfaces:**
- Consumes: `updating_task_id` (from `_workbench_ctx`; after Task 1 it is truthy only for a running `generate` task).
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Write the failing tests in `tests/test_routes_cv_workbench.py`**

```python
def test_findings_stale_note_hidden_when_no_generate_running(client, cv_on, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft")
    text = client.get(f"/jobs/{jid}/cv").text
    assert 'class="cv-findings-stale" hidden' in text


def test_findings_stale_note_visible_while_generate_runs(client, cv_on, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft")
    q.enqueue_task(conn, kind="cv_tailor",
                   params={"job_id": jid, "mode": "generate", "render": "preview_pane"})
    text = client.get(f"/jobs/{jid}/cv").text
    assert 'class="cv-findings-stale">' in text          # present, no hidden attr


def test_findings_container_present_on_first_generate_without_draft(client, cv_on, conn):
    jid = _job(conn)
    q.enqueue_task(conn, kind="cv_tailor",
                   params={"job_id": jid, "mode": "generate", "render": "preview_pane"})
    text = client.get(f"/jobs/{jid}/cv").text
    assert 'id="cv-findings"' in text
    assert 'class="cv-findings-stale">' in text
```

- [ ] **Step 2: Run them, verify they fail**

Run: `python -m pytest tests/test_routes_cv_workbench.py -k "findings_stale or findings_container" -q`
Expected: FAIL — no `cv-findings-stale` element; `#cv-findings` absent without a draft.

- [ ] **Step 3: Prepend the note in `app/templates/cv/_findings.html`**

Right after the opening `{% set findings = job_cv.guardrail_findings if job_cv else [] %}` line:

```html
<p class="cv-findings-stale"{% if not updating_task_id %} hidden{% endif %}>&#9203; Re-checking
  against your guardrails — results will update when the new draft is ready.</p>
```

Leave the rest of the file (`{% if findings %}` … `{% endif %}`) unchanged.

- [ ] **Step 4: Un-gate `#cv-findings` in `app/templates/cv/_preview_pane.html`**

Move `<div id="cv-findings">{% include "cv/_findings.html" %}</div>` out of the
`{% if has_draft %}` block so it always renders. Final tail of the file:

```html
{% if has_draft %}
  <div class="cv-preview-decision">
    <form method="post" action="/jobs/{{ job.id }}/cv/accept" style="display:inline;">
      <button type="submit" class="btn btn-accept">Accept this CV</button>
    </form>
    {% include "cv/_cv_export.html" %}
  </div>
{% endif %}
<div id="cv-findings">{% include "cv/_findings.html" %}</div>
```

- [ ] **Step 5: Extend `showPreviewProgress` in `app/templates/base.html`**

```javascript
  function showPreviewProgress(on) {
    var p = document.querySelector('.cv-preview-progress');
    if (p) p.hidden = !on;
    // the "update in progress" note stands in for "out of date" while it runs
    var s = document.querySelector('.cv-preview-stale');
    if (s) s.hidden = on;
    // the guardrail findings shown are from the previous draft while a regen runs
    var g = document.querySelector('.cv-findings-stale');
    if (g) g.hidden = !on;
  }
```

- [ ] **Step 6: Drop `#cv-autostart` from the progress trigger in `app/templates/base.html`**

Around line 1396, replace:

```javascript
    // Explicit "Update", or the hidden first-visit autostart (which renders a
    // baseline draft) — show the "update in progress" note straight away.
    var upd = e.target.closest('.cv-preview-actions [data-progress-url*="/cv/generate"], #cv-autostart');
    if (upd) { showPreviewProgress(true); return; }
```

with:

```javascript
    // Explicit "Update" — show the "update in progress" note straight away.
    // (The first-visit autostart only runs the plan now; it produces no draft.)
    var upd = e.target.closest('.cv-preview-actions [data-progress-url*="/cv/generate"]');
    if (upd) { showPreviewProgress(true); return; }
```

- [ ] **Step 7: Add CSS in `app/templates/base.html`**

Right after the `.cv-preview-progress:not([hidden]), .cv-preview-stale:not([hidden])` rule (~line 626):

```css
    .cv-findings-stale { margin: 1.5rem 0 0; padding: 0.3rem 0.55rem; font-size: 0.82rem;
      background: var(--warning-tint); color: var(--text-primary);
      border: 1px solid var(--warning); border-radius: 6px; }
    .cv-findings-stale:not([hidden]) ~ .guardrail-summary { opacity: 0.45; }
```

- [ ] **Step 8: Run the tests, verify they pass**

Run: `python -m pytest tests/test_routes_cv_workbench.py -q`
Expected: PASS.

- [ ] **Step 9: Full suite**

Run: `python -m pytest -q`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add app/templates/cv/_findings.html app/templates/cv/_preview_pane.html app/templates/base.html tests/test_routes_cv_workbench.py
git commit -m "feat(cv): flag guardrail findings as stale while a tailored update runs"
```

---

## Manual test (after all tasks, per CLAUDE.md UI handoff)

Use the `run-dev-server` skill (throwaway DB copy). Then:

1. Open a job's **Tailor CV** page for a job with no `job_cv` row. Confirm: the plan runs
   (one progress line about `plan_tailoring`, no "baseline draft" / "guardrails" lines), the
   preview pane shows *"No tailored CV yet…"* with the Base tab active and Tailored/Differences
   disabled, and no findings panel content (just the hidden stale note in the DOM).
2. Hit **Update**. Confirm the "update in progress" banner shows, and — once a second draft
   exists and you Update again — the guardrail findings dim with the *"Re-checking against your
   guardrails"* note while it runs, then refresh.
3. Hand the URL to the user.
