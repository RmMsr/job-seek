import pytest
from unittest.mock import MagicMock
from app.ai.tailor_cv import tailor_cv


def _mock_client(text: str) -> MagicMock:
    client = MagicMock()
    choice = MagicMock()
    choice.message.content = text
    client.chat.completions.create.return_value = MagicMock(choices=[choice])
    return client


def test_returns_raw_markdown():
    out = tailor_cv(_mock_client("# Jane Doe\n\n- Did the thing.\n"), "m", "# base", "instr", "job")
    assert out["markdown"].startswith("# Jane Doe")


def test_peels_one_outer_code_fence():
    out = tailor_cv(_mock_client("```markdown\n# CV\n\n- x\n```"), "m", "# base", "instr", "job")
    assert out["markdown"] == "# CV\n\n- x"


def test_strips_leading_think_block():
    out = tailor_cv(_mock_client("<think>plan plan</think>\n# CV\n\n- x\n"), "m", "# base", "instr", "job")
    assert out["markdown"].startswith("# CV")
    assert "think" not in out["markdown"]


def test_empty_content_raises():
    with pytest.raises(RuntimeError):
        tailor_cv(_mock_client("   "), "m", "# base", "instr", "job")


def test_defaults_temperature_thinking_off_and_token_cap():
    # Thinking is off by default: on this model it runs away to 10k-20k+ reasoning
    # tokens (15-25 min) for no quality gain over the ~65s non-thinking call.
    # max_tokens is a backstop against a runaway, set well above any real CV.
    client = _mock_client("# CV\n- x")
    tailor_cv(client, "m", "# base", "instr", "job")
    kw = client.chat.completions.create.call_args.kwargs
    assert kw["temperature"] == 0.5
    assert kw["max_tokens"] == 4096
    assert kw["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}


def test_thinking_can_be_re_enabled():
    client = _mock_client("# CV\n- x")
    tailor_cv(client, "m", "# base", "instr", "job", think=True)
    kw = client.chat.completions.create.call_args.kwargs
    assert kw["extra_body"] == {"chat_template_kwargs": {"enable_thinking": True}}


def test_system_prompt_has_mandate_and_floor():
    # guardrails is now caller-supplied (the merged, user-editable settings field)
    # rather than a hardcoded module constant — pass it explicitly to check it
    # lands in the system prompt's hard-floor section.
    client = _mock_client("# CV\n- x")
    tailor_cv(client, "m", "# base", "the composed instruction", "job",
              guardrails="Do not add a degree that is not in the base CV.")
    msgs = client.chat.completions.create.call_args.kwargs["messages"]
    system = msgs[0]["content"].lower()
    assert "recruiter" in system or "ten seconds" in system  # mandate
    assert "hard floor" in system and "do not add a degree" in system  # floor present
    # permitted edits are the outer boundary; the plan is subordinate to it
    assert "permitted" in system and "boundary" in system
    assert "the permitted edits win" in system
    # the composed instruction is in the user message, not the system prompt
    assert "the composed instruction" in msgs[1]["content"]


def test_stable_blocks_precede_the_instruction():
    # The source CV and job posting are stable across successive tailor_cv
    # calls for the same job; the instruction is not. Ordering the stable
    # blocks first lets the LLM server reuse the KV-cache prefix.
    client = _mock_client("# CV\n- x")
    tailor_cv(client, "m", "# source cv", "the instruction", "the job posting")
    user = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    assert user.index("# CV to tailor") < user.index("# Instruction")
    assert user.index("# Job posting") < user.index("# Instruction")


def test_transport_error_propagates():
    import pytest
    client = MagicMock()
    client.chat.completions.create.side_effect = RuntimeError("boom")
    with pytest.raises(RuntimeError, match="boom"):
        tailor_cv(client, "m", "# base", "instr", "job")


def test_plan_tailoring_propagates_connection_error():
    import httpx, openai, pytest
    from app.ai.tailor_cv import plan_tailoring
    client = MagicMock()
    client.chat.completions.create.side_effect = openai.APIConnectionError(
        request=httpx.Request("POST", "http://x"))
    with pytest.raises(openai.APIConnectionError):
        plan_tailoring(client, "m", "base", "job", "")
