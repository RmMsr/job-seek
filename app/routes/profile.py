from __future__ import annotations
import sqlite3
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from app.deps import get_db
from app.db import queries as q

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/profile", response_class=HTMLResponse)
def profile_page(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    content = q.get_profile(conn)
    return templates.TemplateResponse(request, "profile/index.html", {"content": content})


@router.post("/profile", response_class=HTMLResponse)
def profile_save(
    request: Request,
    content: str = Form(...),
    conn: sqlite3.Connection = Depends(get_db),
):
    q.upsert_profile(conn, content)
    return templates.TemplateResponse(
        request, "profile/index.html", {"content": content, "saved": True}
    )
