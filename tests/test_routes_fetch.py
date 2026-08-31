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


def test_fetch_table_last_run_shows_relative_age_not_raw_timestamp(client, conn):
    sid = _seed(conn)
    run = q.start_fetch_run(conn, sid)
    q.complete_fetch_run(conn, run, jobs_found=1, jobs_new=1)
    raw_ts = q.get_fetch_stats_by_source(conn)[sid]["last_success_at"]

    html = client.get("/fetch").text
    # The "Last run" column renders the coarse `age` filter as its text …
    assert "just now" in html or "ago" in html
    # … and keeps the exact timestamp only on hover, never as displayed text.
    assert f'title="{raw_ts} UTC"' in html
    assert f">{raw_ts}<" not in html


def test_last_run_links_to_task_with_success_glyph(client, conn):
    sid = q.insert_source(conn, "S", "https://s.test", "generic_listing")
    task = q.enqueue_task(conn, kind="fetch_source", params={"source_id": sid})
    run_id = q.start_fetch_run(conn, sid, task_id=task["id"])
    q.complete_fetch_run(conn, run_id, jobs_found=3, jobs_new=1)
    html = client.get("/fetch").text
    assert f'/tasks/{task["id"]}' in html
    assert "✓" in html


def test_last_run_shows_failure_glyph(client, conn):
    sid = q.insert_source(conn, "S2", "https://s2.test", "generic_listing")
    task = q.enqueue_task(conn, kind="fetch_source", params={"source_id": sid})
    run_id = q.start_fetch_run(conn, sid, task_id=task["id"])
    q.complete_fetch_run(conn, run_id, jobs_found=0, jobs_new=0, error="boom")
    html = client.get("/fetch").text
    assert "✗" in html


def test_fetch_table_links_source_name_to_sources_page(client, conn):
    src_id = q.insert_source(conn, "Acme Board", "https://acme.example/jobs", "generic_listing")
    html = client.get("/fetch").text
    assert f'href="/sources#source-row-{src_id}"' in html
    assert "Acme Board" in html


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
    assert fetched["result"]["action_link"] == f"/sources#source-row-{sid}"
    assert fetched["status"] == "needs_action"
    # needs_action is now a task status, not a mirrored inbox item.
    assert [i for i in q.get_unresolved_inbox_items(conn) if i["kind"] == "task_followup"] == []


def test_post_fetch_all_enqueues_one_root_that_fans_out_children(client, conn):
    sid1 = q.insert_source(conn, "finn.no", "https://finn.no", "generic_listing")
    sid2 = q.insert_source(conn, "other.no", "https://other.no", "generic_listing")
    resp = client.post("/fetch/all")
    assert resp.status_code == 200
    root_id = resp.json()["task_id"]
    root = q.get_task(conn, root_id)
    assert root["kind"] == "fetch_all" and root["parent_task_id"] is None
    execute_task(conn, MagicMock(), "m", MagicMock(browser_profile_dir="/tmp"), root)
    kids = q.get_task_children(conn, root_id)
    assert {k["kind"] for k in kids} == {"fetch_source"}
    assert {k["params"]["source_id"] for k in kids} == {sid1, sid2}
    assert all(k["parent_task_id"] == root_id for k in kids)


def test_fetch_all_child_fanout_excludes_manual_and_disabled(client, conn):
    q.get_or_create_manual_source(conn)
    disabled = q.insert_source(conn, "off.no", "https://off.no", "generic_listing")
    q.update_source(conn, disabled, name="off.no", url="https://off.no", fetcher_type="generic_listing", enabled=False)
    q.insert_source(conn, "on.no", "https://on.no", "generic_listing")
    resp = client.post("/fetch/all")
    root_id = resp.json()["task_id"]
    execute_task(conn, MagicMock(), "m", MagicMock(browser_profile_dir="/tmp"), q.get_task(conn, root_id))
    assert len(q.get_task_children(conn, root_id)) == 1


def test_post_fetch_all_dedupes_a_still_running_root(client, conn):
    q.insert_source(conn, "finn.no", "https://finn.no", "generic_listing")
    first = client.post("/fetch/all").json()["task_id"]
    second = client.post("/fetch/all").json()
    assert second["task_id"] == first
    assert second["already_active"] is True


def test_post_fetch_all_with_no_sources_is_skipped(client, conn):
    resp = client.post("/fetch/all")
    assert resp.status_code == 200
    assert resp.json()["skipped"] is True
