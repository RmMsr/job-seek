from unittest.mock import patch
from app.db import queries as q
from app.fetchers.slack import SlackFetcher

_SLACK_URL = "https://example-workspace.slack.com/archives/C0EXAMPLE1"


def _seed(conn):
    return q.insert_source(conn, "finn.no", "https://finn.no", "http")


def test_sources_page_returns_200(client, conn):
    _seed(conn)
    resp = client.get("/sources")
    assert resp.status_code == 200
    assert "finn.no" in resp.text


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
    assert "Log in" not in resp.text


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
    assert "Log in" not in resp.text


def test_login_route_streams_progress_and_updated_row(client, conn):
    sid = q.insert_source(conn, "Example Slack", _SLACK_URL, "slack")

    def fake_login(self):
        yield "Already logged in to Example Slack."
        return True

    with patch.object(SlackFetcher, "login", fake_login):
        resp = client.post(f"/sources/{sid}/login")

    assert resp.status_code == 200
    assert "Already logged in to Example Slack." in resp.text
    assert "HTML:" in resp.text
    assert "Log in" not in resp.text.split("HTML:", 1)[1]


def test_login_route_reflects_failed_login_in_updated_row(client, conn):
    sid = q.insert_source(conn, "Example Slack", _SLACK_URL, "slack")

    def fake_login(self):
        yield "Timed out waiting for login to Example Slack"
        return False

    with patch.object(SlackFetcher, "login", fake_login):
        resp = client.post(f"/sources/{sid}/login")

    assert resp.status_code == 200
    assert "Log in" in resp.text.split("HTML:", 1)[1]


def test_login_route_404_for_missing_source(client, conn):
    resp = client.post("/sources/999/login")
    assert resp.status_code == 404


def test_login_route_rejects_non_playwright_source(client, conn):
    sid = _seed(conn)
    resp = client.post(f"/sources/{sid}/login")
    assert resp.status_code == 400
