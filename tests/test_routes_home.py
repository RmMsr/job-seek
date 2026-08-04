from app.config import check_config_status
from app.db import queries as q
from app.deps import get_db_optional
from app.main import app


def _use_test_db(conn):
    app.dependency_overrides[get_db_optional] = lambda: conn


def _point_config_check_at(monkeypatch, path):
    # check_config_status() is called with no args (default path="config.toml",
    # resolved relative to cwd) from both home.py and deps.py. Patching the
    # default directly (rather than monkeypatch.chdir) avoids also breaking
    # Jinja2's FileSystemLoader, which resolves "app/templates" relative to cwd.
    monkeypatch.setattr(check_config_status, "__defaults__", (str(path),))


def test_home_missing_config_shows_banner(client, monkeypatch, tmp_path):
    _point_config_check_at(monkeypatch, tmp_path / "config.toml")
    resp = client.get("/")
    assert resp.status_code == 200
    assert "No config.toml found" in resp.text
    assert 'class="checklist"' not in resp.text
    assert "How it works" in resp.text


def test_home_config_without_inference_provider_shows_banner(client, monkeypatch, tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[llm]\nendpoint = ""\nmodel = ""\n\n[database]\npath = "x.db"\n\n[browser]\nprofile_dir = "x"\n'
    )
    _point_config_check_at(monkeypatch, config_path)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "no inference provider configured" in resp.text
    assert "How it works" in resp.text


def test_home_valid_config_nothing_set_up_shows_checklist(client, conn):
    _use_test_db(conn)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Fill in your profile" in resp.text
    assert "Create a search scenario" in resp.text
    assert "Add a source" in resp.text
    assert "Run your first fetch" in resp.text
    assert "Review your first job" in resp.text
    assert "awaiting review" not in resp.text


def test_home_partial_setup_shows_mixed_checklist(client, conn):
    _use_test_db(conn)
    q.upsert_profile(conn, "Python engineer")
    q.insert_scenario(conn, "Remote ML", "")
    resp = client.get("/")
    assert resp.status_code == 200
    assert '<a href="/profile">✓ Fill in your profile</a>' in resp.text
    assert '<a href="/scenarios">✓ Create a search scenario</a>' in resp.text
    assert '<a href="/sources">○ Add a source</a>' in resp.text


def test_home_full_setup_shows_actionable_block(client, conn):
    _use_test_db(conn)
    q.upsert_profile(conn, "Python engineer")
    scenario_id = q.insert_scenario(conn, "Remote ML", "")
    source_id = q.insert_source(conn, "finn.no", "https://finn.no", "http")
    run_id = q.start_fetch_run(conn, source_id)
    q.complete_fetch_run(conn, run_id, jobs_found=1, jobs_new=1)

    job_id = q.insert_job(conn, source_id=source_id, url="http://finn.no/1", title="ML Eng", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, job_id, simplified_content="c", content_type="job_posting", summary="s")
    q.upsert_job_score(conn, job_id, scenario_id, 0.5, "ok", "hash1")
    q.update_job_feedback(conn, job_id, "accepted", "good fit")

    other_job_id = q.insert_job(conn, source_id=source_id, url="http://finn.no/2", title="Other", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, other_job_id, simplified_content="c", content_type="job_posting", summary="s")
    q.upsert_job_score(conn, other_job_id, scenario_id, 0.5, "ok", "hash1")

    resp = client.get("/")
    assert resp.status_code == 200
    assert "1 job awaiting review" in resp.text
    assert "Last fetch:" in resp.text
    assert "Fill in your profile" not in resp.text
