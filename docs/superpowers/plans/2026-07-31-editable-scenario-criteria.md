# Editable Scenario Criteria Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users edit existing scenario criteria in place, make AI-proposed criteria changes editable before accepting, and wire up the AI's "remove this criterion" suggestion (currently rendered but non-functional) to actually delete the matched criterion.

**Architecture:** Follows the existing click-to-edit pattern already used for scenario name/description (`_header.html` / `_header_edit.html` swapped via htmx). Criterion rows move into a dedicated partial so they can be swapped independently. Refine proposals gain a `criterion_id` resolved server-side by matching proposal text against existing criteria, letting "remove" proposals point at a real row to delete.

**Tech Stack:** FastAPI (routes), Jinja2 (`app/templates`), htmx (`hx-get`/`hx-post`/`hx-delete`, out-of-band swaps), sqlite3 (`app/db/queries.py`), pytest + `fastapi.testclient.TestClient`.

## Global Constraints

- No new dependencies — everything needed already exists in the stack.
- Follow existing query/route/template conventions exactly (see file references in each task) rather than introducing new patterns.
- `source` on a criterion is never modified by an edit — it only reflects original provenance (`manual` vs `feedback`).
- Commit after each task (see `CLAUDE.md`: commit frequently, one increment at a time).
- Design spec: `docs/superpowers/specs/2026-07-31-editable-scenario-criteria-design.md` — refer back to it if a task's rationale is unclear.

---

### Task 1: Query layer — `get_criterion` and `update_criterion`

**Files:**
- Modify: `app/db/queries.py` (add after `insert_criterion`, before `delete_criterion`, i.e. around line 106)
- Test: `tests/test_queries.py` (add after `test_criteria_insert_and_delete`, around line 57)

**Interfaces:**
- Produces: `q.get_criterion(conn: sqlite3.Connection, criterion_id: int) -> dict | None`
- Produces: `q.update_criterion(conn: sqlite3.Connection, criterion_id: int, text: str, weight: str) -> None`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_queries.py`:

```python
def test_get_criterion(conn):
    sid = q.insert_scenario(conn, "A", "")
    cid = q.insert_criterion(conn, sid, "Must be remote", "must")
    criterion = q.get_criterion(conn, cid)
    assert criterion["id"] == cid
    assert criterion["text"] == "Must be remote"
    assert criterion["weight"] == "must"


def test_get_criterion_missing_returns_none(conn):
    assert q.get_criterion(conn, 999) is None


def test_update_criterion(conn):
    sid = q.insert_scenario(conn, "A", "")
    cid = q.insert_criterion(conn, sid, "Must be remote", "must")
    q.update_criterion(conn, cid, text="Must be fully remote", weight="prefer")
    criterion = q.get_criterion(conn, cid)
    assert criterion["text"] == "Must be fully remote"
    assert criterion["weight"] == "prefer"


def test_update_criterion_leaves_source_unchanged(conn):
    sid = q.insert_scenario(conn, "A", "")
    cid = q.insert_criterion(conn, sid, "Must be remote", "must", source="feedback")
    q.update_criterion(conn, cid, text="Must be fully remote", weight="prefer")
    assert q.get_criterion(conn, cid)["source"] == "feedback"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_queries.py -k "criterion" -v`
Expected: FAIL with `AttributeError: module 'app.db.queries' has no attribute 'get_criterion'`

- [ ] **Step 3: Implement the queries**

In `app/db/queries.py`, insert between `insert_criterion` and `delete_criterion`:

```python
def get_criterion(conn: sqlite3.Connection, criterion_id: int) -> dict | None:
    return _row_to_dict(conn.execute("SELECT * FROM criteria WHERE id = ?", (criterion_id,)).fetchone())


