from __future__ import annotations
import json
from dataclasses import dataclass
import openai
from app.ai.json_utils import extract_json

_SYSTEM = """You refine job search criteria based on feedback from rejected/accepted jobs.

Each criterion has a weight describing how it affects a job's desirability:
- "must": a hard requirement — jobs lacking this should be rejected outright.
- "prefer": a positive trait — its presence makes a job more desirable, but its absence isn't disqualifying.
- "avoid": a negative trait / red flag — its presence makes a job less desirable or should disqualify it.
A criterion about wanting more of something good (higher pay, better title, more autonomy, etc.) is "must" or "prefer", never "avoid" — "avoid" is only for traits that make a job worse.

Each feedback note is tagged with the job's outcome:
- "[REJECTED] <note>": explains why this job was turned down — usually supports adding an "avoid" criterion for the trait described, or a "must"/"prefer" criterion for something the job was missing.
- "[ACCEPTED] <note>": explains what stood out about a job the user accepted — usually supports a "must"/"prefer" criterion for the trait described.
The same underlying trait can show up from both sides — a REJECTED note about lacking something and an ACCEPTED note praising that same thing are reinforcing signals about one criterion, not unrelated or contradictory ones. Don't propose both adding and removing criteria about the same trait from the same feedback.

Given a scenario, existing criteria, and feedback notes, propose changes as a JSON array.
Each item: {"text": "<criterion>", "weight": "must|prefer|avoid", "action": "add|remove"}.
Only propose changes clearly supported by the feedback. Return [] if no changes needed.
Respond with a valid JSON array only."""


@dataclass
class CriterionProposal:
    text: str
    weight: str
    action: str


def propose_criteria(
    client: openai.OpenAI,
    model: str,
    scenario: dict,
    existing_criteria: list[dict],
    feedback_notes: list[dict],
) -> list[CriterionProposal]:
    existing_text = "\n".join(f"[{c['weight'].upper()}] {c['text']}" for c in existing_criteria)
    notes_text = "\n".join(
        f"- [{n['status'].upper()}] {n['feedback_note']}" for n in feedback_notes
    )
    user_content = (
        f"## Scenario: {scenario['name']}\n{scenario.get('description', '')}\n\n"
        f"## Existing Criteria\n{existing_text}\n\n"
        f"## Recent Feedback Notes\n{notes_text}"
    )
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user_content},
            ],
            temperature=0,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        items = json.loads(extract_json(resp.choices[0].message.content))
        proposals = []
        for item in items:
            if item.get("weight") not in ("must", "prefer", "avoid"):
                continue
            if item.get("action") not in ("add", "remove"):
                continue
            proposals.append(
                CriterionProposal(
                    text=item["text"],
                    weight=item["weight"],
                    action=item["action"],
                )
            )
        return proposals
    except Exception:
        return []


def match_removal_target(proposal_text: str, existing_criteria: list[dict]) -> int | None:
    target = proposal_text.strip().casefold()
    for criterion in existing_criteria:
        if criterion["text"].strip().casefold() == target:
            return criterion["id"]
    return None
