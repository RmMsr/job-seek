from unittest.mock import MagicMock
from app.ai.summarize import summarize


def _mock_client(response_text: str) -> MagicMock:
    client = MagicMock()
    choice = MagicMock()
    choice.message.content = response_text
    client.chat.completions.create.return_value = MagicMock(choices=[choice])
    return client


def test_summarize_returns_llm_output():
    expected = "**Role:** ML Engineer\n**Company:** Acme\n**Remote:** Yes"
    client = _mock_client(expected)
    result = summarize(client, "llama3.2", "long job description text")
    assert result == expected


def test_summarize_calls_llm_once():
    client = _mock_client("summary")
    summarize(client, "llama3.2", "content")
    assert client.chat.completions.create.call_count == 1
