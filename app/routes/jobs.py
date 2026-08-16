from __future__ import annotations
import sqlite3
from urllib.parse import urlsplit
import httpx
import openai
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from app.deps import get_db, get_ai_client, get_model, get_config
from app.db import queries as q
from app.pipeline import run_reprocess_job, run_pass_as_new, run_add_job, run_fetch
from app.fetchers.links import extract_links
from app.fetchers.content import has_enough_content, extract_text as _extract_text_raw
from app.fetchers.playwright_pool import render_html
from app.ai.detect_listing import detect_listing
from app.template_env import templates

router = APIRouter()


class _FetchError(Exception):
    pass


class _NoContentError(_FetchError):
    pass


def _fetch_url_html(url: str) -> str:
    try:
        resp = httpx.get(url, timeout=30, follow_redirects=True)
    except httpx.HTTPError as exc:
        raise _FetchError(str(exc)) from exc
    if resp.status_code != 200:
        raise _FetchError(f"HTTP {resp.status_code}")
    return resp.text


def _extract_text(html: str) -> str:
    if not has_enough_content(html):
        raise _NoContentError("page had little to no extractable text")
    return _extract_text_raw(html)


def _insert_error_job(conn: sqlite3.Connection, url: str, reason: str) -> int:
    source_id = q.get_or_create_manual_source(conn)
    job_id = q.insert_job(conn, source_id=source_id, url=url, title=url, company="", raw_text="")
    q.update_job_pipeline(conn, job_id, simplified_content="", content_type="error")
    q.update_job_feedback(conn, job_id, "trash", reason)
    return job_id


def _notice_line(template_name: str, *, level: str = "info", **context) -> str:
    rendered = templates.get_template(template_name).render(**context)
    prefix = "NOTICE:warning:" if level == "warning" else "NOTICE:"
    return prefix + rendered.replace("\n", "") + "\n"


def _enrich_jobs(conn: sqlite3.Connection, jobs: list[dict]) -> list[dict]:
    sources = {s["id"]: s for s in q.get_sources(conn)}
    for job in jobs:
        job["source_name"] = sources.get(job["source_id"], {}).get("name", "")
    return jobs


def _get_filtered_jobs(
    conn: sqlite3.Connection, status: str | None, content_type: str | None, source_id: int | None = None
) -> list[dict]:
    if source_id is not None:
        return q.get_jobs(conn, source_id=source_id)
    if status == "not_relevant":
        return q.get_jobs(conn, status="new", content_type="job_posting", gate_status="failed")
    if status is None and content_type is None:
        return q.get_jobs(conn, status="new", gate_status="passed")
    if status is None and content_type == "lead":
        return q.get_jobs(conn, status="new", content_type="lead")
    return q.get_jobs(conn, status=status, content_type=content_type)


def _filter_context(request: Request) -> dict:
    has_status = "status" in request.query_params
    has_content_type = "content_type" in request.query_params
    has_source_id = "source_id" in request.query_params
    if not (has_status or has_content_type or has_source_id):
        return {}
    source_id_param = request.query_params.get("source_id") or None
    return {
        "filter_status": request.query_params.get("status") or None,
        "filter_content_type": request.query_params.get("content_type") or None,
        "filter_source_id": int(source_id_param) if source_id_param else None,
    }


def _is_detail_page_request(request: Request) -> bool:
    return request.query_params.get("detail") == "1"


def _stale_badge(
    conn: sqlite3.Connection, job: dict, status: str | None, content_type: str | None, source_id: int | None = None
) -> dict | None:
    filtered_ids = {j["id"] for j in _get_filtered_jobs(conn, status, content_type, source_id)}
    if job["id"] in filtered_ids:
        return None
    anchor = f"#job-{job['id']}"
    if job["status"] == "accepted":
        return {"label": "Moved to Accepted", "href": f"/jobs?status=accepted{anchor}"}
    if job["status"] == "rejected":
        return {"label": "Moved to Rejected", "href": f"/jobs?status=rejected{anchor}"}
    if job["status"] == "trash":
        return {"label": "Moved to Trash", "href": f"/jobs?status=trash{anchor}"}
    if job["content_type"] == "job_posting":
        if job["passed_gate_count"] or job["gate_override"]:
            return {"label": "Moved to New", "href": f"/jobs{anchor}"}
        return {"label": "Moved to Not relevant", "href": f"/jobs?status=not_relevant{anchor}"}
    if job["content_type"] == "lead":
        return {"label": "Moved to Leads", "href": f"/jobs?content_type=lead{anchor}"}
    return {"label": "No longer shown in this view", "href": None}


def _render_removed_job_html(job_id: int) -> str:
    return (
        f'<article class="job-row" id="job-{job_id}">'
        "<p>Reclassified as not job-related and removed.</p></article>"
    )


