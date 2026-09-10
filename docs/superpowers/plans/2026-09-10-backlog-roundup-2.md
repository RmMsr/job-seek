# Backlog Roundup 2 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship ten low/medium-effort backlog items — five small UX/logic fixes, two medium UX features (search-time multi-select, richer task result links), an LLM-error-handling fix, config-toggled LLM tracing, and a supported-boards doc.

**Architecture:** Server-rendered FastAPI + Jinja + htmx. No DB migrations. One new optional `[tracing]` config section. Three new dependencies (tracing only; imported lazily). A new `app/ai/_client.py` completion helper and a new `app/tracing.py`.

**Tech Stack:** Python 3.12+, FastAPI, `sqlite3` stdlib, Jinja2, htmx, pytest + `fastapi.testclient.TestClient`, `unittest.mock.MagicMock` for LLM clients.

Spec: `docs/superpowers/specs/2026-09-10-backlog-roundup-2-design.md`

## Global Constraints

- Run tests with `python -m pytest` — **never** `uv run` (read-only cache in this environment).
- `git` is shadowed by an `rtk git` hook that trips the worktree-isolation guard. Run git as **`/usr/bin/git`** in every command.
- Stage explicit paths in commits — `git add -A` / `git add .` fail here (untracked special files in the repo root). `git add <path> <path>` scoped to real subdirs is fine.
- Every DB connection already runs `PRAGMA foreign_keys = ON` — do not add it again.
- Commit after each task. Commit-message trailer:
  ```
  Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01P98HBm3Ro5AU2WgEzsET3D
  ```
