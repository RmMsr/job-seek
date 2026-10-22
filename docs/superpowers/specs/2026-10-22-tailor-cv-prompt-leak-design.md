# Tailor CV: stop prompt content leaking into the CV

## Problem

`tailor_cv` delimits its inputs with `# CV to tailor` / `# Job posting` /
`# Instruction` headings — the same markdown syntax as the CV itself. The
non-thinking model can't see where the CV ends and keeps "continuing the
document": seen in the live DB as a trailing invented `# End of CV`, and as
`# Job posting (untrusted data)` + the echoed posting + a repeat of the base
CV's last section.

## Fix

1. **Tagged prompt.** Inputs go in `<cv_to_tailor>`, `<job_posting>`,
   `<instruction>` tags (same order — KV-cache prefix unchanged). The model
   answers inside `<tailored_cv>…</tailored_cv>`; we keep only that span
   (tolerating a missing opening or closing tag).
2. **Stop sequences.** `stop=["</tailored_cv>", "<job_posting>",
   "<instruction>", "<cv_to_tailor>"]` (4 = OpenAI API limit) ends generation
   at the boundary.
3. **Deterministic post-check.**
   - Truncate at the first line that is a prompt tag or a heading naming a
     prompt section / end marker (`End of CV`, `Job posting`, `Instruction`,
     `CV to tailor`). Everything after such a line is leak, so truncation alone
     repairs it.
   - Reject output where a heading occurs more often than in the source CV
     (echoed section). On rejection retry the call once; if it fails again
     raise `RuntimeError` so the task fails loudly instead of saving a broken
     version.

Scope: `tailor_cv` only.