def _render_updated_job_html(conn: sqlite3.Connection, request: Request, job_id: int, filter_ctx: dict) -> str:
    job = q.get_job(conn, job_id)
    if job is None:
        return _render_removed_job_html(job_id)
    sources = {s["id"]: s for s in q.get_sources(conn)}
    job["source_name"] = sources.get(job["source_id"], {}).get("name", "")

    stale_badge = None
    if filter_ctx:
        stale_badge = _stale_badge(
            conn, job, filter_ctx.get("filter_status"), filter_ctx.get("filter_content_type"),
            filter_ctx.get("filter_source_id"),
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


def _content_context(
    conn: sqlite3.Connection, status: str | None, content_type: str | None, source_id: int | None = None
) -> dict:
    jobs = _enrich_jobs(conn, _get_filtered_jobs(conn, status, content_type, source_id))
    counts = q.get_job_counts(conn)
    scenarios = q.get_scenarios(conn)
    effective_status = status if (status is not None or content_type is not None or source_id is not None) else "new"
    filter_source = q.get_source(conn, source_id) if source_id is not None else None
    return {
        "jobs": jobs, "stale_jobs": [], "counts": counts, "scenarios": scenarios,
        "status": effective_status, "content_type": content_type,
        "filter_status": status, "filter_content_type": content_type,
        "filter_source_id": source_id, "filter_source": filter_source,
    }


@router.get("/jobs", response_class=HTMLResponse)
def job_list(
    request: Request,
    status: str | None = None,
    content_type: str | None = None,
    source_id: int | None = None,
    conn: sqlite3.Connection = Depends(get_db),
):
    return templates.TemplateResponse(
        request, "jobs/list.html", _content_context(conn, status, content_type, source_id)
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


@router.get("/jobs/{job_id}/delete-confirm", response_class=HTMLResponse)
def job_delete_confirm(job_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    job = q.get_job(conn, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] != "trash":
        raise HTTPException(status_code=400, detail="Only trashed jobs can be deleted")
    context = {"job": job}
    context.update(_filter_context(request))
    if _is_detail_page_request(request):
        context["is_detail_page"] = True
    return templates.TemplateResponse(request, "jobs/_row_delete_confirm.html", context)


@router.delete("/jobs/{job_id}", response_class=HTMLResponse)
def job_delete(job_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    job = q.get_job(conn, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] != "trash":
        raise HTTPException(status_code=400, detail="Only trashed jobs can be deleted")
    q.delete_job(conn, job_id)
    counts_html = templates.get_template("jobs/_counts_oob.html").render(
        request=request, counts=q.get_job_counts(conn)
    )
    return HTMLResponse(content=counts_html)


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
    source_id_filter: str | None = Form(None),
    conn: sqlite3.Connection = Depends(get_db),
):
    for job_id in job_ids:
        q.update_job_feedback(conn, job_id, status, note)

    status_filter = status_filter or None
    content_type_filter = content_type_filter or None
    source_id_filter = source_id_filter or None
    source_id = int(source_id_filter) if source_id_filter else None
    jobs = _enrich_jobs(conn, _get_filtered_jobs(conn, status_filter, content_type_filter, source_id))
    matched_ids = {j["id"] for j in jobs}

    stale_jobs = []
    for job_id in job_ids:
        if job_id in matched_ids:
            continue
        job = q.get_job(conn, job_id)
        if not job:
            continue
        badge = _stale_badge(conn, job, status_filter, content_type_filter, source_id)
        if not badge:
            continue
        job["stale_badge"] = badge
        stale_jobs.append(job)
    stale_jobs = _enrich_jobs(conn, stale_jobs)

    counts = q.get_job_counts(conn)
    scenarios = q.get_scenarios(conn)
    effective_status = (
        status_filter
        if (status_filter is not None or content_type_filter is not None or source_id is not None)
        else "new"
    )
    filter_source = q.get_source(conn, source_id) if source_id is not None else None
    return templates.TemplateResponse(
        request, "jobs/_content.html",
        {
            "jobs": jobs, "stale_jobs": stale_jobs, "counts": counts, "scenarios": scenarios,
            "status": effective_status, "content_type": content_type_filter,
            "filter_status": status_filter, "filter_content_type": content_type_filter,
            "filter_source_id": source_id, "filter_source": filter_source,
        },
    )


@router.post("/jobs/bulk-delete-confirm", response_class=HTMLResponse)
def job_bulk_delete_confirm(
    request: Request,
    job_ids: list[int] = Form(...),
    conn: sqlite3.Connection = Depends(get_db),
):
    return templates.TemplateResponse(request, "jobs/_bulk_delete_confirm.html", {"job_ids": job_ids})


@router.post("/jobs/bulk-delete", response_class=HTMLResponse)
def job_bulk_delete(
    request: Request,
    job_ids: list[int] = Form(...),
    status_filter: str | None = Form(None),
    content_type_filter: str | None = Form(None),
    source_id_filter: str | None = Form(None),
    conn: sqlite3.Connection = Depends(get_db),
):
    q.delete_jobs(conn, job_ids)
    status_filter = status_filter or None
    content_type_filter = content_type_filter or None
    source_id_filter = source_id_filter or None
    source_id = int(source_id_filter) if source_id_filter else None
    return templates.TemplateResponse(
        request, "jobs/_content.html", _content_context(conn, status_filter, content_type_filter, source_id)
    )


@router.post("/jobs/bulk-actions-cancel", response_class=HTMLResponse)
def job_bulk_actions_cancel(
    request: Request,
    status_filter: str | None = Form(None),
    conn: sqlite3.Connection = Depends(get_db),
):
    return templates.TemplateResponse(request, "jobs/_bulk_actions.html", {"status": status_filter or None})


@router.post("/jobs/add-by-url")
def job_add_by_url(
    request: Request,
    url: str = Form(...),
    conn: sqlite3.Connection = Depends(get_db),
    client: openai.OpenAI = Depends(get_ai_client),
    model: str = Depends(get_model),
):
    status = request.query_params.get("status") or None
    content_type = request.query_params.get("content_type") or None

    def stream():
        existing_job = q.get_job_by_url(conn, url)
        if existing_job is not None:
            if existing_job["content_type"] == "error":
                yield _notice_line(
                    "jobs/_already_tracked.html", level="warning", request=request, kind="error",
                    link_href=f"/jobs/{existing_job['id']}",
                )
            else:
                yield _notice_line(
                    "jobs/_already_tracked.html", request=request, kind="job",
                    link_href=f"/jobs/{existing_job['id']}", link_text="View this job",
                )
        else:
            existing_source = q.get_source_by_url(conn, url)
            if existing_source is not None:
                yield _notice_line(
                    "jobs/_already_tracked.html", request=request, kind="source",
                    link_href=f"/sources#source-row-{existing_source['id']}",
                    link_text=f'View "{existing_source["name"]}" in Sources',
                )
            else:
                try:
                    html = _fetch_url_html(url)
                except _FetchError as exc:
                    _insert_error_job(conn, url, f"Failed to fetch: {exc}")
                    yield _notice_line(
                        "jobs/_fetch_failed_notice.html", level="warning",
                        request=request, url=url, reason=str(exc),
                    )
                else:
                    if not has_enough_content(html):
                        rendered = render_html(url)
                        if rendered and has_enough_content(rendered):
                            html = rendered
                    links = extract_links(html, url)
                    detection = detect_listing(client, model, links, url)
                    if detection["is_listing"] and len(detection["job_links"]) >= 2:
                        panel_context = {
                            "request": request,
                            "url": url,
                            "link_count": len(detection["job_links"]),
                            "domain": urlsplit(url).netloc,
                            "default_name": urlsplit(url).netloc,
                        }
                        panel_context.update(_filter_context(request))
                        panel = templates.get_template("jobs/_listing_confirm.html").render(**panel_context)
                        yield "HTML:" + panel.replace("\n", "") + "\n"
                        return
                    try:
                        raw_text = _extract_text(html)
                    except _NoContentError:
                        _insert_error_job(
                            conn, url, "No extractable content — page likely requires JavaScript to render",
                        )
                        yield _notice_line(
                            "jobs/_no_content_notice.html", level="warning", request=request, url=url,
                        )
                    else:
                        source_id = q.get_or_create_manual_source(conn)
                        gen = run_add_job(conn, client, model, source_id, url, raw_text)
                        try:
                            while True:
                                yield next(gen) + "\n"
                        except StopIteration:
                            pass

        html_chunk = templates.get_template("jobs/_content.html").render(
            request=request, **_content_context(conn, status, content_type)
        )
        yield "HTML:" + html_chunk.replace("\n", "") + "\n"

    return StreamingResponse(stream(), media_type="text/plain")


@router.post("/jobs/add-listing-source")
def job_add_listing_source(
    request: Request,
    url: str = Form(...),
    name: str = Form(...),
    conn: sqlite3.Connection = Depends(get_db),
    client: openai.OpenAI = Depends(get_ai_client),
    model: str = Depends(get_model),
    config=Depends(get_config),
):
    status = request.query_params.get("status") or None
    content_type = request.query_params.get("content_type") or None

    def stream():
        source_id = q.insert_source(conn, name, url, "generic_listing")
        source = q.get_source(conn, source_id)
        gen = run_fetch(source, conn, client, model, config.browser_profile_dir)
        try:
            while True:
                yield next(gen) + "\n"
        except StopIteration:
            pass

        html_chunk = templates.get_template("jobs/_content.html").render(
            request=request, **_content_context(conn, status, content_type)
        )
        yield "HTML:" + html_chunk.replace("\n", "") + "\n"

    return StreamingResponse(stream(), media_type="text/plain")
