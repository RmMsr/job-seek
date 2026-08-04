import pytest
from unittest.mock import MagicMock
from app.ai.refine import propose_criteria, CriterionProposal, match_removal_target


def _mock_client(response_text: str) -> MagicMock:
    client = MagicMock()
    choice = MagicMock()
    choice.message.content = response_text
    client.chat.completions.create.return_value = MagicMock(choices=[choice])
    return client


_SCENARIO = {"name": "Remote ML", "description": "Looking for remote ML roles"}
_EXISTING = [{"text": "Must be remote", "weight": "must"}]
_NOTES = [
    {"status": "rejected", "feedback_note": "required on-site work"},
    {"status": "rejected", "feedback_note": "too junior, needs senior level"},
]


def test_propose_criteria_returns_proposals():
    response = '[{"text": "Must be senior level", "weight": "must", "action": "add"}]'
    client = _mock_client(response)
    proposals = propose_criteria(client, "llama3.2", _SCENARIO, _EXISTING, _NOTES)
    assert len(proposals) == 1
    assert isinstance(proposals[0], CriterionProposal)
    assert proposals[0].text == "Must be senior level"
    assert proposals[0].weight == "must"
    assert proposals[0].action == "add"


def test_propose_criteria_invalid_json_returns_empty():
    client = _mock_client("not json")
    proposals = propose_criteria(client, "llama3.2", _SCENARIO, _EXISTING, _NOTES)
    assert proposals == []


def test_propose_criteria_filters_invalid_weight():
    response = '[{"text": "Good criterion", "weight": "invalid_weight", "action": "add"}]'
    client = _mock_client(response)
    proposals = propose_criteria(client, "llama3.2", _SCENARIO, _EXISTING, _NOTES)
    assert proposals == []


def test_propose_criteria_strips_markdown_code_fence():
    response = '```json\n[{"text": "Must be senior level", "weight": "must", "action": "add"}]\n```'
    client = _mock_client(response)
    proposals = propose_criteria(client, "llama3.2", _SCENARIO, _EXISTING, _NOTES)
    assert len(proposals) == 1
    assert proposals[0].text == "Must be senior level"


def test_propose_criteria_disables_model_thinking():
    response = '[{"text": "Must be senior level", "weight": "must", "action": "add"}]'
    client = _mock_client(response)
    propose_criteria(client, "llama3.2", _SCENARIO, _EXISTING, _NOTES)
    call_args = client.chat.completions.create.call_args
    assert call_args.kwargs["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}


def test_propose_criteria_tags_notes_with_outcome():
    # Same note text means opposite things depending on accept/reject, so
    # the prompt must carry that distinction rather than a flat note list.
    response = "[]"
    client = _mock_client(response)
    notes = [
        {"status": "rejected", "feedback_note": "too junior"},
        {"status": "accepted", "feedback_note": "great senior role"},
    ]
    propose_criteria(client, "llama3.2", _SCENARIO, _EXISTING, notes)
    call_args = client.chat.completions.create.call_args
    user_content = call_args.kwargs["messages"][1]["content"]
    assert "[REJECTED] too junior" in user_content
    assert "[ACCEPTED] great senior role" in user_content


def test_propose_criteria_uses_zero_temperature():
    # Repeated refine calls against the same criteria/feedback should give
    # consistent proposals rather than flip-flopping between calls due to
    # sampling noise.
    response = '[{"text": "Must be senior level", "weight": "must", "action": "add"}]'
    client = _mock_client(response)
    propose_criteria(client, "llama3.2", _SCENARIO, _EXISTING, _NOTES)
    call_args = client.chat.completions.create.call_args
    assert call_args.kwargs["temperature"] == 0


_EXISTING_WITH_IDS = [
    {"id": 1, "text": "Must be remote"},
    {"id": 2, "text": "Must have Python experience"},
]


def test_match_removal_target_exact_match():
    assert match_removal_target("Must be remote", _EXISTING_WITH_IDS) == 1


def test_match_removal_target_ignores_case_and_surrounding_whitespace():
    assert match_removal_target("  must be remote  ", _EXISTING_WITH_IDS) == 1


def test_match_removal_target_no_match_returns_none():
    assert match_removal_target("Must have a PhD", _EXISTING_WITH_IDS) is None