def update_criterion(conn: sqlite3.Connection, criterion_id: int, text: str, weight: str) -> None:
    conn.execute(
        "UPDATE criteria SET text = ?, weight = ? WHERE id = ?",
        (text, weight, criterion_id),
    )
    conn.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_queries.py -k "criterion" -v`
Expected: PASS (6 passed — the 2 pre-existing plus 4 new)

- [ ] **Step 5: Commit**

```bash
git add app/db/queries.py tests/test_queries.py
git commit -m "feat: add get_criterion and update_criterion queries"
```

---

### Task 2: Extract criterion row into its own partial, dedupe `index.html`

**Files:**
- Create: `app/templates/scenarios/_criterion.html`
- Modify: `app/templates/scenarios/_criteria.html`
- Modify: `app/templates/scenarios/index.html`
- Test: `tests/test_routes_scenarios.py` (extend `test_add_criterion`, add new test near it, around line 50)

**Interfaces:**
- Produces: `_criterion.html` partial expecting a single Jinja variable `c` (dict with `id`, `text`, `weight`) in scope — read (non-edit) row view.
- Consumes: nothing new — reuses `markdown_inline` filter already registered in `app/template_env.py`.

Today `index.html` inlines its own copy of the criterion `<li>` markup (lines 20-33) that has drifted from `_criteria.html`'s copy — notably it's missing the `| markdown_inline` filter on `c.text`, so criteria render unformatted on first page load but formatted after any htmx add/delete. This task fixes that by making both templates render the same partial.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_routes_scenarios.py`, near `test_add_criterion`:

```python
def test_scenarios_page_renders_criterion_markdown(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Must be *remote*", "must")
    resp = client.get("/scenarios")
    assert resp.status_code == 200
    assert "<em>remote</em>" in resp.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_routes_scenarios.py -k markdown -v`
