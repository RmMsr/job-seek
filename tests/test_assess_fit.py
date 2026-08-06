import pytest
from unittest.mock import MagicMock
from app.ai.assess_fit import assess_fit


def _mock_client(response_text: str) -> MagicMock:
    client = MagicMock()
    choice = MagicMock()
    choice.message.content = response_text
    client.chat.completions.create.return_value = MagicMock(choices=[choice])
    return client


_PROFILE = "Senior ML engineer, 8 years, interested in applied research roles at mission-driven startups."


def test_assess_fit_returns_both_scores_and_reasonings():
    client = _mock_client(
        '{"interest": 0.85, "interest_reasoning": "Strong domain match", '
        '"attainability": 0.6, "attainability_reasoning": "Slightly more senior than typical hires"}'
    )
    result = assess_fit(client, "llama3.2", _PROFILE, "Good role summary")
    assert result["interest"] == pytest.approx(0.85)
    assert result["interest_reasoning"] == "Strong domain match"
    assert result["attainability"] == pytest.approx(0.6)
    assert result["attainability_reasoning"] == "Slightly more senior than typical hires"


def test_assess_fit_clamps_scores():
    client = _mock_client(
        '{"interest": 1.5, "interest_reasoning": "x", "attainability": -0.2, "attainability_reasoning": "y"}'
    )
    result = assess_fit(client, "llama3.2", _PROFILE, "summary")
    assert 0.0 <= result["interest"] <= 1.0
    assert 0.0 <= result["attainability"] <= 1.0


def test_assess_fit_invalid_json_returns_zeros_with_error():
    client = _mock_client("not json")
    result = assess_fit(client, "llama3.2", _PROFILE, "summary")
    assert result["interest"] == 0.0
    assert result["attainability"] == 0.0
    assert "error" in result["interest_reasoning"].lower()
    assert "error" in result["attainability_reasoning"].lower()


def test_assess_fit_strips_markdown_code_fence():
    client = _mock_client(
        '```json\n{"interest": 0.5, "interest_reasoning": "a", "attainability": 0.5, "attainability_reasoning": "b"}\n```'
    )
    result = assess_fit(client, "llama3.2", _PROFILE, "summary")
    assert result["interest"] == pytest.approx(0.5)
    assert result["interest_reasoning"] == "a"


def test_assess_fit_disables_model_thinking():
    client = _mock_client(
        '{"interest": 0.5, "interest_reasoning": "a", "attainability": 0.5, "attainability_reasoning": "b"}'
    )
    assess_fit(client, "llama3.2", _PROFILE, "summary")
    call_args = client.chat.completions.create.call_args
    assert call_args.kwargs["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}
