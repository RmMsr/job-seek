# Slack cookie-only fetch — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Slack fetcher's browser-based credential extraction with a pure-`httpx` path that derives the `xoxc` token from the messages-page HTML using a stored `d` cookie, and make the browser only an optional tool for minting that cookie.

**Architecture:** `SlackFetcher` stops inheriting `PlaywrightFetcher`; `fetch()` does an `httpx` GET of the messages URL with the stored `d` cookie, regexes the `api_token` out of the boot JSON, then runs the existing `conversations.history` pagination loop. The `d` cookie is stored per-source in a new `sources.d_cookie` column, set either by a paste field (gated by a required security acknowledgment) or by an optional Playwright login tool (`SlackCookieLogin`) that opens a headful browser and extracts the cookie.

**Tech Stack:** Python, FastAPI, Jinja2 templates + htmx, SQLite, `httpx`, `respx` (test HTTP mocking), `pytest`, Playwright (only for the optional login tool and unrelated fetchers).

## Global Constraints

- **No community data in code:** never hardcode real Slack workspace/team/channel IDs in source or tests. Tests use `example-workspace` / `C0EXAMPLE1` / `T0EXAMPLE1` and `xoxc-`/`xoxd-` fake values only. (See existing `tests/test_fetcher_slack.py`.)
- **Migration philosophy:** additive, hard, single-shape migrations. Use a plain `ALTER TABLE ... ADD COLUMN`; no backwards-compat shims or dual-schema handling.
- **Commit after each task** (each task ends green with its own commit).
- **Run tests with the worktree venv:** `./projects/job-seek/.venv/bin/python -m pytest`.
- **Security acknowledgment is mandatory:** storing a `d` cookie (paste route or browser-login route) MUST be rejected unless an explicit acknowledgment is present. Warning copy must convey: the `d` cookie is the user's whole-account Slack session, grants read/post as the user across **every** logged-in workspace (not just this channel), is stored locally in plain text, and is revoked by signing out of Slack.

---

## File Structure

- `app/db/schema.py` — add `d_cookie` to the `sources` DDL + a new additive migration, registered in `init_db`.
- `app/db/queries.py` — add `set_source_cookie(conn, source_id, cookie)`. (`SELECT *` already carries the new column through `get_source`/`get_sources`.)
- `app/fetchers/slack.py` — rewrite: drop `PlaywrightFetcher` base and browser credential extraction; add token-from-HTML extraction, an `httpx` `fetch()`, and a cookie-based `check_needs_login()`. Keep mrkdwn/timestamp/message-conversion/pagination logic.
- `app/fetchers/slack_login.py` — **new**: `SlackCookieLogin(PlaywrightFetcher)`, the optional browser tool that opens Slack headful and returns the extracted `d` cookie.
- `app/pipeline.py` — `_make_fetcher` builds `SlackFetcher(source, known_urls=...)` (no `profile_dir`).
- `app/routes/sources.py` — cookie-based `_check_needs_login`; new `POST /sources/{id}/cookie` (paste + acknowledgment gate); adapt `POST /sources/{id}/login` to drive `SlackCookieLogin` and persist the returned cookie.
- `app/templates/sources/_row.html` — reworded needs-login block offering **paste** and **browser login**; the paste form (its own `<form>`, slack-only) with warning copy + required "I understand" checkbox.
- Tests: `tests/test_fetcher_slack.py` (rewrite credential/extract tests), `tests/test_fetcher_slack_login.py` (**new**), `tests/test_routes_sources.py` (update login-route tests, add cookie-route tests).

---

## Task 1: Add `d_cookie` column, migration, and setter query

**Files:**
- Modify: `app/db/schema.py` (the `sources` block in `_DDL` ~line 10-16; add `_migrate_sources_add_d_cookie`; register in `init_db` ~line 84-97)
- Modify: `app/db/queries.py` (add `set_source_cookie` after `update_source`, ~line 66)
- Test: `tests/test_db_sources_cookie.py` (new)

**Interfaces:**
- Produces: `sources.d_cookie TEXT NOT NULL DEFAULT ''`; `set_source_cookie(conn: sqlite3.Connection, source_id: int, cookie: str) -> None`; `get_source(...)["d_cookie"]`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_db_sources_cookie.py`:

```python
import sqlite3
import pytest
from app.db.schema import init_db
from app.db import queries as q


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:", check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    init_db(c)
    yield c
    c.close()


