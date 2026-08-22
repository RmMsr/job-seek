# Async task architecture

## Problem

Every long-running operation (fetch, bulk re-evaluate, single-job
reprocess, source detection, profile/scenario refinement — 15+ call
sites) uses the same pattern: a button click does `fetch()` against a
`StreamingResponse` backed by a synchronous generator, and a shared JS
helper in `base.html` reads the stream chunk-by-chunk to show
elapsed-time-plus-last-line progress, then swaps in HTML fragments the
stream sends back. The operation's entire lifetime — DB connection
included — is scoped to that one HTTP request/response. If the tab
closes, navigates away, or the connection drops, the operation's fate is
undefined, there's no way to check progress from anywhere else, and no
way to come back later and see what happened. Operations that end with
something for the user to decide (a detected source pending
confirmation, a fetch that hit an auth error) have no durable trace of
that either — miss it in the moment and it's gone.

## Scope

Replaces the shared `data-progress-*` mechanism everywhere it's used —
every current `StreamingResponse` endpoint moves to the async model:

- `fetch.py`: `POST /fetch/all`, `POST /fetch/{source_id}`
- `jobs.py`: `POST /jobs/bulk-reevaluate`, `POST /jobs/bulk-reset`,
  `POST /jobs/{job_id}/reset`, `POST /jobs/{job_id}/pass-as-new`,
  `POST /jobs/{job_id}/reevaluate`, `POST /jobs/add-by-url`,
  `POST /jobs/add-listing-source`
- `scenarios.py`: `POST /scenarios/reevaluate`, `POST /scenarios/refine`,
  `POST /scenarios/{id}/refine`
- `sources.py`: `POST /sources/detect`, `POST /sources/detect/confirm`
- `profile.py`: `POST /profile/refine`

`app/pipeline.py`'s generator functions (`run_fetch`,
`run_reprocess_job`, etc.) are unchanged; only how they're invoked and
how their progress reaches the browser changes.

Out of scope for this pass:
- Cancelling a queued/running task.
- Pruning old `tasks` rows.
- Other inbox producers (stale-proposal reminders, follow-up-on-application
  nudges, onboarding checklist) — the inbox schema is deliberately
  general-purpose so these are cheap to add later, but only
  task-completion feeds it in this pass.

## Design

### Data model

`tasks` table (new):
- `id`, `kind` (e.g. `fetch_source`, `fetch_all`, `bulk_reevaluate`,
  `job_reset`, `scenario_refine`, ...), `params` (JSON — source_id,
  job_ids, filter context, the button's `target`/`display` selectors —
  whatever the route needs to re-run and re-render), `status`
  (`queued` / `running` / `done` / `failed`), `log` (progress lines
  appended as the generator yields), `result` (JSON — whatever the
  pipeline function returned), `error`, `created_at`, `started_at`,
  `finished_at`.

`inbox_items` table (new, general-purpose): `id`, `kind` (free-form —
`task_followup` is the only producer for now), `message`, `link`,
`created_at`, `resolved_at` (NULL = open). Nothing in this schema is
task-specific; a completed task that needs a decision (source detected,
listing mismatch, fetch auth error) is just today's one producer.

### Task engine

