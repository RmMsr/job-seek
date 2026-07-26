from __future__ import annotations
from fastapi.templating import Jinja2Templates
from app.markdown_render import render_markdown, render_markdown_inline

templates = Jinja2Templates(directory="app/templates")
templates.env.filters["markdown"] = render_markdown
templates.env.filters["markdown_inline"] = render_markdown_inline