def test_new_source_defaults_to_empty_cookie(conn):
    sid = q.insert_source(conn, "Example Slack", "https://example-workspace.slack.com/archives/C0EXAMPLE1", "slack")
    assert q.get_source(conn, sid)["d_cookie"] == ""


def test_set_source_cookie_persists_value(conn):
    sid = q.insert_source(conn, "Example Slack", "https://example-workspace.slack.com/archives/C0EXAMPLE1", "slack")
    q.set_source_cookie(conn, sid, "xoxd-fake-cookie")
    assert q.get_source(conn, sid)["d_cookie"] == "xoxd-fake-cookie"


def test_migration_adds_cookie_column_to_preexisting_sources_table(conn):
    # Simulate an older DB whose sources table predates d_cookie.
    conn.execute("DROP TABLE sources")
    conn.execute(
        "CREATE TABLE sources (id INTEGER PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL, "
        "fetcher_type TEXT NOT NULL CHECK(fetcher_type IN ('http','playwright','slack','finn_listing','manual')), "
        "enabled INTEGER NOT NULL DEFAULT 1)"
    )
    conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('Old', 'http://x', 'http')")
    conn.commit()
    init_db(conn)  # idempotent; must add the column
    cols = [r[1] for r in conn.execute("PRAGMA table_info(sources)").fetchall()]
    assert "d_cookie" in cols
    assert conn.execute("SELECT d_cookie FROM sources").fetchone()[0] == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./projects/job-seek/.venv/bin/python -m pytest tests/test_db_sources_cookie.py -v`
Expected: FAIL — `KeyError: 'd_cookie'` / `AttributeError: module 'app.db.queries' has no attribute 'set_source_cookie'`.

- [ ] **Step 3: Add the column to the DDL**

In `app/db/schema.py`, in `_DDL`, change the `sources` table to include the column:

```sql
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    fetcher_type TEXT NOT NULL CHECK(fetcher_type IN ('http', 'playwright', 'slack', 'finn_listing', 'manual')),
    enabled INTEGER NOT NULL DEFAULT 1,
    d_cookie TEXT NOT NULL DEFAULT ''
);
```

- [ ] **Step 4: Add the migration function**

In `app/db/schema.py`, near the other additive migrations (mirroring `_migrate_jobs_add_headline`), add:

```python
def _migrate_sources_add_d_cookie(conn: sqlite3.Connection) -> None:
    # Purely additive column (stores the Slack `d` session cookie per source),
    # so a plain ALTER TABLE suffices — no table rebuild.
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='sources'"
    ).fetchone()
    if row is None or "d_cookie" in row[0]:
        return
    conn.execute("ALTER TABLE sources ADD COLUMN d_cookie TEXT NOT NULL DEFAULT ''")
    conn.commit()
```

- [ ] **Step 5: Register the migration in `init_db`**

In `app/db/schema.py`, in `init_db`, add the call after the existing `sources` migrations (it must run after any table rebuild so the column survives):

```python
    _migrate_sources_fetcher_type(conn)
    _migrate_sources_fetcher_type_manual(conn)
    _migrate_sources_add_d_cookie(conn)
```

- [ ] **Step 6: Add the setter query**

In `app/db/queries.py`, after `update_source`, add:

```python
def set_source_cookie(conn: sqlite3.Connection, source_id: int, cookie: str) -> None:
    conn.execute("UPDATE sources SET d_cookie = ? WHERE id = ?", (cookie, source_id))
    conn.commit()
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `./projects/job-seek/.venv/bin/python -m pytest tests/test_db_sources_cookie.py -v`
Expected: PASS (3 passed).

- [ ] **Step 8: Commit**

```bash
git add app/db/schema.py app/db/queries.py tests/test_db_sources_cookie.py
git commit -m "feat: add sources.d_cookie column, migration, and setter"
```

---

## Task 2: Rewrite `SlackFetcher` to cookie-only httpx + wire pipeline

**Files:**
- Modify: `app/fetchers/slack.py` (full rewrite of the class; keep module helpers)
- Modify: `app/pipeline.py:32-40` (`_make_fetcher`)
- Test: `tests/test_fetcher_slack.py` (rewrite credential/extract sections)

