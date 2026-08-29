# LinkedIn URL Rewrite Suggestion — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a user tries to add a logged-in LinkedIn job-search URL (from the Jobs or Sources add panel), detect it and suggest a fetchable public equivalent (LinkedIn's guest-API search endpoint) they can accept as the source.

**Architecture:** A new domain-agnostic `suggest_rewrite(url)` hook with a single LinkedIn rule, called at the top of both existing detect tasks before any fetch. On a match the task returns a `needs_action` panel (shared template) with "Use suggested URL" / "Add original anyway" buttons that re-POST the same endpoint. Plus a small rate-limit-signal log in `HttpFetcher`.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, pytest + respx. Spec: `docs/superpowers/specs/2026-08-29-linkedin-url-rewrite-design.md`.

## Global Constraints

- Personal single-instance app: no backwards-compat shims, no migrations for short-lived `tasks` rows.
- All LinkedIn-specific knowledge lives only in `app/url_rewrite.py`.
- Run tests with `/home/roman/projects/job-seek/.venv/bin/python -m pytest` from the worktree root.
- Commit after each task (see "Commit frequently" in CLAUDE.md).
- `data-progress-body-<field>="#sel"` reads `.value` from `#sel` and POSTs it as form field `<field>`.

---

### Task 1: `app/url_rewrite.py` — `suggest_rewrite`

**Files:**
- Create: `app/url_rewrite.py`
- Test: `tests/test_url_rewrite.py`

**Interfaces:**
- Produces: `suggest_rewrite(url: str) -> RewriteSuggestion | None`; `RewriteSuggestion` is a frozen dataclass with `url: str` and `reason: str`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_url_rewrite.py`:

```python
import pytest
from app.url_rewrite import suggest_rewrite, RewriteSuggestion

_LOGGED_IN = (
    "https://www.linkedin.com/jobs/search-results/"
    "?currentJobId=4435382222&keywords=robotics+norge"
    "&origin=BLENDED_SEARCH_RESULT_NAVIGATION_SEE_ALL"
)


def test_logged_in_search_url_rewrites_to_guest_api():
    s = suggest_rewrite(_LOGGED_IN)
    assert isinstance(s, RewriteSuggestion)
    assert s.url == (
        "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
        "?keywords=robotics+norge&start=0"
    )
    assert "login" in s.reason.lower()


def test_location_param_is_carried_when_present():
    s = suggest_rewrite(
        "https://www.linkedin.com/jobs/search?keywords=data+engineer&location=Norway&geoId=123"
    )
    assert s.url == (
        "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
        "?keywords=data+engineer&location=Norway&start=0"
    )


def test_subdomain_host_matches():
    assert suggest_rewrite("https://no.linkedin.com/jobs/search?keywords=ai") is not None


def test_empty_or_missing_keywords_does_not_match():
    assert suggest_rewrite("https://www.linkedin.com/jobs/search?keywords=") is None
    assert suggest_rewrite("https://www.linkedin.com/jobs/search-results/?origin=x") is None


def test_single_posting_url_does_not_match():
    assert suggest_rewrite("https://www.linkedin.com/jobs/view/robotics-engineer-at-luma-4445272760") is None


def test_already_rewritten_guest_url_does_not_match():
    s = suggest_rewrite(_LOGGED_IN)
    assert suggest_rewrite(s.url) is None


def test_non_linkedin_url_does_not_match():
    assert suggest_rewrite("https://example.com/jobs?keywords=robotics") is None


def test_non_http_input_returns_none():
    assert suggest_rewrite("mailto:jobs@example.com") is None
    assert suggest_rewrite("not a url") is None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `/home/roman/projects/job-seek/.venv/bin/python -m pytest tests/test_url_rewrite.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.url_rewrite'`

- [ ] **Step 3: Write the implementation**

Create `app/url_rewrite.py`:

