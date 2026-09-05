# Targeted re-evaluation tasks — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a profile save automatically recompute only stale job-fit scores, add a manual per-scenario re-score, and turn "Re-evaluate everything" into a lighter fan-out of per-scenario child tasks.

**Architecture:** All re-evaluation already runs through the task queue (`app/task_engine.py`, kinds registered with `@register_task_kind`). We add two new kinds (`scenario_reevaluate_one`, `profile_reassess_fit`), make the existing `run_reevaluate` score-only, convert `scenarios_reevaluate_all` into a `fetch_all`-style fan-out root, and wire the profile routes to enqueue the fit task on save. Scenario edits stay manual — a new button/route enqueues `scenario_reevaluate_one`.

**Tech Stack:** Python 3, FastAPI, SQLite (stdlib `sqlite3`), Jinja2 templates + htmx, pytest.

## Global Constraints

- No database migration. Stored `job_scores.scenario_version_hash` values self-heal on the next re-eval.
- No debounce / no `scheduled_for` column. Triggers enqueue immediately; `find_active_task` dedupes on `(kind, params)` while queued/running.
- `run_reevaluate` is score-only — it must never call `summarize()` or `update_job_pipeline()`.
- Scenario edits (name/description/threshold/criteria) trigger **nothing** automatically. Only the manual button and "Re-evaluate everything" re-score scenarios.
- Profile save (`POST /profile`) and refine-accept (`POST /profile/refine/accept`) auto-enqueue `profile_reassess_fit`.
- Run the full suite with `python -m pytest -q` from the worktree root. `uv run` does not work here (read-only cache).

---

### Task 1: Drop `gate_threshold` from the scenario version hash

`gate_threshold` never feeds the LLM `evaluate()` call — it is applied live at query time. Including it in the hash forces pointless full re-scores on a threshold-only edit.

**Files:**
- Modify: `app/scenario_version.py:5-9` (`compute_version_hash`)
- Test: `tests/test_scenario_version.py`

**Interfaces:**
- Consumes: nothing
- Produces: `compute_version_hash(scenario: dict, criteria: list[dict]) -> str` — unchanged signature; output no longer depends on `scenario["gate_threshold"]`.

- [ ] **Step 1: Replace the `gate_threshold` test with a "does not affect hash" test**

In `tests/test_scenario_version.py`, delete `test_hash_changes_with_gate_threshold` (lines 47-52) and add:

```python
def test_hash_ignores_gate_threshold():
    scenario = {"name": "N", "description": "Remote ML roles"}
    criteria = [{"text": "Must be remote", "weight": "must"}]
    h1 = compute_version_hash({**scenario, "gate_threshold": 0.7}, criteria)
    h2 = compute_version_hash({**scenario, "gate_threshold": 0.9}, criteria)
    assert h1 == h2
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_scenario_version.py::test_hash_ignores_gate_threshold -v`
Expected: FAIL — `h1 != h2` (threshold still in the hash).

- [ ] **Step 3: Remove `gate_threshold` from `compute_version_hash`**

In `app/scenario_version.py`, change the `parts` line in `compute_version_hash`:

```python
def compute_version_hash(scenario: dict, criteria: list[dict]) -> str:
    parts = [scenario.get("name", ""), scenario.get("description", "")]
    for c in sorted(criteria, key=lambda c: (c["weight"], c["text"])):
        parts.append(f"{c['weight']}:{c['text']}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
```

- [ ] **Step 4: Run the scenario-version tests**

Run: `python -m pytest tests/test_scenario_version.py -v`
Expected: PASS (all, including `test_hash_ignores_gate_threshold`, `test_hash_changes_with_name`, `test_hash_changes_with_description`, `test_hash_changes_with_criteria`).

- [ ] **Step 5: Run the full suite to catch fallout**

Run: `python -m pytest -q`
Expected: PASS. If a scenario-route test fails because it asserted a re-score happened after a threshold-only change, update that test to assert no re-score — but there is likely none.

- [ ] **Step 6: Commit**

```bash
git add app/scenario_version.py tests/test_scenario_version.py
git commit -m "feat: drop gate_threshold from scenario version hash"
```

---

### Task 2: Make `run_reevaluate` score-only

`run_reevaluate` currently re-summarizes every stale job (a second LLM call) and overwrites the shared `title`/`headline`/`summary`. The summary does not depend on the scenario. Strip it down to: score the stored `job["summary"]` and upsert the score.

**Files:**
- Modify: `app/pipeline.py:444-479` (`run_reevaluate`)
- Test: `tests/test_routes_scenarios.py` (existing `scenarios_reevaluate_all` task tests exercise `run_reevaluate`)

