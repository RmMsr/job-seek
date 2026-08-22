from unittest.mock import patch, MagicMock
from app.db import queries as q
from app.ai.refine_profile import ProfileProposal
from app.task_engine import execute_task


def _run_refine(conn, proposals):
    task = q.enqueue_task(conn, kind="profile_refine", params={})
    with patch("app.routes.profile.propose_profile_changes", return_value=proposals):
        execute_task(conn, MagicMock(), "model", MagicMock(), task)
    return q.get_task(conn, task["id"])


def _seed_unhandled_note(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_feedback(conn, jid, "rejected", "No AI focus")
    return jid


def test_profile_refine_enqueues_task(client, conn):
    _seed_unhandled_note(conn)
    resp = client.post("/profile/refine")
    assert resp.status_code == 200
    data = resp.json()
    task = q.get_task(conn, data["task_id"])
    assert task["kind"] == "profile_refine"
    assert task["status"] == "queued"
    assert data["already_active"] is False


def test_profile_refine_skips_when_no_unhandled_notes(client, conn):
    resp = client.post("/profile/refine")
    assert resp.status_code == 200
    data = resp.json()
    assert data["skipped"] is True
    assert "task_id" not in data
    assert "No notes to review" in data["message"]


def test_profile_refine_reports_already_active_on_second_click(client, conn):
    _seed_unhandled_note(conn)
    first = client.post("/profile/refine").json()
    second = client.post("/profile/refine").json()
    assert first["task_id"] == second["task_id"]
    assert first["already_active"] is False
    assert second["already_active"] is True


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
    task = _run_refine(conn, proposals)
    html = task["result"]["html_chunks"][0]
    assert ">Cancel</button>" in html
    assert "profile-proposals-area').innerHTML=''" in html


def test_refine_profile_no_proposals_has_cancel_button(client, conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_feedback(conn, jid, "rejected", "No AI focus")
    q.upsert_profile(conn, "## Technologies\n\n- Python\n")
    task = _run_refine(conn, [])
    assert ">Cancel</button>" in task["result"]["html_chunks"][0]


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
    task = _run_refine(conn, proposals)
    html = task["result"]["html_chunks"][0]
    assert 'name="anchor_0" value="Tools"' in html
    assert "into" in html and "Tools" in html


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
    task = _run_refine(conn, proposals)
    assert task["status"] == "done"
    assert "AI/ML" in task["result"]["html_chunks"][0]


def test_refine_profile_add_proposal_has_editable_input(client, conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_feedback(conn, jid, "rejected", "No AI focus")
    q.upsert_profile(conn, "## Technologies\n\n- Python\n")
    proposals = [ProfileProposal(section="Technologies", action="add", text="AI/ML", target=None)]
    task = _run_refine(conn, proposals)
    html = task["result"]["html_chunks"][0]
    assert 'type="text" name="text_0" value="AI/ML"' in html
    assert 'name="kind_0" value="add"' in html
    assert 'name="section_0" value="Technologies"' in html
    assert f'name="job_ids" value="{jid}"' in html


def test_refine_profile_replace_proposal_has_editable_new_text_and_readonly_target(client, conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_feedback(conn, jid, "rejected", "No AI focus")
    q.upsert_profile(conn, "## Technologies\n\n- Go\n")
    proposals = [ProfileProposal(section="Technologies", action="replace", text="Golang", target="Go")]
    task = _run_refine(conn, proposals)
    html = task["result"]["html_chunks"][0]
    assert 'type="text" name="text_0" value="Golang"' in html
    assert 'type="hidden" name="target_0" value="Go"' in html


def test_refine_profile_remove_proposal_is_struck_through(client, conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_feedback(conn, jid, "rejected", "No AI focus")
    q.upsert_profile(conn, "## Technologies\n\n- Python\n")
    proposals = [ProfileProposal(section="Technologies", action="remove", text=None, target="Python")]
    task = _run_refine(conn, proposals)
    html = task["result"]["html_chunks"][0]
    assert "text-decoration:line-through" in html
    assert 'name="target_0" value="Python"' in html


def test_refine_profile_groups_proposals_by_section(client, conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_feedback(conn, jid, "rejected", "No AI focus")
    q.upsert_profile(conn, "## Technologies\n\n- Python\n\n## Methodologies\n\n- Agile\n")
    proposals = [
        ProfileProposal(section="Methodologies", action="add", text="Shift-left", target=None),
        ProfileProposal(section="Technologies", action="add", text="Rust", target=None),
    ]
    task = _run_refine(conn, proposals)
    html = task["result"]["html_chunks"][0]
    methodologies_idx = html.index("<h4")
    assert "Methodologies" in html[methodologies_idx:methodologies_idx + 100]
    assert html.index("Methodologies") < html.index("Technologies")
    assert html.index("Shift-left") < html.index("Technologies")


def test_refine_profile_add_proposal_shows_action_tag(client, conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_feedback(conn, jid, "rejected", "No AI focus")
    q.upsert_profile(conn, "## Technologies\n\n- Python\n")
    proposals = [ProfileProposal(section="Technologies", action="add", text="AI/ML", target=None)]
    task = _run_refine(conn, proposals)
    assert '<span class="tag">add</span>' in task["result"]["html_chunks"][0]


def test_refine_profile_replace_proposal_shows_action_tag(client, conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_feedback(conn, jid, "rejected", "No AI focus")
    q.upsert_profile(conn, "## Technologies\n\n- Go\n")
    proposals = [ProfileProposal(section="Technologies", action="replace", text="Golang", target="Go")]
    task = _run_refine(conn, proposals)
    assert '<span class="tag">replace</span>' in task["result"]["html_chunks"][0]


def test_refine_profile_remove_proposal_shows_action_tag(client, conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_feedback(conn, jid, "rejected", "No AI focus")
    q.upsert_profile(conn, "## Technologies\n\n- Python\n")
    proposals = [ProfileProposal(section="Technologies", action="remove", text=None, target="Python")]
    task = _run_refine(conn, proposals)
    assert '<span class="tag">remove</span>' in task["result"]["html_chunks"][0]


def test_refine_profile_no_proposals_shows_message(client, conn):
    q.upsert_profile(conn, "## Technologies\n\n- Python\n")
    task = _run_refine(conn, [])
    assert "No changes proposed" in task["result"]["html_chunks"][0]


def test_refine_profile_no_proposals_still_allows_marking_reviewed(client, conn):
    # Feedback that produced no actionable proposal was still reviewed —
    # the user should be able to mark it handled instead of it resurfacing
    # on every future refine indefinitely.
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_feedback(conn, jid, "rejected", "No AI focus")
    q.upsert_profile(conn, "## Technologies\n\n- Python\n")
    task = _run_refine(conn, [])
    html = task["result"]["html_chunks"][0]
    assert f'name="job_ids" value="{jid}"' in html
    assert 'hx-post="/profile/refine/accept"' in html


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
    _run_refine(conn, proposals)
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
