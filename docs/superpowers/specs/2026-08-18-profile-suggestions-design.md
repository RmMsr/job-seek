# Profile improvement suggestions from job feedback

## Problem

Job accept/reject/trash notes accumulate real signal about profile fit
(e.g. "No AI focus", "Only senior positions", "I lack leadership experience
for a visible Head of Technology role") but today they just sit on the job
row — nothing turns them into profile edits. The scenario-criteria refine
flow (`app/ai/refine.py`, `/scenarios/{id}/refine`) already does the
analogous thing for scenario criteria: batch up unhandled feedback, ask the
LLM to propose changes, let the user review/apply as checkboxes. This
feature is the same pattern applied to the profile document instead of a
criteria table.

`jobs.feedback_handled_at` already exists in the schema (added in
`945f629`) but nothing ever sets it — it was evidently scaffolded for
exactly this feature and is otherwise dead.

## Data model

No new tables or columns. Reuse `jobs.feedback_note` and
`jobs.feedback_handled_at`.

New query in `app/db/queries.py`:

```python
def get_unhandled_profile_notes(conn, limit: int = 30) -> list[dict]:
    """jobs with a non-empty feedback_note and feedback_handled_at IS NULL,
    most recent (highest id) first. Returns [{id, status, feedback_note}]."""
```

No day-window filter (unlike `get_recent_feedback_notes` for scenarios) —
there's no per-note timestamp to window on (only `fetched_at`, which is
when the job was fetched, not when feedback was given), and the
handled/unhandled flag alone is a sufficient gate given current volume
(~40 notes total).

Marking handled happens **by job ID**, not by anchor timestamp: the
proposal batch is generated from a specific set of job rows, so the
"Apply" form carries those job IDs directly (hidden inputs) and applying
sets `feedback_handled_at = datetime('now')` on exactly those rows via a
new `mark_profile_feedback_handled(conn, job_ids: list[int])`.

## LLM proposal — new `app/ai/refine_profile.py`

Mirrors `app/ai/refine.py`'s shape (system prompt + `propose_*` function +
JSON extraction via `extract_json`), producing profile edits instead of
criteria edits.

**Input:** current profile text, plus the unhandled notes as
`{status, note}` pairs (status is `accepted`, `rejected`, or `trash`).

**System prompt** instructs the LLM:
- `accepted` notes are positive signals (the profile likely already fits,
  or a trait worth reinforcing).
- `rejected` notes are negative/mismatch signals (something the profile
  should flag as unwanted, or an unmet requirement to soften).
- `trash` notes are frequently administrative noise ("duplicate",
  "no longer open", "Failed to fetch: HTTP 403") carrying no preference
  signal — ignore them unless the note itself expresses a genuine
  preference.
- The profile is organized into `##`-level sections; propose changes
  against an existing section by its exact heading text, or a new section
  name if nothing fits.
- Only bullet lines (`- ...`) are valid `add`/`replace`/`remove` targets.
- Only propose changes clearly supported by the feedback. Return `[]` if
  none.

**Output:** JSON array of:

```json
{"section": "Product focus", "action": "add|replace|remove", "text": "...", "target": "..."}
```

- `add`: `text` is the new bullet's content, `target` is `null`.
- `remove`: `target` is the existing bullet's exact text (matched
  casefold-stripped, same technique as `refine.py`'s
  `match_removal_target`), `text` is `null`.
- `replace`: `target` is the existing bullet's exact text, `text` is its
  replacement.

Parsed into a `ProfileProposal` dataclass: `section: str`, `action: str`,
`text: str | None`, `target: str | None`. Malformed items (bad `action`,
missing required field for that action) are skipped, same defensive style
as `propose_criteria`.

## Applying proposals — new `app/profile_apply.py`

```python
def resolve_proposals(proposals: list[ProfileProposal], profile_text: str) -> list[dict]:
def apply_profile_proposals(profile_text: str, resolved: list[dict]) -> str:
```

