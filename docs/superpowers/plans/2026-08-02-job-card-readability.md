# Job Card Readability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give job overview cards a real one-line hook and a normalized title, and make the whole card clickable instead of requiring a small "Details" button.

**Architecture:** `summarize()` becomes a single structured-JSON AI call returning `(title, headline, summary)` instead of a plain summary string, reusing the `extract_json`/`json.loads` pattern `classify()` already uses. `title` and a new `headline` column are persisted via `update_job_pipeline`, with `title` protected against being blanked by a failed/empty AI response. A one-time backfill route regenerates title/headline/summary for jobs that predate this change. The `_row.html` card template displays the headline (falling back to the old truncated-summary preview), drops the redundant company span, and moves the expand `hx-get` from a button onto the whole card.

**Tech Stack:** FastAPI + Jinja2 (htmx-driven partial swaps), sqlite3, pytest, OpenAI-compatible client (mocked in tests via `unittest.mock`).

## Global Constraints

- Migrations in this codebase are hard-downtime, no dual-schema compatibility shims (see `docs/superpowers/specs/2026-08-02-job-card-readability-design.md` Overview and project CLAUDE.md "Database migrations").
- A failed/empty AI response must never blank out a previously-good `title` (spec §3).
- `_row.html` (card) drops the company span; `_feedback.html` (detail view) keeps it (spec §5).
- Commit after each task, per project CLAUDE.md "Commit frequently".

---

### Task 1: `headline` column + migration

**Files:**
- Modify: `app/db/schema.py`
- Test: `tests/test_schema.py`

**Interfaces:**
- Produces: `jobs.headline` column, `TEXT NOT NULL DEFAULT ''`, present after `init_db()` runs on any DB (fresh or pre-existing).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_schema.py`:

```python
def test_jobs_table_has_headline_column(conn):
    init_db(conn)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    assert "headline" in cols


def test_init_db_migrates_jobs_adds_headline_column(conn):
    conn.executescript(
        """
        CREATE TABLE sources (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            fetcher_type TEXT NOT NULL CHECK(fetcher_type IN ('http', 'playwright', 'slack', 'finn_listing')),
            enabled INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE jobs (
            id INTEGER PRIMARY KEY,
            source_id INTEGER NOT NULL REFERENCES sources(id),
            url TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL DEFAULT '',
            company TEXT NOT NULL DEFAULT '',
            raw_text TEXT NOT NULL DEFAULT '',
            simplified_content TEXT NOT NULL DEFAULT '',
            summary TEXT NOT NULL DEFAULT '',
            content_type TEXT CHECK(content_type IN ('job_posting', 'lead', 'irrelevant', 'error')),
            fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
            status TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('new', 'accepted', 'rejected', 'invalid')),
            feedback_note TEXT,
            feedback_scenario_id INTEGER REFERENCES scenarios(id)
        );
        """
    )
    conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s', 'http://x', 'http')")
    conn.execute("INSERT INTO jobs (source_id, url, title) VALUES (1, 'http://job/1', 'Existing Title')")
    conn.commit()

    init_db(conn)

    row = conn.execute("SELECT title, headline FROM jobs WHERE url = 'http://job/1'").fetchone()
    assert row["title"] == "Existing Title"
    assert row["headline"] == ""

    # Idempotent: running init_db again doesn't error or duplicate columns.
    init_db(conn)
    cols = [row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()]
    assert cols.count("headline") == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_schema.py -k headline -v`
Expected: FAIL — `headline` column does not exist.

- [ ] **Step 3: Add the migration**

In `app/db/schema.py`, add a new migration function following the same additive pattern as `_migrate_jobs_add_feedback_scenario_id`, and call it from `init_db`:

```python
def _migrate_jobs_add_headline(conn: sqlite3.Connection) -> None:
    # Purely additive column, no CHECK/constraint change and nothing to drop,
    # so a plain ALTER TABLE suffices instead of a full jobs_new/copy/drop/rename cycle.
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='jobs'"
    ).fetchone()
    if row is None or "headline" in row[0]:
        return
    conn.execute("ALTER TABLE jobs ADD COLUMN headline TEXT NOT NULL DEFAULT ''")
    conn.commit()
