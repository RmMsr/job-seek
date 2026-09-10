from app.db import queries as q
from app.routes.tasks import (
    task_presentation,
    root_presentation,
    _subtree_status,
    _next_step,
)


def test_presentation_fetch_source(conn):
    sid = q.insert_source(conn, "Cord", "https://cord.co", "generic_listing")
    t = q.enqueue_task(conn, kind="fetch_source", params={"source_id": sid})
    p = task_presentation(conn, t)
    assert p["title"] == "Fetch: Cord"
    assert "Cord" in p["goal"]
    assert p["next_step"].startswith("Queued")


def test_presentation_running_uses_progress(conn):
    t = q.enqueue_task(conn, kind="fetch_source", params={})
    q.claim_next_task(conn)
    q.append_task_log(conn, t["id"], "[3/6] Classified as job_posting: http://x")
    p = task_presentation(conn, q.get_task(conn, t["id"]))
    assert p["next_step"] == "Step 3 of 6"


def test_presentation_needs_action(conn):
    t = q.enqueue_task(conn, kind="source_detect", params={"url": "https://x.com"})
    q.complete_task(conn, t["id"], {"needs_action": True, "action_message": "Confirm the detected source"})
    q.set_task_needs_action(conn, t["id"])
    p = task_presentation(conn, q.get_task(conn, t["id"]))
    assert p["next_step"] == "Confirm the detected source"


def test_presentation_source_detect_title_names_host(conn):
    t = q.enqueue_task(conn, kind="source_detect", params={"url": "https://careers.acme.io/jobs"})
    assert task_presentation(conn, t)["title"] == "Add source: careers.acme.io"


def test_presentation_cv_tailor_generate_names_job_and_links_to_workbench(conn):
    sid = q.insert_source(conn, "S", "https://e.com", "generic_listing")
    jid = q.insert_job(conn, source_id=sid, url="https://e.com/j", title="ML Engineer @ Acme",
                       company="", raw_text="")
    t = q.enqueue_task(conn, kind="cv_tailor",
                       params={"job_id": jid, "mode": "generate", "render": "preview_pane"})
    q.complete_task(conn, t["id"], {"job_id": jid})
    p = task_presentation(conn, q.get_task(conn, t["id"]))
    assert p["title"] == "Update CV: ML Engineer @ Acme"
    assert "tailored cv" in p["goal"].lower()
    assert {"label": "CV for ML Engineer @ Acme", "href": f"/jobs/{jid}/cv"} in p["results"]


def test_presentation_cv_tailor_plan_is_directive_eval(conn):
    sid = q.insert_source(conn, "S", "https://e.com", "generic_listing")
    jid = q.insert_job(conn, source_id=sid, url="https://e.com/j", title="ML Engineer @ Acme",
                       company="", raw_text="")
    t = q.enqueue_task(conn, kind="cv_tailor",
                       params={"job_id": jid, "mode": "plan", "render": "plan_pane"})
    p = task_presentation(conn, t)
    assert p["title"] == "Evaluate CV directives: ML Engineer @ Acme"
    assert "directives" in p["goal"].lower()


def test_presentation_cv_tailor_without_job_falls_back(conn):
    t = q.enqueue_task(conn, kind="cv_tailor", params={"job_id": 999, "mode": "generate"})
    assert task_presentation(conn, t)["title"] == "Update CV"


def test_presentation_done_results_link_for_add_by_url(conn):
    sid = q.insert_source(conn, "S", "https://e.com", "generic_listing")
    jid = q.insert_job(conn, source_id=sid, url="https://e.com/j", title="Dev", company="", raw_text="")
    t = q.enqueue_task(conn, kind="job_add_by_url", params={"url": "https://e.com/j"})
    q.complete_task(conn, t["id"], {"job_id": jid})
    p = task_presentation(conn, q.get_task(conn, t["id"]))
    assert {"label": "View Dev", "href": f"/jobs/{jid}"} in p["results"]


def test_single_job_revisit_links_to_job(conn):
    sid = q.insert_source(conn, "S", "https://e.com", "generic_listing")
    jid = q.insert_job(conn, source_id=sid, url="https://e.com/j", title="Dev", company="", raw_text="")
    t = q.enqueue_task(conn, kind="jobs_revisit", params={"job_ids": [jid], "trigger": "manual"})
    q.complete_task(conn, t["id"], {"html_chunks": [], "notices": []})
    p = task_presentation(conn, q.get_task(conn, t["id"]))
    assert {"label": "View Dev", "href": f"/jobs/{jid}"} in p["results"]


