import pytest
from app.config import validate_llm, write_config
from app.main import app
from app.deps import get_db
from app.db import queries as q
from fastapi.testclient import TestClient


def test_validate_llm_unknown_provider():
    assert validate_llm({"provider": "unknown"}) is not None


def test_validate_llm_hosted_requires_api_key():
    assert validate_llm({"provider": "openai", "api_key": ""}) is not None
    assert validate_llm({"provider": "groq", "api_key": "sk-xyz"}) is None


def test_validate_llm_custom_requires_endpoint():
    assert validate_llm({"provider": "custom", "endpoint": ""}) is not None
    assert validate_llm({"provider": "custom", "endpoint": "http://x:1/v1"}) is None


def test_write_config_roundtrip(tmp_path):
    cfg = tmp_path / "config.toml"
    write_config(str(cfg), provider="openai", api_key="sk-test", model="gpt-4o")
    from app.config import load_config
    result = load_config(str(cfg))
    assert result.llm_endpoint == "https://api.openai.com/v1"
    assert result.llm_model == "gpt-4o"
    assert result.llm_api_key == "sk-test"


def test_setup_get_renders_form(client, monkeypatch, tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[llm]\nprovider = "groq"\napi_key = "sk-live"\nmodel = "llama-3.3-70b-versatile"\n'
        '[database]\npath = "test.db"\n'
        '[browser]\nprofile_dir = "browser-profile"\n'
    )
    from app.config import check_config_status, load_config, load_raw_llm
    monkeypatch.setattr(check_config_status, "__defaults__", (str(config_path),))
    monkeypatch.setattr(load_config, "__defaults__", (str(config_path),))
    monkeypatch.setattr(load_raw_llm, "__defaults__", (str(config_path),))

    resp = client.get("/setup")
    assert resp.status_code == 200
    assert "LLM Provider Setup" in resp.text
    assert 'provider = "groq"' not in resp.text  # template doesn't echo provider directly
    assert 'llama-3.3-70b-versatile' in resp.text  # model pre-filled
    assert 'hx-post="/setup/models"' in resp.text
    assert 'list="model-options"' in resp.text
    assert "DEFAULTS" not in resp.text


def test_setup_get_legacy_endpoint_only_config_selects_custom_and_prefills_endpoint(client, monkeypatch, tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[llm]\nendpoint = "http://legacy:9000/v1"\nmodel = "legacy-model"\n'
        '[database]\npath = "test.db"\n'
        '[browser]\nprofile_dir = "browser-profile"\n'
    )
    from app.config import check_config_status, load_config, load_raw_llm
    monkeypatch.setattr(check_config_status, "__defaults__", (str(config_path),))
    monkeypatch.setattr(load_config, "__defaults__", (str(config_path),))
    monkeypatch.setattr(load_raw_llm, "__defaults__", (str(config_path),))

    resp = client.get("/setup")
    assert resp.status_code == 200
    assert '<option value="custom" selected>' in resp.text
    assert 'value="http://legacy:9000/v1"' in resp.text


