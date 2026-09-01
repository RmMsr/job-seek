# Linked status tabs + always-on status badge + toolbar polish

Builds on the full-text-search work already merged into `worktree-fulltext-search`
(`2026-09-01-jobs-fulltext-search-design.md`).

## Problem

- Search returns every status merged and bm25-ranked, so Trash / Not-relevant
  listing-blobs dominate the top and bury real jobs.
- The status tabs are single-select. Seeing New + Accepted + Rejected together means
  clicking three tabs one at a time — slow, no overview.
- The per-row status is only shown for Accepted/Rejected/Trash, and only as an
  after-the-title badge in search results.
- The redesigned toolbar wastes vertical space and the Sort / Select-all cluster
  overflows the right edge below ~820px.

## 1. Linked status tabs

The six tabs (`new`, `lead`, `accepted`, `rejected`, `not_relevant`, `trash`) stay, but
the shown set can now be more than one.

### Interaction

- **Click a tab's label** → show exactly that tab (today's behaviour). Also the way to
  collapse a multi-selection back to one.
- **Click a tab's checkbox** → toggle just that tab in/out of the shown set, leaving the
  others untouched. Can't remove the last one.
- Checkbox rendering (**reveal on interaction**): in the default single-tab state no
  checkboxes are drawn — the bar looks exactly as it does today. Hovering a tab reveals a
  hollow **+** on it. Once two or more tabs are active, every tab shows its box (filled
  check when in the set, hollow + when not).
- When more than one tab is active a **Reset** link appears next to the tabs → back to
  `new` only.
- Merged view: rows carry their status badge (§2), Sort orders the union.

### Search interaction

- Entering a search (`q` non-empty) while only a single tab is active **seeds** the shown
  set to **New + Leads + Accepted + Rejected**. Not relevant + Trash stay out until their
  checkbox is clicked.
- The tab bar stays visible during search and acts as the scope editor (checkboxes only —
  label clicks still work). Sort stays hidden (relevance is forced); Select-all stays
  hidden (bulk-in-search is still out of scope).
- The `.search-summary` line ("N results for …" + Clear search) renders **above** the tab
  bar, not instead of it.
- Clearing the search keeps whatever set you had; label-click or Reset returns to single.
- Known minor: the URL pushed right after the first search keystroke still reads
  `?q=…&status=new` (the request URL) while the view already shows the seeded four. The
  next interaction writes the full `status=new,lead,accepted,rejected` and it self-heals; a
  reload at that instant just re-seeds to the same view. Acceptable.

### State / URL

- `status` becomes a comma list: `?status=new,accepted,rejected`. Order preserved, first
  entry is the "primary" (drives nothing but Reset's fallback and which tab reads as
  active in single mode).
- `JobFilter`:
  - `status_tab: str` → replaced by `statuses: tuple[str, ...]` (default `("new",)`).
  - `status_tab` kept as a `@property` returning `statuses[0]` — templates and
    `_content_context` that only need the primary keep working.
  - `is_multi -> bool` = `len(statuses) > 1`.
  - `from_params`: split `status` on `,`, keep entries in `VALID_TABS`, dedupe preserving
    order, fall back to `("new",)`.
  - `query_params()`: `status` = `",".join(statuses)`.
  - Helpers: `for_status(tab)` → single; `with_status_toggled(tab)` → add if absent,
    remove if present (never empties — removing the last is a no-op); `reset_statuses()` →
    `("new",)`.
  - `cleared()` keeps `statuses` (clears scenario/source/org only, as today it kept the
    tab).

## 2. Always-on status badge

- The `search_status_badge` macro in `jobs/_macros.html` is renamed `status_pill` and its
  gate is removed — it renders on **every** row, in **every** view.