**Interfaces:**
- Consumes: `_eligible_for_reevaluation(conn, scenario) -> (to_evaluate: list[dict], skipped: int, criteria: list[dict], current_hash: str)` (unchanged, `app/pipeline.py:433`); `evaluate(client, model, scenario, criteria, summary) -> (score: float, reasoning: str)` (`app/ai/evaluate.py`); `q.upsert_job_score(conn, job_id, scenario_id, score, reasoning, version_hash)`.
- Produces: `run_reevaluate(conn, client, model, scenario) -> Generator[str, None, int]` — **signature changes**: the `*, scenario_label: str = ""` keyword param is removed. Returns the count of jobs re-scored.

- [ ] **Step 1: Update the two existing tests that assume re-summarization**

In `tests/test_routes_scenarios.py`:

`test_reevaluate_task_execution_updates_jobs` — the job's summary must be *unchanged* by re-evaluation. Full updated test:

```python
def test_reevaluate_task_execution_updates_jobs(conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Must be remote", "must")
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    job_id = q.insert_job(conn, source_id=source_id, url="http://job/1", title="ML Eng", company="C", raw_text="r")
    q.update_job_pipeline(conn, job_id, simplified_content="clean", content_type="job_posting", summary="original summary")

    task = q.enqueue_task(conn, kind="scenarios_reevaluate_all", params={})
    with patch("app.pipeline.summarize") as mock_summarize, \
         patch("app.pipeline.evaluate", return_value=(0.75, "Good match")), \
         patch("app.pipeline.assess_fit", return_value={"interest": 0.5, "interest_reasoning": "x", "attainability": 0.5, "attainability_reasoning": "y"}):
        execute_task(conn, MagicMock(), "model", MagicMock(), task)  # rewritten to _drain_all_tasks(conn) in Task 5 Step 3

    score = q.get_job_score(conn, job_id, sid)
    assert score["relevance_score"] == pytest.approx(0.75)
    # summary + title unchanged proves run_reevaluate called neither summarize() nor update_job_pipeline()
    assert q.get_job(conn, job_id)["summary"] == "original summary"
    assert q.get_job(conn, job_id)["title"] == "ML Eng"
    mock_summarize.assert_not_called()
```

Delete `test_reevaluate_keeps_existing_title_when_ai_title_empty` (lines ~742-757) entirely — title-from-summary behavior no longer exists.

- [ ] **Step 2: Run those tests to verify the failure**

Run: `python -m pytest tests/test_routes_scenarios.py::test_reevaluate_task_execution_updates_jobs -v`
Expected: FAIL — `run_reevaluate` still calls `summarize` and rewrites the summary, so `mock_summarize.assert_not_called()` fails.

- [ ] **Step 3: Rewrite `run_reevaluate` to be score-only**

Replace `app/pipeline.py:444-479` with:

```python
def run_reevaluate(
    conn: sqlite3.Connection,
    client: openai.OpenAI,
    model: str,
    scenario: dict,
) -> Generator[str, None, int]:
    to_evaluate, skipped, criteria, current_hash = _eligible_for_reevaluation(conn, scenario)
    total = len(to_evaluate)

    msg = f"Re-evaluating {total} job(s) for scenario '{scenario['name']}'"
    if skipped:
        msg += f", skipping {skipped} already current"
    yield _progress(msg)

    scored = 0
    for i, job in enumerate(to_evaluate, start=1):
        if not job["summary"]:
            yield _progress(f"[{i}/{total}] Skipped (no summary on file): {job['title'] or job['url']}")
            continue
        score, reasoning = evaluate(client, model, scenario, criteria, job["summary"])
        q.upsert_job_score(conn, job["id"], scenario["id"], score, reasoning, current_hash)
        scored += 1
        yield _progress(f"[{i}/{total}] Re-scored {score}: {job['title'] or job['url']}")

    yield _progress(f"Re-evaluation complete for '{scenario['name']}': {scored} job(s) updated")
    return scored
```

- [ ] **Step 4: Fix the now-broken monolithic caller**

`_task_scenarios_reevaluate_all` in `app/routes/scenarios.py:84-109` calls `run_reevaluate(conn, client, model, scenario, scenario_label=label)`. Remove the `scenario_label=label` kwarg and the `label` local:

```python
@register_task_kind("scenarios_reevaluate_all")
def _task_scenarios_reevaluate_all(conn, client, model, config, params):
    scenarios = q.get_scenarios(conn)
    yield f"Re-evaluating {len(scenarios)} scenario(s)"
    total_updated = 0
    for scenario in scenarios:
        gen = run_reevaluate(conn, client, model, scenario)
        try:
            while True:
                yield next(gen)
        except StopIteration as stop:
            total_updated += stop.value

    fit_gen = run_reassess_fit(conn, client, model)
    fit_updated = 0
    try:
        while True:
            yield next(fit_gen)
    except StopIteration as stop:
        fit_updated = stop.value

    yield (
        f"All scenarios re-evaluated: {total_updated} job(s) updated across "
        f"{len(scenarios)} scenario(s); fit recomputed for {fit_updated} job(s)"
    )
```

