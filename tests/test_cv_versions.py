import sqlite3

import pytest

from app.db import queries as q

# NOTE: every test here takes the shared `conn` fixture from conftest.py, which
# enables `PRAGMA foreign_keys = ON`. Do not hand-roll a connection in a test
# body — without the pragma, SQLite silently ignores foreign-key violations and
# real bugs in the version-pruning / deletion paths go undetected.


def _job(conn, url="http://x/1", title="Role"):
    conn.execute(
        "INSERT OR IGNORE INTO sources (name, url, fetcher_type) VALUES ('s','http://x','manual')"
    )
    cur = conn.execute(
        "INSERT INTO jobs (source_id, url, title) VALUES (1, ?, ?)", (url, title)
    )
    conn.commit()
    return cur.lastrowid


def _save_base(conn, text, base_cv_id=None, **overrides):
    if base_cv_id is None:
        base_cv_id = q.list_base_cvs(conn)[0]["id"]
    q.set_base_cv(conn, base_cv_id, text)
    kwargs = dict(base_instruction="", base_guardrails="", css="", default_scope=[])
    kwargs.update(overrides)
    q.save_cv_settings(conn, **kwargs)
    return base_cv_id


def _age_base_versions(conn, base_cv_id=1):
    """Push every base version out of the 1h manual-edit stacking window."""
    conn.execute(
        "UPDATE cv_versions SET updated_at = datetime('now', '-2 hours') "
        "WHERE entity_type = 'base' AND entity_id = ?",
        (base_cv_id,),
    )
    conn.commit()


def test_manual_edit_stacks_within_the_hour_and_opens_new_version_after(conn):
    base_cv_id = _save_base(conn, "v1")
    first_id = q.get_base_cv(conn, base_cv_id)["current_version_id"]

    _save_base(conn, "v2", base_cv_id=base_cv_id)
    settings = q.get_base_cv(conn, base_cv_id)
    assert settings["current_version_id"] == first_id   # stacked, same version
    assert settings["base_cv"] == "v2"

    conn.execute(
        "UPDATE cv_versions SET updated_at = datetime('now', '-2 hours') WHERE id = ?",
        (first_id,),
    )
    conn.commit()
    _save_base(conn, "v3", base_cv_id=base_cv_id)
    settings = q.get_base_cv(conn, base_cv_id)
    assert settings["current_version_id"] != first_id    # window lapsed -> new version
    assert settings["base_cv"] == "v3"

    versions = q.get_versions(conn, "base", base_cv_id)   # all versions, current included
    assert len(versions) == 2
    stacked = next(v for v in versions if v["id"] == first_id)
    assert stacked["content"] == "v2"                     # the stacked v1->v2 version, now history
    current = next(v for v in versions if v["id"] == settings["current_version_id"])
    assert current["content"] == "v3"


def test_update_action_never_stacks_even_seconds_apart(conn):
    jid = _job(conn)

    q.upsert_job_cv(conn, jid, tailored_cv="draft 1")
    first_id = q.get_job_cv(conn, jid)["current_version_id"]
    q.upsert_job_cv(conn, jid, tailored_cv="draft 2")
    second_id = q.get_job_cv(conn, jid)["current_version_id"]
    assert second_id != first_id
    versions = q.get_versions(conn, "tailored", jid)   # all versions, current included
    assert {v["id"] for v in versions} == {first_id, second_id}


def test_manual_edit_after_an_update_opens_a_new_version(conn):
    """The stacking window is only ever entered from another manual edit — an
    LLM 'update' version is never mutated in place, however fresh it is."""
    jid = _job(conn)

    q.upsert_job_cv(conn, jid, tailored_cv="generated")      # action='update'
    update_id = q.get_job_cv(conn, jid)["current_version_id"]

    q.set_job_cv_tailored(conn, jid, "hand edited")          # action='manual_edit'
    row = q.get_job_cv(conn, jid)
    assert row["current_version_id"] != update_id            # no stacking onto an update
    assert row["tailored_cv"] == "hand edited"
    assert q.get_version(conn, "tailored", jid, update_id)["content"] == "generated"