def test_multi_job_revisit_sweep_has_no_per_job_link(conn):
    sid = q.insert_source(conn, "S", "https://e.com", "generic_listing")
    j1 = q.insert_job(conn, source_id=sid, url="https://e.com/1", title="A", company="", raw_text="")
    j2 = q.insert_job(conn, source_id=sid, url="https://e.com/2", title="B", company="", raw_text="")
    for params in ({}, {"job_ids": [j1, j2], "trigger": "status_change"}):
        t = q.enqueue_task(conn, kind="jobs_revisit", params=params)
        q.complete_task(conn, t["id"], {"html_chunks": [], "notices": []})
        p = task_presentation(conn, q.get_task(conn, t["id"]))
        assert p["results"] == []


def test_presentation_failed_shows_error(conn):
    t = q.enqueue_task(conn, kind="fetch_source", params={})
    q.fail_task(conn, t["id"], "Slack auth expired\nstacktrace line\nmore")
    p = task_presentation(conn, q.get_task(conn, t["id"]))
    assert p["next_step"] == "Slack auth expired"


def test_root_presentation_fetch_all_aggregates(conn):
    root = q.enqueue_task(conn, kind="fetch_all", params={})
    q.complete_task(conn, root["id"], {})
    a = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1}, parent_task_id=root["id"])
    b = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 2}, parent_task_id=root["id"])
    q.fail_task(conn, b["id"], "boom")
    p = root_presentation(conn, q.get_task(conn, root["id"]), q.get_task_children(conn, root["id"]))
    assert p["title"] == "Fetch all"
    assert "2 sources" in p["next_step"]
    assert "1" in p["next_step"] and "fail" in p["next_step"].lower()
    assert p["status"] == "running"  # child a still queued
    assert p["results"] == []


def test_root_presentation_status_priority_needs_action(conn):
    root = q.enqueue_task(conn, kind="source_detect", params={"url": "https://x.io"})
    q.resolve_task(conn, root["id"])
    child = q.enqueue_task(
        conn, kind="source_confirm",
        params={"url": "https://x.io", "name": "N", "fetcher_type": "generic_listing"},
        parent_task_id=root["id"],
    )
    q.complete_task(conn, child["id"], {"needs_action": True, "action_message": "Confirm this listing"})
    q.set_task_needs_action(conn, child["id"])
    p = root_presentation(conn, q.get_task(conn, root["id"]), q.get_task_children(conn, root["id"]))
    assert p["status"] == "needs_action"
    assert p["next_step"] == "Confirm this listing"


def test_root_presentation_threads_child_action_link(conn):
    root = q.enqueue_task(conn, kind="fetch_all", params={})
    q.resolve_task(conn, root["id"])
    child = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 9}, parent_task_id=root["id"])
    q.complete_task(conn, child["id"], {"needs_action": True, "action_message": "Reconnect Slack",
                                       "action_link": "/sources#source-row-9"})
    q.set_task_needs_action(conn, child["id"])
    p = root_presentation(conn, q.get_task(conn, root["id"]), q.get_task_children(conn, root["id"]))
    assert p["status"] == "needs_action"
    assert p["action_link"] == "/sources#source-row-9"
    assert p["has_panel"] is False
    assert p["needs_action_task_id"] == child["id"]
    assert p["next_step"] == "Reconnect Slack"


def test_root_presentation_failed_when_latest_step_failed(conn):
    root = q.enqueue_task(conn, kind="source_detect", params={"url": "https://x.io"})
    q.resolve_task(conn, root["id"])
    child = q.enqueue_task(conn, kind="source_confirm",
                           params={"url": "https://x.io", "name": "N", "fetcher_type": "generic_listing"},
                           parent_task_id=root["id"])
    q.fail_task(conn, child["id"], "network down\ntrace")
    p = root_presentation(conn, q.get_task(conn, root["id"]), q.get_task_children(conn, root["id"]))
    assert p["status"] == "failed"
    assert p["next_step"] == "network down"


def test_results_names_the_source(conn):
    sid = q.insert_source(conn, "Cord", "https://cord.co", "generic_listing")
    t = q.enqueue_task(conn, kind="fetch_source", params={"source_id": sid})
    q.complete_task(conn, t["id"], {"jobs_new": 2})
    p = task_presentation(conn, q.get_task(conn, t["id"]))
    assert {"label": "Jobs from Cord", "href": f"/jobs?source_id={sid}"} in p["results"]


