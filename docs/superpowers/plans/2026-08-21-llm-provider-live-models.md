# LLM Provider Live Model Fetch — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hardcoded per-provider default model in the LLM setup page with a live `GET /v1/models` fetch, so a provider releasing a new model never requires a code change here.

**Architecture:** `app/providers.py` keeps a small hardcoded table of provider base URLs (stable, not the maintenance problem) but drops `default_model` entirely. `app/routes/setup.py` gains a `POST /setup/models` route that builds an `openai.OpenAI` client from the form's endpoint/key and calls `client.models.list()`, returning an htmx-swapped `<datalist>` partial. The model `<input>` stays free-text with `list="model-options"` — never a strict `<select>` — so a provider whose model catalog is huge (OpenRouter) or whose `/v1/models` is stale/unsupported never blocks the user. `app/config.py` gains `validate_llm_for_save`, which requires a non-empty model for hosted providers only at save time, since there's no more preset default to fall back to.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, htmx, `openai` SDK (already a dependency), pytest.

Spec: `docs/superpowers/specs/2026-08-20-llm-provider-setup-design.md` (revised 2026-08-21)

## Global Constraints

- No new dependency — the model list comes from the existing `openai` SDK's `client.models.list()`.
- The model field is always free-text (`<input list="model-options">` + `<datalist>`), never a strict `<select>` — a provider's `/v1/models` list may be incomplete, huge, or unsupported.
- "Load models" is an explicit button click only — never fetched automatically on blur or provider change.
- `ProviderPreset` carries only `endpoint`, `requires_api_key`, `group` — no per-provider model names anywhere in the codebase.
- The hosted-provider "model required" rule is enforced only at save time (`POST /setup`). `/setup/test` and `/setup/models` need only endpoint+key to run.
- No caching of fetched model lists across requests.
- `uv run pytest` is the test command. The sandbox blocks `uv`'s cache directory (read-only), so run it with the sandbox disabled if you hit a "Could not acquire lock" / read-only filesystem error.

---

## Task 1: Drop `default_model` from the provider catalog

**Files:**
- Modify: `app/providers.py`
- Test: `tests/test_providers.py`, `tests/test_config.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `ProviderPreset(endpoint: str, requires_api_key: bool = False, group: str = LOCAL)` (no `default_model` field). `resolve_llm(llm: dict) -> tuple[endpoint: str, model: str, api_key: str]` — `model` is now `llm.get("model", "")` verbatim, no preset fallback. `endpoint(provider)`, `requires_api_key(provider)`, `provider_groups(providers)` unchanged in signature. The module-level `default_model(provider)` function is removed.

- [ ] **Step 1: Write the failing tests**

Replace `tests/test_providers.py` entirely:

```python
from app.providers import (
    LLM_PROVIDERS,
    endpoint,
    provider_groups,
    requires_api_key,
    resolve_llm,
)


def test_catalog_has_all_expected_providers():
    assert set(LLM_PROVIDERS) == {
        "openai",
        "openrouter",
        "groq",
        "gemini",
        "deepseek",
        "mistral",
        "xai",
        "ollama",
        "llamacpp",
        "lmstudio",
        "vllm",
        "custom",
    }


def test_hosted_providers_require_api_key():
    assert requires_api_key("openai")
    assert endpoint("openai") == "https://api.openai.com/v1"
    assert endpoint("gemini") == "https://generativelanguage.googleapis.com/v1beta/openai/"


def test_local_providers_do_not_require_api_key():
    assert not requires_api_key("ollama")
    assert not requires_api_key("llamacpp")
    assert endpoint("llamacpp") == "http://localhost:8080/v1"


def test_resolve_llm_known_provider_derives_endpoint_no_default_model():
    endpoint_, model, api_key = resolve_llm({"provider": "groq"})
    assert endpoint_ == "https://api.groq.com/openai/v1"
    assert model == ""
    assert api_key == "not-needed"


