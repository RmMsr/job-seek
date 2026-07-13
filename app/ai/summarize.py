from __future__ import annotations
import openai

_SYSTEM = (
    "Summarize this job posting as concise markdown. Cover: role title, company, "
    "location/remote status, key requirements, compensation if mentioned, notable "
    "perks or red flags. Be factual and brief. No invented details."
)


def summarize(client: openai.OpenAI, model: str, simplified_content: str) -> str:
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": simplified_content[:6000]},
        ],
        temperature=0.3,
    )
    return resp.choices[0].message.content.strip()
