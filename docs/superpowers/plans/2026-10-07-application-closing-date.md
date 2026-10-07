# Application Closing Date Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract when applications for a job close, show a friendly label next to the posting age, and add a "Closing soonest" sort.

**Architecture:** The existing summarizer LLM call returns one more JSON field (`apply_by`), validated and stored in a new `jobs.apply_by` column. A `closing()` helper in `app/dates.py` turns it into a label rendered via a Jinja macro beside the `job-age` chip. A new `order=closing` sorts in SQL (`_ORDER_BY`), mirrored by the in-Python `_sort_key`.

**Tech Stack:** Python, FastAPI, Jinja2/htmx, SQLite. Tests: `uv run pytest` (needs `dangerouslyDisableSandbox: true` because of the uv cache lock).

Spec: `docs/superpowers/specs/2026-10-07-application-closing-date-design.md`

## Global Constraints

- The UI never uses the word "deadline".
- `apply_by` stored values are only: an ISO date `YYYY-MM-DD`, the literal `rolling`, or `NULL` (no end date, shown as "apply soonest").
- Sort groups, in order: dated & open (nearest first), `NULL`, `rolling`, closed (most recently closed first); ties by `COALESCE(published_at, created_at) DESC`, then `id DESC`.
- "Today" is the UTC date everywhere (matches SQLite `date('now')`).
- No backfill: existing jobs get a value only when reprocessed.
- Stage explicit paths with `git add <paths>`; never `git add -A` / `git add .`. Use `/usr/bin/git`.
- Work and commit inside the worktree `/home/roman/projects/job-seek/.claude/worktrees/application-deadline` on branch `worktree-application-deadline`. Verify with `/usr/bin/git branch --show-current` before each commit.

---

### Task 1: Summarizer extracts `apply_by`

**Files:**
- Modify: `app/ai/summarize.py`
- Test: `tests/test_summarize.py`

**Interfaces:**
- Produces: `JobSummary.apply_by: str` (`"YYYY-MM-DD"`, `"rolling"`, or `""`).

- [ ] **Step 1: Write the failing tests** (append to `tests/test_summarize.py`; `_TODAY = date(2026, 8, 31)` and `_mock_client` already exist there)

```python
def test_summarize_extracts_apply_by_date():
    r = '{"title": "T", "headline": "H", "summary": "S", "apply_by": "2026-09-15"}'
    assert summarize(_mock_client(r), "llama3.2", "x", today=_TODAY).apply_by == "2026-09-15"


def test_summarize_apply_by_allows_past_date():
    r = '{"title": "T", "headline": "H", "summary": "S", "apply_by": "2026-08-01"}'
    assert summarize(_mock_client(r), "llama3.2", "x", today=_TODAY).apply_by == "2026-08-01"


def test_summarize_apply_by_rolling_is_normalised():
    r = '{"title": "T", "headline": "H", "summary": "S", "apply_by": " Rolling "}'
    assert summarize(_mock_client(r), "llama3.2", "x", today=_TODAY).apply_by == "rolling"


def test_summarize_apply_by_accepts_datetime_shape():
    r = '{"title": "T", "headline": "H", "summary": "S", "apply_by": "2026-09-15T23:59:00"}'
    assert summarize(_mock_client(r), "llama3.2", "x", today=_TODAY).apply_by == "2026-09-15"


@pytest.mark.parametrize("value", ["ASAP", "soon", "2028-01-01", "2025-01-01", None, 5, ""])
def test_summarize_apply_by_rejects_other_values(value):
    import json as _json
    r = _json.dumps({"title": "T", "headline": "H", "summary": "S", "apply_by": value})
    assert summarize(_mock_client(r), "llama3.2", "x", today=_TODAY).apply_by == ""


def test_summarize_apply_by_missing_is_empty():
    r = '{"title": "T", "headline": "H", "summary": "S"}'
    assert summarize(_mock_client(r), "llama3.2", "x", today=_TODAY).apply_by == ""


def test_summarize_prompt_asks_for_apply_by():
    client = _mock_client('{"title": "T", "headline": "H", "summary": "S"}')
    summarize(client, "llama3.2", "x", today=_TODAY)
    system = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert '"apply_by"' in system and "rolling" in system
```

Add `import pytest` at the top of the file if it isn't already imported.

