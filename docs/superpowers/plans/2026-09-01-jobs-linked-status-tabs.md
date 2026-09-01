# Linked Status Tabs + Always-On Status Badge + Toolbar Polish — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the Jobs page show several status buckets at once (per-tab checkboxes, no mode switch), show a status pill on every job row, seed search to the "real" buckets, and tighten the toolbar.

**Architecture:** `JobFilter` carries an ordered `statuses` tuple instead of a single `status_tab`. A new `get_jobs_for_tabs()` query ORs per-bucket predicates (`_TAB_PREDICATE`, moved out of the route); `search_jobs()` gains a `tabs` filter. The tab bar renders label links (exclusive nav, unchanged) plus checkbox links (toggle one bucket in/out). The status-pill macro is un-gated and moved to the front of the row's tag list.

**Tech Stack:** Python 3, SQLite (FTS5), FastAPI, Jinja2, htmx, pytest, Playwright (for screenshot verification only).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-09-01-jobs-linked-status-tabs-design.md`.
- Work in the worktree `/home/roman/projects/job-seek/.claude/worktrees/fulltext-search` on branch `worktree-fulltext-search`. Verify with `pwd` + `git branch --show-current` before starting; after each commit `git log --oneline -3` and confirm it landed there. Never touch `main`, never merge.
- Run tests with `python -m pytest` (NOT `uv run` — read-only uv cache).
- `get_jobs()`, `get_job()`, `get_job_counts()` in `queries.py` stay exactly as they are — `pipeline.py` and ~40 tests depend on `get_jobs`'s single-criterion kwargs.
- Every `hx-*` attribute and DOM id the current page relies on must survive (`#jobs-content`, `#jobs-status-marker`, `#jobs-add-result`, `bulk-form`, the `data-progress-*` hooks). The search `<input>` stays in `list.html`, outside `#jobs-content`.
- Commit after each task. Commit-message trailer, every commit:
  ```
  Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_011iDPDqKR6BPVMkKDLxMYc4
  ```
- The dev server for screenshots: throwaway DB copy via the `run-dev-server` skill. `temp/shoot.py` already exists in the worktree (gitignored) and screenshots the running server.

---

## File Structure

| File | Change | Responsibility |
| --- | --- | --- |
| `app/job_filter.py` | modify | `statuses` tuple + `status_tab`/`is_multi` props + `for_status`/`with_status_toggled`/`reset_statuses`. |
| `app/db/queries.py` | modify | `_TAB_PREDICATE`, `_tabs_clause()`, new `get_jobs_for_tabs()`, `tabs=` param on `search_jobs()`. |
| `app/routes/jobs.py` | modify | Delete `_BASE_KWARGS_FOR_TAB`; `_effective_tabs()`; `_jobs_for_filter()` → `get_jobs_for_tabs`/`search_jobs`. |
| `app/templates/jobs/_macros.html` | modify | `search_status_badge` → `status_pill`, un-gated, first in `meta_tags`; delete `status_badge`. |
| `app/templates/jobs/_row.html` | modify | Drop the after-title badge line. |
| `app/templates/jobs/_content.html` | modify | Tab checkbox toggles, Reset link, `statuses` join in markers, search shows tabs + summary. |
| `app/templates/base.html` | modify | Toolbar CSS: tighten stack, lighten card, fix Sort/Select-all overflow, tab-checkbox styling. |
| `tests/test_job_filter.py` | modify | multi-status parsing + helpers. |
| `tests/test_queries.py` | modify | `get_jobs_for_tabs` + `search_jobs(tabs=)`. |
| `tests/test_routes_jobs.py` | modify | multi-status route, checkbox links, search seed, always-on pill. |

---

## Task 1: `JobFilter` — ordered `statuses`

**Files:**
- Modify: `app/job_filter.py`
- Test: `tests/test_job_filter.py`

**Interfaces:**
- Produces:
  - `JobFilter.statuses: tuple[str, ...]` (default `("new",)`), replaces the `status_tab` field.
  - `JobFilter.status_tab -> str` property = `statuses[0]` (keeps existing call sites working).
  - `JobFilter.is_multi -> bool`.
  - `JobFilter.for_status(tab) -> JobFilter` (now exclusive: `statuses=(tab,)`).
  - `JobFilter.with_status_toggled(tab) -> JobFilter` (add in `VALID_TABS` order / remove / never empty).
  - `JobFilter.reset_statuses() -> JobFilter` (`statuses=("new",)`).
  - `query_params()["status"]` = comma-join.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_job_filter.py` (import is `from app.job_filter import JobFilter`):

```python
def test_statuses_default_is_single_new():
    f = JobFilter.from_params({})
    assert f.statuses == ("new",)
    assert f.status_tab == "new"
    assert f.is_multi is False


def test_statuses_comma_list_kept_in_order_and_deduped():
    f = JobFilter.from_params({"status": "accepted,new,accepted,bogus,trash"})
    assert f.statuses == ("accepted", "new", "trash")
    assert f.is_multi is True
    assert f.status_tab == "accepted"


def test_statuses_all_junk_falls_back_to_new():
    assert JobFilter.from_params({"status": "bogus, ,"}).statuses == ("new",)


