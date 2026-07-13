# Job Seek — Design Spec

**Date:** 2026-07-11
**Status:** Approved

## Overview

A partially automated job research tool for a single user on Linux. Regularly fetches open job postings from a curated list of sources, evaluates them against a user profile and active scenario criteria using an LLM, and surfaces well-matching opportunities for triage in a browser UI. Feedback on reviewed jobs refines the scenario criteria over time.

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│  Browser UI (HTMX + server-rendered HTML)           │
├─────────────────────────────────────────────────────┤
│  FastAPI  — routes for UI pages + JSON API          │
├──────────────────┬──────────────────────────────────┤
│  Fetcher layer   │  Matching & AI layer             │
│  (Playwright +   │  (profile, scenarios, criteria,  │
│   parsers)       │   LLM calls)                     │
├──────────────────┴──────────────────────────────────┤
│  SQLite (single file, all persistent state)         │
└─────────────────────────────────────────────────────┘
```

Single Python process. No background scheduler — fetch runs are triggered manually from the UI. Configuration lives in `config.toml` (LLM endpoint URL, model name, browser profile path).

**Stack:**
- Python, FastAPI, HTMX
- SQLite (via Python's built-in `sqlite3`)
- Playwright (persistent browser context for auth-gated sources)
- `httpx` + `BeautifulSoup4` (lightweight HTTP sources)
- OpenAI-compatible chat completions endpoint (local LLM inference)

---

## Data Model

### `jobs`
One row per discovered job posting or lead.

| Field | Type | Notes |
|---|---|---|
| `id` | integer PK | |
| `source_id` | integer FK | references `sources` |
| `url` | text unique | used for deduplication |
| `title` | text | |
| `company` | text | |
| `raw_text` | text | full extracted content |
| `simplified_content` | text | cleaned markdown version of raw content |
| `summary` | text | LLM-generated summary |
| `relevance_score` | real | 0.0–1.0, LLM-generated |
| `score_reasoning` | text | LLM explanation of the score |
| `content_type` | text | `job_posting` \| `lead` \| `irrelevant` \| `error` |
| `scenario_id` | integer FK | scenario active at evaluation time |
| `fetched_at` | datetime | |
| `status` | text | `new` \| `accepted` \| `rejected` \| `invalid` |
| `feedback_note` | text | user-provided note on feedback |

### `sources`
Curated list of sources to fetch from. Editable from the UI.

| Field | Type | Notes |
|---|---|---|
| `id` | integer PK | |
| `name` | text | display name |
| `url` | text | entry point URL |
| `fetcher_type` | text | `http` \| `playwright` |
| `enabled` | boolean | |

### `profile`
Single-row table. Static description of skills, experience, interests, and constraints. Edited as free text (markdown) in the UI.

| Field | Type |
|---|---|
| `id` | integer PK |
| `content` | text |
| `updated_at` | datetime |

### `scenarios`
Named search contexts. Only one is active at a time.

| Field | Type | Notes |
|---|---|---|
| `id` | integer PK | |
| `name` | text | e.g. "remote senior ML", "local part-time filler" |
| `description` | text | narrative description for LLM context |
| `active` | boolean | only one active at a time |
| `created_at` | datetime | |

### `criteria`
Dynamic, feedback-driven rules attached to a scenario.

| Field | Type | Notes |
|---|---|---|
| `id` | integer PK | |
| `scenario_id` | integer FK | |
| `text` | text | natural-language criterion |
| `weight` | text | `must` \| `prefer` \| `avoid` |
| `source` | text | `manual` \| `feedback` |
| `created_at` | datetime | |

### `fetch_runs`
Log of each fetch execution.

| Field | Type | Notes |
|---|---|---|
| `id` | integer PK | |
| `source_id` | integer FK | |
| `started_at` | datetime | |
| `completed_at` | datetime | |
| `jobs_found` | integer | |
| `jobs_new` | integer | |
| `error` | text | null if successful |

---

## Fetcher Layer

All fetchers implement a common interface:

```python
class Fetcher:
    def fetch(self) -> list[RawJob]
```

`RawJob` is a dataclass: `url`, `title`, `company`, `raw_text`. Deduplication is by URL — jobs already in the database are skipped before returning.

### HttpFetcher
For public pages without heavy JS rendering. Uses `httpx` + `BeautifulSoup4`. Suitable for sites like finn.no.

### PlaywrightFetcher
For JS-heavy and login-gated sources. Runs against a persistent browser profile directory (`browser-profile/`, gitignored). On first use per site, launches a visible headed browser window for manual login. Once the user completes login and closes the auth tab, the session is saved. All subsequent runs are headless.

**Slack specifics:** Navigates to the channel URL, scrolls to load recent messages, extracts posts as raw text. Posts are classified by the AI layer; the classification prompt hints that Slack posts are likely leads unless they contain a full job description.

Adding a new source requires only a row in the `sources` table if it fits an existing fetcher type. Novel auth flows or pagination schemes get a new fetcher subclass.

---

## Pipeline

For each fetched item, the pipeline runs in sequence:

```
raw_text
  → simplified_content   (deterministic: strip HTML, normalize, extract main content)
  → content_type         (LLM call 1: classify)
  → [if job_posting or lead]
  → summary              (LLM call 2: summarize)
  → relevance_score      (LLM call 3: evaluate against profile + active scenario criteria)
