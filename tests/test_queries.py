import pytest
from app.db import queries as q


def test_profile_default_empty(conn):
    assert q.get_profile(conn) == ""


def test_upsert_profile(conn):
    q.upsert_profile(conn, "I am a senior ML engineer.")
    assert q.get_profile(conn) == "I am a senior ML engineer."
    q.upsert_profile(conn, "Updated profile.")
    assert q.get_profile(conn) == "Updated profile."


def test_insert_and_get_source(conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no/job/browse.html", "http")
    sources = q.get_sources(conn)
    assert len(sources) == 1
    assert sources[0]["name"] == "finn.no"
    assert q.get_source(conn, sid)["url"] == "https://finn.no/job/browse.html"


def test_get_sources_enabled_only(conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "http")
    conn.execute("UPDATE sources SET enabled = 0 WHERE id = ?", (sid,))
    assert q.get_sources(conn, enabled_only=True) == []
    assert len(q.get_sources(conn)) == 1


def test_scenarios_and_active(conn):
    sid = q.insert_scenario(conn, "Remote ML", "Looking for remote ML roles")
    assert q.get_active_scenario(conn) is None
    q.set_active_scenario(conn, sid)
    active = q.get_active_scenario(conn)
    assert active["name"] == "Remote ML"


def test_set_active_scenario_deactivates_others(conn):
    s1 = q.insert_scenario(conn, "A", "")
    s2 = q.insert_scenario(conn, "B", "")
    q.set_active_scenario(conn, s1)
    q.set_active_scenario(conn, s2)
    scenarios = q.get_scenarios(conn)
    active = [s for s in scenarios if s["active"]]
    assert len(active) == 1
    assert active[0]["id"] == s2


def test_criteria_insert_and_delete(conn):
    sid = q.insert_scenario(conn, "A", "")
    cid = q.insert_criterion(conn, sid, "Must be remote", "must")
    criteria = q.get_criteria(conn, sid)
    assert len(criteria) == 1
    assert criteria[0]["text"] == "Must be remote"
    q.delete_criterion(conn, cid)
    assert q.get_criteria(conn, sid) == []


def test_url_exists(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    assert not q.url_exists(conn, "http://job/1")
    q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    assert q.url_exists(conn, "http://job/1")


def test_insert_and_get_job(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="ML Eng", company="Acme", raw_text="raw")
    job = q.get_job(conn, jid)
    assert job["title"] == "ML Eng"
    assert job["status"] == "new"


def test_update_job_pipeline(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_pipeline(
        conn, jid,
        simplified_content="clean text",
        content_type="job_posting",
        summary="Good role",
        relevance_score=0.85,
        score_reasoning="Matches profile",
    )
    job = q.get_job(conn, jid)
    assert job["content_type"] == "job_posting"
    assert job["relevance_score"] == pytest.approx(0.85)


def test_update_job_feedback(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_feedback(conn, jid, "accepted", "Great match")
    job = q.get_job(conn, jid)
    assert job["status"] == "accepted"
    assert job["feedback_note"] == "Great match"


def test_get_jobs_filter_by_status(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T1", company="C", raw_text="r")
    j2 = q.insert_job(conn, source_id=source_id, url="http://job/2", title="T2", company="C", raw_text="r")
    q.update_job_feedback(conn, j1, "accepted", "note")
    result = q.get_jobs(conn, status="accepted")
    assert len(result) == 1
    assert result[0]["id"] == j1


def test_get_recent_feedback_notes(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_pipeline(conn, j1, simplified_content="", content_type="job_posting", scenario_id=scenario_id)
    q.update_job_feedback(conn, j1, "rejected", "too junior")
    notes = q.get_recent_feedback_notes(conn, scenario_id)
    assert "too junior" in notes


def test_fetch_run_lifecycle(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    run_id = q.start_fetch_run(conn, source_id)
    q.complete_fetch_run(conn, run_id, jobs_found=5, jobs_new=3)
    runs = q.get_recent_fetch_runs(conn)
    assert runs[0]["jobs_found"] == 5
    assert runs[0]["error"] is None
