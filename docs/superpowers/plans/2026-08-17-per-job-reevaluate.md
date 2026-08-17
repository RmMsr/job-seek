# Per-job/bulk "Re-evaluate" Action Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Re-evaluate" action (per-job and bulk-selected) that re-scores a job against every scenario and refreshes its profile fit in place, without moving its status or wiping its content — a lighter counterpart to "Reset to new".

**Architecture:** One new pipeline generator function (`run_reevaluate_job`) shared by a per-job route and a bulk route, following the exact structural pattern `run_reprocess_job` already uses for `/jobs/{id}/reset` and `/jobs/bulk-reset`. Two template edits add the corresponding buttons.

**Tech Stack:** FastAPI (Python), Jinja2 templates, pytest, sqlite3. No new dependencies.

## Global Constraints

- Never touches `job["status"]`, `content_type`, `feedback_handled_at`, `gate_override`, or `scenario_feedback` rows — verified by tests, not just by omission.
- Always recomputes (no version-hash skip): every scenario is re-scored and fit is always reassessed, every call.
- Only acts on `status in ("new", "accepted")` and `content_type in ("job_posting", "lead")`; anything else is skipped with a progress message and zero LLM calls.
- Follow the repo's `Note (optional)` → `Job note (optional)` style: match existing button/label casing conventions ("Re-evaluate", sentence case, matching "Pass as new" / "Reset to new").

---

### Task 1: `run_reevaluate_job` pipeline function

**Files:**
- Modify: `app/pipeline.py` (insert after `run_pass_as_new`, which ends at line 219 — insert the new function starting at line 221, before `_eligible_for_reevaluation`)
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `q.get_criteria`, `q.upsert_job_score`, `q.update_job_pipeline`, `q.update_job_fit` (all exist in `app/db/queries.py`); `summarize(client, model, simplified_content, content_type=...)  -> (title, headline, summary)`, `evaluate(client, model, scenario, criteria, summary) -> (score, reasoning)`, `assess_fit(client, model, profile, summary) -> dict` with keys `interest`, `interest_reasoning`, `attainability`, `attainability_reasoning` (all in `app/ai/*`, already imported in `pipeline.py`); `compute_version_hash(scenario, criteria)`, `compute_profile_hash(profile)` (already imported); `_progress(msg: str) -> str` (defined at `app/pipeline.py:48`).
- Produces: `run_reevaluate_job(conn, client, model, job, scenarios, profile, *, progress_prefix="") -> Generator[str, None, None]`. `job` is a dict as returned by `q.get_job` (has `id`, `url`, `title`, `status`, `content_type`, `simplified_content`). `scenarios` is `list[dict]` as returned by `q.get_scenarios`. Task 2 and Task 4 import this name from `app.pipeline`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_pipeline.py` (append after `test_run_reprocess_job_irrelevant_deletes_job`, using the existing `conn`/`source` fixtures and `_drain` helper already defined in that file):

```python
from app.pipeline import run_reevaluate_job


def test_run_reevaluate_job_rescopes_and_reassesses_without_moving_status(conn, source):
    jid = q.insert_job(
        conn, source_id=source["id"], url="http://example.com/job/1",
        title="Old Title", company="Acme", raw_text="raw text",
    )
    q.update_job_pipeline(
        conn, jid, simplified_content="clean text", content_type="job_posting",
        title="Old Title", headline="old hook", summary="old summary",
    )
    scenario_id = q.get_scenarios(conn)[0]["id"]
    q.upsert_job_score(conn, jid, scenario_id, 0.3, "old reasoning", "old-hash")
    q.update_job_fit(conn, jid, 0.2, "old interest", 0.2, "old attainability", "old-phash")
    q.update_job_feedback(conn, jid, "accepted", "great fit")

    job = q.get_job(conn, jid)
    scenarios = q.get_scenarios(conn)
    profile = q.get_profile(conn)

    with patch("app.pipeline.summarize", return_value=("New Title", "new hook", "new summary")), \
         patch("app.pipeline.evaluate", return_value=(0.9, "now a great match")), \
         patch("app.pipeline.assess_fit", return_value={
             "interest": 0.8, "interest_reasoning": "strong interest",
             "attainability": 0.7, "attainability_reasoning": "reachable",
         }):
        messages, _ = _drain(run_reevaluate_job(conn, MagicMock(), "llama3.2", job, scenarios, profile))

    updated = q.get_job(conn, jid)
    assert updated["status"] == "accepted"
    assert updated["title"] == "New Title"
    assert updated["headline"] == "new hook"
    assert updated["summary"] == "new summary"
    assert updated["interest_score"] == pytest.approx(0.8)
    assert updated["attainability_score"] == pytest.approx(0.7)
    score = q.get_job_score(conn, jid, scenario_id)
    assert score["relevance_score"] == pytest.approx(0.9)
    assert any("Scored 0.9" in m for m in messages)
    assert any("Fit 0.80/0.70" in m for m in messages)


