# Editable Scenario Criteria — Design Spec

**Date:** 2026-07-31
**Status:** Approved

## Overview

Scenario criteria (must/prefer/avoid rows) are currently insert/delete only — there's no way to fix a typo or nudge a weight without deleting a criterion and re-adding it, which also loses its original `source` and `created_at`. Separately, the AI "refine criteria from feedback" flow can propose removing an existing criterion (`action: "remove"`), but the UI renders every proposal — add or remove — with the same "Add" button, so remove suggestions silently do nothing useful (worse, clicking one inserts a duplicate).

This adds in-place editing for existing criteria, makes refine proposals directly editable before accepting, and wires up remove-proposals to actually delete the matched criterion.

---

## 1. Editing existing criteria (click-to-edit)

Mirrors the existing scenario name/description edit pattern (`_header.html` / `_header_edit.html`).

- The criteria list's inline `<li>` markup moves out of `_criteria.html`'s loop into its own partial, `_criterion.html` (read view), included per-row.
- New partial `_criterion_edit.html`: a form with a text input + weight `<select>`, pre-filled with the criterion's current values, plus Save/Cancel — same shape as the scenario edit form.
- New routes in `app/routes/scenarios.py`:
  - `GET /criteria/{id}/edit` → renders `_criterion_edit.html`
  - `GET /criteria/{id}` → renders `_criterion.html` (Cancel target)
  - `POST /criteria/{id}` → updates text/weight via `q.update_criterion`, renders `_criterion.html`
- New queries in `app/db/queries.py`:
  - `update_criterion(conn, criterion_id, text, weight)`
  - `get_criterion(conn, criterion_id)` — single-row fetch, mirrors `get_scenario`
- `source` is never changed by an edit — it only reflects how the criterion was originally created (`manual` vs `feedback`), not who last touched it. Nothing in the codebase currently branches on `source` differently, so this stays simple.
- No cache-invalidation work needed: `app/scenario_version.py` already hashes criteria text/weight into the scenario's content version, so an edit is automatically picked up by the existing "smart re-evaluate" staleness check.

## 2. Editable refine proposals + wiring up "remove"

- `_proposals.html` branches on `p.action`:
  - **`add`** proposals: text/weight become live, editable inputs (not hidden fields), pre-filled with the AI's suggestion, tweakable before clicking "Add" — otherwise unchanged from today.
  - **`remove`** proposals: rendered read-only (just the matched criterion's current text — there's nothing to edit when the action is a deletion), with a "Remove" button in place of "Add".
- Matching a `remove` proposal to a real criterion happens server-side in the `refine_criteria` route, immediately after `propose_criteria()` returns: normalize (trim + casefold) both the proposal's text and each existing criterion's text, and compare. On match, attach that criterion's `id` to the proposal before rendering.
- If a `remove` proposal has no match (LLM paraphrased it, or the criterion was deleted in the meantime), it is omitted from the rendered proposal list entirely — no dead/disabled button shown.
- Clicking "Remove" deletes the matched criterion (reusing the existing delete path) and updates both the criteria list and the proposal panel in the same round trip via an htmx out-of-band swap, so the proposal row doesn't linger pointing at something already gone.
- The dead `accept_proposals` (`POST /scenarios/{id}/refine/accept`) route and its `text_0`/`weight_0`-style form fields are unused by any template today. This is a pre-existing gap unrelated to this work — left as-is, not touched as drive-by cleanup.

## 3. Data layer & testing

- No schema migration — the `criteria` table already has everything needed (`text`, `weight`, `source` untouched by this feature).
- Tests to add:
  - Query-level: `update_criterion` persists changes; `get_criterion` returns the right row.
  - Route-level: edit round-trip (`GET .../edit` → `POST` → row reflects new values); the text-normalization matcher for remove-proposals (exact match, whitespace/case drift, no-match-omits-row); the Remove button actually deletes the matched criterion.
  - Confirm the existing scenario-version staleness test already covers "editing a criterion marks scores stale" — extend it if it doesn't, rather than duplicating a new test.
