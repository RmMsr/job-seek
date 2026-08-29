from __future__ import annotations
import logging
import openai
from app.fetchers.base import RawJob
from app.fetchers.content import has_enough_content, has_enough_text, extract_text, FetchError
from app.fetchers.http import HttpFetcher
from app.fetchers.listing_detect import detect_listing_page
from app.fetchers.playwright_pool import render_html
from app.url_canon import canonicalize_url

logger = logging.getLogger("job_seek")

MAX_DETAIL_FETCHES = 50        # cap on new posting detail pages fetched per run
MAX_PLAYWRIGHT_FALLBACKS = 10  # cap on detail-page Playwright renders per run


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
            detection = detect_listing_page(self._client, self._model, self._source["url"])
        except FetchError as exc:
            logger.warning(
                "generic_listing: could not fetch listing page %s for '%s': %s",
                self._source["url"], self._source["name"], exc,
            )
            return []
        except Exception:
            logger.exception(
                "generic_listing: detection failed for '%s' (%s)",
                self._source["name"], self._source["url"],
            )
            return []

        job_links = [
            c for h in detection.job_links
            if (c := canonicalize_url(h)) not in self._known_urls
        ]

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