- It moves into `meta_tags(job)` as the **first** `<dd>` in the `<dl class="job-tags">`,
  ahead of the matched-scenario tags. The old `status_badge` macro (Accepted/Rejected/
  Trash only) and its call inside `meta_tags` are deleted — `status_pill` covers all six
  buckets:
  - `accepted` / `rejected` / `trash` → from `jobs.status`
  - `lead` → `content_type == 'lead'`
  - `new` → `content_type == 'job_posting'` and (passed a gate or `gate_override`)
  - `not_relevant` → `content_type == 'job_posting'`, otherwise
  - fallback → `content_type` text ("error", "unknown")
- The after-the-title badge in `jobs/_row.html`
  (`{% if filter and filter.searching %}{{ macros.search_status_badge(job) }}{% endif %}`)
  is removed.
- `.status-pill.*` colour classes (teal / amber / green / red / grey) stay as the
  redesign left them.

## 3. Query layer

`get_jobs()` stays exactly as it is — `pipeline.py` and ~40 tests use its single-criterion
kwargs (`status=`, `content_type=`, `gate_status=`, `scenario_gate=`, …) and those are a
genuinely different query from a tab bucket (the pipeline wants `status='new'` regardless
of gate/content_type; the *New tab* means job_posting + passed gate). Don't touch it.

The per-tab bucket definitions move out of `routes/jobs.py`'s `_BASE_KWARGS_FOR_TAB` into
`db/queries.py` as `_TAB_PREDICATE: dict[str, str]` — one SQL boolean per tab, referencing
`jobs.*` plus the `gate.` / `scored.` aliases from `_GATE_JOIN`:

| tab | predicate |
| --- | --- |
| `new` | `jobs.status='new' AND jobs.content_type='job_posting' AND (` _GATE_PASSED_CLAUSE_ `)` |
| `lead` | `jobs.status='new' AND jobs.content_type='lead'` |
| `accepted` | `jobs.status='accepted'` |
| `rejected` | `jobs.status='rejected'` |
| `not_relevant` | `jobs.status='new' AND jobs.content_type='job_posting' AND (` _GATE_FAILED_CLAUSE_ `)` |
| `trash` | `jobs.status='trash'` |

- `_tabs_clause(tabs: list[str]) -> str` → `"(" + " OR ".join(_TAB_PREDICATE[t] for t in tabs) + ")"`.
  `_GATE_PASSED_CLAUSE` / `_GATE_FAILED_CLAUSE` take no params, so no param plumbing.
- **New** `get_jobs_for_tabs(conn, tabs, *, source_id=None, scenario_id=None, org=None, org_none=False, scenario_none=False, order="change") -> list[dict]`
  — `SELECT _GATE_SELECT _GATE_JOIN WHERE _tabs_clause(tabs)` AND `_composable_clauses(...)`
  AND (when `scenario_none`) the existing scored/gate "none" clause; `ORDER BY _ORDER_BY[order]`.
  This is the only new query and the jobs-list route is its only caller.
- `search_jobs(...)`: add `tabs: list[str] | None = None`; when given, AND `_tabs_clause(tabs)`
  onto the `MATCH` query. Everything else unchanged. (New param, sole caller is the route.)
