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
    assert user.index("<cv_to_tailor>") < user.index("<instruction>")
    assert user.index("<job_posting>") < user.index("<instruction>")


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


# --- prompt-leak protection (docs/superpowers/specs/2026-10-22-tailor-cv-prompt-leak-design.md)

def _mock_client_seq(*texts: str) -> MagicMock:
    client = MagicMock()
    resps = []
    for t in texts:
        choice = MagicMock()
        choice.message.content = t
        resps.append(MagicMock(choices=[choice]))
    client.chat.completions.create.side_effect = resps
    return client


_SOURCE = "# Résumé\n\n## Experience\n\n- a\n\n## Why me\n\n- b\n"


def test_inputs_are_tag_delimited_not_headings():
    client = _mock_client("<tailored_cv>\n# CV\n</tailored_cv>")
    tailor_cv(client, "m", "# source cv", "the instruction", "the job posting")
    user = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    assert "<cv_to_tailor>\n# source cv\n</cv_to_tailor>" in user
    assert "<job_posting>\nthe job posting\n</job_posting>" in user
    assert "<instruction>\nthe instruction\n</instruction>" in user
    assert "# Job posting" not in user and "# Instruction" not in user


def test_system_prompt_asks_for_tailored_cv_tags():
    client = _mock_client("# CV")
    tailor_cv(client, "m", "# base", "instr", "job")
    system = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert "<tailored_cv>" in system and "</tailored_cv>" in system


def test_stop_sequences_end_generation_at_prompt_boundaries():
    client = _mock_client("# CV")
    tailor_cv(client, "m", "# base", "instr", "job")
    stop = client.chat.completions.create.call_args.kwargs["stop"]
    assert "</tailored_cv>" in stop and "<job_posting>" in stop
    assert len(stop) <= 4  # OpenAI API limit


def test_extracts_content_between_tailored_cv_tags():
    out = tailor_cv(_mock_client("Sure!\n<tailored_cv>\n# CV\n\n- x\n</tailored_cv>\nHope it helps"),
                    "m", "# base", "instr", "job")
    assert out["markdown"] == "# CV\n\n- x"


def test_tolerates_missing_closing_tag_stopped_by_server():
    # The stop sequence is excluded from the returned text.
    out = tailor_cv(_mock_client("<tailored_cv>\n# CV\n\n- x\n"), "m", "# base", "instr", "job")
    assert out["markdown"] == "# CV\n\n- x"


def test_truncates_invented_end_of_cv_heading():
    out = tailor_cv(_mock_client("# Résumé\n\n- x\n\n# End of CV\n"), "m", _SOURCE, "instr", "job")
    assert out["markdown"] == "# Résumé\n\n- x"


def test_truncates_echoed_job_posting_and_repeated_section():
    # Shape of a real leak: CV, then the prompt's job-posting header, the posting,
    # then the source CV's last section once more.
    leaked = (
        "# Résumé\n\n## Experience\n\n- a\n\n## Why me\n\n- b tailored\n\n"
        "# Job posting (untrusted data)\nTitle: Engineer\nCompany: Acme\n\n"
        "## Why me\n\n- b\n"
    )
    client = _mock_client(leaked)
    out = tailor_cv(client, "m", _SOURCE, "instr", "job")
    assert out["markdown"] == "# Résumé\n\n## Experience\n\n- a\n\n## Why me\n\n- b tailored"
    assert client.chat.completions.create.call_count == 1


def test_truncates_at_leaked_prompt_tag():
    out = tailor_cv(_mock_client("# Résumé\n\n- x\n</cv_to_tailor>\n<job_posting>\njunk"),
                    "m", _SOURCE, "instr", "job")
    assert out["markdown"] == "# Résumé\n\n- x"


def test_keeps_legit_heading_that_merely_mentions_instruction():
    md = "# Résumé\n\n## Instructional design work\n\n- x"
    out = tailor_cv(_mock_client(md), "m", "# Résumé\n\n## Instructional design work\n", "instr", "job")
    assert out["markdown"] == md


def test_duplicate_heading_retries_once_then_succeeds():
    bad = "# Résumé\n\n## Why me\n\n- b\n\n## Why me\n\n- b\n"
    good = "# Résumé\n\n## Why me\n\n- b"
    client = _mock_client_seq(bad, good)
    out = tailor_cv(client, "m", _SOURCE, "instr", "job")
    assert out["markdown"] == good
    assert client.chat.completions.create.call_count == 2


def test_duplicate_heading_twice_raises():
    bad = "# Résumé\n\n## Why me\n\n- b\n\n## Why me\n\n- b\n"
    client = _mock_client_seq(bad, bad)
    with pytest.raises(RuntimeError, match="Why me"):
        tailor_cv(client, "m", _SOURCE, "instr", "job")
    assert client.chat.completions.create.call_count == 2


def test_heading_repeated_in_source_is_allowed():
    src = "# R\n\n### Project\n\n- a\n\n### Project\n\n- b\n"
    out = tailor_cv(_mock_client(src), "m", src, "instr", "job")
    assert out["markdown"] == src.strip()