```

Update `init_db`:

```python
def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL)
    _migrate_sources_fetcher_type(conn)
    _migrate_jobs_scores_to_table(conn)
    _migrate_jobs_add_feedback_scenario_id(conn)
    _migrate_jobs_add_headline(conn)
```

Also add `headline TEXT NOT NULL DEFAULT ''` to the `jobs` table definition inside `_DDL`, right after the `summary` column, so fresh databases get it directly:

```python
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES sources(id),
    url TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL DEFAULT '',
    company TEXT NOT NULL DEFAULT '',
    raw_text TEXT NOT NULL DEFAULT '',
    simplified_content TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    headline TEXT NOT NULL DEFAULT '',
    content_type TEXT CHECK(content_type IN ('job_posting', 'lead', 'irrelevant', 'error')),
    fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
    status TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('new', 'accepted', 'rejected', 'invalid')),
    feedback_note TEXT,
    feedback_scenario_id INTEGER REFERENCES scenarios(id)
);
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_schema.py -v`
Expected: PASS (all tests, including the two new ones and every pre-existing one).

- [ ] **Step 5: Commit**

```bash
git add app/db/schema.py tests/test_schema.py
git commit -m "feat: add headline column to jobs table"
```

---

### Task 2: `update_job_pipeline` gains title/headline, add `get_jobs_missing_headline`

**Files:**
- Modify: `app/db/queries.py`
- Test: `tests/test_queries.py`

**Interfaces:**
- Consumes: `jobs.headline` column from Task 1.
- Produces:
  - `update_job_pipeline(conn, job_id, *, simplified_content: str, content_type: str, title: str | None = None, summary: str = "", headline: str = "") -> None` — `title=None` (the default) leaves the existing title untouched; a non-`None` string overwrites it. `summary`/`headline` always overwrite (matches existing `summary` behavior).
  - `get_jobs_missing_headline(conn) -> list[dict]` — jobs with `headline = ''`, `content_type IN ('job_posting', 'lead')`, `simplified_content != ''`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_queries.py`:

```python
def test_update_job_pipeline_sets_title_and_headline(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="Scraped Title", company="C", raw_text="r")
    q.update_job_pipeline(
        conn, jid,
        simplified_content="clean text",
        content_type="job_posting",
        title="AI Title",
        summary="Good role",
        headline="Great hook",
    )
    job = q.get_job(conn, jid)
    assert job["title"] == "AI Title"
    assert job["headline"] == "Great hook"


def test_update_job_pipeline_leaves_title_unchanged_when_not_passed(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="Scraped Title", company="C", raw_text="r")
    q.update_job_pipeline(
        conn, jid,
        simplified_content="clean text",
        content_type="irrelevant",
    )
    job = q.get_job(conn, jid)
    assert job["title"] == "Scraped Title"
    assert job["headline"] == ""


def test_get_jobs_missing_headline_filters_correctly(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    # Eligible: job_posting, has simplified_content, no headline yet.
    jid_missing = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T1", company="C", raw_text="r")
    q.update_job_pipeline(conn, jid_missing, simplified_content="clean", content_type="job_posting", summary="S1")
    # Not eligible: already has a headline.
    jid_has_headline = q.insert_job(conn, source_id=source_id, url="http://job/2", title="T2", company="C", raw_text="r")
    q.update_job_pipeline(conn, jid_has_headline, simplified_content="clean", content_type="job_posting", summary="S2", headline="Already done")
    # Not eligible: irrelevant content type.
    jid_irrelevant = q.insert_job(conn, source_id=source_id, url="http://job/3", title="T3", company="C", raw_text="r")
    q.update_job_pipeline(conn, jid_irrelevant, simplified_content="clean", content_type="irrelevant")
    # Not eligible: no simplified_content.
    jid_unsimplified = q.insert_job(conn, source_id=source_id, url="http://job/4", title="T4", company="C", raw_text="r")

    missing = q.get_jobs_missing_headline(conn)
    assert [j["id"] for j in missing] == [jid_missing]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_queries.py -k "title_and_headline or leaves_title or missing_headline" -v`
