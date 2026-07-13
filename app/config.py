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