Expected: FAIL — `<em>remote</em>` not in response (raw `*remote*` shown instead, since `index.html` doesn't apply the filter today)

- [ ] **Step 3: Create `_criterion.html`**

```html
<li id="criterion-{{ c.id }}" style="display:flex; align-items:center; gap:0.5rem; margin-bottom:0.3rem;">
  <span class="tag">{{ c.weight }}</span>
  <span>{{ c.text | markdown_inline }}</span>
  <button class="btn" style="padding:2px 8px; font-size:0.8em;"
    hx-get="/criteria/{{ c.id }}/edit"
    hx-target="#criterion-{{ c.id }}"
    hx-swap="outerHTML">Edit</button>
  <button class="btn" style="padding:2px 8px; font-size:0.8em;"
    hx-delete="/criteria/{{ c.id }}"
    hx-target="#criterion-{{ c.id }}"
    hx-swap="outerHTML">&#x2715;</button>
</li>
```

(The Edit button's route doesn't exist yet — added in Task 3. The template will render fine; the button just 404s until then.)

- [ ] **Step 4: Update `_criteria.html`** to include the new partial per row

Replace the full file content with:

```html
<ul id="criteria-{{ scenario_id }}" style="list-style:none; padding:0;">
  {% for c in criteria %}
  {% include "scenarios/_criterion.html" %}
  {% else %}
  <li style="color:#999;">No criteria yet.</li>
  {% endfor %}
</ul>
<form hx-post="/scenarios/{{ scenario_id }}/criteria"
      hx-target="#criteria-{{ scenario_id }}"
      hx-swap="outerHTML"
      style="display:flex; gap:0.5rem; flex-wrap:wrap; margin-top:0.5rem;">
  <input type="text" name="text" placeholder="Criterion text" required style="flex:1; min-width:200px;">
  <select name="weight">
    <option value="must">must</option>
    <option value="prefer">prefer</option>
    <option value="avoid">avoid</option>
  </select>
  <button type="submit" class="btn">Add</button>
</form>
```

- [ ] **Step 5: Update `index.html`** to render `_criteria.html` instead of inlining its own copy

Replace lines 19-45 of `app/templates/scenarios/index.html` (the `<h4>Criteria</h4>` block through the closing `</form>` of the add-criterion form) with:

```html
  <h4 style="margin:0.75rem 0 0.4rem;">Criteria</h4>
  {% with criteria=criteria_by_scenario[scenario.id], scenario_id=scenario.id %}
  {% include "scenarios/_criteria.html" %}
  {% endwith %}
```

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/test_routes_scenarios.py -k markdown -v`
Expected: PASS

- [ ] **Step 7: Run the full scenarios test suite to check nothing broke**

Run: `pytest tests/test_routes_scenarios.py tests/test_queries.py -v`
Expected: all PASS

- [ ] **Step 8: Commit**

```bash
git add app/templates/scenarios/_criterion.html app/templates/scenarios/_criteria.html app/templates/scenarios/index.html tests/test_routes_scenarios.py
git commit -m "refactor: extract criterion row partial, dedupe index.html criteria markup"
```

---

### Task 3: Click-to-edit routes for existing criteria

**Files:**
- Create: `app/templates/scenarios/_criterion_edit.html`
- Modify: `app/routes/scenarios.py` (add routes after `delete_criterion`, around line 118)
- Test: `tests/test_routes_scenarios.py` (add after `test_delete_criterion`, around line 58)

**Interfaces:**
- Consumes: `q.get_criterion`, `q.update_criterion` (Task 1)
- Produces: `GET /criteria/{criterion_id}/edit`, `GET /criteria/{criterion_id}`, `POST /criteria/{criterion_id}` routes

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_routes_scenarios.py`:

```python
def test_edit_criterion_form_returns_fields(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    cid = q.insert_criterion(conn, sid, "Must be remote", "must")
    resp = client.get(f"/criteria/{cid}/edit")
    assert resp.status_code == 200
    assert "Must be remote" in resp.text


def test_update_criterion_route(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    cid = q.insert_criterion(conn, sid, "Must be remote", "must")
    resp = client.post(f"/criteria/{cid}", data={"text": "Must be fully remote", "weight": "prefer"})
    assert resp.status_code == 200
    assert "Must be fully remote" in resp.text
    criterion = q.get_criterion(conn, cid)
    assert criterion["text"] == "Must be fully remote"
    assert criterion["weight"] == "prefer"


def test_update_criterion_leaves_source_unchanged(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    cid = q.insert_criterion(conn, sid, "Must be remote", "must", source="feedback")
    client.post(f"/criteria/{cid}", data={"text": "Must be fully remote", "weight": "prefer"})
    assert q.get_criterion(conn, cid)["source"] == "feedback"


def test_cancel_criterion_edit_returns_display_row(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    cid = q.insert_criterion(conn, sid, "Must be remote", "must")
    resp = client.get(f"/criteria/{cid}")
    assert resp.status_code == 200
    assert "Must be remote" in resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_routes_scenarios.py -k criterion -v`
Expected: FAIL with 404s (routes don't exist yet)

- [ ] **Step 3: Create `_criterion_edit.html`**

```html
<li id="criterion-{{ c.id }}" style="display:flex; align-items:center; gap:0.5rem; margin-bottom:0.3rem;">
  <form hx-post="/criteria/{{ c.id }}"
        hx-target="#criterion-{{ c.id }}"
        hx-swap="outerHTML"
        style="display:flex; gap:0.5rem; flex:1; align-items:center;">
    <select name="weight">
      <option value="must" {% if c.weight == "must" %}selected{% endif %}>must</option>
      <option value="prefer" {% if c.weight == "prefer" %}selected{% endif %}>prefer</option>
      <option value="avoid" {% if c.weight == "avoid" %}selected{% endif %}>avoid</option>
    </select>
    <input type="text" name="text" value="{{ c.text }}" required style="flex:1; min-width:200px;">
    <button type="submit" class="btn">Save</button>
    <button type="button" class="btn"
      hx-get="/criteria/{{ c.id }}"
      hx-target="#criterion-{{ c.id }}"
      hx-swap="outerHTML">Cancel</button>
  </form>
</li>
```

- [ ] **Step 4: Add the routes**

In `app/routes/scenarios.py`, add a helper near `_get_scenario_or_404` (around line 65-69):

```python
def _get_criterion_or_404(conn: sqlite3.Connection, criterion_id: int) -> dict:
    criterion = q.get_criterion(conn, criterion_id)
    if not criterion:
        raise HTTPException(status_code=404, detail="Criterion not found")
    return criterion
```

Then, immediately after the existing `delete_criterion` route (around line 118), add:

```python
@router.get("/criteria/{criterion_id}/edit", response_class=HTMLResponse)
def edit_criterion_form(criterion_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    criterion = _get_criterion_or_404(conn, criterion_id)
    return templates.TemplateResponse(request, "scenarios/_criterion_edit.html", {"c": criterion})


@router.get("/criteria/{criterion_id}", response_class=HTMLResponse)
def criterion_row(criterion_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    criterion = _get_criterion_or_404(conn, criterion_id)
    return templates.TemplateResponse(request, "scenarios/_criterion.html", {"c": criterion})


@router.post("/criteria/{criterion_id}", response_class=HTMLResponse)
def update_criterion(
    criterion_id: int,
    request: Request,
    text: str = Form(...),
    weight: str = Form(...),
    conn: sqlite3.Connection = Depends(get_db),
):
    _get_criterion_or_404(conn, criterion_id)
    q.update_criterion(conn, criterion_id, text=text, weight=weight)
    criterion = q.get_criterion(conn, criterion_id)
    return templates.TemplateResponse(request, "scenarios/_criterion.html", {"c": criterion})
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_routes_scenarios.py -k criterion -v`
Expected: PASS

- [ ] **Step 6: Run the full test suite**

Run: `pytest -v`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add app/templates/scenarios/_criterion_edit.html app/routes/scenarios.py tests/test_routes_scenarios.py
git commit -m "feat: click-to-edit for existing scenario criteria"
```

---

### Task 4: Match AI "remove" proposals to existing criteria

**Files:**
- Modify: `app/ai/refine.py`
- Test: `tests/test_refine.py`

**Interfaces:**
- Produces: `match_removal_target(proposal_text: str, existing_criteria: list[dict]) -> int | None` — `existing_criteria` items must have `id` and `text` keys.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_refine.py`:

```python
from app.ai.refine import match_removal_target


_EXISTING_WITH_IDS = [
    {"id": 1, "text": "Must be remote"},
    {"id": 2, "text": "Must have Python experience"},
]


def test_match_removal_target_exact_match():
    assert match_removal_target("Must be remote", _EXISTING_WITH_IDS) == 1


def test_match_removal_target_ignores_case_and_surrounding_whitespace():
    assert match_removal_target("  must be remote  ", _EXISTING_WITH_IDS) == 1


def test_match_removal_target_no_match_returns_none():
    assert match_removal_target("Must have a PhD", _EXISTING_WITH_IDS) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_refine.py -k match_removal -v`
Expected: FAIL with `ImportError: cannot import name 'match_removal_target'`

- [ ] **Step 3: Implement `match_removal_target`**

In `app/ai/refine.py`, add after the `propose_criteria` function:

```python
def match_removal_target(proposal_text: str, existing_criteria: list[dict]) -> int | None:
    target = proposal_text.strip().casefold()
    for criterion in existing_criteria:
        if criterion["text"].strip().casefold() == target:
            return criterion["id"]
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_refine.py -k match_removal -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/ai/refine.py tests/test_refine.py
git commit -m "feat: match AI remove-proposals to existing criteria by normalized text"
```

---

### Task 5: Editable "add" proposals, resolved "remove" proposals in the refine route

**Files:**
- Modify: `app/routes/scenarios.py` (`refine_criteria`, around lines 121-146)
- Modify: `app/templates/scenarios/_proposals.html`
- Test: `tests/test_routes_scenarios.py` (extend `test_refine_returns_proposals` area, around line 60-69)

**Interfaces:**
- Consumes: `match_removal_target` (Task 4), `CriterionProposal` (existing, from `app.ai.refine`)
- Produces: `_proposals.html` now expects each item in `proposals` to be a dict with keys `text`, `weight`, `action`, `criterion_id` (`criterion_id` is `None` for `action == "add"`)

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_routes_scenarios.py`, near `test_refine_returns_proposals`:

```python
def test_refine_add_proposal_has_editable_inputs(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Must be remote", "must")
    proposals = [CriterionProposal(text="Must be senior", weight="must", action="add")]
    with patch("app.routes.scenarios.propose_criteria", return_value=proposals):
        resp = client.post(f"/scenarios/{sid}/refine")
    assert 'name="text" value="Must be senior"' in resp.text
    assert 'name="weight"' in resp.text


def test_refine_remove_proposal_matched_shows_remove_button(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    cid = q.insert_criterion(conn, sid, "Must be remote", "must")
    proposals = [CriterionProposal(text="Must be remote", weight="must", action="remove")]
    with patch("app.routes.scenarios.propose_criteria", return_value=proposals):
        resp = client.post(f"/scenarios/{sid}/refine")
    assert f"/criteria/{cid}/remove-proposal" in resp.text
    assert ">Remove<" in resp.text


def test_refine_remove_proposal_unmatched_is_omitted(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Must be remote", "must")
    proposals = [CriterionProposal(text="Must have a PhD", weight="must", action="remove")]
    with patch("app.routes.scenarios.propose_criteria", return_value=proposals):
        resp = client.post(f"/scenarios/{sid}/refine")
    assert "Must have a PhD" not in resp.text
    assert "No changes proposed" in resp.text
```

Add the required import at the top of `tests/test_routes_scenarios.py` if not already present: `from app.ai.refine import CriterionProposal` (already imported — check line 4).

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_routes_scenarios.py -k "refine_add_proposal or refine_remove_proposal" -v`
Expected: FAIL (current template always renders hidden inputs + "Add" button, never omits, never links `/remove-proposal`)

- [ ] **Step 3: Update the `refine_criteria` route**

In `app/routes/scenarios.py`, update the import line (around line 8) and the route body:

```python
from app.ai.refine import propose_criteria, match_removal_target
```

Replace the `stream()` function body inside `refine_criteria` (around lines 133-144):

```python
    def stream():
        msg = f"Requesting criteria proposals for '{scenario['name']}' from LLM"
        logger.info(msg)
        yield msg + "\n"
        proposals = propose_criteria(client, model, scenario, existing, notes)
        msg = f"Received {len(proposals)} proposal(s)"
        logger.info(msg)
        yield msg + "\n"
        resolved = []
        for p in proposals:
            if p.action == "remove":
                criterion_id = match_removal_target(p.text, existing)
                if criterion_id is None:
                    continue
                resolved.append({"text": p.text, "weight": p.weight, "action": "remove", "criterion_id": criterion_id})
            else:
                resolved.append({"text": p.text, "weight": p.weight, "action": "add", "criterion_id": None})
        html = templates.get_template("scenarios/_proposals.html").render(
            request=request, proposals=resolved, scenario_id=scenario_id
        )
        yield "HTML:" + html.replace("\n", "")
```

- [ ] **Step 4: Update `_proposals.html`**

Replace the full file content with:

```html
{% if proposals %}
<p><strong>Proposed criteria changes.</strong> Review each one below:</p>
{% for p in proposals %}
<div id="proposal-{{ loop.index0 }}" style="display:flex; gap:0.5rem; align-items:center; margin-bottom:0.5rem; padding:0.4rem; background:#f8f9fa; border-radius:4px;">
  {% if p.action == "remove" %}
  <span class="tag">remove</span>
  <span style="flex:1;">{{ p.text | markdown_inline }}</span>
  <button class="btn btn-reject" style="font-size:0.85em;"
    hx-delete="/criteria/{{ p.criterion_id }}/remove-proposal"
    hx-target="#proposal-{{ loop.index0 }}"
    hx-swap="outerHTML">Remove</button>
  {% else %}
  <form hx-post="/scenarios/{{ scenario_id }}/criteria"
        hx-target="#criteria-{{ scenario_id }}"
        hx-swap="outerHTML"
        style="display:flex; gap:0.5rem; flex:1; align-items:center;">
    <select name="weight">
      <option value="must" {% if p.weight == "must" %}selected{% endif %}>must</option>
      <option value="prefer" {% if p.weight == "prefer" %}selected{% endif %}>prefer</option>
      <option value="avoid" {% if p.weight == "avoid" %}selected{% endif %}>avoid</option>
    </select>
    <input type="text" name="text" value="{{ p.text }}" style="flex:1; min-width:200px;">
    <button type="submit" class="btn btn-accept" style="font-size:0.85em;">Add</button>
  </form>
  {% endif %}
</div>
{% endfor %}
{% else %}
<p>No changes proposed based on current feedback.</p>
{% endif %}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_routes_scenarios.py -k "refine" -v`
Expected: PASS. (`test_refine_remove_proposal_matched_shows_remove_button` will still fail until Task 6 adds the `/remove-proposal` route — that's fine, it 404s but the link text/href are already correct; if the test asserts only on rendered HTML content, not on following the link, it should pass now. Confirm before moving on.)

- [ ] **Step 6: Run the full test suite**

Run: `pytest -v`
Expected: all PASS except possibly the not-yet-relevant remove-proposal *route* behavior (there is no such test yet — that's Task 6)

- [ ] **Step 7: Commit**

```bash
git add app/routes/scenarios.py app/templates/scenarios/_proposals.html tests/test_routes_scenarios.py
git commit -m "feat: make add-proposals editable, resolve remove-proposals to matched criteria"
```

---

### Task 6: Wire up the "Remove" button to actually delete the criterion

**Files:**
- Modify: `app/routes/scenarios.py` (add route after `delete_criterion`, or after Task 3's new routes)
- Test: `tests/test_routes_scenarios.py`

**Interfaces:**
- Consumes: `q.delete_criterion` (existing)
- Produces: `DELETE /criteria/{criterion_id}/remove-proposal`

This uses an htmx out-of-band swap: the response contains a single element whose `id` matches the criterion's list row (`criterion-{id}`) with `hx-swap-oob="delete"`, which removes that row from the criteria list wherever it appears on the page. Since that OOB element is the *entire* response body, htmx also swaps the primary target (the proposal row that triggered the request, via its own `hx-target`/`hx-swap="outerHTML"`) with what's left after extracting the OOB element — nothing — so the proposal row disappears too. This mirrors exactly how the existing `delete_criterion` route already removes its own row with `HTMLResponse(content="")`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_routes_scenarios.py`:

```python
def test_remove_proposal_deletes_criterion(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    cid = q.insert_criterion(conn, sid, "Must be remote", "must")
    resp = client.delete(f"/criteria/{cid}/remove-proposal")
    assert resp.status_code == 200
    assert q.get_criterion(conn, cid) is None


def test_remove_proposal_response_marks_criterion_row_for_oob_delete(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    cid = q.insert_criterion(conn, sid, "Must be remote", "must")
    resp = client.delete(f"/criteria/{cid}/remove-proposal")
    assert f'id="criterion-{cid}"' in resp.text
    assert 'hx-swap-oob="delete"' in resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_routes_scenarios.py -k remove_proposal -v`
Expected: FAIL with 404 (route doesn't exist)

- [ ] **Step 3: Add the route**

In `app/routes/scenarios.py`, add after the Task 3 criterion routes:

```python
@router.delete("/criteria/{criterion_id}/remove-proposal", response_class=HTMLResponse)
def remove_criterion_via_proposal(criterion_id: int, conn: sqlite3.Connection = Depends(get_db)):
    q.delete_criterion(conn, criterion_id)
    return HTMLResponse(content=f'<li id="criterion-{criterion_id}" hx-swap-oob="delete"></li>')
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_routes_scenarios.py -k remove_proposal -v`
Expected: PASS

- [ ] **Step 5: Run the full test suite**

Run: `pytest -v`
Expected: all PASS, including `test_refine_remove_proposal_matched_shows_remove_button` from Task 5

- [ ] **Step 6: Manual smoke check (documented, not automated)**

This step has no automated test — htmx OOB behavior is a browser-side concern pytest/TestClient can't observe. Note in the PR/summary that this should be spot-checked in a running browser: open a scenario with at least one criterion, click "Refine criteria from feedback" with feedback notes that would plausibly cause an LLM to propose removing that criterion (or temporarily patch `propose_criteria` locally to force a `remove` proposal), click "Remove", and confirm both the proposal row and the criterion list row disappear without a page reload.

- [ ] **Step 7: Commit**

```bash
git add app/routes/scenarios.py tests/test_routes_scenarios.py
git commit -m "feat: wire up remove-proposal button to delete the matched criterion"
```

---

### Task 7: Confirm staleness coverage, final full-suite check

**Files:**
- None modified (verification only) — unless the check in Step 1 finds a gap, in which case modify `tests/test_scenario_version.py`

- [ ] **Step 1: Confirm existing hash test covers criterion edits**

Read `tests/test_scenario_version.py::test_hash_changes_with_criteria` (around line 17-22). Confirm it asserts that changing a criterion's `text` or `weight` in the list passed to `compute_version_hash` changes the resulting hash. Since `update_criterion` (Task 1) only ever changes `text`/`weight` on an existing row, and `get_criteria` (used to build the hash input) picks that up automatically, this existing test already covers "editing a criterion invalidates cached scores" — no new test needed.

If that test does NOT cover a text/weight change (only e.g. count changes), add:

```python
def test_hash_changes_when_criterion_text_edited():
    scenario = {"description": "A"}
    before = compute_version_hash(scenario, [{"text": "Must be remote", "weight": "must"}])
    after = compute_version_hash(scenario, [{"text": "Must be fully remote", "weight": "must"}])
    assert before != after
```

- [ ] **Step 2: Run the full test suite**

Run: `pytest -v`
Expected: all PASS

- [ ] **Step 3: Commit (only if Step 1 added a test)**

```bash
git add tests/test_scenario_version.py
git commit -m "test: confirm criterion text edits invalidate the scenario version hash"
```
