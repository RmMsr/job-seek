# Clear Add-Criteria Input on Add Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the add-criteria form so it visibly clears after a successful add, instead of leaving a stale, still-populated form on screen alongside the fresh one.

**Architecture:** Pure HTML/Jinja restructuring in `app/templates/scenarios/_criteria.html` — wrap the existing `<ul>` and `<form>` in one container `<div id="criteria-{{ scenario_id }}">`, moving the id off the `<ul>`. No Python, route, or query changes.

**Tech Stack:** FastAPI, Jinja2, HTMX (server-rendered, no client-side JS state).

## Global Constraints

- No route/handler changes — `add_criterion` (`app/routes/scenarios.py:105-119`) and `accept_proposals` already return the full `_criteria.html` partial; only the partial's markup structure changes.
- No new template files.
- The `#criteria-{{ scenario_id }}` id must keep being the swap target referenced by both `_criteria.html`'s own add-form and `_proposals.html:14`'s accept-form — do not rename it.

**Spec:** `docs/superpowers/specs/2026-08-02-clear-criteria-input-on-add-design.md`

---

### Task 1: Wrap criteria list + add-form in a single swap target

**Files:**
- Modify: `app/templates/scenarios/_criteria.html`
- Test: `tests/test_routes_scenarios.py`

**Interfaces:**
- Consumes: nothing new — reuses existing `add_criterion` route (`app/routes/scenarios.py:105-119`) and `_criteria.html`'s existing template variables (`criteria`, `scenario_id`).
- Produces: the `criteria-{{ scenario_id }}` id now lives on a wrapping `<div>` instead of the `<ul>`. Anything targeting `#criteria-{{ scenario_id }}` (currently: `_criteria.html`'s own add-form, and `_proposals.html:14`'s accept-form) keeps working unchanged since the id string is identical — only the element it's attached to changes.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_routes_scenarios.py` (near `test_add_criterion`, which starts at line 44):

```python
def test_add_criterion_response_wraps_list_and_form_in_shared_target(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    resp = client.post(f"/scenarios/{sid}/criteria", data={"text": "Must be remote", "weight": "must"})
    assert resp.status_code == 200
    # The id that both the add-form and the proposal accept-form target via
    # hx-target must sit on a wrapping element that contains the whole
    # list + form, not on the <ul> alone — otherwise HTMX's outerHTML swap
    # only replaces the <ul>, leaving the old populated <form> orphaned
    # in the DOM as a sibling.
    div_marker = f'<div id="criteria-{sid}">'
    ul_marker = f'<ul id="criteria-{sid}">'
    assert div_marker in resp.text
    assert ul_marker not in resp.text
    div_start = resp.text.index(div_marker)
    form_start = resp.text.index("<form")
    div_end = resp.text.rindex("</div>")
    assert div_start < form_start < div_end
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_routes_scenarios.py::test_add_criterion_response_wraps_list_and_form_in_shared_target -v`
Expected: FAIL — `assert '<div id="criteria-{sid}">' in resp.text` fails because the current template puts the id on `<ul>`, not a wrapping `<div>`.

- [ ] **Step 3: Update the template**

Replace the full contents of `app/templates/scenarios/_criteria.html` with:

```html
<div id="criteria-{{ scenario_id }}">
  <ul style="list-style:none; padding:0;">
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
</div>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_routes_scenarios.py::test_add_criterion_response_wraps_list_and_form_in_shared_target -v`
Expected: PASS

- [ ] **Step 5: Run the full existing test suite to confirm no regressions**

Run: `pytest tests/test_routes_scenarios.py -v`
Expected: PASS — all tests including `test_add_criterion` (line 44), `test_scenarios_page_renders_criterion_markdown` (line 52), and the proposal/refine tests still pass, since none of them assert on the `<ul>` carrying the id directly.

Also run the full suite to be safe: `pytest -v`
Expected: PASS, no failures.

- [ ] **Step 6: Manual browser verification**

Start the dev server per this project's normal run method, open a scenario's detail page in the browser, and:
1. Type text into the "Criterion text" input, pick a weight, click "Add".
2. Confirm the new criterion appears in the list AND the form is empty (no stale populated form left behind).
3. Repeat once more to confirm it's not a one-time fluke (e.g. second add also clears cleanly).
4. If there's an existing scenario with pending AI-refine proposals, accept one "add" proposal via `_proposals.html`'s form and confirm the same clean behavior (list updates, no stray form).

- [ ] **Step 7: Commit**

```bash
git add app/templates/scenarios/_criteria.html tests/test_routes_scenarios.py
git commit -m "fix: clear add-criteria form after successful add

Wrap the criteria <ul> and add-form in a single container carrying the
hx-swap target id, so HTMX's outerHTML swap atomically replaces both —
previously only the <ul> was targeted, leaving the populated <form> as
an orphaned sibling after submit."
```