- `get_job` / `get_job_counts` — untouched. Counts stay per-bucket and independent of which
  tabs are selected (they answer "how many in this bucket, given the scenario/source/org
  narrowing"). `get_job_counts` may reuse `_TAB_PREDICATE` internally if it simplifies, but
  that's optional cleanup, not required.

## 4. Routes (`routes/jobs.py`)

- Delete `_BASE_KWARGS_FOR_TAB`.
- `_effective_tabs(f: JobFilter) -> list[str]`: `list(f.statuses)`, except when
  `f.searching and not f.is_multi` → the current tab unioned with the seed set
  `{"new", "lead", "accepted", "rejected"}`, in `VALID_TABS` order. (So searching from New
  gives the four; searching from Trash gives Trash + the four, rather than silently
  dropping what you were looking at.)
- `_jobs_for_filter(conn, f)`:
  ```
  tabs = _effective_tabs(f)
  if f.searching:
      return q.search_jobs(conn, f.q, tabs=tabs, source_id=f.source_id,
                           scenario_id=f.scenario_id, org=f.org, org_none=f.org_none)
  return q.get_jobs_for_tabs(conn, tabs, source_id=f.source_id, scenario_id=f.scenario_id,
                             org=f.org, org_none=f.org_none, scenario_none=f.scenario_none,
                             order=f.order)
  ```
- `_stale_badge` / `_render_updated_job_html` — unchanged; they funnel through
  `_jobs_for_filter`, so a job that stays within the shown set after a feedback action
  keeps its place and just updates its pill.
- `_content_context` — `"status": f.status_tab` stays (primary); add nothing else, the
  template reads `filter.statuses` directly.
- `_filter_from_bulk_form` + the four bulk routes: the hidden `status_filter` input and
  the `status_filter` form field carry `",".join(filter.statuses)`; `_filter_from_bulk_form`
  passes it straight into `JobFilter.from_params({"status": status_filter or "new", ...})`.

## 5. Templates

- **`jobs/_content.html`**
  - `#jobs-status-marker` value → `{{ filter.statuses | join(',') }}`.
  - `status_filter` hidden input → same join.
  - Tab bar: for each `tab_def`, render the label as an `<a hx-get>` to
    `filter.for_status(tab)` and — when `filter.is_multi` or on hover — a sibling
    checkbox `<a hx-get>` to `filter.with_status_toggled(tab)` (`+` / check glyph by
    membership). `.active` class when `tab in filter.statuses`.
  - `Reset` link (`<a hx-get>` to `filter.reset_statuses()`) shown when `filter.is_multi`.
  - Search-active: render `.search-summary` **and then** the tab bar (checkbox mode);
    keep Sort + `.filter-tools` hidden while `filter.searching`.
- **`jobs/_macros.html`** — rename + un-gate `status_pill`; put it first in `meta_tags`;
  delete `status_badge`.
- **`jobs/_row.html`** — drop the after-title badge line; keep the
  `not (filter and filter.searching)` guard on the bulk checkbox.
- **`jobs/_counts_oob.html`** — unchanged (counts are still per-bucket).

## 6. Toolbar polish (`base.html` style block + `_content.html` markup)

Driven by screenshots (`temp/shoot.py` against the dev server), not guesswork:

- Tighten the stack: `h1` → Add panel → search+filter → tabs → list, each gap ~`--space-2`
  (0.5rem), no oversized band anywhere.
- Lighten the search+filter card: drop the full border/box; a single hairline under the
  search field separating it from the selects row is enough. Keep the inset magnifier and
  `:focus-within` accent.
- Fix the Sort / Select-all cluster: it must never overflow. Move it onto the filter-select
  row as a right-aligned group (`margin-left:auto`) that wraps below the selects on narrow
  widths instead of clipping. Sort + Select-all stay together.
- Status pills: verify they read cleanly as the first tag on every row at both widths.

## 7. Tests

- **`test_job_filter.py`** — `status` comma parse (order kept, junk dropped, dedupe,
  empty→`("new",)`); `query_params()` join; `is_multi`; `for_status` / `with_status_toggled`
  (add, remove, last-one no-op) / `reset_statuses`; `status_tab` property = first.
- **`test_queries.py`** — `get_jobs_for_tabs`: single tab matches the matching
  `get_jobs(...)` result; `tabs=["new","accepted"]` returns the union; `not_relevant` vs
  `new` still split on the gate; source/org/scenario narrowing still applies;
  `search_jobs(tabs=[...])` narrows the MATCH set and excludes `trash` when not asked for.
  Existing `get_jobs` tests are untouched (that function is unchanged).
- **`test_routes_jobs.py`** — `/jobs?status=new,accepted` renders both buckets' jobs with
  pills; the per-tab checkbox links point at toggled `status` values; `Reset` link present
  only when multi; `/jobs?q=engineer` seeds to the four buckets and excludes a seeded
  Trash job until `status=…,trash`; every row in the normal view shows a status pill;
  no after-title badge markup. Update existing assertions that assumed a single `status`
  value in the tab-link URLs.
- Full suite green.
