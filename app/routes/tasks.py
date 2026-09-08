from __future__ import annotations
import re
import sqlite3
from urllib.parse import urlparse, urlsplit
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from app.deps import get_db
from app.db import queries as q
from app import task_engine
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


def resolve_origin_task_id(raw: str | None) -> int | None:
    """A needs_action panel carries its origin action's *root* task id in a
    hidden `origin_task_id` field so a spawned follow-up step attaches under
    the right root. The panel HTML uses the "__ORIGIN_TASK__" sentinel, which
    the task engine substitutes; treat any leftover sentinel / blank / junk as
    "no root"."""
    if not raw or "__" in raw:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _job_title(conn: sqlite3.Connection, job_id) -> str | None:
    if job_id is None:
        return None
    job = q.get_job(conn, job_id)
    return job["title"] if job else None


_JOB_ACTION_LABELS = {
    "job_reset": "Reset job",
    "job_reevaluate": "Re-evaluate job",
    "job_pass_as_new": "Pass job as new",
}

_SINGLE_JOB_KINDS = {"job_reset", "job_pass_as_new", "job_reevaluate"}
_JOB_LINK_KINDS = _SINGLE_JOB_KINDS


def _single_revisit_job_id(task: dict) -> int | None:
    """A jobs_revisit task that targets exactly one job (the per-job Revisit
    button, or a one-job status-change check) — worth a 'View job' link. A
    multi-job sweep gets None."""
    if task["kind"] != "jobs_revisit":
        return None
    ids = task["params"].get("job_ids")
    return ids[0] if ids and len(ids) == 1 else None


def _title(conn: sqlite3.Connection, task: dict) -> str:
    kind = task["kind"]
    params = task["params"]
    if kind == "fetch_source":
        source = q.get_source(conn, params.get("source_id"))
        return f"Fetch: {source['name']}" if source else "fetch source"
    if kind == "fetch_all":
        return "Fetch all"
    if kind == "job_add_by_url":
        return "Add job by URL"
    if kind == "job_add_listing_source":
        name = params.get("name")
        return f"Add listing source: {name}" if name else "Add listing source"
    if kind == "source_detect":
        url = params.get("url") or ""
        host = urlsplit(url).netloc if url else ""
        return f"Add source: {host}" if host else "Add source"
    if kind == "source_confirm":
        name = params.get("name")
        return f"Add source: {name}" if name else "Add source"
    if kind in _JOB_ACTION_LABELS:
        base = _JOB_ACTION_LABELS[kind]
        title = _job_title(conn, params.get("job_id"))
        return f"{base}: {title}" if title else base
    if kind in ("jobs_bulk_reset", "jobs_bulk_reevaluate"):
        n = len(params.get("job_ids") or [])
        verb = "Reset" if kind == "jobs_bulk_reset" else "Re-evaluate"
        return f"{verb} {n} job{'s' if n != 1 else ''}"
    if kind == "scenario_reevaluate_one":
        scenario = q.get_scenario(conn, params.get("scenario_id"))
        return f"Re-evaluate: {scenario['name']}" if scenario else "Re-evaluate scenario"
    if kind == "profile_reassess_fit":
        return "Recompute profile fit"
    if kind == "cv_tailor":
        verb = "Evaluate CV directives" if params.get("mode") == "plan" else "Update CV"
        title = _job_title(conn, params.get("job_id"))
        return f"{verb}: {title}" if title else verb
    return kind.replace("_", " ")


# Back-compat alias — nothing outside this module imports it, but keeps the
# name meaningful in tests/greps.
_task_label = _title


def _goal(conn: sqlite3.Connection, task: dict) -> str:
    kind, params = task["kind"], task["params"]
    if kind == "fetch_all":
        return "Pull new postings from every enabled source"
    if kind == "fetch_source":
        src = q.get_source(conn, params.get("source_id"))
        return f"Pull new postings from {src['name']}" if src else "Pull new postings from a source"
    if kind == "job_add_by_url":
        url = params.get("url", "")
        return f"Add the job at {url[:60]}{'…' if len(url) > 60 else ''}"
    if kind in ("job_reevaluate", "jobs_bulk_reevaluate", "scenarios_reevaluate_all", "scenario_reevaluate_one"):
        return "Re-score against your scenarios and profile"
    if kind == "profile_reassess_fit":
        return "Recompute how well your profile fits each job"
    if kind in ("job_reset", "jobs_bulk_reset"):
        return "Re-run the full pipeline for the selected job(s)"
    if kind == "job_pass_as_new":
        return "Bypass the gate and re-score this job"
    if kind in ("scenarios_refine_all", "scenario_refine_one", "profile_refine"):
        return "Generate improvement suggestions from your feedback"
    if kind in ("source_detect", "source_confirm", "job_add_listing_source"):
        return f"Add {params.get('name') or params.get('url', 'a source')} as a source"
    if kind == "cv_tailor":
        if params.get("mode") == "plan":
            return "Check your tuning directives against this job and suggest changes"
        return "Regenerate the tailored CV from your current directives"
    return ""


