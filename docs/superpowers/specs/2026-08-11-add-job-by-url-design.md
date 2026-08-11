# Add job by URL — design

**Problem:** the only way a job enters the system today is through a configured `sources` row being fetched (`app/pipeline.py`'s `run_fetch`, dispatched via `app/routes/fetch.py`). There's no way to hand-add a single posting you found by other means (e.g. a link a friend sent) without wiring up a whole new source.

**Fix:** a small "paste a URL" form on the jobs list page that fetches the page over plain HTTP, inserts a job row, and runs it through the same classify/summarize/score/fit pipeline (`_ingest_posting`) that automatic fetches use — reusing as much of the existing pipeline and streaming-progress UI as possible.

## 1. The "Manual" source

`jobs.source_id` is `NOT NULL REFERENCES sources(id)`, so a manually-added job still needs a source to point at. Rather than allowing `NULL` (which would ripple through every query that joins `jobs` to `sources`, e.g. `_enrich_jobs` in `app/routes/jobs.py`), a single synthetic `sources` row is lazily created and reused: `name="Manual"`, `url=""`, `fetcher_type="manual"`.

`sources.fetcher_type` has `CHECK(fetcher_type IN ('http', 'playwright', 'slack', 'finn_listing'))`, so `'manual'` isn't currently a legal value. `app/db/schema.py` already has a precedent for widening this exact constraint (`_migrate_sources_fetcher_type`, which rebuilds the table via a `sources_new`/copy/drop/rename cycle because SQLite can't `ALTER ... CHECK`). This design adds:
- `'manual'` to the `CHECK` list in `_DDL`'s `sources` table definition.
- A new `_migrate_sources_fetcher_type_manual(conn)` following the same shape as `_migrate_sources_fetcher_type` (checks `sqlite_master.sql` for `'manual'`, rebuilds if absent), registered in `init_db()`.

A new query helper, `q.get_or_create_manual_source(conn) -> int`, selects the row where `fetcher_type = 'manual'`, inserting it on first use, and returns its `id`.

The Manual source is excluded from source-management UI since it isn't a real, fetchable source:
- `app/routes/sources.py`'s listing (backing `sources/index.html`) filters it out of `q.get_sources(conn)`.
- `app/routes/fetch.py`'s `fetch_panel` does the same for `fetch/panel.html`.

It's *not* hidden from the jobs list/detail views — a manually-added job shows `source_name = "Manual"` same as any other job's source, via the existing `_enrich_jobs` join in `app/routes/jobs.py`.

## 2. Fetching the pasted URL

A new one-off fetch helper (not `HttpFetcher`, which takes a `source` dict and swallows every failure by returning `[]` — unsuitable here since failures need to be visible): given a raw URL string, `httpx.get(url, timeout=30, follow_redirects=True)`, then on a 200 response, BeautifulSoup `get_text(separator="\n")` — the same extraction `HttpFetcher._parse` already does. Returns either the extracted text or raises/signals a failure reason (status code, timeout, connection error) for the caller to handle.

This lives as a small function near the new route, not in `app/fetchers/`, since it isn't a `Fetcher` implementation (no `source` row, no `fetch() -> list[RawJob]` shape) — it's a single ad-hoc fetch.

## 3. Duplicate detection

Before fetching, the route checks `q.url_exists(conn, url)` (same helper `run_fetch` already uses). If the URL is already tracked, the stream immediately reports it as already-tracked with a link to the existing job's detail page (`/jobs/{id}`) and stops — no fetch attempt, no new row, no pipeline run.

## 4. Route: `POST /jobs/add-by-url`

Added to `app/routes/jobs.py`, following the existing streamed-action shape (`job_reset`, `job_bulk_reset`): takes `url: str = Form(...)`, returns a `StreamingResponse` of newline-delimited progress lines.

