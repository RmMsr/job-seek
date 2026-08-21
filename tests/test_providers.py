from app.providers import (
    LLM_PROVIDERS,
    endpoint,
    provider_groups,
    requires_api_key,
    resolve_llm,
)


def test_catalog_has_all_expected_providers():
    assert set(LLM_PROVIDERS) == {
        "openai",
        "openrouter",
        "groq",
        "gemini",
        "deepseek",
        "mistral",
        "xai",
        "custom",
    }


def test_hosted_providers_require_api_key():
    assert requires_api_key("openai")
    assert endpoint("openai") == "https://api.openai.com/v1"
    assert endpoint("gemini") == "https://generativelanguage.googleapis.com/v1beta/openai/"


def test_custom_provider_does_not_require_api_key():
    assert not requires_api_key("custom")


def test_resolve_llm_known_provider_derives_endpoint_no_default_model():
    endpoint_, model, api_key = resolve_llm({"provider": "groq"})
    assert endpoint_ == "https://api.groq.com/openai/v1"
    assert model == ""
    assert api_key == "not-needed"


def test_resolve_llm_explicit_model_and_key_win():
    endpoint_, model, api_key = resolve_llm(
        {"provider": "deepseek", "model": "deepseek-reasoner", "api_key": "sk-123"}
    )
    assert endpoint_ == "https://api.deepseek.com/v1"
    assert model == "deepseek-reasoner"
    assert api_key == "sk-123"


def test_resolve_llm_custom_uses_endpoint():
    endpoint_, _, _ = resolve_llm({"provider": "custom", "endpoint": "http://my-server/v1", "model": "m1"})
    assert endpoint_ == "http://my-server/v1"


def test_resolve_llm_legacy_endpoint_only_config():
    endpoint_, model, api_key = resolve_llm({"endpoint": "http://legacy:9000/v1", "model": "legacy-model"})
    assert endpoint_ == "http://legacy:9000/v1"
    assert model == "legacy-model"
    assert api_key == "not-needed"


def test_provider_groups_bucket_providers():
    groups = provider_groups(LLM_PROVIDERS)
    by_group = {name: [key for key, _ in items] for name, items in groups}
    assert set(by_group) == {"Hosted", "Custom"}
    assert by_group["Hosted"] == [
        "openai",
        "openrouter",
        "groq",
        "gemini",
        "deepseek",
        "mistral",
        "xai",
    ]
    assert by_group["Custom"] == ["custom"]


def test_provider_groups_use_display_labels_not_capitalized_keys():
    groups = provider_groups(LLM_PROVIDERS)
    labels = {key: label for _, items in groups for key, label in items}
    assert labels["openai"] == "OpenAI"
    assert labels["openrouter"] == "OpenRouter"
    assert labels["xai"] == "xAI"
