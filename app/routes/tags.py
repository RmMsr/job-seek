from __future__ import annotations
import sqlite3
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from app.db import queries as q
from app.deps import get_db
from app.job_filter import JobFilter
from app.template_env import templates

router = APIRouter()


def _fragment(request: Request, conn: sqlite3.Connection, job_id: int, *,
              trigger: str, open_tag_input: bool = False) -> HTMLResponse:
    job = q.get_job_with_source_name(conn, job_id)
    detail = request.query_params.get("detail") == "1"
    f = None if detail else JobFilter.from_params(request.query_params)
    resp = templates.TemplateResponse(
        request, "jobs/_meta_tags_fragment.html",
        {"job": job, "filter": f, "open_tag_input": open_tag_input},
    )
    resp.headers["HX-Trigger"] = trigger
    return resp


def _require_job(conn: sqlite3.Connection, job_id: int) -> None:
    if q.get_job(conn, job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")


@router.post("/jobs/{job_id}/tags", response_class=HTMLResponse)
def add_tag(job_id: int, request: Request, name: str = Form(""),
            conn: sqlite3.Connection = Depends(get_db)):
    _require_job(conn, job_id)
    q.add_job_tag(conn, job_id, name)
    return _fragment(request, conn, job_id, trigger="tags-changed", open_tag_input=True)


@router.delete("/jobs/{job_id}/tags/{tag_id}", response_class=HTMLResponse)
def remove_tag(job_id: int, tag_id: int, request: Request,
               conn: sqlite3.Connection = Depends(get_db)):
    _require_job(conn, job_id)
    q.remove_job_tag(conn, job_id, tag_id)
    return _fragment(request, conn, job_id, trigger="tags-changed")


@router.post("/jobs/{job_id}/organization", response_class=HTMLResponse)
def set_organization(job_id: int, request: Request, name: str = Form(""),
                     conn: sqlite3.Connection = Depends(get_db)):
    _require_job(conn, job_id)
    q.set_company_override(conn, job_id, name)
    return _fragment(request, conn, job_id, trigger="orgs-changed")


@router.get("/tags/options", response_class=HTMLResponse)
def tag_options(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    return templates.TemplateResponse(
        request, "tags/_options.html", {"values": q.get_tag_names_by_usage(conn)})


@router.get("/orgs/options", response_class=HTMLResponse)
def org_options(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    return templates.TemplateResponse(
        request, "tags/_options.html", {"values": q.get_distinct_companies(conn)})
