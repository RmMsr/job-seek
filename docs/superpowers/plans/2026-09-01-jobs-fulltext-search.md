# Jobs-page Full-Text Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a search box to `/jobs` that finds jobs by free text across every status at once, ranking role (`title`) and org (`company`) matches above description-body matches.

**Architecture:** A SQLite FTS5 external-content virtual table (`jobs_fts`) indexes five job columns and is kept in sync by three triggers on the `jobs` table. A new `search_jobs()` query function runs `MATCH` + `bm25()` with per-column weights. `JobFilter` gains a `q` field; when it is set, the `/jobs` route renders global, relevance-ranked results with the status tabs and sort control hidden but the scenario/source/org filters still active.

**Tech Stack:** Python 3, SQLite (FTS5, bundled with the stdlib `sqlite3`), FastAPI, Jinja2, htmx, pytest.

## Global Constraints

- FTS5 is available in the environment's `sqlite3` build (verified). No new dependency.
- Migrations are hard-cutover, create-once, no defensive handling of hypothetical older shapes (project `CLAUDE.md` → *Database migrations*).
- Docs stay terse and human-targeted — no padding.
- Commit after each task's tests pass (project `CLAUDE.md` → *Commit frequently*).
- Every commit message ends with:
  ```
  Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_011iDPDqKR6BPVMkKDLxMYc4
  ```
- Run tests with `python -m pytest` (not `uv run`) per memory `feedback_sandbox_uv_and_dev_server`.

---

## File Structure

| File | Change | Responsibility |
| --- | --- | --- |
| `app/db/schema.py` | modify | New `_migrate_add_jobs_fts()`, appended last in `init_db()`. Creates `jobs_fts` + 3 sync triggers + backfill. |
| `app/db/queries.py` | modify | New `search_jobs()` and `_fts_match_query()` helper; extract `_composable_clauses()` shared with `get_jobs()`. |
| `app/job_filter.py` | modify | `JobFilter.q` field, `searching` property, `q` in `query_params()`. |
| `app/routes/jobs.py` | modify | `_jobs_for_filter()` branches to `search_jobs()` when searching; skip counts OOB on feedback while searching. |
| `app/templates/jobs/list.html` | modify | Search `<input>` on its own row, outside `#jobs-content` (avoids focus loss on keystroke swaps). |
| `app/templates/jobs/_content.html` | modify | Hidden `#jobs-status-marker`; hide tabs + Sort + bulk affordances while searching; "N results" + "Clear search". |
| `app/templates/jobs/_macros.html` | modify | New `search_status_badge(job)` macro. |
| `app/templates/jobs/_row.html` | modify | Render the status badge + hide the bulk checkbox while searching. |
| `tests/test_schema.py` | modify | FTS table creation + backfill tests; fix `_tables()` helper. |
| `tests/test_queries.py` | modify | `search_jobs()` ranking / prefix / AND / narrowing / trigger-sync tests. |
| `tests/test_job_filter.py` | modify | `q` parse + round-trip. |
| `tests/test_routes_jobs.py` | modify | End-to-end search route behaviour. |
| `CLAUDE.md` | modify | One-line rule about `jobs_fts` rebuild after a `jobs` table rebuild. |

---

## Task 1: FTS5 index, sync triggers, migration

**Files:**
- Modify: `app/db/schema.py` (add `_migrate_add_jobs_fts`, call it last in `init_db`)
- Modify: `CLAUDE.md` (*Database migrations* section)
- Test: `tests/test_schema.py`

**Interfaces:**
- Produces: table `jobs_fts` with columns `(title, company, headline, summary, simplified_content)` indexed by `rowid == jobs.id`; triggers `jobs_fts_ai`, `jobs_fts_ad`, `jobs_fts_au`; function `_migrate_add_jobs_fts(conn: sqlite3.Connection) -> None`.

- [ ] **Step 1: Fix the `_tables()` helper and add failing FTS tests**

In `tests/test_schema.py`, change the helper to ignore FTS shadow tables:

```python
def _tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {r["name"] for r in rows if not r["name"].startswith("jobs_fts")}
```

Add these tests (put the import at the top with the others: `from app.db.schema import init_db, _migrate_add_jobs_fts`):