def test_manual_edit_never_mutates_an_accepted_version_in_place(conn):
    jid = _job(conn)

    q.set_job_cv_tailored(conn, jid, "draft")
    accepted_id = q.get_job_cv(conn, jid)["current_version_id"]
    q.accept_job_cv(conn, jid)

    q.set_job_cv_tailored(conn, jid, "edited after accept")
    row = q.get_job_cv(conn, jid)
    assert row["current_version_id"] != accepted_id       # new version opened
    assert row["tailored_cv"] == "edited after accept"
    accepted_version = q.get_version(conn, "tailored", jid, accepted_id)
    assert accepted_version["content"] == "draft"          # untouched
    assert accepted_version["accepted_at"] is not None     # badge stayed on it
    assert row["accepted_at"] is None                      # new current isn't accepted


def test_unchanged_content_is_not_versioned_again(conn):
    """Saving settings-only fields round-trips the untouched base_cv back
    through save_cv_settings — that must not spawn a duplicate version or
    move the accepted marker off the base CV."""
    base_cv_id = _save_base(conn, "# Base", css="a{}")
    q.accept_base_cv(conn, base_cv_id)
    before = q.get_base_cv(conn, base_cv_id)
    assert before["accepted_at"] is not None

    _save_base(conn, "# Base", base_cv_id=base_cv_id, css="b{}")  # same content, different CSS
    after = q.get_base_cv(conn, base_cv_id)

    assert after["current_version_id"] == before["current_version_id"]
    assert after["accepted_at"] == before["accepted_at"]   # still accepted
    assert q.get_cv_settings(conn)["css"] == "b{}"          # the settings field did save
    assert len(q.get_versions(conn, "base", base_cv_id)) == 1  # no spurious version


def test_edit_after_revert_opens_a_new_version_instead_of_overwriting(conn):
    """A reverted-to version may be a recent unaccepted manual edit, but it is
    no longer the newest version — stacking onto it would destroy the very
    content that was just reverted to."""
    jid = _job(conn)

    q.set_job_cv_tailored(conn, jid, "version A")            # manual_edit
    a_id = q.get_job_cv(conn, jid)["current_version_id"]
    q.upsert_job_cv(conn, jid, tailored_cv="version B")      # update -> new row
    b_id = q.get_job_cv(conn, jid)["current_version_id"]
    assert b_id != a_id

    assert q.revert_job_cv_version(conn, jid, a_id) is True
    q.set_job_cv_tailored(conn, jid, "version C")            # immediately, within the hour

    row = q.get_job_cv(conn, jid)
    assert row["current_version_id"] not in (a_id, b_id)     # a fresh version
    assert row["tailored_cv"] == "version C"
    assert q.get_version(conn, "tailored", jid, a_id)["content"] == "version A"  # preserved


def test_retention_caps_at_ten_but_keeps_accepted_and_current(conn):
    jid = _job(conn)

    q.upsert_job_cv(conn, jid, tailored_cv="draft 0")
    accepted_id = q.get_job_cv(conn, jid)["current_version_id"]
    q.accept_job_cv(conn, jid)
    for i in range(1, 13):
        q.upsert_job_cv(conn, jid, tailored_cv=f"draft {i}")  # each an 'update' -> always new version

    versions = q.get_versions(conn, "tailored", jid)
    ids = {v["id"] for v in versions}
    assert accepted_id in ids                   # old accepted version survived pruning
    assert len(versions) == 11                  # the 10 most recent + the accepted one
    assert q.get_job_cv(conn, jid)["current_version_id"] in ids
    assert q.get_job_cv(conn, jid)["tailored_cv"] == "draft 12"


def test_retention_caps_at_ten_but_keeps_accepted_and_current_for_the_base_cv(conn):
    base_cv_id = _save_base(conn, "base 0")
    _age_base_versions(conn, base_cv_id)         # so the next edit doesn't stack
    accepted_id = q.get_base_cv(conn, base_cv_id)["current_version_id"]
    q.accept_base_cv(conn, base_cv_id)
    for i in range(1, 13):
        _save_base(conn, f"base {i}", base_cv_id=base_cv_id)
        _age_base_versions(conn, base_cv_id)

    versions = q.get_versions(conn, "base", base_cv_id)
    ids = {v["id"] for v in versions}
    assert accepted_id in ids
    assert len(versions) == 11
    settings = q.get_base_cv(conn, base_cv_id)
    assert settings["current_version_id"] in ids
    assert settings["base_cv"] == "base 12"


