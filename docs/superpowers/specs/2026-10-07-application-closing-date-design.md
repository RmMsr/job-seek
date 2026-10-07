# Application closing date

Detect when applications for a job close, show it next to the posting age, and sort by it. The UI never says "deadline".

## Extraction

The summarizer prompt (`app/ai/summarize.py`) returns one more field, `apply_by`, in the same LLM call as `posted_date`:

- `YYYY-MM-DD`: resolve relative phrases ("within 2 weeks", "by end of month") against the "Today is …" line.
- `"rolling"`: "rolling basis", "open until filled".
- empty string: no end date stated, or an explicit "ASAP".

`JobSummary.apply_by` is validated in the style of `_valid_posted_date`. Past dates are allowed (they mean closed). Accepted window is today ±366 days; `"rolling"` passes through; anything else becomes empty.

## Storage

New column `jobs.apply_by TEXT`, added with a plain additive migration (like `_migrate_jobs_add_published_at`). `NULL` means "soonest" (no end date).

`update_job_pipeline` writes it with `COALESCE(NULLIF(?, ''), apply_by)`, so an empty extraction never wipes an existing value. No backfill: existing jobs get a value only when reprocessed.

## Display

`closing(apply_by)` in `app/dates.py`, registered as a template filter:

| value | label |
|---|---|
| `NULL` | `apply soonest` |
| today | `closes today` |
| tomorrow | `closes tomorrow` |
| ≤ 14 days | `closes in N days` |
| > 14 days | `closes 17 Nov` |
| `rolling` | `rolling` |
| past | `closed yesterday` / `closed N days ago` (muted) |

The exact date goes in a `title` tooltip. A date ≤ 3 days out gets a subtle highlight. The label is shown beside the `job-age` chip everywhere the age appears: the collapsed row (`_row.html`), the expanded card and the detail page (`_feedback.html`).

## Sort

New option "Closing soonest" (`order=closing`) in the jobs order dropdown. Groups, in order:

1. dated, still open: nearest first
2. no date (`NULL`, "soonest")
3. `rolling`
4. closed: most recently closed first

Ties within a group go newest posting first (`COALESCE(published_at, created_at) DESC`). "Open" means `apply_by >= date('now')`.

`_ORDER_BY["closing"]` in `app/db/queries.py` uses a `CASE` bucket. `_sort_key("closing")` in `app/routes/jobs.py` mirrors it; note the stale-row re-sort runs with `reverse=True`, so the key is inverted to match.

## Tests

- `apply_by` validator, and summarizer parsing of `apply_by`
- `closing()` wording at each boundary, with a frozen today
- SQL order vs `_sort_key` parity across all four groups
- label renders in the row and the detail templates; the order dropdown includes "Closing soonest"
