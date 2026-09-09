import re
from app.db import queries as q


def _job(conn):
    conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s','http://x','manual')")
    conn.execute("INSERT INTO jobs (source_id, url, title) VALUES (1,'http://x/1','Role')")
    conn.commit()
    q.save_cv_settings(conn, base_cv="# Me", base_instruction="", base_guardrails="",
                       css="", default_scope=["select", "reorder"])
    return 1


def test_workbench_404_for_missing_job(client):
    assert client.get("/jobs/999/cv").status_code == 404


def test_workbench_renders_first_visit(client, conn):
    jid = _job(conn)
    r = client.get(f"/jobs/{jid}/cv")
    assert r.status_code == 200
    assert "Tailoring plan" in r.text or "plan" in r.text.lower()


def test_preview_html_renders_base_and_tailored(client, conn):
    from unittest.mock import patch
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Tailored marker\n")
    with patch("app.routes.cv.render_preview_html", side_effect=lambda md, css: f"<!DOCTYPE html>\n{md}"):
        rb = client.get(f"/jobs/{jid}/cv/preview.html?variant=base")
        rt = client.get(f"/jobs/{jid}/cv/preview.html?variant=tailored")
    assert rb.status_code == 200 and "# Me" in rb.text          # base CV from _job()
    assert rt.status_code == 200 and "Tailored marker" in rt.text


def test_preview_html_defaults_to_tailored(client, conn):
    from unittest.mock import patch
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# The draft\n")
    with patch("app.routes.cv.render_preview_html", side_effect=lambda md, css: md):
        r = client.get(f"/jobs/{jid}/cv/preview.html")
    assert "The draft" in r.text


def test_preview_html_tailored_404_without_draft(client, conn):
    jid = _job(conn)
    assert client.get(f"/jobs/{jid}/cv/preview.html?variant=tailored").status_code == 404


def test_preview_html_503_without_doc_write(client, conn):
    from unittest.mock import patch
    jid = _job(conn)
    with patch("app.routes.cv.doc_write_available", return_value=False):
        assert client.get(f"/jobs/{jid}/cv/preview.html?variant=base").status_code == 503


def test_stage_headers_show_status_indicators(client, conn):
    jid = _job(conn)
    page = client.get(f"/jobs/{jid}/cv").text
    # three stage-status elements, all "none" on a blank workbench
    assert page.count('class="cv-stage-status"') >= 3
    assert 'data-state="none"' in page


