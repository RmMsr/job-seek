from __future__ import annotations
import html
import re
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


def markdown_to_text(text: str | None) -> str:
    """Fully render then strip tags, for previews that get truncated —
    truncating the raw markdown source first can cut mid-syntax (e.g.
    mid **bold**) and leave stray formatting characters visible."""
    rendered = str(render_markdown(text))
    plain = re.sub(r"<[^>]+>", " ", rendered)
    return re.sub(r"\s+", " ", plain).strip()
