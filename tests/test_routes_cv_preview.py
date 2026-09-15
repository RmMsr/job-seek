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
    assert f'data-src="/jobs/{jid}/cv/diff.html?against_type=base&against_id=' in r.text
    assert "What tailoring changed" not in r.text
    assert 'id="cv-change-report"' not in r.text


def test_preview_pane_diff_picker_defaults_to_accepted_base(client, conn):
    from unittest.mock import patch
    jid = _job(conn)
    q.accept_base_cv(conn)
    accepted_id = q.get_cv_settings(conn)["current_version_id"]
    accepted_hash = q.get_version(conn, "base", 1, accepted_id)["hash"]
    q.upsert_job_cv(conn, jid, tailored_cv="draft 1")

    with patch("app.routes.cv.doc_write_available", return_value=True):
        r = client.get(f"/jobs/{jid}/cv/preview")
    tab = r.text[r.text.index('data-variant="diff"'):r.text.index("</button>", r.text.index('data-variant="diff"'))]
    # "to accepted" isn't repeated on the tab itself — the adjacent picker
    # trigger already names the target.
    assert tab.rstrip().endswith(">Differences to")
    assert f"against_type=base&against_id={accepted_id}" in r.text
    # The nav also embeds a compact picker (the History one) with the same
    # classes, so scope the search to after the tab bar to reach the
    # diff-target picker specifically.
    bar_start = r.text.index('class="cv-preview-tabs"')
    trigger_start = r.text.index('class="cv-version-trigger cv-version-trigger-sm"', bar_start)
    trigger = r.text[trigger_start:r.text.index("</button>", trigger_start)]
    assert accepted_hash[:6] in trigger
    assert 'cv-version-badge-accepted' in trigger


def test_preview_pane_diff_picker_offers_own_versions_and_switches_target(client, conn):
    from unittest.mock import patch
    jid = _job(conn)
    q.accept_base_cv(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="draft 1")
    old_tailored_id = q.get_job_cv(conn, jid)["current_version_id"]
    q.upsert_job_cv(conn, jid, tailored_cv="draft 2")

    with patch("app.routes.cv.doc_write_available", return_value=True):
        r = client.get(f"/jobs/{jid}/cv/preview")
    # The page's own History section (below the content) also has a
    # cv-version-menu, so scope the search to right after the tab bar to
    # reach the diff-target picker's menu specifically — it appears first.
    bar_start = r.text.index('class="cv-preview-tabs"')
    menu_start = r.text.index('class="cv-version-menu"', bar_start)
    menu = r.text[menu_start:r.text.index("</div>\n    </div>", menu_start)]
    # No group headings — each row names its own entity inline instead.
    assert menu.count("Base CV") == 1  # this job's one accepted base version
    assert "Job CV" in menu
    assert f"against_type=tailored&against_id={old_tailored_id}" in menu
    # the current tailored version doesn't offer itself as a diff target
    current_tailored_id = q.get_job_cv(conn, jid)["current_version_id"]
    assert f"against_type=tailored&against_id={current_tailored_id}" not in menu

    with patch("app.routes.cv.doc_write_available", return_value=True):
        r = client.get(f"/jobs/{jid}/cv/preview?against_type=tailored&against_id={old_tailored_id}")
    assert f"data-src=\"/jobs/{jid}/cv/diff.html?against_type=tailored&against_id={old_tailored_id}\"" in r.text


def test_preview_pane_diff_picker_rejects_bad_targets(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="draft 1")
    assert client.get(f"/jobs/{jid}/cv/preview?against_type=tailored&against_id=99999").status_code == 404
    assert client.get(f"/jobs/{jid}/cv/preview?against_type=bogus&against_id=1").status_code == 404
    base_id = q.get_cv_settings(conn)["current_version_id"]
    # a base version id used with against_type=tailored doesn't belong to this job
    assert client.get(f"/jobs/{jid}/cv/preview?against_type=tailored&against_id={base_id}").status_code == 404


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


