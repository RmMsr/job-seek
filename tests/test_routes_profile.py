from app.db import queries as q


def test_profile_page_returns_200(client):
    resp = client.get("/profile")
    assert resp.status_code == 200


def test_profile_save_and_display(client, conn):
    resp = client.post("/profile", data={"content": "I am a senior ML engineer."})
    assert resp.status_code == 200
    assert q.get_profile(conn) == "I am a senior ML engineer."
    resp2 = client.get("/profile")
    assert "I am a senior ML engineer." in resp2.text


def test_profile_save_shows_fading_confirmation(client, conn):
    resp = client.post("/profile", data={"content": "content"})
    assert resp.status_code == 200
    assert '<div class="save-confirmation" aria-live="polite">Saved.</div>' in resp.text


def test_profile_page_has_reevaluate_everything_button(client):
    resp = client.get("/profile")
    assert resp.status_code == 200
    assert 'data-progress-url="/scenarios/reevaluate"' in resp.text
    assert "Re-evaluate everything" in resp.text
