from __future__ import annotations
from fastapi.templating import Jinja2Templates
from app.markdown_render import render_markdown, render_markdown_inline, markdown_to_text
from app.dates import time_ago

templates = Jinja2Templates(directory="app/templates")
templates.env.filters["markdown"] = render_markdown
templates.env.filters["markdown_inline"] = render_markdown_inline
templates.env.filters["markdown_text"] = markdown_to_text
templates.env.filters["time_ago"] = time_ago
