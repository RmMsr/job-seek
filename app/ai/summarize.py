from __future__ import annotations
import json
import openai
from app.ai.json_utils import extract_json

_SYSTEM = (
    "Summarize this job posting. Respond with exactly this JSON shape: "
    '{"title": "<Role - Location (remote/hybrid/onsite) @ Organization>", '
    '"headline": "<one punchy sentence on the most compelling or notable detail>", '
    '"summary": "<concise markdown covering role, company, location/remote status, '
    'key requirements, compensation if mentioned, notable perks or red flags>"}. '
    "For title: use the role as given in the posting, or a concise generated one if "
    "unclear; include location with remote/hybrid/onsite status; include the "
    "organization name. Be factual and brief. No invented details."
)


def summarize(client: openai.OpenAI, model: str, simplified_content: str) -> tuple[str, str, str]:
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": simplified_content[:6000]},
            ],
            temperature=0.3,
            # This model emits a hidden chain-of-thought by default, which is slow and,
            # per A/B testing against real postings, sometimes runs long enough to exhaust
            # the response budget before ever emitting the actual summary. Disabling it
            # was faster and at least as reliable/complete for this text-transformation task.
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        data = json.loads(extract_json(resp.choices[0].message.content))
        return data.get("title", ""), data.get("headline", ""), data.get("summary", "")
    except Exception:
        return "", "", ""
