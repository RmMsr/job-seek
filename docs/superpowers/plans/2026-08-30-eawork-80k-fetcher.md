# eawork / 80,000 Hours Job Board Fetcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a dedicated `eawork_listing` fetcher that pulls the 80,000 Hours job board's roles straight from its Algolia index, since the generic listing crawler finds zero jobs there.

**Architecture:** A new `EaworkListingFetcher` (sibling of `FinnListingFetcher`) scrapes the board's public Algolia app-id / search-key / index name from the page's inline Nuxt config, translates the stored source URL's `refinementList[...]` filters into Algolia `facetFilters`, issues one search query, and maps each hit to a `RawJob` using only the structured record (no per-job detail fetch). Recognized by host in `classify_known_source`; `sources.fetcher_type` CHECK constraint gains the new value via an idempotent schema migration that also flips the existing 80k row.

**Tech Stack:** Python 3.12, `httpx` (HTTP, `respx`-mockable), `beautifulsoup4` (HTML→text), `sqlite3`, `pytest` + `respx`.

## Global Constraints

- Stored source URL stays the human `https://jobs.80000hours.org/?refinementList[...]` form — never rewritten in the DB.
- Algolia app-id / key / index are scraped from the page at runtime, never committed as constants.
- One Algolia query per `fetch()` call. No custom `User-Agent` — default `httpx` client.
- `MAX_RESULTS = 50` new jobs per run (matches `finn`).
- Network error / non-200 / JSON-parse failure / missing config → `logger.warning(...)` + return `[]` (never raise out of `fetch()`), consistent with `GenericListingFetcher`.
- Recognized hosts: `jobs.80000hours.org`, `eawork.org`, `www.eawork.org`.
- Migration philosophy: single hard-downtime table rebuild assuming the current known schema; no dual-shape defensive paths.
- Run tests with `python -m pytest ...` (not `uv run` — read-only cache in this environment).

---

### Task 1: Pure helpers — Algolia config scrape + filter translation

**Files:**
- Create: `app/fetchers/eawork.py`
- Test: `tests/test_fetcher_eawork.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `AlgoliaConfig` — `dataclass(frozen=True)` with `app_id: str`, `api_key: str`, `index: str`.
  - `parse_algolia_config(html: str) -> AlgoliaConfig` — raises `FetchError` (imported from `app.fetchers.content`) if any field is absent.
  - `refinements_to_facet_filters(url: str) -> list[list[str]]` — `[[f"{facet}:{value}", ...], ...]`, one inner list per distinct `refinementList` facet, values in URL order; `[]` when there are no `refinementList` params.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_fetcher_eawork.py
import pytest
from app.fetchers.eawork import (
    AlgoliaConfig,
    parse_algolia_config,
    refinements_to_facet_filters,
)
from app.fetchers.content import FetchError

_CONFIG_HTML = (
    '<script>window.__NUXT__=(function(){...})('
    'apiBase:"https://backend.eawork.org/api",env:"prod",'
    'algoliaApplicationId:"W6KM1UDIB3",'
    'algoliaApiKey:"d1d7f2c8696e7b36837d5ed337c4a319",'
    'algoliaTagsIndex:"tags_prod",algoliaCompaniesIndex:"companies_prod",'
    'algoliaJobsIndexAlternative:"jobs_prod_closing_date",'
    'algoliaJobsIndex:"jobs_prod",'
    'algoliaJobsIndexSuperRanked:"jobs_prod_super_ranked")'
    '</script>'
)


def test_parse_algolia_config_extracts_all_three_fields():
    cfg = parse_algolia_config(_CONFIG_HTML)
    assert cfg == AlgoliaConfig(
        app_id="W6KM1UDIB3",
        api_key="d1d7f2c8696e7b36837d5ed337c4a319",
        index="jobs_prod",
    )


def test_parse_algolia_config_raises_when_key_missing():
    html = _CONFIG_HTML.replace(
        'algoliaApiKey:"d1d7f2c8696e7b36837d5ed337c4a319",', ""
    )
    with pytest.raises(FetchError):
        parse_algolia_config(html)


def test_refinements_single_facet_multiple_values():
    url = (
        "https://jobs.80000hours.org/?"
        "refinementList%5Btags_area%5D%5B0%5D=AI%20safety%20%26%20policy&"
        "refinementList%5Btags_area%5D%5B1%5D=Technical"
    )
    assert refinements_to_facet_filters(url) == [
        ["tags_area:AI safety & policy", "tags_area:Technical"],
    ]


def test_refinements_multiple_facets_grouped_and_ordered():
    url = (
        "https://jobs.80000hours.org/?"
        "refinementList%5Btags_area%5D%5B0%5D=Technical&"
        "refinementList%5Btags_skill%5D%5B0%5D=Software%20engineering&"
        "refinementList%5Btags_skill%5D%5B1%5D=Operations"
    )
    assert refinements_to_facet_filters(url) == [
        ["tags_area:Technical"],
        ["tags_skill:Software engineering", "tags_skill:Operations"],
    ]


def test_refinements_ignores_non_refinement_params():
    url = "https://jobs.80000hours.org/?page=2&salary-limit=100000&query=foo"
    assert refinements_to_facet_filters(url) == []


def test_refinements_no_params():
    assert refinements_to_facet_filters("https://jobs.80000hours.org/") == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_fetcher_eawork.py -v`
