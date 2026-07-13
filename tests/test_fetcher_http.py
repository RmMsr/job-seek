import pytest
import respx
import httpx
from app.fetchers.base import RawJob
from app.fetchers.http import HttpFetcher


_SOURCE = {"id": 1, "name": "Test Board", "url": "https://example.com/jobs", "fetcher_type": "http"}

_HTML = """<html><body>
<div class="job-listing">
  <h2 class="job-title">ML Engineer</h2>
  <span class="company">Acme Corp</span>
  <a href="/jobs/42">View job</a>
  <p>We are hiring a senior ML engineer...</p>
</div>
</body></html>"""


@respx.mock
def test_http_fetcher_returns_raw_jobs():
    respx.get("https://example.com/jobs").mock(return_value=httpx.Response(200, text=_HTML))
    fetcher = HttpFetcher(_SOURCE)
    jobs = fetcher.fetch()
    assert isinstance(jobs, list)


@respx.mock
def test_http_fetcher_handles_network_error():
    respx.get("https://example.com/jobs").mock(side_effect=httpx.ConnectError("timeout"))
    fetcher = HttpFetcher(_SOURCE)
    jobs = fetcher.fetch()
    assert jobs == []


@respx.mock
def test_http_fetcher_handles_non_200():
    respx.get("https://example.com/jobs").mock(return_value=httpx.Response(403, text="Forbidden"))
    fetcher = HttpFetcher(_SOURCE)
    jobs = fetcher.fetch()
    assert jobs == []
