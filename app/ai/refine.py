from __future__ import annotations
import json
from dataclasses import dataclass
import openai
from app.ai.json_utils import extract_json

_SYSTEM = """You refine job search criteria based on feedback about how a scenario's gate scored
specific jobs.

Each criterion has a weight describing how it affects a job's desirability:
- "must": a hard requirement — jobs lacking this should be rejected outright.
- "prefer": a positive trait — its presence makes a job more desirable, but its absence isn't disqualifying.
- "avoid": a negative trait / red flag — its presence makes a job less desirable or should disqualify it.
A criterion about wanting more of something good (higher pay, better title, more autonomy, etc.) is "must" or "prefer", never "avoid" — "avoid" is only for traits that make a job worse.

Each feedback note says which direction a specific job's score should have moved:
- "[SHOULD SCORE HIGHER] <note>": usually supports loosening a "must" that's too strict, adding
  or strengthening a "prefer" for a trait the job has, or narrowing an "avoid" that's wrongly
  triggering on it.
- "[SHOULD SCORE LOWER] <note>": usually supports adding a "must" or "avoid" criterion for
  whatever the job is missing or has that current criteria don't catch, or narrowing a "prefer"
  that's too generously matching it.

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
        f"- [{'SHOULD SCORE HIGHER' if n['direction'] == 'higher' else 'SHOULD SCORE LOWER'}] {n['note']}"
        for n in feedback_notes
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
