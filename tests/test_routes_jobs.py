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
    resp = client.get("/")
    assert resp.status_code == 200
    assert "ML Eng" in resp.text


def test_job_list_empty(client, conn):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "No jobs found" in resp.text


def test_job_list_shows_published_date(client, conn):
    published = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "finn_listing")
    q.insert_job(
        conn, source_id=sid, url="http://finn.no/job/1", title="ML Eng", company="Acme", raw_text="r",
        published_at=published,
    )
    resp = client.get("/")
    assert resp.status_code == 200
    assert "5 days ago" in resp.text


def test_job_list_omits_published_date_when_unknown(client, conn):
    _seed(conn)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "days ago" not in resp.text


def test_job_list_filter_accepted(client, conn):
    sid, jid, scenario_id = _seed(conn)
    q.update_job_feedback(conn, jid, "accepted", "great")
    resp = client.get("/?status=accepted")
    assert resp.status_code == 200
    assert "ML Eng" in resp.text
    resp2 = client.get("/")
    assert "ML Eng" not in resp2.text


def test_job_list_filter_bar_shows_counts(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get("/")
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


def test_job_list_shows_scenario_tag_for_best_score(client, conn):
    _seed(conn)
    resp = client.get("/")
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
    resp = client.get("/")
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
    resp = client.get("/")
    assert "Fully remote, $180k+" in resp.text


def test_job_list_falls_back_to_summary_when_no_headline(client, conn):
    sid, jid, scenario_id = _seed(conn)  # _seed sets summary="Great role", no headline
    resp = client.get("/")
    assert "Great role" in resp.text


def test_job_list_row_omits_company_but_expand_keeps_it(client, conn):
    sid, jid, scenario_id = _seed(conn)  # _seed sets company="Acme"
    resp = client.get("/")
    assert "· Acme" not in resp.text
    resp2 = client.get(f"/jobs/{jid}/expand")
    assert "· Acme" in resp2.text


def test_job_list_title_is_heading_in_its_own_block(client, conn):
    _seed(conn)
    resp = client.get("/")
    assert resp.status_code == 200
    assert '<h3 class="job-title">ML Eng</h3>' in resp.text


def test_job_list_card_is_article(client, conn):
    _seed(conn)
    resp = client.get("/")
    assert resp.status_code == 200
    assert '<article class="job-row"' in resp.text


def test_job_list_tags_are_semantic_definition_list(client, conn):
    _seed(conn)
    resp = client.get("/")
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


def test_job_expand_score_box_repeats_score_scenario_with_reasoning(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert 'class="score-box"' in resp.text
    assert resp.text.count("90%") == 2  # once in top meta row, once in the score box
    assert resp.text.count("Remote ML") == 3  # meta row + score box + best-fit dropdown option
    assert "Good match" in resp.text


def test_job_expand_no_score_box_when_unscored(client, conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "http")
    jid = q.insert_job(conn, source_id=sid, url="http://finn.no/job/2", title="No Score", company="Acme", raw_text="r")
    q.insert_scenario(conn, "First", "")
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert 'class="score-box"' not in resp.text


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
