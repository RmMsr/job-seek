from __future__ import annotations
import logging
import sqlite3
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from app.deps import get_db, get_ai_client, get_model
from app.db import queries as q
from app.ai.refine import propose_criteria, match_removal_target
from app.pipeline import run_reevaluate, count_jobs_needing_reevaluation
from app.template_env import templates
import openai

logger = logging.getLogger("job_seek")

router = APIRouter()

_WEIGHT_ORDER = {"must": 0, "prefer": 1, "avoid": 2}


def _parse_job_ids(raw: str | None) -> list[int]:
    if not raw:
        return []
    return [int(part) for part in raw.split(",") if part.strip().isdigit()]


def _resolve_proposals(proposals: list, existing: list[dict]) -> list[dict]:
    resolved = []
    for p in proposals:
        if p.action == "remove":
            criterion_id = match_removal_target(p.text, existing)
            if criterion_id is None:
                continue
            criterion = next(c for c in existing if c["id"] == criterion_id)
            resolved.append({"text": p.text, "weight": criterion["weight"], "action": "remove", "criterion_id": criterion_id})
        else:
            if match_removal_target(p.text, existing) is not None:
                continue
            resolved.append({"text": p.text, "weight": p.weight, "action": "add", "criterion_id": None})
    resolved.sort(key=lambda r: _WEIGHT_ORDER.get(r["weight"], 3))
    return resolved


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
        # Cheap DB-only reads, no LLM calls: precompute per-scenario counts so
        # per-job progress lines can report position against the grand total,
        # not just each scenario's own count.
        counts = [count_jobs_needing_reevaluation(conn, s) for s in scenarios]
        job_total = sum(counts)

        yield f"Re-evaluating {len(scenarios)} scenario(s), {job_total} job(s) total\n"
        total_updated = 0
        job_offset = 0
        for idx, scenario in enumerate(scenarios, start=1):
            label = f"[Scenario {idx}/{len(scenarios)}: {scenario['name']}] "
            gen = run_reevaluate(
                conn, client, model, scenario,
                job_offset=job_offset, job_total=job_total, scenario_label=label,
            )
            try:
                while True:
                    yield next(gen) + "\n"
            except StopIteration as stop:
                total_updated += stop.value
                job_offset += counts[idx - 1]
        yield f"All scenarios re-evaluated: {total_updated} job(s) updated across {len(scenarios)} scenario(s)\n"

    return StreamingResponse(stream(), media_type="text/plain")


@router.post("/scenarios/refine")
def refine_all_scenarios(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    client: openai.OpenAI = Depends(get_ai_client),
    model: str = Depends(get_model),
):
    scenarios = q.get_scenarios(conn)

    def stream():
        yield f"Refining criteria for {len(scenarios)} scenario(s)\n"
        for idx, scenario in enumerate(scenarios, start=1):
            label = f"[Scenario {idx}/{len(scenarios)}: {scenario['name']}] "
            yield label + "Requesting criteria proposals from LLM\n"
            existing = q.get_criteria(conn, scenario["id"])
            job_ids = q.get_recent_feedback_job_ids(conn, scenario["id"])
            notes = q.get_recent_feedback_notes(conn, scenario["id"])
            proposals = propose_criteria(client, model, scenario, existing, notes)
            resolved = _resolve_proposals(proposals, existing)
            yield label + f"Received {len(resolved)} proposal(s)\n"
            html = templates.get_template("scenarios/_proposals.html").render(
                request=request,
                proposals=resolved,
                scenario_id=scenario["id"],
                feedback_job_ids=",".join(str(i) for i in job_ids),
            )
            chunk = f'<div id="proposals-area-{scenario["id"]}" style="margin-top:0.75rem; width:100%;">{html}</div>'
            yield "HTML:" + chunk.replace("\n", "") + "\n"
        yield f"Refined criteria proposals for {len(scenarios)} scenario(s)\n"

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
    job_ids = q.get_recent_feedback_job_ids(conn, scenario_id)
    notes = q.get_recent_feedback_notes(conn, scenario_id)

    def stream():
        msg = f"Requesting criteria proposals for '{scenario['name']}' from LLM"
        logger.info(msg)
        yield msg + "\n"
        proposals = propose_criteria(client, model, scenario, existing, notes)
        msg = f"Received {len(proposals)} proposal(s)"
        logger.info(msg)
        yield msg + "\n"
        resolved = _resolve_proposals(proposals, existing)
        html = templates.get_template("scenarios/_proposals.html").render(
            request=request,
            proposals=resolved,
            scenario_id=scenario_id,
            feedback_job_ids=",".join(str(i) for i in job_ids),
        )
        yield "HTML:" + html.replace("\n", "")

    return StreamingResponse(stream(), media_type="text/plain")


@router.post("/scenarios/{scenario_id}/refine/accept", response_class=HTMLResponse)
async def accept_proposals(
    scenario_id: int,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
):
    # One atomic apply for the whole batch: each row carries its proposed
    # kind (add/remove, fixed by the LLM) and whether its checkbox was left
    # checked. Unchecked rows are simply skipped — an HTML checkbox that
    # isn't checked is omitted from form data entirely, so "apply_{i}" only
    # appears in the form when that row should be applied.
    form = await request.form()
    i = 0
    while f"kind_{i}" in form:
        if f"apply_{i}" in form:
            kind = form[f"kind_{i}"]
            if kind == "add":
                q.insert_criterion(conn, scenario_id, form[f"text_{i}"], form[f"weight_{i}"], source="feedback")
            elif kind == "remove":
                q.delete_criterion(conn, int(form[f"criterion_id_{i}"]))
        i += 1
    # The whole batch was reviewed in one go, regardless of which individual
    # rows were applied vs skipped, so its feedback is fully handled now.
    q.mark_feedback_handled(conn, _parse_job_ids(form.get("feedback_job_ids")))
    criteria = q.get_criteria(conn, scenario_id)
    html = templates.get_template("scenarios/_criteria.html").render(
        request=request, criteria=criteria, scenario_id=scenario_id
    )
    html += f'<div id="proposals-area-{scenario_id}" hx-swap-oob="true" style="margin-top:0.75rem; width:100%;"></div>'
    return HTMLResponse(content=html)


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
