import httpx
import pytest
import respx
from app.fetchers.content import (
    MIN_CONTENT_LENGTH, MIN_ARTICLE_LENGTH, extract_text, has_enough_content,
    has_enough_text, FetchError, NoContentError, fetch_url_html,
    extract_text_or_raise, extract_page_title, text_length, is_substantially_richer,
    extract_readable_text,
)


def test_has_enough_content_true_for_long_text():
    html = "<html><body><p>" + ("word " * 50) + "</p></body></html>"
    assert has_enough_content(html)


def test_has_enough_content_false_for_short_text():
    html = "<html><body><noscript>Enable JavaScript</noscript></body></html>"
    assert not has_enough_content(html)


def test_has_enough_content_boundary_at_min_length():
    text = "a" * MIN_CONTENT_LENGTH
    assert has_enough_content(f"<html><body><p>{text}</p></body></html>")
    assert not has_enough_content(f"<html><body><p>{text[:-1]}</p></body></html>")


def test_extract_text_strips_tags():
    html = "<html><body><h1>Title</h1><p>Body text</p></body></html>"
    text = extract_text(html)
    assert "Title" in text
    assert "Body text" in text
    assert "<h1>" not in text


def test_has_enough_text_true_for_long_plain_text():
    assert has_enough_text("word " * 50)


def test_has_enough_text_false_for_short_plain_text():
    assert not has_enough_text("You need to enable JavaScript to run this app.")


@respx.mock
def test_fetch_url_html_returns_body_on_200():
    respx.get("http://example.com/page").mock(return_value=httpx.Response(200, text="<p>hi</p>"))
    assert fetch_url_html("http://example.com/page") == "<p>hi</p>"


@respx.mock
def test_fetch_url_html_raises_on_non_200():
    respx.get("http://example.com/missing").mock(return_value=httpx.Response(404))
    with pytest.raises(FetchError, match="HTTP 404"):
        fetch_url_html("http://example.com/missing")


@respx.mock
def test_fetch_url_html_raises_fetch_error_on_connection_failure():
    respx.get("http://example.com/down").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(FetchError):
        fetch_url_html("http://example.com/down")


def test_extract_text_or_raise_returns_text_when_enough_content():
    html = "<html><body><p>" + ("word " * 50) + "</p></body></html>"
    assert "word" in extract_text_or_raise(html)


def test_extract_text_or_raise_raises_no_content_error_when_thin():
    html = "<html><body><noscript>Enable JavaScript</noscript></body></html>"
    with pytest.raises(NoContentError):
        extract_text_or_raise(html)


def test_extract_page_title_returns_title_tag_text():
    html = "<html><head><title>Frontend Developer Jobs in Oslo | Glassdoor</title></head><body></body></html>"
    assert extract_page_title(html) == "Frontend Developer Jobs in Oslo | Glassdoor"


def test_extract_page_title_falls_back_to_h1_when_no_title():
    html = "<html><body><h1>Careers at Acme</h1></body></html>"
    assert extract_page_title(html) == "Careers at Acme"


def test_extract_page_title_prefers_title_over_h1():
    html = "<html><head><title>Page Title</title></head><body><h1>H1 Text</h1></body></html>"
    assert extract_page_title(html) == "Page Title"


def test_extract_page_title_returns_none_when_neither_present():
    html = "<html><body><p>No heading here.</p></body></html>"
    assert extract_page_title(html) is None


def test_extract_page_title_returns_none_for_blank_title():
    html = "<html><head><title>   </title></head><body></body></html>"
    assert extract_page_title(html) is None


@respx.mock
def test_fetch_url_html_rewrites_linkedin_view_url_to_guest_endpoint():
    guest = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/4421669844"
    route = respx.get(guest).mock(return_value=httpx.Response(200, text="<p>posting</p>"))
    body = fetch_url_html(
        "https://fr.linkedin.com/jobs/view/ml-engineer-at-acme-4421669844?position=1"
    )
    assert route.called
    assert body == "<p>posting</p>"


def test_min_article_length_is_1200():
    assert MIN_ARTICLE_LENGTH == 1200


def test_text_length_counts_stripped_extracted_text():
    html = "<html><body><h1>Hi</h1><p>  there  </p></body></html>"
    # "Hi\nthere" -> whitespace between is kept by the separator, ends stripped
    assert text_length(html) == len(extract_text(html).strip())
    assert text_length("<html><body>   </body></html>") == 0


def test_is_substantially_richer_true_when_candidate_has_min_content_length_more_text():
    baseline = "<html><body><p>short shell</p></body></html>"
    richer = "<html><body><p>" + ("real description text " * 40) + "</p></body></html>"
    assert is_substantially_richer(richer, baseline)


def test_is_substantially_richer_false_when_similar_length():
    a = "<html><body><p>" + ("some text here " * 30) + "</p></body></html>"
    b = "<html><body><p>" + ("other text now " * 30) + "</p></body></html>"
    assert not is_substantially_richer(a, b)


def test_is_substantially_richer_boundary_at_min_content_length():
    baseline_html = "<html><body><p>" + ("x" * 100) + "</p></body></html>"
    at = "<html><body><p>" + ("y" * (100 + MIN_CONTENT_LENGTH)) + "</p></body></html>"
    below = "<html><body><p>" + ("y" * (100 + MIN_CONTENT_LENGTH - 1)) + "</p></body></html>"
    assert is_substantially_richer(at, baseline_html)
    assert not is_substantially_richer(below, baseline_html)


def test_extract_readable_text_drops_cookie_and_consent_containers():
    html = (
        "<html><body>"
        "<div class='cookie-banner'>We use cookies. " + ("blah " * 200) + "</div>"
        "<div id='onetrust-consent-sdk'>" + ("consent copy " * 200) + "</div>"
        "<div class='job-body'><h1>Staff Engineer</h1><p>Own the platform roadmap.</p></div>"
        "</body></html>"
    )
    text = extract_readable_text(html)
    assert "Staff Engineer" in text and "Own the platform roadmap." in text
    assert "We use cookies" not in text
    assert "consent copy" not in text


def test_extract_readable_text_also_drops_script_nav_footer():
    html = (
        "<html><body><nav>Home About</nav><script>var x=1;</script>"
        "<div class='content'><p>Real posting body here.</p></div>"
        "<footer>© 2026</footer></body></html>"
    )
    text = extract_readable_text(html)
    assert "Real posting body here." in text
    assert "Home About" not in text and "var x=1" not in text and "© 2026" not in text


def test_extract_text_is_unchanged_by_boilerplate_stripping():
    # extract_text stays pure — only extract_readable_text strips.
    html = "<html><body><div class='cookie-banner'>cookies</div><p>body</p></body></html>"
    assert "cookies" in extract_text(html)
