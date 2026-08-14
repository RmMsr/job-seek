from contextlib import contextmanager
from unittest.mock import MagicMock
import pytest
from app.fetchers.slack_login import SlackCookieLogin

_SOURCE = {
    "id": 1,
    "name": "Example Slack",
    "url": "https://example-workspace.slack.com/archives/C0EXAMPLE1",
    "fetcher_type": "slack",
}
_TARGET_URL = "https://example-workspace.slack.com/messages/C0EXAMPLE1"


def test_needs_login_false_when_on_channel_page():
    page = MagicMock(url=_TARGET_URL)
    assert SlackCookieLogin(_SOURCE, "profile-dir")._needs_login(page) is False


def test_needs_login_true_when_redirected_to_signin():
    page = MagicMock(url="https://app.slack.com/workspace-signin?redir=%2Fgantry%2Fauth")
    assert SlackCookieLogin(_SOURCE, "profile-dir")._needs_login(page) is True


def test_target_url_normalizes_archives_link_to_messages_link():
    assert SlackCookieLogin(_SOURCE, "profile-dir")._target_url() == _TARGET_URL


def _drive(gen):
    msgs = []
    try:
        while True:
            msgs.append(next(gen))
    except StopIteration as stop:
        return msgs, stop.value


def test_login_returns_d_cookie_when_already_logged_in(monkeypatch):
    login = SlackCookieLogin(_SOURCE, "profile-dir")
    fake_ctx = MagicMock()
    fake_ctx.new_page.return_value = MagicMock(url=_TARGET_URL)  # channel in path => logged in
    fake_ctx.cookies.return_value = [{"name": "d", "value": "xoxd-captured"}]
    monkeypatch.setattr(login, "_launch", lambda pw, *, headless: fake_ctx)

    @contextmanager
    def fake_pw():
        yield MagicMock()

    monkeypatch.setattr("app.fetchers.slack_login.sync_playwright", fake_pw)

    msgs, cookie = _drive(login.login())
    assert cookie == "xoxd-captured"
    assert any("successful" in m.lower() or "captured" in m.lower() for m in msgs)