- [ ] **Step 2: Run them and confirm they fail**

Run: `uv run pytest tests/test_summarize.py -k apply_by -v`
Expected: FAIL (`JobSummary` has no attribute `apply_by`).

- [ ] **Step 3: Implement**

In `app/ai/summarize.py`:

1. Add the field to the dataclass:

```python
@dataclass(frozen=True)
class JobSummary:
    title: str = ""
    company: str = ""
    headline: str = ""
    summary: str = ""
    posted_date: str = ""
    apply_by: str = ""
```

2. In `_SYSTEM`, after the `posted_date` JSON field line group (right before `'"source_link": ...`), insert:

```python
    '"apply_by": "<the last day applications are accepted, formatted YYYY-MM-DD; resolve '
    "relative phrases such as 'within 2 weeks' or 'by end of month' against the current "
    "date given at the top of the text; the word rolling if applications are reviewed on "
    "a rolling basis or the posting is open until filled; empty string if no closing date "
    'is stated or it only says ASAP>", '
```

and after the `"For posted_date: ..."` sentence add:

```python
    "For apply_by: only a date or rolling status you can support from the text; never guess one. "
```

3. Add the validator below `_valid_posted_date`:

```python
def _valid_apply_by(value: object, today: date) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    text = value.strip()
    if text.lower() == "rolling":
        return "rolling"
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.fromisoformat(text).date()
        except ValueError:
            return ""
    if abs((parsed - today).days) > 366:
        return ""
    return parsed.isoformat()
```

4. In the non-lead `return JobSummary(...)`, add `apply_by=_valid_apply_by(data.get("apply_by"), today),`.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_summarize.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
/usr/bin/git add app/ai/summarize.py tests/test_summarize.py
/usr/bin/git commit -m "feat(summarize): extract application closing date (apply_by)"
```

---

### Task 2: Store `apply_by` on the job

**Files:**
- Modify: `app/db/schema.py` (`_DDL` jobs table, new migration, `init_db`)
- Modify: `app/db/queries.py` (`update_job_pipeline`)
- Modify: `app/pipeline.py` (`update_job_pipeline` call, ~line 93)
- Test: `tests/test_queries.py`, `tests/test_pipeline.py`, `tests/test_schema.py`

**Interfaces:**
- Consumes: `JobSummary.apply_by` (Task 1).
- Produces: `jobs.apply_by TEXT` column (NULL = no date), present in job dicts from `q.get_job` / `q.get_jobs`; `q.update_job_pipeline(..., apply_by: str = "")`.

- [ ] **Step 1: Write failing tests**

`tests/test_queries.py` (append; the `conn` fixture exists):

```python
def test_update_job_pipeline_stores_apply_by_and_keeps_it_on_empty(conn):
    sid = q.insert_source(conn, "S", "https://s.test", "generic_listing")
    jid = q.insert_job(conn, source_id=sid, url="https://s.test/1", title="T", company="", raw_text="r")
    assert q.get_job(conn, jid)["apply_by"] is None
    q.update_job_pipeline(conn, jid, simplified_content="c", content_type="job_posting", apply_by="2026-09-15")
    assert q.get_job(conn, jid)["apply_by"] == "2026-09-15"
    q.update_job_pipeline(conn, jid, simplified_content="c", content_type="job_posting", apply_by="")
    assert q.get_job(conn, jid)["apply_by"] == "2026-09-15"
    q.update_job_pipeline(conn, jid, simplified_content="c", content_type="job_posting", apply_by="rolling")
    assert q.get_job(conn, jid)["apply_by"] == "rolling"
```

`tests/test_schema.py` (append; follow the file's existing pattern for opening an in-memory DB and running `init_db`, e.g. `sqlite3.connect(":memory:")` + `init_db(conn)`):

```python
def test_migrate_jobs_add_apply_by_adds_column_to_existing_table():
    import sqlite3
    from app.db.schema import init_db, _migrate_jobs_add_apply_by
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    conn.execute("ALTER TABLE jobs DROP COLUMN apply_by")
    _migrate_jobs_add_apply_by(conn)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(jobs)")]
    assert "apply_by" in cols
    _migrate_jobs_add_apply_by(conn)  # idempotent
