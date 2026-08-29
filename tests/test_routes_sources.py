from unittest.mock import patch, ANY, MagicMock
import httpx
import respx
from app.db import queries as q
from app.fetchers.slack import SlackFetcher
from app.fetchers.listing_detect import ListingDetection
from app.pipeline import FetchResult
from app.task_engine import execute_task

_SLACK_URL = "https://example-workspace.slack.com/archives/C0EXAMPLE1"


def _listing(html="<html><body>ok</body></html>",
             job_links=("https://careers.example.com/jobs/1", "https://careers.example.com/jobs/2")):
    return ListingDetection(html, True, list(job_links), False)


def _not_listing(html="<html><body><p>A single job posting.</p></body></html>"):
    return ListingDetection(html, False, [], False)


def _seed(conn):
    return q.insert_source(conn, "finn.no", "https://finn.no", "generic_listing")


def test_sources_page_returns_200(client, conn):
    _seed(conn)
    resp = client.get("/sources")
    assert resp.status_code == 200
    assert "finn.no" in resp.text


def test_source_row_has_source_row_class_for_hash_highlight(client, conn):
    sid = _seed(conn)
    resp = client.get("/sources")
    assert resp.status_code == 200
    assert f'id="source-row-{sid}" class="source-row"' in resp.text


def test_sources_page_shows_needs_login_for_slack_source_without_cookie(client, conn):
    q.insert_source(conn, "Example Slack", _SLACK_URL, "slack")
    resp = client.get("/sources")
    assert resp.status_code == 200
    assert "Needs Slack login" in resp.text
    assert "session cookie set" not in resp.text


def test_sources_page_shows_unverified_for_slack_source_with_cookie_but_no_fetch_yet(client, conn):
    sid = q.insert_source(conn, "Example Slack", _SLACK_URL, "slack")
    q.set_source_cookie(conn, sid, "xoxd-fake-cookie")
    resp = client.get("/sources")
    assert resp.status_code == 200
    assert "will be verified on the next fetch" in resp.text
    assert "Needs Slack login" not in resp.text
    assert "session cookie set" not in resp.text


def test_sources_page_shows_cookie_set_after_a_successful_fetch(client, conn):
    sid = q.insert_source(conn, "Example Slack", _SLACK_URL, "slack")
    q.set_source_cookie(conn, sid, "xoxd-fake-cookie")
    run_id = q.start_fetch_run(conn, sid)
    q.complete_fetch_run(conn, run_id, jobs_found=1, jobs_new=1)
    resp = client.get("/sources")
    assert resp.status_code == 200
    assert "session cookie set" in resp.text
    assert "Needs Slack login" not in resp.text


def test_sources_page_shows_needs_login_after_an_auth_error_fetch_run(client, conn):
    sid = q.insert_source(conn, "Example Slack", _SLACK_URL, "slack")
    q.set_source_cookie(conn, sid, "xoxd-fake-cookie")
    run_id = q.start_fetch_run(conn, sid)
    q.complete_fetch_run(conn, run_id, jobs_found=0, jobs_new=0, error="expired", auth_error=True)
    resp = client.get("/sources")
    assert resp.status_code == 200
    assert "Needs Slack login" in resp.text
    assert "session cookie set" not in resp.text


def test_sources_page_stays_ok_after_a_non_auth_error_following_success(client, conn):
    # A transient failure (network blip, rate limit) after a prior successful
    # fetch must not flip the badge to "needs login" -- only an auth-specific
    # failure should ever do that.
    sid = q.insert_source(conn, "Example Slack", _SLACK_URL, "slack")
    q.set_source_cookie(conn, sid, "xoxd-fake-cookie")
    ok_run = q.start_fetch_run(conn, sid)
    q.complete_fetch_run(conn, ok_run, jobs_found=1, jobs_new=1)
    bad_run = q.start_fetch_run(conn, sid)
    q.complete_fetch_run(conn, bad_run, jobs_found=0, jobs_new=0, error="timeout")
    resp = client.get("/sources")
    assert resp.status_code == 200
    assert "session cookie set" in resp.text
    assert "Needs Slack login" not in resp.text


def test_sources_page_does_not_contact_slack(client, conn):
    sid = q.insert_source(conn, "Example Slack", _SLACK_URL, "slack")
    q.set_source_cookie(conn, sid, "xoxd-fake-cookie")
    with patch.object(SlackFetcher, "check_needs_login", side_effect=AssertionError("must not check live")):
        resp = client.get("/sources")
    assert resp.status_code == 200