- Backlog item numbers (#1, #2, …) refer to `BACKLOG.md` at plan-writing time.
- After all tasks: remove the finished items from `BACKLOG.md` in a final commit.

**Test fixtures — use these exact signatures** (the plan's snippets are illustrative; match these):
```python
sid = q.insert_source(conn, "finn.no", "https://finn.no", "generic_listing")
jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/1",
                   title="ML Eng", company="Acme", raw_text="r")
```
`insert_job` requires `title` and `company` (keyword-only). There is no `create_source`.
For a manual/single source use `q.get_or_create_manual_source(conn)` (returns an int id).
`enqueue_task(conn, kind=..., params=...)` returns a dict with `["id"]`.
`execute_task(conn, client, model, config, task)` — pass `None` for client/model/config
when the task body is monkeypatched to not touch the LLM.

---

## File Structure

| File | Change | Task |
|---|---|---|
| `app/db/queries.py` | `update_job_feedback` message; new `set_job_note` | 1, 6 |
| `app/routes/jobs.py` | `POST /jobs/{id}/note`; `q_filter` in `_filter_from_bulk_form` + 2 routes; `_task_jobs_revisit` outcome; bulk-kind log prefixes | 1, 6, 7 |
| `app/templates/jobs/_feedback.html` | Save-note button | 1 |
| `app/templates/jobs/_content.html` | `q_filter` hidden input; un-gate bulk bar + select-all | 6 |
| `app/templates/jobs/_row.html` | un-gate checkbox during search | 6 |
| `app/templates/fetch/panel.html` | `.page-head` wrapper | 3 |
| `app/dates.py` | new `duration()` | 4 |
| `app/template_env.py` | register `duration` filter | 4 |
| `app/templates/tasks/detail.html` | "Finished in … · … ago"; "Open" → "Review" | 4, 5 |
| `app/routes/tasks.py` | `_results()` — drop "Open"; revisit + bulk result links | 5, 8 |
| `app/ai/_client.py` | **new** — `complete()` helper | 9 |
| `app/ai/*.py` (12 call sites) | use `complete()`, narrow the swallow | 9 |
| `app/config.py` | `[tracing]` load + `write_config` carry-forward | 10 |
| `app/tracing.py` | **new** — `init_tracing()` | 10 |
| `app/main.py` | call `init_tracing` in lifespan | 10 |
| `pyproject.toml` | 3 tracing deps | 10 |
| `config-template.toml`, `config-container-template.toml` | commented `[tracing]` stub | 10 |
| `docs/job-boards.md` | **new** | 11 |
| `README.md` | pointer to the doc | 11 |
| `BACKLOG.md` | remove shipped items | final |

---

## Task 1: Job note save button (#1)

**Files:**
- Modify: `app/db/queries.py` (new `set_job_note`, near `update_job_feedback` ~line 727)
- Modify: `app/routes/jobs.py` (new route; `job_feedback` is ~line 632, `_render_updated_job_html` ~line 157)
- Modify: `app/templates/jobs/_feedback.html` (~line 75, the note `<label>`/`<textarea>`)
- Test: `tests/test_routes_jobs.py`, `tests/test_queries.py`

**Interfaces:**
- Produces: `q.set_job_note(conn, job_id: int, note: str | None) -> None` — writes
  `feedback_note` (NULL when blank), sets `feedback_handled_at = NULL`, does **not**
  touch `status` or `status_changed_at`, records no `job_events` row.
- Produces: `POST /jobs/{job_id}/note` — form field `note: str | None`, optional
  `redirect` hidden field (detail page), returns the re-rendered job card HTML
  (same shape as `POST /jobs/{job_id}/feedback`).

- [ ] **Step 1: Write the failing query test**

In `tests/test_queries.py`:

```python
def test_set_job_note_persists_without_status_change(conn):
    from app.db import queries as q
    sid = q.insert_source(conn, "s", "http://e", "manual")
    jid = q.insert_job(conn, source_id=sid, url="http://e/1", title="T", company="Acme", raw_text="x")
    q.update_job_feedback(conn, jid, "accepted", "")
    q.set_job_note(conn, jid, "  keep an eye on comp band  ")
    job = q.get_job(conn, jid)
    assert job["feedback_note"] == "keep an eye on comp band"
    assert job["status"] == "accepted"  # unchanged
    assert job["feedback_handled_at"] is None
    events = q.get_job_events(conn, jid)
    assert all("keep an eye" not in e["message"] for e in events)

def test_set_job_note_clears_when_blank(conn):
    from app.db import queries as q
    sid = q.insert_source(conn, "s", "http://e", "manual")
    jid = q.insert_job(conn, source_id=sid, url="http://e/1", title="T", company="Acme", raw_text="x")
    q.set_job_note(conn, jid, "something")
    q.set_job_note(conn, jid, "   ")
    assert q.get_job(conn, jid)["feedback_note"] is None
```

Match the module's existing fixture style (see the Test fixtures note above).

- [ ] **Step 2: Run — expect failure**

Run: `python -m pytest tests/test_queries.py -k set_job_note -q`
Expected: FAIL — `AttributeError: module 'app.db.queries' has no attribute 'set_job_note'`

- [ ] **Step 3: Implement `set_job_note`**

In `app/db/queries.py`, after `update_job_feedback`:

```python
def set_job_note(conn: sqlite3.Connection, job_id: int, note: str | None) -> None:
    """Persist a job's feedback note without touching its status. Blank clears it.
    Clears feedback_handled_at so the profile-refine loop re-picks it up, matching
    update_job_feedback."""
    clean = note.strip() if isinstance(note, str) else ""
    conn.execute(
        "UPDATE jobs SET feedback_note = ?, feedback_handled_at = NULL WHERE id = ?",
        (clean or None, job_id),
    )
    conn.commit()
```

- [ ] **Step 4: Run — expect pass**

Run: `python -m pytest tests/test_queries.py -k set_job_note -q`
Expected: PASS

- [ ] **Step 5: Write the failing route test**

In `tests/test_routes_jobs.py` (match the module's existing fixture style for creating a job):

```python
def test_save_note_route_persists_without_status_change(client, conn):
    from app.db import queries as q
    sid = q.insert_source(conn, "s", "http://e", "manual")
    jid = q.insert_job(conn, source_id=sid, url="http://e/1", title="T", company="Acme", raw_text="x")
    r = client.post(f"/jobs/{jid}/note", data={"note": "watch this one"})
    assert r.status_code == 200
    job = q.get_job(conn, jid)
    assert job["feedback_note"] == "watch this one"
    assert job["status"] == "new"
```

- [ ] **Step 6: Run — expect failure**

Run: `python -m pytest tests/test_routes_jobs.py -k save_note -q`
Expected: FAIL — 404 or 405.

- [ ] **Step 7: Implement the route**

In `app/routes/jobs.py`, near `job_feedback` (~line 632). Model it on `job_feedback`'s
signature — it takes `request`, uses `_filter_from_request` / `_is_detail_page_request` /
`_render_updated_job_html`. Minimal version:

```python
@router.post("/jobs/{job_id}/note", response_class=HTMLResponse)
def job_save_note(
    job_id: int,
    request: Request,
    note: str | None = Form(None),
    conn: sqlite3.Connection = Depends(get_db),
):
    q.set_job_note(conn, job_id, note)
    f = _filter_from_request(request)
    detail = _is_detail_page_request(request)
    row_html = _render_updated_job_html(conn, request, job_id, f, detail=detail)
    return HTMLResponse(content=row_html)
```

(Compare against `job_feedback` right below/above it and match its `Form`/`Depends`
imports and the `_render_updated_job_html` call signature exactly.)

- [ ] **Step 8: Run — expect pass**

Run: `python -m pytest tests/test_routes_jobs.py -k save_note -q`
Expected: PASS

- [ ] **Step 9: Add the button to `_feedback.html`**

The note lives in the `feedback-form` (`hx-post` to `/jobs/{id}/feedback`). A nested form
isn't valid, so give the Save-note button its own `hx-post` via htmx (buttons can carry
`hx-*` without a wrapping form). Replace the note `<label>` block (~line 75-77) with:

```html
    <div class="note-field">
      <label><strong>Job note</strong> (optional, will also feed back into adjusting your profile):
        <textarea name="note" id="job-note-{{ job.id }}"
          placeholder="Why accepting/rejecting? What should change?">{{ job.feedback_note or '' }}</textarea>
      </label>
      <button type="button" class="btn btn-subtle"
        hx-post="/jobs/{{ job.id }}/note{{ macros.qsuffix(filter, detail=is_detail_page|default(false)) }}"
        hx-include="#job-note-{{ job.id }}"
        hx-target="#job-{{ job.id }}"
        hx-swap="outerHTML">Save note</button>
    </div>
```

`hx-include` targets the textarea by id so only `note` is posted. No new CSS is required;
add a one-line `.note-field { display: flex; flex-direction: column; align-items: flex-start; gap: 0.35rem; }`
in `base.html` only if the button visually crowds the textarea.

- [ ] **Step 10: Manual check + full suite**

Run: `python -m pytest -q`
Expected: all pass.

- [ ] **Step 11: Commit**

```bash
/usr/bin/git add app/db/queries.py app/routes/jobs.py app/templates/jobs/_feedback.html tests/test_queries.py tests/test_routes_jobs.py
/usr/bin/git commit -m "feat: save a job note without changing its status (#1)"
```

---

## Task 2: Status history should not echo the note text (#2)

**Files:**
- Modify: `app/db/queries.py` (`update_job_feedback` ~line 727-742)
- Test: `tests/test_queries.py`

**Interfaces:**
- Changes: the `job_events` `status` row written by `update_job_feedback` is now
  exactly `Status: <old> → <new>` with no `— "note"` suffix.

- [ ] **Step 1: Update the failing test**

Find the existing test in `tests/test_queries.py` that asserts on the status-change event
message (grep for `Status:` or `→`). Change its expectation to the bare form and add:

```python
def test_status_change_event_omits_note_text(conn):
    from app.db import queries as q
    sid = q.insert_source(conn, "s", "http://e", "manual")
    jid = q.insert_job(conn, source_id=sid, url="http://e/1", title="T", company="Acme", raw_text="x")
    q.update_job_feedback(conn, jid, "rejected", "salary too low, wrong stack")
    msgs = [e["message"] for e in q.get_job_events(conn, jid)]
    assert "Status: new → rejected" in msgs
    assert all("salary too low" not in m for m in msgs)
```

- [ ] **Step 2: Run — expect failure**

Run: `python -m pytest tests/test_queries.py -k "status_change_event or feedback" -q`
Expected: FAIL on the note-suffix assertion.

- [ ] **Step 3: Drop the suffix**

In `update_job_feedback`, replace:

```python
    if record_event and old_status is not None and old_status != status:
        message = f"Status: {old_status} → {status}"
        if isinstance(note, str) and note.strip():
            message += f' — "{note.strip()}"'
        add_job_event(conn, job_id, "status", message)
```

with:

```python
    if record_event and old_status is not None and old_status != status:
        add_job_event(conn, job_id, "status", f"Status: {old_status} → {status}")
```

- [ ] **Step 4: Run — expect pass**

Run: `python -m pytest tests/test_queries.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
/usr/bin/git add app/db/queries.py tests/test_queries.py
/usr/bin/git commit -m "fix: don't repeat the note text into status history (#2)"
```

---

## Task 3: `/fetch` action buttons inline with the title (#4)

**Files:**
- Modify: `app/templates/fetch/panel.html`
- Modify: `app/templates/base.html` (only if `.page-head` needs a tweak for a bare button row)
- Test: `tests/test_routes_fetch.py` (smoke only)

**Interfaces:** none — layout only.

- [ ] **Step 1: Confirm the `.page-head` pattern**

Read `app/templates/jobs/list.html` (`<div class="page-head">` around the `<h1>` + add
panel) and the `.page-head` rules in `app/templates/base.html`. Note whether it's
`display:flex; align-items:center; justify-content:space-between` or similar.

- [ ] **Step 2: Wrap the fetch header**

In `app/templates/fetch/panel.html`, change:

```html
<h1>Fetch</h1>
<div style="margin-bottom:1rem;">
  {% if sources %}
  <button class="btn" data-progress-url="/fetch/all">Fetch all</button>
  {% endif %}
  <button class="btn" data-progress-url="/revisit/all" data-progress-oob
    title="Re-checks each open job's live posting; anything no longer reachable moves to Trash.">Refresh open jobs</button>
</div>
```

to:

```html
<div class="page-head">
  <h1>Fetch</h1>
  <div class="page-head-actions">
    {% if sources %}
    <button class="btn" data-progress-url="/fetch/all">Fetch all</button>
    {% endif %}
    <button class="btn" data-progress-url="/revisit/all" data-progress-oob
      title="Re-checks each open job's live posting; anything no longer reachable moves to Trash.">Refresh open jobs</button>
  </div>
</div>
```

- [ ] **Step 3: Add minimal CSS if needed**

If `.page-head` doesn't already lay a bare action group out on the same line, add to
`base.html`:

```css
.page-head-actions { display: flex; gap: 0.5rem; flex-wrap: wrap; }
```

(Only add this if the buttons don't already sit right. Reuse whatever the Jobs/Sources
`.page-head` provides.)

- [ ] **Step 4: Verify**

Run: `python -m pytest tests/test_routes_fetch.py -q` (or `python -m pytest -k fetch -q`)
Expected: PASS. Also start the dev server (see the `run-dev-server` skill) and eyeball
`/fetch` if convenient — the two buttons should sit on the `<h1>` baseline row.

- [ ] **Step 5: Commit**

```bash
/usr/bin/git add app/templates/fetch/panel.html app/templates/base.html
/usr/bin/git commit -m "fix: put /fetch action buttons in line with the title (#4)"
```

---

## Task 4: Finished task shows duration first (#5)

**Files:**
- Modify: `app/dates.py` (add `duration`)
- Modify: `app/template_env.py` (register the filter — import from `app.dates`, ~line 5 & ~line 32)
- Modify: `app/templates/tasks/detail.html` (~line 13, the `Finished` `<dt>/<dd>`)
- Test: `tests/test_dates.py`, `tests/test_routes_tasks.py`

**Interfaces:**
- Produces: `duration(start: str | None, end: str | None) -> str` — compact elapsed span
  between two ISO-8601 timestamps: `""` if either missing or `end < start`, else
  `"8s"`, `"2m 30s"`, `"1h 4m"` (largest two units, seconds dropped once minutes reach 60).
- Registered as the Jinja filter `duration`, called `{{ start | duration(end) }}`.

- [ ] **Step 1: Write the failing filter test**

Create/extend `tests/test_dates.py`:

```python
from app.dates import duration

def test_duration_seconds():
    assert duration("2026-09-10T10:00:00", "2026-09-10T10:00:08") == "8s"

def test_duration_minutes_seconds():
    assert duration("2026-09-10T10:00:00", "2026-09-10T10:02:30") == "2m 30s"

def test_duration_hours_minutes():
    assert duration("2026-09-10T10:00:00", "2026-09-10T11:04:12") == "1h 4m"

def test_duration_missing_or_reversed():
    assert duration(None, "2026-09-10T10:00:00") == ""
    assert duration("2026-09-10T10:00:00", None) == ""
    assert duration("2026-09-10T10:05:00", "2026-09-10T10:00:00") == ""
```

- [ ] **Step 2: Run — expect failure**

Run: `python -m pytest tests/test_dates.py -k duration -q`
Expected: FAIL — `ImportError: cannot import name 'duration'`

- [ ] **Step 3: Implement `duration`**

In `app/dates.py`:

```python
def duration(start: str | None, end: str | None) -> str:
    """Compact elapsed span between two ISO-8601 timestamps: '8s', '2m 30s',
    '1h 4m'. Empty string if either side is missing or end precedes start."""
    if not start or not end:
        return ""
    a = datetime.fromisoformat(start)
    b = datetime.fromisoformat(end)
    if a.tzinfo is None:
        a = a.replace(tzinfo=timezone.utc)
    if b.tzinfo is None:
        b = b.replace(tzinfo=timezone.utc)
    total = int((b - a).total_seconds())
    if total < 0:
        return ""
    if total < 60:
        return f"{total}s"
    minutes, seconds = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m {seconds}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"
```

- [ ] **Step 4: Run — expect pass**

Run: `python -m pytest tests/test_dates.py -k duration -q`
Expected: PASS

- [ ] **Step 5: Register the filter**

In `app/template_env.py`: change the import to
`from app.dates import time_ago, age, duration` and add near the other filter
registrations: `templates.env.filters["duration"] = duration`.

- [ ] **Step 6: Write the failing route test**

In `tests/test_routes_tasks.py` (match the module's helper for creating a finished task —
grep for `complete_task` / `finished_at`):

```python
def test_finished_task_detail_shows_duration(client, conn):
    from app.db import queries as q
    t = q.enqueue_task(conn, kind="jobs_revisit", params={"job_ids": [], "trigger": "manual"})
    conn.execute(
        "UPDATE tasks SET status='done', started_at='2026-09-10T10:00:00', "
        "finished_at='2026-09-10T10:02:30' WHERE id=?", (t["id"],))
    conn.commit()
    r = client.get(f"/tasks/{t['id']}")
    assert "Finished in 2m 30s" in r.text
```

Adjust column names to the real `tasks` schema (grep `app/db/schema.py` for the tasks
table). If `enqueue_task`'s signature differs, match existing task tests.

- [ ] **Step 7: Run — expect failure**

Run: `python -m pytest tests/test_routes_tasks.py -k duration -q`
Expected: FAIL — text not found.

- [ ] **Step 8: Update `tasks/detail.html`**

Replace line ~13:

```html
  {% if task.finished_at and not is_group %}<dt>{{ 'Needs you since' if task.status == 'needs_action' else 'Finished' }}</dt><dd>{{ task.finished_at | age }}</dd>{% endif %}
```

with:

```html
  {% if task.finished_at and not is_group %}
    <dt>{{ 'Needs you since' if task.status == 'needs_action' else 'Finished' }}</dt>
    <dd>
      {%- if task.status != 'needs_action' and task.started_at %}
        in {{ task.started_at | duration(task.finished_at) }} · {{ task.finished_at | age }}
      {%- else %}
        {{ task.finished_at | age }}
      {%- endif %}
    </dd>
  {% endif %}
```

- [ ] **Step 9: Run — expect pass**

Run: `python -m pytest tests/test_routes_tasks.py -q && python -m pytest tests/test_dates.py -q`
Expected: PASS

- [ ] **Step 10: Commit**

```bash
/usr/bin/git add app/dates.py app/template_env.py app/templates/tasks/detail.html tests/test_dates.py tests/test_routes_tasks.py
/usr/bin/git commit -m "feat: finished task detail leads with run duration (#5)"
```

---

## Task 5: Drop the "Open" keyword (#7a)

**Files:**
- Modify: `app/routes/tasks.py` (`_results()` ~line 187-218)
- Modify: `app/templates/tasks/detail.html` (~line 31, the `awaiting` branch button)
- Test: `tests/test_routes_tasks.py`

**Interfaces:**
- Changes result-link labels: `"Open Scenarios"` → `"Scenarios"`,
  `"Open the CV workbench"` → `"CV workbench"`, `"Open the CV for {title}"` →
  `"CV for {title}"`. The `awaiting` action button text `Open` → `Review`.

- [ ] **Step 1: Update the failing tests**

In `tests/test_routes_tasks.py`, grep for `"Open "` assertions and flip them. Add:

```python
def test_result_labels_have_no_open_prefix(client, conn):
    from app.db import queries as q
    t = q.enqueue_task(conn, kind="scenarios_refine_all", params={})
    conn.execute("UPDATE tasks SET status='done', result='{}' WHERE id=?", (t["id"],))
    conn.commit()
    r = client.get(f"/tasks/{t['id']}")
    assert "Scenarios" in r.text
    assert "Open Scenarios" not in r.text
```

- [ ] **Step 2: Run — expect failure**

Run: `python -m pytest tests/test_routes_tasks.py -k "open_prefix or label" -q`
Expected: FAIL.

- [ ] **Step 3: Rename in `_results()`**

In `app/routes/tasks.py:_results()`:
- `{"label": f"Open the CV for {title}" if title else "Open the CV workbench", ...}`
  → `{"label": f"CV for {title}" if title else "CV workbench", ...}`
- `{"label": "Open Scenarios", "href": "/scenarios"}` → `{"label": "Scenarios", ...}`

- [ ] **Step 4: Rename in `detail.html`**

Line ~31: `<a class="btn btn-primary" href="{{ pres.action_link }}">Open</a>`
→ `<a class="btn btn-primary" href="{{ pres.action_link }}">Review</a>`

- [ ] **Step 5: Run — expect pass**

Run: `python -m pytest tests/test_routes_tasks.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
/usr/bin/git add app/routes/tasks.py app/templates/tasks/detail.html tests/test_routes_tasks.py
/usr/bin/git commit -m "ux: drop the 'Open' keyword from task result links (#7a)"
```

---

## Task 6: Multi-select while a text search is active (#3)

**Files:**
- Modify: `app/routes/jobs.py` (`_filter_from_bulk_form` ~line 204; `job_bulk_feedback` ~line 632; `job_bulk_delete` ~line 693)
- Modify: `app/templates/jobs/_content.html` (hidden inputs ~line 10-15; `.filter-tools` gate ~line 58; bulk-bar gate ~line 97)
- Modify: `app/templates/jobs/_row.html` (~line 9 checkbox gate)
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: `JobFilter.from_params` already accepts `q`; `f.searching` is true when `q` set.
- Changes: `_filter_from_bulk_form(status_filter, scenario_filter, source_id_filter,
  org_filter, order_filter, q_filter=None)` — gains a trailing `q_filter` param placed as
  `"q": q_filter or ""` in the params dict. `job_bulk_feedback` and `job_bulk_delete`
  gain `q_filter: str | None = Form(None)` and pass it last.

- [ ] **Step 1: Write the failing test**

In `tests/test_routes_jobs.py` (match the module's job-creation helpers):

```python
def test_bulk_reject_during_search_keeps_search_view(client, conn):
    from app.db import queries as q
    sid = q.insert_source(conn, "s", "http://e", "manual")
    a = q.insert_job(conn, source_id=sid, url="http://e/a", title="Python dev", company="Acme", raw_text="python role")
    b = q.insert_job(conn, source_id=sid, url="http://e/b", title="Python dev", company="Acme", raw_text="python role")
    q.update_job_feedback(conn, b, "accepted", "")   # b is on a different tab
    for jid in (a, b):
        conn.execute("UPDATE jobs SET title='Python dev', summary='python' WHERE id=?", (jid,))
    conn.commit()
    r = client.post("/jobs/bulk-feedback", data={
        "job_ids": [a, b], "status": "rejected",
        "status_filter": "new,accepted", "q_filter": "python",
    })
    assert r.status_code == 200
    assert q.get_job(conn, a)["status"] == "rejected"
    assert q.get_job(conn, b)["status"] == "rejected"
    # response is still the filtered search render, not a full "new" tab
    assert "Python dev" in r.text
```

Confirm `search_jobs` matches on `raw_text`/`summary`/`title` — grep `app/db/queries.py`
for `search_jobs` and align the fixture text so both jobs match `"python"`.

- [ ] **Step 2: Run — expect failure**

Run: `python -m pytest tests/test_routes_jobs.py -k bulk_reject_during_search -q`
Expected: FAIL — `q_filter` unknown / search view lost.

- [ ] **Step 3: Thread `q_filter` through the routes**

In `_filter_from_bulk_form`:

```python
def _filter_from_bulk_form(
    status_filter: str | None,
    scenario_filter: str | None,
    source_id_filter: str | None,
    org_filter: str | None,
    order_filter: str | None = None,
    q_filter: str | None = None,
) -> JobFilter:
    return JobFilter.from_params({
        "status": status_filter or "new",
        "scenario": scenario_filter or "",
        "source_id": source_id_filter or "",
        "org": org_filter or "",
        "order": order_filter or "",
        "q": q_filter or "",
    })
```

In `job_bulk_feedback` and `job_bulk_delete`: add `q_filter: str | None = Form(None)` to
the signature and pass it as the last arg to `_filter_from_bulk_form(...)`.

- [ ] **Step 4: Add the hidden input**

In `app/templates/jobs/_content.html`, after the other `*_filter` hidden inputs (~line 15):

```html
<input type="hidden" name="q_filter" value="{{ filter.q }}" form="bulk-form">
```

- [ ] **Step 5: Un-gate the checkbox and bulk UI**

- `app/templates/jobs/_row.html` line ~9: change
  `{% if not is_detail_page and not (filter and filter.searching) %}` →
  `{% if not is_detail_page %}`
- `app/templates/jobs/_content.html` line ~97: change `{% if not filter.searching %}`
  (guarding the `.bulk-bar`) → remove the guard (keep the `{% endif %}` bookkeeping —
  delete both the `{% if %}` and its matching `{% endif %}` at line ~128).
- `app/templates/jobs/_content.html` — the `.filter-tools` block (~line 58-76) currently
  holds the Sort select **and** the select-all label under one `{% if not filter.searching %}`.
  Restructure so select-all shows during search but Sort does not:

```html
  <div class="filter-tools">
    {% if not filter.searching %}
    <label>Sort
      <select name="order" hx-get="/jobs" hx-target="#jobs-content" hx-swap="innerHTML"
              hx-push-url="true" hx-vals='{"status": "{{ filter.statuses | join(',') }}"}'
              hx-include="[name='scenario'],[name='source_id'],[name='org']">
        <option value="change" {% if filter.order == 'change' %}selected{% endif %}>Newest changes</option>
        <option value="score" {% if filter.order == 'score' %}selected{% endif %}>Fit score</option>
        <option value="age" {% if filter.order == 'age' %}selected{% endif %}>Posting age</option>
      </select>
    </label>
    {% endif %}
    {% if jobs %}
    <label class="select-all">
      <input type="checkbox" class="select-all-checkbox" aria-label="Select all">
      Select all
    </label>
    {% endif %}
  </div>
```

- [ ] **Step 6: Run — expect pass**

Run: `python -m pytest tests/test_routes_jobs.py -k bulk_reject_during_search -q`
Expected: PASS

- [ ] **Step 7: Add the double-select-all regression test**

The select-all JS in `base.html` already unchecks every `job_ids` box when the header box
is clicked while checked. This test guards the wiring stays intact during search — it's a
DOM/JS behaviour, so assert the rendered markup wires it (no JS runner here):

```python
def test_select_all_present_during_search(client, conn):
    from app.db import queries as q
    sid = q.insert_source(conn, "s", "http://e", "manual")
    jid = q.insert_job(conn, source_id=sid, url="http://e/a", title="Python dev", company="Acme", raw_text="python role")
    conn.execute("UPDATE jobs SET title='Python dev', summary='python' WHERE id=?", (jid,))
    conn.commit()
    r = client.get("/jobs?q=python")
    assert 'class="select-all-checkbox"' in r.text
    assert f'name="job_ids" value="{jid}"' in r.text
```

Run: `python -m pytest tests/test_routes_jobs.py -k "select_all_present_during_search" -q`
Expected: PASS

- [ ] **Step 8: Full suite**

Run: `python -m pytest -q`
Expected: all pass. (Watch for existing tests that asserted checkboxes are **absent**
during search — update those to the new behaviour.)

- [ ] **Step 9: Commit**

```bash
/usr/bin/git add app/routes/jobs.py app/templates/jobs/_content.html app/templates/jobs/_row.html tests/test_routes_jobs.py
/usr/bin/git commit -m "feat: allow multi-select + bulk actions during job search (#3)"
```

---

## Task 7: `jobs_revisit` records which jobs changed (#7b / #9, part 1)

**Files:**
- Modify: `app/routes/jobs.py` (`_task_jobs_revisit` ~line 445-509; `_task_jobs_bulk_reset` ~line 558; `_task_jobs_bulk_reevaluate` ~line 587)
- Test: `tests/test_routes_jobs.py` or `tests/test_routes_tasks.py`

**Interfaces:**
- Produces: a completed multi-job `jobs_revisit` task's `result` dict gains
  `result["outcome"] = {"total": int, "changed": list[int], "closed": list[int]}`
  (job ids). Single-job revisit results are unchanged.
- Changes: per-job progress lines in the multi-job revisit loop and both bulk loops are
  prefixed `[i/N] job <id>: ` instead of `[i/N] `.

- [ ] **Step 1: Write the failing test**

```python
def test_multi_job_revisit_result_lists_changed_and_closed(client, conn, monkeypatch):
    from app.db import queries as q
    from app import pipeline
    sid = q.insert_source(conn, "s", "http://e", "manual")
    j_closed = q.insert_job(conn, source_id=sid, url="http://e/x", title="X", company="Acme", raw_text="x")
    j_ok = q.insert_job(conn, source_id=sid, url="http://e/y", title="Y", company="Acme", raw_text="y")
    for jid in (j_closed, j_ok):
        conn.execute("UPDATE jobs SET summary='ref', simplified_content='ref' WHERE id=?", (jid,))
    conn.commit()

    def fake_revisit(conn, client, model, job, scenarios, profile, *, progress_prefix=""):
        yield f"{progress_prefix}checking"
        if job["id"] == j_closed:
            q.mark_job_closed(conn, job["id"], "gone")
            return pipeline.RevisitOutcome("closed", "gone")
        return pipeline.RevisitOutcome("unchanged")

    monkeypatch.setattr("app.routes.jobs.run_revisit_job", fake_revisit)
    t = q.enqueue_task(conn, kind="jobs_revisit",
                       params={"job_ids": [j_closed, j_ok], "trigger": "manual"})
    from app.task_engine import execute_task
    execute_task(conn, None, None, None, q.get_task(conn, t["id"]))

    result = q.get_task(conn, t["id"])["result"]
    assert result["outcome"]["closed"] == [j_closed]
    assert result["outcome"]["total"] == 2
    log = q.get_task(conn, t["id"])["log"]
    assert f"job {j_closed}:" in log
```

Match `execute_task`'s real signature (grep `app/task_engine.py` — it's
`execute_task(conn, client, model, config, task)`). Check `RevisitOutcome`'s constructor.

- [ ] **Step 2: Run — expect failure**

Run: `python -m pytest tests/test_routes_jobs.py -k multi_job_revisit_result -q`
Expected: FAIL — no `outcome` key / no `job <id>:` in log.

- [ ] **Step 3: Add the id prefix + outcome in `_task_jobs_revisit`**

In the loop (~line 460):

```python
    for i, job in enumerate(jobs, start=1):
        prefix = f"[{i}/{len(jobs)}] job {job['id']}: " if len(jobs) > 1 else f"job {job['id']}: "
        gen = run_revisit_job(conn, client, model, job, scenarios, profile, progress_prefix=prefix)
```

In the multi-job result branch (the `else` at ~line 505):

```python
    else:
        changed_ids = [j["id"] for j, o in outcomes if o and o.verdict == "changed"]
        closed_ids = [j["id"] for j, o in outcomes if o and o.verdict == "closed"]
        summary = f"Revisited {len(outcomes)} · closed {len(closed_ids)} · changed {len(changed_ids)}"
        yield summary
        result["outcome"] = {
            "total": len(outcomes), "changed": changed_ids, "closed": closed_ids,
        }
        result["notices"] = [{"level": "info", "html": f"<p>{summary}</p>"}]
    return result
```

(Keep the existing single-job branch above it untouched.)

- [ ] **Step 4: Add the id prefix in both bulk loops**

`_task_jobs_bulk_reset` and `_task_jobs_bulk_reevaluate`, in each `for idx, job_id ...`
loop change `prefix = f"[{idx}/{len(job_ids)}] "` →
`prefix = f"[{idx}/{len(job_ids)}] job {job_id}: "`.

- [ ] **Step 5: Run — expect pass**

Run: `python -m pytest tests/test_routes_jobs.py -k multi_job_revisit_result -q`
Expected: PASS

- [ ] **Step 6: Full suite**

Run: `python -m pytest -q`
Expected: all pass (some existing revisit/bulk log assertions may need the `job <id>:`
prefix added).

- [ ] **Step 7: Commit**

```bash
/usr/bin/git add app/routes/jobs.py tests/
/usr/bin/git commit -m "feat: record changed/closed job ids on multi-job revisit; job ids in bulk logs (#9)"
```

---

## Task 8: Task result view links changed items + states the change (#7b / #9, part 2)

**Files:**
- Modify: `app/routes/tasks.py` (`_results()` ~line 187-218)
- Test: `tests/test_routes_tasks.py`

**Interfaces:**
- Consumes: `result["outcome"]` from Task 7; `task["params"]["job_ids"]` for the bulk kinds.
- Changes `_results()` output for `jobs_revisit` (multi-job) and `jobs_bulk_reset` /
  `jobs_bulk_reevaluate`.

- [ ] **Step 1: Write the failing test**

```python
def test_revisit_result_view_summarises_and_links_changed(client, conn):
    from app.db import queries as q
    sid = q.insert_source(conn, "s", "http://e", "manual")
    j1 = q.insert_job(conn, source_id=sid, url="http://e/1", title="T", company="Acme", raw_text="x")
    j2 = q.insert_job(conn, source_id=sid, url="http://e/2", title="J2", company="Acme", raw_text="x")
    j3 = q.insert_job(conn, source_id=sid, url="http://e/3", title="J3", company="Acme", raw_text="x")
    conn.execute("UPDATE jobs SET title='Gone Role' WHERE id=?", (j2,))
    t = q.enqueue_task(conn, kind="jobs_revisit",
                       params={"job_ids": [j1, j2, j3], "trigger": "manual"})
    conn.execute(
        "UPDATE tasks SET status='done', result=? WHERE id=?",
        ('{"outcome": {"total": 3, "changed": [], "closed": [%d]}}' % j2, t["id"]))
    conn.commit()
    r = client.get(f"/tasks/{t['id']}")
    assert "3 rechecked" in r.text
    assert "1 moved to Trash" in r.text
    assert f'/jobs/{j2}' in r.text and "Gone Role" in r.text

def test_bulk_reset_result_links_jobs(client, conn):
    from app.db import queries as q
    sid = q.insert_source(conn, "s", "http://e", "manual")
    j1 = q.insert_job(conn, source_id=sid, url="http://e/1", title="T", company="Acme", raw_text="x")
    conn.execute("UPDATE jobs SET title='Reset Me' WHERE id=?", (j1,))
    t = q.enqueue_task(conn, kind="jobs_bulk_reset", params={"job_ids": [j1]})
    conn.execute("UPDATE tasks SET status='done', result='{}' WHERE id=?", (t["id"],))
    conn.commit()
    r = client.get(f"/tasks/{t['id']}")
    assert "1 job reset" in r.text
    assert f'/jobs/{j1}' in r.text and "Reset Me" in r.text
```

- [ ] **Step 2: Run — expect failure**

Run: `python -m pytest tests/test_routes_tasks.py -k "revisit_result_view or bulk_reset_result_links" -q`
Expected: FAIL.

- [ ] **Step 3: Rewrite the relevant branches of `_results()`**

Add a small helper above `_results` and replace the two branches:

```python
_RESULT_LINK_CAP = 10

def _job_links(conn, job_ids, *, label_prefix="View"):
    out = []
    for jid in job_ids[:_RESULT_LINK_CAP]:
        title = _job_title(conn, jid)
        out.append({"label": f"{title}" if title else f"{label_prefix} job {jid}",
                    "href": f"/jobs/{jid}"})
    return out
```

In `_results()`:

```python
    elif kind == "jobs_revisit" and (outcome := r.get("outcome")):
        total = outcome.get("total", 0)
        changed, closed = outcome.get("changed", []), outcome.get("closed", [])
        unchanged = max(0, total - len(changed) - len(closed))
        parts = [f"{total} rechecked"]
        if closed:
            parts.append(f"{len(closed)} moved to Trash")
        if changed:
            parts.append(f"{len(changed)} updated")
        if unchanged:
            parts.append(f"{unchanged} unchanged")
        out.append({"label": " · ".join(parts), "href": None})
        out.extend(_job_links(conn, closed + changed))
        if len(closed) + len(changed) > _RESULT_LINK_CAP or unchanged:
            out.append({"label": "View all rechecked jobs", "href": "/jobs?status=trash"})
    elif kind in ("jobs_bulk_reset", "jobs_bulk_reevaluate"):
        ids = params.get("job_ids") or []
        verb = "reset" if kind == "jobs_bulk_reset" else "re-evaluated"
        out.append({"label": f"{len(ids)} job{'s' if len(ids) != 1 else ''} {verb}", "href": None})
        out.extend(_job_links(conn, ids))
        if len(ids) > _RESULT_LINK_CAP:
            out.append({"label": "Back to jobs", "href": "/jobs"})
```

**Important:** `_results()` output rows may have `href: None` now. Check
`tasks/detail.html` line ~53 (`Results` list) and any `.results` renderer — if it does
`<a href="{{ r.href }}">` unconditionally, guard it:

```html
<ul>{% for r in pres.results %}<li>
  {% if r.href %}<a href="{{ r.href }}">{{ r.label }}</a>{% else %}{{ r.label }}{% endif %}
</li>{% endfor %}</ul>
```

Also check `_task_summary` / `_root_summary` consumers (the status bar JSON) tolerate a
null `href` — they pass `results` straight through as data, so JSON-null is fine, but
grep the status-bar JS in `base.html` for `.href` and guard if it builds an `<a>`.

- [ ] **Step 4: Run — expect pass**

Run: `python -m pytest tests/test_routes_tasks.py -k "revisit_result_view or bulk_reset_result_links" -q`
Expected: PASS

- [ ] **Step 5: Full suite + dev-server eyeball**

Run: `python -m pytest -q`
Expected: all pass. If convenient, run a real multi-job revisit on the throwaway DB and
look at the task page.

- [ ] **Step 6: Commit**

```bash
/usr/bin/git add app/routes/tasks.py app/templates/tasks/detail.html app/templates/base.html tests/test_routes_tasks.py
/usr/bin/git commit -m "feat: task results summarise the change and link the affected jobs (#7b)"
```

---

## Task 9: An unreachable inference endpoint counts as an error (#8)

**Files:**
- Create: `app/ai/_client.py`
- Modify: 10 files in `app/ai/` (12 call sites — see list)
- Test: `tests/test_ai_client.py` (new), plus 2 call-site tests

**Interfaces:**
- Produces: `app.ai._client.complete(client, model, messages, **kwargs) -> str` — runs
  `client.chat.completions.create(model=model, messages=messages, **kwargs)` and returns
  `resp.choices[0].message.content or ""`. Raises anything the OpenAI client raises
  (`openai.APIError` and subclasses: connection, timeout, auth, 4xx, 5xx).

**Call sites (all in `app/ai/`):** `assess_fit.assess_fit`, `classify.classify`,
`detect_listing.detect_listing`, `evaluate.evaluate`,
`generate_source_name.generate_source_name`, `refine.propose_criteria`
(confirm the real fn name), `refine_profile.propose_profile_changes`,
`revisit_check.revisit_check`, `summarize.summarize`, `tailor_cv.plan_tailoring`,
`tailor_cv.tailor_cv`, `tailor_cv.check_guardrails`.

- [ ] **Step 1: Write the failing helper test**

Create `tests/test_ai_client.py`:

```python
import httpx
import openai
import pytest
from unittest.mock import MagicMock
from app.ai._client import complete


def _client(text=None, exc=None):
    c = MagicMock()
    if exc is not None:
        c.chat.completions.create.side_effect = exc
    else:
        choice = MagicMock()
        choice.message.content = text
        c.chat.completions.create.return_value = MagicMock(choices=[choice])
    return c


def test_complete_returns_content():
    c = _client(text='{"ok": true}')
    assert complete(c, "m", [{"role": "user", "content": "hi"}]) == '{"ok": true}'


def test_complete_propagates_connection_error():
    err = openai.APIConnectionError(request=httpx.Request("POST", "http://x"))
    c = _client(exc=err)
    with pytest.raises(openai.APIConnectionError):
        complete(c, "m", [{"role": "user", "content": "hi"}])


def test_complete_none_content_becomes_empty_string():
    c = _client(text=None)
    assert complete(c, "m", []) == ""
```

- [ ] **Step 2: Run — expect failure**

Run: `python -m pytest tests/test_ai_client.py -q`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement the helper**

Create `app/ai/_client.py`:

```python
from __future__ import annotations
import openai


def complete(client: openai.OpenAI, model: str, messages: list[dict], **kwargs) -> str:
    """Run a chat completion and return the assistant message text.

    Deliberately does NOT catch openai errors — a connection / timeout / auth /
    HTTP-status failure means the inference endpoint is unusable and the caller's
    task must fail loudly rather than persist a degraded 'result'. Callers keep
    their own try/except only around parsing the returned string.
    """
    resp = client.chat.completions.create(model=model, messages=messages, **kwargs)
    return resp.choices[0].message.content or ""
```

- [ ] **Step 4: Run — expect pass**

Run: `python -m pytest tests/test_ai_client.py -q`
Expected: PASS

- [ ] **Step 5: Write a failing call-site test**

In `tests/test_tailor_cv_generate.py` (or wherever `plan_tailoring` is tested) add:

```python
def test_plan_tailoring_propagates_connection_error():
    import httpx, openai, pytest
    from unittest.mock import MagicMock
    from app.ai.tailor_cv import plan_tailoring
    c = MagicMock()
    c.chat.completions.create.side_effect = openai.APIConnectionError(
        request=httpx.Request("POST", "http://x"))
    with pytest.raises(openai.APIConnectionError):
        plan_tailoring(c, "m", "base", "job", "")
```

And in `tests/test_refine.py` or an assess-fit test, confirm a **malformed-JSON**
response still degrades (does not raise) — add one if none exists:

```python
def test_assess_fit_tolerates_non_json_response():
    from unittest.mock import MagicMock
    from app.ai.assess_fit import assess_fit
    c = MagicMock()
    choice = MagicMock(); choice.message.content = "sorry, I cannot"
    c.chat.completions.create.return_value = MagicMock(choices=[choice])
    out = assess_fit(c, "m", "profile", "summary")
    assert out["interest"] == 0.0  # degraded, not raised
```

- [ ] **Step 6: Run — expect failure**

Run: `python -m pytest tests/test_tailor_cv_generate.py -k propagates_connection -q`
Expected: FAIL — currently returns `{"directives": []}`.

- [ ] **Step 7: Convert the 12 call sites**

For each site, the transform is identical in shape:

**Pattern A — create + parse share one `try`** (`classify`, `evaluate`, `assess_fit`,
`summarize`, `refine`, `refine_profile`, `plan_tailoring`, `check_guardrails`,
`revisit_check`, `generate_source_name`): pull the `create()` call out to `complete()`
*above* the `try`; keep only parsing inside.

Example — `app/ai/classify.py`:

```python
from app.ai._client import complete
# ...
    system = _SYSTEM + (_SLACK_HINT if is_slack else "")
    content = complete(
        client, model,
        [{"role": "system", "content": system},
         {"role": "user", "content": simplified_content[:4000]}],
        temperature=0, max_tokens=120,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    try:
        data = json.loads(extract_json(content))
        content_type = data.get("type", "error")
        if content_type not in ("job_posting", "lead", "irrelevant", "error"):
            content_type = "error"
        return content_type, data.get("reason", "")
    except Exception as exc:
        return "error", str(exc)
```

Example — `app/ai/tailor_cv.py::plan_tailoring`: replace the whole
`try: resp = client.chat.completions.create(...) ... except Exception:` with
`content = complete(client, model, [...], temperature=0, extra_body=...)` then
`try: data = json.loads(extract_json(content)) ... except Exception: logger.warning(...); return {"directives": []}`.

**Pattern B — create already in its own `try` returning degraded** (`detect_listing`,
`tailor_cv.tailor_cv`): delete that `try/except` entirely and call
`content = complete(...)`; the parse code below already handles a bad `content`.

Example — `app/ai/tailor_cv.py::tailor_cv` (lines ~202-216):

```python
    content = complete(
        client, model,
        [{"role": "system", "content": _tailor_system(guardrails)},
         {"role": "user", "content": user}],
        temperature=temperature, max_tokens=max_tokens,
        extra_body={"chat_template_kwargs": {"enable_thinking": think}},
    )
    md = _unwrap(content)
    if not md:
        raise RuntimeError("tailor_cv: model returned empty content")
    return {"markdown": md}
```

Update `revisit_check`'s docstring: "A failed or unparseable call returns
`("unchanged", ...)`" → "An unparseable response returns `("unchanged", ...)`; a
transport / API failure raises and fails the task."

Keep every `messages=[...]`, `temperature`, `max_tokens`, `extra_body`, and `[:N]`
truncation exactly as it was — only the call mechanism changes.

- [ ] **Step 8: Run the call-site tests**

Run: `python -m pytest tests/test_tailor_cv_generate.py tests/test_tailor_cv_check.py tests/test_refine.py -q`
Expected: PASS (existing `_mock_client` tests still pass — `MagicMock` `.choices[0].message.content` still works through `complete`).

- [ ] **Step 9: Full suite**

Run: `python -m pytest -q`
Expected: all pass. Any test that asserted a degraded result on a **raised** client
error must flip to `pytest.raises`.

- [ ] **Step 10: Commit**

```bash
/usr/bin/git add app/ai/ tests/test_ai_client.py tests/test_tailor_cv_generate.py tests/test_refine.py tests/
/usr/bin/git commit -m "fix: an unreachable/failing inference endpoint fails the task (#8)"
```

---

## Task 10: Config-toggled LLM tracing to Phoenix (#6)

**Files:**
- Modify: `pyproject.toml` (3 deps)
- Modify: `app/config.py` (`Config` fields; `load_config`; `write_config` carry-forward)
- Create: `app/tracing.py`
- Modify: `app/main.py` (lifespan)
- Modify: `config-template.toml`, `config-container-template.toml`
- Test: `tests/test_tracing.py` (new), `tests/test_config.py`

**Interfaces:**
- Produces: `Config.tracing_endpoint: str | None`, `Config.tracing_project: str`
  (default `"job-seek"`).
- Produces: `app.tracing.init_tracing(config: Config) -> None` — no-op (imports nothing
  from OpenTelemetry) when `config.tracing_endpoint` is falsy; otherwise sets up an OTLP
  HTTP span exporter to that endpoint and calls
  `OpenAIInstrumentor().instrument()`. Idempotent via a module-level `_initialized` flag.

- [ ] **Step 1: Add dependencies**

In `pyproject.toml` `dependencies`:

```toml
    "openinference-instrumentation-openai>=0.1",
    "opentelemetry-sdk>=1.20",
    "opentelemetry-exporter-otlp-proto-http>=1.20",
```

Then: `python -m pip install -e .` (or the project's usual install) so the imports resolve.
If the environment can't install (read-only), note it and mock the imports in tests —
`init_tracing`'s guard means nothing imports them unless enabled.

- [ ] **Step 2: Write the failing config test**

In `tests/test_config.py`:

```python
def test_tracing_absent_by_default(tmp_path):
    from app.config import load_config
    p = tmp_path / "c.toml"
    p.write_text('[llm]\nprovider="custom"\nendpoint="http://x"\n\n'
                 '[database]\npath="j.db"\n\n[browser]\nprofile_dir="j"\n')
    cfg = load_config(str(p))
    assert cfg.tracing_endpoint is None
    assert cfg.tracing_project == "job-seek"

def test_tracing_section_loaded(tmp_path):
    from app.config import load_config
    p = tmp_path / "c.toml"
    p.write_text('[llm]\nprovider="custom"\nendpoint="http://x"\n\n'
                 '[database]\npath="j.db"\n\n[browser]\nprofile_dir="j"\n\n'
                 '[tracing]\nendpoint="http://localhost:6006/v1/traces"\nproject_name="js-dev"\n')
    cfg = load_config(str(p))
    assert cfg.tracing_endpoint == "http://localhost:6006/v1/traces"
    assert cfg.tracing_project == "js-dev"

def test_write_config_preserves_tracing(tmp_path):
    from app.config import write_config
    p = tmp_path / "c.toml"
    p.write_text('[llm]\nprovider="custom"\nendpoint="http://x"\n\n'
                 '[database]\npath="j.db"\n\n[browser]\nprofile_dir="j"\n\n'
                 '[tracing]\nendpoint="http://localhost:6006/v1/traces"\n')
    write_config(str(p), provider="openai", api_key="k", model="gpt-4o")
    assert "[tracing]" in p.read_text()
    assert "localhost:6006" in p.read_text()
```

- [ ] **Step 3: Run — expect failure**

Run: `python -m pytest tests/test_config.py -k tracing -q`
Expected: FAIL.

- [ ] **Step 4: Load `[tracing]` in `config.py`**

Add to `Config`:

```python
    tracing_endpoint: str | None = None
    tracing_project: str = "job-seek"
```

In `load_config`, after reading `raw`:

```python
    tracing = raw.get("tracing", {})
    tracing_endpoint = tracing.get("endpoint") or None
    tracing_project = tracing.get("project_name") or "job-seek"
```

and pass both into the `Config(...)` constructor.

In `write_config`, alongside the block that preserves `db_path` / `browser_profile_dir`
from an existing file, capture `tracing_raw = raw.get("tracing", {})`, and when emitting
`lines`, after the `[browser]` block, if `tracing_raw.get("endpoint")`:

```python
    if tracing_raw.get("endpoint"):
        lines.append("[tracing]")
        lines.append(f'endpoint = {json.dumps(tracing_raw["endpoint"])}')
        if tracing_raw.get("project_name"):
            lines.append(f'project_name = {json.dumps(tracing_raw["project_name"])}')
        lines.append("")
```

(Initialise `tracing_raw = {}` next to the other defaults so the no-existing-file path is safe.)

- [ ] **Step 5: Run — expect pass**

Run: `python -m pytest tests/test_config.py -k tracing -q`
Expected: PASS

- [ ] **Step 6: Write the failing tracing test**

Create `tests/test_tracing.py`:

```python
import sys
from unittest.mock import MagicMock
from app.config import Config
import app.tracing as tr


def _cfg(**kw):
    base = dict(llm_endpoint="http://x", llm_model="m", llm_api_key="k",
                browser_profile_dir="j", db_path="j.db")
    base.update(kw)
    return Config(**base)


def test_init_tracing_noop_without_endpoint(monkeypatch):
    tr._initialized = False
    called = []
    monkeypatch.setattr(tr, "_setup", lambda cfg: called.append(cfg))
    tr.init_tracing(_cfg(tracing_endpoint=None))
    assert called == []


def test_init_tracing_sets_up_with_endpoint(monkeypatch):
    tr._initialized = False
    called = []
    monkeypatch.setattr(tr, "_setup", lambda cfg: called.append(cfg))
    tr.init_tracing(_cfg(tracing_endpoint="http://localhost:6006/v1/traces"))
    assert len(called) == 1


def test_init_tracing_idempotent(monkeypatch):
    tr._initialized = False
    called = []
    monkeypatch.setattr(tr, "_setup", lambda cfg: called.append(cfg))
    cfg = _cfg(tracing_endpoint="http://x/v1/traces")
    tr.init_tracing(cfg)
    tr.init_tracing(cfg)
    assert len(called) == 1
```

- [ ] **Step 7: Run — expect failure**

Run: `python -m pytest tests/test_tracing.py -q`
Expected: FAIL — module missing.

- [ ] **Step 8: Implement `app/tracing.py`**

```python
from __future__ import annotations
import logging
from app.config import Config

logger = logging.getLogger("job_seek")
_initialized = False


def _setup(config: Config) -> None:
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from openinference.instrumentation.openai import OpenAIInstrumentor

    provider = TracerProvider(
        resource=Resource.create({"service.name": config.tracing_project})
    )
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=config.tracing_endpoint))
    )
    trace.set_tracer_provider(provider)
    OpenAIInstrumentor().instrument()


def init_tracing(config: Config) -> None:
    """Enable OpenInference tracing of LLM calls when [tracing].endpoint is set.
    No-op (and imports nothing) otherwise. Safe to call more than once."""
    global _initialized
    if _initialized or not config.tracing_endpoint:
        return
    try:
        _setup(config)
        _initialized = True
        logger.info("LLM tracing enabled → %s", config.tracing_endpoint)
    except Exception:
        logger.warning("LLM tracing setup failed; continuing without it", exc_info=True)
```

- [ ] **Step 9: Run — expect pass**

Run: `python -m pytest tests/test_tracing.py -q`
Expected: PASS

- [ ] **Step 10: Wire into `main.py`**

In `app/main.py` `lifespan`, before starting the worker thread:

```python
    try:
        from app.config import load_config
        from app.tracing import init_tracing
        init_tracing(load_config())
    except Exception:
        logging.getLogger("job_seek").warning("tracing init skipped", exc_info=True)
```

(The `try` also covers a missing `config.toml` at startup — the app still boots to its
setup screen.)

- [ ] **Step 11: Template stubs**

Append to `config-template.toml` and `config-container-template.toml`:

```toml

# [tracing]
# endpoint = "http://localhost:6006/v1/traces"   # OTLP-HTTP collector (e.g. Phoenix); omit to disable
# project_name = "job-seek"
```

- [ ] **Step 12: Full suite**

Run: `python -m pytest -q`
Expected: all pass.

- [ ] **Step 13: Commit**

```bash
/usr/bin/git add pyproject.toml uv.lock app/config.py app/tracing.py app/main.py config-template.toml config-container-template.toml tests/test_config.py tests/test_tracing.py
/usr/bin/git commit -m "feat: optional OpenInference tracing of LLM calls, config-toggled (#6)"
```

(Include `uv.lock` only if the install regenerated it.)

---

## Task 11: Supported job boards doc (#12)

**Files:**
- Create: `docs/job-boards.md`
- Modify: `README.md`

**Interfaces:** none.

- [ ] **Step 1: Gather the facts**

Read `app/fetchers/` (`finn.py`/`finn` fetcher, `eawork.py`, `generic_listing.py`,
`slack.py`, `playwright_base.py`, `listing_detect.py`) and `app/url_rewrite.py`. Note for
each: fetcher key, what sites it targets, an example URL (check tests for real ones —
`tests/test_fetcher_*.py`), and known limits (e.g. `generic_listing` page-1 cap on Sopra
Steria / Tieto, per `BACKLOG.md` #10).

- [ ] **Step 2: Write `docs/job-boards.md`**

Short intro sentence, then a table:

```markdown
# Supported job boards

What the fetchers in `app/fetchers/` currently handle. Add a source by URL — Job Seek
picks the fetcher automatically (see source-type detection).

| Board / site | Fetcher | Example URL | Notes |
|---|---|---|---|
| finn.no | `finn` | https://www.finn.no/job/fulltime/search.html?... | Paginated. |
| 80,000 Hours | `eawork` | https://jobs.80000hours.org/... | — |
| Generic listing pages | `generic_listing` | (any listing page with ≥2 job links) | JS-pager sites (Sopra Steria, Tieto) currently cap at the first page (~10 postings) — see BACKLOG. |
| Slack community channels | `slack` | (channel URL + `d` cookie) | Cookie-only; no browser needed. |
| Arbitrary detail pages | Playwright fallback | any single posting URL | Used when static fetch yields too little text. |
| LinkedIn | URL rewrite → `generic_listing` | https://www.linkedin.com/jobs/search/?... | Search URLs are rewritten to a fetchable form. |
```

Fill in real example URLs from the fetcher tests. Keep it terse (per project doc style).

- [ ] **Step 3: Link from README**

Add a line under the relevant README section (e.g. "Sources" / "Fetching"):
`See [docs/job-boards.md](docs/job-boards.md) for the list of supported job boards.`

- [ ] **Step 4: Commit**

```bash
/usr/bin/git add docs/job-boards.md README.md
/usr/bin/git commit -m "docs: list supported job boards (#12)"
```

---

## Final: prune the backlog

- [ ] Remove the shipped items (#1, #2, #3, #4, #5, #6, #7a, #7b/#9, #8, #12) from
  `BACKLOG.md`. Leave `generic_listing` pagination (#10) and the MCP server (#11).
  If #7 was only partially covered, reword the remaining part instead of deleting.
- [ ] Commit:

```bash
/usr/bin/git add BACKLOG.md
/usr/bin/git commit -m "chore: prune backlog items shipped in roundup 2"
```

---

## Self-Review Notes

- **Spec coverage:** A1→T1, A2→T2, A3→T3, A4→T4, A5→T5, B1→T6, B2→T7+T8, C1→T9, C2→T10,
  C3→T11. All spec sections mapped.
- **`_results()` `href: None`:** T8 introduces summary rows with no link; T8 Step 3
  explicitly patches `tasks/detail.html` and flags the status-bar JS — do not skip that.
- **Type consistency:** `complete(client, model, messages, **kwargs) -> str` used
  identically in T9 across all sites. `duration(start, end) -> str` filter called
  `{{ start | duration(end) }}` in T4. `set_job_note(conn, job_id, note)` in T1.
  `result["outcome"] = {"total", "changed", "closed"}` produced in T7, consumed in T8.
- **`q_filter`:** added to `_filter_from_bulk_form` as the last (defaulted) param in T6 so
  the two other callers (`bulk-actions-cancel`) that don't pass it stay valid.
- **Ordering:** T1–T5 and T11 independent. T6 independent. T7 must precede T8. T9, T10
  independent of everything. Recommended order = task order.
