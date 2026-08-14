# Slack cookie-only fetch — design

**Date:** 2026-08-11
**Status:** Approved (pending written review)

## Problem

The current `SlackFetcher` obtains credentials by driving a real Chromium via
Playwright: it launches a persistent browser context, requires an interactive
**headful** login (`headless=False`) on a machine with a display, and then reads
the `xoxc-` token out of `localStorage.localConfig_v2` plus the `d` session
cookie. This does not containerize:

- The interactive login step needs a display; a headless container has none, and
  even with a virtual framebuffer the user cannot see or drive it.
- The persistent browser profile must be a mounted volume, and Chromium is a
  heavy runtime dependency.

## Spike findings (2026-08-11)

A spike (recorded in memory as `project-slack-cookie-only-fetch`) established:

- The `xoxc-` API token is embedded in the **raw server HTML** of the
  `/messages/<channel>` (redirect) page — inside a `boot_data` /
  `"api_token":"xoxc-..."` script literal — whenever the request carries a valid
  `d` session cookie. Verified end-to-end: `httpx.get(messages_url,
  cookies={"d": d})` → regex the token → `conversations.history` with `(token, d)`
  returns `ok=True` with real messages.
- Therefore **no browser is needed on the fetch path**. The single secret is the
  `d` cookie; the `xoxc` token need not be stored — derive it fresh each run.
- A real, rendering, interactive browser is only needed for the **initial login
  that mints `d`** (email / SSO / magic-link / 2FA), and `d` expires, so
  re-auth is periodic regardless of approach.
- Lightweight from-scratch headless engines (Lightpanda, Obscura) are **not**
  drop-ins for the old extraction: they never boot Slack's SPA, so
  `localStorage.localConfig_v2` is never populated. They became irrelevant once
  the raw-HTML token path was found — `httpx` alone suffices.

## Goals

- Remove Chromium/Playwright from the Slack **fetch** path entirely.
- Store a single per-source secret (the `d` cookie); derive the `xoxc` token per
  run from the messages-page HTML.
- Provide two ways to get the cookie in: a **paste field** (universal, the only
  path that works headless in a container) and an **optional browser-login tool**
  (a dev/host convenience that reuses the existing Playwright flow to extract and
  store the cookie).
- Warn the user about what the `d` cookie grants and require an explicit
  acknowledgment before storing it.

## Non-goals

- Official Slack app / bot OAuth tokens (`xoxb`/`xoxp`). Out of scope; the
  session-cookie approach exists precisely for member-only workspaces where an
  app cannot be installed.
- Automatic/unattended cookie refresh. `d` expiry remains a manual re-auth
  (re-paste or re-run the browser login).
- Encrypting the cookie at rest. The app's SQLite DB is already the trust
  boundary for a personal, single-instance deployment; the cookie is stored in
  the DB like other local state. (Called out explicitly in the warning copy.)

## Architecture

### Fetch flow (pure httpx, no browser)

`SlackFetcher` no longer inherits `PlaywrightFetcher`. `fetch()` does:

1. `GET` the resolved messages URL with `cookies={"d": <stored cookie>}`,
   `follow_redirects=True`, a browser-like `User-Agent`.
2. Extract the token via regex `"api_token":"(xoxc-[A-Za-z0-9-]+)"` on the
   response body (first match). If no token is found, the response is a sign-in
   page / the cookie is invalid → raise a "needs login" style error.
3. Run the **existing** history-pagination loop (`_fetch_history_page`,
   already `httpx`-based) and `_message_to_job` conversion unchanged, using the
   derived token and the `d` cookie.

`_fetch_history_page`, `_message_to_job`, `_clean_mrkdwn`, `_ts_to_iso`,
`_resolve_target`, the system-subtype filtering, age cutoff, known-URL skipping,
and all pagination caps are **kept as-is**.

Removed: `_extract_credentials` (localStorage/cookie reading via a Playwright
`page`), the `page`-typed `_extract(page)` signature, and the
`PlaywrightFetcher` base for Slack. `_extract` is refactored to take the derived
token + cookie (or the fetcher drives the loop internally); tests adjust
accordingly.

### Cookie acquisition

Two independent paths, both writing `sources.d_cookie`:

- **Paste field (primary).** A field on the Slack source's **edit row**
  (`_row_edit.html`), shown for `slack` sources, where the user pastes the `d`
  value copied from browser DevTools (Firefox: Storage → Cookies → slack.com →
  `d`; Chrome: Application → Cookies → `d`). Saving validates the cookie (see
  below) and stores it.

- **"Log in via browser" (optional escape hatch).** Reuses the existing
  Playwright headful login + streaming-progress flow on the `/sources/{id}/login`
  route. Its end state changes: after the user completes login in the opened
  window, instead of relying on a persistent profile, it reads
  `context.cookies()` for the `d` cookie and stores it on the source. Only works
  where a display + Playwright are available; where they are not (a headless
  container) the button/flow reports that gracefully and the user falls back to
  paste. This keeps the streaming route and its progress UX, only repurposing the
  final step.