def test_pruning_never_deletes_the_version_current_still_points_at(conn):
    """Regression: after a revert, current_version_id points at an old row that
    the retention window no longer covers. Pruning it would break the foreign
    key the not-yet-updated pointer still holds."""
    jid = _job(conn)

    for i in range(12):
        q.upsert_job_cv(conn, jid, tailored_cv=f"draft {i}")
    surviving = sorted(v["id"] for v in q.get_versions(conn, "tailored", jid))
    oldest_id = surviving[0]                    # already at the edge of the window

    assert q.revert_job_cv_version(conn, jid, oldest_id) is True
    for i in range(12, 24):                     # keep editing past the retention cap
        q.upsert_job_cv(conn, jid, tailored_cv=f"draft {i}")

    row = q.get_job_cv(conn, jid)
    assert row["tailored_cv"] == "draft 23"
    assert row["current_version_id"] is not None


def test_pruning_never_deletes_the_current_base_version_after_a_revert(conn):
    base_cv_id = q.list_base_cvs(conn)[0]["id"]
    for i in range(12):
        _save_base(conn, f"base {i}", base_cv_id=base_cv_id)
        _age_base_versions(conn, base_cv_id)
    surviving = sorted(v["id"] for v in q.get_versions(conn, "base", base_cv_id))
    oldest_id = surviving[0]

    assert q.revert_base_cv_version(conn, base_cv_id, oldest_id) is True
    for i in range(12, 24):
        _save_base(conn, f"base {i}", base_cv_id=base_cv_id)
        _age_base_versions(conn, base_cv_id)

    settings = q.get_base_cv(conn, base_cv_id)
    assert settings["base_cv"] == "base 23"
    assert settings["current_version_id"] is not None


def test_revert_repoints_without_copying_content(conn):
    jid = _job(conn)

    q.upsert_job_cv(conn, jid, tailored_cv="draft 1")
    old_id = q.get_job_cv(conn, jid)["current_version_id"]
    q.upsert_job_cv(conn, jid, tailored_cv="draft 2")

    assert q.revert_job_cv_version(conn, jid, old_id) is True
    row = q.get_job_cv(conn, jid)
    assert row["current_version_id"] == old_id
    assert row["tailored_cv"] == "draft 1"
    # draft 2's version is untouched, still in history
    versions = {v["id"]: v["content"] for v in q.get_versions(conn, "tailored", jid)}
    assert "draft 2" in versions.values()

    assert q.revert_job_cv_version(conn, jid, 99999) is False  # unknown version


def test_accept_base_cv_version_accepts_without_touching_current(conn):
    base_cv_id = _save_base(conn, "v1")
    old_id = q.get_base_cv(conn, base_cv_id)["current_version_id"]
    _age_base_versions(conn, base_cv_id)
    _save_base(conn, "v2", base_cv_id=base_cv_id)
    current_id = q.get_base_cv(conn, base_cv_id)["current_version_id"]
    assert current_id != old_id

    q.accept_base_cv_version(conn, base_cv_id, old_id)
    settings = q.get_base_cv(conn, base_cv_id)
    assert settings["current_version_id"] == current_id   # unchanged
    assert q.get_version(conn, "base", base_cv_id, old_id)["accepted_at"] is not None
    assert q.get_accepted_base_cv(conn, base_cv_id) == "v1"

    # accepting a different version moves the mark, not stacks it
    q.accept_base_cv_version(conn, base_cv_id, current_id)
    assert q.get_version(conn, "base", base_cv_id, old_id)["accepted_at"] is None
    assert q.get_version(conn, "base", base_cv_id, current_id)["accepted_at"] is not None


def test_accept_job_cv_version_accepts_without_touching_current(conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="draft 1")
    old_id = q.get_job_cv(conn, jid)["current_version_id"]
    q.upsert_job_cv(conn, jid, tailored_cv="draft 2")
    current_id = q.get_job_cv(conn, jid)["current_version_id"]
    assert current_id != old_id

    q.accept_job_cv_version(conn, jid, old_id)
    row = q.get_job_cv(conn, jid)
    assert row["current_version_id"] == current_id   # unchanged
    assert q.get_version(conn, "tailored", jid, old_id)["accepted_at"] is not None


