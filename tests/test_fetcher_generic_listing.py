import json
from unittest.mock import MagicMock
import httpx
import respx
from app.fetchers.generic_listing import GenericListingFetcher

_SOURCE = {"id": 1, "name": "Careers", "url": "https://example.com/careers", "fetcher_type": "generic_listing"}

_LISTING_HTML = """<html><body>
<a href="/jobs/1">Senior Engineer</a>
<a href="/jobs/2">Staff Engineer</a>
<a href="/about">About</a>
</body></html>"""


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
        return_value=httpx.Response(200, text="<html><body>Senior Engineer role</body></html>")
    )
    respx.get("https://example.com/jobs/2").mock(
        return_value=httpx.Response(200, text="<html><body>Staff Engineer role</body></html>")
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
        return_value=httpx.Response(200, text="<html><body>Staff Engineer role</body></html>")
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
