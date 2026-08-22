from app.db import queries as q


def test_inbox_resolve_marks_resolved(client, conn):
    item_id = q.create_inbox_item(conn, kind="task_followup", message="x", link="/y")
    resp = client.post(f"/inbox/{item_id}/resolve")
    assert resp.status_code == 200
    assert q.count_unresolved_inbox_items(conn) == 0
