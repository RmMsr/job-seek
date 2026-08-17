# Combined "Re-evaluate everything" sweep Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace two separate, differently-scoped bulk sweeps ("Re-evaluate all scenarios" on the Scenarios page, "Recompute fit scores" on the Profile page) with one combined "Re-evaluate everything" sweep, shown as the same button on both pages, that refreshes both scenario scores and profile fit in one pass. Remove the now-redundant per-scenario "Re-evaluate jobs" button/route.

**Architecture:** `POST /scenarios/reevaluate` becomes the one shared endpoint: it keeps its existing per-scenario hash-skip loop, then appends one more hash-skip pass calling `run_reassess_fit`. `run_reassess_fit`'s eligibility widens to match the scenario sweep's scope. The per-scenario route and the profile's standalone route are deleted; the profile page points its button at the shared endpoint.

**Tech Stack:** FastAPI, Jinja2, sqlite3 (unchanged from existing pipeline/routes).

## Global Constraints

- `run_reassess_fit`'s new eligibility: `status in (new, accepted)`, any `gate_status`, `content_type in (job_posting, lead)` — the same scope `_eligible_for_reevaluation` already uses for scenario scoring. Copy that scope pattern (`q.get_jobs(conn, status="new") + q.get_jobs(conn, status="accepted")`, filtered by content_type) rather than inventing a new one.
- `POST /scenarios/reevaluate` is the one surviving combined-sweep endpoint. No new route is created for this feature.
- `POST /scenarios/{scenario_id}/reevaluate` and `POST /profile/reassess-fit` are deleted (routes, functions, and their dedicated tests) — not deprecated, not kept behind a flag. This is a personal single-instance app; no backwards-compat shim.
- Button label for the combined action is **"Re-evaluate everything"** everywhere it appears.
- Any test that exercises `/scenarios/reevaluate` must mock `app.pipeline.assess_fit` (in addition to whatever it already mocks) — the route now always attempts a fit pass, and an unmocked `assess_fit` call falls through to a real `openai.OpenAI()` client that tests must never hit.

---

### Task 1: Widen `run_reassess_fit` eligibility

**Files:**
- Modify: `app/pipeline.py:317-350` (the `run_reassess_fit` function)
- Test: `tests/test_pipeline.py:634-686`

**Interfaces:**
- Consumes: `q.get_jobs(conn, status=..., content_type=...)` (existing signature, unchanged).
- Produces: `run_reassess_fit(conn, client, model)` keeps its existing signature and `Generator[str, None, int]` return shape — only its internal eligibility query changes. Task 2 depends on this signature being unchanged so it can call it exactly as `run_reevaluate` is already called in the scenario loop.

- [ ] **Step 1: Update the three existing `run_reassess_fit` tests to reflect the new scope**

Replace `tests/test_pipeline.py:657-668` (`test_run_reassess_fit_skips_jobs_below_gate`) — its premise ("gate-failed jobs are skipped") no longer holds — with a test that an `accepted`-status job is now included:

```python
def test_run_reassess_fit_includes_accepted_and_gate_failed_jobs(conn, source):
    scenario_id = q.get_scenarios(conn)[0]["id"]
    accepted_id = q.insert_job(conn, source_id=source["id"], url="http://example.com/job/1", title="T", company="C", raw_text="r")
    q.update_job_pipeline(conn, accepted_id, simplified_content="clean", content_type="job_posting", summary="Good role")
    q.upsert_job_score(conn, accepted_id, scenario_id, 0.9, "great", "hash1")
    q.update_job_feedback(conn, accepted_id, "accepted", "")

    gate_failed_id = q.insert_job(conn, source_id=source["id"], url="http://example.com/job/2", title="U", company="C", raw_text="r")
    q.update_job_pipeline(conn, gate_failed_id, simplified_content="clean", content_type="job_posting", summary="Weak role")
    q.upsert_job_score(conn, gate_failed_id, scenario_id, 0.3, "weak", "hash1")  # below default 0.7 gate

    client = MagicMock()
    choice = MagicMock()
    choice.message.content = '{"interest": 0.8, "interest_reasoning": "a", "attainability": 0.6, "attainability_reasoning": "b"}'
    client.chat.completions.create.return_value = MagicMock(choices=[choice])

    messages, updated_count = _drain(run_reassess_fit(conn, client, "llama3.2"))

    assert updated_count == 2
    assert q.get_job(conn, accepted_id)["interest_score"] == pytest.approx(0.8)
    assert q.get_job(conn, gate_failed_id)["interest_score"] == pytest.approx(0.8)
```

