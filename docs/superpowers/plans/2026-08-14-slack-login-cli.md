# Slack Login CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the Slack browser-login step out of the (headless-deployed) web app process into a standalone CLI tool that a user runs on a machine with a real browser, delivering the captured session cookie to the running app over HTTP.

**Architecture:** A new `app/cli/slack_login.py` module wraps the existing, unmodified `SlackCookieLogin` class: print a warning, require a typed `yes` confirmation, drive the login, then `httpx.post` the resulting cookie to a caller-supplied URL (the app's existing `/sources/{id}/cookie` route). The in-app "Log in via browser" button and its streaming route are deleted; the sources page instead renders the exact command to run.

**Tech Stack:** Python 3.12+, `httpx` (already a dependency), `argparse` (stdlib), `respx` (test HTTP mocking), `pytest`, FastAPI/Jinja2 for the template side.

## Global Constraints

- **Commit after each task**, each ending green with its own commit.
- **Run tests with the worktree venv:** `./projects/job-seek/.venv/bin/python -m pytest`.
- **No changes to `SlackCookieLogin`'s browser-driving internals** (`app/fetchers/slack_login.py`) — the CLI only wraps it.
- **No changes to the manual cookie-paste flow** (form + acknowledgment checkbox in `_row.html`) beyond the DevTools instruction wording change requested below.
- **No new `pyproject.toml` console-script entry** — invocation is `python -m app.cli.slack_login`.
- Confirmation gate: any input other than exactly `yes` (case-insensitive, whitespace-stripped) aborts with exit code 1, before any browser or network activity.

---

## File Structure

- `app/cli/__init__.py` — new, empty (makes `app/cli` a package).
- `app/cli/slack_login.py` — new. CLI entrypoint: argument parsing, warning + confirmation, drives `SlackCookieLogin.login()`, delivers the cookie via `httpx.post`, manages the temp-vs-persistent profile directory.
- `tests/test_cli_slack_login.py` — new. Tests the CLI module in isolation, mocking `SlackCookieLogin.login` and the outbound POST.
- `app/routes/sources.py` — modified. Remove the `/sources/{id}/login` route and the now-unused `SlackCookieLogin`/`StreamingResponse` imports.
- `app/templates/sources/_row.html` — modified. Replace the "Log in via browser" button with a rendered CLI command; reorder the disclosure block so the command appears before the manual paste fallback; mention the `xoxd-` prefix in the DevTools instructions.
- `tests/test_routes_sources.py` — modified. Remove the `test_login_route_*` tests; add a test asserting the sources page renders the exact CLI command.

---

## Task 1: Standalone Slack login CLI tool

**Files:**
- Create: `app/cli/__init__.py`
- Create: `app/cli/slack_login.py`
- Test: `tests/test_cli_slack_login.py`

**Interfaces:**
- Consumes: `SlackCookieLogin(source: dict, profile_dir: str)` with `.login() -> Generator[str, None, str | None]` (from `app.fetchers.slack_login`, unchanged); `_resolve_target(source_url: str) -> tuple[str, str, str]` (from `app.fetchers.slack`, unchanged, returns `(target_url, workspace, channel_id)`).
- Produces: `main(argv: list[str] | None = None) -> int` in `app.cli.slack_login` — the sole public entrypoint, returning a process exit code. `python -m app.cli.slack_login <slack_url> <cookie_post_url> [--reuse-login-profile]` on the command line.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cli_slack_login.py`:

```python
import os
from unittest.mock import patch
import httpx
import respx
from app.cli.slack_login import main
from app.fetchers.slack_login import SlackCookieLogin

_SLACK_URL = "https://example-workspace.slack.com/archives/C0EXAMPLE1"
_COOKIE_POST_URL = "http://testserver/sources/3/cookie"


def _fake_login_success(self):
    yield "Login successful; captured session cookie."
    return "xoxd-captured"


def _fake_login_failure(self):
    yield "Timed out waiting for login to example-workspace"
    return None


@respx.mock
def test_confirmed_login_posts_cookie_and_exits_zero(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": "yes")
    route = respx.post(_COOKIE_POST_URL).mock(return_value=httpx.Response(200))

    with patch.object(SlackCookieLogin, "login", _fake_login_success):
        code = main([_SLACK_URL, _COOKIE_POST_URL])

    assert code == 0
    assert route.calls.call_count == 1
    sent = route.calls.last.request.content
    assert b"d_cookie=xoxd-captured" in sent
    assert b"acknowledged=on" in sent


def test_declined_confirmation_aborts_without_login_or_post(monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda prompt="": "no")

    with patch.object(SlackCookieLogin, "login") as mock_login:
        code = main([_SLACK_URL, _COOKIE_POST_URL])

    assert code == 1
    mock_login.assert_not_called()
    assert "Aborted" in capsys.readouterr().out


def test_warning_mentions_the_cookie_post_url_host(monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda prompt="": "no")

    main([_SLACK_URL, _COOKIE_POST_URL])

    assert "testserver" in capsys.readouterr().out


@respx.mock
def test_failed_login_exits_nonzero_without_posting(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": "yes")
    route = respx.post(_COOKIE_POST_URL).mock(return_value=httpx.Response(200))

    with patch.object(SlackCookieLogin, "login", _fake_login_failure):
        code = main([_SLACK_URL, _COOKIE_POST_URL])

    assert code == 1
    assert route.calls.call_count == 0


@respx.mock
def test_server_rejection_exits_nonzero(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": "yes")
    respx.post(_COOKIE_POST_URL).mock(return_value=httpx.Response(400, text="nope"))

    with patch.object(SlackCookieLogin, "login", _fake_login_success):
        code = main([_SLACK_URL, _COOKIE_POST_URL])

    assert code == 1


@respx.mock
def test_connection_error_when_posting_exits_nonzero(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": "yes")
    respx.post(_COOKIE_POST_URL).mock(side_effect=httpx.ConnectError("refused"))

    with patch.object(SlackCookieLogin, "login", _fake_login_success):
        code = main([_SLACK_URL, _COOKIE_POST_URL])

    assert code == 1


@respx.mock
def test_default_profile_dir_is_temporary_and_cleaned_up(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": "yes")
    respx.post(_COOKIE_POST_URL).mock(return_value=httpx.Response(200))
    captured = {}

    def fake_init(self, source, profile_dir):
        captured["path"] = profile_dir

    with patch.object(SlackCookieLogin, "__init__", fake_init), \
         patch.object(SlackCookieLogin, "login", _fake_login_success):
        main([_SLACK_URL, _COOKIE_POST_URL])

    assert ".cache" not in captured["path"]
    assert not os.path.exists(captured["path"])  # cleaned up after the run


@respx.mock
def test_reuse_login_profile_uses_persistent_cache_path(monkeypatch, tmp_path):
    monkeypatch.setattr("builtins.input", lambda prompt="": "yes")
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    respx.post(_COOKIE_POST_URL).mock(return_value=httpx.Response(200))
    captured = {}

    def fake_init(self, source, profile_dir):
        captured["path"] = profile_dir

    with patch.object(SlackCookieLogin, "__init__", fake_init), \
         patch.object(SlackCookieLogin, "login", _fake_login_success):
        main([_SLACK_URL, _COOKIE_POST_URL, "--reuse-login-profile"])

    expected = tmp_path / ".cache" / "job-seek" / "slack-login" / "example-workspace"
    assert captured["path"] == str(expected)
    assert expected.exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./projects/job-seek/.venv/bin/python -m pytest tests/test_cli_slack_login.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.cli'`.

- [ ] **Step 3: Create the package and implement the CLI module**

Create `app/cli/__init__.py` (empty file).

Create `app/cli/slack_login.py`:

```python
from __future__ import annotations
import argparse
import sys
import tempfile
from pathlib import Path
import httpx
from app.fetchers.slack import _resolve_target
from app.fetchers.slack_login import SlackCookieLogin

_WARNING = (
    "Heads up: this cookie is your whole Slack login. Once captured, it will\n"
    "be sent to {host} and stored there — with it, that app can read and\n"
    "post as you in every Slack workspace you're signed into, not just this\n"
    "one.\n"
)


def _confirm(host: str) -> bool:
    print(_WARNING.format(host=host))
    answer = input("Type 'yes' to continue: ")
    return answer.strip().lower() == "yes"


def _profile_dir(workspace: str, reuse: bool) -> tuple[str, tempfile.TemporaryDirectory | None]:
    if reuse:
        path = Path.home() / ".cache" / "job-seek" / "slack-login" / workspace
        path.mkdir(parents=True, exist_ok=True)
        return str(path), None
    tmp = tempfile.TemporaryDirectory()
    return tmp.name, tmp


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli.slack_login",
        description=(
            "Log in to Slack in a real browser and send the session cookie to a "
            "running job-seek instance."
        ),
    )
    parser.add_argument("slack_url", help="The Slack channel URL shown on the source's sources page")
    parser.add_argument(
        "cookie_post_url", help="The job-seek /sources/{id}/cookie URL to send the captured cookie to"
    )
    parser.add_argument(
        "--reuse-login-profile",
        action="store_true",
        help=(
            "Keep the browser profile on disk between runs "
            "(~/.cache/job-seek/slack-login/<workspace>) so a later run may skip "
            "re-entering credentials if Slack still remembers the browser. Off by "
            "default to keep no state on disk after the tool exits -- opt in to "
            "evaluate whether the usability gain is worth it."
        ),
    )
    args = parser.parse_args(argv)

    host = httpx.URL(args.cookie_post_url).host
    if not _confirm(host):
        print("Aborted: confirmation not received.")
        return 1

    _, workspace, _ = _resolve_target(args.slack_url)
    profile_dir, tmp = _profile_dir(workspace, args.reuse_login_profile)
    try:
        source = {"name": workspace, "url": args.slack_url}
        login = SlackCookieLogin(source, profile_dir)
        cookie = None
        gen = login.login()
        try:
            while True:
                print(next(gen))
        except StopIteration as stop:
            cookie = stop.value
    finally:
        if tmp is not None:
            tmp.cleanup()

    if not cookie:
        print("Login did not produce a session cookie; nothing was sent.")
        return 1

    try:
        resp = httpx.post(args.cookie_post_url, data={"d_cookie": cookie, "acknowledged": "on"})
    except httpx.HTTPError as exc:
        print(f"Could not reach {args.cookie_post_url}: {exc}")
        return 1

    if 200 <= resp.status_code < 300:
        print(f"Cookie sent ({resp.status_code}).")
        return 0
    print(f"Server rejected the cookie ({resp.status_code}): {resp.text}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./projects/job-seek/.venv/bin/python -m pytest tests/test_cli_slack_login.py -v`
Expected: PASS (8 passed).

- [ ] **Step 5: Commit**

```bash
git add app/cli/__init__.py app/cli/slack_login.py tests/test_cli_slack_login.py
git commit -m "feat: standalone Slack login CLI (browser login off the web process)"
```

---

## Task 2: Replace the in-app login button with the CLI command; remove the route

**Files:**
- Modify: `app/routes/sources.py`
- Modify: `app/templates/sources/_row.html`
- Test: `tests/test_routes_sources.py`

**Interfaces:**
- Consumes: nothing from Task 1 at runtime (the rendered command is a plain string; it is not invoked from the web process).
- Produces: no new routes. `GET /sources` and `POST /sources/{id}/cookie` responses render the CLI command using `source.url` and `request.base_url`.

- [ ] **Step 1: Update the failing/changed tests**

In `tests/test_routes_sources.py`:

Remove the `SlackCookieLogin` import (no longer used):

```python
from unittest.mock import patch
from app.db import queries as q
from app.fetchers.slack import SlackFetcher
```

Delete these four tests entirely (the route they test no longer exists):
`test_login_route_streams_progress_and_persists_cookie`,
`test_login_route_reflects_failed_login`,
`test_login_route_404_for_missing_source`,
`test_login_route_rejects_non_slack_source`.

Add a new test after `test_slack_row_shows_cookie_security_warning`:

```python
def test_slack_row_shows_cli_login_command(client, conn):
    sid = q.insert_source(conn, "Example Slack", _SLACK_URL, "slack")
    with patch.object(SlackFetcher, "check_needs_login", return_value=True):
        resp = client.get("/sources")
    assert resp.status_code == 200
    assert "python -m app.cli.slack_login" in resp.text
    assert _SLACK_URL in resp.text
    assert f"http://testserver/sources/{sid}/cookie" in resp.text
    assert "Log in via browser" not in resp.text
```

- [ ] **Step 2: Run tests to verify the new/changed ones fail**

Run: `./projects/job-seek/.venv/bin/python -m pytest tests/test_routes_sources.py -v`
Expected: the four deleted tests are gone from collection; `test_slack_row_shows_cli_login_command` FAILS (`AssertionError: 'python -m app.cli.slack_login' not in ...`); other pre-existing tests continue to pass since nothing else changed yet.

- [ ] **Step 3: Remove the login route from `app/routes/sources.py`**

Change the import block at the top:

```python
from __future__ import annotations
import sqlite3
from typing import Optional
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from app.deps import get_db, get_config
from app.db import queries as q
from app.fetchers.slack import SlackFetcher
from app.pipeline import _make_fetcher
from app.template_env import templates
```

(`StreamingResponse` and `SlackCookieLogin` are dropped — `StreamingResponse` was only used by the route being deleted.)

Delete the entire `trigger_login` function (the `@router.post("/sources/{source_id}/login")` block, from its decorator through the final `return StreamingResponse(stream(), media_type="text/plain")` line) — nothing else in the file references it.

- [ ] **Step 4: Rewrite `app/templates/sources/_row.html`**

Overwrite the full file with:

```html
<tbody id="source-row-{{ source.id }}">
<tr style="border-bottom:1px solid #eee;">
  <td style="padding:0.5rem;">
    <strong>{{ source.name }}</strong><br>
    <small style="word-break:break-all;">{{ source.url }}</small>
    {% if source.fetcher_type == "slack" %}
      {% if needs_login %}
        <br><span style="color:#dc3545;">⚠ Needs Slack login before it can fetch.</span>
      {% elif needs_login is not none %}
        <br><span style="color:#198754;">✓ Slack session cookie set.</span>
      {% endif %}
    {% endif %}
  </td>
  <td style="padding:0.5rem;">{{ source.fetcher_type }}</td>
  <td style="padding:0.5rem;">
    {% if source.enabled %}enabled{% else %}<span style="color:#999;">disabled</span>{% endif %}
  </td>
  <td style="padding:0.5rem;">
    <button class="btn" style="font-size:0.85em; padding:3px 10px;"
      hx-get="/sources/{{ source.id }}/edit"
      hx-target="#source-row-{{ source.id }}"
      hx-swap="outerHTML">Edit</button>
  </td>
</tr>
{% if source.fetcher_type == "slack" %}
<tr style="border-bottom:1px solid #eee;">
  <td colspan="4" style="padding:0 0.5rem 0.75rem;">
    <details>
      <summary style="cursor:pointer; color:#495057; font-size:0.9em;">Slack session cookie</summary>
      <div style="margin-top:0.5rem; padding:0.75rem 1rem; background:#f8f9fa; border:1px solid #e9ecef; border-radius:6px;">
        <p style="margin:0 0 0.75rem; font-size:0.85em; color:#495057;">
          Heads up — this cookie is your whole Slack login, so with it the app can act as you in
          <strong>every Slack workspace you're signed into</strong>, not just this one. It's kept local to
          this app.
        </p>
        <p style="margin:0 0 0.3rem; font-size:0.85em; color:#495057;">
          Run this on a machine with a browser:
        </p>
        <pre style="margin:0 0 0.75rem; padding:0.5rem 0.75rem; background:#212529; color:#e9ecef; border-radius:6px; font-size:0.78em; overflow-x:auto; white-space:pre-wrap; word-break:break-all;"><code>uv run python -m app.cli.slack_login \
  "{{ source.url }}" \
  {{ request.base_url }}sources/{{ source.id }}/cookie</code></pre>
        <p style="margin:0 0 0.6rem; font-size:0.85em; color:#495057;">
          Or paste the <code>d</code> cookie directly: open Slack in your browser, then in DevTools →
          Cookies for <code>slack.com</code>, copy the value of the <code>d</code> cookie — it starts with
          <code>xoxd-</code>. (Chrome: Application tab · Firefox: Storage tab.)
        </p>
        <form hx-post="/sources/{{ source.id }}/cookie"
              hx-target="#source-row-{{ source.id }}" hx-swap="outerHTML"
              style="display:flex; flex-direction:column; gap:0.5rem; max-width:560px;">
          <input type="text" name="d_cookie" placeholder="Paste the `d` cookie value"
                 style="width:100%; box-sizing:border-box; padding:5px 8px;">
          <label style="font-size:0.85em; color:#495057;">
            <input type="checkbox" name="acknowledged" required>
            Yep, I understand — this covers every workspace I'm logged into.
          </label>
          <button type="submit" class="btn" style="font-size:0.85em; padding:3px 10px;">Save cookie</button>
        </form>
      </div>
    </details>
  </td>
</tr>
{% endif %}
</tbody>
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `./projects/job-seek/.venv/bin/python -m pytest tests/test_routes_sources.py -v`
Expected: PASS (all tests, including the new `test_slack_row_shows_cli_login_command`).

- [ ] **Step 6: Run the full suite**

Run: `./projects/job-seek/.venv/bin/python -m pytest -q`
Expected: PASS (whole suite green, no leftover references to the deleted route or `SlackCookieLogin` import in `sources.py`).

- [ ] **Step 7: Commit**

```bash
git add app/routes/sources.py app/templates/sources/_row.html tests/test_routes_sources.py
git commit -m "feat: replace in-app Slack login button with CLI command"
```

---

## Self-Review notes

- **Spec coverage:** CLI module + confirmation gate + delivery (Task 1); browser profile temp-vs-persistent with `--reuse-login-profile` (Task 1); route/button removal, command rendering via `request.base_url`, paste-form untouched aside from the `xoxd-` wording addition (Task 2); testing for both (Task 1 and Task 2 test steps). All spec sections map to a task.
- **Placeholders:** none — every step has complete code.
- **Type consistency:** `main(argv: list[str] | None = None) -> int`, `_profile_dir(workspace: str, reuse: bool) -> tuple[str, tempfile.TemporaryDirectory | None]`, and `SlackCookieLogin(source: dict, profile_dir: str)` are used consistently between the implementation and the tests that patch `__init__`/`login`.
- **Verified against the running environment:** `request.base_url` under `TestClient` was confirmed empirically to render as `"http://testserver/"` (trailing slash), so the template's `{{ request.base_url }}sources/{{ source.id }}/cookie` concatenation and the test's `f"http://testserver/sources/{sid}/cookie"` assertion are both correct as written, not assumed.