(This whole function is replaced by the fan-out in Task 5 — this step just keeps the suite green in between.)

- [ ] **Step 5: Update `test_reevaluate_all_scenarios_counts_scenarios_and_jobs_independently`**

That test (lines ~690-710) asserts the removed `[Scenario 1/2: Remote ML] [1/1] Re-scored` prefix. Replace its body's `with` block and assertions so it seeds a summary and checks the DB:

```python
def test_reevaluate_all_scenarios_counts_scenarios_and_jobs_independently(conn):
    sid_a = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid_a, "Must be remote", "must")
    sid_b = q.insert_scenario(conn, "Robotics", "")
    q.insert_criterion(conn, sid_b, "Must involve embedded systems", "must")
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    job_id = q.insert_job(conn, source_id=source_id, url="http://job/1", title="ML Eng", company="C", raw_text="r")
    q.update_job_pipeline(conn, job_id, simplified_content="clean", content_type="job_posting", summary="seed summary")

    task = q.enqueue_task(conn, kind="scenarios_reevaluate_all", params={})
    with patch("app.pipeline.evaluate", return_value=(0.75, "Good match")), \
         patch("app.pipeline.assess_fit", return_value={"interest": 0.5, "interest_reasoning": "x", "attainability": 0.5, "attainability_reasoning": "y"}):
        execute_task(conn, MagicMock(), "model", MagicMock(), task)  # TODO(task5): switch to _drain_all_tasks(conn)

    assert q.get_job_score(conn, job_id, sid_a)["relevance_score"] == pytest.approx(0.75)
    assert q.get_job_score(conn, job_id, sid_b)["relevance_score"] == pytest.approx(0.75)
```

- [ ] **Step 6: Update remaining `scenarios_reevaluate_all` tests that seed no summary**

Tests `test_reevaluate_includes_accepted_jobs`, `test_reevaluate_excludes_rejected_and_trash_jobs`, `test_reevaluate_skips_jobs_already_current`, `test_reevaluate_all_scenarios_combined_progress`, `test_reevaluate_all_scenarios_recomputes_fit_for_accepted_and_gate_failed_jobs` call `q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="job_posting")` without a `summary`. Add `summary="seed summary"` to each of those `update_job_pipeline` calls so the score-only path has a summary to score. Keep their existing assertions otherwise (the log strings `"Re-evaluating N job(s)"`, `"skipping N already current"`, `"fit recomputed for N job(s)"`, `"All scenarios re-evaluated..."` are all still produced by the interim `_task_scenarios_reevaluate_all` above).

- [ ] **Step 7: Run the scenario-route + pipeline suites**

Run: `python -m pytest tests/test_routes_scenarios.py tests/test_pipeline.py -q`
Expected: PASS.

- [ ] **Step 8: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add app/pipeline.py app/routes/scenarios.py tests/test_routes_scenarios.py
git commit -m "feat: make run_reevaluate score-only (no re-summarize)"
```

---

### Task 3: `scenario_reevaluate_one` task kind + manual per-scenario button

**Files:**
- Modify: `app/routes/scenarios.py` (add task kind after `_task_scenario_refine_one` ~line 160; add route after `reevaluate_all_scenarios` ~line 165)
- Modify: `app/templates/scenarios/index.html` (add button inside each scenario tab panel, after the criteria include block)
- Test: `tests/test_routes_scenarios.py`

**Interfaces:**
- Consumes: `run_reevaluate(conn, client, model, scenario)` (Task 2); `q.get_scenario(conn, scenario_id) -> dict | None`; `q.enqueue_task(conn, kind, params) -> dict` (with `["already_active"]` bool); `_get_scenario_or_404(conn, scenario_id)` (`app/routes/scenarios.py:178`).
- Produces: task kind `scenario_reevaluate_one` with params `{"scenario_id": int}`; route `POST /scenarios/{scenario_id}/reevaluate -> {"task_id": int, "already_active": bool}`.

- [ ] **Step 1: Write the failing task-kind tests**

Add to `tests/test_routes_scenarios.py`:

```python
def test_scenario_reevaluate_one_scopes_to_that_scenario(conn):
    sid_a = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid_a, "Must be remote", "must")
    sid_b = q.insert_scenario(conn, "Robotics", "")
    q.insert_criterion(conn, sid_b, "Must be embedded", "must")
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    job_id = q.insert_job(conn, source_id=source_id, url="http://job/1", title="ML Eng", company="C", raw_text="r")
    q.update_job_pipeline(conn, job_id, simplified_content="clean", content_type="job_posting", summary="seed summary")

    task = q.enqueue_task(conn, kind="scenario_reevaluate_one", params={"scenario_id": sid_a})
    with patch("app.pipeline.evaluate", return_value=(0.75, "Good match")):
        execute_task(conn, MagicMock(), "model", MagicMock(), task)

    assert q.get_task(conn, task["id"])["status"] == "done"
    assert q.get_job_score(conn, job_id, sid_a)["relevance_score"] == pytest.approx(0.75)
    assert q.get_job_score(conn, job_id, sid_b) is None


