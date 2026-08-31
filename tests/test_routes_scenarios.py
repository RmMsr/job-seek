import pytest
from unittest.mock import patch, MagicMock
from app.db import queries as q
from app.ai.refine import CriterionProposal
from app.ai.summarize import JobSummary
from app.task_engine import execute_task


def _run_refine_one(conn, scenario_id):
    task = q.enqueue_task(conn, kind="scenario_refine_one", params={"scenario_id": scenario_id})
    execute_task(conn, MagicMock(), "model", MagicMock(), task)
    return q.get_task(conn, task["id"])


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


def test_update_scenario_route_persists_gate_threshold(client, conn):
    sid = q.insert_scenario(conn, "ai_expert", "fallback")
    resp = client.post(
        f"/scenarios/{sid}",
        data={"name": "ai_expert", "description": "fallback", "gate_threshold": "0.5"},
    )
    assert resp.status_code == 200
    scenario = {s["id"]: s for s in q.get_scenarios(conn)}[sid]
    assert scenario["gate_threshold"] == pytest.approx(0.5)


def test_update_scenario_route_gate_threshold_defaults_to_0_7_when_omitted(client, conn):
    sid = q.insert_scenario(conn, "ai_expert", "fallback")
    resp = client.post(f"/scenarios/{sid}", data={"name": "ai_expert", "description": "fallback"})
    assert resp.status_code == 200
    scenario = {s["id"]: s for s in q.get_scenarios(conn)}[sid]
    assert scenario["gate_threshold"] == pytest.approx(0.7)


def test_edit_scenario_form_shows_current_gate_threshold(client, conn):
    sid = q.insert_scenario(conn, "ai_expert", "fallback")
    q.update_scenario(conn, sid, name="ai_expert", description="fallback", gate_threshold=0.55)
    resp = client.get(f"/scenarios/{sid}/edit")
    assert resp.status_code == 200
    assert 'value="0.55"' in resp.text


def test_scenario_header_shows_gate_threshold(client, conn):
    sid = q.insert_scenario(conn, "ai_expert", "fallback")
    resp = client.get(f"/scenarios/{sid}")
    assert resp.status_code == 200
    assert "Job match threshold" in resp.text
    assert '<output class="threshold-readout">70%</output>' in resp.text


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


def _seed_scenario_feedback(conn, scenario_id):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, jid, scenario_id, "too junior", "lower")


