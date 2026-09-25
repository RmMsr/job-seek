# Score override

Let the user replace a job's computed fit score with their own 0–100 value.

## Data

- New nullable column `jobs.fit_score_override REAL` (0–1, same scale as `fit_score`). Migration: plain `ALTER TABLE jobs ADD COLUMN`.
- Effective score = `COALESCE(fit_score_override, fit_score)`. It drives the "score" sort (`_ORDER_BY["score"]` in `queries.py` and `_sort_key` in `routes/jobs.py`) and the badge colour.
- Re-evaluate / profile re-assess leave the override untouched. `Reset to new` clears it along with the other scores.

## Route

`POST /jobs/{id}/score-override`, form field `score` (int 0–100, anything else → 422).

- If the job has a computed score and `score == round(fit_score * 100)` → override set to NULL (this is also how to undo an override).
- Otherwise store `score / 100`. Jobs without a computed score always store it.
- Logged via `log_score_change` ("Score overridden" / "Score override cleared").
- Responds like the feedback form: re-renders the expanded row, or the detail page view when `detail=1`.

## UI

**Override button** — first item in the Organize group (left of Accept), in both the expanded card and the detail page. Shows the effective score (`73%`, or `–` when none), score-coloured.

**Dial** — clicking the button opens a large floating gauge-arc dial (SVG):

- ~300° arc, 0 at bottom-left → 100 at bottom-right, labels every 10, arc coloured with the score gradient.
- Pointer position shows the exact value in the centre; markers show the computed score (▲) and current override (●).
- Click on the arc saves and closes. Cancel button, Esc and backdrop click close without saving.
- Keyboard: arrows adjust the value, Enter saves.

**Score display** (`fit_score_badge` macro), when an override exists — the computed value muted and struck through, a triangle pointing to the override badge:

- Folded card (left score column): override on top, struck original below with ▴ pointing up to it.
- Tag style (expanded card meta, detail page): inline `~~58%~~ ▸ 73%`.
- Unscored job with override: just the override badge.

## Tests

- Route: set, identical-to-computed clears, unscored job stores, out-of-range → 422.
- Score sort uses the effective score (SQL and in-Python merge).
- Reset clears the override; re-evaluate keeps it.
- Rendering: badge with and without override, folded and tag style; button label.
