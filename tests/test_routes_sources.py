from app.db import queries as q


def _seed(conn):
    return q.insert_source(conn, "finn.no", "https://finn.no", "http")


def test_sources_page_returns_200(client, conn):
    _seed(conn)
    resp = client.get("/sources")
    assert resp.status_code == 200
    assert "finn.no" in resp.text


def test_create_source(client, conn):
    resp = client.post(
        "/sources",
        data={"name": "finn.no", "url": "https://finn.no", "fetcher_type": "http"},
    )
    assert resp.status_code == 200
    sources = q.get_sources(conn)
    assert len(sources) == 1
    assert sources[0]["name"] == "finn.no"


def test_edit_form_returns_source_fields(client, conn):
    sid = _seed(conn)
    resp = client.get(f"/sources/{sid}/edit")
    assert resp.status_code == 200
    assert "https://finn.no" in resp.text


def test_update_source(client, conn):
    sid = _seed(conn)
    resp = client.post(
        f"/sources/{sid}",
        data={"url": "https://finn.no/new", "fetcher_type": "playwright", "enabled": "on"},
    )
    assert resp.status_code == 200
    source = q.get_source(conn, sid)
    assert source["url"] == "https://finn.no/new"
    assert source["fetcher_type"] == "playwright"
    assert source["enabled"] == 1
    assert source["name"] == "finn.no"


def test_update_source_unchecked_enabled_disables(client, conn):
    sid = _seed(conn)
    resp = client.post(
        f"/sources/{sid}",
        data={"url": "https://finn.no", "fetcher_type": "http"},
    )
    assert resp.status_code == 200
    assert q.get_source(conn, sid)["enabled"] == 0


def test_cancel_edit_returns_display_row(client, conn):
    sid = _seed(conn)
    resp = client.get(f"/sources/{sid}")
    assert resp.status_code == 200
    assert "finn.no" in resp.text
