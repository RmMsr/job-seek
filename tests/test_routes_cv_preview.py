import re
from app.db import queries as q


def _job(conn):
    conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s','http://x','manual')")
    conn.execute("INSERT INTO jobs (source_id, url, title) VALUES (1,'http://x/1','Role')")
    conn.commit()
    q.save_cv_settings(conn, base_cv="# Me", base_instruction="", base_guardrails="",
                       css="", default_scope=["select", "reorder"])
    return 1


def test_preview_page_404_for_missing_job(client):
    assert client.get("/jobs/999/cv/preview").status_code == 404


def test_preview_pane_has_stage_status_indicators(client, conn):
    jid = _job(conn)
    page = client.get(f"/jobs/{jid}/cv/preview").text
    assert page.count('class="cv-stage-status"') == 2
    assert 'data-state="none"' in page


def test_preview_draft_and_guardrail_status_stale_after_scope_change(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft", guardrail_findings=[{"rule": "r", "verdict": "ok", "explanation": ""}])
    conn.execute("UPDATE job_cv SET generated_at = datetime('now', '-1 hour') WHERE job_id = ?", (jid,))
    conn.commit()
    q.set_job_cv_scope(conn, jid, [1])
    page = client.get(f"/jobs/{jid}/cv/preview").text
    # both the draft and the guardrail header track staleness
    assert page.count('data-state="stale"') >= 2


def test_preview_marks_draft_out_of_date_when_base_cv_changed(client, conn):
    from app.routes.cv import _base_hash
    jid = _job(conn)
    settings = q.get_cv_settings(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft", base_hash=_base_hash(settings))
    conn.execute("UPDATE job_cv SET generated_at = datetime('now') WHERE job_id = ?", (jid,))
    fresh = client.get(f"/jobs/{jid}/cv/preview").text
    assert 'data-state="fresh"' in fresh and "Outdated" not in fresh
    q.save_cv_settings(conn, base_cv="# a whole new CV", base_instruction=settings["base_instruction"],
                       base_guardrails=settings["base_guardrails"], css=settings["css"],
                       default_scope=settings["default_scope"],
                       directives_template=settings["directives_template"])
    r = client.get(f"/jobs/{jid}/cv/preview")
    assert '<span id="cv-draft-status" class="cv-stage-status" data-state="stale">Outdated</span>' in r.text


def test_preview_badge_shows_running_while_an_update_runs(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft", base_hash="stale")
    conn.execute("UPDATE job_cv SET generated_at = datetime('now') WHERE job_id = ?", (jid,))
    q.enqueue_task(conn, kind="cv_tailor",
                   params={"job_id": jid, "mode": "generate", "render": "preview_pane"})
    r = client.get(f"/jobs/{jid}/cv/preview")
    assert 'data-state="running"' in r.text
    assert 'class="cv-preview-progress" aria-live="polite">' in r.text


def test_preview_progress_note_hidden_when_no_update_running(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft")
    text = client.get(f"/jobs/{jid}/cv/preview").text
    assert "A tailored update is in progress" in text
    assert 'class="cv-preview-progress" aria-live="polite" hidden' in text


def test_preview_cv_hides_progress_when_only_a_plan_task_is_running(client, conn):
    # a plan run produces no draft and doesn't touch guardrails
    jid = _job(conn)
    q.enqueue_task(conn, kind="cv_tailor",
                   params={"job_id": jid, "mode": "plan", "render": "plan_pane"})
    assert q.cv_generate_task_id(conn, jid) is None
    r = client.get(f"/jobs/{jid}/cv/preview")
    assert 'class="cv-preview-progress" aria-live="polite" hidden' in r.text
    assert 'class="cv-findings-stale" aria-live="polite" hidden' in r.text


def test_guardrail_findings_grouped_by_status_with_counts(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft", guardrail_findings=[
        {"rule": "no invented dates", "verdict": "ok", "explanation": ""},
        {"rule": "no invented tools", "verdict": "ok", "explanation": ""},
        {"rule": "no invented degrees", "verdict": "violated", "explanation": "claims a PhD"},
        {"rule": "no invented metrics", "verdict": "unclear", "explanation": "vague number"},
    ])
    r = client.get(f"/jobs/{jid}/cv/preview")
    text = r.text
    assert "<details" in text and "guardrail" in text.lower()
    findings_block = text[text.index('class="guardrail-summary"'):]
    assert findings_block.index("Guardrails") < findings_block.index("guardrail-bar")
    assert "Failed</strong> (1)" in findings_block
    assert "Unclear</span> (1)" in findings_block
    assert "OK</span> (2)" in findings_block
    assert "claims a PhD" in findings_block
    ok_start = findings_block.index("OK</span> (2)")
    failed_start = findings_block.index("Failed</strong> (1)")
    unclear_start = findings_block.index("Unclear</span> (1)")
    ok_details = findings_block[:ok_start][findings_block[:ok_start].rindex("<details"):]
    failed_details = findings_block[:failed_start][findings_block[:failed_start].rindex("<details"):]
    unclear_details = findings_block[:unclear_start][findings_block[:unclear_start].rindex("<details"):]
    assert ok_details.startswith("<details>")
    assert failed_details.startswith("<details open>")
    assert unclear_details.startswith("<details open>")


def test_guardrails_heading_shows_without_a_draft(client, conn):
    jid = _job(conn)
    text = client.get(f"/jobs/{jid}/cv/preview").text
    assert 'class="guardrail-summary"' in text
    assert ">Guardrails" in text
    assert "after you generate a draft" in text


def test_guardrails_heading_and_bar_show_with_findings(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft",
                    guardrail_findings=[{"rule": "No lies", "verdict": "ok", "explanation": ""}])
    text = client.get(f"/jobs/{jid}/cv/preview").text
    assert 'class="guardrail-summary"' in text
    assert ">Guardrails" in text
    assert 'class="guardrail-bar"' in text
    assert "after you generate a draft" not in text


def test_guardrails_placeholder_when_draft_has_no_findings(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft", guardrail_findings=[])
    text = client.get(f"/jobs/{jid}/cv/preview").text
    assert "No guardrail findings recorded" in text
    assert 'class="guardrail-bar"' not in text


def test_guardrails_placeholder_hidden_while_recheck_runs(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft", guardrail_findings=[])
    q.enqueue_task(conn, kind="cv_tailor",
                   params={"job_id": jid, "mode": "generate", "render": "preview_pane"})
    text = client.get(f"/jobs/{jid}/cv/preview").text
    assert "after you generate a draft" not in text
    assert "No guardrail findings recorded" not in text
    assert 'class="cv-findings-stale" aria-live="polite">' in text


def test_scope_selector_is_an_edit_scope_row_with_descriptions_foldout(client, conn):
    jid = _job(conn)
    text = client.get(f"/jobs/{jid}/cv/preview").text
    assert "<legend>Edit scope</legend>" not in text
    assert 'class="cv-scope-row"' in text
    assert "Edit scope" in text
    assert 'class="cv-scope-help"' in text
    assert "<summary>Descriptions</summary>" in text
    assert "<details class=\"cv-scope-help\">" in text and "<details class=\"cv-scope-help\" open>" not in text


def test_scope_selector_checkbox_state_reflects_job_cv_scope(client, conn):
    jid = _job(conn)
    opts = q.get_scope_options(conn)
    chosen, other = opts[0]["id"], opts[1]["id"]
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft", scope=[chosen])
    text = client.get(f"/jobs/{jid}/cv/preview").text
    assert 'class="cv-scope-row"' in text
    form_start = text.index('id="cv-latitude-form"')
    form_end = text.index("</form>", form_start)
    assert form_start < text.index('class="cv-scope-row"', form_start) < form_end
    assert f'value="{chosen}" checked>' in text
    assert f'value="{other}" checked>' not in text


def test_scope_checkboxes_use_live_descriptions(client, conn):
    import html
    jid = _job(conn)
    opts = q.get_scope_options(conn)
    r = client.get(f"/jobs/{jid}/cv/preview")
    text = html.unescape(r.text)
    for opt in opts:
        assert opt["description"] in text
    assert f'value="{opts[0]["id"]}"' in r.text
    if opts[0].get("name"):
        assert f'<dt>{opts[0]["name"]}</dt>' in r.text
        assert f'#{opts[0]["id"]} {opts[0]["name"]}' not in text


def test_preview_pane_layout_guardrails_under_preview_controls_below_iframe(client, conn):
    from unittest.mock import patch
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft\n\n- x\n", scope=[1],
                    guardrail_findings=[{"rule": "r", "verdict": "ok", "explanation": ""}])
    with patch("app.routes.cv.doc_write_available", return_value=True):
        text = client.get(f"/jobs/{jid}/cv/preview").text
    actions = text[text.index('class="cv-preview-actions"'):text.index('class="cv-preview-bar"')]
    assert ">Update</button>" in actions
    assert "Accept this CV" not in actions and "Download PDF" not in actions
    stage = text.index('class="cv-preview-stage')
    assert stage < text.index("Accept this CV") < text.index('id="cv-findings"')


def test_preview_pane_has_base_tailored_tabs(client, conn):
    from unittest.mock import patch
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft\n")
    with patch("app.routes.cv.doc_write_available", return_value=True):
        r = client.get(f"/jobs/{jid}/cv/preview")
    assert 'data-variant="base"' in r.text
    assert 'data-variant="tailored"' in r.text
    assert 'class="cv-preview-stage' in r.text
    assert r.text.count('class="cv-preview-doc') == 4
    assert 'class="cv-preview-doc is-active" data-variant="tailored"' in r.text
    assert 'src="/jobs/%d/cv/preview.html?variant=tailored"' % jid in r.text
    assert 'data-src="/jobs/%d/cv/preview.html?variant=base"' % jid in r.text
    assert 'src="/jobs/%d/cv/preview.html?variant=base"' % jid not in r.text.replace("data-src", "xxxx")
    assert 'class="btn btn-subtle cv-preview-fs"' in r.text


def test_preview_pane_tailored_tab_disabled_without_draft(client, conn):
    from unittest.mock import patch
    jid = _job(conn)
    with patch("app.routes.cv.doc_write_available", return_value=True):
        r = client.get(f"/jobs/{jid}/cv/preview")
    m = re.search(r'<button[^>]*class="cv-preview-tab"[^>]*data-variant="tailored"[^>]*>', r.text)
    assert m and "disabled" in m.group(0)
    assert r.text.count('class="cv-preview-doc') == 1
    assert 'class="cv-preview-doc is-active" data-variant="base"' in r.text
    assert 'src="/jobs/%d/cv/preview.html?variant=base"' % jid in r.text


def test_preview_pane_shows_progress_note_while_a_generate_task_runs(client, conn):
    from unittest.mock import patch
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft\n")
    q.enqueue_task(conn, kind="cv_tailor",
                   params={"job_id": jid, "mode": "generate", "render": "preview_pane"})
    tid = q.cv_generate_task_id(conn, jid)
    assert tid is not None
    with patch("app.routes.cv.doc_write_available", return_value=True):
        r = client.get(f"/jobs/{jid}/cv/preview")
    assert 'class="cv-preview-progress" aria-live="polite">' in r.text
    assert "A tailored update is in progress" in r.text
    assert f"__cvWatchGenerate({tid})" in r.text


def test_preview_cv_has_edit_tab_and_mount_when_draft(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft body\n")
    from unittest.mock import patch
    with patch("app.routes.cv.doc_write_available", return_value=True):
        r = client.get(f"/jobs/{jid}/cv/preview")
    assert 'data-variant="edit"' in r.text
    assert 'data-md-editor-autosave-url="/jobs/%d/cv/save-tailored"' % jid in r.text
    assert "# Draft body" in r.text
    assert r.text.index('data-variant="edit"') < r.text.index('data-variant="diff"')


def test_preview_cv_edit_tab_disabled_without_draft(client, conn):
    jid = _job(conn)
    from unittest.mock import patch
    with patch("app.routes.cv.doc_write_available", return_value=True):
        r = client.get(f"/jobs/{jid}/cv/preview")
    assert re.search(r'data-variant="edit"[^>]*disabled', r.text) or 'data-variant="edit"' not in r.text


def test_preview_cv_no_docwrite_uses_editor_not_readonly_div(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft body\n")
    from unittest.mock import patch
    with patch("app.routes.cv.doc_write_available", return_value=False):
        r = client.get(f"/jobs/{jid}/cv/preview")
    assert "data-md-editor" in r.text


def test_preview_pane_markdown_fallback_without_doc_write(client, conn):
    from unittest.mock import patch
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft body\n")
    with patch("app.routes.cv.doc_write_available", return_value=False):
        r = client.get(f"/jobs/{jid}/cv/preview")
    assert 'class="cv-preview-stage"' not in r.text
    assert "Draft body" in r.text


def test_preview_pane_has_differences_tab(client, conn):
    from unittest.mock import patch
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft\n", base_cv_snapshot="# Base\n")
    with patch("app.routes.cv.doc_write_available", return_value=True):
        r = client.get(f"/jobs/{jid}/cv/preview")
    assert 'data-variant="diff"' in r.text
    assert f'data-src="/jobs/{jid}/cv/diff.html"' in r.text
    assert "What tailoring changed" not in r.text
    assert 'id="cv-change-report"' not in r.text


def test_preview_pane_shows_change_digest_above_the_tabs(client, conn):
    from unittest.mock import patch
    jid = _job(conn)
    q.upsert_job_cv(
        conn, jid,
        base_cv_snapshot="# CV\n\n## Summary\n\nEngineer.\n\n## Skills\n\n- kept\n- dropped bullet\n",
        tailored_cv="# CV\n\n## Summary\n\nEngineer.\n\nBrand new intro paragraph.\n\n## Skills\n\n- kept\n",
    )
    with patch("app.routes.cv.doc_write_available", return_value=True):
        r = client.get(f"/jobs/{jid}/cv/preview")
    assert '<p class="cv-diff-summary-line">' in r.text
    assert "1 bullet dropped" in r.text
    assert "Brand new intro paragraph." in r.text
    assert r.text.index('<div class="cv-diff-summary">') < r.text.index('data-variant="diff"')


def test_preview_pane_no_digest_before_first_draft(client, conn):
    from unittest.mock import patch
    jid = _job(conn)
    with patch("app.routes.cv.doc_write_available", return_value=True):
        r = client.get(f"/jobs/{jid}/cv/preview")
    assert '<div class="cv-diff-summary">' not in r.text


def test_differences_tab_disabled_without_draft(client, conn):
    from unittest.mock import patch
    jid = _job(conn)
    with patch("app.routes.cv.doc_write_available", return_value=True):
        r = client.get(f"/jobs/{jid}/cv/preview")
    assert 'data-variant="diff"' in r.text
    assert f'/jobs/{jid}/cv/diff.html' not in r.text


def test_accepted_view_has_three_tabs(client, conn):
    from unittest.mock import patch
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Final\n", base_cv_snapshot="# Base\n")
    q.finalize_job_cv(conn, jid)
    with patch("app.routes.cv.doc_write_available", return_value=True):
        r = client.get(f"/jobs/{jid}/cv/preview")
    assert r.text.count('class="cv-preview-tab"') == 3
    assert 'data-variant="diff"' in r.text


def test_preview_cv_first_visit_shows_empty_preview_state(client, conn):
    jid = _job(conn)
    text = client.get(f"/jobs/{jid}/cv/preview").text
    assert "No tailored CV yet" in text
    assert "Accept this CV" not in text


def test_findings_stale_note_hidden_when_no_generate_running(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft")
    text = client.get(f"/jobs/{jid}/cv/preview").text
    assert 'class="cv-findings-stale" aria-live="polite" hidden' in text


def test_findings_stale_note_visible_while_generate_runs(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft")
    q.enqueue_task(conn, kind="cv_tailor",
                   params={"job_id": jid, "mode": "generate", "render": "preview_pane"})
    text = client.get(f"/jobs/{jid}/cv/preview").text
    assert 'class="cv-findings-stale" aria-live="polite">' in text


def test_findings_container_present_on_first_generate_without_draft(client, conn):
    jid = _job(conn)
    q.enqueue_task(conn, kind="cv_tailor",
                   params={"job_id": jid, "mode": "generate", "render": "preview_pane"})
    text = client.get(f"/jobs/{jid}/cv/preview").text
    assert 'id="cv-findings"' in text
    assert 'class="cv-findings-stale" aria-live="polite">' in text


def test_preview_cv_shows_header_with_active_subnav_link(client, conn):
    jid = _job(conn)
    page = client.get(f"/jobs/{jid}/cv/preview").text
    assert '<nav class="job-subnav">' in page
    subnav = page[page.index('<nav class="job-subnav">'):page.index('</nav>', page.index('<nav class="job-subnav">'))]
    preview_link = subnav[subnav.index(f'href="/jobs/{jid}/cv/preview"'):]
    assert 'class="active"' in preview_link[:70]
