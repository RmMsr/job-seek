# Backlog Roundup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship eight independent small UX/feature backlog items: jobs-list sort switch, fetch-task health link, card-fold animation, indented task groups, scenario deletion, source-URL shortening, and filter-dropdown polish.

**Architecture:** Server-rendered FastAPI + Jinja + htmx 1.9.12 app. Two additive SQLite column migrations (`jobs.status_changed_at`, `fetch_runs.task_id`) following the established `_migrate_*` pattern in `app/db/schema.py`. Everything else is route + query + template + CSS changes. No new dependencies.

**Tech Stack:** Python 3.12, FastAPI, `sqlite3` stdlib, Jinja2, htmx, pytest + `fastapi.testclient.TestClient`.

## Global Constraints

- `fvm`/Flutter is irrelevant here; run Python tests with `python -m pytest` (never `uv run` — read-only cache in this environment).
- Every DB connection already runs `PRAGMA foreign_keys = ON` (`app/deps.py`, `tests/conftest.py`) — do not add it again.
- Additive migrations only: check `sqlite_master` for the column, `ALTER TABLE ADD COLUMN`, `conn.commit()`. Also add the column to the `_DDL` `CREATE TABLE` string. Register the function in `init_db()`.
- Migration philosophy: single-instance personal app. No backfill unless a row would otherwise disappear from a list. No dual-schema shims.
- Commit after each task. Commit-message trailer:
  ```
  Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_012eN174D41p4RvwLUBo2MEu
  ```
- Jobs-list ordering default is `change` ("newest changes first") — this is an intentional change to the current `fit_score`-first default.
- Filter dropdown copy: `(All)`, `(None)`, `(Single / None)` — verbatim, parens included.

---

## File Structure

| File | Change | For |
|---|---|---|
| `app/db/schema.py` | 2 new `_migrate_*` fns + `_DDL` edits + `init_db` registration | #14, #5 |
| `app/db/queries.py` | `get_jobs`/`get_job_counts` ordering + `org_none`; `update_job_feedback`/`mark_job_gate_override` stamp; `start_fetch_run(task_id)`; `delete_scenario`; `count_job_scores_for_scenario` | #14, #13, #5, #6 |
| `app/job_filter.py` | `order` + `org_none` fields | #14, #13 |
| `app/pipeline.py` | `run_fetch(task_id=…)` → `start_fetch_run` | #5 |
| `app/routes/jobs.py` | pass `order`/`org_none`; `HX-Reswap` on fold; sort key helper | #14, #18, #13 |
| `app/routes/fetch.py` | thread `task_id` into `run_fetch` | #5 |
| `app/routes/sources.py` | thread `task_id` into `run_fetch` | #5 |
| `app/routes/scenarios.py` | `DELETE /scenarios/{id}` | #6 |
| `app/routes/tasks.py` | group child rows under roots in `task_history` | #12 |
| `app/template_env.py` | `short_url` filter | #7 |
| `app/templates/jobs/_content.html` | Sort select; `(All)`/`(None)`; org truncation; manual-source label | #14, #8, #13 |
| `app/templates/fetch/_table.html` | glyph + task link in "Last run" | #5 |
| `app/templates/sources/_row.html` | truncated URL + `<details>` + open link | #7 |
| `app/templates/scenarios/_header_edit.html` | Delete button | #6 |
| `app/templates/tasks/list.html` | grouped `<tbody>` + CSS-only fold | #12 |
| `app/templates/base.html` | `.job-fold` keyframes; `.url-expand`; `.task-group` CSS | #18, #7, #12 |
| `tests/…` | one test module touched per task | all |

---

## Task 1: Migration + query — `jobs.status_changed_at`

**Files:**
- Modify: `app/db/schema.py` (`_DDL` jobs table ~line 36-60; new `_migrate_jobs_add_status_changed_at`; `init_db` ~line 680)
- Modify: `app/db/queries.py` (`update_job_feedback` ~line 376; `mark_job_gate_override` ~line 371)
- Test: `tests/test_schema.py`, `tests/test_queries.py`

**Interfaces:**
- Produces: `jobs.status_changed_at TEXT` (nullable). Stamped `datetime('now')` by `q.update_job_feedback(conn, job_id, status, note)` and `q.mark_job_gate_override(conn, job_id)`.

- [ ] **Step 1: Write the failing test** in `tests/test_queries.py`:

```python
def test_update_job_feedback_stamps_status_changed_at(conn):
    from app.db import queries as q
    sid = q.get_or_create_manual_source(conn)
    jid = q.insert_job(conn, source_id=sid, url="https://x.test/a", title="A", company="", raw_text="")
    assert conn.execute("SELECT status_changed_at FROM jobs WHERE id=?", (jid,)).fetchone()[0] is None
    q.update_job_feedback(conn, jid, "accepted", "yep")
    assert conn.execute("SELECT status_changed_at FROM jobs WHERE id=?", (jid,)).fetchone()[0] is not None


def test_mark_job_gate_override_stamps_status_changed_at(conn):
    from app.db import queries as q
    sid = q.get_or_create_manual_source(conn)
    jid = q.insert_job(conn, source_id=sid, url="https://x.test/b", title="B", company="", raw_text="")
    q.mark_job_gate_override(conn, jid)
    assert conn.execute("SELECT status_changed_at FROM jobs WHERE id=?", (jid,)).fetchone()[0] is not None
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_queries.py::test_update_job_feedback_stamps_status_changed_at -v`
Expected: FAIL — `sqlite3.OperationalError: no such column: status_changed_at`

- [ ] **Step 3: Add the column to `_DDL`**

In `app/db/schema.py`, in the `CREATE TABLE IF NOT EXISTS jobs (...)` block, add after `evaluation_completed_at TEXT`:

```sql
    evaluation_completed_at TEXT,
    status_changed_at TEXT
```

- [ ] **Step 4: Add the migration function**

After `_migrate_jobs_add_evaluation_completed_at` in `app/db/schema.py`:

```python
def _migrate_jobs_add_status_changed_at(conn: sqlite3.Connection) -> None:
    # Purely additive. Records when a job's status last changed (feedback
    # decision or gate override), for the Jobs-list "newest changes" sort.
    # No backfill: NULL sorts as fetched_at via the query's MAX(...).
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='jobs'"
    ).fetchone()
    if row is None or "status_changed_at" in row[0]:
        return
    conn.execute("ALTER TABLE jobs ADD COLUMN status_changed_at TEXT")
    conn.commit()
```

- [ ] **Step 5: Register in `init_db`**

In `init_db()`, add after `_migrate_jobs_add_evaluation_completed_at(conn)`:

```python
    _migrate_jobs_add_status_changed_at(conn)
```

- [ ] **Step 6: Stamp in the queries**

In `app/db/queries.py`, `update_job_feedback`:

```python
def update_job_feedback(conn: sqlite3.Connection, job_id: int, status: str, note: str) -> None:
    conn.execute(
        "UPDATE jobs SET status = ?, feedback_note = ?, feedback_handled_at = NULL, "
        "status_changed_at = datetime('now') WHERE id = ?",
        (status, note, job_id),
    )
    conn.commit()
```