`resolve_proposals` mirrors `_resolve_proposals` in `routes/scenarios.py`:
for each proposal, locate its target bullet line (for `replace`/`remove`)
by exact casefold match anywhere in the document; drop proposals whose
target can't be found. For `add`, locate the target section by
case-insensitive heading match (or note that it's new). Returns a list of
resolved dicts ready to render as checkboxes and to round-trip through the
accept form (same `kind_i`/`section_i`/`text_i`/`target_i` convention as
the criteria proposals' `kind_i`/`text_i`/`weight_i`).

`apply_profile_proposals` takes the resolved+checked subset and mutates
the profile text:
- `add`: append `- {text}` as the last line of the matched `##` section
  (before the next heading or end of document). If the section didn't
  exist, append a new `## {section}` heading with the bullet at the end of
  the document.
- `remove`: delete the matching bullet line.
- `replace`: replace the matching bullet line's text, keeping the `- `
  prefix.

Section/bullet parsing is line-based: a section starts at a line matching
`^## ` and runs until the next `^#` (any level) or EOF; a bullet is a line
matching `^- `.

## Routes — `app/routes/profile.py`

- `POST /profile/refine`: `StreamingResponse`, same shape as
  `/scenarios/{id}/refine` — fetch unhandled notes, call
  `propose_profile_changes`, resolve, render a `_proposals.html`-style
  partial with checkboxes (add plain / remove strikethrough / replace
  before→after, each tagged with its `section`), stream back as
  `"HTML:" + ...`. Hidden `job_ids` (one per contributing job) included in
  the form.
- `POST /profile/refine/accept`: reads the checked rows the same way
  `accept_proposals` does (`kind_i`/`apply_i` pattern), calls
  `apply_profile_proposals`, `upsert_profile`s the result, calls
  `mark_profile_feedback_handled` on the submitted `job_ids`, re-renders
  the profile textarea with the updated content.

## UI — `app/templates/profile/index.html`

One button, "Suggest profile improvements" (mirrors "Refine all
scenarios"/"Re-evaluate everything" placement), with an unhandled-notes
count next to it (e.g. "12 notes not yet reviewed") computed from
`len(get_unhandled_profile_notes(conn))` in `profile_page`'s context.
Clicking streams progress the same way other progress-button actions do,
then swaps in the proposals partial (new
`app/templates/profile/_proposals.html`, structurally the same as
`scenarios/_proposals.html` but with a `section` badge instead of a
`weight` badge, and a third "replace" rendering: strikethrough old text →
new text).

## Why this shape

- No new table: `feedback_handled_at` already exists and was clearly
  meant for this; reusing it avoids schema churn for a personal single-DB
  app (per this project's migration philosophy).
- Marking handled by job ID instead of a time anchor is simpler here than
  the scenario approach, because jobs already have stable identity in the
  form — the anchor trick in `refine.py` exists only because
  `scenario_feedback` rows aren't otherwise addressed individually from
  the accept form.
- Restricting `add`/`replace`/`remove` targets to bullet lines keeps text
  matching unambiguous (exact casefold line match, same as criteria) and
  avoids the profile's occasional prose paragraphs ("About me" intro,
  "Location:"/"Citizenship:" lines) needing fuzzy matching.
- Including all three job statuses as candidate input (rather than
  excluding `trash`) keeps the query simple; the noise problem is handled
  qualitatively in the system prompt instead, where the LLM can tell "no
  longer open" apart from an actual preference note.

## Testing

- `tests/test_refine_profile.py` (new, matching `tests/test_refine.py`'s
  naming for `app/ai/refine.py`): `propose_profile_changes`
  against a fake client — happy path, malformed JSON, malformed items
  skipped, empty notes.
- `tests/test_profile_apply.py` (new): `resolve_proposals` (target found /
  not found, section found / not found) and `apply_profile_proposals` for
  each action, including "section doesn't exist yet" (add creates a new
  `##` section) and "target text not found" (dropped during resolve, so
  `apply_profile_proposals` never sees it).
- `tests/test_routes_profile.py`: extend with `/profile/refine` (streams
  proposals HTML, includes hidden `job_ids`) and `/profile/refine/accept`
  (applies checked rows, persists via `upsert_profile`, marks the
  submitted job IDs' `feedback_handled_at`, leaves unchecked rows'
  underlying jobs still unhandled) tests, mirroring
  `tests/test_routes_scenarios.py`'s refine/accept tests. Also a
  profile-page render test asserting the unhandled-notes count and button
  are present.
