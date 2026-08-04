from __future__ import annotations
import json
import openai
from app.ai.json_utils import extract_json

_SYSTEM = """You evaluate job fit. Given a candidate profile, a search scenario, and a job summary,
return a relevance score from 0.0 to 1.0 and a brief reasoning.

A high score requires two largely independent things to both be true: the job genuinely fits the
scenario's theme (its name and description define what this search is fundamentally about), and
the job is a genuinely good fit for this specific candidate's profile — their actual background,
skill level, and stated constraints. A job that nails the scenario's theme but is a poor fit for
this candidate (wrong seniority, missing core skills the role clearly requires, a mismatch the
profile rules out) should not score high, and neither should a job that suits the candidate well
but doesn't fit the scenario's theme at all.

Treat the criteria list as additional guidance that refines the score within that theme, not a
substitute for it: 'must' = deal-breaker if missing, 'prefer' = nice to have, 'avoid' = negative
signal. Missing a 'prefer' criterion shouldn't sink an otherwise strong match; missing a 'must'
criterion should.

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
