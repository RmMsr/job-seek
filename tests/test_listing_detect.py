from unittest.mock import patch
import pytest
from app.fetchers.content import FetchError
from app.fetchers.listing_detect import detect_listing_page, ListingDetection

_LISTING = "<html><body>" + ("<a href='/j/1'>Dev</a><a href='/j/2'>Ops</a>" * 1) + \
    "<p>" + ("Plenty of real page text here to clear the threshold. " * 6) + "</p></body></html>"
_THIN = "<html><body><noscript>Enable JS</noscript></body></html>"
_BOILERPLATE = "<html><body><nav>Home About Contact</nav><p>" + \
    ("Company blurb with lots of words but no job links at all. " * 6) + "</p></body></html>"


def _dl(is_listing, job_links):
    return {"is_listing": is_listing, "job_links": list(job_links)}


def test_listing_in_raw_html_no_render():
    with patch("app.fetchers.listing_detect.fetch_url_html", return_value=_LISTING), \
         patch("app.fetchers.listing_detect.render_html") as render, \
         patch("app.fetchers.listing_detect.detect_listing",
               return_value=_dl(True, ["https://ex.com/j/1", "https://ex.com/j/2"])):
        out = detect_listing_page(object(), "m", "https://ex.com/careers")
    render.assert_not_called()
    assert out == ListingDetection(_LISTING, True, ["https://ex.com/j/1", "https://ex.com/j/2"], False)


def test_thin_raw_html_escalates_to_render():
    with patch("app.fetchers.listing_detect.fetch_url_html", return_value=_THIN), \
         patch("app.fetchers.listing_detect.render_html", return_value=_LISTING) as render, \
         patch("app.fetchers.listing_detect.detect_listing",
               return_value=_dl(True, ["https://ex.com/j/1", "https://ex.com/j/2"])):
        out = detect_listing_page(object(), "m", "https://ex.com/careers")
    render.assert_called_once_with("https://ex.com/careers")
    assert out.rendered is True and out.html == _LISTING and len(out.job_links) == 2


def test_nonthin_but_no_links_escalates_and_keeps_better_result():
    detect = [_dl(False, []), _dl(True, ["https://ex.com/j/1", "https://ex.com/j/2", "https://ex.com/j/3"])]
    with patch("app.fetchers.listing_detect.fetch_url_html", return_value=_BOILERPLATE), \
         patch("app.fetchers.listing_detect.render_html", return_value=_LISTING) as render, \
         patch("app.fetchers.listing_detect.detect_listing", side_effect=detect):
        out = detect_listing_page(object(), "m", "https://ex.com/careers")
    render.assert_called_once_with("https://ex.com/careers")
    assert out.rendered is True and out.is_listing is True and len(out.job_links) == 3


def test_render_not_better_keeps_raw_result():
    detect = [_dl(True, ["https://ex.com/j/1"]), _dl(True, ["https://ex.com/j/9"])]
    with patch("app.fetchers.listing_detect.fetch_url_html", return_value=_BOILERPLATE), \
         patch("app.fetchers.listing_detect.render_html", return_value=_LISTING), \
         patch("app.fetchers.listing_detect.detect_listing", side_effect=detect):
        out = detect_listing_page(object(), "m", "https://ex.com/careers")
    assert out.rendered is False and out.job_links == ["https://ex.com/j/1"]


def test_fetch_error_propagates():
    with patch("app.fetchers.listing_detect.fetch_url_html", side_effect=FetchError("HTTP 503")):
        with pytest.raises(FetchError):
            detect_listing_page(object(), "m", "https://ex.com/careers")


_POSTING = "<html><body><p>" + (
    "We are hiring a Staff Engineer. You will own the platform roadmap, mentor "
    "engineers, and ship reliability improvements across the fleet. " * 12
) + "</p></body></html>"


def test_nonlisting_render_kept_when_substantially_richer():
    # raw HTML clears the 200-char floor (so the first block is skipped) but is a
    # thin JS shell; the render is a full, link-free posting.
    detect = [_dl(False, []), _dl(False, [])]
    with patch("app.fetchers.listing_detect.fetch_url_html", return_value=_BOILERPLATE), \
         patch("app.fetchers.listing_detect.render_html", return_value=_POSTING) as render, \
         patch("app.fetchers.listing_detect.detect_listing", side_effect=detect):
        out = detect_listing_page(object(), "m", "https://ex.com/job/1")
    render.assert_called_once_with("https://ex.com/job/1")
    assert out.rendered is True and out.is_listing is False
    assert out.html == _POSTING and out.job_links == []


def test_nonlisting_render_discarded_when_not_richer():
    similar = "<html><body><nav>Home About Contact</nav><p>" + \
        ("Roughly the same amount of words as the raw page here. " * 6) + "</p></body></html>"
    detect = [_dl(False, []), _dl(False, [])]
    with patch("app.fetchers.listing_detect.fetch_url_html", return_value=_BOILERPLATE), \
         patch("app.fetchers.listing_detect.render_html", return_value=similar), \
         patch("app.fetchers.listing_detect.detect_listing", side_effect=detect):
        out = detect_listing_page(object(), "m", "https://ex.com/job/1")
    assert out.rendered is False and out.html == _BOILERPLATE and out.is_listing is False


def test_nonlisting_render_returns_none_keeps_raw():
    with patch("app.fetchers.listing_detect.fetch_url_html", return_value=_BOILERPLATE), \
         patch("app.fetchers.listing_detect.render_html", return_value=None), \
         patch("app.fetchers.listing_detect.detect_listing", return_value=_dl(False, [])):
        out = detect_listing_page(object(), "m", "https://ex.com/job/1")
    assert out.rendered is False and out.html == _BOILERPLATE
