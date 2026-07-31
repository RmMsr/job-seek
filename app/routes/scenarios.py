from __future__ import annotations
import logging
import sqlite3
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from app.deps import get_db, get_ai_client, get_model
from app.db import queries as q
from app.ai.refine import propose_criteria, match_removal_target
from app.pipeline import run_reevaluate
from app.template_env import templates
import openai

logger = logging.getLogger("job_seek")

router = APIRouter()


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


@router.post("/scenarios/reevaluate")
def reevaluate_all_scenarios(
    conn: sqlite3.Connection = Depends(get_db),
    client: openai.OpenAI = Depends(get_ai_client),
    model: str = Depends(get_model),
):
    scenarios = q.get_scenarios(conn)

    def stream():
        yield f"Re-evaluating {len(scenarios)} scenario(s)\n"
        total_updated = 0
        for scenario in scenarios:
            gen = run_reevaluate(conn, client, model, scenario)
            try:
                while True:
                    yield next(gen) + "\n"
            except StopIteration as stop:
                total_updated += stop.value
        yield f"All scenarios re-evaluated: {total_updated} job(s) updated across {len(scenarios)} scenario(s)\n"

    return StreamingResponse(stream(), media_type="text/plain")


def _get_scenario_or_404(conn: sqlite3.Connection, scenario_id: int) -> dict:
    scenario = q.get_scenario(conn, scenario_id)
    if not scenario:
        raise HTTPException(status_code=404, detail="Scenario not found")
    return scenario


def _get_criterion_or_404(conn: sqlite3.Connection, criterion_id: int) -> dict:
    criterion = q.get_criterion(conn, criterion_id)
    if not criterion:
        raise HTTPException(status_code=404, detail="Criterion not found")
    return criterion


@router.get("/scenarios/{scenario_id}/edit", response_class=HTMLResponse)
def edit_scenario_form(scenario_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    scenario = _get_scenario_or_404(conn, scenario_id)
    return templates.TemplateResponse(request, "scenarios/_header_edit.html", {"scenario": scenario})


@router.get("/scenarios/{scenario_id}", response_class=HTMLResponse)
def scenario_header(scenario_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    scenario = _get_scenario_or_404(conn, scenario_id)
    return templates.TemplateResponse(request, "scenarios/_header.html", {"scenario": scenario})


@router.post("/scenarios/{scenario_id}", response_class=HTMLResponse)
def update_scenario(
    scenario_id: int,
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    conn: sqlite3.Connection = Depends(get_db),
):
    _get_scenario_or_404(conn, scenario_id)
    q.update_scenario(conn, scenario_id, name=name, description=description)
    scenario = q.get_scenario(conn, scenario_id)
    return templates.TemplateResponse(request, "scenarios/_header.html", {"scenario": scenario})


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


@router.get("/criteria/{criterion_id}/edit", response_class=HTMLResponse)
def edit_criterion_form(criterion_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    criterion = _get_criterion_or_404(conn, criterion_id)
    return templates.TemplateResponse(request, "scenarios/_criterion_edit.html", {"c": criterion})


@router.get("/criteria/{criterion_id}", response_class=HTMLResponse)
def criterion_row(criterion_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    criterion = _get_criterion_or_404(conn, criterion_id)
    return templates.TemplateResponse(request, "scenarios/_criterion.html", {"c": criterion})


@router.post("/criteria/{criterion_id}", response_class=HTMLResponse)
def update_criterion(
    criterion_id: int,
    request: Request,
    text: str = Form(...),
    weight: str = Form(...),
    conn: sqlite3.Connection = Depends(get_db),
):
    _get_criterion_or_404(conn, criterion_id)
    q.update_criterion(conn, criterion_id, text=text, weight=weight)
    criterion = q.get_criterion(conn, criterion_id)
    return templates.TemplateResponse(request, "scenarios/_criterion.html", {"c": criterion})


@router.delete("/criteria/{criterion_id}/remove-proposal", response_class=HTMLResponse)
def remove_criterion_via_proposal(criterion_id: int, conn: sqlite3.Connection = Depends(get_db)):
    q.delete_criterion(conn, criterion_id)
    return HTMLResponse(content=f'<li id="criterion-{criterion_id}" hx-swap-oob="delete"></li>')


@router.post("/scenarios/{scenario_id}/refine")
def refine_criteria(
    scenario_id: int,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    client: openai.OpenAI = Depends(get_ai_client),
    model: str = Depends(get_model),
):
    scenario = _get_scenario_or_404(conn, scenario_id)
    existing = q.get_criteria(conn, scenario_id)
    notes = q.get_recent_feedback_notes(conn, scenario_id)

    def stream():
        msg = f"Requesting criteria proposals for '{scenario['name']}' from LLM"
        logger.info(msg)
        yield msg + "\n"
        proposals = propose_criteria(client, model, scenario, existing, notes)
        msg = f"Received {len(proposals)} proposal(s)"
        logger.info(msg)
        yield msg + "\n"
        resolved = []
        for p in proposals:
            if p.action == "remove":
                criterion_id = match_removal_target(p.text, existing)
                if criterion_id is None:
                    continue
                resolved.append({"text": p.text, "weight": p.weight, "action": "remove", "criterion_id": criterion_id})
            else:
                resolved.append({"text": p.text, "weight": p.weight, "action": "add", "criterion_id": None})
        html = templates.get_template("scenarios/_proposals.html").render(
            request=request, proposals=resolved, scenario_id=scenario_id
        )
        yield "HTML:" + html.replace("\n", "")

    return StreamingResponse(stream(), media_type="text/plain")


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


@router.post("/scenarios/{scenario_id}/reevaluate")
def reevaluate_jobs(
    scenario_id: int,
    conn: sqlite3.Connection = Depends(get_db),
    client: openai.OpenAI = Depends(get_ai_client),
    model: str = Depends(get_model),
):
    scenario = _get_scenario_or_404(conn, scenario_id)

    def stream():
        gen = run_reevaluate(conn, client, model, scenario)
        try:
            while True:
                yield next(gen) + "\n"
        except StopIteration:
            pass

    return StreamingResponse(stream(), media_type="text/plain")