def test_sources_page_excludes_manual_source(client, conn):
    q.get_or_create_manual_source(conn)
    q.insert_source(conn, "Real Source", "http://x", "generic_listing")
    resp = client.get("/sources")
    assert resp.status_code == 200
    assert "Manual" not in resp.text
    assert "Real Source" in resp.text


def test_sources_page_table_has_wrapper_id(client, conn):
    _seed(conn)
    resp = client.get("/sources")
    assert resp.status_code == 200
    assert 'id="sources-table"' in resp.text


def test_post_sources_route_removed(client, conn):
    resp = client.post(
        "/sources",
        data={"name": "finn.no", "url": "https://finn.no", "fetcher_type": "http"},
    )
    assert resp.status_code == 405


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
        data={"name": "Finn AI", "url": "https://finn.no/new", "fetcher_type": "finn_listing", "enabled": "on"},
    )
    assert resp.status_code == 200
    source = q.get_source(conn, sid)
    assert source["url"] == "https://finn.no/new"
    assert source["fetcher_type"] == "finn_listing"
    assert source["enabled"] == 1
    assert source["name"] == "Finn AI"


def test_update_source_unchecked_enabled_disables(client, conn):
    sid = _seed(conn)
    resp = client.post(
        f"/sources/{sid}",
        data={"name": "finn.no", "url": "https://finn.no", "fetcher_type": "generic_listing"},
    )
    assert resp.status_code == 200
    assert q.get_source(conn, sid)["enabled"] == 0


def test_cancel_edit_returns_display_row(client, conn):
    sid = _seed(conn)
    resp = client.get(f"/sources/{sid}")
    assert resp.status_code == 200
    assert "finn.no" in resp.text


def test_update_source_to_slack_shows_login_prompt_when_needed(client, conn):
    sid = _seed(conn)
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
    resp_before = client.get("/sources")
    assert f'hx-post="/sources/{sid}/forget-cookie"' not in resp_before.text

    q.set_source_cookie(conn, sid, "xoxd-pasted")
    resp_after = client.get("/sources")
    assert f'hx-post="/sources/{sid}/forget-cookie"' in resp_after.text


def test_slack_row_shows_cookie_security_warning(client, conn):
    q.insert_source(conn, "Example Slack", _SLACK_URL, "slack")
    resp = client.get("/sources")
    assert resp.status_code == 200
    assert "every Slack workspace" in resp.text  # warning copy present
    assert 'name="acknowledged"' in resp.text     # required checkbox present


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
        data={"name": "finn.no", "url": "https://finn.no", "fetcher_type": "generic_listing", "enabled": "on"},
    )
    assert resp.status_code == 200
    assert f'hx-get="/sources/{sid}/delete-confirm"' in resp.text


def test_slack_row_shows_cli_login_command(client, conn):
    sid = q.insert_source(conn, "Example Slack", _SLACK_URL, "slack")
    resp = client.get("/sources")
    assert resp.status_code == 200
    assert "python -m app.cli.slack_login" in resp.text
    assert _SLACK_URL in resp.text
    assert f"http://testserver/sources/{sid}/cookie" in resp.text
    assert "Log in via browser" not in resp.text
    assert "won't update automatically" in resp.text


def _run_detect(conn, url):
    task = q.enqueue_task(conn, kind="source_detect", params={"url": url})
    execute_task(conn, MagicMock(), MagicMock(), MagicMock(), task)
    return q.get_task(conn, task["id"])


def test_detect_source_enqueues_task(client, conn):
    resp = client.post("/sources/detect", data={"url": "https://example.com/jobs"})
    assert resp.status_code == 200
    task = q.get_task(conn, resp.json()["task_id"])
    assert task["kind"] == "source_detect"
    assert task["params"]["url"] == "https://example.com/jobs"


def test_detect_source_already_tracked_as_source_skips_detection(conn):
    sid = q.insert_source(conn, "Careers Page", "https://careers.example.com/jobs", "generic_listing")
    with patch("app.routes.sources.detect_listing_page") as mock_fetch:
        fetched = _run_detect(conn, "https://careers.example.com/jobs")
    mock_fetch.assert_not_called()
    assert fetched["result"].get("needs_action") is None
    notice_html = fetched["result"]["notices"][0]["html"]
    assert "Already tracked as a source" in notice_html
    assert f'href="/sources#source-row-{sid}"' in notice_html


def test_detect_source_already_tracked_as_job_skips_detection(conn):
    sid = q.insert_source(conn, "Manual", "", "manual")
    jid = q.insert_job(conn, source_id=sid, url="https://example.com/job/1", title="T", company="C", raw_text="r")
    with patch("app.routes.sources.detect_listing_page") as mock_fetch:
        fetched = _run_detect(conn, "https://example.com/job/1")
    mock_fetch.assert_not_called()
    notice_html = fetched["result"]["notices"][0]["html"]
    assert "Already tracked as a job" in notice_html
    assert f'href="/jobs/{jid}"' in notice_html


