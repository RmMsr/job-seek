# Slack login as a standalone CLI tool — design

**Date:** 2026-08-14
**Status:** Approved

## Problem

The Slack cookie-only fetch refactor (see `2026-08-11-slack-cookie-only-fetch-design.md`)
removed Chromium from the Slack *fetch* path, but left an in-app "Log in via
browser" button (`POST /sources/{id}/login`) that still drives a headful
Playwright browser **inside the web app process**. That only works if the
process running the FastAPI app has a display attached — true for local dev,
false for the main deployment target: a headless container. The button is
currently dead weight there.

## Goals

- Move the browser-driven Slack login out of the web app process entirely,
  into a standalone CLI tool a user runs on a machine that has a real browser
  (their laptop), independent of where the containerized app itself runs.
- Deliver the captured `d` cookie to the (possibly remote) running app over
  HTTP, reusing the existing acknowledgment-gated `/sources/{id}/cookie` route
  — no new server-side endpoint, no direct database/filesystem coupling
  between the CLI machine and the app's storage.
- Replace the "Log in via browser" button with the literal command to run,
  so a headless deployment's sources page is still fully actionable.
- Keep the CLI's default footprint minimal (no persisted state after the
  process exits) while allowing an opt-in, explicitly-flagged path to trade
  footprint for a usability gain that is genuinely uncertain (see below) and
  meant to be evaluated with real use before deciding whether to keep it.

## Non-goals

- No changes to the manual cookie-paste flow (`_row.html`'s paste form +
  acknowledgment checkbox) — it is a separate, already-working path and stays
  untouched.
- No new console-script entry point in `pyproject.toml`. Invocation is via
  `python -m app.cli.slack_login`, consistent with the project's existing
  "run everything explicitly" style (e.g. `uvicorn app.main:app`).
- No change to `SlackFetcher`, `SlackCookieLogin`'s browser-driving internals,
  or the fetch path — this is purely about *where* the login step runs and
  how its result gets delivered.

## Architecture

### CLI module

New file `app/cli/slack_login.py` (new `app/cli/` package, with an empty
`__init__.py`). Invocation:

```
uv run python -m app.cli.slack_login "<slack_url>" <cookie_post_url> [--reuse-login-profile]
```

- `slack_url` — the Slack URL as already stored on the source (an
  `/archives/<channel>` or `/messages/<channel>` link); resolved with the
  existing `_resolve_target` helper from `app.fetchers.slack`, same as
  `SlackFetcher` and `SlackCookieLogin` already do.
- `cookie_post_url` — the full URL to `POST` the captured cookie to, e.g.
  `http://job-seek.local:8000/sources/3/cookie`. The sources page renders this
  value directly into the copyable command (see UI section) so the user never
  types it by hand.
- `--reuse-login-profile` — optional flag, off by default (see "Browser
  profile" below).

The module is a thin wrapper around the existing, already-tested
`SlackCookieLogin` class (`app/fetchers/slack_login.py`, unchanged) — it does
not reimplement any browser-driving logic:

1. Print the warning and require typed confirmation (below).
2. Construct `SlackCookieLogin(source, profile_dir)` where `source` is a
   minimal `{"name": ..., "url": slack_url}` dict (the class only reads
   `source["url"]` and `source["name"]` — no DB row is needed) and
   `profile_dir` is either a fresh temp directory or the persistent path,
   depending on the flag.
3. Drive `login()`, printing each yielded progress line to stdout as today's
   streaming route does.
4. On success (a cookie is returned): `httpx.post(cookie_post_url,
   data={"d_cookie": cookie, "acknowledged": "on"})`. Print the resulting
   HTTP status and a short human message; exit 0 on a 2xx response, exit 1
   otherwise (printing the response body/status so the user can see why the
   server rejected it).
5. On failure (login returns `None` — timeout or no cookie found): print the
   failure reason, exit 1, and skip the HTTP call entirely.

The CLI does not parse the HTML fragment the `/cookie` route returns — it only
inspects the HTTP status, keeping it decoupled from that route's internal
response shape.

### Confirmation gate

Before touching the browser, the CLI prints:

```
Heads up: this cookie is your whole Slack login. Once captured, it will
be sent to <host from cookie_post_url> and stored there — with it, that
app can read and post as you in every Slack workspace you're signed
into, not just this one.

Type 'yes' to continue: 
```

Reads a line from stdin; anything other than exactly `yes` (case-insensitive,
surrounding whitespace stripped) prints an abort message and exits 1 —no
browser is opened, nothing is sent. This is the CLI's equivalent of the
paste-form's acknowledgment checkbox; the `acknowledged=on` field sent in the
later `POST` is backed by this real, typed confirmation.

### Browser profile

`SlackCookieLogin`'s existing two-phase logic (try a headless load first; only
open a **visible** window if that fails) is kept exactly as-is — the CLI does
not change that class. What the CLI controls is which directory `profile_dir`
points at:

- **Default (no flag):** a `tempfile.TemporaryDirectory()` created for the
  run and removed when the process exits (via context manager, so it's
  cleaned up even on error). Every run is headful in practice, since a
  brand-new profile has no prior session for the headless probe to find — but
  the probe still runs unconditionally; it's just a fast, harmless no-op in
  this mode. Nothing is left on disk afterward.
- **`--reuse-login-profile`:** a stable path,
  `~/.cache/job-seek/slack-login/<workspace-subdomain>/`, created if absent
  and left in place across runs. This is what makes the headless probe
  potentially succeed on a later run, *if* Slack or the workspace's SSO still
  recognizes the browser by then — genuinely uncertain (it depends on
  session-duration vs. remembered-device policies neither this tool nor the
  app control), which is why it's opt-in rather than the default.

The flag and the persistent-path code carry a comment marking them as
explicitly optional — kept for real-world evaluation of whether the
usability gain is worth the added footprint, and a candidate for deletion if
it turns out not to be.

### Sources UI

`app/routes/sources.py`:
- Remove `POST /sources/{id}/login` (`trigger_login`) entirely.
- Remove the `SlackCookieLogin` import — it is no longer used anywhere in the
  web app process. `app/fetchers/slack_login.py` itself is untouched; only its
  *caller* moves to the CLI.

`app/templates/sources/_row.html`: the "Log in via browser" button and its
`data-progress-url`/`data-progress-target` attributes are removed. In their
place, the disclosure block shows the literal command to copy-paste, built
from the source's stored URL and the current request's host:

```html
uv run python -m app.cli.slack_login \
  "{{ source.url }}" \
  {{ request.base_url }}sources/{{ source.id }}/cookie
```

Using `request.base_url` means the shown command is automatically correct
whether the app is reached at `localhost:8000` in dev or through whatever
host/port a container is exposed on — no separate "public URL" config needed.
This requires `sources_page`, `create_source`, `update_source`, and
`set_cookie` in `app/routes/sources.py` to pass `request` into the
`_row.html`/`index.html` render context wherever they don't already (some
already do, via `TemplateResponse(request, ...)`).

The generic `data-progress-url` JS in `app/templates/base.html` is shared with
several other features (fetch, jobs, scenarios) and is **not** touched — only
the Slack-specific button markup is removed.

The paste form directly below (with its own required acknowledgment
checkbox) is unchanged.

## Testing

- `tests/test_cli_slack_login.py` (new): warning + prompt is printed before
  any browser/network activity; a non-`yes` response aborts without invoking
  `SlackCookieLogin` or `httpx.post`; on a successful login the cookie is
  POSTed with `acknowledged=on` to the given URL and the process exits 0 on a
  2xx response; on a failed login (no cookie returned) the process exits 1 and
  no HTTP call is made; `--reuse-login-profile` results in the persistent
  `~/.cache/job-seek/slack-login/<workspace>` path being used as `profile_dir`
  where the default (no flag) uses a temporary directory that does not exist
  after the process exits.
- `tests/test_routes_sources.py`: remove the `test_login_route_*` tests (the
  route no longer exists); add a test asserting the sources page renders the
  exact CLI command (source URL + `request.base_url`-derived cookie endpoint)
  for a Slack source, and that the "Log in via browser" button/markup is gone.
- `tests/test_fetcher_slack_login.py`: unchanged — `SlackCookieLogin` itself
  is not modified.

## Migration

No database changes. Purely a code-organization and UI change; existing
`sources.d_cookie` values and the paste flow are unaffected.
