from __future__ import annotations
import sqlite3
from typing import Optional
from urllib.parse import urlsplit
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from app.deps import get_db
from app.db import queries as q
from app.fetchers.slack import SlackFetcher
from app.fetchers.links import extract_links
from app.fetchers.content import has_enough_content, fetch_url_html, extract_page_title, FetchError
from app.fetchers.playwright_pool import render_html
from app.ai.classify_known_source import classify_known_source, DETECTABLE_FETCHER_TYPES
from app.ai.detect_listing import detect_listing
from app.ai.generate_source_name import generate_source_name
from app.pipeline import run_fetch
from app.task_engine import register_task_kind
from app.template_env import templates
from app.version import get_app_version, get_build_date

router = APIRouter()


def _slack_login_state(source: dict, stats_by_source: dict) -> str | None:
    """Local-only login state for the sources page: 'needs_login', 'unverified',
    or 'ok', derived entirely from the stored cookie and past fetch outcomes.
    Never makes a network call — that only happens on an explicit fetch or
    cookie save, never as a side effect of rendering this page."""
    if source["fetcher_type"] != "slack":
        return None
    try:
        SlackFetcher(source)  # validates the URL shape only, no network call
    except ValueError:
        return None
    if not source["d_cookie"]:
        return "needs_login"
    stats = stats_by_source.get(source["id"]) or {}
    last_success_at = stats.get("last_success_at")
    last_auth_error_at = stats.get("last_auth_error_at")
    if last_auth_error_at and (not last_success_at or last_auth_error_at > last_success_at):
        return "needs_login"
    if last_success_at:
        return "ok"
    return "unverified"


def _live_slack_check(source: dict) -> bool | None:
    """One-time live check against Slack, used only right after a cookie is
    saved so the user gets immediate pass/fail feedback on what they pasted."""
    try:
        return SlackFetcher(source).check_needs_login()
    except Exception:
        return None


def check_already_tracked_notice_data(conn: sqlite3.Connection, url: str) -> dict | None:
    existing_source = q.get_source_by_url(conn, url)
    if existing_source is not None:
        return {
            "level": "info",
            "html": templates.get_template("jobs/_already_tracked.html").render(
                request=None, kind="source",
                link_href=f"/sources#source-row-{existing_source['id']}",
                link_text=f'View "{existing_source["name"]}" in Sources',
            ),
        }
    existing_job = q.get_job_by_url(conn, url)
    if existing_job is not None:
        return {
            "level": "info",
            "html": templates.get_template("jobs/_already_tracked.html").render(
                request=None, kind="job", link_href=f"/jobs/{existing_job['id']}", link_text="View this job",
            ),
        }
    return None


def _visible_sources(conn: sqlite3.Connection) -> list[dict]:
    return [s for s in q.get_sources(conn) if s["fetcher_type"] != "manual"]


def _sources_context(conn: sqlite3.Connection) -> dict:
    sources = _visible_sources(conn)
    stats_by_source = q.get_fetch_stats_by_source(conn)
    needs_login_by_id = {
        s["id"]: _slack_login_state(s, stats_by_source) for s in sources if s["fetcher_type"] == "slack"
    }
    return {
        "sources": sources,
        "needs_login_by_id": needs_login_by_id,
        "job_counts_by_source": q.get_job_counts_by_source(conn),
    }


