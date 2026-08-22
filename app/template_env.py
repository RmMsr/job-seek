from __future__ import annotations
from pathlib import Path
from fastapi.templating import Jinja2Templates
from app.markdown_render import render_markdown, render_markdown_inline, markdown_to_text
from app.dates import time_ago

_SCORE_STOPS = [(0.0, "low"), (0.40, "mid"), (0.80, "high"), (1.0, "top")]


def score_style(score: float) -> str:
    """Inline custom properties that continuously blend a 0..1 score between
    the two nearest stops of the --fit-* scale, instead of snapping at
    fixed thresholds. The scale trends toward red at the top end."""
    s = max(0.0, min(1.0, score))
    for (lo_s, lo_name), (hi_s, hi_name) in zip(_SCORE_STOPS, _SCORE_STOPS[1:]):
        if s <= hi_s:
            t = 0.0 if hi_s == lo_s else (s - lo_s) / (hi_s - lo_s)
            break
    blend = round(t * 100, 1)
    return (
        f"--from:var(--fit-{lo_name});--to:var(--fit-{hi_name});"
        f"--from-tint:var(--fit-{lo_name}-tint);--to-tint:var(--fit-{hi_name}-tint);"
        f"--blend:{blend}%;"
    )


templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))
templates.env.filters["markdown"] = render_markdown
templates.env.filters["markdown_inline"] = render_markdown_inline
templates.env.filters["markdown_text"] = markdown_to_text
templates.env.filters["time_ago"] = time_ago
templates.env.filters["score_style"] = score_style
