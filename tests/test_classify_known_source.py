from app.ai.classify_known_source import classify_known_source, DETECTABLE_FETCHER_TYPES


def test_classify_known_source_recognizes_slack_archive_url():
    assert classify_known_source("https://example-workspace.slack.com/archives/C0EXAMPLE1") == "slack"


def test_classify_known_source_recognizes_slack_messages_url():
    assert classify_known_source("https://example-workspace.slack.com/messages/C0EXAMPLE1") == "slack"


def test_classify_known_source_recognizes_finn_no_bare_host():
    assert classify_known_source("https://finn.no/job/browse.html") == "finn_listing"


def test_classify_known_source_recognizes_finn_no_www_subdomain():
    assert classify_known_source("https://www.finn.no/job/search?occupation=1.23") == "finn_listing"


def test_classify_known_source_returns_none_for_unrelated_url():
    assert classify_known_source("https://careers.example.com/jobs") is None


def test_classify_known_source_does_not_match_lookalike_host():
    # "notfinn.no" must not be treated as finn.no via a naive substring/endswith check
    assert classify_known_source("https://notfinn.no/jobs") is None


def test_detectable_fetcher_types_constant():
    assert DETECTABLE_FETCHER_TYPES == {"slack", "finn_listing", "generic_listing"}