def _queued_ahead(conn: sqlite3.Connection, task: dict) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM tasks WHERE status = 'queued' AND created_at < ?",
        (task["created_at"],),
    ).fetchone()
    return row[0]


def _done_summary(conn: sqlite3.Connection, task: dict) -> str:
    r = task.get("result") or {}
    kind = task["kind"]
    if kind == "fetch_source":
        n = r.get("jobs_new")
        if n is not None:
            return f"{n} new job{'s' if n != 1 else ''}" if n else "No new postings"
    if kind in ("source_confirm", "job_add_listing_source", "source_detect") and r.get("source_id"):
        src = q.get_source(conn, r["source_id"])
        return f"Source '{src['name']}' created" if src else "Source created"
    if kind == "job_add_by_url" and r.get("job_id"):
        return "Job added"
    return "Done"


def _next_step(conn: sqlite3.Connection, task: dict) -> str:
    status = task["status"]
    if status == "queued":
        n = _queued_ahead(conn, task)
        return f"Queued — {n} ahead" if n else "Queued"
    if status == "running":
        last = (task["log"].strip().split("\n") or [""])[-1]
        prog = _progress_from_line(last)
        if prog:
            return f"Step {prog['current']} of {prog['total']}"
        return last or "Running"
    if status == "needs_action":
        return (task.get("result") or {}).get("action_message") or "Needs your input"
    if status == "failed":
        return (task["error"] or "Failed").strip().split("\n")[0]
    if status == "dismissed":
        return "Dismissed"
    if status == "cancelled":
        return "Cancelled"
    return _done_summary(conn, task)


def _results(conn: sqlite3.Connection, task: dict) -> list[dict]:
    r = task.get("result") or {}
    kind, params = task["kind"], task["params"]
    out: list[dict] = []
    if kind == "fetch_source":
        sid = params.get("source_id")
        if sid:
            src = q.get_source(conn, sid)
            label = f"Jobs from {src['name']}" if src else "Jobs from this source"
            out.append({"label": label, "href": f"/jobs?source_id={sid}"})
    elif kind == "job_add_by_url" and r.get("job_id"):
        title = _job_title(conn, r["job_id"])
        out.append({"label": f"View {title}" if title else "View job", "href": f"/jobs/{r['job_id']}"})
    elif kind in _SINGLE_JOB_KINDS and params.get("job_id"):
        title = _job_title(conn, params["job_id"])
        out.append({"label": f"View {title}" if title else "View job", "href": f"/jobs/{params['job_id']}"})
    elif (rjid := _single_revisit_job_id(task)) is not None:
        title = _job_title(conn, rjid)
        out.append({"label": f"View {title}" if title else "View job", "href": f"/jobs/{rjid}"})
    elif kind == "cv_tailor" and params.get("job_id"):
        title = _job_title(conn, params["job_id"])
        out.append({"label": f"Open the CV for {title}" if title else "Open the CV workbench",
                    "href": f"/jobs/{params['job_id']}/cv"})
    elif kind in ("jobs_bulk_reset", "jobs_bulk_reevaluate"):
        out.append({"label": "Back to jobs", "href": "/jobs"})
    elif kind in ("source_confirm", "job_add_listing_source", "source_detect") and r.get("source_id"):
        out.append({"label": "View source", "href": f"/sources#source-{r['source_id']}"})
    elif kind in ("scenarios_refine_all", "scenario_refine_one", "scenarios_reevaluate_all"):
        out.append({"label": "Open Scenarios", "href": "/scenarios"})
    elif kind == "profile_refine":
        out.append({"label": "Review profile suggestions", "href": "/profile"})
    return out


def _link(conn: sqlite3.Connection, task: dict) -> str | None:
    if task["kind"] in _SINGLE_JOB_KINDS and task["params"].get("job_id") is not None:
        return f"/jobs/{task['params']['job_id']}"
    if (rjid := _single_revisit_job_id(task)) is not None:
        return f"/jobs/{rjid}"
    r = task.get("result") or {}
    if task["kind"] == "job_add_by_url" and r.get("job_id"):
        return f"/jobs/{r['job_id']}"
    return None


