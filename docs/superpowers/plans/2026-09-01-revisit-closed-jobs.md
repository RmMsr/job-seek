# Revisit Closed Jobs — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Re-fetch a job's original URL on demand; if it can no longer be retrieved (reproducibly), append a plain-language note and move the job to Trash.

**Architecture:** A new `run_revisit_job` generator in `app/pipeline.py` reuses the existing single-URL fetch stack (`fetch_url_html` → Playwright fallback → `simplify` → `classify`). One background task kind `jobs_revisit` drives it from three entry points: a sweep button on `/fetch`, a per-job button, and an automatic enqueue when a job is accepted/rejected. No schema change.

**Tech Stack:** Python 3.14, FastAPI, SQLite, Jinja2, htmx, pytest.

## Global Constraints

- Execute everything through the project venv: `python -m pytest …` (not `uv run` — the sandbox cache is read-only).
- Commit after each task (project rule: small incremental commits).
- Personal single-instance app: no backwards-compat shims, no defensive migration branching.
- Never hardcode real Slack workspace/team/channel IDs in source or tests.
- Note copy is user-facing and must be plain language, not technical (no raw "HTTP 404" in the stored note).
- `feedback_note` closure line format, verbatim: `[YYYY-MM-DD] Moved to trash on revisit — <reason>.`
- Reason text by failure, verbatim:
  - HTTP 404 / 410 → `this job can no longer be found`
  - other HTTP / network error → `this job posting could no longer be reached`
  - no extractable content → `this job posting no longer shows any content`
  - re-classified `irrelevant` → `this page is no longer a job posting`
  - re-classified `error` → `this job posting is no longer accessible`
- Change threshold: `difflib.SequenceMatcher(None, old[:20000], new[:20000]).ratio() < 0.95` means "changed".
- Sweep hard cap: 200 jobs per run; emit a log line when the cap truncates the set.

---

## File Structure

- `app/db/queries.py` — add `update_job_raw_text`, `get_revisitable_jobs`, `mark_job_closed`; expose `source_fetcher_type` on job rows via `_GATE_SELECT`.
- `app/pipeline.py` — extract `_evaluate_posting` from `_ingest_posting`; add `RevisitOutcome` + `run_revisit_job`.
- `app/routes/jobs.py` — `jobs_revisit` task kind, `POST /jobs/{job_id}/revisit`, status-change enqueue in `job_feedback` + `job_bulk_feedback`.
- `app/routes/fetch.py` — `POST /revisit/all`.
- `app/templates/fetch/panel.html` — "Revisit open jobs" button.
- `app/templates/jobs/_feedback.html` — per-job "Revisit" button (hidden for Slack sources).
- `app/templates/jobs/_revisit_notice.html` — new notice partial.
- Tests: `tests/test_queries.py`, `tests/test_pipeline.py`, new `tests/test_revisit.py`, `tests/test_routes_jobs.py`, `tests/test_routes_fetch.py`.

---

## Task 1: Query helpers + expose `source_fetcher_type`

**Files:**
- Modify: `app/db/queries.py`
- Test: `tests/test_queries.py`

**Interfaces:**
- Produces:
  - `update_job_raw_text(conn, job_id: int, raw_text: str) -> None`
  - `get_revisitable_jobs(conn) -> list[dict]` — jobs with `status IN ('new','accepted','rejected')` whose source `fetcher_type != 'slack'`, ordered by `jobs.id`.
  - `mark_job_closed(conn, job_id: int, reason: str) -> None` — appends the closure line to `feedback_note` (preserving any existing note), sets `status='trash'`, `feedback_handled_at`, `status_changed_at`.
  - Every job dict from `get_job` / `get_jobs` / `get_revisitable_jobs` now carries `source_fetcher_type: str`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_queries.py`:

```python
def test_get_job_exposes_source_fetcher_type(conn):
    sid = q.insert_source(conn, "board", "http://example.com", "generic_listing")
    jid = q.insert_job(conn, source_id=sid, url="http://example.com/a", title="A",
                       company="", raw_text="x")
    assert q.get_job(conn, jid)["source_fetcher_type"] == "generic_listing"


def test_update_job_raw_text_replaces_only_raw_text(conn):
    sid = q.insert_source(conn, "board", "http://example.com", "generic_listing")
    jid = q.insert_job(conn, source_id=sid, url="http://example.com/a", title="A",
                       company="Acme", raw_text="old")
    q.update_job_raw_text(conn, jid, "new body")
    row = q.get_job(conn, jid)
    assert row["raw_text"] == "new body"
    assert row["title"] == "A" and row["company"] == "Acme"


def test_get_revisitable_jobs_excludes_slack_and_trash(conn):
    gid = q.insert_source(conn, "board", "http://example.com", "generic_listing")
    slk = q.insert_source(conn, "slk", "http://x.slack.com/c", "slack")
    keep = q.insert_job(conn, source_id=gid, url="http://example.com/keep", title="",
                        company="", raw_text="x")
    q.insert_job(conn, source_id=slk, url="http://x.slack.com/c#1", title="",
                 company="", raw_text="x")
    trashed = q.insert_job(conn, source_id=gid, url="http://example.com/t", title="",
                           company="", raw_text="x")
    q.update_job_feedback(conn, trashed, "trash", None)
    ids = [j["id"] for j in q.get_revisitable_jobs(conn)]
    assert ids == [keep]


