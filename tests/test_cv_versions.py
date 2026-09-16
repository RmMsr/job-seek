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


def _save_base(conn, text, **overrides):
    kwargs = dict(base_instruction="", base_guardrails="", css="", default_scope=[])
    kwargs.update(overrides)
    q.save_cv_settings(conn, base_cv=text, **kwargs)


def _age_base_versions(conn):
    """Push every base version out of the 1h manual-edit stacking window."""
    conn.execute(
        "UPDATE cv_versions SET updated_at = datetime('now', '-2 hours') "
        "WHERE entity_type = 'base' AND entity_id = 1"
    )
    conn.commit()


def test_manual_edit_stacks_within_the_hour_and_opens_new_version_after(conn):
    _save_base(conn, "v1")
    first_id = q.get_cv_settings(conn)["current_version_id"]

    _save_base(conn, "v2")
    settings = q.get_cv_settings(conn)
    assert settings["current_version_id"] == first_id   # stacked, same version
    assert settings["base_cv"] == "v2"

    conn.execute(
        "UPDATE cv_versions SET updated_at = datetime('now', '-2 hours') WHERE id = ?",
        (first_id,),
    )
    conn.commit()
    _save_base(conn, "v3")
    settings = q.get_cv_settings(conn)
    assert settings["current_version_id"] != first_id    # window lapsed -> new version
    assert settings["base_cv"] == "v3"

    versions = q.get_versions(conn, "base", 1)            # all versions, current included
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
    _save_base(conn, "# Base", css="a{}")
    q.accept_base_cv(conn)
    before = q.get_cv_settings(conn)
    assert before["accepted_at"] is not None

    _save_base(conn, "# Base", css="b{}")        # same content, different CSS
    after = q.get_cv_settings(conn)

    assert after["current_version_id"] == before["current_version_id"]
    assert after["accepted_at"] == before["accepted_at"]   # still accepted
    assert after["css"] == "b{}"                           # the settings field did save
    assert len(q.get_versions(conn, "base", 1)) == 1       # no spurious version


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
    _save_base(conn, "base 0")
    _age_base_versions(conn)                    # so the next edit doesn't stack
    accepted_id = q.get_cv_settings(conn)["current_version_id"]
    q.accept_base_cv(conn)
    for i in range(1, 13):
        _save_base(conn, f"base {i}")
        _age_base_versions(conn)

    versions = q.get_versions(conn, "base", 1)
    ids = {v["id"] for v in versions}
    assert accepted_id in ids
    assert len(versions) == 11
    settings = q.get_cv_settings(conn)
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
    for i in range(12):
        _save_base(conn, f"base {i}")
        _age_base_versions(conn)
    surviving = sorted(v["id"] for v in q.get_versions(conn, "base", 1))
    oldest_id = surviving[0]

    assert q.revert_base_cv_version(conn, oldest_id) is True
    for i in range(12, 24):
        _save_base(conn, f"base {i}")
        _age_base_versions(conn)

    settings = q.get_cv_settings(conn)
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
    _save_base(conn, "v1")
    old_id = q.get_cv_settings(conn)["current_version_id"]
    _age_base_versions(conn)
    _save_base(conn, "v2")
    current_id = q.get_cv_settings(conn)["current_version_id"]
    assert current_id != old_id

    q.accept_base_cv_version(conn, old_id)
    settings = q.get_cv_settings(conn)
    assert settings["current_version_id"] == current_id   # unchanged
    assert q.get_version(conn, "base", 1, old_id)["accepted_at"] is not None
    assert q.get_accepted_base_cv(conn) == "v1"

    # accepting a different version moves the mark, not stacks it
    q.accept_base_cv_version(conn, current_id)
    assert q.get_version(conn, "base", 1, old_id)["accepted_at"] is None
    assert q.get_version(conn, "base", 1, current_id)["accepted_at"] is not None


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

    q.upsert_job_cv(conn, jid, tailored_cv="draft 1")
    first_id = q.get_job_cv(conn, jid)["current_version_id"]
    first_version = q.get_version(conn, "tailored", jid, first_id)
    assert first_version["parent_version_id"] is None       # nothing preceded it

    q.upsert_job_cv(conn, jid, tailored_cv="draft 2")        # 'update' -> always a new row
    second_id = q.get_job_cv(conn, jid)["current_version_id"]
    second_version = q.get_version(conn, "tailored", jid, second_id)
    assert second_version["parent_version_id"] == first_id


