import os
import shutil
import pytest
from app.cv.render import (
    doc_write_available, render_pdf, render_preview_html, render_diff_html, CvRenderError,
)

_HAS = shutil.which("doc-write-cli") is not None
needs_docwrite = pytest.mark.skipif(not _HAS, reason="doc-write-cli not installed")


def test_available_reflects_path(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _n: None)
    assert doc_write_available() is False
    monkeypatch.setattr(shutil, "which", lambda _n: "/usr/bin/doc-write-cli")
    assert doc_write_available() is True


def test_render_pdf_raises_when_binary_missing(monkeypatch):
    monkeypatch.setattr("app.cv.render.doc_write_available", lambda: False)
    with pytest.raises(CvRenderError):
        render_pdf("# CV\n\n- x\n", "")


@needs_docwrite
def test_render_pdf_produces_nonempty_bytes():
    data = render_pdf("# CV\n\n- Did the thing.\n", "p { color: #333; }")
    assert data[:4] == b"%PDF"
    assert len(data) > 500


def test_render_preview_html_raises_when_binary_missing(monkeypatch):
    monkeypatch.setattr("app.cv.render.doc_write_available", lambda: False)
    with pytest.raises(CvRenderError):
        render_preview_html("# CV\n\n- x\n", "")


@needs_docwrite
def test_render_preview_html_is_self_contained_doc():
    html = render_preview_html("# CV\n\n- Did the thing.\n", "p { color: #333; }")
    assert html.lstrip().startswith("<!DOCTYPE html>")
    assert "Did the thing." in html
    assert "paged" in html.lower()  # inlined pagination engine


def test_render_diff_html_raises_when_binary_missing(monkeypatch):
    monkeypatch.setattr("app.cv.render.doc_write_available", lambda: False)
    with pytest.raises(CvRenderError):
        render_diff_html("# CV\n\n- x\n", "")


def test_diff_css_has_a_change_gutter():
    from app.cv.render import _DIFF_CSS
    assert "box-shadow: inset -3px 0 0 #7c3aed" in _DIFF_CSS   # one purple bar, main = right
    assert "box-shadow: inset 3px 0 0 #7c3aed" in _DIFF_CSS    # sidebar = left
    assert "article > :not(.sidebar) { padding-right: 1.2em" in _DIFF_CSS  # fixed gutter
    assert ".sidebar :is(li, p, h1, h2, h3, h4, h5, h6) { padding-left: 0.8em" in _DIFF_CSS
    assert "transparent 50%)" in _DIFF_CSS
    # no per-kind colours any more
    assert "#b3261e" not in _DIFF_CSS.split("Change gutter")[1]


@needs_docwrite
def test_render_diff_html_is_continuous_and_styled():
    md = ('<p class="cvd-digest">1 bullet dropped</p>\n\n'
          '# CV\n\n- kept\n- <del class="cvd-del cvd-block">gone</del>\n')
    html = render_diff_html(md, "body{font-family:serif}")
    assert "<!doctype html" in html.lower()
    assert ".cvd-del" in html          # _DIFF_CSS injected
    assert "cvd-digest" in html
    # --html wraps content in a single .page div (for page-background tinting)
    # but stays continuous — no pagination controls/multiple physical pages.
    assert html.count('class="page"') == 1
    assert "paged_page" not in html
    assert "dw-preview-controls" not in html


@needs_docwrite
def test_render_strips_script_before_doc_write():
    # a <script> in the markdown must not reach the renderer; still produces a PDF
    data = render_pdf("# CV\n\n<script>bad()</script>\n\n- ok\n", "")
    assert data[:4] == b"%PDF"
