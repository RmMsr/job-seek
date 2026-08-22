from app.db import queries as q


def test_tasks_active_lists_queued_and_running(client, conn):
    q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1})
    resp = client.get("/tasks/active")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["tasks"]) == 1
    assert data["tasks"][0]["kind"] == "fetch_source"
    assert data["tasks"][0]["status"] == "queued"
    assert data["inbox_count"] == 0


def test_tasks_active_excludes_done(client, conn):
    task = q.enqueue_task(conn, kind="fetch_source", params={})
    q.complete_task(conn, task["id"], {})
    resp = client.get("/tasks/active")
    assert resp.json()["tasks"] == []


def test_task_detail_includes_result(client, conn):
    task = q.enqueue_task(conn, kind="fetch_source", params={})
    q.complete_task(conn, task["id"], {"html_chunks": ["<p>x</p>"]})
    resp = client.get(f"/tasks/{task['id']}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "done"
    assert data["result"] == {"html_chunks": ["<p>x</p>"]}


def test_task_detail_includes_full_log(client, conn):
    task = q.enqueue_task(conn, kind="fetch_source", params={})
    q.append_task_log(conn, task["id"], "line one")
    q.append_task_log(conn, task["id"], "line two")
    resp = client.get(f"/tasks/{task['id']}")
    assert resp.json()["log"] == "line one\nline two\n"


def test_task_detail_404_for_missing(client, conn):
    resp = client.get("/tasks/999")
    assert resp.status_code == 404


def test_tasks_active_extracts_progress_from_last_line(client, conn):
    task = q.enqueue_task(conn, kind="fetch_source", params={})
    q.append_task_log(conn, task["id"], "[2/5] Classified as job_posting: http://x")
    resp = client.get("/tasks/active")
    progress = resp.json()["tasks"][0]["progress"]
    assert progress == {"current": 2, "total": 5, "percent": 40}


def test_tasks_active_progress_none_when_no_bracket_pattern(client, conn):
    task = q.enqueue_task(conn, kind="fetch_source", params={})
    q.append_task_log(conn, task["id"], "Checking the page...")
    resp = client.get("/tasks/active")
    assert resp.json()["tasks"][0]["progress"] is None


def test_tasks_active_progress_takes_innermost_bracket_pair(client, conn):
    task = q.enqueue_task(conn, kind="fetch_source", params={})
    q.append_task_log(conn, task["id"], "[Source 1/2: finn.no] [3/5] Classified as job_posting: http://x")
    resp = client.get("/tasks/active")
    progress = resp.json()["tasks"][0]["progress"]
    assert progress == {"current": 3, "total": 5, "percent": 60}


def test_task_log_page_renders_lines_and_status(client, conn):
    task = q.enqueue_task(conn, kind="fetch_source", params={})
    q.append_task_log(conn, task["id"], "first line")
    q.append_task_log(conn, task["id"], "second line")
    resp = client.get(f"/tasks/{task['id']}/log")
    assert resp.status_code == 200
    assert "first line" in resp.text
    assert "second line" in resp.text
    assert "queued" in resp.text


def test_task_log_page_404_for_missing(client, conn):
    resp = client.get("/tasks/999/log")
    assert resp.status_code == 404


def test_task_resume_renders_resume_html(client, conn):
    task = q.enqueue_task(conn, kind="fetch_source", params={})
    q.complete_task(conn, task["id"], {"resume_html": "<p>confirm me</p>"})
    resp = client.get(f"/tasks/{task['id']}/resume")
    assert resp.status_code == 200
    assert "confirm me" in resp.text


def test_task_resume_includes_inbox_item_id_when_present(client, conn):
    task = q.enqueue_task(conn, kind="fetch_source", params={})
    q.complete_task(conn, task["id"], {"resume_html": "<p>confirm me</p>"})
    q.create_inbox_item(conn, kind="task_followup", message="x", link="/y", task_id=task["id"])
    resp = client.get(f"/tasks/{task['id']}/resume")
    assert f'data-inbox-item-id="{q.get_inbox_item_by_task_id(conn, task["id"])["id"]}"' in resp.text


def test_task_resume_omits_inbox_item_id_when_absent(client, conn):
    task = q.enqueue_task(conn, kind="fetch_source", params={})
    q.complete_task(conn, task["id"], {"resume_html": "<p>confirm me</p>"})
    resp = client.get(f"/tasks/{task['id']}/resume")
    # "data-inbox-item-id" itself also appears inside base.html's JS (as a
    # getAttribute() string argument) on every page — check for the actual
    # HTML attribute syntax, not just the substring.
    assert 'data-inbox-item-id="' not in resp.text


def test_task_resume_404_without_resume_html(client, conn):
    task = q.enqueue_task(conn, kind="fetch_source", params={})
    q.complete_task(conn, task["id"], {})
    resp = client.get(f"/tasks/{task['id']}/resume")
    assert resp.status_code == 404
