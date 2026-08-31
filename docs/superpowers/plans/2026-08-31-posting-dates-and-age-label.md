# Posting dates from `summarize` + age-label fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every job an age by extracting the posting date in the existing `summarize` LLM call (plus the hiring org), defaulting `published_at` to processing time so it is never NULL for new jobs, and fix the age badge overlapping the permalink icon / checkbox.

**Architecture:** `summarize` gains two output fields (`company`, `posted_date`) and returns a `JobSummary` dataclass instead of a 3-tuple. `_ingest_posting` threads the two new values into `update_job_pipeline`, which upgrades the columns only when the value is non-empty. `insert_job` defaults `published_at` to `datetime('now')`. The age-label fix is CSS-only in `base.html`.

**Tech Stack:** Python 3.14, FastAPI, SQLite (stdlib `sqlite3`), Jinja2 templates, pytest, `python -m pytest` to run.

## Global Constraints

- Run tests with `python -m pytest` (not `uv run` — read-only cache in this environment).
- No backwards-compat shims / migration chains — this is a single-instance personal app (project migration philosophy). This change needs **no migration at all**.
- `posted_date` wire format is exactly `YYYY-MM-DD`. Stored into the existing `jobs.published_at` TEXT column (which also holds full ISO-8601 datetimes from other fetchers — a date-only string is valid there and `datetime.fromisoformat` / the `time_ago` filter parse it).
- `summarize` has **three** call sites in `app/pipeline.py`: `_ingest_posting` (~line 76), `run_reevaluate_job` (~line 244), `run_reevaluate` (~line 306). Only `_ingest_posting` gets date/company persistence — the re-evaluate paths re-summarize an already-ingested job and must not overwrite its `company`/`published_at` (a relative date in the stored text would resolve against the wrong "now"). All three still need the `JobSummary` field-access update.
- Do **not** change `time_ago` wording — "N days ago" at all ages, as today.
- Do **not** add any real-date-vs-fallback UI distinction.
- Commit after each task with the trailer:
  ```
  Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_017zYcggdHo3NWya3BrLBoe6
  ```

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `app/ai/summarize.py` | Job-summary LLM call | Add `JobSummary` dataclass return type; add `company` + `posted_date` fields, `today` param, validation |
| `app/db/queries.py` | SQL layer | `insert_job` published_at fallback; `update_job_pipeline` gains `company` + `published_at` params |
| `app/pipeline.py` | Ingest orchestration | `_ingest_posting` uses `JobSummary`, passes `today`, threads new fields to `update_job_pipeline` |
| `app/templates/base.html` | Global CSS | Reserve right-side space on `.job-row-header` / `.job-detail-title-row` so `.job-age` clears the permalink icon |
| `tests/test_summarize.py` | | Rewrite for `JobSummary`; new `company`/`posted_date` cases |
| `tests/test_queries.py` | | Update `insert_job` default test; new `update_job_pipeline` coalesce cases |
| `tests/test_pipeline.py` | | Update job_posting-path fixtures/asserts for `JobSummary` + new columns |
| `tests/test_routes_jobs.py` | | Repurpose `test_job_list_omits_published_date_when_unknown` |

---

## Task 1: `JobSummary` dataclass — pure return-type refactor

Change `summarize()` to return a frozen `JobSummary` dataclass instead of the `(title, headline, summary)` tuple. **No behavior change** — `company` and `posted_date` are always `""` in this task. This isolates the ~25 test-assertion updates from the new-feature logic.

**Files:**
- Modify: `app/ai/summarize.py`
- Modify: `app/pipeline.py` — all three `summarize(...)` call sites (~lines 76, 244, 306)
- Test: `tests/test_summarize.py` (rewrite assertions), `tests/test_pipeline.py` (job_posting-path assertions unaffected — verify)

**Interfaces:**
- Produces: `JobSummary` frozen dataclass with fields `title: str = ""`, `company: str = ""`, `headline: str = ""`, `summary: str = ""`, `posted_date: str = ""`.
- Produces: `summarize(client, model, simplified_content, content_type="job_posting", raw_passthrough=True) -> JobSummary` (signature otherwise unchanged in this task).

- [ ] **Step 1: Update the summarize tests to expect `JobSummary`**

Rewrite `tests/test_summarize.py`. Replace tuple unpacking / tuple equality with `JobSummary` field access. Full new file content:

```python
from unittest.mock import MagicMock
from app.ai.summarize import summarize, JobSummary


def _mock_client(response_text: str) -> MagicMock:
    client = MagicMock()
    choice = MagicMock()
    choice.message.content = response_text
    client.chat.completions.create.return_value = MagicMock(choices=[choice])
    return client


def test_summarize_returns_title_headline_and_summary():
    response = (
        '{"title": "ML Engineer - Remote @ Acme", '
        '"headline": "Fully remote with a $180k+ ceiling", '
        '"summary": "**Role:** ML Engineer"}'
    )
    result = summarize(_mock_client(response), "llama3.2", "long job description text")
    assert result.title == "ML Engineer - Remote @ Acme"
    assert result.headline == "Fully remote with a $180k+ ceiling"
    assert result.summary == "**Role:** ML Engineer"


def test_summarize_calls_llm_once():
    client = _mock_client('{"title": "T", "headline": "H", "summary": "S"}')
    summarize(client, "llama3.2", "content")
    assert client.chat.completions.create.call_count == 1


def test_summarize_strips_markdown_code_fence():
    response = '```json\n{"title": "T", "headline": "H", "summary": "S"}\n```'
    result = summarize(_mock_client(response), "llama3.2", "content")
    assert (result.title, result.headline, result.summary) == ("T", "H", "S")


def test_summarize_returns_empty_strings_on_malformed_json():
    result = summarize(_mock_client("not json"), "llama3.2", "content")
    assert result == JobSummary()


def test_summarize_returns_empty_strings_on_api_error():
    client = MagicMock()
    client.chat.completions.create.side_effect = Exception("boom")
    result = summarize(client, "llama3.2", "content")
    assert result == JobSummary()


def test_summarize_disables_model_thinking():
    client = _mock_client('{"title": "T", "headline": "H", "summary": "S"}')
    summarize(client, "llama3.2", "content")
    call_args = client.chat.completions.create.call_args
    assert call_args.kwargs["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}


def test_summarize_defaults_to_job_posting_prompt():
    client = _mock_client('{"title": "T", "headline": "H", "summary": "S"}')
    summarize(client, "llama3.2", "content")
    system = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert "job posting" in system.lower()


def test_summarize_appends_valid_source_link_to_summary():
    response = (
        '{"title": "T", "headline": "H", "summary": "**Role:** ML Engineer", '
        '"source_link": "https://example.com/apply"}'
    )
    content = "Some job posting text.\nApply here: https://example.com/apply\nMore text."
    result = summarize(_mock_client(response), "llama3.2", content)
    assert result.summary == "**Role:** ML Engineer\n\n**Original posting:** [https://example.com/apply](https://example.com/apply)"


def test_summarize_rejects_source_link_not_present_in_content():
    response = (
        '{"title": "T", "headline": "H", "summary": "**Role:** ML Engineer", '
        '"source_link": "https://hallucinated.example.com/made-up"}'
    )
    result = summarize(_mock_client(response), "llama3.2", "Some job posting text with no links at all.")
    assert result.summary == "**Role:** ML Engineer"


def test_summarize_rejects_non_http_source_link():
    response = (
        '{"title": "T", "headline": "H", "summary": "**Role:** ML Engineer", '
        '"source_link": "javascript:alert(1)"}'
    )
    result = summarize(_mock_client(response), "llama3.2", "Some text.\njavascript:alert(1)\nMore text.")
    assert result.summary == "**Role:** ML Engineer"


def test_summarize_leaves_summary_unchanged_when_no_source_link():
    response = '{"title": "T", "headline": "H", "summary": "**Role:** ML Engineer", "source_link": ""}'
    result = summarize(_mock_client(response), "llama3.2", "content with no links")
    assert result.summary == "**Role:** ML Engineer"


def test_summarize_leaves_summary_unchanged_when_source_link_field_missing():
    response = '{"title": "T", "headline": "H", "summary": "**Role:** ML Engineer"}'
    result = summarize(_mock_client(response), "llama3.2", "content with no links")
    assert result.summary == "**Role:** ML Engineer"


def test_summarize_handles_null_source_link_without_crashing():
    response = '{"title": "T", "headline": "H", "summary": "**Role:** ML Engineer", "source_link": null}'
    result = summarize(_mock_client(response), "llama3.2", "content with no links")
    assert result.title == "T"
    assert result.headline == "H"
    assert result.summary == "**Role:** ML Engineer"


def test_summarize_lead_path_unaffected_by_source_link():
    response = '{"organizations": ["Acme"], "headline": "H", "source_link": "https://example.com/apply"}'
    original = "Posted by U123:\n\nAcme is hiring, apply at https://example.com/apply"
    result = summarize(_mock_client(response), "llama3.2", original, content_type="lead")
    assert result.summary == original


def test_summarize_uses_lead_prompt_for_lead_content_type():
    client = _mock_client('{"organizations": ["Acme"], "headline": "H", "summary": "S"}')
    summarize(client, "llama3.2", "content", content_type="lead")
    system = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert "organization" in system.lower()
    assert "job posting" not in system.lower()


def test_summarize_lead_builds_title_from_organizations():
    response = '{"organizations": ["Acme", "Globex"], "headline": "A couple of orgs are hiring"}'
    result = summarize(_mock_client(response), "llama3.2", "Posted by U123:\n\noriginal message", content_type="lead")
    assert result.title == "Acme, Globex"
    assert result.headline == "A couple of orgs are hiring"


def test_summarize_lead_retains_the_original_message_as_the_summary():
    response = '{"organizations": ["Acme"], "headline": "H", "summary": "an AI rewrite that should be ignored"}'
    original = "Posted by U123:\n\nAcme is hiring, DM me"
    result = summarize(_mock_client(response), "llama3.2", original, content_type="lead")
    assert result.summary == original


def test_summarize_lead_handles_no_organizations_found():
    response = '{"organizations": [], "headline": "No specific org named"}'
    result = summarize(_mock_client(response), "llama3.2", "content", content_type="lead")
    assert result.title == ""


def test_summarize_lead_falls_back_to_original_message_on_malformed_json():
    original = "Posted by U123:\n\noriginal message"
    result = summarize(_mock_client("not json"), "llama3.2", original, content_type="lead")
    assert result == JobSummary(summary=original)


def test_summarize_lead_falls_back_to_original_message_on_api_error():
    client = MagicMock()
    client.chat.completions.create.side_effect = Exception("boom")
    original = "Posted by U123:\n\noriginal message"
    result = summarize(client, "llama3.2", original, content_type="lead")
    assert result == JobSummary(summary=original)


def test_summarize_non_slack_lead_gets_ai_summary_not_raw_body():
    response = '{"title": "ML Engineer - Paris @ Acme", "headline": "H", "summary": "**Role:** ML Engineer"}'
    result = summarize(
        _mock_client(response), "llama3.2", "a wall of scraped page chrome",
        content_type="lead", raw_passthrough=False,
    )
    assert result.title == "ML Engineer - Paris @ Acme"
    assert result.summary == "**Role:** ML Engineer"


def test_summarize_non_slack_lead_uses_job_posting_prompt():
    client = _mock_client('{"title": "T", "headline": "H", "summary": "S"}')
    summarize(client, "llama3.2", "content", content_type="lead", raw_passthrough=False)
    system = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert "job posting" in system.lower()


def test_summarize_non_slack_lead_empty_summary_on_api_error():
    client = MagicMock()
    client.chat.completions.create.side_effect = Exception("boom")
    result = summarize(client, "llama3.2", "raw", content_type="lead", raw_passthrough=False)
    assert result == JobSummary()
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_summarize.py -q`
Expected: FAIL — `ImportError: cannot import name 'JobSummary'` / attribute errors.

- [ ] **Step 3: Implement the dataclass and new return type**

Edit `app/ai/summarize.py`. Add imports and the dataclass near the top:

```python
from __future__ import annotations
import json
from dataclasses import dataclass
import openai
from app.ai.json_utils import extract_json


@dataclass(frozen=True)
class JobSummary:
    title: str = ""
    company: str = ""
    headline: str = ""
    summary: str = ""
    posted_date: str = ""
```

Replace the body of `summarize()` after `raw_lead = ...` with:

```python
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _LEAD_SYSTEM if raw_lead else _SYSTEM},
                {"role": "user", "content": simplified_content[:6000]},
            ],
            temperature=0.3,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        data = json.loads(extract_json(resp.choices[0].message.content))
        if raw_lead:
            title = ", ".join(data.get("organizations", []))
            return JobSummary(title=title, headline=data.get("headline", ""), summary=simplified_content)
        summary = data.get("summary", "")
        source_link = _valid_source_link(data.get("source_link", ""), simplified_content)
        if source_link:
            summary = f"{summary}\n\n**Original posting:** [{source_link}]({source_link})"
        return JobSummary(
            title=data.get("title", ""),
            headline=data.get("headline", ""),
            summary=summary,
        )
    except Exception:
        return JobSummary(summary=simplified_content if raw_lead else "")
```

(Keep the existing comment about disabled chain-of-thought above the `create` call.)

- [ ] **Step 4: Update all three production call sites**

Edit `app/pipeline.py`.

**4a — `_ingest_posting`** (job_posting/lead branch). Currently:

```python
        ai_title, headline, job_summary = summarize(
            client, model, simplified, content_type=content_type, raw_passthrough=is_slack,
        )
        q.update_job_pipeline(
            conn, job_id,
            simplified_content=simplified,
            content_type=content_type,
            title=ai_title or fallback_title,
            headline=headline,
            summary=job_summary,
        )
```

Replace with:

```python
        job_summary = summarize(
            client, model, simplified, content_type=content_type, raw_passthrough=is_slack,
        )
        q.update_job_pipeline(
            conn, job_id,
            simplified_content=simplified,
            content_type=content_type,
            title=job_summary.title or fallback_title,
            headline=job_summary.headline,
            summary=job_summary.summary,
        )
```

