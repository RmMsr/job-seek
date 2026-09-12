from app.db import queries as q


def _job(conn):
    conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s','http://x','manual')")
    conn.execute("INSERT INTO jobs (source_id, url, title) VALUES (1,'http://x/1','Role')")
    conn.commit()
    q.save_cv_settings(conn, base_cv="# Me", base_instruction="", base_guardrails="",
                       css="", default_scope=["select", "reorder"])
    return 1


def test_tailor_cv_renders_first_visit(client, conn):
    jid = _job(conn)
    r = client.get(f"/jobs/{jid}/cv")
    assert r.status_code == 200
    assert "Tailoring plan" in r.text or "plan" in r.text.lower()


def test_plan_pane_has_a_stage_status_indicator(client, conn):
    jid = _job(conn)
    page = client.get(f"/jobs/{jid}/cv").text
    assert page.count('class="cv-stage-status"') == 1
    assert 'data-state="none"' in page


def test_tailor_cv_has_no_waiting_on_you_nags(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft")
    conn.execute("UPDATE job_cv SET generated_at = datetime('now', '-1 hour') WHERE job_id = ?", (jid,))
    q.set_job_cv_directives(conn, jid, "- something new")
    text = client.get(f"/jobs/{jid}/cv").text
    for gone in ("Waiting on you", "is-stale", "n-stale", "n-updating"):
        assert gone not in text


def test_tailor_cv_has_no_stage_breadcrumb(client, conn):
    jid = _job(conn)
    page = client.get(f"/jobs/{jid}/cv").text
    assert 'class="cv-workbench-crumb"' not in page


def test_tailor_cv_has_no_workflow_instructions_line(client, conn):
    jid = _job(conn)
    r = client.get(f"/jobs/{jid}/cv")
    assert "cv-workflow-note" not in r.text
    assert "How this works" not in r.text


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


def test_tailor_cv_shows_locked_notice_when_finalized(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Final")
    q.finalize_job_cv(conn, jid)
    page = client.get(f"/jobs/{jid}/cv").text
    assert "read-only" in page
    assert 'id="cv-plan-pane"' not in page
    assert "Analyze and find improvements" not in page
    assert 'action="/jobs/{}/cv/reopen"'.format(jid) in page


def test_tailor_cv_shows_header_with_active_subnav_link(client, conn):
    jid = _job(conn)
    page = client.get(f"/jobs/{jid}/cv").text
    assert '<nav class="job-subnav">' in page
    subnav = page[page.index('<nav class="job-subnav">'):page.index('</nav>', page.index('<nav class="job-subnav">'))]
    tailor_link = subnav[subnav.index(f'href="/jobs/{jid}/cv"'):]
    assert 'class="active"' in tailor_link[:60]
