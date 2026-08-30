from app.fetchers.pagination import next_page_url


def test_offset_params_incremented_by_page_size():
    assert next_page_url("https://x.com/j?start=0", 10) == "https://x.com/j?start=10"
    assert next_page_url("https://x.com/j?offset=20", 10) == "https://x.com/j?offset=30"
    assert next_page_url("https://x.com/j?from=5", 5) == "https://x.com/j?from=10"


def test_page_param_incremented_by_one():
    assert next_page_url("https://x.com/j?page=2", 10) == "https://x.com/j?page=3"


def test_first_match_ordering_when_several_present():
    # start beats page
    assert next_page_url("https://x.com/j?page=1&start=0", 10) == "https://x.com/j?page=1&start=10"
    # offset beats from
    assert next_page_url("https://x.com/j?from=0&offset=0", 10) == "https://x.com/j?from=0&offset=10"


def test_no_recognized_param_returns_none():
    assert next_page_url("https://x.com/j?q=python&sort=date", 10) is None
    assert next_page_url("https://x.com/j", 10) is None


def test_zero_page_size_returns_none():
    assert next_page_url("https://x.com/j?start=0", 0) is None


def test_non_int_value_returns_none():
    assert next_page_url("https://x.com/j?start=abc", 10) is None


def test_other_params_and_fragment_preserved():
    assert (
        next_page_url("https://x.com/j?q=python&start=0&sort=date#results", 10)
        == "https://x.com/j?q=python&start=10&sort=date#results"
    )