def _needs_you(task: dict | None) -> dict:
    """Fields the templates need to render a needs-you row/panel from a single
    (possibly-child) task's result. All-None when `task` isn't awaiting."""
    r = (task.get("result") or {}) if task is not None else {}
    if task is None or task["status"] != "needs_action":
        return {"needs_action_task_id": None, "action_message": None,
                "action_link": None, "has_panel": False}
    return {
        "needs_action_task_id": task["id"],
        "action_message": r.get("action_message") or "Needs your input",
        "action_link": r.get("action_link"),
        "has_panel": bool(r.get("resume_html")),
    }


def task_presentation(conn: sqlite3.Connection, task: dict) -> dict:
    awaiting = task["status"] == "needs_action"
    return {
        "title": _title(conn, task),
        "goal": _goal(conn, task),
        "next_step": _next_step(conn, task),
        "results": _results(conn, task),
        "link": _link(conn, task),
        "review_href": f"/tasks/{task['id']}" if awaiting else None,
        **_needs_you(task),
    }


def _subtree_status(subtree: list[dict]) -> str:
    """Derived state of a root task from its whole subtree (root + children):
    1) anything still queued/running -> running
    2) else anything needs_action  -> needs you
    3) else the root itself was cancelled (whole run stopped) -> cancelled
    4) else the latest-finished task failed -> failed
    5) else every child step was cancelled -> cancelled
    6) else -> done

    A single cancelled child does NOT make the run cancelled — cancellation
    only propagates downward from where the stop was issued, so a run whose
    other steps completed still reads as done.
    """
    if any(t["status"] in ("queued", "running") for t in subtree):
        return "running"
    if any(t["status"] == "needs_action" for t in subtree):
        return "needs_action"
    root = next((t for t in subtree if not t["parent_task_id"]), subtree[0])
    if root["status"] == "cancelled":
        return "cancelled"
    finished = [t for t in subtree if t["finished_at"]]
    if finished:
        latest = max(finished, key=lambda t: (t["finished_at"], t["id"]))
        if latest["status"] == "failed":
            return "failed"
    children = [t for t in subtree if t["parent_task_id"]]
    if children and all(t["status"] == "cancelled" for t in children):
        return "cancelled"
    return "done"


def _step_state(task: dict) -> str:
    """Per-step display status for the detail page's Steps list. A run's
    container row is re-marked 'cancelled' when the whole run is stopped
    (see task_stop); but its own kick-off work did finish, so show it as
    'done'. Everything else shows its real status."""
    if (task["parent_task_id"] is None and task["status"] == "cancelled"
            and task["result"] is not None):
        return "done"
    return task["status"]


def _child_step_counts(children: list[dict]) -> tuple[int, int, int]:
    total = len(children)
    settled = sum(1 for t in children if t["status"] not in ("queued", "running"))
    failed = sum(1 for t in children if t["status"] == "failed")
    return settled, total, failed


def root_presentation(conn: sqlite3.Connection, root: dict, children: list[dict]) -> dict:
    """Presentation for a root task that has spawned child steps — state,
    title, next-step and result links all derived from the subtree."""
    subtree = [root, *children]
    status = _subtree_status(subtree)
    kind = root["kind"]

    title = _title(conn, root)
    goal = _goal(conn, root)
    na = next((t for t in subtree if t["status"] == "needs_action"), None)

    if status == "needs_action":
        next_step = _next_step(conn, na)
    elif status == "failed":
        failed = [t for t in subtree if t["status"] == "failed"]
        next_step = (failed[-1]["error"] or "Failed").strip().split("\n")[0]
    elif status == "cancelled":
        next_step = "Cancelled"
    elif kind == "fetch_all":
        settled, total, failed = _child_step_counts(children)
        next_step = f"{total} source{'s' if total != 1 else ''} — {settled} done"
        if failed:
            next_step += f", {failed} failed"
        if status == "done":
            new = sum((t.get("result") or {}).get("jobs_new") or 0 for t in children)
            if new:
                next_step += f" — {new} new job{'s' if new != 1 else ''}"
    elif status == "running":
        active = next((t for t in subtree if t["status"] in ("queued", "running")), None)
        next_step = _next_step(conn, active) if active else "Working"
    else:  # done chain
        finished = [t for t in subtree if t["finished_at"]]
        last = max(finished, key=lambda t: (t["finished_at"], t["id"])) if finished else root
        next_step = _done_summary(conn, last)

    if kind == "fetch_all":
        # Per-source "Jobs from X" links would be a wall on one row; the child
        # steps below cover the run. Nothing worth a top-level link.
        results = []
    else:
        results, seen = [], set()
        for t in subtree:
            for r in _results(conn, t):
                key = (r["label"], r["href"])
                if key not in seen:
                    seen.add(key)
                    results.append(r)

    return {"title": title, "goal": goal, "next_step": next_step,
            "results": results, "status": status, "link": f"/tasks/{root['id']}",
            # Review on a root always goes to the root's detail page, which
            # surfaces the pending child's panel.
            "review_href": f"/tasks/{root['id']}" if status == "needs_action" else None,
            **_needs_you(na if status == "needs_action" else None)}