```python
from __future__ import annotations
from dataclasses import dataclass
from urllib.parse import urlsplit, parse_qs, urlencode

_GUEST_SEARCH = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"


@dataclass(frozen=True)
class RewriteSuggestion:
    url: str
    reason: str


def _linkedin_job_search(parts) -> RewriteSuggestion | None:
    host = parts.netloc.lower()
    if host != "linkedin.com" and not host.endswith(".linkedin.com"):
        return None
    if parts.path.startswith("/jobs-guest/"):
        return None
    params = parse_qs(parts.query, keep_blank_values=False)
    keywords = (params.get("keywords") or [""])[0]
    if not keywords:
        return None
    query = [("keywords", keywords)]
    location = (params.get("location") or [""])[0]
    if location:
        query.append(("location", location))
    query.append(("start", "0"))
    return RewriteSuggestion(
        url=f"{_GUEST_SEARCH}?{urlencode(query)}",
        reason="LinkedIn search pages need a login. This public search URL can be fetched instead.",
    )


_RULES = [_linkedin_job_search]


def suggest_rewrite(url: str) -> RewriteSuggestion | None:
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    for rule in _RULES:
        hit = rule(parts)
        if hit is not None:
            return hit
    return None
```

Note: `urlencode([("keywords", "robotics norge")])` produces `keywords=robotics+norge`; the logged-in URL's `keywords=robotics+norge` parses back to `"robotics norge"` and re-encodes identically, so the test's expected string holds.

- [ ] **Step 4: Run tests to verify they pass**

Run: `/home/roman/projects/job-seek/.venv/bin/python -m pytest tests/test_url_rewrite.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add app/url_rewrite.py tests/test_url_rewrite.py
git commit -m "feat: add suggest_rewrite with a LinkedIn job-search rule"
```

---

### Task 2: Shared rewrite panel + wire into the Sources detect flow

**Files:**
- Create: `app/templates/_rewrite_panel.html`
- Modify: `app/routes/sources.py` (imports; `_task_source_detect` after the already-tracked check; `detect_source` endpoint)
- Test: `tests/test_routes_sources.py`

**Interfaces:**
- Consumes: `suggest_rewrite`, `RewriteSuggestion` from Task 1.
- Produces: `_rewrite_panel.html` rendered with `original_url`, `suggested_url`, `reason`, `detect_url`, `cancel_url`. `source_detect` task params gain optional `skip_rewrite: bool`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_routes_sources.py` (the `_run_detect` helper and `patch` import already exist):

```python
_LI_SEARCH = "https://www.linkedin.com/jobs/search-results/?keywords=robotics+norge&currentJobId=1&origin=x"


def test_detect_source_linkedin_search_suggests_rewrite(conn):
    with patch("app.routes.sources.detect_listing_page") as mock_detect:
        fetched = _run_detect(conn, _LI_SEARCH)
    mock_detect.assert_not_called()
    result = fetched["result"]
    assert result["needs_action"] is True
    html = result["html_chunks"][0]
    assert "jobs-guest/jobs/api/seeMoreJobPostings/search?keywords=robotics+norge&start=0" in html
    assert 'data-progress-url="/sources/detect"' in html
    assert "Use suggested URL" in html and "Add original anyway" in html


def test_detect_source_skip_rewrite_proceeds_to_detection(conn):
    task = q.enqueue_task(conn, kind="source_detect", params={"url": _LI_SEARCH, "skip_rewrite": True})
    with patch("app.routes.sources.detect_listing_page", return_value=_not_listing()):
        execute_task(conn, MagicMock(), MagicMock(), MagicMock(), task)
    result = q.get_task(conn, task["id"])["result"]
    assert "not a listing" in result["html_chunks"][0].lower() or "single job" in result["html_chunks"][0].lower()


def test_detect_endpoint_forwards_skip_rewrite(client, conn):
    resp = client.post("/sources/detect", data={"url": _LI_SEARCH, "skip_rewrite": "1"})
    task = q.get_task(conn, resp.json()["task_id"])
    assert task["params"]["skip_rewrite"] is True
