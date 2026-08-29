from app.url_canon import canonicalize_url


def test_strips_utm_and_click_ids():
    assert canonicalize_url(
        "https://ex.com/jobs/5?utm_source=x&utm_medium=y&gclid=abc&fbclid=def"
    ) == "https://ex.com/jobs/5"


def test_strips_linkedin_tracking_params_case_insensitively():
    url = ("https://no.linkedin.com/jobs/view/ai-engineer-at-acme-123"
           "?position=12&pageNum=0&refId=aB%2Fc&trackingId=xYz&trk=public_jobs")
    assert canonicalize_url(url) == "https://no.linkedin.com/jobs/view/ai-engineer-at-acme-123"


def test_keeps_functional_params_in_original_order():
    url = "https://web106.reachmee.com/ext/I002/1338/job?site=6&lang=NO&validator=abc&job_id=1124"
    assert canonicalize_url(url) == url


def test_keeps_functional_params_when_mixed_with_tracking():
    assert canonicalize_url(
        "https://ex.com/j?job_id=9&utm_campaign=q&page=2"
    ) == "https://ex.com/j?job_id=9&page=2"


def test_lowercases_host_but_not_path():
    assert canonicalize_url("https://Careers.EXAMPLE.com/Jobs/Senior-Dev") == \
        "https://careers.example.com/Jobs/Senior-Dev"


def test_keeps_fragment():
    # Slack permalinks (.../archives/C123#<ts>) and SPA hash routes carry identity
    # in the fragment; tracking junk never does. So the fragment is preserved.
    assert canonicalize_url("https://ex.com/jobs#section") == "https://ex.com/jobs#section"


def test_strips_tracking_params_but_keeps_fragment():
    assert canonicalize_url("https://ex.com/j?utm_source=x#/jobs/123") == "https://ex.com/j#/jobs/123"


def test_drops_empty_query():
    assert canonicalize_url("https://ex.com/jobs?") == "https://ex.com/jobs"


def test_idempotent():
    url = "https://ex.com/j?job_id=9&utm_campaign=q&refId=z#frag"
    once = canonicalize_url(url)
    assert canonicalize_url(once) == once


def test_passes_through_non_http():
    assert canonicalize_url("mailto:jobs@ex.com") == "mailto:jobs@ex.com"
    assert canonicalize_url("/relative/path?refId=x") == "/relative/path?refId=x"