`mark_job_gate_override`:

```python
def mark_job_gate_override(conn: sqlite3.Connection, job_id: int) -> None:
    conn.execute(
        "UPDATE jobs SET gate_override = 1, status_changed_at = datetime('now') WHERE id = ?",
        (job_id,),
    )
    conn.commit()
```

- [ ] **Step 7: Add a schema idempotency test** in `tests/test_schema.py` (follow the existing "migration runs twice" pattern in that file — search for `init_db(conn)` called twice). Minimal:

```python
def test_status_changed_at_column_present_and_idempotent():
    import sqlite3
    from app.db.schema import init_db
    c = sqlite3.connect(":memory:")
    init_db(c)
    init_db(c)  # must not raise
    cols = {r[1] for r in c.execute("PRAGMA table_info(jobs)")}
    assert "status_changed_at" in cols
    c.close()
```

- [ ] **Step 8: Run tests**

Run: `python -m pytest tests/test_queries.py -k status_changed_at tests/test_schema.py -k status_changed_at -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add app/db/schema.py app/db/queries.py tests/test_queries.py tests/test_schema.py
git commit -m "feat: add jobs.status_changed_at, stamped on feedback and gate override"
```

---

## Task 2: `JobFilter.order` + `get_jobs` ordering

**Files:**
- Modify: `app/job_filter.py`
- Modify: `app/db/queries.py` (`get_jobs` ~line 459-503)
- Test: `tests/test_job_filter.py`, `tests/test_queries.py`

**Interfaces:**
- Consumes: `jobs.status_changed_at` (Task 1).
- Produces:
  - `JobFilter.order: str` — one of `"change"` (default) / `"score"` / `"age"`.
  - `JobFilter.from_params` reads `order`; `query_params()` emits `order` only when `!= "change"`; `cleared()` keeps `status_tab` + `order`; `order` excluded from `is_narrowed`.
  - `q.get_jobs(conn, *, ..., order: str = "change")`.

- [ ] **Step 1: Write failing `JobFilter` tests** in `tests/test_job_filter.py`:

```python
def test_order_defaults_to_change_and_is_omitted():
    f = JobFilter.from_params({})
    assert f.order == "change"
    assert "order" not in f.query_params()
    assert not f.is_narrowed


def test_order_roundtrips_when_non_default():
    f = JobFilter.from_params({"order": "score"})
    assert f.order == "score"
    assert f.query_params()["order"] == "score"


def test_unknown_order_falls_back_to_change():
    assert JobFilter.from_params({"order": "bogus"}).order == "change"


def test_cleared_keeps_order():
    f = JobFilter.from_params({"status": "accepted", "org": "Acme", "order": "age"})
    c = f.cleared()
    assert c.order == "age" and not c.is_narrowed and c.status_tab == "accepted"


def test_for_status_keeps_order():
    assert JobFilter.from_params({"order": "age"}).for_status("trash").order == "age"
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_job_filter.py -k order -v`
Expected: FAIL — `AttributeError: 'JobFilter' object has no attribute 'order'`

- [ ] **Step 3: Implement in `app/job_filter.py`**

```python
VALID_TABS = ("new", "lead", "accepted", "rejected", "not_relevant", "trash")
VALID_ORDERS = ("change", "score", "age")


@dataclass(frozen=True)
class JobFilter:
    status_tab: str = "new"
    scenario_id: int | None = None
    scenario_none: bool = False
    source_id: int | None = None
    org: str | None = None
    order: str = "change"

    @classmethod
    def from_params(cls, params: Mapping[str, str]) -> "JobFilter":
        get = params.get
        tab = get("status") or "new"
        if tab not in VALID_TABS:
            tab = "new"

        raw_scenario = (get("scenario") or get("scenario_id") or "").strip()
        scenario_id: int | None = None
        scenario_none = False
        if raw_scenario == "none":
            scenario_none = True
        elif raw_scenario.isdigit():
            scenario_id = int(raw_scenario)

        raw_source = (get("source_id") or "").strip()
        source_id = int(raw_source) if raw_source.isdigit() else None

        org = (get("org") or "").strip() or None

        order = (get("order") or "").strip()
        if order not in VALID_ORDERS:
            order = "change"

        return cls(tab, scenario_id, scenario_none, source_id, org, order)
```

Update `query_params()` — add before `return out`:

```python
        if self.order != "change":
            out["order"] = self.order
```

Update `cleared()`:

```python
    def cleared(self) -> "JobFilter":
        return JobFilter(status_tab=self.status_tab, order=self.order)
```

(`for_status`, `with_*` use `replace(self, ...)` so they already preserve `order`.)

- [ ] **Step 4: Write failing `get_jobs` ordering test** in `tests/test_queries.py`:

```python
def _mk_job(conn, url, *, fetched, published=None, evaluated=None, changed=None, fit=None):
    from app.db import queries as q
    sid = q.get_or_create_manual_source(conn)
    jid = q.insert_job(conn, source_id=sid, url=url, title=url, company="", raw_text="")
    conn.execute(
        "UPDATE jobs SET fetched_at=?, published_at=?, evaluation_completed_at=?, "
        "status_changed_at=?, fit_score=?, content_type='job_posting' WHERE id=?",
        (fetched, published, evaluated, changed, fit, jid),
    )
    conn.commit()
    return jid


def test_get_jobs_order_change_uses_latest_activity(conn):
    from app.db import queries as q
    a = _mk_job(conn, "https://x.test/a", fetched="2024-01-01T00:00:00", changed="2024-06-01T00:00:00")
    b = _mk_job(conn, "https://x.test/b", fetched="2024-05-01T00:00:00")  # no changed -> falls back to fetched
    ids = [j["id"] for j in q.get_jobs(conn, status="new", order="change")]
    assert ids.index(a) < ids.index(b)  # a changed in June, b's newest activity is May


def test_get_jobs_order_age_uses_published_then_fetched(conn):
    from app.db import queries as q
    a = _mk_job(conn, "https://x.test/a", fetched="2024-09-01T00:00:00", published="2024-01-01T00:00:00")
    b = _mk_job(conn, "https://x.test/b", fetched="2024-02-01T00:00:00")  # no published -> fetched
    ids = [j["id"] for j in q.get_jobs(conn, status="new", order="age")]
    assert ids.index(b) < ids.index(a)  # b (Feb) newer than a (published Jan)


def test_get_jobs_order_score_matches_legacy(conn):
    from app.db import queries as q
    lo = _mk_job(conn, "https://x.test/lo", fetched="2024-01-01T00:00:00", fit=0.2)
    hi = _mk_job(conn, "https://x.test/hi", fetched="2024-01-01T00:00:00", fit=0.9)
    ids = [j["id"] for j in q.get_jobs(conn, status="new", order="score")]
    assert ids.index(hi) < ids.index(lo)
```

- [ ] **Step 5: Run to verify failure**

Run: `python -m pytest tests/test_queries.py -k "get_jobs_order" -v`
Expected: FAIL — `get_jobs() got an unexpected keyword argument 'order'`

- [ ] **Step 6: Implement in `app/db/queries.py` `get_jobs`**

