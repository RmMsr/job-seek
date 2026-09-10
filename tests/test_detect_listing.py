import json
from unittest.mock import MagicMock
from app.ai.detect_listing import _SYSTEM, detect_listing


def _client_with_response(content) -> MagicMock:
    client = MagicMock()
    choice = MagicMock()
    choice.message.content = content
    client.chat.completions.create.return_value = MagicMock(choices=[choice])
    return client


_LINKS = [
    ("https://example.com/jobs/1", "Senior Engineer"),
    ("https://example.com/jobs/2", "Staff Engineer"),
    ("https://example.com/about", "About us"),
]


def test_detect_listing_maps_job_ids_to_hrefs():
    client = _client_with_response(json.dumps({"is_listing": True, "job_ids": [0, 1]}))
    result = detect_listing(client, "m", _LINKS, "https://example.com/careers")
    assert result == {
        "is_listing": True,
        "job_links": ["https://example.com/jobs/1", "https://example.com/jobs/2"],
    }


def test_detect_listing_ignores_out_of_range_indices():
    client = _client_with_response(json.dumps({"is_listing": True, "job_ids": [0, 99]}))
    result = detect_listing(client, "m", _LINKS[:1], "https://example.com/careers")
    assert result == {"is_listing": True, "job_links": ["https://example.com/jobs/1"]}


def test_detect_listing_not_a_listing():
    client = _client_with_response(json.dumps({"is_listing": False, "job_ids": []}))
    result = detect_listing(client, "m", _LINKS, "https://example.com/jobs/1")
    assert result == {"is_listing": False, "job_links": []}


def test_detect_listing_sends_numbered_list_with_urls():
    client = _client_with_response(json.dumps({"is_listing": False, "job_ids": []}))
    detect_listing(client, "m", _LINKS, "https://example.com/careers")
    user_msg = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    assert "0. Senior Engineer [https://example.com/jobs/1]" in user_msg
    assert "1. Staff Engineer [https://example.com/jobs/2]" in user_msg


def test_detect_listing_propagates_llm_exception():
    import pytest
    client = MagicMock()
    client.chat.completions.create.side_effect = RuntimeError("connection refused")
    with pytest.raises(RuntimeError, match="connection refused"):
        detect_listing(client, "m", _LINKS, "https://example.com")


def test_detect_listing_logs_warning_on_unparseable_response(caplog):
    client = _client_with_response("this is not json")
    with caplog.at_level("WARNING", logger="job_seek"):
        result = detect_listing(client, "m", _LINKS, "https://example.com/careers")
    assert result == {"is_listing": False, "job_links": []}
    assert any("unparseable" in r.message and "https://example.com/careers" in r.message
               for r in caplog.records)


def test_detect_listing_prompt_warns_against_role_location_category_pages():
    client = _client_with_response(json.dumps({"is_listing": False, "job_ids": []}))
    detect_listing(client, "m", _LINKS, "https://example.com")
    sent_system = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert sent_system == _SYSTEM
    assert "role" in _SYSTEM and "location" in _SYSTEM