**Interfaces:**
- Consumes: `sources.d_cookie` (Task 1); `_resolve_target`, `_clean_mrkdwn`, `_ts_to_iso` (unchanged module helpers).
- Produces:
  - Module fn `extract_token(html: str) -> str | None` (regex `"api_token":"(xoxc-[A-Za-z0-9-]+)"`).
  - `SlackFetcher(source: dict, known_urls: frozenset[str] = frozenset())` — no `profile_dir`.
  - `SlackFetcher.fetch() -> list[RawJob]`.
  - `SlackFetcher.check_needs_login() -> bool`.
  - `SlackFetcher._collect(token: str) -> list[RawJob]` (the old pagination loop, credential-free).
  - `SlackFetcher._message_to_job(message: dict) -> RawJob | None` (unchanged behavior).

- [ ] **Step 1: Rewrite the failing tests**

Replace the top of `tests/test_fetcher_slack.py` (imports through the "credential extraction" section) and adjust every `SlackFetcher(_SOURCE, "profile-dir")` construction. Concretely:

Change the import line:

```python
from app.fetchers.slack import SlackFetcher, extract_token, _clean_mrkdwn, _ts_to_iso
```

Add cookie + messages-URL constants and an HTML helper near the existing constants:

```python
_COOKIE = "xoxd-fake-cookie"
_MESSAGES_HTML = (
    '<!DOCTYPE html><html><head><script>var boot_data = {};'
    'boot_data.team_id = "T0EXAMPLE1";</script>'
    '<script>window.boot = {"team_id":"T0EXAMPLE1","api_token":"' + _TOKEN + '"};</script>'
    '</head><body>Redirecting…</body></html>'
)
_SIGNIN_HTML = '<!DOCTYPE html><html><body>Sign in to your workspace<input type="password"></body></html>'
```

Where `_SOURCE` is defined, add the cookie so fetch/needs-login tests have a session:

```python
_SOURCE = {
    "id": 1,
    "name": "Test Slack",
    "url": "https://example-workspace.slack.com/archives/C0EXAMPLE1",
    "fetcher_type": "slack",
    "d_cookie": _COOKIE,
}
```

Delete the entire `_mock_page`, `_local_config`, `_cookies` helpers and the whole "credential extraction" test block (`test_extract_credentials_*`) and the "login / URL handling" `test_needs_login_*` tests (those move to Task 3). Keep `test_target_url_*` and `test_constructing_fetcher_raises_for_unsupported_url_form`, updating their constructions to `SlackFetcher(source)` (drop `"profile-dir"`).

Add the new token-extraction and fetch tests:

```python
# -- token extraction --------------------------------------------------------

def test_extract_token_finds_api_token_in_boot_html():
    assert extract_token(_MESSAGES_HTML) == _TOKEN

def test_extract_token_returns_none_for_signin_page():
    assert extract_token(_SIGNIN_HTML) is None


# -- fetch (httpx, cookie-only) ----------------------------------------------

@respx.mock
def test_fetch_extracts_token_then_returns_messages():
    respx.get(_TARGET_URL).mock(return_value=httpx.Response(200, text=_MESSAGES_HTML))
    respx.post(_HISTORY_URL).mock(
        return_value=_history_response([_msg(_ts(1), "Recent post")])
    )
    fetcher = SlackFetcher(_SOURCE)
    jobs = fetcher.fetch()
    assert [_body(j) for j in jobs] == ["Recent post"]

@respx.mock
def test_fetch_raises_when_session_invalid():
    respx.get(_TARGET_URL).mock(return_value=httpx.Response(200, text=_SIGNIN_HTML))
    fetcher = SlackFetcher(_SOURCE)
    with pytest.raises(RuntimeError):
        fetcher.fetch()

@respx.mock
def test_fetch_sends_d_cookie_on_messages_request():
    route = respx.get(_TARGET_URL).mock(return_value=httpx.Response(200, text=_MESSAGES_HTML))
    respx.post(_HISTORY_URL).mock(return_value=_history_response([]))
    SlackFetcher(_SOURCE).fetch()
    assert route.calls.last.request.headers["cookie"].startswith("d=")


# -- check_needs_login -------------------------------------------------------

def test_check_needs_login_true_when_cookie_empty():
    fetcher = SlackFetcher({**_SOURCE, "d_cookie": ""})
    assert fetcher.check_needs_login() is True  # no HTTP call needed

@respx.mock
def test_check_needs_login_false_when_token_present():
    respx.get(_TARGET_URL).mock(return_value=httpx.Response(200, text=_MESSAGES_HTML))
    assert SlackFetcher(_SOURCE).check_needs_login() is False

@respx.mock
def test_check_needs_login_true_when_signin_page():
    respx.get(_TARGET_URL).mock(return_value=httpx.Response(200, text=_SIGNIN_HTML))
    assert SlackFetcher(_SOURCE).check_needs_login() is True
```

