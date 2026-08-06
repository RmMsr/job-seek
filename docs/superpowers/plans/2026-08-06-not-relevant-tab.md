# "Not Relevant" Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the job list's "New" tab into "New" and "Not relevant" (gate-failed job postings still awaiting triage), and remove score-gate filtering from every other tab (Accepted, Rejected, Invalid, Leads), replacing the `show_filtered` toggle entirely.

**Architecture:** No schema change. "Not relevant" is a query-time filter over existing `status='new'` rows using the existing `job_scores`/`scenarios.gate_threshold` gate mechanism (`_GATE_SELECT`/`_GATE_JOIN` in `app/db/queries.py`). `get_jobs()`'s boolean `gate_passed_only` param becomes a three-way `gate_status: str | None` (`None` / `"passed"` / `"failed"`), used only for the New/Not-relevant split. `get_job_counts()` gets a `not_relevant` key, derived so `new + not_relevant` always equals the raw `status='new'` total.

**Tech Stack:** Python 3.14, FastAPI, sqlite3 (raw SQL, no ORM), Jinja2 templates, pytest + `fastapi.testclient.TestClient`.

## Global Constraints

- No DB migration — this is a query/route/template-only change.
- `content_type` values are exactly `'job_posting'`, `'lead'`, `'irrelevant'`, `'error'`, or `NULL` (`app/db/schema.py:46`) — only `'job_posting'` and `'lead'` are ever scored (`app/pipeline.py:66`).
- Gate threshold: a scenario "passes" for a job when `job_scores.relevance_score >= scenarios.gate_threshold` (per-scenario, default `0.7`).
- Tab order in the filter bar: New, Accepted, Rejected, Leads, Not relevant, Invalid.
- Leads are never routed into "Not relevant" — a gate-failed lead is only reachable via the Leads tab.
- Run tests with `uv run pytest -q` from the worktree root.

---

### Task 1: Query layer — `gate_status` param on `get_jobs()`

**Files:**
- Modify: `app/db/queries.py:345-365` (`get_jobs`)
- Test: `tests/test_queries.py:451-479` (`test_get_jobs_gate_passed_only_excludes_below_threshold`, `test_get_jobs_gate_passed_only_keeps_never_scored_jobs`)