def test_presentation_scenario_reevaluate_one_names_the_scenario(conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    t = q.enqueue_task(conn, kind="scenario_reevaluate_one", params={"scenario_id": sid})
    p = task_presentation(conn, t)
    assert p["title"] == "Re-evaluate: Remote ML"
    assert p["goal"] == "Re-score against your scenarios and profile"


def test_presentation_scenario_reevaluate_one_falls_back_without_scenario(conn):
    t = q.enqueue_task(conn, kind="scenario_reevaluate_one", params={"scenario_id": 999})
    assert task_presentation(conn, t)["title"] == "Re-evaluate scenario"


def test_presentation_profile_reassess_fit_has_label_and_goal(conn):
    t = q.enqueue_task(conn, kind="profile_reassess_fit", params={})
    p = task_presentation(conn, t)
    assert p["title"] == "Recompute profile fit"
    assert p["goal"] == "Recompute how well your profile fits each job"


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


def test_state_endpoint_includes_result(client, conn):
    task = q.enqueue_task(conn, kind="fetch_source", params={})
    q.complete_task(conn, task["id"], {"html_chunks": ["<p>x</p>"]})
    resp = client.get(f"/tasks/{task['id']}/state")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "done"
    assert data["result"] == {"html_chunks": ["<p>x</p>"]}


def test_state_endpoint_includes_full_log(client, conn):
    task = q.enqueue_task(conn, kind="fetch_source", params={})
    q.append_task_log(conn, task["id"], "line one")
    q.append_task_log(conn, task["id"], "line two")
    resp = client.get(f"/tasks/{task['id']}/state")
    assert resp.json()["log"] == "line one\nline two\n"


def test_state_endpoint_404_for_missing(client, conn):
    resp = client.get("/tasks/999/state")
    assert resp.status_code == 404


def test_detail_page_404_for_missing(client, conn):
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


def test_tasks_active_labels_fetch_source_with_source_name(client, conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "generic_listing")
    q.enqueue_task(conn, kind="fetch_source", params={"source_id": sid})
    resp = client.get("/tasks/active")
    assert resp.json()["tasks"][0]["label"] == "Fetch: finn.no"


def test_tasks_active_falls_back_to_kind_when_source_missing(client, conn):
    q.enqueue_task(conn, kind="fetch_source", params={"source_id": 999})
    resp = client.get("/tasks/active")
    assert resp.json()["tasks"][0]["label"] == "fetch source"


def test_task_label_job_reset_names_the_job(client, conn):
    src = q.insert_source(conn, "S", "https://e.com", "generic_listing")
    job_id = q.insert_job(conn, source_id=src, url="https://e.com/j", title="Backend Engineer", company="", raw_text="")
    q.enqueue_task(conn, kind="job_reset", params={"job_id": job_id})
    resp = client.get("/tasks/active")
    assert resp.json()["tasks"][0]["label"] == "Reset job: Backend Engineer"


def test_task_label_job_reset_without_job_falls_back(client, conn):
    q.enqueue_task(conn, kind="job_reset", params={})
    resp = client.get("/tasks/active")
    assert resp.json()["tasks"][0]["label"] == "Reset job"


def test_task_label_add_listing_source_names_it(client, conn):
    q.enqueue_task(conn, kind="job_add_listing_source",
                   params={"url": "https://x.com", "name": "Example Board", "fetcher_type": "generic_listing"})
    resp = client.get("/tasks/active")
    assert resp.json()["tasks"][0]["label"] == "Add listing source: Example Board"


def test_task_label_source_confirm_names_it(client, conn):
    q.enqueue_task(conn, kind="source_confirm",
                   params={"url": "https://x.com", "name": "Careers X", "fetcher_type": "generic_listing"})
    resp = client.get("/tasks/active")
    assert resp.json()["tasks"][0]["label"] == "Add source: Careers X"


def test_state_endpoint_link_for_single_job_kinds(client, conn):
    src_id = q.insert_source(conn, "S", "https://e.com", "generic_listing")
    job_id = q.insert_job(conn, source_id=src_id, url="https://e.com/j", title="J", company="", raw_text="")
    task = q.enqueue_task(conn, kind="job_reevaluate", params={"job_id": job_id, "filter_ctx": {}})
    resp = client.get(f"/tasks/{task['id']}/state")
    assert resp.json()["link"] == f"/jobs/{job_id}"


def test_state_endpoint_no_link_for_fetch_source(client, conn):
    src_id = q.insert_source(conn, "S", "https://e.com", "generic_listing")
    task = q.enqueue_task(conn, kind="fetch_source", params={"source_id": src_id})
    assert client.get(f"/tasks/{task['id']}/state").json().get("link") is None


def test_detail_page_renders_summary_and_results(client, conn):
    sid = q.insert_source(conn, "S", "https://e.com", "generic_listing")
    jid = q.insert_job(conn, source_id=sid, url="https://e.com/j", title="Dev", company="", raw_text="")
    t = q.enqueue_task(conn, kind="job_add_by_url", params={"url": "https://e.com/j"})
    q.complete_task(conn, t["id"], {"job_id": jid})
    html = client.get(f"/tasks/{t['id']}").text
    assert "Add job by URL" in html
    assert f'href="/jobs/{jid}"' in html
    assert "task-log" in html


def test_detail_page_shows_needs_action_panel_inline(client, conn):
    t = q.enqueue_task(conn, kind="source_detect", params={"url": "https://x.com"})
    q.complete_task(conn, t["id"], {"needs_action": True, "resume_html": "<p id='panel'>confirm me</p>",
                                    "action_message": "Confirm the detected source"})
    q.set_task_needs_action(conn, t["id"])
    html = client.get(f"/tasks/{t['id']}").text
    assert "confirm me" in html
    assert "Confirm the detected source" in html
    assert f'action="/tasks/{t["id"]}/dismiss"' in html


def test_detail_page_panelless_needs_action_shows_open_link(client, conn):
    t = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1})
    q.complete_task(conn, t["id"], {"needs_action": True,
                                    "action_message": "Reconnect Slack to keep fetching",
                                    "action_link": "/sources#source-row-1"})
    q.set_task_needs_action(conn, t["id"])
    html = client.get(f"/tasks/{t['id']}").text
    assert "Reconnect Slack to keep fetching" in html  # subtitle
    assert '<a class="btn btn-primary" href="/sources#source-row-1">Review</a>' in html
    assert f'action="/tasks/{t["id"]}/dismiss"' in html
    assert 'id="resume-page"' not in html


