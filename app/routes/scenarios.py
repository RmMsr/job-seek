from __future__ import annotations
import sqlite3
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from app.deps import get_db, get_ai_client, get_model
from app.db import queries as q
from app.ai.refine import propose_criteria
from app.ai.summarize import summarize
from app.ai.evaluate import evaluate
import openai

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _scenarios_context(conn: sqlite3.Connection) -> dict:
    scenarios = q.get_scenarios(conn)
    criteria_by_scenario = {s["id"]: q.get_criteria(conn, s["id"]) for s in scenarios}
    return {"scenarios": scenarios, "criteria_by_scenario": criteria_by_scenario}


@router.get("/scenarios", response_class=HTMLResponse)
def scenarios_page(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    ctx = _scenarios_context(conn)
    return templates.TemplateResponse(request, "scenarios/index.html", ctx)


@router.post("/scenarios", response_class=HTMLResponse)
def create_scenario(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    conn: sqlite3.Connection = Depends(get_db),
):
    q.insert_scenario(conn, name, description)
    ctx = _scenarios_context(conn)
    return templates.TemplateResponse(request, "scenarios/index.html", ctx)


@router.post("/scenarios/{scenario_id}/activate", response_class=HTMLResponse)
def activate_scenario(
    scenario_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)
):
    q.set_active_scenario(conn, scenario_id)
    ctx = _scenarios_context(conn)
    return templates.TemplateResponse(request, "scenarios/index.html", ctx)


@router.post("/scenarios/{scenario_id}/criteria", response_class=HTMLResponse)
def add_criterion(
    scenario_id: int,
    request: Request,
    text: str = Form(...),
    weight: str = Form(...),
    conn: sqlite3.Connection = Depends(get_db),
):
    q.insert_criterion(conn, scenario_id, text, weight)
    criteria = q.get_criteria(conn, scenario_id)
    return templates.TemplateResponse(
        request,
        "scenarios/_criteria.html",
        {"criteria": criteria, "scenario_id": scenario_id},
    )


@router.delete("/criteria/{criterion_id}", response_class=HTMLResponse)
def delete_criterion(criterion_id: int, conn: sqlite3.Connection = Depends(get_db)):
    q.delete_criterion(conn, criterion_id)
    return HTMLResponse(content="")


@router.post("/scenarios/{scenario_id}/refine", response_class=HTMLResponse)
def refine_criteria(
    scenario_id: int,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    client: openai.OpenAI = Depends(get_ai_client),
    model: str = Depends(get_model),
):
    scenario = dict(conn.execute("SELECT * FROM scenarios WHERE id = ?", (scenario_id,)).fetchone())
    existing = q.get_criteria(conn, scenario_id)
    notes = q.get_recent_feedback_notes(conn, scenario_id)
    proposals = propose_criteria(client, model, scenario, existing, notes)
    return templates.TemplateResponse(
        request,
        "scenarios/_proposals.html",
        {"proposals": proposals, "scenario_id": scenario_id},
    )


@router.post("/scenarios/{scenario_id}/refine/accept", response_class=HTMLResponse)
async def accept_proposals(
    scenario_id: int,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
):
    form = await request.form()
    i = 0
    while f"text_{i}" in form:
        text = form[f"text_{i}"]
        weight = form[f"weight_{i}"]
        q.insert_criterion(conn, scenario_id, text, weight, source="feedback")
        i += 1
    criteria = q.get_criteria(conn, scenario_id)
    return templates.TemplateResponse(
        request,
        "scenarios/_criteria.html",
        {"criteria": criteria, "scenario_id": scenario_id},
    )


@router.post("/scenarios/{scenario_id}/reevaluate", response_class=HTMLResponse)
def reevaluate_jobs(
    scenario_id: int,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    client: openai.OpenAI = Depends(get_ai_client),
    model: str = Depends(get_model),
):
    scenario = dict(conn.execute("SELECT * FROM scenarios WHERE id = ?", (scenario_id,)).fetchone())
    criteria = q.get_criteria(conn, scenario_id)
    profile = q.get_profile(conn)
    jobs = q.get_jobs(conn, status="new")
    evaluated = [j for j in jobs if j["scenario_id"] == scenario_id and j["content_type"] in ("job_posting", "lead")]
    for job in evaluated:
        new_summary = summarize(client, model, job["simplified_content"]) if job["simplified_content"] else job["summary"]
        score, reasoning = evaluate(client, model, profile, scenario, criteria, new_summary)
        q.update_job_pipeline(
            conn, job["id"],
            simplified_content=job["simplified_content"],
            content_type=job["content_type"],
            summary=new_summary,
            relevance_score=score,
            score_reasoning=reasoning,
            scenario_id=scenario_id,
        )
    ctx = _scenarios_context(conn)
    ctx["reevaluated_count"] = len(evaluated)
    return templates.TemplateResponse(request, "scenarios/index.html", ctx)
