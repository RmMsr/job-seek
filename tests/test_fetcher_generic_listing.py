from unittest.mock import MagicMock, patch
import httpx
import pytest
import respx
from app.fetchers.generic_listing import GenericListingFetcher, PAGE_FETCH_DELAY_SECONDS
from app.fetchers.content import FetchError
from app.fetchers.listing_detect import ListingDetection


@pytest.fixture(autouse=True)
def sleep_mock():
    """The pagination walk sleeps between page fetches — never actually wait in tests."""
    with patch("app.fetchers.generic_listing.time.sleep") as m:
        yield m

_SOURCE = {"id": 1, "name": "Careers", "url": "https://example.com/careers", "fetcher_type": "generic_listing"}

# Realistic-length detail page — short fixtures (e.g. just "Senior Engineer role")
# would themselves fall under the 200-char thin-content threshold and
# unintentionally trigger the Playwright fallback in tests that aren't testing that.
_JOB_DETAIL_HTML = (
    "<html><body><p>"
    + ("We are hiring a Senior Software Engineer to join our platform team. "
       "You will design services, review code, and mentor other engineers. " * 12)
    + "</p></body></html>"
)


def _detection(is_listing, job_links, html="<html><body>ok</body></html>", rendered=False):
    return ListingDetection(html, is_listing, list(job_links), rendered)


def _by_url(mapping, calls=None):
    """side_effect for detect_listing_page keyed by the listing URL it's given."""
    def _side_effect(client, model, url):
        if calls is not None:
            calls.append(url)
        return mapping[url]
    return _side_effect


def _mock_details(urls):
    for u in urls:
        respx.get(u).mock(return_value=httpx.Response(200, text=_JOB_DETAIL_HTML))


@respx.mock
def test_generic_listing_fetcher_returns_raw_jobs_for_detected_links():
    respx.get("https://example.com/jobs/1").mock(return_value=httpx.Response(200, text=_JOB_DETAIL_HTML))
    respx.get("https://example.com/jobs/2").mock(return_value=httpx.Response(200, text=_JOB_DETAIL_HTML))
    with patch("app.fetchers.generic_listing.detect_listing_page",
               return_value=_detection(True, ["https://example.com/jobs/1", "https://example.com/jobs/2"])):
        jobs = GenericListingFetcher(_SOURCE, MagicMock(), "m").fetch()
    assert {j.url for j in jobs} == {"https://example.com/jobs/1", "https://example.com/jobs/2"}


@respx.mock
def test_generic_listing_fetcher_skips_known_urls():
    respx.get("https://example.com/jobs/2").mock(return_value=httpx.Response(200, text=_JOB_DETAIL_HTML))
    with patch("app.fetchers.generic_listing.detect_listing_page",
               return_value=_detection(True, ["https://example.com/jobs/1", "https://example.com/jobs/2"])):
        jobs = GenericListingFetcher(
            _SOURCE, MagicMock(), "m", known_urls=frozenset({"https://example.com/jobs/1"})
        ).fetch()
    assert {j.url for j in jobs} == {"https://example.com/jobs/2"}


def test_generic_listing_fetcher_returns_empty_when_not_a_listing():
    with patch("app.fetchers.generic_listing.detect_listing_page", return_value=_detection(False, [])):
        assert GenericListingFetcher(_SOURCE, MagicMock(), "m").fetch() == []


def test_generic_listing_fetcher_returns_empty_on_listing_fetch_failure(caplog):
    from app.fetchers.content import FetchError
    with patch("app.fetchers.generic_listing.detect_listing_page", side_effect=FetchError("HTTP 503")), \
         caplog.at_level("WARNING", logger="job_seek"):
        jobs = GenericListingFetcher(_SOURCE, MagicMock(), "m").fetch()
    assert jobs == []
    assert any("Careers" in r.message and "https://example.com/careers" in r.message and "503" in r.message
               for r in caplog.records)


@respx.mock
def test_generic_listing_fetcher_falls_back_to_playwright_for_thin_detail_page():
    respx.get("https://example.com/jobs/1").mock(return_value=httpx.Response(200, text=""))
    rendered_detail = "<html><body><p>" + ("Full job description text. " * 10) + "</p></body></html>"
    with patch("app.fetchers.generic_listing.detect_listing_page",
               return_value=_detection(True, ["https://example.com/jobs/1"])), \
         patch("app.fetchers.generic_listing.render_html", return_value=rendered_detail) as mock_render:
        jobs = GenericListingFetcher(_SOURCE, MagicMock(), "m").fetch()
    mock_render.assert_called_once_with("https://example.com/jobs/1")
    assert len(jobs) == 1 and "Full job description" in jobs[0].raw_text


