from __future__ import annotations
import sqlite3
import openai
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from app.deps import get_db, get_ai_client, get_model
from app.db import queries as q
from app.pipeline import run_reprocess_job
from app.template_env import templates

router = APIRouter()


def _enrich_jobs(conn: sqlite3.Connection, jobs: list[dict]) -> list[dict]:
    sources = {s["id"]: s for s in q.get_sources(conn)}
    for job in jobs:
        job["source_name"] = sources.get(job["source_id"], {}).get("name", "")
    return jobs


def _get_filtered_jobs(conn: sqlite3.Connection, status: str | None, content_type: str | None) -> list[dict]:
    if status is None and content_type is None:
        return q.get_jobs(conn, status="new")
    return q.get_jobs(conn, status=status, content_type=content_type)


@router.get("/jobs", response_class=HTMLResponse)
def job_list(
    request: Request,
    status: str | None = None,
    content_type: str | None = None,
    conn: sqlite3.Connection = Depends(get_db),
):
    jobs = _enrich_jobs(conn, _get_filtered_jobs(conn, status, content_type))
    counts = q.get_job_counts(conn)
    scenarios = q.get_scenarios(conn)
    effective_status = status if (status is not None or content_type is not None) else "new"
    return templates.TemplateResponse(
        request, "jobs/list.html",
        {"jobs": jobs, "counts": counts, "scenarios": scenarios, "status": effective_status, "content_type": content_type},
    )


@router.get("/jobs/{job_id}/expand", response_class=HTMLResponse)
def job_expand(job_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    job = q.get_job(conn, job_id)
    sources = {s["id"]: s for s in q.get_sources(conn)}
    job["source_name"] = sources.get(job["source_id"], {}).get("name", "")
    scenarios = q.get_scenarios(conn)
    job_scores = q.get_job_scores(conn, job_id)
    return templates.TemplateResponse(request, "jobs/_feedback.html", {"job": job, "scenarios": scenarios, "job_scores": job_scores})


@router.get("/jobs/{job_id}/collapse", response_class=HTMLResponse)
def job_collapse(job_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    job = q.get_job(conn, job_id)
    sources = {s["id"]: s for s in q.get_sources(conn)}
    job["source_name"] = sources.get(job["source_id"], {}).get("name", "")
    return templates.TemplateResponse(request, "jobs/_row.html", {"job": job})


@router.post("/jobs/{job_id}/feedback", response_class=HTMLResponse)
def job_feedback(
    job_id: int,
    request: Request,
    status: str = Form(...),
    note: str | None = Form(None),
    feedback_scenario_id: int = Form(...),
    conn: sqlite3.Connection = Depends(get_db),
):
    q.update_job_feedback(conn, job_id, status, note, feedback_scenario_id)
    return HTMLResponse(content="", status_code=200)


@router.post("/jobs/{job_id}/reset")
def job_reset(
    job_id: int,
    conn: sqlite3.Connection = Depends(get_db),
    client: openai.OpenAI = Depends(get_ai_client),
    model: str = Depends(get_model),
):
    job = q.get_job(conn, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    scenarios = q.get_scenarios(conn)
    profile = q.get_profile(conn)

    def stream():
        gen = run_reprocess_job(conn, client, model, job, scenarios, profile)
        try:
            while True:
                yield next(gen) + "\n"
        except StopIteration:
            pass

    return StreamingResponse(stream(), media_type="text/plain")


@router.post("/jobs/bulk-reset")
def job_bulk_reset(
    job_ids: list[int] = Form(...),
    conn: sqlite3.Connection = Depends(get_db),
    client: openai.OpenAI = Depends(get_ai_client),
    model: str = Depends(get_model),
):
    scenarios = q.get_scenarios(conn)
    profile = q.get_profile(conn)

    def stream():
        yield f"Resetting {len(job_ids)} job(s)\n"
        for idx, job_id in enumerate(job_ids, start=1):
            job = q.get_job(conn, job_id)
            if not job:
                continue
            prefix = f"[{idx}/{len(job_ids)}] "
            gen = run_reprocess_job(conn, client, model, job, scenarios, profile, progress_prefix=prefix)
            try:
                while True:
                    yield next(gen) + "\n"
            except StopIteration:
                pass
        yield f"Reset complete: {len(job_ids)} job(s) reprocessed\n"

    return StreamingResponse(stream(), media_type="text/plain")


@router.post("/jobs/bulk-feedback", response_class=HTMLResponse)
def job_bulk_feedback(
    request: Request,
    job_ids: list[int] = Form(...),
    status: str = Form(...),
    note: str | None = Form(None),
    feedback_scenario_id: str = Form(""),
    status_filter: str | None = Form(None),
    content_type_filter: str | None = Form(None),
    conn: sqlite3.Connection = Depends(get_db),
):
    for job_id in job_ids:
        scenario_id = (
            int(feedback_scenario_id) if feedback_scenario_id
            else q.get_job(conn, job_id)["best_scenario_id"]
        )
        q.update_job_feedback(conn, job_id, status, note, scenario_id)

    status_filter = status_filter or None
    content_type_filter = content_type_filter or None
    jobs = _enrich_jobs(conn, _get_filtered_jobs(conn, status_filter, content_type_filter))
    counts = q.get_job_counts(conn)
    scenarios = q.get_scenarios(conn)
    return templates.TemplateResponse(
        request, "jobs/_content.html",
        {"jobs": jobs, "counts": counts, "scenarios": scenarios},
    )
