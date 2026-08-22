from __future__ import annotations
import sqlite3
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from app.deps import get_db
from app.db import queries as q
from app.pipeline import run_fetch
from app.task_engine import register_task_kind
from app.template_env import templates

router = APIRouter()


def _fetch_panel_context(conn: sqlite3.Connection) -> dict:
    sources = [s for s in q.get_sources(conn) if s["fetcher_type"] != "manual"]
    runs = q.get_recent_fetch_runs(conn)
    runs_by_source = {}
    for run in runs:
        sid = run["source_id"]
        if sid not in runs_by_source:
            runs_by_source[sid] = run
    stats_by_source = q.get_fetch_stats_by_source(conn)
    return {"sources": sources, "runs_by_source": runs_by_source, "stats_by_source": stats_by_source}


def _auth_error_result(conn: sqlite3.Connection, source: dict, run_id: int) -> dict:
    run = q.get_fetch_run(conn, run_id)
    if run and run["auth_error"]:
        return {
            "needs_action": True,
            "action_message": f"'{source['name']}' needs you to reconnect Slack to keep fetching",
            "action_link": f"/sources#source-row-{source['id']}",
        }
    return {}


@register_task_kind("fetch_source")
def _task_fetch_source(conn, client, model, config, params):
    source = q.get_source(conn, params["source_id"])
    if source is None:
        return {"html_chunks": [], "notices": []}
    gen = run_fetch(source, conn, client, model, config.browser_profile_dir)
    fetch_result = None
    try:
        while True:
            yield next(gen)
    except StopIteration as stop:
        fetch_result = stop.value
    result = {"html_chunks": [], "notices": []}
    if fetch_result is not None:
        result.update(_auth_error_result(conn, source, fetch_result.run_id))
    return result


@router.get("/fetch", response_class=HTMLResponse)
def fetch_panel(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    return templates.TemplateResponse(request, "fetch/panel.html", _fetch_panel_context(conn))


@router.post("/fetch/all")
def trigger_fetch_all(conn: sqlite3.Connection = Depends(get_db)):
    sources = [s for s in q.get_sources(conn) if s["fetcher_type"] != "manual" and s["enabled"]]
    if not sources:
        return {"skipped": True, "message": "No sources to fetch."}
    tasks = [q.enqueue_task(conn, kind="fetch_source", params={"source_id": s["id"]}) for s in sources]
    return {"task_ids": [t["id"] for t in tasks]}


@router.post("/fetch/{source_id}")
def trigger_fetch(source_id: int, conn: sqlite3.Connection = Depends(get_db)):
    source = q.get_source(conn, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    task = q.enqueue_task(conn, kind="fetch_source", params={"source_id": source_id})
    return {"task_id": task["id"], "already_active": task["already_active"]}