Expected: FAIL — `update_job_pipeline()` raises `TypeError` for unexpected `title`/`headline` kwargs, and `get_jobs_missing_headline` doesn't exist.

- [ ] **Step 3: Implement**

In `app/db/queries.py`, replace `update_job_pipeline`:

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
) -> None:
    conn.execute(
        """UPDATE jobs SET
            simplified_content = ?,
            content_type = ?,
            title = COALESCE(?, title),
            summary = ?,
            headline = ?
        WHERE id = ?""",
        (simplified_content, content_type, title, summary, headline, job_id),
    )
    conn.commit()
```

Add `get_jobs_missing_headline` right after `get_jobs` (before `get_job_counts`):

```python
def get_jobs_missing_headline(conn: sqlite3.Connection) -> list[dict]:
    sql = """
        SELECT * FROM jobs
        WHERE headline = ''
          AND content_type IN ('job_posting', 'lead')
          AND simplified_content != ''
        ORDER BY fetched_at
    """
    return _rows_to_dicts(conn.execute(sql).fetchall())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_queries.py -v`
Expected: PASS (all tests, including the three new ones and every pre-existing one — the pre-existing `test_update_job_pipeline` doesn't pass `title`, so it exercises the `COALESCE` default path).

- [ ] **Step 5: Commit**

```bash
git add app/db/queries.py tests/test_queries.py
git commit -m "feat: persist AI-generated title/headline, add missing-headline query"
```

---

### Task 3: `summarize()` returns structured `(title, headline, summary)`

**Files:**
- Modify: `app/ai/summarize.py`
- Test: `tests/test_summarize.py`

**Interfaces:**
- Consumes: `app.ai.json_utils.extract_json(text: str) -> str` (already exists, used by `classify()`).
- Produces: `summarize(client, model, simplified_content: str) -> tuple[str, str, str]` — `(title, headline, summary)`. Returns `("", "", "")` on any failure (malformed JSON or API error).

- [ ] **Step 1: Write the failing tests**

Replace the full contents of `tests/test_summarize.py`:

```python
from unittest.mock import MagicMock
from app.ai.summarize import summarize


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
    client = _mock_client(response)
    title, headline, summary = summarize(client, "llama3.2", "long job description text")
    assert title == "ML Engineer - Remote @ Acme"
    assert headline == "Fully remote with a $180k+ ceiling"
    assert summary == "**Role:** ML Engineer"


def test_summarize_calls_llm_once():
    client = _mock_client('{"title": "T", "headline": "H", "summary": "S"}')
    summarize(client, "llama3.2", "content")
    assert client.chat.completions.create.call_count == 1


def test_summarize_strips_markdown_code_fence():
    response = '```json\n{"title": "T", "headline": "H", "summary": "S"}\n```'
    client = _mock_client(response)
    result = summarize(client, "llama3.2", "content")
    assert result == ("T", "H", "S")


def test_summarize_returns_empty_strings_on_malformed_json():
    client = _mock_client("not json")
    result = summarize(client, "llama3.2", "content")
    assert result == ("", "", "")


def test_summarize_returns_empty_strings_on_api_error():
    client = MagicMock()
    client.chat.completions.create.side_effect = Exception("boom")
    result = summarize(client, "llama3.2", "content")
    assert result == ("", "", "")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_summarize.py -v`
Expected: FAIL — current `summarize()` returns a plain string, not a 3-tuple, and doesn't parse JSON.

- [ ] **Step 3: Implement**

Replace the full contents of `app/ai/summarize.py`:

```python
from __future__ import annotations
import json
import openai
from app.ai.json_utils import extract_json

