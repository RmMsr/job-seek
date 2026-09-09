from __future__ import annotations
import hashlib
import logging
import sqlite3
import time
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from app.db import queries as q
from app.deps import get_db
from app.markdown_render import markdown_to_text
from app.task_engine import register_task_kind
from app.ai.tailor_cv import plan_tailoring, tailor_cv, check_guardrails
from app.cv.instruction import (
    compose_instruction, DEFAULT_GUARDRAILS, resolve_directive_proposals, apply_directive_proposals,
)
from app.cv.sanitize import validate_css
import markdown as _markdown
from app.template_env import templates
from app.cv.cv_diff import build as build_cv_diff
from app.cv.render import (
    render_preview_html, render_diff_html, render_pdf, doc_write_available, CvRenderError,
)
from app.version import get_app_version, get_build_date

router = APIRouter()

logger = logging.getLogger("job_seek")

_JOB_CONTEXT_CAP = 9000


def _job_context(job: dict) -> str:
    body = job.get("simplified_content") or job.get("summary") or job.get("raw_text") or ""
    prose = markdown_to_text(body)[:_JOB_CONTEXT_CAP]
    return (
        f"Title: {job.get('title', '')}\n"
        f"Company: {job.get('company', '') or '(unknown)'}\n\n"
        f"{prose}"
    )


def _job_notes(conn, job: dict) -> str:
    lines: list[str] = []
    note = (job.get("feedback_note") or "").strip()
    if note:
        status = (job.get("status") or "new").upper()
        lines.append(f"[{status}] {note}")
    rows = conn.execute(
        "SELECT note FROM scenario_feedback "
        "WHERE job_id = ? AND note != '' AND direction IS NOT NULL AND handled_at IS NULL",
        (job["id"],),
    ).fetchall()
    lines.extend(r["note"].strip() for r in rows if r["note"].strip())
    return "\n".join(lines)


def _base_hash(settings: dict) -> str:
    raw = "\x00".join([
        settings.get("base_cv", ""),
        settings.get("base_instruction", ""),
        settings.get("base_guardrails", ""),
    ])
    return hashlib.sha256(raw.encode()).hexdigest()


def _plan_context_hash(job_context: str, job_notes: str) -> str:
    """Identifies the inputs a plan run saw (posting + notes) so the workbench can
    show the plan stage as stale when they change."""
    return hashlib.sha256(f"{job_context}\x00{job_notes}".encode()).hexdigest()


def _persist_draft(conn, job_id: int, *, draft: str, findings: list, settings: dict) -> None:
    """Store a freshly generated draft and stamp generated_at. If the guardrail
    check came back empty while guardrails ARE configured, keep the previous
    findings — an empty result there is almost always a transient LLM/JSON
    failure, and silently blanking the guardrails panel is worse than showing a
    slightly stale check."""
    fields: dict = {
        "tailored_cv": draft,
        "base_hash": _base_hash(settings),
        "base_cv_snapshot": settings.get("base_cv", ""),
    }
    if findings or not settings.get("base_guardrails", "").strip():
        fields["guardrail_findings"] = findings
    else:
        logger.warning("cv_tailor: guardrail check returned nothing for job %s — keeping prior findings", job_id)
    q.upsert_job_cv(conn, job_id, **fields)
    conn.execute("UPDATE job_cv SET generated_at = datetime('now') WHERE job_id = ?", (job_id,))
    conn.commit()


def _draft_stale(job_cv: dict | None, settings: dict) -> bool:
    """The shown tailored CV no longer reflects its inputs — base CV / style /
    guardrails changed, or the directives or edit latitude were changed since it
    was generated."""
    if not job_cv or not job_cv.get("tailored_cv"):
        return False
    if job_cv.get("base_hash", "") != _base_hash(settings):
        return True
    ge = job_cv.get("generated_at")
    if not ge:
        return False
    de = job_cv.get("directives_edited_at")
    se = job_cv.get("scope_edited_at")
    return bool((de and de > ge) or (se and se > ge))


def _plan_status(conn: sqlite3.Connection, job: dict, job_cv: dict | None, running: bool) -> str:
    if running:
        return "running"
    if not job_cv or not job_cv.get("plan_generated_at"):
        return "none"
    stored = job_cv.get("plan_context_hash")
    if not stored:                      # plan predates hash tracking — don't nag
        return "fresh"
    current = _plan_context_hash(_job_context(job), _job_notes(conn, job))
    return "fresh" if stored == current else "stale"