def test_detail_page_panelless_no_link_shows_dismiss_only(client, conn):
    t = q.enqueue_task(conn, kind="source_detect", params={"url": "https://x.io"})
    q.complete_task(conn, t["id"], {"needs_action": True, "action_message": "Something is off"})
    q.set_task_needs_action(conn, t["id"])
    html = client.get(f"/tasks/{t['id']}").text
    assert "Something is off" in html
    assert ">Open</a>" not in html
    assert f'action="/tasks/{t["id"]}/dismiss"' in html


def test_panelless_needs_action_dismiss_works(client, conn):
    t = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1})
    q.complete_task(conn, t["id"], {"needs_action": True, "action_message": "x",
                                    "action_link": "/sources"})
    q.set_task_needs_action(conn, t["id"])
    r = client.post(f"/tasks/{t['id']}/dismiss", follow_redirects=False)
    assert r.status_code == 303
    assert q.get_task(conn, t["id"])["status"] == "dismissed"


def test_root_detail_panelless_child_shows_open_link(client, conn):
    root = q.enqueue_task(conn, kind="fetch_all", params={})
    q.complete_task(conn, root["id"], {})
    child = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1}, parent_task_id=root["id"])
    q.complete_task(conn, child["id"], {"needs_action": True,
                                       "action_message": "Reconnect Slack for finn.no",
                                       "action_link": "/sources#source-row-9"})
    q.set_task_needs_action(conn, child["id"])
    html = client.get(f"/tasks/{root['id']}").text
    assert '<a class="btn btn-primary" href="/sources#source-row-9">Review</a>' in html
    assert f'action="/tasks/{child["id"]}/dismiss"' in html  # dismiss targets the child


def test_state_endpoint_returns_json(client, conn):
    t = q.enqueue_task(conn, kind="fetch_source", params={})
    q.append_task_log(conn, t["id"], "hello")
    data = client.get(f"/tasks/{t['id']}/state").json()
    assert data["status"] == "queued"
    assert data["log"] == "hello\n"


def test_log_and_resume_redirect(client, conn):
    t = q.enqueue_task(conn, kind="fetch_source", params={})
    for path in (f"/tasks/{t['id']}/log", f"/tasks/{t['id']}/resume"):
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 301
        assert r.headers["location"] == f"/tasks/{t['id']}"


def test_dismiss_sets_dismissed_status(client, conn):
    t = q.enqueue_task(conn, kind="source_detect", params={})
    q.complete_task(conn, t["id"], {"needs_action": True})
    q.set_task_needs_action(conn, t["id"])
    r = client.post(f"/tasks/{t['id']}/dismiss", follow_redirects=False)
    assert r.status_code == 303
    assert q.get_task(conn, t["id"])["status"] == "dismissed"


