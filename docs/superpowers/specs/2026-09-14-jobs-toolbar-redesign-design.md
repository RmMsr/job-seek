# Jobs toolbar redesign — design

## Problem

The jobs page toolbar (search, Scenario/Source/Organization selects, Sort,
Select-all, and the 7-tab status strip) wraps onto 3-4 rows once the viewport
narrows, and feels cluttered even on desktop. It's a template/CSS change
only — `JobFilter` (`app/job_filter.py`) already supports everything needed
(solo-select via `for_status`, multi-select via `with_status_toggled`), so no
backend or route changes are required.

Approved direction (mockup: see chat) — a single unified layout, CSS-only
responsive, no JS framework, matching this codebase's existing native-control
style (`<select>`, `<details>`):

- **Desktop** (`min-width: 601px`, matching the existing `@media (max-width:
  600px)` breakpoint already used for `.job-row-content`): one row. The four
  everyday statuses (New Jobs, New Leads, Accepted, Pending) stay
  always-visible plain tab links. The three archival statuses (Rejected, Not
  relevant, Trash) move behind a **More** popover, whose trigger badge sums
  their counts so nothing goes invisible. A **Combine** popover replaces the
  old always-visible ✓/+ chip on every tab, holding checkboxes for all 7
  statuses (multi-select is still fully capable, just secondary). A
  **Filters** popover replaces the always-visible Scenario/Source/Org/Sort
  selects, badge shows how many of the three dropdown filters are active.
  Select-all stays a plain visible checkbox.
- **Mobile** (`max-width: 600px`): the status control becomes a native
  `<select>` (all 7 options, primary/single-select). Combine, Filters, and
  Select-all reflow into a second compact row underneath (same DOM nodes as
  desktop, repositioned by CSS — see Layout below).

## Why these specific trade-offs

- **Native `<select>`/`<details>` over a JS component library**: this app
  ships no JS framework and already leans on native controls
  (`<details class="job-advanced">` for the bulk "Advanced…" actions). No
  new dependency, keyboard/focus semantics come free.
- **No click-outside-to-close, no `Escape` handling** on the new `<details>`
  popovers. Native `<details>` doesn't do this out of the box, and adding it
  needs JS. Personal, single-user app — accepted as a v1 limitation, easy to
  add later if it ever bothers anyone.
- **Combine panel shows plain (non-live) counts**, not the oob-updated
  `id="count-*"` spans. Those ids stay on the primary tab labels and the
  More panel (see Counts below) — the *canonical*, usually-visible surfaces.
  Combine is secondary; a plain single-job accept/reject elsewhere on the
  page (which does a row-level swap + oob counts, not a full
  `#jobs-content` re-render) could leave its numbers stale until the next
  full content refresh (any tab switch, filter change, or bulk action).
  Accepted trade-off — avoids adding 7 more oob ids for a panel that's
  rarely open.
- **All three popovers close after every action**, including Combine — a
  checkbox toggle, like every other control here, re-renders the whole
  `#jobs-content` fragment, and a freshly-rendered `<details>` has no `open`
  attribute unless the template adds one. Two ways to keep Combine open
  across consecutive checkbox clicks were considered and rejected:
  auto-`open` whenever `filter.is_multi` (rejected for the same reason as
  Filters below — the Combine badge/`active` state already communicates
  "multi is on," and a "stays open forever while multi-selecting" default
  visually clutters the exact class of interaction this redesign exists to
  declutter), and `hx-preserve` on the `<details>` (rejected: each
  checkbox's toggle target is a *specific* query string computed from
  `scope_filter.with_status_toggled(tab)` at render time; `hx-preserve`
  keeps the DOM node from the *first* render forever, so the second click
  would fire a toggle URL computed against the original, now-stale
  `scope_tabs` — silently dropping the first click's change). One click to
  reopen Combine per additional status is the accepted trade-off, same as
  Filters. If building 3+-way combined views turns out to be common enough
  to be annoying in practice, revisit with a small JS enhancement then —
  not worth the correctness risk to build speculatively now.
- **Mobile `<select>` option text also can't be live-updated** (`<option>`
  content is plain text — no nested `<span id>` for htmx to target). Same
  self-heals-on-next-full-render reasoning as Combine. Not fixed with a
  side badge for the active tab's count — that would need threading `filter`
  into `_counts_oob.html` at all 8 call sites in `app/routes/jobs.py` for a
  cosmetic edge case (a live count behind a closed dropdown, right after a
  single-row action). Out of scope for v1.

