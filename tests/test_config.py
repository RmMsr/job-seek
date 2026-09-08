import pytest
from app.config import Config, check_config_status, load_config, validate_llm, validate_llm_for_save, write_config


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
    # legacy config has no provider/api_key: key falls back to "not-needed"
    assert result.llm_api_key == "not-needed"


def test_load_config_known_provider_derives_endpoint_no_default_model(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[llm]\nprovider = "groq"\n'
        '[database]\npath = "test.db"\n'
        '[browser]\nprofile_dir = "browser-profile"\n'
    )
    result = load_config(str(cfg))
    assert result.llm_endpoint == "https://api.groq.com/openai/v1"
    assert result.llm_model == ""
    assert result.llm_api_key == "not-needed"


def test_load_config_api_key_respected(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[llm]\nprovider = "openai"\napi_key = "sk-live"\nmodel = "gpt-4o"\n'
        '[database]\npath = "test.db"\n'
        '[browser]\nprofile_dir = "browser-profile"\n'
    )
    result = load_config(str(cfg))
    assert result.llm_endpoint == "https://api.openai.com/v1"
    assert result.llm_model == "gpt-4o"
    assert result.llm_api_key == "sk-live"


def test_load_config_custom_provider_uses_endpoint(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[llm]\nprovider = "custom"\nendpoint = "http://my-server:9000/v1"\n'
        '[database]\npath = "test.db"\n'
        '[browser]\nprofile_dir = "browser-profile"\n'
    )
    result = load_config(str(cfg))
    assert result.llm_endpoint == "http://my-server:9000/v1"


def test_check_config_status_custom_provider_without_model_is_ok(tmp_path):
    # A custom/self-hosted endpoint often only ever serves one model, so an
    # endpoint alone (no model) is a complete config for it — unlike hosted
    # providers, which always need an explicit model chosen.
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[llm]\nprovider = "custom"\nendpoint = "http://x:1/v1"\n'
        '[database]\npath = "x.db"\n'
        '[browser]\nprofile_dir = "x"\n'
    )
    status = check_config_status(str(cfg))
    assert status.exists and status.has_llm_endpoint
    assert not status.has_llm_model
    assert status.ok


def test_check_config_status_hosted_provider_without_model_not_ok(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[llm]\nprovider = "openai"\napi_key = "sk-live"\n'
        '[database]\npath = "x.db"\n'
        '[browser]\nprofile_dir = "x"\n'
    )
    status = check_config_status(str(cfg))
    assert status.exists and status.has_llm_endpoint
    assert not status.has_llm_model
    assert not status.ok


def test_check_config_status_ok_with_custom_provider_and_model(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[llm]\nprovider = "custom"\nendpoint = "http://x:1/v1"\nmodel = "llama3.2"\n'
        '[database]\npath = "x.db"\n'
        '[browser]\nprofile_dir = "x"\n'
    )
    status = check_config_status(str(cfg))
    assert status.exists and status.has_llm_endpoint and status.has_llm_model
    assert status.ok


def test_check_config_status_missing_config_not_ok(tmp_path):
    status = check_config_status(str(tmp_path / "config.toml"))
    assert not status.exists
    assert not status.ok


def test_validate_llm_unknown_provider():
    assert validate_llm({"provider": "unknown"}) is not None
    assert "unknown" in validate_llm({"provider": "unknown"}).lower()


def test_validate_llm_hosted_requires_api_key():
    err = validate_llm({"provider": "openai", "api_key": ""})
    assert err is not None
    assert "api key" in err.lower()


def test_validate_llm_hosted_with_api_key_ok():
    assert validate_llm({"provider": "openai", "api_key": "sk-xyz"}) is None


def test_validate_llm_custom_requires_endpoint():
    err = validate_llm({"provider": "custom"})
    assert err is not None
    assert "endpoint" in err.lower()


def test_validate_llm_custom_with_endpoint_ok():
    assert validate_llm({"provider": "custom", "endpoint": "http://x:1/v1"}) is None


def test_write_config_creates_file_with_provider(tmp_path):
    cfg = tmp_path / "config.toml"
    write_config(str(cfg), provider="openai", api_key="sk-xyz", model="gpt-4o")
    content = cfg.read_text()
    assert 'provider = "openai"' in content
    assert 'api_key = "sk-xyz"' in content
    assert 'model = "gpt-4o"' in content
    assert "[database]" in content and "path = \"job-seek.db\"" in content
    assert "[browser]" in content and "profile_dir = \"job-seek\"" in content


def test_write_config_preserves_database_and_browser(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[llm]\nprovider = "openai"\n'
        '[database]\npath = "my.db"\n'
        '[browser]\nprofile_dir = "my-profile"\n'
    )
    write_config(str(cfg), provider="groq")
    content = cfg.read_text()
    assert 'path = "my.db"' in content
    assert 'profile_dir = "my-profile"' in content
    assert 'provider = "groq"' in content


def test_write_config_omits_empty_optional_fields(tmp_path):
    cfg = tmp_path / "config.toml"
    write_config(str(cfg), provider="custom", api_key="", model="")
    content = cfg.read_text()
    assert 'provider = "custom"' in content
    assert "api_key" not in content
    assert "model" not in content


def test_write_config_custom_writes_endpoint(tmp_path):
    cfg = tmp_path / "config.toml"
    write_config(str(cfg), provider="custom", endpoint="http://localhost:9999/v1")
    content = cfg.read_text()
    assert 'endpoint = "http://localhost:9999/v1"' in content


def test_validate_llm_for_save_hosted_requires_model():
    err = validate_llm_for_save({"provider": "openai", "api_key": "sk-xyz", "model": ""})
    assert err is not None
    assert "model" in err.lower()


def test_validate_llm_for_save_hosted_with_model_ok():
    assert validate_llm_for_save({"provider": "openai", "api_key": "sk-xyz", "model": "gpt-4o"}) is None


def test_validate_llm_for_save_custom_allows_empty_model():
    assert validate_llm_for_save({"provider": "custom", "endpoint": "http://x:1/v1", "model": ""}) is None


def test_validate_llm_for_save_propagates_base_validation_errors():
    err = validate_llm_for_save({"provider": "openai", "api_key": "", "model": ""})
    assert err is not None
    assert "api key" in err.lower()


def test_write_config_recovers_from_corrupt_existing_file(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text("this is not [valid toml")
    write_config(str(cfg), provider="custom", model="llama3.2")
    content = cfg.read_text()
    assert 'provider = "custom"' in content
    assert 'path = "job-seek.db"' in content
    assert 'profile_dir = "job-seek"' in content


def test_cv_enabled_true(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('[llm]\nprovider = "custom"\nendpoint = "http://x/v1"\n\n[cv]\nenabled = true\n')
    from app.config import cv_enabled
    assert cv_enabled(str(p)) is True


def test_cv_enabled_defaults_false(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('[llm]\nprovider = "custom"\nendpoint = "http://x/v1"\n')
    from app.config import cv_enabled
    assert cv_enabled(str(p)) is False


def test_cv_enabled_missing_file_false(tmp_path):
    from app.config import cv_enabled
    assert cv_enabled(str(tmp_path / "nope.toml")) is False