from __future__ import annotations
import json
import openai
from app.ai._client import complete
from app.ai.json_utils import extract_json

_SYSTEM = (
    "You generate a short, memorable name for a job-listing source, given its "
    "domain and page title/heading. Respond with exactly this JSON shape: "
    '{"name": "<provider/filter>"}. '
    "Format: lowercase \"provider/filter\" — provider is the short, recognizable "
    "site or company name (not the full domain, drop \"www.\" and the TLD), and "
    "filter is a brief hyphenated slug capturing what's specific about this "
    "particular page (e.g. role, location, or search terms) drawn from the title. "
    "Keep both parts short — a few words at most. Drop generic filler words like "
    "'jobs', 'careers', 'search', 'in', 'the'. "
    'Example: domain "www.glassdoor.com", title "Frontend Developer Jobs in Oslo, '
    'Norway | Glassdoor" -> {"name": "glassdoor/frontend-oslo"}. '
    'If the title gives no useful filter detail, omit the filter (just "provider").'
)


def generate_source_name(client: openai.OpenAI, model: str, domain: str, page_title: str) -> str | None:
    content = complete(
        client,
        model,
        [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": f"Domain: {domain}\nTitle: {page_title}"[:2000]},
        ],
        temperature=0,
        max_tokens=60,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    try:
        data = json.loads(extract_json(content))
        name = data.get("name", "").strip()
        return name or None
    except Exception:
        return None
