from unittest.mock import patch
from app.db import queries as q


def _job(conn):
    conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s','http://x','manual')")
    conn.execute("INSERT INTO jobs (source_id, url, title) VALUES (1,'http://x/1','Role')")
    conn.commit()
    q.save_cv_settings(conn, base_cv="# Me", base_instruction="", base_guardrails="",
                       css="p{color:#111}", default_scope=[1, 2])
    return 1


def test_plan_endpoint_enqueues_task(client, conn):
    jid = _job(conn)
    r = client.post(f"/jobs/{jid}/cv/plan")
    assert r.status_code == 200
    assert "task_id" in r.json()
    assert q.get_task(conn, r.json()["task_id"])["kind"] == "cv_tailor"


def test_generate_endpoint_enqueues_task(client, conn):
    jid = _job(conn)
    r = client.post(f"/jobs/{jid}/cv/generate")
    assert r.status_code == 200
    task = q.get_task(conn, r.json()["task_id"])
    assert task["kind"] == "cv_tailor"
    assert task["params"]["render"] == "preview_pane"


def test_plan_endpoint_404_for_missing_job(client, conn):
    assert client.post("/jobs/999/cv/plan").status_code == 404


def test_save_directives_persists_and_returns_pane(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, scope=[1, 2])  # pre-set; must survive a directives save
    r = client.post(f"/jobs/{jid}/cv/save-directives",
                    data={"tuning_directives": "- foreground Kafka"})
    assert r.status_code == 200
    assert "foreground Kafka" in r.text
    row = q.get_job_cv(conn, jid)
    assert row["tuning_directives"] == "- foreground Kafka"
    assert set(row["scope"]) == {1, 2}          # untouched
    assert row["directives_edited_at"] is not None