def test_accept_does_not_change_the_preview_view(client, conn):
    from unittest.mock import patch
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Final\n", base_cv_snapshot="# Base\n")
    q.accept_job_cv(conn, jid)
    with patch("app.routes.cv.doc_write_available", return_value=True):
        r = client.get(f"/jobs/{jid}/cv/preview")
    # Accepting no longer freezes the view — the normal editable preview pane
    # (with its Update button and edit affordances) still renders.
    assert ">Update</button>" in r.text
    assert 'data-variant="diff"' in r.text


def test_preview_page_shows_read_only_historic_version(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="draft 1")
    old_id = q.get_job_cv(conn, jid)["current_version_id"]
    q.upsert_job_cv(conn, jid, tailored_cv="draft 2")

    r = client.get(f"/jobs/{jid}/cv/preview?version={old_id}")
    assert r.status_code == 200
    assert "Reopen this version" in r.text
    # read-only: no Edit tab / editor mounted. (Not `'cv-preview-editor' not in
    # r.text` — that class name is also baked into base.html's global <style>
    # block on every page, so it's always present regardless of this page's
    # content; `data-variant="edit"` is what _preview_tabs.html actually gates
    # on `editable`.)
    assert 'data-variant="edit"' not in r.text

    # Reopen sits beside the History picker, in the same row, ahead of the
    # separate Download PDF/Copy markdown action bar.
    row_pos = r.text.index('class="cv-version-row"')
    trigger_pos = r.text.index('class="btn cv-version-trigger"')
    reopen_pos = r.text.index("Reopen this version")
    pdf_pos = r.text.index("Download PDF")
    assert row_pos < trigger_pos < reopen_pos < pdf_pos


def test_preview_page_read_only_version_offers_accept(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="draft 1")
    old_id = q.get_job_cv(conn, jid)["current_version_id"]
    q.upsert_job_cv(conn, jid, tailored_cv="draft 2")

    r = client.get(f"/jobs/{jid}/cv/preview?version={old_id}")
    assert "&#9733; Accept this version</button>" in r.text

    r = client.post(f"/jobs/{jid}/cv/versions/{old_id}/accept", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == f"/jobs/{jid}/cv/preview?version={old_id}"
    assert q.get_version(conn, "tailored", jid, old_id)["accepted_at"] is not None
    assert q.get_job_cv(conn, jid)["current_version_id"] != old_id   # current untouched

    # Already accepted — nothing more to do from here (accepting a
    # different version replaces the mark; there's no separate "unaccept").
    r = client.get(f"/jobs/{jid}/cv/preview?version={old_id}")
    assert "&#9733; Accept this version</button>" not in r.text

    assert client.post(f"/jobs/{jid}/cv/versions/99999/accept").status_code == 404
    assert client.post(f"/jobs/999/cv/versions/{old_id}/accept").status_code == 404


def test_preview_pane_shows_when_a_non_current_version_is_accepted(client, conn):
    """Accepting a historic tailored version must not read as "nothing
    accepted" on the editable preview page — job_cv.accepted_at only
    reflects whether *current* is accepted, so the page needs the true
    accepted_version to tell the two apart and avoid re-offering an Accept
    button that would silently steal the mark."""
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="draft 1")
    old_id = q.get_job_cv(conn, jid)["current_version_id"]
    old_hash = q.get_version(conn, "tailored", jid, old_id)["hash"]
    q.upsert_job_cv(conn, jid, tailored_cv="draft 2")
    q.accept_job_cv_version(conn, jid, old_id)

    r = client.get(f"/jobs/{jid}/cv/preview")
    assert f"Version <code>{old_hash[:6]}</code> is accepted" in r.text
    assert f'href="/jobs/{jid}/cv/preview?version={old_id}"' in r.text
    assert "&#9733; Accept this version instead</button>" in r.text
    assert "Accept this CV</button>" not in r.text


