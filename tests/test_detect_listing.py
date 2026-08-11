import json
from unittest.mock import MagicMock
from app.ai.detect_listing import detect_listing


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
