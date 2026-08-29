from __future__ import annotations
import sqlite3
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from app.deps import get_db
from app.db import queries as q
from app.template_env import templates

router = APIRouter()


@router.post("/inbox/{item_id}/resolve", response_class=HTMLResponse)
def inbox_resolve(item_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    q.resolve_inbox_item(conn, item_id)
    item = q.get_inbox_item(conn, item_id)
    if item is None:
        return HTMLResponse(content="")
    return templates.TemplateResponse(request, "home/_task_item.html", {"item": item, "done": True})