## Layout

One flex container per toolbar row, reused verbatim between breakpoints —
**not** duplicated markup per breakpoint, to avoid two live `<select
name="scenario">` etc. elements existing in the DOM at once:

```html
<div class="tb-toolbar">
  <nav class="tb-tabs"> ... primary tab links + More popover ... </nav>
  <div class="tb-status-select-wrap"> ... mobile <select> ... </div>
  <div class="tb-utility"> ... Combine popover, Filters popover, Select-all ... </div>
</div>
```

- `.tb-tabs` — `display: none` at `max-width: 600px`.
- `.tb-status-select-wrap` — `display: none` above `600px`.
- `.tb-utility` — always present; `.tb-toolbar` is `display: flex` normally
  (`.tb-utility` pushed right via `margin-left: auto`), and switches to
  `flex-direction: column` at `max-width: 600px` so `.tb-status-select-wrap`
  (order 1) sits above `.tb-utility` (order 2, `width: 100%`, its own
  `Select-all` still pinned right via `margin-left: auto` inside it).

This keeps every named form control (`scenario`, `source_id`, `org`,
`order`) as a single DOM node regardless of viewport — no duplicate-name
collision risk with any `hx-include="[name='...']"` selector elsewhere on
the page.

## Markup — `app/templates/jobs/_content.html`

Replace the current `filter-row` + `filter-bar` blocks (lines ~19-98)
entirely. Keep the existing hidden inputs (`#jobs-status-marker` and the
four `*_filter` fields for `#bulk-form`) unchanged — they're unrelated to
this layout.

