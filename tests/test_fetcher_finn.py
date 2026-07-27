import respx
import httpx
from unittest.mock import MagicMock, patch
from app.fetchers.finn import FinnListingFetcher

_SOURCE = {"id": 1, "name": "finn.no", "url": "https://www.finn.no/job/search?x=1", "fetcher_type": "finn_listing"}


def _mock_playwright(hrefs):
    cm = MagicMock()
    pw = MagicMock()
    cm.__enter__.return_value = pw
    cm.__exit__.return_value = False
    browser = MagicMock()
    pw.chromium.launch.return_value = browser
    page = MagicMock()
    browser.new_page.return_value = page
    page.eval_on_selector_all.return_value = hrefs
    return cm


@respx.mock
def test_discovers_and_fetches_each_ad():
    hrefs = [
        "https://www.finn.no/job/ad/1",
        "https://www.finn.no/job/ad/2",
        "https://www.finn.no/job/ad/1",
    ]
    respx.get("https://www.finn.no/job/ad/1").mock(return_value=httpx.Response(200, text="<p>Job one</p>"))
    respx.get("https://www.finn.no/job/ad/2").mock(return_value=httpx.Response(200, text="<p>Job two</p>"))

    with patch("app.fetchers.finn.sync_playwright", return_value=_mock_playwright(hrefs)):
        jobs = FinnListingFetcher(_SOURCE).fetch()

    urls = [j.url for j in jobs]
    assert urls == ["https://www.finn.no/job/ad/1", "https://www.finn.no/job/ad/2"]
    assert "Job one" in jobs[0].raw_text
    assert "Job two" in jobs[1].raw_text


@respx.mock
def test_caps_number_of_ads_fetched():
    hrefs = [f"https://www.finn.no/job/ad/{i}" for i in range(30)]
    for i in range(30):
        respx.get(f"https://www.finn.no/job/ad/{i}").mock(return_value=httpx.Response(200, text=f"<p>Job {i}</p>"))

    with patch("app.fetchers.finn.sync_playwright", return_value=_mock_playwright(hrefs)):
        jobs = FinnListingFetcher(_SOURCE).fetch()

    assert len(jobs) == 20


def test_ignores_non_ad_links():
    hrefs = [
        "https://www.finn.no/job/browse.html",
        "https://www.finn.no/job/ad/1",
    ]
    with patch("app.fetchers.finn.sync_playwright", return_value=_mock_playwright(hrefs)), \
         patch("app.fetchers.finn.HttpFetcher") as MockHttp:
        MockHttp.return_value.fetch.return_value = []
        FinnListingFetcher(_SOURCE).fetch()

    called_urls = [c.args[0]["url"] for c in MockHttp.call_args_list]
    assert called_urls == ["https://www.finn.no/job/ad/1"]


def test_returns_empty_list_on_playwright_error():
    with patch("app.fetchers.finn.sync_playwright", side_effect=RuntimeError("boom")):
        jobs = FinnListingFetcher(_SOURCE).fetch()
    assert jobs == []
