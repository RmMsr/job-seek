# Pipeline Progress Visibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Fetch, Re-evaluate jobs, and Refine criteria observable while they run — in the server console via logging, and in the browser via a delayed live progress indicator — instead of blocking silently until they finish.

**Architecture:** The three job-loop functions become Python generators that `yield` short progress strings (and log each one) as they work; their routes wrap the generator in a `StreamingResponse` so the same `POST` request's body is delivered incrementally. A small vanilla-JS helper in `base.html` reads that stream via `fetch()` + `ReadableStream`, shows an elapsed-seconds counter and the latest status line only if the request is still running after 5 seconds, then either swaps in a returned HTML fragment (Refine) or reloads the page (Fetch, Re-evaluate).

**Tech Stack:** Python 3.12, FastAPI (`StreamingResponse`), stdlib `logging`, vanilla JS (`fetch`, `ReadableStream`, `TextDecoder`) — no new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-25-pipeline-progress-visibility-design.md`

## Global Constraints

- No new Python or JS dependency — stdlib `logging` and FastAPI's existing `StreamingResponse` only; frontend uses vanilla JS, no library, no build step.
- All three actions stay `POST` — no GET, no real SSE/`EventSource`, no background task, no polling endpoint.
- Every progress message is logged via `logging.getLogger("job_seek")` at the exact point it's yielded — one call site produces both the console line and the streamed line, so they can never drift apart.
- The browser only shows extra progress UI if the action is still running 5 seconds after it started; faster actions show nothing beyond what exists today.
- Fetch and Re-evaluate routes do not return their final rendered page over the stream — the client reloads instead. Only Refine (a fragment swap into an in-page div) sends a final `HTML:`-prefixed chunk.

---

### Task 1: Convert `run_fetch` into a logging, progress-yielding generator

**Files:**
- Modify: `app/main.py`
- Modify: `app/pipeline.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Produces: `run_fetch(source, conn, client, model, profile_dir)` — now a generator function. Iterating it yields `str` progress messages; when exhausted, `StopIteration.value` holds the `FetchResult` it used to return directly. `FetchResult` itself is unchanged.
- Produces: `logging.getLogger("job_seek")` — the logger name every later task's log calls must reuse.

- [ ] **Step 1: Write a generator-draining test helper and update existing tests to use it**

Open `tests/test_pipeline.py`. Add this helper near the top (after the existing imports/fixtures, before the first test function):

```python
def _drain(gen):
    messages = []
    try:
        while True:
            messages.append(next(gen))
    except StopIteration as stop:
        return messages, stop.value
```

Then update each of the four existing tests to drain the generator instead of calling `run_fetch` as a plain function. Replace:

```python
def test_run_fetch_new_job_stored(conn, source):
    raw_jobs = [RawJob(url="http://example.com/job/1", title="ML Eng", company="Acme", raw_text="<p>We are hiring</p>")]
    client = MagicMock()
    choice = MagicMock()
    choice.message.content = '{"type": "job_posting", "reason": "full description"}'
    summarize_choice = MagicMock()
    summarize_choice.message.content = "Good ML role"
    evaluate_choice = MagicMock()
    evaluate_choice.message.content = '{"score": 0.9, "reasoning": "Great match"}'
    call_count = [0]
    def create(**kwargs):
        call_count[0] += 1
        m = MagicMock()
        if call_count[0] == 1:
            m.choices = [choice]
        elif call_count[0] == 2:
            m.choices = [summarize_choice]
        else:
            m.choices = [evaluate_choice]
        return m
    client.chat.completions.create.side_effect = create

    with patch("app.pipeline.HttpFetcher") as MockFetcher:
        MockFetcher.return_value.fetch.return_value = raw_jobs
        result = run_fetch(source, conn, client, "llama3.2", "browser-profile")

    assert isinstance(result, FetchResult)
    assert result.jobs_new == 1
    assert result.error is None
    jobs = q.get_jobs(conn)
    assert len(jobs) == 1
    assert jobs[0]["url"] == "http://example.com/job/1"
    assert jobs[0]["content_type"] == "job_posting"
```

with:

```python
def test_run_fetch_new_job_stored(conn, source):
    raw_jobs = [RawJob(url="http://example.com/job/1", title="ML Eng", company="Acme", raw_text="<p>We are hiring</p>")]
    client = MagicMock()
    choice = MagicMock()
    choice.message.content = '{"type": "job_posting", "reason": "full description"}'
    summarize_choice = MagicMock()
    summarize_choice.message.content = "Good ML role"
    evaluate_choice = MagicMock()
    evaluate_choice.message.content = '{"score": 0.9, "reasoning": "Great match"}'
    call_count = [0]
    def create(**kwargs):
        call_count[0] += 1
        m = MagicMock()
        if call_count[0] == 1:
            m.choices = [choice]
        elif call_count[0] == 2:
            m.choices = [summarize_choice]
        else:
            m.choices = [evaluate_choice]
        return m
    client.chat.completions.create.side_effect = create

    with patch("app.pipeline.HttpFetcher") as MockFetcher:
        MockFetcher.return_value.fetch.return_value = raw_jobs
        messages, result = _drain(run_fetch(source, conn, client, "llama3.2", "browser-profile"))

    assert isinstance(result, FetchResult)
    assert result.jobs_new == 1
    assert result.error is None
    assert any("job_posting" in m for m in messages)
    jobs = q.get_jobs(conn)
    assert len(jobs) == 1
    assert jobs[0]["url"] == "http://example.com/job/1"
    assert jobs[0]["content_type"] == "job_posting"
```

Replace:

```python
def test_run_fetch_skips_existing_url(conn, source):
    q.insert_job(conn, source_id=source["id"], url="http://example.com/job/1", title="T", company="C", raw_text="r")
    raw_jobs = [RawJob(url="http://example.com/job/1", title="ML Eng", company="Acme", raw_text="text")]
    client = MagicMock()

    with patch("app.pipeline.HttpFetcher") as MockFetcher:
        MockFetcher.return_value.fetch.return_value = raw_jobs
        result = run_fetch(source, conn, client, "llama3.2", "browser-profile")

    assert result.jobs_found == 1
    assert result.jobs_new == 0
```

with:

```python
def test_run_fetch_skips_existing_url(conn, source):
    q.insert_job(conn, source_id=source["id"], url="http://example.com/job/1", title="T", company="C", raw_text="r")
    raw_jobs = [RawJob(url="http://example.com/job/1", title="ML Eng", company="Acme", raw_text="text")]
    client = MagicMock()

    with patch("app.pipeline.HttpFetcher") as MockFetcher:
        MockFetcher.return_value.fetch.return_value = raw_jobs
        messages, result = _drain(run_fetch(source, conn, client, "llama3.2", "browser-profile"))

    assert result.jobs_found == 1
    assert result.jobs_new == 0
    assert any("Skipping duplicate" in m for m in messages)
```

Replace:

```python
def test_run_fetch_records_fetch_run(conn, source):
    with patch("app.pipeline.HttpFetcher") as MockFetcher:
        MockFetcher.return_value.fetch.return_value = []
        run_fetch(source, conn, client=MagicMock(), model="llama3.2", profile_dir="bp")

    runs = q.get_recent_fetch_runs(conn)
    assert len(runs) == 1
    assert runs[0]["completed_at"] is not None
```

with:

```python
def test_run_fetch_records_fetch_run(conn, source):
    with patch("app.pipeline.HttpFetcher") as MockFetcher:
        MockFetcher.return_value.fetch.return_value = []
        _drain(run_fetch(source, conn, client=MagicMock(), model="llama3.2", profile_dir="bp"))

    runs = q.get_recent_fetch_runs(conn)
    assert len(runs) == 1
    assert runs[0]["completed_at"] is not None
```

Replace:

```python
def test_run_fetch_irrelevant_not_evaluated(conn, source):
    raw_jobs = [RawJob(url="http://example.com/job/1", title="", company="", raw_text="meetup next week")]
    client = MagicMock()
    choice = MagicMock()
    choice.message.content = '{"type": "irrelevant", "reason": "not a job"}'
    client.chat.completions.create.return_value = MagicMock(choices=[choice])

    with patch("app.pipeline.HttpFetcher") as MockFetcher:
        MockFetcher.return_value.fetch.return_value = raw_jobs
        run_fetch(source, conn, client, "llama3.2", "browser-profile")

    jobs = q.get_jobs(conn)
    assert jobs[0]["content_type"] == "irrelevant"
    assert jobs[0]["summary"] == ""
    assert client.chat.completions.create.call_count == 1
```

with:

```python
def test_run_fetch_irrelevant_not_evaluated(conn, source):
    raw_jobs = [RawJob(url="http://example.com/job/1", title="", company="", raw_text="meetup next week")]
    client = MagicMock()
    choice = MagicMock()
    choice.message.content = '{"type": "irrelevant", "reason": "not a job"}'
    client.chat.completions.create.return_value = MagicMock(choices=[choice])

    with patch("app.pipeline.HttpFetcher") as MockFetcher:
        MockFetcher.return_value.fetch.return_value = raw_jobs
        _drain(run_fetch(source, conn, client, "llama3.2", "browser-profile"))

    jobs = q.get_jobs(conn)
    assert jobs[0]["content_type"] == "irrelevant"
    assert jobs[0]["summary"] == ""
    assert client.chat.completions.create.call_count == 1
```

Finally, append a new test at the end of the file asserting the logging/streaming contract itself:

```python
def test_run_fetch_yields_progress_and_logs_each_line(conn, source, caplog):
    raw_jobs = [RawJob(url="http://example.com/job/1", title="ML Eng", company="Acme", raw_text="<p>We are hiring</p>")]
    client = _mock_client(
        '{"type": "job_posting", "reason": "full description"}',
        "Good ML role",
        '{"score": 0.9, "reasoning": "Great match"}',
    )

    with patch("app.pipeline.HttpFetcher") as MockFetcher, caplog.at_level("INFO", logger="job_seek"):
        MockFetcher.return_value.fetch.return_value = raw_jobs
        messages, result = _drain(run_fetch(source, conn, client, "llama3.2", "browser-profile"))

    assert any("Starting fetch" in m for m in messages)
    assert any("job_posting" in m for m in messages)
    assert any("Fetch complete" in m for m in messages)
    assert messages == [r.message for r in caplog.records]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_pipeline.py -v`
Expected: FAIL — `_drain` calls `next()` on what is currently a `FetchResult` object (not a generator), raising `TypeError: 'FetchResult' object is not an iterator` (or similar) on every test.

- [ ] **Step 3: Add logging configuration**

In `app/main.py`, add logging setup before the router includes:

```python
from __future__ import annotations
import logging
from fastapi import FastAPI
from app.routes import jobs, fetch, profile, scenarios, sources

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="Job Seek")
app.include_router(jobs.router)
app.include_router(fetch.router)
app.include_router(profile.router)
app.include_router(scenarios.router)
app.include_router(sources.router)
```

- [ ] **Step 4: Convert `run_fetch` to a generator**

Replace the full contents of `app/pipeline.py` with:

```python
from __future__ import annotations
import logging
import sqlite3
from dataclasses import dataclass
from typing import Generator
import openai
from app.db import queries as q
from app.ai.simplify import simplify
from app.ai.classify import classify
from app.ai.summarize import summarize
from app.ai.evaluate import evaluate
from app.fetchers.base import RawJob
from app.fetchers.http import HttpFetcher
from app.fetchers.playwright_base import PlaywrightFetcher
from app.fetchers.slack import SlackFetcher

logger = logging.getLogger("job_seek")


@dataclass
class FetchResult:
    source_id: int
    run_id: int
    jobs_found: int
    jobs_new: int
    error: str | None


def _make_fetcher(source: dict, profile_dir: str):
    ft = source["fetcher_type"]
    if ft == "http":
        return HttpFetcher(source)
    if ft == "slack":
        return SlackFetcher(source, profile_dir)
    return PlaywrightFetcher(source, profile_dir)


def _progress(msg: str) -> str:
    logger.info(msg)
    return msg


def run_fetch(
    source: dict,
    conn: sqlite3.Connection,
    client: openai.OpenAI,
    model: str,
    profile_dir: str,
) -> Generator[str, None, FetchResult]:
    run_id = q.start_fetch_run(conn, source["id"])
    yield _progress(f"Starting fetch for '{source['name']}' ({source['fetcher_type']})")
    try:
        fetcher = _make_fetcher(source, profile_dir)
        raw_jobs: list[RawJob] = fetcher.fetch()
        jobs_found = len(raw_jobs)
        jobs_new = 0

        yield _progress(f"Fetched {jobs_found} raw posting(s) from '{source['name']}'")

        profile = q.get_profile(conn)
        scenario = q.get_active_scenario(conn)
        criteria = q.get_criteria(conn, scenario["id"]) if scenario else []

        for i, raw in enumerate(raw_jobs, start=1):
            if q.url_exists(conn, raw.url):
                yield _progress(f"[{i}/{jobs_found}] Skipping duplicate: {raw.url}")
                continue
            job_id = q.insert_job(
                conn,
                source_id=source["id"],
                url=raw.url,
                title=raw.title,
                company=raw.company,
                raw_text=raw.raw_text,
            )
            jobs_new += 1
            simplified = simplify(raw.raw_text)
            is_slack = source["fetcher_type"] == "slack"
            content_type, _ = classify(client, model, simplified, is_slack=is_slack)
            yield _progress(f"[{i}/{jobs_found}] Classified as {content_type}: {raw.url}")

            if content_type in ("job_posting", "lead") and scenario:
                job_summary = summarize(client, model, simplified)
                score, reasoning = evaluate(client, model, profile, scenario, criteria, job_summary)
                q.update_job_pipeline(
                    conn, job_id,
                    simplified_content=simplified,
                    content_type=content_type,
                    summary=job_summary,
                    relevance_score=score,
                    score_reasoning=reasoning,
                    scenario_id=scenario["id"],
                )
                yield _progress(f"[{i}/{jobs_found}] Scored {score}: {raw.url}")
            else:
                q.update_job_pipeline(
                    conn, job_id,
                    simplified_content=simplified,
                    content_type=content_type,
                )

        q.complete_fetch_run(conn, run_id, jobs_found=jobs_found, jobs_new=jobs_new)
        yield _progress(f"Fetch complete for '{source['name']}': {jobs_new} new / {jobs_found} found")
        return FetchResult(source_id=source["id"], run_id=run_id, jobs_found=jobs_found, jobs_new=jobs_new, error=None)
    except Exception as exc:
        q.complete_fetch_run(conn, run_id, jobs_found=0, jobs_new=0, error=str(exc))
        yield _progress(f"Fetch failed for '{source['name']}': {exc}")
        return FetchResult(source_id=source["id"], run_id=run_id, jobs_found=0, jobs_new=0, error=str(exc))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_pipeline.py -v`
