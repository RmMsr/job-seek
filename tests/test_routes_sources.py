from unittest.mock import patch
from app.db import queries as q
from app.fetchers.slack import SlackFetcher
from app.fetchers.slack_login import SlackCookieLogin

_SLACK_URL = "https://example-workspace.slack.com/archives/C0EXAMPLE1"


def _seed(conn):
    return q.insert_source(conn, "finn.no", "https://finn.no", "http")


def test_sources_page_returns_200(client, conn):
    _seed(conn)
    resp = client.get("/sources")
    assert resp.status_code == 200
    assert "finn.no" in resp.text


def test_sources_page_shows_needs_login_for_slack_source_without_cookie(client, conn):
    q.insert_source(conn, "Example Slack", _SLACK_URL, "slack")
    with patch.object(SlackFetcher, "check_needs_login", return_value=True):
        resp = client.get("/sources")
    assert resp.status_code == 200
    assert "Needs Slack login" in resp.text
    assert "session cookie set" not in resp.text


def test_sources_page_shows_cookie_set_for_slack_source_with_valid_cookie(client, conn):
    sid = q.insert_source(conn, "Example Slack", _SLACK_URL, "slack")
    q.set_source_cookie(conn, sid, "xoxd-fake-cookie")
    with patch.object(SlackFetcher, "check_needs_login", return_value=False):
        resp = client.get("/sources")
    assert resp.status_code == 200
    assert "session cookie set" in resp.text
    assert "Needs Slack login" not in resp.text


def test_sources_page_excludes_manual_source(client, conn):
    q.get_or_create_manual_source(conn)
    q.insert_source(conn, "Real Source", "http://x", "http")
    resp = client.get("/sources")
    assert resp.status_code == 200
    assert "Manual" not in resp.text
    assert "Real Source" in resp.text


def test_create_source(client, conn):
    resp = client.post(
        "/sources",
        data={"name": "finn.no", "url": "https://finn.no", "fetcher_type": "http"},
    )
    assert resp.status_code == 200
    sources = q.get_sources(conn)
    assert len(sources) == 1
    assert sources[0]["name"] == "finn.no"


def test_edit_form_returns_source_fields(client, conn):
    sid = _seed(conn)
    resp = client.get(f"/sources/{sid}/edit")
    assert resp.status_code == 200
    assert "finn.no" in resp.text
    assert 'name="name"' in resp.text
    assert "https://finn.no" in resp.text


def test_update_source(client, conn):
    sid = _seed(conn)
    resp = client.post(
        f"/sources/{sid}",
        data={"name": "Finn AI", "url": "https://finn.no/new", "fetcher_type": "playwright", "enabled": "on"},
    )
    assert resp.status_code == 200
    source = q.get_source(conn, sid)
    assert source["url"] == "https://finn.no/new"
    assert source["fetcher_type"] == "playwright"
    assert source["enabled"] == 1
    assert source["name"] == "Finn AI"


def test_update_source_unchecked_enabled_disables(client, conn):
    sid = _seed(conn)
    resp = client.post(
        f"/sources/{sid}",
        data={"name": "finn.no", "url": "https://finn.no", "fetcher_type": "http"},
    )
    assert resp.status_code == 200
    assert q.get_source(conn, sid)["enabled"] == 0


def test_cancel_edit_returns_display_row(client, conn):
    sid = _seed(conn)
    resp = client.get(f"/sources/{sid}")
    assert resp.status_code == 200
    assert "finn.no" in resp.text


def test_create_slack_source_shows_login_prompt_when_needed(client, conn):
    with patch.object(SlackFetcher, "check_needs_login", return_value=True):
        resp = client.post(
            "/sources",
            data={"name": "Example Slack", "url": _SLACK_URL, "fetcher_type": "slack"},
        )
    assert resp.status_code == 200
    assert "Log in" in resp.text


def test_create_slack_source_no_login_prompt_when_already_logged_in(client, conn):
    with patch.object(SlackFetcher, "check_needs_login", return_value=False):
        resp = client.post(
            "/sources",
            data={"name": "Example Slack", "url": _SLACK_URL, "fetcher_type": "slack"},
        )
    assert resp.status_code == 200
    assert "Needs Slack login" not in resp.text


