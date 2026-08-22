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


from app.task_engine import execute_task


def test_post_fetch_enqueues_task(client, conn):
    sid = _seed(conn)
    resp = client.post(f"/fetch/{sid}")
    assert resp.status_code == 200
    data = resp.json()
    task = q.get_task(conn, data["task_id"])
    assert task["kind"] == "fetch_source"
    assert task["params"] == {"source_id": sid}
    assert task["status"] == "queued"
    assert data["already_active"] is False


def test_post_fetch_reports_already_active_on_second_click(client, conn):
    sid = _seed(conn)
    first = client.post(f"/fetch/{sid}").json()
    second = client.post(f"/fetch/{sid}").json()
    assert first["task_id"] == second["task_id"]
    assert second["already_active"] is True


def test_post_fetch_unknown_source_returns_404(client, conn):
    resp = client.post("/fetch/999")
    assert resp.status_code == 404


def _fake_run_fetch(*args, **kwargs):
    yield "Starting fetch for 'finn.no' (http)"
    yield "Fetch complete for 'finn.no': 2 new / 3 found"
    return FetchResult(source_id=1, run_id=1, jobs_found=3, jobs_new=2, error=None)


def test_fetch_source_task_execution_updates_log_and_status(conn):
    sid = _seed(conn)
    run_id = q.start_fetch_run(conn, sid)
    q.complete_fetch_run(conn, run_id, jobs_found=3, jobs_new=2)
    task = q.enqueue_task(conn, kind="fetch_source", params={"source_id": sid})
    with patch("app.routes.fetch.run_fetch", side_effect=_fake_run_fetch):
        execute_task(conn, MagicMock(), "model", MagicMock(browser_profile_dir="/tmp"), task)
    fetched = q.get_task(conn, task["id"])
    assert fetched["status"] == "done"
    assert "Fetch complete for 'finn.no': 2 new / 3 found" in fetched["log"]


def test_fetch_source_task_flags_auth_error_as_needing_action(conn):
    sid = _seed(conn)

    def fake_fetch(*args, **kwargs):
        run_id = q.start_fetch_run(conn, sid)
        q.complete_fetch_run(conn, run_id, jobs_found=0, jobs_new=0, error="auth", auth_error=True)
        yield "Fetch failed"
        return FetchResult(source_id=sid, run_id=run_id, jobs_found=0, jobs_new=0, error="auth")

    task = q.enqueue_task(conn, kind="fetch_source", params={"source_id": sid})
    with patch("app.routes.fetch.run_fetch", side_effect=fake_fetch):
        execute_task(conn, MagicMock(), "model", MagicMock(browser_profile_dir="/tmp"), task)
    fetched = q.get_task(conn, task["id"])
    assert fetched["result"]["needs_action"] is True
    assert "reconnect Slack" in fetched["result"]["action_message"]
    items = q.get_unresolved_inbox_items(conn)
    assert len(items) == 1


def test_fetch_all_task_execution_aggregates_and_renders_table(conn):
    sid1 = q.insert_source(conn, "finn.no", "https://finn.no", "generic_listing")
    sid2 = q.insert_source(conn, "other.no", "https://other.no", "generic_listing")

    def fake_fetch_all(source, conn, *args, **kwargs):
        run_id = q.start_fetch_run(conn, source["id"])
        yield f"Starting fetch for '{source['name']}'"
        q.complete_fetch_run(conn, run_id, jobs_found=1, jobs_new=1)
        return FetchResult(source_id=source["id"], run_id=run_id, jobs_found=1, jobs_new=1, error=None)

    task = q.enqueue_task(conn, kind="fetch_all", params={"source_ids": [sid1, sid2]})
    with patch("app.routes.fetch.run_fetch", side_effect=fake_fetch_all):
        execute_task(conn, MagicMock(), "model", MagicMock(browser_profile_dir="/tmp"), task)
    fetched = q.get_task(conn, task["id"])
    assert fetched["status"] == "done"
    assert fetched["result"]["notices"][0]["html"]
    assert "2" in fetched["result"]["notices"][0]["html"]
    assert f'id="fetch-row-{sid1}"' in fetched["result"]["html_chunks"][0]


def test_fetch_all_with_no_sources_has_no_html_chunk(conn):
    task = q.enqueue_task(conn, kind="fetch_all", params={"source_ids": []})
    execute_task(conn, MagicMock(), "model", MagicMock(browser_profile_dir="/tmp"), task)
    fetched = q.get_task(conn, task["id"])
    assert fetched["result"]["html_chunks"] == []
