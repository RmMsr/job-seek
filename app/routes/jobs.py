from __future__ import annotations
import sqlite3
import openai
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from app.deps import get_db, get_ai_client, get_model
from app.db import queries as q
from app.pipeline import run_reprocess_job, run_pass_as_new
from app.template_env import templates

router = APIRouter()


def _enrich_jobs(conn: sqlite3.Connection, jobs: list[dict]) -> list[dict]:
    sources = {s["id"]: s for s in q.get_sources(conn)}
    for job in jobs:
        job["source_name"] = sources.get(job["source_id"], {}).get("name", "")
    return jobs


def _get_filtered_jobs(
    conn: sqlite3.Connection, status: str | None, content_type: str | None
) -> list[dict]:
    if status == "not_relevant":
        return q.get_jobs(conn, status="new", content_type="job_posting", gate_status="failed")
    if status is None and content_type is None:
        return q.get_jobs(conn, status="new", gate_status="passed")
    return q.get_jobs(conn, status=status, content_type=content_type)


def _filter_context(request: Request) -> dict:
    if "status" not in request.query_params and "content_type" not in request.query_params:
        return {}
    return {
        "filter_status": request.query_params.get("status") or None,
        "filter_content_type": request.query_params.get("content_type") or None,
    }


def _is_detail_page_request(request: Request) -> bool:
    return request.query_params.get("detail") == "1"


def _stale_badge(conn: sqlite3.Connection, job: dict, status: str | None, content_type: str | None) -> dict | None:
    filtered_ids = {j["id"] for j in _get_filtered_jobs(conn, status, content_type)}
    if job["id"] in filtered_ids:
        return None
    anchor = f"#job-{job['id']}"
    if job["status"] == "accepted":
        return {"label": "Moved to Accepted", "href": f"/jobs?status=accepted{anchor}"}
    if job["status"] == "rejected":
        return {"label": "Moved to Rejected", "href": f"/jobs?status=rejected{anchor}"}
    if job["status"] == "invalid":
        return {"label": "Moved to Invalid", "href": f"/jobs?status=invalid{anchor}"}
    if job["content_type"] == "job_posting":
        if job["passed_gate_count"] or job["gate_override"]:
            return {"label": "Moved to New", "href": f"/jobs{anchor}"}
        return {"label": "Moved to Not relevant", "href": f"/jobs?status=not_relevant{anchor}"}
    if job["content_type"] == "lead":
        return {"label": "Moved to Leads", "href": f"/jobs?content_type=lead{anchor}"}
    return {"label": "No longer shown in this view", "href": None}


