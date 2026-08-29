from __future__ import annotations
import re
import sqlite3
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from app.deps import get_db
from app.db import queries as q
from app.template_env import templates

router = APIRouter()

_PROGRESS_RE = re.compile(r"\[(\d+)/(\d+)\]")


def _progress_from_line(line: str) -> dict | None:
    matches = _PROGRESS_RE.findall(line)
    if not matches:
        return None
    current, total = (int(x) for x in matches[-1])
    if total <= 0:
        return None
    return {"current": current, "total": total, "percent": round(current / total * 100)}


def _task_label(conn: sqlite3.Connection, task: dict) -> str:
    if task["kind"] == "fetch_source":
        source = q.get_source(conn, task["params"].get("source_id"))
        if source:
            return f"Fetch: {source['name']}"
    return task["kind"].replace("_", " ")


_JOB_LINK_KINDS = {"job_reset", "job_pass_as_new", "job_reevaluate"}


def _task_link(task: dict) -> str | None:
    if task["kind"] in _JOB_LINK_KINDS:
        job_id = task["params"].get("job_id")
        if job_id is not None:
            return f"/jobs/{job_id}"
    return None


def _task_summary(conn: sqlite3.Connection, task: dict, *, include_result: bool = False) -> dict:
    log = task["log"].strip()
    last_line = log.split("\n")[-1] if log else ""
    summary = {
        "id": task["id"], "kind": task["kind"], "label": _task_label(conn, task), "status": task["status"],
        "last_line": last_line, "error": task["error"],
        "progress": _progress_from_line(last_line),
    }
    link = _task_link(task)
    if link:
        summary["link"] = link
    if include_result:
        summary["result"] = task["result"]
        summary["log"] = task["log"]
    return summary


@router.get("/tasks/active")
def tasks_active(conn: sqlite3.Connection = Depends(get_db)):
    return {
        "tasks": [_task_summary(conn, t) for t in q.get_active_tasks(conn)],
        "inbox_count": q.count_unresolved_inbox_items(conn),
    }


@router.get("/tasks/{task_id}")
def task_detail(task_id: int, conn: sqlite3.Connection = Depends(get_db)):
    task = q.get_task(conn, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return _task_summary(conn, task, include_result=True)


@router.get("/tasks/{task_id}/log", response_class=HTMLResponse)
def task_log(task_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    task = q.get_task(conn, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    lines = task["log"].strip().split("\n") if task["log"].strip() else []
    return templates.TemplateResponse(
        request, "tasks/log.html", {"task": task, "lines": lines, "job_link": _task_link(task)}
    )


@router.get("/tasks/{task_id}/resume", response_class=HTMLResponse)
def task_resume(task_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    task = q.get_task(conn, task_id)
    if task is None or not task.get("result") or not task["result"].get("resume_html"):
        raise HTTPException(status_code=404, detail="Nothing to resume for this task")
    inbox_item = q.get_inbox_item_by_task_id(conn, task_id)
    return templates.TemplateResponse(
        request, "tasks/resume.html", {
            "resume_html": task["result"]["resume_html"],
            "action_message": task["result"].get("action_message"),
            "inbox_item_id": inbox_item["id"] if inbox_item else None,
        }
    )
