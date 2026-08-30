import json

import httpx
import pytest
import respx
from app.fetchers.eawork import (
    AlgoliaConfig,
    EaworkListingFetcher,
    MAX_RESULTS,
    parse_algolia_config,
    refinements_to_facet_filters,
)
from app.fetchers.content import FetchError
from app.fetchers.eawork import hit_to_raw_job

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


_FULL_HIT = {
    "title": "Associate, Operations",
    "company_name": "Center for AI Safety",
    "company_description": "<p><a href='x'>Center for AI Safety</a> is a nonprofit.</p>",
    "description_short": "<ul>\n<li>Coordinate workflows.</li>\n<li>Triage requests.</li>\n</ul>",
    "description": "",
    "url_external": "https://job-boards.greenhouse.io/cais/jobs/4384681009?utm_source=80000hours",
    "posted_at": 1788043500,
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
    assert job.url == "https://job-boards.greenhouse.io/cais/jobs/4384681009"
    assert job.title == "Associate, Operations"
    assert job.company == "Center for AI Safety"
    assert job.published_at == "2026-08-29T22:45:00+00:00"


def test_hit_to_raw_job_strips_tracking_params_from_url_and_apply_line():
    job = hit_to_raw_job(_FULL_HIT)
    assert "utm_source" not in job.url
    assert "utm_source" not in job.raw_text
    assert "Apply: https://job-boards.greenhouse.io/cais/jobs/4384681009" in job.raw_text


def test_hit_to_raw_job_strips_html_and_includes_tag_lines():
    job = hit_to_raw_job(_FULL_HIT)
    assert "<li>" not in job.raw_text and "<p>" not in job.raw_text
    assert "Coordinate workflows." in job.raw_text
    assert "Skills: Operations" in job.raw_text
    assert "Problem areas: AI safety & policy" in job.raw_text
    assert "Center for AI Safety is a nonprofit." in job.raw_text
    assert "https://job-boards.greenhouse.io/cais/jobs/4384681009" in job.raw_text


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
