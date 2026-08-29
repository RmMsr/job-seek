import pytest
from app.url_rewrite import suggest_rewrite, suggest_source_name, RewriteSuggestion

_LOGGED_IN = (
    "https://www.linkedin.com/jobs/search-results/"
    "?currentJobId=4435382222&keywords=robotics+norge"
    "&origin=BLENDED_SEARCH_RESULT_NAVIGATION_SEE_ALL"
)


def test_logged_in_search_url_rewrites_to_guest_api():
    s = suggest_rewrite(_LOGGED_IN)
    assert isinstance(s, RewriteSuggestion)
    assert s.url == (
        "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
        "?keywords=robotics+norge&start=0"
    )
    assert s.reason.startswith("This URL looks like a LinkedIn job search")


def test_suggest_source_name_from_linkedin_search_terms():
    assert suggest_source_name(_LOGGED_IN) == "linkedin/robotics-norge"
    # Also works on the already-rewritten guest URL (the re-detect pass).
    assert suggest_source_name(suggest_rewrite(_LOGGED_IN).url) == "linkedin/robotics-norge"
    assert suggest_source_name(
        "https://www.linkedin.com/jobs/search?keywords=data+engineer&location=Norway"
    ) == "linkedin/data-engineer/Norway"


def test_suggest_source_name_none_for_other_urls():
    assert suggest_source_name("https://careers.example.com/jobs") is None
    assert suggest_source_name("https://www.linkedin.com/jobs/view/x-123") is None
    assert suggest_source_name("not a url") is None


def test_location_param_is_carried_when_present():
    s = suggest_rewrite(
        "https://www.linkedin.com/jobs/search?keywords=data+engineer&location=Norway&geoId=123"
    )
    assert s.url == (
        "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
        "?keywords=data+engineer&location=Norway&start=0"
    )


def test_subdomain_host_matches():
    assert suggest_rewrite("https://no.linkedin.com/jobs/search?keywords=ai") is not None


def test_empty_or_missing_keywords_does_not_match():
    assert suggest_rewrite("https://www.linkedin.com/jobs/search?keywords=") is None
    assert suggest_rewrite("https://www.linkedin.com/jobs/search-results/?origin=x") is None


def test_single_posting_url_does_not_match():
    assert suggest_rewrite("https://www.linkedin.com/jobs/view/robotics-engineer-at-luma-4445272760") is None


def test_already_rewritten_guest_url_does_not_match():
    s = suggest_rewrite(_LOGGED_IN)
    assert suggest_rewrite(s.url) is None


def test_non_linkedin_url_does_not_match():
    assert suggest_rewrite("https://example.com/jobs?keywords=robotics") is None


def test_non_http_input_returns_none():
    assert suggest_rewrite("mailto:jobs@example.com") is None
    assert suggest_rewrite("not a url") is None
