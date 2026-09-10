import httpx
import openai
import pytest
from unittest.mock import MagicMock
from app.ai._client import complete


def _client(text=None, exc=None):
    c = MagicMock()
    if exc is not None:
        c.chat.completions.create.side_effect = exc
    else:
        choice = MagicMock()
        choice.message.content = text
        c.chat.completions.create.return_value = MagicMock(choices=[choice])
    return c


def test_complete_returns_content():
    c = _client(text='{"ok": true}')
    assert complete(c, "m", [{"role": "user", "content": "hi"}]) == '{"ok": true}'


def test_complete_propagates_connection_error():
    err = openai.APIConnectionError(request=httpx.Request("POST", "http://x"))
    c = _client(exc=err)
    with pytest.raises(openai.APIConnectionError):
        complete(c, "m", [{"role": "user", "content": "hi"}])


def test_complete_none_content_becomes_empty_string():
    c = _client(text=None)
    assert complete(c, "m", []) == ""