def _render_updated_job_html(conn: sqlite3.Connection, request: Request, job_id: int, filter_ctx: dict) -> str:
    job = q.get_job(conn, job_id)
    sources = {s["id"]: s for s in q.get_sources(conn)}
    job["source_name"] = sources.get(job["source_id"], {}).get("name", "")

    stale_badge = None
    if filter_ctx:
        stale_badge = _stale_badge(
            conn, job, filter_ctx.get("filter_status"), filter_ctx.get("filter_content_type")
        )

    if stale_badge:
        job["stale_badge"] = stale_badge
        context = {"job": job}
        context.update(filter_ctx)
        return templates.get_template("jobs/_row.html").render(request=request, **context)

    scenarios = q.get_scenarios(conn)
    job_scores = q.get_job_scores(conn, job_id)
    context = {"job": job, "scenarios": scenarios, "job_scores": job_scores}
    context.update(filter_ctx)
    return templates.get_template("jobs/_feedback.html").render(request=request, **context)


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
        {
            "jobs": jobs, "stale_jobs": [], "counts": counts, "scenarios": scenarios,
            "status": effective_status, "content_type": content_type,
            "filter_status": status, "filter_content_type": content_type,
        },
    )


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(job_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    job = q.get_job(conn, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    sources = {s["id"]: s for s in q.get_sources(conn)}
    job["source_name"] = sources.get(job["source_id"], {}).get("name", "")
    scenarios = q.get_scenarios(conn)
    job_scores = q.get_job_scores(conn, job_id)
    return templates.TemplateResponse(
        request, "jobs/detail.html",
        {"job": job, "scenarios": scenarios, "job_scores": job_scores, "is_detail_page": True},
    )


@router.get("/jobs/{job_id}/expand", response_class=HTMLResponse)
def job_expand(job_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    job = q.get_job(conn, job_id)
    sources = {s["id"]: s for s in q.get_sources(conn)}
    job["source_name"] = sources.get(job["source_id"], {}).get("name", "")
    scenarios = q.get_scenarios(conn)
    job_scores = q.get_job_scores(conn, job_id)
    context = {"job": job, "scenarios": scenarios, "job_scores": job_scores}
    context.update(_filter_context(request))
    if _is_detail_page_request(request):
        context["is_detail_page"] = True
    return templates.TemplateResponse(request, "jobs/_feedback.html", context)


@router.get("/jobs/{job_id}/collapse", response_class=HTMLResponse)
def job_collapse(job_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    job = q.get_job(conn, job_id)
    sources = {s["id"]: s for s in q.get_sources(conn)}
    job["source_name"] = sources.get(job["source_id"], {}).get("name", "")
    context = {"job": job}
    context.update(_filter_context(request))
    if _is_detail_page_request(request):
        context["is_detail_page"] = True
    return templates.TemplateResponse(request, "jobs/_row.html", context)


@router.post("/jobs/{job_id}/feedback", response_class=HTMLResponse)
def job_feedback(
    job_id: int,
    request: Request,
    status: str = Form(...),
    note: str | None = Form(None),
    redirect: str | None = Form(None),
    conn: sqlite3.Connection = Depends(get_db),
):
    q.update_job_feedback(conn, job_id, status, note)
    if redirect:
        return HTMLResponse(content="", headers={"HX-Redirect": redirect})
    filter_ctx = _filter_context(request)
    row_html = _render_updated_job_html(conn, request, job_id, filter_ctx)
    counts = q.get_job_counts(conn)
    counts_html = templates.get_template("jobs/_counts_oob.html").render(request=request, counts=counts)
    return HTMLResponse(content=row_html + counts_html)


@router.post("/jobs/{job_id}/scenario-feedback", response_class=HTMLResponse)
def job_scenario_feedback(
    job_id: int,
    request: Request,
    scenario_id: list[int] = Form(...),
    note: list[str] = Form(...),
    direction: list[str] = Form(...),
    conn: sqlite3.Connection = Depends(get_db),
):
    for sid, n, d in zip(scenario_id, note, direction):
        q.upsert_scenario_feedback(conn, job_id, sid, n, d or None)
    job = q.get_job(conn, job_id)
    job_scores = q.get_job_scores(conn, job_id)
    return templates.TemplateResponse(
        request, "jobs/_score_tabs.html", {"job": job, "job_scores": job_scores, "saved": True}
    )


@router.post("/jobs/{job_id}/reset")
def job_reset(
    job_id: int,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    client: openai.OpenAI = Depends(get_ai_client),
    model: str = Depends(get_model),
):
    job = q.get_job(conn, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    scenarios = q.get_scenarios(conn)
    profile = q.get_profile(conn)
    filter_ctx = _filter_context(request)

    def stream():
        gen = run_reprocess_job(conn, client, model, job, scenarios, profile)
        try:
            while True:
                yield next(gen) + "\n"
        except StopIteration:
            pass
        html = _render_updated_job_html(conn, request, job_id, filter_ctx)
        yield "HTML:" + html.replace("\n", "") + "\n"
        counts_html = templates.get_template("jobs/_counts_oob.html").render(
            request=request, counts=q.get_job_counts(conn)
        )
        yield "HTML:" + counts_html.replace("\n", "")

    return StreamingResponse(stream(), media_type="text/plain")


@router.post("/jobs/{job_id}/pass-as-new")
def job_pass_as_new(
    job_id: int,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    client: openai.OpenAI = Depends(get_ai_client),
    model: str = Depends(get_model),
):
    job = q.get_job(conn, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    profile = q.get_profile(conn)
    filter_ctx = _filter_context(request)

    def stream():
        gen = run_pass_as_new(conn, client, model, job, profile)
        try:
            while True:
                yield next(gen) + "\n"
        except StopIteration:
            pass
        html = _render_updated_job_html(conn, request, job_id, filter_ctx)
        yield "HTML:" + html.replace("\n", "") + "\n"
        counts_html = templates.get_template("jobs/_counts_oob.html").render(
            request=request, counts=q.get_job_counts(conn)
        )
        yield "HTML:" + counts_html.replace("\n", "")

    return StreamingResponse(stream(), media_type="text/plain")


@router.post("/jobs/bulk-reset")
def job_bulk_reset(
    request: Request,
    job_ids: list[int] = Form(...),
    conn: sqlite3.Connection = Depends(get_db),
    client: openai.OpenAI = Depends(get_ai_client),
    model: str = Depends(get_model),
):
    scenarios = q.get_scenarios(conn)
    profile = q.get_profile(conn)
    filter_ctx = _filter_context(request)

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
            html = _render_updated_job_html(conn, request, job_id, filter_ctx)
            yield "HTML:" + html.replace("\n", "") + "\n"
        counts_html = templates.get_template("jobs/_counts_oob.html").render(
            request=request, counts=q.get_job_counts(conn)
        )
        yield "HTML:" + counts_html.replace("\n", "") + "\n"
        yield f"Reset complete: {len(job_ids)} job(s) reprocessed\n"

    return StreamingResponse(stream(), media_type="text/plain")


@router.post("/jobs/bulk-feedback", response_class=HTMLResponse)
def job_bulk_feedback(
    request: Request,
    job_ids: list[int] = Form(...),
    status: str = Form(...),
    note: str | None = Form(None),
    status_filter: str | None = Form(None),
    content_type_filter: str | None = Form(None),
    conn: sqlite3.Connection = Depends(get_db),
):
    for job_id in job_ids:
        q.update_job_feedback(conn, job_id, status, note)

    status_filter = status_filter or None
    content_type_filter = content_type_filter or None
    jobs = _enrich_jobs(conn, _get_filtered_jobs(conn, status_filter, content_type_filter))
    matched_ids = {j["id"] for j in jobs}

    stale_jobs = []
    for job_id in job_ids:
        if job_id in matched_ids:
            continue
        job = q.get_job(conn, job_id)
        if not job:
            continue
        badge = _stale_badge(conn, job, status_filter, content_type_filter)
        if not badge:
            continue
        job["stale_badge"] = badge
        stale_jobs.append(job)
    stale_jobs = _enrich_jobs(conn, stale_jobs)

    counts = q.get_job_counts(conn)
    scenarios = q.get_scenarios(conn)
    effective_status = status_filter if (status_filter is not None or content_type_filter is not None) else "new"
    return templates.TemplateResponse(
        request, "jobs/_content.html",
        {
            "jobs": jobs, "stale_jobs": stale_jobs, "counts": counts, "scenarios": scenarios,
            "status": effective_status, "content_type": content_type_filter,
            "filter_status": status_filter, "filter_content_type": content_type_filter,
        },
    )
