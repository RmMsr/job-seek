import os
from unittest.mock import patch
import httpx
import respx
from app.cli.slack_login import main
from app.fetchers.slack_login import SlackCookieLogin

_SLACK_URL = "https://example-workspace.slack.com/archives/C0EXAMPLE1"
_COOKIE_POST_URL = "http://testserver/sources/3/cookie"


def _fake_login_success(self):
    yield "Login successful; captured session cookie."
    return "xoxd-captured"


def _fake_login_failure(self):
    yield "Timed out waiting for login to example-workspace"
    return None


@respx.mock
def test_confirmed_login_success_tells_user_to_reload_sources_page(monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda prompt="": "yes")
    respx.post(_COOKIE_POST_URL).mock(return_value=httpx.Response(200))

    with patch.object(SlackCookieLogin, "login", _fake_login_success):
        main([_SLACK_URL, _COOKIE_POST_URL])

    assert "reload" in capsys.readouterr().out.lower()


@respx.mock
def test_confirmed_login_posts_cookie_and_exits_zero(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": "yes")
    route = respx.post(_COOKIE_POST_URL).mock(return_value=httpx.Response(200))

    with patch.object(SlackCookieLogin, "login", _fake_login_success):
        code = main([_SLACK_URL, _COOKIE_POST_URL])

    assert code == 0
    assert route.calls.call_count == 1
    sent = route.calls.last.request.content
    assert b"d_cookie=xoxd-captured" in sent
    assert b"acknowledged=on" in sent


def test_declined_confirmation_aborts_without_login_or_post(monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda prompt="": "no")

    with patch.object(SlackCookieLogin, "login") as mock_login:
        code = main([_SLACK_URL, _COOKIE_POST_URL])

    assert code == 1
    mock_login.assert_not_called()
    assert "Aborted" in capsys.readouterr().out


def test_warning_mentions_the_cookie_post_url_host(monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda prompt="": "no")

    main([_SLACK_URL, _COOKIE_POST_URL])

    assert "testserver" in capsys.readouterr().out


@respx.mock
def test_failed_login_exits_nonzero_without_posting(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": "yes")
    route = respx.post(_COOKIE_POST_URL).mock(return_value=httpx.Response(200))

    with patch.object(SlackCookieLogin, "login", _fake_login_failure):
        code = main([_SLACK_URL, _COOKIE_POST_URL])

    assert code == 1
    assert route.calls.call_count == 0


@respx.mock
def test_server_rejection_exits_nonzero(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": "yes")
    respx.post(_COOKIE_POST_URL).mock(return_value=httpx.Response(400, text="nope"))

    with patch.object(SlackCookieLogin, "login", _fake_login_success):
        code = main([_SLACK_URL, _COOKIE_POST_URL])

    assert code == 1


@respx.mock
def test_connection_error_when_posting_exits_nonzero(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": "yes")
    respx.post(_COOKIE_POST_URL).mock(side_effect=httpx.ConnectError("refused"))

    with patch.object(SlackCookieLogin, "login", _fake_login_success):
        code = main([_SLACK_URL, _COOKIE_POST_URL])

    assert code == 1


@respx.mock
def test_default_profile_dir_is_temporary_and_cleaned_up(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": "yes")
    respx.post(_COOKIE_POST_URL).mock(return_value=httpx.Response(200))
    captured = {}

    def fake_init(self, source, profile_dir):
        captured["path"] = profile_dir

    with patch.object(SlackCookieLogin, "__init__", fake_init), \
         patch.object(SlackCookieLogin, "login", _fake_login_success):
        main([_SLACK_URL, _COOKIE_POST_URL])

    assert ".cache" not in captured["path"]
    assert not os.path.exists(captured["path"])  # cleaned up after the run


@respx.mock
def test_reuse_login_profile_uses_persistent_cache_path(monkeypatch, tmp_path):
    monkeypatch.setattr("builtins.input", lambda prompt="": "yes")
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    respx.post(_COOKIE_POST_URL).mock(return_value=httpx.Response(200))
    captured = {}

    def fake_init(self, source, profile_dir):
        captured["path"] = profile_dir

    with patch.object(SlackCookieLogin, "__init__", fake_init), \
         patch.object(SlackCookieLogin, "login", _fake_login_success):
        main([_SLACK_URL, _COOKIE_POST_URL, "--reuse-login-profile"])

    expected = tmp_path / ".cache" / "job-seek" / "slack-login" / "example-workspace"
    assert captured["path"] == str(expected)
    assert expected.exists()
