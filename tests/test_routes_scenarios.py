import pytest
from unittest.mock import patch
from app.db import queries as q
from app.ai.refine import CriterionProposal


def test_scenarios_page_returns_200(client):
    resp = client.get("/scenarios")
    assert resp.status_code == 200


def test_create_scenario(client, conn):
    resp = client.post("/scenarios", data={"name": "Remote ML", "description": "Remote ML roles"})
    assert resp.status_code == 200
    scenarios = q.get_scenarios(conn)
    assert len(scenarios) == 1
    assert scenarios[0]["name"] == "Remote ML"


def test_edit_scenario_form_returns_fields(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "Remote ML roles")
    resp = client.get(f"/scenarios/{sid}/edit")
    assert resp.status_code == 200
    assert "Remote ML roles" in resp.text


def test_update_scenario(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "old description")
    resp = client.post(f"/scenarios/{sid}", data={"name": "Remote ML v2", "description": "new description"})
    assert resp.status_code == 200
    scenario = {s["id"]: s for s in q.get_scenarios(conn)}[sid]
    assert scenario["name"] == "Remote ML v2"
    assert scenario["description"] == "new description"
    assert "Remote ML v2" in resp.text


def test_cancel_scenario_edit_returns_display_header(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "Remote ML roles")
    resp = client.get(f"/scenarios/{sid}")
    assert resp.status_code == 200
    assert "Remote ML roles" in resp.text


