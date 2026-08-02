# Job Detail Semantic HTML — Design Spec

**Date:** 2026-08-02
**Status:** Approved

## Overview

The job card (`app/templates/jobs/_row.html`) and job detail/feedback view (`app/templates/jobs/_feedback.html`) are built from generic, inline-styled `<div>`/`<span>` soup with no structural hints about what each piece of content means. This causes several concrete problems:

- The score-reasoning block in the detail view (between the summary paragraph and the feedback form) is unlabeled and low-contrast, making it unclear what it is or that it's distinct from the summary above it.
- The detail view doesn't show the match score or scenario at all — that information silently disappeared even though the card shows it.
- The card's tags/badges and title are crammed onto one flex line; the title doesn't read as a distinct heading.
- The feedback form's scenario `<select>` sits flush against the accept/reject/invalid buttons, and the "best fit" scenario is only indicated by silent pre-selection, not visibly.

This spec introduces semantic HTML (`<article>`, `<h3>`, `<dl>`, `<section>`) and a shared Jinja macro for the score/scenario/tag pills, so the two views can't drift out of sync again, and fixes the specific readability issues raised.

---

## 1. Shared macros (`app/templates/jobs/_macros.html`, new file)

Two Jinja macros, imported by both `_row.html` and `_feedback.html`:

- **`score_scenario(job)`** — renders the score badge + scenario-name tag as a pair, inside a `<dl>` with visually-hidden `<dt>` labels ("Score", "Scenario") and visible `<dd>` pills (reusing the existing `.score-badge`/`.tag` classes and score-threshold coloring logic). Renders nothing if `job.best_score` is `none`.
- **`meta_tags(job)`** — renders `score_scenario(job)` plus content-type and source tags, all inside one `<dl class="job-tags">` (same visually-hidden-`<dt>` pattern, labels "Content type", "Source"). This is the row both views show at the top.

Using `<dl>`/`<dt>`/`<dd>` (rather than bare `<span class="tag">`) means each pill's meaning is explicit in the markup, not just implied by position — directly addressing "semantic HTML should hint at what a field means" — while the visible rendering stays the same compact pill row via a `.sr-only` class on `<dt>`.

## 2. Card template (`app/templates/jobs/_row.html`)

Restructure from a single flex `<div>` to:

```jinja
<article class="job-row" ...>
  {{ macros.meta_tags(job) }}
  <h3 class="job-title">{{ job.title or "(no title)" }}</h3>
  {% if job.headline %}
    <p class="job-hook">{{ job.headline }}</p>
  {% elif job.summary %}
    <p class="job-hook">{{ job.summary | markdown_text | truncate(200) }}</p>
  {% endif %}
</article>
```

- `<div class="job-row">` becomes `<article class="job-row">` (self-contained composition); `role="button"`, `tabindex="0"`, and the `hx-*` click-to-expand attributes are unchanged.
- The title moves out of the tags row onto its own line as an `<h3>`, satisfying "title should start as a new block from the left."
- The headline/summary preview becomes a `<p>` (it already was semantically a paragraph, just an unstyled `<div>`).

## 3. Detail template (`app/templates/jobs/_feedback.html`)

```jinja
<article class="job-row" id="job-{{ job.id }}">
  {{ macros.meta_tags(job) }}
  <h3 class="job-title">{{ job.title or "(no title)" }}</h3>
  {% if job.headline %}<p class="job-hook">{{ job.headline }}</p>{% endif %}
  <p>
    {% if job.company %}{{ job.company }} · {% endif %}
    <a href="{{ job.url }}" target="_blank" rel="noopener">↗ original</a>
  </p>

  {% if job.summary %}
    <section aria-label="Summary">{{ job.summary | markdown }}</section>
  {% endif %}

  {% if job.best_score_reasoning %}
    <section class="score-box" aria-label="Score reasoning">
      {{ macros.score_scenario(job) }}
      {{ job.best_score_reasoning | markdown }}
    </section>
  {% endif %}

  <form class="feedback-form" ...>
    <label>Scenario:
      <select name="feedback_scenario_id" required>
        {% for s in scenarios %}
          <option value="{{ s.id }}" {% if s.id == job.best_scenario_id %}selected{% endif %}>
            {{ s.name }}{% if s.id == job.best_scenario_id %} (Best fit){% endif %}
          </option>
        {% endfor %}
      </select>
    </label>
    <div class="actions" role="group" aria-label="Decision">
      <button type="submit" name="status" value="accepted" class="btn btn-accept">Accept</button>
      <button type="submit" name="status" value="rejected" class="btn btn-reject">Reject</button>
      <button type="submit" name="status" value="invalid" class="btn btn-invalid">Invalid</button>
    </div>
    <label>Note (required):
      <textarea name="note" required placeholder="Why accepting/rejecting? What should change?"></textarea>
    </label>
  </form>
</article>
```

