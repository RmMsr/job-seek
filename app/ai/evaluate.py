from __future__ import annotations
import json
import openai
from app.ai.json_utils import extract_json

_SYSTEM = """You evaluate job fit. Given a candidate profile, a search scenario, and a job summary,
return a relevance score from 0.0 to 1.0 and a brief reasoning.

The criteria list is what defines fit — score against it directly. The scenario's name and
description are background context for interpreting the criteria, not independent requirements:
a job can score well even if its wording doesn't overlap with the scenario's name or description,
as long as it satisfies the criteria. Only let the name/description narrow your reading of a
criterion when that criterion is genuinely ambiguous without it.

Criteria weights: 'must' = deal-breaker if missing, 'prefer' = nice to have, 'avoid' = negative signal.

Respond with exactly: {"score": <float>, "reasoning": "<2-3 sentences>"}"""


def evaluate(
    client: openai.OpenAI,
    model: str,
    profile: str,
    scenario: dict,
    criteria: list[dict],
    summary: str,
) -> tuple[float, str]:
    criteria_text = "\n".join(
        f"[{c['weight'].upper()}] {c['text']}" for c in criteria
    )
    user_content = (
        f"## Candidate Profile\n{profile}\n\n"
        f"## Search Scenario: {scenario['name']}\n{scenario.get('description', '')}\n\n"
        f"## Criteria\n{criteria_text}\n\n"
        f"## Job Summary\n{summary}"
    )
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user_content[:8000]},
            ],
            temperature=0,
            # This model emits a hidden chain-of-thought by default, which is slow and,
            # per A/B testing against real postings, sometimes runs long enough to exhaust
            # the response budget before ever emitting a score. Disabling it was faster
            # and at least as reliable/accurate for this text-transformation task.
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        data = json.loads(extract_json(resp.choices[0].message.content))
        score = max(0.0, min(1.0, float(data.get("score", 0.0))))
        return score, data.get("reasoning", "")
    except Exception as exc:
        return 0.0, f"error: {exc}"
