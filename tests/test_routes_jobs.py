from datetime import datetime, timezone, timedelta
from unittest.mock import patch
import httpx
import pytest
import respx
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
    assert '<span id="count-new">1</span>' in resp.text
    assert '<span id="count-accepted">0</span>' in resp.text
    assert '<span id="count-rejected">0</span>' in resp.text
    assert '<span id="count-trash">0</span>' in resp.text
    assert '<span id="count-lead">0</span>' in resp.text


def test_job_list_nav_tabs_have_explanatory_tooltips(client, conn):
    _seed(conn)
    resp = client.get("/jobs")
    assert resp.status_code == 200
    for label, snippet in [
        ("New Jobs", 'title="Job postings awaiting your decision'),
        ("New Leads", 'title="Leads awaiting your decision'),
        ("Accepted", "title=\"Jobs and leads you've accepted."),
        ("Rejected", "title=\"Jobs and leads you've rejected"),
        ("Not relevant", 'title="Real job postings that didn\'t pass any scenario\'s relevance gate'),
        ("Trash", 'title="Unusable postings (expired, spam, duplicate, wrong content)'),
    ]:
        assert snippet in resp.text, f"missing tooltip for {label}"


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
    assert f'hx-get="/jobs/{jid}/expand?status=&content_type="' in resp.text
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


def test_job_expand_shows_pass_as_new_button_when_gate_failed(client, conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "http")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/failed", title="Failed Gate", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="job_posting", summary="role")
    scenario_id = q.insert_scenario(conn, "A", "")  # default gate_threshold 0.7
    q.upsert_job_score(conn, jid, scenario_id, 0.2, "too junior", "hash1")

    resp = client.get(f"/jobs/{jid}/expand")

    assert resp.status_code == 200
    assert f'data-progress-url="/jobs/{jid}/pass-as-new"' in resp.text
    assert "Pass as new" in resp.text


def test_job_expand_omits_pass_as_new_button_when_gate_passed(client, conn):
    sid, jid, scenario_id = _seed(conn)  # scored 0.9, passes default 0.7 gate
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert "Pass as new" not in resp.text


def test_job_expand_omits_pass_as_new_button_when_already_overridden(client, conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "http")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/failed", title="Failed Gate", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="job_posting", summary="role")
    scenario_id = q.insert_scenario(conn, "A", "")
    q.upsert_job_score(conn, jid, scenario_id, 0.2, "too junior", "hash1")
    q.mark_job_gate_override(conn, jid)

    resp = client.get(f"/jobs/{jid}/expand")

    assert resp.status_code == 200
    assert "Pass as new" not in resp.text


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


