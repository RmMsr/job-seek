from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup

from app.fetchers.base import RawJob
from app.fetchers.content import FetchError
from app.url_canon import canonicalize_url

logger = logging.getLogger("job_seek")

MAX_RESULTS = 50
_HTTP_TIMEOUT = 30

_CONFIG_PATTERNS = {
    "app_id": re.compile(r'algoliaApplicationId:"([^"]+)"'),
    "api_key": re.compile(r'algoliaApiKey:"([^"]+)"'),
    "index": re.compile(r'algoliaJobsIndex:"([^"]+)"'),
}
_REFINEMENT_RE = re.compile(r"^refinementList\[([^\]]+)\]\[\d+\]$")


@dataclass(frozen=True)
class AlgoliaConfig:
    app_id: str
    api_key: str
    index: str


def parse_algolia_config(html: str) -> AlgoliaConfig:
    values: dict[str, str] = {}
    for field, pattern in _CONFIG_PATTERNS.items():
        m = pattern.search(html)
        if m is None:
            raise FetchError(f"eawork: {field} not found in page config")
        values[field] = m.group(1)
    return AlgoliaConfig(**values)


def refinements_to_facet_filters(url: str) -> list[list[str]]:
    groups: dict[str, list[str]] = {}
    for name, value in parse_qsl(urlsplit(url).query, keep_blank_values=False):
        m = _REFINEMENT_RE.match(name)
        if m is None:
            continue
        facet = m.group(1)
        groups.setdefault(facet, []).append(f"{facet}:{value}")
    return list(groups.values())


def _text(html_or_str: str) -> str:
    return BeautifulSoup(html_or_str or "", "html.parser").get_text(" ", strip=True)


def _joined(hit: dict, *keys: str) -> str:
    out: list[str] = []
    for key in keys:
        for v in hit.get(key) or []:
            if v and v not in out:
                out.append(str(v))
    return ", ".join(out)


def _published_at(hit: dict) -> str | None:
    raw = hit.get("posted_at")
    try:
        epoch = int(raw)
    except (TypeError, ValueError):
        return None
    if epoch <= 0:
        return None
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def hit_to_raw_job(hit: dict) -> RawJob | None:
    url = (hit.get("url_external") or "").strip()
    if not url:
        return None
    url = canonicalize_url(url)

    title = (hit.get("title") or "").strip()
    company = (hit.get("company_name") or "").strip()
    body = _text(hit.get("description_short") or hit.get("description") or "")

    lines: list[str] = [f"{title} — {company}".strip(" —")]
    if body:
        lines += ["", body]

    facts = [
        ("Problem areas", _joined(hit, "tags_area")),
        ("Skills", _joined(hit, "tags_skill")),
        ("Role type", _joined(hit, "tags_role_type")),
        ("Location", _joined(hit, "tags_city", "tags_country", "tags_location_80k")),
        ("Experience", _joined(hit, "tags_exp_required")),
        ("Salary", (hit.get("salary") or "").strip()),
    ]
    fact_lines = [f"{label}: {value}" for label, value in facts if value]
    if fact_lines:
        lines += [""] + fact_lines

    company_desc = _text(hit.get("company_description") or "")
    if company_desc:
        lines += ["", f"About {company}: {company_desc}"]

    lines += ["", f"Apply: {url}"]

    return RawJob(
        url=url,
        title=title,
        company=company,
        raw_text="\n".join(lines),
        published_at=_published_at(hit),
    )


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "/", "", ""))


class EaworkListingFetcher:
    """Fetches the 80,000 Hours / eawork job board via its public Algolia
    index. The board is a JS SPA whose job cards carry no crawlable posting
    URL, so the generic listing path finds nothing; this queries the same
    Algolia endpoint the board's own frontend uses and maps each structured
    record (which carries the real ATS apply URL) to a RawJob."""

    def __init__(self, source: dict, known_urls: frozenset[str] = frozenset()) -> None:
        self._source = source
        self._known_urls = known_urls

    def fetch(self) -> list[RawJob]:
        try:
            cfg = parse_algolia_config(self._board_html())
            hits = self._query(cfg)
        except FetchError as exc:
            logger.warning("eawork: fetch failed for '%s': %s", self._source["name"], exc)
            return []
        except Exception:
            logger.exception("eawork: unexpected failure for '%s'", self._source["name"])
            return []

        jobs: list[RawJob] = []
        for hit in hits:
            job = hit_to_raw_job(hit)  # job.url is already canonicalized
            if job is None or job.url in self._known_urls:
                continue
            jobs.append(job)
            if len(jobs) >= MAX_RESULTS:
                break
        return jobs

    def _board_html(self) -> str:
        try:
            resp = httpx.get(
                _origin(self._source["url"]),
                timeout=_HTTP_TIMEOUT,
                follow_redirects=True,
            )
        except httpx.HTTPError as exc:
            raise FetchError(str(exc)) from exc
        if resp.status_code != 200:
            raise FetchError(f"board page HTTP {resp.status_code}")
        return resp.text

    def _query(self, cfg: AlgoliaConfig) -> list[dict]:
        params = [("query", ""), ("hitsPerPage", "1000")]
        facet_filters = refinements_to_facet_filters(self._source["url"])
        if facet_filters:
            params.append(("facetFilters", json.dumps(facet_filters)))
        url = f"https://{cfg.app_id}-dsn.algolia.net/1/indexes/{cfg.index}/query"
        try:
            resp = httpx.post(
                url,
                headers={
                    "x-algolia-application-id": cfg.app_id,
                    "x-algolia-api-key": cfg.api_key,
                    "content-type": "application/json",
                },
                content=json.dumps({"params": urlencode(params)}),
                timeout=_HTTP_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            raise FetchError(str(exc)) from exc
        if resp.status_code != 200:
            raise FetchError(f"Algolia HTTP {resp.status_code}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise FetchError(f"Algolia response not JSON: {exc}") from exc
        hits = data.get("hits", [])
        if data.get("nbHits", 0) > 1000:
            logger.warning(
                "eawork: %d hits match '%s' filters; only the first 1000 are returned",
                data["nbHits"], self._source["name"],
            )
        return hits
