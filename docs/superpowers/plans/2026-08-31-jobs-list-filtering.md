# Jobs List Filtering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the jobs list a composable scenario / source / organization filter that layers on top of the status tabs, replacing today's mutually-exclusive `source_id` / `scenario_id` deep-link modes.

**Architecture:** A new `JobFilter` value object carries the whole filter state (status tab + scenario + source + org) and is threaded through every jobs route and the templates. `app/db/queries.py` gains `org` / `scenario_gate` params on `get_jobs`, filter-aware `get_job_counts`, and a `get_distinct_companies` helper. The jobs-list template always renders the status tabs plus a new filter sub-row of three `<select>` controls; job-card chips become filter links with distinct per-category colours.

**Tech Stack:** Python 3.14, FastAPI, Starlette `TestClient`, Jinja2 templates, htmx 1.9, SQLite. Tests: `pytest`. Run with `python -m pytest` (not `uv run` — the sandbox cache is read-only).

## Global Constraints

- Personal single-instance app: prefer hard changes over back-compat shims. No DB migration is needed here (no schema change).
- Commit after every task (each task ends green).
- TDD: write the failing test first, watch it fail, implement, watch it pass.
- Never hardcode real community/Slack IDs in code or tests.
- `httpx` `TestClient` repeated form fields: pass `data={"field": [v1, v2]}`, never a list of tuples.
- Full test run must stay green: `python -m pytest -q` (baseline: 1132 passed).
- Keep docs/comments terse.

---

## File map

- **Create** `app/job_filter.py` — the `JobFilter` dataclass + parsing/rendering.
- **Create** `tests/test_job_filter.py` — unit tests for it.
- **Modify** `app/db/queries.py` — `get_jobs`, `get_job_counts`, `_GATE_SELECT`/`_GATE_JOIN`, new `get_distinct_companies`.
- **Modify** `tests/test_queries.py` — new filter tests; update `test_get_job_counts`.
- **Modify** `app/routes/jobs.py` — adopt `JobFilter`; rewrite `_filter_context`, `_get_filtered_jobs`, `_content_context`, `_stale_badge`; route signatures; bulk endpoints.
- **Modify** `tests/test_routes_jobs.py` — filter-composition, chip-link, stale-badge, no-leads-in-new tests.
- **Modify** `app/templates/jobs/_content.html` — always-tabs + filter sub-row.
- **Modify** `app/templates/jobs/_macros.html` — chip links (`meta_tags`).
- **Modify** `app/templates/jobs/list.html` — hidden filter fields.
- **Modify** `app/templates/jobs/_feedback.html`, `app/templates/jobs/_row.html` — use the shared filter-query macro.
- **Modify** `app/templates/base.html` — `.tag-source`, `.tag-org`, `.filter-row` CSS.

---

## Task 1: `JobFilter` value object

**Files:**
- Create: `app/job_filter.py`
- Test: `tests/test_job_filter.py`

**Interfaces:**
- Produces:
  - `JobFilter` frozen dataclass with fields: `status_tab: str = "new"`, `scenario_id: int | None = None`, `scenario_none: bool = False`, `source_id: int | None = None`, `org: str | None = None`.
  - `JobFilter.from_params(params: Mapping[str, str]) -> JobFilter` — accepts a plain dict or Starlette `QueryParams` / form `FormData`.
  - `.is_narrowed -> bool` — True if any of `scenario_id` / `scenario_none` / `source_id` / `org` is set.
  - `.query_params() -> dict[str, str]` — non-default params only, for links/forms (keys: `status`, `scenario`, `source_id`, `org`). `status` always included.
  - `.query_suffix(*, detail: bool = False) -> str` — `"?detail=1"` when `detail` else `"?" + urlencode(self.query_params())`; used on expand/collapse/feedback URLs.
  - `.cleared() -> JobFilter` — same `status_tab`, all narrowing filters dropped.
  - `.for_status(tab: str) -> JobFilter` — copy with a different `status_tab`.
  - `.with_scenario_id(sid) -> JobFilter` / `.with_source_id(sid) -> JobFilter` / `.with_org(org: str) -> JobFilter` — copy with that one narrowing filter set (used by the card chip links; `with_scenario_id` also clears `scenario_none`).

**VALID_TABS** = `("new", "lead", "accepted", "rejected", "not_relevant", "trash")`.

Parsing rules for `from_params`:
- `status`: if in `VALID_TABS` use it, else `"new"`.
- scenario: read `scenario`, falling back to `scenario_id` (legacy alias). Value `"none"` → `scenario_none=True`, `scenario_id=None`. A digit string → `scenario_id=int(...)`, `scenario_none=False`. Empty / absent / non-numeric → both unset.
- `source_id`: digit string → int, else None.
- `org`: non-empty string → value, else None.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_job_filter.py
from app.job_filter import JobFilter


def test_defaults_to_new_tab():
    f = JobFilter.from_params({})
    assert f.status_tab == "new"
    assert not f.is_narrowed
    assert f.query_params() == {"status": "new"}


def test_unknown_tab_falls_back_to_new():
    assert JobFilter.from_params({"status": "bogus"}).status_tab == "new"


def test_scenario_id_param():
    f = JobFilter.from_params({"scenario": "3"})
    assert f.scenario_id == 3 and f.scenario_none is False and f.is_narrowed
    assert f.query_params() == {"status": "new", "scenario": "3"}


def test_scenario_none_sentinel():
    f = JobFilter.from_params({"scenario": "none"})
    assert f.scenario_none is True and f.scenario_id is None and f.is_narrowed
    assert f.query_params()["scenario"] == "none"


