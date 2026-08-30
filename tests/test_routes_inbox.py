from app.db import queries as q


def test_inbox_resolve_marks_resolved_and_clears_card(client, conn):
    item_id = q.create_inbox_item(conn, kind="browser_missing", message="decide me", link="/y")
    resp = client.post(f"/inbox/{item_id}/resolve")
    assert resp.status_code == 200
    assert q.count_unresolved_inbox_items(conn) == 0
    assert resp.text == ""
