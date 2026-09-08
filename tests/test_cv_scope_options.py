import sqlite3
from app.db import queries as q


def test_fresh_db_has_no_scope_options_until_seeded():
    # Uses a bare connection with only the table created (not the full
    # init_db migration path) -- init_db's conn fixture auto-seeds
    # cv_scope_options via _migrate_seed_and_remap_cv_scope_options, so it
    # can't demonstrate the "empty table" case on its own.
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute(
        "CREATE TABLE cv_scope_options (id INTEGER PRIMARY KEY, description TEXT NOT NULL, "
        "default_enabled INTEGER NOT NULL DEFAULT 0, "
        "sort_order INTEGER NOT NULL, created_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    assert q.get_scope_options(c) == []


def test_seed_creates_five_defaults_in_order(conn):
    q.reset_scope_options(conn)
    opts = q.get_scope_options(conn)
    assert len(opts) == 6
    assert [o["sort_order"] for o in opts] == [0, 1, 2, 3, 4, 5]
    assert opts[0]["default_enabled"] == 1
    assert opts[3]["default_enabled"] == 0


def test_seed_writes_scope_names(conn):
    q.reset_scope_options(conn)
    opts = q.get_scope_options(conn)
    assert [o["name"] for o in opts] == ["correct", "choose", "organize", "rephrase", "introduce", "wildcard"]


def test_insert_and_update_round_trip_name(conn):
    q.reset_scope_options(conn)
    nid = q.insert_scope_option(conn, "Custom.", name="custom", default_enabled=True)
    assert q.get_scope_option(conn, nid)["name"] == "custom"
    q.update_scope_option(conn, nid, "Custom.", default_enabled=True, name="renamed")
    assert q.get_scope_option(conn, nid)["name"] == "renamed"


def test_insert_appends_at_end_with_next_sort_order(conn):
    q.reset_scope_options(conn)
    new_id = q.insert_scope_option(conn, "Custom scope type.", default_enabled=True)
    opts = q.get_scope_options(conn)
    assert opts[-1]["id"] == new_id
    assert opts[-1]["sort_order"] == 6
    assert opts[-1]["description"] == "Custom scope type."
    assert opts[-1]["default_enabled"] == 1


def test_update_scope_option_changes_description_and_flag(conn):
    q.reset_scope_options(conn)
    opts = q.get_scope_options(conn)
    first_id = opts[0]["id"]
    q.update_scope_option(conn, first_id, "Edited text.", default_enabled=False)
    updated = q.get_scope_option(conn, first_id)
    assert updated["description"] == "Edited text."
    assert updated["default_enabled"] == 0


def test_delete_scope_option_removes_it(conn):
    q.reset_scope_options(conn)
    opts = q.get_scope_options(conn)
    q.delete_scope_option(conn, opts[0]["id"])
    assert len(q.get_scope_options(conn)) == 5


def test_reset_scope_options_wipes_edits_and_restores_defaults(conn):
    q.reset_scope_options(conn)
    opts = q.get_scope_options(conn)
    q.update_scope_option(conn, opts[0]["id"], "Mangled.", default_enabled=False)
    q.insert_scope_option(conn, "Extra one.")
    q.reset_scope_options(conn)
    fresh = q.get_scope_options(conn)
    assert len(fresh) == 6
    assert fresh[0]["default_enabled"] == 1
    assert "Mangled." not in [o["description"] for o in fresh]
