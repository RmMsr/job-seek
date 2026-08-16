# Source-type auto-detection & add-source unfold — design

**Problem:** adding a source today (`sources/index.html`'s always-visible form) forces the user to pick `fetcher_type` from a raw dropdown (`http`/`playwright`/`slack`/`finn_listing`/`generic_listing`) with no guidance on which one a given URL actually needs. The Jobs page's "Add" flow (`POST /jobs/add-by-url`) already solves the equivalent problem for single postings by fetching the URL and running it through the AI `detect_listing` classifier — this design extends that same detection approach to sources, and replaces the static dropdown form with a small unfolding "+ Add source" control that mirrors the Jobs page's minimal "paste a URL" UX.

Two pre-existing bugs surfaced while reading the affected files are fixed as part of this change (both explained below): the synthetic "Manual" source briefly appearing in the sources table, and a missing `generic_listing` option in the per-row edit dropdown that silently downgrades a source's type on save.

## 1. What gets auto-detected, and how

Only three `fetcher_type` values are reachable through the new add flow: `slack`, `finn_listing`, `generic_listing`. `http` and `playwright` are legacy — every current `http`/`playwright` row except one is disabled, and `generic_listing` already does what they do (fetch + extract) plus AI-driven link discovery and its own Playwright fallback for JS-heavy pages. They stay in the DB `CHECK` constraint and the per-row edit dropdown (existing rows must remain editable) but are never assigned by detection.

**Fast path — no fetch needed** (`app/ai/classify_known_source.py`, new):
```python
def classify_known_source(url: str) -> str | None:
    if SLACK_URL_RE.match(url):
        return "slack"
    host = urlsplit(url).netloc.lower()
    if host == "finn.no" or host.endswith(".finn.no"):
        return "finn_listing"
    return None
```
`SLACK_URL_RE` is promoted from `app/fetchers/slack.py`'s private `_URL_RE` to a public name (`SLACK_URL_RE`), since it's now imported from two modules.

**Slow path — fetch + AI listing check** (only reached when the fast path returns `None`): fetch the URL (`httpx.get`, 30s timeout), fall back to a Playwright render if the plain fetch doesn't yield enough content, extract links, and run the existing `detect_listing(client, model, links, url)` AI classifier. If `is_listing` is true and it found ≥2 job links (same threshold `jobs.py` already uses), the source is `generic_listing`. Otherwise the URL looks like a single job posting, not a listing — detection returns "no listing detected" rather than a fetcher_type.

This slow-path logic already exists almost verbatim in `app/routes/jobs.py:387-446` (`job_add_by_url`). Rather than introducing a second copy of it, the four small building blocks it's made of (`_fetch_url_html`/`_FetchError`/`_NoContentError`, `has_enough_content`, `render_html`, `extract_links`) move from being private helpers in `jobs.py` into `app/fetchers/content.py` (which already owns `has_enough_content`/`extract_text`) so both `app/routes/jobs.py` and the new `app/routes/sources.py` detection code import the same implementations instead of duplicating them. `detect_listing` itself is already a shared, stateless AI helper — no change needed there.

If the fetch itself fails (network error, non-200, timeout), detection can't determine listing-vs-not; it falls back to `generic_listing` rather than blocking the add — same as the "just default to generic_listing" behavior would give, since generic_listing re-attempts fetching (with its own Playwright fallback) on every scheduled run anyway.

## 2. Sources page: two-step add flow

**Route 1 — `POST /sources/detect`** (`url: str = Form(...)`): runs `classify_known_source`, then the slow path if needed. Streams progress lines (`"Fetching page…"`, `"Checking with AI…"`), then a single `HTML:` chunk:
- **Detected** (`fetcher_type` is `slack`/`finn_listing`/`generic_listing`): renders `sources/_detect_confirm.html` — shows the detected type as plain text, an editable Name field prefilled with `urlsplit(url).netloc`, and hidden `url`/`fetcher_type` fields.
- **Not a listing**: renders `sources/_detect_mismatch.html` — a warning plus two buttons: "Add as job instead" (posts to the existing `/jobs/add-by-url`) and "Add as source anyway" (same as the confirm panel, with `fetcher_type` hardcoded to `generic_listing`).

**Route 2 — `POST /sources/detect/confirm`** (`url`, `name`, `fetcher_type: str = Form(...)`): validates `fetcher_type` is one of `{"slack", "finn_listing", "generic_listing"}` (400 otherwise — this is a hidden field round-tripped from our own templates, but the endpoint shouldn't trust it blindly), then `q.insert_source(conn, name, url, fetcher_type)` followed immediately by `run_fetch(...)` for that new source — mirroring what `job_add_listing_source` (jobs.py:473-501) already does for the equivalent "keep as source & fetch" case on the Jobs page. Streams `run_fetch`'s progress lines, then two `HTML:` chunks (OOB mode, see §3): the refreshed `sources/_table.html` and a reset `sources/_add_form.html`.

## 3. UI: unfold via `<details>`, refresh via existing OOB machinery

`sources/index.html`'s static form is replaced with:
```html
<details id="add-source-toggle">
  <summary class="btn">+ Add source</summary>
  {% include "sources/_add_form.html" %}
</details>
```
— the same collapsible pattern the Slack-cookie section in `_row.html` already uses; no new JS for the expand/collapse itself.

`sources/_add_form.html` (new partial) is the always-reusable "paste a URL" state:
```html
<div id="add-source-panel">
  <input type="url" id="add-source-url" name="url" required placeholder="Paste a listing page URL…" class="text-input">
  <button type="button" class="btn"
    data-progress-url="/sources/detect"
    data-progress-body-url="#add-source-url"
    data-progress-target="#add-source-panel"
    data-progress-display="#add-source-progress">Add</button>
  <span id="add-source-progress" class="reset-progress" aria-live="polite"></span>
</div>
```
Step 1's response swaps `#add-source-panel`'s innerHTML (via `data-progress-target`) with the confirm or mismatch panel. Step 2's "Add source" / "Add as source anyway" buttons use `data-progress-oob` instead of a target: the response's two `HTML:` chunks are each id'd top-level elements (`id="sources-table"`, `id="add-source-panel"`) that the existing `applyOob()` function (`base.html`) replaces by id — the same mechanism already used for e.g. `jobs/_counts_oob.html`. This both refreshes the table and collapses the add form back to its blank starting state, with no new JS.

`sources/_table.html` (new partial): the existing `{% if sources %}<table>...</table>{% else %}<p>No sources yet...</p>{% endif %}` block from `sources/index.html`, extracted verbatim and wrapped in `<div id="sources-table">`, so both the initial page render and the OOB refresh chunk share one template.

The mismatch panel's "Add as job instead" button posts to `/jobs/add-by-url` with no `data-progress-target`/`data-progress-oob` attributes — the existing shared JS already falls back to `location.reload()` when neither is set, which is the right behavior here (the Sources page has no `#jobs-content` to swap into, and a reload naturally re-renders the now-correctly-filtered sources table too).

## 4. Jobs page: stop hardcoding `generic_listing`

`job_add_by_url` (jobs.py) already runs `detect_listing` once it has fetched a URL and found it's a listing. Today `_listing_confirm.html`'s "Keep as source & fetch" button always calls `job_add_listing_source`, which hardcodes `fetcher_type="generic_listing"` at creation (jobs.py:487) — so a finn.no search URL or Slack channel link pasted into the Jobs box, if it happens to look like a listing, gets the wrong fetcher.

Fix: after `job_add_by_url` confirms `is_listing`, it also calls `classify_known_source(url)` (the fast path only — the slow path's fetch already happened) and passes the result (or `"generic_listing"` if `None`) into `_listing_confirm.html` as a hidden field, same shape as the new sources confirm panel. `job_add_listing_source` takes `fetcher_type: str = Form(...)` instead of hardcoding it, with the same `{"slack", "finn_listing", "generic_listing"}` validation as `/sources/detect/confirm`.

## 5. Bug fixes bundled in

**"Manual" source flicker:** the synthetic `fetcher_type="manual"` source (`q.get_or_create_manual_source`) is filtered out of the sources table by `GET /sources` (`sources_page`, sources.py:27) but not by the old `POST /sources` (`create_source`, sources.py:36-52), which re-rendered the whole page from an unfiltered `q.get_sources(conn)` — so it flashed into view right after adding or editing a source. This design removes `create_source`/the old add-with-dropdown form entirely (superseded by §2's two-step flow), which eliminates the bug at its original site. The replacement, `confirm_source` (`/sources/detect/confirm`), and `sources_page` both go through one new helper, `_visible_sources(conn)` (filters `fetcher_type != "manual"`), so the same mistake can't recur.

**Missing `generic_listing` option in the edit-row dropdown:** `sources/_row_edit.html`'s `<select name="fetcher_type">` currently lists only `http`/`playwright`/`slack`/`finn_listing`. Two existing sources (`mlai.work/norway`, `Trener@ashbyhq.com`) are `generic_listing`; opening Edit and saving without touching the dropdown silently submits `http` (the browser's default when no `<option>` matches `selected`), corrupting the source's type. Fix: add `<option value="generic_listing">generic_listing</option>`. This dropdown remains the sole way to manually set/override a source's `fetcher_type` after the fact (§1's stated escape hatch), so it needs to cover every value detection can produce.

## Out of scope

- **Re-detecting an existing source's type.** Detection only runs at add time; changing a source's type later still goes through the manual edit-row dropdown.
- **`http`/`playwright` as detectable outcomes.** Per §1, existing rows of those types keep working and stay editable, but nothing new is ever created with them.
- **Auto-collapsing the `<details>` toggle itself.** Only its inner content (`#add-source-panel`) resets after a successful add; the `<details>` element stays open if the user had it open. Matches how the Slack-cookie `<details>` behaves today (no auto-collapse logic exists for it either).

## Testing

1. `classify_known_source`: a Slack archive/messages URL returns `"slack"`; a `finn.no`/`*.finn.no` URL returns `"finn_listing"`; an unrelated URL returns `None`.
2. `POST /sources/detect` with a mocked fetch + `detect_listing` returning `is_listing=True` and ≥2 job links renders the confirm panel with `fetcher_type="generic_listing"`.
3. `POST /sources/detect` with `detect_listing` returning `is_listing=False` renders the mismatch panel.
4. `POST /sources/detect` with a Slack/finn.no URL never calls the fetch/AI path (fast path short-circuits) and renders the confirm panel directly.
5. `POST /sources/detect/confirm` with an invalid `fetcher_type` (e.g. `"http"`) returns 400.
6. `POST /sources/detect/confirm` with a valid `fetcher_type` inserts the source, runs a fetch, and the response contains both OOB chunks (`id="sources-table"`, `id="add-source-panel"`).
7. A DB containing the "Manual" source: `GET /sources` and `POST /sources/detect/confirm` both exclude it from their rendered table.
8. `sources/_row_edit.html` route test: a `generic_listing` source's edit form has `generic_listing` selected (not silently defaulting elsewhere).
9. `job_add_by_url`: a listing URL that also matches the Slack/finn.no fast path produces a `_listing_confirm.html` hidden field with that type, not `generic_listing`.
10. Manual/browser verification: paste a finn.no search URL, a Slack channel URL, and a generic careers-page URL into the new Sources "+ Add source" form; confirm each lands on the right detected type and a real fetch runs. Paste a single job-posting URL and confirm the mismatch panel appears with a working "Add as job instead" button.
