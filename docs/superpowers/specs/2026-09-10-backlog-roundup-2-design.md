# Backlog Roundup 2 — Design

Ten backlog items, low + medium effort, batched into one worktree. Excludes
`generic_listing` pagination (#10) and the MCP server (#11) — both need their own spec.

**Stack:** server-rendered FastAPI + Jinja + htmx, `sqlite3` stdlib, pytest + `TestClient`.
No DB migrations. One new optional config section (`[tracing]`). Three new dependencies
(tracing only; declared unconditionally, imported lazily).

Backlog item numbers below refer to `BACKLOG.md` at the time of writing.

---

## A. Low-effort fixes

### A1 — Job note save button (#1)

Today a job's `feedback_note` only persists as a side effect of Accept / Reject / Trash
(`_feedback.html` textarea `name="note"`, read by `POST /jobs/{id}/feedback`). There is no
way to save a note without also moving the job.

- New route `POST /jobs/{job_id}/note` — `note: str | None = Form(None)`, calls a new
  `q.set_job_note(conn, job_id, note)` which writes `feedback_note` and sets
  `feedback_handled_at = NULL` (so the profile-refine loop still picks it up, matching
  `update_job_feedback`). Returns the re-rendered job card (`_render_updated_job_html`),
  same swap target as the feedback form.
- `_feedback.html` — a **Save note** button (`type="submit"`, `formaction` pointing at the
  new route, or a separate small form) next to the textarea. Sits with the note field, not
  in the Organize action group.
- No `job_events` entry (decided: quiet save).
- Detail page: same button, honouring the existing `redirect` hidden field pattern.
- Tests: `test_routes_jobs.py` — save a note, assert `feedback_note` set and status
  unchanged; save empty, assert cleared.

### A2 — Status history should not echo the note text (#2)

`queries.py:update_job_feedback` builds `Status: old → new — "the note text"`. Drop the
`— "..."` suffix; the event message becomes just `Status: old → new`. The note itself is
still stored on the job and shown in the feedback textarea.

- One change in `update_job_feedback`, remove the `if isinstance(note, str) and note.strip()`
  block that appends to `message`.
- Tests: `test_queries.py` — update with a note, assert the `job_events` row is exactly
  `Status: new → accepted`.

### A3 — `/fetch` action buttons inline with the title (#4)

`fetch/panel.html` renders `<h1>Fetch</h1>` then a separate `<div>` with the "Fetch all" /
"Refresh open jobs" buttons. Jobs and Sources pages already use a `.page-head` flex row
(h1 + controls on one line).

- Wrap the `<h1>` and the button `<div>` in `<div class="page-head">`.
- If `.page-head` alignment needs a tweak for a bare button group (vs. the add-panel it
  was built for), add a minimal rule in `base.html`. No new class if avoidable.
- Tests: none (pure layout). Existing `test_routes_*` smoke coverage of `/fetch` stays green.

### A4 — Finished task shows duration first, timestamp second (#5)

`tasks/detail.html:13` shows `Finished {{ task.finished_at | age }}` ("5m ago"). Want the
elapsed run time to lead.

- New filter `duration(start, end)` in `app/dates.py`: given two ISO-8601 strings returns
  a compact span — `"8s"`, `"2m 30s"`, `"1h 4m"`. Returns `""` if either side missing.
  Registered in `template_env.py`.
- `tasks/detail.html` — for terminal states, render
  `Finished in {{ task.started_at | duration(task.finished_at) }} · {{ task.finished_at | age }}`.
  `needs_action` keeps its current "Needs you since …" wording.
- `tasks/list.html` and any task-card partial that shows a finished time get the same
  treatment where a finished task is rendered.
- Tests: `test_dates.py` (or wherever `age` is tested) — `duration` boundary cases;
  `test_routes_tasks.py` — a finished task detail response contains "Finished in".

### A5 — Drop the "Open" keyword (#7a)

- `routes/tasks.py:_results()` labels: `"Open Scenarios"` → `"Scenarios"`,
  `"Open the CV workbench"` → `"CV workbench"`, `"Open the CV for {title}"` →
  `"CV for {title}"`, `"Open the CV for …"` fallbacks likewise.
- `tasks/detail.html:31` — the `awaiting` branch's `<a class="btn btn-primary">Open</a>`
  becomes `Review` (it links to the task's own action page).
- Tests: `test_routes_tasks.py` — update the label assertions that reference "Open".

---

## B. Medium UX items

### B1 — Multi-select while a text search is active (#3)

`_content.html` and `_row.html` currently hide job checkboxes, the bulk bar, and the
"Select all" control whenever `filter.searching` is true. Search results legitimately span
several status tabs, and a bulk status change across that set is meaningful. The real
blocker is that the bulk routes re-render from `bulk-form` hidden fields that omit `q`, so
the search context would be lost after the action.

Changes:

- `_content.html` — add `<input type="hidden" name="q_filter" value="{{ filter.q }}"
  form="bulk-form">` next to the existing `*_filter` hidden inputs.
- `routes/jobs.py:_filter_from_bulk_form()` — accept `q_filter`, include it as `q` in the
  params dict passed to `JobFilter.from_params`.
- The three routes that call `_filter_from_bulk_form` — `POST /jobs/bulk-feedback`,
  `POST /jobs/bulk-reset` + `bulk-reevaluate` (~line 697), `POST /jobs/bulk-actions`
  (~line 712) — add `q_filter: str | None = Form(None)` and thread it through.
- Remove the three `{% if not filter.searching %}` guards: checkboxes in `_row.html`,
  the bulk bar and the `.filter-tools` select-all block in `_content.html`.
- `_jobs_for_filter` already branches on `f.searching`, so the post-action re-render
  returns the filtered search view, including the existing stale-badge merge for rows
  that dropped out of the result set.
- The select-all toggle logic in `base.html` is unchanged and already clears every
  `job_ids` box on a second click (unchecking the header box) — add a regression test
  for that in search mode.

Accepted behaviour: "Select all" while searching selects every visible row across the
seeded tabs (New / Lead / Accepted / Rejected plus any explicit ones) — consistent with
how the search result list already renders.

- Tests: `test_routes_jobs.py` — select two jobs from different tabs during a search,
  bulk-reject, assert both updated and the response is still the filtered search view;
  a test asserting a second select-all click clears the selection.

### B2 — Task results link to changed items and state the change (#7b / #9)

Scope: `jobs_revisit` (multi-job form and the `revisit/all` sweep), `jobs_bulk_reset`,
`jobs_bulk_reevaluate`.

- `_task_jobs_revisit` already computes `closed` and `changed` lists. In the multi-job
  branch, persist ids into the result:
  `result["outcome"] = {"total": N, "changed": [job_ids], "closed": [job_ids]}`.
- `routes/tasks.py:_results()` — for a `jobs_revisit` task carrying `outcome`, render:
  - a summary line, e.g. `12 rechecked · 2 moved to Trash · 1 updated · 9 unchanged`
    (omit zero clauses);
  - one link per changed and per closed job (title via `_job_title`), capped at ~10;
  - a trailing `View all rechecked jobs` link when there are more, or always as a
    fallback for the unchanged remainder.
- `jobs_bulk_reset` / `jobs_bulk_reevaluate` — replace the flat `Back to jobs` link with
  `{n} jobs re-evaluated` / `{n} jobs reset` plus individual job-title links from
  `params["job_ids"]` (same ~10 cap + overflow link to `/jobs`).
- Log lines: the per-job progress strings in `run_revisit_job` / `run_reprocess_job` and
  the multi-job loop in `_task_jobs_revisit` gain a `job {id}:` marker —
  `[3/12] job 47: …`. Single-job tasks get a leading `job {id}: …` on their first line.
- Tests: `test_routes_tasks.py` — a 3-job revisit where 1 closes: assert the result view
  shows the summary and links the closed job; assert a log line contains `job <id>:`.

---

## C. Infrastructure

### C1 — An unreachable inference endpoint counts as an error (#8)

About a dozen functions in `app/ai/*` wrap **both** `client.chat.completions.create(...)`
and the JSON parsing in a single `try / except Exception`, returning a degraded result
(zero scores, empty plan, `error: <exc>` reasoning) on any failure. A dead, timing-out, or
401 endpoint therefore yields a task that completes "successfully" with empty data.

- New `app/ai/_client.py`:

  ```python
  def complete(client, model, messages, **kwargs) -> str:
      """Run a chat completion and return the message content.
      Transport / auth / server errors propagate; callers keep their own
      try/except only around parsing the returned text."""
      resp = client.chat.completions.create(model=model, messages=messages, **kwargs)
      return resp.choices[0].message.content or ""
  ```

  It deliberately does not catch `openai.APIConnectionError`, `APITimeoutError`,
  `AuthenticationError`, `RateLimitError`, `APIStatusError` (covers 5xx) — they propagate.

- Each call site in `app/ai/*` (`assess_fit`, `classify`, `detect_listing`, `evaluate`,
  `generate_source_name`, `refine`, `refine_profile`, `revisit_check`, `summarize`,
  `tailor_cv` ×3): move the `create()` call to `complete(...)` **above** the `try`; keep
  only `extract_json` / `json.loads` / field coercion inside `try / except`. A model that
  returns prose instead of JSON still degrades gracefully — that resilience is retained
  intentionally.
- `execute_task`'s existing `except Exception` (`task_engine.py:102`) then marks the task
  `failed` with `str(exc)`, which the task UI already surfaces.
- Behaviour change to call out: `revisit_check` and `detect_listing` currently swallow
  everything (a revisit against a dead endpoint silently returns "unchanged"; source
  detection silently falls back to `generic_listing`). After this change a transport / API
  failure raises and fails the task instead. Accepted tradeoff: a transient blip mid-sweep
  aborts the rest of that sweep — but `mark_job_revisited` is stamped before the outcome,
  so the capped sweep rotates past it next run, and a failed task is visible and
  re-runnable. For a single-instance personal app this is better than a run of 200 bogus
  "unchanged" verdicts against a down endpoint.
- `routes/setup.py`'s "test connection" path is unaffected — it already reports errors.
- Tests: `test_ai_client.py` (new) — `complete` propagates a mocked `APIConnectionError`;
  representative call-site tests (`assess_fit`, `plan_tailoring`) — connection error
  raises; malformed-JSON response still returns the degraded shape. `respx` for mocking.

### C2 — LLM call tracing to Phoenix, config-toggled (#6)

- New optional `config.toml` section:

  ```toml
  [tracing]
  endpoint = "http://localhost:6006/v1/traces"   # OTLP-HTTP collector; omit to disable
  project_name = "job-seek"                        # optional
  ```

  Absent or empty `endpoint` ⇒ tracing fully off, tracing packages never imported.

- `Config` gains `tracing_endpoint: str | None` and `tracing_project: str`
  (default `"job-seek"`); `load_config` reads `[tracing]`.
- `config.py:write_config()` currently rewrites the whole file from scratch — extend it to
  carry forward an existing `[tracing]` section (the same way it already preserves
  `[database]` and `[browser]`), so saving a provider in the setup UI does not wipe it.
- New `app/tracing.py` with `init_tracing(config)`:
  - no-op (and no imports) when `config.tracing_endpoint` is falsy;
  - otherwise builds an OTel `TracerProvider` with a batching OTLP-HTTP span exporter
    pointed at `tracing_endpoint`, resource `service.name = tracing_project`, and calls
    `OpenAIInstrumentor().instrument()` from `openinference-instrumentation-openai`.
  - idempotent — a module-level `_initialized` guard so the second caller in a process
    is a no-op.
- Called once from `app/main.py` lifespan startup and once from
  `task_engine.run_worker_forever` (separate process/thread contexts, each with its own
  `openai.OpenAI`; the instrumentor patches the class, so one init per process suffices).
- New dependencies in `pyproject.toml` (declared always, imported only when enabled):
  `openinference-instrumentation-openai`, `opentelemetry-sdk`,
  `opentelemetry-exporter-otlp-proto-http`.
- Scope: LLM client spans only. No FastAPI / httpx auto-instrumentation.
- Tests: `test_tracing.py` — `init_tracing` with no endpoint imports nothing and does
  nothing; with a fake endpoint the instrumentor is invoked (patch
  `OpenAIInstrumentor`). Keep it minimal.
- `config-template.toml` / `config-container-template.toml` gain a commented `[tracing]`
  stub.

### C3 — Supported job boards doc (#12)

- New `docs/job-boards.md` — a table with columns **Board / Site**, **Fetcher**,
  **Example URL**, **Notes**, covering what `app/fetchers/` actually handles: finn.no,
  eawork / 80,000 Hours, `generic_listing` (incl. Sopra Steria & Tieto with the known
  10-posting page-1 cap), Slack (cookie-only), the Playwright detail-fetch fallback, and
  the LinkedIn URL rewrite. Content derived from the fetcher modules and existing specs.
- `README.md` gets a one-line pointer to it.
- No code, no tests.

---

## Sequencing

A1–A5 and C3 are independent and can land in any order. B1, B2, C1, C2 are independent of
each other and of A/C3. C2 depends on nothing but pairs naturally right after C1 (both
touch the LLM path). Suggested order: A2, A3, A4, A5, A1, B1, B2, C1, C2, C3 — cheapest
and least risky first. Commit after each item.
