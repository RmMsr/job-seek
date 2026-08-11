from app.fetchers.links import extract_links

_HTML = """
<html><body>
  <nav><a href="/about">About</a></nav>
  <article>
    <a href="/jobs/1">Senior Engineer</a>
    <a href="/jobs/2">  Staff Engineer  </a>
    <a href="https://other.example.com/jobs/3">External Posting</a>
    <a href="/jobs/1">Senior Engineer (duplicate link)</a>
  </article>
</body></html>
"""


def test_extract_links_resolves_relative_hrefs_absolute():
    links = extract_links(_HTML, "https://example.com/careers")
    hrefs = [href for href, _ in links]
    assert "https://example.com/jobs/1" in hrefs
    assert "https://example.com/about" in hrefs
    assert "https://other.example.com/jobs/3" in hrefs


def test_extract_links_dedupes_by_href():
    links = extract_links(_HTML, "https://example.com/careers")
    hrefs = [href for href, _ in links]
    assert hrefs.count("https://example.com/jobs/1") == 1


def test_extract_links_strips_anchor_text_whitespace():
    links = extract_links(_HTML, "https://example.com/careers")
    by_href = dict(links)
    assert by_href["https://example.com/jobs/2"] == "Staff Engineer"


def test_extract_links_caps_at_200():
    html = "<html><body>" + "".join(f'<a href="/jobs/{i}">Job {i}</a>' for i in range(250)) + "</body></html>"
    links = extract_links(html, "https://example.com/careers")
    assert len(links) == 200


def test_extract_links_ignores_hrefless_anchors():
    html = '<html><body><a name="top">Top</a><a href="/jobs/1">Job</a></body></html>'
    links = extract_links(html, "https://example.com/careers")
    assert [href for href, _ in links] == ["https://example.com/jobs/1"]
