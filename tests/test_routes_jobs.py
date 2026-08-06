from datetime import datetime, timezone, timedelta
from unittest.mock import patch
import pytest
from app.db import queries as q


def _seed(conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "http")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/1", title="ML Eng", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="job_posting", summary="Great role")
    scenario_id = q.insert_scenario(conn, "Remote ML", "")
    q.upsert_job_score(conn, jid, scenario_id, 0.9, "Good match", "hash1")
    q.update_job_fit(conn, jid, 0.8, "Strong domain fit", 0.7, "Close match", "phash1")
    return sid, jid, scenario_id


def test_job_list_returns_200(client, conn):
    _seed(conn)
    resp = client.get("/jobs")
    assert resp.status_code == 200
    assert "ML Eng" in resp.text


def test_job_list_empty(client, conn):
    resp = client.get("/jobs")
    assert resp.status_code == 200
    assert "No jobs found" in resp.text


def test_job_list_shows_published_date(client, conn):
    published = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "finn_listing")
    q.insert_job(
        conn, source_id=sid, url="http://finn.no/job/1", title="ML Eng", company="Acme", raw_text="r",
        published_at=published,
    )
    resp = client.get("/jobs")
    assert resp.status_code == 200
    assert "5 days ago" in resp.text


def test_job_list_omits_published_date_when_unknown(client, conn):
    _seed(conn)
    resp = client.get("/jobs")
    assert resp.status_code == 200
    assert "days ago" not in resp.text


def test_job_list_filter_accepted(client, conn):
    sid, jid, scenario_id = _seed(conn)
    q.update_job_feedback(conn, jid, "accepted", "great")
    resp = client.get("/jobs?status=accepted")
    assert resp.status_code == 200
    assert "ML Eng" in resp.text
    resp2 = client.get("/jobs")
    assert "ML Eng" not in resp2.text