def test_dismiss_non_needs_action_is_noop(client, conn):
    t = q.enqueue_task(conn, kind="fetch_source", params={})
    q.complete_task(conn, t["id"], {})
    r = client.post(f"/tasks/{t['id']}/dismiss", follow_redirects=False)
    assert r.status_code == 303
    assert q.get_task(conn, t["id"])["status"] == "done"


def test_root_detail_lists_steps_linking_to_children(client, conn):
    root = q.enqueue_task(conn, kind="fetch_all", params={})
    q.complete_task(conn, root["id"], {})
    c1 = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1}, parent_task_id=root["id"])
    c2 = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 2}, parent_task_id=root["id"])
    html = client.get(f"/tasks/{root['id']}").text
    assert "Steps" in html
    assert f'href="/tasks/{c1["id"]}"' in html
    assert f'href="/tasks/{c2["id"]}"' in html


def test_group_route_gone(client, conn):
    assert client.get("/tasks/group/anything").status_code == 404


def test_child_detail_has_breadcrumb_to_parent(client, conn):
    root = q.enqueue_task(conn, kind="fetch_all", params={})
    child = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1}, parent_task_id=root["id"])
    html = client.get(f"/tasks/{child['id']}").text
    assert "Part of:" in html
    assert f'href="/tasks/{root["id"]}">Fetch all</a>' in html


def test_root_detail_has_no_breadcrumb(client, conn):
    root = q.enqueue_task(conn, kind="fetch_all", params={})
    q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1}, parent_task_id=root["id"])
    assert "Part of:" not in client.get(f"/tasks/{root['id']}").text


def test_needs_action_dismiss_is_a_real_button(client, conn):
    t = q.enqueue_task(conn, kind="source_detect", params={"url": "https://x.io"})
    q.complete_task(conn, t["id"], {"needs_action": True, "resume_html": "<p>x</p>"})
    q.set_task_needs_action(conn, t["id"])
    html = client.get(f"/tasks/{t['id']}").text
    assert '<button type="submit" class="btn">Dismiss</button>' in html


def test_root_detail_shows_child_needs_action_panel(client, conn):
    root = q.enqueue_task(conn, kind="source_detect", params={"url": "https://x.io"})
    q.resolve_task(conn, root["id"])
    child = q.enqueue_task(conn, kind="source_confirm",
                           params={"url": "https://x.io", "name": "N", "fetcher_type": "generic_listing"},
                           parent_task_id=root["id"])
    q.complete_task(conn, child["id"], {"needs_action": True, "resume_html": "<p id='cpanel'>decide</p>"})
    q.set_task_needs_action(conn, child["id"])
    html = client.get(f"/tasks/{root['id']}").text
    assert "decide" in html
    assert f'action="/tasks/{child["id"]}/dismiss"' in html


def test_resolved_needs_action_detail_shows_summary_not_form(client, conn):
    sid = q.insert_source(conn, "X Careers", "https://x.com", "generic_listing")
    t = q.enqueue_task(conn, kind="source_detect", params={"url": "https://x.com"})
    q.complete_task(conn, t["id"], {
        "needs_action": True, "resume_html": "<form id='f'>x</form>", "source_id": sid,
    })
    q.set_task_needs_action(conn, t["id"])
    q.resolve_task(conn, t["id"])
    html = client.get(f"/tasks/{t['id']}").text
    assert "<form id='f'>" not in html
    assert "X Careers" in html and "created" in html


def test_dismissed_task_detail_says_dismissed(client, conn):
    t = q.enqueue_task(conn, kind="source_detect", params={})
    q.complete_task(conn, t["id"], {"needs_action": True, "resume_html": "<form>x</form>"})
    q.set_task_needs_action(conn, t["id"])
    q.dismiss_task(conn, t["id"])
    html = client.get(f"/tasks/{t['id']}").text
    assert "Dismissed" in html
    assert "<form>x</form>" not in html


def test_history_lists_all_tasks_newest_first(client, conn):
    a = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1})
    b = q.enqueue_task(conn, kind="job_reset", params={"job_id": 2})
    q.fail_task(conn, b["id"], "boom")
    html = client.get("/tasks").text
    assert html.index(f'/tasks/{b["id"]}"') < html.index(f'/tasks/{a["id"]}"')
    assert "failed" in html