def test_query_params_joins_statuses():
    assert JobFilter.from_params({"status": "new,accepted"}).query_params()["status"] == "new,accepted"


def test_for_status_is_exclusive():
    f = JobFilter.from_params({"status": "new,accepted"}).for_status("rejected")
    assert f.statuses == ("rejected",)


def test_with_status_toggled_adds_in_valid_tabs_order():
    f = JobFilter.from_params({"status": "accepted"}).with_status_toggled("new")
    assert f.statuses == ("new", "accepted")


def test_with_status_toggled_removes_present_tab():
    f = JobFilter.from_params({"status": "new,accepted"}).with_status_toggled("new")
    assert f.statuses == ("accepted",)


def test_with_status_toggled_never_empties():
    assert JobFilter.from_params({"status": "new"}).with_status_toggled("new").statuses == ("new",)


def test_reset_statuses_returns_to_new():
    assert JobFilter.from_params({"status": "new,trash"}).reset_statuses().statuses == ("new",)
```

- [ ] **Step 2: Run, verify they fail**

Run: `python -m pytest tests/test_job_filter.py -q`
Expected: the new tests FAIL (`statuses` attribute missing, `is_multi`/`with_status_toggled`/`reset_statuses` missing).

- [ ] **Step 3: Implement**

In `app/job_filter.py`:

Replace the dataclass field `status_tab: str = "new"` with:

```python
    statuses: tuple[str, ...] = ("new",)
```

In `from_params`, replace the current tab handling:

```python
        tab = get("status") or "new"
        if tab not in VALID_TABS:
            tab = "new"
```

with:

```python
        raw_status = get("status") or ""
        picked: list[str] = []
        for part in raw_status.split(","):
            part = part.strip()
            if part in VALID_TABS and part not in picked:
                picked.append(part)
        statuses = tuple(picked) or ("new",)
```

Update the `return cls(...)` call — `statuses` is now the first positional field:

```python
        return cls(statuses, scenario_id, scenario_none, source_id, org, org_none, order, query)
```

Replace `query_params()`'s first line `out = {"status": self.status_tab}` with:

```python
        out = {"status": ",".join(self.statuses)}
```

Replace `cleared()`:

```python
    def cleared(self) -> "JobFilter":
        return JobFilter(statuses=self.statuses, order=self.order)
```

Replace `for_status()`:

```python
    def for_status(self, tab: str) -> "JobFilter":
        return replace(self, statuses=(tab,))
```

Add these (next to the other helpers):

```python
    @property
    def status_tab(self) -> str:
        return self.statuses[0]

    @property
    def is_multi(self) -> bool:
        return len(self.statuses) > 1

    def with_status_toggled(self, tab: str) -> "JobFilter":
        if tab in self.statuses:
            remaining = tuple(s for s in self.statuses if s != tab)
            return replace(self, statuses=remaining or self.statuses)
        return replace(self, statuses=tuple(s for s in VALID_TABS if s in self.statuses or s == tab))

    def reset_statuses(self) -> "JobFilter":
        return replace(self, statuses=("new",))
```

`for_status` is used to build tab-nav URLs; `with_scenario_id` / `with_org` / `with_source_id` already use `replace()` so they carry `statuses` through unchanged.

- [ ] **Step 4: Run, verify pass**

Run: `python -m pytest tests/test_job_filter.py -q`
Expected: all PASS (new tests + the 5 pre-existing `.status_tab` assertions still pass via the property).

- [ ] **Step 5: Commit**

```bash
git add app/job_filter.py tests/test_job_filter.py
git commit -m "$(cat <<'EOF'
feat: JobFilter carries an ordered statuses tuple

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_011iDPDqKR6BPVMkKDLxMYc4
EOF
)"
```

---

## Task 2: `get_jobs_for_tabs()` + `search_jobs(tabs=)`

**Files:**
- Modify: `app/db/queries.py`
- Test: `tests/test_queries.py`

**Interfaces:**
- Consumes: existing module names `_GATE_SELECT`, `_GATE_JOIN`, `_GATE_PASSED_CLAUSE`, `_GATE_FAILED_CLAUSE`, `_ORDER_BY`, `_composable_clauses`, `_rows_to_dicts`, `_SEARCH_RANK`, `_fts_match_query`.
- Produces:
  - `_TAB_PREDICATE: dict[str, str]` — SQL boolean per tab name.
  - `_tabs_clause(tabs: list[str]) -> str` — `"(" + " OR ".join(...) + ")"`.
  - `get_jobs_for_tabs(conn, tabs, *, source_id=None, scenario_id=None, org=None, org_none=False, scenario_none=False, order="change") -> list[dict]`.
  - `search_jobs(conn, query, *, tabs=None, source_id=None, scenario_id=None, org=None, org_none=False) -> list[dict]` — new leading-ish `tabs` kwarg.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_queries.py` (has `from app.db import queries as q`). Reuse the file's existing job-seeding helpers if present; otherwise add:

```python
def _tab_job(conn, sid, url, *, title="T", status="new", content_type="job_posting",
             scenario_id=None, score=0.9):
    jid = q.insert_job(conn, source_id=sid, url=url, title=title, company="", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="c", content_type=content_type,
                          title=title, summary="")
    if scenario_id is not None:
        q.upsert_job_score(conn, jid, scenario_id, score, "m", "h")
    q.mark_job_evaluation_complete(conn, jid)
    if status != "new":
        q.update_job_feedback(conn, jid, status, "n")
    return jid


def test_get_jobs_for_tabs_single_matches_get_jobs_accepted(conn):
    sid = q.insert_source(conn, "s", "http://s", "generic_listing")
    a = _tab_job(conn, sid, "http://s/1", status="accepted")
    _tab_job(conn, sid, "http://s/2", status="new")
    assert [j["id"] for j in q.get_jobs_for_tabs(conn, ["accepted"])] == [a]


def test_get_jobs_for_tabs_unions_buckets(conn):
    sid = q.insert_source(conn, "s", "http://s", "generic_listing")
    scn = q.insert_scenario(conn, "S", "")
    new_job = _tab_job(conn, sid, "http://s/1", scenario_id=scn, score=0.95)  # passes gate -> New
    acc = _tab_job(conn, sid, "http://s/2", status="accepted")
    _tab_job(conn, sid, "http://s/3", status="rejected")
    ids = {j["id"] for j in q.get_jobs_for_tabs(conn, ["new", "accepted"])}
    assert ids == {new_job, acc}


def test_get_jobs_for_tabs_splits_new_and_not_relevant_on_gate(conn):
    sid = q.insert_source(conn, "s", "http://s", "generic_listing")
    scn = q.insert_scenario(conn, "S", "")
    passed = _tab_job(conn, sid, "http://s/1", scenario_id=scn, score=0.95)
    failed = _tab_job(conn, sid, "http://s/2", scenario_id=scn, score=0.05)
    assert [j["id"] for j in q.get_jobs_for_tabs(conn, ["new"])] == [passed]
    assert [j["id"] for j in q.get_jobs_for_tabs(conn, ["not_relevant"])] == [failed]


def test_get_jobs_for_tabs_applies_org_filter(conn):
    sid = q.insert_source(conn, "s", "http://s", "generic_listing")
    a = q.insert_job(conn, source_id=sid, url="http://s/1", title="T", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, a, simplified_content="c", content_type="job_posting", title="T", summary="")
    q.mark_job_evaluation_complete(conn, a)
    b = q.insert_job(conn, source_id=sid, url="http://s/2", title="T", company="Beta", raw_text="r")
    q.update_job_pipeline(conn, b, simplified_content="c", content_type="job_posting", title="T", summary="")
    q.mark_job_evaluation_complete(conn, b)
    assert [j["id"] for j in q.get_jobs_for_tabs(conn, ["new", "not_relevant"], org="Beta")] == [b]


def test_search_jobs_tabs_excludes_trash_unless_asked(conn):
    sid = q.insert_source(conn, "s", "http://s", "generic_listing")
    keep = _tab_job(conn, sid, "http://s/1", title="Rust Engineer")
    trash = _tab_job(conn, sid, "http://s/2", title="Rust Engineer", status="trash")
    got = {j["id"] for j in q.search_jobs(conn, "rust", tabs=["new", "accepted", "rejected"])}
    assert got == {keep}
    got2 = {j["id"] for j in q.search_jobs(conn, "rust", tabs=["new", "trash"])}
    assert trash in got2
```

- [ ] **Step 2: Run, verify they fail**

Run: `python -m pytest tests/test_queries.py -k "for_tabs or tabs_excludes" -q`
Expected: FAIL — `get_jobs_for_tabs` missing / `search_jobs()` got unexpected kwarg `tabs`.

- [ ] **Step 3: Implement**

In `app/db/queries.py`, just above `def get_jobs(` add:

```python
_TAB_PREDICATE: dict[str, str] = {
    "new": f"(jobs.status = 'new' AND jobs.content_type = 'job_posting' AND ({_GATE_PASSED_CLAUSE}))",
    "lead": "(jobs.status = 'new' AND jobs.content_type = 'lead')",
    "accepted": "(jobs.status = 'accepted')",
    "rejected": "(jobs.status = 'rejected')",
    "not_relevant": f"(jobs.status = 'new' AND jobs.content_type = 'job_posting' AND ({_GATE_FAILED_CLAUSE}))",
    "trash": "(jobs.status = 'trash')",
}


def _tabs_clause(tabs: list[str]) -> str:
    parts = [_TAB_PREDICATE[t] for t in tabs if t in _TAB_PREDICATE]
    if not parts:
        parts = [_TAB_PREDICATE["new"]]
    return "(" + " OR ".join(parts) + ")"


def get_jobs_for_tabs(
    conn: sqlite3.Connection,
    tabs: list[str],
    *,
    source_id: int | None = None,
    scenario_id: int | None = None,
    org: str | None = None,
    org_none: bool = False,
    scenario_none: bool = False,
    order: str = "change",
) -> list[dict]:
    clauses = [_tabs_clause(tabs)]
    _c, params = _composable_clauses(
        source_id=source_id, scenario_id=scenario_id, org=org, org_none=org_none
    )
    clauses += _c
    if scenario_none:
        clauses.append(
            "(scored.scored_count IS NOT NULL AND (gate.passed_count IS NULL OR gate.passed_count = 0))"
        )
    sql = (
        f"SELECT {_GATE_SELECT} {_GATE_JOIN} WHERE "
        + " AND ".join(clauses)
        + " ORDER BY "
        + _ORDER_BY.get(order, _ORDER_BY["change"])
    )
    return _rows_to_dicts(conn.execute(sql, params).fetchall())
```