def test_run_reevaluate_job_always_recomputes_even_when_unchanged(conn, source):
    jid = q.insert_job(
        conn, source_id=source["id"], url="http://example.com/job/1",
        title="T", company="C", raw_text="raw",
    )
    q.update_job_pipeline(conn, jid, simplified_content="clean text", content_type="job_posting", summary="s")
    scenarios = q.get_scenarios(conn)
    profile = q.get_profile(conn)

    with patch("app.pipeline.summarize", return_value=("T", "h", "s")), \
         patch("app.pipeline.evaluate", return_value=(0.5, "reason")) as mock_evaluate, \
         patch("app.pipeline.assess_fit", return_value={
             "interest": 0.5, "interest_reasoning": "r",
             "attainability": 0.5, "attainability_reasoning": "r",
         }) as mock_assess:
        _drain(run_reevaluate_job(conn, MagicMock(), "llama3.2", q.get_job(conn, jid), scenarios, profile))
        _drain(run_reevaluate_job(conn, MagicMock(), "llama3.2", q.get_job(conn, jid), scenarios, profile))

    assert mock_evaluate.call_count == 2
    assert mock_assess.call_count == 2


def test_run_reevaluate_job_skips_rejected_job(conn, source):
    jid = q.insert_job(
        conn, source_id=source["id"], url="http://example.com/job/1",
        title="T", company="C", raw_text="raw",
    )
    q.update_job_pipeline(conn, jid, simplified_content="clean text", content_type="job_posting", summary="s")
    q.update_job_feedback(conn, jid, "rejected", "no")
    job = q.get_job(conn, jid)
    scenarios = q.get_scenarios(conn)
    profile = q.get_profile(conn)

    with patch("app.pipeline.summarize") as mock_summarize, \
         patch("app.pipeline.evaluate") as mock_evaluate, \
         patch("app.pipeline.assess_fit") as mock_assess_fit:
        messages, _ = _drain(run_reevaluate_job(conn, MagicMock(), "llama3.2", job, scenarios, profile))

    mock_summarize.assert_not_called()
    mock_evaluate.assert_not_called()
    mock_assess_fit.assert_not_called()
    assert any("Skipped" in m for m in messages)
    assert q.get_job(conn, jid)["status"] == "rejected"


def test_run_reevaluate_job_skips_trash_job(conn, source):
    jid = q.insert_job(
        conn, source_id=source["id"], url="http://example.com/job/1",
        title="T", company="C", raw_text="raw",
    )
    q.update_job_pipeline(conn, jid, simplified_content="clean text", content_type="job_posting", summary="s")
    q.update_job_feedback(conn, jid, "trash", "no")
    job = q.get_job(conn, jid)
    scenarios = q.get_scenarios(conn)
    profile = q.get_profile(conn)

    with patch("app.pipeline.summarize") as mock_summarize:
        messages, _ = _drain(run_reevaluate_job(conn, MagicMock(), "llama3.2", job, scenarios, profile))

    mock_summarize.assert_not_called()
    assert any("Skipped" in m for m in messages)
    assert q.get_job(conn, jid)["status"] == "trash"


