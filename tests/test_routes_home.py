from app.config import check_config_status
from app.db import queries as q
from app.deps import get_db_optional
from app.main import app


def _use_test_db(conn):
    app.dependency_overrides[get_db_optional] = lambda: conn


def _point_config_check_at(monkeypatch, path):
    # check_config_status() is called with no args (default path="config.toml",
    # resolved relative to cwd) from both home.py and deps.py. Patching the
    # default directly is more targeted than monkeypatch.chdir, which would
    # affect every relative path in the process, not just this one.
    monkeypatch.setattr(check_config_status, "__defaults__", (str(path),))


def _use_valid_config(monkeypatch, tmp_path):
    # Points check_config_status() at a minimal config that reads as "ok"
    # (has an llm endpoint, no provider so no model is required) — so these
    # tests exercise the checklist/dashboard body regardless of whether a
    # real config.toml happens to exist in the process's cwd.
    config_path = tmp_path / "config.toml"
    config_path.write_text('[llm]\nendpoint = "http://localhost:8080/v1"\n')
    _point_config_check_at(monkeypatch, config_path)


def test_home_missing_config_shows_banner(client, monkeypatch, tmp_path):
    _point_config_check_at(monkeypatch, tmp_path / "config.toml")
    resp = client.get("/")
    assert resp.status_code == 200
    assert "No config.toml found" in resp.text
    assert 'class="checklist"' not in resp.text
    assert "How it works" in resp.text


def test_home_config_without_inference_provider_shows_banner(client, monkeypatch, tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[llm]\nendpoint = ""\nmodel = ""\n\n[database]\npath = "x.db"\n\n[browser]\nprofile_dir = "x"\n'
    )
    _point_config_check_at(monkeypatch, config_path)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "no inference provider configured" in resp.text
    assert "How it works" in resp.text


def test_home_valid_config_nothing_set_up_shows_checklist(client, conn, monkeypatch, tmp_path):
    _use_test_db(conn)
    _use_valid_config(monkeypatch, tmp_path)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Fill in your profile" in resp.text
    assert "Create a search scenario" in resp.text
    assert "Add a source" in resp.text
    assert "Run your first fetch" in resp.text
    assert "Review your first job" in resp.text
    assert "awaiting review" not in resp.text


def test_home_partial_setup_shows_mixed_checklist(client, conn, monkeypatch, tmp_path):
    _use_test_db(conn)
    _use_valid_config(monkeypatch, tmp_path)
    q.upsert_profile(conn, "Python engineer")
    q.insert_scenario(conn, "Remote ML", "")
    resp = client.get("/")
    assert resp.status_code == 200
    assert '<a href="/profile">✓ Fill in your profile</a>' in resp.text
    assert '<a href="/scenarios">✓ Create a search scenario</a>' in resp.text
    assert '<a href="/sources">○ Add a source</a>' in resp.text


def test_home_full_setup_shows_actionable_block(client, conn, monkeypatch, tmp_path):
    _use_test_db(conn)
    _use_valid_config(monkeypatch, tmp_path)
    q.upsert_profile(conn, "Python engineer")
    scenario_id = q.insert_scenario(conn, "Remote ML", "")
    source_id = q.insert_source(conn, "finn.no", "https://finn.no", "generic_listing")
    run_id = q.start_fetch_run(conn, source_id)
    q.complete_fetch_run(conn, run_id, jobs_found=1, jobs_new=1)

    job_id = q.insert_job(conn, source_id=source_id, url="http://finn.no/1", title="ML Eng", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, job_id, simplified_content="c", content_type="job_posting", summary="s")
    q.upsert_job_score(conn, job_id, scenario_id, 0.5, "ok", "hash1")
    q.update_job_feedback(conn, job_id, "accepted", "good fit")

    other_job_id = q.insert_job(conn, source_id=source_id, url="http://finn.no/2", title="Other", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, other_job_id, simplified_content="c", content_type="job_posting", summary="s")
    q.upsert_job_score(conn, other_job_id, scenario_id, 0.9, "ok", "hash1")  # above default gate_threshold 0.7 — stays "new"
    q.mark_job_evaluation_complete(conn, other_job_id)

    resp = client.get("/")
    assert resp.status_code == 200
    assert "1 job awaiting review" in resp.text
    assert "Last fetch:" in resp.text
    assert "Fill in your profile" not in resp.text