Finally, in the "extract / pagination" and "diagnostic logging" sections, replace every `page = _mock_page(...)` + `fetcher._extract(page)` with a direct `_collect` call. For each such test, delete the `page = _mock_page(...)` line and change `fetcher._extract(page)` to `fetcher._collect(_TOKEN)`. Constructions there drop `"profile-dir"`: `SlackFetcher(_SOURCE, known_urls=...)` stays valid (2nd positional is now `known_urls`); change `SlackFetcher(_SOURCE, "profile-dir")` → `SlackFetcher(_SOURCE)` and `SlackFetcher(_SOURCE, "profile-dir", known_urls=...)` → `SlackFetcher(_SOURCE, known_urls=...)`. The `_CappedFetcher(_SOURCE, "profile-dir")` constructions become `_CappedFetcher(_SOURCE)`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `./projects/job-seek/.venv/bin/python -m pytest tests/test_fetcher_slack.py -v`
Expected: FAIL — `ImportError: cannot import name 'extract_token'` and/or `TypeError`/`AttributeError` on `_collect`/`fetch`.

- [ ] **Step 3: Rewrite `app/fetchers/slack.py`**

Replace the imports, class base, and credential/extract methods. Keep `_URL_RE`, `_MRKDWN_*`, `MAX_AGE_DAYS`, `_SYSTEM_SUBTYPES`, `_resolve_target`, `_ts_to_iso`, `_clean_mrkdwn`, and `_message_to_job` exactly as they are. Apply these changes:

Replace the import block at the top:

```python
from __future__ import annotations
import json
import logging
import re
from datetime import datetime, timezone
import httpx
from app.fetchers.base import RawJob, is_recent

logger = logging.getLogger("job_seek")
```

(Drops the `PlaywrightFetcher` and `urlparse` imports — no longer used here. `json` stays only if still referenced; if unused after the rewrite, remove it.)

Add the token regex next to the other module regexes:

```python
_TOKEN_RE = re.compile(r'"api_token":"(xoxc-[A-Za-z0-9-]+)"')


def extract_token(html: str) -> str | None:
    match = _TOKEN_RE.search(html)
    return match.group(1) if match else None
```

Replace the class definition down through `_extract` with:

```python
class SlackFetcher:
    MAX_HISTORY_PAGES = 20
    PAGE_LIMIT = 100
    MAX_NEW_MESSAGES = 50  # cap on new postings collected per run, matches finn.py's MAX_DETAIL_FETCHES

    def __init__(self, source: dict, known_urls: frozenset[str] = frozenset()) -> None:
        self._source = source
        self._resolved_url, self._workspace, self._channel_id_value = _resolve_target(source["url"])
        self._known_urls = known_urls
        self._cookie = source.get("d_cookie", "") or ""

    def _target_url(self) -> str:
        return self._resolved_url

    def _channel_id(self) -> str:
        return self._channel_id_value

    def _fetch_messages_html(self) -> str:
        resp = httpx.get(
            self._target_url(),
            cookies={"d": self._cookie},
            headers={"User-Agent": "Mozilla/5.0"},
            follow_redirects=True,
            timeout=20,
        )
        resp.raise_for_status()
        return resp.text

    def check_needs_login(self) -> bool:
        if not self._cookie:
            return True
        try:
            html = self._fetch_messages_html()
        except Exception:
            return True
        return extract_token(html) is None

    def fetch(self) -> list[RawJob]:
        token = extract_token(self._fetch_messages_html())
        if token is None:
            raise RuntimeError(
                f"No valid Slack session for {self._source['name']!r}: the `d` cookie is missing or expired."
            )
        return self._collect(token)

    def _fetch_history_page(self, token: str, cursor: str | None) -> dict:
        data = {"token": token, "channel": self._channel_id(), "limit": str(self.PAGE_LIMIT)}
        if cursor:
            data["cursor"] = cursor
        resp = httpx.post(
            f"https://{self._workspace}.slack.com/api/conversations.history",
            data=data,
            cookies={"d": self._cookie},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        resp.raise_for_status()
        body = resp.json()
        if not body.get("ok"):
            raise RuntimeError(f"Slack API error: {body.get('error')}")
        return body
```

