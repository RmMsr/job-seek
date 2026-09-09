from __future__ import annotations
import openai
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from app.config import load_config, load_raw_llm, validate_llm, validate_llm_for_save, write_config
from app.providers import LLM_PROVIDERS, provider_groups
from app.template_env import templates
from app.version import get_app_version, get_build_date

router = APIRouter()


def _resolve_endpoint(provider: str, endpoint: str) -> str:
    preset = LLM_PROVIDERS[provider]
    return endpoint if provider == "custom" else preset.endpoint


def _matching_stored_llm(provider: str, endpoint: str) -> dict:
    stored = load_raw_llm() or {}
    if stored.get("provider") != provider:
        return {}
    if provider == "custom" and stored.get("endpoint") != endpoint:
        return {}
    return stored


def _resolve_api_key(provider: str, endpoint: str, api_key: str) -> str:
    if api_key:
        return api_key
    return _matching_stored_llm(provider, endpoint).get("api_key", "")


def _ping(provider: str, endpoint: str, model: str, api_key: str) -> str:
    client = openai.OpenAI(base_url=endpoint, api_key=api_key, timeout=15.0, max_retries=0)
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "Reply with just the word pong."}],
        max_tokens=1,
    )
    return resp.model or model


def _list_models(endpoint: str, api_key: str) -> list[str]:
    client = openai.OpenAI(base_url=endpoint, api_key=api_key, timeout=15.0, max_retries=0)
    return sorted(m.id for m in client.models.list())


@router.get("/setup", response_class=HTMLResponse)
def setup_form(request: Request):
    raw_llm = load_raw_llm()
    if raw_llm is not None and "provider" not in raw_llm:
        # Legacy endpoint-only config (no provider key) is functionally
        # identical to provider="custom" — resolve_llm() already treats it
        # that way. Normalize here so the dropdown and endpoint field
        # reflect the actual stored config instead of silently defaulting
        # to whichever provider happens to render first.
        raw_llm = {**raw_llm, "provider": "custom"}
    cfg = None
    try:
        cfg = load_config()
    except Exception:
        pass
    return templates.TemplateResponse(
        request,
        "setup/index.html",
        {
            "config": cfg,
            "raw_llm": raw_llm,
            "provider_groups": provider_groups(LLM_PROVIDERS),
            "saved": request.query_params.get("saved") == "1",
            "app_version": get_app_version(),
            "build_date": get_build_date(),
        },
    )


@router.post("/setup/test", response_class=HTMLResponse)
def setup_test(
    request: Request,
    provider: str = Form(...),
    api_key: str = Form(""),
    model: str = Form(""),
    endpoint: str = Form(""),
):
    api_key = _resolve_api_key(provider, endpoint, api_key)
    llm = {"provider": provider, "api_key": api_key, "model": model, "endpoint": endpoint}
    err = validate_llm(llm)
    if err:
        return templates.TemplateResponse(
            request,
            "setup/_test_result.html",
            {"ok": False, "message": err},
        )

    resolved_endpoint = _resolve_endpoint(provider, endpoint)
    resolved_key = api_key or "not-needed"

    try:
        responded_model = _ping(provider, resolved_endpoint, model, resolved_key)
    except Exception as exc:
        return templates.TemplateResponse(
            request,
            "setup/_test_result.html",
            {"ok": False, "message": str(exc)},
        )

    return templates.TemplateResponse(
        request,
        "setup/_test_result.html",
        {"ok": True, "message": f"Connection OK — {responded_model} replied."},
    )


@router.post("/setup/models", response_class=HTMLResponse)
def setup_models(
    request: Request,
    provider: str = Form(...),
    api_key: str = Form(""),
    endpoint: str = Form(""),
):
    api_key = _resolve_api_key(provider, endpoint, api_key)
    llm = {"provider": provider, "api_key": api_key, "endpoint": endpoint}
    err = validate_llm(llm)
    if err:
        return templates.TemplateResponse(
            request,
            "setup/_model_options.html",
            {"error": err, "models": []},
        )

    resolved_endpoint = _resolve_endpoint(provider, endpoint)
    resolved_key = api_key or "not-needed"

    try:
        models = _list_models(resolved_endpoint, resolved_key)
    except Exception as exc:
        return templates.TemplateResponse(
            request,
            "setup/_model_options.html",
            {"error": str(exc), "models": []},
        )

    return templates.TemplateResponse(
        request,
        "setup/_model_options.html",
        {"error": None, "models": models},
    )


@router.post("/setup", response_class=HTMLResponse)
def setup_save(
    request: Request,
    provider: str = Form(...),
    api_key: str = Form(""),
    model: str = Form(""),
    endpoint: str = Form(""),
):
    api_key = _resolve_api_key(provider, endpoint, api_key)

    llm = {"provider": provider, "api_key": api_key, "model": model, "endpoint": endpoint}
    err = validate_llm_for_save(llm)
    if err:
        has_stored_key = bool(_matching_stored_llm(provider, endpoint).get("api_key"))
        return templates.TemplateResponse(
            request,
            "setup/index.html",
            {
                "config": None,
                "raw_llm": {"provider": provider, "model": model, "endpoint": endpoint, "api_key": has_stored_key},
                "provider_groups": provider_groups(LLM_PROVIDERS),
                "saved": False,
                "error": err,
                "app_version": get_app_version(),
                "build_date": get_build_date(),
                },
            status_code=400,
        )

    write_config(
        provider=provider,
        api_key=api_key,
        model=model,
        endpoint=endpoint,
    )
    return RedirectResponse(url="/setup?saved=1", status_code=303)
