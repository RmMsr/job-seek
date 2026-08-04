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


def test_update_source(conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "http")
    q.update_source(conn, sid, url="https://finn.no/new", fetcher_type="playwright", enabled=False)
    source = q.get_source(conn, sid)
    assert source["url"] == "https://finn.no/new"
    assert source["fetcher_type"] == "playwright"
    assert source["enabled"] == 0
    assert source["name"] == "finn.no"


def test_update_scenario(conn):
    sid = q.insert_scenario(conn, "A", "old description")
    q.update_scenario(conn, sid, name="A renamed", description="new description")
    scenarios = {s["id"]: s for s in q.get_scenarios(conn)}
    assert scenarios[sid]["name"] == "A renamed"
    assert scenarios[sid]["description"] == "new description"


def test_criteria_insert_and_delete(conn):
    sid = q.insert_scenario(conn, "A", "")
    cid = q.insert_criterion(conn, sid, "Must be remote", "must")
    criteria = q.get_criteria(conn, sid)
    assert len(criteria) == 1
    assert criteria[0]["text"] == "Must be remote"
    q.delete_criterion(conn, cid)
    assert q.get_criteria(conn, sid) == []


def test_get_criterion(conn):
    sid = q.insert_scenario(conn, "A", "")
    cid = q.insert_criterion(conn, sid, "Must be remote", "must")
    criterion = q.get_criterion(conn, cid)
    assert criterion["id"] == cid
    assert criterion["text"] == "Must be remote"
    assert criterion["weight"] == "must"


def test_get_criterion_missing_returns_none(conn):
    assert q.get_criterion(conn, 999) is None


def test_update_criterion(conn):
    sid = q.insert_scenario(conn, "A", "")
    cid = q.insert_criterion(conn, sid, "Must be remote", "must")
    q.update_criterion(conn, cid, text="Must be fully remote", weight="prefer")
    criterion = q.get_criterion(conn, cid)
    assert criterion["text"] == "Must be fully remote"
    assert criterion["weight"] == "prefer"


def test_update_criterion_leaves_source_unchanged(conn):
    sid = q.insert_scenario(conn, "A", "")
    cid = q.insert_criterion(conn, sid, "Must be remote", "must", source="feedback")
    q.update_criterion(conn, cid, text="Must be fully remote", weight="prefer")
    assert q.get_criterion(conn, cid)["source"] == "feedback"


