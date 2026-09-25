# Reset / re-evaluate uses the job note — Plan

Spec: `docs/superpowers/specs/2026-09-25-reset-uses-job-note-design.md`.
Run tests with `uv run pytest`. TDD. Commit after each task, staging explicit paths only.

## Task 1 — `summarize(job_note=...)`

File: `app/ai/summarize.py`, tests in `tests/test_summarize.py` (use the existing `_mock_client`;
inspect `client.chat.completions.create.call_args.kwargs["messages"]`).

1. Tests first:
   - Blank or whitespace-only `job_note`: the messages are identical to a call without the argument.
   - With a note (posting prompt): the system message contains the note instruction, and the user
     message ends with `# The candidate's own note on this job (trusted)\n<note stripped>`, placed after the posting text.
   - With a note on a raw lead (`content_type="lead"`, `raw_passthrough=True`): the note block is in
     the user message, the lead system prompt has the instruction, and the returned `summary` is still the verbatim `simplified_content`.
   - A `source_link` URL that appears only in the note (not in `simplified_content`) is dropped.
2. Implement:
   - Add a module constant `_NOTE_INSTRUCTION`. Wording: the candidate's own note on this job follows the
     posting and is trusted. Where it contradicts or adds to the posting (location, remote status,
     salary, scope, company), the note wins, and facts taken from the note are marked inline as
     *(per your note)*. Let the note steer what the summary emphasises. Don't paste the note in wholesale.
     For the lead prompt, the same idea applied to organizations and headline.
   - Only when `job_note.strip()` is non-empty: append the instruction to the chosen system prompt, and
     append `\n\n# The candidate's own note on this job (trusted)\n{note}` to `user_content`
     (after the `[:6000]` truncation of the posting).
   - Validation (`_valid_source_link`, raw-lead summary) keeps using `simplified_content` only.
3. Commit.

## Task 2 — pipeline wiring

File: `app/pipeline.py`, tests in `tests/test_pipeline.py`.

1. Tests first (patch `app.pipeline.summarize` with a `MagicMock` returning a `JobSummary`,
   as in the existing re-evaluate tests; for reset, also patch `classify`, `evaluate` and `assess_fit` as needed):
   - `run_reprocess_job` on a job whose note was set via `q.update_job_feedback(conn, jid, "rejected", "actually fully remote")`
     calls `summarize` with `job_note="actually fully remote"`.
   - `run_reevaluate_job` on an accepted job with a note forwards it the same way.
   - A job with no note calls `summarize` with `job_note=""`.
2. Implement: add `job_note: str = ""` keyword to `_evaluate_posting` and `_ingest_posting` and pass it
   through to `summarize`. `run_reprocess_job` passes `job.get("feedback_note") or ""`, read from the
   `job` dict captured before `reset_job` (the note survives the reset anyway). `run_reevaluate_job` passes the same to its `summarize` call.
3. Full `uv run pytest` green, then commit.
