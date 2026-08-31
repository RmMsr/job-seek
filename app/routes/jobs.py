from __future__ import annotations
import sqlite3
from urllib.parse import urlsplit
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from app.deps import get_db
from app.db import queries as q
from app.job_filter import JobFilter
from app.pipeline import run_reprocess_job, run_pass_as_new, run_reevaluate_job, run_add_job, run_fetch
from app.fetchers.content import (
    FetchError, NoContentError, extract_text_or_raise, extract_page_title,
)
from app.fetchers.listing_detect import detect_listing_page
from app.url_rewrite import suggest_rewrite, suggest_source_name
from app.ai.classify_known_source import classify_known_source, DETECTABLE_FETCHER_TYPES
from app.ai.generate_source_name import generate_source_name
from app.routes.sources import check_already_tracked_notice_data
from app.routes.tasks import resolve_origin_task_id
from app.task_engine import register_task_kind
from app.template_env import templates

router = APIRouter()


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


# status tab -> base kwargs for get_jobs (before scenario/source/org narrowing)
_BASE_KWARGS_FOR_TAB = {
    "new": {"status": "new", "content_type": "job_posting", "gate_status": "passed"},
    "lead": {"status": "new", "content_type": "lead"},
    "accepted": {"status": "accepted"},
    "rejected": {"status": "rejected"},
    "not_relevant": {"status": "new", "content_type": "job_posting", "gate_status": "failed"},
    "trash": {"status": "trash"},
}


def _jobs_for_filter(conn: sqlite3.Connection, f: JobFilter) -> list[dict]:
    kwargs = dict(_BASE_KWARGS_FOR_TAB[f.status_tab])
    if f.source_id is not None:
        kwargs["source_id"] = f.source_id
    if f.org_none:
        kwargs["org_none"] = True
    elif f.org is not None:
        kwargs["org"] = f.org
    if f.scenario_id is not None:
        kwargs["scenario_id"] = f.scenario_id
    if f.scenario_none:
        kwargs["scenario_gate"] = "none"
    return q.get_jobs(conn, order=f.order, **kwargs)


def _sort_key(order: str):
    """Match get_jobs' ORDER BY for the in-Python re-sort of merged stale rows."""
    if order == "score":
        return lambda j: (
            j["fit_score"] if j["fit_score"] is not None else float("-inf"),
            j["fetched_at"] or "",
        )
    if order == "age":
        return lambda j: (j["published_at"] or j["fetched_at"] or "",)
    return lambda j: (
        max(
            j.get("status_changed_at") or "",
            j.get("evaluation_completed_at") or "",
            j["fetched_at"] or "",
        ),
    )


def _counts_for_filter(conn: sqlite3.Connection, f: JobFilter) -> dict[str, int]:
    return q.get_job_counts(
        conn,
        scenario_id=f.scenario_id,
        scenario_none=f.scenario_none,
        source_id=f.source_id,
        org=f.org,
        org_none=f.org_none,
    )


def _filter_from_request(request: Request) -> JobFilter:
    return JobFilter.from_params(request.query_params)


def _is_detail_page_request(request: Request) -> bool:
    return request.query_params.get("detail") == "1"


def _filter_task_params(request: Request) -> dict:
    """Serialisable filter state to stash in an enqueued task's params."""
    return {
        "filter": _filter_from_request(request).query_params(),
        "detail": _is_detail_page_request(request),
    }


def _filter_from_task_params(params: dict) -> tuple[JobFilter, bool]:
    return JobFilter.from_params(params.get("filter") or {}), bool(params.get("detail"))


def _stale_badge(
    conn: sqlite3.Connection,
    job: dict,
    f: JobFilter,
    filtered_ids: set[int] | None = None,
) -> dict | None:
    if filtered_ids is None:
        filtered_ids = {j["id"] for j in _jobs_for_filter(conn, f)}
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
            return {"label": "Moved to New", "href": f"/jobs?status=new{anchor}"}
        return {"label": "Moved to Not relevant", "href": f"/jobs?status=not_relevant{anchor}"}
    if job["content_type"] == "lead":
        return {"label": "Moved to Leads", "href": f"/jobs?status=lead{anchor}"}
    return {"label": "No longer shown in this view", "href": None}