def test_confirm_source_rejects_url_already_tracked_as_source(client, conn):
    sid = q.insert_source(conn, "Careers Page", "https://careers.example.com/jobs", "generic_listing")
    resp = client.post(
        "/sources/detect/confirm",
        data={"url": "https://careers.example.com/jobs", "name": "Dup", "fetcher_type": "generic_listing"},
    )
    task = q.get_task(conn, resp.json()["task_id"])
    execute_task(conn, MagicMock(), MagicMock(), MagicMock(browser_profile_dir="/tmp"), task)
    fetched = q.get_task(conn, task["id"])
    assert "Already tracked as a source" in fetched["result"]["notices"][0]["html"]
    assert len(q.get_sources(conn)) == 1
    assert q.get_source(conn, sid)["name"] == "Careers Page"


def test_confirm_source_rejects_url_already_tracked_as_job(client, conn):
    sid = q.insert_source(conn, "Manual", "", "manual")
    jid = q.insert_job(conn, source_id=sid, url="https://example.com/job/1", title="T", company="C", raw_text="r")
    resp = client.post(
        "/sources/detect/confirm",
        data={"url": "https://example.com/job/1", "name": "Dup", "fetcher_type": "generic_listing"},
    )
    task = q.get_task(conn, resp.json()["task_id"])
    execute_task(conn, MagicMock(), MagicMock(), MagicMock(browser_profile_dir="/tmp"), task)
    fetched = q.get_task(conn, task["id"])
    notice_html = fetched["result"]["notices"][0]["html"]
    assert "Already tracked as a job" in notice_html
    assert f'href="/jobs/{jid}"' in notice_html
    assert [s for s in q.get_sources(conn) if s["fetcher_type"] != "manual"] == []


def test_detect_source_fast_path_slack_skips_fetch(conn):
    with patch("app.routes.sources.detect_listing_page") as mock_fetch:
        fetched = _run_detect(conn, _SLACK_URL)
    mock_fetch.assert_not_called()
    html = fetched["result"]["html_chunks"][0]
    assert "Detected type: <strong>slack</strong>" in html
    assert f'value="{_SLACK_URL}"' in html


def test_detect_source_fast_path_finn_skips_fetch(conn):
    with patch("app.routes.sources.detect_listing_page") as mock_fetch:
        fetched = _run_detect(conn, "https://www.finn.no/job/search")
    mock_fetch.assert_not_called()
    assert "Detected type: <strong>finn_listing</strong>" in fetched["result"]["html_chunks"][0]


def test_detect_source_slow_path_listing_detected(conn):
    with patch("app.routes.sources.detect_listing_page", return_value=_listing(
        html="<html><body><a href='/jobs/1'>A</a><a href='/jobs/2'>B</a></body></html>")):
        fetched = _run_detect(conn, "https://careers.example.com/jobs")
    html = fetched["result"]["html_chunks"][0]
    assert "Detected type: <strong>generic_listing</strong>" in html
    assert 'value="careers.example.com"' in html  # default name


def test_detect_source_confirm_panel_has_name_label(conn):
    with patch("app.routes.sources.detect_listing_page") as mock_fetch:
        fetched = _run_detect(conn, _SLACK_URL)
    mock_fetch.assert_not_called()
    html = fetched["result"]["html_chunks"][0]
    assert '<label for="detect-name"' in html
    assert ">Name:</label>" in html


def test_detect_source_confirm_panel_has_cancel_link(conn):
    with patch("app.routes.sources.detect_listing_page") as mock_fetch:
        fetched = _run_detect(conn, _SLACK_URL)
    mock_fetch.assert_not_called()
    assert '<a href="/sources" class="btn btn-subtle">Cancel</a>' in fetched["result"]["html_chunks"][0]


def test_detect_source_mismatch_panel_has_cancel_link(conn):
    with patch("app.routes.sources.detect_listing_page", return_value=_not_listing()):
        fetched = _run_detect(conn, "https://example.com/job/1")
    assert '<a href="/sources" class="btn btn-subtle">Cancel</a>' in fetched["result"]["html_chunks"][0]


def test_detect_source_slow_path_not_a_listing_shows_mismatch(conn):
    with patch("app.routes.sources.detect_listing_page", return_value=_not_listing()):
        fetched = _run_detect(conn, "https://example.com/job/1")
    html = fetched["result"]["html_chunks"][0]
    assert "looks like a single job posting" in html
    assert 'data-progress-url="/jobs/add-by-url"' in html
    assert "Add as source anyway" in html


