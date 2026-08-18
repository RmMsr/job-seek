import pytest
from unittest.mock import patch, MagicMock
from app.db import queries as q
from app.pipeline import FetchResult


def _seed(conn):
    return q.insert_source(conn, "finn.no", "https://finn.no", "generic_listing")


def test_fetch_panel_returns_200(client, conn):
    _seed(conn)
    resp = client.get("/fetch")
    assert resp.status_code == 200
    assert "finn.no" in resp.text


def test_fetch_panel_excludes_manual_source(client, conn):
    q.get_or_create_manual_source(conn)
    q.insert_source(conn, "Real Source", "http://x", "generic_listing")
    resp = client.get("/fetch")
    assert resp.status_code == 200
    assert "Manual" not in resp.text
    assert "Real Source" in resp.text


def test_fetch_panel_empty_sources(client, conn):
    resp = client.get("/fetch")
    assert resp.status_code == 200


def _fake_run_fetch(*args, **kwargs):
    yield "Starting fetch for 'finn.no' (http)"
    yield "Fetched 3 raw posting(s) from 'finn.no'"
    yield "Fetch complete for 'finn.no': 2 new / 3 found"
    return FetchResult(source_id=1, run_id=1, jobs_found=3, jobs_new=2, error=None)


def test_post_fetch_streams_progress(client, conn):
    sid = _seed(conn)
    with patch("app.routes.fetch.run_fetch", side_effect=_fake_run_fetch):
        resp = client.post(f"/fetch/{sid}")
    assert resp.status_code == 200
    assert "Starting fetch" in resp.text
    assert "Fetch complete for 'finn.no': 2 new / 3 found" in resp.text


def test_post_fetch_unknown_source_returns_404(client, conn):
    resp = client.post("/fetch/999")
    assert resp.status_code == 404


def test_fetch_panel_shows_lifetime_stats(client, conn):
    sid = _seed(conn)
    run = q.start_fetch_run(conn, sid)
    q.complete_fetch_run(conn, run, jobs_found=3, jobs_new=2)
    resp = client.get("/fetch")
    assert resp.status_code == 200
    assert "2 added" in resp.text
    assert "1 run" in resp.text


def test_fetch_panel_has_view_all_jobs_link_per_source(client, conn):
    sid = _seed(conn)
    resp = client.get("/fetch")
    assert resp.status_code == 200
    assert f'href="/jobs?source_id={sid}"' in resp.text


def test_fetch_panel_row_has_source_row_class_for_hash_highlight(client, conn):
    sid = _seed(conn)
    resp = client.get("/fetch")
    assert resp.status_code == 200
    assert f'id="fetch-row-{sid}" class="source-row"' in resp.text


def test_fetch_panel_does_not_repeat_source_url(client, conn):
    _seed(conn)
    resp = client.get("/fetch")
    assert resp.status_code == 200
    assert "https://finn.no" not in resp.text


def test_fetch_panel_has_fetch_all_button(client, conn):
    _seed(conn)
    resp = client.get("/fetch")
    assert resp.status_code == 200
    assert '/fetch/all' in resp.text
    assert '>Fetch all<' in resp.text
    assert 'data-progress-target="#fetch-content"' in resp.text


def test_fetch_panel_has_notice_stack_and_content_wrapper(client, conn):
    _seed(conn)
    resp = client.get("/fetch")
    assert resp.status_code == 200
    assert 'id="notice-stack"' in resp.text
    assert 'id="fetch-content"' in resp.text


def _fake_run_fetch_all(source, conn, *args, **kwargs):
    run_id = q.start_fetch_run(conn, source["id"])
    yield f"Starting fetch for '{source['name']}' ({source['fetcher_type']})"
    yield f"Fetch complete for '{source['name']}': 1 new / 1 found"
    q.complete_fetch_run(conn, run_id, jobs_found=1, jobs_new=1)
    return FetchResult(source_id=source["id"], run_id=run_id, jobs_found=1, jobs_new=1, error=None)


def test_post_fetch_all_streams_each_enabled_source(client, conn):
    q.insert_source(conn, "finn.no", "https://finn.no", "generic_listing")
    q.insert_source(conn, "other.no", "https://other.no", "generic_listing")
    with patch("app.routes.fetch.run_fetch", side_effect=_fake_run_fetch_all):
        resp = client.post("/fetch/all")
    assert resp.status_code == 200
    assert "finn.no" in resp.text
    assert "other.no" in resp.text
    assert "2 source" in resp.text
    assert "2 new" in resp.text


def test_post_fetch_all_skips_disabled_and_manual_sources(client, conn):
    q.get_or_create_manual_source(conn)
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "generic_listing")
    other_id = q.insert_source(conn, "disabled.no", "https://disabled.no", "generic_listing")
    q.update_source(conn, other_id, name="disabled.no", url="https://disabled.no", fetcher_type="generic_listing", enabled=False)
    with patch("app.routes.fetch.run_fetch", side_effect=_fake_run_fetch_all):
        resp = client.post("/fetch/all")
    assert resp.status_code == 200
    progress_log = resp.text.split("NOTICE:", 1)[0]
    assert "finn.no" in progress_log
    assert "disabled.no" not in progress_log
    assert "Manual" not in resp.text


def test_post_fetch_all_emits_notice_line_with_aggregate_stats(client, conn):
    q.insert_source(conn, "finn.no", "https://finn.no", "generic_listing")
    q.insert_source(conn, "other.no", "https://other.no", "generic_listing")
    with patch("app.routes.fetch.run_fetch", side_effect=_fake_run_fetch_all):
        resp = client.post("/fetch/all")
    assert resp.status_code == 200
    notice_lines = [line for line in resp.text.split("\n") if line.startswith("NOTICE:")]
    assert len(notice_lines) == 1
    assert "2" in notice_lines[0]  # total_new
    assert "2 source" in notice_lines[0]


def test_post_fetch_all_emits_html_line_with_updated_table(client, conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "generic_listing")
    with patch("app.routes.fetch.run_fetch", side_effect=_fake_run_fetch_all):
        resp = client.post("/fetch/all")
    assert resp.status_code == 200
    html_lines = [line for line in resp.text.split("\n") if line.startswith("HTML:")]
    assert len(html_lines) == 1
    assert f'id="fetch-row-{sid}"' in html_lines[0]
    assert "1 / 1" in html_lines[0]


def test_post_fetch_all_with_no_enabled_sources_emits_notice_and_no_html(client, conn):
    q.get_or_create_manual_source(conn)
    resp = client.post("/fetch/all")
    assert resp.status_code == 200
    lines = resp.text.split("\n")
    notice_lines = [line for line in lines if line.startswith("NOTICE:")]
    html_lines = [line for line in lines if line.startswith("HTML:")]
    assert len(notice_lines) == 1
    assert "0" in notice_lines[0]
    assert "0 source" in notice_lines[0]
    assert html_lines == []