def test_scenario_refine_one_enqueues_task(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    _seed_scenario_feedback(conn, sid)
    resp = client.post(f"/scenarios/{sid}/refine")
    assert resp.status_code == 200
    data = resp.json()
    task = q.get_task(conn, data["task_id"])
    assert task["kind"] == "scenario_refine_one"
    assert task["params"]["scenario_id"] == sid
    assert data["already_active"] is False


def test_scenario_refine_one_skips_when_no_feedback(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    resp = client.post(f"/scenarios/{sid}/refine")
    assert resp.status_code == 200
    data = resp.json()
    assert data["skipped"] is True
    assert "No feedback to review" in data["message"]


def test_scenario_refine_one_unknown_scenario_404s(client, conn):
    resp = client.post("/scenarios/999/refine")
    assert resp.status_code == 404


def test_refine_all_scenarios_skips_when_no_scenario_has_feedback(client, conn):
    q.insert_scenario(conn, "Remote ML", "")
    resp = client.post("/scenarios/refine")
    assert resp.status_code == 200
    data = resp.json()
    assert data["skipped"] is True


def test_refine_all_scenarios_enqueues_when_any_scenario_has_feedback(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_scenario(conn, "Robotics", "")
    _seed_scenario_feedback(conn, sid)
    resp = client.post("/scenarios/refine")
    assert resp.status_code == 200
    data = resp.json()
    assert q.get_task(conn, data["task_id"])["kind"] == "scenarios_refine_all"


def test_refine_returns_proposals(conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Must be remote", "must")
    proposals = [CriterionProposal(text="Must be senior", weight="must", action="add")]
    with patch("app.routes.scenarios.propose_criteria", return_value=proposals):
        fetched = _run_refine_one(conn, sid)
    assert fetched["status"] == "done"
    assert "Requesting criteria proposals" in fetched["log"]
    html = fetched["result"]["html_chunks"][0]
    assert "Must be senior" in html


def test_refine_add_proposal_has_editable_inputs(conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Must be remote", "must")
    proposals = [CriterionProposal(text="Must be senior", weight="must", action="add")]
    with patch("app.routes.scenarios.propose_criteria", return_value=proposals):
        fetched = _run_refine_one(conn, sid)
    html = fetched["result"]["html_chunks"][0]
    assert 'name="text_0" value="Must be senior"' in html
    assert 'name="weight_0"' in html
    assert 'name="kind_0" value="add"' in html


def test_refine_proposal_row_has_checked_apply_checkbox_by_default(conn):
    # Batch-apply model: each row defaults to "included" (checked) — the
    # user unchecks the ones they don't want, then applies the whole batch
    # at once. There's no more per-row instant action.
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Must be remote", "must")
    proposals = [CriterionProposal(text="Must be senior", weight="must", action="add")]
    with patch("app.routes.scenarios.propose_criteria", return_value=proposals):
        fetched = _run_refine_one(conn, sid)
    assert '<input type="checkbox" name="apply_0" checked>' in fetched["result"]["html_chunks"][0]


def test_refine_remove_proposal_carries_criterion_id_and_no_editable_inputs(conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    cid = q.insert_criterion(conn, sid, "Must be remote", "must")
    proposals = [CriterionProposal(text="Must be remote", weight="must", action="remove")]
    with patch("app.routes.scenarios.propose_criteria", return_value=proposals):
        fetched = _run_refine_one(conn, sid)
    html = fetched["result"]["html_chunks"][0]
    assert f'name="criterion_id_0" value="{cid}"' in html
    assert 'name="kind_0" value="remove"' in html
    assert 'name="text_0"' not in html
    assert 'name="weight_0"' not in html


def test_refine_remove_proposal_tag_shows_actual_stored_weight(conn):
    # The LLM's proposal can misreport the weight of the criterion it wants
    # removed (it's only given the criterion's text to match against, and
    # can hallucinate a different weight) — the tag must reflect what's
    # actually stored for that criterion, not whatever the model guessed.
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Salary above 1M", "prefer")
    proposals = [CriterionProposal(text="Salary above 1M", weight="must", action="remove")]
    with patch("app.routes.scenarios.propose_criteria", return_value=proposals):
        fetched = _run_refine_one(conn, sid)
    html = fetched["result"]["html_chunks"][0]
    assert '<span class="tag">prefer</span>' in html
    assert '<span class="tag">must</span>' not in html


def test_refine_remove_proposal_tag_shows_weight_not_action(conn):
    # The tag should match the style of the existing criteria list (which
    # shows the weight), not the literal action name ("remove").
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Graduate or junior positions", "avoid")
    proposals = [CriterionProposal(text="Graduate or junior positions", weight="avoid", action="remove")]
    with patch("app.routes.scenarios.propose_criteria", return_value=proposals):
        fetched = _run_refine_one(conn, sid)
    html = fetched["result"]["html_chunks"][0]
    assert '<span class="tag">avoid</span>' in html
    assert '<span class="tag">remove</span>' not in html


def test_refine_remove_proposal_text_is_struck_through(conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Graduate or junior positions", "avoid")
    proposals = [CriterionProposal(text="Graduate or junior positions", weight="avoid", action="remove")]
    with patch("app.routes.scenarios.propose_criteria", return_value=proposals):
        fetched = _run_refine_one(conn, sid)
    assert "text-decoration:line-through" in fetched["result"]["html_chunks"][0]


def test_refine_add_proposal_duplicating_existing_criterion_is_omitted(conn):
    # The LLM shouldn't be trusted to always notice a criterion it was already
    # given already exists — drop "add" proposals that duplicate one directly,
    # rather than showing a nonsensical "add" suggestion for something that's
    # already there (previously observed flapping between add/remove for the
    # same text across refine calls).
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Graduate or junior positions", "avoid")
    proposals = [CriterionProposal(text="Graduate or junior positions", weight="avoid", action="add")]
    with patch("app.routes.scenarios.propose_criteria", return_value=proposals):
        fetched = _run_refine_one(conn, sid)
    html = fetched["result"]["html_chunks"][0]
    assert "Graduate or junior positions" not in html
    assert "No changes proposed" in html


def test_refine_add_proposal_duplicating_existing_criterion_ignores_case_and_whitespace(conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Graduate or junior positions", "avoid")
    proposals = [CriterionProposal(text="  graduate or JUNIOR positions  ", weight="avoid", action="add")]
    with patch("app.routes.scenarios.propose_criteria", return_value=proposals):
        fetched = _run_refine_one(conn, sid)
    assert "No changes proposed" in fetched["result"]["html_chunks"][0]


def test_refine_remove_proposal_unmatched_is_omitted(conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Must be remote", "must")
    proposals = [CriterionProposal(text="Must have a PhD", weight="must", action="remove")]
    with patch("app.routes.scenarios.propose_criteria", return_value=proposals):
        fetched = _run_refine_one(conn, sid)
    html = fetched["result"]["html_chunks"][0]
    assert "Must have a PhD" not in html
    assert "No changes proposed" in html


def test_refine_html_chunk_has_no_embedded_newline(conn):
    # The old streaming protocol required the HTML fragment to be single-line
    # (newlines stripped before being sent as a "HTML:"-prefixed stream line).
    # Now the fragment travels as plain JSON, so embedded newlines are no
    # longer a hazard — this just confirms exactly one chunk comes back.
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Must be remote", "must")
    proposals = [CriterionProposal(text="Must be senior", weight="must", action="add")]
    with patch("app.routes.scenarios.propose_criteria", return_value=proposals):
        fetched = _run_refine_one(conn, sid)
    chunks = fetched["result"]["html_chunks"]
    assert len(chunks) == 1
    assert "Must be senior" in chunks[0]


def test_refine_alone_does_not_mark_feedback_handled(conn):
    # Feedback stays "live" until you actually act on a proposal derived
    # from it — merely running refine and looking at the suggestions
    # shouldn't consume it.
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Must be remote", "must")
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    job_id = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_pipeline(conn, job_id, simplified_content="", content_type="job_posting")
    q.upsert_scenario_feedback(conn, job_id, sid, "too junior", "lower")

    proposals = [CriterionProposal(text="Must be senior", weight="must", action="add")]
    with patch("app.routes.scenarios.propose_criteria", return_value=proposals):
        _run_refine_one(conn, sid)

    assert q.get_recent_feedback_notes(conn, sid) == [{"direction": "lower", "note": "too junior"}]


def test_refine_embeds_feedback_anchor_in_apply_form(conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    job_id = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_pipeline(conn, job_id, simplified_content="", content_type="job_posting")
    q.upsert_scenario_feedback(conn, job_id, sid, "too junior", "lower")
    anchor = q.get_recent_feedback_anchor(conn, sid)

    proposals = [CriterionProposal(text="Must be senior", weight="must", action="add")]
    with patch("app.routes.scenarios.propose_criteria", return_value=proposals):
        fetched = _run_refine_one(conn, sid)
    assert f'name="feedback_anchor" value="{anchor}"' in fetched["result"]["html_chunks"][0]


def test_manual_add_criterion_does_not_mark_feedback_handled(client, conn):
    # The always-instant manual add-form at the bottom of the criteria list
    # is unrelated to the LLM-suggestion batch-apply flow and carries no
    # feedback_anchor — it must never mark anything handled.
    sid = q.insert_scenario(conn, "Remote ML", "")
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    job_id = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_pipeline(conn, job_id, simplified_content="", content_type="job_posting")
    q.upsert_scenario_feedback(conn, job_id, sid, "too junior", "lower")

    resp = client.post(f"/scenarios/{sid}/criteria", data={"text": "Must be senior", "weight": "must"})
    assert resp.status_code == 200
    assert q.get_recent_feedback_notes(conn, sid) == [{"direction": "lower", "note": "too junior"}]


def test_apply_criteria_proposals_shows_confirmation(client, conn):
    sid = q.insert_scenario(conn, "S", "")
    resp = client.post(
        f"/scenarios/{sid}/refine/accept",
        data={"apply_0": "on", "kind_0": "add", "weight_0": "must", "text_0": "Remote OK",
              "feedback_anchor": ""},
    )
    assert "✓" in resp.text and "added" in resp.text


def test_apply_criteria_no_changes_shows_reviewed(client, conn):
    sid = q.insert_scenario(conn, "S", "")
    resp = client.post(f"/scenarios/{sid}/refine/accept", data={"feedback_anchor": ""})
    assert "✓" in resp.text and "Reviewed" in resp.text


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
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    job_id = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_pipeline(conn, job_id, simplified_content="", content_type="job_posting")
    q.upsert_scenario_feedback(conn, job_id, sid, "too junior", "lower")
    anchor = q.get_recent_feedback_anchor(conn, sid)

    resp = client.post(
        f"/scenarios/{sid}/refine/accept",
        data={"kind_0": "add", "text_0": "Must be senior", "weight_0": "must", "feedback_anchor": anchor},
    )
    assert resp.status_code == 200
    assert q.get_recent_feedback_notes(conn, sid) == []
    assert q.get_criteria(conn, sid) == []


def test_apply_batch_only_marks_this_scenarios_feedback_handled(client, conn):
    sid_a = q.insert_scenario(conn, "Remote ML", "")
    sid_b = q.insert_scenario(conn, "Robotics", "")
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    job_id = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_pipeline(conn, job_id, simplified_content="", content_type="job_posting")
    q.upsert_scenario_feedback(conn, job_id, sid_a, "too junior", "lower")
    q.upsert_scenario_feedback(conn, job_id, sid_b, "should count here too", "higher")
    anchor_a = q.get_recent_feedback_anchor(conn, sid_a)

    client.post(
        f"/scenarios/{sid_a}/refine/accept",
        data={"feedback_anchor": anchor_a},
    )

    assert q.get_recent_feedback_notes(conn, sid_a) == []
    assert q.get_recent_feedback_notes(conn, sid_b) == [{"direction": "higher", "note": "should count here too"}]


def test_apply_batch_ignores_malformed_feedback_anchor(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    resp = client.post(
        f"/scenarios/{sid}/refine/accept",
        data={"feedback_anchor": "1; DROP TABLE jobs"},
    )
    assert resp.status_code == 200


def test_apply_batch_clears_proposals_panel_via_oob(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    resp = client.post(
        f"/scenarios/{sid}/refine/accept",
        data={"kind_0": "add", "apply_0": "on", "text_0": "Must be senior", "weight_0": "must"},
    )
    assert f'id="proposals-area-{sid}" hx-swap-oob="true"' in resp.text


def test_refine_all_scenarios_task_execution_renders_per_scenario_html_chunks(conn):
    sid_a = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid_a, "Must be remote", "must")
    sid_b = q.insert_scenario(conn, "Robotics", "")
    q.insert_criterion(conn, sid_b, "Must involve embedded systems", "must")

    proposals = [CriterionProposal(text="Must be senior", weight="must", action="add")]
    task = q.enqueue_task(conn, kind="scenarios_refine_all", params={})
    with patch("app.routes.scenarios.propose_criteria", return_value=proposals):
        execute_task(conn, MagicMock(), "model", MagicMock(), task)

    fetched = q.get_task(conn, task["id"])
    assert fetched["status"] == "done"
    assert "Refining criteria for 2 scenario(s)" in fetched["log"]
    chunks = fetched["result"]["html_chunks"]
    assert len(chunks) == 2
    # Scenario order is preserved: scenario A's chunk arrives before B's.
    assert f"proposals-area-{sid_a}" in chunks[0]
    assert f"proposals-area-{sid_b}" in chunks[1]


def test_refine_all_scenarios_oob_wrapper_preserves_layout_style(conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    proposals = [CriterionProposal(text="Must be senior", weight="must", action="add")]
    task = q.enqueue_task(conn, kind="scenarios_refine_all", params={})
    with patch("app.routes.scenarios.propose_criteria", return_value=proposals):
        execute_task(conn, MagicMock(), "model", MagicMock(), task)

    fetched = q.get_task(conn, task["id"])
    assert 'style="margin-top:0.75rem; width:100%;"' in fetched["result"]["html_chunks"][0]


def test_refine_all_scenarios_embeds_feedback_anchor(conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    job_id = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_pipeline(conn, job_id, simplified_content="", content_type="job_posting")
    q.upsert_scenario_feedback(conn, job_id, sid, "too junior", "lower")
    anchor = q.get_recent_feedback_anchor(conn, sid)

    proposals = [CriterionProposal(text="Must be senior", weight="must", action="add")]
    task = q.enqueue_task(conn, kind="scenarios_refine_all", params={})
    with patch("app.routes.scenarios.propose_criteria", return_value=proposals):
        execute_task(conn, MagicMock(), "model", MagicMock(), task)

    fetched = q.get_task(conn, task["id"])
    assert f'name="feedback_anchor" value="{anchor}"' in fetched["result"]["html_chunks"][0]


def test_refine_all_scenarios_with_no_scenarios(conn):
    task = q.enqueue_task(conn, kind="scenarios_refine_all", params={})
    execute_task(conn, MagicMock(), "model", MagicMock(), task)
    fetched = q.get_task(conn, task["id"])
    assert fetched["status"] == "done"
    assert "Refining criteria for 0 scenario(s)" in fetched["log"]


def test_refine_accept_adds_criteria(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    resp = client.post(
        f"/scenarios/{sid}/refine/accept",
        data={"kind_0": "add", "apply_0": "on", "text_0": "Must be senior", "weight_0": "must"},
    )
    assert resp.status_code == 200
    criteria = q.get_criteria(conn, sid)
    assert any(c["text"] == "Must be senior" for c in criteria)


def test_reevaluate_task_execution_updates_jobs(conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Must be remote", "must")
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    job_id = q.insert_job(conn, source_id=source_id, url="http://job/1", title="ML Eng", company="C", raw_text="r")
    q.update_job_pipeline(conn, job_id, simplified_content="clean", content_type="job_posting")

    task = q.enqueue_task(conn, kind="scenarios_reevaluate_all", params={})
    with patch("app.pipeline.summarize", return_value=JobSummary(title="ML Engineer - Remote @ Acme", headline="Great hook", summary="Updated summary")), \
         patch("app.pipeline.evaluate", return_value=(0.75, "Good match")), \
         patch("app.pipeline.assess_fit", return_value={"interest": 0.5, "interest_reasoning": "x", "attainability": 0.5, "attainability_reasoning": "y"}):
        execute_task(conn, MagicMock(), "model", MagicMock(), task)

    fetched = q.get_task(conn, task["id"])
    assert fetched["status"] == "done"
    assert "Re-evaluating 1 job(s)" in fetched["log"]
    assert "Re-evaluation complete" in fetched["log"]
    score = q.get_job_score(conn, job_id, sid)
    assert score["relevance_score"] == pytest.approx(0.75)
    job = q.get_job(conn, job_id)
    assert job["summary"] == "Updated summary"


def test_reevaluate_includes_accepted_jobs(conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Must be remote", "must")
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    job_id = q.insert_job(conn, source_id=source_id, url="http://job/1", title="ML Eng", company="C", raw_text="r")
    q.update_job_pipeline(conn, job_id, simplified_content="clean", content_type="job_posting")
    q.update_job_feedback(conn, job_id, "accepted", "")

    task = q.enqueue_task(conn, kind="scenarios_reevaluate_all", params={})
    with patch("app.pipeline.summarize", return_value=JobSummary(title="ML Engineer - Remote @ Acme", headline="Great hook", summary="Updated summary")), \
         patch("app.pipeline.evaluate", return_value=(0.75, "Good match")), \
         patch("app.pipeline.assess_fit", return_value={"interest": 0.5, "interest_reasoning": "x", "attainability": 0.5, "attainability_reasoning": "y"}):
        execute_task(conn, MagicMock(), "model", MagicMock(), task)

    fetched = q.get_task(conn, task["id"])
    assert "Re-evaluating 1 job(s)" in fetched["log"]
    score = q.get_job_score(conn, job_id, sid)
    assert score["relevance_score"] == pytest.approx(0.75)


def test_reevaluate_excludes_rejected_and_trash_jobs(conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Must be remote", "must")
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    rejected_id = q.insert_job(conn, source_id=source_id, url="http://job/1", title="A", company="C", raw_text="r")
    q.update_job_pipeline(conn, rejected_id, simplified_content="clean", content_type="job_posting")
    q.update_job_feedback(conn, rejected_id, "rejected", "")
    trash_id = q.insert_job(conn, source_id=source_id, url="http://job/2", title="B", company="C", raw_text="r")
    q.update_job_pipeline(conn, trash_id, simplified_content="clean", content_type="job_posting")
    q.update_job_feedback(conn, trash_id, "trash", "")

    task = q.enqueue_task(conn, kind="scenarios_reevaluate_all", params={})
    with patch("app.pipeline.summarize", return_value=JobSummary(title="Title", headline="Hook", summary="Updated summary")), \
         patch("app.pipeline.evaluate", return_value=(0.75, "Good match")), \
         patch("app.pipeline.assess_fit", return_value={"interest": 0.5, "interest_reasoning": "x", "attainability": 0.5, "attainability_reasoning": "y"}):
        execute_task(conn, MagicMock(), "model", MagicMock(), task)

    fetched = q.get_task(conn, task["id"])
    assert "Re-evaluating 0 job(s)" in fetched["log"]


def test_reevaluate_skips_jobs_already_current(conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Must be remote", "must")
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    q.insert_job(conn, source_id=source_id, url="http://job/1", title="ML Eng", company="C", raw_text="r")
    job_id = q.get_jobs(conn)[0]["id"]
    q.update_job_pipeline(conn, job_id, simplified_content="clean", content_type="job_posting")

    with patch("app.pipeline.summarize", return_value=JobSummary(title="ML Engineer - Remote @ Acme", headline="Great hook", summary="Updated summary")), \
         patch("app.pipeline.evaluate", return_value=(0.75, "Good match")) as mock_evaluate, \
         patch("app.pipeline.assess_fit", return_value={"interest": 0.5, "interest_reasoning": "x", "attainability": 0.5, "attainability_reasoning": "y"}):
        task1 = q.enqueue_task(conn, kind="scenarios_reevaluate_all", params={})
        execute_task(conn, MagicMock(), "model", MagicMock(), task1)
        # A fresh enqueue after the first task has finished is not deduped
        # (enqueue_task only dedupes against a still-queued/running task).
        task2 = q.enqueue_task(conn, kind="scenarios_reevaluate_all", params={})
        execute_task(conn, MagicMock(), "model", MagicMock(), task2)

    assert mock_evaluate.call_count == 1
    fetched2 = q.get_task(conn, task2["id"])
    assert "skipping 1 already current" in fetched2["log"]
    assert "Re-evaluating 0 job(s)" in fetched2["log"]


def test_reevaluate_all_scenarios_combined_progress(conn):
    sid_a = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid_a, "Must be remote", "must")
    sid_b = q.insert_scenario(conn, "Robotics", "")
    q.insert_criterion(conn, sid_b, "Must involve embedded systems", "must")
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    job_id = q.insert_job(conn, source_id=source_id, url="http://job/1", title="ML Eng", company="C", raw_text="r")
    q.update_job_pipeline(conn, job_id, simplified_content="clean", content_type="job_posting")

    task = q.enqueue_task(conn, kind="scenarios_reevaluate_all", params={})
    with patch("app.pipeline.summarize", return_value=JobSummary(title="ML Engineer - Remote @ Acme", headline="Great hook", summary="Updated summary")), \
         patch("app.pipeline.evaluate", return_value=(0.75, "Good match")), \
         patch("app.pipeline.assess_fit", return_value={"interest": 0.5, "interest_reasoning": "x", "attainability": 0.5, "attainability_reasoning": "y"}):
        execute_task(conn, MagicMock(), "model", MagicMock(), task)

    fetched = q.get_task(conn, task["id"])
    assert fetched["status"] == "done"
    assert "Re-evaluating 2 scenario(s)" in fetched["log"]
    assert "All scenarios re-evaluated: 2 job(s) updated across 2 scenario(s); fit recomputed for 1 job(s)" in fetched["log"]
    assert q.get_job_score(conn, job_id, sid_a) is not None
    assert q.get_job_score(conn, job_id, sid_b) is not None


def test_reevaluate_all_scenarios_counts_scenarios_and_jobs_independently(conn):
    sid_a = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid_a, "Must be remote", "must")
    sid_b = q.insert_scenario(conn, "Robotics", "")
    q.insert_criterion(conn, sid_b, "Must involve embedded systems", "must")
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    job_id = q.insert_job(conn, source_id=source_id, url="http://job/1", title="ML Eng", company="C", raw_text="r")
    q.update_job_pipeline(conn, job_id, simplified_content="clean", content_type="job_posting")

    task = q.enqueue_task(conn, kind="scenarios_reevaluate_all", params={})
    with patch("app.pipeline.summarize", return_value=JobSummary(title="ML Engineer - Remote @ Acme", headline="Great hook", summary="Updated summary")), \
         patch("app.pipeline.evaluate", return_value=(0.75, "Good match")), \
         patch("app.pipeline.assess_fit", return_value={"interest": 0.5, "interest_reasoning": "x", "attainability": 0.5, "attainability_reasoning": "y"}):
        execute_task(conn, MagicMock(), "model", MagicMock(), task)

    # One job re-scored per scenario: the scenario counter climbs 1/2 -> 2/2,
    # but the job counter resets per scenario rather than accumulating across
    # scenarios (there is only ever 1 job to score in each one).
    fetched = q.get_task(conn, task["id"])
    assert "[Scenario 1/2: Remote ML] [1/1] Re-scored" in fetched["log"]
    assert "[Scenario 2/2: Robotics] [1/1] Re-scored" in fetched["log"]


def test_reevaluate_all_scenarios_recomputes_fit_for_accepted_and_gate_failed_jobs(conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Must be remote", "must")
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")

    accepted_id = q.insert_job(conn, source_id=source_id, url="http://job/1", title="A", company="C", raw_text="r")
    q.update_job_pipeline(conn, accepted_id, simplified_content="clean", content_type="job_posting")
    q.update_job_feedback(conn, accepted_id, "accepted", "")

    gate_failed_id = q.insert_job(conn, source_id=source_id, url="http://job/2", title="B", company="C", raw_text="r")
    q.update_job_pipeline(conn, gate_failed_id, simplified_content="clean", content_type="job_posting")

    fit_result = {
        "interest": 0.8, "interest_reasoning": "a",
        "attainability": 0.6, "attainability_reasoning": "b",
    }
    task = q.enqueue_task(conn, kind="scenarios_reevaluate_all", params={})
    with patch("app.pipeline.summarize", return_value=JobSummary(title="Title", headline="Hook", summary="Updated summary")), \
         patch("app.pipeline.evaluate", return_value=(0.2, "weak")), \
         patch("app.pipeline.assess_fit", return_value=fit_result):
        execute_task(conn, MagicMock(), "model", MagicMock(), task)

    fetched = q.get_task(conn, task["id"])
    assert fetched["status"] == "done"
    assert "fit recomputed for 2 job(s)" in fetched["log"]
    assert q.get_job(conn, accepted_id)["interest_score"] == pytest.approx(0.8)
    assert q.get_job(conn, gate_failed_id)["interest_score"] == pytest.approx(0.8)


def test_reevaluate_keeps_existing_title_when_ai_title_empty(conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Must be remote", "must")
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    job_id = q.insert_job(conn, source_id=source_id, url="http://job/1", title="ML Eng", company="C", raw_text="r")
    q.update_job_pipeline(conn, job_id, simplified_content="clean", content_type="job_posting")

    task = q.enqueue_task(conn, kind="scenarios_reevaluate_all", params={})
    with patch("app.pipeline.summarize", return_value=JobSummary(summary="Updated summary")), \
         patch("app.pipeline.evaluate", return_value=(0.75, "Good match")), \
         patch("app.pipeline.assess_fit", return_value={"interest": 0.5, "interest_reasoning": "x", "attainability": 0.5, "attainability_reasoning": "y"}):
        execute_task(conn, MagicMock(), "model", MagicMock(), task)

    job = q.get_job(conn, job_id)
    assert job["title"] == "ML Eng"
    assert job["summary"] == "Updated summary"


def test_refine_proposals_sorted_must_prefer_avoid(conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Must be remote", "must")
    proposals = [
        CriterionProposal(text="Avoid on-call", weight="avoid", action="add"),
        CriterionProposal(text="Must pay well", weight="must", action="add"),
        CriterionProposal(text="Prefer Python", weight="prefer", action="add"),
    ]
    task = q.enqueue_task(conn, kind="scenario_refine_one", params={"scenario_id": sid})
    with patch("app.routes.scenarios.propose_criteria", return_value=proposals):
        execute_task(conn, MagicMock(), "model", MagicMock(), task)

    fetched = q.get_task(conn, task["id"])
    html = fetched["result"]["html_chunks"][0]
    must_pos = html.index("Must pay well")
    prefer_pos = html.index("Prefer Python")
    avoid_pos = html.index("Avoid on-call")
    assert must_pos < prefer_pos < avoid_pos


def test_scenarios_page_shows_too_loose_verdict(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    job_id = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, job_id, sid, "too junior", "lower")

    resp = client.get("/scenarios")

    assert "Criteria may be too loose — consider tightening" in resp.text
    assert '0 votes said "too strict"' in resp.text
    assert '1 vote said "too loose"' in resp.text
    assert "pending, will be used in the next refine" in resp.text
    assert "Also applied earlier" not in resp.text


def test_scenarios_page_shows_too_strict_verdict(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    job_id = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, job_id, sid, "worth it", "higher")

    resp = client.get("/scenarios")

    assert "Criteria may be too strict — consider loosening" in resp.text


def test_scenarios_page_shows_balanced_verdict(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T1", company="C", raw_text="r")
    j2 = q.insert_job(conn, source_id=source_id, url="http://job/2", title="T2", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, j1, sid, "a", "higher")
    q.upsert_scenario_feedback(conn, j2, sid, "b", "lower")

    resp = client.get("/scenarios")

    assert "Feedback seems balanced" in resp.text


def test_scenarios_page_shows_already_applied_line_after_handling(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    job_id = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, job_id, sid, "worth it", "higher")
    anchor = q.get_recent_feedback_anchor(conn, sid)
    q.mark_feedback_handled(conn, sid, anchor)

    resp = client.get("/scenarios")

    assert "No unhandled feedback yet" in resp.text
    assert "Also applied earlier (last 30 days): 1 too strict, 0 too loose" in resp.text


def test_scenarios_page_omits_already_applied_line_when_nothing_handled(client, conn):
    q.insert_scenario(conn, "Remote ML", "")

    resp = client.get("/scenarios")

    assert "No unhandled feedback yet" in resp.text
    assert "Also applied earlier" not in resp.text
    assert 'score-badge score-neutral">No unhandled feedback yet' in resp.text


def test_scenarios_page_shows_feedback_refinement_section(client, conn):
    q.insert_scenario(conn, "Remote ML", "")

    resp = client.get("/scenarios")

    assert "Feedback" in resp.text and "Refinement" in resp.text


def test_delete_scenario_route(client, conn):
    sc = q.insert_scenario(conn, "Doomed", "")
    r = client.delete(f"/scenarios/{sc}")
    assert r.status_code == 200
    assert r.headers["HX-Redirect"] == "/scenarios"
    assert q.get_scenario(conn, sc) is None


def test_delete_missing_scenario_404(client):
    assert client.delete("/scenarios/99999").status_code == 404
