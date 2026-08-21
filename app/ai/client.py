from __future__ import annotations
import openai
from app.config import Config


def make_client(config: Config) -> openai.OpenAI:
    return openai.OpenAI(base_url=config.llm_endpoint, api_key=config.llm_api_key)
