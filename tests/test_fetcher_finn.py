from datetime import datetime, timezone, timedelta
import respx
import httpx
from unittest.mock import MagicMock, patch
from app.fetchers.finn import FinnListingFetcher

_SOURCE = {"id": 1, "name": "finn.no", "url": "https://www.finn.no/job/search?x=1", "fetcher_type": "finn_listing"}


def _dt(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _mock_playwright(pages_cards):
    """pages_cards: list of card-lists (each a list of {"href", "publishedAt"} dicts),
    one entry per simulated page.eval_on_selector_all() call."""
    cm = MagicMock()
    pw = MagicMock()
    cm.__enter__.return_value = pw
    cm.__exit__.return_value = False
    browser = MagicMock()
    pw.chromium.launch.return_value = browser
    page = MagicMock()
    browser.new_page.return_value = page
    page.eval_on_selector_all.side_effect = pages_cards
    return cm, page


@respx.mock
def test_discovers_and_fetches_each_ad():
    page1 = [
        {"href": "https://www.finn.no/job/ad/1", "publishedAt": _dt(1)},
        {"href": "https://www.finn.no/job/ad/2", "publishedAt": _dt(2)},
        {"href": "https://www.finn.no/job/ad/1", "publishedAt": _dt(1)},
    ]
    respx.get("https://www.finn.no/job/ad/1").mock(return_value=httpx.Response(200, text="<p>Job one</p>"))
    respx.get("https://www.finn.no/job/ad/2").mock(return_value=httpx.Response(200, text="<p>Job two</p>"))

    cm, page = _mock_playwright([page1, []])
    with patch("app.fetchers.finn.sync_playwright", return_value=cm):
        jobs = FinnListingFetcher(_SOURCE).fetch()

    urls = [j.url for j in jobs]
    assert urls == ["https://www.finn.no/job/ad/1", "https://www.finn.no/job/ad/2"]
    assert "Job one" in jobs[0].raw_text
    assert "Job two" in jobs[1].raw_text
    assert jobs[0].published_at == page1[0]["publishedAt"]
    assert jobs[1].published_at == page1[1]["publishedAt"]


@respx.mock
def test_caps_number_of_ads_fetched_and_stops_paginating_once_budget_filled():
    cards = [{"href": f"https://www.finn.no/job/ad/{i}", "publishedAt": _dt(1)} for i in range(60)]
    for i in range(60):
        respx.get(f"https://www.finn.no/job/ad/{i}").mock(return_value=httpx.Response(200, text=f"<p>Job {i}</p>"))

    cm, page = _mock_playwright([cards])
    with patch("app.fetchers.finn.sync_playwright", return_value=cm):
        jobs = FinnListingFetcher(_SOURCE).fetch()

    assert len(jobs) == 50
    assert page.eval_on_selector_all.call_count == 1


def test_ignores_non_ad_links():
    cards = [
        {"href": "https://www.finn.no/job/browse.html", "publishedAt": None},
        {"href": "https://www.finn.no/job/ad/1", "publishedAt": _dt(1)},
    ]
    cm, page = _mock_playwright([cards, []])
    with patch("app.fetchers.finn.sync_playwright", return_value=cm), \
         patch("app.fetchers.finn.HttpFetcher") as MockHttp:
        MockHttp.return_value.fetch.return_value = []
        FinnListingFetcher(_SOURCE).fetch()

    called_urls = [c.args[0]["url"] for c in MockHttp.call_args_list]
    assert called_urls == ["https://www.finn.no/job/ad/1"]


def test_returns_empty_list_on_playwright_error():
    with patch("app.fetchers.finn.sync_playwright", side_effect=RuntimeError("boom")):
        jobs = FinnListingFetcher(_SOURCE).fetch()
    assert jobs == []


@respx.mock
def test_paginates_across_multiple_pages_within_cutoff():
    page1 = [{"href": "https://www.finn.no/job/ad/1", "publishedAt": _dt(1)}]
    page2 = [{"href": "https://www.finn.no/job/ad/2", "publishedAt": _dt(2)}]
    respx.get("https://www.finn.no/job/ad/1").mock(return_value=httpx.Response(200, text="<p>Job one</p>"))
    respx.get("https://www.finn.no/job/ad/2").mock(return_value=httpx.Response(200, text="<p>Job two</p>"))

    cm, page = _mock_playwright([page1, page2, []])
    with patch("app.fetchers.finn.sync_playwright", return_value=cm):
        jobs = FinnListingFetcher(_SOURCE).fetch()

    assert [j.url for j in jobs] == [
        "https://www.finn.no/job/ad/1",
        "https://www.finn.no/job/ad/2",
    ]
    goto_urls = [c.args[0] for c in page.goto.call_args_list]
    assert goto_urls[0] == _SOURCE["url"]
    assert "page=2" in goto_urls[1]
    assert "page=3" in goto_urls[2]


@respx.mock
def test_stops_after_max_listing_pages():
    pages = [[{"href": f"https://www.finn.no/job/ad/{i}", "publishedAt": _dt(1)}] for i in range(5)]
    for i in range(5):
        respx.get(f"https://www.finn.no/job/ad/{i}").mock(return_value=httpx.Response(200, text=f"<p>Job {i}</p>"))

    cm, page = _mock_playwright(pages)
    with patch("app.fetchers.finn.sync_playwright", return_value=cm):
        jobs = FinnListingFetcher(_SOURCE).fetch()

    assert page.eval_on_selector_all.call_count == 5
    assert len(jobs) == 5


@respx.mock
def test_stale_ad_interleaved_with_fresh_does_not_halt_pagination():
    page1 = [
        {"href": "https://www.finn.no/job/ad/stale", "publishedAt": _dt(40)},
        {"href": "https://www.finn.no/job/ad/1", "publishedAt": _dt(1)},
    ]
    page2 = [{"href": "https://www.finn.no/job/ad/2", "publishedAt": _dt(2)}]
    respx.get("https://www.finn.no/job/ad/1").mock(return_value=httpx.Response(200, text="<p>Job one</p>"))
    respx.get("https://www.finn.no/job/ad/2").mock(return_value=httpx.Response(200, text="<p>Job two</p>"))

    cm, page = _mock_playwright([page1, page2, []])
    with patch("app.fetchers.finn.sync_playwright", return_value=cm):
        jobs = FinnListingFetcher(_SOURCE).fetch()

    urls = [j.url for j in jobs]
    assert "https://www.finn.no/job/ad/stale" not in urls
    assert urls == ["https://www.finn.no/job/ad/1", "https://www.finn.no/job/ad/2"]


@respx.mock
def test_stops_when_a_full_page_is_past_cutoff():
    page1 = [{"href": "https://www.finn.no/job/ad/1", "publishedAt": _dt(1)}]
    page2 = [{"href": "https://www.finn.no/job/ad/old", "publishedAt": _dt(40)}]
    respx.get("https://www.finn.no/job/ad/1").mock(return_value=httpx.Response(200, text="<p>Job one</p>"))

    cm, page = _mock_playwright([page1, page2])
    with patch("app.fetchers.finn.sync_playwright", return_value=cm):
        jobs = FinnListingFetcher(_SOURCE).fetch()

    assert [j.url for j in jobs] == ["https://www.finn.no/job/ad/1"]
    assert page.eval_on_selector_all.call_count == 2


@respx.mock
def test_skips_already_known_urls():
    page1 = [
        {"href": "https://www.finn.no/job/ad/1", "publishedAt": _dt(1)},
        {"href": "https://www.finn.no/job/ad/2", "publishedAt": _dt(2)},
    ]
    respx.get("https://www.finn.no/job/ad/2").mock(return_value=httpx.Response(200, text="<p>Job two</p>"))
    # ad/1 intentionally has no respx mock: if the fetcher tried to fetch it, respx would raise.

    cm, page = _mock_playwright([page1, []])
    with patch("app.fetchers.finn.sync_playwright", return_value=cm):
        jobs = FinnListingFetcher(_SOURCE, known_urls=frozenset({"https://www.finn.no/job/ad/1"})).fetch()

    assert [j.url for j in jobs] == ["https://www.finn.no/job/ad/2"]
