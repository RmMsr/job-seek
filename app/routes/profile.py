from __future__ import annotations
import sqlite3
import openai
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from app.deps import get_db, get_ai_client, get_model
from app.db import queries as q
from app.ai.refine_profile import propose_profile_changes
from app.profile_apply import resolve_proposals, apply_profile_proposals, group_proposals_by_section
from app.template_env import templates

router = APIRouter()


def _profile_context(conn: sqlite3.Connection) -> dict:
    return {
        "content": q.get_profile(conn),
        "unhandled_notes_count": len(q.get_unhandled_profile_notes(conn)),
    }


@router.get("/profile", response_class=HTMLResponse)
def profile_page(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    return templates.TemplateResponse(request, "profile/index.html", _profile_context(conn))


@router.post("/profile", response_class=HTMLResponse)
def profile_save(
    request: Request,
    content: str = Form(...),
    conn: sqlite3.Connection = Depends(get_db),
):
    q.upsert_profile(conn, content)
    ctx = _profile_context(conn)
    ctx["saved"] = True
    return templates.TemplateResponse(request, "profile/index.html", ctx)


@router.post("/profile/refine")
def refine_profile(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    client: openai.OpenAI = Depends(get_ai_client),
    model: str = Depends(get_model),
):
    profile_text = q.get_profile(conn)
    notes = q.get_unhandled_profile_notes(conn)

    def stream():
        yield "Requesting profile improvement suggestions from LLM\n"
        proposals = propose_profile_changes(client, model, profile_text, notes)
        resolved = resolve_proposals(proposals, profile_text)
        yield f"Received {len(resolved)} proposal(s)\n"
        html = templates.get_template("profile/_proposals.html").render(
            request=request,
            grouped=group_proposals_by_section(resolved),
            job_ids=[n["id"] for n in notes],
        )
        yield "HTML:" + html.replace("\n", "")

    return StreamingResponse(stream(), media_type="text/plain")


@router.post("/profile/refine/accept", response_class=HTMLResponse)
async def accept_profile_proposals(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
):
    form = await request.form()
    resolved = []
    unapplied = []
    i = 0
    while f"kind_{i}" in form:
        row = {
            "action": form[f"kind_{i}"],
            "section": form[f"section_{i}"],
            "text": form.get(f"text_{i}") or None,
            "target": form.get(f"target_{i}") or None,
            "anchor": form.get(f"anchor_{i}") or None,
        }
        if f"apply_{i}" in form:
            resolved.append(row)
        else:
            unapplied.append(row)
        i += 1
    profile_text = q.get_profile(conn)
    new_text = apply_profile_proposals(profile_text, resolved)
    q.upsert_profile(conn, new_text)
    job_ids = [int(v) for v in form.getlist("job_ids")]
    q.mark_profile_feedback_handled(conn, job_ids)
    ctx = _profile_context(conn)
    ctx["saved"] = True
    ctx["unapplied"] = unapplied
    return templates.TemplateResponse(request, "profile/_editor.html", ctx)