def test_legacy_scenario_id_alias():
    f = JobFilter.from_params({"scenario_id": "7"})
    assert f.scenario_id == 7


def test_source_and_org():
    f = JobFilter.from_params({"source_id": "5", "org": "Acme Corp"})
    assert f.source_id == 5 and f.org == "Acme Corp"
    assert f.query_params() == {"status": "new", "source_id": "5", "org": "Acme Corp"}


def test_blank_values_are_unset():
    f = JobFilter.from_params({"scenario": "", "source_id": "", "org": ""})
    assert not f.is_narrowed


def test_query_suffix_detail_wins():
    f = JobFilter.from_params({"status": "accepted", "org": "Acme"})
    assert f.query_suffix(detail=True) == "?detail=1"
    assert "status=accepted" in f.query_suffix()
    assert "org=Acme" in f.query_suffix()


def test_cleared_keeps_tab_only():
    f = JobFilter.from_params({"status": "rejected", "org": "Acme", "source_id": "2"})
    c = f.cleared()
    assert c.status_tab == "rejected" and not c.is_narrowed


def test_for_status_copies_filters():
    f = JobFilter.from_params({"org": "Acme"}).for_status("trash")
    assert f.status_tab == "trash" and f.org == "Acme"


def test_with_helpers_set_one_filter():
    base = JobFilter.from_params({"status": "accepted", "scenario": "none"})
    assert base.with_scenario_id("4").scenario_id == 4
    assert base.with_scenario_id("4").scenario_none is False
    assert base.with_source_id(9).source_id == 9
    assert base.with_org("Globex").org == "Globex"
```

- [ ] **Step 2: Run — expect failure**

Run: `python -m pytest tests/test_job_filter.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'app.job_filter'`).

- [ ] **Step 3: Implement**

```python
# app/job_filter.py
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Mapping
from urllib.parse import urlencode

VALID_TABS = ("new", "lead", "accepted", "rejected", "not_relevant", "trash")


@dataclass(frozen=True)
class JobFilter:
    status_tab: str = "new"
    scenario_id: int | None = None
    scenario_none: bool = False
    source_id: int | None = None
    org: str | None = None

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

        return cls(tab, scenario_id, scenario_none, source_id, org)

    @property
    def is_narrowed(self) -> bool:
        return bool(
            self.scenario_id is not None
            or self.scenario_none
            or self.source_id is not None
            or self.org is not None
        )

    def query_params(self) -> dict[str, str]:
        out = {"status": self.status_tab}
        if self.scenario_none:
            out["scenario"] = "none"
        elif self.scenario_id is not None:
            out["scenario"] = str(self.scenario_id)
        if self.source_id is not None:
            out["source_id"] = str(self.source_id)
        if self.org is not None:
            out["org"] = self.org
        return out

    def query_suffix(self, *, detail: bool = False) -> str:
        if detail:
            return "?detail=1"
        return "?" + urlencode(self.query_params())

    def cleared(self) -> "JobFilter":
        return JobFilter(status_tab=self.status_tab)

    def for_status(self, tab: str) -> "JobFilter":
        return replace(self, status_tab=tab)

    def with_scenario_id(self, sid) -> "JobFilter":
        return replace(self, scenario_id=int(sid), scenario_none=False)

    def with_source_id(self, sid) -> "JobFilter":
        return replace(self, source_id=int(sid))

    def with_org(self, org: str) -> "JobFilter":
        return replace(self, org=org)
```

- [ ] **Step 4: Run — expect pass**

Run: `python -m pytest tests/test_job_filter.py -q`
Expected: PASS (12 tests).

- [ ] **Step 5: Commit**

```bash
git add app/job_filter.py tests/test_job_filter.py
git commit -m "feat: JobFilter value object for composable jobs-list filtering"
```

---

## Task 2: `get_jobs` — `org` and `scenario_gate` filters

**Files:**
- Modify: `app/db/queries.py` (`get_jobs`, around lines 452-487)
- Test: `tests/test_queries.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `get_jobs(conn, *, status=None, content_type=None, gate_status=None, source_id=None, scenario_id=None, org=None, scenario_gate=None)`.
  - `org` (str): adds `jobs.company = ?`.
  - `scenario_gate="none"`: adds the scored-but-cleared-no-gate clause
    `scored.scored_count IS NOT NULL AND (gate.passed_count IS NULL OR gate.passed_count = 0)`.

- [ ] **Step 1: Write failing tests** (append to `tests/test_queries.py`)