```

`tests/test_pipeline.py` (append next to `test_run_fetch_stores_extracted_company_and_posted_date`, reusing the same helpers/fixtures):

```python
def test_run_fetch_stores_extracted_apply_by(conn, source):
    raw_jobs = [RawJob(url="http://example.com/job/1", title="", company="", raw_text="<p>desc</p>")]
    apply_by = (datetime.now(timezone.utc).date() + timedelta(days=10)).isoformat()
    client = _mock_client(
        classify_resp='{"type": "job_posting", "reason": "full description"}',
        summarize_resp='{"title": "T", "company": "Zivid", "headline": "H", '
                       f'"summary": "S", "apply_by": "{apply_by}"}}',
        evaluate_resp='{"score": 0.9, "reasoning": "match"}',
    )
    with patch("app.pipeline.GenericListingFetcher") as MockFetcher:
        MockFetcher.return_value.fetch.return_value = raw_jobs
        _drain(run_fetch(source, conn, client, "llama3.2", "browser-profile"))
    assert q.get_jobs(conn)[0]["apply_by"] == apply_by
```

Make sure `datetime, timezone, timedelta` are imported in `tests/test_pipeline.py`.

- [ ] **Step 2: Run them and confirm they fail**

Run: `uv run pytest tests/test_queries.py tests/test_schema.py tests/test_pipeline.py -k apply_by -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

`app/db/schema.py`:
- In `_DDL`'s `CREATE TABLE jobs`, add `apply_by TEXT,` right after `published_at TEXT,` (~line 111).
- Add next to `_migrate_jobs_add_published_at`:

```python
def _migrate_jobs_add_apply_by(conn: sqlite3.Connection) -> None:
    # Purely additive column, same shape as the published_at migration above.
    cols = [r[1] for r in conn.execute("PRAGMA table_info(jobs)")]
    if not cols or "apply_by" in cols:
        return
    conn.execute("ALTER TABLE jobs ADD COLUMN apply_by TEXT")
    conn.commit()
```

- Call `_migrate_jobs_add_apply_by(conn)` as the **last** line of `init_db` (after `_migrate_jobs_add_fit_score_override(conn)` and anything else at the end). It has to run after the table-rebuild migrations.

`app/db/queries.py` `update_job_pipeline`: add the keyword `apply_by: str = "",` after `published_at`, the SQL line `apply_by = COALESCE(NULLIF(?, ''), apply_by)` after the `published_at` line (add the comma), and `apply_by` to the params tuple before `job_id`.

`app/pipeline.py`: in the `q.update_job_pipeline(...)` call, add `apply_by=job_summary.apply_by,` after `published_at=...`.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_queries.py tests/test_schema.py tests/test_pipeline.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
/usr/bin/git add app/db/schema.py app/db/queries.py app/pipeline.py tests/test_queries.py tests/test_schema.py tests/test_pipeline.py
/usr/bin/git commit -m "feat(jobs): store application closing date"
```

---

### Task 3: Friendly closing label helpers

**Files:**
- Modify: `app/dates.py`, `app/template_env.py`
- Test: `tests/test_dates.py`

**Interfaces:**
- Produces: `closing(apply_by: str | None, today: date | None = None) -> str` and `closing_tone(apply_by: str | None, today: date | None = None) -> str` (`"soon"`, `"closed"` or `""`), registered as Jinja filters `closing` / `closing_tone`. `today` defaults to the current UTC date.

- [ ] **Step 1: Write failing tests** (append to `tests/test_dates.py`)

```python
from datetime import date
import pytest
from app.dates import closing, closing_tone

_T = date(2026, 10, 7)


@pytest.mark.parametrize("value, label", [
    (None, "apply soonest"),
    ("", "apply soonest"),
    ("rolling", "rolling"),
    ("2026-10-07", "closes today"),
    ("2026-10-08", "closes tomorrow"),
    ("2026-10-12", "closes in 5 days"),
    ("2026-10-21", "closes in 14 days"),
    ("2026-10-22", "closes 22 Oct"),
    ("2026-11-17", "closes 17 Nov"),
    ("2026-10-06", "closed yesterday"),
    ("2026-10-04", "closed 3 days ago"),
    ("garbage", "apply soonest"),
])
def test_closing_labels(value, label):
    assert closing(value, today=_T) == label