def test_create_http_source_does_not_attempt_login_check(client, conn):
    with patch.object(SlackFetcher, "check_needs_login") as mock_check:
        resp = client.post(
            "/sources",
            data={"name": "finn.no", "url": "https://finn.no", "fetcher_type": "http"},
        )
    assert resp.status_code == 200
    mock_check.assert_not_called()


def test_update_source_to_slack_shows_login_prompt_when_needed(client, conn):
    sid = _seed(conn)
    with patch.object(SlackFetcher, "check_needs_login", return_value=True):
        resp = client.post(
            f"/sources/{sid}",
            data={"name": "Example Slack", "url": _SLACK_URL, "fetcher_type": "slack", "enabled": "on"},
        )
    assert resp.status_code == 200
    assert "Log in" in resp.text


def test_update_source_with_unsupported_slack_url_does_not_crash(client, conn):
    sid = _seed(conn)
    resp = client.post(
        f"/sources/{sid}",
        data={
            "name": "finn.no",
            "url": "https://not-slack.example.com/x",
            "fetcher_type": "slack",
            "enabled": "on",
        },
    )
    assert resp.status_code == 200
    assert "Needs Slack login" not in resp.text


def test_login_route_streams_progress_and_persists_cookie(client, conn):
    sid = q.insert_source(conn, "Example Slack", _SLACK_URL, "slack")

    def fake_login(self):
        yield "Login successful; captured session cookie."
        return "xoxd-captured"

    with patch.object(SlackCookieLogin, "login", fake_login):
        resp = client.post(f"/sources/{sid}/login")

    assert resp.status_code == 200
    assert "captured session cookie" in resp.text
    assert q.get_source(conn, sid)["d_cookie"] == "xoxd-captured"
    assert "Needs Slack login" not in resp.text.split("HTML:", 1)[1]


def test_login_route_reflects_failed_login(client, conn):
    sid = q.insert_source(conn, "Example Slack", _SLACK_URL, "slack")

    def fake_login(self):
        yield "Timed out waiting for login to Example Slack"
        return None

    with patch.object(SlackCookieLogin, "login", fake_login):
        resp = client.post(f"/sources/{sid}/login")

    assert resp.status_code == 200
    assert q.get_source(conn, sid)["d_cookie"] == ""
    assert "Needs Slack login" in resp.text.split("HTML:", 1)[1]


def test_login_route_404_for_missing_source(client, conn):
    resp = client.post("/sources/999/login")
    assert resp.status_code == 404


def test_login_route_rejects_non_slack_source(client, conn):
    sid = _seed(conn)
    resp = client.post(f"/sources/{sid}/login")
    assert resp.status_code == 400


def test_set_cookie_stores_value_with_acknowledgment(client, conn):
    sid = q.insert_source(conn, "Example Slack", _SLACK_URL, "slack")
    with patch.object(SlackFetcher, "check_needs_login", return_value=False):
        resp = client.post(
            f"/sources/{sid}/cookie",
            data={"d_cookie": "xoxd-pasted", "acknowledged": "on"},
        )
    assert resp.status_code == 200
    assert q.get_source(conn, sid)["d_cookie"] == "xoxd-pasted"
    assert "Needs Slack login" not in resp.text


def test_set_cookie_rejected_without_acknowledgment(client, conn):
    sid = q.insert_source(conn, "Example Slack", _SLACK_URL, "slack")
    resp = client.post(f"/sources/{sid}/cookie", data={"d_cookie": "xoxd-pasted"})
    assert resp.status_code == 400
    assert q.get_source(conn, sid)["d_cookie"] == ""


def test_set_cookie_invalid_shows_needs_login(client, conn):
    sid = q.insert_source(conn, "Example Slack", _SLACK_URL, "slack")
    with patch.object(SlackFetcher, "check_needs_login", return_value=True):
        resp = client.post(
            f"/sources/{sid}/cookie",
            data={"d_cookie": "xoxd-expired", "acknowledged": "on"},
        )
    assert resp.status_code == 200
    assert "Needs Slack login" in resp.text


def test_slack_row_shows_cookie_security_warning(client, conn):
    q.insert_source(conn, "Example Slack", _SLACK_URL, "slack")
    with patch.object(SlackFetcher, "check_needs_login", return_value=True):
        resp = client.get("/sources")
    assert resp.status_code == 200
    assert "every Slack workspace" in resp.text  # warning copy present
    assert 'name="acknowledged"' in resp.text     # required checkbox present
