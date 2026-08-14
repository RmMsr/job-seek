from __future__ import annotations
import logging
import httpx
import openai
from app.fetchers.base import Fetcher, RawJob
from app.fetchers.content import has_enough_content, has_enough_text, extract_text
from app.fetchers.http import HttpFetcher
from app.fetchers.links import extract_links
from app.fetchers.playwright_pool import render_html
from app.ai.detect_listing import detect_listing

logger = logging.getLogger("job_seek")

MAX_DETAIL_FETCHES = 50  # cap on new posting detail pages fetched per run
MAX_PLAYWRIGHT_FALLBACKS = 10  # cap on Playwright renders per run, bounds worst-case run time


class GenericListingFetcher:
    def __init__(
        self,
        source: dict,
        client: openai.OpenAI,
        model: str,
        known_urls: frozenset[str] = frozenset(),
    ) -> None:
        self._source = source
        self._client = client
        self._model = model
        self._known_urls = known_urls

    def fetch(self) -> list[RawJob]:
        try:
            resp = httpx.get(self._source["url"], timeout=30, follow_redirects=True)
            if resp.status_code != 200:
                logger.warning(
                    "generic_listing fetch for '%s' got HTTP %d from %s",
                    self._source["name"], resp.status_code, self._source["url"],
                )
                return []
            html = resp.text
        except Exception:
            logger.exception(
                "generic_listing fetch for '%s' failed to fetch listing page %s",
                self._source["name"], self._source["url"],
            )
            return []

        if not has_enough_content(html):
            rendered = render_html(self._source["url"])
            if rendered and has_enough_content(rendered):
                html = rendered

        links = extract_links(html, self._source["url"])
        result = detect_listing(self._client, self._model, links, self._source["url"])
        job_links = [href for href in result["job_links"] if href not in self._known_urls]

        jobs: list[RawJob] = []
        playwright_fallbacks_used = 0
        for href in job_links[:MAX_DETAIL_FETCHES]:
            page_jobs = HttpFetcher({"url": href}).fetch()
            is_thin = not page_jobs or not has_enough_text(page_jobs[0].raw_text)
            if is_thin and playwright_fallbacks_used < MAX_PLAYWRIGHT_FALLBACKS:
                playwright_fallbacks_used += 1
                rendered = render_html(href)
                if rendered and has_enough_content(rendered):
                    page_jobs = [RawJob(url=href, title="", company="", raw_text=extract_text(rendered))]
            jobs.extend(page_jobs)
        return jobs