_SYSTEM = (
    "Summarize this job posting. Respond with exactly this JSON shape: "
    '{"title": "<Role - Location (remote/hybrid/onsite) @ Organization>", '
    '"headline": "<one punchy sentence on the most compelling or notable detail>", '
    '"summary": "<concise markdown covering role, company, location/remote status, '
    'key requirements, compensation if mentioned, notable perks or red flags>"}. '
    "For title: use the role as given in the posting, or a concise generated one if "
    "unclear; include location with remote/hybrid/onsite status; include the "
    "organization name. Be factual and brief. No invented details."
)


def summarize(client: openai.OpenAI, model: str, simplified_content: str) -> tuple[str, str, str]:
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": simplified_content[:6000]},
            ],
            temperature=0.3,
        )
        data = json.loads(extract_json(resp.choices[0].message.content))
        return data.get("title", ""), data.get("headline", ""), data.get("summary", "")
    except Exception:
        return "", "", ""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_summarize.py -v`
Expected: PASS (all 5 tests).

- [ ] **Step 5: Commit**

```bash
git add app/ai/summarize.py tests/test_summarize.py
git commit -m "feat: have summarize() return a structured title/headline/summary"
```

---

### Task 4: Wire `run_fetch` / `run_reevaluate` to the new `summarize()` shape

**Files:**
- Modify: `app/pipeline.py:85-92` (`run_fetch`), `app/pipeline.py:134-144` (`run_reevaluate`)
- Test: `tests/test_pipeline.py`, `tests/test_routes_scenarios.py`

**Interfaces:**
- Consumes: `summarize(client, model, text) -> tuple[str, str, str]` (Task 3), `update_job_pipeline(..., title=..., headline=..., summary=...)` (Task 2).
- Produces: no new public interface — `run_fetch`/`run_reevaluate` keep their existing signatures and generator/return shapes.

- [ ] **Step 1: Update existing test mocks and write new failing tests**

In `tests/test_pipeline.py`, update every place that mocks a `summarize` response to use the new JSON shape:

- In `test_run_fetch_new_job_stored`, change:
  ```python
  summarize_choice.message.content = "Good ML role"
  ```
  to:
  ```python
  summarize_choice.message.content = '{"title": "ML Engineer - Remote @ Acme", "headline": "Great remote ML role", "summary": "Good ML role"}'
  ```

- In `test_run_fetch_yields_progress_and_logs_each_line`, `test_run_fetch_scores_against_every_scenario`, and `test_run_fetch_with_no_scenarios_still_summarizes`, change the second argument passed to `_mock_client(...)` from:
  ```python
  "Good ML role",
  ```
  to:
  ```python
  '{"title": "ML Engineer - Remote @ Acme", "headline": "Great remote ML role", "summary": "Good ML role"}',
  ```

Then add two new tests to `tests/test_pipeline.py`:

```python
def test_run_fetch_stores_ai_title_and_headline(conn, source):
    raw_jobs = [RawJob(url="http://example.com/job/1", title="scraped title", company="Acme", raw_text="<p>hi</p>")]
    client = _mock_client(
        '{"type": "job_posting", "reason": "full description"}',
        '{"title": "ML Engineer - Remote @ Acme", "headline": "Great remote ML role", "summary": "Good ML role"}',
        '{"score": 0.9, "reasoning": "Great match"}',
    )
    with patch("app.pipeline.HttpFetcher") as MockFetcher:
        MockFetcher.return_value.fetch.return_value = raw_jobs
        _drain(run_fetch(source, conn, client, "llama3.2", "browser-profile"))

    job = q.get_jobs(conn)[0]
    assert job["title"] == "ML Engineer - Remote @ Acme"
    assert job["headline"] == "Great remote ML role"