Keep `_message_to_job` exactly as it is. Then rename the existing `_extract(self, page)` to `_collect(self, token)` and remove its first two lines (`token, cookie = self._extract_credentials(page)`), and update the `_fetch_history_page(token, cookie, cursor)` call inside it to `_fetch_history_page(token, cursor)`. The rest of the loop body is unchanged. Delete the old `_extract_credentials`, `_needs_login`, and any `_launch`/login-related overrides (they no longer exist on this class).

- [ ] **Step 4: Update `_make_fetcher` in the pipeline**

In `app/pipeline.py`, change the slack branch (line ~36-37):

```python
    if ft == "slack":
        return SlackFetcher(source, known_urls=q.get_all_job_urls(conn))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `./projects/job-seek/.venv/bin/python -m pytest tests/test_fetcher_slack.py -v`
Expected: PASS (all rewritten + kept tests green).

- [ ] **Step 6: Commit**

```bash
git add app/fetchers/slack.py app/pipeline.py tests/test_fetcher_slack.py
git commit -m "feat: cookie-only httpx SlackFetcher (token from messages-page HTML)"
```

---

## Task 3: `SlackCookieLogin` — optional browser tool to mint the cookie

**Files:**
- Create: `app/fetchers/slack_login.py`
- Test: `tests/test_fetcher_slack_login.py` (new)

**Interfaces:**
- Consumes: `PlaywrightFetcher` (`_launch`, `_wait_for_login_with_progress`, `_profile_path`); `_resolve_target` from `app.fetchers.slack`.
- Produces: `SlackCookieLogin(source: dict, profile_dir: str)` with `login() -> Generator[str, None, str | None]` — yields progress strings and returns the extracted `d` cookie value, or `None` on failure/timeout.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_fetcher_slack_login.py`:

```python
from contextlib import contextmanager
from unittest.mock import MagicMock
import pytest
from app.fetchers.slack_login import SlackCookieLogin

_SOURCE = {
    "id": 1,
    "name": "Example Slack",
    "url": "https://example-workspace.slack.com/archives/C0EXAMPLE1",
    "fetcher_type": "slack",
}
_TARGET_URL = "https://example-workspace.slack.com/messages/C0EXAMPLE1"


def test_needs_login_false_when_on_channel_page():
    page = MagicMock(url=_TARGET_URL)
    assert SlackCookieLogin(_SOURCE, "profile-dir")._needs_login(page) is False


def test_needs_login_true_when_redirected_to_signin():
    page = MagicMock(url="https://app.slack.com/workspace-signin?redir=%2Fgantry%2Fauth")
    assert SlackCookieLogin(_SOURCE, "profile-dir")._needs_login(page) is True


def test_target_url_normalizes_archives_link_to_messages_link():
    assert SlackCookieLogin(_SOURCE, "profile-dir")._target_url() == _TARGET_URL


def _drive(gen):
    msgs = []
    try:
        while True:
            msgs.append(next(gen))
    except StopIteration as stop:
        return msgs, stop.value


def test_login_returns_d_cookie_when_already_logged_in(monkeypatch):
    login = SlackCookieLogin(_SOURCE, "profile-dir")
    fake_ctx = MagicMock()
    fake_ctx.new_page.return_value = MagicMock(url=_TARGET_URL)  # channel in path => logged in
    fake_ctx.cookies.return_value = [{"name": "d", "value": "xoxd-captured"}]
    monkeypatch.setattr(login, "_launch", lambda pw, *, headless: fake_ctx)

    @contextmanager
    def fake_pw():
        yield MagicMock()

    monkeypatch.setattr("app.fetchers.slack_login.sync_playwright", fake_pw)

    msgs, cookie = _drive(login.login())
    assert cookie == "xoxd-captured"
    assert any("successful" in m.lower() or "captured" in m.lower() for m in msgs)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./projects/job-seek/.venv/bin/python -m pytest tests/test_fetcher_slack_login.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.fetchers.slack_login'`.

- [ ] **Step 3: Implement `app/fetchers/slack_login.py`**