def _render_removed_job_html(job_id: int) -> str:
    return (
        f'<article class="job-row" id="job-{job_id}">'
        "<p>Reclassified as not job-related and removed.</p></article>"
    )


def _render_updated_job_html(
    conn: sqlite3.Connection,
    request: Request | None,
    job_id: int,
    f: JobFilter,
    *,
    detail: bool = False,
) -> str:
    job = q.get_job(conn, job_id)
    if job is None:
        return _render_removed_job_html(job_id)
    sources = {s["id"]: s for s in q.get_sources(conn)}
    job["source_name"] = sources.get(job["source_id"], {}).get("name", "")

    stale_badge = None if detail else _stale_badge(conn, job, f)
    if stale_badge:
        job["stale_badge"] = stale_badge
        return templates.get_template("jobs/_row.html").render(
            request=request, job=job, filter=f, is_detail_page=detail
        )

    scenarios = q.get_scenarios(conn)
    job_scores = q.get_job_scores(conn, job_id)
    return templates.get_template("jobs/_feedback.html").render(
        request=request, job=job, scenarios=scenarios, job_scores=job_scores,
        filter=f, is_detail_page=detail,
    )


def _content_context(conn: sqlite3.Connection, f: JobFilter, *, jobs: list[dict] | None = None) -> dict:
    if jobs is None:
        jobs = _enrich_jobs(conn, _jobs_for_filter(conn, f))
    return {
        "filter": f,
        "jobs": jobs,
        "stale_jobs": [],
        "counts": _counts_for_filter(conn, f),
        "scenarios": q.get_scenarios(conn),
        "sources": q.get_sources(conn),
        "companies": q.get_distinct_companies(conn),
        "status": f.status_tab,
    }


def _filter_from_bulk_form(
    status_filter: str | None,
    scenario_filter: str | None,
    source_id_filter: str | None,
    org_filter: str | None,
    order_filter: str | None = None,
) -> JobFilter:
    return JobFilter.from_params({
        "status": status_filter or "new",
        "scenario": scenario_filter or "",
        "source_id": source_id_filter or "",
        "org": org_filter or "",
        "order": order_filter or "",
    })


@router.get("/jobs", response_class=HTMLResponse)
def job_list(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    f = _filter_from_request(request)
    # htmx filter/tab navigation swaps only #jobs-content; a full browser
    # navigation (no HX-Request header) gets the whole page.
    template = "jobs/_content.html" if request.headers.get("HX-Request") else "jobs/list.html"
    return templates.TemplateResponse(request, template, _content_context(conn, f))


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
        {"job": job, "scenarios": scenarios, "job_scores": job_scores,
         "is_detail_page": True, "filter": None},
    )