Add `order: str = "change"` to the signature. Replace the ORDER BY line:

```python
    _ORDER_BY = {
        "change": (
            "MAX(jobs.status_changed_at, jobs.evaluation_completed_at, jobs.fetched_at) "
            "DESC, jobs.id DESC"
        ),
        "score": "jobs.fit_score DESC NULLS LAST, jobs.fetched_at DESC",
        "age": "COALESCE(jobs.published_at, jobs.fetched_at) DESC, jobs.id DESC",
    }
    sql += " ORDER BY " + _ORDER_BY.get(order, _ORDER_BY["change"])
```

Define `_ORDER_BY` as a module-level constant near `_GATE_SELECT` rather than inside the function; reference it as `_ORDER_BY`.

- [ ] **Step 7: Run tests**

Run: `python -m pytest tests/test_job_filter.py tests/test_queries.py -k "order" -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add app/job_filter.py app/db/queries.py tests/test_job_filter.py tests/test_queries.py
git commit -m "feat: JobFilter.order + get_jobs change/score/age sort"
```

---

## Task 3: Wire sort into the jobs list route + UI

**Files:**
- Modify: `app/routes/jobs.py` (`_jobs_for_filter` ~line 57; `job_bulk_feedback` sort ~line 531)
- Modify: `app/templates/jobs/_content.html` (`.filter-row` ~line 31-67)
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: `JobFilter.order` (Task 2), `q.get_jobs(order=…)` (Task 2).
- Produces: `_sort_key(order)` helper in `jobs.py` returning a `key=` callable for `sorted(...)`.

- [ ] **Step 1: Write failing route test** in `tests/test_routes_jobs.py` (near other filter tests; use existing helpers in that file for seeding — search for a helper like `_seed_job` / `make_job`):

```python
def test_jobs_list_respects_order_param(client, conn):
    # two new-tab jobs, differing only in status_changed_at
    from app.db import queries as q
    sid = q.get_or_create_manual_source(conn)
    old = q.insert_job(conn, source_id=sid, url="https://x.test/old", title="OLD", company="", raw_text="")
    new = q.insert_job(conn, source_id=sid, url="https://x.test/new", title="NEW", company="", raw_text="")
    conn.execute("UPDATE jobs SET content_type='job_posting', evaluation_completed_at=datetime('now'), "
                 "gate_override=1 WHERE id IN (?,?)", (old, new))
    conn.execute("UPDATE jobs SET status_changed_at='2024-01-01T00:00:00' WHERE id=?", (old,))
    conn.execute("UPDATE jobs SET status_changed_at='2024-09-01T00:00:00' WHERE id=?", (new,))
    conn.commit()
    body = client.get("/jobs?status=new&order=change").text
    assert body.index("NEW") < body.index("OLD")
    body2 = client.get("/jobs?status=new&order=age").text  # both fall back to fetched_at ~ now; stable, just 200
    assert "NEW" in body2 and "OLD" in body2
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_routes_jobs.py::test_jobs_list_respects_order_param -v`
Expected: FAIL — order ignored, `NEW`/`OLD` order not guaranteed (or KeyError if `.index` fails). If it happens to pass by luck, make the seed data unambiguous and re-run.

- [ ] **Step 3: Thread `order` through `_jobs_for_filter`**

In `app/routes/jobs.py`:

```python
def _jobs_for_filter(conn: sqlite3.Connection, f: JobFilter) -> list[dict]:
    kwargs = dict(_BASE_KWARGS_FOR_TAB[f.status_tab])
    if f.source_id is not None:
        kwargs["source_id"] = f.source_id
    if f.org is not None:
        kwargs["org"] = f.org
    if f.scenario_id is not None:
        kwargs["scenario_id"] = f.scenario_id
    if f.scenario_none:
        kwargs["scenario_gate"] = "none"
    return q.get_jobs(conn, order=f.order, **kwargs)
```

- [ ] **Step 4: Add `_sort_key` and use it in `job_bulk_feedback`**

Add near `_content_context`:

```python
def _sort_key(order: str):
    """Match get_jobs' ORDER BY for the in-Python re-sort of merged stale rows."""
    if order == "score":
        return lambda j: (
            j["fit_score"] if j["fit_score"] is not None else float("-inf"),
            j["fetched_at"] or "",
        )
    if order == "age":
        return lambda j: (j["published_at"] or j["fetched_at"] or "",)
    return lambda j: (
        max(
            j.get("status_changed_at") or "",
            j.get("evaluation_completed_at") or "",
            j["fetched_at"] or "",
        ),
    )
```

In `job_bulk_feedback`, replace the `jobs = sorted(...)` block:

```python
    jobs = sorted(jobs + stale_jobs, key=_sort_key(f.order), reverse=True)
```

- [ ] **Step 5: Add the Sort control to `_content.html`**

Inside `.filter-row`, after the Organization `<label>` and before the `{% if filter.is_narrowed %}` clear link:

```html
  <label>Sort
    <select name="order" hx-get="/jobs" hx-target="#jobs-content" hx-swap="innerHTML"
            hx-push-url="true" hx-vals='{"status": "{{ status }}"}'
            hx-include="[name='scenario'],[name='source_id'],[name='org']">
      <option value="change" {% if filter.order == 'change' %}selected{% endif %}>Newest changes</option>
      <option value="score" {% if filter.order == 'score' %}selected{% endif %}>Fit score</option>
      <option value="age" {% if filter.order == 'age' %}selected{% endif %}>Posting age</option>
    </select>
  </label>
```

Add `,[name='order']` to the `hx-include` of the Scenario, Source, and Organization selects in the same file.

- [ ] **Step 6: Run tests**

Run: `python -m pytest tests/test_routes_jobs.py -k "order or filter or sort" -v`
Expected: PASS (watch for regressions in existing filter tests)

- [ ] **Step 7: Commit**

```bash
git add app/routes/jobs.py app/templates/jobs/_content.html tests/test_routes_jobs.py
git commit -m "feat: jobs-list sort switch (newest changes / fit score / posting age)"
```

---

## Task 4: `fetch_runs.task_id` migration + plumbing

**Files:**
- Modify: `app/db/schema.py` (`_DDL` fetch_runs ~line 73-82; new `_migrate_fetch_runs_add_task_id`; `init_db`)
- Modify: `app/db/queries.py` (`start_fetch_run` ~line 661)
- Modify: `app/pipeline.py` (`run_fetch` ~line 119-126)
- Modify: `app/routes/fetch.py` (`_task_fetch_source` ~line 42), `app/routes/sources.py` (`_task_source_confirm` ~line 182), `app/routes/jobs.py` (`_task_job_add_listing_source` ~line 729)
- Test: `tests/test_queries.py`, `tests/test_pipeline.py`

**Interfaces:**
- Produces:
  - `fetch_runs.task_id INTEGER REFERENCES tasks(id)` (nullable).
  - `q.start_fetch_run(conn, source_id, task_id: int | None = None) -> int`.
  - `pipeline.run_fetch(source, conn, client, model, profile_dir, task_id: int | None = None)`.

