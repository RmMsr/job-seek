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


def _resolved_settings(conn: sqlite3.Connection) -> dict:
    """Settings as tailoring/diffing/the job workbench's Base tab see them —
    base_cv resolved to the accepted version (or current, if nothing's been
    accepted yet). Everything else is live/unversioned."""
    settings = q.get_cv_settings(conn)
    settings["base_cv"] = q.resolve_base_cv(conn)
    return settings


def _base_hash(settings: dict) -> str:
    return hashlib.sha256(settings.get("base_cv", "").encode()).hexdigest()


def _guardrails_hash(settings: dict) -> str:
    """Unlike _base_hash (which now covers only base_cv, for the draft-content
    staleness badge), this covers both base_cv and base_guardrails: they're
    check_guardrails()'s two ground-truth inputs (base_instruction plays no
    role there), so a change to either makes existing guardrail findings
    stale regardless of what tailor_cv() iterated from."""
    raw = "\x00".join([settings.get("base_cv", ""), settings.get("base_guardrails", "")])
    return hashlib.sha256(raw.encode()).hexdigest()


def _plan_context_hash(job_context: str, job_notes: str) -> str:
    """Identifies the inputs a plan run saw (posting + notes) so the workbench can
    show the plan stage as stale when they change."""
    return hashlib.sha256(f"{job_context}\x00{job_notes}".encode()).hexdigest()


def _persist_draft(conn, job_id: int, *, draft: str, findings: list, settings: dict,
                   note: str = "", consumed_base: bool = True) -> None:
    """Store a freshly generated draft and stamp generated_at. If the guardrail
    check came back empty while guardrails ARE configured, keep the previous
    findings — an empty result there is almost always a transient LLM/JSON
    failure, and silently blanking the guardrails panel is worse than showing a
    slightly stale check.

    base_hash/base_cv_snapshot are only re-stamped when this run's source
    document was actually the live base CV (consumed_base=True) — a run
    that iterated from an existing tailored draft instead didn't read the
    current base CV at all, so re-stamping would falsely mark a possibly
    already-stale base_hash as fresh."""
    fields: dict = {
        "tailored_cv": draft,
        "note": note,
    }
    if consumed_base:
        fields["base_hash"] = _base_hash(settings)
        fields["base_cv_snapshot"] = settings.get("base_cv", "")
    if findings or not settings.get("base_guardrails", "").strip():
        fields["guardrail_findings"] = findings
        fields["guardrails_hash"] = _guardrails_hash(settings)
    else:
        logger.warning("cv_tailor: guardrail check returned nothing for job %s — keeping prior findings", job_id)
    q.upsert_job_cv(conn, job_id, **fields)
    conn.execute(
        "UPDATE job_cv SET generated_at = datetime('now'), "
        "guardrails_checked_at = datetime('now') WHERE job_id = ?",
        (job_id,),
    )
    conn.commit()


def _draft_staleness_reason(job_cv: dict | None, settings: dict) -> str | None:
    """Which of the draft's inputs changed since it was generated, if any:
    'base' — the base CV content itself changed. Since "Apply tailoring
    plan" iterates from the current draft rather than always re-deriving
    from base, this can't be fixed by running it again — only "Reset to
    base CV" (or a manual edit) picks up the new base content.
    'plan' — tuning directives or edit scope changed since the draft was
    generated. These are read fresh into the instruction on every
    generate regardless of which document is being rewritten, so the next
    "Apply tailoring plan" run picks them up automatically. Base
    instruction and guardrails are the same way — always applied live —
    so they aren't tracked here at all.
    None if the draft is fresh."""
    if not job_cv or not job_cv.get("tailored_cv"):
        return None
    if job_cv.get("base_hash", "") != _base_hash(settings):
        return "base"
    ge = job_cv.get("generated_at")
    if not ge:
        return None
    de = job_cv.get("directives_edited_at")
    se = job_cv.get("scope_edited_at")
    return "plan" if ((de and de > ge) or (se and se > ge)) else None


