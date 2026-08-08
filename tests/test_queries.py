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
    q.update_source(
        conn, sid, name="Finn AI", url="https://finn.no/new", fetcher_type="playwright", enabled=False
    )
    source = q.get_source(conn, sid)
    assert source["url"] == "https://finn.no/new"
    assert source["fetcher_type"] == "playwright"
    assert source["enabled"] == 0
    assert source["name"] == "Finn AI"


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
    source_id = q.insert_source(conn, "s", "http://x", "http")
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
    source_id = q.insert_source(conn, "s", "http://x", "http")
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
    assert job["fit_score"] is None


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


def test_reset_job_clears_pipeline_output_and_scores(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
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
    assert q.get_job_scores(conn, jid) == []
    assert q.get_recent_feedback_notes(conn, scenario_id) == []


def test_reset_job_clears_gate_override(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.mark_job_gate_override(conn, jid)

    q.reset_job(conn, jid)

    job = q.get_job(conn, jid)
    assert job["gate_override"] == 0


def test_mark_job_gate_override_sets_flag(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")

    q.mark_job_gate_override(conn, jid)

    job = q.get_job(conn, jid)
    assert job["gate_override"] == 1


def test_get_job_exposes_top_passed_scenario_id(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
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
    source_id = q.insert_source(conn, "s", "http://x", "http")
    q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    assert q.get_jobs(conn) is not None


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
    assert counts == {"new": 1, "accepted": 1, "rejected": 1, "invalid": 0, "lead": 1, "not_relevant": 0}


def test_get_job_counts_splits_new_from_not_relevant(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")  # default gate_threshold 0.7
    passed = q.insert_job(conn, source_id=source_id, url="http://job/passed", title="Passed", company="C", raw_text="r")
    failed = q.insert_job(conn, source_id=source_id, url="http://job/failed", title="Failed", company="C", raw_text="r")
    q.update_job_pipeline(conn, passed, simplified_content="", content_type="job_posting")
    q.update_job_pipeline(conn, failed, simplified_content="", content_type="job_posting")
    q.upsert_job_score(conn, passed, scenario_id, 0.9, "", "hash1")
    q.upsert_job_score(conn, failed, scenario_id, 0.3, "", "hash2")

    counts = q.get_job_counts(conn)

    assert counts["new"] == 1
    assert counts["not_relevant"] == 1
    assert counts["new"] + counts["not_relevant"] == 2  # raw status='new' total


def test_get_job_counts_excludes_overridden_job_from_not_relevant(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")  # default gate_threshold 0.7
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="Overridden", company="C", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="", content_type="job_posting")
    q.upsert_job_score(conn, jid, scenario_id, 0.3, "", "hash1")
    q.mark_job_gate_override(conn, jid)

    counts = q.get_job_counts(conn)

    assert counts["not_relevant"] == 0


def test_get_job_counts_new_excludes_irrelevant_and_error(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    posting = q.insert_job(conn, source_id=source_id, url="http://job/post", title="Post", company="C", raw_text="r")
    irrelevant = q.insert_job(conn, source_id=source_id, url="http://job/irr", title="Irr", company="C", raw_text="r")
    error = q.insert_job(conn, source_id=source_id, url="http://job/err", title="Err", company="C", raw_text="r")
    q.update_job_pipeline(conn, posting, simplified_content="", content_type="job_posting")
    q.update_job_pipeline(conn, irrelevant, simplified_content="", content_type="irrelevant")
    q.update_job_pipeline(conn, error, simplified_content="", content_type="error")

    counts = q.get_job_counts(conn)

    assert counts["new"] == 1


def test_get_recent_feedback_notes(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, j1, scenario_id, "too junior", "lower")
    notes = q.get_recent_feedback_notes(conn, scenario_id)
    assert {"direction": "lower", "note": "too junior"} in notes


def test_get_recent_feedback_notes_reports_direction(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, j1, scenario_id, "should have counted", "higher")
    notes = q.get_recent_feedback_notes(conn, scenario_id)
    assert notes == [{"direction": "higher", "note": "should have counted"}]


def test_get_recent_feedback_notes_scoped_to_scenario(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_a = q.insert_scenario(conn, "A", "")
    scenario_b = q.insert_scenario(conn, "B", "")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, j1, scenario_a, "too junior", "lower")
    assert q.get_recent_feedback_notes(conn, scenario_a) == [{"direction": "lower", "note": "too junior"}]
    assert q.get_recent_feedback_notes(conn, scenario_b) == []


def test_get_recent_feedback_notes_excludes_undirected_comments(conn):
    # A note left with no direction chosen is pure commentary — propose_criteria
    # has no polarity to act on, so it must not see it.
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, j1, scenario_id, "just a thought", None)
    assert q.get_recent_feedback_notes(conn, scenario_id) == []


def test_get_recent_feedback_notes_excludes_feedback_older_than_window(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
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
    source_id = q.insert_source(conn, "s", "http://x", "http")
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
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")
    for i in range(3):
        j = q.insert_job(conn, source_id=source_id, url=f"http://job/{i}", title="T", company="C", raw_text="r")
        q.upsert_scenario_feedback(conn, j, scenario_id, f"note {i}", "lower")
    notes = q.get_recent_feedback_notes(conn, scenario_id, limit=2)
    assert len(notes) == 2


def test_get_recent_feedback_notes_excludes_handled(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T1", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, j1, scenario_id, "too junior", "lower")
    anchor = q.get_recent_feedback_anchor(conn, scenario_id)
    q.mark_feedback_handled(conn, scenario_id, anchor)
    assert q.get_recent_feedback_notes(conn, scenario_id) == []


def test_get_recent_feedback_anchor_is_newest_created_at(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
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
    source_id = q.insert_source(conn, "s", "http://x", "http")
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
    source_id = q.insert_source(conn, "s", "http://x", "http")
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
    source_id = q.insert_source(conn, "s", "http://x", "http")
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
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T1", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, j1, scenario_id, "too junior", "lower")
    anchor = q.get_recent_feedback_anchor(conn, scenario_id)
    q.mark_feedback_handled(conn, scenario_id, anchor)
    q.upsert_scenario_feedback(conn, j1, scenario_id, "actually, too senior", "lower")
    assert q.get_recent_feedback_notes(conn, scenario_id) == [{"direction": "lower", "note": "actually, too senior"}]


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


def test_get_jobs_reports_all_passed_scenario_names(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
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
    source_id = q.insert_source(conn, "s", "http://x", "http")
    strict = q.insert_scenario(conn, "Strict", "")
    q.update_scenario(conn, strict, name="Strict", description="", gate_threshold=0.9)
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.upsert_job_score(conn, jid, strict, 0.8, "close but no", "hash1")  # below 0.9
    job = q.get_jobs(conn)[0]
    assert job["passed_gate_count"] is None


def test_get_jobs_gate_status_passed_excludes_below_threshold(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")  # default gate_threshold 0.7
    below = q.insert_job(conn, source_id=source_id, url="http://job/below", title="Below", company="C", raw_text="r")
    above = q.insert_job(conn, source_id=source_id, url="http://job/above", title="Above", company="C", raw_text="r")
    q.update_job_pipeline(conn, below, simplified_content="", content_type="job_posting")
    q.update_job_pipeline(conn, above, simplified_content="", content_type="job_posting")
    q.upsert_job_score(conn, below, scenario_id, 0.5, "", "hash1")
    q.upsert_job_score(conn, above, scenario_id, 0.9, "", "hash2")

    all_jobs = q.get_jobs(conn)
    assert {j["title"] for j in all_jobs} == {"Below", "Above"}

    passed_only = q.get_jobs(conn, gate_status="passed")
    assert [j["title"] for j in passed_only] == ["Above"]


def test_get_jobs_gate_status_passed_keeps_never_scored_jobs(conn):
    # A job with zero job_scores rows (e.g. no scenarios existed at fetch
    # time) hasn't failed a gate — it was never gated at all — so it must
    # stay visible, unlike a job that was scored and failed every scenario.
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")
    scored_and_failed = q.insert_job(conn, source_id=source_id, url="http://job/failed", title="Failed", company="C", raw_text="r")
    never_scored = q.insert_job(conn, source_id=source_id, url="http://job/unscored", title="Unscored", company="C", raw_text="r")
    q.update_job_pipeline(conn, scored_and_failed, simplified_content="", content_type="job_posting")
    q.upsert_job_score(conn, scored_and_failed, scenario_id, 0.5, "", "hash1")  # below default 0.7

    passed_only = q.get_jobs(conn, gate_status="passed")

    assert [j["title"] for j in passed_only] == ["Unscored"]


def test_get_jobs_gate_status_passed_excludes_irrelevant_and_error(conn):
    # irrelevant/error content is never scored (evaluate() only runs for
    # job_posting/lead), so the old "content_type != job_posting" escape
    # hatch let it slip into the default New view as if it were pending.
    source_id = q.insert_source(conn, "s", "http://x", "http")
    irrelevant = q.insert_job(conn, source_id=source_id, url="http://job/irr", title="Irr", company="C", raw_text="r")
    error = q.insert_job(conn, source_id=source_id, url="http://job/err", title="Err", company="C", raw_text="r")
    posting = q.insert_job(conn, source_id=source_id, url="http://job/post", title="Post", company="C", raw_text="r")
    q.update_job_pipeline(conn, irrelevant, simplified_content="", content_type="irrelevant")
    q.update_job_pipeline(conn, error, simplified_content="", content_type="error")
    q.update_job_pipeline(conn, posting, simplified_content="", content_type="job_posting")

    passed_only = q.get_jobs(conn, gate_status="passed")

    assert [j["title"] for j in passed_only] == ["Post"]


def test_get_jobs_gate_status_passed_excludes_lead_below_threshold(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")  # default gate_threshold 0.7
    lead = q.insert_job(conn, source_id=source_id, url="http://job/lead", title="Lead", company="C", raw_text="r")
    q.update_job_pipeline(conn, lead, simplified_content="", content_type="lead")
    q.upsert_job_score(conn, lead, scenario_id, 0.3, "", "hash1")

    passed_only = q.get_jobs(conn, gate_status="passed")

    assert passed_only == []


def test_get_jobs_gate_status_passed_keeps_unscored_and_gate_passed_leads(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")  # default gate_threshold 0.7
    unscored_lead = q.insert_job(conn, source_id=source_id, url="http://job/lead1", title="Unscored lead", company="C", raw_text="r")
    passed_lead = q.insert_job(conn, source_id=source_id, url="http://job/lead2", title="Passed lead", company="C", raw_text="r")
    q.update_job_pipeline(conn, unscored_lead, simplified_content="", content_type="lead")
    q.update_job_pipeline(conn, passed_lead, simplified_content="", content_type="lead")
    q.upsert_job_score(conn, passed_lead, scenario_id, 0.9, "", "hash1")

    passed_only = q.get_jobs(conn, gate_status="passed")

    assert {j["title"] for j in passed_only} == {"Unscored lead", "Passed lead"}


def test_get_jobs_gate_status_failed_returns_only_failed_postings(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")  # default gate_threshold 0.7
    failed = q.insert_job(conn, source_id=source_id, url="http://job/failed", title="Failed", company="C", raw_text="r")
    passed = q.insert_job(conn, source_id=source_id, url="http://job/passed", title="Passed", company="C", raw_text="r")
    unscored = q.insert_job(conn, source_id=source_id, url="http://job/unscored", title="Unscored", company="C", raw_text="r")
    q.update_job_pipeline(conn, failed, simplified_content="", content_type="job_posting")
    q.update_job_pipeline(conn, passed, simplified_content="", content_type="job_posting")
    q.upsert_job_score(conn, failed, scenario_id, 0.3, "", "hash1")
    q.upsert_job_score(conn, passed, scenario_id, 0.9, "", "hash2")

    failed_only = q.get_jobs(conn, gate_status="failed")

    assert [j["title"] for j in failed_only] == ["Failed"]


def test_get_jobs_gate_status_passed_includes_overridden_job_that_failed_score(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")  # default gate_threshold 0.7
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="Overridden", company="C", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="", content_type="job_posting")
    q.upsert_job_score(conn, jid, scenario_id, 0.3, "", "hash1")
    q.mark_job_gate_override(conn, jid)

    passed_only = q.get_jobs(conn, gate_status="passed")

    assert [j["title"] for j in passed_only] == ["Overridden"]


def test_get_jobs_gate_status_failed_excludes_overridden_job(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
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
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")  # default gate_threshold 0.7
    lead = q.insert_job(conn, source_id=source_id, url="http://job/lead", title="Lead", company="C", raw_text="r")
    q.update_job_pipeline(conn, lead, simplified_content="", content_type="lead")
    q.upsert_job_score(conn, lead, scenario_id, 0.3, "", "hash1")

    failed_only = q.get_jobs(conn, content_type="job_posting", gate_status="failed")

    assert failed_only == []


def test_get_jobs_job_with_no_score_has_no_passed_scenarios(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    job = q.get_jobs(conn)[0]
    assert job["passed_gate_count"] is None
    assert job["passed_scenario_names"] is None
    assert job["top_passed_scenario_id"] is None


def test_update_job_fit_sets_scores_and_computed_fit_score(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
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
    source_id = q.insert_source(conn, "s", "http://x", "http")
    low = q.insert_job(conn, source_id=source_id, url="http://job/low", title="Low", company="C", raw_text="r")
    high = q.insert_job(conn, source_id=source_id, url="http://job/high", title="High", company="C", raw_text="r")
    q.update_job_fit(conn, low, 0.2, "", 0.2, "", "h")
    q.update_job_fit(conn, high, 0.9, "", 0.9, "", "h")
    jobs = q.get_jobs(conn)
    assert [j["title"] for j in jobs] == ["High", "Low"]


def test_upsert_scenario_feedback_inserts_and_updates(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
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
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, jid, scenario_id, "note", "higher")
    q.upsert_scenario_feedback(conn, jid, scenario_id, "", None)
    assert q.get_recent_feedback_notes(conn, scenario_id) == []


def test_upsert_scenario_feedback_keeps_row_with_only_note(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, jid, scenario_id, "just a comment", None)
    row = conn.execute(
        "SELECT note, direction FROM scenario_feedback WHERE job_id = ? AND scenario_id = ?", (jid, scenario_id)
    ).fetchone()
    assert row["note"] == "just a comment"
    assert row["direction"] is None


def test_upsert_scenario_feedback_keeps_row_with_only_direction(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
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
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, jid, scenario_id, "too junior", "lower")
    q.upsert_scenario_feedback(conn, jid, scenario_id, "", "lower")
    assert q.get_recent_feedback_notes(conn, scenario_id) == [{"direction": "lower", "note": "too junior"}]


def test_upsert_scenario_feedback_blank_note_with_new_direction_preserves_note(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, jid, scenario_id, "too junior", "lower")
    q.upsert_scenario_feedback(conn, jid, scenario_id, "", "higher")
    assert q.get_recent_feedback_notes(conn, scenario_id) == [{"direction": "higher", "note": "too junior"}]


def test_upsert_scenario_feedback_resubmitting_unchanged_values_does_not_reset_handled_state(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, jid, scenario_id, "too junior", "lower")
    anchor = q.get_recent_feedback_anchor(conn, scenario_id)
    q.mark_feedback_handled(conn, scenario_id, anchor)
    q.upsert_scenario_feedback(conn, jid, scenario_id, "too junior", "lower")  # identical resubmission
    assert q.get_recent_feedback_notes(conn, scenario_id) == []


def test_get_job_scores_surfaces_feedback_note_and_direction(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.upsert_job_score(conn, jid, scenario_id, 0.5, "reasoning", "hash1")
    q.upsert_scenario_feedback(conn, jid, scenario_id, "too strict", "higher")
    scores = q.get_job_scores(conn, jid)
    assert scores[0]["feedback_note"] == "too strict"
    assert scores[0]["feedback_direction"] == "higher"


def test_get_job_scores_feedback_fields_none_when_no_feedback(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.upsert_job_score(conn, jid, scenario_id, 0.5, "reasoning", "hash1")
    scores = q.get_job_scores(conn, jid)
    assert scores[0]["feedback_note"] is None
    assert scores[0]["feedback_direction"] is None


def test_get_recent_feedback_counts_splits_unhandled_by_direction(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
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
    source_id = q.insert_source(conn, "s", "http://x", "http")
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
    source_id = q.insert_source(conn, "s", "http://x", "http")
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
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")
    for i in range(25):
        j = q.insert_job(conn, source_id=source_id, url=f"http://job/{i}", title="T", company="C", raw_text="r")
        q.upsert_scenario_feedback(conn, j, scenario_id, f"note {i}", "higher")

    counts = q.get_recent_feedback_counts(conn, scenario_id)
    notes = q.get_recent_feedback_notes(conn, scenario_id)

    assert counts["unhandled_higher"] == len(notes) == 20  # capped


def test_get_fetch_stats_by_source_aggregates_runs(conn):
    sid = q.insert_source(conn, "s", "http://x", "http")
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
    sid = q.insert_source(conn, "s", "http://x", "http")
    stats = q.get_fetch_stats_by_source(conn)
    assert sid not in stats