def test_history_status_filter(client, conn):
    a = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1})
    b = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 2})
    q.fail_task(conn, b["id"], "x")
    html = client.get("/tasks?status=failed").text
    assert f'/tasks/{b["id"]}"' in html and f'/tasks/{a["id"]}"' not in html


def test_history_state_column_is_just_the_status(client, conn):
    t = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1})
    q.claim_next_task(conn)
    q.append_task_log(conn, t["id"], "[2/5] working")
    html = client.get("/tasks").text
    # state cell shows the status word, not the next-step detail
    assert "running" in html
    assert "Step 2 of 5" not in html


def test_running_detail_subtitle_is_pollable_and_state_carries_next_step(client, conn):
    t = q.enqueue_task(conn, kind="jobs_revisit", params={})
    q.claim_next_task(conn)
    q.append_task_log(conn, t["id"], "[6/24] Revisiting: http://e.com/j")
    html = client.get(f"/tasks/{t['id']}").text
    # The subtitle the poll refreshes has a stable id, and starts on the live step.
    assert 'id="task-next-step"' in html
    assert "Step 6 of 24" in html
    state = client.get(f"/tasks/{t['id']}/state").json()
    assert state["status"] == "running"
    assert state["next_step"] == "Step 6 of 24"


def test_history_root_row_shows_derived_state(client, conn):
    root = q.enqueue_task(conn, kind="fetch_all", params={})
    q.complete_task(conn, root["id"], {})  # root itself done...
    q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1}, parent_task_id=root["id"])
    html = client.get("/tasks").text
    row = html.split(f'/tasks/{root["id"]}"', 1)[1][:200]
    assert "running" in row  # ...but a child is still queued


def test_history_pagination(client, conn):
    for i in range(55):
        q.enqueue_task(conn, kind="fetch_source", params={"source_id": i})
    page1 = client.get("/tasks").text
    page2 = client.get("/tasks?page=2").text
    assert "page=2" in page1
    assert page1 != page2


def test_task_history_nests_children_under_root(client, conn):
    root = q.enqueue_task(conn, kind="fetch_all", params={})
    child = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1},
                           parent_task_id=root["id"])
    for tid in (root["id"], child["id"]):
        conn.execute(
            "UPDATE tasks SET status='done', finished_at=datetime('now') WHERE id=?", (tid,))
    conn.commit()
    html = client.get("/tasks").text
    assert "task-child-row" in html
    assert f'task-grp-{root["id"]}' in html


def test_task_history_standalone_task_has_no_toggle(client, conn):
    t = q.enqueue_task(conn, kind="job_reset", params={"job_id": 1})
    conn.execute("UPDATE tasks SET status='done', finished_at=datetime('now') WHERE id=?", (t["id"],))
    conn.commit()
    html = client.get("/tasks").text
    assert f'task-grp-{t["id"]}' not in html


def test_cancel_queued_tasks_only_touches_queued(conn):
    a = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1})
    b = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 2})
    q.claim_next_task(conn)  # a -> running
    n = q.cancel_queued_tasks(conn, [a["id"], b["id"]])
    assert n == 1
    assert q.get_task(conn, a["id"])["status"] == "running"
    assert q.get_task(conn, b["id"])["status"] == "cancelled"
    assert q.get_task(conn, b["id"])["finished_at"] is not None


def test_next_step_cancelled(conn):
    t = q.enqueue_task(conn, kind="fetch_all", params={})
    q.cancel_task(conn, t["id"])
    assert _next_step(conn, q.get_task(conn, t["id"])) == "Cancelled"


def test_subtree_status_cancelled(conn):
    root = q.enqueue_task(conn, kind="fetch_all", params={})
    q.complete_task(conn, root["id"], {})
    child = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1},
                           parent_task_id=root["id"])
    q.cancel_task(conn, child["id"])
    subtree = [q.get_task(conn, root["id"]), q.get_task(conn, child["id"])]
    assert _subtree_status(subtree) == "cancelled"


def test_subtree_status_failed_beats_cancelled_when_later(conn):
    root = q.enqueue_task(conn, kind="fetch_all", params={})
    q.complete_task(conn, root["id"], {})
    c1 = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1}, parent_task_id=root["id"])
    q.cancel_task(conn, c1["id"])
    c2 = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 2}, parent_task_id=root["id"])
    q.fail_task(conn, c2["id"], "boom")
    subtree = [q.get_task(conn, root["id"]), q.get_task(conn, c1["id"]), q.get_task(conn, c2["id"])]
    assert _subtree_status(subtree) == "failed"