### Security warning + required acknowledgment

Setting a `d` cookie (via either path) is gated by an explicit acknowledgment,
because the `d` cookie is the user's **entire Slack identity across every
workspace they are signed into** — not scoped to the one channel being fetched.

- The paste form includes warning copy and a **required "I understand"
  checkbox**; the store route rejects the request if the acknowledgment is
  absent.
- The "Log in via browser" trigger carries the same acknowledgment gate before it
  opens the browser / stores the cookie.

**Warning copy (substance, final wording during implementation):**
> The `d` cookie is your Slack session for your whole account. With it, this app
> can read and post as you in **every Slack workspace you are currently logged
> into**, not just this channel. It is stored locally in this app's database in
> plain text. Anyone with access to that database can act as you on Slack. To
> revoke it, sign out of Slack (which invalidates the cookie). Only continue if
> you understand and accept this.

### Data model

Add one column:

```sql
ALTER TABLE sources ADD COLUMN d_cookie TEXT NOT NULL DEFAULT '';
```

Non-destructive, no table rebuild (consistent with the project's
hard-but-simple migration philosophy). Only meaningful for `slack` sources;
empty for all others. `insert_source` / `update_source` / `get_source(s)`
carry the column through; a dedicated `set_source_cookie(conn, id, cookie)`
(or an extended `update_source`) writes it.

### "Needs login" / validation

`check_needs_login` becomes a cheap one-shot with no browser:

- Empty `d_cookie` → needs login.
- Otherwise `GET` the messages URL with the cookie and check whether a token is
  extractable. Token present → logged in. Token absent (sign-in page) → needs
  login.
- A cookie that parses a token but later yields `invalid_auth` from
  `conversations.history` at fetch time surfaces the same needs-login state on
  the next status check.

The `_row.html` "⚠ Needs Slack login" block is reworded to offer both **paste**
(open the edit row) and **Log in via browser**.

### Containerization impact

- Chromium/Playwright is no longer on the Slack fetch path. A headless container
  fetches Slack with zero browser.
- Playwright remains a dependency for `finn_listing`, the generic `playwright`
  fetcher, and the optional browser-login tool. The login tool degrades
  gracefully where no display exists.
- `browser_profile_dir` remains used by those other paths; `SlackFetcher` no
  longer needs `profile_dir` for fetching (it may still receive it for the
  optional login tool, or the login tool is factored separately — decided in the
  plan).

## Affected components

- `app/fetchers/slack.py` — rewrite fetch path to httpx-only + token-from-HTML;
  drop `PlaywrightFetcher` base and `_extract_credentials`; add token-extraction
  helper and a cookie-only `check_needs_login`.
- `app/db/schema.py` — add `d_cookie` column + migration.
- `app/db/queries.py` — carry `d_cookie` through source queries; add a
  cookie-setter.
- `app/pipeline.py` `_make_fetcher` — construct `SlackFetcher` with the stored
  cookie.
- `app/routes/sources.py` — cookie paste/store route with acknowledgment gate
  and validation; adapt the `/login` streaming route to store the extracted
  cookie; `check_needs_login` becomes cookie-based.
- `app/templates/sources/_row.html`, `_row_edit.html` — paste field, warning +
  acknowledgment checkbox, reworded needs-login block.
- The optional browser-login extraction step (reuses `playwright_base.py` login
  machinery; exact factoring decided in the plan).

## Testing

- **`tests/test_fetcher_slack.py`:** replace the `localStorage` / `_mock_page`
  credential-extraction tests with **HTML token-extraction** tests — respx
  serves messages-page HTML containing an embedded `api_token`; assert the token
  is parsed; assert a sign-in page (no token) raises the needs-login error. The
  pagination, mrkdwn-cleanup, timestamp, and message-conversion tests are kept
  (they already mock `conversations.history` via respx); their fetcher
  construction / `_extract` invocation is updated to the new signature.
- **Cookie validation / needs-login:** tests for empty cookie, valid cookie,
  expired cookie (no token in HTML), and `invalid_auth` from the API.
- **Routes / UI:** paste stores the cookie; store is rejected without the
  acknowledgment checkbox; validity states render; the reworded needs-login block
  appears when appropriate.
- **Browser-login tool:** light coverage — it is the escape hatch — asserting the
  extracted `d` cookie is persisted to the source.

## Migration

Single forward migration adding the `d_cookie` column with a default of `''`.
Existing Slack sources will show "needs login" until a cookie is provided, which
is the correct state (the old persistent-profile session is no longer consulted).

## Open questions

None blocking. Remaining choices (exact warning wording; whether the optional
login tool lives in `slack.py`, `playwright_base.py`, or a small separate module;
whether the cookie setter extends `update_source` or is its own query) are
implementation details resolved in the plan.
