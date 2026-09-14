from __future__ import annotations
import sqlite3
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from app.config import check_config_status
from app.deps import get_db_optional
from app.db import queries as q
from app.routes.tasks import task_presentation, root_presentation
from app.template_env import templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    conn: sqlite3.Connection | None = Depends(get_db_optional),
):
    config_status = check_config_status()
    context = {"config_status": config_status}

    if conn is not None:
        counts = q.get_job_counts(conn)
        checklist = [
            {"label": "Fill in your profile", "done": bool(q.get_profile(conn).strip()), "href": "/profile"},
            {"label": "Create a search scenario", "done": bool(q.get_scenarios(conn)), "href": "/scenarios"},
            {"label": "Add a source", "done": bool(q.get_sources(conn)), "href": "/sources"},
            {"label": "Run your first fetch", "done": q.has_completed_fetch_run(conn), "href": "/fetch"},
            {
                "label": "Review your first job",
                "done": (counts["accepted"] + counts["pending"] + counts["rejected"] + counts["trash"]) > 0,
                "href": "/jobs",
            },
        ]
        context["checklist"] = checklist
        context["onboarding_complete"] = all(item["done"] for item in checklist)
        context["jobs_new"] = counts["new"]
        context["last_fetch_at"] = q.get_last_fetch_completed_at(conn)
        entries = q.get_dashboard_tasks(conn)
        for e in entries:
            e["pres"] = (
                root_presentation(conn, e["root"], e["children"]) if e["children"]
                else task_presentation(conn, e["root"])
            )
        context["task_entries"] = entries
        context["standalone_notices"] = q.get_unresolved_inbox_items(conn)

    return templates.TemplateResponse(request, "home/index.html", context)
