from datetime import datetime, timezone, timedelta
import pytest
from app.db import queries as q


def _seed(conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "http")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/1", title="ML Eng", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="job_posting", summary="Great role")
    scenario_id = q.insert_scenario(conn, "Remote ML", "")
    q.upsert_job_score(conn, jid, scenario_id, 0.9, "Good match", "hash1")
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
        data={"status": "accepted", "note": "good fit", "feedback_scenario_id": scenario_id},
    )
    assert resp.status_code == 200
    job = q.get_job(conn, jid)
    assert job["status"] == "accepted"
    assert job["feedback_note"] == "good fit"
    assert job["feedback_scenario_id"] == scenario_id


def test_job_feedback_can_target_non_default_scenario(client, conn):
    sid, jid, best_scenario_id = _seed(conn)
    other_scenario_id = q.insert_scenario(conn, "Other Scenario", "")
    resp = client.post(
        f"/jobs/{jid}/feedback",
        data={"status": "rejected", "note": "not a fit here", "feedback_scenario_id": other_scenario_id},
    )
    assert resp.status_code == 200
    job = q.get_job(conn, jid)
    assert job["feedback_scenario_id"] == other_scenario_id
    assert job["feedback_scenario_id"] != best_scenario_id


def test_job_list_shows_feedback_scenario_tag_when_overridden(client, conn):
    sid, jid, best_scenario_id = _seed(conn)
    other_scenario_id = q.insert_scenario(conn, "Other Scenario", "")
    client.post(
        f"/jobs/{jid}/feedback",
        data={"status": "rejected", "note": "not a fit here", "feedback_scenario_id": other_scenario_id},
    )
    resp = client.get("/jobs?status=rejected")
    assert resp.status_code == 200
    assert '<span class="tag tag-feedback">→ Other Scenario</span>' in resp.text


def test_job_list_omits_feedback_scenario_tag_when_matching_best(client, conn):
    sid, jid, best_scenario_id = _seed(conn)
    client.post(
        f"/jobs/{jid}/feedback",
        data={"status": "rejected", "note": "not a fit here", "feedback_scenario_id": best_scenario_id},
    )
    resp = client.get("/jobs?status=rejected")
    assert resp.status_code == 200
    assert '<span class="tag tag-feedback">' not in resp.text


def test_job_list_shows_scenario_tag_for_best_score(client, conn):
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


def test_job_expand_feedback_form_defaults_to_best_scenario(client, conn):
    sid, jid, best_scenario_id = _seed(conn)
    q.insert_scenario(conn, "Other Scenario", "")
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert "Other Scenario" in resp.text  # every scenario is listed
    assert f'<option value="{best_scenario_id}" selected>' in resp.text


def test_job_expand_feedback_form_has_no_forced_selection_when_unscored(client, conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "http")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/2", title="No Score", company="Acme", raw_text="r")
    q.insert_scenario(conn, "First", "")
    q.insert_scenario(conn, "Second", "")
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert "selected" not in resp.text


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
    assert '<dt class="sr-only">Score</dt>' in resp.text
    assert '<dt class="sr-only">Scenario</dt>' in resp.text
    assert '<dt class="sr-only">Content type</dt>' in resp.text
    assert '<dt class="sr-only">Source</dt>' in resp.text