```jinja
{% set primary_tabs = tab_defs[:4] %}   {# new, lead, accepted, pending #}
{% set archive_tabs = tab_defs[4:] %}   {# rejected, not_relevant, trash #}
{% set scope_tabs = active_tabs if active_tabs is defined else filter.statuses %}
{% set scope_filter = filter.with_statuses(scope_tabs) if filter.searching else filter %}
{% set more_active = archive_tabs | selectattr(0, 'in', scope_tabs) | list | length > 0 %}
{% set archive_total = archive_tabs | map(attribute=3) | sum %}
{# archive_total is rendered unconditionally (even "0") so the oob swap
   in _counts_oob.html always has a target — see Counts below. #}
{% set active_filter_count =
     (1 if (filter.scenario_id is not none or filter.scenario_none) else 0)
   + (1 if filter.source_id is not none else 0)
   + (1 if (filter.org is not none or filter.org_none) else 0) %}

<div class="tb-toolbar">
  <nav class="tb-tabs">
    {% for tab, label, count_id, count, tip in primary_tabs %}
    <a class="tb-tab{% if tab in scope_tabs %} active{% endif %}"
       href="/jobs?{{ filter.for_status(tab).query_params() | urlencode }}"
       hx-get="/jobs?{{ filter.for_status(tab).query_params() | urlencode }}"
       hx-target="#jobs-content" hx-swap="innerHTML" hx-push-url="true"
       title="{{ tip | safe }}">{{ label }}<span class="tb-count" id="{{ count_id }}">{{ count }}</span></a>
    {% endfor %}
    <details>
      <summary class="tb-trigger{% if more_active %} active{% endif %}">More <span class="tb-badge" id="count-more-badge">{{ archive_total }}</span> <span class="tb-chev">▾</span></summary>
      <div class="tb-panel tb-panel-more">
        {% for tab, label, count_id, count, tip in archive_tabs %}
        <a class="tb-more-link{% if tab in scope_tabs %} active{% endif %}"
           href="/jobs?{{ filter.for_status(tab).query_params() | urlencode }}"
           hx-get="/jobs?{{ filter.for_status(tab).query_params() | urlencode }}"
           hx-target="#jobs-content" hx-swap="innerHTML" hx-push-url="true"
           title="{{ tip | safe }}">{{ label }}<span class="tb-count" id="{{ count_id }}">{{ count }}</span></a>
        {% endfor %}
      </div>
    </details>
  </nav>

  <div class="tb-status-select-wrap">
    <select class="tb-status-select" name="status"
            hx-get="/jobs" hx-target="#jobs-content" hx-swap="innerHTML" hx-push-url="true"
            hx-include="[name='scenario'],[name='source_id'],[name='org'],[name='order'],[name='q']">
      {% if filter.is_multi %}
      <option value="" selected disabled>Multiple statuses</option>
      {% endif %}
      {% for tab, label, count_id, count, tip in tab_defs %}
      <option value="{{ tab }}" {% if not filter.is_multi and filter.statuses == (tab,) %}selected{% endif %}>{{ label }} · {{ count }}</option>
      {% endfor %}
    </select>
  </div>

  <div class="tb-utility">
    <details>
      <summary class="tb-trigger{% if filter.is_multi %} active{% endif %}">Combine <span class="tb-chev">▾</span></summary>
      <div class="tb-panel">
        <span class="tb-panel-label">Add statuses to this view</span>
        {% for tab, label, count_id, count, tip in tab_defs %}
        {% set toggle = scope_filter.with_status_toggled(tab).query_params() | urlencode %}
        <label class="tb-check-row">
          <input type="checkbox" {% if tab in scope_tabs %}checked{% endif %}
                 hx-get="/jobs?{{ toggle }}" hx-target="#jobs-content" hx-swap="innerHTML" hx-push-url="true">
          {{ label }} <span class="tb-count-plain">{{ count }}</span>
        </label>
        {% endfor %}
      </div>
    </details>

    <details>
      <summary class="tb-trigger{% if active_filter_count %} active{% endif %}">Filters {% if active_filter_count %}<span class="tb-badge">{{ active_filter_count }}</span>{% endif %} <span class="tb-chev">▾</span></summary>
      <div class="tb-panel">
        <label class="tb-panel-label" for="tb-scenario">Scenario</label>
        <select id="tb-scenario" name="scenario" hx-get="/jobs" hx-target="#jobs-content" hx-swap="innerHTML"
                hx-push-url="true" hx-vals='{"status": "{{ filter.statuses | join(',') }}"}'
                hx-include="[name='source_id'],[name='org'],[name='order'],[name='q']">
          <option value="">(All)</option>
          <option value="none" {% if filter.scenario_none %}selected{% endif %}>(None)</option>
          {% for s in scenarios %}
          <option value="{{ s.id }}" {% if filter.scenario_id == s.id %}selected{% endif %}>{{ s.name }}</option>
          {% endfor %}
        </select>
        <label class="tb-panel-label" for="tb-source">Source</label>
        <select id="tb-source" name="source_id" hx-get="/jobs" hx-target="#jobs-content" hx-swap="innerHTML"
                hx-push-url="true" hx-vals='{"status": "{{ filter.statuses | join(',') }}"}'
                hx-include="[name='scenario'],[name='org'],[name='order'],[name='q']">
          <option value="">(All)</option>
          {% for s in sources %}
          <option value="{{ s.id }}" {% if filter.source_id == s.id %}selected{% endif %}>{{ "(Single / None)" if s.fetcher_type == "manual" else s.name }}</option>
          {% endfor %}
        </select>
        <label class="tb-panel-label" for="tb-org">Organization</label>
        <select id="tb-org" name="org" hx-get="/jobs" hx-target="#jobs-content" hx-swap="innerHTML"
                hx-push-url="true" hx-vals='{"status": "{{ filter.statuses | join(',') }}"}'
                hx-include="[name='scenario'],[name='source_id'],[name='order'],[name='q']">
          <option value="">(All)</option>
          <option value="none" {% if filter.org_none %}selected{% endif %}>(None)</option>
          {% for c in companies %}
          <option value="{{ c }}" {% if filter.org == c %}selected{% endif %}>{{ c | truncate(40, True, '…') }}</option>
          {% endfor %}
        </select>
        {% if not filter.searching %}
        <label class="tb-panel-label" for="tb-order">Sort</label>
        <select id="tb-order" name="order" hx-get="/jobs" hx-target="#jobs-content" hx-swap="innerHTML"
                hx-push-url="true" hx-vals='{"status": "{{ filter.statuses | join(',') }}"}'
                hx-include="[name='scenario'],[name='source_id'],[name='org']">
          <option value="change" {% if filter.order == 'change' %}selected{% endif %}>Newest changes</option>
          <option value="score" {% if filter.order == 'score' %}selected{% endif %}>Fit score</option>
          <option value="age" {% if filter.order == 'age' %}selected{% endif %}>Posting age</option>
        </select>
        {% endif %}
        {% if filter.is_narrowed %}
        <a class="tb-clear-link" href="/jobs?{{ filter.cleared().query_params() | urlencode }}">Clear all filters</a>
        {% endif %}
      </div>
    </details>

    {% if jobs %}
    <label class="tb-select-all">
      <input type="checkbox" class="select-all-checkbox" aria-label="Select all">
      Select all
    </label>
    {% endif %}
  </div>
</div>
```

