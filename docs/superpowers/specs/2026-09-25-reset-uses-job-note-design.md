# Reset / re-evaluate uses the job note

## Goal

When a job is reset to new or re-evaluated, the regenerated summary should use the
candidate's own job note (`jobs.feedback_note`), so the description gets better
from their comments. Scoring and fit read the summary, so they improve too.

## Scope

- Note source: only `jobs.feedback_note`. `reset_job` already keeps it. Scenario
  feedback notes are not used.
- Paths: `run_reprocess_job` (single and bulk reset) and `run_reevaluate_job`.
  Fetch and add-job have no note, so they behave exactly as they do now.

## Design

- `summarize(..., job_note: str = "")` in `app/ai/summarize.py`.
  - A blank or whitespace-only note leaves the prompt exactly as it is now.
  - With a note, the user message gets an extra block
    `# The candidate's own note on this job (trusted)\n<note>`, placed after the
    posting text. The system prompt gets one extra instruction: the note is the
    candidate's own trusted words. Where it contradicts or adds to the posting
    (location, remote status, salary, scope, company), the note wins, and facts
    from the note are marked inline as *(per your note)*. The note also steers
    what the summary emphasises. Don't paste the note in wholesale.
  - Slack leads that keep the original message as the summary (`raw_lead`): the
    note goes to the lead prompt the same way, which affects the title and
    headline. The summary stays the verbatim message.
  - The `source_link` and `posted_date` checks still run against the posting text
    only. A URL that appears only in the note is rejected.
- `_ingest_posting` / `_evaluate_posting` take `job_note: str = ""` and pass it
  on to `summarize`.
- `run_reprocess_job` passes `job.get("feedback_note") or ""`.
  `run_reevaluate_job` passes the same to its `summarize` call.

## Testing

- `summarize`: with no note, the messages are unchanged. With a note, the trusted
  block and the extra instruction are present, for both the posting and the lead
  prompts. A URL that appears only in the note is not accepted as `source_link`.
- Pipeline: reset and re-evaluate both forward the job's `feedback_note` to
  `summarize`.