```python
from __future__ import annotations
import logging
from typing import Generator
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright
from app.fetchers.playwright_base import PlaywrightFetcher
from app.fetchers.slack import _resolve_target

logger = logging.getLogger("job_seek")


class SlackCookieLogin(PlaywrightFetcher):
    """Optional, display-dependent tool: opens Slack in a headful browser so the
    user can log in, then extracts the `d` session cookie. Not used on the fetch
    path — only to mint the cookie SlackFetcher later consumes."""

    def __init__(self, source: dict, profile_dir: str) -> None:
        super().__init__(source, profile_dir)
        self._resolved_url, self._workspace, self._channel_id_value = _resolve_target(source["url"])

    def _target_url(self) -> str:
        return self._resolved_url

    def _channel_id(self) -> str:
        return self._channel_id_value

    def _needs_login(self, page) -> bool:
        return self._channel_id() not in urlparse(page.url).path

    def login(self) -> Generator[str, None, str | None]:
        with sync_playwright() as pw:
            cookie = yield from self._login_and_extract(pw)
            return cookie

    def _login_and_extract(self, playwright) -> Generator[str, None, str | None]:
        ctx = self._launch(playwright, headless=True)
        try:
            page = ctx.new_page()
            page.goto(self._target_url(), wait_until="domcontentloaded", timeout=30000)
            if self._needs_login(page):
                ctx.close()
                ctx = self._launch(playwright, headless=False)
                page = ctx.new_page()
                page.goto(self._target_url(), wait_until="domcontentloaded", timeout=30000)
                yield f"Log in to {self._source['name']} in the browser window that just opened..."
                try:
                    yield from self._wait_for_login_with_progress(page)
                except RuntimeError as exc:
                    yield str(exc)
                    return None
            cookie = next((c["value"] for c in ctx.cookies() if c["name"] == "d"), None)
            if cookie:
                yield "Login successful; captured session cookie."
            else:
                yield "Login finished but no `d` session cookie was found."
            return cookie
        finally:
            ctx.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./projects/job-seek/.venv/bin/python -m pytest tests/test_fetcher_slack_login.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add app/fetchers/slack_login.py tests/test_fetcher_slack_login.py
git commit -m "feat: SlackCookieLogin browser tool that extracts the d cookie"
```

---

## Task 4: Routes + templates — paste field (acknowledgment-gated), browser-login adaptation, cookie-based needs-login

**Files:**
- Modify: `app/routes/sources.py` (`_check_needs_login`; new `set_cookie`; rewrite `trigger_login`)
- Modify: `app/templates/sources/_row.html` (needs-login block + paste form + warning + acknowledgment checkbox)
- Test: `tests/test_routes_sources.py` (update login-route tests; add cookie-route + warning tests)

**Interfaces:**
- Consumes: `q.set_source_cookie` (Task 1); `SlackFetcher.check_needs_login` (Task 2); `SlackCookieLogin.login` (Task 3).
- Produces: `POST /sources/{id}/cookie` (form: `d_cookie`, `acknowledged`) → re-rendered `_row.html`; `POST /sources/{id}/login` now persists the cookie the login tool returns.

- [ ] **Step 1: Write/adjust the failing tests**

In `tests/test_routes_sources.py`, update the imports:

```python
from app.fetchers.slack import SlackFetcher
from app.fetchers.slack_login import SlackCookieLogin
```

Rewrite the two `login`-route tests to patch `SlackCookieLogin.login` (returning a cookie / `None`) and assert persistence, and rewrite the "rejects non-playwright" test to expect a slack-only guard:

```python
def test_login_route_streams_progress_and_persists_cookie(client, conn):
    sid = q.insert_source(conn, "Example Slack", _SLACK_URL, "slack")

    def fake_login(self):
        yield "Login successful; captured session cookie."
        return "xoxd-captured"

    with patch.object(SlackCookieLogin, "login", fake_login):
        resp = client.post(f"/sources/{sid}/login")

    assert resp.status_code == 200
    assert "captured session cookie" in resp.text
    assert q.get_source(conn, sid)["d_cookie"] == "xoxd-captured"
    assert "Needs Slack login" not in resp.text.split("HTML:", 1)[1]


def test_login_route_reflects_failed_login(client, conn):
    sid = q.insert_source(conn, "Example Slack", _SLACK_URL, "slack")

    def fake_login(self):
        yield "Timed out waiting for login to Example Slack"
        return None

    with patch.object(SlackCookieLogin, "login", fake_login):
        resp = client.post(f"/sources/{sid}/login")

    assert resp.status_code == 200
    assert q.get_source(conn, sid)["d_cookie"] == ""
    assert "Needs Slack login" in resp.text.split("HTML:", 1)[1]


def test_login_route_404_for_missing_source(client, conn):
    resp = client.post("/sources/999/login")
    assert resp.status_code == 404


def test_login_route_rejects_non_slack_source(client, conn):
    sid = _seed(conn)
    resp = client.post(f"/sources/{sid}/login")
    assert resp.status_code == 400
```

Add cookie-route tests:

```python
def test_set_cookie_stores_value_with_acknowledgment(client, conn):
    sid = q.insert_source(conn, "Example Slack", _SLACK_URL, "slack")
    with patch.object(SlackFetcher, "check_needs_login", return_value=False):
        resp = client.post(
            f"/sources/{sid}/cookie",
            data={"d_cookie": "xoxd-pasted", "acknowledged": "on"},
        )
    assert resp.status_code == 200
    assert q.get_source(conn, sid)["d_cookie"] == "xoxd-pasted"
    assert "Needs Slack login" not in resp.text


def test_set_cookie_rejected_without_acknowledgment(client, conn):
    sid = q.insert_source(conn, "Example Slack", _SLACK_URL, "slack")
    resp = client.post(f"/sources/{sid}/cookie", data={"d_cookie": "xoxd-pasted"})
    assert resp.status_code == 400
    assert q.get_source(conn, sid)["d_cookie"] == ""


def test_set_cookie_invalid_shows_needs_login(client, conn):
    sid = q.insert_source(conn, "Example Slack", _SLACK_URL, "slack")
    with patch.object(SlackFetcher, "check_needs_login", return_value=True):
        resp = client.post(
            f"/sources/{sid}/cookie",
            data={"d_cookie": "xoxd-expired", "acknowledged": "on"},
        )
    assert resp.status_code == 200
    assert "Needs Slack login" in resp.text


def test_slack_row_shows_cookie_security_warning(client, conn):
    q.insert_source(conn, "Example Slack", _SLACK_URL, "slack")
    with patch.object(SlackFetcher, "check_needs_login", return_value=True):
        resp = client.get("/sources")
    assert resp.status_code == 200
    assert "every Slack workspace" in resp.text  # warning copy present
    assert 'name="acknowledged"' in resp.text     # required checkbox present
```