Notes on the above:
- `tab_defs` is unchanged (still the 7-tuple list already defined at the top
  of `_content.html`).
- `more_active`/`archive_total` use Jinja's `selectattr`/`map(attribute=…)`
  against the tuple's positional index (`0` = tab key, `3` = count) — no new
  Python-side constant needed; the primary/archive split is purely
  `tab_defs[:4]` / `tab_defs[4:]`, which stays correct as long as `VALID_TABS`
  keeps New/Lead/Accepted/Pending first (already true today).
- The primary tab links, More-panel links, and Combine-panel checkboxes all
  reuse the *exact* query-building calls the current code already uses
  (`filter.for_status`, `scope_filter.with_status_toggled`) — no logic
  changes, just relocated markup.
- The mobile `<select name="status">` is a new control. Its own value
  becomes the `status` query param via htmx's default form-element
  serialization (same mechanism the Scenario/Source/Org selects already
  rely on) — no `hx-vals` needed for it. The existing hidden
  `#jobs-status-marker` (also `name="status"`, used only by the top search
  box's own `hx-include="#jobs-status-marker, ..."`, matched by `#id` not by
  name) is unaffected; there's no selector on the page that gathers
  `[name='status']` elements collectively, so two elements sharing that name
  causes no duplicate-param issue.
- Multi-select (`filter.is_multi`) can't be represented by one `<select>`
  value — shows a disabled "Multiple statuses" placeholder option instead;
  picking any real option drops back to solo (matches `for_status` on every
  other entry point).
- Combine, Filters, and More all render as plain `<details>` with no `open`
  attribute — each closes after every action (see trade-offs above for why
  this applies to Combine too, not just Filters/More).

## Counts — `app/templates/jobs/_counts_oob.html`

Six of the seven ids are unchanged, just relocated in the DOM (three move
from the old always-visible tab row into the More panel). Add one new id for
the More trigger's badge:

```jinja
<span class="tb-count" id="count-new" hx-swap-oob="true">{{ counts.new }}</span>
<span class="tb-count" id="count-accepted" hx-swap-oob="true">{{ counts.accepted }}</span>
<span class="tb-count" id="count-pending" hx-swap-oob="true">{{ counts.pending }}</span>
<span class="tb-count" id="count-rejected" hx-swap-oob="true">{{ counts.rejected }}</span>
<span class="tb-count" id="count-lead" hx-swap-oob="true">{{ counts.lead }}</span>
<span class="tb-count" id="count-not_relevant" hx-swap-oob="true">{{ counts.not_relevant }}</span>
<span class="tb-count" id="count-trash" hx-swap-oob="true">{{ counts.trash }}</span>
<span class="tb-badge" id="count-more-badge" hx-swap-oob="true">{{ counts.rejected + counts.not_relevant + counts.trash }}</span>
```

The More trigger's badge (`#count-more-badge` in the markup above) is
rendered unconditionally, including "0" — same convention every other tab
count already follows — so this oob span always has a matching target.

## CSS — `app/templates/base.html`

Delete entirely: `.filter-row`, `.filter-selects` (+ its `label`/`select`/
`.filter-clear` rules), `.filter-tools`, `.filter-bar`, `.filter-links`,
`.tab-item` (+ `.active`, `.tab-item-pulse`, `@keyframes tab-pulse`),
`.tab-label`, `.tab-count`, `.tab-check` (+ `::after`, `:hover`, active
states) — the whole "Jobs page toolbar" block (`base.html` lines ~461-563).
Keep `.jobs-search` and its children as-is (unchanged).