```python
def test_init_db_creates_jobs_fts(conn):
    init_db(conn)
    assert conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='jobs_fts'"
    ).fetchone() is not None


def test_jobs_fts_triggers_sync_on_write(conn):
    init_db(conn)
    conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s', 'http://x', 'slack')")
    conn.execute(
        "INSERT INTO jobs (source_id, url, title, company, raw_text) "
        "VALUES (1, 'http://job/1', 'Kubernetes Platform Lead', 'Acme', 'r')"
    )
    hit = conn.execute(
        "SELECT rowid FROM jobs_fts WHERE jobs_fts MATCH ?", ('"Kubernetes"',)
    ).fetchall()
    assert len(hit) == 1

    conn.execute("UPDATE jobs SET title = 'Rust Compiler Engineer' WHERE id = 1")
    assert conn.execute(
        "SELECT rowid FROM jobs_fts WHERE jobs_fts MATCH ?", ('"Kubernetes"',)
    ).fetchall() == []
    assert len(conn.execute(
        "SELECT rowid FROM jobs_fts WHERE jobs_fts MATCH ?", ('"Rust"',)
    ).fetchall()) == 1

    conn.execute("DELETE FROM jobs WHERE id = 1")
    assert conn.execute(
        "SELECT rowid FROM jobs_fts WHERE jobs_fts MATCH ?", ('"Rust"',)
    ).fetchall() == []


def test_jobs_fts_backfills_existing_rows(conn):
    init_db(conn)
    # Simulate a pre-FTS database: drop the FTS artifacts, insert a job with the
    # triggers gone, then re-run the migration.
    conn.executescript(
        "DROP TRIGGER jobs_fts_ai; DROP TRIGGER jobs_fts_ad; DROP TRIGGER jobs_fts_au; "
        "DROP TABLE jobs_fts;"
    )
    conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s', 'http://x', 'slack')")
    conn.execute(
        "INSERT INTO jobs (source_id, url, title, company, raw_text) "
        "VALUES (1, 'http://job/1', 'Backfilled Staff Role', 'Acme', 'r')"
    )
    _migrate_add_jobs_fts(conn)
    hit = conn.execute(
        "SELECT rowid FROM jobs_fts WHERE jobs_fts MATCH ?", ('"Backfilled"',)
    ).fetchall()
    assert len(hit) == 1
```

- [ ] **Step 2: Run the new tests, verify they fail**

Run: `python -m pytest tests/test_schema.py -q`
Expected: `test_init_db_creates_jobs_fts`, `test_jobs_fts_triggers_sync_on_write`, `test_jobs_fts_backfills_existing_rows` FAIL (`no such table: jobs_fts` / `_migrate_add_jobs_fts` import error). Pre-existing tests still pass.

- [ ] **Step 3: Implement the migration**

In `app/db/schema.py`, add this function just above `def init_db(`:

```python
def _migrate_add_jobs_fts(conn: sqlite3.Connection) -> None:
    # Create-once. Placed last in init_db so it runs after any table rebuild.
    # A future migration that rebuilds the jobs table must follow itself with
    #   INSERT INTO jobs_fts(jobs_fts) VALUES('rebuild')
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='jobs_fts'"
    ).fetchone()
    if row is not None:
        return
    conn.executescript(
        """
        CREATE VIRTUAL TABLE jobs_fts USING fts5(
            title, company, headline, summary, simplified_content,
            content='jobs', content_rowid='id',
            tokenize='porter unicode61'
        );

        CREATE TRIGGER jobs_fts_ai AFTER INSERT ON jobs BEGIN
            INSERT INTO jobs_fts(rowid, title, company, headline, summary, simplified_content)
            VALUES (new.id, new.title, new.company, new.headline, new.summary, new.simplified_content);
        END;

        CREATE TRIGGER jobs_fts_ad AFTER DELETE ON jobs BEGIN
            INSERT INTO jobs_fts(jobs_fts, rowid, title, company, headline, summary, simplified_content)
            VALUES ('delete', old.id, old.title, old.company, old.headline, old.summary, old.simplified_content);
        END;

        CREATE TRIGGER jobs_fts_au AFTER UPDATE ON jobs BEGIN
            INSERT INTO jobs_fts(jobs_fts, rowid, title, company, headline, summary, simplified_content)
            VALUES ('delete', old.id, old.title, old.company, old.headline, old.summary, old.simplified_content);
            INSERT INTO jobs_fts(rowid, title, company, headline, summary, simplified_content)
            VALUES (new.id, new.title, new.company, new.headline, new.summary, new.simplified_content);
        END;

        INSERT INTO jobs_fts(rowid, title, company, headline, summary, simplified_content)
            SELECT id, title, company, headline, summary, simplified_content FROM jobs;
        """
    )
    conn.commit()
```

Then add the call as the **last** line of `init_db()`:

```python
    _migrate_tasks_group_to_parent(conn)
    _migrate_add_jobs_fts(conn)
```

- [ ] **Step 4: Run the tests, verify they pass**

Run: `python -m pytest tests/test_schema.py -q`
Expected: all PASS.

- [ ] **Step 5: Update CLAUDE.md**

In `CLAUDE.md`, at the end of the *Database migrations* section, add:

```markdown
The `jobs_fts` FTS5 index is kept in sync by triggers on the `jobs` table. Any
migration that rebuilds `jobs` (drop/recreate) drops those triggers with it —
follow such a migration with `INSERT INTO jobs_fts(jobs_fts) VALUES('rebuild')`
and recreate the triggers (see `_migrate_add_jobs_fts`).
```

