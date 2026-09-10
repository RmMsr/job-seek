from __future__ import annotations
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import openai
from app.ai._client import complete
from app.ai.json_utils import extract_json


@dataclass(frozen=True)
class JobSummary:
    title: str = ""
    company: str = ""
    headline: str = ""
    summary: str = ""
    posted_date: str = ""


_SYSTEM = (
    "Summarize this job posting. Respond with exactly this JSON shape: "
    '{"title": "<Role - Location (remote/hybrid/onsite)>", '
    '"company": "<name of the hiring organization, or empty string if unclear>", '
    '"headline": "<one punchy sentence on the most compelling or notable detail>", '
    '"summary": "<concise markdown covering role, company, location/remote status, '
    'key requirements, compensation if mentioned, notable perks or red flags>", '
    '"posted_date": "<the date this job was published, formatted YYYY-MM-DD; resolve '
    "relative phrases such as '2 weeks ago' or 'posted last month' against the current "
    'date given at the top of the text; empty string if the posting does not state or '
    'clearly imply when it was published>", '
    '"source_link": "<a URL copied verbatim from the text below that points to the '
    'original job description, application form, or the hiring organization/job page, '
    'or empty string if none is present>"}. '
    "For title: use the role as given in the posting, or a concise generated one if "
    "unclear; include location with remote/hybrid/onsite status. Be factual and "
    "brief. No invented details. "
    "For company: the organization that would employ the hire, not a recruiting agency "
    "or the job board. Empty string if you cannot tell. "
    "For summary: keep it well-structured and easy to scan, and don't drop any of the "
    "standard fields listed above. Within that, favor concrete specifics over generic "
    "advertised-sounding phrasing (e.g. 'competitive salary', 'fast-paced environment', "
    "'collaborative team') — call out what's actually distinctive about this posting, "
    "such as unusual scope or impact, concrete technical/domain details, or notable team "
    "or organization context. "
    "For posted_date: only a date you can support from the text; never guess one. "
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


def _valid_posted_date(value: object, today: date) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    text = value.strip()
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.fromisoformat(text).date()
        except ValueError:
            return ""
    if parsed > today or parsed < today - timedelta(days=366):
        return ""
    return parsed.isoformat()


def summarize(
    client: openai.OpenAI,
    model: str,
    simplified_content: str,
    content_type: str = "job_posting",
    raw_passthrough: bool = True,
    *,
    today: date | None = None,
) -> JobSummary:
    today = today if today is not None else datetime.now(timezone.utc).date()
    # A "lead" keeps its source text verbatim as the summary only when that text
    # is a short human message worth preserving (Slack). For scraped web leads
    # (raw_passthrough=False) the lead is summarised like a posting, so the job
    # body is never a dump of page chrome.
    raw_lead = content_type == "lead" and raw_passthrough
    user_content = simplified_content[:6000]
    if not raw_lead:
        user_content = f"Today is {today.isoformat()}.\n\n{user_content}"
    content = complete(
        client,
        model,
        [
            {"role": "system", "content": _LEAD_SYSTEM if raw_lead else _SYSTEM},
            {"role": "user", "content": user_content},
        ],
        temperature=0.3,
        # This model emits a hidden chain-of-thought by default, which is slow and,
        # per A/B testing against real postings, sometimes runs long enough to exhaust
        # the response budget before ever emitting the actual summary. Disabling it
        # was faster and at least as reliable/complete for this text-transformation task.
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    try:
        data = json.loads(extract_json(content))
        if raw_lead:
            # Leads are short/vague by nature, so the retained original message
            # (already including its author, see SlackFetcher) is more useful
            # than an AI-compressed rewrite — only title/headline come from AI.
            title = ", ".join(data.get("organizations", []))
            return JobSummary(title=title, headline=data.get("headline", ""), summary=simplified_content)
        summary = data.get("summary", "")
        source_link = _valid_source_link(data.get("source_link", ""), simplified_content)
        if source_link:
            summary = f"{summary}\n\n**Original posting:** [{source_link}]({source_link})"
        return JobSummary(
            title=data.get("title", ""),
            company=data.get("company") or "",
            headline=data.get("headline", ""),
            summary=summary,
            posted_date=_valid_posted_date(data.get("posted_date"), today),
        )
    except Exception:
        return JobSummary(summary=simplified_content if raw_lead else "")
