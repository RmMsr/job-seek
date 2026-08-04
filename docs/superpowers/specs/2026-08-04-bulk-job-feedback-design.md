# Bulk Job Feedback (Multi-Select) — Design Spec

**Date:** 2026-08-04
**Status:** Approved

## Overview

Triaging jobs one at a time — expand, pick a scenario, write a note, submit — is slow when several jobs deserve the same verdict (e.g. a batch of obvious duplicates, or ten postings that are clearly out of scope). This adds a multi-select bulk action to the job list: check several jobs, optionally override which scenario the feedback is attributed to, optionally leave one shared note, and apply Accept / Reject / Invalid to all of them at once.

Two related gaps get fixed as part of this work:

1. **Reject vs. Invalid is currently unexplained.** Both are just two `status` values with no distinguishing copy anywhere. This spec fixes their meaning going forward: **Reject** = the posting is real but doesn't match the user's criteria (feeds into scenario criteria tuning via `get_recent_feedback_notes`). **Invalid** = not a usable job posting at all — expired, spam, duplicate, wrong content type — and should *not* be treated as scenario-fit feedback. Both single-job and bulk forms get `title` tooltips on the Reject/Invalid buttons stating this.
2. **No way to fold an expanded row back without submitting.** Today, clicking a row swaps it into the feedback form (`GET /jobs/{id}/expand`), and the only way out is submitting the form (which removes the row from the current filtered view). This adds a symmetric collapse.

**Note also becomes fully optional**, single-job and bulk alike — dropping the `required` constraint from the form and the route. Nothing here changes what "note" is used for, just whether it's mandatory.

---

## 1. Row checkboxes and the bulk form

`_row.html` gains an always-visible checkbox:

```html
<input type="checkbox" name="job_ids" value="{{ job.id }}" form="bulk-form" onclick="event.stopPropagation()">
```

It's associated with a `<form id="bulk-form">` declared once in `list.html` (outside the job loop) via the HTML `form=` attribute, rather than nesting a form inside every row — this way, when a single row's outerHTML gets swapped (expand/collapse), the bulk form and every other row's checkbox state are untouched. `onclick="event.stopPropagation()"` stops the checkbox click from also triggering the row's existing `hx-get=".../expand"` trigger.

**Known accepted edge case:** if a checked job's row is expanded into the feedback form, its checkbox (which lives only in `_row.html`) is removed from the DOM along with the row, silently dropping it from the bulk selection. This is acceptable — expanding a row is itself a single-item action, and re-collapsing brings the checkbox back unchecked.

## 2. Bulk action bar

A bar with `position: sticky; bottom: 0`, hidden by default and shown via CSS whenever at least one checkbox is checked:

```css
#jobs-content:has(input[name="job_ids"]:checked) .bulk-bar { display: flex; }
```

No selection-mode toggle, no show/hide JS. Contents:

- A live "N selected" count, updated by a small vanilla `change` listener on `input[name="job_ids"]` (same inline-`<script>` pattern already used for the progress bar in `base.html` — no bundler introduced).
- "Clear selection" button (`type="button"`, unchecks every `job_ids` checkbox).
- Scenario override `<select name="feedback_scenario_id">`, first option `value=""` labeled "Keep each job's own scenario" (the default), followed by every scenario.
- Optional `<textarea name="note">`, no `required`.
- Three submit buttons sharing `name="status"`: `value="accepted"` (Accept), `value="rejected"` (Reject, `title` tooltip), `value="invalid"` (Invalid, `title` tooltip) — same `.btn-accept/.btn-reject/.btn-invalid` classes as the single-job form.
- Hidden inputs `status_filter` / `content_type_filter`, populated from the list page's current query params, so the backend knows which filtered view to re-render after the bulk update.

`hx-post="/jobs/bulk-feedback" hx-target="#jobs-content" hx-swap="innerHTML"`.

## 3. Template restructuring

Extract the filter-bar + job loop currently inline in `list.html` into a new partial `jobs/_content.html`, included by `list.html` inside `<div id="jobs-content">`. `GET /` renders `list.html` (which pulls in `_content.html`); `POST /jobs/bulk-feedback` renders `_content.html` directly as its HTMX response. This makes the bulk response refresh both the job list *and* the filter-bar counts in one swap, matching how a single-job feedback submission already makes a row disappear once its status no longer matches the active filter.