- [ ] **Step 6: Full test run + commit**

Run: `python -m pytest -q`
Expected: all PASS.

```bash
git add app/db/schema.py tests/test_schema.py CLAUDE.md
git commit -m "$(cat <<'EOF'
feat: add jobs_fts FTS5 index with sync triggers

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_011iDPDqKR6BPVMkKDLxMYc4
EOF
)"
```

---

## Task 2: `search_jobs()` query function

**Files:**
- Modify: `app/db/queries.py`
- Test: `tests/test_queries.py`

**Interfaces:**
- Consumes: `jobs_fts` table + `_GATE_SELECT`, `_GATE_JOIN`, `_rows_to_dicts` (existing module-level names in `queries.py`).
- Produces:
  - `_fts_match_query(raw: str) -> str | None` — turns user text into an FTS5 MATCH string (`'senior python'` → `'"senior" "python"*'`), or `None` when no usable tokens.
  - `_composable_clauses(*, source_id, scenario_id, org, org_none) -> tuple[list[str], list]` — SQL fragment list + params for the source/org/scenario narrowing shared with `get_jobs`.
  - `search_jobs(conn, query, *, source_id=None, scenario_id=None, org=None, org_none=False) -> list[dict]` — relevance-ranked job dicts (same shape as `get_jobs`), spanning all statuses.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_queries.py` (imports already have `from app.db import queries as q`):

```python
def _seed_job(conn, sid, url, title, *, company="", summary="", body="", headline=""):
    jid = q.insert_job(conn, source_id=sid, url=url, title=title, company=company, raw_text="r")
    q.update_job_pipeline(
        conn, jid, simplified_content=body, content_type="job_posting",
        title=title, summary=summary, headline=headline,
    )
    return jid


def test_search_jobs_ranks_title_above_body(conn):
    sid = q.insert_source(conn, "s", "http://s", "generic_listing")
    body_hit = _seed_job(conn, sid, "http://s/1", "Office Manager",
                         body="we use python and rust daily")
    title_hit = _seed_job(conn, sid, "http://s/2", "Python Engineer", body="unrelated")
    results = q.search_jobs(conn, "python")
    assert [r["id"] for r in results] == [title_hit, body_hit]


def test_search_jobs_prefix_matches_last_token(conn):
    sid = q.insert_source(conn, "s", "http://s", "generic_listing")
    jid = _seed_job(conn, sid, "http://s/1", "Kubernetes Platform Lead")
    assert [r["id"] for r in q.search_jobs(conn, "kube")] == [jid]


def test_search_jobs_multi_term_is_and(conn):
    sid = q.insert_source(conn, "s", "http://s", "generic_listing")
    _seed_job(conn, sid, "http://s/1", "Senior Java Developer")
    want = _seed_job(conn, sid, "http://s/2", "Senior Python Developer")
    assert [r["id"] for r in q.search_jobs(conn, "senior python")] == [want]


def test_search_jobs_narrows_by_source_and_org(conn):
    s1 = q.insert_source(conn, "s1", "http://s1", "generic_listing")
    s2 = q.insert_source(conn, "s2", "http://s2", "generic_listing")
    a = _seed_job(conn, s1, "http://s1/1", "Rust Engineer", company="Acme")
    b = _seed_job(conn, s2, "http://s2/1", "Rust Engineer", company="Beta")
    assert [r["id"] for r in q.search_jobs(conn, "rust", source_id=s1)] == [a]
    assert [r["id"] for r in q.search_jobs(conn, "rust", org="Beta")] == [b]


def test_search_jobs_narrows_by_scenario_gate(conn):
    sid = q.insert_source(conn, "s", "http://s", "generic_listing")
    passed = _seed_job(conn, sid, "http://s/1", "Rust Engineer")
    _seed_job(conn, sid, "http://s/2", "Rust Developer")
    scn = q.insert_scenario(conn, "Backend", "")
    q.upsert_job_score(conn, passed, scn, 0.95, "m", "h")
    assert [r["id"] for r in q.search_jobs(conn, "rust", scenario_id=scn)] == [passed]


def test_search_jobs_spans_statuses(conn):
    sid = q.insert_source(conn, "s", "http://s", "generic_listing")
    a = _seed_job(conn, sid, "http://s/1", "Rust Engineer")
    b = _seed_job(conn, sid, "http://s/2", "Rust Developer")
    q.update_job_feedback(conn, b, "rejected", "no")
    ids = {r["id"] for r in q.search_jobs(conn, "rust")}
    assert ids == {a, b}


