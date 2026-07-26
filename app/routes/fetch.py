from __future__ import annotations
import sqlite3
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from app.deps import get_db, get_ai_client, get_model, get_config
from app.db import queries as q
from app.pipeline import run_fetch
from app.template_env import templates
import openai

router = APIRouter()


@router.get("/fetch", response_class=HTMLResponse)
def fetch_panel(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    sources = q.get_sources(conn)
    runs = q.get_recent_fetch_runs(conn)
    runs_by_source = {}
    for run in runs:
        sid = run["source_id"]
        if sid not in runs_by_source:
            runs_by_source[sid] = run
    return templates.TemplateResponse(
        request,
        "fetch/panel.html",
        {"sources": sources, "runs_by_source": runs_by_source},
    )


@router.post("/fetch/{source_id}")
def trigger_fetch(
    source_id: int,
    conn: sqlite3.Connection = Depends(get_db),
    client: openai.OpenAI = Depends(get_ai_client),
    model: str = Depends(get_model),
    config=Depends(get_config),
):
    source = q.get_source(conn, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    def stream():
        gen = run_fetch(source, conn, client, model, config.browser_profile_dir)
        try:
            while True:
                yield next(gen) + "\n"
        except StopIteration:
            pass

    return StreamingResponse(stream(), media_type="text/plain")
