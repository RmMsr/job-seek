import pytest
from unittest.mock import patch
from app.db import queries as q
from app.ai.refine import CriterionProposal


def test_scenarios_page_returns_200(client):
    resp = client.get("/scenarios")
    assert resp.status_code == 200


def test_create_scenario(client, conn):
    resp = client.post("/scenarios", data={"name": "Remote ML", "description": "Remote ML roles"})
    assert resp.status_code == 200
    scenarios = q.get_scenarios(conn)
    assert len(scenarios) == 1
    assert scenarios[0]["name"] == "Remote ML"


def test_edit_scenario_form_returns_fields(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "Remote ML roles")
    resp = client.get(f"/scenarios/{sid}/edit")
    assert resp.status_code == 200
    assert "Remote ML roles" in resp.text


def test_update_scenario(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "old description")
    resp = client.post(f"/scenarios/{sid}", data={"name": "Remote ML v2", "description": "new description"})
    assert resp.status_code == 200
    scenario = {s["id"]: s for s in q.get_scenarios(conn)}[sid]
    assert scenario["name"] == "Remote ML v2"
    assert scenario["description"] == "new description"
    assert "Remote ML v2" in resp.text


def test_update_scenario_route_persists_boosted_checkbox(client, conn):
    sid = q.insert_scenario(conn, "ai_expert", "fallback")
    resp = client.post(
        f"/scenarios/{sid}",
        data={"name": "ai_expert", "description": "fallback", "boosted": "on"},
    )
    assert resp.status_code == 200
    scenario = {s["id"]: s for s in q.get_scenarios(conn)}[sid]
    assert scenario["boosted"] == 1


def test_update_scenario_route_unchecking_boosted_clears_flag(client, conn):
    sid = q.insert_scenario(conn, "ai_expert", "fallback")
    client.post(f"/scenarios/{sid}", data={"name": "ai_expert", "description": "fallback", "boosted": "on"})
    resp = client.post(f"/scenarios/{sid}", data={"name": "ai_expert", "description": "fallback"})
    assert resp.status_code == 200
    scenario = {s["id"]: s for s in q.get_scenarios(conn)}[sid]
    assert scenario["boosted"] == 0


def test_edit_scenario_form_checkbox_checked_when_boosted(client, conn):
    sid = q.insert_scenario(conn, "ai_expert", "fallback")
    q.update_scenario(conn, sid, name="ai_expert", description="fallback", boosted=True)
    resp = client.get(f"/scenarios/{sid}/edit")
    assert resp.status_code == 200
    assert '<input type="checkbox" name="boosted" checked>' in resp.text


def test_edit_scenario_form_checkbox_unchecked_by_default(client, conn):
    sid = q.insert_scenario(conn, "A", "")
    resp = client.get(f"/scenarios/{sid}/edit")
    assert resp.status_code == 200
    assert '<input type="checkbox" name="boosted" checked>' not in resp.text
    assert 'name="boosted"' in resp.text


def test_scenario_header_shows_boosted_tag(client, conn):
    sid = q.insert_scenario(conn, "ai_expert", "fallback")
    q.update_scenario(conn, sid, name="ai_expert", description="fallback", boosted=True)
    resp = client.get(f"/scenarios/{sid}")
    assert resp.status_code == 200
    assert "Boosted" in resp.text


def test_scenario_header_omits_boosted_tag_when_not_boosted(client, conn):
    sid = q.insert_scenario(conn, "A", "")
    resp = client.get(f"/scenarios/{sid}")
    assert resp.status_code == 200
    assert "Boosted" not in resp.text


def test_cancel_scenario_edit_returns_display_header(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "Remote ML roles")
    resp = client.get(f"/scenarios/{sid}")
    assert resp.status_code == 200
    assert "Remote ML roles" in resp.text