def test_setup_post_saves_and_redirects(client, monkeypatch, tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[llm]\nprovider = "groq"\n'
        '[database]\npath = "test.db"\n'
        '[browser]\nprofile_dir = "browser-profile"\n'
    )
    from app.config import check_config_status, load_config
    monkeypatch.setattr(check_config_status, "__defaults__", (str(config_path),))
    monkeypatch.setattr(load_config, "__defaults__", (str(config_path),))
    # write_config uses default path "config.toml" relative to cwd - we need to chdir or monkeypatch
    import os
    original_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        resp = client.post(
            "/setup",
            data={"provider": "openai", "api_key": "sk-new", "model": "gpt-4o", "endpoint": ""},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/setup?saved=1"
        # Verify the written config
        content = config_path.read_text()
        assert 'provider = "openai"' in content
        assert 'api_key = "sk-new"' in content
        assert 'model = "gpt-4o"' in content
    finally:
        os.chdir(original_cwd)


def test_setup_post_rejects_hosted_without_key(client, monkeypatch, tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[llm]\nprovider = "groq"\n'
        '[database]\npath = "test.db"\n'
        '[browser]\nprofile_dir = "browser-profile"\n'
    )
    from app.config import check_config_status, load_config
    monkeypatch.setattr(check_config_status, "__defaults__", (str(config_path),))
    monkeypatch.setattr(load_config, "__defaults__", (str(config_path),))
    import os
    original_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        resp = client.post(
            "/setup",
            data={"provider": "openai", "api_key": "", "model": "gpt-4o", "endpoint": ""},
            follow_redirects=False,
        )
        assert resp.status_code == 400
        assert "API key is required" in resp.text
        assert "LLM Provider Setup" in resp.text
        assert 'provider = "openai"' not in config_path.read_text()
    finally:
        os.chdir(original_cwd)


def test_setup_test_ok_with_mock(client, monkeypatch, tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[llm]\nprovider = "groq"\n'
        '[database]\npath = "test.db"\n'
        '[browser]\nprofile_dir = "browser-profile"\n'
    )
    from app.config import check_config_status, load_config
    monkeypatch.setattr(check_config_status, "__defaults__", (str(config_path),))
    monkeypatch.setattr(load_config, "__defaults__", (str(config_path),))
    # Mock _ping in setup module
    import app.routes.setup as setup_mod
    monkeypatch.setattr(setup_mod, "_ping", lambda *a, **kw: "pong")

    resp = client.post(
        "/setup/test",
        data={"provider": "openai", "api_key": "sk-xyz", "model": "gpt-4o", "endpoint": ""},
    )
    assert resp.status_code == 200
    assert "Connection OK" in resp.text


def test_setup_test_error_without_key(client, monkeypatch, tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[llm]\nprovider = "groq"\n'
        '[database]\npath = "test.db"\n'
        '[browser]\nprofile_dir = "browser-profile"\n'
    )
    from app.config import check_config_status, load_config
    monkeypatch.setattr(check_config_status, "__defaults__", (str(config_path),))
    monkeypatch.setattr(load_config, "__defaults__", (str(config_path),))

    resp = client.post(
        "/setup/test",
        data={"provider": "openai", "api_key": "", "model": "gpt-4o", "endpoint": ""},
    )
    assert resp.status_code == 200
    assert "API key is required" in resp.text


def test_setup_post_rejects_hosted_without_model(client, monkeypatch, tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[llm]\nprovider = "groq"\n'
        '[database]\npath = "test.db"\n'
        '[browser]\nprofile_dir = "browser-profile"\n'
    )
    from app.config import check_config_status, load_config
    monkeypatch.setattr(check_config_status, "__defaults__", (str(config_path),))
    monkeypatch.setattr(load_config, "__defaults__", (str(config_path),))
    import os
    original_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        resp = client.post(
            "/setup",
            data={"provider": "openai", "api_key": "sk-new", "model": "", "endpoint": ""},
            follow_redirects=False,
        )
        assert resp.status_code == 400
        assert "model is required" in resp.text.lower()
        assert "LLM Provider Setup" in resp.text
        assert 'provider = "openai"' not in config_path.read_text()
    finally:
        os.chdir(original_cwd)


def test_setup_models_ok_with_mock(client, monkeypatch, tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[llm]\nprovider = "groq"\n'
        '[database]\npath = "test.db"\n'
        '[browser]\nprofile_dir = "browser-profile"\n'
    )
    from app.config import check_config_status, load_config
    monkeypatch.setattr(check_config_status, "__defaults__", (str(config_path),))
    monkeypatch.setattr(load_config, "__defaults__", (str(config_path),))
    import app.routes.setup as setup_mod
    monkeypatch.setattr(setup_mod, "_list_models", lambda *a, **kw: ["gpt-4o", "gpt-4o-mini"])

    resp = client.post(
        "/setup/models",
        data={"provider": "openai", "api_key": "sk-xyz", "endpoint": ""},
    )
    assert resp.status_code == 200
    assert '<datalist id="model-options">' in resp.text
    assert 'value="gpt-4o"' in resp.text
    assert 'value="gpt-4o-mini"' in resp.text


def test_setup_models_error_without_key(client, monkeypatch, tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[llm]\nprovider = "groq"\n'
        '[database]\npath = "test.db"\n'
        '[browser]\nprofile_dir = "browser-profile"\n'
    )
    from app.config import check_config_status, load_config
    monkeypatch.setattr(check_config_status, "__defaults__", (str(config_path),))
    monkeypatch.setattr(load_config, "__defaults__", (str(config_path),))

    resp = client.post(
        "/setup/models",
        data={"provider": "openai", "api_key": "", "endpoint": ""},
    )
    assert resp.status_code == 200
    assert "API key is required" in resp.text


def test_setup_models_error_on_client_failure(client, monkeypatch, tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[llm]\nprovider = "groq"\n'
        '[database]\npath = "test.db"\n'
        '[browser]\nprofile_dir = "browser-profile"\n'
    )
    from app.config import check_config_status, load_config
    monkeypatch.setattr(check_config_status, "__defaults__", (str(config_path),))
    monkeypatch.setattr(load_config, "__defaults__", (str(config_path),))
    import app.routes.setup as setup_mod

    def _boom(*a, **kw):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(setup_mod, "_list_models", _boom)

    resp = client.post(
        "/setup/models",
        data={"provider": "openai", "api_key": "sk-xyz", "endpoint": ""},
    )
    assert resp.status_code == 200
    assert "connection refused" in resp.text
    assert '<datalist id="model-options"></datalist>' in resp.text


def test_setup_get_with_saved_query_shows_banner(client, monkeypatch, tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[llm]\nprovider = "groq"\n'
        '[database]\npath = "test.db"\n'
        '[browser]\nprofile_dir = "browser-profile"\n'
    )
    from app.config import check_config_status, load_config, load_raw_llm
    monkeypatch.setattr(check_config_status, "__defaults__", (str(config_path),))
    monkeypatch.setattr(load_config, "__defaults__", (str(config_path),))
    monkeypatch.setattr(load_raw_llm, "__defaults__", (str(config_path),))

    resp = client.get("/setup?saved=1")
    assert resp.status_code == 200
    assert "Configuration saved" in resp.text


def test_setup_post_keeps_stored_api_key_when_field_left_blank(client, monkeypatch, tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[llm]\nprovider = "openai"\napi_key = "sk-existing"\nmodel = "gpt-4o-mini"\n'
        '[database]\npath = "test.db"\n'
        '[browser]\nprofile_dir = "browser-profile"\n'
    )
    from app.config import check_config_status, load_config, load_raw_llm
    monkeypatch.setattr(check_config_status, "__defaults__", (str(config_path),))
    monkeypatch.setattr(load_config, "__defaults__", (str(config_path),))
    monkeypatch.setattr(load_raw_llm, "__defaults__", (str(config_path),))
    import os
    original_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        resp = client.post(
            "/setup",
            data={"provider": "openai", "api_key": "", "model": "gpt-4o", "endpoint": ""},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        content = config_path.read_text()
        assert 'api_key = "sk-existing"' in content
        assert 'model = "gpt-4o"' in content
    finally:
        os.chdir(original_cwd)


def test_setup_test_does_not_reuse_stored_key_for_different_provider(client, monkeypatch, tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[llm]\nprovider = "openai"\napi_key = "sk-secret"\nmodel = "gpt-4o"\n'
        '[database]\npath = "test.db"\n'
        '[browser]\nprofile_dir = "browser-profile"\n'
    )
    from app.config import check_config_status, load_config, load_raw_llm
    monkeypatch.setattr(check_config_status, "__defaults__", (str(config_path),))
    monkeypatch.setattr(load_config, "__defaults__", (str(config_path),))
    monkeypatch.setattr(load_raw_llm, "__defaults__", (str(config_path),))

    resp = client.post(
        "/setup/test",
        data={"provider": "groq", "api_key": "", "model": "llama-3.3-70b-versatile", "endpoint": ""},
    )
    assert resp.status_code == 200
    assert "API key is required" in resp.text


def test_setup_test_does_not_reuse_stored_key_for_different_custom_endpoint(client, monkeypatch, tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[llm]\nprovider = "custom"\nendpoint = "http://trusted.example/v1"\napi_key = "sk-secret"\n'
        '[database]\npath = "test.db"\n'
        '[browser]\nprofile_dir = "browser-profile"\n'
    )
    from app.config import check_config_status, load_config, load_raw_llm
    monkeypatch.setattr(check_config_status, "__defaults__", (str(config_path),))
    monkeypatch.setattr(load_config, "__defaults__", (str(config_path),))
    monkeypatch.setattr(load_raw_llm, "__defaults__", (str(config_path),))
    import app.routes.setup as setup_mod
    captured = {}

    def fake_ping(provider, endpoint, model, api_key):
        captured["api_key"] = api_key
        return "pong"

    monkeypatch.setattr(setup_mod, "_ping", fake_ping)

    client.post(
        "/setup/test",
        data={"provider": "custom", "api_key": "", "model": "m1", "endpoint": "http://attacker.example/v1"},
    )
    assert captured["api_key"] == "not-needed"


def test_setup_models_reuses_stored_key_for_same_provider(client, monkeypatch, tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[llm]\nprovider = "openai"\napi_key = "sk-secret"\nmodel = "gpt-4o"\n'
        '[database]\npath = "test.db"\n'
        '[browser]\nprofile_dir = "browser-profile"\n'
    )
    from app.config import check_config_status, load_config, load_raw_llm
    monkeypatch.setattr(check_config_status, "__defaults__", (str(config_path),))
    monkeypatch.setattr(load_config, "__defaults__", (str(config_path),))
    monkeypatch.setattr(load_raw_llm, "__defaults__", (str(config_path),))
    import app.routes.setup as setup_mod
    captured = {}

    def fake_list_models(endpoint, api_key):
        captured["api_key"] = api_key
        return ["gpt-4o"]

    monkeypatch.setattr(setup_mod, "_list_models", fake_list_models)

    client.post(
        "/setup/models",
        data={"provider": "openai", "api_key": "", "endpoint": ""},
    )
    assert captured["api_key"] == "sk-secret"


def test_setup_test_reports_model_from_response_not_request(client, monkeypatch, tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[llm]\nprovider = "groq"\n'
        '[database]\npath = "test.db"\n'
        '[browser]\nprofile_dir = "browser-profile"\n'
    )
    from app.config import check_config_status, load_config, load_raw_llm
    monkeypatch.setattr(check_config_status, "__defaults__", (str(config_path),))
    monkeypatch.setattr(load_config, "__defaults__", (str(config_path),))
    monkeypatch.setattr(load_raw_llm, "__defaults__", (str(config_path),))

    class FakeMessage:
        content = "pong"

    class FakeChoice:
        message = FakeMessage()

    class FakeResponse:
        model = "actual-model-served"
        choices = [FakeChoice()]

    class FakeCompletions:
        def create(self, **kwargs):
            return FakeResponse()

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    import app.routes.setup as setup_mod
    monkeypatch.setattr(setup_mod.openai, "OpenAI", lambda **kw: FakeClient())

    resp = client.post(
        "/setup/test",
        data={"provider": "groq", "api_key": "sk-xyz", "model": "requested-model-name", "endpoint": ""},
    )
    assert resp.status_code == 200
    assert "actual-model-served" in resp.text
    assert "requested-model-name" not in resp.text