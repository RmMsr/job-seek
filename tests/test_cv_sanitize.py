from app.cv.sanitize import sanitize_cv_markdown, validate_css


def test_strips_script_and_style_blocks():
    out = sanitize_cv_markdown("# CV\n\n<script>alert(1)</script>\n\n<style>*{}</style>\n\n- ok\n")
    assert "<script" not in out
    assert "<style" not in out
    assert "- ok" in out


def test_removes_event_handlers_and_inline_style():
    out = sanitize_cv_markdown('<div onclick="x()" style="position:fixed">hi</div>')
    assert "onclick" not in out
    assert "style=" not in out
    assert "hi" in out


def test_neutralises_remote_and_file_urls():
    out = sanitize_cv_markdown('<img src="https://evil/x.png"> <a href="file:///etc/passwd">p</a>')
    assert "https://evil" not in out
    assert "file:" not in out


def test_keeps_aside_and_basic_formatting():
    src = '<aside class="sidebar">\n\n## Skills\n\n- **Python**\n\n</aside>'
    out = sanitize_cv_markdown(src)
    assert "<aside" in out
    assert "Python" in out


def test_frontmatter_passthrough():
    src = "---\nheader: My CV\n---\n\n# CV\n\n- ok\n"
    out = sanitize_cv_markdown(src)
    assert out.startswith("---\nheader: My CV\n---")


def test_blockquote_markers_survive_sanitisation():
    # BeautifulSoup escapes a bare ">" — a line-leading one is blockquote
    # syntax and must come back, or the renderer never sees the quote.
    out = sanitize_cv_markdown("## T\n\n> a testimonial line\n> second line\n\n- ok\n")
    assert "> a testimonial line" in out
    assert "> second line" in out
    assert "&gt;" not in out


def test_nested_blockquote_marker_restored():
    assert sanitize_cv_markdown("> > deep\n") == "> > deep\n"


def test_inline_gt_and_lt_stay_escaped():
    out = sanitize_cv_markdown("templates use a < b and c > d comparisons\n")
    assert "&lt;" in out and "&gt;" in out  # not line-leading -> still escaped


def test_validate_css_rejects_import_and_remote_url():
    assert validate_css("@import url('http://x/a.css');") is not None
    assert validate_css("body { background: url(https://x/a.png); }") is not None
    assert validate_css("body { background: url(data:image/png;base64,AAAA); }") is None
    assert validate_css("p { color: red; }") is None


def test_svg_data_uri_is_neutralised():
    out = sanitize_cv_markdown('<img src="data:image/svg+xml;utf8,<svg onload=x>">')
    assert "svg" not in out.lower() and "onload" not in out
    assert "data:image/svg" not in out.lower()


def test_raster_data_uri_still_allowed():
    out = sanitize_cv_markdown('<img src="data:image/png;base64,AAAA">')
    assert "data:image/png" in out


def test_protocol_relative_url_neutralised():
    out = sanitize_cv_markdown('<a href="//evil.com/x">y</a>')
    assert "//evil.com" not in out
    assert 'href="#"' in out


def test_single_slash_absolute_path_allowed():
    out = sanitize_cv_markdown('<a href="/jobs/1">y</a>')
    assert 'href="/jobs/1"' in out


def test_data_uri_with_trailing_junk_media_type_neutralised():
    out = sanitize_cv_markdown('<img src="data:image/pngx;base64,AAAA">')
    assert "pngx" not in out
    assert 'src="#"' in out


def test_markdown_image_remote_url_neutralised():
    out = sanitize_cv_markdown("# CV\n\n![logo](http://evil/x.png)\n")
    assert "evil" not in out
    assert "![logo](#)" in out


def test_markdown_link_file_url_neutralised():
    out = sanitize_cv_markdown('# CV\n\n[secret](file:///etc/passwd "t")\n')
    assert "file:" not in out
    assert "[secret](#)" in out