Change `search_jobs`'s signature to add `tabs` (put it first among the keyword-only params):

```python
def search_jobs(
    conn: sqlite3.Connection,
    query: str,
    *,
    tabs: list[str] | None = None,
    source_id: int | None = None,
    scenario_id: int | None = None,
    org: str | None = None,
    org_none: bool = False,
) -> list[dict]:
```

and in its body, after `clauses, params = _composable_clauses(...)`:

```python
    if tabs:
        clauses = [_tabs_clause(tabs), *clauses]
```

(The `WHERE jobs_fts MATCH ?` + `" AND ".join(clauses)` assembly already handles the extra clause.)

- [ ] **Step 4: Run, verify pass**

Run: `python -m pytest tests/test_queries.py -q`
Expected: all PASS — the new tests plus every pre-existing `get_jobs` / `search_jobs` test (neither of those functions' existing behaviour changed).

- [ ] **Step 5: Commit**

```bash
git add app/db/queries.py tests/test_queries.py
git commit -m "$(cat <<'EOF'
feat: get_jobs_for_tabs() + tabs filter on search_jobs()

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_011iDPDqKR6BPVMkKDLxMYc4
EOF
)"
```

---

## Task 3: Route wiring

**Files:**
- Modify: `app/routes/jobs.py`
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: `JobFilter.statuses` / `.is_multi` / `.searching` (Task 1); `q.get_jobs_for_tabs` / `q.search_jobs(tabs=)` (Task 2).
- Produces: `_effective_tabs(f: JobFilter) -> list[str]`; `_jobs_for_filter` routes through the new queries.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_routes_jobs.py` (has `from app.db import queries as q`; reuse `_seed_searchable` / `_seed` already in the file):

```python
def test_job_list_multi_status_shows_union(client, conn):
    sid = q.insert_source(conn, "s", "https://s", "generic_listing")
    new_j = q.insert_job(conn, source_id=sid, url="http://s/1", title="Alpha Role", company="A", raw_text="r")
    q.update_job_pipeline(conn, new_j, simplified_content="c", content_type="job_posting", title="Alpha Role", summary="")
    scn = q.insert_scenario(conn, "S", "")
    q.upsert_job_score(conn, new_j, scn, 0.95, "m", "h")
    q.mark_job_evaluation_complete(conn, new_j)
    acc_j = q.insert_job(conn, source_id=sid, url="http://s/2", title="Beta Role", company="B", raw_text="r")
    q.update_job_pipeline(conn, acc_j, simplified_content="c", content_type="job_posting", title="Beta Role", summary="")
    q.mark_job_evaluation_complete(conn, acc_j)
    q.update_job_feedback(conn, acc_j, "accepted", "y")

    only_new = client.get("/jobs?status=new").text
    assert "Alpha Role" in only_new and "Beta Role" not in only_new

    both = client.get("/jobs?status=new,accepted").text
    assert "Alpha Role" in both and "Beta Role" in both


def test_search_seeds_the_real_buckets(client, conn):
    sid = q.insert_source(conn, "s", "https://s", "generic_listing")
    live = q.insert_job(conn, source_id=sid, url="http://s/1", title="Rust Engineer", company="A", raw_text="r")
    q.update_job_pipeline(conn, live, simplified_content="c", content_type="job_posting", title="Rust Engineer", summary="")
    q.mark_job_evaluation_complete(conn, live)
    tr = q.insert_job(conn, source_id=sid, url="http://s/2", title="Rust Engineer Two", company="B", raw_text="r")
    q.update_job_pipeline(conn, tr, simplified_content="c", content_type="job_posting", title="Rust Engineer Two", summary="")
    q.mark_job_evaluation_complete(conn, tr)
    q.update_job_feedback(conn, tr, "trash", "spam")

    seeded = client.get("/jobs?q=rust&status=new").text
    assert "Rust Engineer" in seeded
    assert "Rust Engineer Two" not in seeded          # trash excluded from the seed

    with_trash = client.get("/jobs?q=rust&status=new,lead,accepted,rejected,trash").text
    assert "Rust Engineer Two" in with_trash
```

- [ ] **Step 2: Run, verify they fail**

Run: `python -m pytest tests/test_routes_jobs.py -k "multi_status or seeds_the_real" -q`
Expected: FAIL — `?status=new,accepted` currently 500s (`_BASE_KWARGS_FOR_TAB["new,accepted"]` KeyError) or shows only one bucket.

- [ ] **Step 3: Implement**

In `app/routes/jobs.py`:

Delete the `_BASE_KWARGS_FOR_TAB = { ... }` dict entirely.

Replace `_jobs_for_filter`:

```python
_SEARCH_SEED_TABS = ("new", "lead", "accepted", "rejected")


def _effective_tabs(f: JobFilter) -> list[str]:
    if f.searching and not f.is_multi:
        seed = set(f.statuses) | set(_SEARCH_SEED_TABS)
        return [t for t in VALID_TABS if t in seed]
    return list(f.statuses)


def _jobs_for_filter(conn: sqlite3.Connection, f: JobFilter) -> list[dict]:
    tabs = _effective_tabs(f)
    if f.searching:
        return q.search_jobs(
            conn, f.q, tabs=tabs,
            source_id=f.source_id, scenario_id=f.scenario_id,
            org=f.org, org_none=f.org_none,
        )
    return q.get_jobs_for_tabs(
        conn, tabs,
        source_id=f.source_id, scenario_id=f.scenario_id,
        org=f.org, org_none=f.org_none, scenario_none=f.scenario_none,
        order=f.order,
    )
```

Add the import at the top of `app/routes/jobs.py` if not present:

```python
from app.job_filter import JobFilter, VALID_TABS
```

(Check the existing import line — it currently imports `JobFilter`; extend it to include `VALID_TABS`.)

Leave `_content_context` (`"status": f.status_tab` still fine), `_counts_for_filter`, `_stale_badge`, `_render_updated_job_html`, and `job_feedback`'s `if not f.searching` counts guard unchanged — they all funnel job lists through `_jobs_for_filter`.

- [ ] **Step 4: Run, verify pass**

Run: `python -m pytest tests/test_routes_jobs.py -q`
Expected: all PASS. If a pre-existing test breaks because it asserted a bare `status=<one>` in a tab-link `href`, that is expected fallout from Task 5's markup — defer it to Task 5. If it breaks for another reason, fix here.

- [ ] **Step 5: Commit**

```bash
git add app/routes/jobs.py tests/test_routes_jobs.py
git commit -m "$(cat <<'EOF'
feat: jobs list honours multi-status filters and seeds search

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_011iDPDqKR6BPVMkKDLxMYc4
EOF
)"
```

---

## Task 4: Always-on status pill

**Files:**
- Modify: `app/templates/jobs/_macros.html`
- Modify: `app/templates/jobs/_row.html`
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Produces: `status_pill(job)` macro (replaces `search_status_badge`); rendered as the first `<dd>` in `meta_tags`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_routes_jobs.py`:

```python
def test_every_row_shows_a_status_pill_in_normal_view(client, conn):
    sid = q.insert_source(conn, "s", "https://s", "generic_listing")
    j = q.insert_job(conn, source_id=sid, url="http://s/1", title="Alpha Role", company="A", raw_text="r")
    q.update_job_pipeline(conn, j, simplified_content="c", content_type="job_posting", title="Alpha Role", summary="")
    scn = q.insert_scenario(conn, "S", "")
    q.upsert_job_score(conn, j, scn, 0.95, "m", "h")
    q.mark_job_evaluation_complete(conn, j)
    html = client.get("/jobs?status=new").text
    assert "status-pill status-pill-new" in html


def test_accepted_row_pill_in_accepted_tab(client, conn):
    sid = q.insert_source(conn, "s", "https://s", "generic_listing")
    j = q.insert_job(conn, source_id=sid, url="http://s/1", title="Beta Role", company="B", raw_text="r")
    q.update_job_pipeline(conn, j, simplified_content="c", content_type="job_posting", title="Beta Role", summary="")
    q.mark_job_evaluation_complete(conn, j)
    q.update_job_feedback(conn, j, "accepted", "y")
    html = client.get("/jobs?status=accepted").text
    assert "status-pill status-pill-accepted" in html


def test_no_after_title_badge(client, conn):
    _seed_searchable(conn, q.insert_source(conn, "s", "https://s", "generic_listing"), "http://s/1", "Gamma Engineer")
    html = client.get("/jobs?q=gamma").text
    # the pill lives in the tag list <dl>, not loose in the <h3> header row
    assert 'class="job-row-header"' in html
    header = html.split('class="job-row-header"', 1)[1].split("</div>", 1)[0]
    assert "status-pill" not in header
```

- [ ] **Step 2: Run, verify they fail**

Run: `python -m pytest tests/test_routes_jobs.py -k "status_pill or after_title or accepted_row_pill" -q`
Expected: FAIL — `status-pill-new` not emitted in normal view; the pill is currently in the header.

- [ ] **Step 3: Implement**

In `app/templates/jobs/_macros.html`:

Delete the `status_badge` macro entirely.

Rename `search_status_badge` to `status_pill` (keep its body — the branch logic is already correct for all six buckets):

```jinja
{% macro status_pill(job) %}
{% if job.status == "accepted" %}<span class="status-pill status-pill-accepted">Accepted</span>
{% elif job.status == "rejected" %}<span class="status-pill status-pill-rejected">Rejected</span>
{% elif job.status == "trash" %}<span class="status-pill status-pill-trash">Trash</span>
{% elif job.content_type == "lead" %}<span class="status-pill status-pill-lead">Lead</span>
{% elif job.content_type == "job_posting" and (job.passed_gate_count or job.gate_override) %}<span class="status-pill status-pill-new">New</span>
{% elif job.content_type == "job_posting" %}<span class="status-pill status-pill-plain">Not relevant</span>
{% else %}<span class="status-pill status-pill-plain">{{ job.content_type or "unknown" }}</span>
{% endif %}
{% endmacro %}
```