def test_detect_source_mismatch_panel_stacks_error_above_action_buttons(conn):
    # The error line and the action-button row must be separate stacked blocks,
    # not squeezed onto one flex line inside the add-source form.
    with patch("app.routes.sources.detect_listing_page", return_value=_not_listing()):
        fetched = _run_detect(conn, "https://example.com/job/1")
    html = fetched["result"]["html_chunks"][0]
    assert "flex-direction:column" in html
    assert html.index("not a listing of multiple jobs") < html.index('data-progress-url="/jobs/add-by-url"')


@respx.mock
def test_detect_source_fetch_failure_falls_back_to_generic_listing(conn):
    respx.get("https://example.com/unreachable").mock(side_effect=httpx.ConnectError("boom"))
    fetched = _run_detect(conn, "https://example.com/unreachable")
    assert "Detected type: <strong>generic_listing</strong>" in fetched["result"]["html_chunks"][0]


def test_detect_source_uses_generated_name_when_page_has_title(conn):
    html = ("<html><head><title>Frontend Developer Jobs in Oslo | Careers</title></head>"
            "<body><a href='/jobs/1'>A</a><a href='/jobs/2'>B</a></body></html>")
    with patch("app.routes.sources.detect_listing_page", return_value=_listing(html=html)), \
         patch("app.routes.sources.generate_source_name", return_value="careers/frontend-oslo") as mock_gen:
        fetched = _run_detect(conn, "https://careers.example.com/jobs")
    mock_gen.assert_called_once_with(
        ANY, ANY, "careers.example.com", "Frontend Developer Jobs in Oslo | Careers"
    )
    assert 'value="careers/frontend-oslo"' in fetched["result"]["html_chunks"][0]


def test_detect_source_falls_back_to_domain_when_name_generation_fails(conn):
    html = ("<html><head><title>Frontend Developer Jobs</title></head>"
            "<body><a href='/jobs/1'>A</a><a href='/jobs/2'>B</a></body></html>")
    with patch("app.routes.sources.detect_listing_page", return_value=_listing(html=html)), \
         patch("app.routes.sources.generate_source_name", return_value=None):
        fetched = _run_detect(conn, "https://careers.example.com/jobs")
    assert 'value="careers.example.com"' in fetched["result"]["html_chunks"][0]


def test_detect_source_mismatch_panel_also_uses_generated_name(conn):
    html = "<html><head><title>Senior Engineer at Acme</title></head><body><p>Role details.</p></body></html>"
    with patch("app.routes.sources.detect_listing_page", return_value=_not_listing(html=html)), \
         patch("app.routes.sources.generate_source_name", return_value="acme/senior-engineer"):
        fetched = _run_detect(conn, "https://example.com/job/1")
    html = fetched["result"]["html_chunks"][0]
    assert "looks like a single job posting" in html
    assert 'value="acme/senior-engineer"' in html


def _fake_run_fetch(source, conn, client, model, profile_dir):
    yield f"Starting fetch for '{source['name']}'"
    yield "Fetch complete"
    return FetchResult(source_id=source["id"], run_id=1, jobs_found=1, jobs_new=1, error=None)


def _fake_run_fetch_nothing_found(source, conn, client, model, profile_dir):
    yield f"Starting fetch for '{source['name']}'"
    yield "Fetch complete for '{}': 0 new / 0 found".format(source["name"])
    return FetchResult(source_id=source["id"], run_id=1, jobs_found=0, jobs_new=0, error=None)


def _fake_run_fetch_error(source, conn, client, model, profile_dir):
    yield f"Starting fetch for '{source['name']}'"
    yield "Fetch failed for '{}': boom".format(source["name"])
    return FetchResult(source_id=source["id"], run_id=1, jobs_found=0, jobs_new=0, error="boom")


def _run_confirm(conn, url, name, fetcher_type):
    task = q.enqueue_task(conn, kind="source_confirm", params={"url": url, "name": name, "fetcher_type": fetcher_type})
    execute_task(conn, MagicMock(), MagicMock(), MagicMock(browser_profile_dir="/tmp"), task)
    return q.get_task(conn, task["id"])


def _followups(conn):
    return [i for i in q.get_unresolved_inbox_items(conn) if i["kind"] == "task_followup"]


def test_source_detect_twice_for_same_url_leaves_a_single_prompt(conn):
    url = "https://careers.example.com/jobs"
    with patch("app.routes.sources.detect_listing_page", return_value=_not_listing()):
        _run_detect(conn, url)
        _run_detect(conn, url)
    assert len(_followups(conn)) == 1