- [ ] **Step 1: Write failing test** in `tests/test_queries.py`:

```python
def test_start_fetch_run_records_task_id(conn):
    from app.db import queries as q
    sid = q.get_or_create_manual_source(conn)
    task = q.enqueue_task(conn, kind="fetch_source", params={"source_id": sid})
    run_id = q.start_fetch_run(conn, sid, task_id=task["id"])
    assert conn.execute("SELECT task_id FROM fetch_runs WHERE id=?", (run_id,)).fetchone()[0] == task["id"]


def test_start_fetch_run_task_id_optional(conn):
    from app.db import queries as q
    sid = q.get_or_create_manual_source(conn)
    run_id = q.start_fetch_run(conn, sid)
    assert conn.execute("SELECT task_id FROM fetch_runs WHERE id=?", (run_id,)).fetchone()[0] is None
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_queries.py -k start_fetch_run -v`
Expected: FAIL — `no such column: task_id` / unexpected kwarg

- [ ] **Step 3: `_DDL` — add column** to `CREATE TABLE IF NOT EXISTS fetch_runs`:

```sql
    error TEXT,
    auth_error INTEGER NOT NULL DEFAULT 0,
    task_id INTEGER REFERENCES tasks(id)
```

- [ ] **Step 4: Migration function** (after `_migrate_fetch_runs_add_auth_error`):

```python
def _migrate_fetch_runs_add_task_id(conn: sqlite3.Connection) -> None:
    # Purely additive. Links a fetch run to the task that produced it so the
    # Fetch page's "Last run" health column can deep-link to that task.
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='fetch_runs'"
    ).fetchone()
    if row is None or "task_id" in row[0]:
        return
    conn.execute("ALTER TABLE fetch_runs ADD COLUMN task_id INTEGER REFERENCES tasks(id)")
    conn.commit()
```

Register in `init_db()` after `_migrate_fetch_runs_add_auth_error(conn)`.

- [ ] **Step 5: `start_fetch_run`**

```python
def start_fetch_run(
    conn: sqlite3.Connection, source_id: int, task_id: int | None = None
) -> int:
    cur = conn.execute(
        "INSERT INTO fetch_runs (source_id, task_id) VALUES (?, ?)", (source_id, task_id)
    )
    conn.commit()
    return cur.lastrowid
```

- [ ] **Step 6: `run_fetch`**

Signature → add `task_id: int | None = None` as the last param. Line 126:

```python
    run_id = q.start_fetch_run(conn, source["id"], task_id=task_id)
```

- [ ] **Step 7: Pass `task_id` at the three call sites**

`app/routes/fetch.py` `_task_fetch_source`:

```python
    gen = run_fetch(source, conn, client, model, config.browser_profile_dir, task_id=params["_task_id"])
```

`app/routes/sources.py` `_task_source_confirm`:

```python
    gen = run_fetch(source, conn, client, model, config.browser_profile_dir, task_id=params["_task_id"])
```

`app/routes/jobs.py` `_task_job_add_listing_source`:

```python
    gen = run_fetch(source, conn, client, model, config.browser_profile_dir, task_id=params["_task_id"])
```

(Leave any non-task test callers of `run_fetch` alone — the kwarg defaults to `None`.)

- [ ] **Step 8: Write failing pipeline test** in `tests/test_pipeline.py` (follow the existing `run_fetch` test setup — search `run_fetch(` in that file for the fixture pattern with a fake fetcher). Assert:

```python
    # after driving run_fetch(..., task_id=<id>) to completion:
    row = conn.execute("SELECT task_id FROM fetch_runs ORDER BY id DESC LIMIT 1").fetchone()
    assert row["task_id"] == <id>
```

- [ ] **Step 9: Run tests**

Run: `python -m pytest tests/test_queries.py -k fetch_run tests/test_pipeline.py -k "fetch" tests/test_schema.py -v`
Expected: PASS

- [ ] **Step 10: Commit**

```bash
git add app/db/schema.py app/db/queries.py app/pipeline.py app/routes/fetch.py app/routes/sources.py app/routes/jobs.py tests/
git commit -m "feat: link fetch_runs to the task that produced them"
```

---

## Task 5: "Last run" health glyph + task link

**Files:**
- Modify: `app/templates/fetch/_table.html` (Last run cell ~line 28-31)
- Test: `tests/test_routes_fetch.py`

**Interfaces:**
- Consumes: `fetch_runs.task_id`, `run.error` — `run` = `runs_by_source.get(source.id)` (latest run per source, already provided by `_fetch_panel_context`; `get_recent_fetch_runs` selects `fr.*` so `task_id` is present).

- [ ] **Step 1: Write failing test** in `tests/test_routes_fetch.py` (model on existing fetch-page tests there):

```python
def test_last_run_links_to_task_with_success_glyph(client, conn):
    from app.db import queries as q
    sid = q.insert_source(conn, "S", "https://s.test", "generic_listing")
    task = q.enqueue_task(conn, kind="fetch_source", params={"source_id": sid})
    run_id = q.start_fetch_run(conn, sid, task_id=task["id"])
    q.complete_fetch_run(conn, run_id, jobs_found=3, jobs_new=1)
    html = client.get("/fetch").text
    assert f'/tasks/{task["id"]}' in html
    assert "✓" in html


def test_last_run_shows_failure_glyph(client, conn):
    from app.db import queries as q
    sid = q.insert_source(conn, "S2", "https://s2.test", "generic_listing")
    task = q.enqueue_task(conn, kind="fetch_source", params={"source_id": sid})
    run_id = q.start_fetch_run(conn, sid, task_id=task["id"])
    q.complete_fetch_run(conn, run_id, jobs_found=0, jobs_new=0, error="boom")
    html = client.get("/fetch").text
    assert "✗" in html
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_routes_fetch.py -k last_run -v`
Expected: FAIL — glyph / link absent

- [ ] **Step 3: Rewrite the "Last run" cell** in `app/templates/fetch/_table.html` (replace lines ~28-31):

```html
        <td style="padding:0.5rem;">
          {% if run %}
            {% set ok = not run.error %}
            <span aria-hidden="true" style="color:{{ '#198754' if ok else '#dc3545' }};">{{ '✓' if ok else '✗' }}</span>
            {% set when = run.completed_at or run.started_at %}
            {% if run.task_id %}
              <a href="/tasks/{{ run.task_id }}" title="{{ when }} UTC">{{ when | age }}</a>
            {% else %}
              <span title="{{ when }} UTC">{{ when | age }}</span>
            {% endif %}
            {% if run.error %}<br><span style="color:#dc3545;">⚠ {{ run.error }}</span>{% endif %}
          {% else %}
            <span>&mdash;</span>
          {% endif %}
        </td>
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_routes_fetch.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/templates/fetch/_table.html tests/test_routes_fetch.py
git commit -m "feat: Fetch page 'Last run' shows health glyph and links to its task"
```

---

## Task 6: Card-fold animation on accept/reject/trash

