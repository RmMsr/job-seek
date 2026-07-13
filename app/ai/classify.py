from __future__ import annotations
import json
import openai

_SYSTEM = (
    "You classify text as one of: job_posting, lead, irrelevant, error. "
    "job_posting: contains a full job description. "
    "lead: references a job opportunity without full details. "
    "irrelevant: not job-related. "
    "error: garbled, empty, or access-denied content. "
    "Respond with exactly: {\"type\": \"<label>\", \"reason\": \"<one sentence>\"}"
)

_SLACK_HINT = (
    " Note: this content comes from a Slack community channel where most posts "
    "are job postings or leads to jobs. Default to 'lead' when uncertain."
)


def classify(
    client: openai.OpenAI,
    model: str,
    simplified_content: str,
    is_slack: bool = False,
) -> tuple[str, str]:
    system = _SYSTEM + (_SLACK_HINT if is_slack else "")
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": simplified_content[:4000]},
            ],
            temperature=0,
        )
        data = json.loads(resp.choices[0].message.content)
        content_type = data.get("type", "error")
        if content_type not in ("job_posting", "lead", "irrelevant", "error"):
            content_type = "error"
        return content_type, data.get("reason", "")
    except Exception as exc:
        return "error", str(exc)