def test_stop_cancels_queued_task(client, conn):
    t = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1})
    r = client.post(f"/tasks/{t['id']}/stop", follow_redirects=False)
    assert r.status_code == 303
    assert q.get_task(conn, t["id"])["status"] == "cancelled"
    assert q.claim_next_task(conn) is None  # worker won't pick it up


def test_stop_on_root_cancels_subtree_and_signals_running(client, conn):
    root = q.enqueue_task(conn, kind="fetch_all", params={})
    q.complete_task(conn, root["id"], {})
    running = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1}, parent_task_id=root["id"])
    queued = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 2}, parent_task_id=root["id"])
    q.claim_next_task(conn)  # running -> running

    from app import task_engine as te
    r = client.post(f"/tasks/{root['id']}/stop", follow_redirects=False)
    assert r.status_code == 303
    assert q.get_task(conn, queued["id"])["status"] == "cancelled"
    assert te.cancel_requested(running["id"])
    # Stopping the run re-marks the (finished) fetch_all container cancelled.
    assert q.get_task(conn, root["id"])["status"] == "cancelled"
    te._clear_cancel(running["id"])  # tidy up shared module state


def test_stop_run_marks_root_cancelled_even_with_done_children(client, conn):
    root = q.enqueue_task(conn, kind="fetch_all", params={})
    q.complete_task(conn, root["id"], {})
    done_child = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1}, parent_task_id=root["id"])
    q.complete_task(conn, done_child["id"], {"jobs_new": 3})
    queued = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 2}, parent_task_id=root["id"])

    r = client.post(f"/tasks/{root['id']}/stop", follow_redirects=False)
    assert r.status_code == 303
    assert q.get_task(conn, root["id"])["status"] == "cancelled"
    assert q.get_task(conn, queued["id"])["status"] == "cancelled"
    assert q.get_task(conn, done_child["id"])["status"] == "done"  # already-finished child kept
    subtree = [q.get_task(conn, root["id"]), q.get_task(conn, done_child["id"]),
               q.get_task(conn, queued["id"])]
    assert _subtree_status(subtree) == "cancelled"


def test_stop_on_child_leaves_siblings_and_root_alone(client, conn):
    root = q.enqueue_task(conn, kind="fetch_all", params={})
    q.complete_task(conn, root["id"], {})
    target = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1}, parent_task_id=root["id"])
    sibling = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 2}, parent_task_id=root["id"])
    r = client.post(f"/tasks/{target['id']}/stop", follow_redirects=False)
    assert r.status_code == 303
    assert q.get_task(conn, target["id"])["status"] == "cancelled"
    assert q.get_task(conn, sibling["id"])["status"] == "queued"  # sibling untouched
    assert q.get_task(conn, root["id"])["status"] == "done"       # parent not propagated to


def test_subtree_status_partial_cancel_stays_done(conn):
    root = q.enqueue_task(conn, kind="fetch_all", params={})
    q.complete_task(conn, root["id"], {})
    c1 = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1}, parent_task_id=root["id"])
    q.complete_task(conn, c1["id"], {"jobs_new": 1})
    c2 = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 2}, parent_task_id=root["id"])
    q.cancel_task(conn, c2["id"])
    subtree = [q.get_task(conn, root["id"]), q.get_task(conn, c1["id"]), q.get_task(conn, c2["id"])]
    assert _subtree_status(subtree) == "done"


def test_stop_on_terminal_task_is_noop(client, conn):
    t = q.enqueue_task(conn, kind="fetch_all", params={})
    q.complete_task(conn, t["id"], {})
    r = client.post(f"/tasks/{t['id']}/stop", follow_redirects=False)
    assert r.status_code == 303
    assert q.get_task(conn, t["id"])["status"] == "done"


def test_stop_missing_task_404(client, conn):
    r = client.post("/tasks/999/stop", follow_redirects=False)
    assert r.status_code == 404


def test_detail_shows_stop_button_for_running(client, conn):
    t = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1})
    q.claim_next_task(conn)
    html = client.get(f"/tasks/{t['id']}").text
    assert f'action="/tasks/{t["id"]}/stop"' in html
    assert ">Stop<" in html


def test_detail_shows_stop_button_for_queued(client, conn):
    t = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1})
    html = client.get(f"/tasks/{t['id']}").text
    assert f'action="/tasks/{t["id"]}/stop"' in html


def test_detail_no_stop_button_for_done(client, conn):
    t = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1})
    q.complete_task(conn, t["id"], {})
    html = client.get(f"/tasks/{t['id']}").text
    assert "/stop" not in html