**Interfaces:**
- Consumes: `_GATE_SELECT`, `_GATE_JOIN` (`app/db/queries.py:310-342`, unchanged), `q.insert_job`, `q.insert_scenario`, `q.update_job_pipeline`, `q.upsert_job_score` (all unchanged, pre-existing).
- Produces: `get_jobs(conn, *, status=None, content_type=None, gate_status=None)` — `gate_status` accepts `None` (no gate filtering), `"passed"` (excludes gate-failed `job_posting`s; everything else passes through), `"failed"` (only scored rows that failed every scenario's gate — caller must pair with `content_type="job_posting"` to exclude leads). Later tasks (routes) call this with these exact keyword names.

- [ ] **Step 1: Replace the two `gate_passed_only` tests with `gate_status="passed"` equivalents, plus a new `gate_status="failed"` test**

In `tests/test_queries.py`, replace lines 451-479 (both `test_get_jobs_gate_passed_only_*` functions) with:

```python
def test_get_jobs_gate_status_passed_excludes_below_threshold(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")  # default gate_threshold 0.7
    below = q.insert_job(conn, source_id=source_id, url="http://job/below", title="Below", company="C", raw_text="r")
    above = q.insert_job(conn, source_id=source_id, url="http://job/above", title="Above", company="C", raw_text="r")
    q.update_job_pipeline(conn, below, simplified_content="", content_type="job_posting")
    q.update_job_pipeline(conn, above, simplified_content="", content_type="job_posting")
    q.upsert_job_score(conn, below, scenario_id, 0.5, "", "hash1")
    q.upsert_job_score(conn, above, scenario_id, 0.9, "", "hash2")

    all_jobs = q.get_jobs(conn)
    assert {j["title"] for j in all_jobs} == {"Below", "Above"}

    passed_only = q.get_jobs(conn, gate_status="passed")
    assert [j["title"] for j in passed_only] == ["Above"]


def test_get_jobs_gate_status_passed_keeps_never_scored_jobs(conn):
    # A job with zero job_scores rows (e.g. no scenarios existed at fetch
    # time) hasn't failed a gate — it was never gated at all — so it must
    # stay visible, unlike a job that was scored and failed every scenario.
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")
    scored_and_failed = q.insert_job(conn, source_id=source_id, url="http://job/failed", title="Failed", company="C", raw_text="r")
    never_scored = q.insert_job(conn, source_id=source_id, url="http://job/unscored", title="Unscored", company="C", raw_text="r")
    q.update_job_pipeline(conn, scored_and_failed, simplified_content="", content_type="job_posting")
    q.upsert_job_score(conn, scored_and_failed, scenario_id, 0.5, "", "hash1")  # below default 0.7

    passed_only = q.get_jobs(conn, gate_status="passed")

    assert [j["title"] for j in passed_only] == ["Unscored"]


def test_get_jobs_gate_status_failed_returns_only_failed_postings(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")  # default gate_threshold 0.7
    failed = q.insert_job(conn, source_id=source_id, url="http://job/failed", title="Failed", company="C", raw_text="r")
    passed = q.insert_job(conn, source_id=source_id, url="http://job/passed", title="Passed", company="C", raw_text="r")
    unscored = q.insert_job(conn, source_id=source_id, url="http://job/unscored", title="Unscored", company="C", raw_text="r")
    q.update_job_pipeline(conn, failed, simplified_content="", content_type="job_posting")
    q.update_job_pipeline(conn, passed, simplified_content="", content_type="job_posting")
    q.upsert_job_score(conn, failed, scenario_id, 0.3, "", "hash1")
    q.upsert_job_score(conn, passed, scenario_id, 0.9, "", "hash2")

    failed_only = q.get_jobs(conn, gate_status="failed")

    assert [j["title"] for j in failed_only] == ["Failed"]


def test_get_jobs_gate_status_failed_paired_with_job_posting_excludes_leads(conn):
    # Route layer always pairs gate_status="failed" with content_type="job_posting"
    # so a gate-failed lead never shows up in "Not relevant" — it's Leads-only.
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")  # default gate_threshold 0.7
    lead = q.insert_job(conn, source_id=source_id, url="http://job/lead", title="Lead", company="C", raw_text="r")
    q.update_job_pipeline(conn, lead, simplified_content="", content_type="lead")
    q.upsert_job_score(conn, lead, scenario_id, 0.3, "", "hash1")

    failed_only = q.get_jobs(conn, content_type="job_posting", gate_status="failed")

    assert failed_only == []
```

- [ ] **Step 2: Run the new/changed tests to verify they fail**

Run: `uv run pytest tests/test_queries.py -k gate_status -v`
Expected: FAIL — `get_jobs() got an unexpected keyword argument 'gate_status'` (the function still only accepts `gate_passed_only`).

- [ ] **Step 3: Implement `gate_status` in `get_jobs()`**

In `app/db/queries.py`, replace lines 345-365 with:

```python
def get_jobs(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    content_type: str | None = None,
    gate_status: str | None = None,
) -> list[dict]:
    clauses, params = [], []
    if status is not None:
        clauses.append("jobs.status = ?")
        params.append(status)
    if content_type is not None:
        clauses.append("jobs.content_type = ?")
        params.append(content_type)
    if gate_status == "passed":
        clauses.append(
            "(jobs.content_type != 'job_posting' OR jobs.content_type IS NULL "
            "OR scored.scored_count IS NULL OR gate.passed_count > 0)"
        )
    elif gate_status == "failed":
        clauses.append(
            "scored.scored_count IS NOT NULL AND (gate.passed_count IS NULL OR gate.passed_count = 0)"
        )
    sql = f"SELECT {_GATE_SELECT} {_GATE_JOIN}"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY jobs.fit_score DESC NULLS LAST, jobs.fetched_at DESC"
    return _rows_to_dicts(conn.execute(sql, params).fetchall())
```

- [ ] **Step 4: Run the full query test file to verify everything passes**

Run: `uv run pytest tests/test_queries.py -v`
Expected: PASS, all tests green (including the 4 gate_status tests and every pre-existing test in the file — nothing else in this file references `gate_passed_only`).

- [ ] **Step 5: Search the codebase for any remaining `gate_passed_only` references**

Run: `grep -rn "gate_passed_only" app/ tests/`
Expected: one remaining match, in `app/routes/jobs.py` (`_get_filtered_jobs`'s call site) — Task 3 updates that caller. There should be no match left in `app/db/queries.py` or `tests/test_queries.py`.

- [ ] **Step 6: Commit**

```bash
git add app/db/queries.py tests/test_queries.py
git commit -m "feat: replace gate_passed_only bool with gate_status on get_jobs"
```

---

### Task 2: Query layer — `not_relevant` in `get_job_counts()`

**Files:**
- Modify: `app/db/queries.py:368-375` (`get_job_counts`)
- Test: `tests/test_queries.py:296-305` (`test_get_job_counts`)

**Interfaces:**
- Consumes: `_GATE_JOIN` (`app/db/queries.py:319-342`, unchanged).
- Produces: `get_job_counts(conn)` returns a dict with keys `new`, `accepted`, `rejected`, `invalid`, `lead`, `not_relevant`. `new` no longer includes gate-failed job postings; `new + not_relevant` always equals the raw `status='new'` row count. Task 3/4 (route/template) rely on `counts["not_relevant"]` existing.

- [ ] **Step 1: Update `test_get_job_counts` and add a split-specific test**

In `tests/test_queries.py`, replace line 305 (`assert counts == {"new": 1, "accepted": 1, "rejected": 1, "invalid": 0, "lead": 1}`) with:

```python
    assert counts == {"new": 1, "accepted": 1, "rejected": 1, "invalid": 0, "lead": 1, "not_relevant": 0}
```

Then add a new test directly after `test_get_job_counts` (after line 305, now line 306 post-edit):

```python
def test_get_job_counts_splits_new_from_not_relevant(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")  # default gate_threshold 0.7
    passed = q.insert_job(conn, source_id=source_id, url="http://job/passed", title="Passed", company="C", raw_text="r")
    failed = q.insert_job(conn, source_id=source_id, url="http://job/failed", title="Failed", company="C", raw_text="r")
    q.update_job_pipeline(conn, passed, simplified_content="", content_type="job_posting")
    q.update_job_pipeline(conn, failed, simplified_content="", content_type="job_posting")
    q.upsert_job_score(conn, passed, scenario_id, 0.9, "", "hash1")
    q.upsert_job_score(conn, failed, scenario_id, 0.3, "", "hash2")

    counts = q.get_job_counts(conn)

    assert counts["new"] == 1
    assert counts["not_relevant"] == 1
    assert counts["new"] + counts["not_relevant"] == 2  # raw status='new' total
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_queries.py -k "get_job_counts" -v`
Expected: FAIL — `test_get_job_counts` fails on the dict equality (missing `not_relevant` key), `test_get_job_counts_splits_new_from_not_relevant` fails with `KeyError: 'not_relevant'`.

- [ ] **Step 3: Implement `not_relevant` in `get_job_counts()`**

In `app/db/queries.py`, replace lines 368-375 with:

```python
def get_job_counts(conn: sqlite3.Connection) -> dict[str, int]:
    counts = {"new": 0, "accepted": 0, "rejected": 0, "invalid": 0, "lead": 0, "not_relevant": 0}
    for status, n in conn.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status").fetchall():
        counts[status] = n
    counts["lead"] = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE content_type = ?", ("lead",)
    ).fetchone()[0]
    not_relevant = conn.execute(
        f"""
        SELECT COUNT(*) {_GATE_JOIN}
        WHERE jobs.status = 'new' AND jobs.content_type = 'job_posting'
          AND scored.scored_count IS NOT NULL
          AND (gate.passed_count IS NULL OR gate.passed_count = 0)
        """
    ).fetchone()[0]
    counts["not_relevant"] = not_relevant
    counts["new"] -= not_relevant
    return counts
```

- [ ] **Step 4: Run the full query test file to verify everything passes**

Run: `uv run pytest tests/test_queries.py -v`
Expected: PASS, all tests green.

- [ ] **Step 5: Commit**

```bash
git add app/db/queries.py tests/test_queries.py
git commit -m "feat: split not_relevant out of the new count in get_job_counts"
```

---

### Task 3: Route layer — `status=not_relevant`, drop `show_filtered`

**Files:**
- Modify: `app/routes/jobs.py:20-48` (`_get_filtered_jobs`, `job_list`), `app/routes/jobs.py:151-177` (`job_bulk_feedback`)
- Test: `tests/test_routes_jobs.py:552-563` (`test_job_list_hides_gate_filtered_jobs_by_default`)

**Interfaces:**
- Consumes: `q.get_jobs(conn, *, status=None, content_type=None, gate_status=None)` and `q.get_job_counts(conn)` from Tasks 1-2.
- Produces: `GET /jobs` and `GET /jobs?status=not_relevant` render the right job sets; `GET /jobs` no longer accepts `show_filtered`; `POST /jobs/bulk-feedback` no longer accepts `show_filtered_filter`. Task 4 (templates) relies on the `status` context var passing through `"not_relevant"` unchanged, same as it already does for `"accepted"`/`"rejected"`/`"invalid"`.

- [ ] **Step 1: Replace `test_job_list_hides_gate_filtered_jobs_by_default` and add coverage for the other tabs losing the gate filter**

In `tests/test_routes_jobs.py`, replace lines 552-563 with:

```python
def test_job_list_new_tab_excludes_gate_failed_postings(client, conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "http")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/1", title="Filtered Out", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="job_posting", summary="role")
    scenario_id = q.insert_scenario(conn, "A", "")  # default gate_threshold 0.7
    q.upsert_job_score(conn, jid, scenario_id, 0.3, "not a fit", "hash1")

    resp = client.get("/jobs")
    assert "Filtered Out" not in resp.text

    resp2 = client.get("/jobs?status=not_relevant")
    assert "Filtered Out" in resp2.text


def test_job_list_not_relevant_tab_excludes_leads(client, conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "http")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/1", title="Low Score Lead", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="lead", summary="role")
    scenario_id = q.insert_scenario(conn, "A", "")  # default gate_threshold 0.7
    q.upsert_job_score(conn, jid, scenario_id, 0.3, "not a fit", "hash1")

    resp = client.get("/jobs?status=not_relevant")
    assert "Low Score Lead" not in resp.text


def test_job_list_leads_tab_includes_gate_failed_leads(client, conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "http")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/1", title="Low Score Lead", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="lead", summary="role")
    scenario_id = q.insert_scenario(conn, "A", "")  # default gate_threshold 0.7
    q.upsert_job_score(conn, jid, scenario_id, 0.3, "not a fit", "hash1")

    resp = client.get("/jobs?content_type=lead")
    assert "Low Score Lead" in resp.text


def test_job_list_accepted_tab_includes_gate_failed_jobs(client, conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "http")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/1", title="Low Score Accepted", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="job_posting", summary="role")
    scenario_id = q.insert_scenario(conn, "A", "")  # default gate_threshold 0.7
    q.upsert_job_score(conn, jid, scenario_id, 0.3, "not a fit", "hash1")
    q.update_job_feedback(conn, jid, "accepted", "")

    resp = client.get("/jobs?status=accepted")
    assert "Low Score Accepted" in resp.text
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_routes_jobs.py -k "gate_failed or not_relevant" -v`
Expected: FAIL — `status=not_relevant` isn't recognized yet (falls through to the generic `get_jobs(status="not_relevant", ...)` branch, which finds nothing since no real job has `status='not_relevant'`), and the Accepted/Leads tabs still apply `gate_passed_only=True` by default so the gate-failed jobs are hidden.

- [ ] **Step 3: Implement the route changes**

In `app/routes/jobs.py`, replace lines 21-27 (`_get_filtered_jobs`) with:

```python
def _get_filtered_jobs(
    conn: sqlite3.Connection, status: str | None, content_type: str | None
) -> list[dict]:
    if status == "not_relevant":
        return q.get_jobs(conn, status="new", content_type="job_posting", gate_status="failed")
    if status is None and content_type is None:
        return q.get_jobs(conn, status="new", gate_status="passed")
    return q.get_jobs(conn, status=status, content_type=content_type)
```

Replace lines 30-48 (`job_list`) with:

```python
@router.get("/jobs", response_class=HTMLResponse)
def job_list(
    request: Request,
    status: str | None = None,
    content_type: str | None = None,
    conn: sqlite3.Connection = Depends(get_db),
):
    jobs = _enrich_jobs(conn, _get_filtered_jobs(conn, status, content_type))
    counts = q.get_job_counts(conn)
    scenarios = q.get_scenarios(conn)
    effective_status = status if (status is not None or content_type is not None) else "new"
    return templates.TemplateResponse(
        request, "jobs/list.html",
        {
            "jobs": jobs, "counts": counts, "scenarios": scenarios,
            "status": effective_status, "content_type": content_type,
        },
    )
```

Replace lines 151-177 (`job_bulk_feedback`) with:

```python
@router.post("/jobs/bulk-feedback", response_class=HTMLResponse)
def job_bulk_feedback(
    request: Request,
    job_ids: list[int] = Form(...),
    status: str = Form(...),
    note: str | None = Form(None),
    status_filter: str | None = Form(None),
    content_type_filter: str | None = Form(None),
    conn: sqlite3.Connection = Depends(get_db),
):
    for job_id in job_ids:
        q.update_job_feedback(conn, job_id, status, note)

    status_filter = status_filter or None
    content_type_filter = content_type_filter or None
    jobs = _enrich_jobs(conn, _get_filtered_jobs(conn, status_filter, content_type_filter))
    counts = q.get_job_counts(conn)
    scenarios = q.get_scenarios(conn)
    effective_status = status_filter if (status_filter is not None or content_type_filter is not None) else "new"
    return templates.TemplateResponse(
        request, "jobs/_content.html",
        {
            "jobs": jobs, "counts": counts, "scenarios": scenarios,
            "status": effective_status, "content_type": content_type_filter,
        },
    )
```

- [ ] **Step 4: Run the full route test file to verify everything passes**

Run: `uv run pytest tests/test_routes_jobs.py -v`
Expected: PASS, all tests green. Note: `_content.html` and `list.html` still reference `show_filtered` (Task 4 removes that) — since it's no longer passed in the template context, Jinja renders those blocks as falsy/empty rather than erroring, so this task's tests should pass before Task 4 touches the templates. If any test in this file unexpectedly fails on a `show_filtered`-related assertion, that confirms Task 4 is still needed — proceed to it.

- [ ] **Step 5: Commit**

```bash
git add app/routes/jobs.py tests/test_routes_jobs.py
git commit -m "feat: add not_relevant pseudo-status, drop show_filtered from routes"
```

---

### Task 4: Templates — Not Relevant tab, remove Show filtered

**Files:**
- Modify: `app/templates/jobs/_content.html:1-13`, `app/templates/jobs/list.html:5-9`
- Test: `tests/test_routes_jobs.py` (new test, appended)

**Interfaces:**
- Consumes: `counts.not_relevant` (Task 2), `status == "not_relevant"` context var (Task 3).
- Produces: rendered `/jobs` filter bar with a "Not relevant (N)" tab and no "Show filtered" link; `list.html`'s bulk form no longer emits a `show_filtered_filter` hidden input.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_routes_jobs.py`:

```python
def test_job_list_shows_not_relevant_tab_and_drops_show_filtered(client, conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "http")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/1", title="Filtered Out", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="job_posting", summary="role")
    scenario_id = q.insert_scenario(conn, "A", "")  # default gate_threshold 0.7
    q.upsert_job_score(conn, jid, scenario_id, 0.3, "not a fit", "hash1")

    resp = client.get("/jobs")

    assert "Not relevant (1)" in resp.text
    assert 'href="/jobs?status=not_relevant"' in resp.text
    assert "Show filtered" not in resp.text
    assert 'name="show_filtered_filter"' not in resp.text
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_routes_jobs.py -k show_not_relevant_tab -v`
Expected: FAIL — no "Not relevant" tab in the rendered HTML yet, and "Show filtered" / `show_filtered_filter` are still present.

- [ ] **Step 3: Update `_content.html`**

The current file (`app/templates/jobs/_content.html:1-20`) reads exactly:

```html
<div class="filter-bar">
  <div class="filter-links">
    <a href="/jobs" {% if status == "new" and not content_type %}class="active"{% endif %}>New ({{ counts.new }})</a>
    <a href="/jobs?status=accepted" {% if status == "accepted" %}class="active"{% endif %}>Accepted ({{ counts.accepted }})</a>
    <a href="/jobs?status=rejected" {% if status == "rejected" %}class="active"{% endif %}>Rejected ({{ counts.rejected }})</a>
    <a href="/jobs?status=invalid" {% if status == "invalid" %}class="active"{% endif %}>Invalid ({{ counts.invalid }})</a>
    <a href="/jobs?content_type=lead" {% if content_type == "lead" %}class="active"{% endif %}>Leads ({{ counts.lead }})</a>
    {% if show_filtered %}
      <a href="/jobs" class="active" title="Currently showing jobs that didn't clear any scenario's gate">Show filtered ✓</a>
    {% else %}
      <a href="/jobs?show_filtered=1" title="Show jobs that didn't clear any scenario's gate">Show filtered</a>
    {% endif %}
  </div>
  {% if jobs %}
    <label class="select-all">
      <input type="checkbox" class="select-all-checkbox" aria-label="Select all">
      Select all
    </label>
  {% endif %}
</div>
```

Replace the `<div class="filter-links">...</div>` block (lines 2-13) with:

```html
  <div class="filter-links">
    <a href="/jobs" {% if status == "new" and not content_type %}class="active"{% endif %}>New ({{ counts.new }})</a>
    <a href="/jobs?status=accepted" {% if status == "accepted" %}class="active"{% endif %}>Accepted ({{ counts.accepted }})</a>
    <a href="/jobs?status=rejected" {% if status == "rejected" %}class="active"{% endif %}>Rejected ({{ counts.rejected }})</a>
    <a href="/jobs?content_type=lead" {% if content_type == "lead" %}class="active"{% endif %}>Leads ({{ counts.lead }})</a>
    <a href="/jobs?status=not_relevant" {% if status == "not_relevant" %}class="active"{% endif %}>Not relevant ({{ counts.not_relevant }})</a>
    <a href="/jobs?status=invalid" {% if status == "invalid" %}class="active"{% endif %}>Invalid ({{ counts.invalid }})</a>
  </div>
```

This reorders tabs to New, Accepted, Rejected, Leads, Not relevant, Invalid (moving Invalid after Leads and inserting Not relevant between them) and deletes the `show_filtered` conditional block entirely. The `{% if jobs %}...select-all...{% endif %}` block and the closing `</div>` after it (lines 14-20) are untouched.

- [ ] **Step 4: Update `list.html`**

In `app/templates/jobs/list.html`, remove line 8:

```html
  <input type="hidden" name="show_filtered_filter" value="{{ '1' if show_filtered else '' }}">
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest tests/test_routes_jobs.py -k show_not_relevant_tab -v`
Expected: PASS

- [ ] **Step 6: Run the full test suite**

Run: `uv run pytest -q`
Expected: PASS, all tests green, no failures.

- [ ] **Step 7: Grep for any leftover `show_filtered` references**

Run: `grep -rn "show_filtered" app/ tests/`
Expected: no output.

- [ ] **Step 8: Commit**

```bash
git add app/templates/jobs/_content.html app/templates/jobs/list.html tests/test_routes_jobs.py
git commit -m "feat: add Not relevant tab, remove show_filtered toggle"
```

---

### Task 5: Manual smoke test

**Files:** none (verification only)

**Interfaces:** none — this task only exercises the running app.

- [ ] **Step 1: Start the dev server against a throwaway DB copy**

Invoke the `run-dev-server` skill. It runs (from the worktree root):

```bash
MAIN_ROOT=$(git -C "$(git rev-parse --git-common-dir)/.." rev-parse --show-toplevel)
cp "$MAIN_ROOT/config.toml" .
cp "$MAIN_ROOT/job-seek.db" ./job-seek.db
uv run uvicorn app.main:app --reload --port 8931 > /tmp/job-seek-dev.log 2>&1 &
disown
sleep 2
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8931/jobs
```

Expected: `200` printed. (`config.toml` is likely already present in the worktree from setup — the copy is a harmless no-op if so. If port 8931 is already in use, pick another free port and use it for the rest of this task.)

- [ ] **Step 2: Confirm there's a gate-failed job posting to look at**

```bash
sqlite3 job-seek.db "
SELECT jobs.id, jobs.title FROM jobs
JOIN job_scores js ON js.job_id = jobs.id
JOIN scenarios s ON s.id = js.scenario_id
WHERE jobs.status = 'new' AND jobs.content_type = 'job_posting'
  AND js.relevance_score < s.gate_threshold
LIMIT 1;
"
```

If this prints a row, Steps 3-4 below will show real data in the Not relevant tab. If it prints nothing, the throwaway DB has no gate-failed postings right now — skip straight to Step 3 anyway (the tab will just show a `(0)` count and "No jobs found", which is still a valid check that nothing errors) and note in your final report that the count check in Step 3 couldn't be visually confirmed with real data (the automated tests in Tasks 1-4 already cover the underlying logic).

- [ ] **Step 3: Load `/jobs` and confirm the tab bar**

```bash
curl -s http://127.0.0.1:8931/jobs | grep -oE 'New \([0-9]+\)|Not relevant \([0-9]+\)|Accepted \([0-9]+\)|Rejected \([0-9]+\)|Leads \([0-9]+\)|Invalid \([0-9]+\)'
curl -s http://127.0.0.1:8931/jobs | grep -c "Show filtered"
```

Expected: six tab labels printed (one per line), including `Not relevant (N)`; the second command prints `0`.

- [ ] **Step 4: Click through `/jobs?status=not_relevant`**

```bash
curl -s "http://127.0.0.1:8931/jobs?status=not_relevant" | grep -c 'status=not_relevant" class="active"'
```

Expected: `1` (the Not relevant tab link is marked active on its own page).

- [ ] **Step 5: Stop the dev server**

```bash
pkill -f "uvicorn app.main:app --port 8931"
```

Per `CLAUDE.md`: the throwaway DB/config copies are gitignored and get discarded with the worktree at cleanup — no need to remove them manually.

---

## Post-plan: finishing the branch

Once all tasks are green, follow `CLAUDE.md`'s "Finishing a change" section: squash-merge `worktree-not-relevant-tab` into local `main` from the main checkout (`git merge --squash worktree-not-relevant-tab` then `git commit`), then remove the worktree and delete the branch.
