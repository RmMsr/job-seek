from __future__ import annotations
import json
import openai
from app.ai.json_utils import extract_json

_SYSTEM = """You evaluate job fit. Given a candidate profile, a search scenario, and a job summary,
return a relevance score from 0.0 to 1.0 and a brief reasoning.

Weigh these signals in priority order, each one narrowing the one before it:

1. Scenario name — the single strongest signal of what this search is about. A job that doesn't
   fit the name's theme at all should score low no matter what else matches.
2. Scenario description — elaborates and refines the name's theme. Use it to interpret borderline
   cases, not to override a job that clearly does or doesn't match the name.
3. 'must' criteria and the candidate's profile — both act as hard qualifiers, not fine-tuning: a
   job missing a 'must' criterion, or one this candidate is clearly unqualified for or a poor
   personal fit for (wrong seniority, missing core skills the role clearly requires, a mismatch
   the profile rules out), should score low even if it fits the scenario's theme well.
4. 'prefer' / 'avoid' criteria — fine-tune the score within everything above. A missing 'prefer'
   or a triggered 'avoid' should nudge the score, not sink or save it on their own.

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
