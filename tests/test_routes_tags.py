from app.db import queries as q


def _job(conn, company="Acme"):
    sid = q.insert_source(conn, "s", "https://s.example", "generic_listing")
    return q.insert_job(conn, source_id=sid, url="https://s.example/1", title="T", company=company, raw_text="r")


def test_add_tag_returns_fragment_with_tag_and_open_input(client, conn):
    jid = _job(conn)
    resp = client.post(f"/jobs/{jid}/tags?status=new", data={"name": "Language: English"})
    assert resp.status_code == 200
    assert resp.headers["HX-Trigger"] == "tags-changed"
    assert "Language: English" in resp.text
    assert 'class="job-tags' in resp.text
    assert "autofocus" in resp.text
    assert [t["name"] for t in q.get_job_tags(conn, jid)] == ["Language: English"]


def test_add_tag_unknown_job_404(client, conn):
    assert client.post("/jobs/999/tags", data={"name": "x"}).status_code == 404


def test_remove_tag(client, conn):
    jid = _job(conn)
    t = q.add_job_tag(conn, jid, "Remote")
    resp = client.delete(f"/jobs/{jid}/tags/{t['id']}?detail=1")
    assert resp.status_code == 200
    assert "Remote" not in resp.text
    assert q.get_job_tags(conn, jid) == []


def test_set_and_clear_organization(client, conn):
    jid = _job(conn, company="")
    resp = client.post(f"/jobs/{jid}/organization?detail=1", data={"name": "Brand New Org"})
    assert resp.status_code == 200
    assert resp.headers["HX-Trigger"] == "orgs-changed"
    assert "Brand New Org" in resp.text
    resp = client.post(f"/jobs/{jid}/organization?detail=1", data={"name": ""})
    assert "Brand New Org" not in resp.text
    assert "+ organization" in resp.text


def test_option_routes(client, conn):
    jid = _job(conn)
    q.add_job_tag(conn, jid, 'Say "hi"')
    tags_html = client.get("/tags/options").text
    assert '<option value="Say &#34;hi&#34;">' in tags_html
    assert '<option value="Acme">' in client.get("/orgs/options").text


def test_list_row_shows_manual_tag_as_filter_link_with_remove(client, conn):
    jid = _job(conn)
    q.update_job_pipeline(conn, jid, simplified_content="c", content_type="lead")
    q.add_job_tag(conn, jid, "Language: English")
    html = client.get("/jobs?status=lead").text
    assert 'class="tag tag-manual"' in html
    assert "tag=Language%3A+English" in html
    assert f'hx-delete="/jobs/{jid}/tags/' in html
    assert '<span class="tag-key">Language:</span>' in html


def test_detail_page_has_edit_controls_and_datalists(client, conn):
    jid = _job(conn, company="")
    html = client.get(f"/jobs/{jid}").text
    assert "+ organization" in html and "+ tag" in html
    assert f'hx-post="/jobs/{jid}/tags?detail=1"' in html
    assert 'id="tag-options"' in html and 'id="org-options"' in html


def test_override_marker_and_effective_org_in_header(client, conn):
    jid = _job(conn, company="Acme")
    q.set_company_override(conn, jid, "Acme Robotics")
    html = client.get(f"/jobs/{jid}").text
    assert "tag-org-manual" in html
    assert "extracted: Acme" in html
    assert '<p class="job-header-org">Acme Robotics</p>' in html
