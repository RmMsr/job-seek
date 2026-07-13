from __future__ import annotations
import httpx
from bs4 import BeautifulSoup
from app.fetchers.base import Fetcher, RawJob


class HttpFetcher:
    def __init__(self, source: dict) -> None:
        self._source = source

    def fetch(self) -> list[RawJob]:
        try:
            resp = httpx.get(self._source["url"], timeout=30, follow_redirects=True)
            if resp.status_code != 200:
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
