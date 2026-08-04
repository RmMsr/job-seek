from __future__ import annotations
import tomllib
from dataclasses import dataclass


@dataclass
class Config:
    llm_endpoint: str
    llm_model: str
    browser_profile_dir: str
    db_path: str


def load_config(path: str = "config.toml") -> Config:
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    return Config(
        llm_endpoint=raw["llm"]["endpoint"],
        llm_model=raw["llm"]["model"],
        browser_profile_dir=raw["browser"]["profile_dir"],
        db_path=raw["database"]["path"],
    )


@dataclass
class ConfigStatus:
    exists: bool
    has_llm_endpoint: bool
    has_llm_model: bool

    @property
    def ok(self) -> bool:
        return self.exists and self.has_llm_endpoint and self.has_llm_model


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
    return ConfigStatus(
        exists=True,
        has_llm_endpoint=bool(llm.get("endpoint")),
        has_llm_model=bool(llm.get("model")),
    )