def test_search_jobs_empty_query_returns_empty(conn):
    sid = q.insert_source(conn, "s", "http://s", "generic_listing")
    _seed_job(conn, sid, "http://s/1", "Rust Engineer")
    assert q.search_jobs(conn, "   ") == []
    assert q.search_jobs(conn, "!!! ??") == []
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `python -m pytest tests/test_queries.py -k search_jobs -q`
Expected: FAIL — `module 'app.db.queries' has no attribute 'search_jobs'`.

- [ ] **Step 3: Implement the helpers and `search_jobs`**

In `app/db/queries.py`, add near the top of the file (after imports):

```python
import re

_FTS_TOKEN_RE = re.compile(r"[0-9A-Za-z]+")
```

Add these functions next to `get_jobs` (after `_ORDER_BY`, before `get_jobs`):

```python
def _fts_match_query(raw: str) -> str | None:
    """User text -> FTS5 MATCH string. Tokens AND-ed, last token is a prefix."""
    tokens = _FTS_TOKEN_RE.findall(raw or "")
    if not tokens:
        return None
    quoted = [f'"{t}"' for t in tokens]
    quoted[-1] = quoted[-1] + "*"
    return " ".join(quoted)


def _composable_clauses(
    *,
    source_id: int | None = None,
    scenario_id: int | None = None,
    org: str | None = None,
    org_none: bool = False,
) -> tuple[list[str], list]:
    clauses, params = [], []
    if source_id is not None:
        clauses.append("jobs.source_id = ?")
        params.append(source_id)
    if org_none:
        clauses.append("jobs.company = ''")
    elif org is not None:
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
    return clauses, params


# bm25 column order matches the jobs_fts definition:
# title, company, headline, summary, simplified_content. Lower bm25 = better.
_SEARCH_RANK = "bm25(jobs_fts, 10.0, 10.0, 3.0, 3.0, 1.0)"


def search_jobs(
    conn: sqlite3.Connection,
    query: str,
    *,
    source_id: int | None = None,
    scenario_id: int | None = None,
    org: str | None = None,
    org_none: bool = False,
) -> list[dict]:
    match = _fts_match_query(query)
    if match is None:
        return []
    clauses, params = _composable_clauses(
        source_id=source_id, scenario_id=scenario_id, org=org, org_none=org_none
    )
    sql = (
        f"SELECT {_GATE_SELECT}, {_SEARCH_RANK} AS search_rank {_GATE_JOIN} "
        "JOIN jobs_fts ON jobs_fts.rowid = jobs.id WHERE jobs_fts MATCH ?"
    )
    sql_params = [match, *params]
    if clauses:
        sql += " AND " + " AND ".join(clauses)
    sql += " ORDER BY search_rank"
    return _rows_to_dicts(conn.execute(sql, sql_params).fetchall())
```

Now DRY up `get_jobs`: replace its inline source/org/scenario clause building with the helper. In `get_jobs`, delete these blocks:

```python
    if source_id is not None:
        clauses.append("jobs.source_id = ?")
        params.append(source_id)
    if org_none:
        clauses.append("jobs.company = ''")
    elif org is not None:
        clauses.append("jobs.company = ?")
        params.append(org)
```

and

```python
    if scenario_id is not None:
        clauses.append(
            """EXISTS (
                SELECT 1 FROM job_scores js JOIN scenarios s ON s.id = js.scenario_id
                WHERE js.job_id = jobs.id AND js.scenario_id = ? AND js.relevance_score >= s.gate_threshold
            )"""
        )
        params.append(scenario_id)
```

Replace them with a single call right after `clauses, params = [], []`:

```python
    clauses, params = [], []
    _c, _p = _composable_clauses(
        source_id=source_id, scenario_id=scenario_id, org=org, org_none=org_none
    )
    clauses += _c
    params += _p
```

Keep the rest of `get_jobs` (`status`, `content_type`, `scenario_gate`, `gate_status`) exactly as-is. Order of clauses does not matter (all AND-ed).

- [ ] **Step 4: Run tests, verify they pass**

Run: `python -m pytest tests/test_queries.py -q`
Expected: all PASS (both the new `search_jobs` tests and the pre-existing `get_jobs` tests — the refactor must not regress them).

- [ ] **Step 5: Commit**

```bash
git add app/db/queries.py tests/test_queries.py
git commit -m "$(cat <<'EOF'
feat: add search_jobs() FTS query with weighted ranking

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_011iDPDqKR6BPVMkKDLxMYc4
EOF
)"
```

---

## Task 3: `JobFilter.q`

**Files:**
- Modify: `app/job_filter.py`
- Test: `tests/test_job_filter.py`

**Interfaces:**
- Produces: `JobFilter.q: str` (default `""`), `JobFilter.searching -> bool` property, `q` key in `query_params()` when non-empty.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_job_filter.py` (import is `from app.job_filter import JobFilter`):

```python
def test_filter_parses_and_strips_query():
    f = JobFilter.from_params({"q": "  senior python  "})
    assert f.q == "senior python"
    assert f.searching is True