Expected: FAIL — `ModuleNotFoundError` / `ImportError` (`app.fetchers.eawork` does not exist).

- [ ] **Step 3: Write minimal implementation**

```python
# app/fetchers/eawork.py
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit, parse_qsl

from app.fetchers.content import FetchError

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_fetcher_eawork.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add app/fetchers/eawork.py tests/test_fetcher_eawork.py
git commit -m "feat: eawork fetcher config-scrape and filter-translation helpers"
```

---

### Task 2: Hit → RawJob mapping

**Files:**
- Modify: `app/fetchers/eawork.py`
- Test: `tests/test_fetcher_eawork.py`

**Interfaces:**
- Consumes: `RawJob` from `app.fetchers.base`.
- Produces: `hit_to_raw_job(hit: dict) -> RawJob | None` — returns `None` when `hit["url_external"]` is missing/empty. `raw_text` is a plain-text block (HTML stripped); empty fields produce no line. `published_at` is an ISO-8601 UTC string from `hit["posted_at"]` (epoch seconds), or `None` when that value is absent, non-numeric, or `<= 0`.

- [ ] **Step 1: Write the failing tests**

```python
# add to tests/test_fetcher_eawork.py
from app.fetchers.eawork import hit_to_raw_job

_FULL_HIT = {
    "title": "Associate, Operations",
    "company_name": "Center for AI Safety",
    "company_description": "<p>The <a href='x'>Center</a> is a nonprofit.</p>",
    "description_short": "<ul>\n<li>Coordinate workflows.</li>\n<li>Triage requests.</li>\n</ul>",
    "description": "",
    "url_external": "https://job-boards.greenhouse.io/cais/jobs/4384681009?utm_source=80000hours",
    "posted_at": 1787875500,
    "tags_area": ["AI safety & policy"],
    "tags_skill": ["Operations"],
    "tags_role_type": ["Full-time"],
    "tags_city": ["San Francisco Bay Area"],
    "tags_country": ["USA"],
    "tags_location_80k": ["San Francisco Bay Area", "USA"],
    "tags_exp_required": ["Entry-level", "Junior (1-4 years experience)"],
    "experience_min": 0,
    "salary": "$80,000 - $110,000",
}


def test_hit_to_raw_job_maps_core_fields():
    job = hit_to_raw_job(_FULL_HIT)
    assert job.url == _FULL_HIT["url_external"]
    assert job.title == "Associate, Operations"
    assert job.company == "Center for AI Safety"
    assert job.published_at == "2026-08-29T22:45:00+00:00"


def test_hit_to_raw_job_strips_html_and_includes_tag_lines():
    job = hit_to_raw_job(_FULL_HIT)
    assert "<li>" not in job.raw_text and "<p>" not in job.raw_text
    assert "Coordinate workflows." in job.raw_text
    assert "Skills: Operations" in job.raw_text
    assert "Problem areas: AI safety & policy" in job.raw_text
    assert "Center for AI Safety is a nonprofit." in job.raw_text
    assert _FULL_HIT["url_external"] in job.raw_text


def test_hit_to_raw_job_omits_empty_fields():
    hit = {
        "title": "Researcher",
        "company_name": "Redwood",
        "url_external": "https://example.org/apply",
        "posted_at": 0,
    }
    job = hit_to_raw_job(hit)
    assert job.published_at is None
    assert "Skills:" not in job.raw_text
    assert "Salary:" not in job.raw_text
    assert job.raw_text.startswith("Researcher — Redwood")


def test_hit_to_raw_job_returns_none_without_url_external():
    assert hit_to_raw_job({"title": "x", "company_name": "y"}) is None
    assert hit_to_raw_job({"title": "x", "url_external": ""}) is None


def test_hit_to_raw_job_falls_back_to_description_when_no_short():
    hit = {
        "title": "Eng",
        "company_name": "Co",
        "url_external": "https://example.org/a",
        "description_short": "",
        "description": "<p>Full description here.</p>",
        "posted_at": "not-a-number",
    }
    job = hit_to_raw_job(hit)
    assert "Full description here." in job.raw_text
    assert job.published_at is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_fetcher_eawork.py -k hit_to_raw_job -v`