```

- [ ] **Step 2: Run to verify they fail**

Run: `/home/roman/projects/job-seek/.venv/bin/python -m pytest tests/test_routes_sources.py -k "linkedin or skip_rewrite" -v`
Expected: FAIL — panel/skip logic not implemented; `KeyError`/assertion failures.

- [ ] **Step 3: Create the template**

Create `app/templates/_rewrite_panel.html`:

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

- [ ] **Step 4: Wire into `sources.py`**

Add the import after the existing `from app.fetchers.listing_detect import detect_listing_page` line:

```python
from app.url_rewrite import suggest_rewrite
```

In `_task_source_detect`, immediately after the `if already_tracked is not None:` block returns and before `default_name = urlsplit(url).netloc`:

```python
    if not params.get("skip_rewrite"):
        suggestion = suggest_rewrite(url)
        if suggestion is not None:
            panel = templates.get_template("_rewrite_panel.html").render(
                request=None, original_url=url, suggested_url=suggestion.url,
                reason=suggestion.reason, detect_url="/sources/detect", cancel_url="/sources",
            )
            q.resolve_source_prompts_for_url(conn, url)
            return {
                "notices": [], "html_chunks": [panel],
                "needs_action": True,
                "action_message": "LinkedIn search URL — a fetchable alternative was suggested",
                "resume_html": panel,
            }
```

Update the `detect_source` endpoint:

```python
@router.post("/sources/detect")
def detect_source(
    url: str = Form(...),
    skip_rewrite: bool = Form(False),
    conn: sqlite3.Connection = Depends(get_db),
):
    task = q.enqueue_task(conn, kind="source_detect", params={"url": url, "skip_rewrite": skip_rewrite})
    return {"task_id": task["id"], "already_active": task["already_active"]}
```

- [ ] **Step 5: Run the new tests + the sources suite**

Run: `/home/roman/projects/job-seek/.venv/bin/python -m pytest tests/test_routes_sources.py -v`
Expected: PASS (all, including the 3 new tests)

- [ ] **Step 6: Commit**

```bash
git add app/templates/_rewrite_panel.html app/routes/sources.py tests/test_routes_sources.py
git commit -m "feat: suggest a fetchable URL for LinkedIn searches in the Sources add flow"
```

---

### Task 3: Wire into the Jobs add-by-url flow

**Files:**
- Modify: `app/routes/jobs.py` (imports; `_task_job_add_by_url` in the `else` branch after the already-tracked check; `job_add_by_url` endpoint)
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: `suggest_rewrite` (Task 1), `_rewrite_panel.html` (Task 2).
- Produces: `job_add_by_url` task params gain optional `skip_rewrite: bool`; `_run_add_by_url` test helper gains a `skip_rewrite=False` kwarg.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_routes_jobs.py`:

```python
_LI_SEARCH = "https://www.linkedin.com/jobs/search-results/?keywords=robotics+norge&currentJobId=1&origin=x"


def test_add_by_url_linkedin_search_suggests_rewrite(conn):
    with patch("app.routes.jobs.detect_listing_page") as mock_detect:
        fetched = _run_add_by_url(conn, _LI_SEARCH)
    mock_detect.assert_not_called()
    result = fetched["result"]
    assert result["needs_action"] is True
    html = result["html_chunks"][0]
    assert "jobs-guest/jobs/api/seeMoreJobPostings/search?keywords=robotics+norge&start=0" in html
    assert 'data-progress-url="/jobs/add-by-url"' in html


def test_add_by_url_skip_rewrite_proceeds_to_detection(conn):
    with patch("app.routes.jobs.detect_listing_page", return_value=_not_listing()), \
         patch("app.routes.jobs.extract_text_or_raise", return_value="Some job posting text."), \
         patch("app.routes.jobs.run_add_job", return_value=iter(())):
        fetched = _run_add_by_url(conn, _LI_SEARCH, skip_rewrite=True)
    assert fetched["result"].get("needs_action") is None


def test_add_by_url_endpoint_forwards_skip_rewrite(client, conn):
    resp = client.post("/jobs/add-by-url", data={"url": _LI_SEARCH, "skip_rewrite": "1"})
    task = q.get_task(conn, resp.json()["task_id"])
    assert task["params"]["skip_rewrite"] is True
```