def test_save_scope_persists_and_marks_draft_stale(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft", scope=[1])
    conn.execute("UPDATE job_cv SET generated_at = datetime('now', '-1 hour') WHERE job_id = ?", (jid,))
    conn.commit()
    r = client.post(f"/jobs/{jid}/cv/save-scope", data={"scope": ["1", "2"]})
    assert r.status_code == 200
    assert set(q.get_job_cv(conn, jid)["scope"]) == {1, 2}
    assert q.get_job_cv(conn, jid)["scope_edited_at"] is not None
    assert 'data-state="stale"' in r.text  # preview pane reports the draft as stale


def test_save_scope_only_returns_status_fragments_not_the_whole_pane(client, conn):
    # A scope change only ever affects draft/guardrail freshness, never the
    # tailored markdown — the response must be small status fragments, not a
    # full preview-pane re-render (which would reset the active tab, the
    # iframes, and the Edit-tab editor on every checkbox click).
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft", scope=[1])
    r = client.post(f"/jobs/{jid}/cv/save-scope", data={"scope": ["1", "2"]})
    assert r.status_code == 200
    assert 'id="cv-draft-status"' in r.text
    assert 'id="cv-findings"' in r.text
    assert 'cv-preview-tab' not in r.text       # no tab bar
    assert 'cv-preview-doc' not in r.text        # no iframes / editor mount
    assert 'cv-latitude-form' not in r.text      # the checkboxes themselves aren't re-rendered


def test_accept_does_not_block_further_edits(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft")
    client.post(f"/jobs/{jid}/cv/accept")
    r = client.post(f"/jobs/{jid}/cv/save-scope", data={"scope": ["1"]})
    assert r.status_code == 200
    assert q.get_job_cv(conn, jid)["scope"] == [1]


def test_accept_marks_current_version_and_redirects_to_preview(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft")
    r = client.post(f"/jobs/{jid}/cv/accept", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == f"/jobs/{jid}/cv/preview"
    assert q.get_job_cv(conn, jid)["accepted_at"] is not None
    assert "cv" in [e["kind"] for e in q.get_job_events(conn, jid)]


def test_copy_markdown_available_in_both_states(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Tailored\n\n- a bullet with <special> & chars\n")
    page = client.get(f"/jobs/{jid}/cv/preview").text
    assert "Copy markdown" in page and 'class="cv-md-source"' in page
    assert "a bullet with" in page
    q.accept_job_cv(conn, jid)
    page = client.get(f"/jobs/{jid}/cv/preview").text
    assert "Copy markdown" in page and "a bullet with" in page


def test_editing_routes_still_work_after_accept(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft")
    q.accept_job_cv(conn, jid)
    for path, data in [
        (f"/jobs/{jid}/cv/generate", None),
        (f"/jobs/{jid}/cv/plan", None),
        (f"/jobs/{jid}/cv/save-directives", {"tuning_directives": "- allowed edit"}),
        (f"/jobs/{jid}/cv/reset-directives", None),
        (f"/jobs/{jid}/cv/plan/accept", {}),
    ]:
        r = client.post(path, data=data or {})
        assert r.status_code == 200, path
    # reset-directives ran after the save above, restoring the configured
    # (empty, in this fixture) template — confirms the routes really executed.
    assert q.get_job_cv(conn, jid)["tuning_directives"] == ""


def test_accept_400_without_draft(client, conn):
    jid = _job(conn)
    assert client.post(f"/jobs/{jid}/cv/accept").status_code == 400


def test_pdf_404_without_draft(client, conn):
    jid = _job(conn)
    assert client.get(f"/jobs/{jid}/cv.pdf").status_code == 404


def test_pdf_renders_when_draft_present(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft\n\n- x\n")
    with patch("app.routes.cv.render_pdf", return_value=b"%PDF-1.7 fake") as rp:
        r = client.get(f"/jobs/{jid}/cv.pdf")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF")
    rp.assert_called_once()


def test_pdf_render_error_returns_503(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft")
    from app.cv.render import CvRenderError
    with patch("app.routes.cv.render_pdf", side_effect=CvRenderError("doc-write-cli is not installed")):
        r = client.get(f"/jobs/{jid}/cv.pdf")
    assert r.status_code == 503


def test_plan_pane_has_no_save_directives_submit_button(client, conn):
    jid = _job(conn)
    r = client.get(f"/jobs/{jid}/cv")
    assert "Save directives" not in r.text


def test_directives_autosave_handler_is_in_base(client, conn):
    jid = _job(conn)
    page = client.get(f"/jobs/{jid}/cv").text
    assert "cv-directives-form" in page and "save-directives" in page
    assert 'action="/jobs/{}/cv/save-directives"'.format(jid) in page


def test_scope_control_lives_in_the_preview_pane_not_the_plan_pane(client, conn):
    jid = _job(conn)
    plan_page = client.get(f"/jobs/{jid}/cv").text
    preview_page = client.get(f"/jobs/{jid}/cv/preview").text
    assert 'name="scope"' not in plan_page
    assert "Edit scope:" not in plan_page
    assert 'id="cv-latitude-form"' in preview_page
    assert 'name="scope"' in preview_page
    assert "Edit scope" in preview_page


def test_plan_pane_points_at_edit_scope(client, conn):
    jid = _job(conn)
    page = client.get(f"/jobs/{jid}/cv").text
    assert "Edit scope" in page  # the pointer line lives in the plan pane


def test_directives_still_autosave_without_inline_script(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid)  # avoids the first-visit autostart <script>
    page = client.get(f"/jobs/{jid}/cv").text
    assert 'id="cv-directives-form"' in page
    # base.html's own <main> wrapper is the page content; scope past it to
    # avoid tripping over base.html's persistent infrastructure <script> tags
    # (htmx, editor mounts) which are unrelated to this page's content.
    main = page[page.index("<main>"):page.index("</main>")]
    assert "<script" not in main


def test_directives_editor_has_a_saved_hint(client, conn):
    jid = _job(conn)
    page = client.get(f"/jobs/{jid}/cv").text
    assert 'class="cv-save-hint"' in page
    assert 'class="cv-editor-wrap"' in page


def test_accept_button_is_a_plain_post(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft")
    text = client.get(f"/jobs/{jid}/cv/preview").text
    accept_start = text.index("Accept this CV")
    accept_form = text[max(0, accept_start - 300):accept_start]
    assert 'action="/jobs/{}/cv/accept"'.format(jid) in accept_form
    assert "hx-post" not in accept_form


def test_accept_plan_proposals_applies_checked_add(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, plan=[
        {"action": "add", "section": "Skills match", "rationale": "r", "line": "foreground Kafka", "target": None},
    ])
    r = client.post(
        f"/jobs/{jid}/cv/plan/accept",
        data={"action_0": "add", "section_0": "Skills match", "rationale_0": "r",
              "line_0": "foreground Kafka", "apply_0": "on"},
    )
    assert r.status_code == 200
    row = q.get_job_cv(conn, jid)
    assert row["tuning_directives"].splitlines() == ["## Skills match", "", "- foreground Kafka"]
    assert row["plan"] == []


def test_accept_plan_proposals_applies_replace_against_existing_directive(client, conn):
    jid = _job(conn)
    q.set_job_cv_directives(conn, jid, "- mention platform work")
    q.upsert_job_cv(conn, jid, plan=[
        {"action": "replace", "section": "Role relevance", "rationale": "r",
         "line": "tighten the wording", "target": "mention platform work"},
    ])
    r = client.post(
        f"/jobs/{jid}/cv/plan/accept",
        data={"action_0": "replace", "section_0": "Role relevance", "rationale_0": "r",
              "line_0": "tighten the wording", "target_0": "mention platform work", "apply_0": "on"},
    )
    assert r.status_code == 200
    assert q.get_job_cv(conn, jid)["tuning_directives"] == "- tighten the wording"


def test_accept_plan_proposals_unchecked_row_is_recorded_as_handled(client, conn):
    jid = _job(conn)
    q.set_job_cv_directives(conn, jid, "- keep this")
    q.upsert_job_cv(conn, jid, plan=[
        {"action": "add", "section": "Skills match", "rationale": "r", "line": "new suggestion", "target": None},
    ])
    r = client.post(
        f"/jobs/{jid}/cv/plan/accept",
        data={"action_0": "add", "section_0": "Skills match", "rationale_0": "r", "line_0": "new suggestion"},
    )
    assert r.status_code == 200
    row = q.get_job_cv(conn, jid)
    assert row["tuning_directives"] == "- keep this"      # not applied
    assert row["handled_suggestions"][0]["line"] == "new suggestion"
    assert "Handled suggestions (1)" in r.text
    assert "new suggestion" in r.text
    assert "<details" not in r.text  # always visible, not folded


def test_accept_plan_proposals_stale_target_is_safely_skipped(client, conn):
    jid = _job(conn)
    q.set_job_cv_directives(conn, jid, "- something else entirely")
    r = client.post(
        f"/jobs/{jid}/cv/plan/accept",
        data={"action_0": "replace", "section_0": "Role relevance", "rationale_0": "r",
              "line_0": "new wording", "target_0": "no longer present", "apply_0": "on"},
    )
    assert r.status_code == 200
    assert q.get_job_cv(conn, jid)["tuning_directives"] == "- something else entirely"


def test_accept_plan_proposals_blank_line_on_checked_add_is_not_applied(client, conn):
    jid = _job(conn)
    q.set_job_cv_directives(conn, jid, "- keep this")
    before = q.get_job_cv(conn, jid)["tuning_directives"]
    r = client.post(
        f"/jobs/{jid}/cv/plan/accept",
        data={"action_0": "add", "section_0": "Skills match", "rationale_0": "cleared out",
              "line_0": "", "apply_0": "on"},
    )
    assert r.status_code == 200
    row = q.get_job_cv(conn, jid)
    assert row["tuning_directives"] == before   # nothing applied
    assert row["plan"] == []
    assert row["handled_suggestions"] == []     # checked-but-blank isn't "handled", just dropped


def test_accept_plan_proposals_404_for_missing_job(client, conn):
    assert client.post("/jobs/999/cv/plan/accept").status_code == 404


def test_unhandle_suggestion_puts_it_back_in_play(client, conn):
    jid = _job(conn)
    q.add_handled_suggestions(conn, jid, [
        {"action": "add", "section": "Skills match", "line": "name C++ prominently", "rationale": "r"},
        {"action": "add", "section": "Role relevance", "line": "pivot the narrative", "rationale": "r"},
    ])
    r = client.post(f"/jobs/{jid}/cv/handled/0/delete")
    assert r.status_code == 200
    left = q.get_job_cv(conn, jid)["handled_suggestions"]
    assert [h["line"] for h in left] == ["pivot the narrative"]


def test_reset_directives_also_clears_handled(client, conn):
    jid = _job(conn)
    q.add_handled_suggestions(conn, jid, [{"action": "add", "line": "x", "rationale": "r"}])
    client.post(f"/jobs/{jid}/cv/reset-directives")
    assert q.get_job_cv(conn, jid)["handled_suggestions"] == []


def test_plan_pane_button_says_analyze_and_find_improvements(client, conn):
    jid = _job(conn)
    r = client.get(f"/jobs/{jid}/cv")
    assert "Analyze and find improvements" in r.text
    assert "Re-plan" not in r.text


def test_plan_pane_shows_suggestion_form_when_plan_pending(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, plan=[
        {"action": "add", "section": "Skills match", "rationale": "r", "line": "foreground Kafka", "target": None},
    ])
    r = client.get(f"/jobs/{jid}/cv")
    assert 'hx-post="/jobs/{}/cv/plan/accept"'.format(jid) in r.text
    assert "foreground Kafka" in r.text
    assert "Reset editor to proposed plan" not in r.text


def test_accept_plan_proposal_inserts_under_its_section(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, scope=[1])
    q.set_job_cv_directives(conn, jid, "## Skills match\n## Wording and typography")
    r = client.post(f"/jobs/{jid}/cv/plan/accept", data={
        "action_0": "add", "section_0": "Skills match", "rationale_0": "r",
        "line_0": "name Kubernetes", "apply_0": "on"})
    assert r.status_code == 200
    td = q.get_job_cv(conn, jid)["tuning_directives"]
    assert td.splitlines() == ["## Skills match", "", "- name Kubernetes", "## Wording and typography"]


def test_reset_directives_replaces_with_configured_template(client, conn):
    jid = _job(conn)
    q.save_cv_settings(conn, base_cv="", base_instruction="", base_guardrails="",
                       css="", default_scope=[1], directives_template="## A\n## B")
    q.upsert_job_cv(conn, jid, scope=[1])
    q.set_job_cv_directives(conn, jid, "## A\n- something the user wrote")
    r = client.post(f"/jobs/{jid}/cv/reset-directives", data={})
    assert r.status_code == 200
    assert q.get_job_cv(conn, jid)["tuning_directives"] == "## A\n## B"


def test_save_tailored_persists_and_returns_oob(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Old", base_cv_snapshot="# Me\n")
    r = client.post(f"/jobs/{jid}/cv/save-tailored", data={"markdown": "# New hand-edited\n\n- extra\n"})
    assert r.status_code == 200
    assert q.get_job_cv(conn, jid)["tailored_cv"] == "# New hand-edited\n\n- extra\n"
    assert q.get_job_cv(conn, jid)["edited_at"] is not None
    assert 'id="cv-diff-summary"' in r.text and 'hx-swap-oob="true"' in r.text
    assert 'id="cv-findings"' in r.text
    assert 'id="cv-editor-status"' in r.text


def test_save_tailored_marks_guardrails_stale(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Old",
                    guardrail_findings=[{"rule": "r", "verdict": "ok", "explanation": ""}])
    conn.execute("UPDATE job_cv SET guardrails_checked_at = datetime('now', '-1 hour') WHERE job_id = ?", (jid,))
    conn.commit()
    r = client.post(f"/jobs/{jid}/cv/save-tailored", data={"markdown": "# changed\n"})
    assert 'data-state="stale"' in r.text


def test_save_tailored_404_missing_job(client, conn):
    assert client.post("/jobs/999/cv/save-tailored", data={"markdown": "x"}).status_code == 404


def test_save_tailored_succeeds_after_accept(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Done")
    q.accept_job_cv(conn, jid)
    r = client.post(f"/jobs/{jid}/cv/save-tailored", data={"markdown": "# Changed\n"})
    assert r.status_code == 200
    assert q.get_job_cv(conn, jid)["tailored_cv"] == "# Changed\n"


def test_recheck_guardrails_endpoint_enqueues_task(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft")
    r = client.post(f"/jobs/{jid}/cv/recheck-guardrails")
    assert r.status_code == 200
    task = q.get_task(conn, r.json()["task_id"])
    assert task["kind"] == "cv_tailor"
    assert task["params"]["mode"] == "recheck"
    assert task["params"]["render"] == "findings"


def test_recheck_guardrails_succeeds_after_accept(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Done")
    q.accept_job_cv(conn, jid)
    r = client.post(f"/jobs/{jid}/cv/recheck-guardrails")
    assert r.status_code == 200
    task = q.get_task(conn, r.json()["task_id"])
    assert task["kind"] == "cv_tailor"
    assert task["params"]["mode"] == "recheck"