In `meta_tags`, replace the conditional `status_badge` block:

```jinja
  {% if job.status == "accepted" or job.status == "rejected" or job.status == "trash" %}
  <dt class="sr-only">Status</dt>
  <dd>{{ status_badge(job) }}</dd>
  {% endif %}
```

with an unconditional pill as the **first** entry — move it to immediately after `<dl class="job-tags">`, before the `{% if job.passed_scenario_names %}` block:

```jinja
<dl class="job-tags">
  <dt class="sr-only">Status</dt>
  <dd>{{ status_pill(job) }}</dd>
  {% if job.passed_scenario_names %}
  ...
```

In `app/templates/jobs/_row.html`, delete the line:

```jinja
      {% if filter and filter.searching %}{{ macros.search_status_badge(job) }}{% endif %}
```

- [ ] **Step 4: Run, verify pass**

Run: `python -m pytest tests/test_routes_jobs.py -q`
Expected: all PASS. Update any pre-existing assertion that looked for `search_status_badge` output in the header or for the old `score-badge score-high">Accepted` markup in the tag list — the pill class is now `status-pill status-pill-accepted`. Note each change in the commit body.

- [ ] **Step 5: Commit**

```bash
git add app/templates/jobs/_macros.html app/templates/jobs/_row.html tests/test_routes_jobs.py
git commit -m "$(cat <<'EOF'
feat: status pill on every job row, first in the tag list

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_011iDPDqKR6BPVMkKDLxMYc4
EOF
)"
```

---

## Task 5: Linked-tab UI in `_content.html`

**Files:**
- Modify: `app/templates/jobs/_content.html`
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: `filter.statuses` / `.is_multi` / `.for_status` / `.with_status_toggled` / `.reset_statuses` (Task 1).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_routes_jobs.py`:

```python
def test_tab_checkbox_link_toggles_one_status(client, conn):
    _seed(conn)
    html = client.get("/jobs?status=new").text
    # an "add Accepted to the view" control pointing at status=new,accepted
    assert "status=new%2Caccepted" in html or "status=new,accepted" in html


def test_reset_link_only_when_multi(client, conn):
    _seed(conn)
    assert "js-tab-reset" not in client.get("/jobs?status=new").text
    assert "js-tab-reset" in client.get("/jobs?status=new,accepted").text


def test_search_view_keeps_tab_bar(client, conn):
    _seed_searchable(conn, q.insert_source(conn, "s", "https://s", "generic_listing"), "http://s/1", "Delta Engineer")
    html = client.get("/jobs?q=delta").text
    assert 'class="filter-links"' in html          # tabs stay visible during search
    assert "results for" in html                    # summary also shown
    assert 'name="order"' not in html               # sort still hidden while searching


def test_status_marker_carries_full_set(client, conn):
    _seed(conn)
    html = client.get("/jobs?status=new,accepted").text
    assert '<input type="hidden" id="jobs-status-marker" name="status" value="new,accepted"' in html
```

- [ ] **Step 2: Run, verify they fail**

Run: `python -m pytest tests/test_routes_jobs.py -k "tab_checkbox or reset_link or search_view_keeps or marker_carries" -q`
Expected: FAIL.

- [ ] **Step 3: Implement**

Edit `app/templates/jobs/_content.html`:

**(a)** The two markers that render the status set:

```jinja
<input type="hidden" id="jobs-status-marker" name="status" value="{{ filter.statuses | join(',') }}">
<input type="hidden" name="status_filter" value="{{ filter.statuses | join(',') }}" form="bulk-form">
```

**(b)** Replace the `.filter-bar` block. It currently branches `{% if filter.searching %}` → summary, `{% else %}` → tab links + `.filter-tools`. New shape: the tab links **always** render; the summary renders *in addition* above them when searching; `.filter-tools` renders only when **not** searching.

Clear search returns to the single primary tab with no query — build the target once:

```jinja
{% if filter.searching %}
{% set clear_search = {"status": filter.status_tab} | urlencode %}
<div class="search-summary">
  <span>{{ jobs | length }} result{{ '' if jobs | length == 1 else 's' }} for “{{ filter.q }}”</span>
  <a class="filter-clear" href="/jobs?{{ clear_search }}"
     hx-get="/jobs?{{ clear_search }}"
     hx-target="#jobs-content" hx-swap="innerHTML" hx-push-url="true">Clear search</a>