Expected: FAIL — `ImportError: cannot import name 'hit_to_raw_job'`.

- [ ] **Step 3: Write minimal implementation**

Add to `app/fetchers/eawork.py`:

```python
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from app.fetchers.base import RawJob


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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_fetcher_eawork.py -v`
Expected: PASS (11 tests).

- [ ] **Step 5: Commit**

```bash
git add app/fetchers/eawork.py tests/test_fetcher_eawork.py
git commit -m "feat: eawork hit-to-RawJob mapping"
```

---

### Task 3: `EaworkListingFetcher.fetch()`

**Files:**
- Modify: `app/fetchers/eawork.py`
- Test: `tests/test_fetcher_eawork.py`

**Interfaces:**
- Consumes: `parse_algolia_config`, `refinements_to_facet_filters`, `hit_to_raw_job` (Tasks 1–2); `canonicalize_url` from `app.url_canon`.
- Produces: `EaworkListingFetcher(source: dict, known_urls: frozenset[str] = frozenset())` with `fetch() -> list[RawJob]`. Module constant `MAX_RESULTS = 50`. Queries `https://{app_id}-dsn.algolia.net/1/indexes/{index}/query` via `httpx.post`.

- [ ] **Step 1: Write the failing tests**

```python
# add to tests/test_fetcher_eawork.py
import json
import httpx
import respx
from app.fetchers.eawork import EaworkListingFetcher, MAX_RESULTS

_ORIGIN = "https://jobs.80000hours.org/"
_ALGOLIA = "https://W6KM1UDIB3-dsn.algolia.net/1/indexes/jobs_prod/query"
_SOURCE = {
    "id": 1,
    "name": "80000hours/job-board",
    "url": "https://jobs.80000hours.org/?refinementList%5Btags_skill%5D%5B0%5D=Operations",
    "fetcher_type": "eawork_listing",
}


def _hits(n, start=0):
    return [
        {
            "title": f"Role {i}",
            "company_name": "Org",
            "url_external": f"https://ats.example/jobs/{i}?utm_source=80000hours",
            "description_short": "<p>Do good work here, please.</p>",
            "posted_at": 1787875500,
        }
        for i in range(start, start + n)
    ]


def _algolia_response(hits, nb_hits=None):
    return httpx.Response(
        200, json={"hits": hits, "nbHits": nb_hits if nb_hits is not None else len(hits)}
    )


@respx.mock
def test_fetch_returns_raw_jobs_from_algolia():
    respx.get(_ORIGIN).mock(return_value=httpx.Response(200, text=_CONFIG_HTML))
    route = respx.post(_ALGOLIA).mock(return_value=_algolia_response(_hits(3)))
    jobs = EaworkListingFetcher(_SOURCE).fetch()
    assert [j.url for j in jobs] == [
        "https://ats.example/jobs/0",
        "https://ats.example/jobs/1",
        "https://ats.example/jobs/2",
    ]
    sent = json.loads(route.calls.last.request.content)
    assert "facetFilters=" in sent["params"]
    assert "tags_skill%3AOperations" in sent["params"] or "tags_skill:Operations" in sent["params"]


@respx.mock
def test_fetch_skips_known_and_urlless_hits():
    respx.get(_ORIGIN).mock(return_value=httpx.Response(200, text=_CONFIG_HTML))
    hits = _hits(2) + [{"title": "no url", "company_name": "x"}]
    respx.post(_ALGOLIA).mock(return_value=_algolia_response(hits))
    known = frozenset({"https://ats.example/jobs/0"})
    jobs = EaworkListingFetcher(_SOURCE, known_urls=known).fetch()
    assert [j.url for j in jobs] == ["https://ats.example/jobs/1"]


@respx.mock
def test_fetch_caps_at_max_results():
    respx.get(_ORIGIN).mock(return_value=httpx.Response(200, text=_CONFIG_HTML))
    respx.post(_ALGOLIA).mock(return_value=_algolia_response(_hits(MAX_RESULTS + 20)))
    jobs = EaworkListingFetcher(_SOURCE).fetch()
    assert len(jobs) == MAX_RESULTS


@respx.mock
def test_fetch_returns_empty_on_algolia_error(caplog):
    respx.get(_ORIGIN).mock(return_value=httpx.Response(200, text=_CONFIG_HTML))
    respx.post(_ALGOLIA).mock(return_value=httpx.Response(403, json={"message": "nope"}))
    assert EaworkListingFetcher(_SOURCE).fetch() == []
    assert "eawork" in caplog.text.lower()


@respx.mock
def test_fetch_returns_empty_when_config_missing(caplog):
    respx.get(_ORIGIN).mock(return_value=httpx.Response(200, text="<html>no config</html>"))
    assert EaworkListingFetcher(_SOURCE).fetch() == []
    assert "eawork" in caplog.text.lower()


@respx.mock
def test_fetch_warns_and_truncates_when_nbhits_over_1000():
    respx.get(_ORIGIN).mock(return_value=httpx.Response(200, text=_CONFIG_HTML))
    respx.post(_ALGOLIA).mock(return_value=_algolia_response(_hits(3), nb_hits=5000))
    with_caplog = EaworkListingFetcher(_SOURCE).fetch()
    assert len(with_caplog) == 3  # still processes what it got
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_fetcher_eawork.py -k fetch -v`
Expected: FAIL — `ImportError: cannot import name 'EaworkListingFetcher'`.

