# Jobs list filtering

Replace the ad-hoc, mutually-exclusive `source_id` / `scenario_id` deep-link
"modes" on the jobs list with a composable filter that layers scenario, source,
and organization on top of the status tabs.

Backlog items covered: "Scenario filter for jobs list including None defaulting
to All", "Allow tags for sources to be used as filter" (reduced: the source name
*is* the tag — no new tag vocabulary), "Show organizations as tags that also work
as filters".

## Behaviour

- The six status tabs (New Jobs / New Leads / Accepted / Rejected / Not relevant
  / Trash) stay as the primary axis and are **always** shown.
- A filter sub-row sits beneath the tabs with three `<select>` controls:
  - **Scenario** — `All` (default), `None`, then one option per scenario.
    `None` = job was scored against scenarios but cleared no gate.
  - **Source** — `All` (default), then one option per source (by name).
  - **Organization** — `All` (default), then one option per distinct
    non-empty `jobs.company` value, sorted.
- The three filters AND together and AND with the current status tab
  (e.g. Accepted + scenario X + org Google).
- Status-tab counts are recomputed with the active scenario/source/org filters
  applied, so the number next to each tab matches what that tab would show.
- **New Jobs now excludes leads** — the tab and its count become
  `content_type = 'job_posting'` only. Leads appear solely under New Leads.
  (Today a gate-passing lead shows in both.)
- Filters apply uniformly to every tab. A scenario filter on Trash is valid,
  just usually empty.

## URL / params

`/jobs?status=<tab>&scenario=<id|none>&source_id=<id>&org=<name>`

- `scenario` is the new param; `scenario=none` is the sentinel.
- `scenario_id` (old param, still emitted by links on the Scenarios page) is
  accepted as an alias for `scenario`.
- `source_id` keeps its name (still emitted by Sources / Fetch page links).
- Absent / empty param = that filter is `All`.

## Filter model

New `JobFilter` dataclass (`app/job_filter.py`):

```
status_tab: str            # "new" | "lead" | "accepted" | "rejected" | "not_relevant" | "trash"
scenario_id: int | None    # a specific scenario
scenario_none: bool        # the "None" bucket
source_id: int | None
org: str | None
```

- `JobFilter.from_request(request)` parses query params (handling the
  `scenario` / `scenario_id` alias and the `none` sentinel), defaulting
  `status_tab` to `"new"`.
- `.query_params()` renders it back to a query string for links, hidden form
  fields, and `data-progress-url` attributes.
- `.is_narrowed` — true when any of scenario/source/org is set (drives the
  "Clear all filters" affordance).

`JobFilter` replaces the `filter_ctx` dict threaded through
`_filter_context` / `_stale_badge` / `_render_updated_job_html` /
`_content_context` and the bulk endpoints.

## Query changes (`app/db/queries.py`)

- `get_jobs(...)` gains `org: str | None` and `scenario_gate: str | None`
  (`"none"` sentinel). Keeps existing `scenario_id`, `source_id`, `status`,
  `content_type`, `gate_status`.
  - `org` → `jobs.company = ?` (exact match).
  - `scenario_gate == "none"` → `scored.scored_count IS NOT NULL AND
    (gate.passed_count IS NULL OR gate.passed_count = 0)` (the scored-but-no-gate
    condition already expressed in `_GATE_FAILED_CLAUSE`, without the
    `evaluation_completed_at` / `gate_override` parts).
- `get_job_counts(conn, *, scenario_id=None, scenario_none=False, source_id=None, org=None)`
  — every one of the six counts gets the active filters AND-ed in.
  `counts["new"]` also gains `jobs.content_type = 'job_posting'`.
- New `get_distinct_companies(conn) -> list[str]` —
  `SELECT DISTINCT company FROM jobs WHERE company != '' ORDER BY company`.
- The status tab → base-params mapping lives in one helper
  (`_base_params_for_tab`) replacing the `if/elif` chain in
  `_get_filtered_jobs`.

## UI

### `app/templates/jobs/_content.html`