@pytest.mark.parametrize("value, tone", [
    (None, ""), ("rolling", ""), ("2026-10-07", "soon"), ("2026-10-10", "soon"),
    ("2026-10-11", ""), ("2026-10-06", "closed"), ("garbage", ""),
])
def test_closing_tone(value, tone):
    assert closing_tone(value, today=_T) == tone


def test_closing_labels_never_say_deadline():
    for v in (None, "rolling", "2026-10-07", "2026-10-30", "2026-09-01"):
        assert "deadline" not in closing(v, today=_T).lower()
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `uv run pytest tests/test_dates.py -v`
Expected: ImportError for `closing`.

- [ ] **Step 3: Implement** (append to `app/dates.py`; change its import line to `from datetime import date, datetime, timezone`)

```python
def _apply_by_date(apply_by: str | None) -> date | None:
    if not apply_by or apply_by == "rolling":
        return None
    try:
        return date.fromisoformat(apply_by)
    except ValueError:
        return None


def closing(apply_by: str | None, today: date | None = None) -> str:
    """Friendly label for a job's application closing date ('closes in 5 days',
    'rolling', 'apply soonest' when none is known). Never says 'deadline'."""
    if apply_by == "rolling":
        return "rolling"
    d = _apply_by_date(apply_by)
    if d is None:
        return "apply soonest"
    today = today or datetime.now(timezone.utc).date()
    days = (d - today).days
    if days == 0:
        return "closes today"
    if days == 1:
        return "closes tomorrow"
    if 1 < days <= 14:
        return f"closes in {days} days"
    if days > 14:
        return f"closes {d.day} {d.strftime('%b')}"
    if days == -1:
        return "closed yesterday"
    return f"closed {-days} days ago"


def closing_tone(apply_by: str | None, today: date | None = None) -> str:
    """'soon' within 3 days, 'closed' once past, else ''."""
    d = _apply_by_date(apply_by)
    if d is None:
        return ""
    today = today or datetime.now(timezone.utc).date()
    days = (d - today).days
    if days < 0:
        return "closed"
    return "soon" if days <= 3 else ""
```

In `app/template_env.py`: change the import to `from app.dates import time_ago, age, duration, closing, closing_tone` and register:

```python
templates.env.filters["closing"] = closing
templates.env.filters["closing_tone"] = closing_tone
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_dates.py tests/test_template_filters.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
/usr/bin/git add app/dates.py app/template_env.py tests/test_dates.py
/usr/bin/git commit -m "feat(dates): friendly application closing labels"
```

---

### Task 4: "Closing soonest" sort

**Files:**
- Modify: `app/job_filter.py` (`VALID_ORDERS`), `app/db/queries.py` (`_ORDER_BY`), `app/routes/jobs.py` (`_sort_key`), `app/templates/jobs/_content.html` (order `<select>`, ~line 135)
- Test: `tests/test_queries.py`, `tests/test_job_filter.py`, `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: `jobs.apply_by` (Task 2).
- Produces: `order="closing"` accepted by `JobFilter`, `q.get_jobs(..., order="closing")`, and `_sort_key("closing")`.

- [ ] **Step 1: Write failing tests**

`tests/test_queries.py`: append after `test_get_jobs_order_age_uses_published_then_created`. It reuses that file's `_mk_job(conn, url, created=..., published=...)` helper and sets `apply_by` directly:

```python
def _set_apply_by(conn, jid, value):
    conn.execute("UPDATE jobs SET apply_by=? WHERE id=?", (value, jid))
    conn.commit()


def _closing_fixture(conn):
    from datetime import datetime, timezone, timedelta
    today = datetime.now(timezone.utc).date()
    d = lambda n: (today + timedelta(days=n)).isoformat()
    jobs = {}
    for name, apply_by, published in [
        ("closed_old", d(-10), "2024-01-01T00:00:00"),
        ("rolling", "rolling", "2024-01-01T00:00:00"),
        ("none_old", None, "2024-01-01T00:00:00"),
        ("dated_far", d(20), "2024-01-01T00:00:00"),
        ("closed_recent", d(-1), "2024-01-01T00:00:00"),
        ("none_new", None, "2024-06-01T00:00:00"),
        ("dated_today", d(0), "2024-01-01T00:00:00"),
        ("dated_near", d(3), "2024-01-01T00:00:00"),
    ]:
        jid = _mk_job(conn, f"https://x.test/{name}", created="2024-01-01T00:00:00", published=published)
        _set_apply_by(conn, jid, apply_by)
        jobs[jid] = name
    return jobs