@respx.mock
def test_generic_listing_fetcher_falls_back_when_detail_page_returns_nonempty_but_thin_content():
    # A JS-rendered detail page often comes back 200 OK with real (non-empty) but
    # useless boilerplate text — e.g. Ashby's "You need to enable JavaScript to run
    # this app." shell. HttpFetcher treats that as success (non-empty), so the
    # fallback trigger must check thinness, not just emptiness.
    respx.get("https://example.com/jobs/1").mock(
        return_value=httpx.Response(200, text="<html><body><noscript>Enable JavaScript</noscript></body></html>")
    )
    rendered_detail = "<html><body><p>" + ("Full job description text. " * 10) + "</p></body></html>"
    with patch("app.fetchers.generic_listing.detect_listing_page",
               return_value=_detection(True, ["https://example.com/jobs/1"])), \
         patch("app.fetchers.generic_listing.render_html", return_value=rendered_detail) as mock_render:
        jobs = GenericListingFetcher(_SOURCE, MagicMock(), "m").fetch()
    mock_render.assert_called_once_with("https://example.com/jobs/1")
    assert len(jobs) == 1 and "Full job description" in jobs[0].raw_text


@respx.mock
def test_generic_listing_renders_detail_page_that_is_a_js_shell_over_200_chars():
    # ~640 chars of nav boilerplate + "needs JavaScript": clears has_enough_text
    # (the old trigger) but is nowhere near a real posting's length.
    shell = "<html><body><nav>" + ("Home Careers About Contact Privacy Terms " * 12) + \
        "</nav><p>This site needs JavaScript enabled.</p></body></html>"
    respx.get("https://example.com/jobs/1").mock(return_value=httpx.Response(200, text=shell))
    rendered_detail = "<html><body><p>" + ("Full role description and requirements. " * 20) + "</p></body></html>"
    with patch("app.fetchers.generic_listing.detect_listing_page",
               return_value=_detection(True, ["https://example.com/jobs/1"])), \
         patch("app.fetchers.generic_listing.render_html", return_value=rendered_detail) as mock_render:
        jobs = GenericListingFetcher(_SOURCE, MagicMock(), "m").fetch()
    mock_render.assert_called_once_with("https://example.com/jobs/1")
    assert len(jobs) == 1 and "Full role description" in jobs[0].raw_text


@respx.mock
def test_generic_listing_keeps_raw_detail_when_render_not_richer():
    shell = "<html><body><nav>" + ("Home Careers About Contact Privacy Terms " * 12) + \
        "</nav><p>This site needs JavaScript enabled.</p></body></html>"
    respx.get("https://example.com/jobs/1").mock(return_value=httpx.Response(200, text=shell))
    with patch("app.fetchers.generic_listing.detect_listing_page",
               return_value=_detection(True, ["https://example.com/jobs/1"])), \
         patch("app.fetchers.generic_listing.render_html", return_value=shell):
        jobs = GenericListingFetcher(_SOURCE, MagicMock(), "m").fetch()
    # render was no richer than raw -> keep the raw HttpFetcher result
    assert len(jobs) == 1 and "needs JavaScript" in jobs[0].raw_text


@respx.mock
def test_generic_listing_fetcher_caps_playwright_fallbacks_per_run():
    detail_urls = [f"https://example.com/jobs/{i}" for i in range(15)]
    for url in detail_urls:
        respx.get(url).mock(return_value=httpx.Response(200, text=""))
    with patch("app.fetchers.generic_listing.detect_listing_page",
               return_value=_detection(True, detail_urls)), \
         patch("app.fetchers.generic_listing.render_html", return_value=None) as mock_render:
        jobs = GenericListingFetcher(_SOURCE, MagicMock(), "m").fetch()
    assert mock_render.call_count == 10
    assert jobs == []


@respx.mock
def test_generic_listing_fetcher_canonicalizes_detail_urls():
    respx.get("https://example.com/jobs/1").mock(return_value=httpx.Response(200, text=_JOB_DETAIL_HTML))
    with patch("app.fetchers.generic_listing.detect_listing_page",
               return_value=_detection(True, ["https://example.com/jobs/1?refId=abc"])):
        jobs = GenericListingFetcher(_SOURCE, MagicMock(), "m").fetch()
    assert [j.url for j in jobs] == ["https://example.com/jobs/1"]


_PAGED_SOURCE = {**_SOURCE, "url": "https://example.com/jobs?start=0"}