def test_add_criterion(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    resp = client.post(f"/scenarios/{sid}/criteria", data={"text": "Must be remote", "weight": "must"})
    assert resp.status_code == 200
    criteria = q.get_criteria(conn, sid)
    assert criteria[0]["text"] == "Must be remote"


def test_add_criterion_response_wraps_list_and_form_in_shared_target(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    resp = client.post(f"/scenarios/{sid}/criteria", data={"text": "Must be remote", "weight": "must"})
    assert resp.status_code == 200
    # The id that both the add-form and the proposal accept-form target via
    # hx-target must sit on a wrapping element that contains the whole
    # list + form, not on the <ul> alone — otherwise HTMX's outerHTML swap
    # only replaces the <ul>, leaving the old populated <form> orphaned
    # in the DOM as a sibling.
    div_marker = f'<div id="criteria-{sid}">'
    ul_marker = f'<ul id="criteria-{sid}">'
    assert div_marker in resp.text
    assert ul_marker not in resp.text
    div_start = resp.text.index(div_marker)
    form_start = resp.text.index("<form")
    div_end = resp.text.rindex("</div>")
    assert div_start < form_start < div_end


def test_scenarios_page_renders_criterion_markdown(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Must be *remote*", "must")
    resp = client.get("/scenarios")
    assert resp.status_code == 200
    assert "<em>remote</em>" in resp.text


def test_delete_criterion(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    cid = q.insert_criterion(conn, sid, "Must be remote", "must")
    resp = client.delete(f"/criteria/{cid}")
    assert resp.status_code == 200
    assert q.get_criteria(conn, sid) == []


def test_edit_criterion_form_returns_fields(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    cid = q.insert_criterion(conn, sid, "Must be remote", "must")
    resp = client.get(f"/criteria/{cid}/edit")
    assert resp.status_code == 200
    assert "Must be remote" in resp.text


def test_update_criterion_route(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    cid = q.insert_criterion(conn, sid, "Must be remote", "must")
    resp = client.post(f"/criteria/{cid}", data={"text": "Must be fully remote", "weight": "prefer"})
    assert resp.status_code == 200
    assert "Must be fully remote" in resp.text
    criterion = q.get_criterion(conn, cid)
    assert criterion["text"] == "Must be fully remote"
    assert criterion["weight"] == "prefer"


def test_update_criterion_leaves_source_unchanged(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    cid = q.insert_criterion(conn, sid, "Must be remote", "must", source="feedback")
    client.post(f"/criteria/{cid}", data={"text": "Must be fully remote", "weight": "prefer"})
    assert q.get_criterion(conn, cid)["source"] == "feedback"


def test_cancel_criterion_edit_returns_display_row(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    cid = q.insert_criterion(conn, sid, "Must be remote", "must")
    resp = client.get(f"/criteria/{cid}")
    assert resp.status_code == 200
    assert "Must be remote" in resp.text


def test_remove_proposal_deletes_criterion(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    cid = q.insert_criterion(conn, sid, "Must be remote", "must")
    resp = client.delete(f"/criteria/{cid}/remove-proposal")
    assert resp.status_code == 200
    assert q.get_criterion(conn, cid) is None


def test_remove_proposal_response_marks_criterion_row_for_oob_delete(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    cid = q.insert_criterion(conn, sid, "Must be remote", "must")
    resp = client.delete(f"/criteria/{cid}/remove-proposal")
    assert f'id="criterion-{cid}"' in resp.text
    assert 'hx-swap-oob="delete"' in resp.text


def test_refine_returns_proposals(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Must be remote", "must")
    proposals = [CriterionProposal(text="Must be senior", weight="must", action="add")]
    with patch("app.routes.scenarios.propose_criteria", return_value=proposals):
        resp = client.post(f"/scenarios/{sid}/refine")
    assert resp.status_code == 200
    assert "Requesting criteria proposals" in resp.text
    assert "HTML:" in resp.text
    assert "Must be senior" in resp.text


def test_refine_add_proposal_has_editable_inputs(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Must be remote", "must")
    proposals = [CriterionProposal(text="Must be senior", weight="must", action="add")]
    with patch("app.routes.scenarios.propose_criteria", return_value=proposals):
        resp = client.post(f"/scenarios/{sid}/refine")
    assert 'name="text" value="Must be senior"' in resp.text
    assert 'name="weight"' in resp.text


def test_refine_remove_proposal_matched_shows_remove_button(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    cid = q.insert_criterion(conn, sid, "Must be remote", "must")
    proposals = [CriterionProposal(text="Must be remote", weight="must", action="remove")]
    with patch("app.routes.scenarios.propose_criteria", return_value=proposals):
        resp = client.post(f"/scenarios/{sid}/refine")
    assert f"/criteria/{cid}/remove-proposal" in resp.text
    assert ">Remove<" in resp.text


def test_refine_remove_proposal_unmatched_is_omitted(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Must be remote", "must")
    proposals = [CriterionProposal(text="Must have a PhD", weight="must", action="remove")]
    with patch("app.routes.scenarios.propose_criteria", return_value=proposals):
        resp = client.post(f"/scenarios/{sid}/refine")
    assert "Must have a PhD" not in resp.text
    assert "No changes proposed" in resp.text


def test_refine_html_chunk_has_no_embedded_newline(client, conn):
    # The client reads the stream line-by-line, splitting on "\n", and only
    # recognizes "HTML:" as a prefix of a complete line. A fragment with an
    # embedded newline would be split across multiple "lines" client-side,
    # silently truncating the html to "" instead of the real fragment.
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Must be remote", "must")
    proposals = [CriterionProposal(text="Must be senior", weight="must", action="add")]
    with patch("app.routes.scenarios.propose_criteria", return_value=proposals):
        resp = client.post(f"/scenarios/{sid}/refine")
    lines = resp.text.split("\n")
    html_lines = [line for line in lines if line.startswith("HTML:")]
    assert len(html_lines) == 1
    assert "Must be senior" in html_lines[0]


def test_refine_accept_adds_criteria(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    resp = client.post(
        f"/scenarios/{sid}/refine/accept",
        data={"text_0": "Must be senior", "weight_0": "must"},
    )
    assert resp.status_code == 200
    criteria = q.get_criteria(conn, sid)
    assert any(c["text"] == "Must be senior" for c in criteria)


def test_reevaluate_streams_progress_and_updates_jobs(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Must be remote", "must")
    source_id = q.insert_source(conn, "s", "http://x", "http")
    job_id = q.insert_job(conn, source_id=source_id, url="http://job/1", title="ML Eng", company="C", raw_text="r")
    q.update_job_pipeline(conn, job_id, simplified_content="clean", content_type="job_posting")

    with patch("app.pipeline.summarize", return_value=("ML Engineer - Remote @ Acme", "Great hook", "Updated summary")), \
         patch("app.pipeline.evaluate", return_value=(0.75, "Good match")):
        resp = client.post(f"/scenarios/{sid}/reevaluate")

    assert resp.status_code == 200
    assert "Re-evaluating 1 job(s)" in resp.text
    assert "Re-evaluation complete" in resp.text
    job = q.get_job(conn, job_id)
    assert job["best_score"] == pytest.approx(0.75)
    assert job["summary"] == "Updated summary"


def test_reevaluate_skips_jobs_already_current(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Must be remote", "must")
    source_id = q.insert_source(conn, "s", "http://x", "http")
    q.insert_job(conn, source_id=source_id, url="http://job/1", title="ML Eng", company="C", raw_text="r")
    job_id = q.get_jobs(conn)[0]["id"]
    q.update_job_pipeline(conn, job_id, simplified_content="clean", content_type="job_posting")

    with patch("app.pipeline.summarize", return_value=("ML Engineer - Remote @ Acme", "Great hook", "Updated summary")), \
         patch("app.pipeline.evaluate", return_value=(0.75, "Good match")) as mock_evaluate:
        client.post(f"/scenarios/{sid}/reevaluate")
        resp = client.post(f"/scenarios/{sid}/reevaluate")

    assert mock_evaluate.call_count == 1
    assert "skipping 1 already current" in resp.text
    assert "Re-evaluating 0 job(s)" in resp.text


def test_reevaluate_all_scenarios_streams_combined_progress(client, conn):
    sid_a = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid_a, "Must be remote", "must")
    sid_b = q.insert_scenario(conn, "Robotics", "")
    q.insert_criterion(conn, sid_b, "Must involve embedded systems", "must")
    source_id = q.insert_source(conn, "s", "http://x", "http")
    job_id = q.insert_job(conn, source_id=source_id, url="http://job/1", title="ML Eng", company="C", raw_text="r")
    q.update_job_pipeline(conn, job_id, simplified_content="clean", content_type="job_posting")

    with patch("app.pipeline.summarize", return_value=("ML Engineer - Remote @ Acme", "Great hook", "Updated summary")), \
         patch("app.pipeline.evaluate", return_value=(0.75, "Good match")):
        resp = client.post("/scenarios/reevaluate")

    assert resp.status_code == 200
    assert "Re-evaluating 2 scenario(s)" in resp.text
    assert "All scenarios re-evaluated: 2 job(s) updated across 2 scenario(s)" in resp.text
    assert q.get_job_score(conn, job_id, sid_a) is not None
    assert q.get_job_score(conn, job_id, sid_b) is not None


def test_reevaluate_keeps_existing_title_when_ai_title_empty(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Must be remote", "must")
    source_id = q.insert_source(conn, "s", "http://x", "http")
    job_id = q.insert_job(conn, source_id=source_id, url="http://job/1", title="ML Eng", company="C", raw_text="r")
    q.update_job_pipeline(conn, job_id, simplified_content="clean", content_type="job_posting")

    with patch("app.pipeline.summarize", return_value=("", "", "Updated summary")), \
         patch("app.pipeline.evaluate", return_value=(0.75, "Good match")):
        client.post(f"/scenarios/{sid}/reevaluate")

    job = q.get_job(conn, job_id)
    assert job["title"] == "ML Eng"
    assert job["summary"] == "Updated summary"