- Delete the `{% if filter_source_id %} … replace tab bar … {% endif %}`
  branch. Tabs always render (via the existing `else` block), each `<a href>`
  carrying the current filter query string so switching tabs keeps the
  scenario/source/org filter.
- Add the filter sub-row: three `<select>`, each
  `hx-get="/jobs"` + `hx-include` the sibling selects + `hx-target="#jobs-content"`
  `hx-swap="innerHTML"` + `hx-push-url="true"`, on `change`. Current value
  marked `selected`.
- When `filter.is_narrowed`: show "Clear all filters" (link to `/jobs?status=<tab>`)
  and the filtered total.
- Empty result → "No jobs match these filters." (keep the existing
  `stale_jobs` branch).

### `app/templates/jobs/_macros.html` — `meta_tags`

- Scenario chips: keep `tag tag-accent`, wrap each in an `<a>` that sets
  `scenario=<id>`. Needs the scenario id alongside the name — add a
  `passed_scenario_ids` `GROUP_CONCAT` to the gate join parallel to
  `passed_scenario_names` and zip them in the template.
- Add an **organization** chip (`tag tag-org`) linking to `org=<company>`,
  shown when `job.company` is set.
- Source chip: becomes an `<a>` linking to `source_id=<job.source_id>`,
  class `tag tag-source`.
- Status chip: unchanged (already uses the semantic `score-badge` colours).
- content_type chip: unchanged plain `.tag`.
- Chip links carry the *rest* of the current filter (so clicking an org chip
  inside a scenario filter adds to it). Links `onclick="event.stopPropagation()"`
  like the existing permalink icon, and use `hx-get` / `hx-target="#jobs-content"`
  to stay in-page.

### Chip colours (`app/templates/base.html`)

Distinct, reusing existing themed tokens (work in light + dark):

| chip | class | tokens |
|------|-------|--------|
| scenario | `.tag-accent` (existing) | `--accent` on `--accent-tint` |
| source | `.tag-source` (new) | `--warning` on `--warning-tint` |
| organization | `.tag-org` (new) | `--success-strong` on `--success-tint` |
| status | `score-badge` (existing) | semantic green/red/neutral |
| content type | `.tag` (existing) | neutral |

## Threading updates

- `list.html` hidden `*_filter` fields → emit `scenario` / `source_id` / `org`
  from `filter.query_params()`.
- `_content.html` `data-progress-url` query strings for bulk reevaluate/reset →
  same.
- Bulk endpoints (`bulk-feedback`, `bulk-delete`) gain `org` and `scenario`
  form fields next to the existing `source_id_filter` / `scenario_id_filter`;
  rebuild the `JobFilter` from them.
- `_stale_badge` takes a `JobFilter`; its "moved to …" hrefs keep only the
  status part (a job that fell out of the filtered set links to the plain tab).

## Edge cases

- Org values are raw LLM output — "Google" and "Google LLC" are distinct
  options. Acceptable for a personal single-user app; not normalised. Known
  limitation, documented here only.
- A scenario that is deleted while selected → `get_scenario` returns `None`;
  treat as `All` (drop the filter) rather than erroring.
- `org` value with characters needing URL-encoding — rely on Starlette's
  query param handling; encode in `.query_params()`.

## Testing

- `get_jobs`: each filter alone; scenario+source+org+status combined;
  `scenario_gate="none"` includes a scored-no-gate job and excludes an
  unscored lead and a gate-passing job.
- `get_job_counts`: counts shift when a scenario/source/org filter is passed;
  `counts["new"]` excludes leads.
- `get_distinct_companies`: sorted, no blanks, deduped.
- `JobFilter`: param parsing incl. `scenario_id` alias and `none` sentinel;
  round-trip through `.query_params()`.
- Route tests: filter selects render `selected`; changing one preserves the
  others and the tab; "Clear all filters" resets to `?status=<tab>`; clicking
  an org / source / scenario chip narrows the list.
- Stale badge: a job acted on that no longer matches the active filter gets the
  badge in place, doesn't vanish (single + bulk).
- New Jobs tab renders no leads.
