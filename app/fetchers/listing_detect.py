from __future__ import annotations
import logging
from dataclasses import dataclass
import openai
from app.fetchers.content import fetch_url_html, has_enough_content
from app.fetchers.links import extract_links
from app.fetchers.playwright_pool import render_html
from app.ai.detect_listing import detect_listing

logger = logging.getLogger("job_seek")

MIN_JOB_LINKS = 2  # a raw-HTML "listing" with fewer links than this is worth a render


@dataclass
class ListingDetection:
    html: str
    is_listing: bool
    job_links: list[str]
    rendered: bool


def _detect(client: openai.OpenAI, model: str, html: str, url: str) -> dict:
    return detect_listing(client, model, extract_links(html, url), url)


def detect_listing_page(client: openai.OpenAI, model: str, url: str) -> ListingDetection:
    """Fetch ``url`` and classify it as a job listing (or not), returning the
    individual job-posting links found.

    Escalates to one headless-browser render when the raw HTML is thin, or when
    the raw-HTML detection is not a listing or has fewer than ``MIN_JOB_LINKS``
    links — many careers pages are JS-rendered and their raw HTML carries enough
    boilerplate to look non-thin while containing no job links. The rendered
    result is kept only if it yields strictly more job links.

    Raises ``FetchError`` if the initial HTTP fetch fails.
    """
    html = fetch_url_html(url)
    render_tried = False

    if not has_enough_content(html):
        rendered = render_html(url)
        render_tried = True
        if rendered and has_enough_content(rendered):
            r = _detect(client, model, rendered, url)
            return ListingDetection(rendered, r["is_listing"], r["job_links"], True)

    result = _detect(client, model, html, url)

    if not result["is_listing"] or len(result["job_links"]) < MIN_JOB_LINKS:
        rendered = None if render_tried else render_html(url)
        if rendered and has_enough_content(rendered):
            alt = _detect(client, model, rendered, url)
            if len(alt["job_links"]) > len(result["job_links"]):
                return ListingDetection(rendered, alt["is_listing"], alt["job_links"], True)

    return ListingDetection(html, result["is_listing"], result["job_links"], False)
