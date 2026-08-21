from __future__ import annotations
from dataclasses import dataclass

HOSTED = "Hosted"
CUSTOM = "Custom"


@dataclass(frozen=True)
class ProviderPreset:
    endpoint: str
    label: str
    group: str
    requires_api_key: bool = False


LLM_PROVIDERS: dict[str, ProviderPreset] = {
    "openai": ProviderPreset("https://api.openai.com/v1", "OpenAI", HOSTED, True),
    "openrouter": ProviderPreset("https://openrouter.ai/api/v1", "OpenRouter", HOSTED, True),
    "groq": ProviderPreset("https://api.groq.com/openai/v1", "Groq", HOSTED, True),
    "gemini": ProviderPreset("https://generativelanguage.googleapis.com/v1beta/openai/", "Gemini", HOSTED, True),
    "deepseek": ProviderPreset("https://api.deepseek.com/v1", "DeepSeek", HOSTED, True),
    "mistral": ProviderPreset("https://api.mistral.ai/v1", "Mistral", HOSTED, True),
    "xai": ProviderPreset("https://api.x.ai/v1", "xAI", HOSTED, True),
    "custom": ProviderPreset("", "Custom (local / self-hosted)", CUSTOM, False),
}


def endpoint(provider: str) -> str:
    return LLM_PROVIDERS[provider].endpoint


def requires_api_key(provider: str) -> bool:
    return LLM_PROVIDERS[provider].requires_api_key


def resolve_llm(llm: dict) -> tuple[str, str, str]:
    provider = llm.get("provider", "")
    if provider in LLM_PROVIDERS and provider != "custom":
        llm_endpoint = LLM_PROVIDERS[provider].endpoint
    else:
        # custom provider, or legacy endpoint-only config with no provider key
        llm_endpoint = llm.get("endpoint", "")
    model = llm.get("model", "")
    api_key = llm.get("api_key") or "not-needed"
    return llm_endpoint, model, api_key


def provider_groups(providers: dict[str, ProviderPreset]) -> list[tuple[str, list[tuple[str, str]]]]:
    groups: list[tuple[str, list[tuple[str, str]]]] = []
    for group in (HOSTED, CUSTOM):
        items = [(key, preset.label) for key, preset in providers.items() if preset.group == group]
        if items:
            groups.append((group, items))
    return groups