def test_mark_job_closed_appends_note_and_trashes(conn):
    sid = q.insert_source(conn, "board", "http://example.com", "generic_listing")
    jid = q.insert_job(conn, source_id=sid, url="http://example.com/a", title="A",
                       company="", raw_text="x")
    q.update_job_feedback(conn, jid, "accepted", "Loved the mission")
    q.mark_job_closed(conn, jid, "this job can no longer be found")
    row = q.get_job(conn, jid)
    assert row["status"] == "trash"
    assert row["feedback_note"].startswith("Loved the mission\n\n[")
    assert row["feedback_note"].rstrip().endswith(
        "Moved to trash on revisit — this job can no longer be found.")
    assert row["feedback_handled_at"] is not None
    assert row["status_changed_at"] is not None


def test_mark_job_closed_with_no_existing_note(conn):
    sid = q.insert_source(conn, "board", "http://example.com", "generic_listing")
    jid = q.insert_job(conn, source_id=sid, url="http://example.com/a", title="A",
                       company="", raw_text="x")
    q.mark_job_closed(conn, jid, "this page is no longer a job posting")
    note = q.get_job(conn, jid)["feedback_note"]
    assert note.startswith("[") and "no longer a job posting." in note
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_queries.py -k "source_fetcher_type or update_job_raw_text or revisitable or mark_job_closed" -v`
Expected: FAIL (`KeyError: 'source_fetcher_type'`, `AttributeError: … has no attribute 'update_job_raw_text'`, etc.)

- [ ] **Step 3: Implement**

In `app/db/queries.py`:

Add near the top imports:
```python
from datetime import datetime, timezone
```

In `_GATE_SELECT`, add the column right after `jobs.*,`:
```python
_GATE_SELECT = """
    jobs.*,
    (SELECT fetcher_type FROM sources WHERE sources.id = jobs.source_id) AS source_fetcher_type,
    gate.passed_count AS passed_gate_count,
    ...
```
(leave the rest of `_GATE_SELECT` unchanged)

Add these functions (near `reset_job` / `update_job_feedback`):
```python
def update_job_raw_text(conn: sqlite3.Connection, job_id: int, raw_text: str) -> None:
    conn.execute("UPDATE jobs SET raw_text = ? WHERE id = ?", (raw_text, job_id))
    conn.commit()


def get_revisitable_jobs(conn: sqlite3.Connection) -> list[dict]:
    sql = f"""SELECT {_GATE_SELECT} {_GATE_JOIN}
        WHERE jobs.status IN ('new', 'accepted', 'rejected')
          AND (SELECT fetcher_type FROM sources WHERE sources.id = jobs.source_id) != 'slack'
        ORDER BY jobs.id"""
    return _rows_to_dicts(conn.execute(sql).fetchall())


def mark_job_closed(conn: sqlite3.Connection, job_id: int, reason: str) -> None:
    row = conn.execute("SELECT feedback_note FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        return
    today = datetime.now(timezone.utc).date().isoformat()
    line = f"[{today}] Moved to trash on revisit — {reason}."
    old = (row["feedback_note"] or "").strip()
    note = f"{old}\n\n{line}" if old else line
    conn.execute(
        "UPDATE jobs SET status = 'trash', feedback_note = ?, "
        "feedback_handled_at = datetime('now'), status_changed_at = datetime('now') "
        "WHERE id = ?",
        (note, job_id),
    )
    conn.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_queries.py -k "source_fetcher_type or update_job_raw_text or revisitable or mark_job_closed" -v`
Expected: PASS

- [ ] **Step 5: Full queries + schema regression**

Run: `python -m pytest tests/test_queries.py tests/test_schema.py tests/test_routes_jobs.py -q`
Expected: PASS (the new `_GATE_SELECT` column must not break existing row rendering)

- [ ] **Step 6: Commit**

```bash
git add app/db/queries.py tests/test_queries.py
git commit -m "feat: revisit query helpers + expose source_fetcher_type on job rows"
```

---

## Task 2: Extract `_evaluate_posting` from `_ingest_posting`

**Files:**
- Modify: `app/pipeline.py:58-117`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Produces:
  ```python
  def _evaluate_posting(conn, client, model, job_id, *, simplified, content_type,
                        fallback_title, is_slack, profile, scenarios, url,
                        progress_prefix="", preserve_existing_metadata=False
                        ) -> Generator[str, None, None]
  ```
  Runs summarize → `update_job_pipeline` → per-scenario `evaluate`/`upsert_job_score` → (if gate passed) `assess_fit`/`update_job_fit` → `mark_job_evaluation_complete`. Does **not** read or write `jobs.status`.
- Consumes: nothing new.

This is a pure refactor — existing pipeline tests must stay green.

- [ ] **Step 1: Write the characterization test**

Add to `tests/test_pipeline.py`:

```python
def test_evaluate_posting_scores_without_touching_status(conn, source):
    from app.pipeline import _evaluate_posting
    jid = q.insert_job(conn, source_id=source["id"], url="http://example.com/x",
                       title="orig", company="", raw_text="raw")
    q.update_job_feedback(conn, jid, "accepted", None)
    client = _mock_client(
        classify_resp='{"type": "job_posting", "reason": "ok"}',
        summarize_resp='{"title": "ML Engineer", "headline": "h", "summary": "s", "company": "Acme", "posted_date": ""}',
        evaluate_resp='{"score": 0.9, "reasoning": "great"}',
    )
    _drain(_evaluate_posting(
        conn, client, "llama3.2", jid, simplified="clean text",
        content_type="job_posting", fallback_title="orig", is_slack=False,
        profile=q.get_profile(conn), scenarios=q.get_scenarios(conn),
        url="http://example.com/x",
    ))
    row = q.get_job(conn, jid)
    assert row["status"] == "accepted"          # untouched
    assert row["summary"] == "s"
    assert row["evaluation_completed_at"] is not None
    assert q.get_job_scores(conn, jid)[0]["relevance_score"] == 0.9
```

- [ ] **Step 2: Run it — expect ImportError**

Run: `python -m pytest tests/test_pipeline.py::test_evaluate_posting_scores_without_touching_status -v`
Expected: FAIL (`cannot import name '_evaluate_posting'`)

- [ ] **Step 3: Refactor `_ingest_posting`**

In `app/pipeline.py`, replace lines 58–117 (the current `_ingest_posting`) with:

```python
def _evaluate_posting(
    conn: sqlite3.Connection,
    client: openai.OpenAI,
    model: str,
    job_id: int,
    *,
    simplified: str,
    content_type: str,
    fallback_title: str,
    is_slack: bool,
    profile: str,
    scenarios: list[dict],
    url: str,
    progress_prefix: str = "",
    preserve_existing_metadata: bool = False,
) -> Generator[str, None, None]:
    job_summary = summarize(
        client, model, simplified, content_type=content_type, raw_passthrough=is_slack,
        today=datetime.now(timezone.utc).date(),
    )
    q.update_job_pipeline(
        conn, job_id,
        simplified_content=simplified,
        content_type=content_type,
        title=job_summary.title or fallback_title,
        headline=job_summary.headline,
        summary=job_summary.summary,
        company="" if preserve_existing_metadata else job_summary.company,
        published_at="" if preserve_existing_metadata else job_summary.posted_date,
    )
    passed_gate = False
    for scenario in scenarios:
        criteria = q.get_criteria(conn, scenario["id"])
        score, reasoning = evaluate(client, model, scenario, criteria, job_summary.summary)
        version_hash = compute_version_hash(scenario, criteria)
        q.upsert_job_score(conn, job_id, scenario["id"], score, reasoning, version_hash)
        if score >= scenario["gate_threshold"]:
            passed_gate = True
        yield _progress(f"{progress_prefix}Scored {score} for '{scenario['name']}': {url}")
    if passed_gate:
        result = assess_fit(client, model, profile, job_summary.summary)
        q.update_job_fit(
            conn, job_id,
            result["interest"], result["interest_reasoning"],
            result["attainability"], result["attainability_reasoning"],
            compute_profile_hash(profile),
        )
        yield _progress(
            f"{progress_prefix}Fit {result['interest']:.2f}/{result['attainability']:.2f}: {url}"
        )
    q.mark_job_evaluation_complete(conn, job_id)


def _ingest_posting(
    conn: sqlite3.Connection,
    client: openai.OpenAI,
    model: str,
    job_id: int,
    raw_text: str,
    fallback_title: str,
    is_slack: bool,
    profile: str,
    scenarios: list[dict],
    *,
    url: str,
    progress_prefix: str = "",
    preserve_existing_metadata: bool = False,
) -> Generator[str, None, None]:
    simplified = simplify(raw_text)
    content_type, _ = classify(client, model, simplified, is_slack=is_slack)
    yield _progress(f"{progress_prefix}Classified as {content_type}: {url}")

    if content_type in ("job_posting", "lead"):
        yield from _evaluate_posting(
            conn, client, model, job_id,
            simplified=simplified, content_type=content_type,
            fallback_title=fallback_title, is_slack=is_slack,
            profile=profile, scenarios=scenarios, url=url,
            progress_prefix=progress_prefix,
            preserve_existing_metadata=preserve_existing_metadata,
        )
    elif content_type == "irrelevant":
        q.delete_job(conn, job_id)
    else:
        q.update_job_pipeline(conn, job_id, simplified_content=simplified, content_type=content_type)
```

- [ ] **Step 4: Run the full pipeline + routes suites**

Run: `python -m pytest tests/test_pipeline.py tests/test_routes_jobs.py -q`
Expected: PASS (all existing tests + the new one)

- [ ] **Step 5: Commit**

```bash
git add app/pipeline.py tests/test_pipeline.py
git commit -m "refactor: extract _evaluate_posting from _ingest_posting"
```

---

## Task 3: `run_revisit_job` + `RevisitOutcome`

**Files:**
- Modify: `app/pipeline.py`
- Test: `tests/test_revisit.py` (new)

**Interfaces:**
- Consumes: `_evaluate_posting` (Task 2), `q.update_job_raw_text` / `q.mark_job_closed` (Task 1).
- Produces:
  ```python
  @dataclass
  class RevisitOutcome:
      verdict: str        # "closed" | "changed" | "unchanged" | "skipped"
      reason: str | None   # plain-language reason for "closed", else None

  def run_revisit_job(conn, client, model, job: dict, scenarios: list[dict],
                      profile: str, *, progress_prefix: str = "",
                      ) -> Generator[str, None, RevisitOutcome]
  ```

- [ ] **Step 1: Write the failing tests**

Create `tests/test_revisit.py`:

```python
import sqlite3
import pytest
from unittest.mock import patch
from app.db.schema import init_db
from app.db import queries as q
from app.pipeline import run_revisit_job, RevisitOutcome
from app.fetchers.content import FetchError
from tests.test_pipeline import _mock_client, _drain


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_db(c)
    yield c
    c.close()


@pytest.fixture
def board(conn):
    sid = q.insert_source(conn, "board", "http://example.com", "generic_listing")
    q.insert_scenario(conn, "Remote ML", "")
    q.upsert_profile(conn, "I am an ML engineer.")
    return q.get_source(conn, sid)


def _job(conn, board, *, raw_text="original body text", status="new"):
    jid = q.insert_job(conn, source_id=board["id"], url="http://example.com/j1",
                       title="ML Engineer", company="Acme", raw_text=raw_text)
    if status != "new":
        q.update_job_feedback(conn, jid, status, None)
    return q.get_job(conn, jid)


def _run(conn, job, client=None):
    client = client or _mock_client("{}", "{}", "{}")
    return _drain(run_revisit_job(conn, client, "llama3.2", job,
                                  q.get_scenarios(conn), q.get_profile(conn)))


def test_http_error_retried_once_then_closed(conn, board):
    job = _job(conn, board)
    with patch("app.pipeline.fetch_url_html", side_effect=FetchError("HTTP 404")) as m, \
         patch("app.pipeline.time.sleep"):
        _, outcome = _run(conn, job)
    assert m.call_count == 2
    assert outcome.verdict == "closed"
    assert outcome.reason == "this job can no longer be found"
    assert q.get_job(conn, job["id"])["status"] == "trash"


def test_transient_error_that_clears_on_retry_is_not_closed(conn, board):
    job = _job(conn, board, raw_text="original body text")
    html = "<html><body>" + "long real posting content " * 40 + "</body></html>"
    with patch("app.pipeline.fetch_url_html",
               side_effect=[FetchError("HTTP 503"), html]), \
         patch("app.pipeline.time.sleep"):
        client = _mock_client(
            '{"type": "job_posting", "reason": "ok"}',
            '{"title": "ML Engineer", "headline": "h", "summary": "s", "company": "Acme", "posted_date": ""}',
            '{"score": 0.9, "reasoning": "ok"}')
        _, outcome = _run(conn, job, client)
    assert outcome.verdict in ("unchanged", "changed")
    assert q.get_job(conn, job["id"])["status"] == "new"


def test_thin_page_and_thin_render_closes(conn, board):
    job = _job(conn, board)
    with patch("app.pipeline.fetch_url_html", return_value="<html><body>x</body></html>"), \
         patch("app.pipeline.render_html", return_value=None):
        _, outcome = _run(conn, job)
    assert outcome.verdict == "closed"
    assert outcome.reason == "this job posting no longer shows any content"


def test_reclassified_irrelevant_closes(conn, board):
    job = _job(conn, board)
    html = "<html><body>" + "some page content here " * 40 + "</body></html>"
    client = _mock_client('{"type": "irrelevant", "reason": "nope"}', "{}", "{}")
    with patch("app.pipeline.fetch_url_html", return_value=html):
        _, outcome = _run(conn, job, client)
    assert outcome.verdict == "closed"
    assert outcome.reason == "this page is no longer a job posting"


def test_reclassified_error_closes(conn, board):
    job = _job(conn, board)
    html = "<html><body>" + "please sign in to view " * 40 + "</body></html>"
    client = _mock_client('{"type": "error", "reason": "denied"}', "{}", "{}")
    with patch("app.pipeline.fetch_url_html", return_value=html):
        _, outcome = _run(conn, job, client)
    assert outcome.verdict == "closed"
    assert outcome.reason == "this job posting is no longer accessible"


def test_unchanged_content_only_refreshes_raw_text(conn, board):
    body = "the exact same posting body " * 40
    job = _job(conn, board, raw_text=body, status="accepted")
    q.upsert_job_score(conn, job["id"], q.get_scenarios(conn)[0]["id"], 0.4, "old", "h")
    html = f"<html><body>{body}</body></html>"
    client = _mock_client('{"type": "job_posting", "reason": "ok"}', "{}", "{}")
    with patch("app.pipeline.fetch_url_html", return_value=html):
        _, outcome = _run(conn, job, client)
    assert outcome.verdict == "unchanged"
    row = q.get_job(conn, job["id"])
    assert row["status"] == "accepted"
    assert q.get_job_scores(conn, job["id"])[0]["relevance_score"] == 0.4  # untouched


def test_changed_content_rescored_status_preserved(conn, board):
    job = _job(conn, board, raw_text="tiny old body", status="accepted")
    html = "<html><body>" + "a completely different and much longer posting now " * 40 + "</body></html>"
    client = _mock_client(
        '{"type": "job_posting", "reason": "ok"}',
        '{"title": "ML Engineer", "headline": "h2", "summary": "s2", "company": "Acme", "posted_date": ""}',
        '{"score": 0.95, "reasoning": "now great"}')
    with patch("app.pipeline.fetch_url_html", return_value=html):
        _, outcome = _run(conn, job, client)
    assert outcome.verdict == "changed"
    row = q.get_job(conn, job["id"])
    assert row["status"] == "accepted"
    assert row["summary"] == "s2"
    assert q.get_job_scores(conn, job["id"])[0]["relevance_score"] == 0.95


def test_slack_source_is_skipped(conn):
    slk = q.insert_source(conn, "slk", "http://x.slack.com/c", "slack")
    q.upsert_profile(conn, "p")
    jid = q.insert_job(conn, source_id=slk, url="http://x.slack.com/c#1",
                       title="t", company="", raw_text="body")
    job = q.get_job(conn, jid)
    with patch("app.pipeline.fetch_url_html") as m:
        _, outcome = _drain(run_revisit_job(conn, _mock_client("{}", "{}", "{}"),
                                            "llama3.2", job, [], "p"))
    assert outcome.verdict == "skipped"
    m.assert_not_called()
    assert q.get_job(conn, jid)["status"] == "new"
```

- [ ] **Step 2: Run — expect ImportError / failures**

Run: `python -m pytest tests/test_revisit.py -v`
Expected: FAIL (`cannot import name 'run_revisit_job'`)

- [ ] **Step 3: Implement in `app/pipeline.py`**

Add imports near the top:
```python
import difflib
import time
from app.fetchers.content import (
    fetch_url_html, extract_text, has_enough_text, FetchError,
)
from app.fetchers.playwright_pool import render_html
```

Add after `_ingest_posting`:
```python
@dataclass
class RevisitOutcome:
    verdict: str          # "closed" | "changed" | "unchanged" | "skipped"
    reason: str | None = None


_CHANGE_RATIO_THRESHOLD = 0.95
_REVISIT_RETRY_DELAY_SECONDS = 3.0


def _closed_reason_for_fetch_error(exc: FetchError) -> str:
    msg = str(exc)
    if "404" in msg or "410" in msg:
        return "this job can no longer be found"
    return "this job posting could no longer be reached"


def run_revisit_job(
    conn: sqlite3.Connection,
    client: openai.OpenAI,
    model: str,
    job: dict,
    scenarios: list[dict],
    profile: str,
    *,
    progress_prefix: str = "",
) -> Generator[str, None, RevisitOutcome]:
    url = job["url"]
    if job.get("source_fetcher_type") == "slack":
        yield _progress(f"{progress_prefix}Skipping (Slack post, not re-fetchable): {url}")
        return RevisitOutcome("skipped")

    yield _progress(f"{progress_prefix}Revisiting: {url}")

    try:
        html = fetch_url_html(url)
    except FetchError:
        time.sleep(_REVISIT_RETRY_DELAY_SECONDS)
        try:
            html = fetch_url_html(url)
        except FetchError as exc:
            reason = _closed_reason_for_fetch_error(exc)
            q.mark_job_closed(conn, job["id"], reason)
            yield _progress(f"{progress_prefix}Closed ({exc}) — moved to Trash: {url}")
            return RevisitOutcome("closed", reason)

    text = extract_text(html)
    if not has_enough_text(text):
        rendered = render_html(url)
        if rendered:
            text = extract_text(rendered)
        if not has_enough_text(text):
            reason = "this job posting no longer shows any content"
            q.mark_job_closed(conn, job["id"], reason)
            yield _progress(f"{progress_prefix}Closed (no content) — moved to Trash: {url}")
            return RevisitOutcome("closed", reason)

    simplified = simplify(text)
    content_type, _ = classify(client, model, simplified)
    if content_type == "irrelevant":
        reason = "this page is no longer a job posting"
        q.mark_job_closed(conn, job["id"], reason)
        yield _progress(f"{progress_prefix}Closed (no longer a posting) — moved to Trash: {url}")
        return RevisitOutcome("closed", reason)
    if content_type == "error":
        reason = "this job posting is no longer accessible"
        q.mark_job_closed(conn, job["id"], reason)
        yield _progress(f"{progress_prefix}Closed (inaccessible) — moved to Trash: {url}")
        return RevisitOutcome("closed", reason)

    ratio = difflib.SequenceMatcher(
        None, (job["raw_text"] or "")[:20000], text[:20000]
    ).ratio()
    q.update_job_raw_text(conn, job["id"], text)
    if ratio >= _CHANGE_RATIO_THRESHOLD:
        yield _progress(f"{progress_prefix}Still open, unchanged: {url}")
        return RevisitOutcome("unchanged")

    yield _progress(f"{progress_prefix}Still open, content changed — re-evaluating: {url}")
    is_slack = False
    yield from _evaluate_posting(
        conn, client, model, job["id"],
        simplified=simplified, content_type=content_type,
        fallback_title=job["title"], is_slack=is_slack,
        profile=profile, scenarios=scenarios, url=url,
        progress_prefix=progress_prefix, preserve_existing_metadata=True,
    )
    return RevisitOutcome("changed")
```

- [ ] **Step 4: Run the new suite**

Run: `python -m pytest tests/test_revisit.py -v`
Expected: PASS

- [ ] **Step 5: Regression**

Run: `python -m pytest tests/test_pipeline.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/pipeline.py tests/test_revisit.py
git commit -m "feat: run_revisit_job — re-fetch a job URL and close it on reproducible failure"
```

---

## Task 4: `jobs_revisit` task kind + per-job route + decision-time enqueue

**Files:**
- Modify: `app/routes/jobs.py`
- Create: `app/templates/jobs/_revisit_notice.html`
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: `run_revisit_job` / `RevisitOutcome` (Task 3), `q.get_revisitable_jobs` (Task 1), `q.create_inbox_item`, `q.enqueue_task`.
- Produces:
  - task kind `"jobs_revisit"`, params `{job_ids?: list[int], trigger?: "sweep"|"manual"|"status_change"}`
  - `POST /jobs/{job_id}/revisit` → `{"task_id", "already_active"}` (or `{"skipped", "message"}` when the job is Slack-sourced / missing)

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_routes_jobs.py`. Task execution is driven exactly like the
existing `job_reset` tests: patch `app.routes.jobs.run_revisit_job` with a fake
generator, then call `execute_task(conn, MagicMock(), "model", MagicMock(), task)`.
Queued tasks are listed with `q.get_active_tasks(conn)`.

```python
def _fake_revisit_closed(conn, client, model, job, scenarios, profile, progress_prefix=""):
    from app.pipeline import RevisitOutcome
    q.mark_job_closed(conn, job["id"], "this job can no longer be found")
    yield f"{progress_prefix}Closed: {job['url']}"
    return RevisitOutcome("closed", "this job can no longer be found")


def test_revisit_route_enqueues_task(client, conn):
    sid = q.insert_source(conn, "board", "http://example.com", "generic_listing")
    jid = q.insert_job(conn, source_id=sid, url="http://example.com/x", title="X",
                       company="", raw_text="b")
    r = client.post(f"/jobs/{jid}/revisit")
    assert r.status_code == 200
    task = q.get_task(conn, r.json()["task_id"])
    assert task["kind"] == "jobs_revisit"
    assert task["params"]["job_ids"] == [jid]
    assert task["params"]["trigger"] == "manual"


def test_revisit_route_skips_slack_job(client, conn):
    sid = q.insert_source(conn, "slk", "http://x.slack.com/c", "slack")
    jid = q.insert_job(conn, source_id=sid, url="http://x.slack.com/c#1", title="t",
                       company="", raw_text="b")
    r = client.post(f"/jobs/{jid}/revisit")
    assert r.json().get("skipped") is True
    assert not [t for t in q.get_active_tasks(conn) if t["kind"] == "jobs_revisit"]


def test_revisit_task_execution_closes_and_renders_row(conn):
    sid = q.insert_source(conn, "board", "http://example.com", "generic_listing")
    jid = q.insert_job(conn, source_id=sid, url="http://example.com/x", title="X",
                       company="", raw_text="b")
    task = q.enqueue_task(conn, kind="jobs_revisit",
                          params={"job_ids": [jid], "trigger": "manual"})
    with patch("app.routes.jobs.run_revisit_job", side_effect=_fake_revisit_closed):
        execute_task(conn, MagicMock(), "model", MagicMock(), task)
    assert q.get_job(conn, jid)["status"] == "trash"
    result = q.get_task(conn, task["id"])["result"]
    assert any("Trash" in c for c in result["html_chunks"])


def test_accept_enqueues_status_change_revisit(client, conn):
    sid = q.insert_source(conn, "board", "http://example.com", "generic_listing")
    jid = q.insert_job(conn, source_id=sid, url="http://example.com/j", title="J",
                       company="", raw_text="b")
    client.post(f"/jobs/{jid}/feedback", data={"status": "accepted", "note": ""})
    tasks = [t for t in q.get_active_tasks(conn) if t["kind"] == "jobs_revisit"]
    assert len(tasks) == 1
    assert tasks[0]["params"] == {"job_ids": [jid], "trigger": "status_change"}


def test_slack_job_accept_does_not_enqueue_revisit(client, conn):
    sid = q.insert_source(conn, "slk", "http://x.slack.com/c", "slack")
    jid = q.insert_job(conn, source_id=sid, url="http://x.slack.com/c#1", title="t",
                       company="", raw_text="b")
    client.post(f"/jobs/{jid}/feedback", data={"status": "accepted", "note": ""})
    assert not [t for t in q.get_active_tasks(conn) if t["kind"] == "jobs_revisit"]


def test_status_change_revisit_close_creates_inbox_item(conn):
    sid = q.insert_source(conn, "board", "http://example.com", "generic_listing")
    jid = q.insert_job(conn, source_id=sid, url="http://example.com/j", title="J",
                       company="", raw_text="b")
    task = q.enqueue_task(conn, kind="jobs_revisit",
                          params={"job_ids": [jid], "trigger": "status_change"})
    with patch("app.routes.jobs.run_revisit_job", side_effect=_fake_revisit_closed):
        execute_task(conn, MagicMock(), "model", MagicMock(), task)
    assert any(i["kind"] == "job_closed" for i in q.get_unresolved_inbox_items(conn))


def test_bulk_accept_enqueues_single_revisit_task(client, conn):
    sid = q.insert_source(conn, "board", "http://example.com", "generic_listing")
    ids = [q.insert_job(conn, source_id=sid, url=f"http://example.com/{n}", title=str(n),
                        company="", raw_text="b") for n in range(3)]
    client.post("/jobs/bulk-feedback", data={"job_ids": ids, "status": "rejected", "note": ""})
    tasks = [t for t in q.get_active_tasks(conn) if t["kind"] == "jobs_revisit"]
    assert len(tasks) == 1
    assert sorted(tasks[0]["params"]["job_ids"]) == sorted(ids)
```

- [ ] **Step 2: Run — expect failure**

Run: `python -m pytest tests/test_routes_jobs.py -k "revisit or status_change_revisit or bulk_accept_enqueues" -v`
Expected: FAIL (404 on `/jobs/{id}/revisit`)

- [ ] **Step 3: Implement**

Create `app/templates/jobs/_revisit_notice.html`:
```html
<p><span class="tag">{{ level | default("info") }}</span> {{ message }}</p>
```

In `app/routes/jobs.py`:

Add imports:
```python
from app.pipeline import run_revisit_job
```

Add the task kind (near `_task_job_reevaluate`):
```python
_REVISIT_NOTICE = {
    "closed": ("warning", "Moved to Trash — {reason}."),
    "changed": ("info", "Still open, but the posting changed since we saved it — re-scored."),
    "unchanged": ("info", "Checked just now — still open."),
    "skipped": ("info", "This job was posted via Slack and can't be re-checked automatically."),
}


@register_task_kind("jobs_revisit")
def _task_jobs_revisit(conn, client, model, config, params):
    trigger = params.get("trigger", "sweep")
    job_ids = params.get("job_ids")
    if job_ids:
        jobs = [j for j in (q.get_job(conn, i) for i in job_ids) if j is not None]
    else:
        jobs = q.get_revisitable_jobs(conn)
        if len(jobs) > 200:
            yield _progress(f"Revisiting the first 200 of {len(jobs)} eligible jobs")
            jobs = jobs[:200]

    scenarios = q.get_scenarios(conn)
    profile = q.get_profile(conn)
    outcomes = []
    for i, job in enumerate(jobs, start=1):
        prefix = f"[{i}/{len(jobs)}] " if len(jobs) > 1 else ""
        gen = run_revisit_job(conn, client, model, job, scenarios, profile, progress_prefix=prefix)
        outcome = None
        try:
            while True:
                yield next(gen)
        except StopIteration as stop:
            outcome = stop.value
        outcomes.append((job, outcome))

    closed = [j for j, o in outcomes if o and o.verdict == "closed"]
    changed = sum(1 for _, o in outcomes if o and o.verdict == "changed")

    if trigger == "status_change" and closed:
        if len(closed) == 1:
            j = closed[0]
            q.create_inbox_item(
                conn, "job_closed",
                f"'{j['title'] or j['url']}' was moved to Trash — the posting is no longer "
                f"available, checked right after you decided on it.",
                f"/jobs/{j['id']}",
            )
        else:
            q.create_inbox_item(
                conn, "job_closed",
                f"{len(closed)} jobs you just decided on were moved to Trash — their postings "
                f"are no longer available.",
                "/jobs?status=trash",
            )

    result = {"html_chunks": [], "notices": []}
    if job_ids and len(outcomes) == 1 and outcomes[0][1] is not None:
        job, outcome = outcomes[0]
        level, tmpl = _REVISIT_NOTICE[outcome.verdict]
        f, detail = _filter_from_task_params(params)
        row_html = _render_updated_job_html(conn, None, job["id"], f, detail=detail)
        counts_html = templates.get_template("jobs/_counts_oob.html").render(
            request=None, counts=_counts_for_filter(conn, f)
        )
        notice_html = templates.get_template("jobs/_revisit_notice.html").render(
            request=None, level=level,
            message=tmpl.format(reason=outcome.reason or ""),
        )
        result["html_chunks"] = [notice_html, row_html, counts_html]
    else:
        yield _progress(
            f"Revisited {len(outcomes)} · closed {len(closed)} · changed {changed}"
        )
    return result
```

Add the route (near `job_reevaluate`):
```python
@router.post("/jobs/{job_id}/revisit")
def job_revisit(job_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    job = q.get_job(conn, job_id)
    if job is None or job["source_fetcher_type"] == "slack":
        return {"skipped": True,
                "message": "This job can't be revisited automatically."}
    task = q.enqueue_task(
        conn, kind="jobs_revisit",
        params={"job_ids": [job_id], "trigger": "manual", **_filter_task_params(request)},
    )
    return {"task_id": task["id"], "already_active": task["already_active"]}
```

In `job_feedback`, right after `q.update_job_feedback(conn, job_id, status, note)`:
```python
    if status in ("accepted", "rejected"):
        j = q.get_job(conn, job_id)
        if j is not None and j["source_fetcher_type"] != "slack":
            q.enqueue_task(conn, kind="jobs_revisit",
                           params={"job_ids": [job_id], "trigger": "status_change"})
```

In `job_bulk_feedback`, after the loop that calls `q.update_job_feedback` for each id:
```python
    if status in ("accepted", "rejected"):
        revisitable = [
            jid for jid in job_ids
            if (j := q.get_job(conn, jid)) is not None and j["source_fetcher_type"] != "slack"
        ]
        if revisitable:
            q.enqueue_task(conn, kind="jobs_revisit",
                           params={"job_ids": revisitable, "trigger": "status_change"})
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_routes_jobs.py -k "revisit or status_change or bulk_accept_enqueues" -v`
Expected: PASS

- [ ] **Step 5: Regression**

Run: `python -m pytest tests/test_routes_jobs.py tests/test_task_engine.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/routes/jobs.py app/templates/jobs/_revisit_notice.html tests/test_routes_jobs.py
git commit -m "feat: jobs_revisit task + per-job route + decision-time revisit enqueue"
```

---

## Task 5: Sweep route + `/fetch` button

**Files:**
- Modify: `app/routes/fetch.py`, `app/templates/fetch/panel.html`
- Test: `tests/test_routes_fetch.py`

**Interfaces:**
- Consumes: `q.get_revisitable_jobs` (Task 1), task kind `"jobs_revisit"` (Task 4).
- Produces: `POST /revisit/all` → `{"task_id", "already_active"}` or `{"skipped", "message"}`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_routes_fetch.py`:

```python
def test_revisit_all_skipped_when_nothing_eligible(client, conn):
    r = client.post("/revisit/all")
    assert r.json() == {"skipped": True, "message": "No jobs to revisit."}


def test_revisit_all_enqueues_sweep(client, conn):
    sid = q.insert_source(conn, "board", "http://example.com", "generic_listing")
    q.insert_job(conn, source_id=sid, url="http://example.com/a", title="A",
                 company="", raw_text="b")
    r = client.post("/revisit/all")
    body = r.json()
    assert "task_id" in body
    task = q.get_task(conn, body["task_id"])
    assert task["kind"] == "jobs_revisit" and task["params"] == {}


def test_fetch_panel_has_revisit_button(client, conn):
    html = client.get("/fetch").text
    assert 'data-progress-url="/revisit/all"' in html
```

- [ ] **Step 2: Run — expect failure**

Run: `python -m pytest tests/test_routes_fetch.py -k revisit -v`
Expected: FAIL (404)

- [ ] **Step 3: Implement**

In `app/routes/fetch.py`:
```python
@router.post("/revisit/all")
def trigger_revisit_all(conn: sqlite3.Connection = Depends(get_db)):
    if not q.get_revisitable_jobs(conn):
        return {"skipped": True, "message": "No jobs to revisit."}
    task = q.enqueue_task(conn, kind="jobs_revisit", params={})
    return {"task_id": task["id"], "already_active": task["already_active"]}
```

In `app/templates/fetch/panel.html`, next to the existing "Fetch all" button:
```html
  <button class="btn" data-progress-url="/revisit/all">Revisit open jobs</button>
```
Add a one-line hint under the button row:
```html
  <p class="hint">Re-checks every open job's original posting; anything that can no longer be reached is moved to Trash.</p>
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_routes_fetch.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/routes/fetch.py app/templates/fetch/panel.html tests/test_routes_fetch.py
git commit -m "feat: Revisit-open-jobs sweep button on the Fetch page"
```

---

## Task 6: Per-job "Revisit" button in the job card

**Files:**
- Modify: `app/templates/jobs/_feedback.html`
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: `POST /jobs/{job_id}/revisit` (Task 4), `job.source_fetcher_type` on the row dict (Task 1).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_routes_jobs.py`:

```python
def test_job_card_shows_revisit_button_for_non_slack(client, conn):
    sid = q.insert_source(conn, "board", "http://example.com", "generic_listing")
    jid = q.insert_job(conn, source_id=sid, url="http://example.com/a", title="A",
                       company="", raw_text="b")
    html = client.get(f"/jobs/{jid}").text
    assert f'/jobs/{jid}/revisit' in html


def test_job_card_hides_revisit_button_for_slack(client, conn):
    sid = q.insert_source(conn, "slk", "http://x.slack.com/c", "slack")
    jid = q.insert_job(conn, source_id=sid, url="http://x.slack.com/c#1", title="A",
                       company="", raw_text="b")
    html = client.get(f"/jobs/{jid}").text
    assert f'/jobs/{jid}/revisit' not in html
```

- [ ] **Step 2: Run — expect failure**

Run: `python -m pytest tests/test_routes_jobs.py -k "revisit_button" -v`
Expected: FAIL

- [ ] **Step 3: Implement**

In `app/templates/jobs/_feedback.html`, inside `<details class="job-advanced">`, after the "Reset to new" button:
```html
    {% if job.source_fetcher_type != "slack" %}
    <button type="button" class="btn"
      data-progress-url="/jobs/{{ job.id }}/revisit{{ macros.qsuffix(filter, detail=is_detail_page|default(false)) }}"
      data-progress-oob="1"
      data-progress-display="#reset-progress-{{ job.id }}"
      title="Re-fetch the original posting. If it can no longer be reached, this job moves to Trash.">
      Revisit
    </button>
    {% endif %}
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_routes_jobs.py -k "revisit_button" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/templates/jobs/_feedback.html tests/test_routes_jobs.py
git commit -m "feat: per-job Revisit button in the job card"
```

---

## Task 7: Full suite + manual smoke

- [ ] **Step 1: Full test suite**

Run: `python -m pytest -q`
Expected: PASS (no regressions)

- [ ] **Step 2: Manual smoke via dev server**

Use the `run-dev-server` skill (throwaway DB copy). Check:
- `/fetch` shows "Revisit open jobs"; clicking it runs a task that completes.
- A job whose URL you can break (edit the row's `url` in the throwaway DB to a 404) moves to Trash with the plain-language note after a per-job "Revisit".
- Accepting a still-live job leaves it accepted; the task log shows "still open".

- [ ] **Step 3: Leave the dev server running and hand the URL to the user** for their own check before merge.

---

## Self-Review Notes

- Spec §"What counts as closed" → Task 3 steps (HTTP retry, thin-page, irrelevant, error).
- Spec §"Still open, changed / unchanged" → Task 3 ratio branch + Task 2 `_evaluate_posting`.
- Spec §"Slack excluded" → Task 1 `get_revisitable_jobs`, Task 3 skip, Task 4 route guard, Task 6 template guard.
- Spec §"Three entry points" → Task 4 (per-job + decision), Task 5 (sweep).
- Spec §"Closure note" → Task 1 `mark_job_closed`.
- Spec §"Decision-time race → inbox item" → Task 4 `_task_jobs_revisit` `trigger == "status_change"` branch.
- Spec §"Testing" → Tasks 1–6 test steps + Task 7.
