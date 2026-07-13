import pytest
from unittest.mock import MagicMock
from app.ai.refine import propose_criteria, CriterionProposal


def _mock_client(response_text: str) -> MagicMock:
    client = MagicMock()
    choice = MagicMock()
    choice.message.content = response_text
    client.chat.completions.create.return_value = MagicMock(choices=[choice])
    return client


_SCENARIO = {"name": "Remote ML", "description": "Looking for remote ML roles"}
_EXISTING = [{"text": "Must be remote", "weight": "must"}]
_NOTES = ["Rejected because it required on-site work", "Too junior, needs senior level"]


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