</div>
{% endif %}
<div class="filter-bar">
  <div class="filter-links{% if filter.is_multi %} is-multi{% endif %}">
    {% for tab, label, count_id, count, tip in tab_defs %}
    {% set active = tab in filter.statuses %}
    {% set toggle = filter.with_status_toggled(tab).query_params() | urlencode %}
    {% set solo = filter.for_status(tab).query_params() | urlencode %}
    <span class="tab-item{% if active %} active{% endif %}">
      <a class="tab-check" href="/jobs?{{ toggle }}"
         hx-get="/jobs?{{ toggle }}" hx-target="#jobs-content" hx-swap="innerHTML" hx-push-url="true"
         title="{{ 'Remove from view' if active else 'Add to view' }}"
         aria-label="{{ 'Remove' if active else 'Add' }} {{ label }}">{{ '✓' if active else '+' }}</a>
      <a class="tab-label" href="/jobs?{{ solo }}"
         hx-get="/jobs?{{ solo }}" hx-target="#jobs-content" hx-swap="innerHTML" hx-push-url="true"
         title="{{ tip | safe }}">{{ label }}<span class="tab-count" id="{{ count_id }}">{{ count }}</span></a>
    </span>
    {% endfor %}
    {% if filter.is_multi %}
    <a class="js-tab-reset filter-clear" href="/jobs?{{ filter.reset_statuses().query_params() | urlencode }}"
       hx-get="/jobs?{{ filter.reset_statuses().query_params() | urlencode }}"
       hx-target="#jobs-content" hx-swap="innerHTML" hx-push-url="true">Reset</a>
    {% endif %}
  </div>
  {% if not filter.searching %}
  <div class="filter-tools">
    <label>Sort
      <select name="order" hx-get="/jobs" hx-target="#jobs-content" hx-swap="innerHTML"
              hx-push-url="true" hx-vals='{"status": "{{ filter.statuses | join(',') }}"}'
              hx-include="[name='scenario'],[name='source_id'],[name='org']">
        <option value="change" {% if filter.order == 'change' %}selected{% endif %}>Newest changes</option>
        <option value="score" {% if filter.order == 'score' %}selected{% endif %}>Fit score</option>
        <option value="age" {% if filter.order == 'age' %}selected{% endif %}>Posting age</option>
      </select>
    </label>
    {% if jobs %}
    <label class="select-all">
      <input type="checkbox" class="select-all-checkbox" aria-label="Select all">
      Select all
    </label>
    {% endif %}
  </div>
  {% endif %}