def _draft_status(job_cv: dict | None, settings: dict, running: bool) -> str:
    if running:
        return "running"
    if not job_cv or not job_cv.get("tailored_cv"):
        return "none"
    return "stale" if _draft_stale(job_cv, settings) else "fresh"


def _guardrail_status(job_cv: dict | None, settings: dict, running: bool) -> str:
    if running:
        return "running"
    if not job_cv or not job_cv.get("tailored_cv") or not job_cv.get("guardrail_findings"):
        return "none"
    return "stale" if _draft_stale(job_cv, settings) else "fresh"


def _cv_page_ctx(conn: sqlite3.Connection) -> dict:
    return {
        "settings": q.get_cv_settings(conn),
        "app_version": get_app_version(),
        "build_date": get_build_date(),
    }


def _advanced_ctx(conn: sqlite3.Connection) -> dict:
    return {
        "settings": q.get_cv_settings(conn),
        "scope_options": q.get_scope_options(conn),
        "app_version": get_app_version(),
        "build_date": get_build_date(),
    }


def _rendered_chunk(conn: sqlite3.Connection, job_id: int, which: str, **ctx_overrides) -> str:
    ctx = _workbench_ctx(conn, job_id)
    ctx.update(ctx_overrides)
    # Stage badges are derived from the running-task ids; when a caller clears
    # those (a finishing task rendering its own result), re-derive the badges so
    # they don't stick on "Working…".
    if "updating_task_id" in ctx_overrides:
        run = bool(ctx["updating_task_id"])
        ctx["draft_status"] = _draft_status(ctx["job_cv"], ctx["settings"], run)
        ctx["guardrail_status"] = _guardrail_status(ctx["job_cv"], ctx["settings"], run)
    if "plan_task_id" in ctx_overrides and ctx["job"]:
        ctx["plan_status"] = _plan_status(
            conn, ctx["job"], ctx["job_cv"], bool(ctx["plan_task_id"])
        )
    inner = "cv/_plan_pane.html" if which == "plan_pane" else "cv/_preview_pane.html"
    wrapper = "cv-plan-pane" if which == "plan_pane" else "cv-preview-pane"
    body = templates.get_template(inner).render(request=None, **ctx)
    return f'<div id="{wrapper}">{body}</div>'


def _tailor_result(conn: sqlite3.Connection, job_id: int, params: dict) -> dict:
    result: dict = {"job_id": job_id}
    which = params.get("render")
    if which:
        # This can run from inside a finishing cv_tailor task (either the 'generate'
        # or the 'plan' mode) — it's still 'running' in the DB, so _workbench_ctx
        # would see it as an in-flight regen. It isn't: the updating_task_id=None
        # override keeps this render from painting the pane as still-updating.
        panes = [which] + (["plan_pane"] if which == "preview_pane" else [])
        chunks = []
        for p in panes:
            overrides = {"updating_task_id": None}
            if p == "plan_pane":
                overrides["plan_task_id"] = None
            chunks.append(_rendered_chunk(conn, job_id, p, **overrides))
        result["html_chunks"] = chunks
    return result


def _cv_diff_view(job_cv: dict | None) -> dict | None:
    """The change digest + added-content list shown in the preview pane above the
    tabs. Cheap pure-Python diff (no LLM, no doc-write); None when there's no
    draft or no base snapshot to diff against (pre-tracking drafts)."""
    if not job_cv or not job_cv.get("tailored_cv") or not job_cv.get("base_cv_snapshot"):
        return None
    try:
        r = build_cv_diff(job_cv["base_cv_snapshot"], job_cv["tailored_cv"])
    except Exception:
        logger.exception("cv_diff summary build failed for job %s", job_cv.get("job_id"))
        return None
    return {"summary_line": r.summary_line, "added_items": r.added_items, "is_empty": r.is_empty}


