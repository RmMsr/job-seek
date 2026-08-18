from unittest.mock import patch
from app.db import queries as q
from app.ai.refine_profile import ProfileProposal


def test_profile_page_returns_200(client):
    resp = client.get("/profile")
    assert resp.status_code == 200


def test_profile_save_and_display(client, conn):
    resp = client.post("/profile", data={"content": "I am a senior ML engineer."})
    assert resp.status_code == 200
    assert q.get_profile(conn) == "I am a senior ML engineer."
    resp2 = client.get("/profile")
    assert "I am a senior ML engineer." in resp2.text


def test_profile_save_shows_fading_confirmation(client, conn):
    resp = client.post("/profile", data={"content": "content"})
    assert resp.status_code == 200
    assert '<div class="save-confirmation" aria-live="polite">Saved.</div>' in resp.text


def test_profile_page_has_reevaluate_everything_button(client):
    resp = client.get("/profile")
    assert resp.status_code == 200
    assert 'data-progress-url="/scenarios/reevaluate"' in resp.text
    assert "Re-evaluate everything" in resp.text


def test_profile_page_has_headings_and_lists_hint(client):
    resp = client.get("/profile")
    assert "Use markdown headings and lists like this to allow automated improvements." in resp.text


def test_profile_page_formatting_tips_are_foldable(client):
    resp = client.get("/profile")
    assert "<details" in resp.text
    assert "<summary" in resp.text
    assert "Formatting tips" in resp.text
    assert "# Heading 1" in resp.text
    assert "## Heading 2" in resp.text
    assert "### Heading 3" in resp.text


def test_profile_page_formatting_tips_use_code_block_with_background(client):
    resp = client.get("/profile")
    assert "<pre" in resp.text
    assert "background:#f8f9fa" in resp.text
    assert "font-family:monospace" in resp.text


def test_profile_page_has_unsaved_warning_and_lock_script(client):
    resp = client.get("/profile")
    assert 'id="profile-unsaved-warning"' in resp.text
    assert 'id="profile-save-now-btn"' in resp.text
    assert "isDirty" in resp.text
    assert "MutationObserver" in resp.text


def test_profile_page_lock_script_greys_out_readonly_textarea(client):
    resp = client.get("/profile")
    assert 'textarea.style.background = locked ? "#e9ecef" : ""' in resp.text


def test_profile_page_lock_script_locks_on_click_not_on_data_arrival(client):
    resp = client.get("/profile")
    assert "setLocked(true);" in resp.text
    assert 'data-progress-running' in resp.text


