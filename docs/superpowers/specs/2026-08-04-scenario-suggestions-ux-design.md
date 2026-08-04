# Scenario Suggestions UX — Design Spec

**Date:** 2026-08-04
**Status:** Approved

## Overview

Four related improvements to the scenario page's criteria-suggestion flow:

1. When a suggested criterion is accepted (added, or removed), its suggestion row should disappear from the proposals panel.
2. A suggestion should also be dismissible without acting on it — a non-persisted, client-side-only "hide" that just removes the row from view.
3. A button to fetch suggestions for all scenarios at once, not just one scenario at a time.
4. Criteria (and their suggestions) should be ordered `must` > `prefer` > `avoid`.

This is a server-rendered app (FastAPI + Jinja2 + HTMX), not a client-side SPA. There is already a generic streaming "progress button" pattern in `app/templates/base.html` (used by "Re-evaluate all scenarios") and an existing out-of-band (OOB) delete pattern (used to remove a criterion `<li>` after a "remove" proposal is actioned). This design extends both rather than introducing new mechanisms.

## 1. Criteria ordering (must > prefer > avoid)

`get_criteria` in `app/db/queries.py` currently orders by `created_at` only:

```sql
SELECT * FROM criteria WHERE scenario_id = ? ORDER BY created_at
```

Change to:

```sql
SELECT * FROM criteria WHERE scenario_id = ?
ORDER BY CASE weight WHEN 'must' THEN 0 WHEN 'prefer' THEN 1 WHEN 'avoid' THEN 2 ELSE 3 END, created_at
```

This affects every place the saved criteria list is rendered (`_criteria.html`, via `scenarios_page`, `create_scenario`, `add_criterion`, `accept_proposals`).

The resolved proposals list (built in the refine routes, see §3 below) gets the same weight-rank sort applied in Python before rendering, so the suggestions panel groups the same way as the criteria list.

## 2. Suggestion disappears when actioned

### Proposal row ids must become scenario-scoped

Today, proposal row ids in `_proposals.html` are `proposal-{{ loop.index0 }}` — unique only within one panel. This is safe today because only one scenario's panel is ever populated at a time. It stops being safe once bulk-refine (§4) can populate multiple scenarios' panels simultaneously, producing duplicate DOM ids. Fix: scope the id to `proposal-{{ scenario_id }}-{{ loop.index0 }}`.

### Add-type: OOB delete on accept

The accept form in `_proposals.html` gets a hidden field:

```html
<input type="hidden" name="proposal_id" value="proposal-{{ scenario_id }}-{{ loop.index0 }}">
```

`add_criterion` (`POST /scenarios/{scenario_id}/criteria`, `app/routes/scenarios.py`) gains an optional `proposal_id: str | None = Form(None)` parameter. The manual add-form at the bottom of `_criteria.html` doesn't send this field, so its behavior is unchanged. When present, validate it matches `^proposal-\d+-\d+$` (defense against a tampered request injecting arbitrary HTML/ids) and, if valid, append an OOB delete to the response:

```python
html = templates.get_template("scenarios/_criteria.html").render(...)
if proposal_id and re.fullmatch(r"proposal-\d+-\d+", proposal_id):
    html += f'<div id="{proposal_id}" hx-swap-oob="delete"></div>'
return HTMLResponse(content=html)
```

### Remove-type: already correct, id just needs rescoping

`remove_criterion_via_proposal` (`DELETE /criteria/{criterion_id}/remove-proposal`) returns a response that is *entirely* OOB-tagged content: `<li id="criterion-{id}" hx-swap-oob="delete"></li>`. The Remove button already does:

```html
hx-target="#proposal-{{ loop.index0 }}"
hx-swap="outerHTML"
```