def _draft_stale(job_cv: dict | None, settings: dict) -> bool:
    return bool(_draft_staleness_reason(job_cv, settings))


def _edited_since_guardrail_check(job_cv: dict | None) -> bool:
    """The CV was hand-edited (autosave stamps edited_at) after its guardrail
    findings were last computed — so the findings no longer describe the shown
    markdown."""
    if not job_cv:
        return False
    ea = job_cv.get("edited_at")
    if not ea:
        return False
    ca = job_cv.get("guardrails_checked_at")
    return not ca or ca < ea


def _guardrails_stale(job_cv: dict | None, settings: dict) -> bool:
    """The guardrail set (or the base CV they're checked against) changed
    since the shown findings were computed."""
    if not job_cv:
        return False
    return job_cv.get("guardrails_hash", "") != _guardrails_hash(settings)


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
    if _edited_since_guardrail_check(job_cv):
        return "stale"
    return "stale" if _guardrails_stale(job_cv, settings) else "fresh"


def _cv_page_ctx(
    conn: sqlite3.Connection, against: int | None = None, viewing_version_id: int | None = None,
) -> dict:
    settings = q.get_cv_settings(conn)
    return {
        "settings": settings,
        "has_doc_write": doc_write_available(),
        "app_version": get_app_version(),
        "build_date": get_build_date(),
        "versions": q.get_versions(conn, "base", 1),
        "current_version_id": settings.get("current_version_id"),
        "accepted_version": q.get_accepted_base_version(conn),
        "diff_against_id": (
            against if against is not None else q.resolve_base_diff_target(conn, viewing_version_id)
        ),
        "version_base_url": "/cv",
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
        ctx["draft_stale_reason"] = _draft_staleness_reason(ctx["job_cv"], ctx["settings"])
        ctx["guardrail_status"] = _guardrail_status(ctx["job_cv"], ctx["settings"], run)
    if "plan_task_id" in ctx_overrides and ctx["job"]:
        ctx["plan_status"] = _plan_status(
            conn, ctx["job"], ctx["job_cv"], bool(ctx["plan_task_id"])
        )
    _PANE_TEMPLATES = {
        "plan_pane": ("cv/_plan_pane.html", "cv-plan-pane"),
        "preview_pane": ("cv/_preview_pane.html", "cv-preview-pane"),
        "findings": ("cv/_findings.html", "cv-findings"),
    }
    inner, wrapper = _PANE_TEMPLATES.get(which, _PANE_TEMPLATES["preview_pane"])
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
        overrides = {"updating_task_id": None}
        if which == "plan_pane":
            overrides["plan_task_id"] = None
        result["html_chunks"] = [_rendered_chunk(conn, job_id, which, **overrides)]
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


def _resolve_diff_target(
    conn: sqlite3.Connection, job_id: int, against_type: str | None, against_id: int | None,
) -> tuple[str, int] | None:
    """Validates an explicit (against_type, against_id) diff-target pair —
    'base' resolves against the base CV (entity_id 1), 'tailored' against
    this job's own versions. Returns the resolved content, or None when no
    target was given (against_id omitted) so the caller can fall back to its
    own default. Raises 404 for an unknown type or a version id that
    doesn't belong to it."""
    if against_id is None:
        return None
    if against_type not in ("base", "tailored"):
        raise HTTPException(status_code=404, detail="Unknown diff target")
    entity_id = 1 if against_type == "base" else job_id
    version = q.get_version(conn, against_type, entity_id, against_id)
    if version is None:
        raise HTTPException(status_code=404, detail="Version not found")
    return against_type, against_id


def _workbench_ctx(
    conn: sqlite3.Connection, job_id: int,
    against_type: str | None = None, against_id: int | None = None,
) -> dict:
    job = q.get_job_with_source_name(conn, job_id)
    job_cv = q.get_job_cv(conn, job_id)
    settings = _resolved_settings(conn)
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
        "draft_stale_reason": _draft_staleness_reason(job_cv, settings),
        "guardrail_status": _guardrail_status(job_cv, settings, bool(updating)),
        "has_doc_write": doc_write_available(),
        "cv_diff_view": _cv_diff_view(job_cv),
        "versions": q.get_versions(conn, "tailored", job_id),
        "base_versions": q.get_versions(conn, "base", 1),
        "current_version_id": job_cv["current_version_id"] if job_cv else None,
        "accepted_version": q.get_accepted_job_cv_version(conn, job_id),
        # The Differences tab's default target: whatever's currently accepted
        # for the base CV (or its current version, if nothing's accepted
        # yet) — overridable to any base or own version via the diff-target
        # picker in cv/_preview_tabs.html.
        "diff_against_type": against_type or "base",
        "diff_against_id": against_id if against_id is not None else q.resolve_base_version_id(conn),
        "version_base_url": f"/jobs/{job_id}/cv",
    }