**Files:**
- Modify: `app/routes/jobs.py` (`job_feedback` ~line 280-297)
- Modify: `app/templates/base.html` (keyframes near `@keyframes fade-out` ~line 280; reduced-motion block ~line 289)
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: `_stale_badge(conn, job, f)` (existing, `jobs.py:100`).
- Produces: `job_feedback` sets response header `HX-Reswap: outerHTML swap:0.35s` iff the decided job no longer matches the active filter.

- [ ] **Step 1: Write failing test** in `tests/test_routes_jobs.py`:

```python
def test_feedback_that_removes_job_sets_reswap_delay(client, conn):
    from app.db import queries as q
    sid = q.get_or_create_manual_source(conn)
    jid = q.insert_job(conn, source_id=sid, url="https://x.test/f", title="F", company="", raw_text="")
    conn.execute("UPDATE jobs SET content_type='job_posting', gate_override=1, "
                 "evaluation_completed_at=datetime('now') WHERE id=?", (jid,))
    conn.commit()
    r = client.post(f"/jobs/{jid}/feedback?status=new", data={"status": "accepted"})
    assert r.headers.get("HX-Reswap") == "outerHTML swap:0.35s"


def test_feedback_that_keeps_job_has_no_reswap(client, conn):
    from app.db import queries as q
    sid = q.get_or_create_manual_source(conn)
    jid = q.insert_job(conn, source_id=sid, url="https://x.test/g", title="G", company="", raw_text="")
    conn.execute("UPDATE jobs SET status='accepted', content_type='job_posting', gate_override=1, "
                 "evaluation_completed_at=datetime('now') WHERE id=?", (jid,))
    conn.commit()
    r = client.post(f"/jobs/{jid}/feedback?status=accepted", data={"status": "accepted", "note": "still yes"})
    assert "HX-Reswap" not in r.headers
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_routes_jobs.py -k reswap -v`
Expected: FAIL — header never set

- [ ] **Step 3: Implement in `job_feedback`** (`app/routes/jobs.py`). After computing `row_html` / `counts_html`, before `return`:

```python
    headers = {}
    if not detail:
        decided = q.get_job(conn, job_id)
        if decided is not None and _stale_badge(conn, decided, f) is not None:
            headers["HX-Reswap"] = "outerHTML swap:0.35s"
    return HTMLResponse(content=row_html + counts_html, headers=headers)
```

- [ ] **Step 4: Add the fold CSS** to `app/templates/base.html`. After `@keyframes fade-out { ... }`:

```css
    .job-row-expanded.htmx-swapping {
      overflow: hidden;
      animation: job-fold 0.35s ease forwards;
    }
    @keyframes job-fold {
      from { max-height: 2000px; opacity: 1; }
      to { max-height: 0; opacity: 0; padding-top: 0; padding-bottom: 0;
           margin-top: 0; margin-bottom: 0; }
    }
```

In the existing `@media (prefers-reduced-motion: reduce)` block, add:

```css
      .job-row-expanded.htmx-swapping { animation: none; }
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/test_routes_jobs.py -k "reswap or feedback" -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/routes/jobs.py app/templates/base.html tests/test_routes_jobs.py
git commit -m "feat: fold a job card closed when a decision moves it out of view"
```

---

## Task 7: Indent + collapse task groups on /tasks

**Files:**
- Modify: `app/routes/tasks.py` (`task_history` ~line 370-395)
- Modify: `app/templates/tasks/list.html`
- Modify: `app/templates/base.html` (new `.task-group` CSS)
- Test: `tests/test_routes_tasks.py`

**Interfaces:**
- Consumes: `q.get_recent_terminal_tasks`, `q.get_task`, `q.get_task_children`, `root_presentation`, `task_presentation` (all existing).
- Produces: `task_history` passes `groups: list[dict]` where each dict is
  `{"root": {"task", "pres", "state"}, "children": [{"task", "pres", "state"}, ...]}`.

- [ ] **Step 1: Write failing test** in `tests/test_routes_tasks.py`:

```python
def test_task_history_nests_children_under_root(client, conn):
    from app.db import queries as q
    root = q.enqueue_task(conn, kind="fetch_all", params={})
    child = q.enqueue_task(conn, kind="fetch_source", params={"source_id": 1},
                           parent_task_id=root["id"])
    for tid in (root["id"], child["id"]):
        q.finish_task(conn, tid) if hasattr(q, "finish_task") else conn.execute(
            "UPDATE tasks SET status='done', finished_at=datetime('now') WHERE id=?", (tid,))
    conn.commit()
    html = client.get("/tasks").text
    # child row carries the group-indent class and sits in the same tbody as its root
    assert "task-child-row" in html
    assert f'task-grp-{root["id"]}' in html  # the CSS-only disclosure toggle


def test_task_history_standalone_task_has_no_toggle(client, conn):
    from app.db import queries as q
    t = q.enqueue_task(conn, kind="job_reset", params={"job_id": 1})
    conn.execute("UPDATE tasks SET status='done', finished_at=datetime('now') WHERE id=?", (t["id"],))
    conn.commit()
    html = client.get("/tasks").text
    assert f'task-grp-{t["id"]}' not in html
```

(Check `q` for the actual "mark task done" helper — search `def .*task` in `queries.py`; use a raw `UPDATE` if none fits.)

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_routes_tasks.py -k "nests_children or standalone_task_has_no_toggle" -v`
Expected: FAIL — flat table, classes absent

- [ ] **Step 3: Build groups in `task_history`**

Replace the body of `task_history` after `rows = rows[:per]`:

```python
    def _entry(t):
        children = q.get_task_children(conn, t["id"])
        if children:
            pres = root_presentation(conn, t, children)
            return {"task": t, "pres": pres, "state": pres["status"]}
        return {"task": t, "pres": task_presentation(conn, t), "state": t["status"]}

    # Group the page's rows by their root. Pull in any root / sibling not on
    # this page so a group is never shown half-populated.
    roots: dict[int, dict] = {}
    order: list[int] = []
    for t in rows:
        rid = t["parent_task_id"] or t["id"]
        if rid not in roots:
            root_task = t if t["id"] == rid else q.get_task(conn, rid)
            if root_task is None:
                continue
            roots[rid] = {"root": _entry(root_task), "children": []}
            order.append(rid)
    for rid in order:
        child_tasks = q.get_task_children(conn, rid)
        roots[rid]["children"] = [
            {"task": c, "pres": task_presentation(conn, c), "state": c["status"]}
            for c in child_tasks
        ]

    groups = [roots[r] for r in order]
    return templates.TemplateResponse(request, "tasks/list.html", {
        "groups": groups, "status": status, "page": page, "has_next": has_next,
    })
```

- [ ] **Step 4: Rewrite `tasks/list.html`**

```html
{% extends "base.html" %}
{% block title %}Tasks — Job Seek{% endblock %}
{% block content %}
<h1>Tasks</h1>
<div class="filter-bar">
  <div>
    {% for s in ["all", "active", "done", "failed"] %}
    <a href="/tasks?status={{ s }}" class="{{ 'active' if status == s else '' }}">{{ s|capitalize }}</a>
    {% endfor %}
  </div>
