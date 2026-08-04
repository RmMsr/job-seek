# Scenario Suggestions UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make accepted scenario-criteria suggestions disappear from the proposals panel, add a non-persisted per-suggestion dismiss button, add a bulk "get suggestions for all scenarios" action, and order criteria/suggestions must > prefer > avoid.

**Architecture:** Server-rendered FastAPI + Jinja2 + HTMX app. Extends two existing patterns rather than inventing new ones: HTMX out-of-band (OOB) swaps (already used to delete a criterion row after a "remove" proposal is actioned) and the generic streaming "progress button" in `base.html` (already used by "Re-evaluate all scenarios"), which gains an opt-in multi-target OOB streaming mode.

**Tech Stack:** Python 3, FastAPI, Jinja2, HTMX 1.9.12, sqlite3, pytest + FastAPI `TestClient`.

## Global Constraints

- No schema/migration changes — ordering is a query-level `ORDER BY`, not a stored column.
- `proposal_id` values reflected into HTML must be validated against `^proposal-\d+-\d+$` before being echoed back, since they arrive as unauthenticated form/query input.
- Follow the spec exactly at `docs/superpowers/specs/2026-08-04-scenario-suggestions-ux-design.md` — in particular, do **not** add OOB-delete logic to `remove_criterion_via_proposal`; that path already removes its proposal row correctly via existing `hx-target`/`hx-swap="outerHTML"` semantics (see spec §2, "Remove-type: already correct, id just needs rescoping"). Only its `id`/`hx-target` selector format changes.

---

### Task 1: Order criteria must > prefer > avoid

