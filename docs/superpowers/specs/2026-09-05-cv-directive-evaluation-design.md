# CV directive evaluation — design

## Goal

Today, "Re-plan" on the CV workbench (`_plan_pane.html`) regenerates a
`plan_tailoring()` suggestion list from scratch — comparing the base CV
against the job posting only, never looking at the tuning directives
already in the box. The only way those suggestions reach the directives
is "Reset editor to proposed plan," which wholesale-overwrites whatever
the user hand-wrote. The directives are the real piece of work here and
shouldn't get silently discarded by re-running a plan.

Reframe this as evaluation, not (re)planning: the LLM audits the
*current* directives against the job and base CV, and proposes discrete
add/replace/remove changes the user reviews and accepts individually —
the same interaction model `app/routes/profile.py` already uses for
profile-improvement suggestions.

Interview-style gap-filling questions ("what else did you do in
position X", "have you considered a certification for Y") are a
related but separate capability — different trigger, different output
shape (a question needing an answer, not a text-diff proposal), and
answers may route to the base CV/Profile as well as the job. Out of
scope here; a follow-up spec covers it.

## 1. Evaluation input & output shape

`plan_tailoring()` (`app/ai/tailor_cv.py`) gains one input: the current
tuning-directives text. Its system prompt changes from "produce a
tailoring plan" to "audit these existing directives against the job and
propose improvements."

Each suggestion becomes a structured proposal, parallel to
`ProfileProposal` in `app/ai/refine_profile.py`:

```python
@dataclass
class DirectiveProposal:
    action: str       # "add" | "replace" | "remove"
    category: str     # "strengthen" | "trim" | "reframe" — unchanged, answers *why*
    rationale: str
    line: str | None  # new/replacement directive text; None for remove
    target: str | None  # exact existing directive line; None for add
```

`category` is orthogonal to `action`: a "strengthen" suggestion might be
a brand-new directive (`add`) or a tightened rewrite of an existing one
that's too soft (`replace`). Directives have no sections to anchor
into, unlike the profile, so `DirectiveProposal` carries no
`section`/`anchor` fields.

## 2. Shared bullet-matching primitives

The line-matching and replace/remove mutation logic is identical to
what `app/profile_apply.py` already does for profile bullets — find a
`- ` line by exact (case-insensitive) text, replace it, or delete it.
Only "add" differs: the profile is sectioned and needs anchor-finding;
directives are a flat list and always append.

- New module `app/bullet_edits.py`: `find_bullet(lines, text) -> int | None`,
  `format_bullet(text) -> str`, `replace_bullet(lines, target, text) -> bool`,
  `remove_bullet(lines, target) -> bool`.
- Refactor `app/profile_apply.py` to use these instead of its private
  `_find_bullet_line` — no behavior change, just de-duplication.
- New functions in `app/cv/instruction.py` (next to `compose_instruction`,
  the existing home for directive-text shaping):
  - `resolve_directive_proposals(proposals, tuning_directives) -> list[dict]` —
    drops `add` proposals whose line already exists verbatim, and
    `replace`/`remove` proposals whose `target` doesn't match an
    existing line.
  - `apply_directive_proposals(tuning_directives, resolved) -> str` —
    applies `remove`/`replace` via the shared primitives; `add` appends
    a new `- ` bullet at the end. Defensive like `apply_profile_proposals`:
    a `target` that's gone stale (directives hand-edited between
    evaluate and accept) is silently skipped, not an error.

## 3. Bootstrap vs. review

- **Empty directives box** (new job, nothing written yet): every
  proposal against an empty list is necessarily `add`. The plan task
  auto-applies all resolved proposals immediately via
  `apply_directive_proposals()`, same as today's zero-click seeding —
  just through the shared apply path instead of the old ad hoc
  `_seed_directives_from_plan` join (which is deleted).
- **Non-empty directives box**: resolved proposals are stored in
  `job_cv.plan` for review, replacing whatever was pending from a
  previous evaluate run (latest evaluate always wins — same as today's
  plan-replacement behavior). Zero resolved proposals means no
  suggestion form renders (`job_cv.plan` is empty either way — nothing
  to distinguish it from "not yet evaluated" without extra state).
  No persistent "no changes suggested" banner — reliably distinguishing
  that from "not yet evaluated" would need comparing
  `plan_generated_at`/`directives_edited_at`, which is more subtle than
  this feature warrants. The transient progress-log line shown during
  the evaluate run itself ("... (0 suggestion(s))") is the only signal;
  this deviates from Profile's persistent empty-state message, accepted
  as a reasonable simplification.

## 4. Routes

- `POST /jobs/{id}/cv/plan` (existing "Evaluate directives" trigger,
  `_task_cv_tailor` mode `"plan"`): internally now passes the current
  directives into `plan_tailoring()` and branches per §3 instead of
  always seeding when empty.
- New `POST /jobs/{id}/cv/plan/accept`, mirroring
  `/profile/refine/accept`: reads per-row checkboxes and hidden
  action/target/text fields, calls `apply_directive_proposals()` on the
  accepted subset, saves the merged directives via
  `q.set_job_cv_directives`, clears the pending list in `job_cv.plan`
  (accept counts as "reviewed" whether or not every row was checked),
  re-renders the plan pane.
- Remove the `reset_to_plan` branch in `cv_save_directives`
  (`app/routes/cv.py`) and the now-dead `_seed_directives_from_plan`
  helper — superseded by per-suggestion accept.

## 5. UI (`_plan_pane.html`)

- Rename "Re-plan" (both places it appears — the stale-plan banner and
  the action-button row) to **"Evaluate directives"**.
- Replace the static `<details><summary>Proposed plan (N)</summary>`
  block with an accept/reject form styled like
  `profile/_proposals.html`: each row shows the category tag and
  rationale, plus — for `replace`/`remove` — the existing directive
  struck through with an arrow to the new text, or — for `add` — just
  the new text. Checkbox defaults to checked. One "Apply" button posts
  all rows at once to `/jobs/{id}/cv/plan/accept`.
- Unchecked-but-reviewed rows are listed afterward in a small "Not
  applied — add yourself if still relevant" panel, same as Profile's
  `{% if unapplied %}` block, rather than just vanishing.
- Top pane heading ("Tailoring plan") is unchanged — it still names the
  whole scope+directives editing pane, which hasn't changed shape.
- "Reset editor to proposed plan" button is removed.

## Out of scope

- Interview-style gap-filling questions — separate spec.
- Any change to `check_guardrails` or the generate/tailor path — this
  spec only touches the plan/evaluate step and directive text.

## Testing

- `app/bullet_edits.py`: unit tests for find/format/replace/remove.
- `app/profile_apply.py`: existing tests continue to pass unchanged
  (refactor only) — confirms the extraction didn't alter behavior.
- `resolve_directive_proposals`/`apply_directive_proposals`
  (`app/cv/instruction.py`): add/replace/remove happy paths, invalid
  target ignored, duplicate add ignored, empty directives → all-add
  bootstrap.
- `plan_tailoring()` (`tests/test_tailor_cv_plan.py`): update for the
  new `tuning_directives` parameter and the `DirectiveProposal` output
  shape.
- Route tests (`tests/test_routes_cv_workbench.py`): bootstrap-vs-review
  branching for `/jobs/{id}/cv/plan`; new tests for
  `/jobs/{id}/cv/plan/accept` (accept subset, stale target no-ops
  safely, unchecked rows leave text untouched); remove tests tied to
  the deleted `reset_to_plan` path.
