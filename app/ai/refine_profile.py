from __future__ import annotations
import json
from dataclasses import dataclass
import openai
from app.ai._client import complete
from app.ai.json_utils import extract_json

_SYSTEM = """You propose improvements to a candidate's job-search profile based on feedback
notes left when accepting, rejecting, or trashing specific jobs.

The profile is a markdown document organized into "##" sections (e.g. "Technologies",
"Ideal Employment"). Each bullet point ("- ...") under a section is a discrete fact,
skill, or preference.

Each feedback note carries the job's outcome:
- "[ACCEPTED] <note>": the job was a good fit — a reason to reinforce a matching trait,
  or to note something the profile currently discourages that shouldn't be.
- "[REJECTED] <note>": the job was a mismatch — a reason to add or strengthen a
  disqualifying trait, or to soften a requirement stated too strictly.
- "[TRASH] <note>": trashed jobs are often removed for administrative reasons
  (duplicate, expired, fetch error) with no bearing on fit — ignore these unless the
  note itself clearly expresses a genuine preference.

A "##" section can contain more than one distinct bullet list, each introduced by a
plain text line above it (e.g. under "## Geographic fit", a "Languages" line introduces
one list of language bullets, separate from other facts in that section). When proposing
an "add" into a section that has more than one such list, include an "anchor": the exact
text of that plain line immediately above the target list, so the new bullet lands in the
right list instead of at the end of the whole section. Omit "anchor" (or leave it null)
when the section has only one list, or you're adding a new list of your own.

Given the current profile and feedback notes, propose changes as a JSON array. Each item:
{"section": "<## heading text, existing or new>", "action": "add|replace|remove",
"text": "<new bullet text, for add/replace>", "target": "<exact existing bullet text,
for replace/remove>", "anchor": "<exact text of the line introducing the target list,
for add only, when needed to disambiguate>"}.
Only propose changes to bullet points, and only propose changes clearly supported by
the feedback. Return [] if no changes needed.
Respond with a valid JSON array only."""


@dataclass
class ProfileProposal:
    section: str
    action: str
    text: str | None
    target: str | None
    anchor: str | None = None


def _strip_bullet_prefix(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if stripped.startswith("- "):
        return stripped[2:].strip()
    if stripped.startswith("-"):
        return stripped[1:].strip()
    return stripped


def propose_profile_changes(
    client: openai.OpenAI,
    model: str,
    profile_text: str,
    feedback_notes: list[dict],
) -> list[ProfileProposal]:
    notes_text = "\n".join(f"[{n['status'].upper()}] {n['feedback_note']}" for n in feedback_notes)
    user_content = f"## Current Profile\n{profile_text}\n\n## Job Feedback Notes\n{notes_text}"
    content = complete(
        client,
        model,
        [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": user_content},
        ],
        temperature=0,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    try:
        items = json.loads(extract_json(content))
        proposals = []
        for item in items:
            action = item.get("action")
            if action not in ("add", "replace", "remove"):
                continue
            section = item.get("section")
            if not section:
                continue
            text = _strip_bullet_prefix(item.get("text"))
            target = _strip_bullet_prefix(item.get("target"))
            anchor = item.get("anchor")
            anchor = anchor.strip() if isinstance(anchor, str) and anchor.strip() else None
            if action == "add" and not text:
                continue
            if action == "remove" and not target:
                continue
            if action == "replace" and not (text and target):
                continue
            proposals.append(
                ProfileProposal(section=section, action=action, text=text, target=target, anchor=anchor)
            )
        return proposals
    except Exception:
        return []