def test_preview_page_404s_for_unknown_version(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="draft")
    assert client.get(f"/jobs/{jid}/cv/preview?version=99999").status_code == 404


def test_preview_html_serves_historic_tailored_content(client, conn):
    from unittest.mock import patch
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# MARKER_OLD\n")
    old_id = q.get_job_cv(conn, jid)["current_version_id"]
    q.upsert_job_cv(conn, jid, tailored_cv="# MARKER_CURRENT\n")

    # Stub the renderer (per the convention in test_routes_cv_settings.py) so the
    # assertion is about which markdown the route resolved, not about having
    # doc-write-cli installed.
    with patch("app.routes.cv.doc_write_available", return_value=True), \
         patch("app.routes.cv.render_preview_html", side_effect=lambda md, css: f"<!DOCTYPE html>\n{md}"):
        r = client.get(f"/jobs/{jid}/cv/preview.html?variant=tailored&version={old_id}")
    assert r.status_code == 200
    assert "MARKER_OLD" in r.text
    assert "MARKER_CURRENT" not in r.text     # the ?version= param actually took effect


def test_pdf_serves_historic_tailored_content(client, conn):
    from unittest.mock import patch
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# MARKER_OLD\n")
    old_id = q.get_job_cv(conn, jid)["current_version_id"]
    q.upsert_job_cv(conn, jid, tailored_cv="# MARKER_CURRENT\n")

    # No PDF text-extraction helper exists in this codebase, so assert on the
    # markdown handed to the renderer instead — same intent, no doc-write-cli.
    with patch("app.routes.cv.render_pdf", return_value=b"%PDF-1.7 fake") as rp:
        r = client.get(f"/jobs/{jid}/cv.pdf?version={old_id}")
    assert r.status_code == 200
    rp.assert_called_once()
    rendered_markdown = rp.call_args.args[0]
    assert "MARKER_OLD" in rendered_markdown
    assert "MARKER_CURRENT" not in rendered_markdown


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


def test_preview_pane_has_both_the_diff_picker_and_the_history_section(client, conn):
    from unittest.mock import patch
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="draft 1")
    q.upsert_job_cv(conn, jid, tailored_cv="draft 2")

    # doc_write_available must be True for the tab bar to render at all —
    # otherwise there's no actual tab-bar element in the body to compare
    # against. Even then, base.html's <head> has a same-named CSS rule
    # (".cv-preview-tabs { ... }") that always precedes body content, so the
    # ordering check must look for the actual element (class="cv-preview-tabs")
    # rather than the bare class name, which would match the CSS first.
    with patch("app.routes.cv.doc_write_available", return_value=True):
        r = client.get(f"/jobs/{jid}/cv/preview")
    assert r.status_code == 200

    # The diff-target picker sits inline right after the Differences tab.
    tabs_pos = r.text.index('class="cv-preview-tabs"')
    diff_tab_pos = r.text.index('data-variant="diff"')
    trigger_pos = r.text.index('class="cv-version-trigger cv-version-trigger-sm"')
    assert tabs_pos < diff_tab_pos < trigger_pos

    # The standalone History section is still there too, after the content
    # and the Help section.
    assert 'class="cv-version-list"' in r.text
    diff_summary_pos = r.text.index('id="cv-diff-summary"')
    help_pos = r.text.index('class="cv-help"')
    list_pos = r.text.index('class="cv-version-list"')
    assert diff_summary_pos < tabs_pos < help_pos < list_pos


def test_preview_pane_shows_accepted_badge_and_no_accept_button(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="draft 1")
    client.post(f"/jobs/{jid}/cv/accept")

    r = client.get(f"/jobs/{jid}/cv/preview")
    assert "Accepted" in r.text
    # Already accepted — no action needed (accepting a different version is
    # what replaces it, there's no separate "unaccept").
    assert f'action="/jobs/{jid}/cv/accept"' not in r.text
