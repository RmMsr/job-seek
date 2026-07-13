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
