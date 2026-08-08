from unittest.mock import MagicMock
from app.ai.summarize import summarize


def _mock_client(response_text: str) -> MagicMock:
    client = MagicMock()
    choice = MagicMock()
    choice.message.content = response_text
    client.chat.completions.create.return_value = MagicMock(choices=[choice])
    return client


def test_summarize_returns_title_headline_and_summary():
    response = (
        '{"title": "ML Engineer - Remote @ Acme", '
        '"headline": "Fully remote with a $180k+ ceiling", '
        '"summary": "**Role:** ML Engineer"}'
    )
    client = _mock_client(response)
    title, headline, summary = summarize(client, "llama3.2", "long job description text")
    assert title == "ML Engineer - Remote @ Acme"
    assert headline == "Fully remote with a $180k+ ceiling"
    assert summary == "**Role:** ML Engineer"


def test_summarize_calls_llm_once():
    client = _mock_client('{"title": "T", "headline": "H", "summary": "S"}')
    summarize(client, "llama3.2", "content")
    assert client.chat.completions.create.call_count == 1


def test_summarize_strips_markdown_code_fence():
    response = '```json\n{"title": "T", "headline": "H", "summary": "S"}\n```'
    client = _mock_client(response)
    result = summarize(client, "llama3.2", "content")
    assert result == ("T", "H", "S")


def test_summarize_returns_empty_strings_on_malformed_json():
    client = _mock_client("not json")
    result = summarize(client, "llama3.2", "content")
    assert result == ("", "", "")


def test_summarize_returns_empty_strings_on_api_error():
    client = MagicMock()
    client.chat.completions.create.side_effect = Exception("boom")
    result = summarize(client, "llama3.2", "content")
    assert result == ("", "", "")


def test_summarize_disables_model_thinking():
    client = _mock_client('{"title": "T", "headline": "H", "summary": "S"}')
    summarize(client, "llama3.2", "content")
    call_args = client.chat.completions.create.call_args
    assert call_args.kwargs["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}


def test_summarize_defaults_to_job_posting_prompt():
    client = _mock_client('{"title": "T", "headline": "H", "summary": "S"}')
    summarize(client, "llama3.2", "content")
    system = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert "job posting" in system.lower()


def test_summarize_uses_lead_prompt_for_lead_content_type():
    client = _mock_client('{"organizations": ["Acme"], "headline": "H", "summary": "S"}')
    summarize(client, "llama3.2", "content", content_type="lead")
    system = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert "organization" in system.lower()
    assert "job posting" not in system.lower()


def test_summarize_lead_builds_title_from_organizations():
    response = '{"organizations": ["Acme", "Globex"], "headline": "A couple of orgs are hiring"}'
    client = _mock_client(response)
    title, headline, summary = summarize(
        client, "llama3.2", "Posted by U123:\n\noriginal message", content_type="lead"
    )
    assert title == "Acme, Globex"
    assert headline == "A couple of orgs are hiring"


def test_summarize_lead_retains_the_original_message_as_the_summary():
    # Leads are short/vague by nature — the retained original (already
    # including the author, from SlackFetcher) is more useful than an
    # AI-compressed rewrite, so summary is never AI-generated for leads.
    response = '{"organizations": ["Acme"], "headline": "H", "summary": "an AI rewrite that should be ignored"}'
    client = _mock_client(response)
    original = "Posted by U123:\n\nAcme is hiring, DM me"
    _, _, summary = summarize(client, "llama3.2", original, content_type="lead")
    assert summary == original


def test_summarize_lead_handles_no_organizations_found():
    response = '{"organizations": [], "headline": "No specific org named"}'
    client = _mock_client(response)
    title, _, _ = summarize(client, "llama3.2", "content", content_type="lead")
    assert title == ""


def test_summarize_lead_falls_back_to_original_message_on_malformed_json():
    client = _mock_client("not json")
    original = "Posted by U123:\n\noriginal message"
    result = summarize(client, "llama3.2", original, content_type="lead")
    assert result == ("", "", original)


def test_summarize_lead_falls_back_to_original_message_on_api_error():
    client = MagicMock()
    client.chat.completions.create.side_effect = Exception("boom")
    original = "Posted by U123:\n\noriginal message"
    result = summarize(client, "llama3.2", original, content_type="lead")
    assert result == ("", "", original)