def test_new_version_records_its_parent(conn):
    jid = _job(conn)
    default_id = q.list_base_cvs(conn)[0]["id"]  # lazy-seeds "Default" via resolve_job_base_cv_id
    default_base_version_id = q.get_base_cv(conn, default_id)["current_version_id"]

    q.upsert_job_cv(conn, jid, tailored_cv="draft 1")
    first_id = q.get_job_cv(conn, jid)["current_version_id"]
    first_version = q.get_version(conn, "tailored", jid, first_id)
    # The job's very first tailored version links back to whichever base CV
    # it was generated from -- here, the auto-materialized "Default" base CV.
    assert first_version["parent_version_id"] == default_base_version_id

    q.upsert_job_cv(conn, jid, tailored_cv="draft 2")        # 'update' -> always a new row
    second_id = q.get_job_cv(conn, jid)["current_version_id"]
    second_version = q.get_version(conn, "tailored", jid, second_id)
    assert second_version["parent_version_id"] == first_id


def test_resolve_base_cv_falls_back_to_current_until_accepted(conn):
    base_cv_id = _save_base(conn, "draft base")
    assert q.resolve_base_cv(conn, base_cv_id) == "draft base"   # no accept yet -> current

    q.accept_base_cv(conn, base_cv_id)
    assert q.resolve_base_cv(conn, base_cv_id) == "draft base"

    _save_base(conn, "wip edit", base_cv_id=base_cv_id)
    assert q.resolve_base_cv(conn, base_cv_id) == "draft base"   # accepted stays in effect, draft ignored

    q.accept_base_cv(conn, base_cv_id)
    assert q.resolve_base_cv(conn, base_cv_id) == "wip edit"     # re-accepted -> moves


def test_get_accepted_base_cv_is_none_until_something_is_accepted(conn):
    base_cv_id = _save_base(conn, "draft base")
    assert q.get_accepted_base_cv(conn, base_cv_id) is None
    q.accept_base_cv(conn, base_cv_id)
    assert q.get_accepted_base_cv(conn, base_cv_id) == "draft base"


def test_versions_carry_their_parent_hash_for_display(conn):
    jid = _job(conn)
    default_id = q.list_base_cvs(conn)[0]["id"]  # lazy-seeds "Default" via resolve_job_base_cv_id
    default_base_version = q.get_base_cv(conn, default_id)["current_version_id"]
    default_base_hash = q.get_version(conn, "base", default_id, default_base_version)["hash"]

    q.upsert_job_cv(conn, jid, tailored_cv="draft 1")
    first_id = q.get_job_cv(conn, jid)["current_version_id"]
    first_hash = q.get_version(conn, "tailored", jid, first_id)["hash"]
    # The first tailored version links back to the Default base CV it was
    # generated from, so it now carries a real parent hash, not None.
    assert q.get_version(conn, "tailored", jid, first_id)["parent_hash"] == default_base_hash

    q.upsert_job_cv(conn, jid, tailored_cv="draft 2")
    second_id = q.get_job_cv(conn, jid)["current_version_id"]
    second = q.get_version(conn, "tailored", jid, second_id)
    assert second["parent_hash"] == first_hash

    versions = {v["id"]: v for v in q.get_versions(conn, "tailored", jid)}
    assert versions[second_id]["parent_hash"] == first_hash


def test_first_tailored_version_names_the_base_cv_version_as_its_parent(conn):
    base_cv_id = _save_base(conn, "base draft")
    base_id = q.get_base_cv(conn, base_cv_id)["current_version_id"]
    base_hash = q.get_version(conn, "base", base_cv_id, base_id)["hash"]

    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="first draft")
    first = q.get_version(conn, "tailored", jid, q.get_job_cv(conn, jid)["current_version_id"])
    assert first["parent_hash"] == base_hash
    assert first["parent_entity_type"] == "base"

    # A later tailored version's parent goes back to being the prior tailored
    # version, not the base CV.
    q.upsert_job_cv(conn, jid, tailored_cv="second draft")
    second = q.get_version(conn, "tailored", jid, q.get_job_cv(conn, jid)["current_version_id"])
    assert second["parent_hash"] == first["hash"]
    assert second["parent_entity_type"] == "tailored"