```python
def test_get_jobs_filter_by_org(conn):
    sid = q.insert_source(conn, "s", "http://x", "generic_listing")
    a = q.insert_job(conn, source_id=sid, url="http://job/1", title="T1", company="Acme", raw_text="r")
    q.insert_job(conn, source_id=sid, url="http://job/2", title="T2", company="Globex", raw_text="r")
    result = q.get_jobs(conn, org="Acme")
    assert [j["id"] for j in result] == [a]


def test_get_jobs_scenario_gate_none_returns_scored_but_failed(conn):
    sid = q.insert_source(conn, "s", "http://x", "generic_listing")
    sc = q.insert_scenario(conn, "A", "")  # gate 0.7
    failed = q.insert_job(conn, source_id=sid, url="http://job/f", title="F", company="C", raw_text="r")
    passed = q.insert_job(conn, source_id=sid, url="http://job/p", title="P", company="C", raw_text="r")
    unscored_lead = q.insert_job(conn, source_id=sid, url="http://job/l", title="L", company="C", raw_text="r")
    q.update_job_pipeline(conn, failed, simplified_content="", content_type="job_posting")
    q.update_job_pipeline(conn, passed, simplified_content="", content_type="job_posting")
    q.update_job_pipeline(conn, unscored_lead, simplified_content="", content_type="lead")
    q.upsert_job_score(conn, failed, sc, 0.3, "", "h1")
    q.upsert_job_score(conn, passed, sc, 0.9, "", "h2")
    for j in (failed, passed, unscored_lead):
        q.mark_job_evaluation_complete(conn, j)

    result = {j["id"] for j in q.get_jobs(conn, scenario_gate="none")}
    assert result == {failed}


def test_get_jobs_org_and_scenario_id_compose(conn):
    sid = q.insert_source(conn, "s", "http://x", "generic_listing")
    sc = q.insert_scenario(conn, "A", "")
    hit = q.insert_job(conn, source_id=sid, url="http://job/1", title="T", company="Acme", raw_text="r")
    miss_org = q.insert_job(conn, source_id=sid, url="http://job/2", title="T", company="Globex", raw_text="r")
    for j in (hit, miss_org):
        q.update_job_pipeline(conn, j, simplified_content="", content_type="job_posting")
        q.upsert_job_score(conn, j, sc, 0.9, "", "h")
        q.mark_job_evaluation_complete(conn, j)
    result = {j["id"] for j in q.get_jobs(conn, scenario_id=sc, org="Acme")}
    assert result == {hit}
```

- [ ] **Step 2: Run — expect failure**

Run: `python -m pytest tests/test_queries.py -q -k "filter_by_org or scenario_gate_none or org_and_scenario_id_compose"`
Expected: FAIL (`get_jobs() got an unexpected keyword argument 'org'`).

- [ ] **Step 3: Implement**

In `app/db/queries.py`, extend the `get_jobs` signature and clause block:

```python
def get_jobs(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    content_type: str | None = None,
    gate_status: str | None = None,
    source_id: int | None = None,
    scenario_id: int | None = None,
    org: str | None = None,
    scenario_gate: str | None = None,
) -> list[dict]:
    clauses, params = [], []
    if status is not None:
        clauses.append("jobs.status = ?")
        params.append(status)
    if content_type is not None:
        clauses.append("jobs.content_type = ?")
        params.append(content_type)
    if source_id is not None:
        clauses.append("jobs.source_id = ?")
        params.append(source_id)
    if org is not None:
        clauses.append("jobs.company = ?")
        params.append(org)
    if scenario_id is not None:
        clauses.append(
            """EXISTS (
                SELECT 1 FROM job_scores js JOIN scenarios s ON s.id = js.scenario_id
                WHERE js.job_id = jobs.id AND js.scenario_id = ? AND js.relevance_score >= s.gate_threshold
            )"""
        )
        params.append(scenario_id)
    if scenario_gate == "none":
        clauses.append(
            "(scored.scored_count IS NOT NULL AND (gate.passed_count IS NULL OR gate.passed_count = 0))"
        )
    if gate_status == "passed":
        clauses.append(_GATE_PASSED_CLAUSE)
    elif gate_status == "failed":
        clauses.append(_GATE_FAILED_CLAUSE)
    sql = f"SELECT {_GATE_SELECT} {_GATE_JOIN}"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY jobs.fit_score DESC NULLS LAST, jobs.fetched_at DESC"
    return _rows_to_dicts(conn.execute(sql, params).fetchall())
```

(`scored` and `gate` are already provided by `_GATE_JOIN`.)

- [ ] **Step 4: Run — expect pass**

Run: `python -m pytest tests/test_queries.py -q -k "filter_by_org or scenario_gate_none or org_and_scenario_id_compose"`
Expected: PASS.

- [ ] **Step 5: Full queries suite**

Run: `python -m pytest tests/test_queries.py -q`
Expected: PASS (no regressions).

- [ ] **Step 6: Commit**

```bash
git add app/db/queries.py tests/test_queries.py
git commit -m "feat: get_jobs org and scenario_gate=none filters"
```

---

## Task 3: `get_job_counts` filter-aware + New excludes leads + helpers

**Files:**
- Modify: `app/db/queries.py` (`get_job_counts` ~490-507; `_GATE_JOIN`/`_GATE_SELECT` for `passed_scenario_ids`; new `get_distinct_companies`)
- Test: `tests/test_queries.py` (new tests + update `test_get_job_counts`)

**Interfaces:**
- Consumes: Task 2's clause helpers.
- Produces:
  - `get_job_counts(conn, *, scenario_id=None, scenario_none=False, source_id=None, org=None) -> dict[str,int]` — every count AND- s in the active filters; `counts["new"]` additionally requires `jobs.content_type = 'job_posting'`.
  - `get_distinct_companies(conn) -> list[str]`.
  - Each job row from `get_jobs`/`get_job` gains `passed_scenario_ids: str | None` (comma-joined, aligned with `passed_scenario_names`).

- [ ] **Step 1: Write failing tests**