def test_home_shows_no_tasks_section_when_nothing_pending_or_resolved(client, conn):
    _use_test_db(conn)
    resp = client.get("/")
    assert "all tasks →" not in resp.text


def test_home_shows_active_task_card(client, conn):
    _use_test_db(conn)
    sid = q.insert_source(conn, "Cord", "https://cord.co", "generic_listing")
    q.enqueue_task(conn, kind="fetch_source", params={"source_id": sid})
    html = client.get("/").text
    assert "Fetch: Cord" in html
    assert "Queued" in html
    # rendered as a checklist <li>, not a bordered card
    assert 'class="checklist task-checklist"' in html
    assert "task-li" in html


def test_home_child_task_links_to_own_detail_not_a_group(client, conn):
    _use_test_db(conn)
    root = q.enqueue_task(conn, kind="fetch_all", params={})
    q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1}, parent_task_id=root["id"])
    html = client.get("/").text
    # the root row links to /tasks/{root}, never to a /tasks/group/ URL
    assert f'href="/tasks/{root["id"]}"' in html
    assert "/tasks/group/" not in html


def test_home_shows_needs_action_card_with_dismiss(client, conn):
    _use_test_db(conn)
    t = q.enqueue_task(conn, kind="source_detect", params={"url": "https://x.com"})
    q.complete_task(conn, t["id"], {"needs_action": True, "action_message": "Confirm the detected source"})
    q.set_task_needs_action(conn, t["id"])
    html = client.get("/").text
    assert "Confirm the detected source" in html
    assert f'/tasks/{t["id"]}/dismiss' in html


def test_home_shows_fetch_all_root_as_one_aggregate_row(client, conn):
    _use_test_db(conn)
    root = q.enqueue_task(conn, kind="fetch_all", params={})
    q.complete_task(conn, root["id"], {})
    c1 = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1}, parent_task_id=root["id"])
    q.enqueue_task(conn, kind="fetch_source", params={"source_id": 2}, parent_task_id=root["id"])
    html = client.get("/").text
    assert "Fetch all" in html
    assert "2 sources" in html  # count moved into the derived next-step line
    assert f'href="/tasks/{root["id"]}"' in html
    # children are not their own top-level rows
    assert f'href="/tasks/{c1["id"]}"' not in html


def test_home_done_task_without_results_has_no_second_line(client, conn):
    _use_test_db(conn)
    t = q.enqueue_task(conn, kind="source_confirm",
                       params={"url": "https://x.io", "name": "N", "fetcher_type": "generic_listing"})
    q.complete_task(conn, t["id"], {})  # no source_id -> no result links
    html = client.get("/").text
    li = html.split(f'task-card-{t["id"]}', 1)[1].split("</li>", 1)[0]
    assert "task-li-sub" not in li  # glyph + title + age only
    assert ">Done<" not in li and "✓ Done" not in li


def test_home_done_task_with_results_still_shows_them(client, conn):
    _use_test_db(conn)
    sid = q.insert_source(conn, "Cord", "https://cord.co", "generic_listing")
    t = q.enqueue_task(conn, kind="fetch_source", params={"source_id": sid})
    q.complete_task(conn, t["id"], {"jobs_new": 0})
    html = client.get("/").text
    li = html.split(f'task-card-{t["id"]}', 1)[1].split("</li>", 1)[0]
    assert "Jobs from Cord" in li


def test_home_multi_result_task_shows_only_the_headline(client, conn):
    _use_test_db(conn)
    sid = q.insert_source(conn, "s", "http://e", "manual")
    a = q.insert_job(conn, source_id=sid, url="http://e/a", title="Alpha", company="X", raw_text="x")
    b = q.insert_job(conn, source_id=sid, url="http://e/b", title="Beta", company="X", raw_text="x")
    t = q.enqueue_task(conn, kind="jobs_revisit", params={"job_ids": [a, b], "trigger": "manual"})
    q.complete_task(conn, t["id"], {"outcome": {"total": 2, "changed": [b], "closed": [a]}})
    li = _card_li(client.get("/").text, t["id"])
    assert "2 rechecked" in li
    # the per-job links belong on the task page, not the compact home card
    assert "Trashed: Alpha" not in li and "Updated: Beta" not in li