def test_job_expand_reject_trash_buttons_have_tooltips(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert 'title="Does not match your criteria."' in resp.text
    assert 'title="Not a usable posting (expired, spam, duplicate, wrong content)."' in resp.text


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


def test_job_expand_reset_button_has_progress_oob_attribute(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert f'data-progress-url="/jobs/{jid}/reset"' in resp.text
    assert "data-progress-oob" in resp.text


def test_job_reset_stream_ends_with_html_chunk_for_updated_row(client, conn):
    sid, jid, scenario_id = _seed(conn)
    with patch("app.routes.jobs.run_reprocess_job", side_effect=_fake_run_reprocess_job):
        resp = client.post(f"/jobs/{jid}/reset")
    assert resp.status_code == 200
    assert f'HTML:<article class="job-row" id="job-{jid}">' in resp.text


def test_job_pass_as_new_stream_ends_with_html_chunk_for_updated_row(client, conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "http")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/failed", title="Failed Gate", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="job_posting", summary="role")
    scenario_id = q.insert_scenario(conn, "A", "")
    q.upsert_job_score(conn, jid, scenario_id, 0.2, "too junior", "hash1")
    with patch("app.routes.jobs.run_pass_as_new", side_effect=_fake_run_pass_as_new):
        resp = client.post(f"/jobs/{jid}/pass-as-new")
    assert resp.status_code == 200
    assert f'HTML:<article class="job-row" id="job-{jid}">' in resp.text


def test_job_reset_stream_includes_counts_html_chunk(client, conn):
    sid, jid, scenario_id = _seed(conn)
    with patch("app.routes.jobs.run_reprocess_job", side_effect=_fake_run_reprocess_job):
        resp = client.post(f"/jobs/{jid}/reset")
    assert resp.status_code == 200
    assert 'HTML:<span id="count-new" hx-swap-oob="true">' in resp.text


def test_job_pass_as_new_stream_includes_counts_html_chunk(client, conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "http")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/failed", title="Failed Gate", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="job_posting", summary="role")
    scenario_id = q.insert_scenario(conn, "A", "")
    q.upsert_job_score(conn, jid, scenario_id, 0.2, "too junior", "hash1")
    with patch("app.routes.jobs.run_pass_as_new", side_effect=_fake_run_pass_as_new):
        resp = client.post(f"/jobs/{jid}/pass-as-new")
    assert resp.status_code == 200
    assert 'HTML:<span id="count-new" hx-swap-oob="true">' in resp.text


def test_job_reset_with_filter_query_forwards_it_into_rendered_row(client, conn):
    sid, jid, scenario_id = _seed(conn)
    q.update_job_feedback(conn, jid, "accepted", "")
    with patch("app.routes.jobs.run_reprocess_job", side_effect=_fake_run_reprocess_job):
        resp = client.post(f"/jobs/{jid}/reset?status=accepted&content_type=")
    assert resp.status_code == 200
    # Resetting an accepted job always returns it to status "new", so it falls out of
    # the Accepted tab -> renders as the stale short row, whose own expand link must
    # still carry the filter forward.
    assert f'jobs/{jid}/expand?status=accepted&content_type=' in resp.text


def _fake_run_reprocess_job_to_passing(conn, client, model, job, scenarios, profile, progress_prefix=""):
    yield f"{progress_prefix}Reprocessing: {job['url']}"
    q.reset_job(conn, job["id"])
    q.update_job_pipeline(
        conn, job["id"], simplified_content="clean", content_type="job_posting",
        title=job["title"], headline="", summary="Now a great match",
    )
    scenario = scenarios[0]
    q.upsert_job_score(conn, job["id"], scenario["id"], 0.95, "now passes", "hash-new")
    yield f"{progress_prefix}Reset complete: {job['url']}"


def test_job_reset_from_not_relevant_tab_shows_moved_to_new_badge(client, conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "http")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/1", title="Filtered Out", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="job_posting", summary="role")
    scenario_id = q.insert_scenario(conn, "A", "")  # default gate_threshold 0.7
    q.upsert_job_score(conn, jid, scenario_id, 0.3, "not a fit", "hash1")  # gate-failed -> "Not relevant" tab

    with patch("app.routes.jobs.run_reprocess_job", side_effect=_fake_run_reprocess_job_to_passing):
        resp = client.post(f"/jobs/{jid}/reset?status=not_relevant&content_type=")

    assert resp.status_code == 200
    assert "Moved to New" in resp.text
    assert f'href="/jobs#job-{jid}"' in resp.text


def test_job_reset_that_stays_in_current_filter_shows_no_badge(client, conn):
    sid, jid, scenario_id = _seed(conn)  # already gate-passed, status "new"
    with patch("app.routes.jobs.run_reprocess_job", side_effect=_fake_run_reprocess_job):
        resp = client.post(f"/jobs/{jid}/reset?status=&content_type=")
    assert resp.status_code == 200
    assert "Moved to" not in resp.text


def test_job_reset_without_filter_query_shows_no_badge(client, conn):
    # Simulates the standalone /jobs/{id} page, which never sends filter params.
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "http")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/1", title="Filtered Out", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="job_posting", summary="role")
    scenario_id = q.insert_scenario(conn, "A", "")
    q.upsert_job_score(conn, jid, scenario_id, 0.3, "not a fit", "hash1")

    with patch("app.routes.jobs.run_reprocess_job", side_effect=_fake_run_reprocess_job_to_passing):
        resp = client.post(f"/jobs/{jid}/reset")

    assert resp.status_code == 200
    assert "Moved to" not in resp.text


def test_job_pass_as_new_shows_moved_to_new_badge(client, conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "http")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/failed", title="Failed Gate", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="job_posting", summary="role")
    scenario_id = q.insert_scenario(conn, "A", "")
    q.upsert_job_score(conn, jid, scenario_id, 0.2, "too junior", "hash1")

    with patch("app.routes.jobs.run_pass_as_new", side_effect=_fake_run_pass_as_new):
        resp = client.post(f"/jobs/{jid}/pass-as-new?status=not_relevant&content_type=")

    assert resp.status_code == 200
    assert "Moved to New" in resp.text


def test_job_feedback_with_redirect_field_returns_hx_redirect_header(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.post(
        f"/jobs/{jid}/feedback",
        data={"status": "accepted", "note": "", "redirect": "/jobs"},
    )
    assert resp.status_code == 200
    assert resp.headers["HX-Redirect"] == "/jobs"
    job = q.get_job(conn, jid)
    assert job["status"] == "accepted"


def test_job_feedback_without_redirect_field_has_no_redirect_header(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.post(f"/jobs/{jid}/feedback", data={"status": "accepted", "note": ""})
    assert resp.status_code == 200
    assert "HX-Redirect" not in resp.headers


def test_job_detail_feedback_form_includes_redirect_field(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}")
    assert resp.status_code == 200
    assert '<input type="hidden" name="redirect" value="/jobs">' in resp.text


def test_job_expand_feedback_form_has_no_redirect_field(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert 'name="redirect"' not in resp.text


def test_job_bulk_reset_streams_progress_for_each_job(client, conn):
    sid, j1, scenario_id = _seed(conn)
    j2 = q.insert_job(conn, source_id=sid, url="http://finn.no/job/2", title="Data Eng", company="Acme", raw_text="r")
    q.update_job_feedback(conn, j1, "rejected", "note")
    q.update_job_feedback(conn, j2, "trash", "note")

    with patch("app.routes.jobs.run_reprocess_job", side_effect=_fake_run_reprocess_job):
        resp = client.post("/jobs/bulk-reset", data={"job_ids": [j1, j2]})

    assert resp.status_code == 200
    assert resp.text.count("Reprocessing") == 2
    assert q.get_job(conn, j1)["status"] == "new"
    assert q.get_job(conn, j2)["status"] == "new"


def test_job_bulk_reset_stream_includes_per_job_html_and_counts_chunks(client, conn):
    sid, j1, scenario_id = _seed(conn)
    j2 = q.insert_job(conn, source_id=sid, url="http://finn.no/job/2", title="Data Eng", company="Acme", raw_text="r")
    q.update_job_feedback(conn, j1, "rejected", "note")
    q.update_job_feedback(conn, j2, "trash", "note")

    with patch("app.routes.jobs.run_reprocess_job", side_effect=_fake_run_reprocess_job):
        resp = client.post("/jobs/bulk-reset", data={"job_ids": [j1, j2]})

    assert resp.status_code == 200
    assert resp.text.count(f'HTML:<article class="job-row" id="job-{j1}">') == 1
    assert resp.text.count(f'HTML:<article class="job-row" id="job-{j2}">') == 1
    assert 'HTML:<span id="count-new" hx-swap-oob="true">' in resp.text


def test_job_bulk_reset_with_filter_shows_moved_marker_per_job(client, conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "http")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/1", title="Filtered Out", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="job_posting", summary="role")
    scenario_id = q.insert_scenario(conn, "A", "")  # default gate_threshold 0.7
    q.upsert_job_score(conn, jid, scenario_id, 0.3, "not a fit", "hash1")  # gate-failed -> "Not relevant" tab

    with patch("app.routes.jobs.run_reprocess_job", side_effect=_fake_run_reprocess_job_to_passing):
        resp = client.post("/jobs/bulk-reset?status=not_relevant&content_type=", data={"job_ids": [jid]})

    assert resp.status_code == 200
    assert "Moved to New" in resp.text


def test_job_bulk_reset_button_has_progress_oob_and_filter_query(client, conn):
    resp = client.get("/jobs?status=accepted")
    assert resp.status_code == 200
    assert 'data-progress-url="/jobs/bulk-reset?status=accepted&content_type="' in resp.text
    assert 'data-progress-oob="1"' in resp.text


def _fake_run_pass_as_new(conn, client, model, job, profile):
    yield f"Bypassing gate threshold: {job['url']}"
    q.mark_job_gate_override(conn, job["id"])
    yield f"Fit 0.80/0.60: {job['url']}"


def test_job_pass_as_new_streams_progress_and_marks_override(client, conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "http")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/failed", title="Failed Gate", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="job_posting", summary="role")
    scenario_id = q.insert_scenario(conn, "A", "")  # default gate_threshold 0.7
    q.upsert_job_score(conn, jid, scenario_id, 0.2, "too junior", "hash1")

    with patch("app.routes.jobs.run_pass_as_new", side_effect=_fake_run_pass_as_new):
        resp = client.post(f"/jobs/{jid}/pass-as-new")

    assert resp.status_code == 200
    assert "Bypassing gate threshold" in resp.text
    assert q.get_job(conn, jid)["gate_override"] == 1


def test_job_pass_as_new_unknown_job_returns_404(client, conn):
    resp = client.post("/jobs/999/pass-as-new")
    assert resp.status_code == 404


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


def test_job_bulk_feedback_leaves_moved_job_as_stale_row(client, conn):
    sid, j1, scenario_id = _seed(conn)
    resp = client.post(
        "/jobs/bulk-feedback",
        data={"job_ids": [j1], "status": "rejected", "status_filter": "", "content_type_filter": ""},
    )
    assert resp.status_code == 200
    # The rejected job no longer belongs on the default (New) tab, but instead of
    # vanishing it lingers as a dimmed stale row with a "Moved to Rejected" marker.
    assert "ML Eng" in resp.text
    assert "Moved to Rejected" in resp.text
    assert f'href="/jobs?status=rejected#job-{j1}"' in resp.text
    assert "No jobs found" not in resp.text


def test_job_bulk_feedback_shows_no_jobs_found_when_nothing_matches_or_moved(client, conn):
    # job_ids references a nonexistent job, so nothing lands in either jobs or stale_jobs.
    resp = client.post(
        "/jobs/bulk-feedback",
        data={"job_ids": [999], "status": "rejected", "status_filter": "", "content_type_filter": ""},
    )
    assert resp.status_code == 200
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
    assert 'form="bulk-form" name="status" value="trash"' in resp.text
    assert 'title="Does not match your criteria."' in resp.text
    assert 'title="Not a usable posting (expired, spam, duplicate, wrong content)."' in resp.text


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


def test_job_list_leads_tab_excludes_accepted_and_rejected_leads(client, conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "http")
    jid_accepted = q.insert_job(conn, source_id=sid, url="http://finn.no/job/1", title="Accepted Lead", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, jid_accepted, simplified_content="clean", content_type="lead", summary="role")
    q.update_job_feedback(conn, jid_accepted, "accepted", "")

    jid_rejected = q.insert_job(conn, source_id=sid, url="http://finn.no/job/2", title="Rejected Lead", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, jid_rejected, simplified_content="clean", content_type="lead", summary="role")
    q.update_job_feedback(conn, jid_rejected, "rejected", "")

    resp = client.get("/jobs?content_type=lead")
    assert "Accepted Lead" not in resp.text
    assert "Rejected Lead" not in resp.text
    assert '<span id="count-lead">0</span>' in resp.text


def test_job_list_accepted_tab_includes_gate_failed_jobs(client, conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "http")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/1", title="Low Score Accepted", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="job_posting", summary="role")
    scenario_id = q.insert_scenario(conn, "A", "")  # default gate_threshold 0.7
    q.upsert_job_score(conn, jid, scenario_id, 0.3, "not a fit", "hash1")
    q.update_job_feedback(conn, jid, "accepted", "")

    resp = client.get("/jobs?status=accepted")
    assert "Low Score Accepted" in resp.text


def test_job_list_shows_status_badge_for_accepted_rejected_trash(client, conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "http")

    jid_accepted = q.insert_job(conn, source_id=sid, url="http://finn.no/job/1", title="Accepted Job", company="Acme", raw_text="r")
    q.update_job_feedback(conn, jid_accepted, "accepted", "")

    jid_rejected = q.insert_job(conn, source_id=sid, url="http://finn.no/job/2", title="Rejected Job", company="Acme", raw_text="r")
    q.update_job_feedback(conn, jid_rejected, "rejected", "")

    jid_trash = q.insert_job(conn, source_id=sid, url="http://finn.no/job/3", title="Trashed Job", company="Acme", raw_text="r")
    q.update_job_feedback(conn, jid_trash, "trash", "")

    resp = client.get("/jobs?status=accepted")
    assert '<span class="score-badge score-high">Accepted</span>' in resp.text

    resp = client.get("/jobs?status=rejected")
    assert '<span class="score-badge score-low">Rejected</span>' in resp.text

    resp = client.get("/jobs?status=trash")
    assert '<span class="score-badge score-neutral">Trash</span>' in resp.text


def test_job_list_new_tab_shows_no_status_badge(client, conn):
    _seed(conn)
    resp = client.get("/jobs")
    assert "score-badge score-high\">Accepted" not in resp.text
    assert "score-badge score-low\">Rejected" not in resp.text
    assert "score-badge score-neutral\">Trash" not in resp.text


def test_job_list_shows_not_relevant_tab_and_drops_show_filtered(client, conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "http")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/1", title="Filtered Out", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="job_posting", summary="role")
    scenario_id = q.insert_scenario(conn, "A", "")  # default gate_threshold 0.7
    q.upsert_job_score(conn, jid, scenario_id, 0.3, "not a fit", "hash1")

    resp = client.get("/jobs")

    assert '<span id="count-not_relevant">1</span>' in resp.text
    assert 'href="/jobs?status=not_relevant"' in resp.text
    assert "Show filtered" not in resp.text
    assert 'name="show_filtered_filter"' not in resp.text


def test_job_feedback_updates_counts_oob(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.post(f"/jobs/{jid}/feedback", data={"status": "accepted", "note": ""})
    assert resp.status_code == 200
    assert '<span id="count-new" hx-swap-oob="true">0</span>' in resp.text
    assert '<span id="count-accepted" hx-swap-oob="true">1</span>' in resp.text
    assert '<span id="count-rejected" hx-swap-oob="true">0</span>' in resp.text


def test_job_detail_returns_200_with_job_content(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}")
    assert resp.status_code == 200
    assert "ML Eng" in resp.text
    assert "Accept" in resp.text
    assert "Reject" in resp.text


def test_job_detail_unknown_job_returns_404(client, conn):
    resp = client.get("/jobs/999")
    assert resp.status_code == 404


def test_job_detail_has_back_to_list_link(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}")
    assert resp.status_code == 200
    assert 'href="/jobs"' in resp.text


def test_job_detail_omits_bulk_select_checkbox(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}")
    assert resp.status_code == 200
    assert '<label class="job-select-wrap">' not in resp.text
    assert f'<input type="checkbox" class="job-select" name="job_ids" value="{jid}" form="bulk-form"' not in resp.text


def test_job_expand_still_has_bulk_select_checkbox(client, conn):
    # Guards against the is_detail_page flag leaking into the normal list flow.
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert '<label class="job-select-wrap">' in resp.text


def test_job_list_row_has_permalink_icon(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get("/jobs")
    assert resp.status_code == 200
    assert f'href="/jobs/{jid}" class="permalink-icon"' in resp.text


def test_job_expand_has_permalink_icon(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert f'href="/jobs/{jid}" class="permalink-icon"' in resp.text


def test_job_list_default_tab_expand_link_carries_empty_filter_params(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get("/jobs")
    assert resp.status_code == 200
    assert f'hx-get="/jobs/{jid}/expand?status=&content_type="' in resp.text


def test_job_list_filtered_tab_expand_link_carries_filter_params(client, conn):
    sid, jid, scenario_id = _seed(conn)
    q.update_job_feedback(conn, jid, "accepted", "")
    resp = client.get("/jobs?status=accepted")
    assert resp.status_code == 200
    assert f'hx-get="/jobs/{jid}/expand?status=accepted&content_type="' in resp.text


def test_job_expand_forwards_filter_to_collapse_link(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}/expand?status=accepted&content_type=")
    assert resp.status_code == 200
    assert f'hx-get="/jobs/{jid}/collapse?status=accepted&content_type="' in resp.text


def test_job_expand_without_filter_query_omits_collapse_filter_params(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert f'hx-get="/jobs/{jid}/collapse"' in resp.text
    assert "collapse?status=" not in resp.text


def test_job_accept_from_new_tab_shows_stale_short_row(client, conn):
    sid, jid, scenario_id = _seed(conn)  # status "new", gate-passed -> shown on default /jobs tab
    resp = client.post(
        f"/jobs/{jid}/feedback?status=&content_type=",
        data={"status": "accepted", "note": ""},
    )
    assert resp.status_code == 200
    assert "Moved to Accepted" in resp.text
    assert f'href="/jobs?status=accepted#job-{jid}"' in resp.text
    # Nav counts still update alongside the stale row.
    assert '<span id="count-accepted" hx-swap-oob="true">1</span>' in resp.text


def test_job_reject_from_not_relevant_tab_shows_stale_short_row(client, conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "http")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/1", title="Filtered Out", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="job_posting", summary="role")
    scenario_id = q.insert_scenario(conn, "A", "")  # default gate_threshold 0.7
    q.upsert_job_score(conn, jid, scenario_id, 0.3, "not a fit", "hash1")  # gate-failed -> "Not relevant" tab

    resp = client.post(
        f"/jobs/{jid}/feedback?status=not_relevant&content_type=",
        data={"status": "rejected", "note": "not a fit"},
    )
    assert resp.status_code == 200
    assert "Moved to Rejected" in resp.text
    assert f'href="/jobs?status=rejected#job-{jid}"' in resp.text


def test_job_feedback_that_stays_in_current_filter_shows_no_badge(client, conn):
    sid, jid, scenario_id = _seed(conn)
    q.update_job_feedback(conn, jid, "rejected", "")
    resp = client.post(
        f"/jobs/{jid}/feedback?status=rejected&content_type=",
        data={"status": "rejected", "note": "still not a fit"},
    )
    assert resp.status_code == 200
    assert "Moved to" not in resp.text


def test_job_feedback_without_filter_query_shows_no_badge(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.post(f"/jobs/{jid}/feedback", data={"status": "accepted", "note": ""})
    assert resp.status_code == 200
    assert "Moved to" not in resp.text


def test_job_feedback_with_redirect_still_bypasses_row_rendering(client, conn):
    # The detail page's redirect flow must short-circuit before any row/badge logic.
    sid, jid, scenario_id = _seed(conn)
    resp = client.post(
        f"/jobs/{jid}/feedback",
        data={"status": "accepted", "note": "", "redirect": "/jobs"},
    )
    assert resp.status_code == 200
    assert resp.headers["HX-Redirect"] == "/jobs"
    assert resp.text == ""


def test_base_page_includes_target_highlight_script(client, conn):
    resp = client.get("/jobs")
    assert resp.status_code == 200
    assert "job-row-target-highlight" in resp.text


def test_job_detail_collapse_link_carries_detail_flag(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}")
    assert resp.status_code == 200
    assert f'hx-get="/jobs/{jid}/collapse?detail=1"' in resp.text


def test_job_detail_collapse_keeps_checkbox_hidden(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}/collapse?detail=1")
    assert resp.status_code == 200
    assert '<label class="job-select-wrap">' not in resp.text
    assert f'hx-get="/jobs/{jid}/expand?detail=1"' in resp.text


def test_job_detail_collapsed_then_reexpanded_still_hides_checkbox_and_redirect(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}/expand?detail=1")
    assert resp.status_code == 200
    assert '<label class="job-select-wrap">' not in resp.text
    assert '<input type="hidden" name="redirect" value="/jobs">' in resp.text


def test_job_collapse_without_detail_flag_shows_checkbox(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}/collapse")
    assert resp.status_code == 200
    assert '<label class="job-select-wrap">' in resp.text


_JOB_POSTING_HTML = (
    "<html><body><p>We are hiring a Senior Software Engineer to join our platform team. "
    "You will design, build, and operate distributed systems that power our product for "
    "millions of users worldwide. We're looking for someone with strong experience in "
    "backend development, a collaborative mindset, and a passion for shipping reliable "
    "software. This is a full-time, remote-friendly position with competitive pay and "
    "benefits.</p></body></html>"
)


def _fake_run_add_job(conn, client, model, source_id, url, raw_text):
    jid = q.insert_job(conn, source_id=source_id, url=url, title="Fake Title", company="Acme", raw_text=raw_text)
    q.update_job_pipeline(conn, jid, simplified_content=raw_text, content_type="job_posting", summary="A role")
    yield f"Classified as job_posting: {url}"


_NOT_A_LISTING = {"is_listing": False, "job_links": []}


@respx.mock
def test_add_job_by_url_success_inserts_job_and_streams_progress(client, conn):
    respx.get("http://example.com/job/1").mock(
        return_value=httpx.Response(200, text=_JOB_POSTING_HTML)
    )
    with patch("app.routes.jobs.run_add_job", side_effect=_fake_run_add_job), \
         patch("app.routes.jobs.detect_listing", return_value=_NOT_A_LISTING):
        resp = client.post("/jobs/add-by-url", data={"url": "http://example.com/job/1"})
    assert resp.status_code == 200
    assert "Classified as job_posting" in resp.text
    jobs = q.get_jobs(conn)
    assert len(jobs) == 1
    assert jobs[0]["url"] == "http://example.com/job/1"
    source = q.get_source(conn, jobs[0]["source_id"])
    assert source["fetcher_type"] == "manual"


@respx.mock
def test_add_job_by_url_stream_ends_with_single_html_chunk(client, conn):
    respx.get("http://example.com/job/1").mock(
        return_value=httpx.Response(200, text=_JOB_POSTING_HTML)
    )
    with patch("app.routes.jobs.run_add_job", side_effect=_fake_run_add_job), \
         patch("app.routes.jobs.detect_listing", return_value=_NOT_A_LISTING):
        resp = client.post("/jobs/add-by-url", data={"url": "http://example.com/job/1"})
    assert resp.status_code == 200
    # Only one HTML: chunk should stream back — the target-mode swap in base.html's
    # progress JS keeps only the *last* HTML: line as the replacement innerHTML, so a
    # second trailing chunk (e.g. a separate counts_oob fragment) would silently clobber
    # the real content instead of updating it. _content.html's filter-bar already carries
    # fresh counts, so no second chunk is needed.
    assert resp.text.count("HTML:") == 1
    assert 'HTML:<div class="filter-bar">' in resp.text
    assert 'id="count-new"' in resp.text


@respx.mock
def test_add_job_by_url_duplicate_url_does_not_insert(client, conn):
    sid = q.insert_source(conn, "s", "http://x", "http")
    jid = q.insert_job(conn, source_id=sid, url="http://example.com/job/1", title="T", company="C", raw_text="r")

    resp = client.post("/jobs/add-by-url", data={"url": "http://example.com/job/1"})

    assert resp.status_code == 200
    assert "Already tracked" in resp.text
    assert f"/jobs/{jid}" in resp.text
    assert len(q.get_jobs(conn)) == 1


def test_add_job_by_url_duplicate_url_shows_persistent_link_to_job(client, conn):
    sid = q.insert_source(conn, "s", "http://x", "http")
    jid = q.insert_job(conn, source_id=sid, url="http://example.com/job/1", title="T", company="C", raw_text="r")

    resp = client.post("/jobs/add-by-url", data={"url": "http://example.com/job/1"})

    assert resp.status_code == 200
    assert f'<a href="/jobs/{jid}">View this job</a>' in resp.text


def test_add_job_by_url_existing_source_url_shows_link_to_source(client, conn):
    sid = q.insert_source(conn, "Careers Page", "https://careers.example.com/jobs", "generic_listing")

    resp = client.post("/jobs/add-by-url", data={"url": "https://careers.example.com/jobs"})

    assert resp.status_code == 200
    assert "Already tracked as a source" in resp.text
    assert f'<a href="/sources#source-row-{sid}">' in resp.text
    assert "Careers Page" in resp.text
    assert q.get_jobs(conn) == []


@respx.mock
def test_add_job_by_url_fetch_failure_inserts_error_job(client, conn):
    respx.get("http://example.com/broken").mock(return_value=httpx.Response(404))

    resp = client.post("/jobs/add-by-url", data={"url": "http://example.com/broken"})

    assert resp.status_code == 200
    assert "NOTICE:warning:" in resp.text
    assert "Couldn't add" in resp.text
    assert "HTTP 404" in resp.text
    jobs = q.get_jobs(conn)
    assert len(jobs) == 1
    assert jobs[0]["content_type"] == "error"
    assert jobs[0]["url"] == "http://example.com/broken"


@respx.mock
def test_add_job_by_url_fetch_network_error_inserts_error_job(client, conn):
    respx.get("http://example.com/unreachable").mock(side_effect=httpx.ConnectError("boom"))

    resp = client.post("/jobs/add-by-url", data={"url": "http://example.com/unreachable"})

    assert resp.status_code == 200
    assert "NOTICE:warning:" in resp.text
    assert "Couldn't add" in resp.text
    jobs = q.get_jobs(conn)
    assert len(jobs) == 1
    assert jobs[0]["content_type"] == "error"


@respx.mock
def test_add_job_by_url_js_only_page_shows_no_content_notice(client, conn):
    # Real shell HTML from a client-side-rendered job board (Ashby): 200 OK, but the
    # only text present is a noscript-style placeholder — no real posting content.
    js_shell_html = (
        "<html><body>"
        "<h1>Trener Jobs</h1>"
        "<noscript>You need to enable JavaScript to run this app.</noscript>"
        "</body></html>"
    )
    respx.get("http://example.com/js-app").mock(return_value=httpx.Response(200, text=js_shell_html))

    with patch("app.routes.jobs.detect_listing", return_value=_NOT_A_LISTING):
        resp = client.post("/jobs/add-by-url", data={"url": "http://example.com/js-app"})

    assert resp.status_code == 200
    assert "NOTICE:warning:" in resp.text
    assert "No job content detected" in resp.text
    assert "load its content dynamically" in resp.text
    assert "JavaScript" in resp.text
    jobs = q.get_jobs(conn)
    assert len(jobs) == 1
    assert jobs[0]["content_type"] == "error"
    assert jobs[0]["url"] == "http://example.com/js-app"


_IS_A_LISTING = {
    "is_listing": True,
    "job_links": ["https://careers.example.com/jobs/1", "https://careers.example.com/jobs/2"],
}


@respx.mock
def test_add_job_by_url_listing_detected_shows_confirm_panel(client, conn):
    respx.get("https://careers.example.com/jobs").mock(
        return_value=httpx.Response(200, text="<html><body><a href='/jobs/1'>A</a><a href='/jobs/2'>B</a></body></html>")
    )
    with patch("app.routes.jobs.detect_listing", return_value=_IS_A_LISTING):
        resp = client.post("/jobs/add-by-url", data={"url": "https://careers.example.com/jobs"})

    assert resp.status_code == 200
    assert "Detected 2 job posting" in resp.text
    assert "careers.example.com" in resp.text
    assert 'data-progress-url="/jobs/add-listing-source"' in resp.text
    assert q.get_jobs(conn) == []
    assert [s for s in q.get_sources(conn) if s["fetcher_type"] == "generic_listing"] == []


@respx.mock
def test_add_job_by_url_single_job_link_does_not_trigger_listing_flow(client, conn):
    respx.get("http://example.com/job/1").mock(
        return_value=httpx.Response(200, text=_JOB_POSTING_HTML.replace("</body>", "<a href='/apply'>Apply</a></body>"))
    )
    one_link_listing = {"is_listing": True, "job_links": ["http://example.com/job/1"]}
    with patch("app.routes.jobs.run_add_job", side_effect=_fake_run_add_job), \
         patch("app.routes.jobs.detect_listing", return_value=one_link_listing):
        resp = client.post("/jobs/add-by-url", data={"url": "http://example.com/job/1"})

    assert resp.status_code == 200
    assert "Classified as job_posting" in resp.text
    assert len(q.get_jobs(conn)) == 1


def _fake_run_fetch(source, conn, client, model, profile_dir):
    yield f"Starting fetch for '{source['name']}'"
    q.insert_job(conn, source_id=source["id"], url="https://careers.example.com/jobs/1", title="T", company="C", raw_text="r")
    yield "Fetch complete"


def test_add_listing_source_creates_source_and_streams_fetch(client, conn):
    with patch("app.routes.jobs.run_fetch", side_effect=_fake_run_fetch):
        resp = client.post(
            "/jobs/add-listing-source",
            data={"url": "https://careers.example.com/jobs", "name": "careers.example.com"},
        )

    assert resp.status_code == 200
    assert "Fetch complete" in resp.text
    sources = [s for s in q.get_sources(conn) if s["fetcher_type"] == "generic_listing"]
    assert len(sources) == 1
    assert sources[0]["name"] == "careers.example.com"
    assert sources[0]["url"] == "https://careers.example.com/jobs"


def test_add_listing_source_stream_ends_with_html_chunk(client, conn):
    with patch("app.routes.jobs.run_fetch", side_effect=_fake_run_fetch):
        resp = client.post(
            "/jobs/add-listing-source",
            data={"url": "https://careers.example.com/jobs", "name": "careers.example.com"},
        )

    assert resp.status_code == 200
    assert 'HTML:<div class="filter-bar">' in resp.text


def test_job_list_has_add_by_url_form(client, conn):
    resp = client.get("/jobs")
    assert resp.status_code == 200
    assert 'id="add-job-url"' in resp.text
    assert 'data-progress-url="/jobs/add-by-url"' in resp.text
    assert 'data-progress-body-url="#add-job-url"' in resp.text
    assert 'data-progress-target="#jobs-content"' in resp.text


def test_job_list_add_by_url_button_uses_generalized_body_attribute(client, conn):
    resp = client.get("/jobs")
    assert resp.status_code == 200
    assert 'data-progress-body-url="#add-job-url"' in resp.text
    assert 'data-progress-url-input' not in resp.text