```python
def test_get_job_counts_new_excludes_leads(conn):
    sid = q.insert_source(conn, "s", "http://x", "generic_listing")
    lead = q.insert_job(conn, source_id=sid, url="http://job/l", title="L", company="C", raw_text="r")
    posting = q.insert_job(conn, source_id=sid, url="http://job/p", title="P", company="C", raw_text="r")
    q.update_job_pipeline(conn, lead, simplified_content="", content_type="lead")
    q.update_job_pipeline(conn, posting, simplified_content="", content_type="job_posting")
    q.mark_job_evaluation_complete(conn, lead)
    q.mark_job_evaluation_complete(conn, posting)
    counts = q.get_job_counts(conn)
    assert counts["new"] == 1  # posting only
    assert counts["lead"] == 1


def test_get_job_counts_respects_org_filter(conn):
    sid = q.insert_source(conn, "s", "http://x", "generic_listing")
    a = q.insert_job(conn, source_id=sid, url="http://job/1", title="T", company="Acme", raw_text="r")
    g = q.insert_job(conn, source_id=sid, url="http://job/2", title="T", company="Globex", raw_text="r")
    q.update_job_feedback(conn, a, "accepted", "n")
    q.update_job_feedback(conn, g, "accepted", "n")
    counts = q.get_job_counts(conn, org="Acme")
    assert counts["accepted"] == 1


def test_get_job_counts_respects_scenario_filter(conn):
    sid = q.insert_source(conn, "s", "http://x", "generic_listing")
    sc = q.insert_scenario(conn, "A", "")
    match = q.insert_job(conn, source_id=sid, url="http://job/1", title="T", company="C", raw_text="r")
    other = q.insert_job(conn, source_id=sid, url="http://job/2", title="T", company="C", raw_text="r")
    for j, score in ((match, 0.9), (other, 0.2)):
        q.update_job_pipeline(conn, j, simplified_content="", content_type="job_posting")
        q.upsert_job_score(conn, j, sc, score, "", "h")
        q.mark_job_evaluation_complete(conn, j)
    q.update_job_feedback(conn, match, "accepted", "n")
    q.update_job_feedback(conn, other, "accepted", "n")
    counts = q.get_job_counts(conn, scenario_id=sc)
    assert counts["accepted"] == 1


def test_get_distinct_companies_sorted_no_blanks(conn):
    sid = q.insert_source(conn, "s", "http://x", "generic_listing")
    q.insert_job(conn, source_id=sid, url="http://job/1", title="T", company="Zeta", raw_text="r")
    q.insert_job(conn, source_id=sid, url="http://job/2", title="T", company="Acme", raw_text="r")
    q.insert_job(conn, source_id=sid, url="http://job/3", title="T", company="Acme", raw_text="r")
    q.insert_job(conn, source_id=sid, url="http://job/4", title="T", company="", raw_text="r")
    assert q.get_distinct_companies(conn) == ["Acme", "Zeta"]


def test_get_jobs_exposes_passed_scenario_ids(conn):
    sid = q.insert_source(conn, "s", "http://x", "generic_listing")
    a = q.insert_scenario(conn, "Alpha", "")
    b = q.insert_scenario(conn, "Beta", "")
    jid = q.insert_job(conn, source_id=sid, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="", content_type="job_posting")
    q.upsert_job_score(conn, jid, a, 0.9, "", "h")
    q.upsert_job_score(conn, jid, b, 0.8, "", "h")
    q.mark_job_evaluation_complete(conn, jid)
    job = q.get_job(conn, jid)
    names = job["passed_scenario_names"].split(", ")
    ids = [int(x) for x in job["passed_scenario_ids"].split(",")]
    assert dict(zip(names, ids)) == {"Alpha": a, "Beta": b}
```

Also **update** the existing `test_get_job_counts` (around line 496-506): j3 is a lead, so after this change `counts["new"]` becomes `0`.

```python
    counts = q.get_job_counts(conn)
    assert counts == {"new": 0, "accepted": 1, "rejected": 1, "trash": 0, "lead": 1, "not_relevant": 0}
```

- [ ] **Step 2: Run — expect failure**

Run: `python -m pytest tests/test_queries.py -q -k "counts_new_excludes_leads or counts_respects or distinct_companies or passed_scenario_ids or test_get_job_counts"`
Expected: FAIL.

- [ ] **Step 3: Implement**

**3a.** In `_GATE_JOIN`, add an id aggregate to the inner `gate` subquery (parallel to `passed_scenario_names`). The `ranked` CTE already selects `js.scenario_id`; add to the outer `SELECT`:

```sql
               GROUP_CONCAT(ranked.scenario_id) AS passed_scenario_ids,
```

and in `_GATE_SELECT` add:

```
    gate.passed_scenario_ids AS passed_scenario_ids,
```

Note ordering: `passed_scenario_names` uses `GROUP_CONCAT(ranked.scenario_name, ', ')`. Plain `GROUP_CONCAT(x)` uses `,`. Both aggregate in the same group row order, so names and ids stay aligned. Keep the id separator as `,` (tests split on `,`).

**3b.** Rewrite `get_job_counts`:

```python
def get_job_counts(
    conn: sqlite3.Connection,
    *,
    scenario_id: int | None = None,
    scenario_none: bool = False,
    source_id: int | None = None,
    org: str | None = None,
) -> dict[str, int]:
    extra, eparams = _count_filter_sql(scenario_id, scenario_none, source_id, org)
    counts = {"new": 0, "accepted": 0, "rejected": 0, "trash": 0, "lead": 0, "not_relevant": 0}

    for key in ("accepted", "rejected", "trash"):
        counts[key] = conn.execute(
            f"SELECT COUNT(*) {_GATE_JOIN} WHERE jobs.status = ?{extra}",
            [key, *eparams],
        ).fetchone()[0]

    counts["lead"] = conn.execute(
        f"SELECT COUNT(*) {_GATE_JOIN} "
        f"WHERE jobs.content_type = 'lead' AND jobs.status = 'new'{extra}",
        eparams,
    ).fetchone()[0]

    counts["not_relevant"] = conn.execute(
        f"""
        SELECT COUNT(*) {_GATE_JOIN}
        WHERE jobs.status = 'new' AND jobs.content_type = 'job_posting'
          AND {_GATE_FAILED_CLAUSE}{extra}
        """,
        eparams,
    ).fetchone()[0]

    counts["new"] = conn.execute(
        f"""
        SELECT COUNT(*) {_GATE_JOIN}
        WHERE jobs.status = 'new' AND jobs.content_type = 'job_posting'
          AND {_GATE_PASSED_CLAUSE}{extra}
        """,
        eparams,
    ).fetchone()[0]
    return counts


def _count_filter_sql(
    scenario_id: int | None, scenario_none: bool, source_id: int | None, org: str | None
) -> tuple[str, list]:
    clauses, params = [], []
    if source_id is not None:
        clauses.append("jobs.source_id = ?")
        params.append(source_id)
    if org is not None:
        clauses.append("jobs.company = ?")
        params.append(org)
    if scenario_id is not None:
        clauses.append(
            "EXISTS (SELECT 1 FROM job_scores js JOIN scenarios s ON s.id = js.scenario_id "
            "WHERE js.job_id = jobs.id AND js.scenario_id = ? AND js.relevance_score >= s.gate_threshold)"
        )
        params.append(scenario_id)
    if scenario_none:
        clauses.append(
            "(scored.scored_count IS NOT NULL AND (gate.passed_count IS NULL OR gate.passed_count = 0))"
        )
    return ("".join(" AND " + c for c in clauses), params)
```