Expected: PASS — all 5 tests (4 updated + 1 new).

- [ ] **Step 6: Run the full suite to check for regressions, then commit**

Run: `.venv/bin/python -m pytest`
Expected: some failures in `tests/test_routes_fetch.py` (Task 2 fixes these) — everything else PASS.

```bash
git add app/main.py app/pipeline.py tests/test_pipeline.py
git commit -m "feat: stream fetch pipeline progress and log every step"
```

---

### Task 2: Stream the Fetch trigger route

**Files:**
- Modify: `app/routes/fetch.py`
- Test: `tests/test_routes_fetch.py`

**Interfaces:**
- Consumes: `run_fetch(source, conn, client, model, profile_dir)` generator from Task 1.
- Produces: `POST /fetch/{source_id}` now returns `StreamingResponse` (`media_type="text/plain"`) — body is newline-separated progress lines, no trailing HTML.

- [ ] **Step 1: Write the failing test**

Open `tests/test_routes_fetch.py`. Replace the whole file with:

```python
import pytest
from unittest.mock import patch, MagicMock
from app.db import queries as q
from app.pipeline import FetchResult


def _seed(conn):
    return q.insert_source(conn, "finn.no", "https://finn.no", "http")


def test_fetch_panel_returns_200(client, conn):
    _seed(conn)
    resp = client.get("/fetch")
    assert resp.status_code == 200
    assert "finn.no" in resp.text


def test_fetch_panel_empty_sources(client, conn):
    resp = client.get("/fetch")
    assert resp.status_code == 200


def _fake_run_fetch(*args, **kwargs):
    yield "Starting fetch for 'finn.no' (http)"
    yield "Fetched 3 raw posting(s) from 'finn.no'"
    yield "Fetch complete for 'finn.no': 2 new / 3 found"
    return FetchResult(source_id=1, run_id=1, jobs_found=3, jobs_new=2, error=None)


def test_post_fetch_streams_progress(client, conn):
    sid = _seed(conn)
    with patch("app.routes.fetch.run_fetch", side_effect=_fake_run_fetch):
        resp = client.post(f"/fetch/{sid}")
    assert resp.status_code == 200
    assert "Starting fetch" in resp.text
    assert "Fetch complete for 'finn.no': 2 new / 3 found" in resp.text


def test_post_fetch_unknown_source_returns_404(client, conn):
    resp = client.post("/fetch/999")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_routes_fetch.py -v`
Expected: `test_post_fetch_streams_progress` FAILs — the current route calls `run_fetch(...)` and renders a template with it directly (a generator object, not a `FetchResult`), so it will error or produce unexpected output rather than the expected progress text. The other three tests continue to pass.

- [ ] **Step 3: Rewrite `trigger_fetch` to stream**

Replace the full contents of `app/routes/fetch.py` with:

```python
from __future__ import annotations
import sqlite3
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from app.deps import get_db, get_ai_client, get_model, get_config
from app.db import queries as q
from app.pipeline import run_fetch
import openai

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/fetch", response_class=HTMLResponse)
def fetch_panel(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    sources = q.get_sources(conn)
    runs = q.get_recent_fetch_runs(conn)
    runs_by_source = {}
    for run in runs:
        sid = run["source_id"]
        if sid not in runs_by_source:
            runs_by_source[sid] = run
    return templates.TemplateResponse(
        request,
        "fetch/panel.html",
        {"sources": sources, "runs_by_source": runs_by_source},
    )


@router.post("/fetch/{source_id}")
def trigger_fetch(
    source_id: int,
    conn: sqlite3.Connection = Depends(get_db),
    client: openai.OpenAI = Depends(get_ai_client),
    model: str = Depends(get_model),
    config=Depends(get_config),
):
    source = q.get_source(conn, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    def stream():
        gen = run_fetch(source, conn, client, model, config.browser_profile_dir)
        try:
            while True:
                yield next(gen) + "\n"
        except StopIteration:
            pass

    return StreamingResponse(stream(), media_type="text/plain")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_routes_fetch.py -v`