</div>
<div class="responsive-table-wrap">
<table class="task-history-table">
  <thead><tr><th>Task</th><th>State</th><th>Age</th></tr></thead>
  {% for g in groups %}
  <tbody class="task-group">
    {% set root = g.root %}
    <tr>
      <td>
        {% if g.children %}
        <input type="checkbox" class="task-grp-toggle" id="task-grp-{{ root.task.id }}">
        <label for="task-grp-{{ root.task.id }}" class="task-grp-caret" aria-hidden="true"></label>
        {% endif %}
        <a href="/tasks/{{ root.task.id }}">{{ root.pres.title }}</a>
      </td>
      <td><span class="task-li-icon icon-{{ root.state }}" aria-hidden="true">{{ root.state | status_icon }}</span> {{ root.state }}</td>
      <td>{{ root.task.created_at | age }}</td>
    </tr>
    {% for c in g.children %}
    <tr class="task-child-row">
      <td class="task-child-cell"><a href="/tasks/{{ c.task.id }}">{{ c.pres.title }}</a></td>
      <td><span class="task-li-icon icon-{{ c.state }}" aria-hidden="true">{{ c.state | status_icon }}</span> {{ c.state }}</td>
      <td>{{ c.task.created_at | age }}</td>
    </tr>
    {% endfor %}
  </tbody>
  {% endfor %}
</table>
</div>
<p>
  {% if page > 1 %}<a href="/tasks?status={{ status }}&page={{ page - 1 }}">← newer</a>{% endif %}
  {% if has_next %}<a href="/tasks?status={{ status }}&page={{ page + 1 }}">older →</a>{% endif %}
</p>
{% endblock %}
```

- [ ] **Step 5: Add `.task-group` CSS** to `app/templates/base.html` (near the other `.task-*` rules ~line 355):

```css
    .task-history-table td { padding: 0.4rem 0.5rem; }
    .task-grp-toggle { position: absolute; opacity: 0; width: 0; height: 0; }
    .task-grp-caret { cursor: pointer; display: inline-block; width: 1rem; color: var(--text-muted); }
    .task-grp-caret::before { content: "▸"; }
    .task-grp-toggle:checked + .task-grp-caret::before { content: "▾"; }
    .task-child-row { display: none; }
    .task-grp-toggle:checked ~ .task-child-row,
    .task-group:has(.task-grp-toggle:checked) .task-child-row { display: table-row; }
    .task-child-cell { padding-left: 2rem !important; }