Then in the same branch: `evaluate(client, model, scenario, criteria, job_summary)` → `...criteria, job_summary.summary)`, and `result = assess_fit(client, model, profile, job_summary)` → `assess_fit(client, model, profile, job_summary.summary)`.

**4b — `run_reevaluate_job`** (~line 244). Currently:

```python
    ai_title, headline, new_summary = summarize(
        client, model, job["simplified_content"], content_type=job["content_type"]
    )
    q.update_job_pipeline(
        conn, job["id"],
        simplified_content=job["simplified_content"],
        content_type=job["content_type"],
        title=ai_title or job["title"],
        headline=headline,
        summary=new_summary,
    )
```

Replace with:

```python
    s = summarize(client, model, job["simplified_content"], content_type=job["content_type"])
    new_summary = s.summary
    q.update_job_pipeline(
        conn, job["id"],
        simplified_content=job["simplified_content"],
        content_type=job["content_type"],
        title=s.title or job["title"],
        headline=s.headline,
        summary=new_summary,
    )
```

(`new_summary` stays a local so the later `evaluate(...)` / `assess_fit(...)` / progress lines in that function are untouched. Do **not** pass `company`/`published_at` here.)

**4c — `run_reevaluate`** (~line 306). Currently:

```python
        if job["simplified_content"]:
            ai_title, headline, new_summary = summarize(
                client, model, job["simplified_content"], content_type=job["content_type"]
            )
        else:
            ai_title, headline, new_summary = job["title"], job["headline"], job["summary"]
        score, reasoning = evaluate(client, model, scenario, criteria, new_summary)
        q.update_job_pipeline(
            conn, job["id"],
            simplified_content=job["simplified_content"],
            content_type=job["content_type"],
            title=ai_title or job["title"],
            headline=headline,
            summary=new_summary,
        )
```

Replace with:

```python
        if job["simplified_content"]:
            s = summarize(client, model, job["simplified_content"], content_type=job["content_type"])
            ai_title, headline, new_summary = s.title, s.headline, s.summary
        else:
            ai_title, headline, new_summary = job["title"], job["headline"], job["summary"]
        score, reasoning = evaluate(client, model, scenario, criteria, new_summary)
        q.update_job_pipeline(
            conn, job["id"],
            simplified_content=job["simplified_content"],
            content_type=job["content_type"],
            title=ai_title or job["title"],
            headline=headline,
            summary=new_summary,
        )
```

- [ ] **Step 5: Run the full affected suites**

Run: `python -m pytest tests/test_summarize.py tests/test_pipeline.py -q`
Expected: PASS. (Pipeline job_posting-path tests feed `{"title","headline","summary"}` responses that still parse; lead-path tests unaffected.)

- [ ] **Step 6: Commit**

```bash
git add app/ai/summarize.py app/pipeline.py tests/test_summarize.py
git commit -m "refactor: summarize returns a JobSummary dataclass

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_017zYcggdHo3NWya3BrLBoe6"
```

---

## Task 2: `insert_job` — default `published_at` to processing time

**Files:**
- Modify: `app/db/queries.py` (`insert_job`, ~line 185-200)
- Test: `tests/test_queries.py`

**Interfaces:**
- Consumes: nothing from prior tasks.
- Produces: `insert_job(...)` unchanged signature (`published_at: str | None = None`), but a `None` argument now persists `datetime('now')` instead of SQL NULL.

- [ ] **Step 1: Update the tests**

In `tests/test_queries.py`, **replace** `test_insert_job_published_at_defaults_to_none` with:

```python
def test_insert_job_published_at_defaults_to_processing_time(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    job = q.get_job(conn, jid)
    assert job["published_at"] is not None
    # same clock source as fetched_at, set in the same statement-batch
    assert job["published_at"][:10] == job["fetched_at"][:10]
```

Leave `test_insert_job_stores_published_at` (explicit value) as-is.

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_queries.py::test_insert_job_published_at_defaults_to_processing_time -q`
Expected: FAIL — `assert None is not None`.

- [ ] **Step 3: Implement**

In `app/db/queries.py` `insert_job`, change the `execute` call to:

```python
    cur = conn.execute(
        "INSERT INTO jobs (source_id, url, title, company, raw_text, published_at) "
        "VALUES (?, ?, ?, ?, ?, COALESCE(?, datetime('now')))",
        (source_id, url, title, company, raw_text, published_at),
    )
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_queries.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/db/queries.py tests/test_queries.py
git commit -m "feat: insert_job defaults published_at to processing time

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_017zYcggdHo3NWya3BrLBoe6"
```

---

## Task 3: `update_job_pipeline` — `company` + `published_at` params

**Files:**
- Modify: `app/db/queries.py` (`update_job_pipeline`, ~line 217-237)
- Test: `tests/test_queries.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `update_job_pipeline(conn, job_id, *, simplified_content, content_type, title=None, summary="", headline="", company="", published_at="")`. `company` and `published_at`: when the passed string is non-empty it overwrites the column; when `""` (or the arg is omitted) the existing column value is left untouched.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_queries.py`:

```python
def test_update_job_pipeline_sets_company_and_published_at(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="", raw_text="r")
    q.update_job_pipeline(
        conn, jid, simplified_content="clean", content_type="job_posting",
        summary="s", company="Zivid", published_at="2026-08-17",
    )
    job = q.get_job(conn, jid)
    assert job["company"] == "Zivid"
    assert job["published_at"] == "2026-08-17"


def test_update_job_pipeline_empty_company_and_date_keep_existing(conn):
    source_id = q.insert_source(conn, "s", "http://x", "finn_listing")
    jid = q.insert_job(
        conn, source_id=source_id, url="http://job/1", title="T", company="Acme", raw_text="r",
        published_at="2026-07-01T00:00:00+00:00",
    )
    q.update_job_pipeline(
        conn, jid, simplified_content="clean", content_type="job_posting", summary="s",
    )
    job = q.get_job(conn, jid)
    assert job["company"] == "Acme"
    assert job["published_at"] == "2026-07-01T00:00:00+00:00"
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_queries.py -k "company_and" -q`
Expected: FAIL — `TypeError: update_job_pipeline() got an unexpected keyword argument 'company'`.

- [ ] **Step 3: Implement**

Replace `update_job_pipeline` in `app/db/queries.py` with:

```python
def update_job_pipeline(
    conn: sqlite3.Connection,
    job_id: int,
    *,
    simplified_content: str,
    content_type: str,
    title: str | None = None,
    summary: str = "",
    headline: str = "",
    company: str = "",
    published_at: str = "",
) -> None:
    conn.execute(
        """UPDATE jobs SET
            simplified_content = ?,
            content_type = ?,
            title = COALESCE(?, title),
            summary = ?,
            headline = ?,
            company = COALESCE(NULLIF(?, ''), company),
            published_at = COALESCE(NULLIF(?, ''), published_at)
        WHERE id = ?""",
        (simplified_content, content_type, title, summary, headline, company, published_at, job_id),
    )
    conn.commit()
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_queries.py -q`
Expected: PASS (existing `update_job_pipeline` tests still green — they omit the new args).

- [ ] **Step 5: Commit**

```bash
git add app/db/queries.py tests/test_queries.py
git commit -m "feat: update_job_pipeline can set company and published_at

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_017zYcggdHo3NWya3BrLBoe6"
```

---

## Task 4: `summarize` — extract `company` and `posted_date`

**Files:**
- Modify: `app/ai/summarize.py` (`_SYSTEM`, `summarize` signature + body; add `_valid_posted_date`)
- Test: `tests/test_summarize.py`

**Interfaces:**
- Consumes: `JobSummary` (Task 1).
- Produces: `summarize(client, model, simplified_content, content_type="job_posting", raw_passthrough=True, *, today: date | None = None)` — keyword-only, optional; when `None` it resolves to `date.today()` inside the function. Job-posting path fills `JobSummary.company` and `JobSummary.posted_date`; lead paths leave them `""`. The three pipeline call sites from Task 1 stay valid unchanged (they omit `today`); only `_ingest_posting` will pass it explicitly (Task 5).
- Produces: `_valid_posted_date(value: object, today: date) -> str` — returns a `YYYY-MM-DD` string if `value` parses as an ISO date not after `today`, else `""`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_summarize.py` (add `from datetime import date` at the top):