def test_refine_profile_proposals_have_cancel_button(client, conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_feedback(conn, jid, "rejected", "No AI focus")
    q.upsert_profile(conn, "## Technologies\n\n- Python\n")
    proposals = [ProfileProposal(section="Technologies", action="add", text="AI/ML", target=None)]
    with patch("app.routes.profile.propose_profile_changes", return_value=proposals):
        resp = client.post("/profile/refine")
    assert ">Cancel</button>" in resp.text
    assert "profile-proposals-area').innerHTML=''" in resp.text


def test_refine_profile_no_proposals_has_cancel_button(client, conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_feedback(conn, jid, "rejected", "No AI focus")
    q.upsert_profile(conn, "## Technologies\n\n- Python\n")
    with patch("app.routes.profile.propose_profile_changes", return_value=[]):
        resp = client.post("/profile/refine")
    assert ">Cancel</button>" in resp.text


def test_profile_page_shows_unhandled_notes_count(client, conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_feedback(conn, jid, "rejected", "No AI focus")
    resp = client.get("/profile")
    assert "1 note not yet reviewed" in resp.text


def test_refine_profile_add_proposal_with_anchor_carries_anchor_field(client, conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_feedback(conn, jid, "rejected", "No AI focus")
    q.upsert_profile(conn, "## Team Setup\n\nTools\n\n- Slack\n\nTimezone: UTC+1.\n")
    proposals = [
        ProfileProposal(section="Team Setup", action="add", text="Linear", target=None, anchor="Tools")
    ]
    with patch("app.routes.profile.propose_profile_changes", return_value=proposals):
        resp = client.post("/profile/refine")
    assert 'name="anchor_0" value="Tools"' in resp.text
    assert "into" in resp.text and "Tools" in resp.text


def test_accept_profile_proposals_applies_add_with_anchor_into_correct_list(client, conn):
    q.upsert_profile(conn, "## Team Setup\n\nTools\n\n- Slack\n- Jira\n\nTimezone: UTC+1.\n")
    client.post(
        "/profile/refine/accept",
        data={
            "kind_0": "add", "section_0": "Team Setup", "text_0": "Linear",
            "anchor_0": "Tools", "apply_0": "on",
        },
    )
    lines = q.get_profile(conn).splitlines()
    jira_idx = lines.index("- Jira")
    assert lines[jira_idx + 1] == "- Linear"
    assert lines[jira_idx + 2] == ""
    assert lines[jira_idx + 3] == "Timezone: UTC+1."


def test_refine_profile_returns_proposals(client, conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_feedback(conn, jid, "rejected", "No AI focus")
    q.upsert_profile(conn, "## Technologies\n\n- Python\n")
    proposals = [ProfileProposal(section="Technologies", action="add", text="AI/ML", target=None)]
    with patch("app.routes.profile.propose_profile_changes", return_value=proposals):
        resp = client.post("/profile/refine")
    assert resp.status_code == 200
    assert "HTML:" in resp.text
    assert "AI/ML" in resp.text


def test_refine_profile_add_proposal_has_editable_input(client, conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_feedback(conn, jid, "rejected", "No AI focus")
    q.upsert_profile(conn, "## Technologies\n\n- Python\n")
    proposals = [ProfileProposal(section="Technologies", action="add", text="AI/ML", target=None)]
    with patch("app.routes.profile.propose_profile_changes", return_value=proposals):
        resp = client.post("/profile/refine")
    assert 'type="text" name="text_0" value="AI/ML"' in resp.text
    assert 'name="kind_0" value="add"' in resp.text
    assert 'name="section_0" value="Technologies"' in resp.text
    assert f'name="job_ids" value="{jid}"' in resp.text


def test_refine_profile_replace_proposal_has_editable_new_text_and_readonly_target(client, conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_feedback(conn, jid, "rejected", "No AI focus")
    q.upsert_profile(conn, "## Technologies\n\n- Go\n")
    proposals = [ProfileProposal(section="Technologies", action="replace", text="Golang", target="Go")]
    with patch("app.routes.profile.propose_profile_changes", return_value=proposals):
        resp = client.post("/profile/refine")
    assert 'type="text" name="text_0" value="Golang"' in resp.text
    assert 'type="hidden" name="target_0" value="Go"' in resp.text


def test_refine_profile_remove_proposal_is_struck_through(client, conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_feedback(conn, jid, "rejected", "No AI focus")
    q.upsert_profile(conn, "## Technologies\n\n- Python\n")
    proposals = [ProfileProposal(section="Technologies", action="remove", text=None, target="Python")]
    with patch("app.routes.profile.propose_profile_changes", return_value=proposals):
        resp = client.post("/profile/refine")
    assert "text-decoration:line-through" in resp.text
    assert 'name="target_0" value="Python"' in resp.text


def test_refine_profile_groups_proposals_by_section(client, conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_feedback(conn, jid, "rejected", "No AI focus")
    q.upsert_profile(conn, "## Technologies\n\n- Python\n\n## Methodologies\n\n- Agile\n")
    proposals = [
        ProfileProposal(section="Methodologies", action="add", text="Shift-left", target=None),
        ProfileProposal(section="Technologies", action="add", text="Rust", target=None),
    ]
    with patch("app.routes.profile.propose_profile_changes", return_value=proposals):
        resp = client.post("/profile/refine")
    methodologies_idx = resp.text.index("<h4")
    assert "Methodologies" in resp.text[methodologies_idx:methodologies_idx + 100]
    assert resp.text.index("Methodologies") < resp.text.index("Technologies")
    assert resp.text.index("Shift-left") < resp.text.index("Technologies")


def test_refine_profile_add_proposal_shows_action_tag(client, conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_feedback(conn, jid, "rejected", "No AI focus")
    q.upsert_profile(conn, "## Technologies\n\n- Python\n")
    proposals = [ProfileProposal(section="Technologies", action="add", text="AI/ML", target=None)]
    with patch("app.routes.profile.propose_profile_changes", return_value=proposals):
        resp = client.post("/profile/refine")
    assert '<span class="tag">add</span>' in resp.text


def test_refine_profile_replace_proposal_shows_action_tag(client, conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_feedback(conn, jid, "rejected", "No AI focus")
    q.upsert_profile(conn, "## Technologies\n\n- Go\n")
    proposals = [ProfileProposal(section="Technologies", action="replace", text="Golang", target="Go")]
    with patch("app.routes.profile.propose_profile_changes", return_value=proposals):
        resp = client.post("/profile/refine")
    assert '<span class="tag">replace</span>' in resp.text


def test_refine_profile_remove_proposal_shows_action_tag(client, conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_feedback(conn, jid, "rejected", "No AI focus")
    q.upsert_profile(conn, "## Technologies\n\n- Python\n")
    proposals = [ProfileProposal(section="Technologies", action="remove", text=None, target="Python")]
    with patch("app.routes.profile.propose_profile_changes", return_value=proposals):
        resp = client.post("/profile/refine")
    assert '<span class="tag">remove</span>' in resp.text


def test_refine_profile_no_proposals_shows_message(client, conn):
    q.upsert_profile(conn, "## Technologies\n\n- Python\n")
    with patch("app.routes.profile.propose_profile_changes", return_value=[]):
        resp = client.post("/profile/refine")
    assert "No changes proposed" in resp.text


def test_refine_profile_no_proposals_still_allows_marking_reviewed(client, conn):
    # Feedback that produced no actionable proposal was still reviewed —
    # the user should be able to mark it handled instead of it resurfacing
    # on every future refine indefinitely.
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_feedback(conn, jid, "rejected", "No AI focus")
    q.upsert_profile(conn, "## Technologies\n\n- Python\n")
    with patch("app.routes.profile.propose_profile_changes", return_value=[]):
        resp = client.post("/profile/refine")
    assert f'name="job_ids" value="{jid}"' in resp.text
    assert 'hx-post="/profile/refine/accept"' in resp.text


def test_accept_with_no_proposals_marks_submitted_job_ids_handled(client, conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_feedback(conn, jid, "rejected", "No AI focus")
    q.upsert_profile(conn, "## Technologies\n\n- Python\n")

    client.post("/profile/refine/accept", data={"job_ids": str(jid)})

    assert q.get_unhandled_profile_notes(conn) == []


def test_refine_profile_alone_does_not_mark_feedback_handled(client, conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_feedback(conn, jid, "rejected", "No AI focus")
    q.upsert_profile(conn, "## Technologies\n\n- Python\n")
    proposals = [ProfileProposal(section="Technologies", action="add", text="AI/ML", target=None)]
    with patch("app.routes.profile.propose_profile_changes", return_value=proposals):
        client.post("/profile/refine")
    assert len(q.get_unhandled_profile_notes(conn)) == 1


def test_accept_profile_proposals_applies_checked_add(client, conn):
    q.upsert_profile(conn, "## Technologies\n\n- Python\n")
    resp = client.post(
        "/profile/refine/accept",
        data={
            "kind_0": "add", "section_0": "Technologies", "text_0": "AI/ML", "apply_0": "on",
        },
    )
    assert resp.status_code == 200
    assert "- AI/ML" in q.get_profile(conn)


def test_accept_profile_proposals_skips_unchecked(client, conn):
    q.upsert_profile(conn, "## Technologies\n\n- Python\n")
    client.post(
        "/profile/refine/accept",
        data={"kind_0": "add", "section_0": "Technologies", "text_0": "AI/ML"},
    )
    assert "AI/ML" not in q.get_profile(conn)


def test_accept_profile_proposals_lists_unchecked_as_unapplied(client, conn):
    q.upsert_profile(conn, "## Technologies\n\n- Python\n")
    resp = client.post(
        "/profile/refine/accept",
        data={
            "kind_0": "add", "section_0": "Technologies", "text_0": "AI/ML", "apply_0": "on",
            "kind_1": "add", "section_1": "Methodologies", "text_1": "TDD",
        },
    )
    assert "Not applied" in resp.text
    assert "TDD" in resp.text
    assert "AI/ML" in q.get_profile(conn)
    assert "TDD" not in q.get_profile(conn)


def test_accept_profile_proposals_no_unapplied_section_when_all_checked(client, conn):
    q.upsert_profile(conn, "## Technologies\n\n- Python\n")
    resp = client.post(
        "/profile/refine/accept",
        data={"kind_0": "add", "section_0": "Technologies", "text_0": "AI/ML", "apply_0": "on"},
    )
    assert "Not applied" not in resp.text


def test_accept_profile_proposals_applies_checked_remove(client, conn):
    q.upsert_profile(conn, "## Technologies\n\n- Python\n- Go\n")
    client.post(
        "/profile/refine/accept",
        data={
            "kind_0": "remove", "section_0": "Technologies", "target_0": "Go", "apply_0": "on",
        },
    )
    assert "- Go" not in q.get_profile(conn).splitlines()


def test_accept_profile_proposals_marks_submitted_job_ids_handled(client, conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_feedback(conn, jid, "rejected", "No AI focus")
    q.upsert_profile(conn, "## Technologies\n\n- Python\n")

    client.post(
        "/profile/refine/accept",
        data={
            "kind_0": "add", "section_0": "Technologies", "text_0": "AI/ML", "apply_0": "on",
            "job_ids": str(jid),
        },
    )

    assert q.get_unhandled_profile_notes(conn) == []


def test_accept_profile_proposals_leaves_unhandled_when_not_submitted(client, conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_feedback(conn, jid, "rejected", "No AI focus")
    q.upsert_profile(conn, "## Technologies\n\n- Python\n")

    client.post(
        "/profile/refine/accept",
        data={"kind_0": "add", "section_0": "Technologies", "text_0": "AI/ML", "apply_0": "on"},
    )

    assert len(q.get_unhandled_profile_notes(conn)) == 1
