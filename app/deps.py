from __future__ import annotations
import sqlite3
from typing import Generator
import openai
from app.config import load_config, check_config_status, Config
from app.db.schema import init_db


def get_config() -> Config:
    return load_config()


def _connect(config: Config) -> sqlite3.Connection:
    conn = sqlite3.connect(config.db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _open_db(config: Config) -> sqlite3.Connection:
    """Connect and bring the schema up to date. Migrations only need to run
    once per process (at app startup, and once more for the worker thread's
    own connection) — not on every request, which is what get_db() below
    does via the lighter _connect(). Re-running init_db() per request used to
    scan+rewrite the whole jobs table on every single request via
    _migrate_jobs_canonicalize_urls, contending with the worker thread's
    writes often enough to raise 'database is locked'."""
    conn = _connect(config)
    init_db(conn)
    return conn


def get_db() -> Generator[sqlite3.Connection, None, None]:
    conn = _connect(load_config())
    try:
        yield conn
    finally:
        conn.close()


def get_db_optional() -> Generator[sqlite3.Connection | None, None, None]:
    """Like get_db(), but yields None instead of raising when config.toml is
    missing or has no inference provider configured. Used by the home route,
    which must render a setup banner instead of a raw 500 when the app
    hasn't been configured yet."""
    if not check_config_status().ok:
        yield None
        return
    conn = _connect(load_config())
    try:
        yield conn
    finally:
        conn.close()


def get_ai_client() -> openai.OpenAI:
    config = load_config()
    return openai.OpenAI(base_url=config.llm_endpoint, api_key=config.llm_api_key)


def get_model() -> str:
    return load_config().llm_model
