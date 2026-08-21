from app.ai.client import make_client
from app.config import Config


def _config(**overrides) -> Config:
    defaults = {
        "llm_endpoint": "http://localhost:8080/v1",
        "llm_model": "gemma-4-26b",
        "llm_api_key": "not-needed",
        "browser_profile_dir": "job-seek",
        "db_path": "job-seek.db",
    }
    defaults.update(overrides)
    return Config(**defaults)


def test_make_client_uses_resolved_api_key():
    client = make_client(_config(llm_api_key="sk-test"))
    assert client.api_key == "sk-test"
    assert str(client.base_url) == "http://localhost:8080/v1/"


def test_make_client_defaults_to_not_needed():
    client = make_client(_config(llm_api_key="not-needed"))
    assert client.api_key == "not-needed"