@router.get(
    "/jobs/{job_id}/cv", response_class=HTMLResponse)
def cv_workbench(job_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    if q.get_job(conn, job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return templates.TemplateResponse(request, "cv/workbench.html", _workbench_ctx(conn, job_id))


@router.get(
    "/jobs/{job_id}/cv/preview", response_class=HTMLResponse)
def cv_preview_page(job_id: int, request: Request, version: int | None = None,
                    against_type: str | None = None, against_id: int | None = None,
                    conn: sqlite3.Connection = Depends(get_db)):
    if q.get_job(conn, job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")
    resolved = _resolve_diff_target(conn, job_id, against_type, against_id)
    ctx = _workbench_ctx(
        conn, job_id,
        against_type=resolved[0] if resolved else None,
        against_id=resolved[1] if resolved else None,
    )
    if version is not None:
        v = q.get_version(conn, "tailored", job_id, version)
        if v is None:
            raise HTTPException(status_code=404, detail="Version not found")
        ctx["viewing_version"] = v
    return templates.TemplateResponse(request, "cv/preview.html", ctx)


@router.get(
    "/jobs/{job_id}/cv/preview.html", response_class=HTMLResponse)
def cv_preview_html(job_id: int, variant: str = "tailored", version: int | None = None,
                    conn: sqlite3.Connection = Depends(get_db)):
    job = q.get_job(conn, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    settings = q.get_cv_settings(conn)
    if variant == "base":
        markdown = q.resolve_base_cv(conn)
    elif version is not None:
        v = q.get_version(conn, "tailored", job_id, version)
        if v is None:
            raise HTTPException(status_code=404, detail="Version not found")
        markdown = v["content"]
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
def cv_diff_html(job_id: int, request: Request, version: int | None = None,
                 against_type: str | None = None, against_id: int | None = None,
                 conn: sqlite3.Connection = Depends(get_db)):
    job = q.get_job(conn, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    row = q.get_job_cv(conn, job_id)
    if row is None or not row["tailored_cv"]:
        raise HTTPException(status_code=404, detail="No tailored CV")
    tailored = row["tailored_cv"]
    if version is not None:
        v = q.get_version(conn, "tailored", job_id, version)
        if v is None:
            raise HTTPException(status_code=404, detail="Version not found")
        tailored = v["content"]
    resolved = _resolve_diff_target(conn, job_id, against_type, against_id)
    if resolved is not None:
        r_type, r_id = resolved
        entity_id = 1 if r_type == "base" else job_id
        against_content = q.get_version(conn, r_type, entity_id, r_id)["content"]
    elif not row["base_cv_snapshot"]:
        return templates.TemplateResponse(
            request, "cv/_cv_diff_fallback.html", {"predates": True, "body_html": ""},
        )
    else:
        against_content = row["base_cv_snapshot"]
    settings = q.get_cv_settings(conn)
    try:
        annotated = build_cv_diff(against_content, tailored).annotated_markdown
    except Exception:
        logger.exception("cv_diff build failed for job %s", job_id)
        annotated = tailored
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
def cv_page(request: Request, version: int | None = None, against: int | None = None,
           conn: sqlite3.Connection = Depends(get_db)):
    if against is not None and q.get_version(conn, "base", 1, against) is None:
        raise HTTPException(status_code=404, detail="Version not found")
    ctx = _cv_page_ctx(conn, against=against, viewing_version_id=version)
    if version is not None:
        v = q.get_version(conn, "base", 1, version)
        if v is None:
            raise HTTPException(status_code=404, detail="Version not found")
        ctx["viewing_version"] = v
    return templates.TemplateResponse(request, "cv/index.html", ctx)


@router.post("/cv/save-base", response_class=HTMLResponse)
async def cv_save_base(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    form = await request.form()
    q.set_base_cv(conn, form.get("markdown", ""))
    return templates.TemplateResponse(
        request, "cv/_save_base_oob.html", _cv_page_ctx(conn),
    )


@router.get("/cv/preview.html", response_class=HTMLResponse)
def cv_preview_base_html(version: int | None = None, conn: sqlite3.Connection = Depends(get_db)):
    settings = q.get_cv_settings(conn)
    content = settings["base_cv"]
    if version is not None:
        v = q.get_version(conn, "base", 1, version)
        if v is None:
            raise HTTPException(status_code=404, detail="Version not found")
        content = v["content"]
    if not doc_write_available():
        raise HTTPException(status_code=503, detail="doc-write-cli is not installed")
    try:
        return HTMLResponse(render_preview_html(content, settings["css"]))
    except CvRenderError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.get("/cv/diff.html", response_class=HTMLResponse)
def cv_diff_base_html(request: Request, version: int | None = None, against: int | None = None,
                      conn: sqlite3.Connection = Depends(get_db)):
    settings = q.get_cv_settings(conn)
    content = settings["base_cv"]
    if version is not None:
        v = q.get_version(conn, "base", 1, version)
        if v is None:
            raise HTTPException(status_code=404, detail="Version not found")
        content = v["content"]
    if against is not None:
        against_v = q.get_version(conn, "base", 1, against)
        if against_v is None:
            raise HTTPException(status_code=404, detail="Version not found")
        against_content = against_v["content"]
    else:
        target_id = q.resolve_base_diff_target(conn, version)
        if target_id is None:
            return templates.TemplateResponse(
                request, "cv/_cv_diff_fallback.html",
                {"predates": False, "body_html": "", "message": "Nothing to compare against yet."},
            )
        against_content = q.get_version(conn, "base", 1, target_id)["content"]
    try:
        annotated = build_cv_diff(against_content, content).annotated_markdown
    except Exception:
        logger.exception("cv_diff build failed for base CV")
        annotated = content
    if doc_write_available():
        try:
            return HTMLResponse(render_diff_html(annotated, settings["css"]))
        except CvRenderError as exc:
            raise HTTPException(status_code=503, detail=str(exc))
    body_html = _markdown.markdown(annotated, extensions=["nl2br"])
    return templates.TemplateResponse(
        request, "cv/_cv_diff_fallback.html", {"predates": False, "body_html": body_html},
    )


@router.get("/cv.pdf")
def cv_base_pdf(version: int | None = None, conn: sqlite3.Connection = Depends(get_db)):
    settings = q.get_cv_settings(conn)
    content = settings["base_cv"]
    if version is not None:
        v = q.get_version(conn, "base", 1, version)
        if v is None:
            raise HTTPException(status_code=404, detail="Version not found")
        content = v["content"]
    if not content.strip():
        raise HTTPException(status_code=404, detail="No base CV")
    try:
        data = render_pdf(content, settings["css"])
    except CvRenderError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return Response(content=data, media_type="application/pdf",
                    headers={"Content-Disposition": 'attachment; filename="cv.pdf"'})


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
    settings = _resolved_settings(conn)
    scope_options = q.get_scope_options(conn)
    row = q.get_job_cv(conn, job_id)
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

    if mode == "recheck":
        if row is None or not row["tailored_cv"]:
            return {"job_id": job_id}
        if not settings["base_guardrails"].strip():
            return {"job_id": job_id}
        yield "Checking against your guardrails… (LLM call: check_guardrails)"
        t0 = time.monotonic()
        findings = check_guardrails(client, model, settings["base_guardrails"],
                                    settings["base_cv"], row["tailored_cv"])["findings"]
        elapsed = time.monotonic() - t0
        logger.info("cv_tailor: recheck check_guardrails for job %s took %.1fs", job_id, elapsed)
        if findings:
            q.upsert_job_cv(conn, job_id, guardrail_findings=findings, guardrails_hash=_guardrails_hash(settings))
        else:
            logger.warning("cv_tailor: recheck guardrail check returned nothing for job %s — keeping prior findings", job_id)
        conn.execute(
            "UPDATE job_cv SET guardrails_checked_at = datetime('now') WHERE job_id = ?",
            (job_id,),
        )
        conn.commit()
        yield f"Checked guardrails — took {elapsed:.1f}s ({len(findings)} finding(s))"
        q.add_job_event(conn, job_id, "cv", "Guardrails re-checked")
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
    scope_ids = set(row["scope"])
    scope_note = ", ".join(
        (o["name"] or o["description"]) for o in scope_options if o["id"] in scope_ids
    )
    source_cv = row["tailored_cv"] or settings["base_cv"]
    yield "Generating the tailored CV… (LLM call: tailor_cv)"
    t0 = time.monotonic()
    result = tailor_cv(client, model, source_cv, instr, jc,
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
    _persist_draft(conn, job_id, draft=draft, findings=findings, settings=settings, note=scope_note,
                   consumed_base=(source_cv == settings["base_cv"]))
    q.add_job_event(conn, job_id, "cv", "Applied tailoring plan")
    return _tailor_result(conn, job_id, params)


# --- Workbench actions ---

def _require_job(conn: sqlite3.Connection, job_id: int) -> None:
    """404 for a missing job. Previously folded into `_require_editable`
    alongside the (now-removed) accepted/read-only gate; kept on its own now
    that accepting no longer blocks these routes."""
    if q.get_job(conn, job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")


def _plan_pane(
    request: Request, conn: sqlite3.Connection, job_id: int, extra: dict | None = None,
) -> HTMLResponse:
    ctx = _workbench_ctx(conn, job_id)
    if extra:
        ctx.update(extra)
    return templates.TemplateResponse(request, "cv/_plan_pane.html", ctx)


@router.post("/jobs/{job_id}/cv/plan")
def cv_plan(job_id: int, conn: sqlite3.Connection = Depends(get_db)):
    _require_job(conn, job_id)
    task = q.enqueue_task(conn, kind="cv_tailor",
                          params={"job_id": job_id, "mode": "plan", "render": "plan_pane"})
    return {"task_id": task["id"], "already_active": task["already_active"]}


@router.post("/jobs/{job_id}/cv/generate")
def cv_generate(job_id: int, conn: sqlite3.Connection = Depends(get_db)):
    _require_job(conn, job_id)
    task = q.enqueue_task(conn, kind="cv_tailor",
                          params={"job_id": job_id, "mode": "generate", "render": "preview_pane"})
    return {"task_id": task["id"], "already_active": task["already_active"]}


@router.post("/jobs/{job_id}/cv/recheck-guardrails")
def cv_recheck_guardrails(job_id: int, conn: sqlite3.Connection = Depends(get_db)):
    _require_job(conn, job_id)
    task = q.enqueue_task(conn, kind="cv_tailor",
                          params={"job_id": job_id, "mode": "recheck", "render": "findings"})
    return {"task_id": task["id"], "already_active": task["already_active"]}


@router.post("/jobs/{job_id}/cv/save-directives", response_class=HTMLResponse)
async def cv_save_directives(job_id: int, request: Request,
                             conn: sqlite3.Connection = Depends(get_db)):
    _require_job(conn, job_id)
    form = await request.form()
    q.set_job_cv_directives(conn, job_id, form.get("tuning_directives", ""))
    return _plan_pane(request, conn, job_id)


@router.post("/jobs/{job_id}/cv/save-scope", response_class=HTMLResponse)
async def cv_save_scope(job_id: int, request: Request,
                        conn: sqlite3.Connection = Depends(get_db)):
    _require_job(conn, job_id)
    form = await request.form()
    valid_ids = {o["id"] for o in q.get_scope_options(conn)}
    scope = [int(s) for s in form.getlist("scope") if s.isdigit() and int(s) in valid_ids]
    q.set_job_cv_scope(conn, job_id, scope)
    return templates.TemplateResponse(request, "cv/_scope_status_update.html",
                                      _workbench_ctx(conn, job_id))


@router.post("/jobs/{job_id}/cv/save-tailored", response_class=HTMLResponse)
async def cv_save_tailored(job_id: int, request: Request,
                          conn: sqlite3.Connection = Depends(get_db)):
    _require_job(conn, job_id)
    form = await request.form()
    q.set_job_cv_tailored(conn, job_id, form.get("markdown", ""))
    return templates.TemplateResponse(
        request, "cv/_save_tailored_oob.html", _workbench_ctx(conn, job_id),
    )


@router.post("/jobs/{job_id}/cv/reset-to-base")
def cv_reset_to_base(job_id: int, conn: sqlite3.Connection = Depends(get_db)):
    _require_job(conn, job_id)
    q.reset_job_cv_to_base(conn, job_id)
    q.add_job_event(conn, job_id, "cv", "Reset to base CV")
    return RedirectResponse(f"/jobs/{job_id}/cv/preview", status_code=303)


@router.post("/jobs/{job_id}/cv/reset-directives", response_class=HTMLResponse)
def cv_reset_directives(job_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    _require_job(conn, job_id)
    q.set_job_cv_directives(conn, job_id, q.get_cv_settings(conn)["directives_template"])
    q.clear_handled_suggestions(conn, job_id)
    return _plan_pane(request, conn, job_id)


@router.post("/jobs/{job_id}/cv/handled/{index}/delete", response_class=HTMLResponse)
def cv_unhandle_suggestion(job_id: int, index: int, request: Request,
                           conn: sqlite3.Connection = Depends(get_db)):
    _require_job(conn, job_id)
    q.remove_handled_suggestion(conn, job_id, index)
    return _plan_pane(request, conn, job_id)


@router.post("/jobs/{job_id}/cv/plan/accept", response_class=HTMLResponse)
async def cv_accept_plan_proposals(job_id: int, request: Request,
                                   conn: sqlite3.Connection = Depends(get_db)):
    _require_job(conn, job_id)
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
    q.accept_job_cv(conn, job_id)
    q.add_job_event(conn, job_id, "cv", "CV accepted")
    return RedirectResponse(f"/jobs/{job_id}/cv/preview", status_code=303)


@router.get("/jobs/{job_id}/cv.pdf")
def cv_pdf(job_id: int, version: int | None = None, conn: sqlite3.Connection = Depends(get_db)):
    content = None
    row = q.get_job_cv(conn, job_id)
    if version is not None:
        v = q.get_version(conn, "tailored", job_id, version)
        if v is None:
            raise HTTPException(status_code=404, detail="Version not found")
        content = v["content"]
    elif row is not None:
        content = row["tailored_cv"]
    if not content:
        raise HTTPException(status_code=404, detail="No tailored CV")
    settings = q.get_cv_settings(conn)
    try:
        data = render_pdf(content, settings["css"])
    except CvRenderError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return Response(content=data, media_type="application/pdf",
                    headers={"Content-Disposition": 'attachment; filename="cv.pdf"'})
