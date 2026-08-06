from __future__ import annotations
import sqlite3
import openai
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from app.deps import get_db, get_ai_client, get_model
from app.db import queries as q
from app.pipeline import run_reassess_fit
from app.template_env import templates

router = APIRouter()


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


@router.post("/profile/reassess-fit")
def reassess_fit(
    conn: sqlite3.Connection = Depends(get_db),
    client: openai.OpenAI = Depends(get_ai_client),
    model: str = Depends(get_model),
):
    def stream():
        gen = run_reassess_fit(conn, client, model)
        try:
            while True:
                yield next(gen) + "\n"
        except StopIteration:
            pass

    return StreamingResponse(stream(), media_type="text/plain")