def test_markdown_link_relative_kept():
    out = sanitize_cv_markdown("# CV\n\n[settings](/cv) and [top](#top)\n")
    assert "[settings](/cv)" in out
    assert "[top](#top)" in out


def test_bare_https_autolink_kept():
    out = sanitize_cv_markdown("# CV\n\nSee <https://example.com/x> for more.\n")
    # Re-expressed as an equivalent inline link so BeautifulSoup can't swallow it.
    assert "See [https://example.com/x](https://example.com/x) for more." in out


def test_bare_file_autolink_neutralised():
    out = sanitize_cv_markdown("# CV\n\nSee <file:///etc/passwd> for more.\n")
    assert "file:" not in out
    assert "See # for more." in out


def test_markdown_https_link_kept_in_heading():
    src = "### [2025 – now] [Relagent](https://gitlab.com/RmMsr/relagent), the private AI assistant\n"
    out = sanitize_cv_markdown(src)
    assert src in out


def test_markdown_archive_link_kept():
    link = "[ict-cloud.com](https://web.archive.org/web/20130910081717/http://www.ict-cloud.com/)"
    out = sanitize_cv_markdown(f"### [2009 – 2013] Technical founder of {link}\n")
    assert link in out


def test_markdown_http_link_with_title_kept():
    out = sanitize_cv_markdown('[x](http://example.com "title")\n')
    assert '[x](http://example.com "title")' in out


def test_html_anchor_https_href_kept():
    out = sanitize_cv_markdown('<a href="https://example.com">x</a>')
    assert 'href="https://example.com"' in out


def test_html_img_https_src_still_neutralised():
    out = sanitize_cv_markdown('<img src="https://evil/x.png">')
    assert "evil" not in out
    assert 'src="#"' in out


def test_unsafe_markdown_links_still_neutralised():
    for dest in ("javascript:alert(1)", "//evil.com/x", "file:///etc/passwd"):
        out = sanitize_cv_markdown(f"[x]({dest})\n")
        assert "[x](#)" in out, dest


def test_markdown_image_https_still_neutralised():
    out = sanitize_cv_markdown("![logo](https://evil/x.png)\n")
    assert "evil" not in out
    assert "![logo](#)" in out


def test_validate_css_ignores_url_in_comment():
    assert validate_css("/* background: url(http://x/a.png) */\np { color: red; }") is None
    assert validate_css("/* @import 'http://x/a.css'; */\np { color: red; }") is None


def test_reference_link_definition_remote_url_neutralised():
    out = sanitize_cv_markdown("# CV\n\n[r]: http://evil/x.png\n\n![logo][r]\n")
    assert "evil" not in out
    assert "[r]: #" in out


def test_reference_link_definition_file_url_neutralised():
    out = sanitize_cv_markdown("# CV\n\n[r]: <file:///etc/passwd>\n\n[secret][r]\n")
    assert "file:" not in out
    assert "[r]: #" in out


def test_reference_link_definition_relative_kept():
    out = sanitize_cv_markdown("# CV\n\n[assets]: /assets/logo.png\n\n![logo][assets]\n")
    assert "[assets]: /assets/logo.png" in out


def test_reference_definition_shaped_prose_is_untouched():
    # "[label]: bare words" is not a fetchable URL — do not mangle it.
    src = "# CV\n\n[Note]: I prefer remote work and short meetings\n"
    out = sanitize_cv_markdown(src)
    assert "[Note]: I prefer remote work and short meetings" in out


def test_reference_definition_https_used_only_by_link_kept():
    out = sanitize_cv_markdown("# CV\n\n[Relagent][r]\n\n[r]: https://gitlab.com/RmMsr/relagent\n")
    assert "[r]: https://gitlab.com/RmMsr/relagent" in out


def test_reference_definition_https_used_by_image_neutralised():
    for usage in ("![logo][R]", "![r][]", "![r]"):
        out = sanitize_cv_markdown(f"# CV\n\n{usage}\n\n[r]: https://evil/x.png\n")
        assert "evil" not in out, usage
        assert "[r]: #" in out, usage
