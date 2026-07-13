import pytest
from unittest.mock import MagicMock, patch
from app.ai.classify import classify


def _mock_client(response_text: str) -> MagicMock:
    client = MagicMock()
    choice = MagicMock()
    choice.message.content = response_text
    client.chat.completions.create.return_value = MagicMock(choices=[choice])
    return client


def test_classify_job_posting():
    client = _mock_client('{"type": "job_posting", "reason": "Full job description present"}')
    content_type, reason = classify(client, "llama3.2", "Senior ML Engineer at Acme...")
    assert content_type == "job_posting"
    assert "Full job description" in reason


def test_classify_lead():
    client = _mock_client('{"type": "lead", "reason": "References a job but no full description"}')
    content_type, reason = classify(client, "llama3.2", "Check out openings at Acme", is_slack=True)
    assert content_type == "lead"


def test_classify_irrelevant():
    client = _mock_client('{"type": "irrelevant", "reason": "Not job related"}')
    content_type, _ = classify(client, "llama3.2", "Community meetup next week")
    assert content_type == "irrelevant"


def test_classify_invalid_json_returns_error():
    client = _mock_client("not valid json at all")
    content_type, _ = classify(client, "llama3.2", "some content")
    assert content_type == "error"


def test_classify_sends_slack_hint():
    client = _mock_client('{"type": "lead", "reason": "Slack post"}')
    classify(client, "llama3.2", "content", is_slack=True)
    call_args = client.chat.completions.create.call_args
    prompt = str(call_args)
    assert "slack" in prompt.lower() or "lead" in prompt.lower()
