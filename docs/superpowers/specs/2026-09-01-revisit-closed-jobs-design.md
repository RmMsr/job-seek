# Detect closed jobs by revisiting them

## Goal

Re-fetch a job's original URL and, if it can no longer be retrieved, treat the
opening as closed: append a plain-language note and move it to Trash. Catch
openings that fill or expire after we first ingested them.

## Behaviour

- **What counts as closed.**
  - an HTTP error (404/410/403/5xx) or network failure — retried **once** after a
    short delay before it counts;
  - a page with no extractable content (after the Playwright fallback);
  - an LLM check (see below) that judges the page **`gone`** (no longer this job —
    removed, redirected to a listing, an error/login wall, unrelated content) or
    **`closed`** (still this job, but no longer accepting applicants).
- **The "same posting?" check.** Once a page is reachable with real content, one
  temperature-0 LLM call compares the live page text against the **summary we
  stored** for that job and returns `gone` / `closed` / `unchanged` / `changed`.
  It is told to ignore unrelated page churn — other job listings, view/applicant
  counts, ads, navigation, cookie notices, "posted N days ago". A raw-text diff
  was tried first and abandoned: real boards (Finn) rewrite enough boilerplate
  between fetches that every revisit read as a change. A failed/unparseable call
  returns `unchanged` — an unreliable judgement must never trash a job.
- **Still open, changed.** Refresh the stored raw text and re-run summary +
  scoring + fit — keeping the job's current status and note.
- **Still open, unchanged.** Nothing moves (pure liveness check).
- **No stored summary** (an unprocessed/error job): the check is skipped; a
  reachable page with content counts as still there.
- **Slack-sourced jobs are excluded.** Their URL is a Slack permalink that can't
  be re-fetched, so a revisit would always "fail". The sweep skips them and the
  per-job button is hidden for them.
- **Eligible statuses:** New, Leads, Accepted, Rejected (i.e. `status IN
  ('new','accepted','rejected')` — Leads and "Not relevant" are both `new`).
  Trash is never revisited.

### Three entry points

1. **Sweep** — "Revisit open jobs" button on `/fetch`, next to "Fetch all".
   Walks every eligible non-Slack job (hard cap 200/run, logged if hit).
2. **Per-job** — "Revisit" button in a job's *Advanced…* section, same progress
   pattern as "Reset to new". Hidden for Slack-sourced jobs.
3. **On decision** — accepting or rejecting a job enqueues a revisit for it, to
   confirm the opening is still live at the moment you commit.

### Closure note

Appended to `feedback_note` (existing note kept above it, blank line between):

```
[2026-09-01] Moved to trash on revisit — this job can no longer be found.
```

Reason text by failure:

| Failure | Note text |
|---|---|
| HTTP 404 / 410 | this job can no longer be found |
| other HTTP / network error | this job posting could no longer be reached |
| no extractable content | this job posting no longer shows any content |
| LLM check → `gone` | this job posting is no longer available |
| LLM check → `closed` | this job is no longer accepting applications |

### Surfacing the decision-time race

A revisit triggered by **accept/reject** that closes the job raises an inbox item
(kind `job_closed`), since silently trashing something the user just accepted is
a bad surprise:

- one job closed → item links to it: *"'Senior PM at Acme' was moved to trash —
  this job can no longer be found, checked just after you accepted it."*
- several (bulk decision) → one item linking to the Trash view with a count.

The sweep and the per-job button don't raise inbox items — a closure there is the
expected outcome and shows in the task log / returned notice.

## Implementation

### 1. `run_revisit_job` + `_evaluate_posting` (`app/pipeline.py`)

Extract the `job_posting`/`lead` branch of `_ingest_posting` (summarize → score →
fit → `mark_job_evaluation_complete`) into:

```python
def _evaluate_posting(conn, client, model, job_id, *, simplified, content_type,
                      fallback_title, is_slack, profile, scenarios, url,
                      progress_prefix="", preserve_existing_metadata=False) -> Generator[str, None, None]
```

