from __future__ import annotations
import json
import openai
from app.ai.json_utils import extract_json

_SYSTEM = (
    "Summarize this job posting. Respond with exactly this JSON shape: "
    '{"title": "<Role - Location (remote/hybrid/onsite) @ Organization>", '
    '"headline": "<one punchy sentence on the most compelling or notable detail>", '
    '"summary": "<concise markdown covering role, company, location/remote status, '
    'key requirements, compensation if mentioned, notable perks or red flags>", '
    '"source_link": "<a URL copied verbatim from the text below that points to the '
    'original job description, application form, or the hiring organization/job page, '
    'or empty string if none is present>"}. '
    "For title: use the role as given in the posting, or a concise generated one if "
    "unclear; include location with remote/hybrid/onsite status; include the "
    "organization name. Be factual and brief. No invented details. "
    "For summary: keep it well-structured and easy to scan, and don't drop any of the "
    "standard fields listed above. Within that, favor concrete specifics over generic "
    "advertised-sounding phrasing (e.g. 'competitive salary', 'fast-paced environment', "
    "'collaborative team') — call out what's actually distinctive about this posting, "
    "such as unusual scope or impact, concrete technical/domain details, or notable team "
    "or organization context. "
    "For source_link: only return a URL that appears verbatim in the text below — never "
    "construct, guess, or modify one. If several links are present, prefer the most direct "
    "application link or the original detailed posting over generic organization/social links."
)

_LEAD_SYSTEM = (
    "This message references a possible job opportunity without a full job "
    "description -- it doesn't describe one specific role in detail. Instead of "
    "forcing it into a job-posting shape, identify the organization(s) it suggests "
    "are hiring or might be hiring. Respond with exactly this JSON shape: "
    '{"organizations": ["<Organization name>", ...], '
    '"headline": "<one punchy sentence on what this lead points to>"}. '
    "List every organization the message suggests could be hiring, even if only "
    "vaguely implied. Do not invent a specific role, title, or job description "
    "that isn't actually present in the message. Be factual and brief. If no "
    "organization is identifiable, return an empty organizations list."
)


def _valid_source_link(link: str, simplified_content: str) -> str:
    if not isinstance(link, str):
        return ""
    if not link.startswith(("http://", "https://")):
        return ""
    if link not in simplified_content:
        return ""
    return link


def summarize(
    client: openai.OpenAI,
    model: str,
    simplified_content: str,
    content_type: str = "job_posting",
) -> tuple[str, str, str]:
    is_lead = content_type == "lead"
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _LEAD_SYSTEM if is_lead else _SYSTEM},
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
        if is_lead:
            # Leads are short/vague by nature, so the retained original message
            # (already including its author, see SlackFetcher) is more useful
            # than an AI-compressed rewrite — only title/headline come from AI.
            title = ", ".join(data.get("organizations", []))
            return title, data.get("headline", ""), simplified_content
        title = data.get("title", "")
        summary = data.get("summary", "")
        source_link = _valid_source_link(data.get("source_link", ""), simplified_content)
        if source_link:
            summary = f"{summary}\n\n**Original posting:** [{source_link}]({source_link})"
        return title, data.get("headline", ""), summary
    except Exception:
        return "", "", simplified_content if is_lead else ""