def test_job_expand_has_full_meta_parity_with_card(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert '<dl class="job-tags">' in resp.text
    assert "90%" in resp.text  # score badge, not just reasoning text
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
    assert resp.text.count("90%") == 2  # top meta row + scenario_a's tab card
    assert "40%" in resp.text  # scenario_b's tab card


def test_job_expand_best_scenario_tab_is_checked(client, conn):
    sid, jid, scenario_a = _seed(conn)
    scenario_b = q.insert_scenario(conn, "Other Scenario", "")
    q.upsert_job_score(conn, jid, scenario_b, 0.4, "weaker fit", "hash2")

    resp = client.get(f"/jobs/{jid}/expand")

    assert resp.status_code == 200
    assert f'id="score-tab-{jid}-{scenario_a}"' in resp.text
    assert f'id="score-tab-{jid}-{scenario_a}" name="score-tab-{jid}" class="score-tab-input" checked' in resp.text
    assert f'id="score-tab-{jid}-{scenario_b}" name="score-tab-{jid}" class="score-tab-input" checked' not in resp.text


def test_job_expand_shows_boosted_indicator_for_boosted_scenario(client, conn):
    sid, jid, scenario_a = _seed(conn)
    q.update_scenario(conn, scenario_a, name="Remote ML", description="", boosted=True)

    resp = client.get(f"/jobs/{jid}/expand")

    assert resp.status_code == 200
    assert "Boosted scenario" in resp.text  # title attribute on the indicator icon


def test_job_expand_no_score_box_when_unscored(client, conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "http")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/2", title="No Score", company="Acme", raw_text="r")
    q.insert_scenario(conn, "First", "")
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert 'class="score-box' not in resp.text


def test_job_expand_scenario_label_indicates_best_fit(client, conn):
    sid, jid, best_scenario_id = _seed(conn)
    other_id = q.insert_scenario(conn, "Other Scenario", "")
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert "best fit" in resp.text.lower()
    # options carry the plain scenario name (no per-option marker) and remain freely selectable
    assert f'<option value="{best_scenario_id}" selected>Remote ML</option>' in resp.text
    assert f'<option value="{other_id}" >Other Scenario</option>' in resp.text


def test_job_feedback_without_note_succeeds(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.post(
        f"/jobs/{jid}/feedback",
        data={"status": "accepted", "feedback_scenario_id": scenario_id},
    )
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


def test_job_bulk_feedback_updates_multiple_jobs(client, conn):
    sid, j1, scenario_id = _seed(conn)
    j2 = q.insert_job(conn, source_id=sid, url="http://finn.no/job/2", title="Data Eng", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, j2, simplified_content="clean", content_type="job_posting", summary="Also good")
    q.upsert_job_score(conn, j2, scenario_id, 0.6, "Decent match", "hash2")
    resp = client.post(
        "/jobs/bulk-feedback",
        data={
            "job_ids": [j1, j2],
            "status": "rejected",
            "feedback_scenario_id": "",
            "status_filter": "",
            "content_type_filter": "",
        },
    )
    assert resp.status_code == 200
    assert q.get_job(conn, j1)["status"] == "rejected"
    assert q.get_job(conn, j2)["status"] == "rejected"


def test_job_bulk_feedback_defaults_to_each_jobs_own_best_scenario(client, conn):
    sid, j1, scenario_a = _seed(conn)
    scenario_b = q.insert_scenario(conn, "Other", "")
    j2 = q.insert_job(conn, source_id=sid, url="http://finn.no/job/2", title="Data Eng", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, j2, simplified_content="clean", content_type="job_posting", summary="Also good")
    q.upsert_job_score(conn, j2, scenario_b, 0.6, "Decent match", "hash2")
    client.post(
        "/jobs/bulk-feedback",
        data={"job_ids": [j1, j2], "status": "invalid", "feedback_scenario_id": "", "status_filter": "", "content_type_filter": ""},
    )
    assert q.get_job(conn, j1)["feedback_scenario_id"] == scenario_a
    assert q.get_job(conn, j2)["feedback_scenario_id"] == scenario_b


def test_job_bulk_feedback_explicit_scenario_overrides_all(client, conn):
    sid, j1, scenario_a = _seed(conn)
    scenario_b = q.insert_scenario(conn, "Other", "")
    j2 = q.insert_job(conn, source_id=sid, url="http://finn.no/job/2", title="Data Eng", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, j2, simplified_content="clean", content_type="job_posting", summary="Also good")
    q.upsert_job_score(conn, j2, scenario_b, 0.6, "Decent match", "hash2")
    client.post(
        "/jobs/bulk-feedback",
        data={"job_ids": [j1, j2], "status": "rejected", "feedback_scenario_id": scenario_b, "status_filter": "", "content_type_filter": ""},
    )
    assert q.get_job(conn, j1)["feedback_scenario_id"] == scenario_b
    assert q.get_job(conn, j2)["feedback_scenario_id"] == scenario_b


def test_job_bulk_feedback_note_is_optional(client, conn):
    sid, j1, scenario_id = _seed(conn)
    resp = client.post(
        "/jobs/bulk-feedback",
        data={"job_ids": [j1], "status": "accepted", "feedback_scenario_id": "", "status_filter": "", "content_type_filter": ""},
    )
    assert resp.status_code == 200
    assert q.get_job(conn, j1)["feedback_note"] is None


def test_job_bulk_feedback_returns_filtered_content_reflecting_removed_jobs(client, conn):
    sid, j1, scenario_id = _seed(conn)
    resp = client.post(
        "/jobs/bulk-feedback",
        data={"job_ids": [j1], "status": "rejected", "feedback_scenario_id": "", "status_filter": "", "content_type_filter": ""},
    )
    assert resp.status_code == 200
    assert "ML Eng" not in resp.text
    assert "No jobs found" in resp.text


def test_job_list_has_persistent_bulk_form_shell(client, conn):
    resp = client.get("/jobs")
    assert resp.status_code == 200
    assert '<form id="bulk-form" hx-post="/jobs/bulk-feedback" hx-target="#jobs-content" hx-swap="innerHTML">' in resp.text
    assert '<input type="hidden" name="status_filter" value="new">' in resp.text


def test_job_list_bulk_bar_has_scenario_select_and_actions(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get("/jobs")
    assert resp.status_code == 200
    assert "Keep each job's own scenario" in resp.text
    assert 'form="bulk-form" name="status" value="accepted"' in resp.text
    assert 'form="bulk-form" name="status" value="rejected"' in resp.text
    assert 'form="bulk-form" name="status" value="invalid"' in resp.text
    assert 'title="Does not match your criteria — feeds back into scenario tuning."' in resp.text
    assert 'title="Not a usable posting (expired, spam, duplicate, wrong content) — does not affect scenario criteria."' in resp.text


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
    q.update_job_feedback(conn, j1, "accepted", "", feedback_scenario_id=scenario_id)
    j2 = q.insert_job(conn, source_id=sid, url="http://finn.no/job/2", title="Data Eng", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, j2, simplified_content="clean", content_type="job_posting", summary="Also good")
    resp = client.post(
        "/jobs/bulk-feedback",
        data={"job_ids": [j2], "status": "accepted", "feedback_scenario_id": "", "status_filter": "accepted", "content_type_filter": ""},
    )
    assert resp.status_code == 200
    assert "ML Eng" in resp.text
    assert "Data Eng" in resp.text
