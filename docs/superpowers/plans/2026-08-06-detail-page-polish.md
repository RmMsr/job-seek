# Detail Page Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Four small, independent fixes to the job detail page and the Fetch page: auto-save scenario feedback on action-button click, refresh nav counts inline after a single-job Accept/Reject/Invalid, retain the job-level note on "Reset to new", and tidy up the Fetch page (drop repeated URL, add lifetime stats per source).

**Architecture:** Each fix touches a narrow, independent vertical slice (one route + one or two templates, or one query function). No shared new abstractions are introduced. Tasks can be done in any order; there are no cross-task dependencies.

**Tech Stack:** FastAPI + Jinja2 templates, htmx 1.9.12 (out-of-band swaps, `hx-trigger`), sqlite3 (raw SQL via `app/db/queries.py`), pytest + `TestClient` for route/integration tests.

## Global Constraints

- Follow existing code conventions exactly: inline `style="..."` attributes in table markup (not new CSS classes), `_rows_to_dicts`/`_row_to_dict` helpers for query results, `conn.commit()` after every write in `queries.py`.
- No new dependencies, no new test frameworks. This project has no JS test runner — any JS-only behavior is verified manually in-browser, not via pytest.
- Commit after each task, per project convention (`CLAUDE.md`: "Commit after each completed implementation increment").

---

### Task 1: Auto-save scenario feedback when an action button is pressed

**Files:**
- Modify: `app/templates/base.html:328-346` (last `<script>` block — add a new sibling `<script>` block right after it, before `</body>`)

**Interfaces:**
- Consumes: existing DOM structure — `.feedback-form .actions button[type="submit"]` (Accept/Reject/Invalid buttons in `app/templates/jobs/_feedback.html:56-60`), `.job-row` (`app/templates/jobs/_feedback.html:2` and `app/templates/jobs/_row.html`), `.scenario-feedback-form` (`app/templates/jobs/_score_tabs.html:3`). No changes to any of these files.
- Produces: nothing consumed by later tasks.

No automated test is possible for this task — it's a pure client-side event listener with no server-side behavior change, and this project has no JS test runner (verified: no `*.test.js`, `jest.config.*`, or `playwright.config.*` in the repo, and the existing analogous `data-progress-url` / `.direction-btn` handlers in `base.html` have no automated tests either). Verify manually in-browser instead (Step 3).

- [ ] **Step 1: Add the delegated click listener**

Insert this new `<script>` block into `app/templates/base.html`, immediately after the existing block that ends at line 346 (the `.direction-btn` listener) and before `</body>`:

```html
  <script>
document.body.addEventListener('click', function (e) {
  var btn = e.target.closest('.feedback-form .actions button[type="submit"]');
  if (!btn) return;
  var row = btn.closest('.job-row');
  var scenarioForm = row && row.querySelector('.scenario-feedback-form');
  if (scenarioForm && window.htmx) {
    htmx.trigger(scenarioForm, 'submit');
  }
});
  </script>
```

This fires whenever an Accept/Reject/Invalid button is clicked. It looks up the sibling scenario-feedback form within the same job row and, if present, submits it via `htmx.trigger` — fire-and-forget, no waiting on its response, since it writes to a different DB table (`scenario_feedback`) than the job's own status update in `/jobs/{id}/feedback`, so there's no ordering dependency. It's safe to fire even when nothing changed: `upsert_scenario_feedback` (`app/db/queries.py:229-260`) is idempotent when resubmitted with the pre-filled, unmodified values.

- [ ] **Step 2: Run the full test suite to confirm nothing broke**

Run: `uv run pytest -q`
Expected: `324 passed` (same count as baseline — this task changes no Python code and no existing template assertions).

- [ ] **Step 3: Manually verify in browser**

