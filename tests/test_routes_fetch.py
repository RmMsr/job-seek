import pytest
from unittest.mock import patch, MagicMock
from app.db import queries as q
from app.pipeline import FetchResult


def _seed(conn):
    return q.insert_source(conn, "finn.no", "https://finn.no", "http")


def test_fetch_panel_returns_200(client, conn):
    _seed(conn)
    resp = client.get("/fetch")
    assert resp.status_code == 200
    assert "finn.no" in resp.text


def test_fetch_panel_empty_sources(client, conn):
    resp = client.get("/fetch")
    assert resp.status_code == 200


def test_post_fetch_triggers_pipeline(client, conn):
    sid = _seed(conn)
    mock_result = FetchResult(source_id=sid, run_id=1, jobs_found=3, jobs_new=2, error=None)
    with patch("app.routes.fetch.run_fetch", return_value=mock_result):
        resp = client.post(f"/fetch/{sid}")
    assert resp.status_code == 200
    assert "2" in resp.text


def test_post_fetch_unknown_source_returns_404(client, conn):
    resp = client.post("/fetch/999")
    assert resp.status_code == 404
