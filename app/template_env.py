from __future__ import annotations
from pathlib import Path
from fastapi.templating import Jinja2Templates
from app.markdown_render import render_markdown, render_markdown_inline, markdown_to_text
from app.dates import time_ago

def score_class(score: float) -> str:
    """Map a 0..1 score to its design-system badge class."""
    if score >= 0.85:
        return "score-exceptional"
    if score >= 0.80:
        return "score-high"
    if score >= 0.40:
        return "score-mid"
    return "score-low"


templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))
templates.env.filters["markdown"] = render_markdown
templates.env.filters["markdown_inline"] = render_markdown_inline
templates.env.filters["markdown_text"] = markdown_to_text
templates.env.filters["time_ago"] = time_ago
templates.env.filters["score_class"] = score_class