def test_resolve_llm_explicit_model_and_key_win():
    endpoint_, model, api_key = resolve_llm(
        {"provider": "deepseek", "model": "deepseek-reasoner", "api_key": "sk-123"}
    )
    assert endpoint_ == "https://api.deepseek.com/v1"
    assert model == "deepseek-reasoner"
    assert api_key == "sk-123"


def test_resolve_llm_local_provider_allows_empty_model():
    endpoint_, model, api_key = resolve_llm({"provider": "llamacpp", "model": ""})
    assert endpoint_ == "http://localhost:8080/v1"
    assert model == ""
    assert api_key == "not-needed"


def test_resolve_llm_custom_uses_endpoint():
    endpoint_, _, _ = resolve_llm({"provider": "custom", "endpoint": "http://my-server/v1", "model": "m1"})
    assert endpoint_ == "http://my-server/v1"


def test_resolve_llm_legacy_endpoint_only_config():
    endpoint_, model, api_key = resolve_llm({"endpoint": "http://legacy:9000/v1", "model": "legacy-model"})
    assert endpoint_ == "http://legacy:9000/v1"
    assert model == "legacy-model"
    assert api_key == "not-needed"


def test_provider_groups_bucket_providers():
    groups = provider_groups(LLM_PROVIDERS)
    by_group = {name: [key for key, _ in items] for name, items in groups}
    assert set(by_group) == {"Hosted", "Local", "Custom"}
    assert by_group["Hosted"] == [
        "openai",
        "openrouter",
        "groq",
        "gemini",
        "deepseek",
        "mistral",
        "xai",
    ]
    assert by_group["Local"] == ["ollama", "llamacpp", "lmstudio", "vllm"]
    assert by_group["Custom"] == ["custom"]
```

In `tests/test_config.py`, fix the two tests whose expectations relied on the (now-removed) preset default model — `resolve_llm` is called transitively by `load_config`/`check_config_status`, so these break even though `app/config.py` itself isn't touched in this task:

Replace:
```python
def test_load_config_known_provider_derives_endpoint_and_default_model(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[llm]\nprovider = "groq"\n'
        '[database]\npath = "test.db"\n'
        '[browser]\nprofile_dir = "browser-profile"\n'
    )
    result = load_config(str(cfg))
    assert result.llm_endpoint == "https://api.groq.com/openai/v1"
    assert result.llm_model == "llama-3.3-70b-versatile"
    assert result.llm_api_key == "not-needed"
```
with:
```python
def test_load_config_known_provider_derives_endpoint_no_default_model(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[llm]\nprovider = "groq"\n'
        '[database]\npath = "test.db"\n'
        '[browser]\nprofile_dir = "browser-profile"\n'
    )
    result = load_config(str(cfg))
    assert result.llm_endpoint == "https://api.groq.com/openai/v1"
    assert result.llm_model == ""
    assert result.llm_api_key == "not-needed"
```

Replace:
```python
def test_check_config_status_ok_with_known_provider(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[llm]\nprovider = "ollama"\n'
        '[database]\npath = "x.db"\n'
        '[browser]\nprofile_dir = "x"\n'
    )
    status = check_config_status(str(cfg))
    assert status.exists and status.has_llm_endpoint and status.has_llm_model
    assert status.ok
```
with:
```python
def test_check_config_status_local_provider_without_model_not_ok(tmp_path):
    # No preset default to fall back to any more: a provider-only config
    # resolves to an empty model, which check_config_status already treats
    # as "not fully configured" (has_llm_model requires a non-empty string).
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[llm]\nprovider = "ollama"\n'
        '[database]\npath = "x.db"\n'
        '[browser]\nprofile_dir = "x"\n'
    )
    status = check_config_status(str(cfg))
    assert status.exists and status.has_llm_endpoint
    assert not status.has_llm_model
    assert not status.ok


