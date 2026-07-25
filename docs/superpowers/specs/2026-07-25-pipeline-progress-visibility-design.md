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

Each route wraps its generator in a `fastapi.responses.StreamingResponse` with `media_type="text/plain"`, yielding each progress line followed by a newline. What happens once the stream ends differs by route, because swapping a full rendered page into `document.body.innerHTML` is unreliable — browsers don't parse a nested `<html>/<head>` consistently in that context — so full-page routes just trigger a plain reload instead of shipping a redundant copy of the page over the stream:

- Fetch and Re-evaluate jobs (both currently render/reload the *entire page*): the generator yields progress lines only. No final HTML is sent. When the client sees the stream end, it calls `location.reload()` — a fresh `GET`, exactly as if the user reloaded the page, and exactly what today's full-page swap/reload already amounts to.
- Refine criteria (renders a *fragment* into `#proposals-area-{{ scenario_id }}`, not the whole page): the generator yields progress lines, then one final chunk prefixed `HTML:` containing the rendered `_proposals.html` fragment (unchanged from today's response). The client swaps this into the target div's `innerHTML`, matching today's `hx-target`/`hx-swap="innerHTML"` behavior exactly.

No template restructuring is needed for the underlying page/fragment renders — Fetch and Re-evaluate simply stop sending their final render over the wire (the reload re-fetches it fresh); Refine's final render is unchanged.

### Frontend
A single small vanilla-JS helper (no library, no build step — consistent with the project's existing "HTMX + no JS build step" approach) is added to `base.html` and reused by all three buttons (Fetch, Re-evaluate jobs, Refine criteria from feedback). Since none of htmx's own attribute engine can read an incrementally-streamed body, these three triggers stop using `hx-post`/`hx-target` and instead carry plain `data-*` attributes the helper reads directly: `data-progress-url` (where to POST) on all three, plus `data-progress-target` (a CSS selector, `#proposals-area-{{ scenario_id }}`) on the Refine button only — its absence signals "reload the page when done."

On click, the helper:

1. Issues the POST itself (`fetch(url, {method: "POST"})`).
2. Starts a 5-second timer.
3. Reads the response body incrementally via `response.body.getReader()`, decoding chunks and splitting on newlines.
4. If the 5-second timer fires before the stream ends, reveals a small progress element (inserted next to the button) showing elapsed seconds (ticking up every second) and the most recently received line.
5. If a line is prefixed `HTML:`, buffers it (stripped of the prefix) as the final fragment instead of treating it as a status line.
6. On stream end: removes the progress element and clears the timer, then either sets `document.querySelector(data-progress-target).innerHTML` to the buffered fragment (if `data-progress-target` was present) or calls `location.reload()` (if it wasn't).

**Framing convention:** each yielded chunk is a line of plain text (human-readable, ends in `\n`); a line prefixed `HTML:` is the one exception, carrying the final fragment instead of a status message, and only ever appears last. This is a private convention between the Refine route and this one script — not a general protocol. Fetch and Re-evaluate never emit an `HTML:` line.

### Removed
The Fetch page's "last fetch result" banner (`last_result` context variable, currently populated only immediately after a synchronous POST) is dropped. The same information (found/new counts, error) is already visible per-source in the existing run-history table columns ("Last run", "New / Found"), which show fresh data once `location.reload()` fires.

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

- `run_fetch` becomes directly testable as a generator that both yields progress strings and (via a normal generator `return`) produces a final `FetchResult` retrievable from `StopIteration.value`. Tests drain it with a small helper and assert on the sequence of yielded strings, the returned `FetchResult`, and the final DB state (unchanged from today's assertions).
- Route tests use `TestClient`, which fully drains `StreamingResponse` bodies into `resp.text` — existing assertions that check for specific substrings in the response continue to work unchanged. For Refine, a new assertion checks the `HTML:`-prefixed fragment appears last. For Fetch and Re-evaluate, new assertions check that expected progress lines appear in `resp.text` and that no `HTML:` line is present.
- No test covers the client-side JS timer/DOM behavior — that's manual/browser verification only, per this project's existing testing strategy (UI interaction isn't unit tested elsewhere in the codebase either).

## Out of scope

- Cancelling an in-progress fetch/re-evaluate/refine from the UI.
- Progress visibility for actions that aren't job loops (e.g. saving the profile, activating a scenario) — those are already near-instant.
- Persisting progress across a page reload or server restart (progress is purely a live view of the current in-flight request; nothing is stored beyond what was already persisted today).
