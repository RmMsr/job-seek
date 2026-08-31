from datetime import date, timedelta
from unittest.mock import MagicMock
from app.ai.summarize import summarize, JobSummary

_TODAY = date(2026, 8, 31)


def _mock_client(response_text: str) -> MagicMock:
    client = MagicMock()
    choice = MagicMock()
    choice.message.content = response_text
    client.chat.completions.create.return_value = MagicMock(choices=[choice])
    return client


def test_summarize_returns_title_headline_and_summary():
    response = (
        '{"title": "ML Engineer - Remote", '
        '"headline": "Fully remote with a $180k+ ceiling", '
        '"summary": "**Role:** ML Engineer"}'
    )
    result = summarize(_mock_client(response), "llama3.2", "long job description text")
    assert result.title == "ML Engineer - Remote"
    assert result.headline == "Fully remote with a $180k+ ceiling"
    assert result.summary == "**Role:** ML Engineer"


def test_summarize_calls_llm_once():
    client = _mock_client('{"title": "T", "headline": "H", "summary": "S"}')
    summarize(client, "llama3.2", "content")
    assert client.chat.completions.create.call_count == 1


def test_summarize_strips_markdown_code_fence():
    response = '```json\n{"title": "T", "headline": "H", "summary": "S"}\n```'
    result = summarize(_mock_client(response), "llama3.2", "content")
    assert (result.title, result.headline, result.summary) == ("T", "H", "S")


def test_summarize_returns_empty_strings_on_malformed_json():
    result = summarize(_mock_client("not json"), "llama3.2", "content")
    assert result == JobSummary()


def test_summarize_returns_empty_strings_on_api_error():
    client = MagicMock()
    client.chat.completions.create.side_effect = Exception("boom")
    result = summarize(client, "llama3.2", "content")
    assert result == JobSummary()


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
    content = "Some job posting text.\nApply here: https://example.com/apply\nMore text."
    result = summarize(_mock_client(response), "llama3.2", content)
    assert result.summary == "**Role:** ML Engineer\n\n**Original posting:** [https://example.com/apply](https://example.com/apply)"


def test_summarize_rejects_source_link_not_present_in_content():
    response = (
        '{"title": "T", "headline": "H", "summary": "**Role:** ML Engineer", '
        '"source_link": "https://hallucinated.example.com/made-up"}'
    )
    result = summarize(_mock_client(response), "llama3.2", "Some job posting text with no links at all.")
    assert result.summary == "**Role:** ML Engineer"


def test_summarize_rejects_non_http_source_link():
    response = (
        '{"title": "T", "headline": "H", "summary": "**Role:** ML Engineer", '
        '"source_link": "javascript:alert(1)"}'
    )
    result = summarize(_mock_client(response), "llama3.2", "Some text.\njavascript:alert(1)\nMore text.")
    assert result.summary == "**Role:** ML Engineer"


def test_summarize_leaves_summary_unchanged_when_no_source_link():
    response = '{"title": "T", "headline": "H", "summary": "**Role:** ML Engineer", "source_link": ""}'
    result = summarize(_mock_client(response), "llama3.2", "content with no links")
    assert result.summary == "**Role:** ML Engineer"


def test_summarize_leaves_summary_unchanged_when_source_link_field_missing():
    response = '{"title": "T", "headline": "H", "summary": "**Role:** ML Engineer"}'
    result = summarize(_mock_client(response), "llama3.2", "content with no links")
    assert result.summary == "**Role:** ML Engineer"


def test_summarize_handles_null_source_link_without_crashing():
    # The model may emit `null` (not "") for optional fields it can't fill.
    response = '{"title": "T", "headline": "H", "summary": "**Role:** ML Engineer", "source_link": null}'
    result = summarize(_mock_client(response), "llama3.2", "content with no links")
    assert result.title == "T"
    assert result.headline == "H"
    assert result.summary == "**Role:** ML Engineer"


def test_summarize_lead_path_unaffected_by_source_link():
    response = '{"organizations": ["Acme"], "headline": "H", "source_link": "https://example.com/apply"}'
    original = "Posted by U123:\n\nAcme is hiring, apply at https://example.com/apply"
    result = summarize(_mock_client(response), "llama3.2", original, content_type="lead")
    assert result.summary == original


def test_summarize_uses_lead_prompt_for_lead_content_type():
    client = _mock_client('{"organizations": ["Acme"], "headline": "H", "summary": "S"}')
    summarize(client, "llama3.2", "content", content_type="lead")
    system = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert "organization" in system.lower()
    assert "job posting" not in system.lower()


def test_summarize_lead_builds_title_from_organizations():
    response = '{"organizations": ["Acme", "Globex"], "headline": "A couple of orgs are hiring"}'
    result = summarize(_mock_client(response), "llama3.2", "Posted by U123:\n\noriginal message", content_type="lead")
    assert result.title == "Acme, Globex"
    assert result.headline == "A couple of orgs are hiring"


def test_summarize_lead_retains_the_original_message_as_the_summary():
    # A raw lead keeps its original message verbatim as the summary — never an AI rewrite.
    response = '{"organizations": ["Acme"], "headline": "H", "summary": "an AI rewrite that should be ignored"}'
    original = "Posted by U123:\n\nAcme is hiring, DM me"
    result = summarize(_mock_client(response), "llama3.2", original, content_type="lead")
    assert result.summary == original


def test_summarize_lead_handles_no_organizations_found():
    response = '{"organizations": [], "headline": "No specific org named"}'
    result = summarize(_mock_client(response), "llama3.2", "content", content_type="lead")
    assert result.title == ""


