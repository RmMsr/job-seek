# LinkedIn URL rewrite suggestion

## Goal

A logged-in LinkedIn job-search URL
(`https://www.linkedin.com/jobs/search-results/?keywords=robotics+norge&currentJobId=…&origin=…`)
can't be fetched — it needs a session. When someone tries to add one (from either
the Jobs panel or the Sources panel), detect it and **suggest a public equivalent
the app can actually fetch** — LinkedIn's guest-API search endpoint — and let the
user accept it as the source or proceed with the original anyway.

All LinkedIn-specific knowledge lives in one small module. The mechanism is a
generic URL-rewrite-suggestion hook; LinkedIn is its only rule today.

Not in this change: unifying the two add flows (`/sources/detect` and
`/jobs/add-by-url`). Their single-job vs listing expectations are mirror-image and
the duplication is pre-existing — see *Out of scope*.

## Current structure

- `POST /sources/detect` → `source_detect` task (`app/routes/sources.py`
  `_task_source_detect`): already-tracked check → `classify_known_source` →
  `detect_listing_page` → renders `_detect_confirm.html` or
  `_detect_mismatch.html` as a `needs_action` panel.
- `POST /jobs/add-by-url` → `job_add_by_url` task (`app/routes/jobs.py`
  `_task_job_add_by_url`): existing-job / already-tracked checks →
  `detect_listing_page` → listing → `_listing_confirm.html` `needs_action` panel;
  else `extract_text_or_raise` + `run_add_job` inline.
- Both fetch *after* the already-tracked checks, and both already call the shared
  helpers `check_already_tracked_notice_data`, `detect_listing_page`,
  `canonicalize_url`, `resolve_source_prompts_for_url`.
- Client: a `data-progress-*` button POSTs, polls `/tasks/{id}`, swaps
  `html_chunks[-1]` into `data-progress-target` (or applies `data-progress-oob`
  chunks), shows `notices`. `needs_action` results also create an inbox item so
  the panel survives a reload. `data-progress-body-<field>="#selector"` reads
  `.value` from that element and posts it as form field `<field>`.

## Design

### 1. `app/url_rewrite.py`

```python
@dataclass(frozen=True)
class RewriteSuggestion:
    url: str
    reason: str

def suggest_rewrite(url: str) -> RewriteSuggestion | None: ...
```

- Module-level list of `(match, build)` rules; `suggest_rewrite` returns the
  first hit or `None`. Domain-agnostic mechanism; LinkedIn is the only rule.
- **LinkedIn job-search rule:**
  - match: host is `linkedin.com` / `www.linkedin.com` / `*.linkedin.com`, path
    is **not** under `/jobs-guest/`, and the query has a non-empty `keywords`.
  - build → `https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords=<kw>&location=<loc>&start=0`
    — carry `keywords` and (if present and non-empty) `location` verbatim, drop
    everything else.
  - reason: `"LinkedIn search pages need a login. This public search URL can be
    fetched instead."`
  - A bare `/jobs/view/<id>` (no `keywords`) does not match.
- Idempotent via the `/jobs-guest/` path exclusion: `suggest_rewrite` of an
  already-rewritten URL returns `None`.
- Non-HTTP(S) / unparseable input returns `None`.

Confirmed by live test: the guest-API URL returns 10 static job cards per page
(`base-card__full-link` anchors, no JS), `/jobs/view/<id>` detail pages fetch
anonymously with full descriptions, and card links carry exactly the tracking
params `canonicalize_url` already strips — so once accepted it behaves as an
ordinary `generic_listing` source.

### 2. Wire into both detect tasks

In `_task_source_detect` and `_task_job_add_by_url`, immediately after the
existing already-tracked / existing-job checks and **before** any fetch:

```python
if not params.get("skip_rewrite"):
    suggestion = suggest_rewrite(url)
    if suggestion is not None:
        panel = templates.get_template("_rewrite_panel.html").render(
            request=None, original_url=url, suggested_url=suggestion.url,
            reason=suggestion.reason, detect_url=<this task's POST endpoint>,
            cancel_url=<"/sources" or "/jobs">,
        )
        q.resolve_source_prompts_for_url(conn, url)
        return {
            "notices": [], "html_chunks": [panel],
            "needs_action": True,
            "action_message": "LinkedIn search URL — a fetchable alternative was suggested",
            "resume_html": panel,
        }
```

`detect_url` is `/sources/detect` in the sources task and `/jobs/add-by-url` in
the jobs task (each task knows its own endpoint). The jobs task returns this in
place of its `result` dict, before the listing/single-job branch.

### 3. `app/templates/_rewrite_panel.html` (shared)

Same flex-column layout as the reworked `_detect_mismatch.html`:

```html
<div style="display:flex; flex-direction:column; gap:0.75rem; width:100%;" id="rewrite-panel">
  <p style="margin:0;">{{ reason }}</p>
  <p style="margin:0; word-break:break-all;"><strong>Suggested:</strong> {{ suggested_url }}</p>
  <input type="hidden" id="rw-suggested" value="{{ suggested_url }}">
  <input type="hidden" id="rw-original" value="{{ original_url }}">
  <input type="hidden" id="rw-skip" value="1">
  <div style="display:flex; gap:0.5rem; flex-wrap:wrap;">
    <a href="{{ cancel_url }}" class="btn btn-subtle">Cancel</a>
    <button type="button" class="btn"
      data-progress-url="{{ detect_url }}"
      data-progress-body-url="#rw-original"
      data-progress-body-skip_rewrite="#rw-skip"
      data-progress-target="#rewrite-panel"
      data-progress-display="#rewrite-progress">Add original anyway</button>
    <button type="button" class="btn btn-primary"
      data-progress-url="{{ detect_url }}"
      data-progress-body-url="#rw-suggested"
      data-progress-target="#rewrite-panel"
      data-progress-display="#rewrite-progress">Use suggested URL</button>
  </div>
  <span id="rewrite-progress" class="reset-progress" aria-live="polite"></span>
</div>
```

- **Use suggested URL** → re-POSTs `detect_url` with `url=<suggested>`. The
  guest-API URL doesn't re-match the rule, so the new run proceeds to
  `detect_listing_page` → the normal `_detect_confirm` / `_listing_confirm` panel.
- **Add original anyway** → re-POSTs with `url=<original>` + `skip_rewrite=1`;
  the run skips the rewrite check and fetches the original (which will likely
  land on a login wall → `_detect_mismatch` / error, the user's choice).

### 4. Endpoints accept `skip_rewrite`

`POST /sources/detect` and `POST /jobs/add-by-url` gain
`skip_rewrite: bool = Form(False)`, passed straight into the task params. No
other signature changes.

### 5. Rate-limit signal (mitigation deferred)

No throttling. `generic_listing`'s detail-fetch loop logs a WARNING naming the
host and status when a detail fetch returns HTTP 429 or 999 (LinkedIn's
bot-block code), instead of folding it into the generic failure count — so the
signal is visible in logs if the guest API starts pushing back. One fetch run of
a LinkedIn source is 1 listing request + up to `MAX_DETAIL_FETCHES` (50) detail
requests in a burst.

## Tests

- `tests/test_url_rewrite.py` — new.
  - LinkedIn logged-in search URL → guest-API URL; `keywords` + `location`
    carried, `currentJobId` / `origin` / tracking params dropped.
  - `location` absent → omitted from the result.
  - `www.` and `no.linkedin.com` hosts both match; `keywords` empty/missing → `None`.
  - `/jobs/view/1234` → `None`; already-rewritten `/jobs-guest/…` URL → `None`.
  - non-LinkedIn URL → `None`; `mailto:` / garbage → `None`.
  - idempotent: `suggest_rewrite(suggest_rewrite(u).url) is None`.
- `tests/test_routes_sources.py` — add: posting a LinkedIn search URL to
  `/sources/detect` yields a `needs_action` result whose panel contains the
  suggested guest-API URL and both action buttons; `skip_rewrite=1` bypasses it
  and proceeds to detection (mock `detect_listing_page`).
- `tests/test_routes_jobs.py` — add: the same for `/jobs/add-by-url`.
- `tests/test_fetcher_generic_listing.py` — add: a detail fetch returning HTTP
  429 logs a WARNING naming the host; the run still returns the jobs it did get.
- Full suite green.

## Manual testing

UI-facing. After implementation, run the dev server against a throwaway DB copy
(`run-dev-server` skill) and hand the URL to the user to try: paste the recorded
LinkedIn logged-in search URL into both the Jobs and Sources add panels, accept
the suggestion, confirm the fetch pulls real postings (needs `brick:7000` +
network).

## Out of scope

- **Unifying the two add flows.** `/sources/detect` and `/jobs/add-by-url` keep
  their separate tasks, panels, and confirm endpoints. Their single-job vs
  listing expectations are mirror-image (a lone job posting is the *goal* on the
  Jobs panel and a *mismatch* on the Sources panel), and the shared machinery is
  already factored into helpers. Add a `feature:` backlog item if the duplication
  ever bites.
- LinkedIn pagination (`start` += 10) — the guest API supports it, but
  `generic_listing` pagination is a separate backlog item covering all sites.
- LinkedIn as a first-class `fetcher_type` / cross-post dedup across
  LinkedIn / company site / finn — content-level, separate.
- Actual rate-limit throttling / backoff.
- Rewrite rules for any other site.