def test_filter_blank_query_is_not_searching():
    f = JobFilter.from_params({"status": "new"})
    assert f.q == ""
    assert f.searching is False
    assert "q" not in f.query_params()


def test_filter_query_params_roundtrip_keeps_status_and_q():
    f = JobFilter.from_params({"status": "accepted", "q": "acme"})
    assert f.query_params() == {"status": "accepted", "q": "acme"}
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `python -m pytest tests/test_job_filter.py -k query -q`
Expected: FAIL — `JobFilter` has no attribute `q` / `searching`.

- [ ] **Step 3: Implement**

In `app/job_filter.py`:

Add the field to the dataclass (after `order: str = "change"`):

```python
    q: str = ""
```

In `from_params`, after the `order` handling and before `return cls(...)`:

```python
        query = (get("q") or "").strip()
```

Update the return to pass it (it is the last positional field):

```python
        return cls(tab, scenario_id, scenario_none, source_id, org, org_none, order, query)
```

Add the property (next to `is_narrowed`):

```python
    @property
    def searching(self) -> bool:
        return bool(self.q)
```

In `query_params()`, before `return out`:

```python
        if self.q:
            out["q"] = self.q
```

Note: `cleared()` and `for_status()` use `replace(self, ...)` / explicit construction — `cleared()` builds a fresh `JobFilter(status_tab=..., order=...)` which correctly drops `q`; leave it. `for_status()` uses `replace()` so it keeps `q` — that is fine (switching tabs is not reachable from the search UI, and keeping `q` is harmless).

- [ ] **Step 4: Run tests, verify they pass**

Run: `python -m pytest tests/test_job_filter.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/job_filter.py tests/test_job_filter.py
git commit -m "$(cat <<'EOF'
feat: add q search field to JobFilter

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_011iDPDqKR6BPVMkKDLxMYc4
EOF
)"
```

---

## Task 4: Route + templates — search box, global results, hidden tabs/sort

**Files:**
- Modify: `app/routes/jobs.py` (`_jobs_for_filter`, `job_feedback`)
- Modify: `app/templates/jobs/list.html` (search box, outside `#jobs-content`)
- Modify: `app/templates/jobs/_content.html`
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: `q.search_jobs(...)` (Task 2), `JobFilter.searching` / `.q` (Task 3).
- Produces: `/jobs?q=<text>` renders `#jobs-content` with global relevance-ranked results, tabs + Sort + bulk affordances hidden, scenario/source/org filters live.

**Placement decision:** the search `<input>` lives in `list.html` **outside** `#jobs-content`, on its own row between the Add panel and the results container. If it were inside `#jobs-content` it would be part of the fragment it swaps on every keystroke, and htmx would drop focus/caret mid-typing. Being outside, it is never re-rendered. It reads the current status tab from a hidden `<input name="status">` that `_content.html` renders inside the fragment (so it stays current as tabs change).

- [ ] **Step 1: Write the failing route tests**

Add to `tests/test_routes_jobs.py` (imports already include `from app.db import queries as q`). Reuse the module's `_seed` where useful; add a local helper:

```python
def _seed_searchable(conn, sid, url, title, *, company="", status="new"):
    jid = q.insert_job(conn, source_id=sid, url=url, title=title, company=company, raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="c", content_type="job_posting",
                          title=title, summary="")
    q.mark_job_evaluation_complete(conn, jid)
    if status != "new":
        q.update_job_feedback(conn, jid, status, "note")
    return jid


def test_search_returns_matches_across_statuses(client, conn):
    sid = q.insert_source(conn, "s", "https://s", "generic_listing")
    _seed_searchable(conn, sid, "http://s/1", "Rust Engineer", company="Acme")
    _seed_searchable(conn, sid, "http://s/2", "Rust Developer", company="Beta", status="rejected")
    html = client.get("/jobs?q=rust").text
    assert "Rust Engineer" in html
    assert "Rust Developer" in html
    assert 'class="filter-links"' not in html          # status tabs hidden
    assert 'name="order"' not in html                   # sort control hidden
    assert "2 results" in html


def test_search_blank_query_is_normal_tabbed_view(client, conn):
    _seed(conn)
    html = client.get("/jobs?q=").text
    assert 'class="filter-links"' in html
    assert 'name="order"' in html


def test_search_respects_source_filter(client, conn):
    s1 = q.insert_source(conn, "s1", "https://s1", "generic_listing")
    s2 = q.insert_source(conn, "s2", "https://s2", "generic_listing")
    _seed_searchable(conn, s1, "http://s1/1", "Rust Engineer")
    _seed_searchable(conn, s2, "http://s2/1", "Rust Analyst")
    html = client.get(f"/jobs?q=rust&source_id={s1}").text
    assert "Rust Engineer" in html
    assert "Rust Analyst" not in html


def test_search_box_present_in_normal_view(client, conn):
    _seed(conn)
    html = client.get("/jobs").text
    assert 'name="q"' in html
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `python -m pytest tests/test_routes_jobs.py -k search -q`
Expected: FAIL (no search box; tabs always rendered).

- [ ] **Step 3: Route — branch `_jobs_for_filter` to search**

In `app/routes/jobs.py`, change `_jobs_for_filter`:

```python
def _jobs_for_filter(conn: sqlite3.Connection, f: JobFilter) -> list[dict]:
    if f.searching:
        return q.search_jobs(
            conn, f.q,
            source_id=f.source_id,
            scenario_id=f.scenario_id,
            org=f.org,
            org_none=f.org_none,
        )
    kwargs = dict(_BASE_KWARGS_FOR_TAB[f.status_tab])
    if f.source_id is not None:
        kwargs["source_id"] = f.source_id
    if f.org_none:
        kwargs["org_none"] = True
    elif f.org is not None:
        kwargs["org"] = f.org
    if f.scenario_id is not None:
        kwargs["scenario_id"] = f.scenario_id
    if f.scenario_none:
        kwargs["scenario_gate"] = "none"
    return q.get_jobs(conn, order=f.order, **kwargs)
