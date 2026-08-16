# Surface embedded application/original-posting links in job summaries

## Problem

Some job postings — most concretely Slack-sourced ones — contain a link
within their body text pointing to the real job description or application
page (e.g. a Notion doc, an ATS form). For Slack `job_posting`/`lead`
content, `job.url` (shown in the UI as "↗ original") points at the Slack
message itself, not the posting, so an embedded link is often the only way
to reach the real thing.

Example: job 209 is a Slack posting whose body contains
`[engrewlabs.notion.site/AI-ML-Engine…](https://engrewlabs.notion.site/AI-ML-Engineer-...)`.
That markdown link survives Slack's mrkdwn-to-markdown conversion
(`SlackFetcher`) and the later `simplify()` pass (a no-op on already-plain
text) all the way into `simplified_content`. But `summarize()`'s LLM prompt
never asks for it, so it's silently absent from the generated `summary`.

The `lead` content-type path is unaffected: its `summary` is the original
`simplified_content` verbatim (never AI-rewritten), so any embedded link
already survives there today.

## Scope

Fix the `job_posting` summarization path only (`app/ai/summarize.py`).
Explicitly out of scope: changing HTML→text extraction in
`app/fetchers/http.py` / `app/fetchers/content.py` to preserve `<a href>`
links for finn.no/generic-listing sources. Those sources already expose the
detail page itself as `job.url` (shown as "↗ original"), so there's no
current gap for them — extending link preservation there is speculative
until a concrete case shows up.

## Design

In `app/ai/summarize.py`:

1. **Prompt/schema** — add a `source_link` field to the `_SYSTEM` JSON
   contract. Instruct the model to copy, verbatim, a URL already present in
   the input text that points to the original job description, application
   form, or the hiring organization/job page — preferring the most direct
   link if several appear — or `""` if none is present. Explicitly forbid
   inventing or reconstructing a URL.

2. **Validation** — after parsing the JSON response, only accept
   `source_link` if all of:
   - it's a non-empty string
   - it starts with `http://` or `https://`
   - it appears verbatim as a substring of `simplified_content`

   This guards against the model paraphrasing, truncating, or hallucinating
   a link. If validation fails, treat the link as absent (don't raise —
   consistent with the rest of `summarize()`'s best-effort error handling).

3. **Formatting** — if a valid link survives validation, append it to the
   returned `summary` as a trailing markdown line:

   ```
   <existing summary body>

   **Original posting:** <url>
   ```

   No DB/schema/template changes. It flows through as ordinary summary text
   everywhere summaries already render (job detail page, reevaluation,
   etc.) since it's baked into the `summary` string itself.

`_LEAD_SYSTEM` and the lead-content-type branch of `summarize()` are
unchanged.

### Why this shape

- Reusing the existing `summary` string (rather than a new DB column) keeps
  the change contained to one file and matches the literal ask ("kept in
  the summary"); nothing downstream needs to know a link ever existed.
- `run_reevaluate` (`pipeline.py`) re-summarizes from saved
  `simplified_content` via the same `summarize()` call, so it picks up this
  behavior automatically — no separate pipeline change needed.
- Verbatim substring validation is a cheap, deterministic guard against LLM
  URL hallucination without adding a second network round-trip.

## Testing

Extend `tests/test_summarize.py`:
- model returns a `source_link` present in the input → appended to summary
- model returns a `source_link` NOT present in the input (hallucinated) →
  rejected, summary unchanged
- model returns a `source_link` that isn't `http(s)://` → rejected
- model returns no link / empty string → summary unchanged, no trailing line
- lead content-type path is unaffected by the new field