def _task_summary(conn: sqlite3.Connection, task: dict, *, include_result: bool = False) -> dict:
    pres = task_presentation(conn, task)
    log = task["log"].strip()
    last_line = log.split("\n")[-1] if log else ""
    summary = {
        "id": task["id"], "kind": task["kind"], "label": pres["title"],
        "goal": pres["goal"], "next_step": pres["next_step"], "results": pres["results"],
        "status": task["status"], "last_line": last_line, "error": task["error"],
        "progress": _progress_from_line(last_line), "parent_task_id": task["parent_task_id"],
        "action_message": pres["action_message"], "action_link": pres["action_link"],
    }
    if pres["link"]:
        summary["link"] = pres["link"]
    if include_result:
        summary["result"] = task["result"]
        summary["log"] = task["log"]
    return summary


def _was_needs_action(task: dict) -> bool:
    return (task.get("result") or {}).get("needs_action") is not None


def _root_summary(conn: sqlite3.Connection, root: dict, children: list[dict]) -> dict:
    """Status-bar payload for a root task, derived from its subtree."""
    pres = root_presentation(conn, root, children)
    active = [t for t in [root, *children] if t["status"] in ("queued", "running")]
    progress = None
    if len(active) == 1:
        log = active[0]["log"].strip()
        progress = _progress_from_line(log.split("\n")[-1]) if log else None
    if progress is None and children:
        settled = sum(1 for t in children if t["status"] not in ("queued", "running"))
        progress = {
            "current": settled, "total": len(children),
            "percent": round(settled / len(children) * 100),
        }
    return {
        "id": root["id"], "kind": root["kind"], "label": pres["title"],
        "goal": pres["goal"], "next_step": pres["next_step"], "results": pres["results"],
        "status": pres["status"], "last_line": "", "error": root["error"],
        "progress": progress, "parent_task_id": None,
        "action_message": pres["action_message"], "action_link": pres["action_link"],
    }


@router.get("/tasks/active")
def tasks_active(conn: sqlite3.Connection = Depends(get_db)):
    seen: list[int] = []
    entries = []
    for t in q.get_active_tasks(conn):
        rid = t["parent_task_id"] or t["id"]
        if rid in seen:
            continue
        seen.append(rid)
        root = q.get_task(conn, rid)
        if root is None:
            continue
        children = q.get_task_children(conn, rid)
        if children or root["kind"] == "fetch_all":
            entries.append(_root_summary(conn, root, children))
        else:
            entries.append(_task_summary(conn, root))
    return {"tasks": entries, "inbox_count": q.count_unresolved_inbox_items(conn)}


@router.get("/tasks", response_class=HTMLResponse)
def task_history(request: Request, status: str = "all", page: int = 1,
                 conn: sqlite3.Connection = Depends(get_db)):
    per = 50
    page = max(1, page)
    status_filter = None if status in ("all", "active") else status
    rows = q.get_recent_terminal_tasks(
        conn, limit=per + 1, offset=(page - 1) * per, status=status_filter
    )
    if status == "active":
        rows = [t for t in rows if t["status"] in ("queued", "running", "needs_action")]
    has_next = len(rows) > per
    rows = rows[:per]

    def _entry(t):
        children = q.get_task_children(conn, t["id"])
        if children:
            pres = root_presentation(conn, t, children)
            return {"task": t, "pres": pres, "state": pres["status"]}
        return {"task": t, "pres": task_presentation(conn, t), "state": t["status"]}

    # Group the page's rows by their root. Pull in any root / sibling not on
    # this page so a group is never shown half-populated.
    roots: dict[int, dict] = {}
    order: list[int] = []
    for t in rows:
        rid = t["parent_task_id"] or t["id"]
        if rid not in roots:
            root_task = t if t["id"] == rid else q.get_task(conn, rid)
            if root_task is None:
                continue
            roots[rid] = {"root": _entry(root_task), "children": []}
            order.append(rid)
    for rid in order:
        child_tasks = q.get_task_children(conn, rid)
        roots[rid]["children"] = [
            {"task": c, "pres": task_presentation(conn, c), "state": c["status"]}
            for c in child_tasks
        ]

    groups = [roots[r] for r in order]
    return templates.TemplateResponse(request, "tasks/list.html", {
        "groups": groups, "status": status, "page": page, "has_next": has_next,
    })