def test_summarize_lead_falls_back_to_original_message_on_malformed_json():
    original = "Posted by U123:\n\noriginal message"
    result = summarize(_mock_client("not json"), "llama3.2", original, content_type="lead")
    assert result == JobSummary(summary=original)


def test_summarize_lead_falls_back_to_original_message_on_api_error():
    client = MagicMock()
    client.chat.completions.create.side_effect = Exception("boom")
    original = "Posted by U123:\n\noriginal message"
    result = summarize(client, "llama3.2", original, content_type="lead")
    assert result == JobSummary(summary=original)


def test_summarize_non_slack_lead_gets_ai_summary_not_raw_body():
    response = '{"title": "ML Engineer - Paris", "headline": "H", "summary": "**Role:** ML Engineer"}'
    result = summarize(
        _mock_client(response), "llama3.2", "a wall of scraped page chrome",
        content_type="lead", raw_passthrough=False,
    )
    assert result.title == "ML Engineer - Paris"
    assert result.summary == "**Role:** ML Engineer"


def test_summarize_non_slack_lead_uses_job_posting_prompt():
    client = _mock_client('{"title": "T", "headline": "H", "summary": "S"}')
    summarize(client, "llama3.2", "content", content_type="lead", raw_passthrough=False)
    system = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert "job posting" in system.lower()


def test_summarize_non_slack_lead_empty_summary_on_api_error():
    client = MagicMock()
    client.chat.completions.create.side_effect = Exception("boom")
    result = summarize(client, "llama3.2", "raw", content_type="lead", raw_passthrough=False)
    assert result == JobSummary()


def test_summarize_extracts_company_and_posted_date():
    response = (
        '{"title": "ML Engineer - Remote", "company": "Acme", '
        '"headline": "H", "summary": "S", "posted_date": "2026-08-17"}'
    )
    result = summarize(_mock_client(response), "llama3.2", "text", today=_TODAY)
    assert result.company == "Acme"
    assert result.posted_date == "2026-08-17"


def test_summarize_injects_today_into_user_message():
    client = _mock_client('{"title": "T", "headline": "H", "summary": "S"}')
    summarize(client, "llama3.2", "the posting body", today=_TODAY)
    user_msg = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    assert "2026-08-31" in user_msg
    assert "the posting body" in user_msg


def test_summarize_rejects_future_posted_date():
    response = '{"title": "T", "company": "Acme", "headline": "H", "summary": "S", "posted_date": "2027-01-01"}'
    result = summarize(_mock_client(response), "llama3.2", "text", today=_TODAY)
    assert result.posted_date == ""


def test_summarize_rejects_malformed_posted_date():
    response = '{"title": "T", "company": "Acme", "headline": "H", "summary": "S", "posted_date": "2 weeks ago"}'
    result = summarize(_mock_client(response), "llama3.2", "text", today=_TODAY)
    assert result.posted_date == ""


def test_summarize_accepts_datetime_shaped_posted_date():
    r = '{"title": "T", "headline": "H", "summary": "S", "posted_date": "2026-08-17T09:00:00"}'
    assert summarize(_mock_client(r), "llama3.2", "x", today=_TODAY).posted_date == "2026-08-17"


def test_summarize_rejects_posted_date_more_than_a_year_old():
    r = '{"title": "T", "headline": "H", "summary": "S", "posted_date": "2024-01-01"}'
    assert summarize(_mock_client(r), "llama3.2", "x", today=_TODAY).posted_date == ""


def test_summarize_lead_path_gets_no_today_prefix():
    client = _mock_client('{"organizations": ["Acme"], "headline": "H"}')
    summarize(client, "llama3.2", "Posted by U1:\n\nmsg", content_type="lead", today=_TODAY)
    user_msg = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    assert "Today is" not in user_msg


def test_summarize_missing_company_and_date_fields_are_empty():
    response = '{"title": "T", "headline": "H", "summary": "S"}'
    result = summarize(_mock_client(response), "llama3.2", "text", today=_TODAY)
    assert result.company == ""
    assert result.posted_date == ""


def test_summarize_handles_null_company_and_date():
    response = '{"title": "T", "company": null, "headline": "H", "summary": "S", "posted_date": null}'
    result = summarize(_mock_client(response), "llama3.2", "text", today=_TODAY)
    assert result.company == ""
    assert result.posted_date == ""


def test_summarize_lead_path_leaves_company_and_date_empty():
    response = '{"organizations": ["Acme"], "headline": "H"}'
    result = summarize(_mock_client(response), "llama3.2", "Posted by U1:\n\nmsg", content_type="lead", today=_TODAY)
    assert result.company == ""
    assert result.posted_date == ""


def test_summarize_system_prompt_mentions_posted_date():
    client = _mock_client('{"title": "T", "headline": "H", "summary": "S"}')
    summarize(client, "llama3.2", "content", today=_TODAY)
    system = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert "posted_date" in system


def test_summarize_today_defaults_to_current_date_when_omitted():
    # A real today-relative date is accepted; the call must not raise without `today`.
    # Must be recent: the validator now rejects dates more than ~a year old.
    recent = (date.today() - timedelta(days=30)).isoformat()
    response = (
        '{"title": "T", "company": "Acme", "headline": "H", "summary": "S", '
        f'"posted_date": "{recent}"}}'
    )
    result = summarize(_mock_client(response), "llama3.2", "text")
    assert result.posted_date == recent


def test_summarize_title_spec_excludes_organization():
    client = _mock_client('{"title": "T", "headline": "H", "summary": "S"}')
    summarize(client, "llama3.2", "content", today=_TODAY)
    system = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert "(remote/hybrid/onsite)>" in system          # title format kept, minus the org
    assert "@ Organization" not in system
    assert "include the organization name" not in system
