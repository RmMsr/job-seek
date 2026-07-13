import pytest
from app.config import load_config, Config


def test_load_config(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[llm]\nendpoint = "http://localhost:11434/v1"\nmodel = "llama3.2"\n'
        '[database]\npath = "test.db"\n'
        '[browser]\nprofile_dir = "browser-profile"\n'
    )
    result = load_config(str(cfg))
    assert isinstance(result, Config)
    assert result.llm_endpoint == "http://localhost:11434/v1"
    assert result.llm_model == "llama3.2"
    assert result.db_path == "test.db"
    assert result.browser_profile_dir == "browser-profile"
