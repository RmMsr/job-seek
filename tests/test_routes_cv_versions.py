from app.db import queries as q


def _job(conn):
    conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s','http://x','manual')")
    conn.execute("INSERT INTO jobs (source_id, url, title) VALUES (1,'http://x/1','Role')")
    conn.commit()
    return 1


def test_revert_tailored_version_repoints_current(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="draft 1")
    old_id = q.get_job_cv(conn, jid)["current_version_id"]
    q.upsert_job_cv(conn, jid, tailored_cv="draft 2")

    r = client.post(f"/jobs/{jid}/cv/versions/{old_id}/revert", follow_redirects=False)
    assert r.status_code == 303
    row = q.get_job_cv(conn, jid)
    assert row["current_version_id"] == old_id
    assert row["tailored_cv"] == "draft 1"
    assert "cv" in [e["kind"] for e in q.get_job_events(conn, jid)]


def test_revert_unknown_tailored_version_404s(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="draft")
    assert client.post(f"/jobs/{jid}/cv/versions/99999/revert").status_code == 404


def test_revert_rejects_a_version_belonging_to_another_job(client, conn):
    jid1 = _job(conn)
    conn.execute("INSERT INTO jobs (source_id, url, title) VALUES (1,'http://x/2','Role 2')")
    conn.commit()
    jid2 = 2
    q.upsert_job_cv(conn, jid1, tailored_cv="job1 draft")
    other_version_id = q.get_job_cv(conn, jid1)["current_version_id"]
    q.upsert_job_cv(conn, jid2, tailored_cv="job2 draft")

    assert client.post(f"/jobs/{jid2}/cv/versions/{other_version_id}/revert").status_code == 404


def test_revert_base_cv_version_repoints_current(client, conn):
    base_cv_id = q.list_base_cvs(conn)[0]["id"]
    q.set_base_cv(conn, base_cv_id, "v1")
    old_id = q.get_base_cv(conn, base_cv_id)["current_version_id"]
    # Force the second save past the 1h manual-edit stacking window so it opens
    # a distinct version instead of overwriting v1's row in place.
    conn.execute(
        "UPDATE cv_versions SET updated_at = datetime('now', '-2 hours') WHERE id = ?",
        (old_id,),
    )
    conn.commit()
    q.set_base_cv(conn, base_cv_id, "v2")

    r = client.post(f"/cv/{base_cv_id}/versions/{old_id}/revert", follow_redirects=False)
    assert r.status_code == 303
    assert q.get_base_cv(conn, base_cv_id)["base_cv"] == "v1"


def test_base_cv_accept(client, conn):
    base_cv_id = q.list_base_cvs(conn)[0]["id"]
    q.set_base_cv(conn, base_cv_id, "v1")
    r = client.post(f"/cv/{base_cv_id}/accept", follow_redirects=False)
    assert r.status_code == 303
    assert q.get_base_cv(conn, base_cv_id)["accepted_at"] is not None


def test_revert_base_version_is_scoped_to_its_base_cv(client, conn):
    base_cv_id = q.list_base_cvs(conn)[0]["id"]
    q.set_base_cv(conn, base_cv_id, "v1")
    q.accept_base_cv(conn, base_cv_id)
    q.set_base_cv(conn, base_cv_id, "v2")
    v1_id = q.get_accepted_base_version(conn, base_cv_id)["id"]

    r = client.post(f"/cv/{base_cv_id}/versions/{v1_id}/revert", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == f"/cv/{base_cv_id}"
    assert q.get_base_cv(conn, base_cv_id)["base_cv"] == "v1"
