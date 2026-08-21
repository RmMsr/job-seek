# LLM Provider Setup Page — Design

Date: 2026-08-20, revised 2026-08-21
Status: Approved (revision: model list is fetched live from `/v1/models`
instead of a hardcoded per-provider default, so a new model release never
needs a code change or release; `llamacpp` as example provider, no default
model)

## Problem

Users currently configure the inference provider by hand-editing
`config.toml`'s `[llm]` section with a raw OpenAI-compatible base URL:

```toml
[llm]
endpoint = "http://localhost:11434/v1"
model = "llama3.2"
```

This forces everyone to know the exact base URL of their provider and to
distinguish hosted providers (which need an API key) from local ones. There is
no built-in way to pick "the deepseek API" or "my local llamacpp" without
looking up URLs.

## Goal

Let the user configure the LLM provider through a web setup page:

- Pick one of the common (known) providers by name — base URL is derived, an
  API key is required, model is picked from a live-fetched list or typed by
  hand.
- Or pick a local provider / custom endpoint — model is optional, API key
  optional.
- Test the connection before committing, then write the config for them,
  preserving `[database]` and `[browser]` settings.

No provider's model catalog is hardcoded anywhere: the setup page fetches the
current list from the provider's own `GET /v1/models` (which every provider in
the table below implements, being OpenAI-compatible) when the user asks for
it. A new model release on any provider needs zero code changes here.

## Config schema change (`[llm]`)

New keys, all optional except that a known provider needs an API key:

```toml
[llm]
provider = "openai"           # known provider, local, or "custom"
api_key = "sk-..."            # required for hosted providers; optional for local
model = "gpt-4o-mini"         # required for hosted providers; optional for local/custom
#endpoint = "..."             # only read when provider = "custom"
```

- **Endpoint resolution:** if `provider` is a known preset → preset base URL.
  If `provider = "custom"` → `llm.endpoint`. Legacy config (`endpoint`
  present, no `provider`) keeps working: endpoint is used as-is.
- **API key resolution:** `llm.api_key` if set, else `"not-needed"` (the
  current value, which every OpenAI-compatible local server ignores). Empty
  string is never used because the OpenAI SDK rejects it; hosted providers are
  validated to have a key at save time anyway.
- **Model resolution:** `llm.model` verbatim, no per-provider fallback. A
  local/custom provider with no configured model is allowed to be empty →
  model is empty at runtime (the user sees an LLM error they can fix on
  `/setup`). A hosted provider with no configured model is rejected at save
  time (see Validation rules) since there is no default left to fall back to.

No API key is ever written unless the user typed one.

## Provider presets (`app/providers.py`)

Catalog of OpenAI-compatible base URLs only — no model names. Hosted
providers require an API key; local ones do not. This table is small and
stable (base URLs almost never change); it is not the part that needed fixing.

| key | base URL | group |
|---|---|---|
| `openai` | `https://api.openai.com/v1` | hosted |
| `openrouter` | `https://openrouter.ai/api/v1` | hosted |
| `groq` | `https://api.groq.com/openai/v1` | hosted |
| `gemini` | `https://generativelanguage.googleapis.com/v1beta/openai/` | hosted |
| `deepseek` | `https://api.deepseek.com/v1` | hosted |
| `mistral` | `https://api.mistral.ai/v1` | hosted |
| `xai` | `https://api.x.ai/v1` | hosted |
| `ollama` | `http://localhost:11434/v1` | local |
| `llamacpp` | `http://localhost:8080/v1` | local |
| `lmstudio` | `http://localhost:1234/v1` | local |
| `vllm` | `http://localhost:8000/v1` | local |
| `custom` | (user-supplied) | custom |

`ProviderPreset` drops `default_model` (it previously existed alongside
`endpoint`, `requires_api_key`, `group`); `resolve_llm()` no longer falls back
to it.

`config-template.toml` and `config-container-template.toml` are re-expressed in
the new shape. The example uses the local `llamacpp` provider, model loaded
via the setup page, no api key:

```toml
[llm]
provider = "llamacpp"
model = "gemma-4-26b"
```

## /setup page

New router `app/routes/setup.py`, template `app/templates/setup/index.html`.