@router.get("/sources", response_class=HTMLResponse)
def sources_page(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    context = {**_sources_context(conn), "app_version": get_app_version(), "build_date": get_build_date()}
    return templates.TemplateResponse(request, "sources/index.html", context)


@register_task_kind("source_detect")
def _task_source_detect(conn, client, model, config, params):
    url = params["url"]
    already_tracked = check_already_tracked_notice_data(conn, url)
    if already_tracked is not None:
        return {"notices": [already_tracked], "html_chunks": []}

    default_name = urlsplit(url).netloc
    fetcher_type = classify_known_source(url)
    if fetcher_type is None:
        yield "Checking the page..."
        try:
            html = fetch_url_html(url)
        except FetchError:
            fetcher_type = "generic_listing"
        else:
            if not has_enough_content(html):
                rendered = render_html(url)
                if rendered and has_enough_content(rendered):
                    html = rendered
            page_title = extract_page_title(html)
            if page_title:
                generated_name = generate_source_name(client, model, default_name, page_title)
                if generated_name:
                    default_name = generated_name
            links = extract_links(html, url)
            detection = detect_listing(client, model, links, url)
            if detection["is_listing"] and len(detection["job_links"]) >= 2:
                fetcher_type = "generic_listing"

    if fetcher_type is not None:
        panel = templates.get_template("sources/_detect_confirm.html").render(
            request=None, url=url, name=default_name, fetcher_type=fetcher_type,
        )
    else:
        panel = templates.get_template("sources/_detect_mismatch.html").render(
            request=None, url=url, name=default_name,
        )
    return {
        "notices": [], "html_chunks": [panel],
        "needs_action": True,
        "action_message": f"New source detected: {default_name}",
        "resume_html": panel,
    }


@register_task_kind("source_confirm")
def _task_source_confirm(conn, client, model, config, params):
    url, name, fetcher_type = params["url"], params["name"], params["fetcher_type"]
    already_tracked = check_already_tracked_notice_data(conn, url)
    if already_tracked is not None:
        table = templates.get_template("sources/_table.html").render(request=None, **_sources_context(conn))
        add_form = templates.get_template("sources/_add_form.html").render(request=None)
        return {"notices": [already_tracked], "html_chunks": [table, add_form]}

    source_id = q.insert_source(conn, name, url, fetcher_type)
    source = q.get_source(conn, source_id)
    gen = run_fetch(source, conn, client, model, config.browser_profile_dir)
    fetch_result = None
    try:
        while True:
            yield next(gen)
    except StopIteration as stop:
        fetch_result = stop.value

    notices = []
    if fetch_result is not None and fetch_result.jobs_found == 0:
        notices.append({
            "level": "warning",
            "html": templates.get_template("sources/_fetch_nothing_found_notice.html").render(
                request=None, name=name, url=url, error=fetch_result.error, source_id=source_id,
            ),
        })

    table = templates.get_template("sources/_table.html").render(request=None, **_sources_context(conn))
    add_form = templates.get_template("sources/_add_form.html").render(request=None)
    return {"notices": notices, "html_chunks": [table, add_form]}


@router.post("/sources/detect")
def detect_source(url: str = Form(...), conn: sqlite3.Connection = Depends(get_db)):
    task = q.enqueue_task(conn, kind="source_detect", params={"url": url})
    return {"task_id": task["id"], "already_active": task["already_active"]}


@router.post("/sources/detect/confirm")
def confirm_source(
    url: str = Form(...), name: str = Form(...), fetcher_type: str = Form(...),
    conn: sqlite3.Connection = Depends(get_db),
):
    if fetcher_type not in DETECTABLE_FETCHER_TYPES:
        raise HTTPException(status_code=400, detail="Invalid fetcher_type")
    task = q.enqueue_task(
        conn, kind="source_confirm", params={"url": url, "name": name, "fetcher_type": fetcher_type}
    )
    return {"task_id": task["id"], "already_active": task["already_active"]}


def _get_source_or_404(conn: sqlite3.Connection, source_id: int) -> dict:
    source = q.get_source(conn, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    return source


@router.get("/sources/{source_id}/edit", response_class=HTMLResponse)
def edit_source_form(source_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    source = _get_source_or_404(conn, source_id)
    return templates.TemplateResponse(request, "sources/_row_edit.html", {"source": source})


@router.get("/sources/{source_id}", response_class=HTMLResponse)
def source_row(source_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    source = _get_source_or_404(conn, source_id)
    job_count = q.count_jobs_by_source(conn, source_id)
    return templates.TemplateResponse(request, "sources/_row.html", {"source": source, "job_count": job_count})


@router.post("/sources/{source_id}", response_class=HTMLResponse)
def update_source(
    source_id: int,
    request: Request,
    name: str = Form(...),
    url: str = Form(...),
    fetcher_type: str = Form(...),
    enabled: Optional[str] = Form(None),
    conn: sqlite3.Connection = Depends(get_db),
):
    _get_source_or_404(conn, source_id)
    q.update_source(
        conn, source_id, name=name, url=url, fetcher_type=fetcher_type, enabled=enabled is not None
    )
    source = q.get_source(conn, source_id)
    needs_login = _slack_login_state(source, q.get_fetch_stats_by_source(conn))
    job_count = q.count_jobs_by_source(conn, source_id)
    return templates.TemplateResponse(
        request, "sources/_row.html", {"source": source, "needs_login": needs_login, "job_count": job_count}
    )


@router.post("/sources/{source_id}/cookie", response_class=HTMLResponse)
def set_cookie(
    source_id: int,
    request: Request,
    d_cookie: str = Form(...),
    acknowledged: Optional[str] = Form(None),
    conn: sqlite3.Connection = Depends(get_db),
):
    _get_source_or_404(conn, source_id)
    if acknowledged is None:
        raise HTTPException(
            status_code=400,
            detail="You must acknowledge the security warning before saving the cookie.",
        )
    q.set_source_cookie(conn, source_id, d_cookie.strip())
    source = q.get_source(conn, source_id)
    live_result = _live_slack_check(source)
    if live_result is True:
        needs_login = "needs_login"
    elif live_result is False:
        needs_login = "ok"
    else:
        needs_login = _slack_login_state(source, q.get_fetch_stats_by_source(conn))
    job_count = q.count_jobs_by_source(conn, source_id)
    return templates.TemplateResponse(
        request, "sources/_row.html", {"source": source, "needs_login": needs_login, "job_count": job_count}
    )


@router.post("/sources/{source_id}/forget-cookie", response_class=HTMLResponse)
def forget_cookie(
    source_id: int,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
):
    _get_source_or_404(conn, source_id)
    q.set_source_cookie(conn, source_id, "")
    source = q.get_source(conn, source_id)
    needs_login = _slack_login_state(source, q.get_fetch_stats_by_source(conn))
    job_count = q.count_jobs_by_source(conn, source_id)
    return templates.TemplateResponse(
        request, "sources/_row.html", {"source": source, "needs_login": needs_login, "job_count": job_count}
    )


@router.get("/sources/{source_id}/delete-confirm", response_class=HTMLResponse)
def delete_source_confirm(source_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    source = _get_source_or_404(conn, source_id)
    job_count = q.count_jobs_by_source(conn, source_id)
    return templates.TemplateResponse(
        request, "sources/_row_delete_confirm.html", {"source": source, "job_count": job_count}
    )


@router.delete("/sources/{source_id}", response_class=HTMLResponse)
def delete_source(source_id: int, conn: sqlite3.Connection = Depends(get_db)):
    _get_source_or_404(conn, source_id)
    q.delete_source(conn, source_id)
    return HTMLResponse(content="")
