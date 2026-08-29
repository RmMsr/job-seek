from __future__ import annotations
import json
import logging
import openai
from app.ai.json_utils import extract_json

logger = logging.getLogger("job_seek")

_SYSTEM = (
    "You look at a numbered list of links extracted from a web page and decide "
    "whether the page is a listing of multiple individual job postings (e.g. a "
    "careers page or job board search results), as opposed to a single job "
    "posting, an article, or an unrelated page. Each line is "
    "\"<n>. <anchor text> [<url>]\". "
    "If it is a listing, identify which links point to individual job postings "
    "(not navigation, filters, pagination, login, or unrelated content). "
    "A link to an individual job posting names a specific role at a specific "
    "employer. Be wary of links that only name a role and a location (e.g. "
    "\"Machine Learning Engineer Jobs in Oslo\", or a URL like "
    "\"/jobs/ai-engineer-jobs-in-oslo\") with no employer attached — these are "
    "category or search-filter pages that themselves list postings for that "
    "role, not a specific posting, even though they look job-like. A big block "
    "of near-identical links, one per job title or seniority level, is a strong "
    "sign of a role/category filter list rather than individual postings. "
    "Respond with exactly: "
    "{\"is_listing\": <true|false>, \"job_ids\": [<n>, ...]} "
    "where each <n> is the leading number of a link that points to an "
    "individual job posting."
)


def detect_listing(
    client: openai.OpenAI,
    model: str,
    links: list[tuple[str, str]],
    page_url: str,
) -> dict:
    link_lines = "\n".join(f"{i}. {text} [{href}]" for i, (href, text) in enumerate(links))
    user_message = f"Page: {page_url}\n\nLinks:\n{link_lines}"
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user_message[:16000]},
            ],
            temperature=0,
            max_tokens=4000,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
    except Exception:
        logger.warning("detect_listing: LLM call failed for %s", page_url, exc_info=True)
        return {"is_listing": False, "job_links": []}

    content = resp.choices[0].message.content
    try:
        data = json.loads(extract_json(content))
    except (ValueError, TypeError):
        logger.warning("detect_listing: unparseable response for %s: %r", page_url, content)
        return {"is_listing": False, "job_links": []}

    is_listing = bool(data.get("is_listing", False))
    job_links = [
        links[n][0]
        for n in data.get("job_ids", [])
        if isinstance(n, int) and not isinstance(n, bool) and 0 <= n < len(links)
    ]
    return {"is_listing": is_listing, "job_links": job_links}