- `GET /setup` — renders the form, pre-filled from the current `config.toml`
  (if readable). Provider dropdown with `optgroup`s: Hosted, Local, Custom.
  When "custom" is selected, an endpoint field is shown. Model field is a
  plain text input (`<input list="model-options">`), pre-filled with the
  configured model if any, otherwise empty — no per-provider default is
  filled in. API key field is empty (password input).
- `POST /setup` — validates, writes `config.toml`, redirects to `/setup` with
  a success notice. Htmx is not needed for the submit; a normal POST + redirect
  is fine and robust.
- `POST /setup/test` — validates the form the same way, then performs a
  minimal `chat.completions` call (`max_tokens=1`, "ping") against the
  resolved endpoint/model/key. Returns an htmx partial ("Connection OK" /
  error message) swapped under the form. No state is written.
- `POST /setup/models` — a "Load models" button next to the model field
  triggers this via htmx (explicit action, never automatic). Runs the same
  fetch-time validation as `/setup/test` (endpoint/key only — no model check,
  since fetching *is* how the user finds a model), then calls
  `client.models.list()` against the resolved endpoint/key. On success,
  returns a `<datalist id="model-options">` partial swapped into the page, so
  the model input's autocomplete offers the live list while staying free-text
  (the user can always type a model the list doesn't have — needed for
  providers whose model catalog is huge, e.g. OpenRouter, or whose
  `/v1/models` is stale or unsupported). On failure (network error, auth
  error, or an endpoint that doesn't implement `/v1/models`), returns the same
  inline-error partial pattern as `/setup/test`; the model input is untouched
  and still fully usable by hand. No state is written.

Validation rules:

- provider must be a known key.
- hosted provider + empty api_key → error ("an API key is required for …");
  applies to `/setup`, `/setup/test`, and `/setup/models`.
- `custom` + empty endpoint → error; applies to all three routes.
- hosted provider + empty model → error ("a model is required for …"); applies
  to `/setup` (save) only — `/setup/test` and `/setup/models` don't need a
  model yet.
- local/custom: model may be empty at every route.

### Writing config.toml

The writer merges with any existing file so `[database]` and `[browser]` are
preserved (and defaults `job-seek.db` / `job-seek` used when absent). Only the
`[llm]` section is rewritten. String values are escaped with `json.dumps`
(JSON basic-string syntax is valid TOML for our values). No new dependency.

The file written to is the same path `load_config()` reads (`config.toml` in
cwd), so local runs and the container (where `config.toml` is a symlink to
`data/config.toml`) both work unchanged. In the container the write goes
through the symlink to the volume-mounted file.

## Wiring

- `app/config.py` — `Config` gains `llm_api_key: str`; `load_config()`
  resolves endpoint/model/api key via `app.providers`. `ConfigStatus` gained
  nothing: `.ok` still means config exists and an LLM endpoint+model resolved.
- `app/ai/client.py` and `app/deps.py:get_ai_client()` — build the OpenAI
  client with `config.llm_api_key` instead of the hardcoded `"not-needed"`.
- `app/main.py` — include `setup.router`.
- `base.html` — add a Setup nav link (always visible).
- `home/index.html` — the config banner points at `/setup` instead of
  "edit config.toml by hand".

## Out of scope

- CLI wizard (user chose web page).
- Managing `database`/`browser` settings on the setup page (preserved, not edited).
- Provider credentials stored anywhere but `config.toml`.

## Tests

- `test_config.py` — resolution tests: known provider → endpoint, model passed
  through verbatim (no default-model fallback); custom provider →
  `llm.endpoint`; legacy endpoint-only file → unchanged behavior; api_key
  pass-through and the `"not-needed"` fallback; missing config still yields
  `ConfigStatus` not-ok.
- `test_routes_setup.py` — `GET /setup` renders (no config → form + banner;
  with config → pre-filled, no default model injected when config has none);
  `POST /setup` writes a config that `load_config()` then reads back correctly
  and preserves `[database]` / `[browser]`; hosted provider without key →
  inline error, nothing written; hosted provider with key but no model →
  inline error, nothing written; `POST /setup/test` success/failure partials
  (client/make_client mocked); `POST /setup/models` success renders a
  `<datalist>` from a mocked `client.models.list()`, failure (auth error /
  exception) renders the inline-error partial, hosted-without-key short
  circuits before any client call.