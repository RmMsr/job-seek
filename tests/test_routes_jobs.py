import pytest
from app.db import queries as q


def _seed(conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "http")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/1", title="ML Eng", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="job_posting", summary="Great role")
    scenario_id = q.insert_scenario(conn, "Remote ML", "")
    q.upsert_job_score(conn, jid, scenario_id, 0.9, "Good match", "hash1")
    return sid, jid


def test_job_list_returns_200(client, conn):
    _seed(conn)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "ML Eng" in resp.text


def test_job_list_empty(client, conn):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "No jobs found" in resp.text


def test_job_list_filter_accepted(client, conn):
    sid, jid = _seed(conn)
    q.update_job_feedback(conn, jid, "accepted", "great")
    resp = client.get("/?status=accepted")
    assert resp.status_code == 200
    assert "ML Eng" in resp.text
    resp2 = client.get("/")
    assert "ML Eng" not in resp2.text


def test_job_expand(client, conn):
    sid, jid = _seed(conn)
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert "Accept" in resp.text
    assert "Reject" in resp.text


def test_job_feedback_updates_status(client, conn):
    sid, jid = _seed(conn)
    resp = client.post(f"/jobs/{jid}/feedback", data={"status": "accepted", "note": "good fit"})
    assert resp.status_code == 200
    job = q.get_job(conn, jid)
    assert job["status"] == "accepted"
    assert job["feedback_note"] == "good fit"


def test_job_list_shows_scenario_tag_for_best_score(client, conn):
    _seed(conn)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Remote ML" in resp.text


def test_job_expand_shows_scenario_tag_with_reasoning(client, conn):
    sid, jid = _seed(conn)
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert "Good match" in resp.text
    assert "Remote ML" in resp.text