```

Items classified as `irrelevant` or `error` are stored but not evaluated further.

Re-evaluation (when criteria change) re-runs LLM calls 2 and 3 on existing jobs and overwrites `summary`, `relevance_score`, `score_reasoning`, and `scenario_id`. Re-evaluation is triggered manually from the Scenarios UI, not automatically.

---

## Matching & AI Layer

Three LLM calls, each with a single responsibility. All go to the same OpenAI-compatible endpoint. Model and endpoint URL are set in `config.toml`.

### 1. Classify
**Input:** `simplified_content`
**Output:** `content_type` (`job_posting` | `lead` | `irrelevant` | `error`) + one-sentence reason

Short prompt. For Slack sources the prompt notes that posts are likely leads.

### 2. Summarize
**Input:** `simplified_content`
**Output:** `summary` (markdown)

Concise summary covering: role, company, location/remote status, key requirements, notable perks or red flags. The summary is the primary reading surface in the UI.

### 3. Evaluate
**Input:** profile text + active scenario description + criteria list (with weights) + `summary`
**Output:** `relevance_score` (0.0–1.0) + `score_reasoning` (short explanation of what matched and what didn't)

### Feedback Loop (criteria refinement)
User feedback notes accumulate on job rows. A "Refine criteria" action per scenario sends recent feedback notes to the LLM, which proposes new or updated `criteria` entries. The user reviews proposals and accepts or rejects each before anything is written to the database. Fully human-confirmed — no automatic criteria updates.

---

## Browser UI

Server-rendered HTML with HTMX. No JS build step. Four views:

### Job List (main view)
Default landing page. Shows `job_posting` and `lead` entries with `status = new`, sorted by `relevance_score` descending. Each row: score badge, content type tag, title, company, source name, and LLM summary. Clicking a row expands it inline to show `score_reasoning` and a link to the original URL. Filter bar switches between `new`, `accepted`, `rejected`, `invalid`, and `lead` views.

### Feedback Panel (inline)
Appears on row expand. Buttons: Accept / Reject / Invalid. Selecting one reveals a required note field and Submit button. On submit, the row collapses and disappears from the current view without a page reload. Note is stored on the job row.

### Fetch Panel
List of enabled sources with last-run timestamp and job counts from `fetch_runs`. "Fetch all" and per-source "Fetch" buttons. Progress streams back via server-sent events — sources complete one by one visibly. Failed sources show a red indicator with the error message.

### Profile & Scenarios
Two tabs. **Profile tab:** textarea with static profile text, save button. **Scenarios tab:** list of scenarios, active/inactive toggle, per-scenario criteria list (add/edit manually, delete), and a "Refine criteria" button that triggers the LLM feedback loop with a review step before writing.

---

## Error Handling

**Fetch errors:** Caught per-source, written to `fetch_runs.error`. Never crash the overall fetch run. Auth expiry for Playwright sources shows a notification in the UI to trigger the manual login step.

**LLM call failures:** 3 retries with exponential backoff. On total failure, job is stored with `content_type = error` and a note in `score_reasoning`. No silent data loss.

---

## Testing Strategy

Layers are independently testable:

- **Fetchers:** Tested with recorded HTTP fixtures and Playwright page mocks. No live network in tests.
- **Simplification:** Pure function on text input, unit tested.
- **AI layer:** LLM calls behind a thin interface. Tests mock the interface and verify prompt construction and result storage.
- **API routes:** FastAPI test client with in-memory SQLite.

No tests hit live sources or live LLM — those are manual verification.

---

## Project Layout

```
job-seek/
  app/
    fetchers/       # one module per source type (http.py, playwright.py, slack.py, ...)
    ai/             # classify.py, summarize.py, evaluate.py, refine.py
    db/             # schema.py, queries.py
    routes/         # FastAPI route handlers (jobs.py, fetch.py, profile.py, scenarios.py)
    templates/      # HTMX HTML templates (Jinja2)
  tests/
  config.toml       # endpoint URL, model name, browser profile path
  browser-profile/  # Playwright persistent session (gitignored)
  docs/
```

---

## Out of Scope (v1)

- Scheduled/automatic fetch runs
- AI harness for exploratory pipeline composition
- Multi-user support
- Mobile UI