`_ingest_posting` calls it for the posting branch (behaviour unchanged). It does
**not** touch `status`.

```python
def run_revisit_job(conn, client, model, job, scenarios, profile) -> Generator[str, None, RevisitOutcome]
```

`RevisitOutcome` dataclass: `verdict` (`"closed"｜"changed"｜"unchanged"｜"skipped"`),
`reason` (plain text or `None`).

Steps:

1. `source = q.get_source(...)`; if `source["fetcher_type"] == "slack"` →
   return `skipped`.
2. `try: html = fetch_url_html(job["url"])` — on `FetchError`, `time.sleep(3)`,
   retry once. Still `FetchError` → `closed`. Reason: 404/410 in the message →
   "this job can no longer be found", else "this job posting could no longer be
   reached".
3. `text = extract_text(html)`; if `not has_enough_text(text)` → try
   `render_html(url)` (Playwright), re-check. Still thin → `closed` /
   "no longer shows any content".
4. `simplified = simplify(text)`. `reference = job["summary"] or job["simplified_content"]`.
   If `reference` is empty → return `unchanged` (nothing on file to compare).
5. `state, _ = revisit_check(client, model, simplified, reference)` — new module
   `app/ai/revisit_check.py`, one temperature-0 call, returns
   `gone｜closed｜unchanged｜changed` (unknown label / exception → `unchanged`).
   - `gone` → `closed` / "this job posting is no longer available".
   - `closed` → `closed` / "this job is no longer accepting applications".
   - `changed` → `q.update_job_raw_text(...)`, then `yield from _evaluate_posting(...,
     content_type=job["content_type"] if in ('job_posting','lead') else 'job_posting',
     preserve_existing_metadata=True)`; return `changed`.
   - `unchanged` → return `unchanged` (touch nothing).

On `closed`: `q.mark_job_closed(conn, job["id"], reason)` and return.

### 1a. `revisit_check` (`app/ai/revisit_check.py`)

`revisit_check(client, model, page_text, known_summary) -> tuple[str, str]`.
System prompt: given (A) our stored summary and (B) the live page text, return
`{"state": "<gone｜closed｜unchanged｜changed>", "reason": "<one sentence>"}`,
explicitly ignoring unrelated page content (other listings, view counts, ads,
nav, "posted N days ago"). `temperature=0`, `max_tokens=120`,
`enable_thinking=False`. Any failure or unknown label → `("unchanged", detail)`.

### 2. Queries (`app/db/queries.py`)

- `update_job_raw_text(conn, job_id, raw_text)` — one-column update + commit.
- `get_revisitable_jobs(conn) -> list[dict]`:
  ```sql
  SELECT jobs.* FROM jobs JOIN sources ON sources.id = jobs.source_id
  WHERE jobs.status IN ('new','accepted','rejected')
    AND sources.fetcher_type != 'slack'
  ORDER BY jobs.id
  ```
- `mark_job_closed(conn, job_id, reason)`:
  - read current `feedback_note`; `line = f"[{date.today()}] Moved to trash on
    revisit — {reason}."`; new note = `line` if empty else `f"{old}\n\n{line}"`.
  - `UPDATE jobs SET status='trash', feedback_note=?, feedback_handled_at=datetime('now'),
    status_changed_at=datetime('now') WHERE id=?` (handled_at set so it isn't
    treated as fresh feedback for profile refinement).

No schema change.

### 3. Task kind + routes (`app/routes/jobs.py`)

```python
@register_task_kind("jobs_revisit")
def _task_jobs_revisit(conn, client, model, config, params):
    job_ids = params.get("job_ids")
    trigger = params.get("trigger", "sweep")
    jobs = ([q.get_job(conn, i) for i in job_ids] if job_ids
            else q.get_revisitable_jobs(conn)[:200])
    ...
```

