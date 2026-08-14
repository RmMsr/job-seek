from __future__ import annotations
import sqlite3
from typing import Optional
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from app.deps import get_db, get_config
from app.db import queries as q
from app.fetchers.slack import SlackFetcher
from app.fetchers.slack_login import SlackCookieLogin
from app.pipeline import _make_fetcher
from app.template_env import templates

router = APIRouter()


def _check_needs_login(source: dict, config, conn: sqlite3.Connection) -> bool | None:
    if source["fetcher_type"] != "slack":
        return None
    try:
        fetcher = _make_fetcher(source, config.browser_profile_dir, conn)
        return fetcher.check_needs_login()
    except Exception:
        return None


@router.get("/sources", response_class=HTMLResponse)
def sources_page(request: Request, conn: sqlite3.Connection = Depends(get_db), config=Depends(get_config)):
    sources = [s for s in q.get_sources(conn) if s["fetcher_type"] != "manual"]
    needs_login_by_id = {
        s["id"]: _check_needs_login(s, config, conn) for s in sources if s["fetcher_type"] == "slack"
    }
    return templates.TemplateResponse(
        request, "sources/index.html", {"sources": sources, "needs_login_by_id": needs_login_by_id}
    )


@router.post("/sources", response_class=HTMLResponse)
def create_source(
    request: Request,
    name: str = Form(...),
    url: str = Form(...),
    fetcher_type: str = Form(...),
    conn: sqlite3.Connection = Depends(get_db),
    config=Depends(get_config),
):
    source_id = q.insert_source(conn, name, url, fetcher_type)
    source = q.get_source(conn, source_id)
    needs_login = _check_needs_login(source, config, conn)
    return templates.TemplateResponse(
        request,
        "sources/index.html",
        {"sources": q.get_sources(conn), "needs_login_by_id": {source_id: needs_login}},
    )


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
    return templates.TemplateResponse(request, "sources/_row.html", {"source": source})


@router.post("/sources/{source_id}", response_class=HTMLResponse)
def update_source(
    source_id: int,
    request: Request,
    name: str = Form(...),
    url: str = Form(...),
    fetcher_type: str = Form(...),
    enabled: Optional[str] = Form(None),
    conn: sqlite3.Connection = Depends(get_db),
    config=Depends(get_config),
):
    _get_source_or_404(conn, source_id)
    q.update_source(
        conn, source_id, name=name, url=url, fetcher_type=fetcher_type, enabled=enabled is not None
    )
    source = q.get_source(conn, source_id)
    needs_login = _check_needs_login(source, config, conn)
    return templates.TemplateResponse(
        request, "sources/_row.html", {"source": source, "needs_login": needs_login}
    )


@router.post("/sources/{source_id}/cookie", response_class=HTMLResponse)
def set_cookie(
    source_id: int,
    request: Request,
    d_cookie: str = Form(...),
    acknowledged: Optional[str] = Form(None),
    conn: sqlite3.Connection = Depends(get_db),
    config=Depends(get_config),
):
    _get_source_or_404(conn, source_id)
    if acknowledged is None:
        raise HTTPException(
            status_code=400,
            detail="You must acknowledge the security warning before saving the cookie.",
        )
    q.set_source_cookie(conn, source_id, d_cookie.strip())
    source = q.get_source(conn, source_id)
    needs_login = _check_needs_login(source, config, conn)
    return templates.TemplateResponse(
        request, "sources/_row.html", {"source": source, "needs_login": needs_login}
    )


@router.post("/sources/{source_id}/login")
def trigger_login(
    source_id: int,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    config=Depends(get_config),
):
    source = _get_source_or_404(conn, source_id)
    if source["fetcher_type"] != "slack":
        raise HTTPException(status_code=400, detail="Only Slack sources support browser login")
    login = SlackCookieLogin(source, config.browser_profile_dir)

    def stream():
        gen = login.login()
        cookie = None
        try:
            while True:
                yield next(gen) + "\n"
        except StopIteration as stop:
            cookie = stop.value
        if cookie:
            q.set_source_cookie(conn, source_id, cookie)
        source_after = q.get_source(conn, source_id)
        # A cookie returned by the login tool was already validated by reaching
        # the channel page, so trust it directly rather than re-validating over
        # the network (which the just-captured cookie may not survive in tests).
        needs_login = not cookie
        html = templates.get_template("sources/_row.html").render(
            request=request, source=source_after, needs_login=needs_login
        )
        yield "HTML:" + html.replace("\n", "")

    return StreamingResponse(stream(), media_type="text/plain")
