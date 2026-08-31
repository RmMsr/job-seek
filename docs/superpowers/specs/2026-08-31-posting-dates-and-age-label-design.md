# Posting dates from `summarize`, and the age-label fix

## Problem

Jobs from `generic_listing` sources (the catch-all — LinkedIn, most job boards)
have no age information. `jobs.published_at` is only ever set from
`RawJob.published_at`, which only the `finn_listing`, `eawork_listing`, and
`slack` fetchers populate. Nothing else fills it in and nothing backfills it, so
the age badge (`{% if job.published_at %}`) never renders for those jobs — e.g.
`/jobs/343` (a LinkedIn-sourced posting).

Separately: when the badge *does* render, it overlaps the permalink icon and the
bulk-select checkbox on the collapsed card (backlog bug — "Age label on collapsed
job card overlaps with link and checkbox. It should be left of the link icon").

The `generic_listing` detail fetch for LinkedIn already stores a coarse
`"2 weeks ago"` string in `raw_text` (verified against the guest posting
fragment). That's enough to recover an approximate date.

## Approach

Extract the posting date in the `summarize` LLM call — it already runs for every
`job_posting`/`lead`, already receives `simplified_content[:6000]`, and already
returns a parsed JSON object. Adding one field costs ~10-15 output tokens, no
extra round trip (a dedicated call would add ~1s/job on the local model).

While editing `summarize`'s schema, also extract the hiring **organization** into
a structured `company` field — today the org name is only baked into the `title`
string, and `jobs.company` is left `""` for every `generic_listing` job.

Guarantee `published_at` is never NULL for new jobs by defaulting it to the
processing time at insert; `summarize` upgrades it to the real posting date when
it finds one.

No changes to `finn`/`eawork`/`slack` fetchers, to `classify`, or to scoring.

## Components

### 1. `app/ai/summarize.py` — schema, validation, return type

`_SYSTEM` JSON gains two fields (the `_LEAD_SYSTEM` Slack path is unchanged —
Slack leads already carry a real `published_at` from the message ts):

| field | instruction |
|---|---|
| `company` | hiring organization name, or `""` if unclear |
| `posted_date` | date the posting was published, as `YYYY-MM-DD`, or `""` if not stated or clearly implied — do not guess |

- The user message is prefixed with `Today is <YYYY-MM-DD>.\n\n` so the model can
  resolve relative phrasing ("2 weeks ago", "posted last month") to an absolute
  date. The date is passed in as a parameter (`today: date`), not read from the
  clock inside `summarize`, so tests are deterministic.
- `posted_date` is validated our side: `date.fromisoformat(value)` must succeed
  **and** the result must not be in the future relative to `today`; otherwise
  drop to `""`.
- Return type changes from the `(title, headline, summary)` tuple to a frozen
  dataclass `JobSummary(title: str, company: str, headline: str, summary: str,
  posted_date: str)`. Five positional returns is too error-prone. The `except`
  fallback returns `JobSummary("", "", "", <simplified or "">, "")`.
- The existing `source_link` handling (appended into `summary`) is untouched.

### 2. `app/pipeline.py` — wiring

- `_ingest_posting` unpacks the dataclass and passes `company` and `posted_date`
  into `update_job_pipeline`.
- `summarize(...)` gains the `today=` argument. Source it once at the top of
  `_ingest_posting` (`datetime.now(timezone.utc).date()`).

### 3. `app/db/queries.py` — persistence

- `update_job_pipeline` gains `company: str = ""` and `published_at: str = ""`
  keyword params. Both written as `COALESCE(NULLIF(?, ''), <col>)` — a non-empty
  extracted value wins, otherwise the existing column value is kept. (So the
  insert-time fallback in #4 survives when `summarize` returns `""`.)
- `insert_job`: the `published_at` column value becomes
  `COALESCE(?, datetime('now'))`. All three insert paths (`pipeline` fetch,
  `run_add_job`, the add-by-URL route) go through this one function, so every new
  job has a timestamp immediately.

### 4. Existing NULL rows

No backfill, no migration. `insert_job`'s `COALESCE` covers every new job. The
finite, shrinking set of pre-existing `published_at IS NULL` rows keeps rendering
as it does today — the templates keep their `{% if job.published_at %}` guard, so
those rows just show no badge. If `summarize` extracts a real date on a later
reprocess of such a row, `update_job_pipeline` writes it; otherwise the row stays
NULL. Acceptable.

### 5. Age-label layout — `app/templates/base.html` CSS

The badge (`.job-age`, `margin-left: auto`) is pushed to the right edge of its
flex row, sliding under the absolutely-positioned `.job-link-icon`
(`right: 0.5rem`, or `2.3rem` when a `.job-select-wrap` checkbox is present) and
the checkbox itself.

- `.job-row-header` and `.job-detail-title-row`: reserve right-side space so the
  badge stops left of the link icon. Without a checkbox the icon zone is
  ~`0.5rem + icon width`; with one it starts at `2.3rem`. Use the existing
  `:has(.job-select-wrap)` hook (already used for `.job-link-icon`) to switch
  between two `padding-right` values (≈`2.2rem` / ≈`4rem` — tune visually).
- Check the mobile `@media` block (~line 476-482): there `.job-row-content` is
  `display:block` and both the checkbox and icon `float:right`. Confirm the badge
  (still `margin-left:auto` inside a now-block header — auto margin is inert
  without flex) doesn't ride under the floats on the first line; if it does, give
  it a clear or drop it below.

No change to `time_ago` wording — "N days ago" at all ages, as today.

No UI distinction between a real posting date and the `datetime('now')` fallback:
the badge reads the same either way.

## Testing

Unit:

- `tests/test_summarize.py`: `posted_date` and `company` parsed from a mocked
  response; `posted_date` in the future → `""`; malformed `posted_date` → `""`;
  `today` prefix present in the outgoing user message; `except` path returns the
  dataclass shape. Existing summarize assertions updated for the new return type.
- `tests/test_pipeline.py`: `_ingest_posting` writes `company` and
  `published_at` from the summary; a `""` `posted_date` leaves the insert-time
  `published_at` intact; `company=""` doesn't clobber a fetcher-provided company.
- New `tests/test_queries.py` cases (or wherever `insert_job`/`update_job_pipeline`
  are covered): `insert_job` with `published_at=None` stores `datetime('now')`;
  `update_job_pipeline` `COALESCE(NULLIF(...))` semantics for both columns.

Manual (visual, on the dev server against a throwaway DB): the age badge sits
left of the permalink icon on a collapsed card both with and without the
bulk-select checkbox present, and on the expanded detail header; narrow-viewport
check for the mobile layout.
