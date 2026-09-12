# Job-centric CV views

## Problem

The CV workbench (`/jobs/{id}/cv`) crams directives/plan, preview, and
guardrails into one page, split into two panes. Now that the tailored CV is
manually editable, that page is doing too much at once, and it feels
disconnected from the job it belongs to — you have to bounce between
`/jobs/{id}` and `/jobs/{id}/cv` with no shared framing.

We want a single job-centric hub: one shared header identifying the job, and
a small set of views the user can move between quickly — the job offering
itself, tailoring the CV, previewing/accepting it, and the job's history.

## Views

Four full-page views, each its own URL, sharing a common header + subnav
(plain `<a href>` navigation — no hx-boost, matching the existing
`/cv` / `/cv/advanced` subnav pattern):

| View | Route | Template |
|---|---|---|
| Offering | `GET /jobs/{id}` (existing) | `jobs/detail.html` |
| Tailor CV | `GET /jobs/{id}/cv` (existing) | `cv/workbench.html` |
| Preview CV | `GET /jobs/{id}/cv/preview` (new) | `cv/preview.html` (new) |
| History | `GET /jobs/{id}/history` (new) | `jobs/history.html` (new) |

The job list's inline row-expansion (`jobs/_feedback.html`, used for the
in-list accordion via `/jobs/{id}/expand`) is **untouched** — it stays the
current compact single-card view for quick triage from the list. Only the
standalone detail page gets this restructuring.

### Common header

A new partial, `jobs/_job_header.html`, included at the top of all four
templates: job title, organisation, and the existing tag row (status,
content type, matched scenarios, organisation tag, source — via the current
`meta_tags` macro in `jobs/_macros.html`), followed by the subnav (the four
links above, with an `active` class on whichever matches the current
request path).

Everything else that's on today's detail page header (fit score badge,
published-age, "&#8599; original" link, stale-job badge, headline/hook) is
*not* part of the shared header — it moves into the Offering view's body,
below the header, alongside the rest of the job's detail content.

### Offering (`jobs/detail.html`)

Today's `jobs/_feedback.html` content, minus the header bits now in
`_job_header.html`, minus history, minus the "Tailor CV" CTA:

- Fit score badge, published-age, original-posting link, stale badge,
  headline, summary
- Score tabs (`jobs/_score_tabs.html`) and the fit scorecard
  (interest/attainability)
- The job-note textarea (autosaving)
- Action groups: Accept / Reject / Trash / Delete, and the "Advanced…"
  `<details>` (Pass as new / Re-evaluate / Reset to new / Revisit)

The "Tailor CV" CTA button is removed — the subnav tab is now the only entry
point into CV tailoring. "&larr; Back to list" moves from the top of the page
to the bottom of this view (only this view — the other three don't carry
it).

### Tailor CV (`cv/workbench.html`)

Today's `cv/_plan_pane.html` content only — the directives form, "Analyze
and find improvements" / "Reset to template", suggested directive changes,
and the handled-suggestions list. No preview pane, no guardrails. The
auto-start-planning-on-first-visit behavior (the hidden auto-click button
when `job_cv is None`) is unchanged.

When the job's CV is finalized (`job_cv.finalized_at` set), this view shows
a brief read-only notice ("Accepted — read-only. Start over to edit again.")
instead of the directives form, matching today's behavior of hiding the plan
pane entirely once finalized.

### Preview CV (`cv/preview.html`, new)

Today's `cv/_preview_pane.html` as-is: edit-latitude form, "Update" button,
diff summary, the Base/Tailored/Edit/Differences tab stage
(`cv/_preview_tabs.html`), Accept/export — which already includes the
guardrails findings section (`cv/_findings.html`) at the bottom.

Reachable at any time, including before any tailoring has happened: with no
draft yet, it shows the existing "No tailored CV yet" empty state with the
base CV shown for reference (the same fallback `_preview_pane.html` already
has).

When finalized, this view renders today's `cv/_accepted.html` content
instead (read-only tab stage, export, "Start over").

### History (`jobs/history.html`, new)

The job-events list, pulled out of the `<details>` that currently sits
inside the feedback form in `_feedback.html`, into its own plain read-only
page (`q.get_job_events`). No form wrapper needed.

## Cross-pane update change

Today, generating a draft on the workbench also refreshes the plan pane's
status badge via an out-of-band swap: `_tailor_result`'s `preview_pane` case
in `app/routes/cv.py` renders both the `preview_pane` and `plan_pane`
chunks. Once Tailor CV and Preview CV are separate pages, that second OOB
target is never present in the DOM of the Preview CV page. Drop the
`plan_pane` addition from that case — it would just be wasted rendering
(htmx already silently no-ops an OOB swap with no matching target in the
DOM, but there's no reason to keep paying the render cost). The Tailor CV
page picks up the fresh status the next time it's loaded, same as any other
page navigation.

## Implementation notes

- **New route** `GET /jobs/{job_id}/cv/preview` (`app/routes/cv.py`): 404 if
  job missing, otherwise renders `cv/preview.html` with
  `_workbench_ctx(conn, job_id)` — the same context `cv_workbench` already
  builds; the new template uses the preview/accepted half of it.
- **New route** `GET /jobs/{job_id}/history` (`app/routes/jobs.py`): 404 if
  job missing, loads `job` + `job_events`, renders `jobs/history.html`.
- **Shared header context**: all four routes need `job` with `source_name`
  populated (the same `sources = {s["id"]: s for s in q.get_sources(conn)}`
  lookup `job_detail` already does) so `_job_header.html`'s tag row can
  render the source tag. Factor that lookup into one small helper reused by
  all four routes rather than duplicating it.
- **CSS**: reuse the existing `job-detail-title-row` / `job-detail-meta` /
  `job-tags` classes for `_job_header.html`; add a `.job-subnav` styled like
  the existing `.setup-subnav-tabs`.

## Testing

- Route tests for the two new GET endpoints: 200 with the right template
  context, 404 for a missing job.
- Update the existing template-content assertions in
  `tests/test_routes_cv_workbench.py` and the job-detail route tests to
  match the trimmed-down content of `jobs/detail.html` / `cv/workbench.html`.
- A template test that renders `_job_header.html` standalone (title,
  organisation, tags, active-subnav-link) for a representative job.
- No JS test infra exists in this project — subnav click-through and the
  finalized-state read-only views are manual/dev-server testing, handed to
  the user per this project's UI-change convention.

## Files

**New**: `app/templates/jobs/_job_header.html`, `app/templates/cv/preview.html`,
`app/templates/jobs/history.html`.

**Modified**: `app/templates/jobs/detail.html`, `app/templates/jobs/_feedback.html`
(content extraction only — list-row usage via `/jobs/{id}/expand` stays
intact), `app/templates/cv/workbench.html`, `app/routes/jobs.py`,
`app/routes/cv.py`, `app/templates/base.html` (subnav CSS).
