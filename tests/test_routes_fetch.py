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


def _fake_run_fetch(*args, **kwargs):
    yield "Starting fetch for 'finn.no' (http)"
    yield "Fetched 3 raw posting(s) from 'finn.no'"
    yield "Fetch complete for 'finn.no': 2 new / 3 found"
    return FetchResult(source_id=1, run_id=1, jobs_found=3, jobs_new=2, error=None)


def test_post_fetch_streams_progress(client, conn):
    sid = _seed(conn)
    with patch("app.routes.fetch.run_fetch", side_effect=_fake_run_fetch):
        resp = client.post(f"/fetch/{sid}")
    assert resp.status_code == 200
    assert "Starting fetch" in resp.text
    assert "Fetch complete for 'finn.no': 2 new / 3 found" in resp.text


def test_post_fetch_unknown_source_returns_404(client, conn):
    resp = client.post("/fetch/999")
    assert resp.status_code == 404
