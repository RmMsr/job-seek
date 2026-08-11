from __future__ import annotations
import httpx
import openai
from app.fetchers.base import Fetcher, RawJob
from app.fetchers.http import HttpFetcher
from app.fetchers.links import extract_links
from app.ai.detect_listing import detect_listing

MAX_DETAIL_FETCHES = 50  # cap on new posting detail pages fetched per run


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
                return []
            html = resp.text
        except Exception:
            return []

        links = extract_links(html, self._source["url"])
        result = detect_listing(self._client, self._model, links, self._source["url"])
        job_links = [href for href in result["job_links"] if href not in self._known_urls]

        jobs: list[RawJob] = []
        for href in job_links[:MAX_DETAIL_FETCHES]:
            jobs.extend(HttpFetcher({"url": href}).fetch())
        return jobs
