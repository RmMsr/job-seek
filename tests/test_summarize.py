from unittest.mock import MagicMock
from app.ai.summarize import summarize


def _mock_client(response_text: str) -> MagicMock:
    client = MagicMock()
    choice = MagicMock()
    choice.message.content = response_text
    client.chat.completions.create.return_value = MagicMock(choices=[choice])
    return client


def test_summarize_returns_title_headline_and_summary():
    response = (
        '{"title": "ML Engineer - Remote @ Acme", '
        '"headline": "Fully remote with a $180k+ ceiling", '
        '"summary": "**Role:** ML Engineer"}'
    )
    client = _mock_client(response)
    title, headline, summary = summarize(client, "llama3.2", "long job description text")
    assert title == "ML Engineer - Remote @ Acme"
    assert headline == "Fully remote with a $180k+ ceiling"
    assert summary == "**Role:** ML Engineer"


def test_summarize_calls_llm_once():
    client = _mock_client('{"title": "T", "headline": "H", "summary": "S"}')
    summarize(client, "llama3.2", "content")
    assert client.chat.completions.create.call_count == 1


def test_summarize_strips_markdown_code_fence():
    response = '```json\n{"title": "T", "headline": "H", "summary": "S"}\n```'
    client = _mock_client(response)
    result = summarize(client, "llama3.2", "content")
    assert result == ("T", "H", "S")


def test_summarize_returns_empty_strings_on_malformed_json():
    client = _mock_client("not json")
    result = summarize(client, "llama3.2", "content")
    assert result == ("", "", "")


def test_summarize_returns_empty_strings_on_api_error():
    client = MagicMock()
    client.chat.completions.create.side_effect = Exception("boom")
    result = summarize(client, "llama3.2", "content")
    assert result == ("", "", "")