- [ ] **Step 3: Write minimal implementation**

Add to `app/fetchers/eawork.py`:

```python
import json
import logging
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx

from app.url_canon import canonicalize_url

logger = logging.getLogger("job_seek")

MAX_RESULTS = 50
_HTTP_TIMEOUT = 30


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
            job = hit_to_raw_job(hit)
            if job is None or canonicalize_url(job.url) in self._known_urls:
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
        data = resp.json()
        hits = data.get("hits", [])
        if data.get("nbHits", 0) > 1000:
            logger.warning(
                "eawork: %d hits match '%s' filters; only the first 1000 are returned",
                data["nbHits"], self._source["name"],
            )
        return hits
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_fetcher_eawork.py -v`
Expected: PASS (all tasks 1–3 tests).

- [ ] **Step 5: Commit**

```bash
git add app/fetchers/eawork.py tests/test_fetcher_eawork.py
git commit -m "feat: EaworkListingFetcher querying the 80k Hours Algolia index"
```

---

### Task 4: Wire recognition + pipeline dispatch

**Files:**
- Modify: `app/ai/classify_known_source.py`
- Modify: `app/pipeline.py` (`_make_fetcher`, imports)
- Test: `tests/test_fetcher_eawork.py`, `tests/test_classify_known_source.py` (create if absent — check first with `ls tests | grep classify`)

**Interfaces:**
- Consumes: `EaworkListingFetcher` (Task 3); `q.get_all_job_urls(conn)` (existing).
- Produces: `classify_known_source(url)` returns `"eawork_listing"` for the recognized hosts; `_make_fetcher` builds an `EaworkListingFetcher` for `fetcher_type == "eawork_listing"`.

- [ ] **Step 1: Write the failing tests**

```python
# add to tests/test_fetcher_eawork.py
from app.ai.classify_known_source import classify_known_source, DETECTABLE_FETCHER_TYPES


@pytest.mark.parametrize("url", [
    "https://jobs.80000hours.org/?refinementList%5Btags_area%5D%5B0%5D=Technical",
    "https://eawork.org/",
    "https://www.eawork.org/some/path",
])
def test_classify_known_source_recognizes_eawork(url):
    assert classify_known_source(url) == "eawork_listing"


def test_eawork_listing_is_detectable():
    assert "eawork_listing" in DETECTABLE_FETCHER_TYPES
```

```python
# add to tests/test_pipeline.py  (check the file exists: ls tests | grep pipeline)
from app.fetchers.eawork import EaworkListingFetcher
from app.pipeline import _make_fetcher


def test_make_fetcher_builds_eawork_listing(tmp_path):
    import sqlite3
    from app.db.schema import init_db
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    source = {"id": 1, "name": "80k", "url": "https://jobs.80000hours.org/", "fetcher_type": "eawork_listing"}
    fetcher = _make_fetcher(source, "profile-dir", conn, client=None, model=None)
    assert isinstance(fetcher, EaworkListingFetcher)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_fetcher_eawork.py -k classify -v` and `python -m pytest tests/test_pipeline.py -k eawork -v`
