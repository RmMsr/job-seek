from app.template_env import short_url


def test_short_url_drops_scheme():
    assert short_url("https://example.com/jobs") == "example.com/jobs"


def test_short_url_truncates_long():
    out = short_url("https://example.com/" + "a" * 100, limit=30)
    assert len(out) == 30 and out.endswith("…")


def test_short_url_keeps_short_untouched():
    assert short_url("http://x.io/a") == "x.io/a"


def test_short_url_handles_empty():
    assert short_url("") == ""