def test_run_fetch_keeps_scraped_title_when_ai_title_empty(conn, source):
    raw_jobs = [RawJob(url="http://example.com/job/1", title="scraped title", company="Acme", raw_text="<p>hi</p>")]
    client = _mock_client(
        '{"type": "job_posting", "reason": "full description"}',
        '{"title": "", "headline": "", "summary": "Good ML role"}',
        '{"score": 0.9, "reasoning": "Great match"}',
    )
    with patch("app.pipeline.HttpFetcher") as MockFetcher:
        MockFetcher.return_value.fetch.return_value = raw_jobs
        _drain(run_fetch(source, conn, client, "llama3.2", "browser-profile"))

    job = q.get_jobs(conn)[0]
    assert job["title"] == "scraped title"
    assert job["headline"] == ""
```

In `tests/test_routes_scenarios.py`, update the three `patch("app.pipeline.summarize", return_value="Updated summary")` occurrences (in `test_reevaluate_streams_progress_and_updates_jobs`, `test_reevaluate_skips_jobs_already_current`, `test_reevaluate_all_scenarios_streams_combined_progress`) to:

```python
patch("app.pipeline.summarize", return_value=("ML Engineer - Remote @ Acme", "Great hook", "Updated summary")),
```

Then add a new test to `tests/test_routes_scenarios.py`:

```python
def test_reevaluate_keeps_existing_title_when_ai_title_empty(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Must be remote", "must")
    source_id = q.insert_source(conn, "s", "http://x", "http")
    job_id = q.insert_job(conn, source_id=source_id, url="http://job/1", title="ML Eng", company="C", raw_text="r")
    q.update_job_pipeline(conn, job_id, simplified_content="clean", content_type="job_posting")

    with patch("app.pipeline.summarize", return_value=("", "", "Updated summary")), \
         patch("app.pipeline.evaluate", return_value=(0.75, "Good match")):
        client.post(f"/scenarios/{sid}/reevaluate")

    job = q.get_job(conn, job_id)
    assert job["title"] == "ML Eng"
    assert job["summary"] == "Updated summary"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_pipeline.py tests/test_routes_scenarios.py -v`
Expected: FAIL — `run_fetch`/`run_reevaluate` still unpack `summarize()`'s old single-string return as a plain string; the new tests fail with `ValueError: too many values to unpack` or the title/headline assertions fail.

- [ ] **Step 3: Implement**

In `app/pipeline.py`, replace the `run_fetch` summarize block (currently lines 85-92):

```python
            if content_type in ("job_posting", "lead"):
                ai_title, headline, job_summary = summarize(client, model, simplified)
                q.update_job_pipeline(
                    conn, job_id,
                    simplified_content=simplified,
                    content_type=content_type,
                    title=ai_title or raw.title,
                    headline=headline,
                    summary=job_summary,
                )
```

Replace the `run_reevaluate` loop body (currently lines 134-144):

```python
    for i, job in enumerate(to_evaluate, start=1):
        if job["simplified_content"]:
            ai_title, headline, new_summary = summarize(client, model, job["simplified_content"])
        else:
            ai_title, headline, new_summary = job["title"], job["headline"], job["summary"]
        score, reasoning = evaluate(client, model, profile, scenario, criteria, new_summary)
        q.update_job_pipeline(
            conn, job["id"],
            simplified_content=job["simplified_content"],
            content_type=job["content_type"],
            title=ai_title or job["title"],
            headline=headline,
            summary=new_summary,
        )
        q.upsert_job_score(conn, job["id"], scenario["id"], score, reasoning, current_hash)
        yield _progress(f"[{i}/{len(to_evaluate)}] Re-scored {score}: {job['title'] or job['url']}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_pipeline.py tests/test_routes_scenarios.py -v`
Expected: PASS (all tests).

- [ ] **Step 5: Run the full test suite to check for other regressions**

Run: `uv run pytest -v`
Expected: PASS. If anything outside these two files fails, it's a caller of `summarize()` or `update_job_pipeline()` not yet accounted for — inspect the failure and fix it before proceeding (do not skip past a red suite).

- [ ] **Step 6: Commit**

```bash
git add app/pipeline.py tests/test_pipeline.py tests/test_routes_scenarios.py
git commit -m "feat: wire fetch/re-evaluate pipeline to structured summarize() output"
```

---

### Task 5: `run_backfill_headlines` pipeline function

**Files:**
- Modify: `app/pipeline.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `q.get_jobs_missing_headline(conn)` (Task 2), `summarize()` (Task 3), `update_job_pipeline(..., title=..., headline=..., summary=...)` (Task 2).
- Produces: `run_backfill_headlines(conn, client, model) -> Generator[str, None, int]` — same generator/return shape as `run_reevaluate`, yields progress strings, returns the count of jobs updated.

- [ ] **Step 1: Write the failing tests**

In `tests/test_pipeline.py`, update the import line at the top from:
```python
from app.pipeline import run_fetch, FetchResult, _make_fetcher
```
to:
```python
from app.pipeline import run_fetch, FetchResult, _make_fetcher, run_backfill_headlines
```

Add:

```python
def test_run_backfill_headlines_updates_jobs_missing_headline(conn, source):
    job_id = q.insert_job(conn, source_id=source["id"], url="http://example.com/job/1", title="scraped title", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, job_id, simplified_content="clean", content_type="job_posting", summary="Old summary")
    client = MagicMock()
    choice = MagicMock()
    choice.message.content = '{"title": "ML Engineer - Remote @ Acme", "headline": "Great remote ML role", "summary": "New summary"}'
    client.chat.completions.create.return_value = MagicMock(choices=[choice])

    messages, count = _drain(run_backfill_headlines(conn, client, "llama3.2"))

    assert count == 1
    job = q.get_job(conn, job_id)
    assert job["title"] == "ML Engineer - Remote @ Acme"
    assert job["headline"] == "Great remote ML role"
    assert job["summary"] == "New summary"
    assert any("Backfilling 1 job(s)" in m for m in messages)
    assert any("Backfill complete" in m for m in messages)


def test_run_backfill_headlines_skips_jobs_that_already_have_one(conn, source):
    job_id = q.insert_job(conn, source_id=source["id"], url="http://example.com/job/1", title="T", company="C", raw_text="r")
    q.update_job_pipeline(conn, job_id, simplified_content="clean", content_type="job_posting", summary="S", headline="Already has one")
    client = MagicMock()

    messages, count = _drain(run_backfill_headlines(conn, client, "llama3.2"))

    assert count == 0
    assert client.chat.completions.create.call_count == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_pipeline.py -k backfill_headlines -v`
Expected: FAIL — `run_backfill_headlines` doesn't exist (`ImportError`).

- [ ] **Step 3: Implement**

Append to `app/pipeline.py`, after `run_reevaluate`:

```python
def run_backfill_headlines(
    conn: sqlite3.Connection,
    client: openai.OpenAI,
    model: str,
) -> Generator[str, None, int]:
    jobs = q.get_jobs_missing_headline(conn)
    yield _progress(f"Backfilling {len(jobs)} job(s) missing a headline")

    for i, job in enumerate(jobs, start=1):
        ai_title, headline, new_summary = summarize(client, model, job["simplified_content"])
        q.update_job_pipeline(
            conn, job["id"],
            simplified_content=job["simplified_content"],
            content_type=job["content_type"],
            title=ai_title or job["title"],
            headline=headline,
            summary=new_summary,
        )
        yield _progress(f"[{i}/{len(jobs)}] Backfilled: {job['title'] or job['url']}")

    yield _progress(f"Backfill complete: {len(jobs)} job(s) updated")
    return len(jobs)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_pipeline.py -v`
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add app/pipeline.py tests/test_pipeline.py
git commit -m "feat: add run_backfill_headlines pipeline generator"
```

---

### Task 6: `POST /jobs/backfill-headlines` route

**Files:**
- Modify: `app/routes/jobs.py`
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: `run_backfill_headlines(conn, client, model)` (Task 5), `get_ai_client`/`get_model` deps (already used by `app/routes/scenarios.py`).
- Produces: `POST /jobs/backfill-headlines` — `StreamingResponse` of newline-joined progress lines, `media_type="text/plain"`, same shape as `POST /scenarios/{id}/reevaluate`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_routes_jobs.py` (needs `from unittest.mock import patch` added to the imports at the top):

```python
from unittest.mock import patch
```

```python
def test_backfill_headlines_streams_progress_and_updates_jobs(client, conn):
    sid, jid, scenario_id = _seed(conn)
    with patch("app.pipeline.summarize", return_value=("AI Title", "Great hook", "Full summary")):
        resp = client.post("/jobs/backfill-headlines")

    assert resp.status_code == 200
    assert "Backfilling 1 job(s)" in resp.text
    assert "Backfill complete" in resp.text
    job = q.get_job(conn, jid)
    assert job["title"] == "AI Title"
    assert job["headline"] == "Great hook"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_routes_jobs.py -k backfill_headlines -v`
Expected: FAIL — `404 Not Found`, route doesn't exist.

- [ ] **Step 3: Implement**

In `app/routes/jobs.py`, update the imports at the top:

```python
from __future__ import annotations
import sqlite3
import openai
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from app.deps import get_db, get_ai_client, get_model
from app.db import queries as q
from app.pipeline import run_backfill_headlines
from app.template_env import templates

router = APIRouter()
```

Add the route after `job_feedback`:

```python
@router.post("/jobs/backfill-headlines")
def backfill_headlines(
    conn: sqlite3.Connection = Depends(get_db),
    client: openai.OpenAI = Depends(get_ai_client),
    model: str = Depends(get_model),
):
    def stream():
        gen = run_backfill_headlines(conn, client, model)
        try:
            while True:
                yield next(gen) + "\n"
        except StopIteration:
            pass

    return StreamingResponse(stream(), media_type="text/plain")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_routes_jobs.py -v`
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add app/routes/jobs.py tests/test_routes_jobs.py
git commit -m "feat: add POST /jobs/backfill-headlines route"
```

---

### Task 7: Card template — headline, no company span, whole card clickable

**Files:**
- Modify: `app/templates/jobs/_row.html`, `app/templates/base.html`
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: `job.headline`, `job.title` (now AI-normalized after Tasks 3-4 run), `job.summary` (fallback), `job.company` (no longer read by this template).
- Produces: no new interface — this is the leaf UI change.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_routes_jobs.py`:

```python
def test_job_list_card_is_clickable_and_has_no_details_button(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get("/")
    assert resp.status_code == 200
    assert f'hx-get="/jobs/{jid}/expand"' in resp.text
    assert "Details" not in resp.text
    assert 'role="button"' in resp.text


def test_job_list_shows_headline_when_present(client, conn):
    sid, jid, scenario_id = _seed(conn)
    q.update_job_pipeline(
        conn, jid,
        simplified_content="clean", content_type="job_posting",
        summary="Great role", headline="Fully remote, $180k+",
    )
    resp = client.get("/")
    assert "Fully remote, $180k+" in resp.text


def test_job_list_falls_back_to_summary_when_no_headline(client, conn):
    sid, jid, scenario_id = _seed(conn)  # _seed sets summary="Great role", no headline
    resp = client.get("/")
    assert "Great role" in resp.text


def test_job_list_row_omits_company_but_expand_keeps_it(client, conn):
    sid, jid, scenario_id = _seed(conn)  # _seed sets company="Acme"
    resp = client.get("/")
    assert "· Acme" not in resp.text
    resp2 = client.get(f"/jobs/{jid}/expand")
    assert "· Acme" in resp2.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_routes_jobs.py -k "clickable or headline or omits_company" -v`
Expected: FAIL — card still has a `<button>` instead of `hx-get` on the row `<div>`, no `role="button"`, and still shows the company span.

- [ ] **Step 3: Implement**

Replace the full contents of `app/templates/jobs/_row.html`:

```html
<div class="job-row" id="job-{{ job.id }}" role="button" tabindex="0" style="cursor:pointer"
  hx-get="/jobs/{{ job.id }}/expand"
  hx-target="#job-{{ job.id }}"
  hx-swap="outerHTML"
  hx-trigger="click, keyup[key=='Enter']">
  <div style="display:flex; align-items:center; gap:0.5rem; flex-wrap:wrap;">
    {% set score = job.best_score %}
    {% if score is not none %}
      {% if score >= 0.7 %}
        <span class="score-badge score-high">{{ "%.0f"|format(score * 100) }}%</span>
      {% elif score >= 0.4 %}
        <span class="score-badge score-mid">{{ "%.0f"|format(score * 100) }}%</span>
      {% else %}
        <span class="score-badge score-low">{{ "%.0f"|format(score * 100) }}%</span>
      {% endif %}
      <span class="tag">{{ job.best_scenario_name }}</span>
    {% endif %}
    <span class="tag">{{ job.content_type or "unknown" }}</span>
    <span class="tag">{{ job.source_name or "" }}</span>
    <strong>{{ job.title or "(no title)" }}</strong>
  </div>
  {% if job.headline %}
    <div style="margin-top:0.4rem; color:#444;">{{ job.headline }}</div>
  {% elif job.summary %}
    <div style="margin-top:0.4rem; color:#444;">{{ job.summary | markdown_text | truncate(200) }}</div>
  {% endif %}
</div>
```

In `app/templates/base.html`, add a hover rule right after the existing `.job-row` rule:

```css
    .job-row { border: 1px solid #dee2e6; border-radius: 6px; padding: 0.75rem; margin-bottom: 0.5rem; }
    .job-row:hover { background: #f8f9fa; }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_routes_jobs.py -v`
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add app/templates/jobs/_row.html app/templates/base.html tests/test_routes_jobs.py
git commit -m "feat: make job cards fully clickable and show AI headline"
```

---

### Task 8: "Backfill titles & headlines" button on the jobs list page

**Files:**
- Modify: `app/templates/jobs/list.html`
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: `POST /jobs/backfill-headlines` (Task 6), the existing `data-progress-url` JS handler in `app/templates/base.html` (no changes needed there — it already drives the "Re-evaluate all scenarios" button the same way).
- Produces: no new interface — this is the leaf UI change.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_routes_jobs.py`:

```python
def test_job_list_shows_backfill_button(client, conn):
    resp = client.get("/")
    assert resp.status_code == 200
    assert 'data-progress-url="/jobs/backfill-headlines"' in resp.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_routes_jobs.py -k backfill_button -v`
Expected: FAIL — button not present in `list.html`.

- [ ] **Step 3: Implement**

In `app/templates/jobs/list.html`, add the button between the filter bar and the jobs loop:

```html
{% extends "base.html" %}
{% block title %}Jobs — Job Seek{% endblock %}
{% block content %}
<h1>Jobs</h1>
<div class="filter-bar">
  <a href="/">New ({{ counts.new }})</a>
  <a href="/?status=accepted">Accepted ({{ counts.accepted }})</a>
  <a href="/?status=rejected">Rejected ({{ counts.rejected }})</a>
  <a href="/?status=invalid">Invalid ({{ counts.invalid }})</a>
  <a href="/?content_type=lead">Leads ({{ counts.lead }})</a>
</div>
<button class="btn" data-progress-url="/jobs/backfill-headlines" style="margin-bottom:1rem;">Backfill titles &amp; headlines</button>
{% if jobs %}
  {% for job in jobs %}
    {% include "jobs/_row.html" %}
  {% endfor %}
{% else %}
  <p>No jobs found.</p>
{% endif %}
{% endblock %}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_routes_jobs.py -v`
Expected: PASS (all tests).

- [ ] **Step 5: Run the full test suite**

Run: `uv run pytest -v`
Expected: PASS — every test in the project, confirming no regressions across the whole change set.

- [ ] **Step 6: Commit**

```bash
git add app/templates/jobs/list.html tests/test_routes_jobs.py
git commit -m "feat: add backfill titles/headlines button to jobs list page"
```