Update the `_run_add_by_url` helper:

```python
def _run_add_by_url(conn, url, filter_ctx=None, status=None, content_type=None, skip_rewrite=False):
    task = q.enqueue_task(conn, kind="job_add_by_url", params={
        "url": url, "status": status, "content_type": content_type,
        "filter_ctx": filter_ctx or {}, "skip_rewrite": skip_rewrite,
    })
    execute_task(conn, MagicMock(), "model", MagicMock(browser_profile_dir="/tmp"), task)
    return q.get_task(conn, task["id"])
```

(If `run_add_job` is imported into `tests/test_routes_jobs.py` under a different path, patch the name as it is imported in `app/routes/jobs.py` — check the existing single-job-add tests around line 1700 for the exact patch target and mirror it.)

- [ ] **Step 2: Run to verify they fail**

Run: `/home/roman/projects/job-seek/.venv/bin/python -m pytest tests/test_routes_jobs.py -k "linkedin or skip_rewrite" -v`
Expected: FAIL.

- [ ] **Step 3: Wire into `jobs.py`**

Add the import after `from app.fetchers.listing_detect import detect_listing_page`:

```python
from app.url_rewrite import suggest_rewrite
```

In `_task_job_add_by_url`, inside the `else:` that follows `if already_tracked is not None:`, as the first statements before `try: detection = detect_listing_page(client, model, url)`:

```python
            if not params.get("skip_rewrite"):
                suggestion = suggest_rewrite(url)
                if suggestion is not None:
                    panel = templates.get_template("_rewrite_panel.html").render(
                        request=None, original_url=url, suggested_url=suggestion.url,
                        reason=suggestion.reason, detect_url="/jobs/add-by-url", cancel_url="/jobs",
                    )
                    html_chunks.append(panel)
                    q.resolve_source_prompts_for_url(conn, url)
                    result["needs_action"] = True
                    result["action_message"] = "LinkedIn search URL — a fetchable alternative was suggested"
                    result["resume_html"] = panel
                    return result
```

Update the `job_add_by_url` endpoint to accept `skip_rewrite: bool = Form(False)` and add `"skip_rewrite": skip_rewrite` to the `enqueue_task` params dict.

- [ ] **Step 4: Run the new tests + the jobs suite**

Run: `/home/roman/projects/job-seek/.venv/bin/python -m pytest tests/test_routes_jobs.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/routes/jobs.py tests/test_routes_jobs.py
git commit -m "feat: suggest a fetchable URL for LinkedIn searches in the Jobs add flow"
```

---

### Task 4: Rate-limit signal in `HttpFetcher`

**Files:**
- Modify: `app/fetchers/http.py`
- Test: `tests/test_fetcher_http.py`

**Interfaces:**
- No API change. `HttpFetcher.fetch()` still returns `list[RawJob]` (empty on non-200). Adds a WARNING log on HTTP 429 / 999.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_fetcher_http.py` (check the file header for existing `respx` / `httpx` imports; add them if missing):

```python
import respx, httpx
from app.fetchers.http import HttpFetcher


@respx.mock
def test_http_fetcher_logs_warning_on_rate_limit(caplog):
    respx.get("https://www.linkedin.com/jobs/view/1").mock(return_value=httpx.Response(429, text="slow down"))
    with caplog.at_level("WARNING", logger="job_seek"):
        jobs = HttpFetcher({"url": "https://www.linkedin.com/jobs/view/1"}).fetch()
    assert jobs == []
    assert any("429" in r.message and "linkedin.com" in r.message for r in caplog.records)


@respx.mock
def test_http_fetcher_no_warning_on_ordinary_404(caplog):
    respx.get("https://example.com/gone").mock(return_value=httpx.Response(404))
    with caplog.at_level("WARNING", logger="job_seek"):
        HttpFetcher({"url": "https://example.com/gone"}).fetch()
    assert not caplog.records