def test_check_config_status_ok_with_known_provider_and_model(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[llm]\nprovider = "ollama"\nmodel = "llama3.2"\n'
        '[database]\npath = "x.db"\n'
        '[browser]\nprofile_dir = "x"\n'
    )
    status = check_config_status(str(cfg))
    assert status.exists and status.has_llm_endpoint and status.has_llm_model
    assert status.ok
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_providers.py tests/test_config.py -v`
Expected: FAIL — `test_resolve_llm_known_provider_derives_endpoint_no_default_model` and `test_load_config_known_provider_derives_endpoint_no_default_model` fail because `resolve_llm` still falls back to the preset default model (`model == ""` assertion fails, actual is `"llama-3.3-70b-versatile"`); `test_check_config_status_local_provider_without_model_not_ok` fails because `status.ok` is still `True`.

- [ ] **Step 3: Implement — drop `default_model` from `app/providers.py`**

Replace the whole file:

```python
from __future__ import annotations
from dataclasses import dataclass

HOSTED = "Hosted"
LOCAL = "Local"
CUSTOM = "Custom"


@dataclass(frozen=True)
class ProviderPreset:
    endpoint: str
    requires_api_key: bool = False
    group: str = LOCAL


LLM_PROVIDERS: dict[str, ProviderPreset] = {
    "openai": ProviderPreset("https://api.openai.com/v1", True, HOSTED),
    "openrouter": ProviderPreset("https://openrouter.ai/api/v1", True, HOSTED),
    "groq": ProviderPreset("https://api.groq.com/openai/v1", True, HOSTED),
    "gemini": ProviderPreset("https://generativelanguage.googleapis.com/v1beta/openai/", True, HOSTED),
    "deepseek": ProviderPreset("https://api.deepseek.com/v1", True, HOSTED),
    "mistral": ProviderPreset("https://api.mistral.ai/v1", True, HOSTED),
    "xai": ProviderPreset("https://api.x.ai/v1", True, HOSTED),
    "ollama": ProviderPreset("http://localhost:11434/v1", False, LOCAL),
    "llamacpp": ProviderPreset("http://localhost:8080/v1", False, LOCAL),
    "lmstudio": ProviderPreset("http://localhost:1234/v1", False, LOCAL),
    "vllm": ProviderPreset("http://localhost:8000/v1", False, LOCAL),
    "custom": ProviderPreset("", False, CUSTOM),
}


def endpoint(provider: str) -> str:
    return LLM_PROVIDERS[provider].endpoint


def requires_api_key(provider: str) -> bool:
    return LLM_PROVIDERS[provider].requires_api_key


def resolve_llm(llm: dict) -> tuple[str, str, str]:
    provider = llm.get("provider", "")
    if provider in LLM_PROVIDERS and provider != "custom":
        llm_endpoint = LLM_PROVIDERS[provider].endpoint
    else:
        # custom provider, or legacy endpoint-only config with no provider key
        llm_endpoint = llm.get("endpoint", "")
    model = llm.get("model", "")
    api_key = llm.get("api_key") or "not-needed"
    return llm_endpoint, model, api_key


def provider_groups(providers: dict[str, ProviderPreset]) -> list[tuple[str, list[tuple[str, str]]]]:
    groups: list[tuple[str, list[tuple[str, str]]]] = []
    for group in (HOSTED, LOCAL, CUSTOM):
        items = [(key, key.capitalize()) for key, preset in providers.items() if preset.group == group]
        if items:
            groups.append((group, items))
    return groups
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_providers.py tests/test_config.py -v`
Expected: PASS (all tests in both files)

- [ ] **Step 5: Commit**

```bash
git add app/providers.py tests/test_providers.py tests/test_config.py
git commit -m "feat: drop hardcoded default model from provider catalog"
```

---

## Task 2: Require a model at save time for hosted providers

**Files:**
- Modify: `app/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: `LLM_PROVIDERS` and `validate_llm` (both already in `app/config.py`).
- Produces: `validate_llm_for_save(llm: dict) -> str | None` — runs `validate_llm` first (unknown provider / hosted-missing-key / custom-missing-endpoint), then additionally rejects a hosted provider with an empty `model`. Used by `app/routes/setup.py` in Task 3.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py` (and add `validate_llm_for_save` to the existing `from app.config import ...` line at the top):

```python
def test_validate_llm_for_save_hosted_requires_model():
    err = validate_llm_for_save({"provider": "openai", "api_key": "sk-xyz", "model": ""})
    assert err is not None
    assert "model" in err.lower()