**3c.** Add `get_distinct_companies`:

```python
def get_distinct_companies(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT company FROM jobs WHERE company != '' ORDER BY company COLLATE NOCASE"
    ).fetchall()
    return [r[0] for r in rows]
```

- [ ] **Step 4: Run — expect pass**

Run: `python -m pytest tests/test_queries.py -q`
Expected: PASS (all, including updated `test_get_job_counts`).

- [ ] **Step 5: Commit**

```bash
git add app/db/queries.py tests/test_queries.py
git commit -m "feat: filter-aware get_job_counts, drop leads from New, add company/scenario-id helpers"
```

---

## Task 4: Route layer adopts `JobFilter`

**Files:**
- Modify: `app/routes/jobs.py`
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: `JobFilter` (Task 1); `get_jobs`/`get_job_counts`/`get_distinct_companies` (Tasks 2-3).
- Produces (template context keys, used by Task 5):
  - `filter` — the `JobFilter`.
  - `scenarios` — `q.get_scenarios(conn)`.
  - `sources` — `q.get_sources(conn)`.
  - `companies` — `q.get_distinct_companies(conn)`.
  - `counts`, `jobs`, `stale_jobs`, `status` (= `filter.status_tab`).
  - Drop the old keys `filter_status` / `filter_content_type` / `filter_source_id` / `filter_scenario_id` / `filter_source` / `filter_scenario` / `content_type` from the jobs-list context (Task 5 stops using them).

### Design notes

Replace `_get_filtered_jobs` + `_filter_context` + the `_content_context` tangle with:

```python
from app.job_filter import JobFilter

# status tab -> base kwargs for get_jobs (before scenario/source/org narrowing)
def _base_kwargs_for_tab(tab: str) -> dict:
    return {
        "new": {"status": "new", "content_type": "job_posting", "gate_status": "passed"},
        "lead": {"status": "new", "content_type": "lead"},
        "accepted": {"status": "accepted"},
        "rejected": {"status": "rejected"},
        "not_relevant": {"status": "new", "content_type": "job_posting", "gate_status": "failed"},
        "trash": {"status": "trash"},
    }[tab]


def _jobs_for_filter(conn, f: JobFilter) -> list[dict]:
    kwargs = _base_kwargs_for_tab(f.status_tab)
    if f.source_id is not None:
        kwargs["source_id"] = f.source_id
    if f.org is not None:
        kwargs["org"] = f.org
    if f.scenario_id is not None:
        kwargs["scenario_id"] = f.scenario_id
    if f.scenario_none:
        kwargs["scenario_gate"] = "none"
    return q.get_jobs(conn, **kwargs)


def _filter_from_request(request: Request) -> JobFilter:
    return JobFilter.from_params(request.query_params)
```

- `_content_context(conn, f)` builds the dict with `filter=f`, `jobs=_enrich_jobs(conn, _jobs_for_filter(conn, f))`, `counts=q.get_job_counts(conn, scenario_id=f.scenario_id, scenario_none=f.scenario_none, source_id=f.source_id, org=f.org)`, `scenarios`, `sources`, `companies`, `stale_jobs=[]`, `status=f.status_tab`.
- `_stale_badge(conn, job, f: JobFilter)`: compute `filtered_ids = {j["id"] for j in _jobs_for_filter(conn, f)}`; if `job["id"]` in it → `None`. Keep the existing status→label/href mapping but build hrefs from `f.for_status(<tab>).cleared_of_scenario_source_org?`— simplest: link to `/jobs?status=<tab>` (drop narrowing filters in the "moved to" link). Anchor `#job-<id>` preserved.
  - `not_relevant` href: `/jobs?status=not_relevant`.
  - `lead` href: `/jobs?status=lead` (note: `content_type=lead` deep-link still works via alias? No — `status=lead` is the tab now). Use `/jobs?status=lead`.
