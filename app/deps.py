from __future__ import annotations
import sqlite3
from typing import Generator
import openai
from app.config import load_config, Config
from app.db.schema import init_db


def get_config() -> Config:
    return load_config()


def get_db() -> Generator[sqlite3.Connection, None, None]:
    config = load_config()
    conn = sqlite3.connect(config.db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    try:
        yield conn
    finally:
        conn.close()


def get_ai_client() -> openai.OpenAI:
    config = load_config()
    return openai.OpenAI(base_url=config.llm_endpoint, api_key="not-needed")


def get_model() -> str:
    return load_config().llm_model
