# Scenario fit header: unify pills, rename save button, add explainer note

## Goal

The "Scenario fit" foldable section on a job's detail view shows each scenario's
name twice: once as a static pill in the `<summary>` (glance view when
collapsed) and again as a separate tab-selector card once expanded. Unify
these into a single element, rename the submit button to be clearer about
what it does, and add a short note explaining the section's purpose while
expanded.

## Current structure

`app/templates/jobs/_score_tabs.html`:
- `<details class="score-compare-details">` wraps the whole section.
- `<summary>` contains "Scenario fit" plus one `score-compare-pair` pill per
  scenario (name tag + score badge), always visible.
- Inside, a `<form class="scenario-feedback-form">` contains, per scenario, a
  radio input + `score-tab-card` label (name only, no score) that acts as the
  tab selector, followed by that scenario's `score-tab-panel`
  (`_score_tab_panel.html`: reasoning, direction toggle, note). Panel
  visibility is driven by a CSS adjacent-sibling selector:
  `.score-tab-input:checked + .score-tab-card + .score-tab-panel`.
- Submit button: "Save all feedback". `routes/jobs.py`'s
  `scenario-feedback` handler takes `scenario_id`/`note`/`direction` as
  `list[...]` — every panel's fields submit together regardless of which tab
  is currently visible. The tab radio's own name/value is never read by the
  backend; it's purely a UI selector.

## Design

### 1. Unify pill + tab selector

Move the radio inputs and pill labels out of `<summary>` into a new
`.score-pill-row` div — a sibling of `<summary>` and `<form>`, not nested
inside either:

```html
<details class="score-compare-details">
  <summary><span>Scenario fit</span></summary>
  <div class="score-pill-row">
    <input type="radio" id="score-tab-{job.id}-{js.scenario_id}"
           name="score-tab-{job.id}" value="{js.scenario_id}"
           class="score-tab-input" form="scenario-feedback-form-{job.id}"
           {checked if top_passed_scenario_id}>
    <label for="score-tab-{job.id}-{js.scenario_id}" class="score-pill ...">
      <span class="tag">{scenario_name}</span>
      <span class="score-badge score-fluid">{pct}</span>
    </label>
    ... one pair per scenario ...
  </div>
  <p class="score-compare-note">...</p>  <!-- see §4 -->
  <form id="scenario-feedback-form-{job.id}" class="scenario-feedback-form" ...>
    ... panels ...
    <button type="submit">Send collected feedback for this job</button>
  </form>
</details>
```

Reasons for this shape:
- Putting interactive controls directly inside `<summary>` is unreliable —
  clicks on nested inputs/labels can also trigger the browser's native
  open/close toggle on the parent `<details>`, fighting any custom
  open/select logic. Keeping the pill row as a `<summary>` sibling avoids
  that entirely.
- The radios can no longer be physical descendants of `<form>`, so they use
  the `form="scenario-feedback-form-{job.id}"` attribute to still submit
  with it (harmless — the backend never reads this field by name).
- `.score-pill` keeps today's `score-compare-pair` look (tag + score badge,
  pill-shaped) — the merged element takes the pill's visual identity, not
  the plain tab-card's.

`.score-pill-row` gets a CSS override so it stays visible even when
`<details>` is closed (author styles beat the browser's default
`details:not([open]) > *:not(summary) { display: none }`), preserving
today's "glanceable scores when collapsed" behavior. The `<form>` (and the
new note, §4) keep the native collapse/expand behavior — hidden when folded.

### 2. Panel switching via JS instead of CSS-sibling trick

Radios and panels are no longer DOM-adjacent, so the existing
`:checked + label + panel` CSS selector no longer applies. Replace it with a
delegated `change` listener (same pattern as the existing `.direction-btn`
handler in `base.html`):

```js
document.body.addEventListener('change', function (e) {
  var input = e.target.closest('.score-tab-input');
  if (!input) return;
  var details = input.closest('.score-compare-details');
  if (!details) return;
  details.querySelectorAll('.score-tab-panel').forEach(function (p) {
    p.classList.toggle('score-tab-panel-active', p.dataset.scenarioId === input.value);
  });
});
```

Each panel (`_score_tab_panel.html`) gets `data-scenario-id="{js.scenario_id}"`
and starts with `score-tab-panel-active` when it matches
`job.top_passed_scenario_id`, matching today's default-checked scenario.
CSS: `.score-tab-panel { display: none; }` /
`.score-tab-panel-active { display: block; }`.

### 3. Clicking a pill always expands the section

Per-answer: one click on a pill both selects that scenario's tab and expands
the section if folded. A `change` event alone misses the case where the
already-checked pill is clicked while collapsed (no state change → no
`change` event), so a separate delegated `click` listener force-opens the
details on any pill click:

```js
document.body.addEventListener('click', function (e) {
  var pill = e.target.closest('.score-pill');
  if (!pill) return;
  var details = pill.closest('.score-compare-details');
  if (details) details.open = true;
});
```

Re-folding stays a native `<summary>` click (the "Scenario fit" label +
chevron), unchanged from today.

### 4. Explainer note (expanded state only)

A short note appears only while the section is expanded — placed as a
`<p class="score-compare-note">` between `.score-pill-row` and `<form>`
(a non-`<summary>` child of `<details>`, so it's hidden natively when
collapsed, same as the form):

> **How well does this job match your search criteria?** Tuning this helps
> showing you only relevant new jobs.

Styled small/muted, consistent with other secondary text in the app (e.g.
`.stale-note`).

### 5. Button rename

`"Save all feedback"` → `"Send collected feedback for this job"`. No
behavior change.

## CSS changes (`app/templates/base.html`)

- Remove `.score-compare-pair`, `.score-compare-pair .tag`,
  `.score-compare-pair .score-badge`, `.score-tabs`, `.score-tab-card`,
  `.score-tab-card .tag`, and the old
  `.score-tab-input:checked + .score-tab-card` /
  `.score-tab-input:checked + .score-tab-card + .score-tab-panel` rules.
- Add `.score-pill-row` (flex-wrap row, force-visible per §1),
  `.score-pill` (pill shape, replaces `.score-compare-pair`'s look),
  `.score-tab-input:checked + .score-pill` (active highlight, replaces the
  old checked-tab-card rule), `.score-tab-panel` /
  `.score-tab-panel-active` (replaces the old sibling-selector visibility
  rule), and `.score-compare-note`.
- Simplify `.score-compare-details summary` rules — it now only ever
  contains the "Scenario fit" label, not pills.
- `.score-pill-failed` (strikethrough for below-gate scenarios) carries over
  unchanged, now applied to `.score-pill` instead of both
  `.score-compare-pair` and `.score-tab-card`.

## Tests

`tests/test_routes_jobs.py` has exact-string assertions on the current
markup that need updating:
- ~line 223-224: `score-tab-input` id/name/`checked` assertions — update for
  the new `value="{scenario_id}" form="scenario-feedback-form-{job.id}"`
  attributes.
- ~line 1077: exact button HTML string — update to the new label.
- ~line 1108: `resp.text.count("Save all feedback")` — update string and
  keep the count assertion (still expect exactly one button).

No other route/backend behavior changes; `scenario-feedback` handler is
untouched.

## Out of scope

- Any change to what gets submitted (still all scenarios' feedback, every
  time — matches the renamed button's "collected feedback" framing).
- Accessibility beyond what already exists (native radiogroup semantics via
  the `<input type="radio">`/`<label>` pairs, unchanged from today).
