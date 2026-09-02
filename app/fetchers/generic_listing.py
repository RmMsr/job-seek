from __future__ import annotations
import logging
import time
import openai
from app.fetchers.base import RawJob
from app.fetchers.content import (
    MIN_CONTENT_LENGTH, MIN_ARTICLE_LENGTH, has_enough_content, extract_readable_text, FetchError,
)
from app.fetchers.http import HttpFetcher
from app.fetchers.listing_detect import detect_listing_page, ListingDetection, MIN_JOB_LINKS
from app.fetchers.pagination import next_page_url
from app.fetchers.playwright_pool import render_html
from app.url_canon import canonicalize_url

logger = logging.getLogger("job_seek")

MAX_LISTING_PAGES = 5         # hard cap on paginated listing pages walked per run
MAX_DETAIL_FETCHES = 50       # cap on new posting detail pages fetched per run
MAX_PLAYWRIGHT_FALLBACKS = 10  # cap on detail-page Playwright renders per run
PAGE_FETCH_DELAY_SECONDS = 2.0  # pause before each page-2+ request; LinkedIn's guest
                                # search endpoint serves an empty 200 when hit too fast


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

        links = self._walk_pages(detection)

        job_links = [c for c in links if c not in self._known_urls][:MAX_DETAIL_FETCHES]

        jobs: list[RawJob] = []
        playwright_fallbacks_used = 0
        for href in job_links:
            page_jobs = HttpFetcher({"url": href}).fetch()
            raw_text = page_jobs[0].raw_text if page_jobs else ""
            is_thin = len(raw_text.strip()) < MIN_ARTICLE_LENGTH
            if is_thin and playwright_fallbacks_used < MAX_PLAYWRIGHT_FALLBACKS:
                playwright_fallbacks_used += 1
                rendered = render_html(href)
                if rendered and has_enough_content(rendered):
                    rendered_text = extract_readable_text(rendered)
                    if len(rendered_text.strip()) >= len(raw_text.strip()) + MIN_CONTENT_LENGTH:
                        page_jobs = [RawJob(url=href, title="", company="", raw_text=rendered_text)]
            jobs.extend(page_jobs)
        return jobs

    def _walk_pages(self, page1: ListingDetection) -> list[str]:
        """Collect canonical job links across page 1 and up to
        MAX_LISTING_PAGES-1 further pages, order-preserving and deduped."""
        seen: set[str] = set()
        ordered: list[str] = []

        def add(canon: list[str]) -> int:
            """Append not-yet-seen links; return how many of those aren't known."""
            new_unknown = 0
            for c in canon:
                if c in seen:
                    continue
                seen.add(c)
                ordered.append(c)
                if c not in self._known_urls:
                    new_unknown += 1
            return new_unknown

        page1_links = [canonicalize_url(h) for h in page1.job_links]
        page1_count = len(page1_links)
        add(page1_links)
        if page1_count < MIN_JOB_LINKS:
            return ordered

        page_url = self._source["url"]
        for _ in range(2, MAX_LISTING_PAGES + 1):
            next_url = next_page_url(page_url, page1_count)  # stride fixed at page1_count
            if next_url is None:
                break
            detection = self._fetch_listing_page(next_url)
            if detection is None:
                break

            page_links = [canonicalize_url(h) for h in detection.job_links]
            if all(c in seen for c in page_links):
                break  # site ignores the param and keeps serving page 1

            new_unknown = add(page_links)
            if len(page_links) < page1_count:
                break  # a partial page is the last page (its links are kept)
            if new_unknown == 0:
                break  # whole page already in known_urls
            if sum(c not in self._known_urls for c in ordered) >= MAX_DETAIL_FETCHES:
                break
            page_url = next_url

        return ordered

    def _fetch_listing_page(self, url: str) -> ListingDetection | None:
        """``detect_listing_page(url)`` with a fixed pre-request delay and one
        retry when the page comes back empty — a ``FetchError``, not a listing,
        or no job links. LinkedIn's guest search endpoint intermittently answers
        a rapid request with an empty 200 that a later retry serves in full.
        Returns ``None`` once both attempts come up empty (stop the walk)."""
        for attempt in (1, 2):
            time.sleep(PAGE_FETCH_DELAY_SECONDS)
            try:
                detection = detect_listing_page(self._client, self._model, url)
            except FetchError as exc:
                logger.warning("generic_listing: pagination page %s failed (try %d): %s", url, attempt, exc)
                continue
            except Exception:
                logger.exception("generic_listing: pagination detection failed at %s", url)
                return None
            if detection.is_listing and detection.job_links:
                return detection
            logger.info("generic_listing: pagination page %s came back empty (try %d)", url, attempt)
        return None