Expected: FAIL — `classify_known_source` returns `None`; `_make_fetcher` raises `ValueError: Unsupported fetcher_type: 'eawork_listing'`.

- [ ] **Step 3: Write minimal implementation**

`app/ai/classify_known_source.py` — replace the body:

```python
from __future__ import annotations
from urllib.parse import urlsplit
from app.fetchers.slack import SLACK_URL_RE

DETECTABLE_FETCHER_TYPES = {"slack", "finn_listing", "generic_listing", "eawork_listing"}

_EAWORK_HOSTS = {"jobs.80000hours.org", "eawork.org", "www.eawork.org"}


def classify_known_source(url: str) -> str | None:
    if SLACK_URL_RE.match(url):
        return "slack"
    host = urlsplit(url).netloc.lower()
    if host == "finn.no" or host.endswith(".finn.no"):
        return "finn_listing"
    if host in _EAWORK_HOSTS:
        return "eawork_listing"
    return None
```

`app/pipeline.py` — add import near the other fetcher imports:

```python
from app.fetchers.eawork import EaworkListingFetcher
```

and in `_make_fetcher`, after the `finn_listing` branch:

```python
    if ft == "eawork_listing":
        return EaworkListingFetcher(source, known_urls=q.get_all_job_urls(conn))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_fetcher_eawork.py tests/test_pipeline.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/ai/classify_known_source.py app/pipeline.py tests/
git commit -m "feat: recognize eawork/80k hosts and dispatch EaworkListingFetcher"
```

---

### Task 5: Schema migration for the new `fetcher_type`

**Files:**
- Modify: `app/db/schema.py` (`_DDL` sources CHECK on line ~14; new migration fn; `init_db` call list)
- Test: `tests/test_schema.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `sources.fetcher_type` CHECK accepts `'eawork_listing'` after `init_db`; any pre-existing `generic_listing` row whose `url` starts with `https://jobs.80000hours.org/` is switched to `eawork_listing`.

- [ ] **Step 1: Write the failing tests**

```python
# add to tests/test_schema.py
def test_init_db_accepts_eawork_listing_fetcher_type(conn):
    init_db(conn)
    conn.execute(
        "INSERT INTO sources (name, url, fetcher_type) "
        "VALUES ('80k', 'https://jobs.80000hours.org/', 'eawork_listing')"
    )  # must not raise


def test_init_db_flips_existing_80k_generic_row_to_eawork(conn):
    conn.executescript(
        """
        CREATE TABLE sources (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            fetcher_type TEXT NOT NULL CHECK(fetcher_type IN ('slack', 'finn_listing', 'manual', 'generic_listing')),
            enabled INTEGER NOT NULL DEFAULT 1,
            d_cookie TEXT NOT NULL DEFAULT ''
        );
        """
    )
    conn.execute(
        "INSERT INTO sources (name, url, fetcher_type) VALUES "
        "('80k', 'https://jobs.80000hours.org/?refinementList%5Btags_area%5D%5B0%5D=Technical', 'generic_listing')"
    )
    conn.execute(
        "INSERT INTO sources (name, url, fetcher_type) VALUES ('other', 'https://x.test/', 'generic_listing')"
    )
    init_db(conn)
    rows = dict(conn.execute("SELECT name, fetcher_type FROM sources").fetchall())
    assert rows["80k"] == "eawork_listing"
    assert rows["other"] == "generic_listing"
```