```

This one change makes `_content_context`, `_stale_badge`, and the feedback re-render all use search results when `f.searching` (they all funnel through `_jobs_for_filter`). A decided job still matches the query, so it stays in place with no stale badge.

- [ ] **Step 4: Route — skip the counts OOB swap while searching**

In `job_feedback` (in `app/routes/jobs.py`), the tail currently always appends `counts_html`. Change it so the counts partial is only built and appended when not searching:

```python
    f = _filter_from_request(request)
    detail = _is_detail_page_request(request)
    row_html = _render_updated_job_html(conn, request, job_id, f, detail=detail)
    counts_html = ""
    if not f.searching:
        counts = _counts_for_filter(conn, f)
        counts_html = templates.get_template("jobs/_counts_oob.html").render(
            request=request, counts=counts
        )
    headers = {}
```

(Leave the `if not detail:` stale-badge block below it unchanged.)

- [ ] **Step 5a: Template — `list.html` (the search box)**

Edit `app/templates/jobs/list.html`. Add the search row between the `{% endcall %}` of the Add panel and `<div id="jobs-add-result"…>`:

```html
{% endcall %}
<form class="jobs-search" role="search" onsubmit="return false">
  <input type="search" name="q" value="{{ filter.q }}" id="job-search-input"
         placeholder="Search jobs — title, organization, description…" aria-label="Search jobs"
         hx-get="/jobs" hx-target="#jobs-content" hx-swap="innerHTML" hx-push-url="true"
         hx-trigger="keyup changed delay:300ms, search"
         hx-include="#jobs-status-marker, [name='scenario'], [name='source_id'], [name='org']">
</form>
<div id="jobs-add-result" data-add-result></div>
```

`filter` and `status` are already in the template context (`_content_context`). On a full page load `filter.q` repopulates the box. `hx-include` pulls the current tab from the hidden `#jobs-status-marker` input added in Step 5b and the three filter selects (all inside `#jobs-content`, all kept current by fragment swaps). The marker is included **by id**, not `[name='status']`, so an expanded row's feedback form (which also has a `status` field) is never swept into the search request.

- [ ] **Step 5b: Template — `_content.html`**

Edit `app/templates/jobs/_content.html`:

**(a0)** At the very top of the file (before the existing hidden bulk-form inputs), add a hidden input carrying the current tab for the outside-the-fragment search box to include:

```html
<input type="hidden" id="jobs-status-marker" name="status" value="{{ filter.status_tab }}">
```

**(a)** Wrap the tab links and add the results summary. Replace the `<div class="filter-bar">` block's contents:

```html
<div class="filter-bar">
  {% if filter.searching %}
  <div class="search-summary">
    {{ jobs | length }} result{{ '' if jobs | length == 1 else 's' }}
    <a class="filter-clear" href="/jobs?status={{ status }}"
       hx-get="/jobs?status={{ status }}" hx-target="#jobs-content" hx-swap="innerHTML" hx-push-url="true">Clear search</a>
  </div>
  {% else %}
  <div class="filter-links">
    {% for tab, label, count_id, count, tip in tab_defs %}
    {% set target = filter.for_status(tab).query_params() | urlencode %}
    <a href="/jobs?{{ target }}"
       hx-get="/jobs?{{ target }}" hx-target="#jobs-content" hx-swap="innerHTML" hx-push-url="true"
       {% if status == tab %}class="active"{% endif %}
       title="{{ tip | safe }}">{{ label }} (<span id="{{ count_id }}">{{ count }}</span>)</a>
    {% endfor %}
  </div>
  {% endif %}
  {% if jobs and not filter.searching %}
    <label class="select-all">
      <input type="checkbox" class="select-all-checkbox" aria-label="Select all">
      Select all
    </label>
  {% endif %}
</div>
```

