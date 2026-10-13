import json
from app.template_env import templates
from app.db import queries as q
from app.tags import normalize_tag_name


def _job(conn, company="Acme"):
    sid = q.insert_source(conn, "s", "https://s.example", "generic_listing")
    return q.insert_job(conn, source_id=sid, url="https://s.example/1", title="T", company=company, raw_text="r")


def test_normalize_tag_name_trims_and_collapses():
    assert normalize_tag_name("  Language:   English \n") == "Language: English"
    assert normalize_tag_name(None) == ""
    assert normalize_tag_name("   ") == ""


def test_org_defaults_to_llm_company(conn):
    jid = _job(conn)
    job = q.get_job(conn, jid)
    assert job["org"] == "Acme"
    assert job["company_override"] is None


def test_override_wins_and_clears(conn):
    jid = _job(conn)
    assert q.set_company_override(conn, jid, "  Acme   Robotics ") is True
    assert q.get_job(conn, jid)["org"] == "Acme Robotics"
    assert q.set_company_override(conn, jid, "") is True
    job = q.get_job(conn, jid)
    assert job["org"] == "Acme" and job["company_override"] is None


def test_override_fills_missing_company(conn):
    jid = _job(conn, company="")
    q.set_company_override(conn, jid, "Brand New Org")
    assert q.get_job(conn, jid)["org"] == "Brand New Org"


def test_override_equal_to_llm_value_is_not_stored(conn):
    jid = _job(conn)
    assert q.set_company_override(conn, jid, "Acme") is False
    assert q.get_job(conn, jid)["company_override"] is None


def test_override_unchanged_returns_false_and_logs_once(conn):
    jid = _job(conn)
    q.set_company_override(conn, jid, "Beta")
    assert q.set_company_override(conn, jid, "Beta") is False
    msgs = [e["message"] for e in q.get_job_events(conn, jid) if e["kind"] == "org_override"]
    assert msgs == ["Organization set to “Beta”"]


def test_clear_override_logs_event(conn):
    jid = _job(conn)
    q.set_company_override(conn, jid, "Beta")
    q.set_company_override(conn, jid, "")
    msgs = [e["message"] for e in q.get_job_events(conn, jid) if e["kind"] == "org_override"]
    assert msgs[0] == "Organization override cleared"


def test_override_survives_pipeline_update(conn):
    jid = _job(conn, company="")
    q.set_company_override(conn, jid, "Mine")
    q.update_job_pipeline(conn, jid, simplified_content="c", content_type="job_posting", company="LLM Co")
    job = q.get_job(conn, jid)
    assert job["company"] == "LLM Co" and job["org"] == "Mine"


def test_org_filter_and_distinct_use_effective_org(conn):
    jid = _job(conn, company="")
    assert [j["id"] for j in q.get_jobs(conn, org_none=True)] == [jid]
    q.set_company_override(conn, jid, "Zed")
    assert q.get_jobs(conn, org_none=True) == []
    assert [j["id"] for j in q.get_jobs(conn, org="Zed")] == [jid]
    assert q.get_distinct_companies(conn) == ["Zed"]
    q.update_job_pipeline(conn, jid, simplified_content="c", content_type="lead")
    assert q.get_job_counts(conn, org="Zed")["lead"] == 1
    assert q.get_job_counts(conn, org_none=True)["lead"] == 0



def _job2(conn, sid, n):
    return q.insert_job(conn, source_id=sid, url=f"https://s.example/{n}", title="T", company="", raw_text="r")


def test_add_tag_creates_and_reuses_case_insensitively(conn):
    jid = _job(conn)
    t1 = q.add_job_tag(conn, jid, " Language:  English ")
    assert t1["name"] == "Language: English"
    sid = q.get_job(conn, jid)["source_id"]
    other = _job2(conn, sid, 2)
    t2 = q.add_job_tag(conn, other, "language: english")
    assert t2 == t1  # first spelling stays canonical
    assert conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0] == 1


def test_add_empty_tag_is_rejected(conn):
    jid = _job(conn)
    assert q.add_job_tag(conn, jid, "   ") is None
    assert q.get_job_tags(conn, jid) == []


def test_add_same_tag_twice_is_idempotent_and_logs_once(conn):
    jid = _job(conn)
    q.add_job_tag(conn, jid, "Remote")
    q.add_job_tag(conn, jid, "remote")
    assert [t["name"] for t in q.get_job_tags(conn, jid)] == ["Remote"]
    msgs = [e["message"] for e in q.get_job_events(conn, jid) if e["kind"] == "tag_added"]
    assert msgs == ["Tag added: Remote"]


