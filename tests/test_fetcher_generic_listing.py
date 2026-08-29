from unittest.mock import MagicMock, patch
import httpx
import respx
from app.fetchers.generic_listing import GenericListingFetcher
from app.fetchers.listing_detect import ListingDetection

_SOURCE = {"id": 1, "name": "Careers", "url": "https://example.com/careers", "fetcher_type": "generic_listing"}

# Realistic-length detail page — short fixtures (e.g. just "Senior Engineer role")
# would themselves fall under the 200-char thin-content threshold and
# unintentionally trigger the Playwright fallback in tests that aren't testing that.
_JOB_DETAIL_HTML = (
    "<html><body><p>" + ("We are hiring a Senior Software Engineer to join our team. " * 5) + "</p></body></html>"
)


def _detection(is_listing, job_links, html="<html><body>ok</body></html>", rendered=False):
    return ListingDetection(html, is_listing, list(job_links), rendered)


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
