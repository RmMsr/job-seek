from __future__ import annotations
import json
import openai
from app.ai.json_utils import extract_json

_SYSTEM = """You assess how well a job posting fits a candidate's profile and career stage.
Given a candidate profile and a job summary, return two independent 0.0-1.0 scores.

1. Interest — how well the role aligns with the candidate's domain/mission interests, the
   company/team's stage and culture, and where this sits on the candidate's career trajectory
   (a stretch role, a lateral move, a dead end). Score high only for a role that stands out on
   these dimensions, not merely one that doesn't clash with them — most roles that merely don't
   conflict with the profile should score in the middle or below, not high.
2. Attainability — how realistic landing this role is, given the candidate's actual experience,
   skills, and seniority against what the posting asks for. Its reasoning must name concrete
   gaps (e.g. missing years of experience, a skill the posting requires that the profile doesn't
   show) whenever the score is below 1.0, not vague hedging.

Respond with exactly: {"interest": <float>, "interest_reasoning": "<1-2 sentences>",
"attainability": <float>, "attainability_reasoning": "<1-2 sentences>"}"""


def assess_fit(
    client: openai.OpenAI,
    model: str,
    profile: str,
    summary: str,
) -> dict:
    user_content = f"## Candidate Profile\n{profile}\n\n## Job Summary\n{summary}"
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user_content[:8000]},
            ],
            temperature=0,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        data = json.loads(extract_json(resp.choices[0].message.content))
        interest = max(0.0, min(1.0, float(data.get("interest", 0.0))))
        attainability = max(0.0, min(1.0, float(data.get("attainability", 0.0))))
        return {
            "interest": interest,
            "interest_reasoning": data.get("interest_reasoning", ""),
            "attainability": attainability,
            "attainability_reasoning": data.get("attainability_reasoning", ""),
        }
    except Exception as exc:
        return {
            "interest": 0.0,
            "interest_reasoning": f"error: {exc}",
            "attainability": 0.0,
            "attainability_reasoning": f"error: {exc}",
        }
