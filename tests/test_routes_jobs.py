from datetime import datetime, timezone, timedelta
from unittest.mock import patch, ANY, MagicMock
import httpx
import pytest
import respx
from app.db import queries as q
from app.fetchers.listing_detect import ListingDetection
from app.pipeline import FetchResult
from app.task_engine import execute_task


def _seed(conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "generic_listing")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/1", title="ML Eng", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="job_posting", summary="Great role")
    scenario_id = q.insert_scenario(conn, "Remote ML", "")
    q.upsert_job_score(conn, jid, scenario_id, 0.9, "Good match", "hash1")
    q.update_job_fit(conn, jid, 0.8, "Strong domain fit", 0.7, "Close match", "phash1")
    q.mark_job_evaluation_complete(conn, jid)
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
    jid = q.insert_job(
        conn, source_id=sid, url="http://finn.no/job/1", title="ML Eng", company="Acme", raw_text="r",
        published_at=published,
    )
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="job_posting", summary="Great role")
    q.mark_job_evaluation_complete(conn, jid)
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


def test_job_list_row_and_expand_both_show_company(client, conn):
    sid, jid, scenario_id = _seed(conn)  # _seed sets company="Acme"
    resp = client.get("/jobs")
    assert '<span class="job-company">Acme</span>' in resp.text
    resp2 = client.get(f"/jobs/{jid}/expand")
    assert "Acme" in resp2.text


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
    assert '<span class="sr-only">Fit score</span>' in resp.text
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
    assert '<h2 class="job-detail-title">ML Eng</h2>' in resp.text


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
    checked_a = (
        f'id="score-tab-{jid}-{scenario_a}" name="active_scenario_id" value="{scenario_a}" '
        f'class="score-tab-input" form="scenario-feedback-form-{jid}" checked'
    )
    checked_b = (
        f'id="score-tab-{jid}-{scenario_b}" name="active_scenario_id" value="{scenario_b}" '
        f'class="score-tab-input" form="scenario-feedback-form-{jid}" checked'
    )
    assert checked_a in resp.text
    assert checked_b not in resp.text


def test_job_expand_shows_explainer_note(client, conn):
    sid, jid, scenario_id = _seed(conn)

    resp = client.get(f"/jobs/{jid}/expand")

    assert resp.status_code == 200
    assert "How well does this job match your search criteria?" in resp.text
    assert "Tuning this helps showing you only relevant new jobs." in resp.text


def test_job_expand_scenario_name_appears_once_per_scenario(client, conn):
    # The scenario-fit header used to render each scenario's name pill twice:
    # once as a static summary pill, once as a separate tab-selector card.
    # The merged pill/tab element should render it exactly once. (The job's
    # "Matched scenarios" meta tag list also shows scenario names elsewhere
    # on the page — unrelated, and out of scope here — so this scopes the
    # count to the score-pill's own markup rather than the raw name text.)
    sid, jid, scenario_a = _seed(conn)
    scenario_b = q.insert_scenario(conn, "Other Scenario", "")
    q.upsert_job_score(conn, jid, scenario_b, 0.4, "weaker fit", "hash2")

    resp = client.get(f"/jobs/{jid}/expand")

    assert resp.status_code == 200
    assert resp.text.count('<span class="tag">Remote ML</span>') == 1
    assert resp.text.count('<span class="tag">Other Scenario</span>') == 1


def test_job_expand_panel_has_scenario_id_and_default_active_class(client, conn):
    sid, jid, scenario_a = _seed(conn)
    scenario_b = q.insert_scenario(conn, "Other Scenario", "")
    q.upsert_job_score(conn, jid, scenario_b, 0.4, "weaker fit", "hash2")

    resp = client.get(f"/jobs/{jid}/expand")

    assert resp.status_code == 200
    assert f'class="score-tab-panel score-tab-panel-active" data-scenario-id="{scenario_a}"' in resp.text
    assert f'class="score-tab-panel" data-scenario-id="{scenario_b}"' in resp.text


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
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "generic_listing")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/2", title="No Fit Yet", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="job_posting", summary="role")
    scenario_id = q.insert_scenario(conn, "A", "")
    q.upsert_job_score(conn, jid, scenario_id, 0.9, "great", "hash1")  # passes gate, no stage-2 run yet
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert "Not yet assessed" in resp.text


def test_job_expand_no_score_box_when_unscored(client, conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "generic_listing")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/2", title="No Score", company="Acme", raw_text="r")
    q.insert_scenario(conn, "First", "")
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert 'class="score-box' not in resp.text


def test_job_expand_shows_pass_as_new_button_when_gate_failed(client, conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "generic_listing")
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
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "generic_listing")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/failed", title="Failed Gate", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="job_posting", summary="role")
    scenario_id = q.insert_scenario(conn, "A", "")
    q.upsert_job_score(conn, jid, scenario_id, 0.2, "too junior", "hash1")
    q.mark_job_gate_override(conn, jid)

    resp = client.get(f"/jobs/{jid}/expand")

    assert resp.status_code == 200
    assert "Pass as new" not in resp.text