def test_validate_llm_for_save_hosted_with_model_ok():
    assert validate_llm_for_save({"provider": "openai", "api_key": "sk-xyz", "model": "gpt-4o"}) is None


def test_validate_llm_for_save_local_allows_empty_model():
    assert validate_llm_for_save({"provider": "ollama", "model": ""}) is None


def test_validate_llm_for_save_propagates_base_validation_errors():
    err = validate_llm_for_save({"provider": "openai", "api_key": "", "model": ""})
    assert err is not None
    assert "api key" in err.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py -v -k validate_llm_for_save`
Expected: FAIL — `ImportError: cannot import name 'validate_llm_for_save' from 'app.config'`

- [ ] **Step 3: Implement — add `validate_llm_for_save` to `app/config.py`**

Add directly below the existing `validate_llm` function:

```python
def validate_llm_for_save(llm: dict) -> str | None:
    err = validate_llm(llm)
    if err:
        return err
    provider = llm.get("provider", "")
    if provider != "custom" and LLM_PROVIDERS[provider].requires_api_key and not llm.get("model"):
        return f"A model is required for {provider}."
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add app/config.py tests/test_config.py
git commit -m "feat: require a model at save time for hosted LLM providers"
```

---

## Task 3: `POST /setup/models` — live model fetch

**Files:**
- Modify: `app/routes/setup.py`
- Create: `app/templates/setup/_model_options.html`
- Test: `tests/test_routes_setup.py`

**Interfaces:**
- Consumes: `validate_llm`, `validate_llm_for_save` (Task 2, `app/config.py`), `LLM_PROVIDERS` (Task 1, `app/providers.py`).
- Produces: `_resolve_endpoint(provider: str, endpoint: str) -> str` and `_list_models(endpoint: str, api_key: str) -> list[str]` (module-private helpers in `app/routes/setup.py`, patched directly in tests the same way `_ping` already is). Route `POST /setup/models` (form fields `provider`, `api_key`, `endpoint` — no `model`), returns the `setup/_model_options.html` partial. `setup_save` now uses `validate_llm_for_save` instead of `validate_llm`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_routes_setup.py`:

```python
def test_setup_post_rejects_hosted_without_model(client, monkeypatch, tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[llm]\nprovider = "groq"\n'
        '[database]\npath = "test.db"\n'
        '[browser]\nprofile_dir = "browser-profile"\n'
    )
    from app.config import check_config_status, load_config
    monkeypatch.setattr(check_config_status, "__defaults__", (str(config_path),))
    monkeypatch.setattr(load_config, "__defaults__", (str(config_path),))
    import os
    original_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        resp = client.post(
            "/setup",
            data={"provider": "openai", "api_key": "sk-new", "model": "", "endpoint": ""},
            follow_redirects=False,
        )
        assert resp.status_code == 400
        assert "model is required" in resp.text.lower()
        assert 'provider = "openai"' not in config_path.read_text()
    finally:
        os.chdir(original_cwd)


def test_setup_models_ok_with_mock(client, monkeypatch, tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[llm]\nprovider = "groq"\n'
        '[database]\npath = "test.db"\n'
        '[browser]\nprofile_dir = "browser-profile"\n'
    )
    from app.config import check_config_status, load_config
    monkeypatch.setattr(check_config_status, "__defaults__", (str(config_path),))
    monkeypatch.setattr(load_config, "__defaults__", (str(config_path),))
    import app.routes.setup as setup_mod
    monkeypatch.setattr(setup_mod, "_list_models", lambda *a, **kw: ["gpt-4o", "gpt-4o-mini"])

    resp = client.post(
        "/setup/models",
        data={"provider": "openai", "api_key": "sk-xyz", "endpoint": ""},
    )
    assert resp.status_code == 200
    assert '<datalist id="model-options">' in resp.text
    assert 'value="gpt-4o"' in resp.text
    assert 'value="gpt-4o-mini"' in resp.text


def test_setup_models_error_without_key(client, monkeypatch, tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[llm]\nprovider = "groq"\n'
        '[database]\npath = "test.db"\n'
        '[browser]\nprofile_dir = "browser-profile"\n'
    )
    from app.config import check_config_status, load_config
    monkeypatch.setattr(check_config_status, "__defaults__", (str(config_path),))
    monkeypatch.setattr(load_config, "__defaults__", (str(config_path),))

    resp = client.post(
        "/setup/models",
        data={"provider": "openai", "api_key": "", "endpoint": ""},
    )
    assert resp.status_code == 200
    assert "API key is required" in resp.text


def test_setup_models_error_on_client_failure(client, monkeypatch, tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[llm]\nprovider = "groq"\n'
        '[database]\npath = "test.db"\n'
        '[browser]\nprofile_dir = "browser-profile"\n'
    )
    from app.config import check_config_status, load_config
    monkeypatch.setattr(check_config_status, "__defaults__", (str(config_path),))
    monkeypatch.setattr(load_config, "__defaults__", (str(config_path),))
    import app.routes.setup as setup_mod

    def _boom(*a, **kw):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(setup_mod, "_list_models", _boom)

    resp = client.post(
        "/setup/models",
        data={"provider": "openai", "api_key": "sk-xyz", "endpoint": ""},
    )
    assert resp.status_code == 200
    assert "connection refused" in resp.text
    assert '<datalist id="model-options"></datalist>' in resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_routes_setup.py -v`
Expected: FAIL — `test_setup_post_rejects_hosted_without_model` fails (still 303, config gets written) because `setup_save` still calls `validate_llm`, not `validate_llm_for_save`; the three `test_setup_models_*` tests fail with 404 since `POST /setup/models` doesn't exist yet.

- [ ] **Step 3: Create the model-options partial**

`app/templates/setup/_model_options.html`:

```html
{% if error %}
<div class="notice" style="background: #f8d7da; border-color: #dc3545; color: #721c24;">
  <p>{{ error }}</p>
  <button class="notice-dismiss" onclick="this.parentElement.remove()">×</button>
</div>
<datalist id="model-options"></datalist>
{% else %}
<datalist id="model-options">
  {% for m in models %}
  <option value="{{ m }}"></option>
  {% endfor %}
</datalist>
<p>{{ models|length }} model(s) loaded — start typing in the Model field to pick one.</p>
{% endif %}
```

- [ ] **Step 4: Implement — rewrite `app/routes/setup.py`**

Replace the whole file:

```python
from __future__ import annotations
import openai
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from app.config import load_config, load_raw_llm, validate_llm, validate_llm_for_save, write_config
from app.providers import LLM_PROVIDERS, provider_groups
from app.template_env import templates

router = APIRouter()


def _resolve_endpoint(provider: str, endpoint: str) -> str:
    preset = LLM_PROVIDERS[provider]
    return endpoint if provider == "custom" else preset.endpoint


def _ping(provider: str, endpoint: str, model: str, api_key: str) -> str:
    client = openai.OpenAI(base_url=endpoint, api_key=api_key)
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "Reply with just the word pong."}],
        max_tokens=1,
    )
    return resp.choices[0].message.content or "pong"


def _list_models(endpoint: str, api_key: str) -> list[str]:
    client = openai.OpenAI(base_url=endpoint, api_key=api_key)
    return sorted(m.id for m in client.models.list())


@router.get("/setup", response_class=HTMLResponse)
def setup_form(request: Request):
    raw_llm = load_raw_llm()
    cfg = None
    try:
        cfg = load_config()
    except Exception:
        pass
    return templates.TemplateResponse(
        request,
        "setup/index.html",
        {"config": cfg, "raw_llm": raw_llm, "provider_groups": provider_groups(LLM_PROVIDERS)},
    )


@router.post("/setup/test", response_class=HTMLResponse)
def setup_test(
    request: Request,
    provider: str = Form(...),
    api_key: str = Form(""),
    model: str = Form(""),
    endpoint: str = Form(""),
):
    llm = {"provider": provider, "api_key": api_key, "model": model, "endpoint": endpoint}
    err = validate_llm(llm)
    if err:
        return templates.TemplateResponse(
            request,
            "setup/_test_result.html",
            {"ok": False, "message": err},
        )

    resolved_endpoint = _resolve_endpoint(provider, endpoint)
    if not model:
        return templates.TemplateResponse(
            request,
            "setup/_test_result.html",
            {"ok": False, "message": "Model is required to test the connection."},
        )
    resolved_key = api_key or "not-needed"

    try:
        _ping(provider, resolved_endpoint, model, resolved_key)
    except Exception as exc:
        return templates.TemplateResponse(
            request,
            "setup/_test_result.html",
            {"ok": False, "message": str(exc)},
        )

    return templates.TemplateResponse(
        request,
        "setup/_test_result.html",
        {"ok": True, "message": f"Connection OK — {model} replied."},
    )


@router.post("/setup/models", response_class=HTMLResponse)
def setup_models(
    request: Request,
    provider: str = Form(...),
    api_key: str = Form(""),
    endpoint: str = Form(""),
):
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
    provider: str = Form(...),
    api_key: str = Form(""),
    model: str = Form(""),
    endpoint: str = Form(""),
):
    llm = {"provider": provider, "api_key": api_key, "model": model, "endpoint": endpoint}
    err = validate_llm_for_save(llm)
    if err:
        raise HTTPException(status_code=400, detail=err)

    write_config(
        provider=provider,
        api_key=api_key,
        model=model,
        endpoint=endpoint,
    )
    return RedirectResponse(url="/setup?saved=1", status_code=303)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_routes_setup.py -v`
Expected: PASS (all tests)

- [ ] **Step 6: Commit**

```bash
git add app/routes/setup.py app/templates/setup/_model_options.html tests/test_routes_setup.py
git commit -m "feat: POST /setup/models fetches the live model list from the provider"
```

---

## Task 4: Wire the "Load models" button into the setup page

**Files:**
- Modify: `app/templates/setup/index.html`
- Test: `tests/test_routes_setup.py`

**Interfaces:**
- Consumes: `POST /setup/models` (Task 3) via htmx; `setup/_model_options.html`'s `<datalist id="model-options">` (Task 3).
- Produces: nothing consumed by later tasks — this is the last code task.

- [ ] **Step 1: Write the failing test**