- Route signatures: `job_list`, `job_expand`, `job_collapse`, `job_delete_confirm`, `job_feedback`, all `job_*` task-enqueue routes, and the bulk routes read the filter from `request` (query params) — except the bulk POST routes which read it from **form fields** (see below). Replace `_filter_context(request)` calls with `_filter_from_request(request)` and store `params["filter"] = f.query_params()` in enqueued task params (a plain dict; rebuild with `JobFilter.from_params` in the task handler).
- Task handlers (`_task_job_reset` etc.) currently do `params["filter_ctx"]`; change to `JobFilter.from_params(params["filter"])` and pass to `_render_updated_job_html`.
- `_render_updated_job_html(conn, request, job_id, f: JobFilter)`: always compute the stale badge (no more "empty dict → skip"). A job still matching → normal `_feedback.html`; else `_row.html` with `stale_badge`. Pass `filter=f` into both templates.
- Bulk POST routes (`bulk-feedback`, `bulk-delete`): accept form fields `status_filter`, `scenario_filter`, `source_id_filter`, `org_filter`; build `JobFilter.from_params({"status": status_filter or "new", "scenario": scenario_filter or "", "source_id": source_id_filter or "", "org": org_filter or ""})`.
- `job_bulk_actions_cancel`: keep passing `status` (now `status_filter` form field) through.
- `job_add_by_url` / listing-source task: they call `_content_context(conn, status, content_type)` — update to build a `JobFilter` from the enqueue params (`params["filter"]`) or default; keep behaviour (they just re-render the list).

### Tests

