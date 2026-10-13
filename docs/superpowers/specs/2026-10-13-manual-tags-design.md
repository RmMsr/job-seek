# Manual tags & organization override — design

Backlog: "Allow manual tags and filling missing tags like: Missing organization
(just 1 per job), free re-usable tags like 'Language: English'. Interface should
make it easy to find existing tags to reduce duplicates."

## Data model

**Organization override.** New column `jobs.company_override TEXT` (NULL = none).
Effective org = `COALESCE(NULLIF(company_override, ''), company)`. Every place that
shows or filters by org uses the effective value: org pill, detail header,
`org=` / `org=none` filtering, the org list query, tailored-CV meta line. The LLM
keeps writing `company`; re-processing never touches `company_override`. Saving
an empty override clears it (LLM value shows again).

**Tags.** A tag is a single free-text string; `Language: English` is just a
naming convention, no key/value logic.

```sql
tags     (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE)
job_tags (job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
          tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
          PRIMARY KEY (job_id, tag_id))
```

- Names are trimmed and inner whitespace collapsed; empty names are rejected.
- Adding a name that matches an existing tag case-insensitively attaches that
  tag (first spelling stays canonical) — no duplicates.
- A tag left with no jobs is deleted, so suggestions only show tags in use.
- Migration is additive (new column + tables); `jobs` is not rebuilt, FTS
  triggers are untouched.

Out of scope: full-text search (`q=`) does not match tags or the org override;
no tag management page (rename/merge); no LLM-assigned tags; no bulk tagging.

## Editing UI

Lives in the shared `meta_tags` macro (`jobs/_macros.html`), so list row,
expanded card and detail header behave identically. All controls use
`event.stopPropagation()` so they don't toggle the row.

**Tags**
- Manual tags render as pills after the existing ones, visually distinct from
  system pills. Clicking a pill adds it as a must-have filter (where a filter
  context exists, like the org pill); `×` removes the tag from the job.
- A trailing `+ tag` pill swaps into an inline input. Enter adds and keeps the
  input open and focused for the next tag; Escape/blur closes it.
- Autocomplete: one page-level `<datalist>` of all tags ordered by usage count
  (browser does case-insensitive substring matching). Any typed value is
  accepted — a new name creates a new tag. The datalist is refreshed
  out-of-band when a tag is created or deleted.

**Organization**
- Org pill gets a small `✎` that swaps into an inline input prefilled with the
  effective org; jobs without an org show a muted `+ organization` pill instead.
- Free text with a datalist of existing effective orgs as suggestions — any
  value, including a brand-new org, is accepted. Empty clears the override.
- An overridden org gets a subtle marker; its tooltip shows the LLM value (if any).

**Routes** — each returns the re-rendered pill row for that job, swapped in place:
- `POST /jobs/{id}/tags` (`name`) — attach (reuse or create).
- `DELETE /jobs/{id}/tags/{tag_id}` — detach; delete tag if now unused.
- `POST /jobs/{id}/organization` (`name`) — set override, or clear when empty.

Each change is logged via `add_job_event` (kinds `tag_added`, `tag_removed`,
`org_override`) so it appears in History.

## Filtering

- `JobFilter` gains `tags: tuple[str, ...]` (must have) and
  `not_tags: tuple[str, ...]` (must not have). URL: repeated params,
  `?tag=Remote&tag=Language: English&not_tag=Agency`. Carried through tab
  switches, sorting, search and bulk-action forms like `org` is today.
- SQL: one `EXISTS (…)` per must-have tag, one `NOT EXISTS (…)` per excluded
  tag, matched by name case-insensitively. Tab counts respect them.
- Active tag filters appear as toolbar chips (`+ Remote ×`, `− Agency ×`):
  clicking the label flips include/exclude, `×` removes. They count toward the
  active-filter badge.
- Filter panel gets a "Tags" row: datalist-backed input plus **Must have** and
  **Exclude** buttons, so a tag can be excluded without finding a job that has it.

## Testing

- Query layer: name normalisation, case-insensitive reuse, orphan cleanup,
  effective-org resolution, override survives a re-summarize, tag
  include/exclude filter combinations, counts.
- `JobFilter`: parse/serialise repeated `tag` / `not_tag`, `with_*` helpers.
- Routes: add/remove tag, set/clear org override return the updated fragment
  and log events.
