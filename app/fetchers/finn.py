from __future__ import annotations
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from playwright.sync_api import sync_playwright
from app.fetchers.base import RawJob, is_recent
from app.fetchers.http import HttpFetcher
from app.url_canon import canonicalize_url

MAX_AGE_DAYS = 30          # ads published before this many days ago are ignored
MAX_LISTING_PAGES = 5      # hard cap on paginated listing pages walked per run
MAX_DETAIL_FETCHES = 50    # cap on new ad detail pages fetched per run


class FinnListingFetcher:
    """Discovers individual job ad URLs from a finn.no search/listing page
    (which requires JS to render) via a headless Playwright load, walking
    pagination up to MAX_LISTING_PAGES and stopping once ads fall outside
    MAX_AGE_DAYS, then fetches each ad page as a plain static page —
    finn.no ad pages are server-rendered."""

    def __init__(self, source: dict, known_urls: frozenset[str] = frozenset()) -> None:
        self._source = source
        self._known_urls = known_urls

    def fetch(self) -> list[RawJob]:
        ads = self._discover_ads()
        new_ads = [
            (c, pub) for url, pub in ads
            if (c := canonicalize_url(url)) not in self._known_urls
        ]
        jobs: list[RawJob] = []
        for url, published_at in new_ads[:MAX_DETAIL_FETCHES]:
            for job in HttpFetcher({"url": url}).fetch():
                job.published_at = published_at
                jobs.append(job)
        return jobs

    def _discover_ads(self) -> list[tuple[str, str | None]]:
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                try:
                    page = browser.new_page()
                    seen: set[str] = set()
                    collected: list[tuple[str, str | None]] = []
                    new_within_cutoff = 0
                    for page_num in range(1, MAX_LISTING_PAGES + 1):
                        page.goto(self._page_url(page_num), wait_until="networkidle", timeout=30000)
                        cards = page.eval_on_selector_all(
                            "a[href*='/job/ad/']",
                            """els => els.map(e => {
                                const container = e.closest('article') || e.closest('section');
                                const time = container ? container.querySelector('time') : null;
                                return {href: e.href, publishedAt: time ? time.getAttribute('datetime') : null};
                            })""",
                        )
                        page_has_fresh_ad = False
                        for card in cards:
                            href = card["href"]
                            if "/job/ad/" not in href or href in seen:
                                continue
                            seen.add(href)
                            published_at = card["publishedAt"]
                            collected.append((href, published_at))
                            if is_recent(published_at, MAX_AGE_DAYS):
                                page_has_fresh_ad = True
                                if href not in self._known_urls:
                                    new_within_cutoff += 1
                        if not page_has_fresh_ad:
                            break
                        if new_within_cutoff >= MAX_DETAIL_FETCHES:
                            break
                finally:
                    browser.close()
        except Exception:
            return []

        return [(url, pub) for url, pub in collected if is_recent(pub, MAX_AGE_DAYS)]

    def _page_url(self, page_num: int) -> str:
        if page_num == 1:
            return self._source["url"]
        parts = urlsplit(self._source["url"])
        query = dict(parse_qsl(parts.query))
        query["page"] = str(page_num)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
