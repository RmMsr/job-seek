from unittest.mock import MagicMock
from app.ai.tailor_cv import plan_tailoring, DirectiveProposal, _PLAN_SYSTEM


def _mock_client(text: str) -> MagicMock:
    client = MagicMock()
    choice = MagicMock()
    choice.message.content = text
    client.chat.completions.create.return_value = MagicMock(choices=[choice])
    return client


def test_plan_stable_blocks_precede_the_variable_ones():
    # Base CV and job posting are stable across plan re-runs for a job; the
    # tuning directives and handled-suggestions list change every time. Ordering
    # the stable blocks first lets the LLM server reuse the KV-cache prefix.
    client = _mock_client('{"directives": []}')
    plan_tailoring(client, "m", "# CV", "the job posting",
                   tuning_directives="- foreground the platform work",
                   handled=[{"action": "add", "line": "name Rust", "rationale": "x"}])
    user = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    assert user.index("# Job posting") < user.index("# Current tuning directives")
    assert user.index("# Job posting") < user.index("already handled")


def test_plan_returns_valid_proposals():
    client = _mock_client(
        '{"directives": ['
        '{"action": "add", "section": "Skills match", "rationale": "job leads with k8s", '
        '"line": "foreground the platform work, keep the mentoring line"},'
        '{"action": "add", "section": "Wording and typography", "rationale": "irrelevant", '
        '"line": "compress the agency roles to one line"}]}'
    )
    out = plan_tailoring(client, "m", "# CV", "Senior Platform Engineer ...")
    assert len(out["directives"]) == 2
    assert isinstance(out["directives"][0], DirectiveProposal)
    assert out["directives"][0].section == "Skills match"
    assert out["directives"][0].action == "add"


def test_plan_accepts_replace_with_target():
    client = _mock_client(
        '{"directives": [{"action": "replace", "section": "Role relevance", "rationale": "too vague", '
        '"line": "tighten to name the platform explicitly", "target": "mention platform work"}]}'
    )
    out = plan_tailoring(client, "m", "# CV", "job",
                          tuning_directives="- mention platform work")
    assert out["directives"][0].action == "replace"
    assert out["directives"][0].target == "mention platform work"
    assert out["directives"][0].line == "tighten to name the platform explicitly"


def test_plan_accepts_remove_without_line():
    client = _mock_client(
        '{"directives": [{"action": "remove", "section": "Role relevance", "rationale": "stale", '
        '"target": "compress the agency roles"}]}'
    )
    out = plan_tailoring(client, "m", "# CV", "job",
                          tuning_directives="- compress the agency roles")
    assert out["directives"][0].action == "remove"
    assert out["directives"][0].line is None
    assert out["directives"][0].target == "compress the agency roles"


def test_plan_drops_invalid_action():
    client = _mock_client(
        '{"directives": [{"action": "modify", "section": "Skills match", "rationale": "x", "line": "y"}]}'
    )
    out = plan_tailoring(client, "m", "# CV", "job")
    assert out["directives"] == []


def test_plan_drops_add_without_line():
    client = _mock_client(
        '{"directives": [{"action": "add", "section": "Skills match", "rationale": "x", "line": ""}]}'
    )
    out = plan_tailoring(client, "m", "# CV", "job")
    assert out["directives"] == []


def test_plan_drops_replace_without_target():
    client = _mock_client(
        '{"directives": [{"action": "replace", "section": "Skills match", "rationale": "x", "line": "y"}]}'
    )
    out = plan_tailoring(client, "m", "# CV", "job")
    assert out["directives"] == []


def test_plan_drops_remove_without_target():
    client = _mock_client(
        '{"directives": [{"action": "remove", "section": "Skills match", "rationale": "x"}]}'
    )
    out = plan_tailoring(client, "m", "# CV", "job")
    assert out["directives"] == []


def test_plan_requires_section():
    client = _mock_client('{"directives": [{"action": "add", "rationale": "x", "line": "y"}]}')
    out = plan_tailoring(client, "m", "# CV", "job")
    assert out["directives"] == []


