from unittest.mock import MagicMock
from app.ai.refine_profile import propose_profile_changes, ProfileProposal


def _mock_client(response_text: str) -> MagicMock:
    client = MagicMock()
    choice = MagicMock()
    choice.message.content = response_text
    client.chat.completions.create.return_value = MagicMock(choices=[choice])
    return client


_PROFILE = "# About me\n\n## Technologies\n\n- Python\n- Go\n"
_NOTES = [
    {"status": "rejected", "feedback_note": "No AI focus"},
    {"status": "accepted", "feedback_note": "Great AI engineering role"},
]


def test_propose_profile_changes_returns_proposals():
    response = '[{"section": "Technologies", "action": "add", "text": "AI engineering", "target": null}]'
    client = _mock_client(response)
    proposals = propose_profile_changes(client, "llama3.2", _PROFILE, _NOTES)
    assert len(proposals) == 1
    assert isinstance(proposals[0], ProfileProposal)
    assert proposals[0].section == "Technologies"
    assert proposals[0].action == "add"
    assert proposals[0].text == "AI engineering"
    assert proposals[0].target is None


def test_propose_profile_changes_invalid_json_returns_empty():
    client = _mock_client("not json")
    assert propose_profile_changes(client, "llama3.2", _PROFILE, _NOTES) == []


def test_propose_profile_changes_strips_markdown_code_fence():
    response = '```json\n[{"section": "Technologies", "action": "add", "text": "AI engineering", "target": null}]\n```'
    client = _mock_client(response)
    proposals = propose_profile_changes(client, "llama3.2", _PROFILE, _NOTES)
    assert len(proposals) == 1
    assert proposals[0].text == "AI engineering"


def test_propose_profile_changes_filters_invalid_action():
    response = '[{"section": "Technologies", "action": "modify", "text": "x", "target": null}]'
    client = _mock_client(response)
    assert propose_profile_changes(client, "llama3.2", _PROFILE, _NOTES) == []


def test_propose_profile_changes_filters_missing_section():
    response = '[{"action": "add", "text": "x", "target": null}]'
    client = _mock_client(response)
    assert propose_profile_changes(client, "llama3.2", _PROFILE, _NOTES) == []


def test_propose_profile_changes_filters_add_without_text():
    response = '[{"section": "Technologies", "action": "add", "text": null, "target": null}]'
    client = _mock_client(response)
    assert propose_profile_changes(client, "llama3.2", _PROFILE, _NOTES) == []


def test_propose_profile_changes_filters_remove_without_target():
    response = '[{"section": "Technologies", "action": "remove", "text": null, "target": null}]'
    client = _mock_client(response)
    assert propose_profile_changes(client, "llama3.2", _PROFILE, _NOTES) == []


def test_propose_profile_changes_filters_replace_without_target():
    response = '[{"section": "Technologies", "action": "replace", "text": "new", "target": null}]'
    client = _mock_client(response)
    assert propose_profile_changes(client, "llama3.2", _PROFILE, _NOTES) == []


def test_propose_profile_changes_accepts_remove():
    response = '[{"section": "Technologies", "action": "remove", "text": null, "target": "Go"}]'
    client = _mock_client(response)
    proposals = propose_profile_changes(client, "llama3.2", _PROFILE, _NOTES)
    assert len(proposals) == 1
    assert proposals[0].action == "remove"
    assert proposals[0].target == "Go"


def test_propose_profile_changes_accepts_replace():
    response = '[{"section": "Technologies", "action": "replace", "text": "Golang", "target": "Go"}]'
    client = _mock_client(response)
    proposals = propose_profile_changes(client, "llama3.2", _PROFILE, _NOTES)
    assert len(proposals) == 1
    assert proposals[0].action == "replace"
    assert proposals[0].text == "Golang"
    assert proposals[0].target == "Go"


def test_propose_profile_changes_tags_notes_with_status():
    client = _mock_client("[]")
    propose_profile_changes(client, "llama3.2", _PROFILE, _NOTES)
    call_args = client.chat.completions.create.call_args
    user_content = call_args.kwargs["messages"][1]["content"]
    assert "[REJECTED] No AI focus" in user_content
    assert "[ACCEPTED] Great AI engineering role" in user_content


def test_propose_profile_changes_carries_anchor_for_add():
    response = '[{"section": "Team Setup", "action": "add", "text": "Linear", "target": null, "anchor": "Tools"}]'
    client = _mock_client(response)
    proposals = propose_profile_changes(client, "llama3.2", _PROFILE, _NOTES)
    assert proposals[0].anchor == "Tools"


def test_propose_profile_changes_defaults_anchor_to_none_when_absent():
    response = '[{"section": "Technologies", "action": "add", "text": "Rust", "target": null}]'
    client = _mock_client(response)
    proposals = propose_profile_changes(client, "llama3.2", _PROFILE, _NOTES)
    assert proposals[0].anchor is None


def test_propose_profile_changes_treats_blank_anchor_as_none():
    response = '[{"section": "Technologies", "action": "add", "text": "Rust", "target": null, "anchor": "  "}]'
    client = _mock_client(response)
    proposals = propose_profile_changes(client, "llama3.2", _PROFILE, _NOTES)
    assert proposals[0].anchor is None


def test_propose_profile_changes_strips_leading_dash_from_text():
    response = '[{"section": "Technologies", "action": "add", "text": "- AI engineering", "target": null}]'
    client = _mock_client(response)
    proposals = propose_profile_changes(client, "llama3.2", _PROFILE, _NOTES)
    assert proposals[0].text == "AI engineering"


def test_propose_profile_changes_strips_leading_dash_from_target():
    response = '[{"section": "Technologies", "action": "remove", "text": null, "target": "- Go"}]'
    client = _mock_client(response)
    proposals = propose_profile_changes(client, "llama3.2", _PROFILE, _NOTES)
    assert proposals[0].target == "Go"


def test_propose_profile_changes_uses_zero_temperature_and_disables_thinking():
    client = _mock_client("[]")
    propose_profile_changes(client, "llama3.2", _PROFILE, _NOTES)
    call_args = client.chat.completions.create.call_args
    assert call_args.kwargs["temperature"] == 0
    assert call_args.kwargs["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}