- [ ] **Step 1: Write failing tests** (append to `tests/test_routes_jobs.py`; reuse existing helpers/fixtures — inspect the file's top for the `client` fixture and job-creation helpers first)

```python
def test_new_tab_excludes_leads(client, ...):
    # create one gate-passing job_posting and one lead; GET /jobs
    # assert lead title absent from response, posting title present

def test_scenario_filter_composes_with_accepted_tab(client, ...):
    # accepted job matching scenario A, accepted job not matching;
    # GET /jobs?status=accepted&scenario=<A>; only the matching one shown;
    # "Accepted (1)" in the tab count

def test_scenario_none_filter(client, ...):
    # GET /jobs?status=not_relevant&scenario=none -> scored-no-gate jobs

def test_source_filter_via_query(client, ...):
    # GET /jobs?source_id=<s> narrows; tabs still rendered (not the old
    # "All jobs from X" replacement)

def test_org_filter_via_query(client, ...):
    # GET /jobs?org=Acme narrows to Acme jobs

def test_clear_all_filters_link_present_when_narrowed(client, ...):
    # GET /jobs?org=Acme -> response contains href="/jobs?status=new"

def test_legacy_scenario_id_param_still_works(client, ...):
    # GET /jobs?scenario_id=<A> behaves like ?scenario=<A>

def test_feedback_action_under_org_filter_gives_stale_badge(client, ...):
    # under ?org=Acme, reject a job -> row comes back with "Moved to Rejected"
```

- [ ] **Step 2: Run — expect failure** — `python -m pytest tests/test_routes_jobs.py -q -k "new_tab_excludes_leads or scenario_filter_composes or scenario_none_filter or source_filter_via_query or org_filter_via_query or clear_all_filters or legacy_scenario_id or feedback_action_under_org"`

- [ ] **Step 3: Implement** the route changes above.

- [ ] **Step 4: Run — expect pass** — new tests, then the whole file:
  `python -m pytest tests/test_routes_jobs.py -q`
  Expect: some **existing** tests that assert the old "All jobs from X (N) — Clear filter" text, or `?content_type=lead` links, or leads-in-New will now fail. Update those assertions to the new behaviour (tabs always present, `?status=lead`, `scenario`/`org` params). List each one you change in the commit body.

- [ ] **Step 5: Commit**

```bash
git add app/routes/jobs.py tests/test_routes_jobs.py
git commit -m "feat: jobs routes use JobFilter; filters compose with status tabs"
```

---

## Task 5: Templates — always-tabs, filter sub-row, chip links, colours

**Files:**
- Modify: `app/templates/jobs/_content.html`, `app/templates/jobs/_macros.html`, `app/templates/jobs/list.html`, `app/templates/jobs/_feedback.html`, `app/templates/jobs/_row.html`, `app/templates/base.html`
- Test: `tests/test_routes_jobs.py` (render assertions)

**Interfaces:**
- Consumes: context keys from Task 4 (`filter`, `scenarios`, `sources`, `companies`, `counts`, `jobs`, `stale_jobs`, `status`).

### 5a. Shared filter-query macro (`_macros.html`)

Add:

```jinja
{% macro qsuffix(filter, detail=false) -%}
{%- if detail -%}?detail=1{%- else -%}?{{ filter.query_params() | urlencode }}{%- endif -%}
{%- endmacro %}
```

`dict | urlencode` in Jinja produces `a=1&b=2`. Verify with a quick test; if it doesn't, fall back to `filter.query_suffix(detail=detail)` (already returns the full `?...` string) and adjust call sites to not add their own `?`.

Replace every existing inline
`{% if filter_status is defined %}?status=...{% endif %}` /
`{% if is_detail_page %}?detail=1{% elif filter_status is defined %}...{% endif %}`
occurrence in `_row.html` (1×), `_feedback.html` (5×: collapse, feedback POST, delete-confirm, pass-as-new, reevaluate, reset), and `_content.html` (2×: bulk reevaluate/reset) with:

```jinja
{{ macros.qsuffix(filter, detail=is_detail_page) }}
```

(`_row.html` already imports `macros`; `_feedback.html` imports it; `_content.html` — add `{% import "jobs/_macros.html" as macros %}` at top if missing.)

### 5b. `_content.html` — tabs + filter row

Replace the whole `<div class="filter-bar"> … </div>` opening block. New structure:

```jinja
{% import "jobs/_macros.html" as macros %}
<div class="filter-bar">
  <div class="filter-links">
    <a href="/jobs?{{ filter.for_status('new').cleared().query_params() | urlencode }}"
       hx-get="/jobs?{{ filter.for_status('new').query_params() | urlencode }}"
       hx-target="#jobs-content" hx-push-url="true"
       {% if status == 'new' %}class="active"{% endif %}
       title="Job postings awaiting your decision — passed at least one scenario's relevance gate.">New Jobs (<span id="count-new">{{ counts.new }}</span>)</a>
    {# ...same pattern for lead / accepted / rejected / not_relevant / trash,
       each using filter.for_status('<tab>')... #}
  </div>
  {% if jobs %}
    <label class="select-all"><input type="checkbox" class="select-all-checkbox" aria-label="Select all"> Select all</label>
  {% endif %}
</div>
<div class="filter-row">
  <label>Scenario
    <select name="scenario" hx-get="/jobs" hx-target="#jobs-content" hx-swap="innerHTML"
            hx-push-url="true" hx-include="[name='status_hidden'],[name='source_id'],[name='org']">
      <option value="">All</option>
      <option value="none" {% if filter.scenario_none %}selected{% endif %}>None</option>
      {% for s in scenarios %}
        <option value="{{ s.id }}" {% if filter.scenario_id == s.id %}selected{% endif %}>{{ s.name }}</option>
      {% endfor %}
    </select>
  </label>
  <label>Source
    <select name="source_id" hx-get="/jobs" hx-target="#jobs-content" hx-swap="innerHTML"
            hx-push-url="true" hx-include="[name='status_hidden'],[name='scenario'],[name='org']">
      <option value="">All</option>
      {% for s in sources %}
        <option value="{{ s.id }}" {% if filter.source_id == s.id %}selected{% endif %}>{{ s.name }}</option>
      {% endfor %}
    </select>
  </label>
  <label>Organization
    <select name="org" hx-get="/jobs" hx-target="#jobs-content" hx-swap="innerHTML"
            hx-push-url="true" hx-include="[name='status_hidden'],[name='scenario'],[name='source_id']">
      <option value="">All</option>
      {% for c in companies %}
        <option value="{{ c }}" {% if filter.org == c %}selected{% endif %}>{{ c }}</option>
      {% endfor %}
    </select>
  </label>
  <input type="hidden" name="status_hidden" value="{{ status }}" form="filter-status-carrier">
  {% if filter.is_narrowed %}
    <a class="filter-clear" href="/jobs?status={{ status }}"
       hx-get="/jobs?status={{ status }}" hx-target="#jobs-content" hx-swap="innerHTML" hx-push-url="true">Clear all filters</a>
  {% endif %}
</div>
```

**Problem to solve:** the three selects must submit `status` too. Options, pick the simplest that works:
1. Give each `<select>` `hx-vals='{"status": "{{ status }}"}'` (htmx merges it into the request). This is the cleanest — drop the `status_hidden` input and the `hx-include` status reference. Keep `hx-include` for the *other two* selects by name.

Use option 1:

```jinja
<select name="scenario" hx-get="/jobs" hx-target="#jobs-content" hx-swap="innerHTML"
        hx-push-url="true" hx-vals='{"status": "{{ status }}"}'
        hx-include="[name='source_id'],[name='org']">
```

Remove the `status_hidden` hidden input entirely.

Delete the old `{% if filter_source_id %}` / `{% elif filter_scenario_id %}` branch that replaced the tab bar — tabs now always render.

Empty state: change `<p>No jobs found.</p>` to
`<p>{% if filter.is_narrowed %}No jobs match these filters.{% else %}No jobs found.{% endif %}</p>`.

### 5c. `list.html` hidden bulk fields

Replace lines 6-9:

```jinja
  <input type="hidden" name="status_filter" value="{{ filter.status_tab }}">
  <input type="hidden" name="scenario_filter" value="{{ filter.query_params().get('scenario', '') }}">
  <input type="hidden" name="source_id_filter" value="{{ filter.source_id or '' }}">
  <input type="hidden" name="org_filter" value="{{ filter.org or '' }}">
```

(Drop `content_type_filter`.) Update `bulk-feedback` / `bulk-delete` route signatures in Task 4 accordingly — do that Task-4 edit now if not already done.

### 5d. `_macros.html` — `meta_tags` chip links

```jinja
{% macro meta_tags(job, filter=none) %}
<dl class="job-tags">
  {% if job.passed_scenario_names %}
  <dt class="sr-only">Matched scenarios</dt>
  {% set sc_ids = (job.passed_scenario_ids or '').split(',') %}
  {% for name in job.passed_scenario_names.split(', ') %}
  <dd>{% if filter and sc_ids[loop.index0] %}<a class="tag tag-accent" onclick="event.stopPropagation()"
      hx-get="/jobs?{{ filter.with_scenario_id(sc_ids[loop.index0]).query_params() | urlencode }}"
      hx-target="#jobs-content" hx-swap="innerHTML" hx-push-url="true">{{ name }}</a>
    {% else %}<span class="tag tag-accent">{{ name }}</span>{% endif %}</dd>
  {% endfor %}
  {% endif %}
  {% if job.status in ("accepted", "rejected", "trash") %}
  <dt class="sr-only">Status</dt><dd>{{ status_badge(job) }}</dd>
  {% endif %}
  <dt class="sr-only">Content type</dt>
  <dd><span class="tag">{{ job.content_type or "unknown" }}</span></dd>
  {% if job.company %}
  <dt class="sr-only">Organization</dt>
  <dd>{% if filter %}<a class="tag tag-org" onclick="event.stopPropagation()"
      hx-get="/jobs?{{ filter.with_org(job.company).query_params() | urlencode }}"
      hx-target="#jobs-content" hx-swap="innerHTML" hx-push-url="true">{{ job.company }}</a>
    {% else %}<span class="tag tag-org">{{ job.company }}</span>{% endif %}</dd>
  {% endif %}
  <dt class="sr-only">Source</dt>
  <dd>{% if filter and job.source_id %}<a class="tag tag-source" onclick="event.stopPropagation()"
      hx-get="/jobs?{{ filter.with_source_id(job.source_id).query_params() | urlencode }}"
      hx-target="#jobs-content" hx-swap="innerHTML" hx-push-url="true">{{ job.source_name or "" }}</a>
    {% else %}<span class="tag tag-source">{{ job.source_name or "" }}</span>{% endif %}</dd>
</dl>
{% endmacro %}
```

This uses the `with_scenario_id` / `with_source_id` / `with_org` helpers defined in Task 1.

Update the three `{{ macros.meta_tags(job) }}` call sites (`_row.html`, `_feedback.html`, and the `_score_tabs`/detail one if present) to `{{ macros.meta_tags(job, filter=filter) }}` where a `filter` is in context; on the standalone job **detail page** (`job_detail` route) there is no `#jobs-content` target, so pass `filter=none` there (chips render as plain spans). Confirm `job_detail`'s context does not set `filter`.

### 5e. `base.html` CSS

After line 167 (`.tag-feedback …`):

```css
    .tag-source { background: var(--warning-tint); color: var(--warning); }
    .tag-org { background: var(--success-tint); color: var(--success-strong); }
    a.tag { text-decoration: none; cursor: pointer; }
    a.tag:hover { filter: brightness(1.08); text-decoration: underline; }
```

After the `.select-all` rules (~line 327):

```css
    .filter-row { display: flex; flex-wrap: wrap; gap: 0.75rem; align-items: center; margin-bottom: 1rem; }
    .filter-row label { display: flex; align-items: center; gap: 0.35rem; font-size: 0.85em; color: var(--text-secondary); }
    .filter-row select { font-family: var(--font-sans); font-size: 0.9em; padding: 2px 4px; }
    .filter-clear { font-size: 0.85em; }
```

### Tests

- [ ] **Step 1: Write failing render tests** (`tests/test_routes_jobs.py`)

```python
def test_filter_row_selects_render_with_selected_state(client, ...):
    # GET /jobs?org=Acme -> '<option value="Acme" selected' in html;
    # scenario/source selects present with "All" option

def test_status_tabs_always_render_even_with_source_filter(client, ...):
    # GET /jobs?source_id=<s> -> 'New Jobs (' in html AND 'Accepted (' in html
    # (old behaviour replaced them with "All jobs from X")

def test_org_chip_is_a_filter_link(client, ...):
    # a job with company "Acme" on /jobs -> response has
    # class="tag tag-org" ... hx-get="/jobs?...org=Acme"

def test_job_detail_page_chips_are_plain(client, ...):
    # GET /jobs/<id> -> 'tag tag-org' present but no hx-get on it
```

- [ ] **Step 2: Run — expect failure**
- [ ] **Step 3: Implement** the template + CSS edits.
- [ ] **Step 4: Run** — `python -m pytest tests/test_routes_jobs.py -q` then full `python -m pytest -q`. Fix any remaining stragglers.
- [ ] **Step 5: Commit**

```bash
git add app/templates/ app/job_filter.py tests/test_routes_jobs.py
git commit -m "feat: jobs-list filter sub-row, always-on status tabs, coloured filter chips"
```

---

## Task 6: Full sweep + manual QA handoff

**Files:** none (verification only), plus any small fixes.

- [ ] **Step 1:** `python -m pytest -q` — full suite green (≥ 1132 + new tests).
- [ ] **Step 2:** `grep -rn "filter_ctx\|filter_status\|filter_scenario_id\|filter_source_id\|content_type_filter" app/` — expect **no** hits in `app/` (all replaced). Grep templates too. Fix leftovers.
- [ ] **Step 3:** Start the dev server against a throwaway DB (use the `run-dev-server` skill recipe). Manually check:
  - New Jobs shows no leads; New Leads still works.
  - Scenario / Source / Organization selects narrow the list and each other; counts update.
  - "None" scenario on Not relevant / Rejected shows scored-no-gate jobs.
  - Clicking an org / source / scenario chip on a card narrows the list; URL updates.
  - "Clear all filters" resets to the plain tab.
  - Accept/reject a job while a filter is active → row gets a "Moved to …" badge in place.
  - Chip colours are visually distinct in light and dark mode.
  - Deep links from Sources page (`?source_id=`) and Scenarios page (`?scenario_id=`) still land correctly.
- [ ] **Step 4:** Leave the dev server running and hand the URL to the user for their own check. Do not merge until they approve.

---

## Self-review notes

- Spec "counts reflect filters" → Task 3 + `test_get_job_counts_respects_*`.
- Spec "New excludes leads" → Task 3 (`counts`) + Task 4 (`_base_kwargs_for_tab['new']`) + tests in both.
- Spec "scenario None = passed no gate" → Task 2 `scenario_gate="none"` clause + test.
- Spec "compose with status" → Task 4 `_jobs_for_filter` + `test_scenario_filter_composes_with_accepted_tab`.
- Spec "chip colours distinct" → Task 5e.
- Spec "legacy `scenario_id` / `source_id` deep-links keep working" → Task 1 alias parsing + `test_legacy_scenario_id_param_still_works`.
- Spec "stale badge in place" → Task 4 `_stale_badge` + `_render_updated_job_html` + test.
- Spec "deleted scenario selected → treat as All": `get_jobs(scenario_id=<gone>)` returns empty rather than error; the select just won't have that option marked. Acceptable — add a note if a test surfaces a real problem.
