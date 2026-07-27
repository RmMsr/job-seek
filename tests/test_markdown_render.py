from app.markdown_render import render_markdown, render_markdown_inline, markdown_to_text


def test_render_markdown_bold():
    assert str(render_markdown("**bold**")) == "<p><strong>bold</strong></p>"


def test_render_markdown_empty():
    assert str(render_markdown("")) == ""
    assert str(render_markdown(None)) == ""


def test_render_markdown_escapes_html():
    result = str(render_markdown("<script>alert(1)</script>"))
    assert "<script>" not in result
    assert "&lt;script&gt;" in result


def test_render_markdown_single_newline_breaks_line():
    result = str(render_markdown("line one\nline two"))
    assert "<br" in result


def test_render_markdown_inline_unwraps_single_paragraph():
    assert str(render_markdown_inline("Must be remote")) == "Must be remote"


def test_render_markdown_inline_keeps_formatting():
    assert str(render_markdown_inline("Must be **senior**")) == "Must be <strong>senior</strong>"


def test_markdown_to_text_strips_tags():
    assert markdown_to_text("**Role:** ML Engineer") == "Role: ML Engineer"


def test_markdown_to_text_truncation_leaves_no_stray_markers():
    # Truncating the raw source mid-token (as the row preview used to do)
    # could leave a stray "**" visible; truncating post-render avoids that.
    text = "**Key requirements** include Python and a love of edge devices"
    truncated = markdown_to_text(text)[:12]
    assert "**" not in truncated


def test_markdown_to_text_empty():
    assert markdown_to_text("") == ""
    assert markdown_to_text(None) == ""
