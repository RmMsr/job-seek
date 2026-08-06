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


from unittest.mock import patch


def test_reassess_fit_streams_progress(client, conn):
    def _fake_run_reassess_fit(conn, client, model):
        yield "Recomputing fit scores for 0 job(s)"
        yield "Fit recompute complete: 0 job(s) updated"
        return 0

    with patch("app.routes.profile.run_reassess_fit", side_effect=_fake_run_reassess_fit):
        resp = client.post("/profile/reassess-fit")

    assert resp.status_code == 200
    assert "Recomputing fit scores" in resp.text
    assert "Fit recompute complete" in resp.text


def test_profile_page_has_reassess_fit_button(client):
    resp = client.get("/profile")
    assert resp.status_code == 200
    assert 'data-progress-url="/profile/reassess-fit"' in resp.text
