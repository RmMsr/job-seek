from __future__ import annotations
import json
from collections.abc import Mapping
from urllib.parse import urlencode as _urlencode
from pathlib import Path
from fastapi.templating import Jinja2Templates
from app.markdown_render import render_markdown, render_markdown_inline, markdown_to_text
from app.dates import time_ago, age, duration, closing, closing_tone

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
templates.env.filters["age"] = age
templates.env.filters["duration"] = duration
templates.env.filters["closing"] = closing
templates.env.filters["closing_tone"] = closing_tone
templates.env.filters["score_style"] = score_style

_STATUS_ICONS = {
    "queued": "○",
    "running": "◔",
    "needs_action": "!",
    "done": "✓",
    "failed": "✗",
    "dismissed": "–",
    "cancelled": "⊘",
}


def status_icon(status: str) -> str:
    return _STATUS_ICONS.get(status, "•")


templates.env.filters["status_icon"] = status_icon


def short_url(url: str, limit: int = 50) -> str:
    """host + path + query with the scheme stripped, truncated with an ellipsis."""
    if not url:
        return ""
    stripped = url.split("://", 1)[-1]
    if len(stripped) <= limit:
        return stripped
    return stripped[: limit - 1] + "…"


templates.env.filters["short_url"] = short_url


def job_tags(value: str | None) -> list[dict]:
    """Decode a job row's tags_json column (see queries._GATE_SELECT)."""
    return json.loads(value) if value else []


templates.env.filters["job_tags"] = job_tags


_jinja_urlencode = templates.env.filters["urlencode"]


def urlencode_multi(value):
    """Jinja's urlencode, but list values in a mapping become repeated params
    (JobFilter.query_params() carries tag/not_tag lists)."""
    if isinstance(value, Mapping):
        return _urlencode(value, doseq=True)
    return _jinja_urlencode(value)


templates.env.filters["urlencode"] = urlencode_multi
