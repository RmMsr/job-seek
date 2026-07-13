from __future__ import annotations
import json
import openai

_SYSTEM = """You evaluate job fit. Given a candidate profile, a search scenario with criteria,
and a job summary, return a relevance score from 0.0 to 1.0 and a brief reasoning.

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
        )
        data = json.loads(resp.choices[0].message.content)
        score = max(0.0, min(1.0, float(data.get("score", 0.0))))
        return score, data.get("reasoning", "")
    except Exception as exc:
        return 0.0, f"error: {exc}"
