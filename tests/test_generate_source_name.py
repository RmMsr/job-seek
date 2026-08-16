from unittest.mock import MagicMock
from app.ai.generate_source_name import generate_source_name


def _mock_client(response_text: str) -> MagicMock:
    client = MagicMock()
    choice = MagicMock()
    choice.message.content = response_text
    client.chat.completions.create.return_value = MagicMock(choices=[choice])
    return client


def test_generate_source_name_returns_provider_filter_name():
    client = _mock_client('{"name": "glassdoor/frontend-oslo"}')
    name = generate_source_name(client, "llama3.2", "www.glassdoor.com", "Frontend Developer Jobs in Oslo | Glassdoor")
    assert name == "glassdoor/frontend-oslo"


def test_generate_source_name_strips_markdown_code_fence():
    client = _mock_client('```json\n{"name": "mlai/norway"}\n```')
    name = generate_source_name(client, "llama3.2", "mlai.work", "ML Jobs in Norway")
    assert name == "mlai/norway"


def test_generate_source_name_returns_none_for_blank_name():
    client = _mock_client('{"name": ""}')
    name = generate_source_name(client, "llama3.2", "example.com", "Untitled")
    assert name is None


def test_generate_source_name_returns_none_on_invalid_json():
    client = _mock_client("not valid json at all")
    name = generate_source_name(client, "llama3.2", "example.com", "Some Title")
    assert name is None


def test_generate_source_name_returns_none_on_client_error():
    client = MagicMock()
    client.chat.completions.create.side_effect = RuntimeError("boom")
    name = generate_source_name(client, "llama3.2", "example.com", "Some Title")
    assert name is None
