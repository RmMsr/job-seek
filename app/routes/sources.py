from __future__ import annotations
import sqlite3
from typing import Optional
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from app.deps import get_db, get_config
from app.db import queries as q
from app.fetchers.playwright_base import PlaywrightFetcher
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
def sources_page(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    return templates.TemplateResponse(request, "sources/index.html", {"sources": q.get_sources(conn)})


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


@router.post("/sources/{source_id}/login")
def trigger_login(
    source_id: int,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    config=Depends(get_config),
):
    source = _get_source_or_404(conn, source_id)
    fetcher = _make_fetcher(source, config.browser_profile_dir, conn)
    if not isinstance(fetcher, PlaywrightFetcher):
        raise HTTPException(status_code=400, detail="This source type doesn't support interactive login")

    def stream():
        gen = fetcher.login()
        login_ok = False
        try:
            while True:
                yield next(gen) + "\n"
        except StopIteration as stop:
            login_ok = bool(stop.value)
        source_after = q.get_source(conn, source_id)
        html = templates.get_template("sources/_row.html").render(
            request=request, source=source_after, needs_login=not login_ok
        )
        yield "HTML:" + html.replace("\n", "")

    return StreamingResponse(stream(), media_type="text/plain")
