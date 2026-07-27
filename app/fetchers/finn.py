from __future__ import annotations
from playwright.sync_api import sync_playwright
from app.fetchers.base import RawJob
from app.fetchers.http import HttpFetcher

_MAX_ADS = 20


class FinnListingFetcher:
    """Discovers individual job ad URLs from a finn.no search/listing page
    (which requires JS to render) via a headless Playwright load, then fetches
    each ad page as a plain static page — finn.no ad pages are server-rendered."""

    def __init__(self, source: dict) -> None:
        self._source = source

    def fetch(self) -> list[RawJob]:
        ad_urls = self._discover_ad_urls()
        jobs: list[RawJob] = []
        for url in ad_urls[:_MAX_ADS]:
            jobs.extend(HttpFetcher({"url": url}).fetch())
        return jobs

    def _discover_ad_urls(self) -> list[str]:
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                try:
                    page = browser.new_page()
                    page.goto(self._source["url"], wait_until="networkidle", timeout=30000)
                    hrefs = page.eval_on_selector_all(
                        "a[href*='/job/ad/']",
                        "els => els.map(e => e.href)",
                    )
                finally:
                    browser.close()
        except Exception:
            return []

        seen: list[str] = []
        for href in hrefs:
            if "/job/ad/" in href and href not in seen:
                seen.append(href)
        return seen