@router.get("/tasks/{task_id}/state")
def task_state(task_id: int, conn: sqlite3.Connection = Depends(get_db)):
    task = q.get_task(conn, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return _task_summary(conn, task, include_result=True)


@router.get("/tasks/{task_id}/log")
def task_log_redirect(task_id: int):
    return RedirectResponse(f"/tasks/{task_id}", status_code=301)


@router.get("/tasks/{task_id}/resume")
def task_resume_redirect(task_id: int):
    return RedirectResponse(f"/tasks/{task_id}", status_code=301)


@router.get("/tasks/{task_id}", response_class=HTMLResponse)
def task_detail(task_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    task = q.get_task(conn, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    children = q.get_task_children(conn, task_id)

    # Breadcrumb up to the root for a child step.
    parent = None
    if task["parent_task_id"]:
        root = q.get_task(conn, task["parent_task_id"])
        if root is not None:
            parent = {"id": root["id"], "title": _title(conn, root)}

    if children:
        subtree = [task, *children]
        pres = root_presentation(conn, task, children)
        steps = []
        for t in subtree:
            state = _step_state(t)
            pt = {**t, "status": "done"} if state != t["status"] else t
            steps.append({"task": t, "pres": task_presentation(conn, pt), "state": state})
        na = next((t for t in subtree if t["status"] == "needs_action"), None)
        resume_html = (na.get("result") or {}).get("resume_html") if na else None
        resolved_panel = pres["status"] == "cancelled" or (
            pres["status"] == "done" and any(_was_needs_action(t) for t in subtree)
        )
        return templates.TemplateResponse(request, "tasks/detail.html", {
            "is_group": True, "task": task, "pres": pres, "steps": steps,
            "resume_html": resume_html,
            "awaiting": pres["status"] == "needs_action",
            "panel_task_id": na["id"] if na else task_id,
            "resolved_panel": resolved_panel, "parent": parent,
            "active": pres["status"] == "running",
        })

    pres = task_presentation(conn, task)
    lines = task["log"].strip().split("\n") if task["log"].strip() else []
    resume_html = (
        (task.get("result") or {}).get("resume_html")
        if task["status"] == "needs_action" else None
    )
    resolved_panel = (
        task["status"] == "cancelled"
        or (task["status"] in ("done", "dismissed") and _was_needs_action(task))
    )
    return templates.TemplateResponse(request, "tasks/detail.html", {
        "is_group": False, "task": task, "pres": pres, "lines": lines,
        "resume_html": resume_html, "panel_task_id": task_id,
        "awaiting": task["status"] == "needs_action",
        "resolved_panel": resolved_panel, "parent": parent,
        "active": task["status"] in ("queued", "running"),
    })


@router.post("/tasks/{task_id}/dismiss")
def task_dismiss(task_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    task = q.get_task(conn, task_id)
    if task is not None and task["status"] == "needs_action":
        q.dismiss_task(conn, task_id)
    ref = request.headers.get("referer") or "/"
    dest = ref if urlparse(ref).netloc == urlparse(str(request.url)).netloc else "/"
    return RedirectResponse(dest, status_code=303)


@router.post("/tasks/{task_id}/stop")
def task_stop(task_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    task = q.get_task(conn, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    # Stop propagates DOWNWARD only: the clicked task plus its child steps —
    # never its parent or siblings. (Chains are flattened, so a task's
    # children are its whole downward set.)
    children = q.get_task_children(conn, task_id)
    subtree = [task, *children]
    q.cancel_queued_tasks(conn, [t["id"] for t in subtree if t["status"] == "queued"])
    for t in subtree:
        if t["status"] == "running":
            task_engine.request_cancel(t["id"])
    if children and task["status"] not in ("queued", "running"):
        # Stopping a whole run: mark the (already-finished) container row
        # cancelled so the run reads as cancelled even if its kick-off step
        # and some children had completed. The kick-off's own completion is
        # still shown per-step.
        q.cancel_task(conn, task_id)
    ref = request.headers.get("referer") or "/"
    dest = ref if urlparse(ref).netloc == urlparse(str(request.url)).netloc else "/"
    return RedirectResponse(dest, status_code=303)