Use the `run-dev-server` skill to start the dev server against a throwaway copy of `job-seek.db`. In the browser:
1. Go to `/jobs`, expand a job that has scenario scores (has a score-tabs section).
2. Click "▲ Should score higher" on one scenario tab and type a note, but do **not** click "Save all feedback".
3. Click "Accept" (or "Reject"/"Invalid").
4. Reload `/jobs?status=accepted` (or the relevant filter) and re-expand the same job — confirm the scenario tab still shows the direction and note you entered (i.e. it wasn't lost).

Stop the dev server once verified.

- [ ] **Step 4: Commit**

```bash
git add app/templates/base.html
git commit -m "feat: auto-save scenario feedback when an action button is pressed"
```

---

### Task 2: Nav count update on action button press

**Files:**
- Modify: `app/templates/jobs/_content.html:3-8` (wrap each count number in a `<span id="count-...">`)
- Create: `app/templates/jobs/_counts_oob.html` (out-of-band count spans, reused by the feedback route)
- Modify: `app/routes/jobs.py:69-78` (`job_feedback` — return the OOB spans instead of an empty response)
- Modify: `tests/test_routes_jobs.py:59-67` (`test_job_list_filter_bar_shows_counts` — update literal-text assertions to match the new span markup)
- Modify: `tests/test_routes_jobs.py:609` (`test_job_list_shows_not_relevant_tab_and_drops_show_filtered` — same)
- Modify: `tests/test_routes_jobs.py` (add a new test for the OOB response)

**Interfaces:**
- Consumes: `q.get_job_counts(conn) -> dict[str, int]` (`app/db/queries.py:375`, keys: `new`, `accepted`, `rejected`, `invalid`, `lead`, `not_relevant`) — already used elsewhere, unchanged.
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Write the failing tests**

In `tests/test_routes_jobs.py`, replace `test_job_list_filter_bar_shows_counts` (currently lines 59-67) with:

```python
def test_job_list_filter_bar_shows_counts(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get("/jobs")
    assert resp.status_code == 200
    assert '<span id="count-new">1</span>' in resp.text
    assert '<span id="count-accepted">0</span>' in resp.text
    assert '<span id="count-rejected">0</span>' in resp.text
    assert '<span id="count-invalid">0</span>' in resp.text
    assert '<span id="count-lead">0</span>' in resp.text
```

In the same file, in `test_job_list_shows_not_relevant_tab_and_drops_show_filtered` (currently around line 609), change:

```python
    assert "Not relevant (1)" in resp.text
```

to:

```python
    assert '<span id="count-not_relevant">1</span>' in resp.text
```

Add this new test at the end of the file:

```python
def test_job_feedback_updates_counts_oob(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.post(f"/jobs/{jid}/feedback", data={"status": "accepted", "note": ""})
    assert resp.status_code == 200
    assert '<span id="count-new" hx-swap-oob="true">0</span>' in resp.text
    assert '<span id="count-accepted" hx-swap-oob="true">1</span>' in resp.text
    assert '<span id="count-rejected" hx-swap-oob="true">0</span>' in resp.text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_routes_jobs.py -k "counts_oob or filter_bar_shows_counts or not_relevant_tab" -v`
Expected: `test_job_list_filter_bar_shows_counts` and `not_relevant_tab` FAIL (markup doesn't have the spans yet), `test_job_feedback_updates_counts_oob` FAILs with an `AssertionError` (response body is currently empty).

- [ ] **Step 3: Wrap the counts in spans**

In `app/templates/jobs/_content.html`, replace lines 3-8:

```html
    <a href="/jobs" {% if status == "new" and not content_type %}class="active"{% endif %}>New ({{ counts.new }})</a>
    <a href="/jobs?status=accepted" {% if status == "accepted" %}class="active"{% endif %}>Accepted ({{ counts.accepted }})</a>
    <a href="/jobs?status=rejected" {% if status == "rejected" %}class="active"{% endif %}>Rejected ({{ counts.rejected }})</a>
    <a href="/jobs?content_type=lead" {% if content_type == "lead" %}class="active"{% endif %}>Leads ({{ counts.lead }})</a>
    <a href="/jobs?status=not_relevant" {% if status == "not_relevant" %}class="active"{% endif %}>Not relevant ({{ counts.not_relevant }})</a>
    <a href="/jobs?status=invalid" {% if status == "invalid" %}class="active"{% endif %}>Invalid ({{ counts.invalid }})</a>
```

with:

```html
    <a href="/jobs" {% if status == "new" and not content_type %}class="active"{% endif %}>New (<span id="count-new">{{ counts.new }}</span>)</a>
    <a href="/jobs?status=accepted" {% if status == "accepted" %}class="active"{% endif %}>Accepted (<span id="count-accepted">{{ counts.accepted }}</span>)</a>
    <a href="/jobs?status=rejected" {% if status == "rejected" %}class="active"{% endif %}>Rejected (<span id="count-rejected">{{ counts.rejected }}</span>)</a>
    <a href="/jobs?content_type=lead" {% if content_type == "lead" %}class="active"{% endif %}>Leads (<span id="count-lead">{{ counts.lead }}</span>)</a>
    <a href="/jobs?status=not_relevant" {% if status == "not_relevant" %}class="active"{% endif %}>Not relevant (<span id="count-not_relevant">{{ counts.not_relevant }}</span>)</a>
    <a href="/jobs?status=invalid" {% if status == "invalid" %}class="active"{% endif %}>Invalid (<span id="count-invalid">{{ counts.invalid }}</span>)</a>
```

- [ ] **Step 4: Create the OOB counts partial**

Create `app/templates/jobs/_counts_oob.html`:

```html
<span id="count-new" hx-swap-oob="true">{{ counts.new }}</span>
<span id="count-accepted" hx-swap-oob="true">{{ counts.accepted }}</span>
<span id="count-rejected" hx-swap-oob="true">{{ counts.rejected }}</span>
<span id="count-lead" hx-swap-oob="true">{{ counts.lead }}</span>
<span id="count-not_relevant" hx-swap-oob="true">{{ counts.not_relevant }}</span>
<span id="count-invalid" hx-swap-oob="true">{{ counts.invalid }}</span>
```

- [ ] **Step 5: Return the OOB partial from `job_feedback`**

In `app/routes/jobs.py`, replace the `job_feedback` function (currently lines 69-78):

```python
@router.post("/jobs/{job_id}/feedback", response_class=HTMLResponse)
def job_feedback(
    job_id: int,
    request: Request,
    status: str = Form(...),
    note: str | None = Form(None),
    conn: sqlite3.Connection = Depends(get_db),
):
    q.update_job_feedback(conn, job_id, status, note)
    return HTMLResponse(content="", status_code=200)
```

with:

```python
@router.post("/jobs/{job_id}/feedback", response_class=HTMLResponse)
def job_feedback(
    job_id: int,
    request: Request,
    status: str = Form(...),
    note: str | None = Form(None),
    conn: sqlite3.Connection = Depends(get_db),
):
    q.update_job_feedback(conn, job_id, status, note)
    counts = q.get_job_counts(conn)
    return templates.TemplateResponse(request, "jobs/_counts_oob.html", {"counts": counts})
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_routes_jobs.py -v`
Expected: all tests in the file PASS.

- [ ] **Step 7: Run the full test suite**

Run: `uv run pytest -q`
Expected: `324 passed` (no new tests were skipped or removed — same count as baseline plus... note: this task adds 1 new test and modifies 2 existing ones without adding/removing net test count elsewhere, so if the printed count differs from 324, confirm it's `325 passed` and that the delta is exactly the one new test added in Step 1).

- [ ] **Step 8: Commit**

```bash
git add app/templates/jobs/_content.html app/templates/jobs/_counts_oob.html app/routes/jobs.py tests/test_routes_jobs.py
git commit -m "feat: refresh nav counts inline after a single-job feedback action"
```

---

### Task 3: "Reset to new" retains the job-level note

**Files:**
- Modify: `app/db/queries.py:278-297` (`reset_job` — stop clearing `feedback_note`)
- Modify: `tests/test_queries.py:236-263` (`test_reset_job_clears_pipeline_output_and_scores` — update the assertion for `feedback_note`)

**Interfaces:**
- Consumes: nothing new.
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Update the test to assert the note survives**

In `tests/test_queries.py`, in `test_reset_job_clears_pipeline_output_and_scores`, change:

```python
    assert job["feedback_note"] is None
```

to:

```python
    assert job["feedback_note"] == "note"
```

(The test already calls `q.update_job_feedback(conn, jid, "accepted", "note")` before `q.reset_job(conn, jid)` — see line 247 — so `"note"` is the value that must survive the reset.)

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_queries.py::test_reset_job_clears_pipeline_output_and_scores -v`
Expected: FAIL — `assert None == 'note'`.

- [ ] **Step 3: Stop clearing `feedback_note` in `reset_job`**

In `app/db/queries.py`, in `reset_job`, remove the `feedback_note = NULL,` line from the `UPDATE jobs SET ...` statement. Before:

```python
def reset_job(conn: sqlite3.Connection, job_id: int) -> None:
    conn.execute(
        """UPDATE jobs SET
            status = 'new',
            content_type = NULL,
            simplified_content = '',
            summary = '',
            headline = '',
            feedback_note = NULL,
            feedback_handled_at = NULL,
            interest_score = NULL,
            interest_reasoning = NULL,
            attainability_score = NULL,
            attainability_reasoning = NULL,
            fit_score = NULL,
            profile_version_hash = NULL
        WHERE id = ?""",
        (job_id,),
    )
```

After:

```python
def reset_job(conn: sqlite3.Connection, job_id: int) -> None:
    conn.execute(
        """UPDATE jobs SET
            status = 'new',
            content_type = NULL,
            simplified_content = '',
            summary = '',
            headline = '',
            feedback_handled_at = NULL,
            interest_score = NULL,
            interest_reasoning = NULL,
            attainability_score = NULL,
            attainability_reasoning = NULL,
            fit_score = NULL,
            profile_version_hash = NULL
        WHERE id = ?""",
        (job_id,),
    )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_queries.py::test_reset_job_clears_pipeline_output_and_scores -v`
Expected: PASS.

- [ ] **Step 5: Run the full test suite**

Run: `uv run pytest -q`
Expected: `324 passed`.

- [ ] **Step 6: Commit**

```bash
git add app/db/queries.py tests/test_queries.py
git commit -m "fix: retain job-level note when resetting a job to new"
```

---

### Task 4: Fetch page — drop repeated URL, add lifetime stats

**Files:**
- Modify: `app/db/queries.py` (add `get_fetch_stats_by_source`, placed after `get_recent_fetch_runs` at line 489)
- Modify: `app/routes/fetch.py:14-27` (`fetch_panel` — fetch and pass `stats_by_source`)
- Modify: `app/templates/fetch/panel.html` (drop the URL line, add a "Lifetime" column)
- Test: `tests/test_queries.py` (new tests for `get_fetch_stats_by_source`)
- Test: `tests/test_routes_fetch.py` (new tests for the panel's rendered output)

**Interfaces:**
- Consumes: `fetch_runs` table (`source_id`, `jobs_found`, `jobs_new`, `completed_at`, `error` — `app/db/schema.py:70-78`); `q.start_fetch_run` / `q.complete_fetch_run` (`app/db/queries.py:456-479`, unchanged, used by the new tests to seed data).
- Produces: `get_fetch_stats_by_source(conn: sqlite3.Connection) -> dict[int, dict]`, keyed by `source_id`, each value a dict with keys `run_count: int`, `total_new: int`, `total_found: int`, `last_success_at: str | None`. A source with zero fetch runs is **absent** from the returned dict (not present with zeroed-out values) — callers must use `.get(source_id)` and handle `None`.

- [ ] **Step 1: Write the failing query tests**

Add to `tests/test_queries.py`:

```python
def test_get_fetch_stats_by_source_aggregates_runs(conn):
    sid = q.insert_source(conn, "s", "http://x", "http")
    run1 = q.start_fetch_run(conn, sid)
    q.complete_fetch_run(conn, run1, jobs_found=3, jobs_new=2)
    run2 = q.start_fetch_run(conn, sid)
    q.complete_fetch_run(conn, run2, jobs_found=1, jobs_new=0, error="boom")

    stats = q.get_fetch_stats_by_source(conn)

    assert stats[sid]["run_count"] == 2
    assert stats[sid]["total_new"] == 2
    assert stats[sid]["total_found"] == 4
    assert stats[sid]["last_success_at"] is not None


def test_get_fetch_stats_by_source_omits_sources_with_no_runs(conn):
    sid = q.insert_source(conn, "s", "http://x", "http")
    stats = q.get_fetch_stats_by_source(conn)
    assert sid not in stats
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_queries.py -k get_fetch_stats_by_source -v`
Expected: FAIL with `AttributeError: module 'app.db.queries' has no attribute 'get_fetch_stats_by_source'`.

- [ ] **Step 3: Implement `get_fetch_stats_by_source`**

In `app/db/queries.py`, add this function immediately after `get_recent_fetch_runs` (which ends at line 489, right before `has_completed_fetch_run`):

```python
def get_fetch_stats_by_source(conn: sqlite3.Connection) -> dict[int, dict]:
    rows = conn.execute(
        """
        SELECT source_id,
               COUNT(*) AS run_count,
               SUM(jobs_new) AS total_new,
               SUM(jobs_found) AS total_found,
               MAX(CASE WHEN error IS NULL THEN completed_at END) AS last_success_at
        FROM fetch_runs
        GROUP BY source_id
        """
    ).fetchall()
    return {row["source_id"]: dict(row) for row in rows}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_queries.py -k get_fetch_stats_by_source -v`
Expected: PASS.

- [ ] **Step 5: Write the failing route tests**

Add to `tests/test_routes_fetch.py`:

```python
def test_fetch_panel_shows_lifetime_stats(client, conn):
    sid = _seed(conn)
    run = q.start_fetch_run(conn, sid)
    q.complete_fetch_run(conn, run, jobs_found=3, jobs_new=2)
    resp = client.get("/fetch")
    assert resp.status_code == 200
    assert "2 new" in resp.text
    assert "1 run" in resp.text


def test_fetch_panel_does_not_repeat_source_url(client, conn):
    _seed(conn)
    resp = client.get("/fetch")
    assert resp.status_code == 200
    assert "https://finn.no" not in resp.text
```

- [ ] **Step 6: Run the tests to verify they fail**

Run: `uv run pytest tests/test_routes_fetch.py -v`
Expected: `test_fetch_panel_shows_lifetime_stats` FAILs (no "Lifetime" markup yet), `test_fetch_panel_does_not_repeat_source_url` FAILs (`https://finn.no` is still on the page).

- [ ] **Step 7: Pass `stats_by_source` from the route**

In `app/routes/fetch.py`, replace `fetch_panel` (currently lines 14-27):

```python
@router.get("/fetch", response_class=HTMLResponse)
def fetch_panel(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    sources = q.get_sources(conn)
    runs = q.get_recent_fetch_runs(conn)
    runs_by_source = {}
    for run in runs:
        sid = run["source_id"]
        if sid not in runs_by_source:
            runs_by_source[sid] = run
    return templates.TemplateResponse(
        request,
        "fetch/panel.html",
        {"sources": sources, "runs_by_source": runs_by_source},
    )
```

with:

```python
@router.get("/fetch", response_class=HTMLResponse)
def fetch_panel(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    sources = q.get_sources(conn)
    runs = q.get_recent_fetch_runs(conn)
    runs_by_source = {}
    for run in runs:
        sid = run["source_id"]
        if sid not in runs_by_source:
            runs_by_source[sid] = run
    stats_by_source = q.get_fetch_stats_by_source(conn)
    return templates.TemplateResponse(
        request,
        "fetch/panel.html",
        {"sources": sources, "runs_by_source": runs_by_source, "stats_by_source": stats_by_source},
    )
```

- [ ] **Step 8: Update the template**

In `app/templates/fetch/panel.html`, remove the URL line (currently line 21):

```html
          <strong>{{ source.name }}</strong><br>
          <small>{{ source.url }}</small>
```

becomes:

```html
          <strong>{{ source.name }}</strong>
```

Add a "Lifetime" column header, after the existing "New / Found" header (currently line 11):

```html
        <th style="text-align:left; padding:0.5rem; border-bottom:2px solid #dee2e6;">New / Found</th>
```

becomes:

```html
        <th style="text-align:left; padding:0.5rem; border-bottom:2px solid #dee2e6;">New / Found</th>
        <th style="text-align:left; padding:0.5rem; border-bottom:2px solid #dee2e6;">Lifetime</th>
```

Add the corresponding cell, after the existing "New / Found" cell (currently lines 27-29):

```html
        <td style="padding:0.5rem;">
          {% if run %}{{ run.jobs_new }} / {{ run.jobs_found }}{% else %}—{% endif %}
        </td>
```

becomes:

```html
        <td style="padding:0.5rem;">
          {% if run %}{{ run.jobs_new }} / {{ run.jobs_found }}{% else %}—{% endif %}
        </td>
        <td style="padding:0.5rem;">
          {% set stats = stats_by_source.get(source.id) %}
          {% if stats %}
            {{ stats.run_count }} run{{ 's' if stats.run_count != 1 else '' }}, {{ stats.total_new }} new
            {% if stats.last_success_at %}<br><small>last success {{ stats.last_success_at }}</small>{% endif %}
          {% else %}
            —
          {% endif %}
        </td>
```

- [ ] **Step 9: Run the tests to verify they pass**

Run: `uv run pytest tests/test_routes_fetch.py -v`
Expected: all PASS.

- [ ] **Step 10: Run the full test suite**

Run: `uv run pytest -q`
Expected: all tests PASS (baseline 324 + 2 new query tests + 2 new route tests from this task, plus the +1 from Task 2 — confirm the final count is `329 passed`).

- [ ] **Step 11: Commit**

```bash
git add app/db/queries.py app/routes/fetch.py app/templates/fetch/panel.html tests/test_queries.py tests/test_routes_fetch.py
git commit -m "feat: drop repeated URL and add lifetime stats to the Fetch page"
```

---

### Task 5: Manual verification pass

**Files:** none (verification only).

- [ ] **Step 1: Start the dev server**

Use the `run-dev-server` skill (throwaway copy of `job-seek.db`).

- [ ] **Step 2: Verify auto-save of scenario feedback (Task 1)**

On `/jobs`, expand a job that has scenario scores (has a score-tabs section). Click "▲ Should score higher" on one scenario tab and type a note, but do **not** click "Save all feedback". Click "Accept" (or "Reject"/"Invalid"). Reload the page, filter to the status you just set, and re-expand the same job — confirm the scenario tab still shows the direction and note you entered (i.e. it wasn't lost).

- [ ] **Step 3: Verify nav count update (Task 2)**

On `/jobs`, note the "New (N)" count. Expand a job and click Accept. Confirm the row disappears and the "New" count in the filter bar decrements immediately, with no page reload, and the "Accepted" count increments. Confirm the previously-active filter tab (e.g. "New") is still visibly highlighted as active.

- [ ] **Step 4: Verify reset retains the note (Task 3)**

Accept or reject a job with a note in its feedback textarea. Expand it, open "Advanced…", click "Reset to new". After it completes (page reloads), find the job again (now under "New"), expand it, and confirm the note textarea still shows the note you wrote.

- [ ] **Step 5: Verify Fetch page (Task 4)**

Go to `/fetch`. Confirm no source's URL is repeated under its name (only shown once, on `/sources`). Trigger a fetch for a source, then reload `/fetch` and confirm the "Lifetime" column shows a run count and total-new figure that updates after each fetch.

- [ ] **Step 6: Stop the dev server**

Per project convention, stop the dev server once manual testing is complete; the throwaway DB copy is gitignored and discarded with the worktree at cleanup.

## Self-Review Notes

- **Spec coverage:** all 4 spec items map 1:1 to Tasks 1-4. Task 5 covers the CLAUDE.md requirement to manually verify UI changes in-browser before claiming completion.
- **Placeholder scan:** none found — every step has literal code/commands.
- **Type consistency:** `get_fetch_stats_by_source` returns `dict[int, dict]` with keys `run_count`/`total_new`/`total_found`/`last_success_at`, used consistently in Task 4's template and tests. `job_feedback`'s route signature is unchanged (same `Form(...)` params) — only its return value changes.
