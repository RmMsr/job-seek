from __future__ import annotations
import sqlite3
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from app.deps import get_db
from app.db import queries as q
from app.template_env import templates

router = APIRouter()


def _enrich_jobs(conn: sqlite3.Connection, jobs: list[dict]) -> list[dict]:
    sources = {s["id"]: s for s in q.get_sources(conn)}
    for job in jobs:
        job["source_name"] = sources.get(job["source_id"], {}).get("name", "")
    return jobs


@router.get("/", response_class=HTMLResponse)
def job_list(
    request: Request,
    status: str | None = None,
    content_type: str | None = None,
    conn: sqlite3.Connection = Depends(get_db),
):
    if status is None and content_type is None:
        jobs = q.get_jobs(conn, status="new")
    else:
        jobs = q.get_jobs(conn, status=status, content_type=content_type)
    jobs = _enrich_jobs(conn, jobs)
    counts = q.get_job_counts(conn)
    return templates.TemplateResponse(request, "jobs/list.html", {"jobs": jobs, "counts": counts})


@router.get("/jobs/{job_id}/expand", response_class=HTMLResponse)
def job_expand(job_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    job = q.get_job(conn, job_id)
    sources = {s["id"]: s for s in q.get_sources(conn)}
    job["source_name"] = sources.get(job["source_id"], {}).get("name", "")
    scenarios = q.get_scenarios(conn)
    return templates.TemplateResponse(request, "jobs/_feedback.html", {"job": job, "scenarios": scenarios})


@router.post("/jobs/{job_id}/feedback", response_class=HTMLResponse)
def job_feedback(
    job_id: int,
    request: Request,
    status: str = Form(...),
    note: str = Form(...),
    feedback_scenario_id: int = Form(...),
    conn: sqlite3.Connection = Depends(get_db),
):
    q.update_job_feedback(conn, job_id, status, note, feedback_scenario_id)
    return HTMLResponse(content="", status_code=200)