</div>
```

**(c)** The Sort `hx-vals` and every filter-select `hx-vals='{"status": "{{ status }}"}'` currently pass the single `status`. Change each to `'{"status": "{{ filter.statuses | join(',') }}"}'` so a filter change preserves the whole set. (Three selects in `.filter-row` + the Sort select.)

**(d)** The "no results" `<p>` line and the search input's own `hx-vals` (in `list.html`) — the search input has `hx-include="#jobs-status-marker, ..."` which now carries the joined value, so no change needed there. Leave `list.html` alone.

- [ ] **Step 4: Run, verify pass**

Run: `python -m pytest tests/test_routes_jobs.py -q`
Expected: all PASS. Fix any pre-existing test that asserted the old single-`<a>`-per-tab markup or `filter-links a` structure — the tab is now `.tab-item > .tab-check + .tab-label`. Keep the assertions' intent (tab present / active / count shown), update the selector. List every change in the commit body.

- [ ] **Step 5: Commit**

```bash
git add app/templates/jobs/_content.html tests/test_routes_jobs.py
git commit -m "$(cat <<'EOF'
feat: per-tab checkboxes to combine status buckets in the jobs list

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_011iDPDqKR6BPVMkKDLxMYc4
EOF
)"
```

---

## Task 6: Toolbar polish (screenshot-driven)

**Files:**
- Modify: `app/templates/base.html` (the `/* Jobs page toolbar */` style block, ~lines 349–436)
- Modify: `app/templates/jobs/_content.html` (only if a wrapper/class is needed)
- No new tests — this is visual. Existing route tests must stay green.

**Interfaces:** none — pure styling. Do not rename any class the Task 5 tests assert on (`filter-links`, `tab-item`, `tab-check`, `tab-label`, `tab-count`, `js-tab-reset`, `search-summary`, `filter-tools`, `select-all`, `status-pill*`).

- [ ] **Step 1: Start the dev server + baseline screenshots**

Use the `run-dev-server` skill (throwaway DB copy). Then:

```bash
python temp/shoot.py
```

Read `temp/jobs-normal-1200.png`, `temp/jobs-normal-820.png`, `temp/jobs-search-1200.png`, `temp/jobs-narrowed-1200.png`. If the port in `temp/shoot.py` doesn't match the server, edit the four URLs in that file first.

- [ ] **Step 2: Apply the polish**

Invoke the `frontend-design` skill and work against the screenshots. Target state (acceptance criteria — re-screenshot until all hold):

1. **Vertical rhythm.** Gaps between `h1` → Add row → search/filter → tab bar → first job row are each roughly one text line (~0.5rem), no band noticeably larger than the others. The whole toolbar above the first job fits well within a laptop viewport.
2. **Card weight.** The search+filter block is no longer a heavy bordered box. Acceptable: the search field with a subtle background/hairline and a single divider line to the selects row; the selects row without its own full border. It must not look like a raised card competing with the job rows.
3. **Sort / Select-all.** Never clips at any width from 1440 down to 700px. At wide widths it does not sit alone in an empty band. Put it on the same row as the Scenario/Source/Organization selects, right-aligned (`margin-left:auto`), wrapping *below* the selects on narrow widths rather than overflowing. Sort and Select-all stay adjacent.
4. **Tab checkboxes.** In the single-status default (`.filter-links` without `.is-multi`), the `+` on inactive tabs is hidden until the `.tab-item` is hovered/focused; the active tab shows no marker (or a subtle one). When `.filter-links.is-multi`, every tab shows its marker (`✓` filled/accent for active, `+` hollow for inactive). Markers are small, aligned with the label baseline, and clearly clickable (cursor, hover colour). The bar must not look cluttered in the common single-status case.
5. **Reset link** reads as a quiet text link at the end of the tab row, not a button.
6. **Search-active.** The `search-summary` line sits directly above the tab bar; the two together don't add a big gap. Sort/Select-all absent (already handled in markup).
7. **Status pill** is legible as the first tag on every row at both widths, visually distinct from the numeric `%` score badge, and doesn't wrap awkwardly.

- [ ] **Step 3: Regression check**

Run: `python -m pytest -q tests/test_routes_jobs.py`
Expected: green (styling shouldn't touch assertions; if you renamed something the tests check, you went too far — revert that rename).

- [ ] **Step 4: Final screenshots + commit**

```bash
python temp/shoot.py
```
Re-read all four. When they meet the criteria:

```bash
git add app/templates/base.html app/templates/jobs/_content.html
git commit -m "$(cat <<'EOF'
style(jobs): tighten toolbar, lighten filter card, fix Sort/Select-all overflow

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_011iDPDqKR6BPVMkKDLxMYc4
EOF
)"
```

---

## Task 7: Full verification + manual smoke

- [ ] **Step 1: Whole suite**

Run: `python -m pytest -q`
Expected: all PASS. Investigate any failure; do not hand off red.

- [ ] **Step 2: Lint (if configured)**

Check `pyproject.toml` for `[tool.ruff]`. If present: `python -m ruff check app tests` and fix anything new. Else skip.

- [ ] **Step 3: Manual smoke via dev server**

With the throwaway-DB dev server running, screenshot + click through:
- `/jobs` — single "New" tab, no checkboxes visible until hover, status pill on every row, toolbar compact.
- Hover another tab → `+` appears; click it → both tabs active, all markers show, Reset link appears, rows from both buckets interleaved by sort, each with its pill.
- Click a tab label → collapses to that one tab exclusively.
- `/jobs?status=new,accepted,rejected` directly → three buckets merged.
- Type in search → tabs stay, seeded to New/Leads/Accepted/Rejected, "N results for …" above the tabs, Sort gone; click the Trash tab's `+` → trash matches appear; Clear search → back to single New tab.
- Expand a row, Accept it → row updates in place with the new pill (still shown, since Accepted is in the set) or gets the stale badge (if not).
- Narrow by Organization while multi-status → set preserved, results narrowed.

- [ ] **Step 4: Hand off**

Leave the dev server running. Give the user the URL (normal + `?q=…` + a `?status=new,accepted,rejected` link). Report: what changed per task, the `python -m pytest -q` summary line, every test assertion updated and why, and a short description of the final toolbar in single / multi / search states. Do not merge.

---

## Self-Review

**Spec coverage:**
- Linked tabs, checkbox toggle, label = exclusive, Reset, reveal-on-hover → Tasks 1, 5, 6. ✓
- `status=` comma list, `statuses` tuple, `status_tab`/`is_multi`/helpers → Task 1. ✓
- Search seeds New+Leads+Accepted+Rejected unioned with current tab; tabs stay visible in search; summary above tabs; Sort/Select-all hidden in search → Tasks 3 (`_effective_tabs`), 5 (markup). ✓
- `_TAB_PREDICATE` moved to queries, `_tabs_clause`, `get_jobs_for_tabs`, `search_jobs(tabs=)`, `get_jobs`/`get_job`/`get_job_counts` untouched → Task 2. ✓
- Delete `_BASE_KWARGS_FOR_TAB`, `_effective_tabs`, `_jobs_for_filter` rewrite, bulk-form status join → Task 3 + Task 5(a). ✓
- Status pill: rename `search_status_badge`→`status_pill`, un-gate, first in `meta_tags`, delete `status_badge`, drop after-title badge → Task 4. ✓
- Toolbar polish (tighten, lighten card, fix Sort/Select-all overflow, pill legibility) → Task 6. ✓
- Tests for filter / queries / routes → Tasks 1–5; full-suite + manual smoke → Task 7. ✓
- `#jobs-status-marker` / `status_filter` carry the joined set → Task 5(a). ✓

**Placeholder scan:** No TBD/TODO. Task 6 has acceptance criteria + a screenshot loop rather than literal CSS — appropriate for a visual task; the criteria are concrete and checkable. Every logic step shows full code.

**Type consistency:** `statuses` (tuple), `status_tab` (property, str), `is_multi`, `with_status_toggled`, `reset_statuses`, `for_status` — names identical between Task 1 definition and Tasks 3/5 use. `get_jobs_for_tabs` / `_tabs_clause` / `_TAB_PREDICATE` / `search_jobs(tabs=)` — identical between Task 2 and Task 3. `_effective_tabs` / `_SEARCH_SEED_TABS` — Task 3 only. `status_pill` — Task 4 def, Task 4 use in `meta_tags`. Template classes `tab-item`/`tab-check`/`tab-label`/`js-tab-reset` — Task 5 def, Task 5 tests + Task 6 "do not rename" list. ✓
