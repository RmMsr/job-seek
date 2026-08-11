from __future__ import annotations
import json
import openai
from app.ai.json_utils import extract_json

_SYSTEM = (
    "You look at a list of links extracted from a web page and decide whether the "
    "page is a listing of multiple individual job postings (e.g. a careers page or "
    "job board search results), as opposed to a single job posting, an article, or "
    "an unrelated page. Each link is given as \"<url> — <anchor text>\". "
    "If it is a listing, identify which links point to individual job postings "
    "(not navigation, filters, pagination, login, or unrelated content). "
    "Respond with exactly: "
    "{\"is_listing\": <true|false>, \"job_links\": [\"<url>\", ...]}"
)


def detect_listing(
    client: openai.OpenAI,
    model: str,
    links: list[tuple[str, str]],
    page_url: str,
) -> dict:
    valid_hrefs = {href for href, _ in links}
    link_lines = "\n".join(f"{href} — {text}" for href, text in links)
    user_message = f"Page: {page_url}\n\nLinks:\n{link_lines}"
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user_message[:8000]},
            ],
            temperature=0,
            max_tokens=2000,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        data = json.loads(extract_json(resp.choices[0].message.content))
        is_listing = bool(data.get("is_listing", False))
        job_links = [href for href in data.get("job_links", []) if href in valid_hrefs]
        return {"is_listing": is_listing, "job_links": job_links}
    except Exception:
        return {"is_listing": False, "job_links": []}
