import json
from unittest.mock import MagicMock, patch
import httpx
import respx
from app.fetchers.generic_listing import GenericListingFetcher

_SOURCE = {"id": 1, "name": "Careers", "url": "https://example.com/careers", "fetcher_type": "generic_listing"}

_LISTING_HTML = """<html><body>
<a href="/jobs/1">Senior Engineer</a>
<a href="/jobs/2">Staff Engineer</a>
<a href="/about">About</a>
<p>""" + ("We are a fast-growing company building great products. " * 5) + """</p>
</body></html>"""

# Realistic-length detail page — short fixtures (e.g. just "Senior Engineer role")
# would themselves fall under the 200-char thin-content threshold and
# unintentionally trigger the Playwright fallback in tests that aren't testing that.
_JOB_DETAIL_HTML = (
    "<html><body><p>" + ("We are hiring a Senior Software Engineer to join our team. " * 5) + "</p></body></html>"
)


def _client_returning(is_listing: bool, job_links: list[str]) -> MagicMock:
    client = MagicMock()
    choice = MagicMock()
    choice.message.content = json.dumps({"is_listing": is_listing, "job_links": job_links})
    client.chat.completions.create.return_value = MagicMock(choices=[choice])
    return client


@respx.mock
def test_generic_listing_fetcher_returns_raw_jobs_for_detected_links():
    respx.get("https://example.com/careers").mock(return_value=httpx.Response(200, text=_LISTING_HTML))
    respx.get("https://example.com/jobs/1").mock(
        return_value=httpx.Response(200, text=_JOB_DETAIL_HTML)
    )
    respx.get("https://example.com/jobs/2").mock(
        return_value=httpx.Response(200, text=_JOB_DETAIL_HTML)
    )
    client = _client_returning(True, ["https://example.com/jobs/1", "https://example.com/jobs/2"])

    fetcher = GenericListingFetcher(_SOURCE, client, "llama3.2")
    jobs = fetcher.fetch()

    urls = {job.url for job in jobs}
    assert urls == {"https://example.com/jobs/1", "https://example.com/jobs/2"}


@respx.mock
def test_generic_listing_fetcher_skips_known_urls():
    respx.get("https://example.com/careers").mock(return_value=httpx.Response(200, text=_LISTING_HTML))
    respx.get("https://example.com/jobs/2").mock(
        return_value=httpx.Response(200, text=_JOB_DETAIL_HTML)
    )
    client = _client_returning(True, ["https://example.com/jobs/1", "https://example.com/jobs/2"])

    fetcher = GenericListingFetcher(
        _SOURCE, client, "llama3.2", known_urls=frozenset({"https://example.com/jobs/1"})
    )
    jobs = fetcher.fetch()

    assert {job.url for job in jobs} == {"https://example.com/jobs/2"}


@respx.mock
def test_generic_listing_fetcher_returns_empty_when_not_a_listing():
    respx.get("https://example.com/careers").mock(return_value=httpx.Response(200, text=_LISTING_HTML))
    client = _client_returning(False, [])

    fetcher = GenericListingFetcher(_SOURCE, client, "llama3.2")
    jobs = fetcher.fetch()

    assert jobs == []


@respx.mock
def test_generic_listing_fetcher_handles_fetch_failure():
    respx.get("https://example.com/careers").mock(side_effect=httpx.ConnectError("boom"))
    client = _client_returning(True, [])

    fetcher = GenericListingFetcher(_SOURCE, client, "llama3.2")
    jobs = fetcher.fetch()

    assert jobs == []


@respx.mock
def test_generic_listing_fetcher_logs_exception_on_listing_fetch_failure(caplog):
    respx.get("https://example.com/careers").mock(side_effect=httpx.ConnectError("boom"))
    client = _client_returning(True, [])

    with caplog.at_level("INFO", logger="job_seek"):
        fetcher = GenericListingFetcher(_SOURCE, client, "llama3.2")
        jobs = fetcher.fetch()

    assert jobs == []
    failure_records = [
        r for r in caplog.records
        if "Careers" in r.message and "https://example.com/careers" in r.message and r.levelname != "INFO"
    ]
    assert failure_records
    assert any(r.exc_info for r in failure_records)


@respx.mock
def test_generic_listing_fetcher_logs_warning_on_non_200_listing_response(caplog):
    respx.get("https://example.com/careers").mock(return_value=httpx.Response(503))
    client = _client_returning(True, [])

    with caplog.at_level("INFO", logger="job_seek"):
        fetcher = GenericListingFetcher(_SOURCE, client, "llama3.2")
        jobs = fetcher.fetch()

    assert jobs == []
    assert any(
        "Careers" in r.message and "503" in r.message and "https://example.com/careers" in r.message
        for r in caplog.records
    )