```

- [ ] **Step 2: Run to verify it fails**

Run: `/home/roman/projects/job-seek/.venv/bin/python -m pytest tests/test_fetcher_http.py -k rate_limit -v`
Expected: FAIL — no warning logged.

- [ ] **Step 3: Implement**

Edit `app/fetchers/http.py`:

```python
from __future__ import annotations
import logging
from urllib.parse import urlsplit
import httpx
from bs4 import BeautifulSoup
from app.fetchers.base import Fetcher, RawJob

logger = logging.getLogger("job_seek")

_RATE_LIMIT_STATUS = {429, 999}  # 999 = LinkedIn bot-block


class HttpFetcher:
    def __init__(self, source: dict) -> None:
        self._source = source

    def fetch(self) -> list[RawJob]:
        try:
            resp = httpx.get(self._source["url"], timeout=30, follow_redirects=True)
            if resp.status_code != 200:
                if resp.status_code in _RATE_LIMIT_STATUS:
                    logger.warning(
                        "HTTP %s fetching %s from %s — rate-limited or bot-blocked",
                        resp.status_code, self._source["url"],
                        urlsplit(self._source["url"]).netloc,
                    )
                return []
            return self._parse(resp.text)
        except Exception:
            return []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/home/roman/projects/job-seek/.venv/bin/python -m pytest tests/test_fetcher_http.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/fetchers/http.py tests/test_fetcher_http.py
git commit -m "feat: log a warning when a detail fetch is rate-limited (HTTP 429/999)"
```

---

### Task 5: Full suite + backlog

**Files:**
- Modify: `BACKLOG.md`

- [ ] **Step 1: Run the whole suite**

Run: `/home/roman/projects/job-seek/.venv/bin/python -m pytest -q`
Expected: all pass (was 976; +~17 new).

- [ ] **Step 2: Update BACKLOG.md**

Remove the two LinkedIn `fetch:` lines now handled (the `- fetch: linkedin` / `- fetch: linkedin like: …` entries). Add under the appropriate section:

```
- feature: unify the /sources/detect and /jobs/add-by-url add flows — shared
  detection pipeline, panels, and confirm task. Deferred from the LinkedIn
  URL-rewrite change; do it if the duplication starts to bite.
```

- [ ] **Step 3: Commit**

```bash
git add BACKLOG.md
git commit -m "chore: backlog — LinkedIn rewrite done, note deferred add-flow unification"
```

---

## Self-Review

- **Spec coverage:** §1 `url_rewrite.py` → Task 1. §2 wiring both tasks → Tasks 2–3. §3 shared panel → Task 2. §4 `skip_rewrite` endpoints → Tasks 2–3. §5 rate-limit signal → Task 4. Tests §: `test_url_rewrite.py` (T1), route tests (T2–3), `generic_listing`/http 429 (T4, placed in `test_fetcher_http.py` where the code lives — noted deviation from the spec's suggested file). Manual testing → after Task 5, hand dev server to the user.
- **Placeholders:** none — every step has concrete code or an exact command.
- **Type consistency:** `RewriteSuggestion(url, reason)` used identically in Tasks 1–3. `suggest_rewrite` import path `app.url_rewrite` consistent. `params.get("skip_rewrite")` (truthy check) tolerates missing key in old-shape params.
- **Known soft spot:** Task 3's `run_add_job` patch target — the plan tells the implementer to mirror the existing single-job-add tests rather than guessing. `_not_listing()` in `test_routes_jobs.py` returns a `ListingDetection`; the skip-rewrite test also patches `extract_text_or_raise` so no network is hit.

## After implementation

UI-facing change. Per CLAUDE.md: start the dev server against a throwaway `job-seek.db` copy (`run-dev-server` skill) and hand the URL to the user to try the LinkedIn suggestion in both add panels before merging. Do not squash-merge until they've signed off.
