from __future__ import annotations
import json
from dataclasses import dataclass
import openai
from app.ai.json_utils import extract_json

_SYSTEM = """You refine job search criteria based on feedback from rejected/accepted jobs.
Given a scenario, existing criteria, and feedback notes, propose changes as a JSON array.
Each item: {"text": "<criterion>", "weight": "must|prefer|avoid", "action": "add|remove"}.
Only propose changes supported by the feedback. Return [] if no changes needed.
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
    feedback_notes: list[str],
) -> list[CriterionProposal]:
    existing_text = "\n".join(f"[{c['weight'].upper()}] {c['text']}" for c in existing_criteria)
    notes_text = "\n".join(f"- {n}" for n in feedback_notes)
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
            temperature=0.2,
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