def test_plan_keeps_proposal_with_section():
    client = _mock_client('{"directives": [{"action": "add", "section": "Skills match", '
                          '"rationale": "job leads with k8s", "line": "foreground the platform work"}]}')
    out = plan_tailoring(client, "m", "# CV", "job")
    assert out["directives"][0].section == "Skills match"


def test_plan_system_prompt_says_walk_the_headings():
    client = _mock_client('{"directives": []}')
    plan_tailoring(client, "m", "# CV", "job")
    system = client.chat.completions.create.call_args.kwargs["messages"][0]["content"].lower()
    assert "heading" in system and "section" in system


def test_plan_invalid_json_returns_empty():
    out = plan_tailoring(_mock_client("not json"), "m", "# CV", "job")
    assert out == {"directives": []}


def test_plan_strips_code_fence():
    client = _mock_client(
        '```json\n{"directives": [{"action":"add","section":"Skills match","rationale":"r","line":"l"}]}\n```'
    )
    out = plan_tailoring(client, "m", "# CV", "job")
    assert out["directives"][0].line == "l"


def test_plan_uses_temperature_zero_thinking_off():
    client = _mock_client('{"directives": []}')
    plan_tailoring(client, "m", "# CV", "job")
    kw = client.chat.completions.create.call_args.kwargs
    assert kw["temperature"] == 0
    assert kw["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}


def test_plan_prompt_lists_handled_suggestions_and_system_forbids_them():
    client = _mock_client('{"directives": []}')
    plan_tailoring(client, "m", "# CV", "job",
                   handled=[{"action": "add", "line": "name C++ prominently",
                             "rationale": "job mentions C++"}])
    msgs = client.chat.completions.create.call_args.kwargs["messages"]
    assert "name C++ prominently" in msgs[1]["content"]
    assert "already handled" in msgs[1]["content"].lower()
    assert "handled" in msgs[0]["content"].lower()


def test_plan_omits_handled_block_when_none():
    client = _mock_client('{"directives": []}')
    plan_tailoring(client, "m", "# CV", "job")
    assert "already handled" not in client.chat.completions.create.call_args.kwargs["messages"][1]["content"].lower()


def test_plan_system_proposes_the_full_opportunity_unbound_by_scope():
    system = _PLAN_SYSTEM.lower()
    assert "permitted edit types" not in system
    assert "full opportunity" in system or "widest" in system
    # it must still forbid claims the base CV can't support
    assert "honestly support" in system or "base cv can honestly" in system


def test_plan_call_has_no_permitted_edit_types_block():
    client = _mock_client('{"directives": []}')
    plan_tailoring(client, "m", "# CV", "job", tuning_directives="- x")
    user = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    assert "Permitted edit types" not in user


def test_plan_frames_job_as_untrusted():
    client = _mock_client('{"directives": []}')
    plan_tailoring(client, "m", "# CV", "job text")
    system = client.chat.completions.create.call_args.kwargs["messages"][0]["content"].lower()
    assert "untrusted" in system or "not.*instruction" in system or "data" in system


def test_plan_includes_job_notes_as_trusted_block():
    client = _mock_client('{"directives": []}')
    plan_tailoring(client, "m", "# CV", "job text",
                   job_notes="Accepted — I want the hardware-boundary work, less GenAI.")
    user = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    assert "hardware-boundary work" in user
    lower = user.lower()
    assert "candidate" in lower or "your notes" in lower or "own notes" in lower


def test_plan_omits_notes_block_when_empty():
    client = _mock_client('{"directives": []}')
    plan_tailoring(client, "m", "# CV", "job text", job_notes="")
    user = client.chat.completions.create.call_args.kwargs["messages"][1]["content"].lower()
    assert "notes on this job" not in user


def test_plan_includes_current_tuning_directives_in_prompt():
    client = _mock_client('{"directives": []}')
    plan_tailoring(client, "m", "# CV", "job text",
                   tuning_directives="- foreground the platform work")
    user = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    assert "foreground the platform work" in user


def test_plan_shows_placeholder_when_no_existing_directives():
    client = _mock_client('{"directives": []}')
    plan_tailoring(client, "m", "# CV", "job text")
    user = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    assert "none yet" in user.lower()
