from app.markdown_render import render_markdown, render_markdown_inline


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