def test_add_criterion(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    resp = client.post(f"/scenarios/{sid}/criteria", data={"text": "Must be remote", "weight": "must"})
    assert resp.status_code == 200
    criteria = q.get_criteria(conn, sid)
    assert criteria[0]["text"] == "Must be remote"


def test_add_criterion_response_wraps_list_and_form_in_shared_target(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    resp = client.post(f"/scenarios/{sid}/criteria", data={"text": "Must be remote", "weight": "must"})
    assert resp.status_code == 200
    # The id that both the add-form and the proposal accept-form target via
    # hx-target must sit on a wrapping element that contains the whole
    # list + form, not on the <ul> alone — otherwise HTMX's outerHTML swap
    # only replaces the <ul>, leaving the old populated <form> orphaned
    # in the DOM as a sibling.
    div_marker = f'<div id="criteria-{sid}">'
    ul_marker = f'<ul id="criteria-{sid}">'
    assert div_marker in resp.text
    assert ul_marker not in resp.text
    div_start = resp.text.index(div_marker)
    form_start = resp.text.index("<form")
    div_end = resp.text.rindex("</div>")
    assert div_start < form_start < div_end


def test_scenarios_page_renders_criterion_markdown(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Must be *remote*", "must")
    resp = client.get("/scenarios")
    assert resp.status_code == 200
    assert "<em>remote</em>" in resp.text


def test_delete_criterion(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    cid = q.insert_criterion(conn, sid, "Must be remote", "must")
    resp = client.delete(f"/criteria/{cid}")
    assert resp.status_code == 200
    assert q.get_criteria(conn, sid) == []


def test_edit_criterion_form_returns_fields(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    cid = q.insert_criterion(conn, sid, "Must be remote", "must")
    resp = client.get(f"/criteria/{cid}/edit")
    assert resp.status_code == 200
    assert "Must be remote" in resp.text


def test_update_criterion_route(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    cid = q.insert_criterion(conn, sid, "Must be remote", "must")
    resp = client.post(f"/criteria/{cid}", data={"text": "Must be fully remote", "weight": "prefer"})
    assert resp.status_code == 200
    assert "Must be fully remote" in resp.text
    criterion = q.get_criterion(conn, cid)
    assert criterion["text"] == "Must be fully remote"
    assert criterion["weight"] == "prefer"


def test_update_criterion_leaves_source_unchanged(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    cid = q.insert_criterion(conn, sid, "Must be remote", "must", source="feedback")
    client.post(f"/criteria/{cid}", data={"text": "Must be fully remote", "weight": "prefer"})
    assert q.get_criterion(conn, cid)["source"] == "feedback"


def test_cancel_criterion_edit_returns_display_row(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    cid = q.insert_criterion(conn, sid, "Must be remote", "must")
    resp = client.get(f"/criteria/{cid}")
    assert resp.status_code == 200
    assert "Must be remote" in resp.text


def test_refine_returns_proposals(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Must be remote", "must")
    proposals = [CriterionProposal(text="Must be senior", weight="must", action="add")]
    with patch("app.routes.scenarios.propose_criteria", return_value=proposals):
        resp = client.post(f"/scenarios/{sid}/refine")
    assert resp.status_code == 200
    assert "Requesting criteria proposals" in resp.text
    assert "HTML:" in resp.text
    assert "Must be senior" in resp.text


def test_refine_add_proposal_has_editable_inputs(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Must be remote", "must")
    proposals = [CriterionProposal(text="Must be senior", weight="must", action="add")]
    with patch("app.routes.scenarios.propose_criteria", return_value=proposals):
        resp = client.post(f"/scenarios/{sid}/refine")
    assert 'name="text_0" value="Must be senior"' in resp.text
    assert 'name="weight_0"' in resp.text
    assert 'name="kind_0" value="add"' in resp.text


def test_refine_proposal_row_has_checked_apply_checkbox_by_default(client, conn):
    # Batch-apply model: each row defaults to "included" (checked) — the
    # user unchecks the ones they don't want, then applies the whole batch
    # at once. There's no more per-row instant action.
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Must be remote", "must")
    proposals = [CriterionProposal(text="Must be senior", weight="must", action="add")]
    with patch("app.routes.scenarios.propose_criteria", return_value=proposals):
        resp = client.post(f"/scenarios/{sid}/refine")
    assert '<input type="checkbox" name="apply_0" checked>' in resp.text


def test_refine_remove_proposal_carries_criterion_id_and_no_editable_inputs(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    cid = q.insert_criterion(conn, sid, "Must be remote", "must")
    proposals = [CriterionProposal(text="Must be remote", weight="must", action="remove")]
    with patch("app.routes.scenarios.propose_criteria", return_value=proposals):
        resp = client.post(f"/scenarios/{sid}/refine")
    assert f'name="criterion_id_0" value="{cid}"' in resp.text
    assert 'name="kind_0" value="remove"' in resp.text
    assert 'name="text_0"' not in resp.text
    assert 'name="weight_0"' not in resp.text


def test_refine_remove_proposal_tag_shows_actual_stored_weight(client, conn):
    # The LLM's proposal can misreport the weight of the criterion it wants
    # removed (it's only given the criterion's text to match against, and
    # can hallucinate a different weight) — the tag must reflect what's
    # actually stored for that criterion, not whatever the model guessed.
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Salary above 1M", "prefer")
    proposals = [CriterionProposal(text="Salary above 1M", weight="must", action="remove")]
    with patch("app.routes.scenarios.propose_criteria", return_value=proposals):
        resp = client.post(f"/scenarios/{sid}/refine")
    assert '<span class="tag">prefer</span>' in resp.text
    assert '<span class="tag">must</span>' not in resp.text


def test_refine_remove_proposal_tag_shows_weight_not_action(client, conn):
    # The tag should match the style of the existing criteria list (which
    # shows the weight), not the literal action name ("remove").
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Graduate or junior positions", "avoid")
    proposals = [CriterionProposal(text="Graduate or junior positions", weight="avoid", action="remove")]
    with patch("app.routes.scenarios.propose_criteria", return_value=proposals):
        resp = client.post(f"/scenarios/{sid}/refine")
    assert '<span class="tag">avoid</span>' in resp.text
    assert '<span class="tag">remove</span>' not in resp.text


def test_refine_remove_proposal_text_is_struck_through(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Graduate or junior positions", "avoid")
    proposals = [CriterionProposal(text="Graduate or junior positions", weight="avoid", action="remove")]
    with patch("app.routes.scenarios.propose_criteria", return_value=proposals):
        resp = client.post(f"/scenarios/{sid}/refine")
    assert "text-decoration:line-through" in resp.text


def test_refine_add_proposal_duplicating_existing_criterion_is_omitted(client, conn):
    # The LLM shouldn't be trusted to always notice a criterion it was already
    # given already exists — drop "add" proposals that duplicate one directly,
    # rather than showing a nonsensical "add" suggestion for something that's
    # already there (previously observed flapping between add/remove for the
    # same text across refine calls).
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Graduate or junior positions", "avoid")
    proposals = [CriterionProposal(text="Graduate or junior positions", weight="avoid", action="add")]
    with patch("app.routes.scenarios.propose_criteria", return_value=proposals):
        resp = client.post(f"/scenarios/{sid}/refine")
    assert "Graduate or junior positions" not in resp.text
    assert "No changes proposed" in resp.text


def test_refine_add_proposal_duplicating_existing_criterion_ignores_case_and_whitespace(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Graduate or junior positions", "avoid")
    proposals = [CriterionProposal(text="  graduate or JUNIOR positions  ", weight="avoid", action="add")]
    with patch("app.routes.scenarios.propose_criteria", return_value=proposals):
        resp = client.post(f"/scenarios/{sid}/refine")
    assert "No changes proposed" in resp.text


def test_refine_remove_proposal_unmatched_is_omitted(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Must be remote", "must")
    proposals = [CriterionProposal(text="Must have a PhD", weight="must", action="remove")]
    with patch("app.routes.scenarios.propose_criteria", return_value=proposals):
        resp = client.post(f"/scenarios/{sid}/refine")
    assert "Must have a PhD" not in resp.text
    assert "No changes proposed" in resp.text


def test_refine_html_chunk_has_no_embedded_newline(client, conn):
    # The client reads the stream line-by-line, splitting on "\n", and only
    # recognizes "HTML:" as a prefix of a complete line. A fragment with an
    # embedded newline would be split across multiple "lines" client-side,
    # silently truncating the html to "" instead of the real fragment.
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Must be remote", "must")
    proposals = [CriterionProposal(text="Must be senior", weight="must", action="add")]
    with patch("app.routes.scenarios.propose_criteria", return_value=proposals):
        resp = client.post(f"/scenarios/{sid}/refine")
    lines = resp.text.split("\n")
    html_lines = [line for line in lines if line.startswith("HTML:")]
    assert len(html_lines) == 1
    assert "Must be senior" in html_lines[0]


def test_refine_alone_does_not_mark_feedback_handled(client, conn):
    # Feedback stays "live" until you actually act on a proposal derived
    # from it — merely running refine and looking at the suggestions
    # shouldn't consume it.
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Must be remote", "must")
    source_id = q.insert_source(conn, "s", "http://x", "http")
    job_id = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_pipeline(conn, job_id, simplified_content="", content_type="job_posting")
    q.update_job_feedback(conn, job_id, "rejected", "too junior", feedback_scenario_id=sid)

    proposals = [CriterionProposal(text="Must be senior", weight="must", action="add")]
    with patch("app.routes.scenarios.propose_criteria", return_value=proposals):
        client.post(f"/scenarios/{sid}/refine")

    assert q.get_recent_feedback_job_ids(conn, sid) == [job_id]


def test_refine_embeds_feedback_job_ids_in_apply_form(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    source_id = q.insert_source(conn, "s", "http://x", "http")
    job_id = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_pipeline(conn, job_id, simplified_content="", content_type="job_posting")
    q.update_job_feedback(conn, job_id, "rejected", "too junior", feedback_scenario_id=sid)

    proposals = [CriterionProposal(text="Must be senior", weight="must", action="add")]
    with patch("app.routes.scenarios.propose_criteria", return_value=proposals):
        resp = client.post(f"/scenarios/{sid}/refine")
    assert f'name="feedback_job_ids" value="{job_id}"' in resp.text


def test_manual_add_criterion_does_not_mark_feedback_handled(client, conn):
    # The always-instant manual add-form at the bottom of the criteria list
    # is unrelated to the LLM-suggestion batch-apply flow and carries no
    # feedback_job_ids — it must never mark anything handled.
    sid = q.insert_scenario(conn, "Remote ML", "")
    source_id = q.insert_source(conn, "s", "http://x", "http")
    job_id = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_pipeline(conn, job_id, simplified_content="", content_type="job_posting")
    q.update_job_feedback(conn, job_id, "rejected", "too junior", feedback_scenario_id=sid)

    resp = client.post(f"/scenarios/{sid}/criteria", data={"text": "Must be senior", "weight": "must"})
    assert resp.status_code == 200
    assert q.get_recent_feedback_job_ids(conn, sid) == [job_id]


def test_apply_batch_inserts_checked_add_and_deletes_checked_remove(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    cid = q.insert_criterion(conn, sid, "Must be remote", "must")
    resp = client.post(
        f"/scenarios/{sid}/refine/accept",
        data={
            "kind_0": "add",
            "apply_0": "on",
            "text_0": "Must be senior",
            "weight_0": "must",
            "kind_1": "remove",
            "apply_1": "on",
            "criterion_id_1": str(cid),
        },
    )
    assert resp.status_code == 200
    criteria = q.get_criteria(conn, sid)
    assert [c["text"] for c in criteria] == ["Must be senior"]


def test_apply_batch_skips_unchecked_rows(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    cid = q.insert_criterion(conn, sid, "Must be remote", "must")
    resp = client.post(
        f"/scenarios/{sid}/refine/accept",
        data={
            # apply_0 omitted entirely, as an unchecked HTML checkbox would be.
            "kind_0": "add",
            "text_0": "Must be senior",
            "weight_0": "must",
            "kind_1": "remove",
            # apply_1 omitted too.
            "criterion_id_1": str(cid),
        },
    )
    assert resp.status_code == 200
    criteria = q.get_criteria(conn, sid)
    assert [c["text"] for c in criteria] == ["Must be remote"]


def test_apply_batch_marks_feedback_handled_even_when_all_rows_skipped(client, conn):
    # One atomic submit reviews the whole batch, regardless of which
    # individual rows were applied — even an all-skip submission means the
    # batch was looked at and consciously rejected.
    sid = q.insert_scenario(conn, "Remote ML", "")
    source_id = q.insert_source(conn, "s", "http://x", "http")
    job_id = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_pipeline(conn, job_id, simplified_content="", content_type="job_posting")
    q.update_job_feedback(conn, job_id, "rejected", "too junior", feedback_scenario_id=sid)

    resp = client.post(
        f"/scenarios/{sid}/refine/accept",
        data={"kind_0": "add", "text_0": "Must be senior", "weight_0": "must", "feedback_job_ids": str(job_id)},
    )
    assert resp.status_code == 200
    assert q.get_recent_feedback_job_ids(conn, sid) == []
    assert q.get_criteria(conn, sid) == []


def test_apply_batch_ignores_malformed_feedback_job_ids(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    resp = client.post(
        f"/scenarios/{sid}/refine/accept",
        data={"feedback_job_ids": "1; DROP TABLE jobs"},
    )
    assert resp.status_code == 200


def test_apply_batch_clears_proposals_panel_via_oob(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    resp = client.post(
        f"/scenarios/{sid}/refine/accept",
        data={"kind_0": "add", "apply_0": "on", "text_0": "Must be senior", "weight_0": "must"},
    )
    assert f'id="proposals-area-{sid}" hx-swap-oob="true"' in resp.text


def test_refine_all_scenarios_streams_per_scenario_oob_html(client, conn):
    sid_a = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid_a, "Must be remote", "must")
    sid_b = q.insert_scenario(conn, "Robotics", "")
    q.insert_criterion(conn, sid_b, "Must involve embedded systems", "must")

    proposals = [CriterionProposal(text="Must be senior", weight="must", action="add")]
    with patch("app.routes.scenarios.propose_criteria", return_value=proposals):
        resp = client.post("/scenarios/refine")

    assert resp.status_code == 200
    text = resp.text
    assert "Refining criteria for 2 scenario(s)" in text
    assert f'id="proposals-area-{sid_a}"' in text
    assert f'id="proposals-area-{sid_b}"' in text

    lines = text.split("\n")
    html_lines = [line for line in lines if line.startswith("HTML:")]
    assert len(html_lines) == 2
    # Scenario order is preserved: scenario A's chunk arrives before B's.
    assert f"proposals-area-{sid_a}" in html_lines[0]
    assert f"proposals-area-{sid_b}" in html_lines[1]


def test_refine_all_scenarios_oob_wrapper_preserves_layout_style(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    proposals = [CriterionProposal(text="Must be senior", weight="must", action="add")]
    with patch("app.routes.scenarios.propose_criteria", return_value=proposals):
        resp = client.post("/scenarios/refine")

    assert 'style="margin-top:0.75rem; width:100%;"' in resp.text


def test_refine_all_scenarios_embeds_feedback_job_ids(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    source_id = q.insert_source(conn, "s", "http://x", "http")
    job_id = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_pipeline(conn, job_id, simplified_content="", content_type="job_posting")
    q.update_job_feedback(conn, job_id, "rejected", "too junior", feedback_scenario_id=sid)

    proposals = [CriterionProposal(text="Must be senior", weight="must", action="add")]
    with patch("app.routes.scenarios.propose_criteria", return_value=proposals):
        resp = client.post("/scenarios/refine")
    assert f'name="feedback_job_ids" value="{job_id}"' in resp.text


def test_refine_all_scenarios_with_no_scenarios(client, conn):
    resp = client.post("/scenarios/refine")
    assert resp.status_code == 200
    assert "Refining criteria for 0 scenario(s)" in resp.text


def test_refine_accept_adds_criteria(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    resp = client.post(
        f"/scenarios/{sid}/refine/accept",
        data={"kind_0": "add", "apply_0": "on", "text_0": "Must be senior", "weight_0": "must"},
    )
    assert resp.status_code == 200
    criteria = q.get_criteria(conn, sid)
    assert any(c["text"] == "Must be senior" for c in criteria)


def test_reevaluate_streams_progress_and_updates_jobs(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Must be remote", "must")
    source_id = q.insert_source(conn, "s", "http://x", "http")
    job_id = q.insert_job(conn, source_id=source_id, url="http://job/1", title="ML Eng", company="C", raw_text="r")
    q.update_job_pipeline(conn, job_id, simplified_content="clean", content_type="job_posting")

    with patch("app.pipeline.summarize", return_value=("ML Engineer - Remote @ Acme", "Great hook", "Updated summary")), \
         patch("app.pipeline.evaluate", return_value=(0.75, "Good match")):
        resp = client.post(f"/scenarios/{sid}/reevaluate")

    assert resp.status_code == 200
    assert "Re-evaluating 1 job(s)" in resp.text
    assert "Re-evaluation complete" in resp.text
    job = q.get_job(conn, job_id)
    assert job["best_score"] == pytest.approx(0.75)
    assert job["summary"] == "Updated summary"


def test_reevaluate_skips_jobs_already_current(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Must be remote", "must")
    source_id = q.insert_source(conn, "s", "http://x", "http")
    q.insert_job(conn, source_id=source_id, url="http://job/1", title="ML Eng", company="C", raw_text="r")
    job_id = q.get_jobs(conn)[0]["id"]
    q.update_job_pipeline(conn, job_id, simplified_content="clean", content_type="job_posting")

    with patch("app.pipeline.summarize", return_value=("ML Engineer - Remote @ Acme", "Great hook", "Updated summary")), \
         patch("app.pipeline.evaluate", return_value=(0.75, "Good match")) as mock_evaluate:
        client.post(f"/scenarios/{sid}/reevaluate")
        resp = client.post(f"/scenarios/{sid}/reevaluate")

    assert mock_evaluate.call_count == 1
    assert "skipping 1 already current" in resp.text
    assert "Re-evaluating 0 job(s)" in resp.text


def test_reevaluate_all_scenarios_streams_combined_progress(client, conn):
    sid_a = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid_a, "Must be remote", "must")
    sid_b = q.insert_scenario(conn, "Robotics", "")
    q.insert_criterion(conn, sid_b, "Must involve embedded systems", "must")
    source_id = q.insert_source(conn, "s", "http://x", "http")
    job_id = q.insert_job(conn, source_id=source_id, url="http://job/1", title="ML Eng", company="C", raw_text="r")
    q.update_job_pipeline(conn, job_id, simplified_content="clean", content_type="job_posting")

    with patch("app.pipeline.summarize", return_value=("ML Engineer - Remote @ Acme", "Great hook", "Updated summary")), \
         patch("app.pipeline.evaluate", return_value=(0.75, "Good match")):
        resp = client.post("/scenarios/reevaluate")

    assert resp.status_code == 200
    assert "Re-evaluating 2 scenario(s)" in resp.text
    assert "All scenarios re-evaluated: 2 job(s) updated across 2 scenario(s)" in resp.text
    assert q.get_job_score(conn, job_id, sid_a) is not None
    assert q.get_job_score(conn, job_id, sid_b) is not None


def test_reevaluate_all_scenarios_shows_global_job_position(client, conn):
    sid_a = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid_a, "Must be remote", "must")
    sid_b = q.insert_scenario(conn, "Robotics", "")
    q.insert_criterion(conn, sid_b, "Must involve embedded systems", "must")
    source_id = q.insert_source(conn, "s", "http://x", "http")
    job_id = q.insert_job(conn, source_id=source_id, url="http://job/1", title="ML Eng", company="C", raw_text="r")
    q.update_job_pipeline(conn, job_id, simplified_content="clean", content_type="job_posting")

    with patch("app.pipeline.summarize", return_value=("ML Engineer - Remote @ Acme", "Great hook", "Updated summary")), \
         patch("app.pipeline.evaluate", return_value=(0.75, "Good match")):
        resp = client.post("/scenarios/reevaluate")

    assert "[Scenario 1/2: Remote ML] [1/2] Re-scored" in resp.text
    assert "[Scenario 2/2: Robotics] [2/2] Re-scored" in resp.text


def test_reevaluate_single_scenario_route_unaffected_by_global_labeling(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Must be remote", "must")
    source_id = q.insert_source(conn, "s", "http://x", "http")
    job_id = q.insert_job(conn, source_id=source_id, url="http://job/1", title="ML Eng", company="C", raw_text="r")
    q.update_job_pipeline(conn, job_id, simplified_content="clean", content_type="job_posting")

    with patch("app.pipeline.summarize", return_value=("ML Engineer - Remote @ Acme", "Great hook", "Updated summary")), \
         patch("app.pipeline.evaluate", return_value=(0.75, "Good match")):
        resp = client.post(f"/scenarios/{sid}/reevaluate")

    assert "[1/1] Re-scored" in resp.text
    assert "Scenario 1/1" not in resp.text


def test_reevaluate_keeps_existing_title_when_ai_title_empty(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Must be remote", "must")
    source_id = q.insert_source(conn, "s", "http://x", "http")
    job_id = q.insert_job(conn, source_id=source_id, url="http://job/1", title="ML Eng", company="C", raw_text="r")
    q.update_job_pipeline(conn, job_id, simplified_content="clean", content_type="job_posting")

    with patch("app.pipeline.summarize", return_value=("", "", "Updated summary")), \
         patch("app.pipeline.evaluate", return_value=(0.75, "Good match")):
        client.post(f"/scenarios/{sid}/reevaluate")

    job = q.get_job(conn, job_id)
    assert job["title"] == "ML Eng"
    assert job["summary"] == "Updated summary"


def test_refine_proposals_sorted_must_prefer_avoid(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Must be remote", "must")
    proposals = [
        CriterionProposal(text="Avoid on-call", weight="avoid", action="add"),
        CriterionProposal(text="Must pay well", weight="must", action="add"),
        CriterionProposal(text="Prefer Python", weight="prefer", action="add"),
    ]
    with patch("app.routes.scenarios.propose_criteria", return_value=proposals):
        resp = client.post(f"/scenarios/{sid}/refine")
    text = resp.text
    must_pos = text.index("Must pay well")
    prefer_pos = text.index("Prefer Python")
    avoid_pos = text.index("Avoid on-call")
    assert must_pos < prefer_pos < avoid_pos