**(b)** The `<div class="filter-row">` keeps the Scenario / Source / Organization selects — no search input here (it lives in `list.html`, Step 5a).

**(c)** Add `,[name='q']` to the `hx-include` of the Scenario, Source, and Organization `<select>`s, so changing a filter while a search is active keeps the query. Example for Scenario:

```html
    <select name="scenario" hx-get="/jobs" hx-target="#jobs-content" hx-swap="innerHTML"
            hx-push-url="true" hx-vals='{"status": "{{ status }}"}'
            hx-include="[name='source_id'],[name='org'],[name='order'],[name='q']">
```

Do the same for `name="source_id"` and `name="org"`.

**(d)** Wrap the Sort `<label>` so it disappears while searching:

```html
  {% if not filter.searching %}
  <label>Sort
    <select name="order" hx-get="/jobs" hx-target="#jobs-content" hx-swap="innerHTML"
            hx-push-url="true" hx-vals='{"status": "{{ status }}"}'
            hx-include="[name='scenario'],[name='source_id'],[name='org'],[name='q']">
      <option value="change" {% if filter.order == 'change' %}selected{% endif %}>Newest changes</option>
      <option value="score" {% if filter.order == 'score' %}selected{% endif %}>Fit score</option>
      <option value="age" {% if filter.order == 'age' %}selected{% endif %}>Posting age</option>
    </select>
  </label>
  {% endif %}
```

**(e)** Hide the bulk decision panel while searching — wrap the whole `<div class="bulk-bar decision-panel">…</div>` block:

```html
{% if not filter.searching %}
<div class="bulk-bar decision-panel">
  ... unchanged ...
</div>
{% endif %}
```

**(f)** The "no results" line — update the `{% elif not stale_jobs %}` message branch to cover search:

```html
{% elif not stale_jobs %}
  <p>{% if filter.searching %}No jobs match “{{ filter.q }}”.{% elif filter.is_narrowed %}No jobs match these filters.{% else %}No jobs found.{% endif %}</p>
{% endif %}
```

- [ ] **Step 6: Run tests, verify they pass**

Run: `python -m pytest tests/test_routes_jobs.py -q`
Expected: all PASS (new search tests + all pre-existing jobs-route tests).

- [ ] **Step 7: Commit**

```bash
git add app/routes/jobs.py app/templates/jobs/list.html app/templates/jobs/_content.html tests/test_routes_jobs.py
git commit -m "$(cat <<'EOF'
feat: wire jobs-page search box to global FTS results

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_011iDPDqKR6BPVMkKDLxMYc4
EOF
)"
```

---

## Task 5: Per-row status pill in search results

**Files:**
- Modify: `app/templates/jobs/_macros.html`
- Modify: `app/templates/jobs/_row.html`
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: `filter.searching` (Task 3), job dict fields `status`, `content_type`, `passed_gate_count`, `gate_override`.
- Produces: `search_status_badge(job)` macro in `jobs/_macros.html`; rendered on each row and bulk checkbox hidden when `filter.searching`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_routes_jobs.py` (reuse `_seed_searchable` from Task 4):

```python
def test_search_rows_show_status_pill(client, conn):
    sid = q.insert_source(conn, "s", "https://s", "generic_listing")
    new_job = q.insert_job(conn, source_id=sid, url="http://s/1", title="Go Engineer",
                           company="Acme", raw_text="r")
    q.update_job_pipeline(conn, new_job, simplified_content="c", content_type="job_posting",
                          title="Go Engineer", summary="")
    scn = q.insert_scenario(conn, "Backend", "")
    q.upsert_job_score(conn, new_job, scn, 0.95, "m", "h")
    q.mark_job_evaluation_complete(conn, new_job)

    _seed_searchable(conn, sid, "http://s/2", "Go Developer", company="Beta", status="accepted")

    html = client.get("/jobs?q=go").text
    assert ">New</span>" in html
    assert ">Accepted</span>" in html


def test_search_rows_have_no_bulk_checkbox(client, conn):
    sid = q.insert_source(conn, "s", "https://s", "generic_listing")
    _seed_searchable(conn, sid, "http://s/1", "Go Engineer")
    html = client.get("/jobs?q=go").text
    assert 'name="job_ids"' not in html
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `python -m pytest tests/test_routes_jobs.py -k "status_pill or bulk_checkbox" -q`
Expected: FAIL.

- [ ] **Step 3: Add the macro**

In `app/templates/jobs/_macros.html`, add after the existing `status_badge` macro:

