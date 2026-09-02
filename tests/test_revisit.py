import sqlite3
import pytest
from unittest.mock import patch
from app.db.schema import init_db
from app.db import queries as q
from app.pipeline import run_revisit_job, RevisitOutcome, run_reprocess_job
from app.fetchers.content import FetchError
from tests.test_pipeline import _mock_client, _drain


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_db(c)
    yield c
    c.close()


@pytest.fixture
def board(conn):
    sid = q.insert_source(conn, "board", "http://example.com", "generic_listing")
    q.insert_scenario(conn, "Remote ML", "")
    q.upsert_profile(conn, "I am an ML engineer.")
    return q.get_source(conn, sid)


def _job(conn, board, *, raw_text="original body text", status="new",
         summary="A solid ML engineering role at Acme.", content_type="job_posting"):
    jid = q.insert_job(conn, source_id=board["id"], url="http://example.com/j1",
                       title="ML Engineer", company="Acme", raw_text=raw_text)
    q.update_job_pipeline(conn, jid, simplified_content=raw_text,
                          content_type=content_type, summary=summary)
    if status != "new":
        q.update_job_feedback(conn, jid, status, None)
    return q.get_job(conn, jid)


def _run(conn, job, client=None):
    client = client or _mock_client("{}", "{}", "{}")
    return _drain(run_revisit_job(conn, client, "llama3.2", job,
                                  q.get_scenarios(conn), q.get_profile(conn)))


_LONG = "<html><body>" + "long real posting content " * 40 + "</body></html>"


def test_http_error_retried_once_then_closed(conn, board):
    job = _job(conn, board)
    with patch("app.pipeline.fetch_url_html", side_effect=FetchError("HTTP 404")) as m, \
         patch("app.pipeline.time.sleep"):
        _, outcome = _run(conn, job)
    assert m.call_count == 2
    assert outcome.verdict == "closed"
    assert outcome.reason == "this job can no longer be found"
    assert q.get_job(conn, job["id"])["status"] == "trash"


def test_transient_error_that_clears_on_retry_is_not_closed(conn, board):
    job = _job(conn, board)
    with patch("app.pipeline.fetch_url_html",
               side_effect=[FetchError("HTTP 503"), _LONG]), \
         patch("app.pipeline.time.sleep"), \
         patch("app.pipeline.revisit_check", return_value=("unchanged", "same")):
        _, outcome = _run(conn, job)
    assert outcome.verdict == "unchanged"
    assert q.get_job(conn, job["id"])["status"] == "new"


def test_thin_page_and_thin_render_closes(conn, board):
    job = _job(conn, board)
    with patch("app.pipeline.fetch_url_html", return_value="<html><body>x</body></html>"), \
         patch("app.pipeline.render_html", return_value=None):
        _, outcome = _run(conn, job)
    assert outcome.verdict == "closed"
    assert outcome.reason == "this job posting no longer shows any content"


def test_revisit_check_gone_closes(conn, board):
    job = _job(conn, board)
    with patch("app.pipeline.fetch_url_html", return_value=_LONG), \
         patch("app.pipeline.revisit_check", return_value=("gone", "redirected to a listing")):
        _, outcome = _run(conn, job)
    assert outcome.verdict == "closed"
    assert outcome.reason == "this job posting is no longer available"
    assert q.get_job(conn, job["id"])["status"] == "trash"


def test_revisit_check_closed_closes(conn, board):
    job = _job(conn, board)
    with patch("app.pipeline.fetch_url_html", return_value=_LONG), \
         patch("app.pipeline.revisit_check", return_value=("closed", "applications closed")):
        _, outcome = _run(conn, job)
    assert outcome.verdict == "closed"
    assert outcome.reason == "this job is no longer accepting applications"


def test_unchanged_leaves_job_untouched(conn, board):
    job = _job(conn, board, status="accepted")
    q.upsert_job_score(conn, job["id"], q.get_scenarios(conn)[0]["id"], 0.4, "old", "h")
    with patch("app.pipeline.fetch_url_html", return_value=_LONG), \
         patch("app.pipeline.revisit_check", return_value=("unchanged", "same role")):
        _, outcome = _run(conn, job)
    assert outcome.verdict == "unchanged"
    row = q.get_job(conn, job["id"])
    assert row["status"] == "accepted"
    assert row["raw_text"] == "original body text"          # not overwritten
    assert q.get_job_scores(conn, job["id"])[0]["relevance_score"] == 0.4


def test_revisit_bumps_fetched_at_not_created_at(conn, board):
    job = _job(conn, board)
    conn.execute(
        "UPDATE jobs SET created_at='2024-01-01T00:00:00', fetched_at='2024-01-01T00:00:00' "
        "WHERE id=?", (job["id"],),
    )
    conn.commit()
    with patch("app.pipeline.fetch_url_html", return_value=_LONG), \
         patch("app.pipeline.revisit_check", return_value=("unchanged", "same")):
        _run(conn, job)
    row = q.get_job(conn, job["id"])
    assert row["created_at"] == "2024-01-01T00:00:00"
    assert row["fetched_at"] > "2024-01-01T00:00:00"


