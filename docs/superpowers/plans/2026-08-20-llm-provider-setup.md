# LLM Provider Setup Page — Plan

Date: 2026-08-20
Spec: docs/superpowers/specs/2026-08-20-llm-provider-setup-design.md

TDD throughout: write/update tests first, watch them fail, then implement.
Commit after each green task (CLAUDE.md: commit frequently). All work in the
`llm-provider-setup` worktree. `uv run pytest` is the test command.

## Task 1 — Provider preset catalog (`app/providers.py`)

New module with the provider table (endpoint, default model, api-key-required
flag, group) and resolution helpers used by config:

- `LLM_PROVIDERS: dict[str, ProviderPreset]` — the 11 presets + custom,
  exactly as in the spec table.
- `resolve_llm(llm: dict) -> tuple[endpoint, model, api_key]` — endpoint from
  preset or (custom/legacy) `llm["endpoint"]`; model from `llm["model"]` or
  preset default; api_key from `llm["api_key"]` or `"not-needed"`.
- `default_model(provider)`, `endpoint(provider)`, `requires_api_key(provider)`
  convenience accessors.
- `provider_groups(providers: dict) -> list[(group, [(key, label)])]` for
  rendering the dropdown.

Failing tests first in `tests/test_providers.py`. Keep pure (no I/O) so tests
are trivial.

## Task 2 — Config wiring (`app/config.py`)

- `Config` gains `llm_api_key: str`.
- `load_config()` calls `resolve_llm(raw.get("llm", {}))` for endpoint/model/
  api_key, keeping `["database"]["path"]` / `["browser"]["profile_dir"]` as-is.
- `check_config_status()` unchanged semantics: ok when config exists and an
  endpoint + model resolved (bool(resolve) on the llm dict).

Extend `tests/test_config.py`: known provider file → derived endpoint + default
model + `"not-needed"` key; custom/legacy endpoint file → legacy behavior;
api_key respected; missing/broken config → not ok. Existing test
(`endpoint`-only file) must still pass unchanged (backwards compat).

## Task 3 — Client uses configured API key (`app/ai/client.py`, `app/deps.py`)

- `make_client(config)` → `openai.OpenAI(base_url=config.llm_endpoint,
  api_key=config.llm_api_key)`.
- `get_ai_client()` in `deps.py` mirrors that (builds from `load_config()`).

Tests: assert `openai.OpenAI` is constructed with the resolved key — use
`monkeypatch`/`respx`? The client constructor is pure; test via
`make_client(Config(...))` asserting `client.api_key`. No new route tests here.

## Task 4 — Config writer (`app/config.py` or `app/config_write.py`)

`write_config(path, *, provider, api_key, model, endpoint)`:

- Read existing file (if any) to preserve `[database].path` and
  `[browser].profile_dir`; defaults `"job-seek.db"` / `"job-seek"`.
- Emit `[llm]` with provider, api_key (only if non-empty), model (only if
  non-empty), endpoint (only if provider == "custom"). Use `json.dumps` for
  string escaping.
- Returns nothing; raises on write failure.

Tests in `test_config.py` (or new `test_config_write.py`): round-trips through
`load_config()`; preserves database/browser; omits empty api_key.

## Task 5 — Validation helper

`validate(llm: dict) -> str | None` returning the first problem, or None:

- unknown provider,
- hosted provider with empty api_key → "An API key is required for {provider}.",
- custom with empty endpoint → "An endpoint URL is required for a custom provider.",
- custom with empty model allowed.

Reused by `POST /setup` and `POST /setup/test`. Tests: error strings for the
meaningful branches.

## Task 6 — Setup routes (`app/routes/setup.py`, registered in `app/main.py`)

- `GET /setup` → form, pre-filled from current config (provider, model, and
  endpoint for custom; api_key left empty).
- `POST /setup` → validate; on error re-render form with error; on success
  `write_config(...)` then redirect to `/setup` with a success message (query
  param or flash-style via `?saved=1`).
- `POST /setup/test` → validate; then call a minimal chat completion
  (`max_tokens=1`) through a freshly built client against the resolved
  endpoint/model/key. Return an htmx partial: "Connection OK — <model> replied"
  or the exception message.

Tests in `tests/test_routes_setup.py` using the existing `client` fixture:
GET renders; POST writes config + redirect; POST invalid (hosted no key)
re-renders with error and does not write; /setup/test success and failure
(monkeypatch the OpenAI client call to avoid real network — patch
`app.routes.setup._ping` or the client import).

## Task 7 — Template + banner + nav

- `app/templates/setup/index.html` — form, select with optgroups, model input
  pre-filled via JS when provider changes (small inline script), endpoint row
  shown only for custom, api key password input, Test button (hx-post to
  /setup/test, swap target under form), Save button (normal POST).
- `base.html` — "Setup" nav link.
- `home/index.html` — banner text: replace "edit config.toml by hand" with a
  link to `/setup`.

Update `tests/test_routes_home.py` expectations if the banner strings change.
Verify manually in the dev server (Task 9).

## Task 8 — Docs: templates, README

- `config-template.toml` and `config-container-template.toml` → new `[llm]`
  shape, `provider = "llamacpp"`, `model = "gemma-4-26b"`, no api key.
- README "Setup" section: mention visiting `/setup` as the one-step provider
  configuration.

No tests for this task; it is docs. Commit separately.

## Task 9 — Manual verification (run-dev-server)

Copy `config.toml` + `job-seek.db` into the worktree, start the dev server on
port 8931. Exercise:

- `/` shows no config banner once configured; banner elsewhere points to /setup.
- `/setup` renders; switching provider pre-fills model; custom reveals endpoint.
- Save a custom/local endpoint; confirm `config.toml` written with
  database/browser preserved.
- Test connection against the actual local llm if reachable (else verify the
  failure path renders a readable message).
- Watch `/jobs` still works with the new config.

Leave the dev server running and hand the URL to the user.

## Notes

- `openai.OpenAI` api_key must be a non-empty string; `"not-needed"` fallback
  everywhere (never empty).
- Do not add any new dependency (hand-rolled TOML writer with `json.dumps`
  escaping).
- Container symlink (`config.toml` → `data/config.toml`): writing to the
  resolved path is fine; do not replace-with-rename in the writer (it must
  follow the symlink), so write bytes directly to the open file.