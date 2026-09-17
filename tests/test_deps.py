from unittest.mock import patch
import pytest
from app.config import Config
from app import deps


def _config(tmp_path):
    return Config(
        llm_endpoint="http://x", llm_model="m", llm_api_key="k",
        browser_profile_dir=str(tmp_path), db_path=str(tmp_path / "job-seek.db"),
    )


def test_get_db_does_not_run_migrations_per_request(tmp_path):
    """Regression: get_db() used to call init_db() (via _open_db) on every
    request, which included a full jobs-table scan+rewrite in
    _migrate_jobs_canonicalize_urls — frequent enough to collide with the
    worker thread's writes and raise 'database is locked'. Schema setup now
    happens once (app startup / _open_db); get_db() only connects."""
    config = _config(tmp_path)
    deps._open_db(config).close()  # establishes the schema, like startup does

    with patch.object(deps, "init_db") as mock_init_db, \
         patch.object(deps, "load_config", return_value=config):
        for _ in range(3):
            gen = deps.get_db()
            conn = next(gen)
            conn.execute("SELECT 1")
            with pytest.raises(StopIteration):
                next(gen)
    mock_init_db.assert_not_called()


def test_open_db_still_runs_migrations(tmp_path):
    config = _config(tmp_path)
    conn = deps._open_db(config)
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='jobs'"
        ).fetchone()
        assert row is not None
    finally:
        conn.close()
