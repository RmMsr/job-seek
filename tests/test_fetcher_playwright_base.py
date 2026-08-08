from unittest.mock import MagicMock
import pytest
from app.fetchers.playwright_base import PlaywrightFetcher

_SOURCE = {"id": 1, "name": "Test Source", "url": "https://example.com/target"}


class _LoginAwareFetcher(PlaywrightFetcher):
    def _needs_login(self, page) -> bool:
        return "signin" in page.url


def _make_playwright(contexts):
    pw = MagicMock()
    pw.chromium.launch_persistent_context.side_effect = contexts
    return pw


def test_needs_login_default_false():
    fetcher = PlaywrightFetcher(_SOURCE, "profile-dir")
    page = MagicMock(url="https://example.com/target")
    assert fetcher._needs_login(page) is False


def test_target_url_default_is_source_url():
    fetcher = PlaywrightFetcher(_SOURCE, "profile-dir")
    assert fetcher._target_url() == _SOURCE["url"]


def test_fetch_with_navigates_to_target_url_not_source_url():
    class _RewrittenUrlFetcher(PlaywrightFetcher):
        def _target_url(self) -> str:
            return "https://example.com/rewritten"

    ctx = MagicMock()
    page = MagicMock(url="https://example.com/rewritten")
    page.content.return_value = "<html>ok</html>"
    ctx.new_page.return_value = page
    pw = _make_playwright([ctx])

    fetcher = _RewrittenUrlFetcher(_SOURCE, "profile-dir")
    fetcher._fetch_with(pw)

    assert page.goto.call_args.args[0] == "https://example.com/rewritten"


def test_fetch_with_skips_prompt_when_already_logged_in(monkeypatch):
    input_calls = []
    monkeypatch.setattr("builtins.input", lambda *a: input_calls.append(1))

    ctx = MagicMock()
    page = MagicMock(url="https://example.com/target")
    page.content.return_value = "<html>ok</html>"
    ctx.new_page.return_value = page
    pw = _make_playwright([ctx])

    fetcher = _LoginAwareFetcher(_SOURCE, "profile-dir")
    jobs = fetcher._fetch_with(pw)

    assert pw.chromium.launch_persistent_context.call_count == 1
    assert pw.chromium.launch_persistent_context.call_args.kwargs["headless"] is True
    assert input_calls == []
    assert jobs[0].raw_text == "<html>ok</html>"
    ctx.close.assert_called_once()
    assert page.goto.call_args.kwargs["wait_until"] == "domcontentloaded"


def test_fetch_with_waits_for_login_then_succeeds():
    ctx1 = MagicMock()
    page1 = MagicMock(url="https://example.com/signin")
    ctx1.new_page.return_value = page1

    ctx2 = MagicMock()
    page2 = MagicMock(url="https://example.com/signin")

    poll_calls = {"n": 0}

    def _poll(_ms):
        poll_calls["n"] += 1
        if poll_calls["n"] >= 2:
            page2.url = "https://example.com/target"

    page2.wait_for_timeout.side_effect = _poll
    page2.content.return_value = "<html>logged in</html>"
    ctx2.new_page.return_value = page2

    pw = _make_playwright([ctx1, ctx2])

    fetcher = _LoginAwareFetcher(_SOURCE, "profile-dir")
    jobs = fetcher._fetch_with(pw)

    call_kwargs = [c.kwargs for c in pw.chromium.launch_persistent_context.call_args_list]
    assert call_kwargs[0]["headless"] is True
    assert call_kwargs[1]["headless"] is False
    ctx1.close.assert_called_once()
    assert poll_calls["n"] == 2
    assert jobs[0].raw_text == "<html>logged in</html>"


def test_fetch_with_raises_if_login_wait_times_out():
    class _NeverLogsIn(_LoginAwareFetcher):
        LOGIN_TIMEOUT_SECONDS = 0

    ctx1 = MagicMock()
    page1 = MagicMock(url="https://example.com/signin")
    ctx1.new_page.return_value = page1

    ctx2 = MagicMock()
    page2 = MagicMock(url="https://example.com/signin")
    ctx2.new_page.return_value = page2

    pw = _make_playwright([ctx1, ctx2])

    fetcher = _NeverLogsIn(_SOURCE, "profile-dir")
    with pytest.raises(RuntimeError):
        fetcher._fetch_with(pw)


# -- preflight login check ----------------------------------------------------