Since the whole response is pulled out for OOB processing, the "main" swap content htmx applies to `hx-target` is empty — which, combined with `hx-swap="outerHTML"`, removes the proposal row itself. So remove-type suggestions already disappear correctly today; no route or response changes are needed here. The only change is updating the `hx-target` selector (and the row's own `id`) to the new scenario-scoped format, `#proposal-{{ scenario_id }}-{{ loop.index0 }}`, to match §2's id rescoping.

## 3. Dismiss button (non-persisted hide)

Every proposal row — add or remove type — gets a "Dismiss" button, styled with the existing `.btn-invalid` class:

```html
<button type="button" class="btn btn-invalid proposal-dismiss" style="font-size:0.85em;">Dismiss</button>
```

Handled entirely client-side in `base.html` via a delegated listener (no request, nothing persisted):

```js
document.body.addEventListener("click", function (evt) {
  if (!evt.target.classList.contains("proposal-dismiss")) return;
  var row = evt.target.closest('[id^="proposal-"]');
  if (row) row.remove();
});
```

## 4. Bulk "get suggestions for all scenarios"

### Route

New `POST /scenarios/refine` in `app/routes/scenarios.py`, streaming like the existing `POST /scenarios/reevaluate` does. For each scenario in turn: yield a progress line, compute proposals, yield another progress line with the count, then yield an `HTML:` line containing that scenario's proposals wrapped in an OOB target:

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

`_resolve_proposals(proposals, existing)` is extracted from the existing `refine_criteria` route body (the `action == "remove"` matching + weight-rank sort from §1) so both the single-scenario and bulk routes share it:

```python
_WEIGHT_ORDER = {"must": 0, "prefer": 1, "avoid": 2}

def _resolve_proposals(proposals, existing: list[dict]) -> list[dict]:
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

`refine_criteria` (single-scenario) keeps its own LLM call and logging, but delegates the resolution step to `_resolve_proposals`.

### Button

New button on `scenarios/index.html`, next to "Re-evaluate all scenarios":

```html
<button class="btn" data-progress-url="/scenarios/refine" data-progress-oob="1">
  Get suggestions for all scenarios
</button>
```

No `data-progress-target` — results are routed per-scenario via OOB swap (see below), not to one fixed element.

### JS: `data-progress-oob` mode

The existing generic progress-button handler in `base.html` currently buffers only the *last* `HTML:` line and applies it once, at stream end, into a single `data-progress-target`. That doesn't fit bulk refine, which needs each scenario's `HTML:` chunk applied immediately as it streams in, routed to that scenario's own `#proposals-area-{id}`.

Add a hidden scratch element once in `base.html`:

```html
<div id="oob-scratch" hidden></div>
```

Extend `handleLine` to check for the new opt-in attribute:

```js
var oobMode = el.hasAttribute("data-progress-oob");
...
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

`finish()` skips the single-target swap when `oobMode` is true (each chunk was already applied as it arrived); all other behavior (elapsed-time reveal, error handling) is unchanged and shared with non-oob buttons like "Re-evaluate all scenarios".

Each chunk's outer `<div id="proposals-area-{id}" hx-swap-oob="true">` matches the real `<div id="proposals-area-{{ scenario.id }}">` already in `scenarios/index.html`, so htmx's OOB swap replaces it in place (outerHTML) wherever it lives in the real DOM, leaving the scratch div empty afterward.

## Testing

- `tests/test_routes_scenarios.py`:
  - `get_criteria` / criteria-list rendering returns items ordered must, then prefer, then avoid (mixed-insertion-order fixture).
  - Accepting an add-type proposal (`POST .../criteria` with `proposal_id` set) includes the OOB delete `<div ... hx-swap-oob="delete">` in the response; without `proposal_id`, it doesn't.
  - A malformed `proposal_id` (fails the `proposal-\d+-\d+` pattern) is ignored rather than reflected into the response.
  - Existing `test_remove_proposal_response_marks_criterion_row_for_oob_delete` continues to pass unchanged (no route/response change for remove-type); `_proposals.html`'s rendered `hx-target`/row `id` use the new scenario-scoped format.
  - `POST /scenarios/refine` streams one `HTML:` line per scenario, each with the correct `proposals-area-{id}` wrapper, in scenario order.
  - Proposal row ids rendered by `_proposals.html` are scenario-scoped (`proposal-{scenario_id}-{index}`).
- Manual/UI check: dismiss button removes a row with no network request (check via browser dev tools / network tab).
- Manual/UI check: bulk refine populates multiple scenarios' panels progressively without a page reload.
- No test for the pure client-side dismiss listener beyond markup presence — it's trivial DOM removal, not worth a browser-driven test in this suite.
