import sqlite3
import pytest
from app.db import queries as q
from app.db.schema import init_db


def test_profile_default_empty(conn):
    assert q.get_profile(conn) == ""


def test_upsert_profile(conn):
    q.upsert_profile(conn, "I am a senior ML engineer.")
    assert q.get_profile(conn) == "I am a senior ML engineer."
    q.upsert_profile(conn, "Updated profile.")
    assert q.get_profile(conn) == "Updated profile."


def test_insert_and_get_source(conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no/job/browse.html", "generic_listing")
    sources = q.get_sources(conn)
    assert len(sources) == 1
    assert sources[0]["name"] == "finn.no"
    assert q.get_source(conn, sid)["url"] == "https://finn.no/job/browse.html"


def test_get_sources_enabled_only(conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "generic_listing")
    conn.execute("UPDATE sources SET enabled = 0 WHERE id = ?", (sid,))
    assert q.get_sources(conn, enabled_only=True) == []
    assert len(q.get_sources(conn)) == 1


def test_update_source(conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "generic_listing")
    q.update_source(
        conn, sid, name="Finn AI", url="https://finn.no/new", fetcher_type="finn_listing", enabled=False
    )
    source = q.get_source(conn, sid)
    assert source["url"] == "https://finn.no/new"
    assert source["fetcher_type"] == "finn_listing"
    assert source["enabled"] == 0
    assert source["name"] == "Finn AI"


def test_get_or_create_manual_source_creates_once(conn):
    first_id = q.get_or_create_manual_source(conn)
    second_id = q.get_or_create_manual_source(conn)
    assert first_id == second_id
    source = q.get_source(conn, first_id)
    assert source["name"] == "Manual"
    assert source["fetcher_type"] == "manual"
    sources = [s for s in q.get_sources(conn) if s["fetcher_type"] == "manual"]
    assert len(sources) == 1


def test_get_job_counts_by_source(conn):
    s1 = q.insert_source(conn, "s1", "http://x", "generic_listing")
    s2 = q.insert_source(conn, "s2", "http://y", "generic_listing")
    q.insert_job(conn, source_id=s1, url="http://job/1", title="T1", company="C", raw_text="r")
    q.insert_job(conn, source_id=s1, url="http://job/2", title="T2", company="C", raw_text="r")
    q.insert_job(conn, source_id=s2, url="http://job/3", title="T3", company="C", raw_text="r")
    counts = q.get_job_counts_by_source(conn)
    assert counts == {s1: 2, s2: 1}


def test_get_job_counts_by_source_omits_sources_with_no_jobs(conn):
    s1 = q.insert_source(conn, "s1", "http://x", "generic_listing")
    counts = q.get_job_counts_by_source(conn)
    assert counts == {}


def test_count_jobs_by_source(conn):
    s1 = q.insert_source(conn, "s1", "http://x", "generic_listing")
    q.insert_job(conn, source_id=s1, url="http://job/1", title="T1", company="C", raw_text="r")
    assert q.count_jobs_by_source(conn, s1) == 1


def test_count_jobs_by_source_zero_when_no_jobs(conn):
    s1 = q.insert_source(conn, "s1", "http://x", "generic_listing")
    assert q.count_jobs_by_source(conn, s1) == 0


def test_delete_source_removes_source_and_its_jobs(conn):
    s1 = q.insert_source(conn, "s1", "http://x", "generic_listing")
    jid = q.insert_job(conn, source_id=s1, url="http://job/1", title="T1", company="C", raw_text="r")
    q.delete_source(conn, s1)
    assert q.get_source(conn, s1) is None
    assert q.get_job(conn, jid) is None


def test_delete_source_removes_its_fetch_runs(conn):
    s1 = q.insert_source(conn, "s1", "http://x", "generic_listing")
    run_id = q.start_fetch_run(conn, s1)
    q.complete_fetch_run(conn, run_id, jobs_found=1, jobs_new=1)
    q.delete_source(conn, s1)
    assert q.get_recent_fetch_runs(conn) == []


def test_delete_source_cascades_to_job_scores_and_scenario_feedback(conn):
    s1 = q.insert_source(conn, "s1", "http://x", "generic_listing")
    scenario_id = q.insert_scenario(conn, "A", "")
    jid = q.insert_job(conn, source_id=s1, url="http://job/1", title="T1", company="C", raw_text="r")
    q.upsert_job_score(conn, jid, scenario_id, 0.5, "reasoning", "hash1")
    q.upsert_scenario_feedback(conn, jid, scenario_id, "note", "higher")

    q.delete_source(conn, s1)

    assert q.get_job_scores(conn, jid) == []
    row = conn.execute(
        "SELECT 1 FROM scenario_feedback WHERE job_id = ?", (jid,)
    ).fetchone()
    assert row is None


def test_delete_source_leaves_other_sources_and_jobs_intact(conn):
    s1 = q.insert_source(conn, "s1", "http://x", "generic_listing")
    s2 = q.insert_source(conn, "s2", "http://y", "generic_listing")
    q.insert_job(conn, source_id=s1, url="http://job/1", title="T1", company="C", raw_text="r")
    j2 = q.insert_job(conn, source_id=s2, url="http://job/2", title="T2", company="C", raw_text="r")

    q.delete_source(conn, s1)

    assert q.get_source(conn, s2) is not None
    assert q.get_job(conn, j2) is not None


def test_delete_job_removes_job(conn):
    sid = q.insert_source(conn, "s1", "http://x", "generic_listing")
    jid = q.insert_job(conn, source_id=sid, url="http://job/1", title="T1", company="C", raw_text="r")
    q.update_job_feedback(conn, jid, "trash", None)

    q.delete_job(conn, jid)

    assert q.get_job(conn, jid) is None


def test_delete_job_cascades_to_job_scores_and_scenario_feedback(conn):
    sid = q.insert_source(conn, "s1", "http://x", "generic_listing")
    scenario_id = q.insert_scenario(conn, "A", "")
    jid = q.insert_job(conn, source_id=sid, url="http://job/1", title="T1", company="C", raw_text="r")
    q.upsert_job_score(conn, jid, scenario_id, 0.5, "reasoning", "hash1")
    q.upsert_scenario_feedback(conn, jid, scenario_id, "note", "higher")
    q.update_job_feedback(conn, jid, "trash", None)

    q.delete_job(conn, jid)

    assert q.get_job_scores(conn, jid) == []
    row = conn.execute("SELECT 1 FROM scenario_feedback WHERE job_id = ?", (jid,)).fetchone()
    assert row is None


def test_delete_jobs_removes_only_trash_status_jobs(conn):
    sid = q.insert_source(conn, "s1", "http://x", "generic_listing")
    trash_id = q.insert_job(conn, source_id=sid, url="http://job/1", title="T1", company="C", raw_text="r")
    q.update_job_feedback(conn, trash_id, "trash", None)
    new_id = q.insert_job(conn, source_id=sid, url="http://job/2", title="T2", company="C", raw_text="r")

    q.delete_jobs(conn, [trash_id, new_id])

    assert q.get_job(conn, trash_id) is None
    assert q.get_job(conn, new_id) is not None


def test_delete_jobs_empty_list_is_noop(conn):
    sid = q.insert_source(conn, "s1", "http://x", "generic_listing")
    jid = q.insert_job(conn, source_id=sid, url="http://job/1", title="T1", company="C", raw_text="r")
    q.update_job_feedback(conn, jid, "trash", None)

    q.delete_jobs(conn, [])

    assert q.get_job(conn, jid) is not None


def test_get_job_by_url_returns_job(conn):
    sid = q.insert_source(conn, "s", "http://x", "generic_listing")
    jid = q.insert_job(conn, source_id=sid, url="http://job/1", title="T", company="C", raw_text="r")
    job = q.get_job_by_url(conn, "http://job/1")
    assert job["id"] == jid


def test_get_job_by_url_returns_none_when_missing(conn):
    assert q.get_job_by_url(conn, "http://nope") is None


def test_get_source_by_url_returns_source(conn):
    sid = q.insert_source(conn, "s", "http://x", "generic_listing")
    source = q.get_source_by_url(conn, "http://x")
    assert source["id"] == sid


def test_get_source_by_url_returns_none_when_missing(conn):
    assert q.get_source_by_url(conn, "http://nope") is None


def test_update_scenario(conn):
    sid = q.insert_scenario(conn, "A", "old description")
    q.update_scenario(conn, sid, name="A renamed", description="new description")
    scenarios = {s["id"]: s for s in q.get_scenarios(conn)}
    assert scenarios[sid]["name"] == "A renamed"
    assert scenarios[sid]["description"] == "new description"


def test_update_scenario_persists_gate_threshold(conn):
    sid = q.insert_scenario(conn, "ai_expert", "fallback")
    q.update_scenario(conn, sid, name="ai_expert", description="fallback", gate_threshold=0.5)
    scenario = {s["id"]: s for s in q.get_scenarios(conn)}[sid]
    assert scenario["gate_threshold"] == pytest.approx(0.5)


def test_update_scenario_gate_threshold_defaults_to_0_7(conn):
    sid = q.insert_scenario(conn, "A", "")
    q.update_scenario(conn, sid, name="A", description="")
    scenario = {s["id"]: s for s in q.get_scenarios(conn)}[sid]
    assert scenario["gate_threshold"] == pytest.approx(0.7)


def test_get_job_scores_returns_all_scenarios_ordered_by_raw_score(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    scenario_a = q.insert_scenario(conn, "A", "")
    scenario_b = q.insert_scenario(conn, "B", "")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.upsert_job_score(conn, jid, scenario_a, 0.7, "a reasoning", "h1")
    q.upsert_job_score(conn, jid, scenario_b, 0.4, "b reasoning", "h2")

    scores = q.get_job_scores(conn, jid)

    assert [s["scenario_id"] for s in scores] == [scenario_a, scenario_b]  # raw score order
    assert scores[0]["scenario_name"] == "A"
    assert scores[0]["scenario_gate_threshold"] == pytest.approx(0.7)
    assert scores[0]["score_reasoning"] == "a reasoning"
    assert scores[1]["scenario_name"] == "B"


def test_get_job_scores_empty_for_unscored_job(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    assert q.get_job_scores(conn, jid) == []


def test_criteria_insert_and_delete(conn):
    sid = q.insert_scenario(conn, "A", "")
    cid = q.insert_criterion(conn, sid, "Must be remote", "must")
    criteria = q.get_criteria(conn, sid)
    assert len(criteria) == 1
    assert criteria[0]["text"] == "Must be remote"
    q.delete_criterion(conn, cid)
    assert q.get_criteria(conn, sid) == []


def test_get_criteria_orders_must_prefer_avoid(conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    # Insert out of weight order to prove sorting isn't just created_at passthrough.
    q.insert_criterion(conn, sid, "Avoid startups", "avoid")
    q.insert_criterion(conn, sid, "Prefer Python", "prefer")
    q.insert_criterion(conn, sid, "Must be remote", "must")
    q.insert_criterion(conn, sid, "Must pay well", "must")

    criteria = q.get_criteria(conn, sid)

    assert [c["weight"] for c in criteria] == ["must", "must", "prefer", "avoid"]
    # Within the same weight, original (created_at) order is preserved.
    assert [c["text"] for c in criteria if c["weight"] == "must"] == ["Must be remote", "Must pay well"]


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
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
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


def test_insert_job_published_at_defaults_to_processing_time(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    job = q.get_job(conn, jid)
    assert job["published_at"] is not None
    # Same INSERT statement ⇒ SQLite freezes datetime('now'), so byte-identical.
    assert job["published_at"] == job["fetched_at"]


def test_get_all_job_urls_empty(conn):
    assert q.get_all_job_urls(conn) == frozenset()


def test_get_all_job_urls_returns_all_urls(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.insert_job(conn, source_id=source_id, url="http://job/2", title="T2", company="C2", raw_text="r2")
    assert q.get_all_job_urls(conn) == frozenset({"http://job/1", "http://job/2"})


def test_insert_and_get_job(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="ML Eng", company="Acme", raw_text="raw")
    job = q.get_job(conn, jid)
    assert job["title"] == "ML Eng"
    assert job["status"] == "new"


def test_update_job_pipeline(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
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
    assert job["fit_score"] is None


def test_update_job_pipeline_sets_title_and_headline(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
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
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="Scraped Title", company="C", raw_text="r")
    q.update_job_pipeline(
        conn, jid,
        simplified_content="clean text",
        content_type="irrelevant",
    )
    job = q.get_job(conn, jid)
    assert job["title"] == "Scraped Title"
    assert job["headline"] == ""


def test_update_job_pipeline_sets_company_and_published_at(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="", raw_text="r")
    q.update_job_pipeline(
        conn, jid, simplified_content="clean", content_type="job_posting",
        summary="s", company="Zivid", published_at="2026-08-17",
    )
    job = q.get_job(conn, jid)
    assert job["company"] == "Zivid"
    assert job["published_at"] == "2026-08-17"


def test_update_job_pipeline_empty_company_and_date_keep_existing(conn):
    source_id = q.insert_source(conn, "s", "http://x", "finn_listing")
    jid = q.insert_job(
        conn, source_id=source_id, url="http://job/1", title="T", company="Acme", raw_text="r",
        published_at="2026-07-01T00:00:00+00:00",
    )
    q.update_job_pipeline(
        conn, jid, simplified_content="clean", content_type="job_posting", summary="s",
    )
    job = q.get_job(conn, jid)
    assert job["company"] == "Acme"
    assert job["published_at"] == "2026-07-01T00:00:00+00:00"


def test_update_job_feedback(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_feedback(conn, jid, "accepted", "Great match")
    job = q.get_job(conn, jid)
    assert job["status"] == "accepted"
    assert job["feedback_note"] == "Great match"


def test_reset_job_clears_pipeline_output_and_scores(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    scenario_id = q.insert_scenario(conn, "A", "")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_pipeline(
        conn, jid, simplified_content="clean", content_type="job_posting",
        title="AI Title", headline="hook", summary="summary text",
    )
    q.upsert_job_score(conn, jid, scenario_id, 0.8, "great", "h1")
    q.update_job_fit(conn, jid, 0.7, "good interest", 0.6, "some gaps", "phash1")
    q.upsert_scenario_feedback(conn, jid, scenario_id, "shouldn't count", "lower")
    q.update_job_feedback(conn, jid, "accepted", "note")
    q.mark_job_evaluation_complete(conn, jid)

    q.reset_job(conn, jid)

    job = q.get_job(conn, jid)
    assert job["status"] == "new"
    assert job["content_type"] is None
    assert job["simplified_content"] == ""
    assert job["summary"] == ""
    assert job["headline"] == ""
    assert job["feedback_note"] == "note"
    assert job["raw_text"] == "r"
    assert job["interest_score"] is None
    assert job["fit_score"] is None
    assert job["profile_version_hash"] is None
    assert job["evaluation_completed_at"] is None
    assert q.get_job_scores(conn, jid) == []
    assert q.get_recent_feedback_notes(conn, scenario_id) == []


def test_mark_job_evaluation_complete_sets_timestamp(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    assert q.get_job(conn, jid)["evaluation_completed_at"] is None

    q.mark_job_evaluation_complete(conn, jid)

    assert q.get_job(conn, jid)["evaluation_completed_at"] is not None


def test_reset_job_clears_gate_override(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.mark_job_gate_override(conn, jid)

    q.reset_job(conn, jid)

    job = q.get_job(conn, jid)
    assert job["gate_override"] == 0


def test_mark_job_gate_override_sets_flag(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")

    q.mark_job_gate_override(conn, jid)

    job = q.get_job(conn, jid)
    assert job["gate_override"] == 1


def test_get_job_exposes_top_passed_scenario_id(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    scenario_a = q.insert_scenario(conn, "A", "")
    scenario_b = q.insert_scenario(conn, "B", "")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.upsert_job_score(conn, jid, scenario_a, 0.4, "ok", "h1")
    q.upsert_job_score(conn, jid, scenario_b, 0.8, "great", "h2")
    job = q.get_job(conn, jid)
    assert job["top_passed_scenario_id"] == scenario_b
    assert job["top_passed_scenario_name"] == "B"


def test_get_jobs_works_without_feedback_scenario_id_column(conn):
    # Regression guard: _GATE_JOIN used to join on jobs.feedback_scenario_id,
    # which Task 1 dropped — this must not raise sqlite3.OperationalError.
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    assert q.get_jobs(conn) is not None


def test_get_jobs_filter_by_status(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T1", company="C", raw_text="r")
    j2 = q.insert_job(conn, source_id=source_id, url="http://job/2", title="T2", company="C", raw_text="r")
    q.update_job_feedback(conn, j1, "accepted", "note")
    result = q.get_jobs(conn, status="accepted")
    assert len(result) == 1
    assert result[0]["id"] == j1


def test_get_jobs_filter_by_source_id_ignores_status(conn):
    s1 = q.insert_source(conn, "s1", "http://x", "generic_listing")
    s2 = q.insert_source(conn, "s2", "http://y", "generic_listing")
    j1 = q.insert_job(conn, source_id=s1, url="http://job/1", title="T1", company="C", raw_text="r")
    j2 = q.insert_job(conn, source_id=s1, url="http://job/2", title="T2", company="C", raw_text="r")
    q.insert_job(conn, source_id=s2, url="http://job/3", title="T3", company="C", raw_text="r")
    q.update_job_feedback(conn, j2, "accepted", "note")

    result = q.get_jobs(conn, source_id=s1)

    assert {j["id"] for j in result} == {j1, j2}


def test_get_job_counts(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T1", company="C", raw_text="r")
    j2 = q.insert_job(conn, source_id=source_id, url="http://job/2", title="T2", company="C", raw_text="r")
    j3 = q.insert_job(conn, source_id=source_id, url="http://job/3", title="T3", company="C", raw_text="r")
    q.update_job_feedback(conn, j1, "accepted", "note")
    q.update_job_feedback(conn, j2, "rejected", "note")
    q.update_job_pipeline(conn, j3, simplified_content="", content_type="lead")
    q.mark_job_evaluation_complete(conn, j3)
    counts = q.get_job_counts(conn)
    assert counts == {"new": 0, "accepted": 1, "rejected": 1, "trash": 0, "lead": 1, "not_relevant": 0}


def test_get_job_counts_splits_new_from_not_relevant(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    scenario_id = q.insert_scenario(conn, "A", "")  # default gate_threshold 0.7
    passed = q.insert_job(conn, source_id=source_id, url="http://job/passed", title="Passed", company="C", raw_text="r")
    failed = q.insert_job(conn, source_id=source_id, url="http://job/failed", title="Failed", company="C", raw_text="r")
    q.update_job_pipeline(conn, passed, simplified_content="", content_type="job_posting")
    q.update_job_pipeline(conn, failed, simplified_content="", content_type="job_posting")
    q.upsert_job_score(conn, passed, scenario_id, 0.9, "", "hash1")
    q.upsert_job_score(conn, failed, scenario_id, 0.3, "", "hash2")
    q.mark_job_evaluation_complete(conn, passed)
    q.mark_job_evaluation_complete(conn, failed)

    counts = q.get_job_counts(conn)

    assert counts["new"] == 1
    assert counts["not_relevant"] == 1
    assert counts["new"] + counts["not_relevant"] == 2  # raw status='new' total


def test_get_job_counts_excludes_overridden_job_from_not_relevant(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    scenario_id = q.insert_scenario(conn, "A", "")  # default gate_threshold 0.7
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="Overridden", company="C", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="", content_type="job_posting")
    q.upsert_job_score(conn, jid, scenario_id, 0.3, "", "hash1")
    q.mark_job_gate_override(conn, jid)

    counts = q.get_job_counts(conn)

    assert counts["not_relevant"] == 0


def test_get_job_counts_new_excludes_irrelevant_and_error(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    posting = q.insert_job(conn, source_id=source_id, url="http://job/post", title="Post", company="C", raw_text="r")
    irrelevant = q.insert_job(conn, source_id=source_id, url="http://job/irr", title="Irr", company="C", raw_text="r")
    error = q.insert_job(conn, source_id=source_id, url="http://job/err", title="Err", company="C", raw_text="r")
    q.update_job_pipeline(conn, posting, simplified_content="", content_type="job_posting")
    q.update_job_pipeline(conn, irrelevant, simplified_content="", content_type="irrelevant")
    q.update_job_pipeline(conn, error, simplified_content="", content_type="error")
    q.mark_job_evaluation_complete(conn, posting)

    counts = q.get_job_counts(conn)

    assert counts["new"] == 1


def test_get_unhandled_profile_notes_returns_notes(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_feedback(conn, j1, "rejected", "No AI focus")
    notes = q.get_unhandled_profile_notes(conn)
    assert notes == [{"id": j1, "status": "rejected", "feedback_note": "No AI focus"}]


def test_get_unhandled_profile_notes_excludes_empty_notes(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_feedback(conn, j1, "accepted", "")
    assert q.get_unhandled_profile_notes(conn) == []


def test_get_unhandled_profile_notes_excludes_handled(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_feedback(conn, j1, "rejected", "No AI focus")
    q.mark_profile_feedback_handled(conn, [j1])
    assert q.get_unhandled_profile_notes(conn) == []


def test_get_unhandled_profile_notes_orders_most_recent_first(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T1", company="C", raw_text="r")
    j2 = q.insert_job(conn, source_id=source_id, url="http://job/2", title="T2", company="C", raw_text="r")
    q.update_job_feedback(conn, j1, "rejected", "first")
    q.update_job_feedback(conn, j2, "accepted", "second")
    notes = q.get_unhandled_profile_notes(conn)
    assert [n["id"] for n in notes] == [j2, j1]


def test_get_unhandled_profile_notes_respects_limit(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    for i in range(3):
        jid = q.insert_job(conn, source_id=source_id, url=f"http://job/{i}", title="T", company="C", raw_text="r")
        q.update_job_feedback(conn, jid, "rejected", f"note {i}")
    assert len(q.get_unhandled_profile_notes(conn, limit=2)) == 2


def test_mark_profile_feedback_handled_only_marks_given_jobs(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T1", company="C", raw_text="r")
    j2 = q.insert_job(conn, source_id=source_id, url="http://job/2", title="T2", company="C", raw_text="r")
    q.update_job_feedback(conn, j1, "rejected", "one")
    q.update_job_feedback(conn, j2, "rejected", "two")

    q.mark_profile_feedback_handled(conn, [j1])

    remaining = q.get_unhandled_profile_notes(conn)
    assert [n["id"] for n in remaining] == [j2]


def test_mark_profile_feedback_handled_empty_list_is_noop(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_feedback(conn, j1, "rejected", "note")
    q.mark_profile_feedback_handled(conn, [])
    assert len(q.get_unhandled_profile_notes(conn)) == 1


def test_get_recent_feedback_notes(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    scenario_id = q.insert_scenario(conn, "A", "")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, j1, scenario_id, "too junior", "lower")
    notes = q.get_recent_feedback_notes(conn, scenario_id)
    assert {"direction": "lower", "note": "too junior"} in notes


def test_get_recent_feedback_notes_reports_direction(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    scenario_id = q.insert_scenario(conn, "A", "")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, j1, scenario_id, "should have counted", "higher")
    notes = q.get_recent_feedback_notes(conn, scenario_id)
    assert notes == [{"direction": "higher", "note": "should have counted"}]


def test_get_recent_feedback_notes_scoped_to_scenario(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    scenario_a = q.insert_scenario(conn, "A", "")
    scenario_b = q.insert_scenario(conn, "B", "")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, j1, scenario_a, "too junior", "lower")
    assert q.get_recent_feedback_notes(conn, scenario_a) == [{"direction": "lower", "note": "too junior"}]
    assert q.get_recent_feedback_notes(conn, scenario_b) == []


def test_get_recent_feedback_notes_excludes_undirected_comments(conn):
    # A note left with no direction chosen is pure commentary — propose_criteria
    # has no polarity to act on, so it must not see it.
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    scenario_id = q.insert_scenario(conn, "A", "")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, j1, scenario_id, "just a thought", None)
    assert q.get_recent_feedback_notes(conn, scenario_id) == []


def test_get_recent_feedback_notes_excludes_feedback_older_than_window(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    scenario_id = q.insert_scenario(conn, "A", "")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T1", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, j1, scenario_id, "too junior", "lower")
    conn.execute(
        "UPDATE scenario_feedback SET created_at = datetime('now', '-40 days') WHERE job_id = ? AND scenario_id = ?",
        (j1, scenario_id),
    )
    conn.commit()
    assert q.get_recent_feedback_notes(conn, scenario_id) == []


def test_get_recent_feedback_notes_includes_feedback_within_window(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    scenario_id = q.insert_scenario(conn, "A", "")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T1", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, j1, scenario_id, "too junior", "lower")
    conn.execute(
        "UPDATE scenario_feedback SET created_at = datetime('now', '-20 days') WHERE job_id = ? AND scenario_id = ?",
        (j1, scenario_id),
    )
    conn.commit()
    assert q.get_recent_feedback_notes(conn, scenario_id) == [{"direction": "lower", "note": "too junior"}]


def test_get_recent_feedback_notes_row_cap_applies_within_window(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    scenario_id = q.insert_scenario(conn, "A", "")
    for i in range(3):
        j = q.insert_job(conn, source_id=source_id, url=f"http://job/{i}", title="T", company="C", raw_text="r")
        q.upsert_scenario_feedback(conn, j, scenario_id, f"note {i}", "lower")
    notes = q.get_recent_feedback_notes(conn, scenario_id, limit=2)
    assert len(notes) == 2


def test_get_recent_feedback_notes_excludes_handled(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    scenario_id = q.insert_scenario(conn, "A", "")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T1", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, j1, scenario_id, "too junior", "lower")
    anchor = q.get_recent_feedback_anchor(conn, scenario_id)
    q.mark_feedback_handled(conn, scenario_id, anchor)
    assert q.get_recent_feedback_notes(conn, scenario_id) == []


def test_get_recent_feedback_anchor_is_newest_created_at(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    scenario_id = q.insert_scenario(conn, "A", "")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T1", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, j1, scenario_id, "too junior", "lower")
    row = conn.execute(
        "SELECT created_at FROM scenario_feedback WHERE job_id = ? AND scenario_id = ?", (j1, scenario_id)
    ).fetchone()
    assert q.get_recent_feedback_anchor(conn, scenario_id) == row["created_at"]


def test_get_recent_feedback_anchor_none_when_no_recent_feedback(conn):
    scenario_id = q.insert_scenario(conn, "A", "")
    assert q.get_recent_feedback_anchor(conn, scenario_id) is None


def test_mark_feedback_handled_scoped_to_one_scenario(conn):
    # A job can carry independent gate feedback for two scenarios — handling
    # one scenario's proposals must not clear the other's pending feedback.
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    scenario_a = q.insert_scenario(conn, "A", "")
    scenario_b = q.insert_scenario(conn, "B", "")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T1", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, j1, scenario_a, "too junior", "lower")
    q.upsert_scenario_feedback(conn, j1, scenario_b, "should count here too", "higher")
    anchor_a = q.get_recent_feedback_anchor(conn, scenario_a)
    q.mark_feedback_handled(conn, scenario_a, anchor_a)
    assert q.get_recent_feedback_notes(conn, scenario_a) == []
    assert q.get_recent_feedback_notes(conn, scenario_b) == [{"direction": "higher", "note": "should count here too"}]


def test_mark_feedback_handled_sweeps_rows_beyond_the_cap(conn):
    # The LLM only ever sees the 20 most recent rows, but accepting that
    # batch's proposals should clear every unhandled row up to that point,
    # not just the 20 that were sampled — otherwise stragglers beyond the
    # cap can never be marked handled.
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    scenario_id = q.insert_scenario(conn, "A", "")
    for i in range(3):
        j = q.insert_job(conn, source_id=source_id, url=f"http://job/{i}", title="T", company="C", raw_text="r")
        q.upsert_scenario_feedback(conn, j, scenario_id, f"note {i}", "lower")

    anchor = q.get_recent_feedback_anchor(conn, scenario_id, limit=1)  # only the newest row is "in the batch"
    q.mark_feedback_handled(conn, scenario_id, anchor)

    assert q.get_recent_feedback_notes(conn, scenario_id, limit=10) == []


def test_mark_feedback_handled_leaves_rows_created_after_anchor(conn):
    # A vote cast after refine was triggered (but before its proposals were
    # accepted) wasn't part of what the LLM saw, so it must stay unhandled.
    # created_at has only second-level resolution, so backdate the first
    # row explicitly rather than relying on real-time ordering between two
    # upserts in the same test.
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    scenario_id = q.insert_scenario(conn, "A", "")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T1", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, j1, scenario_id, "too junior", "lower")
    conn.execute(
        "UPDATE scenario_feedback SET created_at = datetime('now', '-1 minutes') WHERE job_id = ? AND scenario_id = ?",
        (j1, scenario_id),
    )
    conn.commit()
    anchor = q.get_recent_feedback_anchor(conn, scenario_id)

    j2 = q.insert_job(conn, source_id=source_id, url="http://job/2", title="T2", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, j2, scenario_id, "should count higher", "higher")

    q.mark_feedback_handled(conn, scenario_id, anchor)

    assert q.get_recent_feedback_notes(conn, scenario_id) == [{"direction": "higher", "note": "should count higher"}]


def test_mark_feedback_handled_none_anchor_is_noop(conn):
    scenario_id = q.insert_scenario(conn, "A", "")
    q.mark_feedback_handled(conn, scenario_id, None)  # must not raise


def test_upsert_scenario_feedback_resets_handled_state(conn):
    # Re-saving feedback on a job is fresh input the LLM hasn't seen yet,
    # even if its prior feedback had already been handled.
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    scenario_id = q.insert_scenario(conn, "A", "")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T1", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, j1, scenario_id, "too junior", "lower")
    anchor = q.get_recent_feedback_anchor(conn, scenario_id)
    q.mark_feedback_handled(conn, scenario_id, anchor)
    q.upsert_scenario_feedback(conn, j1, scenario_id, "actually, too senior", "lower")
    assert q.get_recent_feedback_notes(conn, scenario_id) == [{"direction": "lower", "note": "actually, too senior"}]


def test_fetch_run_lifecycle(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    run_id = q.start_fetch_run(conn, source_id)
    q.complete_fetch_run(conn, run_id, jobs_found=5, jobs_new=3)
    runs = q.get_recent_fetch_runs(conn)
    assert runs[0]["jobs_found"] == 5
    assert runs[0]["error"] is None


def test_upsert_job_score_inserts_then_updates(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    scenario_id = q.insert_scenario(conn, "A", "")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.upsert_job_score(conn, jid, scenario_id, 0.4, "first pass", "hash1")
    q.upsert_job_score(conn, jid, scenario_id, 0.9, "second pass", "hash2")
    score = q.get_job_score(conn, jid, scenario_id)
    assert score["relevance_score"] == pytest.approx(0.9)
    assert score["score_reasoning"] == "second pass"
    assert score["scenario_version_hash"] == "hash2"


def test_get_job_score_missing_returns_none(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    scenario_id = q.insert_scenario(conn, "A", "")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    assert q.get_job_score(conn, jid, scenario_id) is None


def test_get_job_score_hashes_scoped_to_scenario(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    scenario_a = q.insert_scenario(conn, "A", "")
    scenario_b = q.insert_scenario(conn, "B", "")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    j2 = q.insert_job(conn, source_id=source_id, url="http://job/2", title="T", company="C", raw_text="r")
    q.upsert_job_score(conn, j1, scenario_a, 0.5, "r1", "hash1")
    q.upsert_job_score(conn, j2, scenario_b, 0.5, "r2", "hash2")
    assert q.get_job_score_hashes(conn, scenario_a) == {j1: "hash1"}


def test_get_jobs_reports_all_passed_scenario_names(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    scenario_a = q.insert_scenario(conn, "A", "")
    scenario_b = q.insert_scenario(conn, "B", "")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="", content_type="job_posting")
    q.upsert_job_score(conn, jid, scenario_a, 0.8, "great fit", "hash1")
    q.upsert_job_score(conn, jid, scenario_b, 0.9, "even better", "hash2")
    job = q.get_jobs(conn)[0]
    assert job["passed_gate_count"] == 2
    assert set(job["passed_scenario_names"].split(", ")) == {"A", "B"}


def test_get_jobs_excludes_scenario_below_its_own_gate_threshold(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    strict = q.insert_scenario(conn, "Strict", "")
    q.update_scenario(conn, strict, name="Strict", description="", gate_threshold=0.9)
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.upsert_job_score(conn, jid, strict, 0.8, "close but no", "hash1")  # below 0.9
    job = q.get_jobs(conn)[0]
    assert job["passed_gate_count"] is None


def test_get_jobs_gate_status_passed_excludes_below_threshold(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    scenario_id = q.insert_scenario(conn, "A", "")  # default gate_threshold 0.7
    below = q.insert_job(conn, source_id=source_id, url="http://job/below", title="Below", company="C", raw_text="r")
    above = q.insert_job(conn, source_id=source_id, url="http://job/above", title="Above", company="C", raw_text="r")
    q.update_job_pipeline(conn, below, simplified_content="", content_type="job_posting")
    q.update_job_pipeline(conn, above, simplified_content="", content_type="job_posting")
    q.upsert_job_score(conn, below, scenario_id, 0.5, "", "hash1")
    q.upsert_job_score(conn, above, scenario_id, 0.9, "", "hash2")
    q.mark_job_evaluation_complete(conn, below)
    q.mark_job_evaluation_complete(conn, above)

    all_jobs = q.get_jobs(conn)
    assert {j["title"] for j in all_jobs} == {"Below", "Above"}

    passed_only = q.get_jobs(conn, gate_status="passed")
    assert [j["title"] for j in passed_only] == ["Above"]


def test_get_jobs_gate_status_passed_excludes_unclassified_jobs_still_in_evaluation(conn):
    # A freshly inserted job has content_type = NULL until classify() runs
    # in the background — it's still mid-pipeline, not yet gated one way or
    # the other, so it must not appear as if it had passed.
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    scenario_id = q.insert_scenario(conn, "A", "")
    scored_and_failed = q.insert_job(conn, source_id=source_id, url="http://job/failed", title="Failed", company="C", raw_text="r")
    still_evaluating = q.insert_job(conn, source_id=source_id, url="http://job/pending", title="Pending", company="C", raw_text="r")
    q.update_job_pipeline(conn, scored_and_failed, simplified_content="", content_type="job_posting")
    q.upsert_job_score(conn, scored_and_failed, scenario_id, 0.5, "", "hash1")  # below default 0.7

    passed_only = q.get_jobs(conn, gate_status="passed")

    assert passed_only == []


def test_get_jobs_gate_status_passed_excludes_job_mid_scoring_loop(conn):
    # Classified, and its one recorded score already clears the gate, but
    # the pipeline hasn't reached mark_job_evaluation_complete yet (still
    # scoring against other scenarios) — must not appear as passed.
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    scenario_id = q.insert_scenario(conn, "A", "")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="Mid-scoring", company="C", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="", content_type="job_posting")
    q.upsert_job_score(conn, jid, scenario_id, 0.9, "", "hash1")  # clears the gate...

    passed_only = q.get_jobs(conn, gate_status="passed")
    failed_only = q.get_jobs(conn, gate_status="failed")

    assert passed_only == []  # ...but shouldn't show yet, evaluation isn't marked complete
    assert failed_only == []


def test_get_jobs_gate_status_passed_includes_job_once_evaluation_marked_complete(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    scenario_id = q.insert_scenario(conn, "A", "")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="Done", company="C", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="", content_type="job_posting")
    q.upsert_job_score(conn, jid, scenario_id, 0.9, "", "hash1")
    q.mark_job_evaluation_complete(conn, jid)

    passed_only = q.get_jobs(conn, gate_status="passed")

    assert [j["title"] for j in passed_only] == ["Done"]


def test_get_jobs_gate_status_passed_excludes_irrelevant_and_error(conn):
    # irrelevant/error content is never scored (evaluate() only runs for
    # job_posting/lead), so the old "content_type != job_posting" escape
    # hatch let it slip into the default New view as if it were pending.
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    irrelevant = q.insert_job(conn, source_id=source_id, url="http://job/irr", title="Irr", company="C", raw_text="r")
    error = q.insert_job(conn, source_id=source_id, url="http://job/err", title="Err", company="C", raw_text="r")
    posting = q.insert_job(conn, source_id=source_id, url="http://job/post", title="Post", company="C", raw_text="r")
    q.update_job_pipeline(conn, irrelevant, simplified_content="", content_type="irrelevant")
    q.update_job_pipeline(conn, error, simplified_content="", content_type="error")
    q.update_job_pipeline(conn, posting, simplified_content="", content_type="job_posting")
    q.mark_job_evaluation_complete(conn, posting)

    passed_only = q.get_jobs(conn, gate_status="passed")

    assert [j["title"] for j in passed_only] == ["Post"]


def test_get_jobs_gate_status_passed_excludes_lead_below_threshold(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    scenario_id = q.insert_scenario(conn, "A", "")  # default gate_threshold 0.7
    lead = q.insert_job(conn, source_id=source_id, url="http://job/lead", title="Lead", company="C", raw_text="r")
    q.update_job_pipeline(conn, lead, simplified_content="", content_type="lead")
    q.upsert_job_score(conn, lead, scenario_id, 0.3, "", "hash1")

    passed_only = q.get_jobs(conn, gate_status="passed")

    assert passed_only == []


def test_get_jobs_gate_status_passed_keeps_unscored_and_gate_passed_leads(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    scenario_id = q.insert_scenario(conn, "A", "")  # default gate_threshold 0.7
    unscored_lead = q.insert_job(conn, source_id=source_id, url="http://job/lead1", title="Unscored lead", company="C", raw_text="r")
    passed_lead = q.insert_job(conn, source_id=source_id, url="http://job/lead2", title="Passed lead", company="C", raw_text="r")
    q.update_job_pipeline(conn, unscored_lead, simplified_content="", content_type="lead")
    q.update_job_pipeline(conn, passed_lead, simplified_content="", content_type="lead")
    q.upsert_job_score(conn, passed_lead, scenario_id, 0.9, "", "hash1")
    q.mark_job_evaluation_complete(conn, unscored_lead)
    q.mark_job_evaluation_complete(conn, passed_lead)

    passed_only = q.get_jobs(conn, gate_status="passed")

    assert {j["title"] for j in passed_only} == {"Unscored lead", "Passed lead"}


def test_get_jobs_gate_status_failed_returns_only_failed_postings(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    scenario_id = q.insert_scenario(conn, "A", "")  # default gate_threshold 0.7
    failed = q.insert_job(conn, source_id=source_id, url="http://job/failed", title="Failed", company="C", raw_text="r")
    passed = q.insert_job(conn, source_id=source_id, url="http://job/passed", title="Passed", company="C", raw_text="r")
    unscored = q.insert_job(conn, source_id=source_id, url="http://job/unscored", title="Unscored", company="C", raw_text="r")
    q.update_job_pipeline(conn, failed, simplified_content="", content_type="job_posting")
    q.update_job_pipeline(conn, passed, simplified_content="", content_type="job_posting")
    q.upsert_job_score(conn, failed, scenario_id, 0.3, "", "hash1")
    q.upsert_job_score(conn, passed, scenario_id, 0.9, "", "hash2")
    q.mark_job_evaluation_complete(conn, failed)
    q.mark_job_evaluation_complete(conn, passed)

    failed_only = q.get_jobs(conn, gate_status="failed")

    assert [j["title"] for j in failed_only] == ["Failed"]


def test_get_jobs_gate_status_passed_includes_overridden_job_that_failed_score(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    scenario_id = q.insert_scenario(conn, "A", "")  # default gate_threshold 0.7
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="Overridden", company="C", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="", content_type="job_posting")
    q.upsert_job_score(conn, jid, scenario_id, 0.3, "", "hash1")
    q.mark_job_gate_override(conn, jid)

    passed_only = q.get_jobs(conn, gate_status="passed")

    assert [j["title"] for j in passed_only] == ["Overridden"]


def test_get_jobs_gate_status_failed_excludes_overridden_job(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    scenario_id = q.insert_scenario(conn, "A", "")  # default gate_threshold 0.7
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="Overridden", company="C", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="", content_type="job_posting")
    q.upsert_job_score(conn, jid, scenario_id, 0.3, "", "hash1")
    q.mark_job_gate_override(conn, jid)

    failed_only = q.get_jobs(conn, gate_status="failed")

    assert failed_only == []


def test_get_jobs_gate_status_failed_paired_with_job_posting_excludes_leads(conn):
    # Route layer always pairs gate_status="failed" with content_type="job_posting"
    # so a gate-failed lead never shows up in "Not relevant" — it's Leads-only.
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    scenario_id = q.insert_scenario(conn, "A", "")  # default gate_threshold 0.7
    lead = q.insert_job(conn, source_id=source_id, url="http://job/lead", title="Lead", company="C", raw_text="r")
    q.update_job_pipeline(conn, lead, simplified_content="", content_type="lead")
    q.upsert_job_score(conn, lead, scenario_id, 0.3, "", "hash1")

    failed_only = q.get_jobs(conn, content_type="job_posting", gate_status="failed")

    assert failed_only == []


def test_get_jobs_job_with_no_score_has_no_passed_scenarios(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    job = q.get_jobs(conn)[0]
    assert job["passed_gate_count"] is None
    assert job["passed_scenario_names"] is None
    assert job["top_passed_scenario_id"] is None


def test_update_job_fit_sets_scores_and_computed_fit_score(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_fit(conn, jid, 0.8, "Strong domain fit", 0.6, "Slightly junior", "phash1")
    job = q.get_job(conn, jid)
    assert job["interest_score"] == pytest.approx(0.8)
    assert job["interest_reasoning"] == "Strong domain fit"
    assert job["attainability_score"] == pytest.approx(0.6)
    assert job["attainability_reasoning"] == "Slightly junior"
    assert job["fit_score"] == pytest.approx(0.7)  # average
    assert job["profile_version_hash"] == "phash1"


def test_get_jobs_orders_by_fit_score_desc(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    low = q.insert_job(conn, source_id=source_id, url="http://job/low", title="Low", company="C", raw_text="r")
    high = q.insert_job(conn, source_id=source_id, url="http://job/high", title="High", company="C", raw_text="r")
    q.update_job_fit(conn, low, 0.2, "", 0.2, "", "h")
    q.update_job_fit(conn, high, 0.9, "", 0.9, "", "h")
    jobs = q.get_jobs(conn)
    assert [j["title"] for j in jobs] == ["High", "Low"]


def test_upsert_scenario_feedback_inserts_and_updates(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    scenario_id = q.insert_scenario(conn, "A", "")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, jid, scenario_id, "too broad", "lower")
    scores = q.get_job_scores(conn, jid)
    # No job_scores row exists yet — get_job_scores only surfaces scored scenarios —
    # so verify via get_recent_feedback_notes instead, which reads scenario_feedback directly.
    assert q.get_recent_feedback_notes(conn, scenario_id) == [{"direction": "lower", "note": "too broad"}]

    q.upsert_scenario_feedback(conn, jid, scenario_id, "actually fine", "higher")
    assert q.get_recent_feedback_notes(conn, scenario_id) == [{"direction": "higher", "note": "actually fine"}]


def test_upsert_scenario_feedback_deletes_when_both_blank(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    scenario_id = q.insert_scenario(conn, "A", "")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, jid, scenario_id, "note", "higher")
    q.upsert_scenario_feedback(conn, jid, scenario_id, "", None)
    assert q.get_recent_feedback_notes(conn, scenario_id) == []


def test_upsert_scenario_feedback_keeps_row_with_only_note(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    scenario_id = q.insert_scenario(conn, "A", "")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, jid, scenario_id, "just a comment", None)
    row = conn.execute(
        "SELECT note, direction FROM scenario_feedback WHERE job_id = ? AND scenario_id = ?", (jid, scenario_id)
    ).fetchone()
    assert row["note"] == "just a comment"
    assert row["direction"] is None


def test_upsert_scenario_feedback_keeps_row_with_only_direction(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    scenario_id = q.insert_scenario(conn, "A", "")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, jid, scenario_id, "", "higher")
    row = conn.execute(
        "SELECT note, direction FROM scenario_feedback WHERE job_id = ? AND scenario_id = ?", (jid, scenario_id)
    ).fetchone()
    assert row["note"] == ""
    assert row["direction"] == "higher"


def test_upsert_scenario_feedback_blank_note_preserves_existing_note(conn):
    # The combined multi-scenario feedback form clears note textareas after
    # a save, so a later submit touching only a different scenario's fields
    # must not wipe this one's previously-saved note just because its box
    # is now visually blank.
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    scenario_id = q.insert_scenario(conn, "A", "")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, jid, scenario_id, "too junior", "lower")
    q.upsert_scenario_feedback(conn, jid, scenario_id, "", "lower")
    assert q.get_recent_feedback_notes(conn, scenario_id) == [{"direction": "lower", "note": "too junior"}]


def test_upsert_scenario_feedback_blank_note_with_new_direction_preserves_note(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    scenario_id = q.insert_scenario(conn, "A", "")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, jid, scenario_id, "too junior", "lower")
    q.upsert_scenario_feedback(conn, jid, scenario_id, "", "higher")
    assert q.get_recent_feedback_notes(conn, scenario_id) == [{"direction": "higher", "note": "too junior"}]


def test_upsert_scenario_feedback_resubmitting_unchanged_values_does_not_reset_handled_state(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    scenario_id = q.insert_scenario(conn, "A", "")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, jid, scenario_id, "too junior", "lower")
    anchor = q.get_recent_feedback_anchor(conn, scenario_id)
    q.mark_feedback_handled(conn, scenario_id, anchor)
    q.upsert_scenario_feedback(conn, jid, scenario_id, "too junior", "lower")  # identical resubmission
    assert q.get_recent_feedback_notes(conn, scenario_id) == []


def test_get_job_scores_surfaces_feedback_note_and_direction(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    scenario_id = q.insert_scenario(conn, "A", "")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.upsert_job_score(conn, jid, scenario_id, 0.5, "reasoning", "hash1")
    q.upsert_scenario_feedback(conn, jid, scenario_id, "too strict", "higher")
    scores = q.get_job_scores(conn, jid)
    assert scores[0]["feedback_note"] == "too strict"
    assert scores[0]["feedback_direction"] == "higher"


def test_get_job_scores_feedback_fields_none_when_no_feedback(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    scenario_id = q.insert_scenario(conn, "A", "")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.upsert_job_score(conn, jid, scenario_id, 0.5, "reasoning", "hash1")
    scores = q.get_job_scores(conn, jid)
    assert scores[0]["feedback_note"] is None
    assert scores[0]["feedback_direction"] is None


def test_get_recent_feedback_counts_splits_unhandled_by_direction(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    scenario_id = q.insert_scenario(conn, "A", "")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T1", company="C", raw_text="r")
    j2 = q.insert_job(conn, source_id=source_id, url="http://job/2", title="T2", company="C", raw_text="r")
    j3 = q.insert_job(conn, source_id=source_id, url="http://job/3", title="T3", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, j1, scenario_id, "a", "higher")
    q.upsert_scenario_feedback(conn, j2, scenario_id, "b", "higher")
    q.upsert_scenario_feedback(conn, j3, scenario_id, "c", "lower")

    counts = q.get_recent_feedback_counts(conn, scenario_id)

    assert counts == {"unhandled_higher": 2, "unhandled_lower": 1, "handled_higher": 0, "handled_lower": 0}


def test_get_recent_feedback_counts_splits_handled_by_direction(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    scenario_id = q.insert_scenario(conn, "A", "")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T1", company="C", raw_text="r")
    j2 = q.insert_job(conn, source_id=source_id, url="http://job/2", title="T2", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, j1, scenario_id, "a", "higher")
    q.upsert_scenario_feedback(conn, j2, scenario_id, "b", "lower")
    anchor = q.get_recent_feedback_anchor(conn, scenario_id)
    q.mark_feedback_handled(conn, scenario_id, anchor)

    counts = q.get_recent_feedback_counts(conn, scenario_id)

    assert counts == {"unhandled_higher": 0, "unhandled_lower": 0, "handled_higher": 1, "handled_lower": 1}


def test_get_recent_feedback_counts_excludes_rows_older_than_window(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    scenario_id = q.insert_scenario(conn, "A", "")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T1", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, j1, scenario_id, "a", "higher")
    conn.execute(
        "UPDATE scenario_feedback SET created_at = datetime('now', '-40 days') WHERE job_id = ? AND scenario_id = ?",
        (j1, scenario_id),
    )
    conn.commit()

    counts = q.get_recent_feedback_counts(conn, scenario_id)

    assert counts == {"unhandled_higher": 0, "unhandled_lower": 0, "handled_higher": 0, "handled_lower": 0}


def test_get_recent_feedback_counts_unhandled_matches_notes_row_count(conn):
    # The displayed unhandled count must be mechanically identical to what
    # propose_criteria actually receives.
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    scenario_id = q.insert_scenario(conn, "A", "")
    for i in range(25):
        j = q.insert_job(conn, source_id=source_id, url=f"http://job/{i}", title="T", company="C", raw_text="r")
        q.upsert_scenario_feedback(conn, j, scenario_id, f"note {i}", "higher")

    counts = q.get_recent_feedback_counts(conn, scenario_id)
    notes = q.get_recent_feedback_notes(conn, scenario_id)

    assert counts["unhandled_higher"] == len(notes) == 20  # capped


def test_get_fetch_stats_by_source_aggregates_runs(conn):
    sid = q.insert_source(conn, "s", "http://x", "generic_listing")
    run1 = q.start_fetch_run(conn, sid)
    q.complete_fetch_run(conn, run1, jobs_found=3, jobs_new=2)
    run2 = q.start_fetch_run(conn, sid)
    q.complete_fetch_run(conn, run2, jobs_found=1, jobs_new=0, error="boom")

    stats = q.get_fetch_stats_by_source(conn)

    assert stats[sid]["run_count"] == 2
    assert stats[sid]["total_new"] == 2
    assert stats[sid]["total_found"] == 4
    assert stats[sid]["last_success_at"] is not None


def test_get_fetch_stats_by_source_omits_sources_with_no_runs(conn):
    sid = q.insert_source(conn, "s", "http://x", "generic_listing")
    stats = q.get_fetch_stats_by_source(conn)
    assert sid not in stats


def test_enqueue_task_creates_queued_row(conn):
    task = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1})
    assert task["status"] == "queued"
    assert task["kind"] == "fetch_source"
    assert task["params"] == {"source_id": 1}
    assert task["result"] is None
    assert task["already_active"] is False


def test_enqueue_task_dedupes_identical_active_task():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    first = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1})
    second = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1})
    assert first["id"] == second["id"]
    assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1
    assert first["already_active"] is False
    assert second["already_active"] is True


def test_enqueue_task_does_not_dedupe_after_completion():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    first = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1})
    q.complete_task(conn, first["id"], {"html_chunks": []})
    second = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1})
    assert second["id"] != first["id"]


def test_claim_next_task_returns_oldest_queued_and_marks_running(conn):
    q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1})
    q.enqueue_task(conn, kind="fetch_source", params={"source_id": 2})
    claimed = q.claim_next_task(conn)
    assert claimed["params"] == {"source_id": 1}
    assert claimed["status"] == "running"
    assert claimed["started_at"] is not None
    assert q.claim_next_task(conn)["params"] == {"source_id": 2}
    assert q.claim_next_task(conn) is None


def test_append_task_log_accumulates_lines(conn):
    task = q.enqueue_task(conn, kind="fetch_source", params={})
    q.append_task_log(conn, task["id"], "line one")
    q.append_task_log(conn, task["id"], "line two")
    fetched = q.get_task(conn, task["id"])
    assert fetched["log"] == "line one\nline two\n"


def test_complete_task_sets_status_and_result(conn):
    task = q.enqueue_task(conn, kind="fetch_source", params={})
    q.complete_task(conn, task["id"], {"html_chunks": ["<p>ok</p>"]})
    fetched = q.get_task(conn, task["id"])
    assert fetched["status"] == "done"
    assert fetched["result"] == {"html_chunks": ["<p>ok</p>"]}
    assert fetched["finished_at"] is not None


def test_fail_task_sets_status_and_error(conn):
    task = q.enqueue_task(conn, kind="fetch_source", params={})
    q.fail_task(conn, task["id"], "boom")
    fetched = q.get_task(conn, task["id"])
    assert fetched["status"] == "failed"
    assert fetched["error"] == "boom"


def test_recover_interrupted_tasks_fails_running_rows(conn):
    task = q.enqueue_task(conn, kind="fetch_source", params={})
    q.claim_next_task(conn)
    n = q.recover_interrupted_tasks(conn)
    assert n == 1
    fetched = q.get_task(conn, task["id"])
    assert fetched["status"] == "failed"
    assert fetched["error"] == "interrupted by restart"


def test_get_active_tasks_excludes_done_and_failed(conn):
    a = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1})
    b = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 2})
    q.complete_task(conn, a["id"], {})
    active = q.get_active_tasks(conn)
    assert [t["id"] for t in active] == [b["id"]]


def test_cv_generate_task_id_matches_only_generate_tasks(conn):
    conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s','http://s','manual')")
    j7 = conn.execute("INSERT INTO jobs (source_id, url, title) VALUES (1,'http://s/7','A')").lastrowid
    j8 = conn.execute("INSERT INTO jobs (source_id, url, title) VALUES (1,'http://s/8','B')").lastrowid
    conn.commit()
    assert q.cv_generate_task_id(conn, j7) is None
    # a plan task never counts — it produces no draft
    plan = q.enqueue_task(conn, kind="cv_tailor",
                          params={"job_id": j7, "mode": "plan", "render": "plan_pane"})
    assert q.cv_generate_task_id(conn, j7) is None
    q.complete_task(conn, plan["id"], {})
    gen = q.enqueue_task(conn, kind="cv_tailor",
                         params={"job_id": j7, "mode": "generate", "render": "preview_pane"})
    assert q.cv_generate_task_id(conn, j7) == gen["id"]
    assert q.cv_generate_task_id(conn, j8) is None  # other job
    q.complete_task(conn, gen["id"], {})
    assert q.cv_generate_task_id(conn, j7) is None  # finished


def test_inbox_item_lifecycle(conn):
    item_id = q.create_inbox_item(conn, kind="browser_missing", message="hi", link="/x")
    assert q.count_unresolved_inbox_items(conn) == 1
    items = q.get_unresolved_inbox_items(conn)
    assert items[0]["message"] == "hi"
    q.resolve_inbox_item(conn, item_id)
    assert q.count_unresolved_inbox_items(conn) == 0


def test_get_fetch_run_returns_row(conn):
    sid = q.insert_source(conn, "s", "http://x", "generic_listing")
    run_id = q.start_fetch_run(conn, sid)
    q.complete_fetch_run(conn, run_id, jobs_found=1, jobs_new=1, auth_error=True)
    run = q.get_fetch_run(conn, run_id)
    assert run["auth_error"] == 1


def test_enqueue_task_stores_parent_task_id(conn):
    root = q.enqueue_task(conn, kind="fetch_all", params={})
    child = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1}, parent_task_id=root["id"])
    assert q.get_task(conn, child["id"])["parent_task_id"] == root["id"]
    assert q.get_task(conn, root["id"])["parent_task_id"] is None


def test_set_task_needs_action(conn):
    t = q.enqueue_task(conn, kind="source_detect", params={})
    q.claim_next_task(conn)
    q.set_task_needs_action(conn, t["id"])
    row = q.get_task(conn, t["id"])
    assert row["status"] == "needs_action"
    assert row["finished_at"] is not None


def test_resolve_and_dismiss_task(conn):
    a = q.enqueue_task(conn, kind="source_detect", params={})
    q.set_task_needs_action(conn, a["id"])
    q.resolve_task(conn, a["id"])
    assert q.get_task(conn, a["id"])["status"] == "done"
    b = q.enqueue_task(conn, kind="source_detect", params={"x": 1})
    q.set_task_needs_action(conn, b["id"])
    q.dismiss_task(conn, b["id"])
    assert q.get_task(conn, b["id"])["status"] == "dismissed"


def test_get_task_children_ordered(conn):
    root = q.enqueue_task(conn, kind="fetch_all", params={})
    q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1}, parent_task_id=root["id"])
    q.enqueue_task(conn, kind="fetch_source", params={"source_id": 2}, parent_task_id=root["id"])
    kids = q.get_task_children(conn, root["id"])
    assert [t["params"]["source_id"] for t in kids] == [1, 2]
    assert q.get_task_children(conn, 999) == []


def test_get_dashboard_tasks_active_always_terminal_recent_only(conn):
    active = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1})
    old = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 2})
    q.complete_task(conn, old["id"], {})
    conn.execute("UPDATE tasks SET finished_at = datetime('now', '-2 days') WHERE id = ?", (old["id"],))
    recent = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 3})
    q.complete_task(conn, recent["id"], {})
    conn.commit()
    entries = q.get_dashboard_tasks(conn)
    ids = {e["root"]["id"] for e in entries}
    assert active["id"] in ids
    assert recent["id"] in ids
    assert old["id"] not in ids


def test_get_dashboard_tasks_one_entry_per_root(conn):
    root = q.enqueue_task(conn, kind="fetch_all", params={})
    c1 = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1}, parent_task_id=root["id"])
    q.enqueue_task(conn, kind="fetch_source", params={"source_id": 2}, parent_task_id=root["id"])
    solo = q.enqueue_task(conn, kind="job_reset", params={"job_id": 5})
    entries = q.get_dashboard_tasks(conn)
    root_ids = [e["root"]["id"] for e in entries]
    # children do not surface as their own top-level entry
    assert c1["id"] not in root_ids
    assert root["id"] in root_ids and solo["id"] in root_ids
    root_entry = next(e for e in entries if e["root"]["id"] == root["id"])
    assert len(root_entry["children"]) == 2
    solo_entry = next(e for e in entries if e["root"]["id"] == solo["id"])
    assert solo_entry["children"] == []


def test_get_dashboard_tasks_includes_stale_root_with_active_child(conn):
    root = q.enqueue_task(conn, kind="fetch_all", params={})
    q.complete_task(conn, root["id"], {})
    conn.execute("UPDATE tasks SET finished_at = datetime('now', '-3 days') WHERE id = ?", (root["id"],))
    conn.commit()
    child = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1}, parent_task_id=root["id"])
    entries = q.get_dashboard_tasks(conn)
    root_entry = next(e for e in entries if e["root"]["id"] == root["id"])
    assert [c["id"] for c in root_entry["children"]] == [child["id"]]


def test_resolve_source_prompts_for_url_dismisses_needs_action_task(conn):
    t = q.enqueue_task(conn, kind="source_detect", params={"url": "https://x.com/careers"})
    q.set_task_needs_action(conn, t["id"])
    other = q.enqueue_task(conn, kind="source_detect", params={"url": "https://x.com/other"})
    q.set_task_needs_action(conn, other["id"])
    n = q.resolve_source_prompts_for_url(conn, "https://x.com/careers")
    assert n == 1
    assert q.get_task(conn, t["id"])["status"] == "dismissed"
    assert q.get_task(conn, other["id"])["status"] == "needs_action"


def test_get_recent_terminal_tasks_filter_and_order(conn):
    a = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1})
    b = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 2})
    q.complete_task(conn, a["id"], {})
    q.fail_task(conn, b["id"], "boom")
    rows = q.get_recent_terminal_tasks(conn)
    assert [r["id"] for r in rows] == [b["id"], a["id"]]
    failed = q.get_recent_terminal_tasks(conn, status="failed")
    assert [r["id"] for r in failed] == [b["id"]]


def test_get_jobs_filter_by_org(conn):
    sid = q.insert_source(conn, "s", "http://x", "generic_listing")
    a = q.insert_job(conn, source_id=sid, url="http://job/1", title="T1", company="Acme", raw_text="r")
    q.insert_job(conn, source_id=sid, url="http://job/2", title="T2", company="Globex", raw_text="r")
    result = q.get_jobs(conn, org="Acme")
    assert [j["id"] for j in result] == [a]


def test_get_jobs_scenario_gate_none_returns_scored_but_failed(conn):
    sid = q.insert_source(conn, "s", "http://x", "generic_listing")
    sc = q.insert_scenario(conn, "A", "")  # gate 0.7
    failed = q.insert_job(conn, source_id=sid, url="http://job/f", title="F", company="C", raw_text="r")
    passed = q.insert_job(conn, source_id=sid, url="http://job/p", title="P", company="C", raw_text="r")
    unscored_lead = q.insert_job(conn, source_id=sid, url="http://job/l", title="L", company="C", raw_text="r")
    q.update_job_pipeline(conn, failed, simplified_content="", content_type="job_posting")
    q.update_job_pipeline(conn, passed, simplified_content="", content_type="job_posting")
    q.update_job_pipeline(conn, unscored_lead, simplified_content="", content_type="lead")
    q.upsert_job_score(conn, failed, sc, 0.3, "", "h1")
    q.upsert_job_score(conn, passed, sc, 0.9, "", "h2")
    for j in (failed, passed, unscored_lead):
        q.mark_job_evaluation_complete(conn, j)

    result = {j["id"] for j in q.get_jobs(conn, scenario_gate="none")}
    assert result == {failed}


def test_get_jobs_org_and_scenario_id_compose(conn):
    sid = q.insert_source(conn, "s", "http://x", "generic_listing")
    sc = q.insert_scenario(conn, "A", "")
    hit = q.insert_job(conn, source_id=sid, url="http://job/1", title="T", company="Acme", raw_text="r")
    miss_org = q.insert_job(conn, source_id=sid, url="http://job/2", title="T", company="Globex", raw_text="r")
    for j in (hit, miss_org):
        q.update_job_pipeline(conn, j, simplified_content="", content_type="job_posting")
        q.upsert_job_score(conn, j, sc, 0.9, "", "h")
        q.mark_job_evaluation_complete(conn, j)
    result = {j["id"] for j in q.get_jobs(conn, scenario_id=sc, org="Acme")}
    assert result == {hit}


def test_get_job_counts_new_excludes_leads(conn):
    sid = q.insert_source(conn, "s", "http://x", "generic_listing")
    lead = q.insert_job(conn, source_id=sid, url="http://job/l", title="L", company="C", raw_text="r")
    posting = q.insert_job(conn, source_id=sid, url="http://job/p", title="P", company="C", raw_text="r")
    q.update_job_pipeline(conn, lead, simplified_content="", content_type="lead")
    q.update_job_pipeline(conn, posting, simplified_content="", content_type="job_posting")
    q.mark_job_evaluation_complete(conn, lead)
    q.mark_job_evaluation_complete(conn, posting)
    counts = q.get_job_counts(conn)
    assert counts["new"] == 1  # posting only
    assert counts["lead"] == 1


def test_get_job_counts_respects_org_filter(conn):
    sid = q.insert_source(conn, "s", "http://x", "generic_listing")
    a = q.insert_job(conn, source_id=sid, url="http://job/1", title="T", company="Acme", raw_text="r")
    g = q.insert_job(conn, source_id=sid, url="http://job/2", title="T", company="Globex", raw_text="r")
    q.update_job_feedback(conn, a, "accepted", "n")
    q.update_job_feedback(conn, g, "accepted", "n")
    counts = q.get_job_counts(conn, org="Acme")
    assert counts["accepted"] == 1


def test_get_job_counts_respects_scenario_filter(conn):
    sid = q.insert_source(conn, "s", "http://x", "generic_listing")
    sc = q.insert_scenario(conn, "A", "")
    match = q.insert_job(conn, source_id=sid, url="http://job/1", title="T", company="C", raw_text="r")
    other = q.insert_job(conn, source_id=sid, url="http://job/2", title="T", company="C", raw_text="r")
    for j, score in ((match, 0.9), (other, 0.2)):
        q.update_job_pipeline(conn, j, simplified_content="", content_type="job_posting")
        q.upsert_job_score(conn, j, sc, score, "", "h")
        q.mark_job_evaluation_complete(conn, j)
    q.update_job_feedback(conn, match, "accepted", "n")
    q.update_job_feedback(conn, other, "accepted", "n")
    counts = q.get_job_counts(conn, scenario_id=sc)
    assert counts["accepted"] == 1


def test_get_distinct_companies_sorted_no_blanks(conn):
    sid = q.insert_source(conn, "s", "http://x", "generic_listing")
    q.insert_job(conn, source_id=sid, url="http://job/1", title="T", company="Zeta", raw_text="r")
    q.insert_job(conn, source_id=sid, url="http://job/2", title="T", company="Acme", raw_text="r")
    q.insert_job(conn, source_id=sid, url="http://job/3", title="T", company="Acme", raw_text="r")
    q.insert_job(conn, source_id=sid, url="http://job/4", title="T", company="", raw_text="r")
    assert q.get_distinct_companies(conn) == ["Acme", "Zeta"]


def test_get_jobs_exposes_passed_scenario_ids(conn):
    sid = q.insert_source(conn, "s", "http://x", "generic_listing")
    a = q.insert_scenario(conn, "Alpha", "")
    b = q.insert_scenario(conn, "Beta", "")
    jid = q.insert_job(conn, source_id=sid, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="", content_type="job_posting")
    q.upsert_job_score(conn, jid, a, 0.9, "", "h")
    q.upsert_job_score(conn, jid, b, 0.8, "", "h")
    q.mark_job_evaluation_complete(conn, jid)
    job = q.get_job(conn, jid)
    names = job["passed_scenario_names"].split(", ")
    ids = [int(x) for x in job["passed_scenario_ids"].split(",")]
    assert dict(zip(names, ids)) == {"Alpha": a, "Beta": b}


def test_update_job_feedback_stamps_status_changed_at(conn):
    sid = q.get_or_create_manual_source(conn)
    jid = q.insert_job(conn, source_id=sid, url="https://x.test/sca", title="A", company="", raw_text="")
    assert conn.execute("SELECT status_changed_at FROM jobs WHERE id=?", (jid,)).fetchone()[0] is None
    q.update_job_feedback(conn, jid, "accepted", "yep")
    assert conn.execute("SELECT status_changed_at FROM jobs WHERE id=?", (jid,)).fetchone()[0] is not None


def test_mark_job_gate_override_stamps_status_changed_at(conn):
    sid = q.get_or_create_manual_source(conn)
    jid = q.insert_job(conn, source_id=sid, url="https://x.test/scb", title="B", company="", raw_text="")
    q.mark_job_gate_override(conn, jid)
    assert conn.execute("SELECT status_changed_at FROM jobs WHERE id=?", (jid,)).fetchone()[0] is not None


def _mk_job(conn, url, *, created, published=None, evaluated=None, changed=None, fit=None):
    sid = q.get_or_create_manual_source(conn)
    jid = q.insert_job(conn, source_id=sid, url=url, title=url, company="", raw_text="")
    conn.execute(
        "UPDATE jobs SET created_at=?, published_at=?, evaluation_completed_at=?, "
        "status_changed_at=?, fit_score=?, content_type='job_posting' WHERE id=?",
        (created, published, evaluated, changed, fit, jid),
    )
    conn.commit()
    return jid


def test_get_jobs_order_change_uses_latest_activity(conn):
    a = _mk_job(conn, "https://x.test/a", created="2024-01-01T00:00:00", changed="2024-06-01T00:00:00")
    b = _mk_job(conn, "https://x.test/b", created="2024-05-01T00:00:00")
    ids = [j["id"] for j in q.get_jobs(conn, status="new", order="change")]
    assert ids.index(a) < ids.index(b)


def test_get_jobs_order_age_uses_published_then_created(conn):
    a = _mk_job(conn, "https://x.test/a", created="2024-09-01T00:00:00", published="2024-01-01T00:00:00")
    b = _mk_job(conn, "https://x.test/b", created="2024-02-01T00:00:00")
    ids = [j["id"] for j in q.get_jobs(conn, status="new", order="age")]
    assert ids.index(b) < ids.index(a)


def test_get_jobs_order_score_matches_legacy(conn):
    lo = _mk_job(conn, "https://x.test/lo", created="2024-01-01T00:00:00", fit=0.2)
    hi = _mk_job(conn, "https://x.test/hi", created="2024-01-01T00:00:00", fit=0.9)
    ids = [j["id"] for j in q.get_jobs(conn, status="new", order="score")]
    assert ids.index(hi) < ids.index(lo)


def test_start_fetch_run_records_task_id(conn):
    sid = q.get_or_create_manual_source(conn)
    task = q.enqueue_task(conn, kind="fetch_source", params={"source_id": sid})
    run_id = q.start_fetch_run(conn, sid, task_id=task["id"])
    assert conn.execute("SELECT task_id FROM fetch_runs WHERE id=?", (run_id,)).fetchone()[0] == task["id"]


def test_start_fetch_run_task_id_optional(conn):
    sid = q.get_or_create_manual_source(conn)
    run_id = q.start_fetch_run(conn, sid)
    assert conn.execute("SELECT task_id FROM fetch_runs WHERE id=?", (run_id,)).fetchone()[0] is None


def test_get_jobs_org_none_matches_blank_company(conn):
    sid = q.get_or_create_manual_source(conn)
    blank = q.insert_job(conn, source_id=sid, url="https://x.test/blank", title="B", company="", raw_text="")
    named = q.insert_job(conn, source_id=sid, url="https://x.test/named", title="N", company="Acme", raw_text="")
    conn.execute("UPDATE jobs SET content_type='job_posting', gate_override=1, "
                 "evaluation_completed_at=datetime('now') WHERE id IN (?,?)", (blank, named))
    conn.commit()
    ids = {j["id"] for j in q.get_jobs(conn, status="new", org_none=True)}
    assert ids == {blank}
    counts = q.get_job_counts(conn, org_none=True)
    assert counts["new"] == 1


def test_delete_scenario_cascades(conn):
    sc = q.insert_scenario(conn, "S", "d")
    q.insert_criterion(conn, sc, "must have X", "must")
    sid = q.get_or_create_manual_source(conn)
    jid = q.insert_job(conn, source_id=sid, url="https://x.test/j", title="J", company="", raw_text="")
    conn.execute(
        "INSERT INTO job_scores (job_id, scenario_id, relevance_score, scenario_version_hash) "
        "VALUES (?,?,?,?)", (jid, sc, 0.8, "h"))
    conn.commit()
    assert q.count_job_scores_for_scenario(conn, sc) == 1
    q.delete_scenario(conn, sc)
    assert q.get_scenario(conn, sc) is None
    assert conn.execute("SELECT COUNT(*) FROM criteria WHERE scenario_id=?", (sc,)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM job_scores WHERE scenario_id=?", (sc,)).fetchone()[0] == 0


def _seed_job(conn, sid, url, title, *, company="", summary="", body="", headline=""):
    jid = q.insert_job(conn, source_id=sid, url=url, title=title, company=company, raw_text="r")
    q.update_job_pipeline(
        conn, jid, simplified_content=body, content_type="job_posting",
        title=title, summary=summary, headline=headline,
    )
    return jid


def test_search_jobs_ranks_title_above_body(conn):
    sid = q.insert_source(conn, "s", "http://s", "generic_listing")
    body_hit = _seed_job(conn, sid, "http://s/1", "Office Manager",
                         body="we use python and rust daily")
    title_hit = _seed_job(conn, sid, "http://s/2", "Python Engineer", body="unrelated")
    results = q.search_jobs(conn, "python")
    assert [r["id"] for r in results] == [title_hit, body_hit]


def test_search_jobs_prefix_matches_last_token(conn):
    sid = q.insert_source(conn, "s", "http://s", "generic_listing")
    jid = _seed_job(conn, sid, "http://s/1", "Kubernetes Platform Lead")
    assert [r["id"] for r in q.search_jobs(conn, "kube")] == [jid]


def test_fts_match_query_keeps_unicode_words_whole():
    assert q._fts_match_query("Dataplattformingeniør") == '"Dataplattformingeniør"*'
    assert q._fts_match_query("Berlin Büro") == '"Berlin" "Büro"*'


def test_search_jobs_finds_accented_words(conn):
    sid = q.insert_source(conn, "s", "http://s", "generic_listing")
    hit = _seed_job(conn, sid, "http://s/1", "Dataplattformingeniør – Oslo")
    _seed_job(conn, sid, "http://s/2", "Frontend Developer")
    assert [r["id"] for r in q.search_jobs(conn, "dataplattformingeniør")] == [hit]
    assert [r["id"] for r in q.search_jobs(conn, "Büro")] == []  # sanity: no false match


def test_search_jobs_multi_term_is_and(conn):
    sid = q.insert_source(conn, "s", "http://s", "generic_listing")
    _seed_job(conn, sid, "http://s/1", "Senior Java Developer")
    want = _seed_job(conn, sid, "http://s/2", "Senior Python Developer")
    assert [r["id"] for r in q.search_jobs(conn, "senior python")] == [want]


def test_search_jobs_narrows_by_source_and_org(conn):
    s1 = q.insert_source(conn, "s1", "http://s1", "generic_listing")
    s2 = q.insert_source(conn, "s2", "http://s2", "generic_listing")
    a = _seed_job(conn, s1, "http://s1/1", "Rust Engineer", company="Acme")
    b = _seed_job(conn, s2, "http://s2/1", "Rust Engineer", company="Beta")
    assert [r["id"] for r in q.search_jobs(conn, "rust", source_id=s1)] == [a]
    assert [r["id"] for r in q.search_jobs(conn, "rust", org="Beta")] == [b]


def test_search_jobs_narrows_by_scenario_gate(conn):
    sid = q.insert_source(conn, "s", "http://s", "generic_listing")
    passed = _seed_job(conn, sid, "http://s/1", "Rust Engineer")
    _seed_job(conn, sid, "http://s/2", "Rust Developer")
    scn = q.insert_scenario(conn, "Backend", "")
    q.upsert_job_score(conn, passed, scn, 0.95, "m", "h")
    assert [r["id"] for r in q.search_jobs(conn, "rust", scenario_id=scn)] == [passed]


def test_search_jobs_spans_statuses(conn):
    sid = q.insert_source(conn, "s", "http://s", "generic_listing")
    a = _seed_job(conn, sid, "http://s/1", "Rust Engineer")
    b = _seed_job(conn, sid, "http://s/2", "Rust Developer")
    q.update_job_feedback(conn, b, "rejected", "no")
    ids = {r["id"] for r in q.search_jobs(conn, "rust")}
    assert ids == {a, b}


def test_search_jobs_empty_query_returns_empty(conn):
    sid = q.insert_source(conn, "s", "http://s", "generic_listing")
    _seed_job(conn, sid, "http://s/1", "Rust Engineer")
    assert q.search_jobs(conn, "   ") == []
    assert q.search_jobs(conn, "!!! ??") == []


def _tab_job(conn, sid, url, *, title="T", status="new", content_type="job_posting",
             scenario_id=None, score=0.9):
    jid = q.insert_job(conn, source_id=sid, url=url, title=title, company="", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="c", content_type=content_type,
                          title=title, summary="")
    if scenario_id is not None:
        q.upsert_job_score(conn, jid, scenario_id, score, "m", "h")
    q.mark_job_evaluation_complete(conn, jid)
    if status != "new":
        q.update_job_feedback(conn, jid, status, "n")
    return jid


def test_get_jobs_for_tabs_single_matches_get_jobs_accepted(conn):
    sid = q.insert_source(conn, "s", "http://s", "generic_listing")
    a = _tab_job(conn, sid, "http://s/1", status="accepted")
    _tab_job(conn, sid, "http://s/2", status="new")
    assert [j["id"] for j in q.get_jobs_for_tabs(conn, ["accepted"])] == [a]


def test_get_jobs_for_tabs_unions_buckets(conn):
    sid = q.insert_source(conn, "s", "http://s", "generic_listing")
    scn = q.insert_scenario(conn, "S", "")
    new_job = _tab_job(conn, sid, "http://s/1", scenario_id=scn, score=0.95)  # passes gate -> New
    acc = _tab_job(conn, sid, "http://s/2", status="accepted")
    _tab_job(conn, sid, "http://s/3", status="rejected")
    ids = {j["id"] for j in q.get_jobs_for_tabs(conn, ["new", "accepted"])}
    assert ids == {new_job, acc}


def test_get_jobs_for_tabs_splits_new_and_not_relevant_on_gate(conn):
    sid = q.insert_source(conn, "s", "http://s", "generic_listing")
    scn = q.insert_scenario(conn, "S", "")
    passed = _tab_job(conn, sid, "http://s/1", scenario_id=scn, score=0.95)
    failed = _tab_job(conn, sid, "http://s/2", scenario_id=scn, score=0.05)
    assert [j["id"] for j in q.get_jobs_for_tabs(conn, ["new"])] == [passed]
    assert [j["id"] for j in q.get_jobs_for_tabs(conn, ["not_relevant"])] == [failed]


def test_get_jobs_for_tabs_applies_org_filter(conn):
    sid = q.insert_source(conn, "s", "http://s", "generic_listing")
    a = q.insert_job(conn, source_id=sid, url="http://s/1", title="T", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, a, simplified_content="c", content_type="job_posting", title="T", summary="")
    q.mark_job_evaluation_complete(conn, a)
    b = q.insert_job(conn, source_id=sid, url="http://s/2", title="T", company="Beta", raw_text="r")
    q.update_job_pipeline(conn, b, simplified_content="c", content_type="job_posting", title="T", summary="")
    q.mark_job_evaluation_complete(conn, b)
    assert [j["id"] for j in q.get_jobs_for_tabs(conn, ["new", "not_relevant"], org="Beta")] == [b]


def test_search_jobs_tabs_excludes_trash_unless_asked(conn):
    sid = q.insert_source(conn, "s", "http://s", "generic_listing")
    keep = _tab_job(conn, sid, "http://s/1", title="Rust Engineer")
    trash = _tab_job(conn, sid, "http://s/2", title="Rust Engineer", status="trash")
    got = {j["id"] for j in q.search_jobs(conn, "rust", tabs=["new", "accepted", "rejected"])}
    assert got == {keep}
    got2 = {j["id"] for j in q.search_jobs(conn, "rust", tabs=["new", "trash"])}
    assert trash in got2


def test_get_job_exposes_source_fetcher_type(conn):
    sid = q.insert_source(conn, "board", "http://example.com", "generic_listing")
    jid = q.insert_job(conn, source_id=sid, url="http://example.com/a", title="A",
                       company="", raw_text="x")
    assert q.get_job(conn, jid)["source_fetcher_type"] == "generic_listing"


def test_update_job_raw_text_replaces_only_raw_text(conn):
    sid = q.insert_source(conn, "board", "http://example.com", "generic_listing")
    jid = q.insert_job(conn, source_id=sid, url="http://example.com/a", title="A",
                       company="Acme", raw_text="old")
    q.update_job_raw_text(conn, jid, "new body")
    row = q.get_job(conn, jid)
    assert row["raw_text"] == "new body"
    assert row["title"] == "A" and row["company"] == "Acme"


def _revisit_job(conn, source_id, url, *, content_type="job_posting", evaluated=True,
                 status="new", gate_override=0):
    jid = q.insert_job(conn, source_id=source_id, url=url, title="", company="", raw_text="x")
    conn.execute(
        "UPDATE jobs SET content_type=?, evaluation_completed_at=?, gate_override=? WHERE id=?",
        (content_type, "2024-01-01T00:00:00" if evaluated else None, gate_override, jid),
    )
    conn.commit()
    if status != "new":
        q.update_job_feedback(conn, jid, status, None)
    return jid


def test_get_revisitable_jobs_scope(conn):
    gid = q.insert_source(conn, "board", "http://example.com", "generic_listing")
    slk = q.insert_source(conn, "slk", "http://x.slack.com/c", "slack")

    gate_passed = _revisit_job(conn, gid, "http://example.com/passed")
    lead = _revisit_job(conn, gid, "http://example.com/lead", content_type="lead")
    accepted = _revisit_job(conn, gid, "http://example.com/acc", status="accepted")

    # Excluded: gate-failed "Not relevant", error rows, rejected, trash, slack, unevaluated.
    _revisit_job(conn, gid, "http://example.com/notrel", evaluated=False)
    _revisit_job(conn, gid, "http://example.com/err", content_type="error")
    _revisit_job(conn, gid, "http://example.com/rej", status="rejected")
    _revisit_job(conn, gid, "http://example.com/trash", status="trash")
    _revisit_job(conn, slk, "http://x.slack.com/c#1")

    ids = {j["id"] for j in q.get_revisitable_jobs(conn)}
    assert ids == {gate_passed, lead, accepted}


def test_get_revisitable_jobs_orders_by_stalest_fetch_first(conn):
    gid = q.insert_source(conn, "board", "http://example.com", "generic_listing")
    a = _revisit_job(conn, gid, "http://example.com/a")
    b = _revisit_job(conn, gid, "http://example.com/b")
    conn.execute("UPDATE jobs SET fetched_at='2024-06-01T00:00:00' WHERE id=?", (a,))
    conn.execute("UPDATE jobs SET fetched_at='2024-01-01T00:00:00' WHERE id=?", (b,))
    conn.commit()
    assert [j["id"] for j in q.get_revisitable_jobs(conn)] == [b, a]


def test_mark_job_revisited_bumps_only_fetched_at(conn):
    gid = q.insert_source(conn, "board", "http://example.com", "generic_listing")
    jid = _revisit_job(conn, gid, "http://example.com/a")
    conn.execute(
        "UPDATE jobs SET created_at='2024-01-01T00:00:00', fetched_at='2024-01-01T00:00:00', "
        "status_changed_at='2024-01-01T00:00:00' WHERE id=?", (jid,),
    )
    conn.commit()
    q.mark_job_revisited(conn, jid)
    row = q.get_job(conn, jid)
    assert row["created_at"] == "2024-01-01T00:00:00"
    assert row["status_changed_at"] == "2024-01-01T00:00:00"
    assert row["fetched_at"] > "2024-01-01T00:00:00"


def test_mark_job_closed_trashes_and_logs_event(conn):
    sid = q.insert_source(conn, "board", "http://example.com", "generic_listing")
    jid = q.insert_job(conn, source_id=sid, url="http://example.com/a", title="A",
                       company="", raw_text="x")
    q.update_job_feedback(conn, jid, "accepted", "Loved the mission")
    q.mark_job_closed(conn, jid, "this job can no longer be found")
    row = q.get_job(conn, jid)
    assert row["status"] == "trash"
    assert row["status_changed_at"] is not None
    # user's own note is left untouched
    assert row["feedback_note"] == "Loved the mission"
    messages = [e["message"] for e in q.get_job_events(conn, jid)]
    assert "Moved to Trash on revisit — this job can no longer be found" in messages


def test_mark_job_closed_with_no_existing_note(conn):
    sid = q.insert_source(conn, "board", "http://example.com", "generic_listing")
    jid = q.insert_job(conn, source_id=sid, url="http://example.com/a", title="A",
                       company="", raw_text="x")
    q.mark_job_closed(conn, jid, "this page is no longer a job posting")
    assert q.get_job(conn, jid)["feedback_note"] is None
    assert q.get_job_events(conn, jid)[0]["message"] == (
        "Moved to Trash on revisit — this page is no longer a job posting"
    )


# --- job_events / changelog ---

def test_add_and_get_job_events_newest_first(conn):
    sid = q.get_or_create_manual_source(conn)
    jid = q.insert_job(conn, source_id=sid, url="https://x.test/ev", title="A", company="", raw_text="")
    q.add_job_event(conn, jid, "status", "first")
    conn.execute("UPDATE job_events SET created_at = '2020-01-01T00:00:00' WHERE message = 'first'")
    q.add_job_event(conn, jid, "score", "second")
    conn.commit()
    events = q.get_job_events(conn, jid)
    assert [e["message"] for e in events] == ["second", "first"]
    assert events[0]["kind"] == "score"


def test_get_job_events_empty_for_new_job(conn):
    sid = q.get_or_create_manual_source(conn)
    jid = q.insert_job(conn, source_id=sid, url="https://x.test/ev2", title="A", company="", raw_text="")
    assert q.get_job_events(conn, jid) == []


def test_update_job_feedback_logs_status_change_with_note(conn):
    sid = q.get_or_create_manual_source(conn)
    jid = q.insert_job(conn, source_id=sid, url="https://x.test/fb1", title="A", company="", raw_text="")
    q.update_job_feedback(conn, jid, "rejected", "role moved to London")
    events = q.get_job_events(conn, jid)
    assert len(events) == 1
    assert events[0]["kind"] == "status"
    assert events[0]["message"] == "Status: new → rejected"


def test_status_change_event_omits_note_text(conn):
    sid = q.insert_source(conn, "s", "http://e", "manual")
    jid = q.insert_job(conn, source_id=sid, url="http://e/1", title="T", company="Acme", raw_text="x")
    q.update_job_feedback(conn, jid, "rejected", "salary too low, wrong stack")
    msgs = [e["message"] for e in q.get_job_events(conn, jid)]
    assert "Status: new → rejected" in msgs
    assert all("salary too low" not in m for m in msgs)


def test_update_job_feedback_no_event_when_status_unchanged(conn):
    sid = q.get_or_create_manual_source(conn)
    jid = q.insert_job(conn, source_id=sid, url="https://x.test/fb2", title="A", company="", raw_text="")
    q.update_job_feedback(conn, jid, "new", "just a note")
    assert q.get_job_events(conn, jid) == []


def test_update_job_feedback_record_event_false_suppresses(conn):
    sid = q.get_or_create_manual_source(conn)
    jid = q.insert_job(conn, source_id=sid, url="https://x.test/fb3", title="A", company="", raw_text="")
    q.update_job_feedback(conn, jid, "trash", "err", record_event=False)
    assert q.get_job_events(conn, jid) == []


def test_update_job_feedback_logs_without_note(conn):
    sid = q.get_or_create_manual_source(conn)
    jid = q.insert_job(conn, source_id=sid, url="https://x.test/fb4", title="A", company="", raw_text="")
    q.update_job_feedback(conn, jid, "accepted", None)
    assert q.get_job_events(conn, jid)[0]["message"] == "Status: new → accepted"


def test_set_job_note_persists_without_status_change(conn):
    sid = q.insert_source(conn, "s", "http://e", "manual")
    jid = q.insert_job(conn, source_id=sid, url="http://e/1", title="T", company="Acme", raw_text="x")
    q.update_job_feedback(conn, jid, "accepted", "")
    q.set_job_note(conn, jid, "  keep an eye on comp band  ")
    job = q.get_job(conn, jid)
    assert job["feedback_note"] == "keep an eye on comp band"
    assert job["status"] == "accepted"  # unchanged
    assert job["feedback_handled_at"] is None
    events = q.get_job_events(conn, jid)
    assert all("keep an eye" not in e["message"] for e in events)


def test_set_job_note_clears_when_blank(conn):
    sid = q.insert_source(conn, "s", "http://e", "manual")
    jid = q.insert_job(conn, source_id=sid, url="http://e/1", title="T", company="Acme", raw_text="x")
    q.set_job_note(conn, jid, "something")
    q.set_job_note(conn, jid, "   ")
    assert q.get_job(conn, jid)["feedback_note"] is None


def test_mark_job_gate_override_logs_event(conn):
    sid = q.get_or_create_manual_source(conn)
    jid = q.insert_job(conn, source_id=sid, url="https://x.test/go", title="A", company="", raw_text="")
    q.mark_job_gate_override(conn, jid)
    assert q.get_job_events(conn, jid)[0]["message"] == "Filed as New — gate threshold bypassed"


def test_reset_job_logs_status_change_when_not_new(conn):
    sid = q.get_or_create_manual_source(conn)
    jid = q.insert_job(conn, source_id=sid, url="https://x.test/rs", title="A", company="", raw_text="")
    q.update_job_feedback(conn, jid, "rejected", None)
    q.reset_job(conn, jid)
    messages = [e["message"] for e in q.get_job_events(conn, jid)]
    assert "Status: rejected → new" in messages


def test_reset_job_no_status_event_when_already_new(conn):
    sid = q.get_or_create_manual_source(conn)
    jid = q.insert_job(conn, source_id=sid, url="https://x.test/rs2", title="A", company="", raw_text="")
    q.reset_job(conn, jid)
    assert [e for e in q.get_job_events(conn, jid) if e["kind"] == "status"] == []


def _score_job(conn, threshold=0.7):
    sid = q.get_or_create_manual_source(conn)
    scn = q.insert_scenario(conn, "AI Safety", "")
    conn.execute("UPDATE scenarios SET gate_threshold = ? WHERE id = ?", (threshold, scn))
    jid = q.insert_job(conn, source_id=sid, url=f"https://x.test/s{scn}", title="A", company="", raw_text="")
    conn.commit()
    return jid, scn


def test_upsert_job_score_no_event_on_first_score(conn):
    jid, scn = _score_job(conn)
    q.upsert_job_score(conn, jid, scn, 0.4, "r", "h")
    assert q.get_job_events(conn, jid) == []


def test_upsert_job_score_logs_meaningful_move(conn):
    jid, scn = _score_job(conn)
    q.upsert_job_score(conn, jid, scn, 0.40, "r", "h")
    q.upsert_job_score(conn, jid, scn, 0.55, "r", "h")
    msgs = [e["message"] for e in q.get_job_events(conn, jid)]
    assert msgs == ['Re-scored "AI Safety" 0.40 → 0.55']


def test_upsert_job_score_ignores_tiny_move(conn):
    jid, scn = _score_job(conn)
    q.upsert_job_score(conn, jid, scn, 0.40, "r", "h")
    q.upsert_job_score(conn, jid, scn, 0.43, "r", "h")
    assert q.get_job_events(conn, jid) == []


def test_upsert_job_score_logs_gate_crossing_even_if_tiny(conn):
    jid, scn = _score_job(conn, threshold=0.7)
    q.upsert_job_score(conn, jid, scn, 0.69, "r", "h")
    q.upsert_job_score(conn, jid, scn, 0.71, "r", "h")
    msgs = [e["message"] for e in q.get_job_events(conn, jid)]
    assert msgs == ['Re-scored "AI Safety" 0.69 → 0.71 — now passes gate']


def test_upsert_job_score_logs_gate_drop(conn):
    jid, scn = _score_job(conn, threshold=0.7)
    q.upsert_job_score(conn, jid, scn, 0.72, "r", "h")
    q.upsert_job_score(conn, jid, scn, 0.68, "r", "h")
    msgs = [e["message"] for e in q.get_job_events(conn, jid)]
    assert msgs == ['Re-scored "AI Safety" 0.72 → 0.68 — no longer passes gate']


def test_update_job_fit_no_event_first_time(conn):
    jid, scn = _score_job(conn)
    q.update_job_fit(conn, jid, 0.6, "i", 0.6, "a", "p")
    assert q.get_job_events(conn, jid) == []


def test_update_job_fit_logs_meaningful_move(conn):
    jid, scn = _score_job(conn)
    q.update_job_fit(conn, jid, 0.6, "i", 0.6, "a", "p")   # fit_score = 0.60
    q.update_job_fit(conn, jid, 0.8, "i", 0.8, "a", "p")   # fit_score = 0.80
    msgs = [e["message"] for e in q.get_job_events(conn, jid)]
    assert msgs == ["Fit re-assessed 0.60 → 0.80"]


# --- CV ---

def test_get_cv_settings_autocreates_and_defaults(conn):
    from app.cv.instruction import DEFAULT_BASE_CV

    s = q.get_cv_settings(conn)
    assert s["base_cv"] == DEFAULT_BASE_CV
    assert s["default_scope"] == ["select", "reorder"]
    # idempotent
    assert q.get_cv_settings(conn)["base_cv"] == DEFAULT_BASE_CV


def test_save_and_reload_cv_settings(conn):
    q.save_cv_settings(
        conn, base_cv="# Me", base_instruction="British English",
        base_guardrails="No invented dates", css="p{color:red}",
        default_scope=["select", "reorder", "rephrase"],
    )
    s = q.get_cv_settings(conn)
    assert s["base_cv"] == "# Me"
    assert s["default_scope"] == ["select", "reorder", "rephrase"]
    assert s["css"] == "p{color:red}"


def test_save_cv_settings_round_trips_directives_template(conn):
    q.save_cv_settings(conn, base_cv="", base_instruction="", base_guardrails="",
                       css="", default_scope=[1], directives_template="## Foo\n## Bar")
    assert q.get_cv_settings(conn)["directives_template"] == "## Foo\n## Bar"


def _seed_job_for_cv(conn):
    conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s','http://x','manual')")
    conn.execute("INSERT INTO jobs (source_id, url, title) VALUES (1,'http://x/1','Role')")
    conn.commit()
    return 1


def test_job_cv_upsert_roundtrip_json(conn):
    jid = _seed_job_for_cv(conn)
    assert q.get_job_cv(conn, jid) is None
    q.upsert_job_cv(conn, jid, scope=["select"], plan=[{"category": "trim", "line": "x", "rationale": "y"}],
                    tailored_cv="# Draft", base_hash="abc")
    row = q.get_job_cv(conn, jid)
    assert row["scope"] == ["select"]
    assert row["plan"][0]["line"] == "x"
    assert row["tailored_cv"] == "# Draft"
    assert row["base_hash"] == "abc"


def test_set_directives_stamps_edited_at(conn):
    jid = _seed_job_for_cv(conn)
    q.upsert_job_cv(conn, jid, scope=["select"])
    q.set_job_cv_directives(conn, jid, "- foreground X, keep Y")
    row = q.get_job_cv(conn, jid)
    assert row["tuning_directives"] == "- foreground X, keep Y"
    assert row["directives_edited_at"] is not None


def test_finalize_and_unfinalize(conn):
    jid = _seed_job_for_cv(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="x")
    q.finalize_job_cv(conn, jid)
    assert q.get_job_cv(conn, jid)["finalized_at"] is not None
    q.unfinalize_job_cv(conn, jid)
    assert q.get_job_cv(conn, jid)["finalized_at"] is None


def test_set_job_cv_scope_persists_and_stamps(conn):
    jid = _seed_job_for_cv(conn)
    q.set_job_cv_scope(conn, jid, [2, 3])
    row = q.get_job_cv(conn, jid)
    assert row["scope"] == [2, 3]
    assert row["scope_edited_at"] is not None


def test_cv_plan_task_id_only_matches_plan_mode(conn):
    jid = _seed_job_for_cv(conn)
    q.enqueue_task(conn, kind="cv_tailor", params={"job_id": jid, "mode": "generate"})
    assert q.cv_plan_task_id(conn, jid) is None
    t = q.enqueue_task(conn, kind="cv_tailor", params={"job_id": jid, "mode": "plan"})
    assert q.cv_plan_task_id(conn, jid) == t["id"]