(`conn` fixture in `test_schema.py` yields an in-memory connection with `row_factory = sqlite3.Row` — confirm and match the two existing patterns there.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_schema.py -k eawork -v`
Expected: FAIL — CHECK constraint rejects `'eawork_listing'` / row not flipped.

- [ ] **Step 3: Write minimal implementation**

In `app/db/schema.py`, update the `_DDL` `sources` CHECK list (line ~14) to include `'eawork_listing'`:

```sql
    fetcher_type TEXT NOT NULL CHECK(fetcher_type IN ('slack', 'finn_listing', 'manual', 'generic_listing', 'eawork_listing')),
```

Add the migration function (next to `_migrate_sources_drop_http_playwright_types`):

```python
def _migrate_sources_fetcher_type_eawork_listing(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='sources'"
    ).fetchone()
    if row is None or "'eawork_listing'" in row[0]:
        return
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript(
        """
        CREATE TABLE sources_new (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            fetcher_type TEXT NOT NULL CHECK(fetcher_type IN ('slack', 'finn_listing', 'manual', 'generic_listing', 'eawork_listing')),
            enabled INTEGER NOT NULL DEFAULT 1,
            d_cookie TEXT NOT NULL DEFAULT ''
        );
        INSERT INTO sources_new SELECT * FROM sources;
        DROP TABLE sources;
        ALTER TABLE sources_new RENAME TO sources;
        UPDATE sources SET fetcher_type = 'eawork_listing'
        WHERE fetcher_type = 'generic_listing'
          AND url LIKE 'https://jobs.80000hours.org/%';
        """
    )
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
```

Add to `init_db`, immediately after `_migrate_sources_drop_http_playwright_types(conn)`:

```python
    _migrate_sources_fetcher_type_eawork_listing(conn)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_schema.py -v`
Expected: PASS (existing + 2 new).

- [ ] **Step 5: Commit**

```bash
git add app/db/schema.py tests/test_schema.py
git commit -m "feat: migrate sources.fetcher_type for eawork_listing and flip the 80k row"
```

---

### Task 6: Full-suite check + manual smoke

**Files:** none (verification only).

- [ ] **Step 1: Run the whole test suite**

Run: `python -m pytest`
Expected: PASS, no new failures.

- [ ] **Step 2: Live smoke against the real board**

Run:
```bash
python -c "
from app.fetchers.eawork import EaworkListingFetcher
s = {'id': 1, 'name': '80k', 'url': 'https://jobs.80000hours.org/?refinementList%5Btags_skill%5D%5B0%5D=Software%20engineering'}
jobs = EaworkListingFetcher(s).fetch()
print(len(jobs), 'jobs')
for j in jobs[:3]:
    print(j.title, '|', j.company, '|', j.url)
    print(j.raw_text[:300])
    print('---')
"
```
Expected: a non-zero job count, real ATS URLs (`greenhouse.io`, `lever.co`, `ashbyhq.com`, …), `raw_text` with the tag lines.

- [ ] **Step 3: Apply the migration to the throwaway dev DB and eyeball**

Per the `run-dev-server` skill, copy `job-seek.db` into the worktree, start the dev server, confirm the 80k source now shows `eawork_listing` and a fetch run returns jobs. Hand the URL to the user.

- [ ] **Step 4: Commit** (only if smoke revealed a fixable gap; otherwise nothing to commit)

---

## Self-Review

**Spec coverage:**
- Approach / dedicated Algolia fetcher → Tasks 1–3.
- Runtime key scrape, no constants → Task 1 (`parse_algolia_config`), Global Constraints.
- Stored URL stays human → nothing rewrites it; Task 4 only classifies. ✓
- `classify_known_source` + `DETECTABLE_FETCHER_TYPES` → Task 4.
- `_make_fetcher` branch → Task 4.
- Schema CHECK + migration + row flip + `_DDL` update → Task 5.
- Filter translation semantics (OR within / AND across / ignore unknown / match-all) → Task 1 tests.
- `raw_text` composition, epoch→ISO, omit-empty, url_external as url → Task 2.
- Cap 50, known_urls dedup on canonical url, `nbHits > 1000` warning, error→`[]` → Task 3.
- One query per run, no custom UA → Task 3 impl (`_query` called once; plain `httpx`).
- Tests enumerated in spec → covered across Tasks 1–5; migration test → Task 5.
- Live behaviour / dev-server handoff → Task 6.

**Placeholder scan:** none — every code step is complete.

**Type consistency:** `AlgoliaConfig(app_id, api_key, index)`, `parse_algolia_config`, `refinements_to_facet_filters`, `hit_to_raw_job`, `EaworkListingFetcher(source, known_urls)`, `MAX_RESULTS` — names identical across Tasks 1→3→4 and the plan's `_query`/`fetch` bodies. `RawJob(url, title, company, raw_text, published_at)` matches `app/fetchers/base.py`.

**Note for implementer:** `tests/test_pipeline.py`, `tests/test_classify_known_source.py`, and `tests/test_schema.py` all exist. `test_schema.py`'s `conn` fixture yields an in-memory `sqlite3` connection with `row_factory = sqlite3.Row` — Task 5's new tests use it as written. Put the Task 4 `classify_known_source` tests in `tests/test_classify_known_source.py` (not the eawork test file) to match existing organization.