def test_first_tailored_version_names_the_accepted_base_version_when_one_exists(conn):
    base_cv_id = _save_base(conn, "old accepted base")
    accepted_id = q.get_base_cv(conn, base_cv_id)["current_version_id"]
    accepted_hash = q.get_version(conn, "base", base_cv_id, accepted_id)["hash"]
    q.accept_base_cv(conn, base_cv_id)
    _age_base_versions(conn, base_cv_id)
    _save_base(conn, "newer unaccepted draft", base_cv_id=base_cv_id)  # current moves on, accepted stays put

    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="draft")
    first = q.get_version(conn, "tailored", jid, q.get_job_cv(conn, jid)["current_version_id"])
    assert first["parent_hash"] == accepted_hash   # tailoring used the accepted version


def test_set_base_cv_autosaves_as_a_manual_edit_leaving_other_settings_untouched(conn):
    base_cv_id = q.list_base_cvs(conn)[0]["id"]
    q.set_base_cv(conn, base_cv_id, "old")
    q.save_cv_settings(conn, base_instruction="keep me",
                       base_guardrails="keep me too", css="keep", default_scope=[])
    q.set_base_cv(conn, base_cv_id, "new hand-edited base")
    base = q.get_base_cv(conn, base_cv_id)
    s = q.get_cv_settings(conn)
    assert base["base_cv"] == "new hand-edited base"
    assert s["base_instruction"] == "keep me"
    assert s["base_guardrails"] == "keep me too"
    assert s["css"] == "keep"
    current = q.get_version(conn, "base", base_cv_id, base["current_version_id"])
    assert current["action"] == "manual_edit"


def test_cv_versions_action_allows_reset_and_note_defaults_empty(conn):
    conn.execute(
        "INSERT INTO cv_versions (hash, entity_type, entity_id, content, action) "
        "VALUES ('abcd1234', 'tailored', 1, 'content', 'reset')"
    )
    conn.commit()
    row = conn.execute("SELECT action, note FROM cv_versions WHERE hash = 'abcd1234'").fetchone()
    assert row["action"] == "reset"
    assert row["note"] == ""


def test_upsert_job_cv_records_note_on_update_version(conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="draft 1", note="correct, choose")
    version_id = q.get_job_cv(conn, jid)["current_version_id"]
    version = q.get_version(conn, "tailored", jid, version_id)
    assert version["action"] == "update"
    assert version["note"] == "correct, choose"


def test_upsert_job_cv_note_defaults_to_empty(conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="draft 1")
    version_id = q.get_job_cv(conn, jid)["current_version_id"]
    assert q.get_version(conn, "tailored", jid, version_id)["note"] == ""


def test_reset_job_cv_to_base_copies_base_content_and_labels_reset(conn):
    base_cv_id = _save_base(conn, "base content")
    base_id = q.get_base_cv(conn, base_cv_id)["current_version_id"]
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="tailored draft")

    q.reset_job_cv_to_base(conn, jid)

    row = q.get_job_cv(conn, jid)
    assert row["tailored_cv"] == "base content"
    version = q.get_version(conn, "tailored", jid, row["current_version_id"])
    assert version["action"] == "reset"
    assert version["parent_version_id"] == base_id   # base version, not the prior tailored one


def test_reset_job_cv_to_base_stamps_base_hash_fresh(conn):
    from app.routes.cv import _base_hash
    base_cv_id = _save_base(conn, "base content")
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="tailored draft", base_hash="stale-hash")

    q.reset_job_cv_to_base(conn, jid)

    row = q.get_job_cv(conn, jid)
    settings = q.get_cv_settings(conn)
    settings["base_cv"] = q.resolve_base_cv(conn, base_cv_id)
    assert row["base_hash"] == _base_hash(settings)
    assert row["base_cv_snapshot"] == "base content"


def test_reset_job_cv_to_base_leaves_scope_and_directives_untouched(conn):
    _save_base(conn, "base content")
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="tailored draft", scope=[1, 2])
    q.set_job_cv_directives(conn, jid, "- my directive")

    q.reset_job_cv_to_base(conn, jid)

    row = q.get_job_cv(conn, jid)
    assert row["scope"] == [1, 2]
    assert row["tuning_directives"] == "- my directive"


def test_reset_job_cv_to_base_is_a_noop_when_already_matching_base(conn):
    _save_base(conn, "base content")
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="base content")   # already equals base
    before_id = q.get_job_cv(conn, jid)["current_version_id"]

    q.reset_job_cv_to_base(conn, jid)

    row = q.get_job_cv(conn, jid)
    assert row["current_version_id"] == before_id     # no new version created
    assert len(q.get_versions(conn, "tailored", jid)) == 1