Flow:
1. Validate non-duplicate (§3). If duplicate, stream the message and return.
2. Fetch the URL (§2).
   - **On failure:** insert a job row via `q.insert_job(conn, source_id=manual_source_id, url=url, title=url, company="", raw_text="")` with `content_type` set to `"error"` directly (bypassing `_ingest_posting`, since there's no raw text to classify) — mirroring how classify failures already land on `content_type = "error"` in the jobs table's `CHECK` list. Stream an error line describing the failure.
   - **On success:** insert the job row with the extracted `raw_text` (`title=""`, `company=""`, matching what `run_fetch` does for `HttpFetcher`-sourced jobs — real title/company come from `summarize()` inside the pipeline), then `yield from _ingest_posting(...)` exactly as `run_fetch` does per-job, streaming its usual "Classified as…" / "Scored…" / "Fit…" lines.
3. Regardless of outcome (new row, error row, or duplicate short-circuit), the stream ends with two `HTML:`-prefixed chunks, matching `job_reset`'s convention:
   - A re-render of `jobs/_content.html` under the request's current filters (so the new/errored job appears if it matches; the whole list refreshes rather than trying to splice in a row that didn't previously exist in the DOM).
   - The `jobs/_counts_oob.html` fragment, to keep nav tab counts in sync.

If `_ingest_posting` deletes the job (irrelevant content), the final list re-render simply won't include it — same as `run_fetch` today.

## 5. UI

**Form placement:** a small always-visible form at the top of `jobs/list.html`, above the filter bar:

```html
<form class="add-job-by-url">
  <input type="url" id="add-job-url" name="url" placeholder="Paste a job posting URL…" required>
  <button type="button" class="btn"
    data-progress-url="/jobs/add-by-url"
    data-progress-url-input="#add-job-url"
    data-progress-target="#jobs-content"
    data-progress-display="#add-job-progress">Add</button>
  <span id="add-job-progress" class="reset-progress" aria-live="polite"></span>
</form>
```

**JS:** `base.html`'s shared `data-progress-url` click handler (the same one powering Fetch/Reset/bulk-reset buttons) gains one small addition, parallel to its existing `data-progress-jobs` → `job_ids` handling: if the clicked element has `data-progress-url-input`, read that selector's input value and send it as `url` in the POST body (`URLSearchParams` with `url` = the input's value), instead of firing with no body. No other change to the shared streaming/OOB/finish machinery — `data-progress-target="#jobs-content"` already does exactly what's needed (swap the whole list container's `innerHTML` with the streamed `HTML:` chunk on completion).

After a successful add, the input is left as-is (not cleared) only if the button click handler errors before completion; on a normal `finish()` the list re-render happens and the form/input remain in the DOM untouched — clearing the input on success is a one-line addition inside `finish()`'s non-failure branch, resetting `#add-job-url` when `data-progress-url-input` was set.

## Out of scope

- **JS-rendered pages / login-gated sites.** Plain HTTP only, per the `HttpFetcher` precedent — a site that needs Playwright can be added as a proper `playwright` source instead.
- **Editing a manually-added job's URL/source after the fact.** Same lifecycle as any other job once inserted (reset, feedback, accept/reject/trash all work unmodified since it's a normal `jobs` row).
- **Bulk/multi-URL paste.** One URL per submission.

## Testing

1. `q.get_or_create_manual_source`: first call creates the row (`fetcher_type='manual'`), second call returns the same `id` without inserting a duplicate.
2. Migration test: an existing DB with the old `sources.fetcher_type` CHECK (no `'manual'`) can, after `init_db`, insert a `fetcher_type='manual'` row without a constraint violation; pre-existing `http`/`playwright`/`slack`/`finn_listing` rows survive the rebuild unchanged.
3. Route test: `POST /jobs/add-by-url` with a URL already present in `jobs` returns a stream containing the "already tracked" message and does not insert a second row.
4. Route test: `POST /jobs/add-by-url` against a URL whose fetch fails (mock httpx to raise/return non-200) inserts a job row with `content_type='error'` and streams an error line.
5. Route test: `POST /jobs/add-by-url` against a URL whose fetch succeeds (mock httpx + a fixed HTML body) runs the full pipeline and results in a job row with `source_id` pointing at the Manual source and a non-error `content_type`.
6. Route test: the final `HTML:` chunk contains the `jobs/_counts_oob.html` fragment's expected `id`s.
7. `sources/index.html` and `fetch/panel.html` route tests: a Manual source present in the DB does not appear in either page's rendered output.
8. Manual/browser verification: paste a real static job-posting URL, watch progress stream, confirm the job appears in the list with correct source "Manual"; paste the same URL again and confirm the duplicate message.