```

(The `:checked ~ .task-child-row` sibling selector does not cross the row boundary reliably inside a `tbody`; the `.task-group:has(...)` rule is the one that actually works in modern browsers. Keep both for resilience.)

- [ ] **Step 6: Run tests**

Run: `python -m pytest tests/test_routes_tasks.py -v`
Expected: PASS (check existing `/tasks` history tests still pass — some may assert on `items`; update them to `groups` if needed)

- [ ] **Step 7: Commit**

```bash
git add app/routes/tasks.py app/templates/tasks/list.html app/templates/base.html tests/test_routes_tasks.py
git commit -m "feat: group child step-tasks under their root on /tasks, collapsed by default"
```

---

## Task 8: Destructive scenario deletion

**Files:**
- Modify: `app/db/queries.py` (near other scenario fns ~line 100-150; new `delete_scenario`, `count_job_scores_for_scenario`)
- Modify: `app/routes/scenarios.py` (new `DELETE /scenarios/{scenario_id}`; ~line 178)
- Modify: `app/templates/scenarios/_header_edit.html`
- Test: `tests/test_queries.py`, `tests/test_routes_scenarios.py`

**Interfaces:**
- Produces:
  - `q.delete_scenario(conn, scenario_id: int) -> None` — deletes criteria + scenario; `job_scores` / `scenario_feedback` cascade.
  - `q.count_job_scores_for_scenario(conn, scenario_id: int) -> int`.
  - `DELETE /scenarios/{scenario_id}` → full `scenarios/index.html` re-render (200); 404 if missing.

- [ ] **Step 1: Write failing query test** in `tests/test_queries.py`:

```python
def test_delete_scenario_cascades(conn):
    from app.db import queries as q
    sc = q.insert_scenario(conn, "S", "d")
    q.insert_criterion(conn, sc, "must have X", "must")
    sid = q.get_or_create_manual_source(conn)
    jid = q.insert_job(conn, source_id=sid, url="https://x.test/j", title="J", company="", raw_text="")
    conn.execute(
        "INSERT INTO job_scores (job_id, scenario_id, relevance_score, scenario_version_hash) "
        "VALUES (?,?,?,?)", (jid, sc, 0.8, "h"))
    conn.commit()
    assert q.count_job_scores_for_scenario(conn, sc) == 1
    q.delete_scenario(conn, sc)
    assert q.get_scenario(conn, sc) is None
    assert conn.execute("SELECT COUNT(*) FROM criteria WHERE scenario_id=?", (sc,)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM job_scores WHERE scenario_id=?", (sc,)).fetchone()[0] == 0
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_queries.py -k delete_scenario -v`
Expected: FAIL — `module 'app.db.queries' has no attribute 'delete_scenario'`

- [ ] **Step 3: Implement queries** in `app/db/queries.py`:

```python
def count_job_scores_for_scenario(conn: sqlite3.Connection, scenario_id: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM job_scores WHERE scenario_id = ?", (scenario_id,)
    ).fetchone()[0]


def delete_scenario(conn: sqlite3.Connection, scenario_id: int) -> None:
    # criteria has ON DELETE CASCADE too, but be explicit; job_scores and
    # scenario_feedback cascade via their FKs (foreign_keys pragma is on).
    conn.execute("DELETE FROM criteria WHERE scenario_id = ?", (scenario_id,))
    conn.execute("DELETE FROM scenarios WHERE id = ?", (scenario_id,))
    conn.commit()
```

- [ ] **Step 4: Write failing route test** in `tests/test_routes_scenarios.py`:

```python
def test_delete_scenario_route(client, conn):
    from app.db import queries as q
    sc = q.insert_scenario(conn, "Doomed", "")
    r = client.delete(f"/scenarios/{sc}")
    assert r.status_code == 200
    assert q.get_scenario(conn, sc) is None
    assert "Doomed" not in r.text


def test_delete_missing_scenario_404(client):
    assert client.delete("/scenarios/99999").status_code == 404
```

- [ ] **Step 5: Run to verify failure**

Run: `python -m pytest tests/test_routes_scenarios.py -k delete_scenario -v`
Expected: FAIL — 405 Method Not Allowed

- [ ] **Step 6: Add the route** in `app/routes/scenarios.py` (after `update_scenario`):

```python
@router.delete("/scenarios/{scenario_id}", response_class=HTMLResponse)
def delete_scenario(scenario_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    _get_scenario_or_404(conn, scenario_id)
    q.delete_scenario(conn, scenario_id)
    return templates.TemplateResponse(request, "scenarios/index.html", _scenarios_context(conn))
```

- [ ] **Step 7: Add the Delete button** to `app/templates/scenarios/_header_edit.html`, inside the `<div style="display:flex; gap:0.5rem;">` action row, after the Save button:

```html
      <button type="button" class="btn btn-reject" style="margin-left:auto;"
        hx-delete="/scenarios/{{ scenario.id }}"
        hx-target="body"
        hx-swap="innerHTML"
        hx-confirm="Delete '{{ scenario.name }}'? This removes its criteria and all {{ job_scores_count }} job score(s) for it. Job fit scores are not recalculated.">
        Delete scenario
      </button>
```

Pass the count into the template — in `edit_scenario_form`:

```python
@router.get("/scenarios/{scenario_id}/edit", response_class=HTMLResponse)
def edit_scenario_form(scenario_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    scenario = _get_scenario_or_404(conn, scenario_id)
    return templates.TemplateResponse(
        request, "scenarios/_header_edit.html",
        {"scenario": scenario, "job_scores_count": q.count_job_scores_for_scenario(conn, scenario_id)},
    )
```

- [ ] **Step 8: Run tests**

Run: `python -m pytest tests/test_routes_scenarios.py tests/test_queries.py -k scenario -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add app/db/queries.py app/routes/scenarios.py app/templates/scenarios/_header_edit.html tests/
git commit -m "feat: destructive scenario deletion"
```

---

## Task 9: Shorten source URLs on /sources

**Files:**
- Modify: `app/template_env.py` (new `short_url`)
- Modify: `app/templates/sources/_row.html` (URL line ~line 5)
- Modify: `app/templates/base.html` (new `.url-expand` CSS)
- Test: `tests/test_dates.py` is date-only — create `tests/test_template_filters.py`; plus a case in `tests/test_routes_sources.py`

**Interfaces:**
- Produces: `short_url(url: str, limit: int = 50) -> str` — drops the scheme, keeps `host + path + query`, truncates to `limit` with a trailing `…`.

- [ ] **Step 1: Write failing test** — create `tests/test_template_filters.py`:

```python
from app.template_env import short_url


def test_short_url_drops_scheme():
    assert short_url("https://example.com/jobs") == "example.com/jobs"


def test_short_url_truncates_long():
    out = short_url("https://example.com/" + "a" * 100, limit=30)
    assert len(out) == 30 and out.endswith("…")


def test_short_url_keeps_short_untouched():
    assert short_url("http://x.io/a") == "x.io/a"


def test_short_url_handles_empty():
    assert short_url("") == ""
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_template_filters.py -v`
Expected: FAIL — `ImportError: cannot import name 'short_url'`

- [ ] **Step 3: Implement in `app/template_env.py`**

```python
def short_url(url: str, limit: int = 50) -> str:
    """host + path + query with the scheme stripped, truncated with an ellipsis."""
    if not url:
        return ""
    stripped = url.split("://", 1)[-1]
    if len(stripped) <= limit:
        return stripped
    return stripped[: limit - 1] + "…"
```

Register: `templates.env.filters["short_url"] = short_url`.

- [ ] **Step 4: Update `app/templates/sources/_row.html`**

Replace `<small style="word-break:break-all;">{{ source.url }}</small>` with:

```html
    {% if source.url %}
    <span class="url-expand-wrap">
      <details class="url-expand">
        <summary><small>{{ source.url | short_url }}</small></summary>
        <small style="word-break:break-all;">{{ source.url }}</small>
      </details>
      <a href="{{ source.url }}" target="_blank" rel="noopener" class="url-open"><small>open ↗</small></a>
    </span>
    {% endif %}
```

- [ ] **Step 5: Add `.url-expand` CSS** to `app/templates/base.html`:

```css
    .url-expand-wrap { display: inline-flex; align-items: baseline; gap: 0.5rem; flex-wrap: wrap; }
    .url-expand { display: inline; }
    .url-expand summary { cursor: pointer; color: var(--text-secondary); list-style: revert; }
    .url-open { white-space: nowrap; }
```

- [ ] **Step 6: Add a route test** in `tests/test_routes_sources.py`:

```python
def test_sources_page_shortens_long_url(client, conn):
    from app.db import queries as q
    long = "https://boards.example.com/careers/search?q=" + "x" * 80
    q.insert_source(conn, "Long", long, "generic_listing")
    html = client.get("/sources").text
    assert "…" in html
    assert f'href="{long}"' in html  # the open ↗ link keeps the full URL
```

- [ ] **Step 7: Run tests**

Run: `python -m pytest tests/test_template_filters.py tests/test_routes_sources.py -k "url or short" -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add app/template_env.py app/templates/sources/_row.html app/templates/base.html tests/test_template_filters.py tests/test_routes_sources.py
git commit -m "feat: shorten source URLs on /sources with expand + open link"
```

---

## Task 10: Filter dropdown polish — `(All)` / `(None)` / org truncation / manual-source label

**Files:**
- Modify: `app/job_filter.py` (`org_none`)
- Modify: `app/db/queries.py` (`get_jobs` + `get_job_counts` / `_count_filter_sql` for `org_none`)
- Modify: `app/routes/jobs.py` (`_jobs_for_filter`, `_counts_for_filter`, `_filter_from_bulk_form`)
- Modify: `app/templates/jobs/_content.html`
- Test: `tests/test_job_filter.py`, `tests/test_queries.py`, `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: `JobFilter` (Task 2).
- Produces:
  - `JobFilter.org_none: bool = False` — set when `org == "none"`; `query_params()` emits `org=none`; in `is_narrowed`; dropped by `cleared()`.
  - `q.get_jobs(..., org_none: bool = False)` and `q.get_job_counts(..., org_none=False)` → `jobs.company = ''`.

- [ ] **Step 1: Write failing `JobFilter` tests** in `tests/test_job_filter.py`:

```python
def test_org_none_sentinel():
    f = JobFilter.from_params({"org": "none"})
    assert f.org_none is True and f.org is None and f.is_narrowed
    assert f.query_params()["org"] == "none"


def test_org_none_cleared():
    assert JobFilter.from_params({"org": "none"}).cleared().org_none is False
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_job_filter.py -k org_none -v`
Expected: FAIL — no `org_none`

- [ ] **Step 3: Implement in `app/job_filter.py`**

Add field `org_none: bool = False` (after `org`, before `order`). In `from_params`:

```python
        raw_org = (get("org") or "").strip()
        org_none = raw_org == "none"
        org = None if org_none else (raw_org or None)
```

Pass `org_none` positionally into `cls(...)` in the right slot. In `is_narrowed` add `or self.org_none`. In `query_params()`:

```python
        if self.org_none:
            out["org"] = "none"
        elif self.org is not None:
            out["org"] = self.org
```

`cleared()` already resets everything except `status_tab` + `order` — good (it constructs a fresh `JobFilter`).

- [ ] **Step 4: Write failing query test** in `tests/test_queries.py`:

```python
def test_get_jobs_org_none_matches_blank_company(conn):
    from app.db import queries as q
    sid = q.get_or_create_manual_source(conn)
    blank = q.insert_job(conn, source_id=sid, url="https://x.test/blank", title="B", company="", raw_text="")
    named = q.insert_job(conn, source_id=sid, url="https://x.test/named", title="N", company="Acme", raw_text="")
    conn.execute("UPDATE jobs SET content_type='job_posting', gate_override=1, "
                 "evaluation_completed_at=datetime('now') WHERE id IN (?,?)", (blank, named))
    conn.commit()
    ids = {j["id"] for j in q.get_jobs(conn, status="new", org_none=True)}
    assert ids == {blank}
    counts = q.get_job_counts(conn, org_none=True)
    assert counts["new"] == 1
```

- [ ] **Step 5: Run to verify failure**

Run: `python -m pytest tests/test_queries.py -k org_none -v`
Expected: FAIL — unexpected kwarg

- [ ] **Step 6: Implement in `app/db/queries.py`**

`get_jobs`: add `org_none: bool = False`. In the clause build:

```python
    if org_none:
        clauses.append("jobs.company = ''")
    elif org is not None:
        clauses.append("jobs.company = ?")
        params.append(org)
```

(Replace the existing `if org is not None` block.)

`_count_filter_sql`: add `org_none: bool` param:

```python
def _count_filter_sql(
    scenario_id, scenario_none, source_id, org, org_none=False
) -> tuple[str, list]:
    clauses, params = [], []
    if source_id is not None:
        clauses.append("jobs.source_id = ?"); params.append(source_id)
    if org_none:
        clauses.append("jobs.company = ''")
    elif org is not None:
        clauses.append("jobs.company = ?"); params.append(org)
    ...
```

`get_job_counts`: add `org_none: bool = False`, pass to `_count_filter_sql`.

- [ ] **Step 7: Thread through `app/routes/jobs.py`**

`_jobs_for_filter`: `if f.org_none: kwargs["org_none"] = True` (alongside the `if f.org is not None` branch — make them mutually exclusive).

`_counts_for_filter`:

```python
def _counts_for_filter(conn, f):
    return q.get_job_counts(
        conn, scenario_id=f.scenario_id, scenario_none=f.scenario_none,
        source_id=f.source_id, org=f.org, org_none=f.org_none,
    )
```

`_filter_from_bulk_form` already routes through `JobFilter.from_params({"org": org_filter or ""})` — an `org_filter` of `"none"` will now be interpreted correctly, no change needed. The hidden `org_filter` input in `_content.html` must emit `none` when `filter.org_none` — see Step 8.

- [ ] **Step 8: Update `app/templates/jobs/_content.html`**

Hidden bulk input (line ~13):

```html
<input type="hidden" name="org_filter" value="{% if filter.org_none %}none{% else %}{{ filter.org or '' }}{% endif %}" form="bulk-form">
```

Scenario select — labels:

```html
      <option value="">(All)</option>
      <option value="none" {% if filter.scenario_none %}selected{% endif %}>(None)</option>
```

Source select:

```html
      <option value="">(All)</option>
      {% for s in sources %}
        <option value="{{ s.id }}" {% if filter.source_id == s.id %}selected{% endif %}>{{ "(Single / None)" if s.fetcher_type == "manual" else s.name }}</option>
      {% endfor %}
```

Organization select:

```html
      <option value="">(All)</option>
      <option value="none" {% if filter.org_none %}selected{% endif %}>(None)</option>
      {% for c in companies %}
        <option value="{{ c }}" {% if filter.org == c %}selected{% endif %}>{{ c | truncate(40, True, '…') }}</option>
      {% endfor %}
```

- [ ] **Step 9: Write failing route test** in `tests/test_routes_jobs.py`:

```python
def test_org_none_filter_and_labels(client, conn):
    from app.db import queries as q
    sid = q.get_or_create_manual_source(conn)
    blank = q.insert_job(conn, source_id=sid, url="https://x.test/b", title="BLANKJOB", company="", raw_text="")
    named = q.insert_job(conn, source_id=sid, url="https://x.test/n", title="NAMEDJOB", company="Acme", raw_text="")
    conn.execute("UPDATE jobs SET content_type='job_posting', gate_override=1, "
                 "evaluation_completed_at=datetime('now') WHERE id IN (?,?)", (blank, named))
    conn.commit()
    body = client.get("/jobs?status=new").text
    assert "(All)" in body and "(Single / None)" in body
    filtered = client.get("/jobs?status=new&org=none").text
    assert "BLANKJOB" in filtered and "NAMEDJOB" not in filtered
```

- [ ] **Step 10: Run tests**

Run: `python -m pytest tests/test_job_filter.py tests/test_queries.py tests/test_routes_jobs.py -k "org_none or labels or org" -v`
Expected: PASS

- [ ] **Step 11: Full suite**

Run: `python -m pytest -q`
Expected: PASS (fix any fallout in existing tests that asserted on the old `"All"` / `"None"` dropdown text or the old jobs default sort)

- [ ] **Step 12: Commit**

```bash
git add app/job_filter.py app/db/queries.py app/routes/jobs.py app/templates/jobs/_content.html tests/
git commit -m "feat: filter-dropdown polish — (All)/(None), org (None) filter, org truncation, manual-source label"
```

---

## Task 11: Manual verification pass

**Files:** none (dev server)

- [ ] **Step 1:** Start the dev server against a throwaway DB — use the `run-dev-server` skill.
- [ ] **Step 2:** Verify by hand:
  - `/jobs` — Sort select switches order; "Newest changes" is the default; accepting a job on the New tab folds the card to the stale badge.
  - `/jobs` — org dropdown shows `(All)` / `(None)` / truncated names; Source shows `(Single / None)`; `(None)` filters to blank-company jobs.
  - `/fetch` — run a fetch; "Last run" shows `✓`/`✗` and links to the task.
  - `/tasks` — a `fetch_all` run's children are indented and collapsed under the root; clicking the caret expands them.
  - `/scenarios` — Edit → Delete scenario, confirm dialog shows the score count, scenario disappears.
  - `/sources` — long URLs show short form + expand + `open ↗`.
- [ ] **Step 3:** Hand the URL to the user for their own check. Stop the server once done.

---

## Self-Review

- **Spec coverage:** #14 → Tasks 1-3; #5 → Tasks 4-5; #18 → Task 6; #12 → Task 7; #6 → Task 8; #7 → Task 9; #8 + #13 → Task 10. All covered.
- **Placeholder scan:** test-file seeding references "existing helpers" in `test_routes_jobs.py` / `test_pipeline.py` / `test_routes_tasks.py` — the implementer must look those up; explicit raw-SQL fallbacks are given in each case so no step is blocked.
- **Type consistency:** `order` values `change|score|age` consistent across `JobFilter`, `get_jobs._ORDER_BY`, `_sort_key`, template. `org_none` consistent across `JobFilter`, `get_jobs`, `_count_filter_sql`, `get_job_counts`, `_jobs_for_filter`, `_counts_for_filter`, templates. `start_fetch_run(conn, source_id, task_id=None)` / `run_fetch(..., task_id=None)` consistent across pipeline + 3 call sites. `delete_scenario` / `count_job_scores_for_scenario` consistent between Task 8 query + route + template.
- **Ordering:** schema tasks (1, 4) precede their consumers. Task 10 depends on Task 2's `JobFilter` changes (field ordering in the dataclass) — implementer must add `org_none` before `order` in the field list as noted.