## 4. Routes (`app/routes/jobs.py`)

- `POST /jobs/{job_id}/feedback`: `note` becomes `Form(None)` (was `Form(...)`).
- New `GET /jobs/{job_id}/collapse`: mirrors `job_expand` but renders `jobs/_row.html` instead of `_feedback.html` — same job lookup + source-name enrichment.
- New `POST /jobs/bulk-feedback`:
  ```python
  job_ids: list[int] = Form(...)
  status: str = Form(...)
  note: str | None = Form(None)
  feedback_scenario_id: str = Form("")   # "" sentinel = keep each job's own
  status_filter: str | None = Form(None)
  content_type_filter: str | None = Form(None)
  ```
  For each `job_id`: if `feedback_scenario_id` is non-empty, use it for every job; otherwise look up that job's own `best_scenario_id` via `q.get_job(conn, job_id)` (already computed by the existing best-score join — no new SQL needed) and use that. Call `q.update_job_feedback(conn, job_id, status, note, scenario_id)` per job. Then re-render `_content.html` with `q.get_jobs(conn, status=status_filter, content_type=content_type_filter)` and `q.get_job_counts(conn)`, same as `job_list` does today.

## 5. Fold/collapse (`_feedback.html`)

Wrap the title/headline/meta block — the same content shown when the row is collapsed — in a clickable element carrying the same trigger pattern the collapsed row uses today:

```html
<div role="button" tabindex="0" style="cursor:pointer"
  hx-get="/jobs/{{ job.id }}/collapse"
  hx-target="#job-{{ job.id }}"
  hx-swap="outerHTML"
  hx-trigger="click, keyup[key=='Enter']">
  {{ macros.meta_tags(job) }}
  <h3 class="job-title">...</h3>
  {% if job.headline %}<p class="job-hook">...</p>{% endif %}
</div>
```

The `<form>` (scenario select, note, buttons) sits outside this wrapper, so interacting with any form control never triggers a collapse.

## 6. Data layer (`app/db/queries.py`)

`update_job_feedback` and `get_job` already support everything the bulk route needs (`get_job` already returns `best_scenario_id` via the existing best-score join) — no changes there.

One existing gap needs closing to make the Reject/Invalid split real rather than just a tooltip: `get_recent_feedback_notes` (used by `POST /scenarios/{id}/refine-criteria`, `app/routes/scenarios.py:182`, to pull notes for LLM-assisted criteria tuning) currently filters only by `feedback_scenario_id`, so an "invalid" job's note is picked up exactly like a "rejected" one. Add `AND status != 'invalid'` to its `WHERE` clause so invalid feedback never reaches criteria tuning, matching the semantics in the Overview.

## 7. Reject/Invalid tooltips

Add `title` attributes to the Reject and Invalid buttons in both `_feedback.html` and the bulk bar:

- Reject: `title="Doesn't match your criteria — feeds back into scenario tuning."`
- Invalid: `title="Not a usable posting (expired, spam, duplicate, wrong content) — doesn't affect scenario criteria."`

## 8. Testing

- Route: `POST /jobs/{id}/feedback` with no `note` field succeeds (was previously a 422).
- Route: `GET /jobs/{id}/collapse` returns the same markup shape as a fresh `_row.html` render for that job.
- Route: `POST /jobs/bulk-feedback` with 3 `job_ids`, no scenario override, applies each job's own `best_scenario_id`; with an explicit override, applies that scenario to all 3 regardless of their individual best match.
- Route: bulk response reflects `status_filter`/`content_type_filter` — e.g. bulk-rejecting from the "new" filter returns a list that no longer contains those jobs, with updated counts.
- Template: checkbox `onclick` stops propagation (manual/browser check — no template-level test).
- Template: bulk bar `:has()` visibility toggle (manual/browser check).
- Query: `get_recent_feedback_notes` excludes notes from jobs with `status = 'invalid'` even when `feedback_scenario_id` matches; still returns notes from `rejected`/`accepted` jobs tagged to that scenario.