def test_url_exists(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    assert not q.url_exists(conn, "http://job/1")
    q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    assert q.url_exists(conn, "http://job/1")


def test_insert_job_stores_published_at(conn):
    source_id = q.insert_source(conn, "s", "http://x", "finn_listing")
    jid = q.insert_job(
        conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r",
        published_at="2026-07-01T00:00:00+00:00",
    )
    job = q.get_job(conn, jid)
    assert job["published_at"] == "2026-07-01T00:00:00+00:00"


def test_insert_job_published_at_defaults_to_none(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    job = q.get_job(conn, jid)
    assert job["published_at"] is None


def test_get_all_job_urls_empty(conn):
    assert q.get_all_job_urls(conn) == frozenset()


def test_get_all_job_urls_returns_all_urls(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.insert_job(conn, source_id=source_id, url="http://job/2", title="T2", company="C2", raw_text="r2")
    assert q.get_all_job_urls(conn) == frozenset({"http://job/1", "http://job/2"})


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
    )
    job = q.get_job(conn, jid)
    assert job["content_type"] == "job_posting"
    assert job["summary"] == "Good role"
    assert job["best_score"] is None


def test_update_job_pipeline_sets_title_and_headline(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="Scraped Title", company="C", raw_text="r")
    q.update_job_pipeline(
        conn, jid,
        simplified_content="clean text",
        content_type="job_posting",
        title="AI Title",
        summary="Good role",
        headline="Great hook",
    )
    job = q.get_job(conn, jid)
    assert job["title"] == "AI Title"
    assert job["headline"] == "Great hook"


def test_update_job_pipeline_leaves_title_unchanged_when_not_passed(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="Scraped Title", company="C", raw_text="r")
    q.update_job_pipeline(
        conn, jid,
        simplified_content="clean text",
        content_type="irrelevant",
    )
    job = q.get_job(conn, jid)
    assert job["title"] == "Scraped Title"
    assert job["headline"] == ""


def test_update_job_feedback(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_feedback(conn, jid, "accepted", "Great match")
    job = q.get_job(conn, jid)
    assert job["status"] == "accepted"
    assert job["feedback_note"] == "Great match"


def test_update_job_feedback_persists_scenario_id(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_feedback(conn, jid, "accepted", "good fit", feedback_scenario_id=scenario_id)
    job = q.get_job(conn, jid)
    assert job["feedback_scenario_id"] == scenario_id


def test_get_job_exposes_best_scenario_id(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_a = q.insert_scenario(conn, "A", "")
    scenario_b = q.insert_scenario(conn, "B", "")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.upsert_job_score(conn, jid, scenario_a, 0.4, "ok", "h1")
    q.upsert_job_score(conn, jid, scenario_b, 0.8, "great", "h2")
    job = q.get_job(conn, jid)
    assert job["best_scenario_id"] == scenario_b


def test_get_jobs_filter_by_status(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T1", company="C", raw_text="r")
    j2 = q.insert_job(conn, source_id=source_id, url="http://job/2", title="T2", company="C", raw_text="r")
    q.update_job_feedback(conn, j1, "accepted", "note")
    result = q.get_jobs(conn, status="accepted")
    assert len(result) == 1
    assert result[0]["id"] == j1


def test_get_job_counts(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T1", company="C", raw_text="r")
    j2 = q.insert_job(conn, source_id=source_id, url="http://job/2", title="T2", company="C", raw_text="r")
    j3 = q.insert_job(conn, source_id=source_id, url="http://job/3", title="T3", company="C", raw_text="r")
    q.update_job_feedback(conn, j1, "accepted", "note")
    q.update_job_feedback(conn, j2, "rejected", "note")
    q.update_job_pipeline(conn, j3, simplified_content="", content_type="lead")
    counts = q.get_job_counts(conn)
    assert counts == {"new": 1, "accepted": 1, "rejected": 1, "invalid": 0, "lead": 1}


def test_get_recent_feedback_notes(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_pipeline(conn, j1, simplified_content="", content_type="job_posting")
    q.upsert_job_score(conn, j1, scenario_id, 0.5, "reasoning", "hash1")
    q.update_job_feedback(conn, j1, "rejected", "too junior", feedback_scenario_id=scenario_id)
    notes = q.get_recent_feedback_notes(conn, scenario_id)
    assert "too junior" in notes


def test_get_recent_feedback_notes_scoped_to_scenario(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_a = q.insert_scenario(conn, "A", "")
    scenario_b = q.insert_scenario(conn, "B", "")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_pipeline(conn, j1, simplified_content="", content_type="job_posting")
    q.upsert_job_score(conn, j1, scenario_a, 0.5, "reasoning", "hash1")
    q.upsert_job_score(conn, j1, scenario_b, 0.9, "reasoning", "hash2")  # scores higher for B...
    q.update_job_feedback(conn, j1, "rejected", "too junior", feedback_scenario_id=scenario_a)  # ...but tagged to A
    assert q.get_recent_feedback_notes(conn, scenario_a) == ["too junior"]
    assert q.get_recent_feedback_notes(conn, scenario_b) == []


def test_get_recent_feedback_notes_excludes_invalid_status(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T1", company="C", raw_text="r")
    q.update_job_pipeline(conn, j1, simplified_content="", content_type="job_posting")
    q.upsert_job_score(conn, j1, scenario_id, 0.5, "reasoning", "hash1")
    q.update_job_feedback(conn, j1, "invalid", "expired listing", feedback_scenario_id=scenario_id)
    j2 = q.insert_job(conn, source_id=source_id, url="http://job/2", title="T2", company="C", raw_text="r")
    q.update_job_pipeline(conn, j2, simplified_content="", content_type="job_posting")
    q.upsert_job_score(conn, j2, scenario_id, 0.5, "reasoning", "hash2")
    q.update_job_feedback(conn, j2, "rejected", "too junior", feedback_scenario_id=scenario_id)
    notes = q.get_recent_feedback_notes(conn, scenario_id)
    assert notes == ["too junior"]


def test_fetch_run_lifecycle(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    run_id = q.start_fetch_run(conn, source_id)
    q.complete_fetch_run(conn, run_id, jobs_found=5, jobs_new=3)
    runs = q.get_recent_fetch_runs(conn)
    assert runs[0]["jobs_found"] == 5
    assert runs[0]["error"] is None


def test_upsert_job_score_inserts_then_updates(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.upsert_job_score(conn, jid, scenario_id, 0.4, "first pass", "hash1")
    q.upsert_job_score(conn, jid, scenario_id, 0.9, "second pass", "hash2")
    score = q.get_job_score(conn, jid, scenario_id)
    assert score["relevance_score"] == pytest.approx(0.9)
    assert score["score_reasoning"] == "second pass"
    assert score["scenario_version_hash"] == "hash2"


def test_get_job_score_missing_returns_none(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    assert q.get_job_score(conn, jid, scenario_id) is None


def test_get_job_score_hashes_scoped_to_scenario(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_a = q.insert_scenario(conn, "A", "")
    scenario_b = q.insert_scenario(conn, "B", "")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    j2 = q.insert_job(conn, source_id=source_id, url="http://job/2", title="T", company="C", raw_text="r")
    q.upsert_job_score(conn, j1, scenario_a, 0.5, "r1", "hash1")
    q.upsert_job_score(conn, j2, scenario_b, 0.5, "r2", "hash2")
    assert q.get_job_score_hashes(conn, scenario_a) == {j1: "hash1"}


def test_get_jobs_shows_best_score_across_scenarios(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_a = q.insert_scenario(conn, "A", "")
    scenario_b = q.insert_scenario(conn, "B", "")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="", content_type="job_posting")
    q.upsert_job_score(conn, jid, scenario_a, 0.3, "low fit", "hash1")
    q.upsert_job_score(conn, jid, scenario_b, 0.8, "great fit", "hash2")
    job = q.get_jobs(conn)[0]
    assert job["best_score"] == pytest.approx(0.8)
    assert job["best_score_reasoning"] == "great fit"
    assert job["best_scenario_name"] == "B"


def test_get_job_shows_best_score(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.upsert_job_score(conn, jid, scenario_id, 0.6, "decent", "hash1")
    job = q.get_job(conn, jid)
    assert job["best_score"] == pytest.approx(0.6)
    assert job["best_scenario_name"] == "A"


def test_get_jobs_orders_by_best_score_desc(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")
    low = q.insert_job(conn, source_id=source_id, url="http://job/low", title="Low", company="C", raw_text="r")
    high = q.insert_job(conn, source_id=source_id, url="http://job/high", title="High", company="C", raw_text="r")
    q.upsert_job_score(conn, low, scenario_id, 0.2, "", "hash1")
    q.upsert_job_score(conn, high, scenario_id, 0.9, "", "hash1")
    jobs = q.get_jobs(conn)
    assert [j["title"] for j in jobs] == ["High", "Low"]


def test_get_jobs_job_with_no_score_has_none(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    job = q.get_jobs(conn)[0]
    assert job["best_score"] is None
    assert job["best_scenario_name"] is None