def test_manual_edit_and_reset_record_no_note(conn):
    jid = _job(conn)
    q.set_job_cv_tailored(conn, jid, "hand edited")
    row = q.get_job_cv(conn, jid)
    assert q.get_version(conn, "tailored", jid, row["current_version_id"])["note"] == ""

    q.reset_job_cv_to_base(conn, jid)
    row = q.get_job_cv(conn, jid)
    assert q.get_version(conn, "tailored", jid, row["current_version_id"])["note"] == ""


def test_create_list_rename_base_cv(conn):
    default_id = q.list_base_cvs(conn)[0]["id"]   # lazy-seeds "Default"
    assert q.list_base_cvs(conn) == [{"id": default_id, "name": "Default",
                                       "current_version_id": q.list_base_cvs(conn)[0]["current_version_id"],
                                       "created_at": q.list_base_cvs(conn)[0]["created_at"]}]

    second_id = q.create_base_cv(conn, "Backend", content="# Backend CV")
    names = {b["id"]: b["name"] for b in q.list_base_cvs(conn)}
    assert names == {default_id: "Default", second_id: "Backend"}
    assert q.get_base_cv(conn, second_id)["base_cv"] == "# Backend CV"

    q.rename_base_cv(conn, second_id, "Backend Engineer")
    assert q.get_base_cv(conn, second_id)["name"] == "Backend Engineer"


def test_create_base_cv_without_content_still_seeds_a_version(conn):
    base_cv_id = q.create_base_cv(conn, "Backend")
    row = q.get_base_cv(conn, base_cv_id)
    assert row["current_version_id"] is not None
    versions = q.get_versions(conn, "base", base_cv_id)
    assert len(versions) == 1
    assert versions[0]["content"] == ""
    assert versions[0]["action"] == "manual_edit"


def test_create_base_cv_duplicate_name_raises(conn):
    q.list_base_cvs(conn)  # seeds "Default"
    with pytest.raises(sqlite3.IntegrityError):
        q.create_base_cv(conn, "Default")


def test_delete_base_cv_unreferenced_removes_row_and_versions(conn):
    default_id = q.list_base_cvs(conn)[0]["id"]
    second_id = q.create_base_cv(conn, "Backend", content="# Backend CV")
    q.set_base_cv(conn, second_id, "# Backend CV v2")  # two versions

    assert q.delete_base_cv(conn, second_id) is True
    assert q.get_base_cv(conn, second_id) is None
    assert q.get_versions(conn, "base", second_id) == []
    assert [b["id"] for b in q.list_base_cvs(conn)] == [default_id]


def test_delete_base_cv_referenced_without_force_is_refused(conn):
    default_id = q.list_base_cvs(conn)[0]["id"]
    second_id = q.create_base_cv(conn, "Backend")
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, base_cv_id=second_id)

    assert q.count_jobs_using_base_cv(conn, second_id) == 1
    assert q.delete_base_cv(conn, second_id) is False
    assert q.get_base_cv(conn, second_id) is not None  # untouched


def test_delete_base_cv_referenced_with_force_deletes_and_nulls_job_link(conn):
    q.list_base_cvs(conn)  # seeds "Default" — otherwise "Backend" would be the
    # only base CV, and delete_base_cv's last-remaining protection (see its
    # docstring) refuses even with force=True.
    second_id = q.create_base_cv(conn, "Backend")
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, base_cv_id=second_id)

    assert q.delete_base_cv(conn, second_id, force=True) is True
    assert q.get_base_cv(conn, second_id) is None
    assert q.get_job_cv(conn, jid)["base_cv_id"] is None  # ON DELETE SET NULL


def test_delete_base_cv_refuses_last_remaining(conn):
    default_id = q.list_base_cvs(conn)[0]["id"]
    with pytest.raises(ValueError):
        q.delete_base_cv(conn, default_id, force=True)
    assert q.get_base_cv(conn, default_id) is not None


def test_resolve_job_base_cv_id_falls_back_to_default(conn):
    default_id = q.list_base_cvs(conn)[0]["id"]
    jid = _job(conn)
    assert q.resolve_job_base_cv_id(conn, jid) == default_id  # no job_cv row yet

    second_id = q.create_base_cv(conn, "Backend")
    q.upsert_job_cv(conn, jid, base_cv_id=second_id)
    assert q.resolve_job_base_cv_id(conn, jid) == second_id
