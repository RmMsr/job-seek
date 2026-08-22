from __future__ import annotations
import json
import tomllib
from dataclasses import dataclass
from pathlib import Path
from app.providers import LLM_PROVIDERS, resolve_llm


@dataclass
class Config:
    llm_endpoint: str
    llm_model: str
    llm_api_key: str
    browser_profile_dir: str
    db_path: str


def load_config(path: str = "config.toml") -> Config:
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    llm_endpoint, llm_model, llm_api_key = resolve_llm(raw.get("llm", {}))
    return Config(
        llm_endpoint=llm_endpoint,
        llm_model=llm_model,
        llm_api_key=llm_api_key,
        browser_profile_dir=raw["browser"]["profile_dir"],
        db_path=raw["database"]["path"],
    )


@dataclass
class ConfigStatus:
    exists: bool
    has_llm_endpoint: bool
    has_llm_model: bool
    model_required: bool = True

    @property
    def ok(self) -> bool:
        return self.exists and self.has_llm_endpoint and (self.has_llm_model or not self.model_required)


def check_config_status(path: str = "config.toml") -> ConfigStatus:
    """Non-raising config check, unlike load_config(). get_db() also calls
    load_config() (for db_path), so a missing/broken config.toml would 500
    before a route body ever runs if this check isn't done independently
    first, without a DB dependency."""
    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    except (FileNotFoundError, tomllib.TOMLDecodeError):
        return ConfigStatus(exists=False, has_llm_endpoint=False, has_llm_model=False)
    llm = raw.get("llm", {})
    provider = llm.get("provider", "")
    llm_endpoint, llm_model, _ = resolve_llm(llm)
    return ConfigStatus(
        exists=True,
        has_llm_endpoint=bool(llm_endpoint),
        has_llm_model=bool(llm_model),
        # Mirrors resolve_llm()'s own hosted-vs-custom split: a hosted preset
        # needs an explicit model, but a custom/self-hosted endpoint (or a
        # legacy endpoint-only config with no provider key) often only ever
        # serves one model, so requiring one here would be a false "not
        # configured" reading of an intentionally model-less setup.
        model_required=provider in LLM_PROVIDERS and provider != "custom",
    )


def load_raw_llm(path: str = "config.toml") -> dict | None:
    """Load the raw [llm] section from config.toml without resolution."""
    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
        return raw.get("llm", {})
    except (FileNotFoundError, tomllib.TOMLDecodeError):
        return None


def validate_llm(llm: dict) -> str | None:
    provider = llm.get("provider", "")
    if provider not in LLM_PROVIDERS:
        return "Unknown provider. Choose from: " + ", ".join(sorted(LLM_PROVIDERS.keys()))
    if provider != "custom" and LLM_PROVIDERS[provider].requires_api_key:
        if not llm.get("api_key"):
            return f"An API key is required for {provider}."
    if provider == "custom":
        if not llm.get("endpoint"):
            return "An endpoint URL is required for a custom provider."
    return None


def validate_llm_for_save(llm: dict) -> str | None:
    err = validate_llm(llm)
    if err:
        return err
    provider = llm.get("provider", "")
    if provider != "custom" and LLM_PROVIDERS[provider].requires_api_key and not llm.get("model"):
        return f"A model is required for {provider}."
    return None


def write_config(
    path: str = "config.toml",
    *,
    provider: str,
    api_key: str = "",
    model: str = "",
    endpoint: str = "",
) -> None:
    p = Path(path)
    db_path = "job-seek.db"
    browser_profile_dir = "job-seek"
    if p.exists():
        try:
            with open(p, "rb") as f:
                raw = tomllib.load(f)
            db_path = raw.get("database", {}).get("path", db_path)
            browser_profile_dir = raw.get("browser", {}).get("profile_dir", browser_profile_dir)
        except tomllib.TOMLDecodeError:
            pass

    lines = []
    lines.append("[llm]")
    lines.append(f'provider = {json.dumps(provider)}')
    if api_key:
        lines.append(f'api_key = {json.dumps(api_key)}')
    if model:
        lines.append(f'model = {json.dumps(model)}')
    if provider == "custom" and endpoint:
        lines.append(f'endpoint = {json.dumps(endpoint)}')
    lines.append("")
    lines.append("[database]")
    lines.append(f'path = {json.dumps(db_path)}')
    lines.append("")
    lines.append("[browser]")
    lines.append(f'profile_dir = {json.dumps(browser_profile_dir)}')
    lines.append("")

    # Write directly (follow symlink if present) — do not rename-replace
    p.write_text("\n".join(lines))