- Iterate, `yield from run_revisit_job(...)` per job, collecting `(job, outcome)`.
  ~1s pause between jobs on the sweep path.
- `trigger == "status_change"` and ≥1 closed → `q.create_inbox_item(conn,
  "job_closed", <message>, <link>)` (single-job link vs Trash view + count).
- Return `html_chunks` = updated row(s) + `_counts_oob` (per-job / small-set
  path), and `notices` summarising the outcome
  ("Checked just now — still open." / "Moved to Trash — this job can no longer be
  found." / "Content changed since we saved it — re-scored.").
- Sweep path returns a plain summary line (`"Revisited N · closed M · changed K"`);
  no row HTML (too many).

Routes:

```python
@router.post("/jobs/{job_id}/revisit")   # -> {"task_id", "already_active"}, kind jobs_revisit, params {"job_ids":[id],"trigger":"manual", **filter}
```

In `job_feedback`: after `q.update_job_feedback`, if `status in ("accepted","rejected")`
and the job is revisitable, `q.enqueue_task(conn, "jobs_revisit",
{"job_ids":[job_id], "trigger":"status_change"})`.

In `job_bulk_feedback`: same, one task with all affected revisitable ids.

### 4. Sweep trigger (`app/routes/fetch.py`)

```python
@router.post("/revisit/all")   # -> {"task_id","already_active"} or {"skipped","message"}
def trigger_revisit_all(conn = Depends(get_db)):
    if not q.get_revisitable_jobs(conn):
        return {"skipped": True, "message": "No jobs to revisit."}
    task = q.enqueue_task(conn, kind="jobs_revisit", params={})
    ...
```

### 5. Templates

- **`fetch/panel.html`** — `<button class="btn" data-progress-url="/revisit/all">Revisit
  open jobs</button>` beside "Fetch all", with a one-line hint.
- **`jobs/_feedback.html`** — in `<details class="job-advanced">`, when
  `job.source_fetcher_type != "slack"`:
  ```html
  <button type="button" class="btn"
    data-progress-url="/jobs/{{ job.id }}/revisit{{ macros.qsuffix(...) }}"
    data-progress-oob="1" data-progress-display="#reset-progress-{{ job.id }}"
    title="Re-fetch the original posting; if it's gone, move this job to Trash.">
    Revisit
  </button>
  ```
  (`get_job` must expose `source_fetcher_type` — add a join/column if not already
  present.)
- **`jobs/_revisit_notice.html`** — small partial for the per-job returned notice.

## Testing

- **`test_revisit_check.py`** — label parsing; unknown label → `unchanged`;
  API error / unparseable → `unchanged`; fence stripping; token/thinking caps.
- **`test_revisit.py`**:
  - HTTP error → retried once → `closed`; transient error that clears on retry →
    not closed.
  - 200 with thin body, Playwright also thin → `closed`.
  - `revisit_check` → `gone` → `closed`; → `closed` → `closed`.
  - → `unchanged` → job untouched (status, `raw_text`, scores all unchanged).
  - → `changed` → summary/scores updated, `raw_text` refreshed, `status` preserved.
  - no stored summary → `revisit_check` skipped, `unchanged`.
  - Slack source → `skipped`, job untouched.
- **`test_queries.py`** — `mark_job_closed` appends below an existing note and
  preserves it; sets `status='trash'` + timestamps.
- **`test_routes_jobs.py`**:
  - `POST /jobs/{id}/revisit` enqueues `jobs_revisit`; task closes a dead job and
    the row re-renders into Trash with the note.
  - accepting a job enqueues a `status_change` revisit; a closure from it creates
    a `job_closed` inbox item.
  - bulk accept → single revisit task over the affected ids.
- **fetch panel** — `POST /revisit/all` enqueues the sweep; returns `skipped`
  when nothing is eligible.
- **`test_queries.py`** — `get_revisitable_jobs` excludes Slack sources and Trash.