Extend the existing `test_setup_get_renders_form` in `tests/test_routes_setup.py` — add these assertions at the end of the function (after the existing ones, still inside the `try`/before the function ends — there's no `finally` in this particular test, so just append):

```python
    assert 'hx-post="/setup/models"' in resp.text
    assert 'list="model-options"' in resp.text
    assert "DEFAULTS" not in resp.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_routes_setup.py -v -k test_setup_get_renders_form`
Expected: FAIL — `assert 'hx-post="/setup/models"' in resp.text` fails, the button doesn't exist yet.

- [ ] **Step 3: Implement — rewrite `app/templates/setup/index.html`**

Replace the whole file:

```html
{% extends "base.html" %}
{% block title %}Setup — Job Seek{% endblock %}
{% block content %}

<h1>LLM Provider Setup</h1>

{% if saved %}
<div class="notice">
  <p>Configuration saved. You can now close this page and use the app.</p>
  <button class="notice-dismiss" onclick="this.parentElement.remove()">×</button>
</div>
{% endif %}

<form method="post" action="/setup">
  <div>
    <label>Provider
      <select name="provider" id="provider" required>
        {% for group_name, items in provider_groups %}
        <optgroup label="{{ group_name }}">
          {% for key, label in items %}
          <option value="{{ key }}" {% if raw_llm and raw_llm.provider == key %}selected{% elif raw_llm is none and key == "llamacpp" %}selected{% endif %}>
            {{ label }}
          </option>
          {% endfor %}
        </optgroup>
        {% endfor %}
      </select>
    </label>
  </div>

  <div id="endpoint-row" style="display: none;">
    <label>Endpoint URL
      <input type="url" name="endpoint" id="endpoint" placeholder="https://your-llm.example/v1" {% if raw_llm and raw_llm.provider == "custom" and raw_llm.endpoint %}value="{{ raw_llm.endpoint }}"{% endif %}>
    </label>
  </div>

  <div>
    <label>API Key (required for hosted providers)
      <input type="password" name="api_key" id="api_key" placeholder="sk-..." autocomplete="off">
    </label>
  </div>

  <div>
    <label>Model (required for hosted providers; optional for local/custom)
      <input type="text" name="model" id="model" list="model-options" placeholder="e.g. gpt-4o-mini" {% if raw_llm and raw_llm.model %}value="{{ raw_llm.model }}"{% elif config and config.llm_model %}value="{{ config.llm_model }}"{% endif %}>
    </label>
    <button type="button" class="btn" hx-post="/setup/models" hx-target="#model-options-container" hx-swap="innerHTML" hx-include="[name='provider'], [name='api_key'], [name='endpoint']">
      Load models
    </button>
    <div id="model-options-container"><datalist id="model-options"></datalist></div>
  </div>

  <div style="display: flex; gap: 0.5rem; margin-top: 1rem; align-items: center;">
    <button type="submit" class="btn">Save</button>
    <button type="button" class="btn" hx-post="/setup/test" hx-target="#test-result" hx-swap="innerHTML" hx-include="[name='provider'], [name='api_key'], [name='model'], [name='endpoint']">
      Test Connection
    </button>
  </div>

  <div id="test-result" style="margin-top: 0.75rem;"></div>
</form>

<script>
(function () {
  var providerSelect = document.getElementById("provider");
  var endpointRow = document.getElementById("endpoint-row");

  function updateUI() {
    endpointRow.style.display = (providerSelect.value === "custom") ? "block" : "none";
  }

  providerSelect.addEventListener("change", updateUI);
  updateUI();
})();
</script>
{% endblock %}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_routes_setup.py -v`
Expected: PASS (all tests)

Then run the full suite to catch any regression:

Run: `uv run pytest -q`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add app/templates/setup/index.html tests/test_routes_setup.py
git commit -m "feat: wire the Load models button and datalist into the setup page"
```

---

## Task 5: Manual verification

Use the `run-dev-server` skill (throwaway `job-seek.db` copy, `--reload`). `config.toml` is already present in this worktree.

- [ ] **Step 1: Start the dev server**

Follow the `run-dev-server` skill's recipe.

- [ ] **Step 2: Exercise `/setup`**

- Visit `/setup`. Confirm the Model field is empty (or shows the current config's model) — no provider-specific text is auto-filled when switching providers, only the endpoint row toggles for "Custom".
- Pick a reachable provider (the local one already configured, or any hosted provider you have a real key for), fill in the API key if needed, click **Load models**. Confirm the request succeeds and the Model field's autocomplete offers real model names when you start typing (the browser's native `<datalist>` UI). Confirm you can still type an arbitrary model name not in the list.
- Click **Load models** with a hosted provider and no API key filled in. Confirm the inline error ("An API key is required for …") appears and no request to the provider was made.
- Select "Hosted" → e.g. OpenAI, leave Model empty, click **Save**. Confirm a 400 with "A model is required for openai." and that `config.toml` was not overwritten.
- Fill in a valid model, click **Save**. Confirm the redirect to `/setup?saved=1` and the success banner.

- [ ] **Step 3: Confirm the rest of the app still works**

Visit `/jobs` (or the home page) and confirm nothing broke.

- [ ] **Step 4: Hand off**

Leave the dev server running and give the user the URL — do not stop it or offer to merge/clean up until they've tried it themselves (per CLAUDE.md's UI dev-server handoff convention).
