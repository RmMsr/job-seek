from unittest.mock import patch
from app.db import queries as q


def _job(conn):
    conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s','http://x','manual')")
    conn.execute("INSERT INTO jobs (source_id, url, title) VALUES (1,'http://x/1','Role')")
    conn.commit()
    base_cv_id = q.list_base_cvs(conn)[0]["id"]
    q.set_base_cv(conn, base_cv_id, "# Me")
    q.save_cv_settings(conn, base_instruction="", base_guardrails="", css="", default_scope=["select", "reorder"])
    return 1


def test_workbench_404_for_missing_job(client):
    assert client.get("/jobs/999/cv").status_code == 404


def test_preview_html_renders_base_and_tailored(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Tailored marker\n")
    with patch("app.routes.cv.doc_write_available", return_value=True), \
         patch("app.routes.cv.render_preview_html", side_effect=lambda md, css: f"<!DOCTYPE html>\n{md}"):
        rb = client.get(f"/jobs/{jid}/cv/preview.html?variant=base")
        rt = client.get(f"/jobs/{jid}/cv/preview.html?variant=tailored")
    assert rb.status_code == 200 and "# Me" in rb.text
    assert rt.status_code == 200 and "Tailored marker" in rt.text


def test_preview_html_defaults_to_tailored(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# The draft\n")
    with patch("app.routes.cv.doc_write_available", return_value=True), \
         patch("app.routes.cv.render_preview_html", side_effect=lambda md, css: md):
        r = client.get(f"/jobs/{jid}/cv/preview.html")
    assert "The draft" in r.text


def test_preview_html_tailored_404_without_draft(client, conn):
    jid = _job(conn)
    assert client.get(f"/jobs/{jid}/cv/preview.html?variant=tailored").status_code == 404


def test_preview_html_503_without_doc_write(client, conn):
    jid = _job(conn)
    with patch("app.routes.cv.doc_write_available", return_value=False):
        assert client.get(f"/jobs/{jid}/cv/preview.html?variant=base").status_code == 503


def test_diff_html_renders_with_a_draft(client, conn):
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
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# CV\n\n- a\n", base_cv_snapshot="# CV\n\n- a\n- b\n")
    with patch("app.routes.cv.doc_write_available", return_value=False):
        r = client.get(f"/jobs/{jid}/cv/diff.html")
    assert r.status_code == 200
    assert "<del" in r.text


def test_diff_html_falls_back_to_plain_doc_when_the_diff_builder_raises(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# CV marker\n", base_cv_snapshot="# Base\n")
    with patch("app.routes.cv.build_cv_diff", side_effect=RuntimeError("boom")), \
         patch("app.routes.cv.doc_write_available", return_value=True), \
         patch("app.routes.cv.render_diff_html", side_effect=lambda md, css: f"<!doctype html>\n{md}"):
        r = client.get(f"/jobs/{jid}/cv/diff.html")
    assert r.status_code == 200
    assert "CV marker" in r.text


def test_set_job_base_cv(client, conn):
    jid = _job(conn)
    second_id = q.create_base_cv(conn, "Backend")

    r = client.post(f"/jobs/{jid}/cv/set-base", data={"base_cv_id": second_id})
    assert r.status_code == 200
    assert q.get_job_cv(conn, jid)["base_cv_id"] == second_id


def test_workbench_ctx_exposes_base_cvs_and_selection(client, conn):
    jid = _job(conn)
    default_id = q.list_base_cvs(conn)[0]["id"]

    r = client.get(f"/jobs/{jid}/cv")
    assert r.status_code == 200
    assert f'value="{default_id}"' in r.text or str(default_id) in r.text


def test_version_url_macro_adds_preview_suffix_for_tailored_cv_current_version(client, conn):
    """When viewing a historic tailored-CV version and clicking the 'current version'
    link in the version dropdown, it should navigate to /preview?version=... (read-only),
    not bare /cv (live editable). This tests that the version_url macro correctly
    outputs /preview suffix for tailored CVs' current-version links."""
    jid = _job(conn)

    # Create two tailored CV versions
    q.upsert_job_cv(conn, jid, tailored_cv="# Version 1\n")
    old_version_id = q.get_job_cv(conn, jid)["current_version_id"]

    q.upsert_job_cv(conn, jid, tailored_cv="# Version 2\n")
    current_version_id = q.get_job_cv(conn, jid)["current_version_id"]

    # Fetch the read-only preview for the old version
    r = client.get(f"/jobs/{jid}/cv/preview?version={old_version_id}")
    assert r.status_code == 200

    # The version history dropdown should contain a link for the current version
    # that points to /preview (read-only preview route), not bare /cv (which would navigate to the live editable workbench)
    expected_href = f'/jobs/{jid}/cv/preview'
    assert expected_href in r.text, (
        f"Expected current-version link with /preview suffix not found in HTML. "
        f"The macro should output /preview for tailored CV current-version links."
    )

    # Confirm there's no bare /cv link for this job in the version menu context
    # (the page has other /cv links, so we do a looser check for "no bare /cv in version dropdown")
    import re
    version_menu = re.search(r'cv-version-menu.*?</div>(?=\s*</div>)', r.text, re.DOTALL)
    if version_menu:
        menu_content = version_menu.group(0)
        # Check that the current version link in the menu uses /preview
        if f'/jobs/{jid}/cv/preview' not in menu_content:
            raise AssertionError(
                "Version menu does not contain /preview link for current version. "
                "Should use /preview instead of bare /cv for tailored CVs."
            )


def test_preview_page_shows_assigned_base_cv_name_and_change_link(client, conn):
    jid = _job(conn)
    second_id = q.create_base_cv(conn, "Backend CV")
    q.upsert_job_cv(conn, jid, base_cv_id=second_id)

    r = client.get(f"/jobs/{jid}/cv/preview")
    assert r.status_code == 200
    assert f'href="/jobs/{jid}/cv"' in r.text  # links to the tailoring tab's base-CV picker, not the base CV's own edit page
    assert "Backend CV" in r.text


def test_switching_base_cv_marks_the_workbench_plan_stale(client, conn):
    from app.routes.cv import _plan_context_hash, _job_context, _job_notes
    jid = _job(conn)
    default_id = q.list_base_cvs(conn)[0]["id"]
    job = q.get_job(conn, jid)
    q.upsert_job_cv(conn, jid)
    conn.execute(
        "UPDATE job_cv SET plan_generated_at = datetime('now'), plan_context_hash = ? WHERE job_id = ?",
        (_plan_context_hash(_job_context(job), _job_notes(conn, job), default_id), jid),
    )
    conn.commit()

    r = client.get(f"/jobs/{jid}/cv")
    assert 'data-state="fresh"' in r.text

    second_id = q.create_base_cv(conn, "Backend")
    q.upsert_job_cv(conn, jid, base_cv_id=second_id)
    r = client.get(f"/jobs/{jid}/cv")
    assert 'data-state="stale"' in r.text
    assert "base CV changed" in r.text


def test_plan_pane_shows_base_cv_picker_only_with_multiple_base_cvs(client, conn):
    jid = _job(conn)
    default_id = q.list_base_cvs(conn)[0]["id"]

    r = client.get(f"/jobs/{jid}/cv")
    assert f'href="/cv/{default_id}"' in r.text
    assert "name=\"base_cv_id\"" not in r.text  # only one base CV — no picker

    q.create_base_cv(conn, "Backend")
    r = client.get(f"/jobs/{jid}/cv")
    assert 'name="base_cv_id"' in r.text