def test_job_expand_shows_reevaluate_button_when_new(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert f'data-progress-url="/jobs/{jid}/reevaluate"' in resp.text


def test_job_expand_shows_reevaluate_button_when_accepted(client, conn):
    sid, jid, scenario_id = _seed(conn)
    q.update_job_feedback(conn, jid, "accepted", "")
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert f'data-progress-url="/jobs/{jid}/reevaluate"' in resp.text


def test_job_expand_omits_reevaluate_button_when_rejected(client, conn):
    sid, jid, scenario_id = _seed(conn)
    q.update_job_feedback(conn, jid, "rejected", "")
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert f'data-progress-url="/jobs/{jid}/reevaluate"' not in resp.text


def test_job_expand_omits_reevaluate_button_when_trash(client, conn):
    sid, jid, scenario_id = _seed(conn)
    q.update_job_feedback(conn, jid, "trash", "")
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert f'data-progress-url="/jobs/{jid}/reevaluate"' not in resp.text


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
    assert "Job note" in resp.text
    assert '<textarea name="note" required' not in resp.text


def test_job_expand_reject_trash_buttons_have_tooltips(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert 'title="Does not match your criteria."' in resp.text
    assert 'title="Not a usable posting (expired, spam, duplicate, wrong content)."' in resp.text


def test_job_expand_shows_delete_instead_of_trash_when_already_trash(client, conn):
    sid, jid, scenario_id = _seed(conn)
    q.update_job_feedback(conn, jid, "trash", None)
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert f'hx-get="/jobs/{jid}/delete-confirm"' in resp.text
    assert 'name="status" value="trash"' not in resp.text


def test_job_expand_shows_trash_when_not_trash(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert 'name="status" value="trash"' in resp.text
    assert "delete-confirm" not in resp.text


def test_job_collapse_returns_row_view(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}/collapse")
    assert resp.status_code == 200
    assert f'hx-get="/jobs/{jid}/expand"' in resp.text
    assert f'id="job-{jid}"' in resp.text


def test_job_collapse_of_moved_job_keeps_stale_badge(client, conn):
    sid, jid, scenario_id = _seed(conn)
    q.update_job_feedback(conn, jid, "accepted", None)
    resp = client.get(f"/jobs/{jid}/collapse?status=&content_type=")
    assert resp.status_code == 200
    assert "Moved to Accepted" in resp.text
    assert f'href="/jobs?status=accepted#job-{jid}"' in resp.text


def test_job_expand_of_moved_job_shows_stale_badge(client, conn):
    sid, jid, scenario_id = _seed(conn)
    q.update_job_feedback(conn, jid, "accepted", None)
    resp = client.get(f"/jobs/{jid}/expand?status=&content_type=")
    assert resp.status_code == 200
    assert "Moved to Accepted" in resp.text
    assert f'href="/jobs?status=accepted#job-{jid}"' in resp.text
    # Still the full expanded panel, not the short row.
    assert "feedback-form" in resp.text


def test_job_delete_confirm_renders_confirm_panel(client, conn):
    sid, jid, scenario_id = _seed(conn)
    q.update_job_feedback(conn, jid, "trash", None)
    resp = client.get(f"/jobs/{jid}/delete-confirm")
    assert resp.status_code == 200
    assert "Delete this job?" in resp.text
    assert f'hx-delete="/jobs/{jid}"' in resp.text
    assert f'id="job-{jid}"' in resp.text


def test_job_delete_confirm_404_for_missing_job(client, conn):
    resp = client.get("/jobs/999/delete-confirm")
    assert resp.status_code == 404


def test_job_delete_confirm_rejects_non_trash_job(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}/delete-confirm")
    assert resp.status_code == 400


def test_job_delete_removes_trash_job(client, conn):
    sid, jid, scenario_id = _seed(conn)
    q.update_job_feedback(conn, jid, "trash", None)
    resp = client.delete(f"/jobs/{jid}")
    assert resp.status_code == 200
    assert q.get_job(conn, jid) is None


def test_job_delete_response_includes_updated_trash_count(client, conn):
    sid, jid, scenario_id = _seed(conn)
    q.update_job_feedback(conn, jid, "trash", None)
    resp = client.delete(f"/jobs/{jid}")
    assert resp.status_code == 200
    assert '<span id="count-trash" hx-swap-oob="true">0</span>' in resp.text


def test_job_delete_rejects_non_trash_job(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.delete(f"/jobs/{jid}")
    assert resp.status_code == 400
    assert q.get_job(conn, jid) is not None


def test_job_delete_404_for_missing_job(client, conn):
    resp = client.delete("/jobs/999")
    assert resp.status_code == 404


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


def test_job_reset_enqueues_task(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.post(f"/jobs/{jid}/reset")
    assert resp.status_code == 200
    task = q.get_task(conn, resp.json()["task_id"])
    assert task["kind"] == "job_reset"
    assert task["params"]["job_id"] == jid


def test_job_reset_task_execution_resets_job(conn):
    sid, jid, scenario_id = _seed(conn)
    q.update_job_feedback(conn, jid, "rejected", "not a fit")
    task = q.enqueue_task(conn, kind="job_reset", params={"job_id": jid, "filter_ctx": {}})

    with patch("app.routes.jobs.run_reprocess_job", side_effect=_fake_run_reprocess_job):
        execute_task(conn, MagicMock(), "model", MagicMock(), task)

    fetched = q.get_task(conn, task["id"])
    assert fetched["status"] == "done"
    assert "Reprocessing" in fetched["log"]
    assert "Reset complete" in fetched["log"]
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


def test_job_reset_task_execution_html_chunk_for_updated_row(conn):
    sid, jid, scenario_id = _seed(conn)
    task = q.enqueue_task(conn, kind="job_reset", params={"job_id": jid, "filter_ctx": {}})
    with patch("app.routes.jobs.run_reprocess_job", side_effect=_fake_run_reprocess_job):
        execute_task(conn, MagicMock(), "model", MagicMock(), task)
    fetched = q.get_task(conn, task["id"])
    assert (
        f'<article class="job-row job-row-expanded" id="job-{jid}" style="view-transition-name: job-row-{jid}">'
        in fetched["result"]["html_chunks"][0]
    )


def test_job_pass_as_new_task_execution_html_chunk_for_updated_row(conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "generic_listing")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/failed", title="Failed Gate", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="job_posting", summary="role")
    scenario_id = q.insert_scenario(conn, "A", "")
    q.upsert_job_score(conn, jid, scenario_id, 0.2, "too junior", "hash1")
    task = q.enqueue_task(conn, kind="job_pass_as_new", params={"job_id": jid, "filter_ctx": {}})
    with patch("app.routes.jobs.run_pass_as_new", side_effect=_fake_run_pass_as_new):
        execute_task(conn, MagicMock(), "model", MagicMock(), task)
    fetched = q.get_task(conn, task["id"])
    assert (
        f'<article class="job-row job-row-expanded" id="job-{jid}" style="view-transition-name: job-row-{jid}">'
        in fetched["result"]["html_chunks"][0]
    )


def test_job_reset_task_execution_includes_counts_html_chunk(conn):
    sid, jid, scenario_id = _seed(conn)
    task = q.enqueue_task(conn, kind="job_reset", params={"job_id": jid, "filter_ctx": {}})
    with patch("app.routes.jobs.run_reprocess_job", side_effect=_fake_run_reprocess_job):
        execute_task(conn, MagicMock(), "model", MagicMock(), task)
    fetched = q.get_task(conn, task["id"])
    assert '<span id="count-new" hx-swap-oob="true">' in fetched["result"]["html_chunks"][1]


def test_job_pass_as_new_task_execution_includes_counts_html_chunk(conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "generic_listing")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/failed", title="Failed Gate", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="job_posting", summary="role")
    scenario_id = q.insert_scenario(conn, "A", "")
    q.upsert_job_score(conn, jid, scenario_id, 0.2, "too junior", "hash1")
    task = q.enqueue_task(conn, kind="job_pass_as_new", params={"job_id": jid, "filter_ctx": {}})
    with patch("app.routes.jobs.run_pass_as_new", side_effect=_fake_run_pass_as_new):
        execute_task(conn, MagicMock(), "model", MagicMock(), task)
    fetched = q.get_task(conn, task["id"])
    assert '<span id="count-new" hx-swap-oob="true">' in fetched["result"]["html_chunks"][1]


def test_job_reset_task_execution_with_filter_forwards_it_into_rendered_row(conn):
    sid, jid, scenario_id = _seed(conn)
    q.update_job_feedback(conn, jid, "accepted", "")
    filter_ctx = {"filter_status": "accepted", "filter_content_type": ""}
    task = q.enqueue_task(conn, kind="job_reset", params={"job_id": jid, "filter_ctx": filter_ctx})
    with patch("app.routes.jobs.run_reprocess_job", side_effect=_fake_run_reprocess_job):
        execute_task(conn, MagicMock(), "model", MagicMock(), task)
    fetched = q.get_task(conn, task["id"])
    # Resetting an accepted job always returns it to status "new", so it falls out of
    # the Accepted tab -> renders as the stale short row, whose own expand link must
    # still carry the filter forward.
    assert f'jobs/{jid}/expand?status=accepted&content_type=' in fetched["result"]["html_chunks"][0]


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


def test_job_reset_task_execution_from_not_relevant_tab_shows_moved_to_new_badge(conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "generic_listing")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/1", title="Filtered Out", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="job_posting", summary="role")
    scenario_id = q.insert_scenario(conn, "A", "")  # default gate_threshold 0.7
    q.upsert_job_score(conn, jid, scenario_id, 0.3, "not a fit", "hash1")  # gate-failed -> "Not relevant" tab

    filter_ctx = {"filter_status": "not_relevant", "filter_content_type": ""}
    task = q.enqueue_task(conn, kind="job_reset", params={"job_id": jid, "filter_ctx": filter_ctx})
    with patch("app.routes.jobs.run_reprocess_job", side_effect=_fake_run_reprocess_job_to_passing):
        execute_task(conn, MagicMock(), "model", MagicMock(), task)

    fetched = q.get_task(conn, task["id"])
    html = fetched["result"]["html_chunks"][0]
    assert "Moved to New" in html
    assert f'href="/jobs#job-{jid}"' in html


def test_job_reset_task_execution_that_stays_in_current_filter_shows_no_badge(conn):
    sid, jid, scenario_id = _seed(conn)  # already gate-passed, status "new"
    filter_ctx = {"filter_status": None, "filter_content_type": None}
    task = q.enqueue_task(conn, kind="job_reset", params={"job_id": jid, "filter_ctx": filter_ctx})
    with patch("app.routes.jobs.run_reprocess_job", side_effect=_fake_run_reprocess_job):
        execute_task(conn, MagicMock(), "model", MagicMock(), task)
    fetched = q.get_task(conn, task["id"])
    assert "Moved to" not in fetched["result"]["html_chunks"][0]


def test_job_reset_task_execution_without_filter_shows_no_badge(conn):
    # Simulates the standalone /jobs/{id} page, which never sends filter params.
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "generic_listing")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/1", title="Filtered Out", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="job_posting", summary="role")
    scenario_id = q.insert_scenario(conn, "A", "")
    q.upsert_job_score(conn, jid, scenario_id, 0.3, "not a fit", "hash1")

    task = q.enqueue_task(conn, kind="job_reset", params={"job_id": jid, "filter_ctx": {}})
    with patch("app.routes.jobs.run_reprocess_job", side_effect=_fake_run_reprocess_job_to_passing):
        execute_task(conn, MagicMock(), "model", MagicMock(), task)

    fetched = q.get_task(conn, task["id"])
    assert "Moved to" not in fetched["result"]["html_chunks"][0]


def test_job_pass_as_new_task_execution_shows_moved_to_new_badge(conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "generic_listing")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/failed", title="Failed Gate", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="job_posting", summary="role")
    scenario_id = q.insert_scenario(conn, "A", "")
    q.upsert_job_score(conn, jid, scenario_id, 0.2, "too junior", "hash1")

    filter_ctx = {"filter_status": "not_relevant", "filter_content_type": ""}
    task = q.enqueue_task(conn, kind="job_pass_as_new", params={"job_id": jid, "filter_ctx": filter_ctx})
    with patch("app.routes.jobs.run_pass_as_new", side_effect=_fake_run_pass_as_new):
        execute_task(conn, MagicMock(), "model", MagicMock(), task)

    fetched = q.get_task(conn, task["id"])
    assert "Moved to New" in fetched["result"]["html_chunks"][0]


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


def test_bulk_reset_enqueues_task_with_job_ids(client, conn):
    sid, j1, scenario_id = _seed(conn)
    j2 = q.insert_job(conn, source_id=sid, url="http://finn.no/job/2", title="Data Eng", company="Acme", raw_text="r")
    resp = client.post("/jobs/bulk-reset", data={"job_ids": [j1, j2]})
    assert resp.status_code == 200
    task = q.get_task(conn, resp.json()["task_id"])
    assert task["kind"] == "jobs_bulk_reset"
    assert task["params"]["job_ids"] == [j1, j2]


def test_bulk_reset_task_execution_resets_each_job(conn):
    sid, j1, scenario_id = _seed(conn)
    j2 = q.insert_job(conn, source_id=sid, url="http://finn.no/job/2", title="Data Eng", company="Acme", raw_text="r")
    q.update_job_feedback(conn, j1, "rejected", "note")
    q.update_job_feedback(conn, j2, "trash", "note")

    task = q.enqueue_task(conn, kind="jobs_bulk_reset", params={"job_ids": [j1, j2], "filter_ctx": {}})
    with patch("app.routes.jobs.run_reprocess_job", side_effect=_fake_run_reprocess_job):
        execute_task(conn, MagicMock(), "model", MagicMock(), task)

    fetched = q.get_task(conn, task["id"])
    assert fetched["status"] == "done"
    assert fetched["log"].count("Reprocessing") == 2
    assert q.get_job(conn, j1)["status"] == "new"
    assert q.get_job(conn, j2)["status"] == "new"


def test_bulk_reset_task_execution_produces_chunk_per_job_plus_counts(conn):
    sid, j1, scenario_id = _seed(conn)
    j2 = q.insert_job(conn, source_id=sid, url="http://finn.no/job/2", title="Data Eng", company="Acme", raw_text="r")
    q.update_job_feedback(conn, j1, "rejected", "note")
    q.update_job_feedback(conn, j2, "trash", "note")

    task = q.enqueue_task(conn, kind="jobs_bulk_reset", params={"job_ids": [j1, j2], "filter_ctx": {}})
    with patch("app.routes.jobs.run_reprocess_job", side_effect=_fake_run_reprocess_job):
        execute_task(conn, MagicMock(), "model", MagicMock(), task)

    fetched = q.get_task(conn, task["id"])
    chunks = fetched["result"]["html_chunks"]
    assert len(chunks) == 3  # 2 job rows + counts
    assert f'<article class="job-row job-row-expanded" id="job-{j1}" style="view-transition-name: job-row-{j1}">' in chunks[0]
    assert f'<article class="job-row job-row-expanded" id="job-{j2}" style="view-transition-name: job-row-{j2}">' in chunks[1]
    assert '<span id="count-new" hx-swap-oob="true">' in chunks[2]


def test_bulk_reset_task_execution_with_filter_shows_moved_marker_per_job(conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "generic_listing")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/1", title="Filtered Out", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="job_posting", summary="role")
    scenario_id = q.insert_scenario(conn, "A", "")  # default gate_threshold 0.7
    q.upsert_job_score(conn, jid, scenario_id, 0.3, "not a fit", "hash1")  # gate-failed -> "Not relevant" tab

    filter_ctx = {"filter_status": "not_relevant", "filter_content_type": ""}
    task = q.enqueue_task(conn, kind="jobs_bulk_reset", params={"job_ids": [jid], "filter_ctx": filter_ctx})
    with patch("app.routes.jobs.run_reprocess_job", side_effect=_fake_run_reprocess_job_to_passing):
        execute_task(conn, MagicMock(), "model", MagicMock(), task)

    fetched = q.get_task(conn, task["id"])
    assert "Moved to New" in fetched["result"]["html_chunks"][0]


def test_job_bulk_reset_button_has_progress_oob_and_filter_query(client, conn):
    resp = client.get("/jobs?status=accepted")
    assert resp.status_code == 200
    assert 'data-progress-url="/jobs/bulk-reset?status=accepted&content_type="' in resp.text
    assert 'data-progress-oob="1"' in resp.text


def test_job_bulk_reevaluate_button_has_progress_oob_and_filter_query(client, conn):
    resp = client.get("/jobs?status=accepted")
    assert resp.status_code == 200
    assert 'data-progress-url="/jobs/bulk-reevaluate?status=accepted&content_type="' in resp.text
    assert 'data-progress-jobs' in resp.text

def test_bulk_reevaluate_enqueues_task_with_job_ids(client, conn):
    j1 = _seed(conn)[1]
    resp = client.post("/jobs/bulk-reevaluate", data={"job_ids": [j1]})
    assert resp.status_code == 200
    task = q.get_task(conn, resp.json()["task_id"])
    assert task["kind"] == "jobs_bulk_reevaluate"
    assert task["params"]["job_ids"] == [j1]


def test_bulk_reevaluate_task_execution_updates_scores_for_each_job(conn):
    sid, j1, scenario_id = _seed(conn)
    j2 = q.insert_job(conn, source_id=sid, url="http://finn.no/job/2", title="Data Eng", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, j2, simplified_content="clean", content_type="job_posting", summary="role")
    q.upsert_job_score(conn, j2, scenario_id, 0.4, "reason", "hash1")

    task = q.enqueue_task(conn, kind="jobs_bulk_reevaluate", params={"job_ids": [j1, j2], "filter_ctx": {}})
    with patch("app.routes.jobs.run_reevaluate_job", side_effect=_fake_run_reevaluate_job):
        execute_task(conn, MagicMock(), "model", MagicMock(), task)

    fetched = q.get_task(conn, task["id"])
    assert fetched["status"] == "done"
    assert fetched["log"].count("Scored 0.85") == 2
    assert "Re-evaluation complete: 2 job(s)" in fetched["log"]


def test_bulk_reevaluate_task_execution_produces_chunk_per_job_plus_counts(conn):
    sid, j1, scenario_id = _seed(conn)
    j2 = q.insert_job(conn, source_id=sid, url="http://finn.no/job/2", title="Data Eng", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, j2, simplified_content="clean", content_type="job_posting", summary="role")
    q.upsert_job_score(conn, j2, scenario_id, 0.4, "reason", "hash1")

    task = q.enqueue_task(conn, kind="jobs_bulk_reevaluate", params={"job_ids": [j1, j2], "filter_ctx": {}})
    with patch("app.routes.jobs.run_reevaluate_job", side_effect=_fake_run_reevaluate_job):
        execute_task(conn, MagicMock(), "model", MagicMock(), task)

    fetched = q.get_task(conn, task["id"])
    chunks = fetched["result"]["html_chunks"]
    assert len(chunks) == 3  # 2 job rows + counts
    assert f'<article class="job-row job-row-expanded" id="job-{j1}" style="view-transition-name: job-row-{j1}">' in chunks[0]
    assert f'<article class="job-row job-row-expanded" id="job-{j2}" style="view-transition-name: job-row-{j2}">' in chunks[1]
    assert '<span id="count-new" hx-swap-oob="true">' in chunks[2]


def test_bulk_reevaluate_task_execution_preserves_status(conn):
    sid, j1, scenario_id = _seed(conn)
    q.update_job_feedback(conn, j1, "accepted", "good fit")

    task = q.enqueue_task(conn, kind="jobs_bulk_reevaluate", params={"job_ids": [j1], "filter_ctx": {}})
    with patch("app.routes.jobs.run_reevaluate_job", side_effect=_fake_run_reevaluate_job):
        execute_task(conn, MagicMock(), "model", MagicMock(), task)

    assert q.get_job(conn, j1)["status"] == "accepted"


def _fake_run_pass_as_new(conn, client, model, job, profile):
    yield f"Bypassing gate threshold: {job['url']}"
    q.mark_job_gate_override(conn, job["id"])
    yield f"Fit 0.80/0.60: {job['url']}"


def test_job_pass_as_new_enqueues_task(client, conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "generic_listing")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/failed", title="Failed Gate", company="Acme", raw_text="r")
    resp = client.post(f"/jobs/{jid}/pass-as-new")
    assert resp.status_code == 200
    task = q.get_task(conn, resp.json()["task_id"])
    assert task["kind"] == "job_pass_as_new"
    assert task["params"]["job_id"] == jid


def test_job_pass_as_new_task_execution_marks_override(conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "generic_listing")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/failed", title="Failed Gate", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="job_posting", summary="role")
    scenario_id = q.insert_scenario(conn, "A", "")  # default gate_threshold 0.7
    q.upsert_job_score(conn, jid, scenario_id, 0.2, "too junior", "hash1")

    task = q.enqueue_task(conn, kind="job_pass_as_new", params={"job_id": jid, "filter_ctx": {}})
    with patch("app.routes.jobs.run_pass_as_new", side_effect=_fake_run_pass_as_new):
        execute_task(conn, MagicMock(), "model", MagicMock(), task)

    fetched = q.get_task(conn, task["id"])
    assert fetched["status"] == "done"
    assert "Bypassing gate threshold" in fetched["log"]
    assert q.get_job(conn, jid)["gate_override"] == 1


def test_job_pass_as_new_unknown_job_returns_404(client, conn):
    resp = client.post("/jobs/999/pass-as-new")
    assert resp.status_code == 404


def _fake_run_reevaluate_job(conn, client, model, job, scenarios, profile, progress_prefix=""):
    yield f"{progress_prefix}Scored 0.85 for 'Remote ML': {job['url']}"
    q.upsert_job_score(conn, job["id"], scenarios[0]["id"], 0.85, "now a match", "hash-new")
    yield f"{progress_prefix}Fit 0.75/0.65: {job['url']}"
    q.update_job_fit(conn, job["id"], 0.75, "strong interest", 0.65, "reachable", "phash-new")


def test_job_reevaluate_enqueues_task(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.post(f"/jobs/{jid}/reevaluate")
    assert resp.status_code == 200
    task = q.get_task(conn, resp.json()["task_id"])
    assert task["kind"] == "job_reevaluate"
    assert task["params"]["job_id"] == jid


def test_job_reevaluate_task_execution_updates_scores(conn):
    sid, jid, scenario_id = _seed(conn)
    q.update_job_feedback(conn, jid, "accepted", "good fit")

    task = q.enqueue_task(conn, kind="job_reevaluate", params={"job_id": jid, "filter_ctx": {}})
    with patch("app.routes.jobs.run_reevaluate_job", side_effect=_fake_run_reevaluate_job):
        execute_task(conn, MagicMock(), "model", MagicMock(), task)

    fetched = q.get_task(conn, task["id"])
    assert fetched["status"] == "done"
    assert "Scored 0.85" in fetched["log"]
    assert "Fit 0.75/0.65" in fetched["log"]
    assert q.get_job(conn, jid)["status"] == "accepted"


def test_job_reevaluate_unknown_job_returns_404(client, conn):
    resp = client.post("/jobs/999/reevaluate")
    assert resp.status_code == 404


def test_job_reevaluate_task_execution_html_chunk_for_updated_row(conn):
    sid, jid, scenario_id = _seed(conn)
    task = q.enqueue_task(conn, kind="job_reevaluate", params={"job_id": jid, "filter_ctx": {}})
    with patch("app.routes.jobs.run_reevaluate_job", side_effect=_fake_run_reevaluate_job):
        execute_task(conn, MagicMock(), "model", MagicMock(), task)
    fetched = q.get_task(conn, task["id"])
    assert (
        f'<article class="job-row job-row-expanded" id="job-{jid}" style="view-transition-name: job-row-{jid}">'
        in fetched["result"]["html_chunks"][0]
    )


def test_job_reevaluate_task_execution_includes_counts_html_chunk(conn):
    sid, jid, scenario_id = _seed(conn)
    task = q.enqueue_task(conn, kind="job_reevaluate", params={"job_id": jid, "filter_ctx": {}})
    with patch("app.routes.jobs.run_reevaluate_job", side_effect=_fake_run_reevaluate_job):
        execute_task(conn, MagicMock(), "model", MagicMock(), task)
    fetched = q.get_task(conn, task["id"])
    assert '<span id="count-new" hx-swap-oob="true">' in fetched["result"]["html_chunks"][1]


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


def test_job_bulk_feedback_keeps_moved_job_in_its_original_sort_position(client, conn):
    # Three jobs in fit-score order (high to low): A, B, C. Bulk-accepting the
    # middle one (B) must not banish it to the end of the list — it should
    # still land between A and C, exactly where a single-job action would
    # have left it in place.
    sid, jid_a, scenario_id = _seed(conn)  # fit_score 0.8, gate-passing relevance 0.9
    jid_b = q.insert_job(conn, source_id=sid, url="http://finn.no/job/2", title="Job B", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, jid_b, simplified_content="clean", content_type="job_posting", summary="ok")
    q.upsert_job_score(conn, jid_b, scenario_id, 0.9, "ok", "hashb")  # gate-passing relevance
    q.update_job_fit(conn, jid_b, 0.6, "ok fit", 0.6, "ok", "phashb")
    q.mark_job_evaluation_complete(conn, jid_b)
    jid_c = q.insert_job(conn, source_id=sid, url="http://finn.no/job/3", title="Job C", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, jid_c, simplified_content="clean", content_type="job_posting", summary="ok")
    q.upsert_job_score(conn, jid_c, scenario_id, 0.9, "ok", "hashc")  # gate-passing relevance
    q.update_job_fit(conn, jid_c, 0.4, "ok fit", 0.4, "ok", "phashc")
    q.mark_job_evaluation_complete(conn, jid_c)

    resp = client.post(
        "/jobs/bulk-feedback",
        data={"job_ids": [jid_b], "status": "accepted", "status_filter": "", "content_type_filter": ""},
    )
    assert resp.status_code == 200
    pos_a = resp.text.index(f'id="job-{jid_a}"')
    pos_b = resp.text.index(f'id="job-{jid_b}"')
    pos_c = resp.text.index(f'id="job-{jid_c}"')
    assert pos_a < pos_b < pos_c
    assert "Moved to Accepted" in resp.text


def test_job_bulk_feedback_shows_no_jobs_found_when_nothing_matches_or_moved(client, conn):
    # job_ids references a nonexistent job, so nothing lands in either jobs or stale_jobs.
    resp = client.post(
        "/jobs/bulk-feedback",
        data={"job_ids": [999], "status": "rejected", "status_filter": "", "content_type_filter": ""},
    )
    assert resp.status_code == 200
    assert "No jobs found" in resp.text


def test_job_bulk_delete_confirm_shows_count(client, conn):
    sid, j1, scenario_id = _seed(conn)
    j2 = q.insert_job(conn, source_id=sid, url="http://finn.no/job/2", title="Data Eng", company="Acme", raw_text="r")
    resp = client.post("/jobs/bulk-delete-confirm", data={"job_ids": [j1, j2]})
    assert resp.status_code == 200
    assert "Delete 2 selected jobs? This cannot be undone." in resp.text


def test_job_bulk_delete_confirm_singular_for_one_job(client, conn):
    sid, j1, scenario_id = _seed(conn)
    resp = client.post("/jobs/bulk-delete-confirm", data={"job_ids": [j1]})
    assert resp.status_code == 200
    assert "Delete 1 selected job? This cannot be undone." in resp.text


def test_job_bulk_delete_removes_only_trash_jobs(client, conn):
    sid, j1, scenario_id = _seed(conn)
    q.update_job_feedback(conn, j1, "trash", None)
    j2 = q.insert_job(conn, source_id=sid, url="http://finn.no/job/2", title="Data Eng", company="Acme", raw_text="r")
    resp = client.post(
        "/jobs/bulk-delete",
        data={"job_ids": [j1, j2], "status_filter": "trash", "content_type_filter": "", "source_id_filter": ""},
    )
    assert resp.status_code == 200
    assert q.get_job(conn, j1) is None
    assert q.get_job(conn, j2) is not None


def test_job_bulk_delete_response_reflects_remaining_trash_jobs(client, conn):
    sid, j1, scenario_id = _seed(conn)
    q.update_job_feedback(conn, j1, "trash", None)
    resp = client.post(
        "/jobs/bulk-delete",
        data={"job_ids": [j1], "status_filter": "trash", "content_type_filter": "", "source_id_filter": ""},
    )
    assert resp.status_code == 200
    assert "No jobs found" in resp.text


def test_job_bulk_actions_cancel_shows_trash_outside_trash_view(client, conn):
    resp = client.post("/jobs/bulk-actions-cancel", data={"status_filter": ""})
    assert resp.status_code == 200
    assert 'name="status" value="trash"' in resp.text
    assert "bulk-delete-confirm" not in resp.text


def test_job_bulk_actions_cancel_shows_delete_in_trash_view(client, conn):
    resp = client.post("/jobs/bulk-actions-cancel", data={"status_filter": "trash"})
    assert resp.status_code == 200
    assert 'hx-post="/jobs/bulk-delete-confirm"' in resp.text
    assert 'name="status" value="trash"' not in resp.text


def test_job_list_bulk_bar_shows_delete_in_trash_view(client, conn):
    sid, jid, scenario_id = _seed(conn)
    q.update_job_feedback(conn, jid, "trash", None)
    resp = client.get("/jobs?status=trash")
    assert resp.status_code == 200
    assert 'hx-post="/jobs/bulk-delete-confirm"' in resp.text


def test_job_list_bulk_bar_shows_trash_outside_trash_view(client, conn):
    resp = client.get("/jobs")
    assert resp.status_code == 200
    assert 'name="status" value="trash"' in resp.text
    assert 'hx-post="/jobs/bulk-delete-confirm"' not in resp.text


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
    assert 'class="score-box score-compare score-compare-open"' in resp.text
    assert 'aria-pressed="true"' in resp.text
    assert "All feedback saved" in resp.text


def test_scenario_feedback_keeps_non_default_tab_active_after_save(client, conn):
    # Submitting feedback re-renders from the scenario the user had open
    # (via the submitted active_scenario_id), not always the top-passed one
    # — otherwise the response looks like it switched to a different,
    # possibly-empty panel, which reads as "my input got cleared".
    sid, jid, scenario_a = _seed(conn)
    scenario_b = q.insert_scenario(conn, "Other Scenario", "")
    q.upsert_job_score(conn, jid, scenario_b, 0.4, "weaker fit", "hash2")

    resp = client.post(
        f"/jobs/{jid}/scenario-feedback",
        data={
            "scenario_id": [scenario_a, scenario_b],
            "note": ["", "not close at all"],
            "direction": ["", "lower"],
            "active_scenario_id": [scenario_b],
        },
    )

    assert resp.status_code == 200
    assert f'class="score-tab-panel score-tab-panel-active" data-scenario-id="{scenario_b}"' in resp.text
    assert f'class="score-tab-panel" data-scenario-id="{scenario_a}"' in resp.text
    assert (
        f'id="score-tab-{jid}-{scenario_b}" name="active_scenario_id" value="{scenario_b}" '
        f'class="score-tab-input" form="scenario-feedback-form-{jid}" checked' in resp.text
    )


def test_job_expand_save_all_feedback_button_is_visually_primary(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert '<button type="submit" class="btn btn-primary">Send collected feedback for this job</button>' in resp.text


def test_scenario_feedback_keeps_note_textarea_populated_after_save(client, conn):
    # The "All feedback saved" banner is the send confirmation now — clearing
    # the textarea on top of that just makes it look like the note didn't stick.
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
    assert resp.text.count("Send collected feedback for this job") == 1
    assert resp.text.count(f'hx-post="/jobs/{jid}/scenario-feedback"') == 1


def test_job_list_has_persistent_bulk_form_shell(client, conn):
    resp = client.get("/jobs")
    assert resp.status_code == 200
    assert '<form id="bulk-form" hx-post="/jobs/bulk-feedback" hx-target="#jobs-content" hx-swap="innerHTML">' in resp.text
    # The default "New" view has no explicit status filter (it's the
    # gate-passed default), so the hidden field must carry that same
    # unfiltered semantics — not a literal "new" status, which would apply
    # gate-agnostic filtering and diverge from what's actually shown.
    assert '<input type="hidden" name="status_filter" value="">' in resp.text


def test_job_list_bulk_form_shell_carries_explicit_filter(client, conn):
    resp = client.get("/jobs?status=accepted")
    assert resp.status_code == 200
    assert '<input type="hidden" name="status_filter" value="accepted">' in resp.text


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
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "generic_listing")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/1", title="Filtered Out", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="job_posting", summary="role")
    scenario_id = q.insert_scenario(conn, "A", "")  # default gate_threshold 0.7
    q.upsert_job_score(conn, jid, scenario_id, 0.3, "not a fit", "hash1")
    q.mark_job_evaluation_complete(conn, jid)

    resp = client.get("/jobs")
    assert "Filtered Out" not in resp.text

    resp2 = client.get("/jobs?status=not_relevant")
    assert "Filtered Out" in resp2.text


def test_job_list_not_relevant_tab_excludes_leads(client, conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "generic_listing")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/1", title="Low Score Lead", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="lead", summary="role")
    scenario_id = q.insert_scenario(conn, "A", "")  # default gate_threshold 0.7
    q.upsert_job_score(conn, jid, scenario_id, 0.3, "not a fit", "hash1")

    resp = client.get("/jobs?status=not_relevant")
    assert "Low Score Lead" not in resp.text


def test_job_list_leads_tab_includes_gate_failed_leads(client, conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "generic_listing")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/1", title="Low Score Lead", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="lead", summary="role")
    scenario_id = q.insert_scenario(conn, "A", "")  # default gate_threshold 0.7
    q.upsert_job_score(conn, jid, scenario_id, 0.3, "not a fit", "hash1")

    resp = client.get("/jobs?content_type=lead")
    assert "Low Score Lead" in resp.text


def test_job_list_leads_tab_excludes_accepted_and_rejected_leads(client, conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "generic_listing")
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
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "generic_listing")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/1", title="Low Score Accepted", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="job_posting", summary="role")
    scenario_id = q.insert_scenario(conn, "A", "")  # default gate_threshold 0.7
    q.upsert_job_score(conn, jid, scenario_id, 0.3, "not a fit", "hash1")
    q.update_job_feedback(conn, jid, "accepted", "")

    resp = client.get("/jobs?status=accepted")
    assert "Low Score Accepted" in resp.text


def test_job_list_shows_status_badge_for_accepted_rejected_trash(client, conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "generic_listing")

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
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "generic_listing")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/1", title="Filtered Out", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="job_posting", summary="role")
    scenario_id = q.insert_scenario(conn, "A", "")  # default gate_threshold 0.7
    q.upsert_job_score(conn, jid, scenario_id, 0.3, "not a fit", "hash1")
    q.mark_job_evaluation_complete(conn, jid)

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
    assert f'href="/jobs/{jid}" class="job-link-icon"' in resp.text


def test_job_expand_has_permalink_icon(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert f'href="/jobs/{jid}" class="job-link-icon"' in resp.text


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
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "generic_listing")
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


def _fake_run_add_job_lead(conn, client, model, source_id, url, raw_text):
    jid = q.insert_job(conn, source_id=source_id, url=url, title="Fake Lead", company="", raw_text=raw_text)
    q.update_job_pipeline(conn, jid, simplified_content=raw_text, content_type="lead", summary="Maybe hiring")
    yield f"Classified as lead: {url}"


_NOT_A_LISTING = {"is_listing": False, "job_links": []}


def _not_listing(html=_JOB_POSTING_HTML):
    return ListingDetection(html, False, [], False)


def _listing(job_links, html="<html><body>listing</body></html>"):
    return ListingDetection(html, True, list(job_links), False)


def _run_add_by_url(conn, url, filter_ctx=None, status=None, content_type=None):
    task = q.enqueue_task(conn, kind="job_add_by_url", params={
        "url": url, "status": status, "content_type": content_type, "filter_ctx": filter_ctx or {},
    })
    execute_task(conn, MagicMock(), "model", MagicMock(browser_profile_dir="/tmp"), task)
    return q.get_task(conn, task["id"])


def _run_add_listing_source(conn, url, name, fetcher_type, status=None, content_type=None):
    task = q.enqueue_task(conn, kind="job_add_listing_source", params={
        "url": url, "name": name, "fetcher_type": fetcher_type, "status": status, "content_type": content_type,
    })
    execute_task(conn, MagicMock(), "model", MagicMock(browser_profile_dir="/tmp"), task)
    return q.get_task(conn, task["id"])


def test_add_by_url_enqueues_task(client, conn):
    resp = client.post("/jobs/add-by-url", data={"url": "https://example.com/job/1"})
    assert resp.status_code == 200
    task = q.get_task(conn, resp.json()["task_id"])
    assert task["kind"] == "job_add_by_url"
    assert task["params"]["url"] == "https://example.com/job/1"


@respx.mock
def test_add_job_by_url_success_inserts_job_and_streams_progress(conn):
    with patch("app.routes.jobs.run_add_job", side_effect=_fake_run_add_job), \
         patch("app.routes.jobs.detect_listing_page", return_value=_not_listing()):
        fetched = _run_add_by_url(conn, "http://example.com/job/1")
    assert "Classified as job_posting" in fetched["log"]
    jobs = q.get_jobs(conn)
    assert len(jobs) == 1
    assert jobs[0]["url"] == "http://example.com/job/1"
    source = q.get_source(conn, jobs[0]["source_id"])
    assert source["fetcher_type"] == "manual"
    # No scenarios configured, so this job_posting can't pass any gate — lands on
    # the "Not relevant" tab rather than the default view. Still gets a notice.
    assert fetched["result"]["notices"]
    assert f'href="/jobs?status=not_relevant#job-{jobs[0]["id"]}"' in fetched["result"]["notices"][0]["html"]


def _fake_run_add_job_passed_gate(conn, client, model, source_id, url, raw_text):
    jid = q.insert_job(conn, source_id=source_id, url=url, title="Fake Title", company="Acme", raw_text=raw_text)
    q.update_job_pipeline(conn, jid, simplified_content=raw_text, content_type="job_posting", summary="A role")
    scenario_id = q.insert_scenario(conn, "Remote ML", "")
    q.upsert_job_score(conn, jid, scenario_id, 0.9, "Good match", "hash1")
    yield f"Classified as job_posting: {url}"


def _fake_run_add_job_error(conn, client, model, source_id, url, raw_text):
    jid = q.insert_job(conn, source_id=source_id, url=url, title="", company="", raw_text=raw_text)
    q.update_job_pipeline(conn, jid, simplified_content=raw_text, content_type="error")
    yield f"Classified as error: {url}"


def _fake_run_add_job_irrelevant(conn, client, model, source_id, url, raw_text):
    jid = q.insert_job(conn, source_id=source_id, url=url, title="", company="", raw_text=raw_text)
    yield f"Classified as irrelevant: {url}"
    q.delete_job(conn, jid)


@respx.mock
def test_add_job_by_url_job_posting_passed_gate_shows_notice(conn):
    with patch("app.routes.jobs.run_add_job", side_effect=_fake_run_add_job_passed_gate), \
         patch("app.routes.jobs.detect_listing_page", return_value=_not_listing()):
        fetched = _run_add_by_url(conn, "http://example.com/job/1")
    job = q.get_job_by_url(conn, "http://example.com/job/1")
    assert fetched["result"]["notices"]
    assert f'href="/jobs#job-{job["id"]}"' in fetched["result"]["notices"][0]["html"]


@respx.mock
def test_add_job_by_url_error_shows_persistent_notice(conn):
    with patch("app.routes.jobs.run_add_job", side_effect=_fake_run_add_job_error), \
         patch("app.routes.jobs.detect_listing_page", return_value=_not_listing()):
        fetched = _run_add_by_url(conn, "http://example.com/job/1")
    job = q.get_job_by_url(conn, "http://example.com/job/1")
    assert job["content_type"] == "error"
    notice = fetched["result"]["notices"][0]
    assert notice["level"] == "warning"
    assert f'href="/jobs/{job["id"]}"' in notice["html"]


@respx.mock
def test_add_job_by_url_irrelevant_shows_discarded_notice(conn):
    with patch("app.routes.jobs.run_add_job", side_effect=_fake_run_add_job_irrelevant), \
         patch("app.routes.jobs.detect_listing_page", return_value=_not_listing()):
        fetched = _run_add_by_url(conn, "http://example.com/job/1")
    assert q.get_job_by_url(conn, "http://example.com/job/1") is None
    assert fetched["result"]["notices"]
    assert "discarded" in fetched["result"]["notices"][0]["html"]


@respx.mock
def test_add_job_by_url_lead_shows_persistent_notice_with_link(conn):
    with patch("app.routes.jobs.run_add_job", side_effect=_fake_run_add_job_lead), \
         patch("app.routes.jobs.detect_listing_page", return_value=_not_listing()):
        fetched = _run_add_by_url(conn, "http://example.com/job/1")
    assert fetched["result"]["notices"]
    job = q.get_job_by_url(conn, "http://example.com/job/1")
    assert job["content_type"] == "lead"
    assert f'href="/jobs?content_type=lead#job-{job["id"]}"' in fetched["result"]["notices"][0]["html"]


@respx.mock
def test_add_job_by_url_stream_ends_with_single_html_chunk(conn):
    with patch("app.routes.jobs.run_add_job", side_effect=_fake_run_add_job), \
         patch("app.routes.jobs.detect_listing_page", return_value=_not_listing()):
        fetched = _run_add_by_url(conn, "http://example.com/job/1")
    # Only one HTML chunk should come back — the target-mode swap in base.html's
    # polling JS uses only the *last* html_chunks entry as the replacement innerHTML, so a
    # second trailing chunk (e.g. a separate counts_oob fragment) would silently clobber
    # the real content instead of updating it. _content.html's filter-bar already carries
    # fresh counts, so no second chunk is needed.
    chunks = fetched["result"]["html_chunks"]
    assert len(chunks) == 1
    assert '<div class="filter-bar">' in chunks[0]
    assert 'id="count-new"' in chunks[0]


@respx.mock
def test_add_job_by_url_duplicate_url_does_not_insert(conn):
    sid = q.insert_source(conn, "s", "http://x", "generic_listing")
    jid = q.insert_job(conn, source_id=sid, url="http://example.com/job/1", title="T", company="C", raw_text="r")

    fetched = _run_add_by_url(conn, "http://example.com/job/1")

    notice_html = fetched["result"]["notices"][0]["html"]
    assert "Already tracked" in notice_html
    assert f"/jobs/{jid}" in notice_html
    assert len(q.get_jobs(conn)) == 1


def test_add_job_by_url_duplicate_url_shows_persistent_link_to_job(conn):
    sid = q.insert_source(conn, "s", "http://x", "generic_listing")
    jid = q.insert_job(conn, source_id=sid, url="http://example.com/job/1", title="T", company="C", raw_text="r")

    fetched = _run_add_by_url(conn, "http://example.com/job/1")

    assert f'<a href="/jobs/{jid}">View this job</a>' in fetched["result"]["notices"][0]["html"]


def test_add_job_by_url_existing_source_url_shows_link_to_source(conn):
    sid = q.insert_source(conn, "Careers Page", "https://careers.example.com/jobs", "generic_listing")

    fetched = _run_add_by_url(conn, "https://careers.example.com/jobs")

    notice_html = fetched["result"]["notices"][0]["html"]
    assert "Already tracked as a source" in notice_html
    assert f'<a href="/sources#source-row-{sid}">' in notice_html
    assert "Careers Page" in notice_html
    assert q.get_jobs(conn) == []


def test_add_job_by_url_duplicate_error_job_points_to_trash(conn):
    sid = q.insert_source(conn, "Manual", "", "manual")
    jid = q.insert_job(conn, source_id=sid, url="http://example.com/broken", title="http://example.com/broken", company="", raw_text="")
    q.update_job_pipeline(conn, jid, simplified_content="", content_type="error")
    q.update_job_feedback(conn, jid, "trash", "Failed to fetch: HTTP 404")

    fetched = _run_add_by_url(conn, "http://example.com/broken")

    notice = fetched["result"]["notices"][0]
    assert notice["level"] == "warning"
    assert "recorded as an error" in notice["html"]
    assert f'<a href="/jobs/{jid}">view it</a>' in notice["html"]
    assert '<a href="/jobs?status=trash">Trash</a>' in notice["html"]
    assert len(q.get_jobs(conn)) == 1


@respx.mock
def test_add_job_by_url_fetch_failure_inserts_error_job(conn):
    respx.get("http://example.com/broken").mock(return_value=httpx.Response(404))

    fetched = _run_add_by_url(conn, "http://example.com/broken")

    notice = fetched["result"]["notices"][0]
    assert notice["level"] == "warning"
    assert "Couldn't add" in notice["html"]
    assert "HTTP 404" in notice["html"]
    jobs = q.get_jobs(conn)
    assert len(jobs) == 1
    assert jobs[0]["content_type"] == "error"
    assert jobs[0]["status"] == "trash"
    assert jobs[0]["url"] == "http://example.com/broken"


@respx.mock
def test_add_job_by_url_fetch_network_error_inserts_error_job(conn):
    respx.get("http://example.com/unreachable").mock(side_effect=httpx.ConnectError("boom"))

    fetched = _run_add_by_url(conn, "http://example.com/unreachable")

    notice = fetched["result"]["notices"][0]
    assert notice["level"] == "warning"
    assert "Couldn't add" in notice["html"]
    jobs = q.get_jobs(conn)
    assert len(jobs) == 1
    assert jobs[0]["content_type"] == "error"
    assert jobs[0]["status"] == "trash"


@respx.mock
def test_add_job_by_url_js_only_page_shows_no_content_notice(conn):
    # Real shell HTML from a client-side-rendered job board (Ashby): 200 OK, but the
    # only text present is a noscript-style placeholder — no real posting content.
    js_shell_html = (
        "<html><body>"
        "<h1>Trener Jobs</h1>"
        "<noscript>You need to enable JavaScript to run this app.</noscript>"
        "</body></html>"
    )
    with patch("app.routes.jobs.detect_listing_page", return_value=_not_listing(html=js_shell_html)):
        fetched = _run_add_by_url(conn, "http://example.com/js-app")

    notice = fetched["result"]["notices"][0]
    assert notice["level"] == "warning"
    assert "No job content detected" in notice["html"]
    assert "load its content dynamically" in notice["html"]
    assert "JavaScript" in notice["html"]
    jobs = q.get_jobs(conn)
    assert len(jobs) == 1
    assert jobs[0]["content_type"] == "error"
    assert jobs[0]["status"] == "trash"
    assert jobs[0]["url"] == "http://example.com/js-app"


def test_add_job_by_url_js_only_page_playwright_fallback_succeeds(conn):
    # The listing-detect helper's render escalation is exercised in
    # tests/test_listing_detect.py; here we only check the job-add outcome when
    # the helper hands back rich content.
    with patch("app.routes.jobs.detect_listing_page", return_value=_not_listing(html=_JOB_POSTING_HTML)), \
         patch("app.routes.jobs.run_add_job", side_effect=_fake_run_add_job):
        fetched = _run_add_by_url(conn, "http://example.com/js-app")

    assert "Classified as job_posting" in fetched["log"]
    jobs = q.get_jobs(conn)
    assert len(jobs) == 1
    assert jobs[0]["content_type"] == "job_posting"
    assert jobs[0]["url"] == "http://example.com/js-app"


def test_add_job_by_url_thin_page_not_rendered_when_already_rich(conn):
    with patch("app.routes.jobs.detect_listing_page", return_value=_not_listing(html=_JOB_POSTING_HTML)), \
         patch("app.routes.jobs.run_add_job", side_effect=_fake_run_add_job):
        fetched = _run_add_by_url(conn, "http://example.com/job/1")

    assert fetched["status"] == "done"


_IS_A_LISTING = {
    "is_listing": True,
    "job_links": ["https://careers.example.com/jobs/1", "https://careers.example.com/jobs/2"],
}


def test_add_job_by_url_listing_detected_shows_confirm_panel(conn):
    with patch("app.routes.jobs.detect_listing_page", return_value=_listing(_IS_A_LISTING["job_links"])):
        fetched = _run_add_by_url(conn, "https://careers.example.com/jobs")

    html = fetched["result"]["html_chunks"][0]
    assert "Detected 2 job posting" in html
    assert "careers.example.com" in html
    assert 'data-progress-url="/jobs/add-listing-source"' in html
    assert '<label for="listing-name"' in html
    assert ">Name:</label>" in html
    assert fetched["result"]["needs_action"] is True
    assert q.get_jobs(conn) == []
    assert [s for s in q.get_sources(conn) if s["fetcher_type"] == "generic_listing"] == []


def test_add_job_by_url_single_job_link_does_not_trigger_listing_flow(conn):
    with patch("app.routes.jobs.run_add_job", side_effect=_fake_run_add_job), \
         patch("app.routes.jobs.detect_listing_page",
               return_value=_listing(["http://example.com/job/1"], html=_JOB_POSTING_HTML)):
        fetched = _run_add_by_url(conn, "http://example.com/job/1")

    assert "Classified as job_posting" in fetched["log"]
    assert len(q.get_jobs(conn)) == 1


def _fake_run_fetch(source, conn, client, model, profile_dir):
    yield f"Starting fetch for '{source['name']}'"
    q.insert_job(conn, source_id=source["id"], url="https://careers.example.com/jobs/1", title="T", company="C", raw_text="r")
    yield "Fetch complete"
    return FetchResult(source_id=source["id"], run_id=1, jobs_found=1, jobs_new=1, error=None)


def _fake_run_fetch_nothing_found(source, conn, client, model, profile_dir):
    yield f"Starting fetch for '{source['name']}'"
    return FetchResult(source_id=source["id"], run_id=1, jobs_found=0, jobs_new=0, error=None)


def test_add_listing_source_enqueues_task(client, conn):
    resp = client.post(
        "/jobs/add-listing-source",
        data={"url": "https://example.com/jobs", "name": "Example", "fetcher_type": "generic_listing"},
    )
    assert resp.status_code == 200
    task = q.get_task(conn, resp.json()["task_id"])
    assert task["kind"] == "job_add_listing_source"


def test_add_listing_source_creates_source_and_executes_fetch(conn):
    with patch("app.routes.jobs.run_fetch", side_effect=_fake_run_fetch):
        fetched = _run_add_listing_source(conn, "https://careers.example.com/jobs", "careers.example.com", "generic_listing")

    assert "Fetch complete" in fetched["log"]
    sources = [s for s in q.get_sources(conn) if s["fetcher_type"] == "generic_listing"]
    assert len(sources) == 1
    assert sources[0]["name"] == "careers.example.com"


def test_add_listing_source_resolves_open_listing_detected_prompt(conn):
    url = "https://careers.example.com/jobs"
    job_links = ["https://careers.example.com/jobs/1", "https://careers.example.com/jobs/2"]
    with patch("app.routes.jobs.detect_listing_page", return_value=_listing(job_links)):
        _run_add_by_url(conn, url)
    assert [i for i in q.get_unresolved_inbox_items(conn) if i["kind"] == "task_followup"]

    with patch("app.routes.jobs.run_fetch", side_effect=_fake_run_fetch):
        _run_add_listing_source(conn, url, "careers.example.com", "generic_listing")
    assert not [i for i in q.get_unresolved_inbox_items(conn) if i["kind"] == "task_followup"]


def test_add_listing_source_rejects_url_already_tracked_as_source(conn):
    sid = q.insert_source(conn, "Existing", "https://careers.example.com/jobs", "generic_listing")
    with patch("app.routes.jobs.run_fetch", side_effect=_fake_run_fetch):
        fetched = _run_add_listing_source(conn, "https://careers.example.com/jobs", "Dup", "generic_listing")
    assert "Already tracked as a source" in fetched["result"]["notices"][0]["html"]
    assert len(q.get_sources(conn)) == 1
    assert q.get_source(conn, sid)["name"] == "Existing"


def test_add_listing_source_rejects_url_already_tracked_as_job(conn):
    manual_sid = q.insert_source(conn, "Manual", "", "manual")
    jid = q.insert_job(conn, source_id=manual_sid, url="https://careers.example.com/jobs", title="T", company="C", raw_text="r")
    with patch("app.routes.jobs.run_fetch", side_effect=_fake_run_fetch):
        fetched = _run_add_listing_source(conn, "https://careers.example.com/jobs", "Dup", "generic_listing")
    notice_html = fetched["result"]["notices"][0]["html"]
    assert "Already tracked as a job" in notice_html
    assert f'href="/jobs/{jid}"' in notice_html
    assert [s for s in q.get_sources(conn) if s["fetcher_type"] != "manual"] == []


def test_add_listing_source_stream_ends_with_html_chunk(conn):
    with patch("app.routes.jobs.run_fetch", side_effect=_fake_run_fetch):
        fetched = _run_add_listing_source(conn, "https://careers.example.com/jobs", "careers.example.com", "generic_listing")

    assert '<div class="filter-bar">' in fetched["result"]["html_chunks"][0]


def test_add_listing_source_shows_persistent_notice_with_link(conn):
    with patch("app.routes.jobs.run_fetch", side_effect=_fake_run_fetch):
        fetched = _run_add_listing_source(conn, "https://careers.example.com/jobs", "careers.example.com", "generic_listing")
    assert fetched["result"]["notices"]
    sources = [s for s in q.get_sources(conn) if s["fetcher_type"] == "generic_listing"]
    assert len(sources) == 1
    assert f'href="/fetch#fetch-row-{sources[0]["id"]}"' in fetched["result"]["notices"][0]["html"]


def test_add_listing_source_shows_warning_notice_when_nothing_found(conn):
    with patch("app.routes.jobs.run_fetch", side_effect=_fake_run_fetch_nothing_found):
        fetched = _run_add_listing_source(conn, "https://careers.example.com/jobs", "careers.example.com", "generic_listing")
    notice = fetched["result"]["notices"][0]
    assert notice["level"] == "warning"
    assert "found nothing" in notice["html"]
    sources = q.get_sources(conn)
    assert len(sources) == 1
    assert f'href="/fetch#fetch-row-{sources[0]["id"]}"' in notice["html"]


def test_add_listing_source_passes_through_finn_listing_type(conn):
    with patch("app.routes.jobs.run_fetch", side_effect=_fake_run_fetch):
        _run_add_listing_source(conn, "https://www.finn.no/job/search", "Finn AI", "finn_listing")
    sources = q.get_sources(conn)
    assert len(sources) == 1
    assert sources[0]["fetcher_type"] == "finn_listing"


def test_add_listing_source_rejects_invalid_fetcher_type(client, conn):
    resp = client.post(
        "/jobs/add-listing-source",
        data={"url": "https://example.com", "name": "example.com", "fetcher_type": "http"},
    )
    assert resp.status_code == 400
    assert q.get_sources(conn) == []


def test_add_job_by_url_listing_confirm_panel_carries_detected_fetcher_type(conn):
    with patch("app.routes.jobs.detect_listing_page", return_value=_listing(_IS_A_LISTING["job_links"])):
        fetched = _run_add_by_url(conn, "https://careers.example.com/jobs")
    assert 'id="listing-fetcher-type" value="generic_listing"' in fetched["result"]["html_chunks"][0]


def test_add_job_by_url_listing_confirm_panel_detects_finn_no(conn):
    finn_links = ["https://www.finn.no/job/1", "https://www.finn.no/job/2"]
    with patch("app.routes.jobs.detect_listing_page", return_value=_listing(finn_links)):
        fetched = _run_add_by_url(conn, "https://www.finn.no/job/search")
    assert 'id="listing-fetcher-type" value="finn_listing"' in fetched["result"]["html_chunks"][0]


def test_add_job_by_url_listing_confirm_panel_uses_generated_name_when_page_has_title(conn):
    html = ("<html><head><title>Frontend Developer Jobs in Oslo | Careers</title></head>"
            "<body><a href='/jobs/1'>A</a><a href='/jobs/2'>B</a></body></html>")
    with patch("app.routes.jobs.detect_listing_page", return_value=_listing(_IS_A_LISTING["job_links"], html=html)), \
         patch("app.routes.jobs.generate_source_name", return_value="careers/frontend-oslo") as mock_gen:
        fetched = _run_add_by_url(conn, "https://careers.example.com/jobs")
    mock_gen.assert_called_once_with(
        ANY, ANY, "careers.example.com", "Frontend Developer Jobs in Oslo | Careers"
    )
    assert 'id="listing-name" value="careers/frontend-oslo"' in fetched["result"]["html_chunks"][0]


def test_add_job_by_url_listing_confirm_panel_falls_back_to_domain_when_name_generation_fails(conn):
    html = ("<html><head><title>Frontend Developer Jobs</title></head>"
            "<body><a href='/jobs/1'>A</a><a href='/jobs/2'>B</a></body></html>")
    with patch("app.routes.jobs.detect_listing_page", return_value=_listing(_IS_A_LISTING["job_links"], html=html)), \
         patch("app.routes.jobs.generate_source_name", return_value=None):
        fetched = _run_add_by_url(conn, "https://careers.example.com/jobs")
    assert 'id="listing-name" value="careers.example.com"' in fetched["result"]["html_chunks"][0]


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


def test_job_list_source_id_shows_every_status_for_that_source(client, conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "generic_listing")
    other_sid = q.insert_source(conn, "other.no", "https://other.no", "generic_listing")
    j_new = q.insert_job(conn, source_id=sid, url="http://finn.no/job/1", title="New Job", company="C", raw_text="r")
    j_accepted = q.insert_job(conn, source_id=sid, url="http://finn.no/job/2", title="Accepted Job", company="C", raw_text="r")
    q.update_job_feedback(conn, j_accepted, "accepted", "")
    j_trash = q.insert_job(conn, source_id=sid, url="http://finn.no/job/3", title="Trashed Job", company="C", raw_text="r")
    q.update_job_feedback(conn, j_trash, "trash", "")
    q.insert_job(conn, source_id=other_sid, url="http://other.no/job/1", title="Other Source Job", company="C", raw_text="r")

    resp = client.get(f"/jobs?source_id={sid}")

    assert resp.status_code == 200
    assert "New Job" in resp.text
    assert "Accepted Job" in resp.text
    assert "Trashed Job" in resp.text
    assert "Other Source Job" not in resp.text


def test_job_list_source_id_header_shows_name_and_count(client, conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "generic_listing")
    q.insert_job(conn, source_id=sid, url="http://finn.no/job/1", title="Job A", company="C", raw_text="r")
    resp = client.get(f"/jobs?source_id={sid}")
    assert resp.status_code == 200
    assert "All jobs from finn.no (1)" in resp.text
    assert '<a href="/jobs">Clear filter</a>' in resp.text


def test_job_list_source_id_hides_status_tabs(client, conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "generic_listing")
    resp = client.get(f"/jobs?source_id={sid}")
    assert resp.status_code == 200
    assert "New Jobs (" not in resp.text


def test_job_feedback_within_source_view_stays_visible_no_stale_badge(client, conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "generic_listing")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/1", title="Job A", company="C", raw_text="r")

    resp = client.post(
        f"/jobs/{jid}/feedback?source_id={sid}",
        data={"status": "accepted", "note": ""},
    )

    assert resp.status_code == 200
    assert "Moved to" not in resp.text
    assert "Job A" in resp.text