def test_detail_cancelled_task_says_cancelled(client, conn):
    t = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1})
    q.cancel_task(conn, t["id"])
    html = client.get(f"/tasks/{t['id']}").text
    assert "⊘ Cancelled" in html
    assert "/stop" not in html
    # The bold subtitle is suppressed once the resolved panel states the outcome.
    assert "<strong>Cancelled</strong>" not in html


def test_detail_stopped_run_shows_kickoff_done_but_overall_cancelled(client, conn):
    sid = q.insert_source(conn, "Cord", "https://cord.co", "generic_listing")
    root = q.enqueue_task(conn, kind="fetch_all", params={})
    q.complete_task(conn, root["id"], {})
    child = q.enqueue_task(conn, kind="fetch_source", params={"source_id": sid}, parent_task_id=root["id"])
    client.post(f"/tasks/{root['id']}/stop", follow_redirects=False)

    html = client.get(f"/tasks/{root['id']}").text
    assert "⊘ Cancelled" in html                     # overall result
    assert 'class="task-li-icon icon-done"' in html   # kick-off step still reads done
    assert "Fetch history" not in html                # link removed


def test_fetch_source_results_drop_fetch_history(conn):
    sid = q.insert_source(conn, "Cord", "https://cord.co", "generic_listing")
    t = q.enqueue_task(conn, kind="fetch_source", params={"source_id": sid})
    q.complete_task(conn, t["id"], {"jobs_new": 1})
    labels = [r["label"] for r in task_presentation(conn, q.get_task(conn, t["id"]))["results"]]
    assert "Fetch history" not in labels
    assert "Jobs from Cord" in labels


def test_revisit_result_view_summarises_and_links_changed(client, conn):
    sid = q.insert_source(conn, "s", "http://e", "manual")
    j1 = q.insert_job(conn, source_id=sid, url="http://e/1", title="T", company="Acme", raw_text="x")
    j2 = q.insert_job(conn, source_id=sid, url="http://e/2", title="J2", company="Acme", raw_text="x")
    j3 = q.insert_job(conn, source_id=sid, url="http://e/3", title="J3", company="Acme", raw_text="x")
    conn.execute("UPDATE jobs SET title='Gone Role' WHERE id=?", (j2,))
    t = q.enqueue_task(conn, kind="jobs_revisit",
                       params={"job_ids": [j1, j2, j3], "trigger": "manual"})
    conn.execute(
        "UPDATE tasks SET status='done', result=? WHERE id=?",
        ('{"outcome": {"total": 3, "changed": [], "closed": [%d]}}' % j2, t["id"]))
    conn.commit()
    r = client.get(f"/tasks/{t['id']}")
    assert "3 rechecked" in r.text
    assert "1 moved to Trash" in r.text
    assert f'/jobs/{j2}' in r.text and "Trashed: Gone Role" in r.text
    assert "View all rechecked jobs" not in r.text


def test_bulk_reset_result_links_jobs(client, conn):
    sid = q.insert_source(conn, "s", "http://e", "manual")
    j1 = q.insert_job(conn, source_id=sid, url="http://e/1", title="T", company="Acme", raw_text="x")
    conn.execute("UPDATE jobs SET title='Reset Me' WHERE id=?", (j1,))
    t = q.enqueue_task(conn, kind="jobs_bulk_reset", params={"job_ids": [j1]})
    conn.execute("UPDATE tasks SET status='done', result='{}' WHERE id=?", (t["id"],))
    conn.commit()
    r = client.get(f"/tasks/{t['id']}")
    assert "1 job reset" in r.text
    assert f'/jobs/{j1}' in r.text and "Reset Me" in r.text


def test_result_labels_have_no_open_prefix(client, conn):
    t = q.enqueue_task(conn, kind="scenarios_refine_all", params={})
    conn.execute("UPDATE tasks SET status='done', result='{}' WHERE id=?", (t["id"],))
    conn.commit()
    r = client.get(f"/tasks/{t['id']}")
    assert "Scenarios" in r.text
    assert "Open Scenarios" not in r.text


def test_finished_task_detail_shows_duration(client, conn):
    t = q.enqueue_task(conn, kind="jobs_revisit", params={"job_ids": [], "trigger": "manual"})
    conn.execute(
        "UPDATE tasks SET status='done', started_at='2026-09-10T10:00:00', "
        "finished_at='2026-09-10T10:02:30' WHERE id=?", (t["id"],))
    conn.commit()
    r = client.get(f"/tasks/{t['id']}")
    assert "Finished" in r.text
    assert "in 2m 30s" in r.text