def test_scenario_reevaluate_one_noop_when_scenario_deleted(conn):
    task = q.enqueue_task(conn, kind="scenario_reevaluate_one", params={"scenario_id": 999})
    execute_task(conn, MagicMock(), "model", MagicMock(), task)
    fetched = q.get_task(conn, task["id"])
    assert fetched["status"] == "done"
    assert "no longer exists" in fetched["log"]
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_routes_scenarios.py::test_scenario_reevaluate_one_scopes_to_that_scenario tests/test_routes_scenarios.py::test_scenario_reevaluate_one_noop_when_scenario_deleted -v`
Expected: FAIL — `Unknown task kind: 'scenario_reevaluate_one'` (task ends `status == "failed"`).

- [ ] **Step 3: Add the task kind**

In `app/routes/scenarios.py`, after `_task_scenario_refine_one` (around line 160), add:

```python
@register_task_kind("scenario_reevaluate_one")
def _task_scenario_reevaluate_one(conn, client, model, config, params):
    scenario = q.get_scenario(conn, params["scenario_id"])
    if scenario is None:
        yield "Scenario no longer exists — nothing to re-evaluate"
        return {}
    yield from run_reevaluate(conn, client, model, scenario)
    return {}
```

- [ ] **Step 4: Run the task-kind tests**

Run: `python -m pytest tests/test_routes_scenarios.py::test_scenario_reevaluate_one_scopes_to_that_scenario tests/test_routes_scenarios.py::test_scenario_reevaluate_one_noop_when_scenario_deleted -v`
Expected: PASS.

- [ ] **Step 5: Write the failing route tests**

Add to `tests/test_routes_scenarios.py`:

```python
def test_post_scenario_reevaluate_enqueues_scoped_task(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    resp = client.post(f"/scenarios/{sid}/reevaluate")
    assert resp.status_code == 200
    data = resp.json()
    task = q.get_task(conn, data["task_id"])
    assert task["kind"] == "scenario_reevaluate_one"
    assert task["params"] == {"scenario_id": sid}
    assert data["already_active"] is False


def test_post_scenario_reevaluate_dedupes_while_queued(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    first = client.post(f"/scenarios/{sid}/reevaluate").json()
    second = client.post(f"/scenarios/{sid}/reevaluate").json()
    assert first["task_id"] == second["task_id"]
    assert second["already_active"] is True


def test_post_scenario_reevaluate_404_for_missing_scenario(client, conn):
    resp = client.post("/scenarios/999/reevaluate")
    assert resp.status_code == 404
```

- [ ] **Step 6: Run to verify failure**

Run: `python -m pytest tests/test_routes_scenarios.py::test_post_scenario_reevaluate_enqueues_scoped_task -v`
Expected: FAIL — 405 or 404 (route not defined).

- [ ] **Step 7: Add the route**

In `app/routes/scenarios.py`, immediately after `reevaluate_all_scenarios` (the `POST /scenarios/reevaluate` handler, ~line 165):

```python
@router.post("/scenarios/{scenario_id}/reevaluate")
def reevaluate_one_scenario(scenario_id: int, conn: sqlite3.Connection = Depends(get_db)):
    _get_scenario_or_404(conn, scenario_id)
    task = q.enqueue_task(conn, kind="scenario_reevaluate_one", params={"scenario_id": scenario_id})
    return {"task_id": task["id"], "already_active": task["already_active"]}
```

Note: `_get_scenario_or_404` is defined lower in the file (~line 178). Python resolves it at call time, so referencing it from a route defined above is fine.

- [ ] **Step 8: Run the route tests**

Run: `python -m pytest tests/test_routes_scenarios.py -k scenario_reevaluate -v`
Expected: PASS (all five new tests).

- [ ] **Step 8b: Add a regression test that scenario edits enqueue nothing**

Add to `tests/test_routes_scenarios.py` (this should already pass — it locks in that we did NOT wire edit endpoints):

```python
def test_scenario_edits_do_not_enqueue_reevaluation(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "old")
    client.post(f"/scenarios/{sid}", data={"name": "Remote ML", "description": "new", "gate_threshold": "0.8"})
    cid = q.insert_criterion(conn, sid, "Must be remote", "must")
    resp = client.post(f"/scenarios/{sid}/criteria", data={"text": "Prefer Python", "weight": "prefer"})
    assert resp.status_code == 200
    client.post(f"/criteria/{cid}", data={"text": "Must be fully remote", "weight": "must"})
    client.delete(f"/criteria/{cid}")

    assert q.find_active_task(conn, "scenario_reevaluate_one", {"scenario_id": sid}) is None
    assert q.get_active_tasks(conn) == []
```

Run: `python -m pytest tests/test_routes_scenarios.py::test_scenario_edits_do_not_enqueue_reevaluation -v`
Expected: PASS immediately (no wiring was added). If it fails, an edit endpoint is enqueuing — remove that enqueue.

- [ ] **Step 9: Write the failing template test**

Add to `tests/test_routes_scenarios.py`:

```python
def test_scenarios_page_has_per_scenario_reevaluate_button(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    resp = client.get("/scenarios")
    assert f'data-progress-url="/scenarios/{sid}/reevaluate"' in resp.text
    assert "Re-evaluate this scenario" in resp.text
```

- [ ] **Step 10: Run to verify failure**

Run: `python -m pytest tests/test_routes_scenarios.py::test_scenarios_page_has_per_scenario_reevaluate_button -v`
Expected: FAIL — string not in page.

- [ ] **Step 11: Add the button to the scenario tab panel**

In `app/templates/scenarios/index.html`, inside the `{% for scenario in scenarios %}` tab-panel loop, after the criteria `{% endwith %}` (line 40) and before `<div class="scenario-feedback-block">`:

```html
    <button class="btn" style="margin-top:0.5rem;"
      data-progress-url="/scenarios/{{ scenario.id }}/reevaluate"
      title="Re-score every new/accepted job against this scenario's current criteria (only jobs whose score is out of date).">
      Re-evaluate this scenario
    </button>
```

- [ ] **Step 12: Run the template test + full suite**

Run: `python -m pytest tests/test_routes_scenarios.py::test_scenarios_page_has_per_scenario_reevaluate_button -v && python -m pytest -q`
Expected: PASS.

- [ ] **Step 13: Commit**

```bash
git add app/routes/scenarios.py app/templates/scenarios/index.html tests/test_routes_scenarios.py
git commit -m "feat: manual per-scenario re-evaluate button + scenario_reevaluate_one task"
```

---

### Task 4: `profile_reassess_fit` task kind + profile-save triggers

**Files:**
- Modify: `app/routes/profile.py` (add import + task kind after `_task_profile_refine` ~line 50; enqueue from `profile_save` and `accept_profile_proposals`)
- Test: `tests/test_routes_profile.py`

**Interfaces:**
- Consumes: `run_reassess_fit(conn, client, model) -> Generator[str, None, int]` (`app/pipeline.py:482`); `q.enqueue_task(conn, kind, params)`; `q.get_profile(conn) -> str`; `q.upsert_profile(conn, text)`; `q.find_active_task(conn, kind, params) -> dict | None`; `apply_profile_proposals(profile_text, resolved) -> str`.
- Produces: task kind `profile_reassess_fit` with params `{}`.

- [ ] **Step 1: Write the failing task-kind test**

Add to `tests/test_routes_profile.py` (add `import pytest` at the top if missing):

```python
def test_profile_reassess_fit_task_recomputes_stale_fit(conn):
    q.upsert_profile(conn, "I am a senior ML engineer.")
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="job_posting", summary="seed summary")

    task = q.enqueue_task(conn, kind="profile_reassess_fit", params={})
    with patch("app.pipeline.assess_fit", return_value={
        "interest": 0.8, "interest_reasoning": "a",
        "attainability": 0.6, "attainability_reasoning": "b",
    }):
        execute_task(conn, MagicMock(), "model", MagicMock(), task)

    assert q.get_task(conn, task["id"])["status"] == "done"
    assert q.get_job(conn, jid)["interest_score"] == pytest.approx(0.8)
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_routes_profile.py::test_profile_reassess_fit_task_recomputes_stale_fit -v`
Expected: FAIL — `Unknown task kind: 'profile_reassess_fit'`.

- [ ] **Step 3: Add the task kind**

In `app/routes/profile.py`, add to the imports:

```python
from app.pipeline import run_reassess_fit
```

Then after `_task_profile_refine` (around line 50):

```python
@register_task_kind("profile_reassess_fit")
def _task_profile_reassess_fit(conn, client, model, config, params):
    yield from run_reassess_fit(conn, client, model)
    return {}
```

- [ ] **Step 4: Run the task-kind test**

Run: `python -m pytest tests/test_routes_profile.py::test_profile_reassess_fit_task_recomputes_stale_fit -v`
Expected: PASS.

- [ ] **Step 5: Write the failing trigger tests**

Add to `tests/test_routes_profile.py`:

```python
def test_profile_save_enqueues_fit_reassess_when_text_changed(client, conn):
    q.upsert_profile(conn, "old profile")
    client.post("/profile", data={"content": "new profile text"})
    assert q.find_active_task(conn, "profile_reassess_fit", {}) is not None


def test_profile_save_does_not_enqueue_when_text_unchanged(client, conn):
    q.upsert_profile(conn, "same text")
    client.post("/profile", data={"content": "same text"})
    assert q.find_active_task(conn, "profile_reassess_fit", {}) is None


def test_accept_profile_proposals_enqueues_fit_reassess_when_applied(client, conn):
    q.upsert_profile(conn, "## Technologies\n\n- Python\n")
    client.post(
        "/profile/refine/accept",
        data={"kind_0": "add", "section_0": "Technologies", "text_0": "AI/ML", "apply_0": "on"},
    )
    assert q.find_active_task(conn, "profile_reassess_fit", {}) is not None


def test_accept_profile_proposals_no_enqueue_when_nothing_applied(client, conn):
    q.upsert_profile(conn, "## Technologies\n\n- Python\n")
    client.post("/profile/refine/accept", data={"job_ids": "1"})
    assert q.find_active_task(conn, "profile_reassess_fit", {}) is None
```

- [ ] **Step 6: Run to verify failure**

Run: `python -m pytest tests/test_routes_profile.py -k "enqueues_fit_reassess or does_not_enqueue or no_enqueue" -v`
Expected: FAIL — no task enqueued.

- [ ] **Step 7: Enqueue from `profile_save` on real change**

In `app/routes/profile.py`, `profile_save` (line 27):

```python
@router.post("/profile", response_class=HTMLResponse)
def profile_save(
    request: Request,
    content: str = Form(...),
    conn: sqlite3.Connection = Depends(get_db),
):
    changed = content != q.get_profile(conn)
    q.upsert_profile(conn, content)
    if changed:
        q.enqueue_task(conn, kind="profile_reassess_fit", params={})
    ctx = _profile_context(conn)
    ctx["saved"] = True
    return templates.TemplateResponse(request, "profile/index.html", ctx)
```

- [ ] **Step 8: Enqueue from `accept_profile_proposals` when the text changed**

In `accept_profile_proposals` (line 61), around line 83-85:

```python
    profile_text = q.get_profile(conn)
    new_text = apply_profile_proposals(profile_text, resolved)
    q.upsert_profile(conn, new_text)
    if new_text != profile_text:
        q.enqueue_task(conn, kind="profile_reassess_fit", params={})
```

- [ ] **Step 9: Run the trigger tests + full profile suite**

Run: `python -m pytest tests/test_routes_profile.py -q`
Expected: PASS. Existing tests (`test_profile_save_and_display`, `test_accept_profile_proposals_*`) still pass — the enqueue is an additive side effect that leaves the response unchanged.

- [ ] **Step 10: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS.

- [ ] **Step 11: Commit**

```bash
git add app/routes/profile.py tests/test_routes_profile.py
git commit -m "feat: auto-enqueue profile fit reassess on profile save"
```

---

### Task 5: Convert `scenarios_reevaluate_all` into a fan-out root

Replace the monolithic loop with a `fetch_all`-style root that enqueues one `scenario_reevaluate_one` child per scenario plus one `profile_reassess_fit` child.

**Files:**
- Modify: `app/routes/scenarios.py` (`_task_scenarios_reevaluate_all`; import line 9)
- Modify: `tests/test_routes_scenarios.py` (add `_drain_all_tasks` helper; rewrite the `scenarios_reevaluate_all` behavioral tests)

**Interfaces:**
- Consumes: `params["_task_id"]` (injected by `execute_task`, `app/task_engine.py:82`); `q.enqueue_task(conn, kind, params, parent_task_id=...)`; `q.get_task_children(conn, root_id) -> list[dict]`; `q.claim_next_task(conn) -> dict | None`.
- Produces: `scenarios_reevaluate_all` completes immediately after fanning out; children carry all the work.

- [ ] **Step 1: Add a `_drain_all_tasks` helper to the test module**

At the top of `tests/test_routes_scenarios.py`, after the imports and `_run_refine_one`, add:

```python
def _drain_all_tasks(conn):
    """Run every queued task to completion, worker-style, including children
    spawned by fan-out roots."""
    while True:
        task = q.claim_next_task(conn)
        if task is None:
            return
        execute_task(conn, MagicMock(), "model", MagicMock(), task)
```

- [ ] **Step 2: Write the failing fan-out tests**

Replace `test_reevaluate_all_scenarios_combined_progress` and `test_reevaluate_all_scenarios_recomputes_fit_for_accepted_and_gate_failed_jobs` with:

```python
def test_reevaluate_all_fans_out_one_child_per_scenario_plus_fit(client, conn):
    sid_a = q.insert_scenario(conn, "Remote ML", "")
    sid_b = q.insert_scenario(conn, "Robotics", "")
    root_id = client.post("/scenarios/reevaluate").json()["task_id"]

    execute_task(conn, MagicMock(), "model", MagicMock(), q.get_task(conn, root_id))

    kids = q.get_task_children(conn, root_id)
    assert sorted(k["kind"] for k in kids) == [
        "profile_reassess_fit", "scenario_reevaluate_one", "scenario_reevaluate_one",
    ]
    scenario_ids = {k["params"]["scenario_id"] for k in kids if k["kind"] == "scenario_reevaluate_one"}
    assert scenario_ids == {sid_a, sid_b}
    assert all(k["parent_task_id"] == root_id for k in kids)
    assert q.get_task(conn, root_id)["status"] == "done"


def test_reevaluate_all_children_rescore_every_scenario(conn):
    sid_a = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid_a, "Must be remote", "must")
    sid_b = q.insert_scenario(conn, "Robotics", "")
    q.insert_criterion(conn, sid_b, "Must be embedded", "must")
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    job_id = q.insert_job(conn, source_id=source_id, url="http://job/1", title="ML Eng", company="C", raw_text="r")
    q.update_job_pipeline(conn, job_id, simplified_content="clean", content_type="job_posting", summary="seed summary")

    q.enqueue_task(conn, kind="scenarios_reevaluate_all", params={})
    with patch("app.pipeline.evaluate", return_value=(0.75, "Good match")), \
         patch("app.pipeline.assess_fit", return_value={"interest": 0.5, "interest_reasoning": "x", "attainability": 0.5, "attainability_reasoning": "y"}):
        _drain_all_tasks(conn)

    assert q.get_job_score(conn, job_id, sid_a)["relevance_score"] == pytest.approx(0.75)
    assert q.get_job_score(conn, job_id, sid_b)["relevance_score"] == pytest.approx(0.75)
    assert q.get_job(conn, job_id)["interest_score"] == pytest.approx(0.5)


def test_reevaluate_all_with_no_scenarios_still_queues_fit(conn):
    q.enqueue_task(conn, kind="scenarios_reevaluate_all", params={})
    task = q.claim_next_task(conn)
    execute_task(conn, MagicMock(), "model", MagicMock(), task)
    kids = q.get_task_children(conn, task["id"])
    assert [k["kind"] for k in kids] == ["profile_reassess_fit"]
```

- [ ] **Step 3: Rewrite the remaining `scenarios_reevaluate_all` tests to drain children**

`test_reevaluate_task_execution_updates_jobs` and `test_reevaluate_all_scenarios_counts_scenarios_and_jobs_independently` (updated in Task 2): change the `execute_task(...)  # TODO(task5)` line to `_drain_all_tasks(conn)` and delete the TODO comment. Assertions already check DB state, so they carry over.

`test_reevaluate_includes_accepted_jobs` — full replacement:

```python
def test_reevaluate_includes_accepted_jobs(conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Must be remote", "must")
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    job_id = q.insert_job(conn, source_id=source_id, url="http://job/1", title="ML Eng", company="C", raw_text="r")
    q.update_job_pipeline(conn, job_id, simplified_content="clean", content_type="job_posting", summary="seed summary")
    q.update_job_feedback(conn, job_id, "accepted", "")

    q.enqueue_task(conn, kind="scenarios_reevaluate_all", params={})
    with patch("app.pipeline.evaluate", return_value=(0.75, "Good match")), \
         patch("app.pipeline.assess_fit", return_value={"interest": 0.5, "interest_reasoning": "x", "attainability": 0.5, "attainability_reasoning": "y"}):
        _drain_all_tasks(conn)

    assert q.get_job_score(conn, job_id, sid)["relevance_score"] == pytest.approx(0.75)
```

`test_reevaluate_excludes_rejected_and_trash_jobs` — full replacement:

```python
def test_reevaluate_excludes_rejected_and_trash_jobs(conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Must be remote", "must")
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    rejected_id = q.insert_job(conn, source_id=source_id, url="http://job/1", title="A", company="C", raw_text="r")
    q.update_job_pipeline(conn, rejected_id, simplified_content="clean", content_type="job_posting", summary="seed")
    q.update_job_feedback(conn, rejected_id, "rejected", "")
    trash_id = q.insert_job(conn, source_id=source_id, url="http://job/2", title="B", company="C", raw_text="r")
    q.update_job_pipeline(conn, trash_id, simplified_content="clean", content_type="job_posting", summary="seed")
    q.update_job_feedback(conn, trash_id, "trash", "")

    q.enqueue_task(conn, kind="scenarios_reevaluate_all", params={})
    with patch("app.pipeline.evaluate", return_value=(0.75, "x")) as mock_evaluate, \
         patch("app.pipeline.assess_fit", return_value={"interest": 0.5, "interest_reasoning": "x", "attainability": 0.5, "attainability_reasoning": "y"}):
        _drain_all_tasks(conn)

    mock_evaluate.assert_not_called()
```

`test_reevaluate_skips_jobs_already_current` — full replacement:

```python
def test_reevaluate_skips_jobs_already_current(conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Must be remote", "must")
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    job_id = q.insert_job(conn, source_id=source_id, url="http://job/1", title="ML Eng", company="C", raw_text="r")
    q.update_job_pipeline(conn, job_id, simplified_content="clean", content_type="job_posting", summary="seed summary")

    with patch("app.pipeline.evaluate", return_value=(0.75, "Good match")) as mock_evaluate, \
         patch("app.pipeline.assess_fit", return_value={"interest": 0.5, "interest_reasoning": "x", "attainability": 0.5, "attainability_reasoning": "y"}):
        q.enqueue_task(conn, kind="scenarios_reevaluate_all", params={})
        _drain_all_tasks(conn)
        q.enqueue_task(conn, kind="scenarios_reevaluate_all", params={})
        _drain_all_tasks(conn)

    assert mock_evaluate.call_count == 1
```

- [ ] **Step 4: Run to verify failures**

Run: `python -m pytest tests/test_routes_scenarios.py -k reevaluate -v`
Expected: FAIL — root task still runs the monolithic loop; no children created, `get_task_children` empty.

- [ ] **Step 5: Rewrite `_task_scenarios_reevaluate_all` as a fan-out root**

Replace `_task_scenarios_reevaluate_all` in `app/routes/scenarios.py`:

```python
@register_task_kind("scenarios_reevaluate_all")
def _task_scenarios_reevaluate_all(conn, client, model, config, params):
    """Root task: fan out one scenario_reevaluate_one child per scenario, plus
    one profile_reassess_fit child. Completes immediately — displayed state is
    derived from the children (like fetch_all)."""
    scenarios = q.get_scenarios(conn)
    for scenario in scenarios:
        q.enqueue_task(
            conn, kind="scenario_reevaluate_one",
            params={"scenario_id": scenario["id"]},
            parent_task_id=params["_task_id"],
        )
    q.enqueue_task(
        conn, kind="profile_reassess_fit", params={},
        parent_task_id=params["_task_id"],
    )
    yield f"Queued re-evaluation of {len(scenarios)} scenario(s) + profile fit"
    return {}
```

- [ ] **Step 6: Drop the now-unused `run_reassess_fit` import from scenarios.py**

In `app/routes/scenarios.py` line 9, change:

```python
from app.pipeline import run_reevaluate, run_reassess_fit
```

to:

```python
from app.pipeline import run_reevaluate
```

- [ ] **Step 7: Run the scenario-route suite**

Run: `python -m pytest tests/test_routes_scenarios.py -q`
Expected: PASS.

- [ ] **Step 8: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS. `tests/test_routes_profile.py::test_profile_page_has_reevaluate_everything_button` and `tests/test_routes_tasks.py` stay green — the "Re-evaluate everything" button and `/scenarios/reevaluate` route are unchanged; a root task with children uses the existing `_root_summary` path (`app/routes/tasks.py:405`).

- [ ] **Step 9: Manual smoke test**

Use the `run-dev-server` skill to start the server against a throwaway DB. Then:
- Save the profile with a change → a `profile reassess fit` task appears in the bottom status bar.
- On the scenarios page, click "Re-evaluate this scenario" → a scoped task runs.
- Click "Re-evaluate everything" → the status bar shows a root with N+1 children.
Stop the server when done.

- [ ] **Step 10: Commit**

```bash
git add app/routes/scenarios.py tests/test_routes_scenarios.py
git commit -m "feat: fan out 'Re-evaluate everything' into per-scenario child tasks"
```

---

## Notes for the implementer

- **Task kind registration:** `@register_task_kind` runs at import time. `app/main.py` imports all routers, so `scenario_reevaluate_one` (in `scenarios.py`) and `profile_reassess_fit` (in `profile.py`) are both registered before any task runs. Tests reach this via the `client` fixture / `from app.task_engine import execute_task` (which imports `app.main` transitively through conftest).
- **`params` JSON round-trip:** `enqueue_task` stores params as JSON; `q.get_task(...)["params"]` returns a dict. `{"scenario_id": sid}` where `sid` is an int round-trips as an int (see the existing `fetch_source` tests comparing `k["params"]["source_id"] == sid`).
- **Dedupe wrinkle (documented, not a bug):** if `profile_save` already queued a standalone `profile_reassess_fit`, the fan-out root's `enqueue_task` for the fit child returns that existing (parent-less) task, so it won't appear under the root. Acceptable — the fit work still runs once. No test asserts against this.
- **`_progress` returns the message** it logs, so `yield _progress(...)` both logs and yields — keep that idiom.
- **Do not** add re-summarization back anywhere. Refreshing a stale summary is the job of the per-job "reset to new" / reprocess action.