_THIN_LISTING_HTML = "<html><body><noscript>Enable JavaScript</noscript></body></html>"


@respx.mock
def test_generic_listing_fetcher_falls_back_to_playwright_for_thin_listing_page():
    respx.get("https://example.com/careers").mock(return_value=httpx.Response(200, text=_THIN_LISTING_HTML))
    respx.get("https://example.com/jobs/1").mock(
        return_value=httpx.Response(200, text=_JOB_DETAIL_HTML)
    )
    client = _client_returning(True, ["https://example.com/jobs/1"])

    with patch("app.fetchers.generic_listing.render_html", return_value=_LISTING_HTML) as mock_render:
        fetcher = GenericListingFetcher(_SOURCE, client, "llama3.2")
        jobs = fetcher.fetch()

    mock_render.assert_called_once_with("https://example.com/careers")
    assert {job.url for job in jobs} == {"https://example.com/jobs/1"}


@respx.mock
def test_generic_listing_fetcher_falls_back_to_playwright_for_thin_detail_page():
    respx.get("https://example.com/careers").mock(return_value=httpx.Response(200, text=_LISTING_HTML))
    respx.get("https://example.com/jobs/1").mock(return_value=httpx.Response(200, text=""))
    client = _client_returning(True, ["https://example.com/jobs/1"])

    rendered_detail_html = "<html><body><p>" + ("Full job description text. " * 10) + "</p></body></html>"
    with patch("app.fetchers.generic_listing.render_html", return_value=rendered_detail_html) as mock_render:
        fetcher = GenericListingFetcher(_SOURCE, client, "llama3.2")
        jobs = fetcher.fetch()

    mock_render.assert_called_once_with("https://example.com/jobs/1")
    assert len(jobs) == 1
    assert jobs[0].url == "https://example.com/jobs/1"
    assert "Full job description" in jobs[0].raw_text


@respx.mock
def test_generic_listing_fetcher_falls_back_when_detail_page_returns_nonempty_but_thin_content():
    # A JS-rendered detail page often comes back 200 OK with real (non-empty) but
    # useless boilerplate text — e.g. Ashby's "You need to enable JavaScript to run
    # this app." shell. HttpFetcher treats that as success (non-empty), so the
    # fallback trigger must check thinness, not just emptiness.
    respx.get("https://example.com/careers").mock(return_value=httpx.Response(200, text=_LISTING_HTML))
    respx.get("https://example.com/jobs/1").mock(
        return_value=httpx.Response(200, text="<html><body><noscript>Enable JavaScript</noscript></body></html>")
    )
    client = _client_returning(True, ["https://example.com/jobs/1"])

    rendered_detail_html = "<html><body><p>" + ("Full job description text. " * 10) + "</p></body></html>"
    with patch("app.fetchers.generic_listing.render_html", return_value=rendered_detail_html) as mock_render:
        fetcher = GenericListingFetcher(_SOURCE, client, "llama3.2")
        jobs = fetcher.fetch()

    mock_render.assert_called_once_with("https://example.com/jobs/1")
    assert len(jobs) == 1
    assert jobs[0].url == "https://example.com/jobs/1"
    assert "Full job description" in jobs[0].raw_text


@respx.mock
def test_generic_listing_fetcher_caps_playwright_fallbacks_per_run():
    # detect_listing filters the LLM's job_links against hrefs actually present
    # on the page, so the listing page here needs a real anchor for each of the
    # 15 detail URLs (unlike _LISTING_HTML, which only has 2 job anchors).
    detail_urls = [f"https://example.com/jobs/{i}" for i in range(15)]
    listing_html = (
        "<html><body>"
        + "".join(f'<a href="/jobs/{i}">Job {i}</a>' for i in range(15))
        + "<p>" + ("We are a fast-growing company building great products. " * 5) + "</p>"
        + "</body></html>"
    )
    respx.get("https://example.com/careers").mock(return_value=httpx.Response(200, text=listing_html))
    for url in detail_urls:
        respx.get(url).mock(return_value=httpx.Response(200, text=""))
    client = _client_returning(True, detail_urls)

    with patch("app.fetchers.generic_listing.render_html", return_value=None) as mock_render:
        fetcher = GenericListingFetcher(_SOURCE, client, "llama3.2")
        jobs = fetcher.fetch()

    assert mock_render.call_count == 10
    assert jobs == []
