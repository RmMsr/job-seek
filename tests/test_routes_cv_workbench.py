from unittest.mock import patch
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