def test_job_list_filter_bar_shows_counts(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get("/jobs")
    assert resp.status_code == 200
    assert "New (1)" in resp.text
    assert "Accepted (0)" in resp.text
    assert "Rejected (0)" in resp.text
    assert "Invalid (0)" in resp.text
    assert "Leads (0)" in resp.text


def test_job_expand(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert "Accept" in resp.text
    assert "Reject" in resp.text


def test_job_feedback_updates_status(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.post(
        f"/jobs/{jid}/feedback",
        data={"status": "accepted", "note": "good fit"},
    )
    assert resp.status_code == 200
    job = q.get_job(conn, jid)
    assert job["status"] == "accepted"
    assert job["feedback_note"] == "good fit"


def test_job_list_shows_scenario_tag_for_passed_scenario(client, conn):
    _seed(conn)
    resp = client.get("/jobs")
    assert resp.status_code == 200
    assert "Remote ML" in resp.text


def test_job_expand_shows_scenario_tag_with_reasoning(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert "Good match" in resp.text
    assert "Remote ML" in resp.text


def test_job_list_card_is_clickable_and_has_no_details_button(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get("/jobs")
    assert resp.status_code == 200
    assert f'hx-get="/jobs/{jid}/expand"' in resp.text
    assert "Details" not in resp.text
    assert 'role="button"' in resp.text


def test_job_list_shows_headline_when_present(client, conn):
    sid, jid, scenario_id = _seed(conn)
    q.update_job_pipeline(
        conn, jid,
        simplified_content="clean", content_type="job_posting",
        summary="Great role", headline="Fully remote, $180k+",
    )
    resp = client.get("/jobs")
    assert "Fully remote, $180k+" in resp.text


def test_job_list_falls_back_to_summary_when_no_headline(client, conn):
    sid, jid, scenario_id = _seed(conn)  # _seed sets summary="Great role", no headline
    resp = client.get("/jobs")
    assert "Great role" in resp.text


def test_job_list_row_omits_company_but_expand_keeps_it(client, conn):
    sid, jid, scenario_id = _seed(conn)  # _seed sets company="Acme"
    resp = client.get("/jobs")
    assert "· Acme" not in resp.text
    resp2 = client.get(f"/jobs/{jid}/expand")
    assert "· Acme" in resp2.text


def test_job_list_title_is_heading_in_its_own_block(client, conn):
    _seed(conn)
    resp = client.get("/jobs")
    assert resp.status_code == 200
    assert '<h3 class="job-title">ML Eng</h3>' in resp.text


def test_job_list_card_is_article(client, conn):
    _seed(conn)
    resp = client.get("/jobs")
    assert resp.status_code == 200
    assert '<article class="job-row"' in resp.text


def test_job_list_tags_are_semantic_definition_list(client, conn):
    _seed(conn)
    resp = client.get("/jobs")
    assert resp.status_code == 200
    assert '<dl class="job-tags">' in resp.text
    assert '<dt class="sr-only">Fit score</dt>' in resp.text
    assert '<dt class="sr-only">Matched scenarios</dt>' in resp.text
    assert '<dt class="sr-only">Content type</dt>' in resp.text
    assert '<dt class="sr-only">Source</dt>' in resp.text


def test_job_expand_has_full_meta_parity_with_card(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert '<dl class="job-tags">' in resp.text
    assert "75%" in resp.text  # fit-score badge, not just reasoning text
    assert "Remote ML" in resp.text
    assert "job_posting" in resp.text
    assert "finn.no" in resp.text
    assert '<h3 class="job-title">ML Eng</h3>' in resp.text


def test_job_expand_shows_tab_per_scored_scenario(client, conn):
    sid, jid, scenario_a = _seed(conn)
    scenario_b = q.insert_scenario(conn, "Other Scenario", "")
    q.upsert_job_score(conn, jid, scenario_b, 0.4, "weaker fit", "hash2")

    resp = client.get(f"/jobs/{jid}/expand")

    assert resp.status_code == 200
    assert 'class="score-box' in resp.text
    assert "Remote ML" in resp.text
    assert "Other Scenario" in resp.text
    assert "Good match" in resp.text  # scenario_a's reasoning, from _seed
    assert "weaker fit" in resp.text  # scenario_b's reasoning
    assert "90%" in resp.text  # scenario_a's gate-score tab card
    assert "40%" in resp.text  # scenario_b's gate-score tab card


def test_job_expand_top_passed_scenario_tab_is_checked(client, conn):
    sid, jid, scenario_a = _seed(conn)
    scenario_b = q.insert_scenario(conn, "Other Scenario", "")
    q.upsert_job_score(conn, jid, scenario_b, 0.4, "weaker fit", "hash2")

    resp = client.get(f"/jobs/{jid}/expand")

    assert resp.status_code == 200
    assert f'id="score-tab-{jid}-{scenario_a}" name="score-tab-{jid}" class="score-tab-input" checked' in resp.text
    assert f'id="score-tab-{jid}-{scenario_b}" name="score-tab-{jid}" class="score-tab-input" checked' not in resp.text


def test_job_expand_shows_gate_pass_indicator(client, conn):
    sid, jid, scenario_a = _seed(conn)
    scenario_b = q.insert_scenario(conn, "Other Scenario", "")
    q.update_scenario(conn, scenario_b, name="Other Scenario", description="", gate_threshold=0.9)
    q.upsert_job_score(conn, jid, scenario_b, 0.4, "weaker fit", "hash2")  # below its own 0.9 gate

    resp = client.get(f"/jobs/{jid}/expand")

    assert resp.status_code == 200
    assert "Passed gate" in resp.text
    assert "Below gate threshold" in resp.text


def test_job_expand_shows_fit_scorecard(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert "Interest" in resp.text
    assert "Attainability" in resp.text
    assert "Strong domain fit" in resp.text
    assert "Close match" in resp.text


def test_job_expand_shows_not_yet_assessed_when_fit_missing(client, conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "http")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/2", title="No Fit Yet", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="job_posting", summary="role")
    scenario_id = q.insert_scenario(conn, "A", "")
    q.upsert_job_score(conn, jid, scenario_id, 0.9, "great", "hash1")  # passes gate, no stage-2 run yet
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert "Not yet assessed" in resp.text


def test_job_expand_no_score_box_when_unscored(client, conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "http")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/2", title="No Score", company="Acme", raw_text="r")
    q.insert_scenario(conn, "First", "")
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert 'class="score-box' not in resp.text


def test_job_feedback_without_note_succeeds(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.post(f"/jobs/{jid}/feedback", data={"status": "accepted"})
    assert resp.status_code == 200
    job = q.get_job(conn, jid)
    assert job["status"] == "accepted"
    assert job["feedback_note"] is None


def test_job_expand_note_field_is_optional(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert "Note (optional)" in resp.text
    assert '<textarea name="note" required' not in resp.text


def test_job_expand_reject_invalid_buttons_have_tooltips(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert 'title="Does not match your criteria — feeds back into scenario tuning."' in resp.text
    assert 'title="Not a usable posting (expired, spam, duplicate, wrong content) — does not affect scenario criteria."' in resp.text


def test_job_collapse_returns_row_view(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}/collapse")
    assert resp.status_code == 200
    assert f'hx-get="/jobs/{jid}/expand"' in resp.text
    assert f'id="job-{jid}"' in resp.text


def test_job_expand_header_is_collapsible(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert f'hx-get="/jobs/{jid}/collapse"' in resp.text


def test_job_list_has_swappable_content_wrapper(client, conn):
    resp = client.get("/jobs")
    assert resp.status_code == 200
    assert '<div id="jobs-content">' in resp.text


def test_job_list_row_has_bulk_select_checkbox(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get("/jobs")
    assert resp.status_code == 200
    assert f'<input type="checkbox" class="job-select" name="job_ids" value="{jid}" form="bulk-form"' in resp.text
    assert 'onclick="event.stopPropagation()"' in resp.text
    assert '<label class="job-select-wrap">' in resp.text


def test_job_expand_has_bulk_select_checkbox(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert f'<input type="checkbox" class="job-select" name="job_ids" value="{jid}" form="bulk-form"' in resp.text
    assert '<label class="job-select-wrap">' in resp.text


def _fake_run_reprocess_job(conn, client, model, job, scenarios, profile, progress_prefix=""):
    yield f"{progress_prefix}Reprocessing: {job['url']}"
    q.reset_job(conn, job["id"])
    yield f"{progress_prefix}Reset complete: {job['url']}"


def test_job_reset_streams_progress_and_resets_job(client, conn):
    sid, jid, scenario_id = _seed(conn)
    q.update_job_feedback(conn, jid, "rejected", "not a fit")

    with patch("app.routes.jobs.run_reprocess_job", side_effect=_fake_run_reprocess_job):
        resp = client.post(f"/jobs/{jid}/reset")

    assert resp.status_code == 200
    assert "Reprocessing" in resp.text
    assert "Reset complete" in resp.text
    assert q.get_job(conn, jid)["status"] == "new"


def test_job_reset_unknown_job_returns_404(client, conn):
    resp = client.post("/jobs/999/reset")
    assert resp.status_code == 404


def test_job_bulk_reset_streams_progress_for_each_job(client, conn):
    sid, j1, scenario_id = _seed(conn)
    j2 = q.insert_job(conn, source_id=sid, url="http://finn.no/job/2", title="Data Eng", company="Acme", raw_text="r")
    q.update_job_feedback(conn, j1, "rejected", "note")
    q.update_job_feedback(conn, j2, "invalid", "note")

    with patch("app.routes.jobs.run_reprocess_job", side_effect=_fake_run_reprocess_job):
        resp = client.post("/jobs/bulk-reset", data={"job_ids": [j1, j2]})

    assert resp.status_code == 200
    assert resp.text.count("Reprocessing") == 2
    assert q.get_job(conn, j1)["status"] == "new"
    assert q.get_job(conn, j2)["status"] == "new"


def test_job_bulk_feedback_updates_multiple_jobs(client, conn):
    sid, j1, scenario_id = _seed(conn)
    j2 = q.insert_job(conn, source_id=sid, url="http://finn.no/job/2", title="Data Eng", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, j2, simplified_content="clean", content_type="job_posting", summary="Also good")
    q.upsert_job_score(conn, j2, scenario_id, 0.6, "Decent match", "hash2")
    resp = client.post(
        "/jobs/bulk-feedback",
        data={"job_ids": [j1, j2], "status": "rejected", "status_filter": "", "content_type_filter": ""},
    )
    assert resp.status_code == 200
    assert q.get_job(conn, j1)["status"] == "rejected"
    assert q.get_job(conn, j2)["status"] == "rejected"


def test_job_bulk_feedback_note_is_optional(client, conn):
    sid, j1, scenario_id = _seed(conn)
    resp = client.post(
        "/jobs/bulk-feedback",
        data={"job_ids": [j1], "status": "accepted", "status_filter": "", "content_type_filter": ""},
    )
    assert resp.status_code == 200
    assert q.get_job(conn, j1)["feedback_note"] is None


def test_job_bulk_feedback_returns_filtered_content_reflecting_removed_jobs(client, conn):
    sid, j1, scenario_id = _seed(conn)
    resp = client.post(
        "/jobs/bulk-feedback",
        data={"job_ids": [j1], "status": "rejected", "status_filter": "", "content_type_filter": ""},
    )
    assert resp.status_code == 200
    assert "ML Eng" not in resp.text
    assert "No jobs found" in resp.text


def test_scenario_feedback_saves_note_and_direction(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.post(
        f"/jobs/{jid}/scenario-feedback",
        data={"scenario_id": [scenario_id], "note": ["should have scored lower"], "direction": ["lower"]},
    )
    assert resp.status_code == 200
    scores = q.get_job_scores(conn, jid)
    assert scores[0]["feedback_note"] == "should have scored lower"
    assert scores[0]["feedback_direction"] == "lower"


def test_scenario_feedback_saves_multiple_scenarios_in_one_submit(client, conn):
    sid, jid, scenario_a = _seed(conn)
    scenario_b = q.insert_scenario(conn, "Other Scenario", "")
    q.upsert_job_score(conn, jid, scenario_b, 0.4, "weaker fit", "hash2")
    resp = client.post(
        f"/jobs/{jid}/scenario-feedback",
        data={
            "scenario_id": [scenario_a, scenario_b],
            "note": ["great fit", "not close at all"],
            "direction": ["higher", "lower"],
        },
    )
    assert resp.status_code == 200
    scores = {s["scenario_id"]: s for s in q.get_job_scores(conn, jid)}
    assert scores[scenario_a]["feedback_note"] == "great fit"
    assert scores[scenario_a]["feedback_direction"] == "higher"
    assert scores[scenario_b]["feedback_note"] == "not close at all"
    assert scores[scenario_b]["feedback_direction"] == "lower"


def test_scenario_feedback_empty_direction_treated_as_unset(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.post(
        f"/jobs/{jid}/scenario-feedback",
        data={"scenario_id": [scenario_id], "note": ["just a comment"], "direction": [""]},
    )
    assert resp.status_code == 200
    scores = q.get_job_scores(conn, jid)
    assert scores[0]["feedback_note"] == "just a comment"
    assert scores[0]["feedback_direction"] is None


def test_scenario_feedback_returns_updated_tabs_with_confirmation(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.post(
        f"/jobs/{jid}/scenario-feedback",
        data={"scenario_id": [scenario_id], "note": ["should count more"], "direction": ["higher"]},
    )
    assert resp.status_code == 200
    assert 'class="score-box score-compare"' in resp.text
    assert 'aria-pressed="true"' in resp.text
    assert "Feedback saved" in resp.text


def test_job_expand_save_all_feedback_button_is_visually_primary(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert '<button type="submit" class="btn btn-primary">Save all feedback</button>' in resp.text


def test_scenario_feedback_keeps_note_textarea_populated_after_save(client, conn):
    # The "Feedback saved" banner is the send confirmation now — clearing the
    # textarea on top of that just makes it look like the note didn't stick.
    sid, jid, scenario_id = _seed(conn)
    resp = client.post(
        f"/jobs/{jid}/scenario-feedback",
        data={"scenario_id": [scenario_id], "note": ["should count more"], "direction": ["higher"]},
    )
    assert resp.status_code == 200
    assert "should count more" in resp.text
    assert 'data-direction="higher"' in resp.text
    assert 'aria-pressed="true"' in resp.text


def test_job_expand_prefills_note_textarea_from_stored_feedback(client, conn):
    sid, jid, scenario_id = _seed(conn)
    q.upsert_scenario_feedback(conn, jid, scenario_id, "previously noted", "lower")
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert "previously noted" in resp.text


def test_job_expand_has_one_save_button_for_all_scenario_feedback(client, conn):
    sid, jid, scenario_a = _seed(conn)
    scenario_b = q.insert_scenario(conn, "Other Scenario", "")
    q.upsert_job_score(conn, jid, scenario_b, 0.4, "weaker fit", "hash2")
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert resp.text.count("Save all feedback") == 1
    assert resp.text.count(f'hx-post="/jobs/{jid}/scenario-feedback"') == 1


def test_job_list_has_persistent_bulk_form_shell(client, conn):
    resp = client.get("/jobs")
    assert resp.status_code == 200
    assert '<form id="bulk-form" hx-post="/jobs/bulk-feedback" hx-target="#jobs-content" hx-swap="innerHTML">' in resp.text
    assert '<input type="hidden" name="status_filter" value="new">' in resp.text


def test_job_list_bulk_bar_has_actions_and_no_scenario_select(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get("/jobs")
    assert resp.status_code == 200
    assert "Keep each job's own scenario" not in resp.text
    assert 'name="feedback_scenario_id"' not in resp.text
    assert 'form="bulk-form" name="status" value="accepted"' in resp.text
    assert 'form="bulk-form" name="status" value="rejected"' in resp.text
    assert 'form="bulk-form" name="status" value="invalid"' in resp.text
    assert 'title="Does not match your criteria — feeds back into scenario tuning."' in resp.text
    assert 'title="Not a usable posting (expired, spam, duplicate, wrong content) — does not affect scenario criteria."' in resp.text


def test_job_expand_shows_scenario_feedback_form_per_tab(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert f'hx-post="/jobs/{jid}/scenario-feedback"' in resp.text
    assert "Why should this scenario score different?" in resp.text
    assert "Should score higher" in resp.text
    assert "Should score lower" in resp.text


def test_job_expand_has_no_scenario_select(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert '<select name="feedback_scenario_id"' not in resp.text


def test_job_list_bulk_form_reflects_active_filter(client, conn):
    resp = client.get("/jobs?status=accepted")
    assert resp.status_code == 200
    assert '<input type="hidden" name="status_filter" value="accepted">' in resp.text


def test_base_page_includes_bulk_bar_visibility_and_count_script(client, conn):
    resp = client.get("/jobs")
    assert resp.status_code == 200
    assert ':has(input[name="job_ids"]:checked)' in resp.text
    assert "bulk-count" in resp.text
    assert "bulk-clear" in resp.text


def test_base_page_includes_drag_select_script(client, conn):
    resp = client.get("/jobs")
    assert resp.status_code == 200
    assert "dragStartCheckbox" in resp.text
    assert "rowCheckbox" in resp.text


def test_job_bulk_feedback_respects_status_filter_for_response(client, conn):
    sid, j1, scenario_id = _seed(conn)
    q.update_job_feedback(conn, j1, "accepted", "")
    j2 = q.insert_job(conn, source_id=sid, url="http://finn.no/job/2", title="Data Eng", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, j2, simplified_content="clean", content_type="job_posting", summary="Also good")
    resp = client.post(
        "/jobs/bulk-feedback",
        data={"job_ids": [j2], "status": "accepted", "status_filter": "accepted", "content_type_filter": ""},
    )
    assert resp.status_code == 200
    assert "ML Eng" in resp.text
    assert "Data Eng" in resp.text


def test_job_list_new_tab_excludes_gate_failed_postings(client, conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "http")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/1", title="Filtered Out", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="job_posting", summary="role")
    scenario_id = q.insert_scenario(conn, "A", "")  # default gate_threshold 0.7
    q.upsert_job_score(conn, jid, scenario_id, 0.3, "not a fit", "hash1")

    resp = client.get("/jobs")
    assert "Filtered Out" not in resp.text

    resp2 = client.get("/jobs?status=not_relevant")
    assert "Filtered Out" in resp2.text


def test_job_list_not_relevant_tab_excludes_leads(client, conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "http")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/1", title="Low Score Lead", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="lead", summary="role")
    scenario_id = q.insert_scenario(conn, "A", "")  # default gate_threshold 0.7
    q.upsert_job_score(conn, jid, scenario_id, 0.3, "not a fit", "hash1")

    resp = client.get("/jobs?status=not_relevant")
    assert "Low Score Lead" not in resp.text


def test_job_list_leads_tab_includes_gate_failed_leads(client, conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "http")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/1", title="Low Score Lead", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="lead", summary="role")
    scenario_id = q.insert_scenario(conn, "A", "")  # default gate_threshold 0.7
    q.upsert_job_score(conn, jid, scenario_id, 0.3, "not a fit", "hash1")

    resp = client.get("/jobs?content_type=lead")
    assert "Low Score Lead" in resp.text


def test_job_list_accepted_tab_includes_gate_failed_jobs(client, conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "http")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/1", title="Low Score Accepted", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="job_posting", summary="role")
    scenario_id = q.insert_scenario(conn, "A", "")  # default gate_threshold 0.7
    q.upsert_job_score(conn, jid, scenario_id, 0.3, "not a fit", "hash1")
    q.update_job_feedback(conn, jid, "accepted", "")

    resp = client.get("/jobs?status=accepted")
    assert "Low Score Accepted" in resp.text


def test_job_list_shows_not_relevant_tab_and_drops_show_filtered(client, conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "http")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/1", title="Filtered Out", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="job_posting", summary="role")
    scenario_id = q.insert_scenario(conn, "A", "")  # default gate_threshold 0.7
    q.upsert_job_score(conn, jid, scenario_id, 0.3, "not a fit", "hash1")

    resp = client.get("/jobs")

    assert "Not relevant (1)" in resp.text
    assert 'href="/jobs?status=not_relevant"' in resp.text
    assert "Show filtered" not in resp.text
    assert 'name="show_filtered_filter"' not in resp.text