@router.get("/jobs/{job_id}/expand", response_class=HTMLResponse)
def job_expand(job_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    job = q.get_job(conn, job_id)
    sources = {s["id"]: s for s in q.get_sources(conn)}
    job["source_name"] = sources.get(job["source_id"], {}).get("name", "")
    scenarios = q.get_scenarios(conn)
    job_scores = q.get_job_scores(conn, job_id)
    detail = _is_detail_page_request(request)
    f = _filter_from_request(request)
    if not detail:
        stale_badge = _stale_badge(conn, job, f)
        if stale_badge:
            job["stale_badge"] = stale_badge
    context = {
        "job": job, "scenarios": scenarios, "job_scores": job_scores,
        "filter": f, "is_detail_page": detail,
    }
    return templates.TemplateResponse(request, "jobs/_feedback.html", context)


@router.get("/jobs/{job_id}/collapse", response_class=HTMLResponse)
def job_collapse(job_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    job = q.get_job(conn, job_id)
    sources = {s["id"]: s for s in q.get_sources(conn)}
    job["source_name"] = sources.get(job["source_id"], {}).get("name", "")
    detail = _is_detail_page_request(request)
    f = _filter_from_request(request)
    if not detail:
        stale_badge = _stale_badge(conn, job, f)
        if stale_badge:
            job["stale_badge"] = stale_badge
    context = {"job": job, "filter": f, "is_detail_page": detail}
    return templates.TemplateResponse(request, "jobs/_row.html", context)


@router.get("/jobs/{job_id}/delete-confirm", response_class=HTMLResponse)
def job_delete_confirm(job_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    job = q.get_job(conn, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] != "trash":
        raise HTTPException(status_code=400, detail="Only trashed jobs can be deleted")
    context = {
        "job": job,
        "filter": _filter_from_request(request),
        "is_detail_page": _is_detail_page_request(request),
    }
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
    f = _filter_from_request(request)
    detail = _is_detail_page_request(request)
    row_html = _render_updated_job_html(conn, request, job_id, f, detail=detail)
    counts = _counts_for_filter(conn, f)
    counts_html = templates.get_template("jobs/_counts_oob.html").render(request=request, counts=counts)
    headers = {}
    if not detail:
        decided = q.get_job(conn, job_id)
        if decided is not None and _stale_badge(conn, decided, f) is not None:
            headers["HX-Reswap"] = "outerHTML swap:0.35s"
    return HTMLResponse(content=row_html + counts_html, headers=headers)


@router.post("/jobs/{job_id}/scenario-feedback", response_class=HTMLResponse)
def job_scenario_feedback(
    job_id: int,
    request: Request,
    scenario_id: list[int] = Form(...),
    note: list[str] = Form(...),
    direction: list[str] = Form(...),
    active_scenario_id: int | None = Form(None),
    conn: sqlite3.Connection = Depends(get_db),
):
    for sid, n, d in zip(scenario_id, note, direction):
        q.upsert_scenario_feedback(conn, job_id, sid, n, d or None)
    job = q.get_job(conn, job_id)
    job_scores = q.get_job_scores(conn, job_id)
    return templates.TemplateResponse(
        request,
        "jobs/_score_tabs.html",
        {
            "job": job,
            "job_scores": job_scores,
            "saved": True,
            "active_scenario_id": active_scenario_id or job["top_passed_scenario_id"],
        },
    )


@register_task_kind("job_reset")
def _task_job_reset(conn, client, model, config, params):
    job = q.get_job(conn, params["job_id"])
    if job is None:
        return {"html_chunks": [], "notices": []}
    scenarios = q.get_scenarios(conn)
    profile = q.get_profile(conn)
    gen = run_reprocess_job(conn, client, model, job, scenarios, profile)
    try:
        while True:
            yield next(gen)
    except StopIteration:
        pass
    f, detail = _filter_from_task_params(params)
    html = _render_updated_job_html(conn, None, params["job_id"], f, detail=detail)
    counts_html = templates.get_template("jobs/_counts_oob.html").render(
        request=None, counts=_counts_for_filter(conn, f)
    )
    return {"html_chunks": [html, counts_html], "notices": []}


@register_task_kind("job_pass_as_new")
def _task_job_pass_as_new(conn, client, model, config, params):
    job = q.get_job(conn, params["job_id"])
    if job is None:
        return {"html_chunks": [], "notices": []}
    profile = q.get_profile(conn)
    gen = run_pass_as_new(conn, client, model, job, profile)
    try:
        while True:
            yield next(gen)
    except StopIteration:
        pass
    f, detail = _filter_from_task_params(params)
    html = _render_updated_job_html(conn, None, params["job_id"], f, detail=detail)
    counts_html = templates.get_template("jobs/_counts_oob.html").render(
        request=None, counts=_counts_for_filter(conn, f)
    )
    return {"html_chunks": [html, counts_html], "notices": []}


@register_task_kind("job_reevaluate")
def _task_job_reevaluate(conn, client, model, config, params):
    job = q.get_job(conn, params["job_id"])
    if job is None:
        return {"html_chunks": [], "notices": []}
    scenarios = q.get_scenarios(conn)
    profile = q.get_profile(conn)
    gen = run_reevaluate_job(conn, client, model, job, scenarios, profile)
    try:
        while True:
            yield next(gen)
    except StopIteration:
        pass
    f, detail = _filter_from_task_params(params)
    html = _render_updated_job_html(conn, None, params["job_id"], f, detail=detail)
    counts_html = templates.get_template("jobs/_counts_oob.html").render(
        request=None, counts=_counts_for_filter(conn, f)
    )
    return {"html_chunks": [html, counts_html], "notices": []}


@router.post("/jobs/{job_id}/reset")
def job_reset(job_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    job = q.get_job(conn, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    task = q.enqueue_task(
        conn, kind="job_reset", params={"job_id": job_id, **_filter_task_params(request)}
    )
    return {"task_id": task["id"], "already_active": task["already_active"]}


@router.post("/jobs/{job_id}/pass-as-new")
def job_pass_as_new(job_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    job = q.get_job(conn, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    task = q.enqueue_task(
        conn, kind="job_pass_as_new", params={"job_id": job_id, **_filter_task_params(request)}
    )
    return {"task_id": task["id"], "already_active": task["already_active"]}


@router.post("/jobs/{job_id}/reevaluate")
def job_reevaluate(job_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    job = q.get_job(conn, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    task = q.enqueue_task(
        conn, kind="job_reevaluate", params={"job_id": job_id, **_filter_task_params(request)}
    )
    return {"task_id": task["id"], "already_active": task["already_active"]}


@register_task_kind("jobs_bulk_reset")
def _task_jobs_bulk_reset(conn, client, model, config, params):
    job_ids = params["job_ids"]
    f, detail = _filter_from_task_params(params)
    scenarios = q.get_scenarios(conn)
    profile = q.get_profile(conn)
    yield f"Resetting {len(job_ids)} job(s)"
    html_chunks = []
    for idx, job_id in enumerate(job_ids, start=1):
        job = q.get_job(conn, job_id)
        if not job:
            continue
        prefix = f"[{idx}/{len(job_ids)}] "
        gen = run_reprocess_job(conn, client, model, job, scenarios, profile, progress_prefix=prefix)
        try:
            while True:
                yield next(gen)
        except StopIteration:
            pass
        html_chunks.append(_render_updated_job_html(conn, None, job_id, f, detail=detail))
    html_chunks.append(
        templates.get_template("jobs/_counts_oob.html").render(
            request=None, counts=_counts_for_filter(conn, f)
        )
    )
    yield f"Reset complete: {len(job_ids)} job(s) reprocessed"
    return {"html_chunks": html_chunks, "notices": []}


@register_task_kind("jobs_bulk_reevaluate")
def _task_jobs_bulk_reevaluate(conn, client, model, config, params):
    job_ids = params["job_ids"]
    f, detail = _filter_from_task_params(params)
    scenarios = q.get_scenarios(conn)
    profile = q.get_profile(conn)
    yield f"Re-evaluating {len(job_ids)} job(s)"
    html_chunks = []
    for idx, job_id in enumerate(job_ids, start=1):
        job = q.get_job(conn, job_id)
        if not job:
            continue
        prefix = f"[{idx}/{len(job_ids)}] "
        gen = run_reevaluate_job(conn, client, model, job, scenarios, profile, progress_prefix=prefix)
        try:
            while True:
                yield next(gen)
        except StopIteration:
            pass
        html_chunks.append(_render_updated_job_html(conn, None, job_id, f, detail=detail))
    html_chunks.append(
        templates.get_template("jobs/_counts_oob.html").render(
            request=None, counts=_counts_for_filter(conn, f)
        )
    )
    yield f"Re-evaluation complete: {len(job_ids)} job(s) updated"
    return {"html_chunks": html_chunks, "notices": []}


@router.post("/jobs/bulk-reset")
def job_bulk_reset(request: Request, job_ids: list[int] = Form(...), conn: sqlite3.Connection = Depends(get_db)):
    task = q.enqueue_task(
        conn, kind="jobs_bulk_reset", params={"job_ids": job_ids, **_filter_task_params(request)}
    )
    return {"task_id": task["id"], "already_active": task["already_active"]}


@router.post("/jobs/bulk-reevaluate")
def job_bulk_reevaluate(request: Request, job_ids: list[int] = Form(...), conn: sqlite3.Connection = Depends(get_db)):
    task = q.enqueue_task(
        conn, kind="jobs_bulk_reevaluate", params={"job_ids": job_ids, **_filter_task_params(request)}
    )
    return {"task_id": task["id"], "already_active": task["already_active"]}


@router.post("/jobs/bulk-feedback", response_class=HTMLResponse)
def job_bulk_feedback(
    request: Request,
    job_ids: list[int] = Form(...),
    status: str = Form(...),
    note: str | None = Form(None),
    status_filter: str | None = Form(None),
    scenario_filter: str | None = Form(None),
    source_id_filter: str | None = Form(None),
    org_filter: str | None = Form(None),
    order_filter: str | None = Form(None),
    conn: sqlite3.Connection = Depends(get_db),
):
    for job_id in job_ids:
        q.update_job_feedback(conn, job_id, status, note)

    f = _filter_from_bulk_form(status_filter, scenario_filter, source_id_filter, org_filter, order_filter)
    jobs = _enrich_jobs(conn, _jobs_for_filter(conn, f))
    matched_ids = {j["id"] for j in jobs}

    # Jobs just acted on that no longer match the current filter get a stale
    # badge instead of vanishing — merged back into the same sort position
    # they'd otherwise occupy, so a bulk action updates rows in place just
    # like a single-job action does, rather than banishing them to the end.
    stale_jobs = []
    for job_id in job_ids:
        if job_id in matched_ids:
            continue
        job = q.get_job(conn, job_id)
        if not job:
            continue
        badge = _stale_badge(conn, job, f, filtered_ids=matched_ids)
        if not badge:
            continue
        job["stale_badge"] = badge
        stale_jobs.append(job)
    stale_jobs = _enrich_jobs(conn, stale_jobs)
    jobs = sorted(jobs + stale_jobs, key=_sort_key(f.order), reverse=True)
    return templates.TemplateResponse(
        request, "jobs/_content.html", _content_context(conn, f, jobs=jobs)
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
    scenario_filter: str | None = Form(None),
    source_id_filter: str | None = Form(None),
    org_filter: str | None = Form(None),
    order_filter: str | None = Form(None),
    conn: sqlite3.Connection = Depends(get_db),
):
    q.delete_jobs(conn, job_ids)
    f = _filter_from_bulk_form(status_filter, scenario_filter, source_id_filter, org_filter, order_filter)
    return templates.TemplateResponse(request, "jobs/_content.html", _content_context(conn, f))


@router.post("/jobs/bulk-actions-cancel", response_class=HTMLResponse)
def job_bulk_actions_cancel(
    request: Request,
    status_filter: str | None = Form(None),
    conn: sqlite3.Connection = Depends(get_db),
):
    return templates.TemplateResponse(request, "jobs/_bulk_actions.html", {"status": status_filter or None})


@register_task_kind("job_add_by_url")
def _task_job_add_by_url(conn, client, model, config, params):
    url = params["url"]
    f = JobFilter.from_params(params.get("filter") or {})
    notices: list[dict] = []
    html_chunks: list[str] = []
    result = {"notices": notices, "html_chunks": html_chunks}

    existing_job = q.get_job_by_url(conn, url)
    if existing_job is not None:
        if existing_job["content_type"] == "error":
            notices.append({
                "level": "warning",
                "html": templates.get_template("jobs/_already_tracked.html").render(
                    request=None, kind="error", link_href=f"/jobs/{existing_job['id']}",
                ),
            })
        else:
            notices.append({
                "level": "info",
                "html": templates.get_template("jobs/_already_tracked.html").render(
                    request=None, kind="job", link_href=f"/jobs/{existing_job['id']}", link_text="View this job",
                ),
            })
    else:
        already_tracked = check_already_tracked_notice_data(conn, url)
        if already_tracked is not None:
            notices.append(already_tracked)
        else:
            if not params.get("skip_rewrite"):
                suggestion = suggest_rewrite(url)
                if suggestion is not None:
                    panel = templates.get_template("_rewrite_panel.html").render(
                        request=None, original_url=url, suggested_url=suggestion.url,
                        reason=suggestion.reason, detect_url="/jobs/add-by-url", cancel_url="/jobs",
                        target="#jobs-content", action_target="#jobs-add-result",
                    )
                    html_chunks.append(panel)
                    q.resolve_source_prompts_for_url(conn, url)
                    result["needs_action"] = True
                    result["action_message"] = "LinkedIn search URL — a fetchable alternative was suggested"
                    result["resume_html"] = panel
                    return result
            try:
                detection = detect_listing_page(client, model, url)
            except FetchError as exc:
                _insert_error_job(conn, url, f"Failed to fetch: {exc}")
                notices.append({
                    "level": "warning",
                    "html": templates.get_template("jobs/_fetch_failed_notice.html").render(
                        request=None, url=url, reason=str(exc),
                    ),
                })
            else:
                html = detection.html
                if detection.is_listing and len(detection.job_links) >= 2:
                    domain = urlsplit(url).netloc
                    default_name = suggest_source_name(url) or domain
                    page_title = extract_page_title(html)
                    if page_title:
                        generated_name = generate_source_name(client, model, domain, page_title)
                        if generated_name:
                            default_name = generated_name
                    panel_context = {
                        "request": None, "url": url,
                        "fetcher_type": classify_known_source(url) or "generic_listing",
                        "link_count": len(detection.job_links), "domain": domain,
                        "default_name": default_name,
                    }
                    panel_context["filter"] = f
                    panel = templates.get_template("jobs/_listing_confirm.html").render(**panel_context)
                    html_chunks.append(panel)
                    result["needs_action"] = True
                    result["action_message"] = f"'{default_name}' looks like a job listing — confirm how to add it"
                    result["resume_html"] = panel
                    return result
                try:
                    raw_text = extract_text_or_raise(html)
                except NoContentError:
                    _insert_error_job(
                        conn, url, "No extractable content — page likely requires JavaScript to render"
                    )
                    notices.append({
                        "level": "warning",
                        "html": templates.get_template("jobs/_no_content_notice.html").render(request=None, url=url),
                    })
                else:
                    source_id = q.get_or_create_manual_source(conn)
                    gen = run_add_job(conn, client, model, source_id, url, raw_text)
                    try:
                        while True:
                            yield next(gen)
                    except StopIteration:
                        pass
                    added_job = q.get_job_by_url(conn, url)
                    if added_job is None:
                        notices.append({
                            "level": "info",
                            "html": templates.get_template("jobs/_discarded_notice.html").render(request=None),
                        })
                    else:
                        added_job = q.get_job(conn, added_job["id"])
                        if added_job["content_type"] == "lead":
                            notices.append({
                                "level": "info",
                                "html": templates.get_template("jobs/_lead_added_notice.html").render(
                                    request=None, job_id=added_job["id"],
                                ),
                            })
                        elif added_job["content_type"] == "error":
                            notices.append({
                                "level": "warning",
                                "html": templates.get_template("jobs/_error_added_notice.html").render(
                                    request=None, job_id=added_job["id"],
                                ),
                            })
                        elif added_job["content_type"] == "job_posting":
                            passed_gate = bool(added_job["passed_gate_count"]) or bool(added_job["gate_override"])
                            template_name = (
                                "jobs/_job_added_notice.html" if passed_gate
                                else "jobs/_not_relevant_added_notice.html"
                            )
                            notices.append({
                                "level": "info",
                                "html": templates.get_template(template_name).render(
                                    request=None, job_id=added_job["id"],
                                ),
                            })

    q.resolve_source_prompts_for_url(conn, url)
    html_chunks.append(
        templates.get_template("jobs/_content.html").render(
            request=None, **_content_context(conn, f)
        )
    )
    return result


@register_task_kind("job_add_listing_source")
def _task_job_add_listing_source(conn, client, model, config, params):
    url, name, fetcher_type = params["url"], params["name"], params["fetcher_type"]
    f = JobFilter.from_params(params.get("filter") or {})
    notices = []

    q.resolve_source_prompts_for_url(conn, url)
    already_tracked = check_already_tracked_notice_data(conn, url)
    if already_tracked is not None:
        notices.append(already_tracked)
        html_chunks = [templates.get_template("jobs/_content.html").render(
            request=None, **_content_context(conn, f)
        )]
        return {"notices": notices, "html_chunks": html_chunks}

    source_id = q.insert_source(conn, name, url, fetcher_type)
    source = q.get_source(conn, source_id)
    gen = run_fetch(source, conn, client, model, config.browser_profile_dir, task_id=params["_task_id"])
    fetch_result = None
    try:
        while True:
            yield next(gen)
    except StopIteration as stop:
        fetch_result = stop.value

    if fetch_result is not None and fetch_result.jobs_found == 0:
        notices.append({
            "level": "warning",
            "html": templates.get_template("sources/_fetch_nothing_found_notice.html").render(
                request=None, name=name, url=url, error=fetch_result.error, source_id=source_id,
            ),
        })
    else:
        notices.append({
            "level": "info",
            "html": templates.get_template("jobs/_source_added_notice.html").render(
                request=None, name=name, source_id=source_id,
            ),
        })

    html_chunks = [templates.get_template("jobs/_content.html").render(
        request=None, **_content_context(conn, f)
    )]
    return {"notices": notices, "html_chunks": html_chunks}


@router.post("/jobs/add-by-url")
def job_add_by_url(
    request: Request,
    url: str = Form(...),
    skip_rewrite: bool = Form(False),
    origin_task_id: str = Form(""),
    conn: sqlite3.Connection = Depends(get_db),
):
    # Normally its own root; when reached as a step of an add-source attempt
    # (the "add as job instead" mismatch button) it attaches under that root.
    root_id = resolve_origin_task_id(origin_task_id)
    task = q.enqueue_task(conn, kind="job_add_by_url", params={
        "url": url,
        "filter": _filter_from_request(request).query_params(),
        "skip_rewrite": skip_rewrite,
    }, parent_task_id=root_id)
    if root_id is not None:
        q.resolve_task(conn, root_id)
    return {"task_id": task["id"], "already_active": task["already_active"]}


@router.post("/jobs/add-listing-source")
def job_add_listing_source(
    request: Request, url: str = Form(...), name: str = Form(...), fetcher_type: str = Form(...),
    origin_task_id: str = Form(""),
    conn: sqlite3.Connection = Depends(get_db),
):
    if fetcher_type not in DETECTABLE_FETCHER_TYPES:
        raise HTTPException(status_code=400, detail="Invalid fetcher_type")
    root_id = resolve_origin_task_id(origin_task_id)
    task = q.enqueue_task(conn, kind="job_add_listing_source", params={
        "url": url, "name": name, "fetcher_type": fetcher_type,
        "filter": _filter_from_request(request).query_params(),
    }, parent_task_id=root_id)
    if root_id is not None:
        q.resolve_task(conn, root_id)
    return {"task_id": task["id"], "already_active": task["already_active"]}