def test_source_confirm_resolves_open_detect_prompt(conn):
    url = "https://careers.example.com/jobs"
    with patch("app.routes.sources.detect_listing_page", return_value=_listing()):
        _run_detect(conn, url)
    assert _followups(conn)
    with patch("app.routes.sources.run_fetch", side_effect=_fake_run_fetch):
        _run_confirm(conn, url, "careers.example.com", "generic_listing")
    assert not _followups(conn)


def test_confirm_source_enqueues_task(client, conn):
    resp = client.post(
        "/sources/detect/confirm",
        data={"url": "https://example.com/jobs", "name": "Example", "fetcher_type": "generic_listing"},
    )
    assert resp.status_code == 200
    task = q.get_task(conn, resp.json()["task_id"])
    assert task["kind"] == "source_confirm"


def test_confirm_source_creates_source_and_executes_fetch(conn):
    with patch("app.routes.sources.run_fetch", side_effect=_fake_run_fetch):
        fetched = _run_confirm(conn, "https://careers.example.com/jobs", "careers.example.com", "generic_listing")
    assert "Fetch complete" in fetched["log"]
    sources = q.get_sources(conn)
    assert len(sources) == 1
    assert sources[0]["fetcher_type"] == "generic_listing"
    assert sources[0]["name"] == "careers.example.com"


def test_confirm_source_response_has_both_oob_chunks(conn):
    with patch("app.routes.sources.run_fetch", side_effect=_fake_run_fetch):
        fetched = _run_confirm(conn, "https://careers.example.com/jobs", "careers.example.com", "generic_listing")
    chunks = fetched["result"]["html_chunks"]
    assert '<div id="sources-table">' in chunks[0]
    assert '<form id="add-source-panel"' in chunks[1]


def test_confirm_source_no_notice_when_jobs_found(conn):
    with patch("app.routes.sources.run_fetch", side_effect=_fake_run_fetch):
        fetched = _run_confirm(conn, "https://careers.example.com/jobs", "careers.example.com", "generic_listing")
    assert fetched["result"]["notices"] == []


def test_confirm_source_shows_persistent_notice_when_nothing_found(conn):
    with patch("app.routes.sources.run_fetch", side_effect=_fake_run_fetch_nothing_found):
        fetched = _run_confirm(conn, "https://careers.example.com/jobs", "careers.example.com", "generic_listing")
    notice = fetched["result"]["notices"][0]
    assert notice["level"] == "warning"
    assert "careers.example.com" in notice["html"]
    sources = q.get_sources(conn)
    assert len(sources) == 1
    assert f'href="/fetch#fetch-row-{sources[0]["id"]}"' in notice["html"]


def test_confirm_source_shows_persistent_notice_on_fetch_error(conn):
    with patch("app.routes.sources.run_fetch", side_effect=_fake_run_fetch_error):
        fetched = _run_confirm(conn, "https://careers.example.com/jobs", "careers.example.com", "generic_listing")
    notice = fetched["result"]["notices"][0]
    assert notice["level"] == "warning"
    assert "boom" in notice["html"]


def test_sources_page_has_notice_stack(client, conn):
    resp = client.get("/sources")
    assert resp.status_code == 200
    assert 'id="notice-stack"' in resp.text


def test_confirm_source_rejects_invalid_fetcher_type(client, conn):
    resp = client.post(
        "/sources/detect/confirm",
        data={"url": "https://example.com", "name": "example.com", "fetcher_type": "http"},
    )
    assert resp.status_code == 400
    assert q.get_sources(conn) == []


def test_confirm_source_slack_shows_needs_login(conn):
    with patch("app.routes.sources.run_fetch", side_effect=_fake_run_fetch):
        fetched = _run_confirm(conn, _SLACK_URL, "Example Slack", "slack")
    assert "Needs Slack login" in fetched["result"]["html_chunks"][0]


def test_sources_page_has_add_source_unfold(client, conn):
    resp = client.get("/sources")
    assert resp.status_code == 200
    assert "+ Add" in resp.text
    assert 'data-progress-url="/sources/detect"' in resp.text
    assert 'id="add-source-panel"' in resp.text


def test_edit_form_includes_generic_listing_option(client, conn):
    sid = q.insert_source(conn, "mlai.work/norway", "https://mlai.work/l/norway", "generic_listing")
    resp = client.get(f"/sources/{sid}/edit")
    assert resp.status_code == 200
    assert '<option value="generic_listing" selected>generic_listing</option>' in resp.text