def test_resolve_base_cv_falls_back_to_current_until_accepted(conn):
    _save_base(conn, "draft base")
    assert q.resolve_base_cv(conn) == "draft base"   # no accept yet -> current

    q.accept_base_cv(conn)
    assert q.resolve_base_cv(conn) == "draft base"

    _save_base(conn, "wip edit")
    assert q.resolve_base_cv(conn) == "draft base"   # accepted stays in effect, draft ignored

    q.accept_base_cv(conn)
    assert q.resolve_base_cv(conn) == "wip edit"     # re-accepted -> moves


def test_get_accepted_base_cv_is_none_until_something_is_accepted(conn):
    _save_base(conn, "draft base")
    assert q.get_accepted_base_cv(conn) is None
    q.accept_base_cv(conn)
    assert q.get_accepted_base_cv(conn) == "draft base"


def test_versions_carry_their_parent_hash_for_display(conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="draft 1")
    first_id = q.get_job_cv(conn, jid)["current_version_id"]
    first_hash = q.get_version(conn, "tailored", jid, first_id)["hash"]
    assert q.get_version(conn, "tailored", jid, first_id)["parent_hash"] is None

    q.upsert_job_cv(conn, jid, tailored_cv="draft 2")
    second_id = q.get_job_cv(conn, jid)["current_version_id"]
    second = q.get_version(conn, "tailored", jid, second_id)
    assert second["parent_hash"] == first_hash

    versions = {v["id"]: v for v in q.get_versions(conn, "tailored", jid)}
    assert versions[second_id]["parent_hash"] == first_hash


def test_first_tailored_version_names_the_base_cv_version_as_its_parent(conn):
    _save_base(conn, "base draft")
    base_id = q.get_cv_settings(conn)["current_version_id"]
    base_hash = q.get_version(conn, "base", 1, base_id)["hash"]

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
    _save_base(conn, "old accepted base")
    accepted_id = q.get_cv_settings(conn)["current_version_id"]
    accepted_hash = q.get_version(conn, "base", 1, accepted_id)["hash"]
    q.accept_base_cv(conn)
    _age_base_versions(conn)
    _save_base(conn, "newer unaccepted draft")   # current moves on, accepted stays put

    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="draft")
    first = q.get_version(conn, "tailored", jid, q.get_job_cv(conn, jid)["current_version_id"])
    assert first["parent_hash"] == accepted_hash   # tailoring used the accepted version


def test_set_base_cv_autosaves_as_a_manual_edit_leaving_other_settings_untouched(conn):
    q.save_cv_settings(conn, base_cv="old", base_instruction="keep me",
                       base_guardrails="keep me too", css="keep", default_scope=[])
    q.set_base_cv(conn, "new hand-edited base")
    s = q.get_cv_settings(conn)
    assert s["base_cv"] == "new hand-edited base"
    assert s["base_instruction"] == "keep me"
    assert s["base_guardrails"] == "keep me too"
    assert s["css"] == "keep"
    current = q.get_version(conn, "base", 1, s["current_version_id"])
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
    _save_base(conn, "base content")
    base_id = q.get_cv_settings(conn)["current_version_id"]
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
    _save_base(conn, "base content")
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="tailored draft", base_hash="stale-hash")

    q.reset_job_cv_to_base(conn, jid)

    row = q.get_job_cv(conn, jid)
    settings = q.get_cv_settings(conn)
    settings["base_cv"] = q.resolve_base_cv(conn)
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