Expected: PASS — all 4 tests.

- [ ] **Step 5: Run the full suite, then commit**

Run: `.venv/bin/python -m pytest`
Expected: PASS (Task 1's fetch-route failures are now resolved).

```bash
git add app/routes/fetch.py tests/test_routes_fetch.py
git commit -m "feat: stream fetch progress over the trigger endpoint"
```

---

### Task 3: Frontend progress helper, wired to the Fetch button

**Files:**
- Modify: `app/templates/base.html`
- Modify: `app/templates/fetch/panel.html`

**Interfaces:**
- Produces: any element with a `data-progress-url` attribute becomes clickable-and-streamed by the shared helper. An element that also carries `data-progress-target` (a CSS selector) gets that selector's `innerHTML` replaced with the stream's final `HTML:`-prefixed chunk; without it, the page reloads (`location.reload()`) once the stream ends.

There is no automated test for this task — this codebase has no JS test harness anywhere (consistent with the design spec's Testing Strategy section: UI interaction isn't unit tested elsewhere either). Verification is manual, done at the end of Step 2 and again in Task 6.

- [ ] **Step 1: Add the shared progress-streaming helper to `base.html`**

In `app/templates/base.html`, insert this `<script>` block immediately before the closing `</body>` tag (after `<main>{% block content %}{% endblock %}</main>`, keeping the existing htmx `<script>` tag in `<head>` untouched):

```html
  <script>
    (function () {
      function onClick(evt) {
        var el = evt.target.closest("[data-progress-url]");
        if (!el) return;
        evt.preventDefault();

        var url = el.getAttribute("data-progress-url");
        var targetSelector = el.getAttribute("data-progress-target");
        var progressEl = document.createElement("span");
        progressEl.style.marginLeft = "0.5rem";
        progressEl.style.color = "#666";
        progressEl.hidden = true;
        el.insertAdjacentElement("afterend", progressEl);

        var start = Date.now();
        var revealed = false;
        var lastLine = "";
        var tickTimer = null;
        var htmlChunk = null;

        function tick() {
          var elapsed = Math.round((Date.now() - start) / 1000);
          progressEl.textContent = elapsed + "s" + (lastLine ? " — " + lastLine : "");
        }

        var revealTimer = setTimeout(function () {
          revealed = true;
          progressEl.hidden = false;
          tick();
          tickTimer = setInterval(tick, 1000);
        }, 5000);

        function finish() {
          clearTimeout(revealTimer);
          if (tickTimer) clearInterval(tickTimer);
          progressEl.remove();
          if (targetSelector) {
            var target = document.querySelector(targetSelector);
            if (target && htmlChunk != null) target.innerHTML = htmlChunk;
          } else {
            location.reload();
          }
        }

        function handleLine(line) {
          if (!line) return;
          if (line.indexOf("HTML:") === 0) {
            htmlChunk = line.slice(5);
          } else {
            lastLine = line;
            if (revealed) tick();
          }
        }

        fetch(url, { method: "POST" }).then(function (response) {
          var reader = response.body.getReader();
          var decoder = new TextDecoder();
          var buffer = "";

          function pump() {
            return reader.read().then(function (result) {
              if (result.done) {
                if (buffer) handleLine(buffer);
                finish();
                return;
              }
              buffer += decoder.decode(result.value, { stream: true });
              var lines = buffer.split("\n");
              buffer = lines.pop();
              lines.forEach(handleLine);
              return pump();
            });
          }

          return pump();
        });
      }

      document.body.addEventListener("click", onClick);
    })();
  </script>
```

- [ ] **Step 2: Wire the Fetch button to the helper and drop the `last_result` banner**

In `app/templates/fetch/panel.html`, remove the banner block right after `<h1>Fetch</h1>`:

```html
{% if last_result %}
  <div style="padding:0.75rem; background:#d4edda; border-radius:4px; margin-bottom:1rem;">
    Fetched: {{ last_result.jobs_found }} found, {{ last_result.jobs_new }} new.
    {% if last_result.error %}<strong>Error: {{ last_result.error }}</strong>{% endif %}
  </div>
{% endif %}
```

so the file starts:

```html
{% extends "base.html" %}
{% block title %}Fetch — Job Seek{% endblock %}
{% block content %}
<h1>Fetch</h1>
{% if sources %}
```

Then replace the Fetch button cell:

```html
        <td style="padding:0.5rem;">
          {% if source.enabled %}
            <button class="btn"
              hx-post="/fetch/{{ source.id }}"
              hx-target="body"
              hx-swap="innerHTML"
              hx-indicator="#spinner-{{ source.id }}">
              Fetch
            </button>
            <span id="spinner-{{ source.id }}" class="htmx-indicator">⏳</span>
          {% else %}
            <span style="color:#999;">disabled</span>
          {% endif %}
        </td>
```

with:

```html
        <td style="padding:0.5rem;">
          {% if source.enabled %}
            <button class="btn" data-progress-url="/fetch/{{ source.id }}">Fetch</button>
          {% else %}
            <span style="color:#999;">disabled</span>
          {% endif %}
        </td>
```

- [ ] **Step 3: Run the backend test suite to confirm nothing broke**

Run: `.venv/bin/python -m pytest`
Expected: PASS (these are template-only changes; no Python test targets these templates directly, but `test_fetch_panel_returns_200` renders `fetch/panel.html` and must still succeed).

- [ ] **Step 4: Manually verify the stream is actually incremental**

Start the app (`.venv/bin/uvicorn app.main:app --port 8734 &`) against a source that will take a few seconds (or temporarily point a source's `url` at something slow), then run:

```bash
curl -N -X POST http://127.0.0.1:8734/fetch/<source_id>
```

Expected: lines print to the terminal one at a time as they're produced (not all at once at the end) — confirming the response really streams rather than buffering. Stop the server afterward.

- [ ] **Step 5: Commit**

```bash
git add app/templates/base.html app/templates/fetch/panel.html
git commit -m "feat: show live fetch progress in the browser after 5s"
```

---

### Task 4: Stream the Re-evaluate jobs route

**Files:**
- Modify: `app/routes/scenarios.py`
- Modify: `app/templates/scenarios/index.html`
- Test: `tests/test_routes_scenarios.py`

**Interfaces:**
- Produces: `POST /scenarios/{scenario_id}/reevaluate` now returns `StreamingResponse` (`media_type="text/plain"`) — progress lines only, no final HTML. Client reloads the page when the stream ends (no `data-progress-target` on this button).

- [ ] **Step 1: Write the failing test**

There is currently no test for `reevaluate_jobs` in `tests/test_routes_scenarios.py`. Add these imports to the top of the file (alongside the existing ones):

```python
from app.db import queries as q
```

(already present) — then add, near the bottom of the file:

```python
def test_reevaluate_streams_progress_and_updates_jobs(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.set_active_scenario(conn, sid)
    q.insert_criterion(conn, sid, "Must be remote", "must")
    source_id = q.insert_source(conn, "s", "http://x", "http")
    job_id = q.insert_job(conn, source_id=source_id, url="http://job/1", title="ML Eng", company="C", raw_text="r")
    q.update_job_pipeline(conn, job_id, simplified_content="clean", content_type="job_posting", scenario_id=sid)

    with patch("app.routes.scenarios.summarize", return_value="Updated summary"), \
         patch("app.routes.scenarios.evaluate", return_value=(0.75, "Good match")):
        resp = client.post(f"/scenarios/{sid}/reevaluate")

    assert resp.status_code == 200
    assert "Re-evaluating 1 job(s)" in resp.text
    assert "Re-evaluation complete" in resp.text
    job = q.get_job(conn, job_id)
    assert job["relevance_score"] == pytest.approx(0.75)
    assert job["summary"] == "Updated summary"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_routes_scenarios.py::test_reevaluate_streams_progress_and_updates_jobs -v`
Expected: FAIL — the current route renders `scenarios/index.html` (an HTML page), so `"Re-evaluating 1 job(s)"` won't be in `resp.text`.

- [ ] **Step 3: Rewrite `reevaluate_jobs` to stream**

In `app/routes/scenarios.py`, add these imports at the top (alongside the existing ones):

```python
from __future__ import annotations
import logging
import sqlite3
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from app.deps import get_db, get_ai_client, get_model
from app.db import queries as q
from app.ai.refine import propose_criteria
from app.ai.summarize import summarize
from app.ai.evaluate import evaluate
import openai

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
logger = logging.getLogger("job_seek")
```

Then replace the `reevaluate_jobs` function:

```python
@router.post("/scenarios/{scenario_id}/reevaluate", response_class=HTMLResponse)
def reevaluate_jobs(
    scenario_id: int,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    client: openai.OpenAI = Depends(get_ai_client),
    model: str = Depends(get_model),
):
    scenario = dict(conn.execute("SELECT * FROM scenarios WHERE id = ?", (scenario_id,)).fetchone())
    criteria = q.get_criteria(conn, scenario_id)
    profile = q.get_profile(conn)
    jobs = q.get_jobs(conn, status="new")
    evaluated = [j for j in jobs if j["scenario_id"] == scenario_id and j["content_type"] in ("job_posting", "lead")]
    for job in evaluated:
        new_summary = summarize(client, model, job["simplified_content"]) if job["simplified_content"] else job["summary"]
        score, reasoning = evaluate(client, model, profile, scenario, criteria, new_summary)
        q.update_job_pipeline(
            conn, job["id"],
            simplified_content=job["simplified_content"],
            content_type=job["content_type"],
            summary=new_summary,
            relevance_score=score,
            score_reasoning=reasoning,
            scenario_id=scenario_id,
        )
    ctx = _scenarios_context(conn)
    ctx["reevaluated_count"] = len(evaluated)
    return templates.TemplateResponse(request, "scenarios/index.html", ctx)
```

with:

```python
@router.post("/scenarios/{scenario_id}/reevaluate")
def reevaluate_jobs(
    scenario_id: int,
    conn: sqlite3.Connection = Depends(get_db),
    client: openai.OpenAI = Depends(get_ai_client),
    model: str = Depends(get_model),
):
    scenario = _get_scenario_or_404(conn, scenario_id)
    criteria = q.get_criteria(conn, scenario_id)
    profile = q.get_profile(conn)
    jobs = q.get_jobs(conn, status="new")
    evaluated = [j for j in jobs if j["scenario_id"] == scenario_id and j["content_type"] in ("job_posting", "lead")]

    def stream():
        total = len(evaluated)
        msg = f"Re-evaluating {total} job(s) for scenario '{scenario['name']}'"
        logger.info(msg)
        yield msg + "\n"
        for i, job in enumerate(evaluated, start=1):
            new_summary = summarize(client, model, job["simplified_content"]) if job["simplified_content"] else job["summary"]
            score, reasoning = evaluate(client, model, profile, scenario, criteria, new_summary)
            q.update_job_pipeline(
                conn, job["id"],
                simplified_content=job["simplified_content"],
                content_type=job["content_type"],
                summary=new_summary,
                relevance_score=score,
                score_reasoning=reasoning,
                scenario_id=scenario_id,
            )
            msg = f"[{i}/{total}] Re-scored {score}: {job['title'] or job['url']}"
            logger.info(msg)
            yield msg + "\n"
        msg = f"Re-evaluation complete: {total} job(s) updated"
        logger.info(msg)
        yield msg + "\n"

    return StreamingResponse(stream(), media_type="text/plain")
```

Note `_get_scenario_or_404` is already defined earlier in this file (added when scenario name/description editing was built) — it returns a plain `dict`, same shape as the `dict(conn.execute(...).fetchone())` call it replaces.

- [ ] **Step 4: Update the Re-evaluate button and drop the now-dead banner**

In `app/templates/scenarios/index.html`, remove this block (its `reevaluated_count` context variable is never set anymore):

```html
{% if reevaluated_count is defined %}
<div style="padding:0.5rem; background:#d4edda; border-radius:4px; margin-bottom:1rem;">Re-evaluated {{ reevaluated_count }} job(s).</div>
{% endif %}
```

Then replace:

```html
    <form method="post" action="/scenarios/{{ scenario.id }}/reevaluate" style="display:inline;">
      <button type="submit" class="btn">Re-evaluate jobs</button>
    </form>
```

with:

```html
    <button class="btn" data-progress-url="/scenarios/{{ scenario.id }}/reevaluate">Re-evaluate jobs</button>
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_routes_scenarios.py -v`
Expected: PASS — all tests including the new one.

- [ ] **Step 6: Run the full suite, then commit**

Run: `.venv/bin/python -m pytest`
Expected: PASS.

```bash
git add app/routes/scenarios.py app/templates/scenarios/index.html tests/test_routes_scenarios.py
git commit -m "feat: stream re-evaluate-jobs progress"
```

---

### Task 5: Stream the Refine criteria route

**Files:**
- Modify: `app/routes/scenarios.py`
- Modify: `app/templates/scenarios/index.html`
- Test: `tests/test_routes_scenarios.py`

**Interfaces:**
- Produces: `POST /scenarios/{scenario_id}/refine` now returns `StreamingResponse` (`media_type="text/plain"`) — progress lines, then one final chunk prefixed `HTML:` containing the rendered `_proposals.html` fragment (client strips the prefix and swaps it into `#proposals-area-{scenario_id}`).

- [ ] **Step 1: Update the existing test for the new response shape**

In `tests/test_routes_scenarios.py`, replace:

```python
def test_refine_returns_proposals(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Must be remote", "must")
    proposals = [CriterionProposal(text="Must be senior", weight="must", action="add")]
    with patch("app.routes.scenarios.propose_criteria", return_value=proposals):
        resp = client.post(f"/scenarios/{sid}/refine")
    assert resp.status_code == 200
    assert "Must be senior" in resp.text
```

with:

```python
def test_refine_returns_proposals(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Must be remote", "must")
    proposals = [CriterionProposal(text="Must be senior", weight="must", action="add")]
    with patch("app.routes.scenarios.propose_criteria", return_value=proposals):
        resp = client.post(f"/scenarios/{sid}/refine")
    assert resp.status_code == 200
    assert "Requesting criteria proposals" in resp.text
    assert "HTML:" in resp.text
    assert "Must be senior" in resp.text
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_routes_scenarios.py::test_refine_returns_proposals -v`
Expected: FAIL — current response is only the rendered fragment, with no `"Requesting criteria proposals"` progress line and no `"HTML:"` prefix.

- [ ] **Step 3: Rewrite `refine_criteria` to stream**

In `app/routes/scenarios.py`, replace:

```python
@router.post("/scenarios/{scenario_id}/refine", response_class=HTMLResponse)
def refine_criteria(
    scenario_id: int,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    client: openai.OpenAI = Depends(get_ai_client),
    model: str = Depends(get_model),
):
    scenario = dict(conn.execute("SELECT * FROM scenarios WHERE id = ?", (scenario_id,)).fetchone())
    existing = q.get_criteria(conn, scenario_id)
    notes = q.get_recent_feedback_notes(conn, scenario_id)
    proposals = propose_criteria(client, model, scenario, existing, notes)
    return templates.TemplateResponse(
        request,
        "scenarios/_proposals.html",
        {"proposals": proposals, "scenario_id": scenario_id},
    )
```

with:

```python
@router.post("/scenarios/{scenario_id}/refine")
def refine_criteria(
    scenario_id: int,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    client: openai.OpenAI = Depends(get_ai_client),
    model: str = Depends(get_model),
):
    scenario = _get_scenario_or_404(conn, scenario_id)
    existing = q.get_criteria(conn, scenario_id)
    notes = q.get_recent_feedback_notes(conn, scenario_id)

    def stream():
        msg = f"Requesting criteria proposals for '{scenario['name']}' from LLM"
        logger.info(msg)
        yield msg + "\n"
        proposals = propose_criteria(client, model, scenario, existing, notes)
        msg = f"Received {len(proposals)} proposal(s)"
        logger.info(msg)
        yield msg + "\n"
        html = templates.get_template("scenarios/_proposals.html").render(
            request=request, proposals=proposals, scenario_id=scenario_id
        )
        yield "HTML:" + html

    return StreamingResponse(stream(), media_type="text/plain")
```

- [ ] **Step 4: Add `data-progress-target` to the Refine button**

In `app/templates/scenarios/index.html`, replace:

```html
    <button class="btn"
      hx-post="/scenarios/{{ scenario.id }}/refine"
      hx-target="#proposals-area-{{ scenario.id }}"
      hx-swap="innerHTML">
      Refine criteria from feedback
    </button>
```

with:

```html
    <button class="btn"
      data-progress-url="/scenarios/{{ scenario.id }}/refine"
      data-progress-target="#proposals-area-{{ scenario.id }}">
      Refine criteria from feedback
    </button>
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_routes_scenarios.py -v`
Expected: PASS — all tests.

- [ ] **Step 6: Run the full suite, then commit**

Run: `.venv/bin/python -m pytest`
Expected: PASS.

```bash
git add app/routes/scenarios.py app/templates/scenarios/index.html tests/test_routes_scenarios.py
git commit -m "feat: stream refine-criteria progress and fragment"
```

---

### Task 6: End-to-end verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `.venv/bin/python -m pytest`
Expected: PASS, zero failures.

- [ ] **Step 2: Manually verify against the real database**

Start the app: `.venv/bin/uvicorn app.main:app --port 8734 &`

Watch the terminal output while exercising each of the three actions from the browser (or via `curl -N -X POST` against a real source/scenario ID from `job-seek.db`, using values discovered via `sqlite3 job-seek.db "SELECT id,name FROM sources;"` / `"SELECT id,name FROM scenarios;"`):

- `POST /fetch/<source_id>` — confirm log lines appear in the uvicorn console as the fetch runs, matching the lines seen in the response body.
- `POST /scenarios/<scenario_id>/reevaluate` — same check.
- `POST /scenarios/<scenario_id>/refine` — same check, and confirm the response ends with an `HTML:`-prefixed fragment.

Expected: console log lines and streamed response lines match 1:1 for each action; no exceptions in the console.

Stop the server: `pkill -f "uvicorn app.main:app --port 8734"`

- [ ] **Step 3: Open the app in a browser and click each button**

Confirm: for a fast action (e.g. Refine with few criteria), no progress indicator ever appears — behavior looks identical to before. For a slow action (Fetch against a real source, or temporarily add an `time.sleep` for a manual check), confirm the elapsed-seconds counter appears after ~5 seconds next to the button and updates every second, and that the page correctly reloads (Fetch/Re-evaluate) or the proposals fragment appears (Refine) once the action finishes.

- [ ] **Step 4: Commit if any fixups were needed during verification**

```bash
git add -A
git commit -m "fix: address issues found during end-to-end verification"
```

(Skip this step if verification found nothing to fix.)