def test_check_needs_login_with_true_when_login_needed():
    ctx = MagicMock()
    page = MagicMock(url="https://example.com/signin")
    ctx.new_page.return_value = page
    pw = _make_playwright([ctx])

    fetcher = _LoginAwareFetcher(_SOURCE, "profile-dir")
    assert fetcher._check_needs_login_with(pw) is True
    assert pw.chromium.launch_persistent_context.call_args.kwargs["headless"] is True
    ctx.close.assert_called_once()


def test_check_needs_login_with_false_when_already_logged_in():
    ctx = MagicMock()
    page = MagicMock(url="https://example.com/target")
    ctx.new_page.return_value = page
    pw = _make_playwright([ctx])

    fetcher = _LoginAwareFetcher(_SOURCE, "profile-dir")
    assert fetcher._check_needs_login_with(pw) is False


# -- interactive login generator ----------------------------------------------


def _drain(gen):
    lines = []
    try:
        while True:
            lines.append(next(gen))
    except StopIteration as stop:
        return lines, stop.value


def test_login_with_returns_true_without_opening_second_browser_when_already_logged_in():
    ctx = MagicMock()
    page = MagicMock(url="https://example.com/target")
    ctx.new_page.return_value = page
    pw = _make_playwright([ctx])

    fetcher = _LoginAwareFetcher(_SOURCE, "profile-dir")
    lines, result = _drain(fetcher._login_with(pw))

    assert result is True
    assert pw.chromium.launch_persistent_context.call_count == 1
    assert any("Already logged in" in line for line in lines)


def test_login_with_opens_headed_browser_and_waits_when_login_needed():
    ctx1 = MagicMock()
    page1 = MagicMock(url="https://example.com/signin")
    ctx1.new_page.return_value = page1

    ctx2 = MagicMock()
    page2 = MagicMock(url="https://example.com/signin")

    poll_calls = {"n": 0}

    def _poll(_ms):
        poll_calls["n"] += 1
        if poll_calls["n"] >= 2:
            page2.url = "https://example.com/target"

    page2.wait_for_timeout.side_effect = _poll
    ctx2.new_page.return_value = page2

    pw = _make_playwright([ctx1, ctx2])

    fetcher = _LoginAwareFetcher(_SOURCE, "profile-dir")
    lines, result = _drain(fetcher._login_with(pw))

    assert result is True
    call_kwargs = [c.kwargs for c in pw.chromium.launch_persistent_context.call_args_list]
    assert call_kwargs[0]["headless"] is True
    assert call_kwargs[1]["headless"] is False
    assert any("window that just opened" in line for line in lines)
    assert any("Login successful" in line for line in lines)


def test_login_with_yields_periodic_progress_while_waiting():
    class _NeverLogsIn(_LoginAwareFetcher):
        LOGIN_TIMEOUT_SECONDS = 100
        LOGIN_PROGRESS_EVERY_SECONDS = 0  # yield progress every poll, deterministically

    ctx1 = MagicMock()
    page1 = MagicMock(url="https://example.com/signin")
    ctx1.new_page.return_value = page1

    ctx2 = MagicMock()
    page2 = MagicMock(url="https://example.com/signin")

    poll_calls = {"n": 0}

    def _poll(_ms):
        poll_calls["n"] += 1
        if poll_calls["n"] >= 2:
            page2.url = "https://example.com/target"

    page2.wait_for_timeout.side_effect = _poll
    ctx2.new_page.return_value = page2

    pw = _make_playwright([ctx1, ctx2])

    fetcher = _NeverLogsIn(_SOURCE, "profile-dir")
    lines, result = _drain(fetcher._login_with(pw))

    assert result is True
    assert any("Still waiting" in line for line in lines)


def test_login_with_returns_false_and_yields_message_on_timeout():
    class _NeverLogsIn(_LoginAwareFetcher):
        LOGIN_TIMEOUT_SECONDS = 0

    ctx1 = MagicMock()
    page1 = MagicMock(url="https://example.com/signin")
    ctx1.new_page.return_value = page1

    ctx2 = MagicMock()
    page2 = MagicMock(url="https://example.com/signin")
    ctx2.new_page.return_value = page2

    pw = _make_playwright([ctx1, ctx2])

    fetcher = _NeverLogsIn(_SOURCE, "profile-dir")
    lines, result = _drain(fetcher._login_with(pw))

    assert result is False
    assert any("Timed out" in line for line in lines)