(The pre-existing `test_create_slack_source_*` and `test_update_source_to_slack_*` tests that patch `SlackFetcher.check_needs_login` still assert `"Log in"` appears; keep them, but ensure the reworded row in Step 3 still contains the substring `Log in`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `./projects/job-seek/.venv/bin/python -m pytest tests/test_routes_sources.py -v`
Expected: FAIL — `ImportError` for `SlackCookieLogin` and 404/405 on `/cookie`, missing warning copy.

- [ ] **Step 3: Rework `app/templates/sources/_row.html`**

Replace the needs-login block (lines 5-10) so that, for slack sources, it shows the reworded warning, a "Log in via browser" button, and a paste form with the security acknowledgment. Use this body for the first `<td>`:

```html
  <td style="padding:0.5rem;">
    <strong>{{ source.name }}</strong><br>
    <small style="word-break:break-all;">{{ source.url }}</small>
    {% if source.fetcher_type == "slack" %}
      {% if needs_login %}
        <br><span style="color:#dc3545;">⚠ Needs Slack login before it can fetch.</span>
      {% else %}
        <br><span style="color:#198754;">✓ Slack session cookie set.</span>
      {% endif %}
      <details style="margin-top:0.4rem;">
        <summary style="cursor:pointer;">Set Slack session cookie</summary>
        <p style="font-size:0.8em; color:#664d03; background:#fff3cd; padding:0.5rem; border-radius:4px;">
          The <code>d</code> cookie is your Slack session for your whole account. With it, this app can
          read and post as you in <strong>every Slack workspace you are currently logged into</strong>,
          not just this channel. It is stored locally in this app's database in plain text — anyone with
          access to that database can act as you on Slack. To revoke it, sign out of Slack. Only continue
          if you understand and accept this.
        </p>
        <form hx-post="/sources/{{ source.id }}/cookie"
              hx-target="#source-row-{{ source.id }}" hx-swap="outerHTML"
              style="display:flex; flex-direction:column; gap:0.4rem;">
          <input type="text" name="d_cookie" placeholder="Paste the `d` cookie value"
                 style="width:100%; box-sizing:border-box;">
          <label style="font-size:0.85em;">
            <input type="checkbox" name="acknowledged" required> I understand and accept the above.
          </label>
          <div style="display:flex; gap:0.5rem;">
            <button type="submit" class="btn" style="font-size:0.85em; padding:3px 10px;">Save cookie</button>
            <button type="button" class="btn" style="font-size:0.85em; padding:3px 10px;"
              data-progress-url="/sources/{{ source.id }}/login"
              data-progress-target="#source-row-{{ source.id }}">Log in via browser</button>
          </div>
        </form>
      </details>
    {% endif %}
  </td>
```

(The literal string `Log in` appears in "Log in via browser", satisfying the retained `assert "Log in" in resp.text` tests; the new tests assert on `Needs Slack login` / warning copy.)

- [ ] **Step 4: Rework `app/routes/sources.py`**

Update the import block to add:

```python
from app.fetchers.slack import SlackFetcher
from app.fetchers.slack_login import SlackCookieLogin
```

`_check_needs_login` is unchanged in behavior (it already builds the fetcher and calls `check_needs_login`), but simplify the fetcher construction to not depend on the browser profile for slack — it still works via `_make_fetcher`. Leave `_check_needs_login` as-is.

Add the cookie route (after `update_source`):

```python
@router.post("/sources/{source_id}/cookie", response_class=HTMLResponse)
def set_cookie(
    source_id: int,
    request: Request,
    d_cookie: str = Form(...),
    acknowledged: Optional[str] = Form(None),
    conn: sqlite3.Connection = Depends(get_db),
    config=Depends(get_config),
):
    _get_source_or_404(conn, source_id)
    if acknowledged is None:
        raise HTTPException(
            status_code=400,
            detail="You must acknowledge the security warning before saving the cookie.",
        )
    q.set_source_cookie(conn, source_id, d_cookie.strip())
    source = q.get_source(conn, source_id)
    needs_login = _check_needs_login(source, config, conn)
    return templates.TemplateResponse(
        request, "sources/_row.html", {"source": source, "needs_login": needs_login}
    )
```

Rewrite `trigger_login` to use the browser-login tool and persist the returned cookie:

```python
@router.post("/sources/{source_id}/login")
def trigger_login(
    source_id: int,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    config=Depends(get_config),
):
    source = _get_source_or_404(conn, source_id)
    if source["fetcher_type"] != "slack":
        raise HTTPException(status_code=400, detail="Only Slack sources support browser login")
    login = SlackCookieLogin(source, config.browser_profile_dir)

    def stream():
        gen = login.login()
        cookie = None
        try:
            while True:
                yield next(gen) + "\n"
        except StopIteration as stop:
            cookie = stop.value
        if cookie:
            q.set_source_cookie(conn, source_id, cookie)
        source_after = q.get_source(conn, source_id)
        needs_login = _check_needs_login(source_after, config, conn)
        html = templates.get_template("sources/_row.html").render(
            request=request, source=source_after, needs_login=needs_login
        )
        yield "HTML:" + html.replace("\n", "")

    return StreamingResponse(stream(), media_type="text/plain")
```

Remove the now-unused `from app.fetchers.playwright_base import PlaywrightFetcher` import and the `from app.pipeline import _make_fetcher` import only if `_make_fetcher` is no longer referenced (it is still used by `_check_needs_login` — keep that import).

- [ ] **Step 5: Run tests to verify they pass**

Run: `./projects/job-seek/.venv/bin/python -m pytest tests/test_routes_sources.py -v`
Expected: PASS (updated + new tests green).

- [ ] **Step 6: Run the full suite**

Run: `./projects/job-seek/.venv/bin/python -m pytest -q`
Expected: PASS (whole suite green).

- [ ] **Step 7: Commit**

```bash
git add app/routes/sources.py app/templates/sources/_row.html tests/test_routes_sources.py
git commit -m "feat: cookie paste (acknowledgment-gated) + browser-login cookie capture in sources UI"
```

---

## Self-Review notes

- **Spec coverage:** fetch path httpx-only (Task 2), token-from-HTML (Task 2), paste intake (Task 4), optional browser login (Task 3 + Task 4), `d_cookie` schema + migration (Task 1), cookie-based needs-login/validation (Task 2 `check_needs_login` + Task 4 rendering), security warning + required acknowledgment (Task 4), containerization impact (SlackFetcher no longer imports/uses Playwright — Task 2), testing across all tasks. All spec sections map to a task.
- **Placeholders:** none — every code and test step is complete.
- **Type consistency:** `extract_token`, `SlackFetcher(source, known_urls=…)`, `_collect(token)`, `SlackCookieLogin(source, profile_dir).login() -> str | None`, `set_source_cookie(conn, id, cookie)`, and the `/cookie` form fields (`d_cookie`, `acknowledged`) are used consistently across tasks.
- **UI placement note:** the spec left "edit row vs. dedicated inline control" open; this plan uses a dedicated inline `<form>` inside `_row.html` (avoids illegal nested forms with the existing edit form) — consistent with the approved design's inline-control option.
