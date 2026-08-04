from __future__ import annotations
import json
import openai
from app.ai.json_utils import extract_json

_SYSTEM = """You evaluate job fit. Given a candidate profile, a search scenario, and a job summary,
return a relevance score from 0.0 to 1.0 and a brief reasoning.

The scenario's name and description define what this search is fundamentally about — a job must
genuinely align with that core theme to score high, regardless of how well it satisfies individual
criteria. Treat the criteria list as additional guidance that refines the score within that theme,
not a substitute for it: 'must' = deal-breaker if missing, 'prefer' = nice to have, 'avoid' = negative
signal. A job that fits the scenario's theme well but lacks a 'prefer' criterion should still score
reasonably; a job that doesn't fit the scenario's theme at all should score low even if it happens
to satisfy several criteria.

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