def test_draft_status_is_stale_after_scope_change(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft", guardrail_findings=[{"rule": "r", "verdict": "ok", "explanation": ""}])
    conn.execute("UPDATE job_cv SET generated_at = datetime('now', '-1 hour') WHERE job_id = ?", (jid,))
    conn.commit()
    q.set_job_cv_scope(conn, jid, [1])
    page = client.get(f"/jobs/{jid}/cv").text
    # both the draft and the guardrail header track staleness
    assert page.count('data-state="stale"') >= 2


def test_workbench_has_no_waiting_on_you_nags(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft")
    conn.execute("UPDATE job_cv SET generated_at = datetime('now', '-1 hour') WHERE job_id = ?", (jid,))
    q.set_job_cv_directives(conn, jid, "- something new")
    text = client.get(f"/jobs/{jid}/cv").text
    for gone in ("Waiting on you", "is-stale", "n-stale", "n-updating"):
        assert gone not in text


def test_preview_marks_draft_out_of_date_when_base_cv_changed(client, conn):
    from app.routes.cv import _base_hash
    jid = _job(conn)
    settings = q.get_cv_settings(conn)
    # generated against the current settings -> not stale
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft", base_hash=_base_hash(settings))
    conn.execute("UPDATE job_cv SET generated_at = datetime('now') WHERE job_id = ?", (jid,))
    fresh = client.get(f"/jobs/{jid}/cv").text
    assert 'data-state="fresh"' in fresh and "Outdated" not in fresh
    # base CV changes -> hash mismatch -> the preview badge says so
    q.save_cv_settings(conn, base_cv="# a whole new CV", base_instruction=settings["base_instruction"],
                       base_guardrails=settings["base_guardrails"], css=settings["css"],
                       default_scope=settings["default_scope"],
                       directives_template=settings["directives_template"])
    r = client.get(f"/jobs/{jid}/cv")
    assert '<span class="cv-stage-status" data-state="stale">Outdated</span>' in r.text


def test_preview_badge_shows_running_while_an_update_runs(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft", base_hash="stale")
    conn.execute("UPDATE job_cv SET generated_at = datetime('now') WHERE job_id = ?", (jid,))
    q.enqueue_task(conn, kind="cv_tailor",
                   params={"job_id": jid, "mode": "generate", "render": "preview_pane"})
    r = client.get(f"/jobs/{jid}/cv")
    assert 'data-state="running"' in r.text
    assert 'class="cv-preview-progress" aria-live="polite">' in r.text


def test_preview_progress_note_hidden_when_no_update_running(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft")
    text = client.get(f"/jobs/{jid}/cv").text
    assert "A tailored update is in progress" in text
    assert 'class="cv-preview-progress" aria-live="polite" hidden' in text


def test_guardrail_findings_grouped_by_status_with_counts(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft", guardrail_findings=[
        {"rule": "no invented dates", "verdict": "ok", "explanation": ""},
        {"rule": "no invented tools", "verdict": "ok", "explanation": ""},
        {"rule": "no invented degrees", "verdict": "violated", "explanation": "claims a PhD"},
        {"rule": "no invented metrics", "verdict": "unclear", "explanation": "vague number"},
    ])
    r = client.get(f"/jobs/{jid}/cv")
    text = r.text
    assert "<details" in text and "guardrail" in text.lower()
    findings_block = text[text.index('class="guardrail-summary"'):]
    # heading carries the bar, not a collapsed summary line
    assert findings_block.index("Guardrails") < findings_block.index("guardrail-bar")
    # one details group per status, each with its own count
    assert "Failed</strong> (1)" in findings_block
    assert "Unclear</span> (1)" in findings_block
    assert "OK</span> (2)" in findings_block
    assert "claims a PhD" in findings_block
    # ok folded by default, failed and unclear expanded
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
    text = client.get(f"/jobs/{jid}/cv").text
    assert 'class="guardrail-summary"' in text
    assert ">Guardrails" in text
    assert "after you generate a draft" in text


def test_guardrails_heading_and_bar_show_with_findings(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft",
                    guardrail_findings=[{"rule": "No lies", "verdict": "ok", "explanation": ""}])
    text = client.get(f"/jobs/{jid}/cv").text
    assert 'class="guardrail-summary"' in text
    assert ">Guardrails" in text
    assert 'class="guardrail-bar"' in text
    assert "after you generate a draft" not in text


def test_guardrails_placeholder_when_draft_has_no_findings(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft", guardrail_findings=[])
    text = client.get(f"/jobs/{jid}/cv").text
    assert "No guardrail findings recorded" in text
    assert 'class="guardrail-bar"' not in text


def test_guardrails_placeholder_hidden_while_recheck_runs(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft", guardrail_findings=[])
    q.enqueue_task(conn, kind="cv_tailor",
                   params={"job_id": jid, "mode": "generate", "render": "preview_pane"})
    text = client.get(f"/jobs/{jid}/cv").text
    assert "after you generate a draft" not in text
    assert "No guardrail findings recorded" not in text
    assert 'class="cv-findings-stale" aria-live="polite">' in text   # stale line shows, un-hidden


def test_scope_selector_is_an_edit_latitude_row_with_descriptions_foldout(client, conn):
    jid = _job(conn)
    text = client.get(f"/jobs/{jid}/cv").text
    assert "<legend>Edit scope</legend>" not in text        # no full fieldset
    assert 'class="cv-scope-row"' in text
    assert "Edit latitude" in text                           # label before the checkboxes
    assert 'class="cv-scope-help"' in text
    assert "<summary>Descriptions</summary>" in text
    # the details foldout is closed by default (user reveals it)
    assert "<details class=\"cv-scope-help\">" in text and "<details class=\"cv-scope-help\" open>" not in text


def test_scope_selector_checkbox_state_reflects_job_cv_scope(client, conn):
    jid = _job(conn)
    opts = q.get_scope_options(conn)
    chosen, other = opts[0]["id"], opts[1]["id"]
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft", scope=[chosen])
    text = client.get(f"/jobs/{jid}/cv").text
    assert 'class="cv-scope-row"' in text
    # the checkboxes live inside #cv-latitude-form, which posts to /save-scope
    form_start = text.index('id="cv-latitude-form"')
    form_end = text.index("</form>", form_start)
    assert form_start < text.index('class="cv-scope-row"', form_start) < form_end
    # the chosen option is checked, another is not
    assert f'value="{chosen}" checked>' in text
    assert f'value="{other}" checked>' not in text


def test_workbench_has_a_stage_breadcrumb(client, conn):
    jid = _job(conn)
    page = client.get(f"/jobs/{jid}/cv").text
    assert 'class="cv-workbench-crumb"' in page
    for label in ("Directives", "Plan", "Draft", "Guardrails"):
        assert label in page


def test_workbench_has_no_workflow_instructions_line(client, conn):
    jid = _job(conn)
    r = client.get(f"/jobs/{jid}/cv")
    assert "cv-workflow-note" not in r.text
    assert "How this works" not in r.text


def test_preview_pane_layout_guardrails_under_preview_controls_below_iframe(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft\n\n- x\n", scope=[1],
                    guardrail_findings=[{"rule": "r", "verdict": "ok", "explanation": ""}])
    text = client.get(f"/jobs/{jid}/cv").text
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
        r = client.get(f"/jobs/{jid}/cv")
    assert 'data-variant="base"' in r.text
    assert 'data-variant="tailored"' in r.text
    assert 'class="cv-preview-stage' in r.text
    # both variants have their own iframe, but only the active one loads on
    # page load — the other carries data-src and fetches on first activation
    assert r.text.count('class="cv-preview-doc') == 3  # base + tailored + differences
    assert 'class="cv-preview-doc is-active" data-variant="tailored"' in r.text
    assert 'src="/jobs/%d/cv/preview.html?variant=tailored"' % jid in r.text
    assert 'data-src="/jobs/%d/cv/preview.html?variant=base"' % jid in r.text
    assert 'src="/jobs/%d/cv/preview.html?variant=base"' % jid not in r.text.replace("data-src", "xxxx")
    assert 'class="btn btn-subtle cv-preview-fs"' in r.text


def test_preview_pane_tailored_tab_disabled_without_draft(client, conn):
    from unittest.mock import patch
    jid = _job(conn)
    with patch("app.routes.cv.doc_write_available", return_value=True):
        r = client.get(f"/jobs/{jid}/cv")
    m = re.search(r'<button[^>]*class="cv-preview-tab"[^>]*data-variant="tailored"[^>]*>', r.text)
    assert m and "disabled" in m.group(0)
    # only the base iframe is mounted, and it is the active one
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
        r = client.get(f"/jobs/{jid}/cv")
    # survives a reload: the note renders visible (no hidden attr) and reattaches
    assert 'class="cv-preview-progress" aria-live="polite">' in r.text
    assert "A tailored update is in progress" in r.text
    assert f"__cvWatchGenerate({tid})" in r.text


def test_first_pass_plan_task_does_not_show_preview_progress_note(client, conn):
    # a plan run produces no draft and doesn't touch guardrails
    jid = _job(conn)
    q.enqueue_task(conn, kind="cv_tailor",
                   params={"job_id": jid, "mode": "plan", "render": "plan_pane"})
    assert q.cv_generate_task_id(conn, jid) is None
    r = client.get(f"/jobs/{jid}/cv")
    assert 'class="cv-preview-progress" aria-live="polite" hidden' in r.text
    assert 'class="cv-findings-stale" aria-live="polite" hidden' in r.text


def test_preview_pane_markdown_fallback_without_doc_write(client, conn):
    from unittest.mock import patch
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft body\n")
    with patch("app.routes.cv.doc_write_available", return_value=False):
        r = client.get(f"/jobs/{jid}/cv")
    assert 'class="cv-preview-stage"' not in r.text
    assert "Draft body" in r.text  # rendered markdown


def test_plan_pane_scope_checkboxes_use_live_descriptions(client, conn):
    import html
    jid = _job(conn)
    opts = q.get_scope_options(conn)
    r = client.get(f"/jobs/{jid}/cv")
    text = html.unescape(r.text)  # Jinja escapes apostrophes (e.g. "job's") as &#39;
    for opt in opts:
        assert opt["description"] in text
    assert f'value="{opts[0]["id"]}"' in r.text
    # the scope's short identifier names the chip and the descriptions foldout, no numbering
    if opts[0].get("name"):
        assert f'<dt>{opts[0]["name"]}</dt>' in r.text
        assert f'#{opts[0]["id"]} {opts[0]["name"]}' not in text  # no "#1 correct" style


def test_directives_note_mentions_heading_and_bullet_structure(client, conn):
    jid = _job(conn)
    r = client.get(f"/jobs/{jid}/cv")
    assert "## Heading" in r.text and "- bullet" in r.text


def test_first_visit_directives_textarea_prefilled_with_template(client, conn):
    jid = _job(conn)
    q.save_cv_settings(conn, base_cv="# Me", base_instruction="", base_guardrails="",
                       css="", default_scope=[1], directives_template="## Role relevance\n## Skills match")
    r = client.get(f"/jobs/{jid}/cv")
    ta = r.text[r.text.index('name="tuning_directives"'):]
    body = ta[ta.index(">") + 1:ta.index("</textarea>")]
    assert "## Role relevance" in body and "## Skills match" in body


def test_diff_html_renders_with_a_draft(client, conn):
    from unittest.mock import patch
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# CV\n\n- kept\n",
                    base_cv_snapshot="# CV\n\n- kept\n- dropped\n")
    with patch("app.routes.cv.doc_write_available", return_value=True), \
         patch("app.routes.cv.render_diff_html", side_effect=lambda md, css: f"<!doctype html>\n{md}"):
        r = client.get(f"/jobs/{jid}/cv/diff.html")
    assert r.status_code == 200
    assert "cvd-del" in r.text and "dropped" in r.text


def test_diff_html_404_without_draft(client, conn):
    jid = _job(conn)
    assert client.get(f"/jobs/{jid}/cv/diff.html").status_code == 404


def test_diff_html_predates_tracking_when_snapshot_empty(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# CV\n", base_cv_snapshot="")
    r = client.get(f"/jobs/{jid}/cv/diff.html")
    assert r.status_code == 200
    assert "predates change tracking" in r.text


def test_diff_html_fallback_without_doc_write(client, conn):
    from unittest.mock import patch
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# CV\n\n- a\n", base_cv_snapshot="# CV\n\n- a\n- b\n")
    with patch("app.routes.cv.doc_write_available", return_value=False):
        r = client.get(f"/jobs/{jid}/cv/diff.html")
    assert r.status_code == 200
    assert "<del" in r.text  # markdown filter rendered the annotated md


def test_diff_html_falls_back_to_plain_doc_when_the_diff_builder_raises(client, conn):
    from unittest.mock import patch
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# CV marker\n", base_cv_snapshot="# Base\n")
    with patch("app.routes.cv.build_cv_diff", side_effect=RuntimeError("boom")), \
         patch("app.routes.cv.doc_write_available", return_value=True), \
         patch("app.routes.cv.render_diff_html", side_effect=lambda md, css: f"<!doctype html>\n{md}"):
        r = client.get(f"/jobs/{jid}/cv/diff.html")
    assert r.status_code == 200
    assert "CV marker" in r.text  # the plain tailored doc, not a 500


def test_preview_pane_has_differences_tab(client, conn):
    from unittest.mock import patch
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft\n", base_cv_snapshot="# Base\n")
    with patch("app.routes.cv.doc_write_available", return_value=True):
        r = client.get(f"/jobs/{jid}/cv")
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
        r = client.get(f"/jobs/{jid}/cv")
    assert '<p class="cv-diff-summary-line">' in r.text
    assert "1 bullet dropped" in r.text
    # the newly generated block is listed, above the tab bar
    assert "Brand new intro paragraph." in r.text
    assert r.text.index('<div class="cv-diff-summary">') < r.text.index('data-variant="diff"')


def test_preview_pane_no_digest_before_first_draft(client, conn):
    from unittest.mock import patch
    jid = _job(conn)
    with patch("app.routes.cv.doc_write_available", return_value=True):
        r = client.get(f"/jobs/{jid}/cv")
    assert '<div class="cv-diff-summary">' not in r.text


def test_differences_tab_disabled_without_draft(client, conn):
    from unittest.mock import patch
    jid = _job(conn)
    with patch("app.routes.cv.doc_write_available", return_value=True):
        r = client.get(f"/jobs/{jid}/cv")
    assert 'data-variant="diff"' in r.text
    assert f'/jobs/{jid}/cv/diff.html' not in r.text


def test_accepted_view_has_three_tabs(client, conn):
    from unittest.mock import patch
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Final\n", base_cv_snapshot="# Base\n")
    q.finalize_job_cv(conn, jid)
    with patch("app.routes.cv.doc_write_available", return_value=True):
        r = client.get(f"/jobs/{jid}/cv")
    assert r.text.count('class="cv-preview-tab"') == 3
    assert 'data-variant="diff"' in r.text


def test_workbench_first_visit_shows_empty_preview_state(client, conn):
    jid = _job(conn)
    text = client.get(f"/jobs/{jid}/cv").text
    assert "No tailored CV yet" in text
    assert "Accept this CV" not in text           # gated on has_draft


def test_findings_stale_note_hidden_when_no_generate_running(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft")
    text = client.get(f"/jobs/{jid}/cv").text
    assert 'class="cv-findings-stale" aria-live="polite" hidden' in text


def test_findings_stale_note_visible_while_generate_runs(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft")
    q.enqueue_task(conn, kind="cv_tailor",
                   params={"job_id": jid, "mode": "generate", "render": "preview_pane"})
    text = client.get(f"/jobs/{jid}/cv").text
    assert 'class="cv-findings-stale" aria-live="polite">' in text          # present, no hidden attr


def test_findings_container_present_on_first_generate_without_draft(client, conn):
    jid = _job(conn)
    q.enqueue_task(conn, kind="cv_tailor",
                   params={"job_id": jid, "mode": "generate", "render": "preview_pane"})
    text = client.get(f"/jobs/{jid}/cv").text
    assert 'id="cv-findings"' in text
    assert 'class="cv-findings-stale" aria-live="polite">' in text