```python
_TODAY = date(2026, 8, 31)


def test_summarize_extracts_company_and_posted_date():
    response = (
        '{"title": "ML Engineer - Remote @ Acme", "company": "Acme", '
        '"headline": "H", "summary": "S", "posted_date": "2026-08-17"}'
    )
    result = summarize(_mock_client(response), "llama3.2", "text", today=_TODAY)
    assert result.company == "Acme"
    assert result.posted_date == "2026-08-17"


def test_summarize_injects_today_into_user_message():
    client = _mock_client('{"title": "T", "headline": "H", "summary": "S"}')
    summarize(client, "llama3.2", "the posting body", today=_TODAY)
    user_msg = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    assert "2026-08-31" in user_msg
    assert "the posting body" in user_msg


def test_summarize_rejects_future_posted_date():
    response = '{"title": "T", "company": "Acme", "headline": "H", "summary": "S", "posted_date": "2027-01-01"}'
    result = summarize(_mock_client(response), "llama3.2", "text", today=_TODAY)
    assert result.posted_date == ""


def test_summarize_rejects_malformed_posted_date():
    response = '{"title": "T", "company": "Acme", "headline": "H", "summary": "S", "posted_date": "2 weeks ago"}'
    result = summarize(_mock_client(response), "llama3.2", "text", today=_TODAY)
    assert result.posted_date == ""


def test_summarize_missing_company_and_date_fields_are_empty():
    response = '{"title": "T", "headline": "H", "summary": "S"}'
    result = summarize(_mock_client(response), "llama3.2", "text", today=_TODAY)
    assert result.company == ""
    assert result.posted_date == ""


def test_summarize_handles_null_company_and_date():
    response = '{"title": "T", "company": null, "headline": "H", "summary": "S", "posted_date": null}'
    result = summarize(_mock_client(response), "llama3.2", "text", today=_TODAY)
    assert result.company == ""
    assert result.posted_date == ""


def test_summarize_lead_path_leaves_company_and_date_empty():
    response = '{"organizations": ["Acme"], "headline": "H"}'
    result = summarize(_mock_client(response), "llama3.2", "Posted by U1:\n\nmsg", content_type="lead", today=_TODAY)
    assert result.company == ""
    assert result.posted_date == ""


def test_summarize_system_prompt_mentions_posted_date():
    client = _mock_client('{"title": "T", "headline": "H", "summary": "S"}')
    summarize(client, "llama3.2", "content", today=_TODAY)
    system = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert "posted_date" in system


def test_summarize_today_defaults_to_current_date_when_omitted():
    # A real today-relative date is accepted; the call must not raise without `today`.
    response = '{"title": "T", "company": "Acme", "headline": "H", "summary": "S", "posted_date": "2020-01-01"}'
    result = summarize(_mock_client(response), "llama3.2", "text")
    assert result.posted_date == "2020-01-01"
```

The existing tests in this file do not pass `today` and keep working (it is optional). The new-field tests above pass `today=_TODAY` for determinism.

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_summarize.py -q`
Expected: FAIL — the new `company` / `posted_date` / `today`-injection assertions fail (fields always `""`, no date in the user message, `posted_date` not in the system prompt).

- [ ] **Step 3: Implement**

In `app/ai/summarize.py`:

Add `from datetime import date` to the imports.

Replace `_SYSTEM` with (note the two new fields and two new instruction sentences):

```python
_SYSTEM = (
    "Summarize this job posting. Respond with exactly this JSON shape: "
    '{"title": "<Role - Location (remote/hybrid/onsite) @ Organization>", '
    '"company": "<name of the hiring organization, or empty string if unclear>", '
    '"headline": "<one punchy sentence on the most compelling or notable detail>", '
    '"summary": "<concise markdown covering role, company, location/remote status, '
    'key requirements, compensation if mentioned, notable perks or red flags>", '
    '"posted_date": "<the date this job was published, formatted YYYY-MM-DD; resolve '
    "relative phrases such as '2 weeks ago' or 'posted last month' against the current "
    'date given at the top of the text; empty string if the posting does not state or '
    'clearly imply when it was published>", '
    '"source_link": "<a URL copied verbatim from the text below that points to the '
    'original job description, application form, or the hiring organization/job page, '
    'or empty string if none is present>"}. '
    "For title: use the role as given in the posting, or a concise generated one if "
    "unclear; include location with remote/hybrid/onsite status; include the "
    "organization name. Be factual and brief. No invented details. "
    "For company: the organization that would employ the hire, not a recruiting agency "
    "or the job board. Empty string if you cannot tell. "
    "For summary: keep it well-structured and easy to scan, and don't drop any of the "
    "standard fields listed above. Within that, favor concrete specifics over generic "
    "advertised-sounding phrasing (e.g. 'competitive salary', 'fast-paced environment', "
    "'collaborative team') — call out what's actually distinctive about this posting, "
    "such as unusual scope or impact, concrete technical/domain details, or notable team "
    "or organization context. "
    "For posted_date: only a date you can support from the text; never guess one. "
    "For source_link: only return a URL that appears verbatim in the text below — never "
    "construct, guess, or modify one. If several links are present, prefer the most direct "
    "application link or the original detailed posting over generic organization/social links."
)
```

Add the validator (next to `_valid_source_link`):

```python
def _valid_posted_date(value: object, today: date) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    try:
        parsed = date.fromisoformat(value.strip())
    except ValueError:
        return ""
    if parsed > today:
        return ""
    return parsed.isoformat()
```

Change the signature and resolve `today` at the top of the body:

```python
def summarize(
    client: openai.OpenAI,
    model: str,
    simplified_content: str,
    content_type: str = "job_posting",
    raw_passthrough: bool = True,
    *,
    today: date | None = None,
) -> JobSummary:
    today = today if today is not None else date.today()
    # A "lead" keeps its source text verbatim as the summary only when ...
    raw_lead = content_type == "lead" and raw_passthrough
```

(keep the existing `raw_lead` comment). In the body, change the user message to carry the date, and fill the two fields on the job-posting return. The `messages` list becomes:

```python
            messages=[
                {"role": "system", "content": _LEAD_SYSTEM if raw_lead else _SYSTEM},
                {"role": "user", "content": f"Today is {today.isoformat()}.\n\n{simplified_content[:6000]}"},
            ],