def test_revisit_bumps_fetched_at_even_when_closed(conn, board):
    job = _job(conn, board)
    conn.execute("UPDATE jobs SET fetched_at='2024-01-01T00:00:00' WHERE id=?", (job["id"],))
    conn.commit()
    with patch("app.pipeline.fetch_url_html", side_effect=FetchError("HTTP 404")), \
         patch("app.pipeline.time.sleep"):
        _, outcome = _run(conn, job)
    assert outcome.verdict == "closed"
    assert q.get_job(conn, job["id"])["fetched_at"] > "2024-01-01T00:00:00"


def test_changed_rescored_status_preserved(conn, board):
    job = _job(conn, board, status="accepted")
    client = _mock_client(
        '{"type": "job_posting", "reason": "ok"}',
        '{"title": "ML Engineer", "headline": "h2", "summary": "s2", "company": "Acme", "posted_date": ""}',
        '{"score": 0.95, "reasoning": "now great"}')
    with patch("app.pipeline.fetch_url_html", return_value=_LONG), \
         patch("app.pipeline.revisit_check", return_value=("changed", "comp band moved")):
        _, outcome = _run(conn, job, client)
    assert outcome.verdict == "changed"
    row = q.get_job(conn, job["id"])
    assert row["status"] == "accepted"
    assert row["summary"] == "s2"
    assert row["raw_text"] != "original body text"          # refreshed on real change
    assert q.get_job_scores(conn, job["id"])[0]["relevance_score"] == 0.95


def test_no_stored_summary_is_treated_as_reachable(conn, board):
    jid = q.insert_job(conn, source_id=board["id"], url="http://example.com/j2",
                       title="", company="", raw_text="x")
    job = q.get_job(conn, jid)
    with patch("app.pipeline.fetch_url_html", return_value=_LONG), \
         patch("app.pipeline.revisit_check") as check:
        _, outcome = _run(conn, job)
    check.assert_not_called()
    assert outcome.verdict == "unchanged"
    assert q.get_job(conn, jid)["status"] == "new"


def test_slack_source_is_skipped(conn):
    slk = q.insert_source(conn, "slk", "http://x.slack.com/c", "slack")
    q.upsert_profile(conn, "p")
    jid = q.insert_job(conn, source_id=slk, url="http://x.slack.com/c#1",
                       title="t", company="", raw_text="body")
    job = q.get_job(conn, jid)
    with patch("app.pipeline.fetch_url_html") as m:
        _, outcome = _drain(run_revisit_job(conn, _mock_client("{}", "{}", "{}"),
                                            "llama3.2", job, [], "p"))
    assert outcome.verdict == "skipped"
    m.assert_not_called()
    assert q.get_job(conn, jid)["status"] == "new"


def test_reprocess_logs_score_change_vs_pre_reset(conn, board):
    jid = q.insert_job(conn, source_id=board["id"], url="http://example.com/rp",
                       title="ML Engineer", company="Acme", raw_text="original posting body text " * 20)
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="job_posting",
                          summary="An ML role.")
    scn = q.get_scenarios(conn)[0]["id"]
    q.upsert_job_score(conn, jid, scn, 0.30, "old", "h")   # pre-reset baseline
    job = q.get_job(conn, jid)

    client = _mock_client(
        '{"type": "job_posting", "reason": "ok"}',
        '{"title": "ML Engineer", "headline": "h", "summary": "s", "company": "Acme", "posted_date": ""}',
        '{"score": 0.90, "reasoning": "much better now"}')
    _drain(run_reprocess_job(conn, client, "llama3.2", job, q.get_scenarios(conn), q.get_profile(conn)))

    msgs = [e["message"] for e in q.get_job_events(conn, jid)]
    assert any(m.startswith('Re-scored "Remote ML" 0.30 → 0.90') for m in msgs)


def test_changed_revisit_logs_event(conn, board):
    job = _job(conn, board, status="accepted")
    client = _mock_client(
        '{"type": "job_posting", "reason": "ok"}',
        '{"title": "ML Engineer", "headline": "h2", "summary": "s2", "company": "Acme", "posted_date": ""}',
        '{"score": 0.95, "reasoning": "now great"}')
    with patch("app.pipeline.fetch_url_html", return_value=_LONG), \
         patch("app.pipeline.revisit_check", return_value=("changed", "comp band moved")):
        _run(conn, job, client)
    msgs = [e["message"] for e in q.get_job_events(conn, job["id"])]
    assert "Revisit: posting changed — comp band moved" in msgs
