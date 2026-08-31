from __future__ import annotations
import html as html_lib
import sqlite3
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from app.deps import get_db
from app.db import queries as q
from app.ai.refine import propose_criteria, match_removal_target
from app.pipeline import run_reevaluate, run_reassess_fit
from app.task_engine import register_task_kind
from app.template_env import templates

router = APIRouter()

_WEIGHT_ORDER = {"must": 0, "prefer": 1, "avoid": 2}


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


def _feedback_quality_verdict(higher: int, lower: int) -> dict:
    total = higher + lower
    if total == 0:
        return {"label": "No unhandled feedback yet", "css_class": "score-neutral"}
    ratio = higher / total
    if ratio > 0.6:
        return {"label": "Criteria may be too strict — consider loosening", "css_class": "score-mid"}
    if ratio < 0.4:
        return {"label": "Criteria may be too loose — consider tightening", "css_class": "score-mid"}
    return {"label": "Feedback seems balanced", "css_class": "score-high"}


def _scenarios_context(conn: sqlite3.Connection) -> dict:
    scenarios = q.get_scenarios(conn)
    criteria_by_scenario = {s["id"]: q.get_criteria(conn, s["id"]) for s in scenarios}
    feedback_counts_by_scenario = {s["id"]: q.get_recent_feedback_counts(conn, s["id"]) for s in scenarios}
    feedback_verdict_by_scenario = {
        s["id"]: _feedback_quality_verdict(
            feedback_counts_by_scenario[s["id"]]["unhandled_higher"],
            feedback_counts_by_scenario[s["id"]]["unhandled_lower"],
        )
        for s in scenarios
    }
    return {
        "scenarios": scenarios,
        "criteria_by_scenario": criteria_by_scenario,
        "feedback_counts_by_scenario": feedback_counts_by_scenario,
        "feedback_verdict_by_scenario": feedback_verdict_by_scenario,
    }


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


@register_task_kind("scenarios_reevaluate_all")
def _task_scenarios_reevaluate_all(conn, client, model, config, params):
    scenarios = q.get_scenarios(conn)
    yield f"Re-evaluating {len(scenarios)} scenario(s)"
    total_updated = 0
    for idx, scenario in enumerate(scenarios, start=1):
        label = f"[Scenario {idx}/{len(scenarios)}: {scenario['name']}] "
        gen = run_reevaluate(conn, client, model, scenario, scenario_label=label)
        try:
            while True:
                yield next(gen)
        except StopIteration as stop:
            total_updated += stop.value

    fit_gen = run_reassess_fit(conn, client, model)
    fit_updated = 0
    try:
        while True:
            yield next(fit_gen)
    except StopIteration as stop:
        fit_updated = stop.value

    yield (
        f"All scenarios re-evaluated: {total_updated} job(s) updated across "
        f"{len(scenarios)} scenario(s); fit recomputed for {fit_updated} job(s)"
    )
    return {"notices": [], "html_chunks": []}


@register_task_kind("scenarios_refine_all")
def _task_scenarios_refine_all(conn, client, model, config, params):
    scenarios = q.get_scenarios(conn)
    yield f"Refining criteria for {len(scenarios)} scenario(s)"
    html_chunks = []
    scenarios_with_proposals = []
    for idx, scenario in enumerate(scenarios, start=1):
        label = f"[Scenario {idx}/{len(scenarios)}: {scenario['name']}] "
        yield label + "Requesting criteria proposals from LLM"
        existing = q.get_criteria(conn, scenario["id"])
        anchor = q.get_recent_feedback_anchor(conn, scenario["id"])
        notes = q.get_recent_feedback_notes(conn, scenario["id"])
        proposals = propose_criteria(client, model, scenario, existing, notes)
        resolved = _resolve_proposals(proposals, existing)
        yield label + f"Received {len(resolved)} proposal(s)"
        if resolved:
            scenarios_with_proposals.append(scenario)
        html = templates.get_template("scenarios/_proposals.html").render(
            request=None, proposals=resolved, scenario_id=scenario["id"], feedback_anchor=anchor,
        )
        html_chunks.append(f'<div id="proposals-area-{scenario["id"]}" style="margin-top:0.75rem; width:100%;">{html}</div>')
    yield f"Refined criteria proposals for {len(scenarios)} scenario(s)"
    notices = []
    if scenarios_with_proposals:
        links = ", ".join(
            f'<a href="#proposals-area-{s["id"]}">{html_lib.escape(s["name"])}</a>' for s in scenarios_with_proposals
        )
        notices.append({"level": "info", "html": f"<p>New suggestions for: {links}</p>"})
    return {"notices": notices, "html_chunks": html_chunks}


