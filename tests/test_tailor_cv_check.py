from unittest.mock import MagicMock
from app.ai.tailor_cv import check_guardrails


def _mock_client(text: str) -> MagicMock:
    client = MagicMock()
    choice = MagicMock()
    choice.message.content = text
    client.chat.completions.create.return_value = MagicMock(choices=[choice])
    return client


def test_findings_parsed_and_verdicts_validated():
    client = _mock_client(
        '{"findings": ['
        '{"rule": "no invented dates", "verdict": "ok", "explanation": "fine"},'
        '{"rule": "no invented tools", "verdict": "violated", "explanation": "claims Terraform"},'
        '{"rule": "x", "verdict": "banana", "explanation": "y"}]}'
    )
    out = check_guardrails(client, "m", "", "# base", "# tailored")
    verdicts = [f["verdict"] for f in out["findings"]]
    assert "ok" in verdicts and "violated" in verdicts
    assert "banana" not in verdicts  # invalid verdict dropped


def test_guardrails_come_only_from_the_passed_text():
    # check_guardrails no longer hardcodes a floor — the caller (cv.py) always
    # passes the full merged guardrails text from settings.
    client = _mock_client('{"findings": []}')
    check_guardrails(client, "m", "", "# base", "# tailored")
    user = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    assert "# Rules\n\n\n# Base CV" in user or "# Rules\n\n# Base CV" in user


def test_guardrails_numbered_cleanly_without_duplication():
    client = _mock_client('{"findings": []}')
    check_guardrails(client, "m", "Do not add a degree.\nKeep the PhD line verbatim.", "# base", "# tailored")
    user = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    assert user.count("Do not add a degree.") == 1
    assert "1. Do not add a degree." in user
    assert "2. Keep the PhD line verbatim." in user


def test_base_guardrails_appended():
    client = _mock_client('{"findings": []}')
    check_guardrails(client, "m", "Keep the PhD line verbatim.", "# base", "# tailored")
    user = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    assert "Keep the PhD line verbatim." in user


def test_invalid_json_returns_empty():
    assert check_guardrails(_mock_client("nope"), "m", "", "b", "t") == {"findings": []}


def test_temperature_zero_thinking_off():
    client = _mock_client('{"findings": []}')
    check_guardrails(client, "m", "", "b", "t")
    kw = client.chat.completions.create.call_args.kwargs
    assert kw["temperature"] == 0
    assert kw["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}
