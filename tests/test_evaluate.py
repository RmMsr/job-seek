import pytest
from unittest.mock import MagicMock
from app.ai.evaluate import evaluate


def _mock_client(response_text: str) -> MagicMock:
    client = MagicMock()
    choice = MagicMock()
    choice.message.content = response_text
    client.chat.completions.create.return_value = MagicMock(choices=[choice])
    return client


_SCENARIO = {"name": "Remote ML", "description": "Looking for remote ML roles"}
_CRITERIA = [
    {"text": "Must be remote", "weight": "must"},
    {"text": "Prefer Python", "weight": "prefer"},
]


def test_evaluate_returns_score_and_reasoning():
    client = _mock_client('{"score": 0.85, "reasoning": "Matches remote and Python criteria"}')
    score, reasoning = evaluate(client, "llama3.2", "I am a senior ML engineer", _SCENARIO, _CRITERIA, "Good role summary")
    assert score == pytest.approx(0.85)
    assert "remote" in reasoning.lower() or "Python" in reasoning


def test_evaluate_clamps_score():
    client = _mock_client('{"score": 1.5, "reasoning": "Perfect"}')
    score, _ = evaluate(client, "llama3.2", "profile", _SCENARIO, _CRITERIA, "summary")
    assert 0.0 <= score <= 1.0


def test_evaluate_invalid_json_returns_zero():
    client = _mock_client("not json")
    score, reasoning = evaluate(client, "llama3.2", "profile", _SCENARIO, _CRITERIA, "summary")
    assert score == 0.0
    assert "error" in reasoning.lower()


def test_evaluate_strips_markdown_code_fence():
    client = _mock_client('```json\n{"score": 0.6, "reasoning": "Decent match"}\n```')
    score, reasoning = evaluate(client, "llama3.2", "profile", _SCENARIO, _CRITERIA, "summary")
    assert score == pytest.approx(0.6)
    assert reasoning == "Decent match"