def _card_li(html, task_id):
    return html.split(f'task-card-{task_id}', 1)[1].split("</li>", 1)[0]


def test_home_needs_action_with_panel_shows_review(client, conn):
    _use_test_db(conn)
    t = q.enqueue_task(conn, kind="source_detect", params={"url": "https://x.io"})
    q.complete_task(conn, t["id"], {"needs_action": True, "action_message": "Confirm it",
                                    "resume_html": "<p>panel</p>"})
    q.set_task_needs_action(conn, t["id"])
    li = _card_li(client.get("/").text, t["id"])
    assert f'class="btn btn-primary" href="/tasks/{t["id"]}">Review</a>' in li
    assert f'<form method="post" action="/tasks/{t["id"]}/dismiss"><button type="submit" class="btn">Dismiss</button></form>' in li
    assert '<div class="task-li-actions">' in li
    assert "task-li-sub task-li-actions" not in li


def test_home_needs_action_no_panel_but_link_shows_open(client, conn):
    _use_test_db(conn)
    t = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1})
    q.complete_task(conn, t["id"], {"needs_action": True,
                                    "action_message": "Reconnect Slack to keep fetching",
                                    "action_link": "/sources#source-row-1"})
    q.set_task_needs_action(conn, t["id"])
    li = _card_li(client.get("/").text, t["id"])
    assert "Reconnect Slack to keep fetching" in li
    assert '<a class="btn btn-primary" href="/sources#source-row-1">Open</a>' in li
    assert ">Review</a>" not in li  # no dead Review → detail page
    assert f'action="/tasks/{t["id"]}/dismiss"' in li


def test_home_needs_action_no_panel_no_link_dismiss_only(client, conn):
    _use_test_db(conn)
    t = q.enqueue_task(conn, kind="source_detect", params={"url": "https://x.io"})
    q.complete_task(conn, t["id"], {"needs_action": True, "action_message": "Something is off"})
    q.set_task_needs_action(conn, t["id"])
    li = _card_li(client.get("/").text, t["id"])
    assert "Something is off" in li
    assert ">Review</a>" not in li and ">Open</a>" not in li
    assert f'action="/tasks/{t["id"]}/dismiss"' in li


def test_home_browser_missing_notice_dismiss_is_a_button(client, conn):
    _use_test_db(conn)
    q.create_inbox_item(conn, kind="browser_missing", message="Install chromium", link="/sources")
    html = client.get("/").text
    li = html.split("inbox-item-", 1)[1].split("</li>", 1)[0]
    assert '<div class="task-li-actions">' in li
    assert 'class="btn"' in li and "btn-link" not in li


def test_home_hides_old_done_task(client, conn):
    _use_test_db(conn)
    t = q.enqueue_task(conn, kind="fetch_source", params={})
    q.complete_task(conn, t["id"], {})
    conn.execute("UPDATE tasks SET finished_at = datetime('now','-2 days') WHERE id = ?", (t["id"],))
    conn.commit()
    assert f'/tasks/{t["id"]}"' not in client.get("/").text


def test_home_resolved_needs_action_card_shows_decided_summary(client, conn):
    _use_test_db(conn)
    t = q.enqueue_task(conn, kind="source_detect", params={"url": "https://x.com"})
    q.complete_task(conn, t["id"], {"needs_action": True, "resume_html": "<form id='live'>x</form>"})
    q.set_task_needs_action(conn, t["id"])
    q.dismiss_task(conn, t["id"])
    html = client.get("/").text
    assert "<form id='live'>" not in html
    # dismissed row: struck-through title, no live panel
    assert "task-completed" in html


def test_home_still_shows_browser_missing_notice(client, conn):
    _use_test_db(conn)
    q.create_inbox_item(conn, kind="browser_missing", message="Install chromium", link="/sources")
    assert "Install chromium" in client.get("/").text
