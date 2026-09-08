from __future__ import annotations
import os
import shutil
import subprocess
import tempfile
from app.cv.sanitize import sanitize_cv_markdown

_CLI = "doc-write-cli"
_TIMEOUT = 60


class CvRenderError(Exception):
    pass


def doc_write_available() -> bool:
    return shutil.which(_CLI) is not None


def _run(markdown: str, css: str, out_name: str, work: str, fmt: str) -> str:
    if not doc_write_available():
        raise CvRenderError(f"{_CLI} is not installed")
    md_path = os.path.join(work, "cv.md")
    out_path = os.path.join(work, out_name)
    with open(md_path, "w") as f:
        f.write(sanitize_cv_markdown(markdown))
    cmd = [_CLI, f"--{fmt}", md_path, out_path]
    if css.strip():
        css_path = os.path.join(work, "cv.css")
        with open(css_path, "w") as f:
            f.write(css)
        cmd += ["--css", css_path]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, timeout=_TIMEOUT, cwd=work,
        env={"PATH": os.environ.get("PATH", "")},
    )
    if proc.returncode != 0:
        raise CvRenderError((proc.stderr or proc.stdout or "doc-write-cli failed").strip()[:500])
    return out_path


def render_pdf(markdown: str, css: str) -> bytes:
    with tempfile.TemporaryDirectory(prefix="cv-render-") as work:
        out = _run(markdown, css, "cv.pdf", work, "pdf")
        with open(out, "rb") as f:
            return f.read()


_DIFF_CSS = """
.cvd-del { color: #b3261e; text-decoration: line-through; }
.cvd-ins { color: #0a7d33; text-decoration: none; background: #e9f6ec; }
.cvd-block { display: block; padding: 0.15em 0.5em; margin: 0.15em 0; }
.cvd-added { background: #fff3d6; }
.cvd-move, .cvd-new {
  font-size: 0.78em; font-weight: 700; padding: 0 0.4em; margin-left: 0.35em;
  border-radius: 0.5em; vertical-align: 0.08em; white-space: nowrap;
}
.cvd-move { color: #444; background: #ececec; border: 1px solid #ccc; cursor: help; }
.cvd-move.cvd-up   { color: #14396b; background: #e1ecfb; border-color: #9fbde6; }
.cvd-move.cvd-down { color: #5c3a1e; background: #f0e4d4; border-color: #cbab82; }
.cvd-new  { color: #7a5c00; background: #ffe9a8; border: 1px solid #e0c366; }
/* section move note — its own line, hugging the heading above it */
.cvd-hnote {
  display: inline-block; font-size: 0.72em; font-weight: 700; cursor: help;
  margin: -0.35em 0 0.7em; padding: 0.05em 0.55em; border-radius: 0.5em;
  color: #444; background: #ececec; border: 1px solid #ccc;
}
.cvd-hnote.cvd-up   { color: #14396b; background: #e1ecfb; border-color: #9fbde6; }
.cvd-hnote.cvd-down { color: #5c3a1e; background: #f0e4d4; border-color: #cbab82; }
/* inline heading correction: a redline span appended to the heading */
.cvd-hnote-edit { font-size: 0.82em; font-weight: 400; margin-left: 0.3em; }
/* a whole removed section: struck heading in the heading font + folded body */
.cvd-gone { margin: 0.6em 0 1em; }
.cvd-gone-h { color: #b3261e; font-weight: 700; line-height: 1.2; }
.cvd-gone-h del { text-decoration: line-through; }
.cvd-gone-h2 { font-size: 1.4em; }
.cvd-gone-h3 { font-size: 1.15em; }
.cvd-gone-d { font-size: 0.9em; margin: 0.2em 0 0 0.4em; }
.cvd-gone-d > summary { cursor: pointer; color: #b3261e; list-style: revert; }
.cvd-gone-line { margin: 0.2em 0; }
.cvd-gone-line del { text-decoration: line-through; color: #8a6d6d; }
/* Change gutter. Every main-content element keeps a fixed right-hand gutter
   (and the sidebar a smaller left one) whether or not it changed, so a marker
   never reflows anything when it appears. A touched line then gets a 3px purple
   bar in that gutter plus a purple fade back into the text. One colour for
   every kind of change — redline, move, struck / added / removed block. */
article > :not(.sidebar) { padding-right: 1.2em; }
.sidebar :is(li, p, h1, h2, h3, h4, h5, h6) { padding-left: 0.8em; }

:is(li,p):has(.cvd-del,.cvd-ins,.cvd-move,.cvd-new),
:is(h1,h2,h3,h4,h5,h6):has(.cvd-hnote-edit),
:is(h1,h2,h3,h4,h5,h6):has(+ .cvd-hnote),
del.cvd-block, ins.cvd-block, .cvd-added, .cvd-gone {
  box-shadow: inset -3px 0 0 #7c3aed;
  background-image: linear-gradient(to left, rgba(124,58,237,0.20), transparent 50%);
}
.sidebar :is(li,p):has(.cvd-del,.cvd-ins,.cvd-move,.cvd-new),
.sidebar del.cvd-block, .sidebar ins.cvd-block, .sidebar .cvd-added {
  box-shadow: inset 3px 0 0 #7c3aed;
  background-image: linear-gradient(to right, rgba(124,58,237,0.20), transparent 50%);
}
/* a block already inside a marked <li>/<p> would double the bar */
:is(li,p) > .cvd-block { box-shadow: none; background-image: none; }
"""


def render_diff_html(markdown: str, css: str) -> str:
    """Continuous single-page HTML for the Differences tab — the tailored CV
    with base->tailored diff marks woven in. ``_DIFF_CSS`` is a trusted constant
    appended to the user's CSS (it bypasses ``validate_css``)."""
    with tempfile.TemporaryDirectory(prefix="cv-diff-") as work:
        out = _run(markdown, (css or "") + "\n" + _DIFF_CSS, "cv.html", work, "html")
        with open(out) as f:
            return f.read()


def render_preview_html(markdown: str, css: str) -> str:
    """A self-contained, paginated HTML preview document for iframe embedding.
    doc-write-cli's --html-pages writes a paginated preview that approximates
    the PDF page by page, with built-in prev/next controls."""
    with tempfile.TemporaryDirectory(prefix="cv-render-") as work:
        out = _run(markdown, css, "cv.html", work, "html-pages")
        with open(out) as f:
            return f.read()