def test_run_reevaluate_job_skips_non_scoreable_content_type(conn, source):
    jid = q.insert_job(
        conn, source_id=source["id"], url="http://example.com/job/1",
        title="T", company="C", raw_text="raw",
    )
    q.update_job_pipeline(conn, jid, simplified_content="", content_type="error")
    job = q.get_job(conn, jid)
    scenarios = q.get_scenarios(conn)
    profile = q.get_profile(conn)

    with patch("app.pipeline.summarize") as mock_summarize:
        messages, _ = _drain(run_reevaluate_job(conn, MagicMock(), "llama3.2", job, scenarios, profile))

    mock_summarize.assert_not_called()
    assert any("Skipped" in m for m in messages)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_pipeline.py -k run_reevaluate_job -v`
Expected: FAIL with `ImportError: cannot import name 'run_reevaluate_job'`

- [ ] **Step 3: Implement `run_reevaluate_job`**

In `app/pipeline.py`, insert immediately after `run_pass_as_new` (after the line `    )` that closes its final `yield _progress(...)` call, i.e. right before `def _eligible_for_reevaluation`:

```python
def run_reevaluate_job(
    conn: sqlite3.Connection,
    client: openai.OpenAI,
    model: str,
    job: dict,
    scenarios: list[dict],
    profile: str,
    *,
    progress_prefix: str = "",
) -> Generator[str, None, None]:
    if job["status"] in ("rejected", "trash") or job["content_type"] not in ("job_posting", "lead"):
        yield _progress(f"{progress_prefix}Skipped (not eligible for re-evaluation): {job['url']}")
        return

    ai_title, headline, new_summary = summarize(
        client, model, job["simplified_content"], content_type=job["content_type"]
    )
    q.update_job_pipeline(
        conn, job["id"],
        simplified_content=job["simplified_content"],
        content_type=job["content_type"],
        title=ai_title or job["title"],
        headline=headline,
        summary=new_summary,
    )

    for scenario in scenarios:
        criteria = q.get_criteria(conn, scenario["id"])
        version_hash = compute_version_hash(scenario, criteria)
        score, reasoning = evaluate(client, model, scenario, criteria, new_summary)
        q.upsert_job_score(conn, job["id"], scenario["id"], score, reasoning, version_hash)
        yield _progress(f"{progress_prefix}Scored {score} for '{scenario['name']}': {job['url']}")

    result = assess_fit(client, model, profile, new_summary)
    q.update_job_fit(
        conn, job["id"],
        result["interest"], result["interest_reasoning"],
        result["attainability"], result["attainability_reasoning"],
        compute_profile_hash(profile),
    )
    yield _progress(
        f"{progress_prefix}Fit {result['interest']:.2f}/{result['attainability']:.2f}: {job['url']}"
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_pipeline.py -k run_reevaluate_job -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Run the full test suite**

Run: `python -m pytest tests/ -q`
Expected: PASS (only the pre-existing unrelated `test_job_expand_note_field_is_optional` failure, if still present)

- [ ] **Step 6: Commit**

```bash
git add app/pipeline.py tests/test_pipeline.py
git commit -m "feat: add run_reevaluate_job pipeline function

Re-scores a job against every scenario and refreshes its profile fit
in place, without moving status or wiping content — the compute half
of Reset without the destructive half.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: Per-job route `POST /jobs/{job_id}/reevaluate`

**Files:**
- Modify: `app/routes/jobs.py:9` (import), and insert new route after `job_pass_as_new` (currently ends at line 328, right before the blank line and `@router.post("/jobs/{job_id}/reset")`... actually `job_pass_as_new` is defined *after* `job_reset` in the file — insert the new route immediately after `job_pass_as_new`, which ends at line 328)
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: `run_reevaluate_job` from Task 1 (`app.pipeline.run_reevaluate_job(conn, client, model, job, scenarios, profile, *, progress_prefix="")`); existing helpers `_filter_context(request) -> dict` (`app/routes/jobs.py:59`) and `_render_updated_job_html(conn, request, job_id, filter_ctx) -> str` (`app/routes/jobs.py:106`); `q.get_job`, `q.get_scenarios`, `q.get_profile`, `q.get_job_counts`.
- Produces: route `POST /jobs/{job_id}/reevaluate`, streaming `text/plain`, same shape as `POST /jobs/{job_id}/reset`. Task 3's button targets this URL.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_routes_jobs.py`, near the existing reset/pass-as-new tests (after `test_job_pass_as_new_unknown_job_returns_404`, using the existing `_seed` helper defined at the top of the file):

```python
def _fake_run_reevaluate_job(conn, client, model, job, scenarios, profile, progress_prefix=""):
    yield f"{progress_prefix}Scored 0.85 for 'Remote ML': {job['url']}"
    q.upsert_job_score(conn, job["id"], scenarios[0]["id"], 0.85, "now a match", "hash-new")
    yield f"{progress_prefix}Fit 0.75/0.65: {job['url']}"
    q.update_job_fit(conn, job["id"], 0.75, "strong interest", 0.65, "reachable", "phash-new")


def test_job_reevaluate_streams_progress_and_updates_scores(client, conn):
    sid, jid, scenario_id = _seed(conn)
    q.update_job_feedback(conn, jid, "accepted", "good fit")

    with patch("app.routes.jobs.run_reevaluate_job", side_effect=_fake_run_reevaluate_job):
        resp = client.post(f"/jobs/{jid}/reevaluate")

    assert resp.status_code == 200
    assert "Scored 0.85" in resp.text
    assert "Fit 0.75/0.65" in resp.text
    assert q.get_job(conn, jid)["status"] == "accepted"


def test_job_reevaluate_unknown_job_returns_404(client, conn):
    resp = client.post("/jobs/999/reevaluate")
    assert resp.status_code == 404


def test_job_reevaluate_stream_ends_with_html_chunk_for_updated_row(client, conn):
    sid, jid, scenario_id = _seed(conn)
    with patch("app.routes.jobs.run_reevaluate_job", side_effect=_fake_run_reevaluate_job):
        resp = client.post(f"/jobs/{jid}/reevaluate")
    assert resp.status_code == 200
    assert f'HTML:<article class="job-row" id="job-{jid}">' in resp.text


def test_job_reevaluate_stream_includes_counts_html_chunk(client, conn):
    sid, jid, scenario_id = _seed(conn)
    with patch("app.routes.jobs.run_reevaluate_job", side_effect=_fake_run_reevaluate_job):
        resp = client.post(f"/jobs/{jid}/reevaluate")
    assert resp.status_code == 200
    assert 'HTML:<span id="count-new" hx-swap-oob="true">' in resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_routes_jobs.py -k job_reevaluate -v`
Expected: FAIL — `AttributeError: <module 'app.routes.jobs'> does not have the attribute 'run_reevaluate_job'` (route module hasn't imported it yet) and/or 404s from the route not existing.

- [ ] **Step 3: Add the import and the route**

In `app/routes/jobs.py:9`, change:

```python
from app.pipeline import run_reprocess_job, run_pass_as_new, run_add_job, run_fetch
```

to:

```python
from app.pipeline import run_reprocess_job, run_pass_as_new, run_reevaluate_job, run_add_job, run_fetch
```

Then insert this route immediately after `job_pass_as_new` (after its closing `return StreamingResponse(stream(), media_type="text/plain")` at line 328):

```python
@router.post("/jobs/{job_id}/reevaluate")
def job_reevaluate(
    job_id: int,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    client: openai.OpenAI = Depends(get_ai_client),
    model: str = Depends(get_model),
):
    job = q.get_job(conn, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    scenarios = q.get_scenarios(conn)
    profile = q.get_profile(conn)
    filter_ctx = _filter_context(request)

    def stream():
        gen = run_reevaluate_job(conn, client, model, job, scenarios, profile)
        try:
            while True:
                yield next(gen) + "\n"
        except StopIteration:
            pass
        html = _render_updated_job_html(conn, request, job_id, filter_ctx)
        yield "HTML:" + html.replace("\n", "") + "\n"
        counts_html = templates.get_template("jobs/_counts_oob.html").render(
            request=request, counts=q.get_job_counts(conn)
        )
        yield "HTML:" + counts_html.replace("\n", "")

    return StreamingResponse(stream(), media_type="text/plain")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_routes_jobs.py -k job_reevaluate -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Run the full test suite**

Run: `python -m pytest tests/ -q`
Expected: PASS (only the pre-existing unrelated failure, if still present)

- [ ] **Step 6: Commit**

```bash
git add app/routes/jobs.py tests/test_routes_jobs.py
git commit -m "feat: add per-job POST /jobs/{id}/reevaluate route

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: Per-job "Re-evaluate" button

**Files:**
- Modify: `app/templates/jobs/_feedback.html:75-93` (the `<details class="job-advanced">` block)
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: route from Task 2 (`POST /jobs/{job_id}/reevaluate`); existing template variables `job` (has `.status`), `filter_status`/`filter_content_type`/`filter_source_id` (may be undefined — same `is defined` guard used by the neighboring buttons); existing shared progress display `#reset-progress-{{ job.id }}` (already rendered at the bottom of this same file, shared by "Pass as new" and "Reset to new").
- Produces: a button visible only when `job.status in ("new", "accepted")`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_routes_jobs.py`, near the existing pass-as-new visibility tests (after `test_job_expand_omits_pass_as_new_button_when_already_overridden`):

```python
def test_job_expand_shows_reevaluate_button_when_new(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert f'data-progress-url="/jobs/{jid}/reevaluate"' in resp.text


def test_job_expand_shows_reevaluate_button_when_accepted(client, conn):
    sid, jid, scenario_id = _seed(conn)
    q.update_job_feedback(conn, jid, "accepted", "")
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert f'data-progress-url="/jobs/{jid}/reevaluate"' in resp.text


def test_job_expand_omits_reevaluate_button_when_rejected(client, conn):
    sid, jid, scenario_id = _seed(conn)
    q.update_job_feedback(conn, jid, "rejected", "")
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert f'data-progress-url="/jobs/{jid}/reevaluate"' not in resp.text


def test_job_expand_omits_reevaluate_button_when_trash(client, conn):
    sid, jid, scenario_id = _seed(conn)
    q.update_job_feedback(conn, jid, "trash", "")
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert f'data-progress-url="/jobs/{jid}/reevaluate"' not in resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_routes_jobs.py -k expand_reevaluate -v`
Expected: FAIL — the two "shows" tests fail because the button doesn't exist yet (the "omits" tests will already pass, since a nonexistent button is trivially absent; that's fine, they lock in the behavior going forward).

- [ ] **Step 3: Add the button**

In `app/templates/jobs/_feedback.html`, the `<details class="job-advanced">` block currently reads (lines 75-93):

```html
  <details class="job-advanced">
    <summary>Advanced&hellip;</summary>
    {% if job.content_type == "job_posting" and job.scored_gate_count and not job.passed_gate_count and not job.gate_override %}
      <button type="button" class="btn"
        data-progress-url="/jobs/{{ job.id }}/pass-as-new{% if filter_status is defined %}?status={{ filter_status or '' }}&content_type={{ filter_content_type or '' }}{% if filter_source_id %}&source_id={{ filter_source_id }}{% endif %}{% endif %}"
        data-progress-oob="1"
        data-progress-display="#reset-progress-{{ job.id }}"
        title="Bypass the gate threshold for this job and file it as New, keeping its existing scores as a record of why it failed.">
        Pass as new
      </button>
    {% endif %}
    <button type="button" class="btn btn-reset"
      data-progress-url="/jobs/{{ job.id }}/reset{% if filter_status is defined %}?status={{ filter_status or '' }}&content_type={{ filter_content_type or '' }}{% if filter_source_id %}&source_id={{ filter_source_id }}{% endif %}{% endif %}"
      data-progress-oob="1"
      data-progress-display="#reset-progress-{{ job.id }}"
      title="Wipe the simplified content, classification, summary and scores, then immediately rerun the full pipeline from the original posting text.">
      Reset to new
    </button>
  </details>
```

Insert a new button between the "Pass as new" `{% endif %}` and the "Reset to new" button:

```html
  <details class="job-advanced">
    <summary>Advanced&hellip;</summary>
    {% if job.content_type == "job_posting" and job.scored_gate_count and not job.passed_gate_count and not job.gate_override %}
      <button type="button" class="btn"
        data-progress-url="/jobs/{{ job.id }}/pass-as-new{% if filter_status is defined %}?status={{ filter_status or '' }}&content_type={{ filter_content_type or '' }}{% if filter_source_id %}&source_id={{ filter_source_id }}{% endif %}{% endif %}"
        data-progress-oob="1"
        data-progress-display="#reset-progress-{{ job.id }}"
        title="Bypass the gate threshold for this job and file it as New, keeping its existing scores as a record of why it failed.">
        Pass as new
      </button>
    {% endif %}
    {% if job.status in ("new", "accepted") %}
      <button type="button" class="btn"
        data-progress-url="/jobs/{{ job.id }}/reevaluate{% if filter_status is defined %}?status={{ filter_status or '' }}&content_type={{ filter_content_type or '' }}{% if filter_source_id %}&source_id={{ filter_source_id }}{% endif %}{% endif %}"
        data-progress-oob="1"
        data-progress-display="#reset-progress-{{ job.id }}"
        title="Re-score this job against every scenario and refresh its profile fit, without moving it out of its current status.">
        Re-evaluate
      </button>
    {% endif %}
    <button type="button" class="btn btn-reset"
      data-progress-url="/jobs/{{ job.id }}/reset{% if filter_status is defined %}?status={{ filter_status or '' }}&content_type={{ filter_content_type or '' }}{% if filter_source_id %}&source_id={{ filter_source_id }}{% endif %}{% endif %}"
      data-progress-oob="1"
      data-progress-display="#reset-progress-{{ job.id }}"
      title="Wipe the simplified content, classification, summary and scores, then immediately rerun the full pipeline from the original posting text.">
      Reset to new
    </button>
  </details>
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_routes_jobs.py -k expand_reevaluate -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Run the full test suite**

Run: `python -m pytest tests/ -q`
Expected: PASS (only the pre-existing unrelated failure, if still present)

- [ ] **Step 6: Commit**

```bash
git add app/templates/jobs/_feedback.html tests/test_routes_jobs.py
git commit -m "feat: add per-job Re-evaluate button

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: Bulk route `POST /jobs/bulk-reevaluate`

**Files:**
- Modify: `app/routes/jobs.py` (insert after `job_bulk_reset`, which ends at line 364, right before `@router.post("/jobs/bulk-feedback", ...)`)
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: `run_reevaluate_job` (Task 1, already imported in Task 2); `_filter_context`, `_render_updated_job_html`; `q.get_scenarios`, `q.get_profile`, `q.get_job`, `q.get_job_counts`.
- Produces: route `POST /jobs/bulk-reevaluate` accepting `job_ids: list[int]` form data, same streaming shape as `POST /jobs/bulk-reset`. Task 5's button targets this URL.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_routes_jobs.py`, after `test_job_bulk_reset_button_has_progress_oob_and_filter_query` (reusing `_fake_run_reevaluate_job` from Task 2, which already accepts `progress_prefix`):

```python
def test_job_bulk_reevaluate_streams_progress_for_each_job(client, conn):
    sid, j1, scenario_id = _seed(conn)
    j2 = q.insert_job(conn, source_id=sid, url="http://finn.no/job/2", title="Data Eng", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, j2, simplified_content="clean", content_type="job_posting", summary="role")
    q.upsert_job_score(conn, j2, scenario_id, 0.4, "reason", "hash1")

    with patch("app.routes.jobs.run_reevaluate_job", side_effect=_fake_run_reevaluate_job):
        resp = client.post("/jobs/bulk-reevaluate", data={"job_ids": [j1, j2]})

    assert resp.status_code == 200
    assert resp.text.count("Scored 0.85") == 2
    assert "Re-evaluation complete: 2 job(s)" in resp.text


def test_job_bulk_reevaluate_stream_includes_per_job_html_and_counts_chunks(client, conn):
    sid, j1, scenario_id = _seed(conn)
    j2 = q.insert_job(conn, source_id=sid, url="http://finn.no/job/2", title="Data Eng", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, j2, simplified_content="clean", content_type="job_posting", summary="role")
    q.upsert_job_score(conn, j2, scenario_id, 0.4, "reason", "hash1")

    with patch("app.routes.jobs.run_reevaluate_job", side_effect=_fake_run_reevaluate_job):
        resp = client.post("/jobs/bulk-reevaluate", data={"job_ids": [j1, j2]})

    assert resp.status_code == 200
    assert resp.text.count(f'HTML:<article class="job-row" id="job-{j1}">') == 1
    assert resp.text.count(f'HTML:<article class="job-row" id="job-{j2}">') == 1
    assert 'HTML:<span id="count-new" hx-swap-oob="true">' in resp.text


def test_job_bulk_reevaluate_preserves_status(client, conn):
    sid, j1, scenario_id = _seed(conn)
    q.update_job_feedback(conn, j1, "accepted", "good fit")

    with patch("app.routes.jobs.run_reevaluate_job", side_effect=_fake_run_reevaluate_job):
        resp = client.post("/jobs/bulk-reevaluate", data={"job_ids": [j1]})

    assert resp.status_code == 200
    assert q.get_job(conn, j1)["status"] == "accepted"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_routes_jobs.py -k bulk_reevaluate -v`
Expected: FAIL — 404/405 (route doesn't exist yet)

- [ ] **Step 3: Add the route**

Insert immediately after `job_bulk_reset`'s closing `return StreamingResponse(stream(), media_type="text/plain")` (line 364), before `@router.post("/jobs/bulk-feedback", ...)`:

```python
@router.post("/jobs/bulk-reevaluate")
def job_bulk_reevaluate(
    request: Request,
    job_ids: list[int] = Form(...),
    conn: sqlite3.Connection = Depends(get_db),
    client: openai.OpenAI = Depends(get_ai_client),
    model: str = Depends(get_model),
):
    scenarios = q.get_scenarios(conn)
    profile = q.get_profile(conn)
    filter_ctx = _filter_context(request)

    def stream():
        yield f"Re-evaluating {len(job_ids)} job(s)\n"
        for idx, job_id in enumerate(job_ids, start=1):
            job = q.get_job(conn, job_id)
            if not job:
                continue
            prefix = f"[{idx}/{len(job_ids)}] "
            gen = run_reevaluate_job(conn, client, model, job, scenarios, profile, progress_prefix=prefix)
            try:
                while True:
                    yield next(gen) + "\n"
            except StopIteration:
                pass
            html = _render_updated_job_html(conn, request, job_id, filter_ctx)
            yield "HTML:" + html.replace("\n", "") + "\n"
        counts_html = templates.get_template("jobs/_counts_oob.html").render(
            request=request, counts=q.get_job_counts(conn)
        )
        yield "HTML:" + counts_html.replace("\n", "") + "\n"
        yield f"Re-evaluation complete: {len(job_ids)} job(s) updated\n"

    return StreamingResponse(stream(), media_type="text/plain")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_routes_jobs.py -k bulk_reevaluate -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the full test suite**

Run: `python -m pytest tests/ -q`
Expected: PASS (only the pre-existing unrelated failure, if still present)

- [ ] **Step 6: Commit**

```bash
git add app/routes/jobs.py tests/test_routes_jobs.py
git commit -m "feat: add bulk POST /jobs/bulk-reevaluate route

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 5: Bulk "Re-evaluate" button

**Files:**
- Modify: `app/templates/jobs/_content.html:36-49` (the bulk-bar `<details class="job-advanced">` block and its progress divs)
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: route from Task 4 (`POST /jobs/bulk-reevaluate`); the same `data-progress-jobs` multi-select wiring already used by "Reset to new" (reads checked `.job-select` checkboxes with `name="job_ids"`, defined in `_feedback.html`/`_row.html`, no changes needed there).
- Produces: a bulk button, unconditionally visible (same as "Reset to new" — not status-gated, since `run_reevaluate_job` already no-ops on ineligible jobs within the selection).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_routes_jobs.py`, after `test_job_bulk_reset_button_has_progress_oob_and_filter_query`:

```python
def test_job_bulk_reevaluate_button_has_progress_oob_and_filter_query(client, conn):
    resp = client.get("/jobs?status=accepted")
    assert resp.status_code == 200
    assert 'data-progress-url="/jobs/bulk-reevaluate?status=accepted&content_type="' in resp.text
    assert 'data-progress-jobs' in resp.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_routes_jobs.py -k bulk_reevaluate_button -v`
Expected: FAIL — string not found in response

- [ ] **Step 3: Add the button**

In `app/templates/jobs/_content.html`, the bulk bar currently reads (lines 30-50):

```html
<div class="bulk-bar decision-panel">
  <button type="button" class="bulk-clear bulk-clear-x" aria-label="Clear selection" title="Clear selection">×</button>
  <label class="decision-panel-note">Note (optional):
    <textarea name="note" form="bulk-form" placeholder="Optional note for all selected jobs"></textarea>
  </label>
  <div class="decision-panel-left">
    <details class="job-advanced">
      <summary>Advanced&hellip;</summary>
      <button type="button" class="btn btn-reset"
        data-progress-url="/jobs/bulk-reset{% if filter_status is defined %}?status={{ filter_status or '' }}&content_type={{ filter_content_type or '' }}{% if filter_source_id %}&source_id={{ filter_source_id }}{% endif %}{% endif %}"
        data-progress-jobs
        data-progress-oob="1"
        data-progress-display="#bulk-reset-progress"
        title="Wipe the simplified content, classification, summary and scores for each selected job, then immediately rerun the full pipeline from the original posting text.">
        Reset to new
      </button>
    </details>
    {% include "jobs/_bulk_actions.html" %}
  </div>
  <div class="reset-progress" id="bulk-reset-progress" aria-live="polite"></div>
</div>
```

Replace it with:

```html
<div class="bulk-bar decision-panel">
  <button type="button" class="bulk-clear bulk-clear-x" aria-label="Clear selection" title="Clear selection">×</button>
  <label class="decision-panel-note">Note (optional):
    <textarea name="note" form="bulk-form" placeholder="Optional note for all selected jobs"></textarea>
  </label>
  <div class="decision-panel-left">
    <details class="job-advanced">
      <summary>Advanced&hellip;</summary>
      <button type="button" class="btn"
        data-progress-url="/jobs/bulk-reevaluate{% if filter_status is defined %}?status={{ filter_status or '' }}&content_type={{ filter_content_type or '' }}{% if filter_source_id %}&source_id={{ filter_source_id }}{% endif %}{% endif %}"
        data-progress-jobs
        data-progress-oob="1"
        data-progress-display="#bulk-reevaluate-progress"
        title="Re-score each selected job against every scenario and refresh its profile fit, without moving any of them out of their current status.">
        Re-evaluate
      </button>
      <button type="button" class="btn btn-reset"
        data-progress-url="/jobs/bulk-reset{% if filter_status is defined %}?status={{ filter_status or '' }}&content_type={{ filter_content_type or '' }}{% if filter_source_id %}&source_id={{ filter_source_id }}{% endif %}{% endif %}"
        data-progress-jobs
        data-progress-oob="1"
        data-progress-display="#bulk-reset-progress"
        title="Wipe the simplified content, classification, summary and scores for each selected job, then immediately rerun the full pipeline from the original posting text.">
        Reset to new
      </button>
    </details>
    {% include "jobs/_bulk_actions.html" %}
  </div>
  <div class="reset-progress" id="bulk-reevaluate-progress" aria-live="polite"></div>
  <div class="reset-progress" id="bulk-reset-progress" aria-live="polite"></div>
</div>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_routes_jobs.py -k bulk_reevaluate_button -v`
Expected: PASS

- [ ] **Step 5: Run the full test suite**

Run: `python -m pytest tests/ -q`
Expected: PASS (only the pre-existing unrelated failure, if still present)

- [ ] **Step 6: Commit**

```bash
git add app/templates/jobs/_content.html tests/test_routes_jobs.py
git commit -m "feat: add bulk Re-evaluate button

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 6: Manual verification

Not a code task — hand off to the user per project convention (UI-facing change).

- [ ] **Step 1: Start the dev server** against a throwaway `job-seek.db` copy (see the `run-dev-server` skill) and report the URL to the user.
- [ ] **Step 2: Confirm with the user**: click "Re-evaluate" on a New job, an Accepted job, and via bulk-select on a mix — verify status never changes, scores/fit visibly update, and the button is absent on Rejected/Trash rows.
- [ ] **Step 3:** Wait for the user's go-ahead before offering to merge.
