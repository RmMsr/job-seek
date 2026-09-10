from __future__ import annotations
import openai


def complete(client: openai.OpenAI, model: str, messages: list[dict], **kwargs) -> str:
    """Run a chat completion and return the assistant message text.

    Deliberately does NOT catch openai errors — a connection / timeout / auth /
    HTTP-status failure means the inference endpoint is unusable and the caller's
    task must fail loudly rather than persist a degraded 'result'. Callers keep
    their own try/except only around parsing the returned string.
    """
    resp = client.chat.completions.create(model=model, messages=messages, **kwargs)
    return resp.choices[0].message.content or ""
