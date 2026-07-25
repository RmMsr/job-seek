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


def test_activate_scenario(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    resp = client.post(f"/scenarios/{sid}/activate")
    assert resp.status_code == 200
    assert q.get_active_scenario(conn)["id"] == sid


def test_add_criterion(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    resp = client.post(f"/scenarios/{sid}/criteria", data={"text": "Must be remote", "weight": "must"})
    assert resp.status_code == 200
    criteria = q.get_criteria(conn, sid)
    assert criteria[0]["text"] == "Must be remote"


def test_delete_criterion(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    cid = q.insert_criterion(conn, sid, "Must be remote", "must")
    resp = client.delete(f"/criteria/{cid}")
    assert resp.status_code == 200
    assert q.get_criteria(conn, sid) == []


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
    q.set_active_scenario(conn, sid)
    q.insert_criterion(conn, sid, "Must be remote", "must")
    source_id = q.insert_source(conn, "s", "http://x", "http")
    job_id = q.insert_job(conn, source_id=source_id, url="http://job/1", title="ML Eng", company="C", raw_text="r")
    q.update_job_pipeline(conn, job_id, simplified_content="clean", content_type="job_posting", scenario_id=sid)

    with patch("app.routes.scenarios.summarize", return_value="Updated summary"), \
         patch("app.routes.scenarios.evaluate", return_value=(0.75, "Good match")):
        resp = client.post(f"/scenarios/{sid}/reevaluate")

    assert resp.status_code == 200
    assert "Re-evaluating 1 job(s)" in resp.text
    assert "Re-evaluation complete" in resp.text
    job = q.get_job(conn, job_id)
    assert job["relevance_score"] == pytest.approx(0.75)
    assert job["summary"] == "Updated summary"