_CLOSING_EXPECTED = [
    "dated_today", "dated_near", "dated_far",
    "none_new", "none_old",
    "rolling",
    "closed_recent", "closed_old",
]


def test_get_jobs_order_closing_groups_and_orders(conn):
    names = _closing_fixture(conn)
    got = [names[j["id"]] for j in q.get_jobs(conn, status="new", order="closing")]
    assert got == _CLOSING_EXPECTED


def test_sort_key_closing_matches_sql_order(conn):
    from app.routes.jobs import _sort_key
    names = _closing_fixture(conn)
    rows = q.get_jobs(conn, status="new", order="closing")
    shuffled = list(reversed(rows))
    resorted = sorted(shuffled, key=_sort_key("closing"), reverse=True)
    assert [names[j["id"]] for j in resorted] == _CLOSING_EXPECTED
```

If `_mk_job` doesn't accept `published=`, check its signature (~line 1670 of `tests/test_queries.py`) and adapt the call; don't change the expected order.

`tests/test_job_filter.py`:

```python
def test_order_closing_is_valid():
    assert JobFilter.from_params({"order": "closing"}).order == "closing"
```

`tests/test_routes_jobs.py`:

```python
def test_job_list_order_dropdown_has_closing_soonest(client, conn):
    _seed(conn)
    resp = client.get("/jobs")
    assert '<option value="closing"' in resp.text
    assert "Closing soonest" in resp.text
    assert "deadline" not in resp.text.lower()


def test_job_list_order_closing_returns_200(client, conn):
    _seed(conn)
    resp = client.get("/jobs?order=closing")
    assert resp.status_code == 200
    assert '<option value="closing" selected' in resp.text
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `uv run pytest tests/test_queries.py tests/test_job_filter.py tests/test_routes_jobs.py -k closing -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

`app/job_filter.py`: `VALID_ORDERS = ("change", "score", "age", "closing")`.

`app/db/queries.py` `_ORDER_BY`: add

```python
    # Dated & open (nearest first), no date, rolling, closed (most recent first).
    # 'rolling' is matched before the date comparisons: as a string it sorts
    # above any ISO date.
    "closing": (
        "CASE WHEN jobs.apply_by IS NULL THEN 1 "
        "WHEN jobs.apply_by = 'rolling' THEN 2 "
        "WHEN jobs.apply_by >= date('now') THEN 0 ELSE 3 END, "
        "CASE WHEN jobs.apply_by != 'rolling' AND jobs.apply_by >= date('now') "
        "THEN jobs.apply_by END ASC, "
        "CASE WHEN jobs.apply_by != 'rolling' AND jobs.apply_by < date('now') "
        "THEN jobs.apply_by END DESC, "
        "COALESCE(jobs.published_at, jobs.created_at) DESC, jobs.id DESC"
    ),
```

`app/routes/jobs.py` `_sort_key`: add before the final fallback `return`:

```python
    if order == "closing":
        today = datetime.now(timezone.utc).date()

        def closing_key(j):
            # Applied with reverse=True, so every component is inverted
            # relative to the ascending SQL order.
            ab = j.get("apply_by")
            bucket, day = 1, 0
            if ab == "rolling":
                bucket = 2
            elif ab:
                try:
                    d = date.fromisoformat(ab)
                except ValueError:
                    d = None
                if d is not None:
                    bucket, day = (0, -d.toordinal()) if d >= today else (3, d.toordinal())
            return (-bucket, day, j["published_at"] or j["created_at"] or "", j["id"])
        return closing_key