def _workbench_ctx(conn: sqlite3.Connection, job_id: int) -> dict:
    job = q.get_job(conn, job_id)
    job_cv = q.get_job_cv(conn, job_id)
    settings = q.get_cv_settings(conn)
    updating = q.cv_generate_task_id(conn, job_id)
    planning = q.cv_plan_task_id(conn, job_id)
    return {
        "job": job,
        "job_cv": job_cv,
        "settings": settings,
        "scope_options": q.get_scope_options(conn),
        "updating_task_id": updating,
        "plan_task_id": planning,
        "plan_status": _plan_status(conn, job, job_cv, bool(planning)) if job else "none",
        "draft_status": _draft_status(job_cv, settings, bool(updating)),
        "guardrail_status": _guardrail_status(job_cv, settings, bool(updating)),
        "has_doc_write": doc_write_available(),
        "cv_diff_view": _cv_diff_view(job_cv),
    }


@router.get(
    "/jobs/{job_id}/cv", response_class=HTMLResponse)
def cv_workbench(job_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    if q.get_job(conn, job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return templates.TemplateResponse(request, "cv/workbench.html", _workbench_ctx(conn, job_id))


@router.get(
    "/jobs/{job_id}/cv/preview.html", response_class=HTMLResponse)
def cv_preview_html(job_id: int, variant: str = "tailored",
                    conn: sqlite3.Connection = Depends(get_db)):
    job = q.get_job(conn, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    settings = q.get_cv_settings(conn)
    if variant == "base":
        markdown = settings["base_cv"]
    else:
        row = q.get_job_cv(conn, job_id)
        if row is None or not row["tailored_cv"]:
            raise HTTPException(status_code=404, detail="No tailored CV")
        markdown = row["tailored_cv"]
    if not doc_write_available():
        raise HTTPException(status_code=503, detail="doc-write-cli is not installed")
    try:
        return HTMLResponse(render_preview_html(markdown, settings["css"]))
    except CvRenderError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.get(
    "/jobs/{job_id}/cv/diff.html", response_class=HTMLResponse)
def cv_diff_html(job_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    job = q.get_job(conn, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    row = q.get_job_cv(conn, job_id)
    if row is None or not row["tailored_cv"]:
        raise HTTPException(status_code=404, detail="No tailored CV")
    if not row["base_cv_snapshot"]:
        return templates.TemplateResponse(
            request, "cv/_cv_diff_fallback.html", {"predates": True, "body_html": ""},
        )
    settings = q.get_cv_settings(conn)
    try:
        annotated = build_cv_diff(row["base_cv_snapshot"], row["tailored_cv"]).annotated_markdown
    except Exception:
        logger.exception("cv_diff build failed for job %s", job_id)
        annotated = row["tailored_cv"]  # fall back to the plain document
    if doc_write_available():
        try:
            return HTMLResponse(render_diff_html(annotated, settings["css"]))
        except CvRenderError as exc:
            raise HTTPException(status_code=503, detail=str(exc))
    body_html = _markdown.markdown(annotated, extensions=["nl2br"])
    return templates.TemplateResponse(
        request, "cv/_cv_diff_fallback.html", {"predates": False, "body_html": body_html},
    )


@router.get("/cv", response_class=HTMLResponse)
def cv_page(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    return templates.TemplateResponse(request, "cv/index.html", _cv_page_ctx(conn))


@router.post("/cv", response_class=HTMLResponse)
async def cv_save(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    form = await request.form()
    current = q.get_cv_settings(conn)
    q.save_cv_settings(
        conn, base_cv=form.get("base_cv", ""), base_instruction=current["base_instruction"],
        base_guardrails=current["base_guardrails"], css=current["css"],
        default_scope=current["default_scope"],
        directives_template=current["directives_template"],
    )
    ctx = _cv_page_ctx(conn)
    ctx["saved"] = True
    ctx["has_doc_write"] = doc_write_available()
    return templates.TemplateResponse(request, "cv/index.html", ctx)


@router.get("/cv/preview.html", response_class=HTMLResponse)
def cv_preview_base_html(conn: sqlite3.Connection = Depends(get_db)):
    settings = q.get_cv_settings(conn)
    if not doc_write_available():
        raise HTTPException(status_code=503, detail="doc-write-cli is not installed")
    try:
        return HTMLResponse(render_preview_html(settings["base_cv"], settings["css"]))
    except CvRenderError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.get("/cv/advanced", response_class=HTMLResponse)
def cv_advanced_page(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    ctx = _advanced_ctx(conn)
    ctx["saved"] = request.query_params.get("saved")
    return templates.TemplateResponse(request, "cv/advanced.html", ctx)


# The save/reset routes below follow the codebase's post-redirect-get pattern
# (see routes/setup.py): persist, then 303 to the GET with ?saved=<section> so
# the URL stays on /cv/advanced and a reload can't re-submit.

@router.post("/cv/save-style")
async def cv_save_style(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    form = await request.form()
    current = q.get_cv_settings(conn)
    q.save_cv_settings(
        conn, base_cv=current["base_cv"], base_instruction=form.get("base_instruction", ""),
        base_guardrails=current["base_guardrails"], css=current["css"],
        default_scope=current["default_scope"],
        directives_template=current["directives_template"],
    )
    return RedirectResponse("/cv/advanced?saved=style", status_code=303)


@router.post("/cv/save-guardrails")
async def cv_save_guardrails(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    form = await request.form()
    current = q.get_cv_settings(conn)
    q.save_cv_settings(
        conn, base_cv=current["base_cv"], base_instruction=current["base_instruction"],
        base_guardrails=form.get("base_guardrails", ""), css=current["css"],
        default_scope=current["default_scope"],
        directives_template=current["directives_template"],
    )
    return RedirectResponse("/cv/advanced?saved=guardrails", status_code=303)


@router.post("/cv/save-css", response_class=HTMLResponse)
async def cv_save_css(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    form = await request.form()
    css = form.get("css", "")
    err = validate_css(css)
    current = q.get_cv_settings(conn)
    if err:
        # Invalid CSS: re-render in place so the bad input and the error stay on
        # screen together — no redirect (nothing was saved).
        ctx = _advanced_ctx(conn)
        ctx["error"] = err
        ctx["settings"] = {**ctx["settings"], "css": css}
        return templates.TemplateResponse(request, "cv/advanced.html", ctx)
    q.save_cv_settings(
        conn, base_cv=current["base_cv"], base_instruction=current["base_instruction"],
        base_guardrails=current["base_guardrails"], css=css, default_scope=current["default_scope"],
        directives_template=current["directives_template"],
    )
    return RedirectResponse("/cv/advanced?saved=css", status_code=303)


@router.post("/cv/reset-guardrails")
def cv_reset_guardrails(conn: sqlite3.Connection = Depends(get_db)):
    settings = q.get_cv_settings(conn)
    q.save_cv_settings(
        conn, base_cv=settings["base_cv"], base_instruction=settings["base_instruction"],
        base_guardrails=DEFAULT_GUARDRAILS, css=settings["css"], default_scope=settings["default_scope"],
        directives_template=settings["directives_template"],
    )
    return RedirectResponse("/cv/advanced?saved=guardrails", status_code=303)


@router.post("/cv/reset-style")
def cv_reset_style(conn: sqlite3.Connection = Depends(get_db)):
    settings = q.get_cv_settings(conn)
    q.save_cv_settings(
        conn, base_cv=settings["base_cv"], base_instruction="",
        base_guardrails=settings["base_guardrails"], css=settings["css"],
        default_scope=settings["default_scope"],
        directives_template=settings["directives_template"],
    )
    return RedirectResponse("/cv/advanced?saved=style", status_code=303)


@router.post("/cv/reset-css")
def cv_reset_css(conn: sqlite3.Connection = Depends(get_db)):
    settings = q.get_cv_settings(conn)
    q.save_cv_settings(
        conn, base_cv=settings["base_cv"], base_instruction=settings["base_instruction"],
        base_guardrails=settings["base_guardrails"], css="",
        default_scope=settings["default_scope"],
        directives_template=settings["directives_template"],
    )
    return RedirectResponse("/cv/advanced?saved=css", status_code=303)


@router.post("/cv/save-directives-template")
async def cv_save_directives_template(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    form = await request.form()
    c = q.get_cv_settings(conn)
    q.save_cv_settings(conn, base_cv=c["base_cv"], base_instruction=c["base_instruction"],
                       base_guardrails=c["base_guardrails"], css=c["css"],
                       default_scope=c["default_scope"],
                       directives_template=form.get("directives_template", ""))
    return RedirectResponse("/cv/advanced?saved=directives_template", status_code=303)


@router.post("/cv/reset-directives-template")
def cv_reset_directives_template(conn: sqlite3.Connection = Depends(get_db)):
    from app.cv.instruction import DEFAULT_DIRECTIVES_TEMPLATE
    c = q.get_cv_settings(conn)
    q.save_cv_settings(conn, base_cv=c["base_cv"], base_instruction=c["base_instruction"],
                       base_guardrails=c["base_guardrails"], css=c["css"],
                       default_scope=c["default_scope"],
                       directives_template=DEFAULT_DIRECTIVES_TEMPLATE)
    return RedirectResponse("/cv/advanced?saved=directives_template", status_code=303)


@router.post("/cv/scope-options", response_class=HTMLResponse)
async def add_scope_option(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    form = await request.form()
    description = form.get("description", "").strip()
    if description:
        q.insert_scope_option(conn, description, name=form.get("name", "").strip(),
                              default_enabled=bool(form.get("default_enabled")))
    return templates.TemplateResponse(
        request, "cv/_scope_options.html", {"scope_options": q.get_scope_options(conn)}
    )


@router.post("/cv/scope-options/reset", response_class=HTMLResponse)
def reset_scope_options_route(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    # Registered before the dynamic /cv/scope-options/{scope_option_id} DELETE
    # route below — FastAPI matches path routes in registration order, and a
    # POST to .../reset would otherwise be swallowed by a dynamic route
    # first, which then 422s trying to parse "reset" as an int id.
    q.reset_scope_options(conn)
    return templates.TemplateResponse(
        request, "cv/_scope_options.html", {"scope_options": q.get_scope_options(conn)}
    )


@router.post("/cv/scope-options/save-all", response_class=HTMLResponse)
async def save_all_scope_options_route(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    # Registered before the dynamic /cv/scope-options/{scope_option_id} DELETE
    # route below, same reasoning as reset above.
    form = await request.form()
    ids = [int(i) for i in form.getlist("id")]
    names = form.getlist("name")
    descriptions = form.getlist("description")
    enabled_ids = {int(i) for i in form.getlist("default_enabled")}
    for opt_id, name, description in zip(ids, names, descriptions):
        description = description.strip()
        if description:
            q.update_scope_option(conn, opt_id, description, default_enabled=opt_id in enabled_ids,
                                  name=name.strip())
    return templates.TemplateResponse(
        request, "cv/_scope_options.html",
        {"scope_options": q.get_scope_options(conn), "saved": "scope"},
    )


@router.delete("/cv/scope-options/{scope_option_id}", response_class=HTMLResponse)
def delete_scope_option_route(scope_option_id: int, conn: sqlite3.Connection = Depends(get_db)):
    q.delete_scope_option(conn, scope_option_id)
    return HTMLResponse(content="")


@register_task_kind("cv_tailor")
def _task_cv_tailor(conn, client, model, config, params):
    job_id = params["job_id"]
    mode = params.get("mode", "plan")
    job = q.get_job(conn, job_id)
    if job is None:
        return {"job_id": job_id}
    settings = q.get_cv_settings(conn)
    scope_options = q.get_scope_options(conn)
    row = q.get_job_cv(conn, job_id)
    if row and row["finalized_at"]:
        return {"job_id": job_id}  # accepted CV is read-only
    jc = _job_context(job)

    if mode == "plan":
        current_directives = row["tuning_directives"] if row else settings["directives_template"]
        handled = row["handled_suggestions"] if row else []
        notes = _job_notes(conn, job)
        yield "Evaluating your tuning directives against the job… (LLM call: plan_tailoring)"
        t0 = time.monotonic()
        plan = plan_tailoring(client, model, settings["base_cv"], jc, notes,
                              tuning_directives=current_directives, handled=handled)
        elapsed = time.monotonic() - t0
        logger.info("cv_tailor: plan_tailoring for job %s took %.1fs", job_id, elapsed)
        resolved = resolve_directive_proposals(plan["directives"], current_directives, handled=handled)
        yield f"Evaluated directives — took {elapsed:.1f}s ({len(resolved)} suggestion(s))"
        if row is None:
            # Fresh row: seed scope to the configured default (same as generate).
            q.upsert_job_cv(conn, job_id, scope=[o["id"] for o in scope_options if o["default_enabled"]])
            if current_directives.strip():
                q.set_job_cv_directives(conn, job_id, current_directives)
        q.upsert_job_cv(conn, job_id, plan=resolved)
        conn.execute(
            "UPDATE job_cv SET plan_generated_at = datetime('now'), plan_context_hash = ? "
            "WHERE job_id = ?",
            (_plan_context_hash(jc, notes), job_id),
        )
        conn.commit()
        q.add_job_event(conn, job_id, "cv", "CV plan generated")
        return _tailor_result(conn, job_id, params)

    # mode == "generate"
    if row is None:
        q.upsert_job_cv(conn, job_id, scope=[o["id"] for o in scope_options if o["default_enabled"]])
        row = q.get_job_cv(conn, job_id)
    instr = compose_instruction(
        base_instruction=settings["base_instruction"], scope=row["scope"],
        scope_options=scope_options, guardrails=settings["base_guardrails"],
        tuning_directives=row["tuning_directives"],
    )
    yield "Generating the tailored CV… (LLM call: tailor_cv)"
    t0 = time.monotonic()
    result = tailor_cv(client, model, settings["base_cv"], instr, jc,
                       guardrails=settings["base_guardrails"])
    elapsed = time.monotonic() - t0
    logger.info("cv_tailor: tailor_cv for job %s took %.1fs", job_id, elapsed)
    draft = result["markdown"]
    if not draft:
        raise RuntimeError("CV generation returned empty output")
    yield f"Generated the tailored CV — took {elapsed:.1f}s ({len(draft)} chars)"
    yield "Checking against your guardrails… (LLM call: check_guardrails)"
    t0 = time.monotonic()
    findings = check_guardrails(client, model, settings["base_guardrails"],
                                settings["base_cv"], draft)["findings"]
    elapsed = time.monotonic() - t0
    logger.info("cv_tailor: check_guardrails for job %s took %.1fs", job_id, elapsed)
    yield f"Checked guardrails — took {elapsed:.1f}s ({len(findings)} finding(s))"
    _persist_draft(conn, job_id, draft=draft, findings=findings, settings=settings)
    q.add_job_event(conn, job_id, "cv", "CV regenerated")
    return _tailor_result(conn, job_id, params)


# --- Workbench actions ---

def _plan_pane(
    request: Request, conn: sqlite3.Connection, job_id: int, extra: dict | None = None,
) -> HTMLResponse:
    ctx = _workbench_ctx(conn, job_id)
    if extra:
        ctx.update(extra)
    return templates.TemplateResponse(request, "cv/_plan_pane.html", ctx)


def _require_editable(conn: sqlite3.Connection, job_id: int) -> None:
    """An accepted CV is read-only. Every route that would change the draft,
    the directives, the scope, or the plan calls this first so a stale tab or
    stray request can't mutate a finalised CV — you must Start over to edit."""
    if q.get_job(conn, job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")
    row = q.get_job_cv(conn, job_id)
    if row and row["finalized_at"]:
        raise HTTPException(status_code=409,
                            detail="This CV is accepted and read-only. Use “Start over” to edit it.")


@router.post("/jobs/{job_id}/cv/plan")
def cv_plan(job_id: int, conn: sqlite3.Connection = Depends(get_db)):
    _require_editable(conn, job_id)
    task = q.enqueue_task(conn, kind="cv_tailor",
                          params={"job_id": job_id, "mode": "plan", "render": "plan_pane"})
    return {"task_id": task["id"], "already_active": task["already_active"]}


@router.post("/jobs/{job_id}/cv/generate")
def cv_generate(job_id: int, conn: sqlite3.Connection = Depends(get_db)):
    _require_editable(conn, job_id)
    task = q.enqueue_task(conn, kind="cv_tailor",
                          params={"job_id": job_id, "mode": "generate", "render": "preview_pane"})
    return {"task_id": task["id"], "already_active": task["already_active"]}


@router.post("/jobs/{job_id}/cv/save-directives", response_class=HTMLResponse)
async def cv_save_directives(job_id: int, request: Request,
                             conn: sqlite3.Connection = Depends(get_db)):
    _require_editable(conn, job_id)
    form = await request.form()
    q.set_job_cv_directives(conn, job_id, form.get("tuning_directives", ""))
    return _plan_pane(request, conn, job_id)


@router.post("/jobs/{job_id}/cv/save-scope", response_class=HTMLResponse)
async def cv_save_scope(job_id: int, request: Request,
                        conn: sqlite3.Connection = Depends(get_db)):
    _require_editable(conn, job_id)
    form = await request.form()
    valid_ids = {o["id"] for o in q.get_scope_options(conn)}
    scope = [int(s) for s in form.getlist("scope") if s.isdigit() and int(s) in valid_ids]
    q.set_job_cv_scope(conn, job_id, scope)
    return templates.TemplateResponse(request, "cv/_preview_pane.html",
                                      _workbench_ctx(conn, job_id))


@router.post("/jobs/{job_id}/cv/reset-directives", response_class=HTMLResponse)
def cv_reset_directives(job_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    _require_editable(conn, job_id)
    q.set_job_cv_directives(conn, job_id, q.get_cv_settings(conn)["directives_template"])
    q.clear_handled_suggestions(conn, job_id)
    return _plan_pane(request, conn, job_id)


@router.post("/jobs/{job_id}/cv/handled/{index}/delete", response_class=HTMLResponse)
def cv_unhandle_suggestion(job_id: int, index: int, request: Request,
                           conn: sqlite3.Connection = Depends(get_db)):
    _require_editable(conn, job_id)
    q.remove_handled_suggestion(conn, job_id, index)
    return _plan_pane(request, conn, job_id)


@router.post("/jobs/{job_id}/cv/plan/accept", response_class=HTMLResponse)
async def cv_accept_plan_proposals(job_id: int, request: Request,
                                   conn: sqlite3.Connection = Depends(get_db)):
    _require_editable(conn, job_id)
    form = await request.form()
    accepted = []
    handled = []  # reviewed and left unchecked — a deliberate judgement call
    i = 0
    while f"action_{i}" in form:
        row = {
            "action": form[f"action_{i}"],
            "section": form.get(f"section_{i}", ""),
            "rationale": form.get(f"rationale_{i}", ""),
            "line": form.get(f"line_{i}") or None,
            "target": form.get(f"target_{i}") or None,
        }
        needs_line = row["action"] in ("add", "replace")
        checked = f"apply_{i}" in form
        if checked and not (needs_line and not row["line"]):
            accepted.append(row)
        elif not checked:
            handled.append(row)
        i += 1
    job_cv = q.get_job_cv(conn, job_id)
    current_directives = job_cv["tuning_directives"] if job_cv else ""
    new_text = apply_directive_proposals(current_directives, accepted)
    q.set_job_cv_directives(conn, job_id, new_text)
    q.add_handled_suggestions(conn, job_id, handled)
    q.upsert_job_cv(conn, job_id, plan=[])
    return _plan_pane(request, conn, job_id)


@router.post("/jobs/{job_id}/cv/accept")
def cv_accept(job_id: int, conn: sqlite3.Connection = Depends(get_db)):
    row = q.get_job_cv(conn, job_id)
    if row is None or not row["tailored_cv"]:
        raise HTTPException(status_code=400, detail="No CV to accept")
    if not row["finalized_at"]:
        q.finalize_job_cv(conn, job_id)
        q.add_job_event(conn, job_id, "cv", "CV accepted")
    return RedirectResponse(f"/jobs/{job_id}/cv", status_code=303)


@router.post("/jobs/{job_id}/cv/reopen")
def cv_reopen(job_id: int, conn: sqlite3.Connection = Depends(get_db)):
    if q.get_job(conn, job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")
    row = q.get_job_cv(conn, job_id)
    if row and row["finalized_at"]:
        q.unfinalize_job_cv(conn, job_id)
        q.add_job_event(conn, job_id, "cv", "CV reopened for editing")
    return RedirectResponse(f"/jobs/{job_id}/cv", status_code=303)


@router.get("/jobs/{job_id}/cv.pdf")
def cv_pdf(job_id: int, conn: sqlite3.Connection = Depends(get_db)):
    row = q.get_job_cv(conn, job_id)
    if row is None or not row["tailored_cv"]:
        raise HTTPException(status_code=404, detail="No CV")
    settings = q.get_cv_settings(conn)
    try:
        data = render_pdf(row["tailored_cv"], settings["css"])
    except CvRenderError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return Response(content=data, media_type="application/pdf",
                    headers={"Content-Disposition": 'attachment; filename="cv.pdf"'})