```

and the job-posting return becomes:

```python
        return JobSummary(
            title=data.get("title", ""),
            company=data.get("company") or "",
            headline=data.get("headline", ""),
            summary=summary,
            posted_date=_valid_posted_date(data.get("posted_date"), today),
        )
```

(The `raw_lead` return and the `except` return are unchanged from Task 1 — they already leave `company`/`posted_date` at their `""` defaults.)

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_summarize.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/ai/summarize.py tests/test_summarize.py
git commit -m "feat: summarize extracts company and posted_date

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_017zYcggdHo3NWya3BrLBoe6"
```

---

## Task 5: Pipeline wiring — thread `today`, `company`, `posted_date`

**Files:**
- Modify: `app/pipeline.py` (`_ingest_posting`)
- Test: `tests/test_pipeline.py`, `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: `summarize(..., today=)` → `JobSummary` (Task 4); `update_job_pipeline(..., company=, published_at=)` (Task 3).
- Produces: after ingesting a `job_posting`/`lead`, `jobs.company` and `jobs.published_at` reflect the `JobSummary` when it carried values; otherwise the fetcher/insert values stand.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_pipeline.py` (imports at top already include `MagicMock, patch`; add `from datetime import datetime, timezone, timedelta` if not present — check and add only what's missing):

```python
def test_run_fetch_stores_extracted_company_and_posted_date(conn, source):
    raw_jobs = [RawJob(url="http://example.com/job/1", title="", company="", raw_text="<p>desc</p>")]
    client = _mock_client(
        classify_resp='{"type": "job_posting", "reason": "full description"}',
        summarize_resp='{"title": "Team Lead @ Zivid", "company": "Zivid", "headline": "H", '
                       '"summary": "S", "posted_date": "2026-08-17"}',
        evaluate_resp='{"score": 0.9, "reasoning": "match"}',
    )
    with patch("app.pipeline.GenericListingFetcher") as MockFetcher:
        MockFetcher.return_value.fetch.return_value = raw_jobs
        _drain(run_fetch(source, conn, client, "llama3.2", "browser-profile"))
    job = q.get_jobs(conn)[0]
    assert job["company"] == "Zivid"
    assert job["published_at"] == "2026-08-17"


def test_run_fetch_falls_back_to_processing_time_when_no_posted_date(conn, source):
    raw_jobs = [RawJob(url="http://example.com/job/1", title="", company="", raw_text="<p>desc</p>")]
    client = _mock_client(
        classify_resp='{"type": "job_posting", "reason": "full description"}',
        summarize_resp='{"title": "T", "company": "", "headline": "H", "summary": "S", "posted_date": ""}',
        evaluate_resp='{"score": 0.9, "reasoning": "match"}',
    )
    with patch("app.pipeline.GenericListingFetcher") as MockFetcher:
        MockFetcher.return_value.fetch.return_value = raw_jobs
        _drain(run_fetch(source, conn, client, "llama3.2", "browser-profile"))
    job = q.get_jobs(conn)[0]
    assert job["published_at"] is not None
    assert job["published_at"][:10] == job["fetched_at"][:10]
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_pipeline.py -k "extracted_company or falls_back_to_processing" -q`
Expected: FAIL — `test_run_fetch_stores_extracted_company_and_posted_date` fails (`job["company"] == ""`, `published_at` is the insert time not `"2026-08-17"`) because `_ingest_posting` does not yet forward `company`/`published_at` to `update_job_pipeline`. (`test_run_fetch_falls_back_to_processing_time...` may already pass — that's fine.)

- [ ] **Step 3: Implement**

In `app/pipeline.py`:

At the top of the file, add `from datetime import datetime, timezone` (check existing imports first — add only if missing).

In `_ingest_posting`, the job_posting/lead branch. Change the `summarize` call and the `update_job_pipeline` call:

```python
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
            company=job_summary.company,
            published_at=job_summary.posted_date,
        )
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_pipeline.py -q`
Expected: PASS.

- [ ] **Step 5: Repurpose the now-invalid routes test**

In `tests/test_routes_jobs.py`, `test_job_list_omits_published_date_when_unknown` is no longer meaningful (new jobs always have a date). **Replace** it with:

```python
def test_job_list_shows_processing_time_when_no_posting_date(client, conn):
    # _seed inserts via insert_job with no published_at -> defaults to now
    _seed(conn)
    resp = client.get("/jobs")
    assert resp.status_code == 200
    assert "today" in resp.text  # time_ago(now) == "today"
```

- [ ] **Step 6: Run the routes suite**

Run: `python -m pytest tests/test_routes_jobs.py -q`
Expected: PASS.

- [ ] **Step 7: Full suite**

Run: `python -m pytest -q`
Expected: PASS. If any other test asserted a NULL `published_at` or unpacked `summarize` as a tuple, fix it the same way (dataclass access / expect a processing-time value).

- [ ] **Step 8: Commit**

```bash
git add app/pipeline.py tests/test_pipeline.py tests/test_routes_jobs.py
git commit -m "feat: pipeline persists extracted company and posted_date

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_017zYcggdHo3NWya3BrLBoe6"
```

---

## Task 6: Age-label layout fix (CSS)

The badge `.job-age` (`margin-left: auto`) is pushed to the right edge of its flex row and slides under the absolutely-positioned `.job-link-icon` (`right: 0.5rem`, or `2.3rem` when a `.job-select-wrap` checkbox is present) plus the checkbox. Reserve right-side space on the two header rows so the badge stops left of the icon.

**Files:**
- Modify: `app/templates/base.html` (CSS block, ~lines 208, 227; check the `@media` block ~476-482)

**Interfaces:** none (CSS only).

- [ ] **Step 1: Edit the desktop rules**

In `app/templates/base.html`, change line ~208 from:

```css
    .job-row-header { display: flex; align-items: baseline; gap: 0.9rem; padding-right: 0; }
```

to:

```css
    .job-row-header { display: flex; align-items: baseline; gap: 0.9rem; padding-right: 2.4rem; }
    .job-row-content:has(.job-select-wrap) .job-row-header { padding-right: 4.2rem; }
```

and line ~227 from:

```css
    .job-detail-title-row { display: flex; align-items: baseline; gap: 0.75rem; padding-right: 0; }
```

to:

```css
    .job-detail-title-row { display: flex; align-items: baseline; gap: 0.75rem; padding-right: 2.4rem; }
    .job-row-expanded:has(.job-select-wrap) .job-detail-title-row { padding-right: 4.2rem; }
```

- [ ] **Step 2: Keep the mobile override honest**

In the `@media` block (~line 481) there is:

```css
      .job-row-header { display: block; padding-right: 0; }
```

Leave `padding-right: 0` here — on mobile `.job-row-content` is `display:block` and the icon/checkbox `float:right`, so inline content (including `.job-age`) wraps around them rather than under them. Add a matching reset for the detail row so the desktop value doesn't leak in; change that line to:

```css
      .job-row-header, .job-detail-title-row { display: block; padding-right: 0; }
```

(Confirm `.job-detail-title-row` isn't already listed elsewhere in the media block; if it is, merge rather than duplicate.)

- [ ] **Step 3: Manual visual check** (no automated test — layout)

Start the dev server against a throwaway DB (see the `run-dev-server` skill). Then, on `/jobs`:
1. A collapsed card with an age badge — badge sits to the **left** of the 🔗 icon, not under it, with the bulk-select checkbox present.
2. On `/jobs/<id>` (a detail page, checkbox absent) — expanded header badge clears the 🔗 icon.
3. Narrow the viewport below the mobile breakpoint — badge wraps cleanly, no overlap with the floated icon/checkbox.

Adjust the `2.4rem` / `4.2rem` values if the gap looks too wide or still overlaps (icon glyph width varies by platform font).

- [ ] **Step 4: Commit**

```bash
git add app/templates/base.html
git commit -m "fix: age badge no longer overlaps the permalink icon and checkbox

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_017zYcggdHo3NWya3BrLBoe6"
```

---

## Task 7: Full verification + manual pipeline check

- [ ] **Step 1: Full test suite**

Run: `python -m pytest -q`
Expected: all green.

- [ ] **Step 2: Manual end-to-end on the dev server**

Against a throwaway DB copy (per the `run-dev-server` skill), add a real LinkedIn job by URL (a `generic_listing` path) or run a fetch for an existing generic source, and confirm:
- the resulting job has a non-empty `published_at` (either an extracted date or the fetch time),
- `company` is populated when the posting names an employer,
- the age badge renders and is positioned correctly.

Leave the dev server running and hand the URL to the user for their own check (per the project's UI-handoff rule) before offering to merge.

- [ ] **Step 3: Update the backlog**

Remove the line `- bug: Age label on collapsed job card overlaps with link and checkbox. It should be left of the link icon.` from `BACKLOG.md`. Commit:

```bash
git add BACKLOG.md
git commit -m "chore: drop fixed age-label backlog item

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_017zYcggdHo3NWya3BrLBoe6"
```

---

## Task 8: Final-review fixes + `/fetch` last-run age

Addresses the two Important findings from the whole-branch review, folds in the safe Minor cleanups, and adds the newly-requested `/fetch` change. Base commit: `e553eaa`.

**Files:**
- Modify: `app/pipeline.py` (`_ingest_posting` + its 3 call sites)
- Modify: `app/ai/summarize.py` (validator hardening, UTC default, prompt prefix, PEP8)
- Modify: `app/templates/fetch/_table.html` (last-run → `age` filter)
- Test: `tests/test_pipeline.py`, `tests/test_summarize.py`, `tests/test_queries.py`, `tests/test_routes_jobs.py`, `tests/test_routes_fetch.py`

**Interfaces:**
- Produces: `_ingest_posting(..., *, url, progress_prefix="", preserve_existing_metadata: bool = False)`. When `True`, the `update_job_pipeline` call passes `company=""` and `published_at=""` so existing column values (a fetcher-supplied date/org, or the insert-time fallback) are kept regardless of what `summarize` extracted.

### Important fix 1 + 2 — don't let LLM dates overwrite trusted values / drift on reprocess

- [ ] **Step 1: Failing tests** in `tests/test_pipeline.py`:

```python
def test_run_fetch_keeps_fetcher_published_at_and_company_over_llm(conn, source):
    raw_jobs = [RawJob(
        url="http://example.com/job/1", title="T", company="FetcherCo", raw_text="<p>d</p>",
        published_at="2026-07-01T00:00:00+00:00",
    )]
    client = _mock_client(
        classify_resp='{"type": "job_posting", "reason": "full"}',
        summarize_resp='{"title": "T", "company": "LLMGuess", "headline": "H", "summary": "S", '
                       '"posted_date": "2026-08-20"}',
        evaluate_resp='{"score": 0.9, "reasoning": "m"}',
    )
    with patch("app.pipeline.GenericListingFetcher") as MockFetcher:
        MockFetcher.return_value.fetch.return_value = raw_jobs
        _drain(run_fetch(source, conn, client, "llama3.2", "browser-profile"))
    job = q.get_jobs(conn)[0]
    assert job["published_at"] == "2026-07-01T00:00:00+00:00"
    assert job["company"] == "FetcherCo"


def test_run_reprocess_job_does_not_overwrite_existing_published_at(conn, source):
    jid = q.insert_job(
        conn, source_id=source["id"], url="http://example.com/job/1", title="T",
        company="Acme", raw_text="<p>posted 2 weeks ago</p>", published_at="2026-06-01",
    )
    q.update_job_pipeline(conn, jid, simplified_content="s", content_type="job_posting", summary="s")
    client = _mock_client(
        '{"type": "job_posting", "reason": "full"}',
        '{"title": "T", "company": "NewGuess", "headline": "H", "summary": "S", "posted_date": "2026-08-25"}',
        '{"score": 0.9, "reasoning": "m"}',
    )
    job = q.get_job(conn, jid)
    _drain(run_reprocess_job(conn, client, "llama3.2", job, q.get_scenarios(conn), q.get_profile(conn)))
    updated = q.get_job(conn, jid)
    assert updated["published_at"] == "2026-06-01"
    assert updated["company"] == "Acme"
```

- [ ] **Step 2:** run both — expect FAIL (LLM values currently win).

Run: `python -m pytest tests/test_pipeline.py -k "keeps_fetcher or does_not_overwrite_existing" -q`

- [ ] **Step 3: Implement.** In `app/pipeline.py`:

Add the param to `_ingest_posting`'s signature (after `progress_prefix`):

```python
    *,
    url: str,
    progress_prefix: str = "",
    preserve_existing_metadata: bool = False,
) -> Generator[str, None, None]:
```

In the job_posting/lead branch, change the two args to `update_job_pipeline`:

```python
            company="" if preserve_existing_metadata else job_summary.company,
            published_at="" if preserve_existing_metadata else job_summary.posted_date,
```

Update the three call sites:
- `run_fetch` (~line 154): add `preserve_existing_metadata=raw.published_at is not None,`
- `run_add_job` (~line 185): leave as-is (defaults to `False`).
- `run_reprocess_job` (~line 204): add `preserve_existing_metadata=job["published_at"] is not None,`

- [ ] **Step 4:** run `python -m pytest tests/test_pipeline.py -q` — expect PASS (incl. the existing `test_run_fetch_stores_extracted_company_and_posted_date`, whose `RawJob` has no `published_at` so the flag is `False`).

### `summarize.py` hardening (Minor findings)

- [ ] **Step 5: Tests** in `tests/test_summarize.py` (`_TODAY = date(2026, 8, 31)` already defined by Task 4):

```python
def test_summarize_accepts_datetime_shaped_posted_date():
    r = '{"title": "T", "headline": "H", "summary": "S", "posted_date": "2026-08-17T09:00:00"}'
    assert summarize(_mock_client(r), "llama3.2", "x", today=_TODAY).posted_date == "2026-08-17"


def test_summarize_rejects_posted_date_more_than_a_year_old():
    r = '{"title": "T", "headline": "H", "summary": "S", "posted_date": "2024-01-01"}'
    assert summarize(_mock_client(r), "llama3.2", "x", today=_TODAY).posted_date == ""


def test_summarize_lead_path_gets_no_today_prefix():
    client = _mock_client('{"organizations": ["Acme"], "headline": "H"}')
    summarize(client, "llama3.2", "Posted by U1:\n\nmsg", content_type="lead", today=_TODAY)
    user_msg = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    assert "Today is" not in user_msg
```

- [ ] **Step 6: Implement** in `app/ai/summarize.py`:

Two blank lines between the `JobSummary` class and `_SYSTEM`.

`_valid_posted_date` — try a datetime-shaped parse and add a ~1-year lower bound:

```python
def _valid_posted_date(value: object, today: date) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    text = value.strip()
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.fromisoformat(text).date()
        except ValueError:
            return ""
    if parsed > today or parsed < today - timedelta(days=366):
        return ""
    return parsed.isoformat()
```

Add `from datetime import date, datetime, timedelta` (replacing the `from datetime import date` line).

`today` default → UTC, and only prefix the user message on the non-lead path:

```python
    today = today if today is not None else datetime.now(timezone.utc).date()
```
(add `timezone` to the datetime import: `from datetime import date, datetime, timedelta, timezone`)

```python
    user_content = simplified_content[:6000]
    if not raw_lead:
        user_content = f"Today is {today.isoformat()}.\n\n{user_content}"
```
and use `user_content` in the `messages` list.

- [ ] **Step 7:** `python -m pytest tests/test_summarize.py -q` — PASS.

### `/fetch` last-run age (new scope)

- [ ] **Step 8: Test** in `tests/test_routes_fetch.py` — a source with a recent successful `fetch_runs` row renders a relative age, not a raw timestamp. Follow the existing fixture style in that file (look at `test_fetch_table_links_source_name_to_sources_page`). Assert the "Last run" cell contains e.g. `"ago"` or `"just now"` and does **not** contain the raw `YYYY-MM-DD HH:MM:SS` string.

- [ ] **Step 9: Implement** — `app/templates/fetch/_table.html`, the "Last run" cell (line ~29). Change:

```html
          <span style="white-space:nowrap;">{% if stats and stats.last_success_at %}{{ stats.last_success_at }}{% else %}—{% endif %}</span>
```
to:
```html
          <span style="white-space:nowrap;"{% if stats and stats.last_success_at %} title="{{ stats.last_success_at }} UTC"{% endif %}>{% if stats and stats.last_success_at %}{{ stats.last_success_at | age }}{% else %}—{% endif %}</span>
```

The `age` filter (`app/dates.py`) already renders `just now` / `5m ago` / `3h ago` / `2d ago` and is registered in `app/template_env.py`. `last_success_at` is `fetch_runs.completed_at` (`datetime('now')` format, naive → `age` treats it as UTC).

- [ ] **Step 10:** `python -m pytest tests/test_routes_fetch.py -q` — PASS.

### Remaining Minor cleanups

- [ ] **Step 11:**
  - `tests/test_pipeline.py`: remove the unused `datetime, timezone, timedelta` import line added in Task 5 (only if nothing in the file now uses them — the Step 1 tests here don't).
  - `tests/test_queries.py` `test_insert_job_published_at_defaults_to_processing_time` and `tests/test_pipeline.py` `test_run_fetch_falls_back_to_processing_time_when_no_posted_date`: change the `[:10]` date-prefix comparison to an exact `assert job["published_at"] == job["fetched_at"]` (same INSERT statement ⇒ SQLite freezes `datetime('now')`, so they're byte-identical; removes the midnight-rollover flake).
  - `tests/test_routes_jobs.py` `test_job_list_shows_processing_time_when_no_posting_date`: scope the assertion — `assert '<span class="job-age">today</span>' in resp.text` instead of the bare `"today" in resp.text`.
  - `tests/test_summarize.py`: restore the short "why this case exists" comments the Task 1 rewrite dropped on `test_summarize_handles_null_source_link_without_crashing` (model may emit `null` for optional fields) and `test_summarize_lead_retains_the_original_message_as_the_summary` (leads keep the original, never an AI rewrite).

- [ ] **Step 12: Full suite + commit**

Run: `python -m pytest -q` — all green.

Stage **explicit paths only** (the repo root has untracked special files that break `git add -A`/`git add .`, and `.superpowers/` scratch must not be committed):

```bash
git add app/pipeline.py app/ai/summarize.py app/templates/fetch/_table.html \
        tests/test_pipeline.py tests/test_summarize.py tests/test_queries.py \
        tests/test_routes_jobs.py tests/test_routes_fetch.py
git commit -m "fix: preserve trusted dates in ingest; /fetch last-run age; review nits

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_017zYcggdHo3NWya3BrLBoe6"
```

(If the summarize import line ended up needing `date` only after all, adjust — the file must import exactly what it uses.)

- [ ] **Step 13: Manual re-check** — restart nothing; the dev server auto-reads templates. Reload `http://127.0.0.1:8931/fetch` and confirm the "Last run" column shows relative ages with the exact time on hover.

---

## Task 9: Drop `@ Organization` from the generated job title

Now that `company` is a structured field (populated by `summarize` for generic jobs, by the fetcher for finn/eawork/slack) and templates render it as its own chip next to the title, the `@ Organization` suffix in the AI-generated title is redundant. Remove it from the job-posting prompt only. The Slack-lead prompt (`_LEAD_SYSTEM`) is unchanged — a lead's title legitimately *is* the org name(s) because there's no single role. Base commit: Task 8's commit.

No code parses `@` out of the title (verified: the only ` @ ` reference in `app/` is the `_SYSTEM` format string itself); templates display `job.title` and `job.company` independently.

**Files:**
- Modify: `app/ai/summarize.py` (`_SYSTEM` only)
- Test: `tests/test_summarize.py`

- [ ] **Step 1: Failing test** in `tests/test_summarize.py`:

```python
def test_summarize_title_spec_excludes_organization():
    client = _mock_client('{"title": "T", "headline": "H", "summary": "S"}')
    summarize(client, "llama3.2", "content", today=_TODAY)
    system = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert "(remote/hybrid/onsite)>" in system          # title format kept, minus the org
    assert "@ Organization" not in system
    assert "include the organization name" not in system
```

- [ ] **Step 2: Run — expect FAIL**

Run: `python -m pytest tests/test_summarize.py::test_summarize_title_spec_excludes_organization -q`
Expected: FAIL (`@ Organization` still present).

- [ ] **Step 3: Implement** — in `app/ai/summarize.py` `_SYSTEM`:

Change the title field in the JSON shape:
```python
    '{"title": "<Role - Location (remote/hybrid/onsite)>", '
```
(was `"<Role - Location (remote/hybrid/onsite) @ Organization>"`)

Change the title instruction sentence — drop the org clause:
```python
    "For title: use the role as given in the posting, or a concise generated one if "
    "unclear; include location with remote/hybrid/onsite status. Be factual and brief. "
    "No invented details. "
```
(was `"... remote/hybrid/onsite status; include the organization name. Be factual and brief. No invented details. "`)

Leave everything else in `_SYSTEM` (including the `company` field and its instruction) and all of `_LEAD_SYSTEM` untouched.

- [ ] **Step 4: Update the illustrative mock titles in `tests/test_summarize.py`**

These are passthrough test data, not format assertions, but they should reflect the new prompt. Replace the three occurrences of `ML Engineer - Remote @ Acme` → `ML Engineer - Remote` and `ML Engineer - Paris @ Acme` → `ML Engineer - Paris` in this file (and the matching `assert result.title == ...` lines). Do **not** touch `tests/test_pipeline.py` / `tests/test_routes_scenarios.py` — their mock titles are arbitrary strings that exercise passthrough and remain valid.

- [ ] **Step 5: Run**

Run: `python -m pytest tests/test_summarize.py -q`
Expected: PASS.

- [ ] **Step 6: Full suite + commit**

Run: `python -m pytest -q` — all green.

```bash
git add app/ai/summarize.py tests/test_summarize.py
git commit -m "feat: drop redundant @ Organization from generated job titles

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_017zYcggdHo3NWya3BrLBoe6"
```

---

## Task 10: Extend the `age` filter past days (weeks / months / years)

The `age` filter (`app/dates.py`) currently tops out at `"Nd ago"`, so the `/fetch` "Last run" column shows `"22d ago"` for a 3-week-old run and `"380d ago"` for an old disabled source. Extend it with weeks / months / years. This affects every `age` consumer (task cards, task list/detail, the fetch table) — all benefit; a task finished 3 weeks ago reading `"3w ago"` is an improvement, not a regression. `time_ago` (the job-age filter) is a **separate** function and stays `"N days ago"` — do not touch it. Base commit: Task 9's commit (`f51ba4f`).

**Files:**
- Modify: `app/dates.py` (`age` only)
- Test: `tests/test_dates.py`

- [ ] **Step 1: Failing tests** — add to `tests/test_dates.py` (after the existing `age` tests):

```python
def test_age_weeks():
    ts = (datetime.now(timezone.utc) - timedelta(days=15)).isoformat()
    assert age(ts) == "2w ago"


def test_age_months():
    ts = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    assert age(ts) == "3mo ago"


def test_age_years():
    ts = (datetime.now(timezone.utc) - timedelta(days=800)).isoformat()
    assert age(ts) == "2y ago"


def test_age_six_days_still_days():
    ts = (datetime.now(timezone.utc) - timedelta(days=6)).isoformat()
    assert age(ts) == "6d ago"
```

- [ ] **Step 2: Run — expect FAIL**

Run: `python -m pytest tests/test_dates.py -k "weeks or months or years" -q`
Expected: FAIL (`"15d ago" != "2w ago"` etc.).

- [ ] **Step 3: Implement** — in `app/dates.py`, replace the final line of `age` (`return f"{hours // 24}d ago"`) with:

```python
    days = hours // 24
    if days < 7:
        return f"{days}d ago"
    if days < 30:
        return f"{days // 7}w ago"
    if days < 365:
        return f"{days // 30}mo ago"
    return f"{days // 365}y ago"
```

Update the docstring to reflect the new range, e.g.:
```python
    """Render an ISO-8601 timestamp as a short coarse relative string:
    'just now', '5m ago', '3h ago', '2d ago', '3w ago', '5mo ago', '2y ago'.
    Finer-grained near zero than time_ago; used for task and last-fetch ages."""
```

- [ ] **Step 4: Run**

Run: `python -m pytest tests/test_dates.py -q`
Expected: PASS (the existing `test_age_days` with 50h → `"2d ago"` still holds; `days=2 < 7`).

- [ ] **Step 5: Full suite + commit**

Run: `python -m pytest -q` — all green (check `tests/test_routes_fetch.py` and any task-age route/render tests still pass — the fetch test uses a fresh "just now" run, task tests use recent timestamps).

```bash
git add app/dates.py tests/test_dates.py
git commit -m "feat: age filter extends to weeks/months/years

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_017zYcggdHo3NWya3BrLBoe6"
```

---

## Task 11: Collapsed job-card first-line alignment (CSS)

Controller-implemented directly (visual iteration against the dev server, not a subagent). Follow-up to the Task 6 badge fix — the user reported the title/company/age baseline and the permalink-icon + checkbox alignment still looking off on the collapsed card's first line.

Diagnosis (measured in-browser): `.job-title` inherited `line-height: 1.5` (Crimson Text serif), giving it a much taller line box than the sans `.job-company` / `.job-age`, so baseline alignment read as "small text floating high". The `.job-link-icon` / `.job-select-wrap` were pinned to `top: 0.3rem` (card top edge), ~7px above the title's first line.

Changes in `app/templates/base.html` (collapsed card only — expanded card's `.job-detail-*` and the mobile float layout untouched; the `> ` child combinator scopes the icon/checkbox `top` overrides to `_row.html`'s structure, since `_feedback.html` puts them as direct children of `.job-row-expanded`):
- `.job-title` → add `line-height: 1.3`
- `.job-row-header` → add `line-height: 1.3`
- `.job-link-icon` → `height: 1.85rem` → `1.6rem`
- new: `.job-row-content > .job-link-icon { top: 0.6rem; }` and `.job-row-content > .job-select-wrap { top: 0.5rem; }`

Verified: measured centers within ~0.5px (title 432 / company 432 / age 432.5 / icon 431.5 / checkbox 432); screenshots at desktop (1-line and wrapped title), expanded detail, and mobile all clean; `tests/test_routes_jobs.py` + `tests/test_routes_fetch.py` green (216 passed).

Commit: `app/templates/base.html` only.

---

## Self-Review

**Spec coverage:**
- summarize `company` + `posted_date` fields, `YYYY-MM-DD`, today injected, future-date rejection → Task 4 ✓
- `JobSummary` dataclass → Task 1 ✓
- `_ingest_posting` sources `today`, threads fields → Task 5 ✓
- `update_job_pipeline` `COALESCE(NULLIF(...))` for both columns → Task 3 ✓
- `insert_job` `COALESCE(?, datetime('now'))` → Task 2 ✓
- No backfill / no migration → nothing to do; noted in Global Constraints ✓
- Templates keep `{% if job.published_at %}` guard → unchanged, noted ✓ (Task 5 repurposes the one test that assumed unknown dates)
- Age-label CSS on `.job-row-header` + `.job-detail-title-row`, `:has(.job-select-wrap)` variants, mobile check → Task 6 ✓
- `time_ago` wording unchanged → Global Constraints ✓
- No real-vs-fallback UI distinction → Global Constraints ✓
- Tests: summarize schema/validation, insert_job default, update_job_pipeline coalesce, pipeline wiring → Tasks 1-5 ✓
- Manual visual check for label position → Task 6 Step 3, Task 7 Step 2 ✓

**Placeholder scan:** `2.4rem` / `4.2rem` are concrete starting values with an explicit visual-tuning step — acceptable for a CSS layout task. No TBD/TODO/"handle edge cases".

**Type consistency:** `JobSummary(title, company, headline, summary, posted_date)` used consistently across Tasks 1/4/5. `summarize(..., today=)` required-kw introduced in Task 4, all call sites updated in Tasks 4-5. `update_job_pipeline(..., company="", published_at="")` defined in Task 3, called with those names in Task 5. `_valid_posted_date(value, today)` defined and used in Task 4.
