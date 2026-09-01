# Full-text search on the Jobs page

## Goal

A search box on `/jobs` that finds jobs by free text across **all statuses** at once,
ranking role (`title`) and org (`company`) matches above description-body matches.

## Behaviour

- **Global.** A query ignores the status tabs — results come from New, Leads, Accepted,
  Rejected, Not relevant and Trash together. Each result row shows a status pill.
- **Composable.** The Scenario / Source / Organization filters still narrow the results.
  The Sort control and the tab bar are hidden while a query is active; a "Clear search"
  link and an "N results" line take their place. Clearing the query returns to the tab
  you were on (`status` stays in the URL throughout).
- **Ranking is fixed** to relevance: `title`/`company` hits outrank `headline`/`summary`
  hits, which outrank `simplified_content` (body) hits. No user-facing sort choice.
- **Search-as-you-type.** Debounced ~300ms after the last keystroke. Multi-word queries
  are AND-ed; the last word is a prefix (`eng` matches `engineer`).

## Implementation

### 1. FTS5 index + triggers (`app/db/schema.py`)

External-content FTS5 table, plus three sync triggers:

```sql
CREATE VIRTUAL TABLE jobs_fts USING fts5(
    title, company, headline, summary, simplified_content,
    content='jobs', content_rowid='id',
    tokenize='porter unicode61'
);
```

`AFTER INSERT`, `AFTER UPDATE` and `AFTER DELETE` triggers on `jobs` keep `jobs_fts` in
sync (the UPDATE/DELETE triggers use the `'delete'` command form required for
external-content tables). Because the triggers live at the SQLite level, no call site in
`queries.py` or `pipeline.py` changes.

New migration `_migrate_add_jobs_fts`, appended **last** in `init_db`:
- guard: return if `jobs_fts` already exists in `sqlite_master`
- create the virtual table and the three triggers
- backfill: `INSERT INTO jobs_fts(rowid, title, company, headline, summary, simplified_content)
  SELECT id, title, company, headline, summary, simplified_content FROM jobs`

Create-once, no defensive re-sync on startup. One rule added to CLAUDE.md's *Database
migrations* section: a future migration that rebuilds the `jobs` table must follow itself
with `INSERT INTO jobs_fts(jobs_fts) VALUES('rebuild')`.

### 2. `search_jobs()` (`app/db/queries.py`)

```python
def search_jobs(conn, query, *, source_id=None, scenario_id=None,
                org=None, org_none=False) -> list[dict]
```

- **Parse** `query`: split on non-alphanumeric runs, drop empties, wrap each token in
  double quotes, append `*` to the last token. `"senior python"` → `"senior" "python"*`.
  No tokens → return `[]`.
- **SQL**: `_GATE_SELECT` + `_GATE_JOIN` + `JOIN jobs_fts ON jobs_fts.rowid = jobs.id`,
  `WHERE jobs_fts MATCH ?` plus the composable clauses, `ORDER BY
  bm25(jobs_fts, 10.0, 10.0, 3.0, 3.0, 1.0)` ascending (lower bm25 = better match).
- **No** status / content_type / gate filtering — search is global.
- The source_id / org / org_none / scenario_id clause building shared with `get_jobs` is
  pulled into a `_composable_clauses(...)` helper used by both.

### 3. `JobFilter` (`app/job_filter.py`)

- new field `q: str = ""`, parsed from `params.get("q")` and stripped
- `query_params()` includes `q` when non-empty
- new `@property searching` → `bool(self.q)`
- `status` is always retained in `query_params()` (already is) so "Clear search" works

### 4. Route + context (`app/routes/jobs.py`)

- `job_list`: when `f.searching`, build the content context from `search_jobs(...)` with
  a `searching=True` flag, instead of `_jobs_for_filter` / counts.
- `_jobs_for_filter` and `_stale_badge` branch on `f.searching` and call `search_jobs`,
  so the post-feedback single-row re-render keeps working. A decided job still matches the
  query, so it stays in place and gets no stale badge — its status pill just updates.
- The counts OOB swap is skipped while searching (the tab counters aren't in the DOM).

### 5. Templates

- **`jobs/list.html`**: the `<input type="search" name="q">` sits on its own row above
  the tab bar, **outside** `#jobs-content` — the fragment htmx swaps on every keystroke —
  so the input keeps focus and caret while typing. `hx-get="/jobs"`,
  `hx-target="#jobs-content"`, `hx-trigger="keyup changed delay:300ms, search"`,
  `hx-include` a hidden `#jobs-status-marker` (current tab) + the scenario/source/org
  selects.
- **`jobs/_content.html`**: render the hidden `#jobs-status-marker`; add `[name='q']` to
  each filter select's `hx-include`. When `filter.searching`: hide `.filter-links` and the
  Sort `<label>`; show a "Clear search" link + "N results".
- **`jobs/_macros.html`**: new `search_status_badge(job)` macro — New / Lead / Not
  relevant / Accepted / Rejected / Trash. "Not relevant" = `content_type='job_posting'`
  with no passed gate and no `gate_override`.
- **`jobs/_row.html`**: render `search_status_badge(job)` when `filter.searching`.

## Testing

- **`test_schema.py`** — migration creates `jobs_fts` and backfills existing rows.
- **`test_queries.py`** — `search_jobs`: title hit outranks body hit; prefix match;
  multi-term AND; source / org / scenario narrowing; trigger sync (insert → findable,
  retitle → new term hits and old term doesn't, delete → gone).
- **`test_routes_jobs.py`** — `/jobs?q=` returns results spanning statuses, hides the tab
  bar, honours the source/org filters; empty `q` renders the normal tabbed view.
- **`test_job_filter.py`** — `q` parse and `query_params()` round-trip.