Leave `test_run_reassess_fit_updates_gate_passed_jobs` (`tests/test_pipeline.py:637-654`) and `test_run_reassess_fit_skips_already_current_profile_hash` (`tests/test_pipeline.py:670-686`) as-is — both remain valid under the new scope (a `status=new` job with a passing gate is still eligible; the hash-skip behavior is unchanged).

- [ ] **Step 2: Run the updated/new tests to verify they fail against the current implementation**

Run: `uv run pytest tests/test_pipeline.py -k reassess_fit -v`
Expected: `test_run_reassess_fit_includes_accepted_and_gate_failed_jobs` FAILS (the gate-failed and accepted jobs aren't eligible yet under the old scope: `updated_count` will be 1, not 2).

- [ ] **Step 3: Widen the eligibility query in `run_reassess_fit`**

In `app/pipeline.py`, replace:

```python
    eligible = [
        j for j in q.get_jobs(conn, status="new", gate_status="passed")
        if j["content_type"] in ("job_posting", "lead")
    ]
```

with:

```python
    candidates = q.get_jobs(conn, status="new") + q.get_jobs(conn, status="accepted")
    eligible = [j for j in candidates if j["content_type"] in ("job_posting", "lead")]
```

- [ ] **Step 4: Run the tests again to verify they pass**

Run: `uv run pytest tests/test_pipeline.py -k reassess_fit -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Run the full pipeline test file to check for regressions**

Run: `uv run pytest tests/test_pipeline.py -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add app/pipeline.py tests/test_pipeline.py
git commit -m "feat: widen fit-reassess eligibility to match scenario sweep scope"
```

---

### Task 2: Fold fit-reassess into the scenario sweep; remove the per-scenario route

**Files:**
- Modify: `app/routes/scenarios.py:1-107` (import line + `reevaluate_all_scenarios`), `app/routes/scenarios.py:298-313` (delete `reevaluate_jobs`)
- Modify: `app/templates/scenarios/index.html:14` (relabel), `app/templates/scenarios/index.html:27-29` (delete per-scenario button block)
- Test: `tests/test_routes_scenarios.py` (see step-by-step edits below)

**Interfaces:**
- Consumes: `run_reassess_fit(conn, client, model)` from Task 1, called exactly like `run_reevaluate` already is in this same function — same drain-via-`StopIteration.value` pattern.
- Produces: nothing new consumed by later tasks — Task 3 only needs to know the surviving URL is `/scenarios/reevaluate` and the label is "Re-evaluate everything", both already fixed by the Global Constraints.

- [ ] **Step 1: Update `reevaluate_all_scenarios`'s import**

In `app/routes/scenarios.py`, change:

```python
from app.pipeline import run_reevaluate
```

to:

```python
from app.pipeline import run_reevaluate, run_reassess_fit
```

- [ ] **Step 2: Convert the per-scenario-route tests in `tests/test_routes_scenarios.py` to hit the combined endpoint**

These six tests currently `client.post(f"/scenarios/{sid}/reevaluate")` — the route they hit is being deleted in Step 5, but the job-level behavior they check (hash-skip, accepted-inclusion, rejected/trash-exclusion, title fallback) is still exercised by `run_reevaluate` inside the combined sweep. Convert each one in place: change the URL to the fixed string `"/scenarios/reevaluate"` and add `patch("app.pipeline.assess_fit", return_value={"interest": 0.5, "interest_reasoning": "x", "attainability": 0.5, "attainability_reasoning": "y"})` to every `with patch(...)` block that posts to this route (per the Global Constraint — the combined route always attempts a fit pass, and it must never hit a real client in tests). Leave every other assertion as-is; substring assertions like `"Re-evaluating 1 job(s)"` still match since they only look for a fragment of the per-scenario progress line, which is now nested inside the combined stream's output — unaffected by prefixing or the new trailing fit line.

Apply this to:
- `test_reevaluate_streams_progress_and_updates_jobs` (`tests/test_routes_scenarios.py:495-512`)
- `test_reevaluate_includes_accepted_jobs` (`tests/test_routes_scenarios.py:515-530`)
- `test_reevaluate_excludes_rejected_and_trash_jobs` (`tests/test_routes_scenarios.py:533-548`)
- `test_reevaluate_skips_jobs_already_current` (`tests/test_routes_scenarios.py:551-566`) — both `client.post(...)` calls in this test change URL; the single `with` block wrapping both already covers adding the `assess_fit` patch once.
- `test_reevaluate_keeps_existing_title_when_ai_title_empty` (`tests/test_routes_scenarios.py:624-637`)

Example (`test_reevaluate_streams_progress_and_updates_jobs`, showing the full pattern to replicate in the other four):

```python
def test_reevaluate_streams_progress_and_updates_jobs(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Must be remote", "must")
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    job_id = q.insert_job(conn, source_id=source_id, url="http://job/1", title="ML Eng", company="C", raw_text="r")
    q.update_job_pipeline(conn, job_id, simplified_content="clean", content_type="job_posting")

    with patch("app.pipeline.summarize", return_value=("ML Engineer - Remote @ Acme", "Great hook", "Updated summary")), \
         patch("app.pipeline.evaluate", return_value=(0.75, "Good match")), \
         patch("app.pipeline.assess_fit", return_value={"interest": 0.5, "interest_reasoning": "x", "attainability": 0.5, "attainability_reasoning": "y"}):
        resp = client.post("/scenarios/reevaluate")

    assert resp.status_code == 200
    assert "Re-evaluating 1 job(s)" in resp.text
    assert "Re-evaluation complete" in resp.text
    score = q.get_job_score(conn, job_id, sid)
    assert score["relevance_score"] == pytest.approx(0.75)
    job = q.get_job(conn, job_id)
    assert job["summary"] == "Updated summary"
```

- [ ] **Step 3: Delete `test_reevaluate_single_scenario_route_unaffected_by_global_labeling`**

Delete this test in full (`tests/test_routes_scenarios.py:609-621`) — it specifically verified that the per-scenario route's progress labeling differs from the all-scenarios route's. That route is being deleted in Step 5, so the test has no remaining subject. The multi-scenario labeling behavior it was contrasting against is already covered by `test_reevaluate_all_scenarios_counts_scenarios_and_jobs_independently`.

- [ ] **Step 4: Add `assess_fit` patches to the two existing all-scenarios tests, and update the summary-line assertion**

In `test_reevaluate_all_scenarios_streams_combined_progress` (`tests/test_routes_scenarios.py:569-586`), add the `assess_fit` patch to the `with` block, and change the assertion:

```python
    assert "All scenarios re-evaluated: 2 job(s) updated across 2 scenario(s)" in resp.text
```

to:

```python
    assert "All scenarios re-evaluated: 2 job(s) updated across 2 scenario(s); fit recomputed for 1 job(s)" in resp.text
```

(One job exists in this test, `status=new` by default, so it's eligible for exactly one fit update.)

In `test_reevaluate_all_scenarios_counts_scenarios_and_jobs_independently` (`tests/test_routes_scenarios.py:589-606`), add the same `assess_fit` patch to its `with` block (no assertion changes needed — this test only checks the per-scenario counter lines).

- [ ] **Step 5: Add a new test asserting fit is recomputed for accepted and gate-failed jobs**

Add to `tests/test_routes_scenarios.py`, near the other all-scenarios tests:

```python
def test_reevaluate_all_scenarios_recomputes_fit_for_accepted_and_gate_failed_jobs(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Must be remote", "must")
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")

    accepted_id = q.insert_job(conn, source_id=source_id, url="http://job/1", title="A", company="C", raw_text="r")
    q.update_job_pipeline(conn, accepted_id, simplified_content="clean", content_type="job_posting")
    q.update_job_feedback(conn, accepted_id, "accepted", "")

    gate_failed_id = q.insert_job(conn, source_id=source_id, url="http://job/2", title="B", company="C", raw_text="r")
    q.update_job_pipeline(conn, gate_failed_id, simplified_content="clean", content_type="job_posting")

    fit_result = {
        "interest": 0.8, "interest_reasoning": "a",
        "attainability": 0.6, "attainability_reasoning": "b",
    }
    with patch("app.pipeline.summarize", return_value=("Title", "Hook", "Updated summary")), \
         patch("app.pipeline.evaluate", return_value=(0.2, "weak")), \
         patch("app.pipeline.assess_fit", return_value=fit_result):
        resp = client.post("/scenarios/reevaluate")

    assert resp.status_code == 200
    assert "fit recomputed for 2 job(s)" in resp.text
    assert q.get_job(conn, accepted_id)["interest_score"] == pytest.approx(0.8)
    assert q.get_job(conn, gate_failed_id)["interest_score"] == pytest.approx(0.8)
```

(`evaluate` returns `0.2`, below the default 0.7 gate, so `gate_failed_id` fails its scenario gate — this proves fit no longer depends on gate status.)

- [ ] **Step 6: Run the scenarios test file to verify everything still fails only where expected (route not yet changed)**

Run: `uv run pytest tests/test_routes_scenarios.py -v`
Expected: the tests converted/added in Steps 2-5 FAIL (route `/scenarios/{id}/reevaluate` still exists and `/scenarios/reevaluate` doesn't yet call `run_reassess_fit`, so the new assertions about fit and the new summary-line text don't match).

- [ ] **Step 7: Extend `reevaluate_all_scenarios` to append the fit pass**

In `app/routes/scenarios.py`, replace the `reevaluate_all_scenarios` function body:

```python
@router.post("/scenarios/reevaluate")
def reevaluate_all_scenarios(
    conn: sqlite3.Connection = Depends(get_db),
    client: openai.OpenAI = Depends(get_ai_client),
    model: str = Depends(get_model),
):
    scenarios = q.get_scenarios(conn)

    def stream():
        yield f"Re-evaluating {len(scenarios)} scenario(s)\n"
        total_updated = 0
        for idx, scenario in enumerate(scenarios, start=1):
            label = f"[Scenario {idx}/{len(scenarios)}: {scenario['name']}] "
            gen = run_reevaluate(conn, client, model, scenario, scenario_label=label)
            try:
                while True:
                    yield next(gen) + "\n"
            except StopIteration as stop:
                total_updated += stop.value

        fit_gen = run_reassess_fit(conn, client, model)
        fit_updated = 0
        try:
            while True:
                yield next(fit_gen) + "\n"
        except StopIteration as stop:
            fit_updated = stop.value

        yield (
            f"All scenarios re-evaluated: {total_updated} job(s) updated across "
            f"{len(scenarios)} scenario(s); fit recomputed for {fit_updated} job(s)\n"
        )

    return StreamingResponse(stream(), media_type="text/plain")
```

- [ ] **Step 8: Delete the per-scenario route**

In `app/routes/scenarios.py`, delete the `reevaluate_jobs` function entirely (`app/routes/scenarios.py:298-313`):

```python
@router.post("/scenarios/{scenario_id}/reevaluate")
def reevaluate_jobs(
    scenario_id: int,
    conn: sqlite3.Connection = Depends(get_db),
    client: openai.OpenAI = Depends(get_ai_client),
    model: str = Depends(get_model),
):
    scenario = _get_scenario_or_404(conn, scenario_id)

    def stream():
        gen = run_reevaluate(conn, client, model, scenario)
        try:
            while True:
                yield next(gen) + "\n"
        except StopIteration:
            pass
```

- [ ] **Step 9: Update the scenarios page template**

In `app/templates/scenarios/index.html`, change line 14:

```html
  <button class="btn" data-progress-url="/scenarios/reevaluate">Re-evaluate all scenarios</button>
```

to:

```html
  <button class="btn" data-progress-url="/scenarios/reevaluate">Re-evaluate everything</button>
```

And delete the per-scenario button block (`app/templates/scenarios/index.html:27-29`):

```html
  <div style="margin-top:0.75rem; display:flex; gap:0.5rem; flex-wrap:wrap;">
    <button class="btn" data-progress-url="/scenarios/{{ scenario.id }}/reevaluate">Re-evaluate jobs</button>
  </div>

```

- [ ] **Step 10: Run the scenarios test file to verify everything passes**

Run: `uv run pytest tests/test_routes_scenarios.py -v`
Expected: all pass.

- [ ] **Step 11: Run the full test suite to check for regressions**

Run: `uv run pytest -q`
Expected: same pass/fail count as the pre-existing baseline (one known pre-existing failure, `test_job_expand_note_field_is_optional`, unrelated to this change — see Task 3's baseline note).

- [ ] **Step 12: Commit**

```bash
git add app/routes/scenarios.py app/templates/scenarios/index.html tests/test_routes_scenarios.py
git commit -m "feat: fold fit-reassess into the scenario sweep, remove per-scenario route"
```

---

### Task 3: Remove the profile page's standalone route; point its button at the combined sweep

**Files:**
- Modify: `app/routes/profile.py:1-46` (delete `reassess_fit`, drop now-unused import)
- Modify: `app/templates/profile/index.html:13-20`
- Test: `tests/test_routes_profile.py`

**Interfaces:**
- Consumes: the `/scenarios/reevaluate` URL and "Re-evaluate everything" label fixed by Task 2 — no code dependency, just the string the button points at and displays.

- [ ] **Step 1: Remove the two obsolete tests in `tests/test_routes_profile.py`**

Delete `test_reassess_fit_streams_progress` and `test_profile_page_has_reassess_fit_button` in full (`tests/test_routes_profile.py:26-43`). Also remove the now-unused `from unittest.mock import patch` import at line 23 — nothing else in this file uses `patch`.

- [ ] **Step 2: Add the new button-presence test**

Add to `tests/test_routes_profile.py`:

```python
def test_profile_page_has_reevaluate_everything_button(client):
    resp = client.get("/profile")
    assert resp.status_code == 200
    assert 'data-progress-url="/scenarios/reevaluate"' in resp.text
    assert "Re-evaluate everything" in resp.text
```

- [ ] **Step 3: Run the profile test file to verify the new test fails (template not yet updated)**

Run: `uv run pytest tests/test_routes_profile.py -v`
Expected: `test_profile_page_has_reevaluate_everything_button` FAILS (button doesn't exist yet); the two deleted tests are simply gone, not run.

- [ ] **Step 4: Remove the `/profile/reassess-fit` route and its now-unused import**

In `app/routes/profile.py`, delete the `reassess_fit` function entirely (lines 32-46):

```python
@router.post("/profile/reassess-fit")
def reassess_fit(
    conn: sqlite3.Connection = Depends(get_db),
    client: openai.OpenAI = Depends(get_ai_client),
    model: str = Depends(get_model),
):
    def stream():
        gen = run_reassess_fit(conn, client, model)
        try:
            while True:
                yield next(gen) + "\n"
        except StopIteration:
            pass

    return StreamingResponse(stream(), media_type="text/plain")
```

And remove the now-unused import (line 8):

```python
from app.pipeline import run_reassess_fit
```

`StreamingResponse` was only ever used by `reassess_fit` in this file — once it's gone, change line 5 from:

```python
from fastapi.responses import HTMLResponse, StreamingResponse
```

to:

```python
from fastapi.responses import HTMLResponse
```

- [ ] **Step 5: Replace the profile page's button block**

In `app/templates/profile/index.html`, replace lines 13-20:

```html
<div style="margin-top:1.5rem; padding-top:1rem; border-top:1px solid #dee2e6;">
  <button type="button" class="btn" data-progress-url="/profile/reassess-fit"
    data-progress-display="#reassess-progress"
    title="Recompute the interest/attainability scorecard for every job that already passed a scenario gate but has a stale or missing fit score.">
    Recompute fit scores
  </button>
  <div class="reset-progress" id="reassess-progress" aria-live="polite"></div>
</div>
```

with:

```html
<div style="margin-top:1.5rem; padding-top:1rem; border-top:1px solid #dee2e6;">
  <button class="btn" data-progress-url="/scenarios/reevaluate"
    title="Re-score every new/accepted job against every scenario and refresh its profile fit.">
    Re-evaluate everything
  </button>
</div>
```

- [ ] **Step 6: Run the profile test file to verify it passes**

Run: `uv run pytest tests/test_routes_profile.py -v`
Expected: all pass.

- [ ] **Step 7: Run the full test suite**

Run: `uv run pytest -q`
Expected: same result as Task 2's Step 11 (one known pre-existing, unrelated failure: `test_job_expand_note_field_is_optional`).

- [ ] **Step 8: Commit**

```bash
git add app/routes/profile.py app/templates/profile/index.html tests/test_routes_profile.py
git commit -m "feat: remove standalone fit-reassess route, point profile page at combined sweep"
```

---

### Task 4: Manual verification

- [ ] **Step 1: Start the dev server**

Use the `run-dev-server` skill (throwaway `job-seek.db` copy, per `CLAUDE.md`).

- [ ] **Step 2: Confirm with the user**

Hand the URL to the user. Ask them to check:
- The Scenarios page shows one "Re-evaluate everything" button (no more per-scenario "Re-evaluate jobs" buttons), and clicking it streams progress covering every scenario followed by a fit pass, ending in a summary line reporting both counts.
- The Profile page shows the same "Re-evaluate everything" button (no more "Recompute fit scores"), and clicking it does the same combined sweep.
- An `accepted`-status job's fit score updates from either entry point (previously excluded unless it had also passed a gate).

Wait for the user's go-ahead before offering to merge, per `CLAUDE.md`.