def test_remove_tag_deletes_orphan_and_logs(conn):
    jid = _job(conn)
    t = q.add_job_tag(conn, jid, "Remote")
    assert q.remove_job_tag(conn, jid, t["id"]) == "Remote"
    assert conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0] == 0
    assert q.remove_job_tag(conn, jid, t["id"]) is None
    msgs = [e["message"] for e in q.get_job_events(conn, jid) if e["kind"] == "tag_removed"]
    assert msgs == ["Tag removed: Remote"]


def test_remove_keeps_tag_used_elsewhere(conn):
    jid = _job(conn)
    sid = q.get_job(conn, jid)["source_id"]
    other = _job2(conn, sid, 2)
    t = q.add_job_tag(conn, jid, "Remote")
    q.add_job_tag(conn, other, "Remote")
    q.remove_job_tag(conn, jid, t["id"])
    assert q.get_tag_names_by_usage(conn) == ["Remote"]


def test_tag_names_by_usage_order(conn):
    jid = _job(conn)
    sid = q.get_job(conn, jid)["source_id"]
    other = _job2(conn, sid, 2)
    q.add_job_tag(conn, jid, "b-rare")
    q.add_job_tag(conn, jid, "z-common")
    q.add_job_tag(conn, other, "z-common")
    q.add_job_tag(conn, other, "A-rare")
    assert q.get_tag_names_by_usage(conn) == ["z-common", "A-rare", "b-rare"]


def test_job_rows_carry_tags_json(conn):
    jid = _job(conn)
    q.add_job_tag(conn, jid, "b")
    q.add_job_tag(conn, jid, "A")
    names = [t["name"] for t in json.loads(q.get_job(conn, jid)["tags_json"])]
    assert names == ["A", "b"]
    assert json.loads(q.get_job(conn, _job2(conn, q.get_job(conn, jid)["source_id"], 3))["tags_json"]) == []


def test_job_tags_template_filter():
    f = templates.env.filters["job_tags"]
    assert f(None) == []
    assert f('[{"id": 1, "name": "x"}]') == [{"id": 1, "name": "x"}]


def test_deleting_job_cascades_job_tags(conn):
    jid = _job(conn)
    q.add_job_tag(conn, jid, "Remote")
    conn.execute("DELETE FROM jobs WHERE id = ?", (jid,))
    assert conn.execute("SELECT COUNT(*) FROM job_tags").fetchone()[0] == 0
    assert q.get_tag_names_by_usage(conn) == []  # orphan tag row is invisible


def _three_tagged_jobs(conn):
    sid = q.insert_source(conn, "s", "https://s.example", "generic_listing")
    ids = []
    for n, tags in enumerate([["Remote", "English"], ["Remote"], ["English", "Agency"]]):
        jid = q.insert_job(conn, source_id=sid, url=f"https://s.example/{n}", title=f"J{n}", company="", raw_text="r")
        q.update_job_pipeline(conn, jid, simplified_content="rust role", content_type="lead", summary="rust")
        for t in tags:
            q.add_job_tag(conn, jid, t)
        ids.append(jid)
    return ids


def test_tag_include_is_and(conn):
    a, b, c = _three_tagged_jobs(conn)
    got = {j["id"] for j in q.get_jobs_for_tabs(conn, ["lead"], tags=["remote", "English"])}
    assert got == {a}


def test_tag_exclude_is_and_not(conn):
    a, b, c = _three_tagged_jobs(conn)
    got = {j["id"] for j in q.get_jobs_for_tabs(conn, ["lead"], not_tags=["agency"])}
    assert got == {a, b}
    got = {j["id"] for j in q.get_jobs_for_tabs(conn, ["lead"], tags=["English"], not_tags=["Agency"])}
    assert got == {a}


def test_tag_filters_apply_to_get_jobs_search_and_counts(conn):
    a, b, c = _three_tagged_jobs(conn)
    assert {j["id"] for j in q.get_jobs(conn, tags=["Remote"])} == {a, b}
    assert {j["id"] for j in q.search_jobs(conn, "rust", tags=["Remote"], not_tags=["English"])} == {b}
    assert q.get_job_counts(conn, tags=["Remote"])["lead"] == 2
    assert q.get_job_counts(conn, not_tags=["Remote"], q="rust")["lead"] == 1