Add new rules for `.tb-toolbar`, `.tb-tabs`, `.tb-tab` (+ `.active`),
`.tb-count` (rename of the kept `.tab-count` styling — same visual
treatment, tabular-nums, muted color, accent when active), `.tb-trigger` (+
`.active` — same treatment as `.tb-tab.active`, i.e. accent text/border, for
when More/Filters/Combine reflect an active-but-closed state; + `.tb-chev`,
rotated when parent `details[open]`), `.tb-badge` (small solid
accent pill, matches `.status-pill` sizing conventions already in the file),
`.tb-panel` (popover: `position: absolute`, surface elevation one step above
`--ground` — i.e. `background: var(--surface)`, existing border token,
`border-radius: 9px`, a shadow in the same weight as `.job-row:hover`'s
`0 2px 8px rgba(0,0,0,.04)` composed with a `0 8px 24px rgba(0,0,0,.08)`
resting shadow, entrance animation `scale(.96)→scale(1)` + fade,
`transform-origin: top right` on desktop / `top left` on the mobile Combine
panel per its position, duration ~160ms `cubic-bezier(.23,1,.32,1)` per this
project's existing `tab-pulse`-style easing conventions), `.tb-check-row`,
`.tb-count-plain`, `.tb-more-link`, `.tb-clear-link` (reuse `.filter-clear`'s
existing look), `.tb-status-select-wrap` (+ its own dropdown-caret
background-image or a small `::after`, matching `.jobs-search`'s icon
technique), `.tb-status-select` (styled like other `select` elements already
are, per the file's existing `textarea, input, select { ... }` base rule —
just wider/bolder as the primary mobile control), `.tb-select-all` (rename
of `.select-all`, same styling). Respect `prefers-reduced-motion` for the
panel entrance animation (existing `@media (prefers-reduced-motion: reduce)`
block already in the file — add the new keyframe/transition names there).

Hit targets: `.tb-trigger` and `.tb-tab` get enough vertical padding to
clear ~40px on touch (mirrors `.tab-check`'s existing tap-target intent, now
achieved via generous `<summary>`/`<a>` padding instead of a separate
pseudo-element, since these are no longer small square chips).

## JS — `app/templates/base.html`

Delete the entire long-press-to-toggle IIFE (`base.html` lines ~1755-1812).
It exists only to make the old always-visible `.tab-check` chip reachable on
touch without a separate tap target; that chip is gone (multi-select now
lives inside the openly-tappable Combine checkboxes), so the workaround has
nothing left to work around.

No new JS is added — `<details>`/`<summary>` and native `<select>` handle
opening, closing (via re-clicking `<summary>`), and keyboard access for
free.

## Testing

This is presentational — no `JobFilter`, query, or route logic changes — so
no new unit tests. Manual verification (via `run-dev-server`) at both a
desktop and a ~375px mobile viewport width, in light and dark mode:

- Toolbar never wraps to more than the two documented lines (one desktop,
  two mobile), tab counts included, at any width down to 320px.
- Solo tab click (primary label, More-panel link, or mobile `<select>`) —
  same landing page as today.
- Combine checkbox toggling builds/removes a multi-status view, matches
  today's ✓/+ chip behavior; the panel closes after each toggle (accepted
  trade-off) and the trigger shows its `active` state whenever `is_multi`.
- Filters popover: each of Scenario/Source/Org/Sort still filters
  correctly; badge count matches the number of active dropdown filters;
  "Clear all filters" still does a full-page navigation (unchanged
  behavior, comment already explains why) and empties the search box.
- Accepting/rejecting a job from its row still live-decrements the visible
  primary-tab or More-panel count via oob swap.
- Existing jobs-page Playwright/route tests (if any assert on the removed
  `.tab-check`/`.filter-row` classes) get updated to the new class names.

## Out of scope

- Click-outside / `Escape`-to-close for the new popovers (see trade-offs
  above).
- Live oob-updating counts inside the Combine panel or the mobile
  `<select>` options (see trade-offs above) — both self-heal on the next
  full `#jobs-content` render.
- Any change to which statuses count as "primary" vs "archival" being
  user-configurable — the four/three split is hardcoded per this spec's
  approved mockup.