One background thread, started at app startup, consuming a `queue.Queue`
of task ids (serialized — one task executes at a time; others wait).
For each task the worker opens its own sqlite connection (not
request-scoped, mirrors `deps.get_db`'s connection setup), calls the
existing pipeline generator, and on each yielded string appends it to
`log` and sets `status='running'`. `StopIteration.value` becomes
`result`; an uncaught exception becomes `error` + `status='failed'`.
This is the only place touching the generators — `pipeline.py` needs no
changes.

**Debounce.** Before inserting a new `tasks` row, look for an existing
row with the same `kind` + same `params` and `status IN ('queued',
'running')`. If found, don't insert — point the click at the existing
task instead. This is exact-match dedup; it won't catch overlapping-but
-not-identical cases (e.g. "fetch source 3" clicked while "fetch all"
already covers source 3) — those just run back-to-back via the
serialized queue, which is fine.

**Concurrent DB access.** Switch sqlite to WAL journal mode — readers no
longer block behind the worker's long-running writer, avoiding
"database is locked" errors while a fetch runs and you browse other
pages.

### Route layer

Every `data-progress-*` handler shrinks to: validate input → enqueue (or
find-duplicate) → return `{task_id}`. The actual work moves entirely
into the worker calling the same pipeline function the route used to
call inline. No route handler streams a response anymore.

### Frontend

Replace the `fetch()` + `getReader()` stream-pump in `base.html` with:

- **On click:** POST to enqueue, get `task_id`, hand it to the tray.
- **Tray** (persistent, in page chrome): polls `GET /tasks/active` every
  ~2s, listing queued/running tasks with elapsed time + last log line —
  same look as today's inline progress span, just polled instead of
  streamed. A `visibilitychange` listener fires an immediate poll when a
  backgrounded tab regains focus, so status is fresh the moment you look
  back, without maintaining any connection while backgrounded.
- **Auto-refresh while watching:** each task's `params` carries the
  original `target`/`display` selector info. While polling, if the
  current page still contains that element, the tray applies the same
  HTML swap it does today once the task hits `done` (via a plain
  follow-up GET that re-renders the fragment — not HTML streamed back
  from the operation itself). Stay on the page and it behaves exactly
  like today; navigate away and it just finishes in the background.
- **Inbox** badge next to the tray showing the count of unresolved
  `inbox_items`; click opens a panel/page listing them with their
  `link`.

The `HTML:`/`NOTICE:`-prefixed-line stream protocol is retired entirely
— the worker writes plain log lines to `task.log`, and the one place
that still needs an HTML fragment (auto-refresh) fetches it fresh
rather than having it streamed inline.

**Why polling, not long-polling or WebSockets:** short polling is
stateless per request — a dropped poll on a flaky mobile connection just
means "try again in 2s," no connection state to recover. Long polling
and WebSockets both depend on a connection surviving over time, which is
where flaky connections hurt more, not less. Sub-2-second latency
doesn't matter for operations that already run seconds-to-minutes.

### Startup recovery

On app start, the worker reloads any `status='queued'` rows into the
in-memory queue (safe — they never started). Any row still
`status='running'` from a previous process (crashed, or replaced by
`--reload`) is marked `failed` with `error='interrupted by restart'` —
not auto-resumed, since there's no way to know how far a partially-run
fetch/reprocess got or whether resuming mid-way would duplicate writes.
Re-triggering is just clicking the button again.

## Testing

- Task engine: enqueue → worker picks it up → `status` transitions
  `queued` → `running` → `done`/`failed`; `log` accumulates yielded
  lines; `result`/`error` populated correctly for success/exception.
- Debounce: enqueuing the same `kind`+`params` while one is
  `queued`/`running` returns the existing `task_id`, doesn't insert a
  second row.
- Startup recovery: a `running` row present at worker startup is marked
  `failed` with the interrupted-restart error; a `queued` row is picked
  up and executed.
- Route layer: each converted endpoint returns a `task_id` immediately
  and does not block on the operation; existing route-level tests are
  updated to enqueue-and-poll instead of asserting stream contents.
- Inbox: a task result marked as needing follow-up produces exactly one
  `inbox_items` row with the expected `message`/`link`; resolving it
  sets `resolved_at`.

## Addendum: UX refinements from first live use

After the initial build, live use against real data surfaced usability
issues the spec hadn't anticipated. Changes made in response:

- **No more per-button live counter.** The "Ns — last line" text next to
  a clicked button is gone. A single ephemeral notice ("`<label>` —
  running in the background…") fires on enqueue instead; completion
  still applies the same HTML-swap/notice behavior silently.
- **The always-on tray is replaced by a floating status bar** (fixed to
  the viewport bottom, outside `<main>`) showing only the current
  task's kind, `[current/total]` + percent when the log's last line
  carries a `[x/y]` marker (parsed server-side via regex — no change to
  how task-kind functions yield progress text), a link to that task's
  full log, and a count of additional queued tasks.
- **New `/tasks/{id}/log` page** (no nav entry — linked only from the
  status bar) shows one task's log lines top to bottom, auto-refreshing
  while the task is active.
- **The inbox moved onto the Start page** as a "Tasks" section (no more
  standalone `/inbox` page or nav link) and gained `inbox_items.task_id`
  so `/tasks/{id}/resume` can look up its own inbox item. Resolution
  happens automatically when the resume page's own action-button task
  completes (client-side, once `pollWatched` sees success) — not via a
  server-side "is this actually done" check. Resolved items show
  struck-through, capped at 5 most-recently-resolved within 24 hours
  (`get_recent_resolved_inbox_items`).
