from unittest.mock import MagicMock
from app.ai.revisit_check import revisit_check


def _mock_client(response_text: str) -> MagicMock:
    client = MagicMock()
    choice = MagicMock()
    choice.message.content = response_text
    client.chat.completions.create.return_value = MagicMock(choices=[choice])
    return client


def test_unchanged():
    client = _mock_client('{"state": "unchanged", "reason": "same role and requirements"}')
    state, reason = revisit_check(client, "llama3.2", "page text", "known summary")
    assert state == "unchanged"
    assert "same role" in reason


def test_gone():
    client = _mock_client('{"state": "gone", "reason": "redirected to the jobs list"}')
    state, _ = revisit_check(client, "llama3.2", "page text", "known summary")
    assert state == "gone"


def test_closed():
    client = _mock_client('{"state": "closed", "reason": "applications are closed"}')
    state, _ = revisit_check(client, "llama3.2", "page text", "known summary")
    assert state == "closed"


def test_changed():
    client = _mock_client('{"state": "changed", "reason": "seniority and comp band both moved"}')
    state, _ = revisit_check(client, "llama3.2", "page text", "known summary")
    assert state == "changed"


def test_unknown_label_falls_back_to_unchanged():
    client = _mock_client('{"state": "maybe", "reason": "unsure"}')
    state, _ = revisit_check(client, "llama3.2", "page text", "known summary")
    assert state == "unchanged"


def test_unparseable_response_falls_back_to_unchanged():
    client = _mock_client("not json")
    state, _ = revisit_check(client, "llama3.2", "page text", "known summary")
    assert state == "unchanged"


def test_api_error_propagates():
    import pytest
    client = MagicMock()
    client.chat.completions.create.side_effect = RuntimeError("boom")
    with pytest.raises(RuntimeError, match="boom"):
        revisit_check(client, "llama3.2", "page text", "known summary")


def test_strips_markdown_fence():
    client = _mock_client('```json\n{"state": "gone", "reason": "404 page"}\n```')
    state, _ = revisit_check(client, "llama3.2", "page text", "known summary")
    assert state == "gone"


def test_caps_tokens_and_disables_thinking():
    client = _mock_client('{"state": "unchanged", "reason": "x"}')
    revisit_check(client, "llama3.2", "page", "summary")
    kwargs = client.chat.completions.create.call_args.kwargs
    assert kwargs["max_tokens"] == 120
    assert kwargs["temperature"] == 0
    assert kwargs["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}
