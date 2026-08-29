from __future__ import annotations
import logging
from urllib.parse import urlsplit
import httpx
from bs4 import BeautifulSoup
from app.fetchers.base import Fetcher, RawJob

logger = logging.getLogger("job_seek")

_RATE_LIMIT_STATUS = {429, 999}  # 999 = LinkedIn bot-block


class HttpFetcher:
    def __init__(self, source: dict) -> None:
        self._source = source

    def fetch(self) -> list[RawJob]:
        try:
            resp = httpx.get(self._source["url"], timeout=30, follow_redirects=True)
            if resp.status_code != 200:
                if resp.status_code in _RATE_LIMIT_STATUS:
                    logger.warning(
                        "HTTP %s fetching %s from %s — rate-limited or bot-blocked",
                        resp.status_code, self._source["url"],
                        urlsplit(self._source["url"]).netloc,
                    )
                return []
            return self._parse(resp.text)
        except Exception:
            return []

    def _parse(self, html: str) -> list[RawJob]:
        soup = BeautifulSoup(html, "html.parser")
        raw_text = soup.get_text(separator="\n")
        if not raw_text.strip():
            return []
        return [
            RawJob(
                url=self._source["url"],
                title="",
                company="",
                raw_text=raw_text,
            )
        ]