```jinja
{% macro search_status_badge(job) %}
{% if job.status == "accepted" %}<span class="score-badge score-high">Accepted</span>
{% elif job.status == "rejected" %}<span class="score-badge score-low">Rejected</span>
{% elif job.status == "trash" %}<span class="score-badge score-neutral">Trash</span>
{% elif job.content_type == "lead" %}<span class="score-badge score-neutral">Lead</span>
{% elif job.content_type == "job_posting" and (job.passed_gate_count or job.gate_override) %}<span class="score-badge score-high">New</span>
{% elif job.content_type == "job_posting" %}<span class="score-badge score-neutral">Not relevant</span>
{% else %}<span class="score-badge score-neutral">{{ job.content_type or "unknown" }}</span>
{% endif %}
{% endmacro %}
```

- [ ] **Step 4: Render it on the row + hide the checkbox**

In `app/templates/jobs/_row.html`:

Change the checkbox guard from:

```jinja
    {% if not is_detail_page %}
    <label class="job-select-wrap">
```

to:

```jinja
    {% if not is_detail_page and not (filter and filter.searching) %}
    <label class="job-select-wrap">
```

In the `<div class="job-row-header">`, add the badge after the company span:

```jinja
    <div class="job-row-header">
      <h3 class="job-title">{{ job.title or "(no title)" }}</h3>
      {% if job.company %}<span class="job-company">{{ job.company }}</span>{% endif %}
      {% if filter and filter.searching %}{{ macros.search_status_badge(job) }}{% endif %}
      {% if job.published_at %}<span class="job-age">{{ job.published_at | time_ago }}</span>{% endif %}
    </div>
```

- [ ] **Step 5: Run tests, verify they pass**

Run: `python -m pytest tests/test_routes_jobs.py -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add app/templates/jobs/_macros.html app/templates/jobs/_row.html tests/test_routes_jobs.py
git commit -m "$(cat <<'EOF'
feat: show per-row status pill in jobs search results

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_011iDPDqKR6BPVMkKDLxMYc4
EOF
)"
```

---

## Task 6: Full verification + manual smoke

**Files:** none (verification only)

- [ ] **Step 1: Full test suite**

Run: `python -m pytest -q`
Expected: all PASS, no warnings about FTS.

- [ ] **Step 2: Lint check if the project has one**

Check `pyproject.toml` for a `[tool.ruff]` section. If present, run `python -m ruff check app tests` and fix anything new. If absent, skip.

- [ ] **Step 3: Manual smoke via dev server**

Use the `run-dev-server` skill (throwaway DB copy). Then:
- Load `/jobs`, confirm the Search box appears in the filter row.
- Type a term present in a job title — results should appear within ~300ms, tabs and Sort disappear, each row shows a status pill, and the "N results / Clear search" line shows.
- Add a Source or Organization filter — results narrow, query stays in the box.
- Click "Clear search" — returns to the normal tabbed view on the same status tab.
- Expand a result row and Accept/Reject it — the row updates in place with the new pill, still listed.

- [ ] **Step 4: Hand off to the user**

Leave the dev server running and give the user the URL for their own check (project `CLAUDE.md` → *Manual testing*). Do not merge until they approve.

---

## Self-Review

**Spec coverage:**
- Global search across statuses → Task 2 (`search_jobs`, no status filter), Task 4 (`_jobs_for_filter` branch), Task 4 test `test_search_returns_matches_across_statuses`. ✓
- Role/org rank above description → Task 2 `_SEARCH_RANK` weights + `test_search_jobs_ranks_title_above_body`. ✓
- Composable scenario/source/org → Task 2 `_composable_clauses` + tests; Task 4 template `hx-include` wiring + `test_search_respects_source_filter`. ✓
- Tabs + Sort hidden while searching → Task 4 template (a)(d) + `test_search_returns_matches_across_statuses`. ✓
- "Clear search" returns to prior tab → Task 3 (`status` kept in `query_params`), Task 4 template (a). ✓
- Search-as-you-type, debounced, multi-word AND, last-word prefix → Task 2 `_fts_match_query` + tests; Task 4 `hx-trigger="keyup changed delay:300ms, search"`. ✓
- FTS5 external-content table + triggers + create-once migration last in `init_db` → Task 1. ✓
- CLAUDE.md rebuild note → Task 1 Step 5. ✓
- Per-row status pill → Task 5. ✓
- Tests for schema / queries / job_filter / routes → Tasks 1–5. ✓

**Placeholder scan:** No TBD/TODO; every code step shows full code; commands have expected output. Task 6 Step 2 is conditional ("if ruff is configured") — acceptable, it is an explicit check-then-act, not a vague instruction.

**Type consistency:** `search_jobs` / `_fts_match_query` / `_composable_clauses` / `_SEARCH_RANK` names match between Task 2's definition and Task 4's usage. `JobFilter.q` / `.searching` match between Task 3 and Task 4/5. `search_status_badge` macro name matches between Task 5 Step 3 and Step 4. `_seed_searchable` defined in Task 4 Step 1, reused in Task 5 Step 1 (same file). ✓