@respx.mock
def test_generic_listing_fetcher_walks_pages_until_short_page():
    p1 = [f"https://example.com/jobs/{i}" for i in range(10)]
    p2 = [f"https://example.com/jobs/{i}" for i in range(10, 20)]
    p3 = [f"https://example.com/jobs/{i}" for i in range(20, 24)]  # short page → last
    _mock_details(p1 + p2 + p3)
    calls: list[str] = []
    mapping = {
        "https://example.com/jobs?start=0": _detection(True, p1),
        "https://example.com/jobs?start=10": _detection(True, p2),
        "https://example.com/jobs?start=20": _detection(True, p3),
    }
    with patch("app.fetchers.generic_listing.detect_listing_page", side_effect=_by_url(mapping, calls)):
        jobs = GenericListingFetcher(_PAGED_SOURCE, MagicMock(), "m").fetch()
    assert calls == [
        "https://example.com/jobs?start=0",
        "https://example.com/jobs?start=10",
        "https://example.com/jobs?start=20",
    ]
    assert {j.url for j in jobs} == set(p1 + p2 + p3)


@respx.mock
def test_generic_listing_fetcher_stops_at_max_listing_pages():
    pages = {i: [f"https://example.com/jobs/{i}-{k}" for k in range(3)] for i in range(0, 18, 3)}
    _mock_details([u for links in pages.values() for u in links])
    calls: list[str] = []
    mapping = {f"https://example.com/jobs?start={i}": _detection(True, links) for i, links in pages.items()}
    with patch("app.fetchers.generic_listing.detect_listing_page", side_effect=_by_url(mapping, calls)):
        jobs = GenericListingFetcher(_PAGED_SOURCE, MagicMock(), "m").fetch()
    assert calls == [f"https://example.com/jobs?start={i}" for i in (0, 3, 6, 9, 12)]
    assert "https://example.com/jobs?start=15" not in calls
    assert len(jobs) == 15


@respx.mock
def test_generic_listing_fetcher_stops_when_page_repeats_page_one():
    p1 = ["https://example.com/jobs/1", "https://example.com/jobs/2", "https://example.com/jobs/3"]
    detail_routes = {u: respx.get(u).mock(return_value=httpx.Response(200, text=_JOB_DETAIL_HTML)) for u in p1}
    calls: list[str] = []
    mapping = {
        "https://example.com/jobs?start=0": _detection(True, p1),
        "https://example.com/jobs?start=3": _detection(True, p1),  # same links again
    }
    with patch("app.fetchers.generic_listing.detect_listing_page", side_effect=_by_url(mapping, calls)):
        jobs = GenericListingFetcher(_PAGED_SOURCE, MagicMock(), "m").fetch()
    assert calls == ["https://example.com/jobs?start=0", "https://example.com/jobs?start=3"]
    assert [j.url for j in jobs] == p1
    assert all(route.call_count == 1 for route in detail_routes.values())


@respx.mock
def test_generic_listing_fetcher_no_pagination_param_single_call():
    links = ["https://example.com/jobs/1", "https://example.com/jobs/2", "https://example.com/jobs/3"]
    _mock_details(links)
    calls: list[str] = []
    mapping = {"https://example.com/careers": _detection(True, links)}
    with patch("app.fetchers.generic_listing.detect_listing_page", side_effect=_by_url(mapping, calls)):
        jobs = GenericListingFetcher(_SOURCE, MagicMock(), "m").fetch()
    assert calls == ["https://example.com/careers"]
    assert {j.url for j in jobs} == set(links)


@respx.mock
def test_generic_listing_fetcher_does_not_paginate_below_min_job_links():
    _mock_details(["https://example.com/jobs/1"])
    calls: list[str] = []
    mapping = {"https://example.com/jobs?start=0": _detection(True, ["https://example.com/jobs/1"])}
    with patch("app.fetchers.generic_listing.detect_listing_page", side_effect=_by_url(mapping, calls)):
        jobs = GenericListingFetcher(_PAGED_SOURCE, MagicMock(), "m").fetch()
    assert calls == ["https://example.com/jobs?start=0"]
    assert [j.url for j in jobs] == ["https://example.com/jobs/1"]


@respx.mock
def test_generic_listing_fetcher_stops_when_whole_page_already_known():
    p1 = ["https://example.com/jobs/0", "https://example.com/jobs/1", "https://example.com/jobs/2"]
    p2 = ["https://example.com/jobs/3", "https://example.com/jobs/4", "https://example.com/jobs/5"]
    _mock_details(p1)
    calls: list[str] = []
    mapping = {
        "https://example.com/jobs?start=0": _detection(True, p1),
        "https://example.com/jobs?start=3": _detection(True, p2),
        "https://example.com/jobs?start=6": _detection(True, ["https://example.com/jobs/9"]),
    }
    known = frozenset(p2)
    with patch("app.fetchers.generic_listing.detect_listing_page", side_effect=_by_url(mapping, calls)):
        jobs = GenericListingFetcher(_PAGED_SOURCE, MagicMock(), "m", known_urls=known).fetch()
    assert calls == ["https://example.com/jobs?start=0", "https://example.com/jobs?start=3"]
    assert {j.url for j in jobs} == set(p1)