**Files:**
- Modify: `app/db/queries.py:89-94` (`get_criteria`)
- Test: `tests/test_db_queries.py` (create if it doesn't already cover `get_criteria`; check first)

**Interfaces:**
- Produces: `get_criteria(conn, scenario_id) -> list[dict]` now returns rows ordered `must`, `prefer`, `avoid`, then `created_at` within each group. No signature change — existing callers (`app/routes/scenarios.py`) are unaffected by the interface, only by row order.

- [ ] **Step 1: Check for an existing test file covering `get_criteria`**

Run: `grep -rn "get_criteria" /path/to/job-seek/tests/`

If a `tests/test_db_queries.py` exists and already has criteria tests, add the new test there. Otherwise create `tests/test_db_queries.py` with the imports below.

- [ ] **Step 2: Write the failing test**

```python
import sqlite3
import pytest
from app.db.schema import init_db
from app.db import queries as q


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:", check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    init_db(c)
    yield c
    c.close()


def test_get_criteria_orders_must_prefer_avoid(conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    # Insert out of weight order to prove sorting isn't just created_at passthrough.
    q.insert_criterion(conn, sid, "Avoid startups", "avoid")
    q.insert_criterion(conn, sid, "Prefer Python", "prefer")
    q.insert_criterion(conn, sid, "Must be remote", "must")
    q.insert_criterion(conn, sid, "Must pay well", "must")

    criteria = q.get_criteria(conn, sid)

    assert [c["weight"] for c in criteria] == ["must", "must", "prefer", "avoid"]
    # Within the same weight, original (created_at) order is preserved.
    assert [c["text"] for c in criteria if c["weight"] == "must"] == ["Must be remote", "Must pay well"]
```

(Omit the fixture if `tests/test_db_queries.py` already exists with an equivalent `conn` fixture — reuse it instead of duplicating.)

- [ ] **Step 3: Run test to verify it fails**

Run: `cd /path/to/job-seek && python -m pytest tests/test_db_queries.py::test_get_criteria_orders_must_prefer_avoid -v`
Expected: FAIL — criteria come back in insertion order (`avoid, prefer, must, must`), not weight order.

- [ ] **Step 4: Implement the ordering**

In `app/db/queries.py`, replace:

```python
def get_criteria(conn: sqlite3.Connection, scenario_id: int) -> list[dict]:
    return _rows_to_dicts(
        conn.execute(
            "SELECT * FROM criteria WHERE scenario_id = ? ORDER BY created_at", (scenario_id,)
        ).fetchall()
    )
```

with:

```python
def get_criteria(conn: sqlite3.Connection, scenario_id: int) -> list[dict]:
    return _rows_to_dicts(
        conn.execute(
            """
            SELECT * FROM criteria WHERE scenario_id = ?
            ORDER BY CASE weight WHEN 'must' THEN 0 WHEN 'prefer' THEN 1 WHEN 'avoid' THEN 2 ELSE 3 END,
                     created_at
            """,
            (scenario_id,),
        ).fetchall()
    )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /path/to/job-seek && python -m pytest tests/test_db_queries.py::test_get_criteria_orders_must_prefer_avoid -v`
Expected: PASS

- [ ] **Step 6: Run the full existing test suite to check for regressions**

Run: `cd /path/to/job-seek && python -m pytest tests/ -v`
Expected: all PASS (existing tests insert criteria in ascending must/prefer/avoid-friendly order already, e.g. `test_scenarios_page_renders_criterion_markdown` inserts a single criterion, so this should not break anything).

- [ ] **Step 7: Commit**

```bash
git add app/db/queries.py tests/test_db_queries.py
git commit -m "feat: order criteria must > prefer > avoid"
```

---

### Task 2: Extract shared proposal-resolution helper and sort proposals by weight

**Files:**
- Modify: `app/routes/scenarios.py` (add `_resolve_proposals`, use it in `refine_criteria`)
- Test: `tests/test_routes_scenarios.py`

**Interfaces:**
- Consumes: `CriterionProposal` from `app.ai.refine` (fields: `text: str`, `weight: str`, `action: str`); `match_removal_target(text: str, existing: list[dict]) -> int | None` from `app.ai.refine`.
- Produces: `_resolve_proposals(proposals: list[CriterionProposal], existing: list[dict]) -> list[dict]` in `app/routes/scenarios.py`. Each returned dict has keys `text`, `weight`, `action` (`"add"` or `"remove"`), `criterion_id` (`int | None`). Sorted must, prefer, avoid. This is consumed by Task 4's new bulk-refine route.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_routes_scenarios.py`:

```python
def test_refine_proposals_sorted_must_prefer_avoid(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Must be remote", "must")
    proposals = [
        CriterionProposal(text="Avoid on-call", weight="avoid", action="add"),
        CriterionProposal(text="Must pay well", weight="must", action="add"),
        CriterionProposal(text="Prefer Python", weight="prefer", action="add"),
    ]
    with patch("app.routes.scenarios.propose_criteria", return_value=proposals):
        resp = client.post(f"/scenarios/{sid}/refine")
    text = resp.text
    must_pos = text.index("Must pay well")
    prefer_pos = text.index("Prefer Python")
    avoid_pos = text.index("Avoid on-call")
    assert must_pos < prefer_pos < avoid_pos
```

This test needs `q` imported (`from app.db import queries as q`) — already present at the top of `tests/test_routes_scenarios.py`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /path/to/job-seek && python -m pytest tests/test_routes_scenarios.py::test_refine_proposals_sorted_must_prefer_avoid -v`
Expected: FAIL — proposals currently render in the LLM's returned order (avoid, must, prefer), not weight-sorted.

- [ ] **Step 3: Extract `_resolve_proposals` and use it in `refine_criteria`**

In `app/routes/scenarios.py`, add near the top (after the router/logger setup, before route definitions):

```python
_WEIGHT_ORDER = {"must": 0, "prefer": 1, "avoid": 2}


def _resolve_proposals(proposals: list, existing: list[dict]) -> list[dict]:
    resolved = []
    for p in proposals:
        if p.action == "remove":
            criterion_id = match_removal_target(p.text, existing)
            if criterion_id is None:
                continue
            resolved.append({"text": p.text, "weight": p.weight, "action": "remove", "criterion_id": criterion_id})
        else:
            resolved.append({"text": p.text, "weight": p.weight, "action": "add", "criterion_id": None})
    resolved.sort(key=lambda r: _WEIGHT_ORDER.get(r["weight"], 3))
    return resolved
```

Then replace the resolution loop inside `refine_criteria`'s `stream()`:

```python
        resolved = []
        for p in proposals:
            if p.action == "remove":
                criterion_id = match_removal_target(p.text, existing)
                if criterion_id is None:
                    continue
                resolved.append({"text": p.text, "weight": p.weight, "action": "remove", "criterion_id": criterion_id})
            else:
                resolved.append({"text": p.text, "weight": p.weight, "action": "add", "criterion_id": None})
```

with:

```python
        resolved = _resolve_proposals(proposals, existing)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /path/to/job-seek && python -m pytest tests/test_routes_scenarios.py::test_refine_proposals_sorted_must_prefer_avoid -v`
Expected: PASS

- [ ] **Step 5: Run the full existing test suite to check for regressions**

Run: `cd /path/to/job-seek && python -m pytest tests/ -v`
Expected: all PASS — `test_refine_remove_proposal_unmatched_is_omitted` and similar still hold since `_resolve_proposals` preserves the exact prior filtering logic.

- [ ] **Step 6: Commit**

```bash
git add app/routes/scenarios.py tests/test_routes_scenarios.py
git commit -m "refactor: extract _resolve_proposals helper, sort proposals must > prefer > avoid"
```

---

### Task 3: Scope proposal row ids to the scenario, and rewire the accept-form to auto-remove its row

**Files:**
- Modify: `app/templates/scenarios/_proposals.html`
- Modify: `app/routes/scenarios.py` (`add_criterion`)
- Test: `tests/test_routes_scenarios.py`

**Interfaces:**
- Consumes: `_resolve_proposals` output from Task 2 (dicts with `text`, `weight`, `action`, `criterion_id`).
- Produces: `add_criterion` now accepts an optional `proposal_id: str | None = Form(None)` field. When present and matching `^proposal-\d+-\d+$`, the response has an appended `<div id="{proposal_id}" hx-swap-oob="delete"></div>`. `_proposals.html` row ids become `proposal-{{ scenario_id }}-{{ loop.index0 }}` (was `proposal-{{ loop.index0 }}`), and the Remove button's `hx-target` is updated to match.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_routes_scenarios.py`:

```python
def test_accept_add_proposal_marks_proposal_row_for_oob_delete(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    resp = client.post(
        f"/scenarios/{sid}/criteria",
        data={"text": "Must be senior", "weight": "must", "proposal_id": "proposal-{}-0".format(sid)},
    )
    assert resp.status_code == 200
    assert f'id="proposal-{sid}-0"' in resp.text
    assert 'hx-swap-oob="delete"' in resp.text


def test_manual_add_criterion_has_no_oob_delete(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    resp = client.post(f"/scenarios/{sid}/criteria", data={"text": "Must be senior", "weight": "must"})
    assert resp.status_code == 200
    assert 'hx-swap-oob="delete"' not in resp.text


def test_accept_add_proposal_rejects_malformed_proposal_id(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    resp = client.post(
        f"/scenarios/{sid}/criteria",
        data={"text": "Must be senior", "weight": "must", "proposal_id": "<script>alert(1)</script>"},
    )
    assert resp.status_code == 200
    assert "<script>" not in resp.text
    assert 'hx-swap-oob="delete"' not in resp.text


def test_refine_proposal_rows_are_scenario_scoped(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Must be remote", "must")
    proposals = [CriterionProposal(text="Must be senior", weight="must", action="add")]
    with patch("app.routes.scenarios.propose_criteria", return_value=proposals):
        resp = client.post(f"/scenarios/{sid}/refine")
    assert f'id="proposal-{sid}-0"' in resp.text
    assert f'name="proposal_id" value="proposal-{sid}-0"' in resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /path/to/job-seek && python -m pytest tests/test_routes_scenarios.py -k "proposal_row or oob_delete or malformed_proposal_id or scenario_scoped" -v`
Expected: FAIL — `add_criterion` doesn't accept `proposal_id` yet, and `_proposals.html` rows are still `proposal-{{ loop.index0 }}` with no hidden field.

- [ ] **Step 3: Update `_proposals.html`**

Full replacement for `app/templates/scenarios/_proposals.html`:

```html
{% if proposals %}
<p><strong>Proposed criteria changes.</strong> Review each one below:</p>
{% for p in proposals %}
<div id="proposal-{{ scenario_id }}-{{ loop.index0 }}" style="display:flex; gap:0.5rem; align-items:center; margin-bottom:0.5rem; padding:0.4rem; background:#f8f9fa; border-radius:4px;">
  {% if p.action == "remove" %}
  <span class="tag">remove</span>
  <span style="flex:1;">{{ p.text | markdown_inline }}</span>
  <button class="btn btn-reject" style="font-size:0.85em;"
    hx-delete="/criteria/{{ p.criterion_id }}/remove-proposal"
    hx-target="#proposal-{{ scenario_id }}-{{ loop.index0 }}"
    hx-swap="outerHTML">Remove</button>
  {% else %}
  <form hx-post="/scenarios/{{ scenario_id }}/criteria"
        hx-target="#criteria-{{ scenario_id }}"
        hx-swap="outerHTML"
        style="display:flex; gap:0.5rem; flex:1; align-items:center;">
    <input type="hidden" name="proposal_id" value="proposal-{{ scenario_id }}-{{ loop.index0 }}">
    <select name="weight">
      <option value="must" {% if p.weight == "must" %}selected{% endif %}>must</option>
      <option value="prefer" {% if p.weight == "prefer" %}selected{% endif %}>prefer</option>
      <option value="avoid" {% if p.weight == "avoid" %}selected{% endif %}>avoid</option>
    </select>
    <input type="text" name="text" value="{{ p.text }}" style="flex:1; min-width:200px;">
    <button type="submit" class="btn btn-accept" style="font-size:0.85em;">Add</button>
  </form>
  {% endif %}
  <button type="button" class="btn btn-invalid proposal-dismiss" style="font-size:0.85em;">Dismiss</button>
</div>
{% endfor %}
{% else %}
<p>No changes proposed based on current feedback.</p>
{% endif %}
```

(This also folds in Task 5's Dismiss button, since it lives on the same row markup — see Task 5 below for the JS wiring that makes it functional. Rendering the button now, before its JS exists, is harmless: it's an inert `type="button"` with no handler yet.)

- [ ] **Step 4: Update `add_criterion` in `app/routes/scenarios.py`**

Add `import re` to the top of the file if not already present. Replace:

```python
@router.post("/scenarios/{scenario_id}/criteria", response_class=HTMLResponse)
def add_criterion(
    scenario_id: int,
    request: Request,
    text: str = Form(...),
    weight: str = Form(...),
    conn: sqlite3.Connection = Depends(get_db),
):
    q.insert_criterion(conn, scenario_id, text, weight)
    criteria = q.get_criteria(conn, scenario_id)
    return templates.TemplateResponse(
        request,
        "scenarios/_criteria.html",
        {"criteria": criteria, "scenario_id": scenario_id},
    )
```

with:

```python
_PROPOSAL_ID_RE = re.compile(r"proposal-\d+-\d+")


@router.post("/scenarios/{scenario_id}/criteria", response_class=HTMLResponse)
def add_criterion(
    scenario_id: int,
    request: Request,
    text: str = Form(...),
    weight: str = Form(...),
    proposal_id: str | None = Form(None),
    conn: sqlite3.Connection = Depends(get_db),
):
    q.insert_criterion(conn, scenario_id, text, weight)
    criteria = q.get_criteria(conn, scenario_id)
    html = templates.get_template("scenarios/_criteria.html").render(
        request=request, criteria=criteria, scenario_id=scenario_id
    )
    if proposal_id and _PROPOSAL_ID_RE.fullmatch(proposal_id):
        html += f'<div id="{proposal_id}" hx-swap-oob="delete"></div>'
    return HTMLResponse(content=html)
```

(`_PROPOSAL_ID_RE` is placed near `_resolve_proposals`/module-level constants from Task 2, above the route functions.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /path/to/job-seek && python -m pytest tests/test_routes_scenarios.py -k "proposal_row or oob_delete or malformed_proposal_id or scenario_scoped" -v`
Expected: PASS

- [ ] **Step 6: Run the full existing test suite to check for regressions**

Run: `cd /path/to/job-seek && python -m pytest tests/ -v`
Expected: all PASS. In particular `test_add_criterion_response_wraps_list_and_form_in_shared_target` still holds — the `<div id="criteria-{sid}">...</div>` wrapper content is unchanged, only appended-after content differs.

- [ ] **Step 7: Commit**

```bash
git add app/templates/scenarios/_proposals.html app/routes/scenarios.py tests/test_routes_scenarios.py
git commit -m "feat: auto-remove accepted add-proposal row, scope proposal ids per scenario"
```

---

### Task 4: Bulk "get suggestions for all scenarios"

**Files:**
- Modify: `app/routes/scenarios.py` (new `refine_all_scenarios` route)
- Test: `tests/test_routes_scenarios.py`

**Interfaces:**
- Consumes: `_resolve_proposals` from Task 2; `q.get_scenarios`, `q.get_criteria`, `q.get_recent_feedback_notes` from `app.db.queries`; `propose_criteria` from `app.ai.refine`; `templates.get_template("scenarios/_proposals.html")`.
- Produces: `POST /scenarios/refine` — a `StreamingResponse` (`text/plain`) yielding, per scenario in order: a `"[Scenario i/N: name] Requesting criteria proposals from LLM\n"` line, a `"[Scenario i/N: name] Received K proposal(s)\n"` line, then an `"HTML:"`-prefixed line (no embedded newlines) wrapping that scenario's rendered `_proposals.html` in `<div id="proposals-area-{scenario_id}" hx-swap-oob="true">...</div>`. This is consumed by Task 6's new front-end button/JS.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_routes_scenarios.py`:

```python
def test_refine_all_scenarios_streams_per_scenario_oob_html(client, conn):
    sid_a = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid_a, "Must be remote", "must")
    sid_b = q.insert_scenario(conn, "Robotics", "")
    q.insert_criterion(conn, sid_b, "Must involve embedded systems", "must")

    proposals = [CriterionProposal(text="Must be senior", weight="must", action="add")]
    with patch("app.routes.scenarios.propose_criteria", return_value=proposals):
        resp = client.post("/scenarios/refine")

    assert resp.status_code == 200
    text = resp.text
    assert "Refining criteria for 2 scenario(s)" in text
    assert f'id="proposals-area-{sid_a}" hx-swap-oob="true"' in text
    assert f'id="proposals-area-{sid_b}" hx-swap-oob="true"' in text

    lines = text.split("\n")
    html_lines = [line for line in lines if line.startswith("HTML:")]
    assert len(html_lines) == 2
    # Scenario order is preserved: scenario A's chunk arrives before B's.
    assert f"proposals-area-{sid_a}" in html_lines[0]
    assert f"proposals-area-{sid_b}" in html_lines[1]


def test_refine_all_scenarios_with_no_scenarios(client, conn):
    resp = client.post("/scenarios/refine")
    assert resp.status_code == 200
    assert "Refining criteria for 0 scenario(s)" in resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /path/to/job-seek && python -m pytest tests/test_routes_scenarios.py -k "refine_all_scenarios" -v`
Expected: FAIL with 404 — `POST /scenarios/refine` doesn't exist yet.

- [ ] **Step 3: Implement the route**

In `app/routes/scenarios.py`, add this route. Place it near `reevaluate_all_scenarios` (after it), and **before** the `{scenario_id}`-parameterized routes so FastAPI's path matching doesn't need to worry about ordering ambiguity (`/scenarios/refine` vs `/scenarios/{scenario_id}` — FastAPI matches literal paths before path-param patterns registered later in the same router table only if declared earlier; keep it next to `reevaluate_all_scenarios`, which already sits above the `{scenario_id}` routes, to follow the same precedent):

```python
@router.post("/scenarios/refine")
def refine_all_scenarios(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    client: openai.OpenAI = Depends(get_ai_client),
    model: str = Depends(get_model),
):
    scenarios = q.get_scenarios(conn)

    def stream():
        yield f"Refining criteria for {len(scenarios)} scenario(s)\n"
        for idx, scenario in enumerate(scenarios, start=1):
            label = f"[Scenario {idx}/{len(scenarios)}: {scenario['name']}] "
            yield label + "Requesting criteria proposals from LLM\n"
            existing = q.get_criteria(conn, scenario["id"])
            notes = q.get_recent_feedback_notes(conn, scenario["id"])
            proposals = propose_criteria(client, model, scenario, existing, notes)
            resolved = _resolve_proposals(proposals, existing)
            yield label + f"Received {len(resolved)} proposal(s)\n"
            html = templates.get_template("scenarios/_proposals.html").render(
                request=request, proposals=resolved, scenario_id=scenario["id"]
            )
            chunk = f'<div id="proposals-area-{scenario["id"]}" hx-swap-oob="true">{html}</div>'
            yield "HTML:" + chunk.replace("\n", "") + "\n"
        yield f"Refined criteria proposals for {len(scenarios)} scenario(s)\n"

    return StreamingResponse(stream(), media_type="text/plain")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /path/to/job-seek && python -m pytest tests/test_routes_scenarios.py -k "refine_all_scenarios" -v`
Expected: PASS

- [ ] **Step 5: Run the full existing test suite to check for regressions**

Run: `cd /path/to/job-seek && python -m pytest tests/ -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add app/routes/scenarios.py tests/test_routes_scenarios.py
git commit -m "feat: add bulk refine-all-scenarios endpoint"
```

---

### Task 5: Client-side dismiss button

**Files:**
- Modify: `app/templates/base.html`

**Interfaces:**
- Consumes: the `.proposal-dismiss` button and `[id^="proposal-"]` row wrapper rendered by `_proposals.html` (Task 3).
- Produces: no new interface for other tasks — this is a leaf, purely client-side behavior.

- [ ] **Step 1: Add the delegated click listener**

In `app/templates/base.html`, add a new `<script>` block just before `</body>` (after the existing drag-select script block that ends around line 238):

```html
  <script>
    (function () {
      document.body.addEventListener("click", function (evt) {
        if (!evt.target.classList.contains("proposal-dismiss")) return;
        var row = evt.target.closest('[id^="proposal-"]');
        if (row) row.remove();
      });
    })();
  </script>
```

- [ ] **Step 2: Manual verification (no automated test — see spec's Testing section rationale)**

Run the app locally (see project's run instructions / `run` skill if unsure) and:
1. Navigate to `/scenarios`.
2. Click "Refine criteria from feedback" on a scenario with at least one proposal returned (mock or real, whichever is available locally).
3. Click "Dismiss" on a proposal row.
4. Confirm the row disappears immediately, with no network request in the browser's Network tab.
5. Reload the page and confirm nothing was persisted (the scenario's criteria are unchanged; a fresh refine would show the same proposal again).

- [ ] **Step 3: Run the full existing test suite to check for regressions**

Run: `cd /path/to/job-seek && python -m pytest tests/ -v`
Expected: all PASS (this task touches only `base.html`'s script block, no Python).

- [ ] **Step 4: Commit**

```bash
git add app/templates/base.html
git commit -m "feat: dismiss proposal row without persisting"
```

---

### Task 6: Bulk-refine button and multi-target OOB streaming JS

**Files:**
- Modify: `app/templates/base.html` (progress-button handler)
- Modify: `app/templates/scenarios/index.html` (new button)

**Interfaces:**
- Consumes: `POST /scenarios/refine` from Task 4, which streams `HTML:` lines each wrapping `<div id="proposals-area-{scenario_id}" hx-swap-oob="true">...</div>`.
- Produces: a `data-progress-oob` opt-in attribute usable by any future progress-button; a `#oob-scratch` element in `base.html`.

- [ ] **Step 1: Add the scratch element**

In `app/templates/base.html`, add just before the first `<script>` block (right after `<main>{% block content %}{% endblock %}</main>`):

```html
  <div id="oob-scratch" hidden></div>
```

- [ ] **Step 2: Extend the progress-button handler for `data-progress-oob`**

In `app/templates/base.html`, inside the first `<script>` block's `onClick` function, locate:

```javascript
        var url = el.getAttribute("data-progress-url");
        var targetSelector = el.getAttribute("data-progress-target");
```

Add right after it:

```javascript
        var oobMode = el.hasAttribute("data-progress-oob");
```

Locate the `handleLine` function:

```javascript
        function handleLine(line) {
          if (!line) return;
          if (line.indexOf("HTML:") === 0) {
            htmlChunk = line.slice(5);
          } else {
            lastLine = line;
            if (revealed) tick();
          }
        }
```

Replace with:

```javascript
        function applyOob(chunk) {
          var scratch = document.getElementById("oob-scratch");
          scratch.innerHTML = chunk;
          if (window.htmx) window.htmx.process(scratch);
        }

        function handleLine(line) {
          if (!line) return;
          if (line.indexOf("HTML:") === 0) {
            var chunk = line.slice(5);
            if (oobMode) {
              applyOob(chunk);
            } else {
              htmlChunk = chunk;
            }
          } else {
            lastLine = line;
            if (revealed) tick();
          }
        }
```

Locate `finish()`:

```javascript
        function finish() {
          clearTimeout(revealTimer);
          if (tickTimer) clearInterval(tickTimer);
          delete el.dataset.progressRunning;
          if (failed) {
            progressEl.hidden = false;
            progressEl.style.color = "#dc3545";
            progressEl.textContent = lastLine || "Failed";
            return;
          }
          progressEl.remove();
          if (targetSelector) {
            var target = document.querySelector(targetSelector);
            if (target && htmlChunk != null) {
              target.innerHTML = htmlChunk;
              if (window.htmx) window.htmx.process(target);
            }
          } else {
            location.reload();
          }
        }
```

Replace the `progressEl.remove(); ... } else { location.reload(); }` tail with:

```javascript
          progressEl.remove();
          if (oobMode) {
            // Each HTML: chunk was already applied as it streamed in via applyOob().
          } else if (targetSelector) {
            var target = document.querySelector(targetSelector);
            if (target && htmlChunk != null) {
              target.innerHTML = htmlChunk;
              if (window.htmx) window.htmx.process(target);
            }
          } else {
            location.reload();
          }
```

- [ ] **Step 3: Add the bulk-refine button to `scenarios/index.html`**

In `app/templates/scenarios/index.html`, locate:

```html
{% if scenarios %}
<button class="btn" data-progress-url="/scenarios/reevaluate" style="margin-bottom:1.5rem;">Re-evaluate all scenarios</button>
{% endif %}
```

Replace with:

```html
{% if scenarios %}
<div style="margin-bottom:1.5rem; display:flex; gap:0.5rem; flex-wrap:wrap;">
  <button class="btn" data-progress-url="/scenarios/reevaluate">Re-evaluate all scenarios</button>
  <button class="btn" data-progress-url="/scenarios/refine" data-progress-oob="1">Get suggestions for all scenarios</button>
</div>
{% endif %}
```

- [ ] **Step 4: Manual verification (no automated test for the JS itself)**

The streaming/OOB-routing behavior lives in browser JS (`fetch` + `ReadableStream` + `htmx.process`), which the project's test suite (FastAPI `TestClient`, no browser) cannot execute. The server-side contract (correct per-scenario `HTML:` chunks) is already covered by Task 4's tests. Verify the client behavior manually:

1. Run the app locally.
2. Create two scenarios, each with at least one criterion.
3. Click "Get suggestions for all scenarios".
4. Confirm both scenarios' proposal panels populate (progressively, one after the other) without a full page reload.
5. Confirm accepting or dismissing a suggestion in one scenario's panel doesn't affect the other's.

- [ ] **Step 5: Run the full existing test suite to check for regressions**

Run: `cd /path/to/job-seek && python -m pytest tests/ -v`
Expected: all PASS (template-only changes plus a JS extension gated behind a new opt-in attribute; `data-progress-target`-based buttons like "Re-evaluate all scenarios" and the single-scenario "Refine criteria from feedback" take the unchanged `oobMode === false` branch).

- [ ] **Step 6: Commit**

```bash
git add app/templates/base.html app/templates/scenarios/index.html
git commit -m "feat: bulk refine-all-scenarios button with multi-target OOB streaming"
```

---

## Post-plan checklist

- [ ] Re-read `docs/superpowers/specs/2026-08-04-scenario-suggestions-ux-design.md` once more and confirm every numbered section (1–4) has a corresponding completed task above.
- [ ] Confirm `python -m pytest tests/ -v` passes with zero failures after the final commit.
- [ ] If anything in this plan turned out to be wrong or underspecified during implementation and required a genuine design decision, that should already have been raised via `AskUserQuestion` rather than guessed — flag it in the final report if so.
