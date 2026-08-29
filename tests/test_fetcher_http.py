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


@respx.mock
def test_http_fetcher_logs_warning_on_rate_limit(caplog):
    respx.get("https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/1").mock(
        return_value=httpx.Response(429, text="slow down")
    )
    with caplog.at_level("WARNING", logger="job_seek"):
        jobs = HttpFetcher({"url": "https://www.linkedin.com/jobs/view/1"}).fetch()
    assert jobs == []
    assert any("429" in r.message and "linkedin.com" in r.message for r in caplog.records)


@respx.mock
def test_http_fetcher_no_warning_on_ordinary_404(caplog):
    respx.get("https://example.com/gone").mock(return_value=httpx.Response(404))
    with caplog.at_level("WARNING", logger="job_seek"):
        HttpFetcher({"url": "https://example.com/gone"}).fetch()
    assert not caplog.records


_LI_VIEW = "https://fr.linkedin.com/jobs/view/ml-engineer-at-acme-4421669844?position=1"
_LI_GUEST = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/4421669844"
_LI_BODY = "<html><body><p>" + ("word " * 60) + "</p></body></html>"


@respx.mock
def test_http_fetcher_rewrites_linkedin_view_url_to_guest_endpoint():
    route = respx.get(_LI_GUEST).mock(return_value=httpx.Response(200, text=_LI_BODY))
    jobs = HttpFetcher({"url": _LI_VIEW}).fetch()
    assert route.called
    assert jobs and jobs[0].url == _LI_VIEW  # stored/display URL is unchanged


@respx.mock
def test_http_fetcher_leaves_non_linkedin_url_untouched():
    route = respx.get("https://example.com/jobs/1").mock(return_value=httpx.Response(200, text=_LI_BODY))
    HttpFetcher({"url": "https://example.com/jobs/1"}).fetch()
    assert route.called
