# Job Card Readability — Design Spec

**Date:** 2026-08-02
**Status:** Approved

## Overview

Job cards on the overview list (`app/templates/jobs/_row.html`) are hard to scan: the description preview is the full multi-topic AI summary flattened to plain text and hard-truncated at 200 characters, often cutting off mid-thought. The heading uses whatever title the scraper happened to extract, which is inconsistent across sources and sometimes empty (`(no title)`). Expanding a job requires precisely hitting a small "Details" button.

This adds an AI-generated one-line hook per job, normalizes the displayed title into a consistent format, and makes the entire card clickable instead of requiring the button.

---

## 1. `summarize()` returns three fields (`app/ai/summarize.py`)

Return type changes from `str` to `tuple[str, str, str]` = `(title, headline, summary)`, requested as a single JSON response — same `extract_json` + `json.loads` pattern `classify()` already uses:

```json
{"title": "<Role - Location (remote/hybrid/onsite) @ Organization>",
 "headline": "<one punchy sentence on the most compelling/notable detail>",
 "summary": "<existing markdown body: role, company, location, requirements, comp, perks/red flags>"}
```

- `title`: role as given in the posting (or a concise generated one if unclear), plus location including remote/hybrid/onsite status, plus organization name.
- `headline`: a single sentence highlighting the most compelling or notable thing about the posting (e.g. standout comp, requirement, or perk) — distinct from `title`, which is structural facts, not a hook.
- `summary`: unchanged in content/purpose from today.

On any failure (malformed JSON, API error), returns `("", "", "")` — same graceful-degradation shape as today's `except: return ""`.

## 2. Storage (`app/db/schema.py`, `app/db/queries.py`)

- New column: `headline TEXT NOT NULL DEFAULT ''`, added via a plain additive migration (`_migrate_jobs_add_headline`), following the same pattern as `_migrate_jobs_add_feedback_scenario_id` (`ALTER TABLE jobs ADD COLUMN ...`, no rebuild needed since it's a simple additive column with no constraint).
- `title` is **reused**, not added. Today it's set once at insert time from the scraper (`raw.title`) and never updated again. It now also gets overwritten whenever `summarize()` succeeds.
- `update_job_pipeline(conn, job_id, *, simplified_content, content_type, title="", summary="", headline="")` — gains `title` and `headline` params, persists all three.

## 3. Pipeline wiring (`app/pipeline.py`)

Both call sites unpack the 3-tuple and resolve a fallback so a failed/empty AI response never blanks out a previously-good title:

- `run_fetch`: `ai_title, headline, job_summary = summarize(client, model, simplified)`, then `title=ai_title or raw.title` passed to `update_job_pipeline`.
- `run_reevaluate`: `ai_title, headline, new_summary = summarize(...)` (or `(job["title"], job["headline"], job["summary"])` when `simplified_content` is empty, matching the existing early-out), then `title=ai_title or job["title"]`.

## 4. One-time backfill for existing jobs

New `run_backfill_headlines(conn, client, model)` generator in `pipeline.py`, same shape/streaming-progress pattern as `run_reevaluate`:

- Selects jobs via new `q.get_jobs_missing_headline(conn)`: `headline = ''` AND `content_type IN ('job_posting', 'lead')` AND `simplified_content != ''`.
- For each: calls `summarize()`, updates `title`/`headline`/`summary` via `update_job_pipeline` (same fallback-preserving logic as above), yields a progress line.

Exposed as `POST /jobs/backfill-headlines` in `app/routes/jobs.py` (`StreamingResponse`, same pattern as `POST /scenarios/reevaluate`). Triggered by a `data-progress-url="/jobs/backfill-headlines"` button on the jobs list page (`list.html`), placed near the filter bar — mirrors the existing "Re-evaluate all scenarios" button on the scenarios page. No `hx-target` specified, so it finishes with a full page reload.

## 5. Card template (`app/templates/jobs/_row.html`)

- Heading: `<strong>{{ job.title or "(no title)" }}</strong>` unchanged in code — behavior changes because `job.title` is now AI-normalized after the pipeline runs. `(no title)` remains as the fallback for jobs that were never summarized (irrelevant/error content types) or where AI generation failed and there was no prior title.
- The separate `{% if job.company %}<span>· {{ job.company }}</span>{% endif %}` is **removed from this template** — the normalized title already carries the organization. `_feedback.html` (detail view) keeps its own company span unchanged.
- The description preview block changes from the truncated-summary text to:
  ```jinja
  {% if job.headline %}
    <div style="margin-top:0.4rem; color:#444;">{{ job.headline }}</div>
  {% elif job.summary %}
    <div style="margin-top:0.4rem; color:#444;">{{ job.summary | markdown_text | truncate(200) }}</div>
  {% endif %}
  ```
  falling back to today's truncated-summary behavior for jobs not yet backfilled or where generation failed.
- Whole-card click: the `<button hx-get="/jobs/{{ job.id }}/expand" ...>Details</button>` is removed. Its `hx-get`, `hx-target="#job-{{ job.id }}"`, `hx-swap="outerHTML"` move onto the outer `.job-row` div, joined by `hx-trigger="click, keyup[key=='Enter']"`, `role="button"`, `tabindex="0"`, and `style="cursor:pointer"` for basic keyboard accessibility.

## 6. Hover affordance (`app/templates/base.html`)

Add `.job-row:hover { background: #f8f9fa; }` so the card's clickability is visually discoverable now that the button is gone.

## 7. Testing

- `summarize()`: successful JSON response splits into `(title, headline, summary)`; malformed JSON / API exception returns `("", "", "")`.
- Pipeline: `run_fetch` and `run_reevaluate` fall back to the existing title when `summarize()` returns an empty title; `headline` and `title` are persisted via `update_job_pipeline`.
- Migration: fresh DB gets the `headline` column with `''` default; existing DB gets the column added without touching existing rows' data.
- `run_backfill_headlines`: only touches jobs with empty `headline` and non-empty `simplified_content`/eligible `content_type`; skips jobs already backfilled.
- Route: `POST /jobs/backfill-headlines` streams progress and updates job rows.
- Template: card renders `job.headline` when present, falls back to truncated `job.summary` when absent; card `<div>` carries `hx-get` and responds to both click and Enter; `_row.html` no longer renders a company span; `_feedback.html` still does.
