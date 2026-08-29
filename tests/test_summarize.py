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


def test_summarize_appends_valid_source_link_to_summary():
    response = (
        '{"title": "T", "headline": "H", "summary": "**Role:** ML Engineer", '
        '"source_link": "https://example.com/apply"}'
    )
    client = _mock_client(response)
    content = "Some job posting text.\nApply here: https://example.com/apply\nMore text."
    _, _, summary = summarize(client, "llama3.2", content)
    assert summary == "**Role:** ML Engineer\n\n**Original posting:** [https://example.com/apply](https://example.com/apply)"


def test_summarize_rejects_source_link_not_present_in_content():
    response = (
        '{"title": "T", "headline": "H", "summary": "**Role:** ML Engineer", '
        '"source_link": "https://hallucinated.example.com/made-up"}'
    )
    client = _mock_client(response)
    content = "Some job posting text with no links at all."
    _, _, summary = summarize(client, "llama3.2", content)
    assert summary == "**Role:** ML Engineer"


def test_summarize_rejects_non_http_source_link():
    response = (
        '{"title": "T", "headline": "H", "summary": "**Role:** ML Engineer", '
        '"source_link": "javascript:alert(1)"}'
    )
    client = _mock_client(response)
    content = "Some text.\njavascript:alert(1)\nMore text."
    _, _, summary = summarize(client, "llama3.2", content)
    assert summary == "**Role:** ML Engineer"


def test_summarize_leaves_summary_unchanged_when_no_source_link():
    response = '{"title": "T", "headline": "H", "summary": "**Role:** ML Engineer", "source_link": ""}'
    client = _mock_client(response)
    _, _, summary = summarize(client, "llama3.2", "content with no links")
    assert summary == "**Role:** ML Engineer"


def test_summarize_leaves_summary_unchanged_when_source_link_field_missing():
    # Model may omit the field entirely (e.g. older prompt caching, non-compliant model).
    response = '{"title": "T", "headline": "H", "summary": "**Role:** ML Engineer"}'
    client = _mock_client(response)
    _, _, summary = summarize(client, "llama3.2", "content with no links")
    assert summary == "**Role:** ML Engineer"


def test_summarize_handles_null_source_link_without_crashing():
    # Model may emit null for optional fields (plausible, common LLM behavior).
    # This should not raise AttributeError or silently lose the summary.
    response = '{"title": "T", "headline": "H", "summary": "**Role:** ML Engineer", "source_link": null}'
    client = _mock_client(response)
    title, headline, summary = summarize(client, "llama3.2", "content with no links")
    assert title == "T"
    assert headline == "H"
    assert summary == "**Role:** ML Engineer"


def test_summarize_lead_path_unaffected_by_source_link():
    response = '{"organizations": ["Acme"], "headline": "H", "source_link": "https://example.com/apply"}'
    client = _mock_client(response)
    original = "Posted by U123:\n\nAcme is hiring, apply at https://example.com/apply"
    _, _, summary = summarize(client, "llama3.2", original, content_type="lead")
    assert summary == original


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


def test_summarize_non_slack_lead_gets_ai_summary_not_raw_body():
    response = '{"title": "ML Engineer - Paris @ Acme", "headline": "H", "summary": "**Role:** ML Engineer"}'
    client = _mock_client(response)
    title, headline, summary = summarize(
        client, "llama3.2", "a wall of scraped page chrome",
        content_type="lead", raw_passthrough=False,
    )
    assert title == "ML Engineer - Paris @ Acme"
    assert summary == "**Role:** ML Engineer"


def test_summarize_non_slack_lead_uses_job_posting_prompt():
    client = _mock_client('{"title": "T", "headline": "H", "summary": "S"}')
    summarize(client, "llama3.2", "content", content_type="lead", raw_passthrough=False)
    system = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert "job posting" in system.lower()


def test_summarize_non_slack_lead_empty_summary_on_api_error():
    client = MagicMock()
    client.chat.completions.create.side_effect = Exception("boom")
    result = summarize(client, "llama3.2", "raw", content_type="lead", raw_passthrough=False)
    assert result == ("", "", "")