@register_task_kind("scenario_refine_one")
def _task_scenario_refine_one(conn, client, model, config, params):
    scenario = q.get_scenario(conn, params["scenario_id"])
    if scenario is None:
        return {"notices": [], "html_chunks": []}
    existing = q.get_criteria(conn, scenario["id"])
    anchor = q.get_recent_feedback_anchor(conn, scenario["id"])
    notes = q.get_recent_feedback_notes(conn, scenario["id"])
    yield f"Requesting criteria proposals for '{scenario['name']}' from LLM"
    proposals = propose_criteria(client, model, scenario, existing, notes)
    yield f"Received {len(proposals)} proposal(s)"
    resolved = _resolve_proposals(proposals, existing)
    html = templates.get_template("scenarios/_proposals.html").render(
        request=None, proposals=resolved, scenario_id=scenario["id"], feedback_anchor=anchor,
    )
    return {"notices": [], "html_chunks": [html]}


@router.post("/scenarios/reevaluate")
def reevaluate_all_scenarios(conn: sqlite3.Connection = Depends(get_db)):
    task = q.enqueue_task(conn, kind="scenarios_reevaluate_all", params={})
    return {"task_id": task["id"], "already_active": task["already_active"]}


@router.post("/scenarios/refine")
def refine_all_scenarios(conn: sqlite3.Connection = Depends(get_db)):
    scenarios = q.get_scenarios(conn)
    has_feedback = any(q.get_recent_feedback_notes(conn, s["id"]) for s in scenarios)
    if not has_feedback:
        return {"skipped": True, "message": "No feedback to review yet."}
    task = q.enqueue_task(conn, kind="scenarios_refine_all", params={})
    return {"task_id": task["id"], "already_active": task["already_active"]}


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
    return templates.TemplateResponse(
        request, "scenarios/_header_edit.html",
        {"scenario": scenario, "job_scores_count": q.count_job_scores_for_scenario(conn, scenario_id)},
    )


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
    gate_threshold: float = Form(0.7),
    conn: sqlite3.Connection = Depends(get_db),
):
    _get_scenario_or_404(conn, scenario_id)
    q.update_scenario(conn, scenario_id, name=name, description=description, gate_threshold=gate_threshold)
    scenario = q.get_scenario(conn, scenario_id)
    return templates.TemplateResponse(request, "scenarios/_header.html", {"scenario": scenario})


@router.delete("/scenarios/{scenario_id}")
def delete_scenario(scenario_id: int, conn: sqlite3.Connection = Depends(get_db)):
    _get_scenario_or_404(conn, scenario_id)
    q.delete_scenario(conn, scenario_id)
    # Deleting a scenario reshapes the whole tab strip; a full client redirect
    # is cleaner and more robust than swapping the entire page body via htmx.
    return HTMLResponse(content="", headers={"HX-Redirect": "/scenarios"})


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
def refine_criteria(scenario_id: int, conn: sqlite3.Connection = Depends(get_db)):
    scenario = _get_scenario_or_404(conn, scenario_id)
    if not q.get_recent_feedback_notes(conn, scenario_id):
        return {"skipped": True, "message": f"No feedback to review for '{scenario['name']}' yet."}
    task = q.enqueue_task(conn, kind="scenario_refine_one", params={"scenario_id": scenario_id})
    return {"task_id": task["id"], "already_active": task["already_active"]}


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
    added = removed = 0
    while f"kind_{i}" in form:
        if f"apply_{i}" in form:
            kind = form[f"kind_{i}"]
            if kind == "add":
                q.insert_criterion(conn, scenario_id, form[f"text_{i}"], form[f"weight_{i}"], source="feedback")
                added += 1
            elif kind == "remove":
                q.delete_criterion(conn, int(form[f"criterion_id_{i}"]))
                removed += 1
        i += 1
    # The whole batch was reviewed in one go, regardless of which individual
    # rows were applied vs skipped, so its feedback is fully handled now.
    q.mark_feedback_handled(conn, scenario_id, form.get("feedback_anchor") or None)
    criteria = q.get_criteria(conn, scenario_id)
    html = templates.get_template("scenarios/_criteria.html").render(
        request=request, criteria=criteria, scenario_id=scenario_id
    )
    parts = []
    if added:
        parts.append(f"{added} criteri{'on' if added == 1 else 'a'} added")
    if removed:
        parts.append(f"{removed} removed")
    summary = "✓ " + (", ".join(parts) if parts else "Reviewed — no changes")
    html += (
        f'<div id="proposals-area-{scenario_id}" hx-swap-oob="true" '
        'style="margin-top:0.75rem; width:100%;">'
        f'<p class="save-confirmation">{summary}</p></div>'
    )
    return HTMLResponse(content=html)
