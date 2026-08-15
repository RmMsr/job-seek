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
    assert "Needs Slack login" in resp.text


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
    assert "Needs Slack login" in resp.text


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


def test_forget_cookie_clears_stored_value(client, conn):
    sid = q.insert_source(conn, "Example Slack", _SLACK_URL, "slack")
    q.set_source_cookie(conn, sid, "xoxd-pasted")

    resp = client.post(f"/sources/{sid}/forget-cookie")

    assert resp.status_code == 200
    assert q.get_source(conn, sid)["d_cookie"] == ""
    assert "Needs Slack login" in resp.text


def test_forget_cookie_404_for_missing_source(client, conn):
    resp = client.post("/sources/999/forget-cookie")
    assert resp.status_code == 404


def test_row_shows_forget_button_only_when_cookie_is_set(client, conn):
    sid = q.insert_source(conn, "Example Slack", _SLACK_URL, "slack")
    with patch.object(SlackFetcher, "check_needs_login", return_value=True):
        resp_before = client.get("/sources")
    assert f'hx-post="/sources/{sid}/forget-cookie"' not in resp_before.text

    q.set_source_cookie(conn, sid, "xoxd-pasted")
    with patch.object(SlackFetcher, "check_needs_login", return_value=False):
        resp_after = client.get("/sources")
    assert f'hx-post="/sources/{sid}/forget-cookie"' in resp_after.text


def test_slack_row_shows_cookie_security_warning(client, conn):
    q.insert_source(conn, "Example Slack", _SLACK_URL, "slack")
    with patch.object(SlackFetcher, "check_needs_login", return_value=True):
        resp = client.get("/sources")
    assert resp.status_code == 200
    assert "every Slack workspace" in resp.text  # warning copy present
    assert 'name="acknowledged"' in resp.text     # required checkbox present


def test_sources_page_add_source_form_includes_generic_listing_option(client, conn):
    resp = client.get("/sources")
    assert resp.status_code == 200
    assert '<option value="generic_listing">generic_listing</option>' in resp.text


def test_delete_source_removes_row_and_returns_empty_body(client, conn):
    sid = _seed(conn)
    resp = client.delete(f"/sources/{sid}")
    assert resp.status_code == 200
    assert resp.text == ""
    assert q.get_source(conn, sid) is None


def test_delete_source_removes_its_jobs(client, conn):
    sid = _seed(conn)
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/1", title="T", company="C", raw_text="r")
    resp = client.delete(f"/sources/{sid}")
    assert resp.status_code == 200
    assert q.get_job(conn, jid) is None


def test_delete_source_404_for_missing_source(client, conn):
    resp = client.delete("/sources/999")
    assert resp.status_code == 404


def test_sources_page_delete_button_opens_confirm_panel(client, conn):
    sid = _seed(conn)
    resp = client.get("/sources")
    assert resp.status_code == 200
    assert f'hx-get="/sources/{sid}/delete-confirm"' in resp.text


def test_delete_confirm_panel_shows_job_count_and_link_to_jobs(client, conn):
    sid = _seed(conn)
    q.insert_job(conn, source_id=sid, url="http://finn.no/job/1", title="T1", company="C", raw_text="r")
    q.insert_job(conn, source_id=sid, url="http://finn.no/job/2", title="T2", company="C", raw_text="r")
    resp = client.get(f"/sources/{sid}/delete-confirm")
    assert resp.status_code == 200
    assert "Delete <strong>finn.no</strong> and its 2 jobs?" in resp.text
    assert f'href="/jobs?source_id={sid}"' in resp.text


def test_delete_confirm_panel_singular_for_one_job(client, conn):
    sid = _seed(conn)
    q.insert_job(conn, source_id=sid, url="http://finn.no/job/1", title="T1", company="C", raw_text="r")
    resp = client.get(f"/sources/{sid}/delete-confirm")
    assert resp.status_code == 200
    assert "Delete <strong>finn.no</strong> and its 1 job?" in resp.text


def test_delete_confirm_panel_no_jobs_link_when_zero_jobs(client, conn):
    sid = _seed(conn)
    resp = client.get(f"/sources/{sid}/delete-confirm")
    assert resp.status_code == 200
    assert "Delete <strong>finn.no</strong> and its 0 jobs?" in resp.text
    assert f'href="/jobs?source_id={sid}"' not in resp.text


def test_delete_confirm_panel_404_for_missing_source(client, conn):
    resp = client.get("/sources/999/delete-confirm")
    assert resp.status_code == 404


def test_update_source_response_includes_delete_button_with_current_job_count(client, conn):
    sid = _seed(conn)
    q.insert_job(conn, source_id=sid, url="http://finn.no/job/1", title="T1", company="C", raw_text="r")
    resp = client.post(
        f"/sources/{sid}",
        data={"name": "finn.no", "url": "https://finn.no", "fetcher_type": "http", "enabled": "on"},
    )
    assert resp.status_code == 200
    assert f'hx-get="/sources/{sid}/delete-confirm"' in resp.text


def test_slack_row_shows_cli_login_command(client, conn):
    sid = q.insert_source(conn, "Example Slack", _SLACK_URL, "slack")
    with patch.object(SlackFetcher, "check_needs_login", return_value=True):
        resp = client.get("/sources")
    assert resp.status_code == 200
    assert "python -m app.cli.slack_login" in resp.text
    assert _SLACK_URL in resp.text
    assert f"http://testserver/sources/{sid}/cookie" in resp.text
    assert "Log in via browser" not in resp.text
    assert "won't update automatically" in resp.text
