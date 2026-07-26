from __future__ import annotations
import html
import markdown
from markupsafe import Markup


def render_markdown(text: str | None) -> Markup:
    if not text:
        return Markup("")
    escaped = html.escape(text)
    return Markup(markdown.markdown(escaped, extensions=["nl2br"]))


def render_markdown_inline(text: str | None) -> Markup:
    """Like render_markdown, but unwraps a single enclosing <p> so the
    result is safe to place inside an inline element (e.g. <span>)."""
    rendered = str(render_markdown(text))
    if rendered.startswith("<p>") and rendered.endswith("</p>") and rendered.count("<p>") == 1:
        rendered = rendered[len("<p>"):-len("</p>")]
    return Markup(rendered)
