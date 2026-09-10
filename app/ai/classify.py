from __future__ import annotations
import json
import openai
from app.ai._client import complete
from app.ai.json_utils import extract_json

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
    content = complete(
        client,
        model,
        [
            {"role": "system", "content": system},
            {"role": "user", "content": simplified_content[:4000]},
        ],
        temperature=0,
        max_tokens=120,  # response is a short JSON label + one-sentence reason
        # This model emits a hidden chain-of-thought (reasoning_content) by default,
        # which dominates latency for a task this simple. Disabling it via the chat
        # template is the only thing that actually suppresses it (a system-prompt
        # instruction not to reason is ignored by the model).
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    try:
        data = json.loads(extract_json(content))
        content_type = data.get("type", "error")
        if content_type not in ("job_posting", "lead", "irrelevant", "error"):
            content_type = "error"
        return content_type, data.get("reason", "")
    except Exception as exc:
        return "error", str(exc)
