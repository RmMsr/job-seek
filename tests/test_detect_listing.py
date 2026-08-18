import json
from unittest.mock import MagicMock
from app.ai.detect_listing import _SYSTEM, detect_listing


def _client_with_response(content: str) -> MagicMock:
    client = MagicMock()
    choice = MagicMock()
    choice.message.content = content
    client.chat.completions.create.return_value = MagicMock(choices=[choice])
    return client


def test_detect_listing_returns_is_listing_and_job_links():
    links = [
        ("https://example.com/jobs/1", "Senior Engineer"),
        ("https://example.com/jobs/2", "Staff Engineer"),
        ("https://example.com/about", "About us"),
    ]
    client = _client_with_response(json.dumps({
        "is_listing": True,
        "job_links": ["https://example.com/jobs/1", "https://example.com/jobs/2"],
    }))
    result = detect_listing(client, "llama3.2", links, "https://example.com/careers")
    assert result == {
        "is_listing": True,
        "job_links": ["https://example.com/jobs/1", "https://example.com/jobs/2"],
    }


def test_detect_listing_filters_hallucinated_links():
    links = [("https://example.com/jobs/1", "Senior Engineer")]
    client = _client_with_response(json.dumps({
        "is_listing": True,
        "job_links": ["https://example.com/jobs/1", "https://example.com/jobs/999"],
    }))
    result = detect_listing(client, "llama3.2", links, "https://example.com/careers")
    assert result == {"is_listing": True, "job_links": ["https://example.com/jobs/1"]}


def test_detect_listing_not_a_listing():
    links = [("https://example.com/apply", "Apply now")]
    client = _client_with_response(json.dumps({"is_listing": False, "job_links": []}))
    result = detect_listing(client, "llama3.2", links, "https://example.com/jobs/1")
    assert result == {"is_listing": False, "job_links": []}


def test_detect_listing_fails_open_on_exception():
    client = MagicMock()
    client.chat.completions.create.side_effect = RuntimeError("connection refused")
    result = detect_listing(client, "llama3.2", [("https://example.com/x", "X")], "https://example.com")
    assert result == {"is_listing": False, "job_links": []}


def test_detect_listing_prompt_warns_against_role_location_category_pages():
    # Sites like jobsinai.com generate one link per role/city combination (e.g.
    # "/jobs/ai-engineer-jobs-in-oslo"), which reads as job-like to a naive model
    # even though it's a category/filter page listing postings for that role, not
    # a specific posting. The system prompt must call this out explicitly.
    client = _client_with_response(json.dumps({"is_listing": False, "job_links": []}))
    detect_listing(client, "llama3.2", [("https://example.com/x", "X")], "https://example.com")
    sent_system_message = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert sent_system_message == _SYSTEM
    assert "role" in _SYSTEM and "location" in _SYSTEM
