# Clear Add-Criteria Input on Add — Design Spec

**Date:** 2026-08-02
**Status:** Approved

## Overview

When adding a criterion to a scenario, the new criterion appears in the list, but the "add criterion" input the user typed into stays visible on screen, still populated with the text they just submitted. It should disappear/clear after a successful add.

## Root Cause

This is a server-rendered app (FastAPI + Jinja2 + HTMX), not a client-side SPA, so there's no JS state to reset. The bug is an HTMX swap-targeting mismatch in `app/templates/scenarios/_criteria.html`:

- The partial renders a `<ul id="criteria-{{ scenario_id }}">` and, as a sibling, the add-criterion `<form>`.
- The add-form does `hx-target="#criteria-{{ scenario_id }}"` with `hx-swap="outerHTML"`, and `add_criterion` (`app/routes/scenarios.py:105-119`) responds with the *entire* `_criteria.html` partial — a fresh `<ul>` plus a fresh, empty `<form>`.
- HTMX swaps that whole response in place of the `<ul>` it targeted. This inserts a new list and a new empty form, but the *original* form — the one the user typed into, which lived as a sibling after the old `<ul>` — was never itself the swap target, so it's orphaned in the DOM: still visible, still showing the typed text.

A second consumer has the identical pattern and would exhibit the same bug: the "accept proposed criterion" form in `app/templates/scenarios/_proposals.html:13-16` also does `hx-target="#criteria-{{ scenario_id }}"` / `hx-swap="outerHTML"`, posting to the same `add_criterion` route.

## Fix

Wrap the `<ul>` and `<form>` together in a single container element, and move the `id="criteria-{{ scenario_id }}"` from the `<ul>` onto that wrapper:

```html
<div id="criteria-{{ scenario_id }}">
  <ul style="list-style:none; padding:0;">
    ...
  </ul>
  <form hx-post="/scenarios/{{ scenario_id }}/criteria"
        hx-target="#criteria-{{ scenario_id }}"
        hx-swap="outerHTML"
        ...>
    ...
  </form>
</div>
```

No other files change:
- `add_criterion` and `_proposals.html`'s accept-form both already target `#criteria-{{ scenario_id }}` with `hx-swap="outerHTML"` and already receive the full `_criteria.html` partial as the response — once that id lives on the wrapping div, the swap becomes atomic: the whole old div (list + stale populated form) is replaced by the whole new div (list + fresh empty form).
- No route or handler changes needed.
- No new template files.

## Out of scope

- `POST /scenarios/{id}/refine/accept` (`accept_proposals`, `app/routes/scenarios.py:197-215`) also renders `_criteria.html`, but no template currently posts to it (pre-existing dead code per `docs/superpowers/specs/2026-07-31-editable-scenario-criteria-design.md`). Not touched by this fix.

## Testing

- Manual/UI check: add a criterion via the main add-form and confirm the input clears and only one fresh empty form remains.
- Manual/UI check: accept a proposed "add" criterion via `_proposals.html` and confirm the same (list updates, no stray populated form).
- No new automated test is warranted — this is a pure template-structure fix with no new logic branch to cover; existing route tests for `add_criterion` (if any) continue to assert on response content, which is unaffected in substance (same `<ul>`/`<form>` content, just re-nested).
