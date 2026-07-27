from unittest.mock import MagicMock
from app.fetchers.slack import SlackFetcher

_SOURCE = {"id": 1, "name": "Example Slack", "url": "https://app.slack.com/client/T1/C1", "fetcher_type": "slack"}


def _mock_message(text):
    m = MagicMock()
    m.inner_text.return_value = text
    return m


def test_extract_gives_each_message_a_unique_url():
    page = MagicMock()
    page.query_selector_all.return_value = [
        _mock_message("Hiring an ML engineer, remote"),
        _mock_message("Looking for a robotics intern"),
    ]

    fetcher = SlackFetcher(_SOURCE, "profile-dir")
    jobs = fetcher._extract(page)

    assert len(jobs) == 2
    assert jobs[0].url != jobs[1].url
    assert all(j.url.startswith(_SOURCE["url"]) for j in jobs)


def test_extract_same_text_yields_same_url_for_dedup():
    page1 = MagicMock()
    page1.query_selector_all.return_value = [_mock_message("Same post")]
    page2 = MagicMock()
    page2.query_selector_all.return_value = [_mock_message("Same post")]

    fetcher = SlackFetcher(_SOURCE, "profile-dir")
    jobs1 = fetcher._extract(page1)
    jobs2 = fetcher._extract(page2)

    assert jobs1[0].url == jobs2[0].url


def test_extract_skips_empty_messages():
    page = MagicMock()
    page.query_selector_all.return_value = [_mock_message("   "), _mock_message("Real post")]

    fetcher = SlackFetcher(_SOURCE, "profile-dir")
    jobs = fetcher._extract(page)

    assert len(jobs) == 1
    assert jobs[0].raw_text == "Real post"