@respx.mock
def test_generic_listing_fetcher_sleeps_before_each_page_but_not_page_one(sleep_mock):
    p1 = [f"https://example.com/jobs/{i}" for i in range(10)]
    p2 = [f"https://example.com/jobs/{i}" for i in range(10, 20)]
    p3 = [f"https://example.com/jobs/{i}" for i in range(20, 24)]  # short → last page
    _mock_details(p1 + p2 + p3)
    mapping = {
        "https://example.com/jobs?start=0": _detection(True, p1),
        "https://example.com/jobs?start=10": _detection(True, p2),
        "https://example.com/jobs?start=20": _detection(True, p3),
    }
    with patch("app.fetchers.generic_listing.detect_listing_page", side_effect=_by_url(mapping)):
        GenericListingFetcher(_PAGED_SOURCE, MagicMock(), "m").fetch()
    # one sleep per page-2+ fetch (2 pages past page 1), none before page 1
    assert sleep_mock.call_args_list == [((PAGE_FETCH_DELAY_SECONDS,),)] * 2


@respx.mock
def test_generic_listing_fetcher_retries_once_on_empty_page(sleep_mock):
    p1 = [f"https://example.com/jobs/{i}" for i in range(10)]
    p2 = [f"https://example.com/jobs/{i}" for i in range(10, 20)]
    p3 = [f"https://example.com/jobs/{i}" for i in range(20, 23)]  # short → last page
    _mock_details(p1 + p2 + p3)
    calls: list[str] = []
    empty = _detection(False, [])
    responses = {
        "https://example.com/jobs?start=0": [_detection(True, p1)],
        "https://example.com/jobs?start=10": [empty, _detection(True, p2)],  # empty, then full on retry
        "https://example.com/jobs?start=20": [_detection(True, p3)],
    }

    def _side_effect(client, model, url):
        calls.append(url)
        return responses[url].pop(0)

    with patch("app.fetchers.generic_listing.detect_listing_page", side_effect=_side_effect):
        jobs = GenericListingFetcher(_PAGED_SOURCE, MagicMock(), "m").fetch()
    assert calls == [
        "https://example.com/jobs?start=0",
        "https://example.com/jobs?start=10",  # first (empty) attempt
        "https://example.com/jobs?start=10",  # retry
        "https://example.com/jobs?start=20",
    ]
    assert {j.url for j in jobs} == set(p1 + p2 + p3)


@respx.mock
def test_generic_listing_fetcher_stops_when_retry_also_empty(sleep_mock):
    p1 = [f"https://example.com/jobs/{i}" for i in range(10)]
    _mock_details(p1)
    calls: list[str] = []
    empty = _detection(False, [])
    mapping = {
        "https://example.com/jobs?start=0": _detection(True, p1),
        "https://example.com/jobs?start=10": empty,  # empty on both the attempt and the retry
    }
    with patch("app.fetchers.generic_listing.detect_listing_page", side_effect=_by_url(mapping, calls)):
        jobs = GenericListingFetcher(_PAGED_SOURCE, MagicMock(), "m").fetch()
    assert calls == [
        "https://example.com/jobs?start=0",
        "https://example.com/jobs?start=10",
        "https://example.com/jobs?start=10",
    ]
    assert {j.url for j in jobs} == set(p1)


@respx.mock
def test_generic_listing_fetcher_retries_on_fetch_error(sleep_mock):
    p1 = [f"https://example.com/jobs/{i}" for i in range(10)]
    p2 = [f"https://example.com/jobs/{i}" for i in range(10, 13)]  # short → last page
    _mock_details(p1 + p2)
    responses = {
        "https://example.com/jobs?start=0": [_detection(True, p1)],
        "https://example.com/jobs?start=10": [FetchError("HTTP 429"), _detection(True, p2)],
    }

    def _side_effect(client, model, url):
        r = responses[url].pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    with patch("app.fetchers.generic_listing.detect_listing_page", side_effect=_side_effect):
        jobs = GenericListingFetcher(_PAGED_SOURCE, MagicMock(), "m").fetch()
    assert {j.url for j in jobs} == set(p1 + p2)