Key points:

- **Parity with the card:** the same `meta_tags(job)` macro call means score, scenario, content type, source, and title all appear exactly as they do on the card — nothing from the short version is missing here.
- **Score box:** `score_scenario(job)` is called a *second* time here, paired directly with the reasoning text inside a bordered `.score-box` section. This repeats the score badge + scenario tag immediately above the reasoning (rather than replacing the top-row instance), so the reasoning is unambiguously grouped with its score/scenario without needing any heading text — the pills themselves carry the meaning. No heading is added to this block.
- **No new heading text is introduced** for the reasoning block — the box border/background plus the repeated badge/tag serve as the label.
- **Best-fit indication:** the scenario `<select>` option matching `job.best_scenario_id` gets an appended `(Best fit)` suffix, so the pre-selection is visible, not just implicit.
- **Decision buttons:** wrapped in `<div class="actions" role="group" aria-label="Decision">` and given `margin-top` spacing (see CSS below) so they're visually separated from the scenario selector above them.

No backend/query changes are needed — `get_job()` already selects `jobs.*` plus `best_score`, `best_scenario_id`, `best_scenario_name`, and `best_score_reasoning` (`app/db/queries.py` `_BEST_SCORE_SELECT`), and `source_name` is already attached in the `/jobs/{id}/expand` route.

## 4. CSS additions (`app/templates/base.html`)

```css
.sr-only { position:absolute; width:1px; height:1px; padding:0; margin:-1px;
  overflow:hidden; clip:rect(0,0,0,0); white-space:nowrap; border:0; }
dl.job-tags { display:flex; flex-wrap:wrap; gap:0.5rem; align-items:center; margin:0; }
dl.job-tags dd { margin:0; }
.job-title { margin:0.5rem 0 0.25rem; font-size:1.1rem; }
.job-hook { margin:0.25rem 0 0; color:#444; }
.score-box { margin-top:0.75rem; padding:0.75rem; border:1px solid #dee2e6;
  border-radius:6px; background:#f8f9fa; }
.score-box dl.job-tags { margin-bottom:0.5rem; }
.feedback-form .actions { margin-top:0.75rem; }
```

Exact values are illustrative; the implementer should match existing spacing/color conventions already in `base.html` (e.g. `.job-details` border-top, existing `.tag`/`.score-badge` colors) rather than inventing a new palette.

## 5. Testing

- Macro rendering: `score_scenario()` renders the badge+tag pair with correct threshold coloring and renders nothing when `best_score` is `none`; `meta_tags()` includes content-type/source tags plus the score/scenario pair.
- Card template: renders `<article>` with `<h3>` title on its own block, tags rendered via `meta_tags`, headline/summary preview as `<p>`; existing click-to-expand `hx-*` attributes unchanged.
- Detail template: renders the same `meta_tags` output as the card for a given job (parity check); score-reasoning present → `.score-box` section renders with a second `score_scenario` call plus reasoning text; score-reasoning absent → no `.score-box`; scenario `<option>` matching `best_scenario_id` includes `(Best fit)` text, others don't.
- Existing feedback-submission tests (POST `/jobs/{id}/feedback`) continue to pass unchanged — no backend behavior changes in this spec.
