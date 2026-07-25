# Pipeline Progress Visibility — Design Spec

**Date:** 2026-07-25
**Status:** Approved

## Overview

Fetch, re-evaluate, and refine-criteria all loop over jobs making LLM calls and can run for a while with zero visibility: the browser just waits on a blocking POST, and the server produces no output until the whole thing finishes. This closes that gap in two ways:

1. **Backend logging** — every pipeline step is logged via Python's `logging` module, visible in the console the app runs in, regardless of the browser.
2. **Streamed progress in the browser** — the same three long-running actions stream their progress lines back over the existing POST request. If an action is still running after 5 seconds, the UI shows a small live panel (elapsed-seconds counter + latest status line); if it finishes sooner, nothing extra ever appears.

This was called for in the original design (`docs/superpowers/specs/2026-07-11-job-seek-design.md`, Fetch Panel section: "Progress streams back via server-sent events") but was never built. This spec supersedes that detail with a simpler mechanism (see "Why not SSE" below) while keeping the same user-facing goal.

---

## Backend logging

A single logger, `logging.getLogger("job_seek")`, configured once in `app/main.py`:

```python
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
```

Log calls (`logger.info(...)`) are added at each meaningful step in `app/pipeline.py` and the two `app/routes/scenarios.py` handlers that loop over jobs:

- Fetch: source started, fetcher type, raw postings found, per-job (new vs. duplicate skipped, simplified, classified as X, summarized, scored X), run completed (found/new counts), any exception.
- Re-evaluate: scenario + job count, per-job re-scored, run completed.
- Refine criteria: request sent, proposals received (count), or failure.

These are the *same strings* used for the streamed progress lines (see below) — one log call site produces both the console log line and the line sent to the browser, so they can never drift apart.

---

## Streaming mechanism

### Why not SSE
True Server-Sent Events require a `GET` (per the `EventSource` spec) or hand-rolled SSE framing over a kept-open connection, plus a client-side event parser. Since these actions are state-changing (they write jobs/criteria to the database), keeping them as `POST` matters. A `POST` response body can already be streamed incrementally and read incrementally client-side via the Fetch API's `ReadableStream` — no SSE framing, no new library, no background task or polling endpoint. This keeps every route a plain synchronous request/response; only how the body is produced and consumed changes.

### Backend
`run_fetch` (in `app/pipeline.py`) and the bodies of `reevaluate_jobs` and `refine_criteria` (in `app/routes/scenarios.py`) become generator functions that `yield` short human-readable progress strings (and log each one) as they go, instead of doing all the work and returning once at the end. Their side effects (DB writes, `q.complete_fetch_run`, etc.) stay exactly where they are today — only the "report progress" behavior is added.

Each route wraps its generator in a `fastapi.responses.StreamingResponse` with `media_type="text/plain"`, yielding each progress line followed by a newline. The final yielded chunk is always the real HTML this route already produces today:

- Fetch: the full `fetch/panel.html` page (unchanged from today's response) — client swaps it into `document.body`, mirroring the button's current `hx-target="body" hx-swap="innerHTML"`.
- Re-evaluate jobs: the full `scenarios/index.html` page (unchanged from today's response). This button is currently a plain `<form>` (full page reload on submit) rather than HTMX — it's converted to use the same JS helper, also targeting `document.body`, since a plain form submit can't be read incrementally.
- Refine criteria: the `_proposals.html` fragment (unchanged from today's response) — client swaps it into `#proposals-area-{{ scenario_id }}`, matching the button's current `hx-target`.

The client always treats the last chunk specially (see below), so no template restructuring is needed — each route still renders the exact same final HTML it does today, just preceded by progress lines.

### Frontend
A single small vanilla-JS helper (no library, no build step — consistent with the project's existing "HTMX + no JS build step" approach) is added to `base.html` and reused by all three buttons (Fetch, Re-evaluate jobs, Refine criteria from feedback). Since none of htmx's own attribute engine can read an incrementally-streamed body, these three triggers stop using `hx-post`/`hx-target` and instead carry two plain `data-*` attributes the helper reads directly: `data-progress-url` (where to POST) and `data-progress-target` (a CSS selector: `body` for Fetch/Re-evaluate, `#proposals-area-{{ scenario_id }}` for Refine — same targets as today's `hx-target` values).

On click, the helper:

1. Issues the POST itself (`fetch(url, {method: "POST", body: ...})`).
2. Starts a 5-second timer.
3. Reads the response body incrementally via `response.body.getReader()`, decoding chunks and splitting on newlines.
4. If the 5-second timer fires before the stream ends, reveals a small progress element (inserted next to the button) showing elapsed seconds (ticking up every second) and the most recently received line.
5. Buffers the final chunk (the real HTML) separately from the plain-text progress lines.
6. On stream end, sets `document.querySelector(target).innerHTML` (or replaces `document.body` outright for `body`) to the buffered HTML, then removes the progress element and clears the timer.

**Framing convention:** each yielded chunk is either a progress line (plain text, human-readable, ends in `\n`) or is prefixed with a `HTML:` marker followed by the final fragment (no trailing newline, always the last chunk). This is a private convention between these routes and this one script — not a general protocol.

### Removed
The Fetch page's "last fetch result" banner (`last_result` context variable, currently populated only immediately after a synchronous POST) is dropped. The same information (found/new counts, error) is already visible per-source in the existing run-history table columns ("Last run", "New / Found"), which get refreshed as part of the final swap.

---

## Affected files

- `app/main.py` — logging configuration
- `app/pipeline.py` — `run_fetch` becomes a generator
- `app/routes/fetch.py` — `trigger_fetch` returns `StreamingResponse`
- `app/routes/scenarios.py` — `reevaluate_jobs` and `refine_criteria` become generators returning `StreamingResponse`
- `app/templates/base.html` — shared progress-streaming JS helper
- `app/templates/fetch/panel.html` — Fetch button wired to the helper; drop `last_result` banner
- `app/templates/scenarios/index.html` — Re-evaluate and Refine buttons wired to the helper

## Testing strategy

- `run_fetch` becomes directly testable as a generator: tests drive it with `list(run_fetch(...))` and assert on the sequence of yielded strings, plus the final DB state (unchanged from today's assertions).
- Route tests use `TestClient`, which fully drains `StreamingResponse` bodies into `resp.text` — existing assertions that check for specific substrings in the response continue to work unchanged. New assertions check that expected progress lines appear before the final `HTML:`-prefixed fragment.
- No test covers the client-side JS timer/DOM behavior — that's manual/browser verification only, per this project's existing testing strategy (UI interaction isn't unit tested elsewhere in the codebase either).

## Out of scope

- Cancelling an in-progress fetch/re-evaluate/refine from the UI.
- Progress visibility for actions that aren't job loops (e.g. saving the profile, activating a scenario) — those are already near-instant.
- Persisting progress across a page reload or server restart (progress is purely a live view of the current in-flight request; nothing is stored beyond what was already persisted today).