```

Add `from datetime import date, datetime, timezone` to `app/routes/jobs.py` (merge with any existing `datetime` import).

`app/templates/jobs/_content.html`: after the `age` option add

```html
          <option value="closing" {% if filter.order == 'closing' %}selected{% endif %}>Closing soonest</option>
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_queries.py tests/test_job_filter.py tests/test_routes_jobs.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
/usr/bin/git add app/job_filter.py app/db/queries.py app/routes/jobs.py app/templates/jobs/_content.html tests/test_queries.py tests/test_job_filter.py tests/test_routes_jobs.py
/usr/bin/git commit -m "feat(jobs): sort by closing soonest"
```

---

### Task 5: Show the closing label beside the age

**Files:**
- Modify: `app/templates/jobs/_macros.html` (new macro), `app/templates/jobs/_row.html` (~line 18), `app/templates/jobs/_feedback.html` (~lines 23 and 47), `app/templates/base.html` (CSS next to `.job-age`, ~line 319)
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: `closing` / `closing_tone` filters (Task 3), `job.apply_by` (Task 2).

Use the `frontend-design` skill for the styling. Keep it quiet: the chip matches `.job-age`'s size and muted color, "soon" uses `--warning`, and "closed" stays muted with a strikethrough-free, lighter treatment (e.g. `opacity: 0.7`). Check it in both light and dark themes.

- [ ] **Step 1: Write failing tests** (append to `tests/test_routes_jobs.py`)

```python
def _set_apply_by(conn, jid, value):
    conn.execute("UPDATE jobs SET apply_by=? WHERE id=?", (value, jid))
    conn.commit()


def test_job_list_row_shows_closing_label(client, conn):
    _, jid, _ = _seed(conn)
    soon = (datetime.now(timezone.utc).date() + timedelta(days=2)).isoformat()
    _set_apply_by(conn, jid, soon)
    resp = client.get("/jobs")
    assert "closes in 2 days" in resp.text
    assert f'title="Applications close {soon}"' in resp.text
    assert "job-closing-soon" in resp.text


def test_job_list_row_shows_apply_soonest_without_date(client, conn):
    _seed(conn)
    assert "apply soonest" in client.get("/jobs").text


def test_job_expand_and_detail_show_closing_label(client, conn):
    _, jid, _ = _seed(conn)
    _set_apply_by(conn, jid, "rolling")
    assert "rolling" in client.get(f"/jobs/{jid}/expand").text
    assert 'class="job-closing' in client.get(f"/jobs/{jid}").text
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `uv run pytest tests/test_routes_jobs.py -k "closing or soonest" -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

`app/templates/jobs/_macros.html`: add

```jinja
{% macro closing_chip(job) -%}
{%- set tone = job.apply_by | closing_tone -%}
<span class="job-closing{% if tone %} job-closing-{{ tone }}{% endif %}"
  {%- if job.apply_by and job.apply_by != 'rolling' %} title="Applications close {{ job.apply_by }}"{% endif %}>{{ job.apply_by | closing }}</span>
{%- endmacro %}
```

Then, in each of the three places, put the chip right after the age line:
- `_row.html` (~line 18), inside `.job-row-header`
- `_feedback.html` (~line 23), inside the detail-page `.job-detail-meta`
- `_feedback.html` (~line 47), inside `.job-detail-title-row`

```jinja
      {{ macros.closing_chip(job) }}
```

The chip renders even when `published_at` is empty. Both templates already `import "jobs/_macros.html" as macros`.

`app/templates/base.html`, next to `.job-age`:

```css
    .job-closing { font-size: 0.8rem; color: var(--text-muted); white-space: nowrap; margin-left: 0.6rem; }
    .job-closing-soon { color: var(--warning); font-weight: 600; }
    .job-closing-closed { opacity: 0.7; }
```

If `.job-age` is missing (no `published_at`), its `margin-left: auto` no longer pushes the chip right. Add `.job-row-header .job-closing:first-of-type` handling, or simply give `.job-closing` `margin-left: auto` when it directly follows the title: `.job-title + .job-closing, .job-detail-title + .job-closing { margin-left: auto; }`. In the mobile block (~line 1084), mirror `.job-row-header .job-age { margin-right: 0.6rem; }` for `.job-closing` so the two stay inline and spaced.

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
/usr/bin/git add app/templates/jobs/_macros.html app/templates/jobs/_row.html app/templates/jobs/_feedback.html app/templates/base.html tests/test_routes_jobs.py
/usr/bin/git commit -m "feat(jobs): show application closing label beside posting age"
```

- [ ] **Step 6: UI handoff**

Use the `run-dev-server` skill to start the dev server in this worktree against a throwaway DB copy. To get sample values, reprocess two or three jobs through the real UI (this runs the real summarizer), and report which jobs those were. Leave the server running and report the URL. Do not merge.
