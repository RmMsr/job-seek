# Revisit task: timestamps, scope, and UX polish

Follow-up to `2026-09-01-revisit-closed-jobs-design.md`. Three problems:

1. The `jobs_revisit` sweep pulls in jobs it shouldn't — gate-failed "Not relevant"
   postings (e.g. job 228), `content_type='error'`, and every `rejected` job.
2. The 200-job sweep cap slices by `jobs.id ASC`, so it always re-checks the same
   oldest rows and never rotates through a backlog.
3. `jobs.fetched_at` is misnamed: it's set once at row creation and only ever used
   as a *created-at* sort key. There's no field for "when did we last pull this
   job's live page", which is exactly what a rotating sweep needs.

Plus four fetch-panel UX fixes.

## 1. Split `fetched_at` into `created_at` + `fetched_at`

- **`created_at`** — immutable, set at row birth. Every current use of `fetched_at`
  (three `_ORDER_BY` clauses + the `_sort_key` mirror in `routes/jobs.py`) moves to
  this. User-facing ordering is unchanged.
- **`fetched_at`** — mutable, "last time we pulled this job's live page (or its
  birth)". Backfilled to `created_at`. Only `run_revisit_job` bumps it — reprocess
  and re-evaluate work from stored text and leave it alone.

### Migration

Canonical DDL in `schema.py` renames the column and adds the new one. New migration
`_migrate_jobs_split_created_fetched`, appended to `init_db`'s chain, guarded on
`"created_at" in <jobs CREATE sql>`:

```sql
ALTER TABLE jobs RENAME COLUMN fetched_at TO created_at;
ALTER TABLE jobs ADD COLUMN fetched_at TEXT NOT NULL DEFAULT (datetime('now'));
UPDATE jobs SET fetched_at = created_at;
```

FTS triggers reference only text columns, so `RENAME COLUMN` leaves them intact — no
rebuild, no trigger recreation. No new index (~330 rows).

## 2. Fix `get_revisitable_jobs`

```sql
WHERE <source is not slack>
  AND ( (status='new' AND content_type='job_posting' AND (_GATE_PASSED_CLAUSE))
     OR (status='new' AND content_type='lead')
     OR status='accepted' )
ORDER BY jobs.fetched_at ASC, jobs.id ASC
```

Reuses `_GATE_JOIN` (already in the query via `_GATE_SELECT`) for the `gate`/`scored`
subqueries `_GATE_PASSED_CLAUSE` needs. Drops gate-failed, `error`, and `rejected`.
Inclusions match the `new` / `lead` / `accepted` tab predicates.

`_REVISIT_SWEEP_CAP = 200` stays as a safety valve; with `fetched_at ASC` ordering
it now rotates stalest-first if the eligible set ever exceeds 200 (today it's ~24).

## 3. Stamp `fetched_at` on revisit

New `q.mark_job_revisited(conn, job_id)` → `UPDATE jobs SET fetched_at = datetime('now')`.
Called at the end of `run_revisit_job` for every verdict that actually fetched the
page — i.e. not the `skipped` (Slack) early return.

## 4. Fetch-panel UX

- Button `/revisit/all`: label **"Revisit open jobs" → "Refresh open jobs"**.
- The `<p class="hint">` explanatory line becomes a native `title=` tooltip on the
  button (`.hint` is unstyled and used nowhere else; no tooltip system exists).
  Reworded: *"Re-checks each open job's live posting; anything no longer reachable
  moves to Trash."*
- `fetch/_table.html` header **"Last run" → "Last fetch"**.
- **No page reload on refresh.** Today the sweep returns no HTML chunks, so the
  progress JS falls through to `location.reload()` (`base.html:844`). Add
  `data-progress-oob` to the button (oob path applies no swap, skips the reload)
  and return the *"Revisited N · closed X · changed Y"* summary as a **notice** from
  `_task_jobs_revisit`'s sweep branch so the outcome still surfaces.

## 5. Single-job revisit → result link

`_results()` / `_link()` in `routes/tasks.py` match `_SINGLE_JOB_KINDS` on
`params["job_id"]`; `jobs_revisit` uses `params["job_ids"]` (a list), so a per-job
revisit currently shows no "View job" link on its task page. Add: `kind ==
"jobs_revisit"` with exactly one `job_ids` entry → `View <title>` result linking to
`/jobs/<id>`, and the same in `_link()` for the task-list row. Multi-job sweeps get
no per-job link.

## Tests

- `test_queries.py`: `get_revisitable_jobs` excludes not-relevant / error / rejected,
  includes gate-passed `new` / lead / accepted, orders by `fetched_at`;
  `mark_job_revisited` bumps only `fetched_at`, not `created_at` / `status_changed_at`.
- `test_revisit.py`: `fetched_at` advances after `run_revisit_job`; sweep is
  stalest-first; a `rejected` job is not swept.
- Migration test: old-shape DB → `created_at` == old `fetched_at`, `fetched_at`
  backfilled equal.
- `test_routes_tasks.py`: single-job `jobs_revisit` task exposes a `/jobs/<id>`
  result link; multi-job does not.
- `test_routes_fetch.py`: `/revisit/all` sweep result carries the summary notice.
- Existing sort/order-by tests stay green (pure rename).
