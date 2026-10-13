# Manual Tags & Organization Override Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user set a manual organization per job and attach free, reusable tags, and filter the job list by any combination of must-have / must-not-have tags.

**Architecture:** One additive migration (`jobs.company_override`, `tags`, `job_tags`). The shared job SELECT (`_GATE_SELECT` in `app/db/queries.py`) exposes an effective `org` column and a `tags_json` column, so every job-rendering path gets both for free. `JobFilter` gains `tags` / `not_tags`; the SQL filter helpers gain matching `EXISTS` / `NOT EXISTS` clauses. Editing happens inline in the shared `meta_tags` Jinja macro via a new `app/routes/tags.py` router; suggestions come from two page-level `<datalist>`s loaded by htmx.

**Tech Stack:** FastAPI, SQLite (JSON1 built in, sqlite 3.53), Jinja2, htmx 1.9, pytest via `uv run pytest`.

Spec: `docs/superpowers/specs/2026-10-13-manual-tags-design.md`

## Global Constraints

- Run tests with `uv run pytest` (needs `dangerouslyDisableSandbox: true` for the uv cache lock). Never fall back to system `python -m pytest`.
- Use `/usr/bin/git`, not `git` (an rtk hook rewrites `git`). Stage explicit paths — never `git add -A` / `git add .`.
- Commit after each task. End commit messages with:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_011v4Cgv3vbWAL4Nm12JWB9L
  ```
- Work only inside `/home/roman/projects/job-seek/.claude/worktrees/manual-tags` (branch `worktree-manual-tags`). Verify with `/usr/bin/git branch --show-current` before the first commit.
- Migration is additive; do **not** rebuild `jobs` (keeps `jobs_fts` triggers intact).
- A tag is a single free-text string; no key/value logic.
- Any typed org or tag value is accepted (datalists are suggestions only).
- Tag name equality is case-insensitive everywhere (`COLLATE NOCASE` in SQL, `str.casefold()` in Python).
- httpx `TestClient` repeated form fields: `data={"field": ["a", "b"]}`, not a list of tuples.
- Tasks 7 and 8 are UI work: use the `frontend-design` skill for spacing, button hierarchy and state styling. CSS lives inline in `app/templates/base.html` next to the existing `.tag*` rules (~line 271) and must use existing CSS variables (`--border`, `--accent`, `--accent-tint`, `--success-tint`, `--success-strong`, `--text-secondary`, …) so dark mode keeps working.

---

## File Structure

- Create `app/tags.py` — `normalize_tag_name()` (pure, no DB).
- Modify `app/db/schema.py` — migration `_migrate_add_manual_tags`, registered last in `init_db`.
- Modify `app/db/queries.py` — effective org (`_EFFECTIVE_ORG`), `org` + `tags_json` in `_GATE_SELECT`, tag CRUD, tag/org filter clauses, `get_distinct_companies`, `list_tailored_cvs`.
- Modify `app/job_filter.py` — `tags` / `not_tags`, parsing, serialisation, helpers.
- Modify `app/template_env.py` — `job_tags` filter; `urlencode` filter that handles list values.
- Create `app/routes/tags.py` — edit routes + datalist option routes. Register in `app/main.py`.
- Modify `app/routes/jobs.py` — thread tags through list/search/counts/bulk; `/jobs/tag-filter` route.
- Create `app/templates/jobs/_meta_tags_fragment.html`, `app/templates/tags/_options.html`.
- Modify `app/templates/jobs/_macros.html`, `_content.html`, `list.html`, `_job_header.html`, `_feedback.html`, `app/templates/base.html`, `app/routes/cv.py`.
- Tests: create `tests/test_tags.py`, `tests/test_routes_tags.py`; extend `tests/test_schema.py`, `tests/test_job_filter.py`, `tests/test_queries.py`, `tests/test_routes_jobs.py`.

---

### Task 1: Schema migration + organization override queries

**Files:**
- Create: `app/tags.py`
- Modify: `app/db/schema.py` (new migration after `_migrate_jobs_add_apply_by`, call appended at end of `init_db`)
- Modify: `app/db/queries.py` (`_GATE_SELECT` ~line 1308, `_composable_clauses` ~1396, `_count_filter_sql` ~1597, `get_distinct_companies` ~1622, `list_tailored_cvs` ~408)
- Test: `tests/test_tags.py`, `tests/test_schema.py`

**Interfaces:**
- Produces: `app.tags.normalize_tag_name(raw: str | None) -> str`; `q.set_company_override(conn, job_id: int, raw: str | None) -> bool` (True if changed); job dicts from any `_GATE_SELECT` query carry `org: str` (effective) and `company_override: str | None`; `q._EFFECTIVE_ORG` SQL expression string.

- [ ] **Step 1: Write failing tests** in `tests/test_tags.py`:

```python
from app.db import queries as q
from app.tags import normalize_tag_name


def _job(conn, company="Acme"):
    sid = q.insert_source(conn, "s", "https://s.example", "generic_listing")
    return q.insert_job(conn, source_id=sid, url="https://s.example/1", title="T", company=company, raw_text="r")


def test_normalize_tag_name_trims_and_collapses():
    assert normalize_tag_name("  Language:   English \n") == "Language: English"
    assert normalize_tag_name(None) == ""
    assert normalize_tag_name("   ") == ""


def test_org_defaults_to_llm_company(conn):
    jid = _job(conn)
    job = q.get_job(conn, jid)
    assert job["org"] == "Acme"
    assert job["company_override"] is None


def test_override_wins_and_clears(conn):
    jid = _job(conn)
    assert q.set_company_override(conn, jid, "  Acme   Robotics ") is True
    assert q.get_job(conn, jid)["org"] == "Acme Robotics"
    assert q.set_company_override(conn, jid, "") is True
    job = q.get_job(conn, jid)
    assert job["org"] == "Acme" and job["company_override"] is None


def test_override_fills_missing_company(conn):
    jid = _job(conn, company="")
    q.set_company_override(conn, jid, "Brand New Org")
    assert q.get_job(conn, jid)["org"] == "Brand New Org"


def test_override_equal_to_llm_value_is_not_stored(conn):
    jid = _job(conn)
    assert q.set_company_override(conn, jid, "Acme") is False
    assert q.get_job(conn, jid)["company_override"] is None


def test_override_unchanged_returns_false_and_logs_once(conn):
    jid = _job(conn)
    q.set_company_override(conn, jid, "Beta")
    assert q.set_company_override(conn, jid, "Beta") is False
    msgs = [e["message"] for e in q.get_job_events(conn, jid) if e["kind"] == "org_override"]
    assert msgs == ["Organization set to “Beta”"]


def test_clear_override_logs_event(conn):
    jid = _job(conn)
    q.set_company_override(conn, jid, "Beta")
    q.set_company_override(conn, jid, "")
    msgs = [e["message"] for e in q.get_job_events(conn, jid) if e["kind"] == "org_override"]
    assert msgs[0] == "Organization override cleared"


def test_override_survives_pipeline_update(conn):
    jid = _job(conn, company="")
    q.set_company_override(conn, jid, "Mine")
    q.update_job_pipeline(conn, jid, simplified_content="c", content_type="job_posting", company="LLM Co")
    job = q.get_job(conn, jid)
    assert job["company"] == "LLM Co" and job["org"] == "Mine"


def test_org_filter_and_distinct_use_effective_org(conn):
    jid = _job(conn, company="")
    assert [j["id"] for j in q.get_jobs(conn, org_none=True)] == [jid]
    q.set_company_override(conn, jid, "Zed")
    assert q.get_jobs(conn, org_none=True) == []
    assert [j["id"] for j in q.get_jobs(conn, org="Zed")] == [jid]
    assert q.get_distinct_companies(conn) == ["Zed"]
    q.update_job_pipeline(conn, jid, simplified_content="c", content_type="lead")
    assert q.get_job_counts(conn, org="Zed")["lead"] == 1
    assert q.get_job_counts(conn, org_none=True)["lead"] == 0
```

In `tests/test_schema.py` add:

```python
def test_manual_tags_migration_is_idempotent(conn):
    from app.db.schema import init_db
    init_db(conn)  # second run must be a no-op
    cols = [r[1] for r in conn.execute("PRAGMA table_info(jobs)")]
    assert "company_override" in cols
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"tags", "job_tags"} <= tables
```

(Use the existing `conn` fixture from `tests/conftest.py`.)

- [ ] **Step 2: Run** `uv run pytest tests/test_tags.py tests/test_schema.py -q` — expect failures (`ModuleNotFoundError: app.tags`, missing column).

- [ ] **Step 3: Implement.**

`app/tags.py`:

```python
from __future__ import annotations
import re

_WS_RE = re.compile(r"\s+")


def normalize_tag_name(raw: str | None) -> str:
    """Trim and collapse inner whitespace. Used for tag names and manual org names."""
    return _WS_RE.sub(" ", (raw or "").strip())
```

`app/db/schema.py`, after `_migrate_jobs_add_apply_by`:

```python
def _migrate_add_manual_tags(conn: sqlite3.Connection) -> None:
    # Additive: manual org override column + free tag tables. Runs last so
    # earlier jobs-rebuild migrations can't drop the column.
    cols = [r[1] for r in conn.execute("PRAGMA table_info(jobs)")]
    if cols and "company_override" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN company_override TEXT")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE COLLATE NOCASE
        );
        CREATE TABLE IF NOT EXISTS job_tags (
            job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
            tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
            PRIMARY KEY (job_id, tag_id)
        );
        CREATE INDEX IF NOT EXISTS idx_job_tags_tag ON job_tags(tag_id);
        """
    )
    conn.commit()
```

Append `_migrate_add_manual_tags(conn)` as the last line of `init_db`.

`app/db/queries.py`:

1. Above `_GATE_SELECT`, add:

```python
# Manual override wins over the LLM-extracted company (spec: manual tags).
_EFFECTIVE_ORG = "COALESCE(NULLIF(jobs.company_override, ''), jobs.company)"
```

2. In `_GATE_SELECT`, after `jobs.*,` add a line `{_EFFECTIVE_ORG} AS org,` — `_GATE_SELECT` is a plain string, so turn it into an f-string: `_GATE_SELECT = f"""` and keep everything else verbatim.

3. In both `_composable_clauses` and `_count_filter_sql` replace

```python
        clauses.append("jobs.company = ''")
    elif org is not None:
        clauses.append("jobs.company = ?")
```

with

```python
        clauses.append(f"{_EFFECTIVE_ORG} = ''")
    elif org is not None:
        clauses.append(f"{_EFFECTIVE_ORG} = ?")
```

4. `get_distinct_companies`:

```python
def get_distinct_companies(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        f"SELECT DISTINCT {_EFFECTIVE_ORG} AS org FROM jobs "
        "WHERE org != '' ORDER BY org COLLATE NOCASE"
    ).fetchall()
    return [r[0] for r in rows]
```

5. In `list_tailored_cvs` replace `jobs.company,` with `{_EFFECTIVE_ORG} AS company,` and make that SQL an f-string (no other `{` in it).

6. Next to `add_job_event`, add:

```python
def set_company_override(conn: sqlite3.Connection, job_id: int, raw: str | None) -> bool:
    """Set (or, with an empty value, clear) the manual organization. A value
    equal to the LLM-extracted company is stored as no override. Returns
    whether anything changed; logs an 'org_override' job event when it did."""
    from app.tags import normalize_tag_name
    row = conn.execute("SELECT company, company_override FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        return False
    name = normalize_tag_name(raw)
    new = name if name and name != row["company"] else None
    if new == row["company_override"]:
        return False
    conn.execute("UPDATE jobs SET company_override = ? WHERE id = ?", (new, job_id))
    conn.commit()
    message = f"Organization set to “{new}”" if new else "Organization override cleared"
    add_job_event(conn, job_id, "org_override", message)
    return True
```

(Move the `normalize_tag_name` import to the top of `queries.py` if there is no import cycle — `app/tags.py` imports nothing from `app`, so a top-level import is fine; prefer it.)

Note: `test_override_equal_to_llm_value_is_not_stored` → `new` is None and existing override is None → returns False. `test_clear_override_logs_event` → events are ordered newest first by `get_job_events`, so `msgs[0]` is the clear.

- [ ] **Step 4: Run** `uv run pytest tests/test_tags.py tests/test_schema.py tests/test_queries.py -q` — expect PASS.
- [ ] **Step 5: Commit** `feat(tags): schema + manual organization override` (paths: `app/tags.py app/db/schema.py app/db/queries.py tests/test_tags.py tests/test_schema.py`).

---

### Task 2: Tag CRUD queries + `tags_json` on job rows

**Files:**
- Modify: `app/db/queries.py`
- Modify: `app/template_env.py`
- Test: `tests/test_tags.py`

**Interfaces:**
- Consumes: `normalize_tag_name`, `add_job_event`.
- Produces:
  - `q.add_job_tag(conn, job_id: int, raw: str | None) -> dict | None` — `{"id": int, "name": str}` of the attached tag, or None if the name is empty.
  - `q.remove_job_tag(conn, job_id: int, tag_id: int) -> str | None` — removed tag's name, or None if it wasn't attached.
  - `q.get_job_tags(conn, job_id: int) -> list[dict]` — `[{"id", "name"}]` ordered by name (NOCASE).
  - `q.get_tag_names_by_usage(conn) -> list[str]` — only tags in use, most used first, then name.
  - Job dicts from `_GATE_SELECT` carry `tags_json: str` (JSON array of `{"id","name"}`).
  - Jinja filter `job_tags(value: str | None) -> list[dict]`.

- [ ] **Step 1: Write failing tests** (append to `tests/test_tags.py`):

```python
import json
from app.template_env import templates


def _job2(conn, sid, n):
    return q.insert_job(conn, source_id=sid, url=f"https://s.example/{n}", title="T", company="", raw_text="r")


def test_add_tag_creates_and_reuses_case_insensitively(conn):
    jid = _job(conn)
    t1 = q.add_job_tag(conn, jid, " Language:  English ")
    assert t1["name"] == "Language: English"
    sid = q.get_job(conn, jid)["source_id"]
    other = _job2(conn, sid, 2)
    t2 = q.add_job_tag(conn, other, "language: english")
    assert t2 == t1  # first spelling stays canonical
    assert conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0] == 1


def test_add_empty_tag_is_rejected(conn):
    jid = _job(conn)
    assert q.add_job_tag(conn, jid, "   ") is None
    assert q.get_job_tags(conn, jid) == []


def test_add_same_tag_twice_is_idempotent_and_logs_once(conn):
    jid = _job(conn)
    q.add_job_tag(conn, jid, "Remote")
    q.add_job_tag(conn, jid, "remote")
    assert [t["name"] for t in q.get_job_tags(conn, jid)] == ["Remote"]
    msgs = [e["message"] for e in q.get_job_events(conn, jid) if e["kind"] == "tag_added"]
    assert msgs == ["Tag added: Remote"]


def test_remove_tag_deletes_orphan_and_logs(conn):
    jid = _job(conn)
    t = q.add_job_tag(conn, jid, "Remote")
    assert q.remove_job_tag(conn, jid, t["id"]) == "Remote"
    assert conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0] == 0
    assert q.remove_job_tag(conn, jid, t["id"]) is None
    msgs = [e["message"] for e in q.get_job_events(conn, jid) if e["kind"] == "tag_removed"]
    assert msgs == ["Tag removed: Remote"]


def test_remove_keeps_tag_used_elsewhere(conn):
    jid = _job(conn)
    sid = q.get_job(conn, jid)["source_id"]
    other = _job2(conn, sid, 2)
    t = q.add_job_tag(conn, jid, "Remote")
    q.add_job_tag(conn, other, "Remote")
    q.remove_job_tag(conn, jid, t["id"])
    assert q.get_tag_names_by_usage(conn) == ["Remote"]


def test_tag_names_by_usage_order(conn):
    jid = _job(conn)
    sid = q.get_job(conn, jid)["source_id"]
    other = _job2(conn, sid, 2)
    q.add_job_tag(conn, jid, "b-rare")
    q.add_job_tag(conn, jid, "z-common")
    q.add_job_tag(conn, other, "z-common")
    q.add_job_tag(conn, other, "A-rare")
    assert q.get_tag_names_by_usage(conn) == ["z-common", "A-rare", "b-rare"]


def test_job_rows_carry_tags_json(conn):
    jid = _job(conn)
    q.add_job_tag(conn, jid, "b")
    q.add_job_tag(conn, jid, "A")
    names = [t["name"] for t in json.loads(q.get_job(conn, jid)["tags_json"])]
    assert names == ["A", "b"]
    assert json.loads(q.get_job(conn, _job2(conn, q.get_job(conn, jid)["source_id"], 3))["tags_json"]) == []


def test_job_tags_template_filter():
    f = templates.env.filters["job_tags"]
    assert f(None) == []
    assert f('[{"id": 1, "name": "x"}]') == [{"id": 1, "name": "x"}]


def test_deleting_job_cascades_job_tags(conn):
    jid = _job(conn)
    q.add_job_tag(conn, jid, "Remote")
    conn.execute("DELETE FROM jobs WHERE id = ?", (jid,))
    assert conn.execute("SELECT COUNT(*) FROM job_tags").fetchone()[0] == 0
    assert q.get_tag_names_by_usage(conn) == []  # orphan tag row is invisible
```

- [ ] **Step 2: Run** `uv run pytest tests/test_tags.py -q` — expect FAIL (`AttributeError: add_job_tag`).

- [ ] **Step 3: Implement** in `app/db/queries.py`.

Add to `_GATE_SELECT` (after the `org` line):

```python
    (SELECT json_group_array(json_object('id', t.id, 'name', t.name))
       FROM (SELECT tags.id, tags.name FROM job_tags
             JOIN tags ON tags.id = job_tags.tag_id
             WHERE job_tags.job_id = jobs.id
             ORDER BY tags.name COLLATE NOCASE) AS t) AS tags_json,
```

Add near `set_company_override`:

```python
def add_job_tag(conn: sqlite3.Connection, job_id: int, raw: str | None) -> dict | None:
    name = normalize_tag_name(raw)
    if not name:
        return None
    # tags.name is UNIQUE COLLATE NOCASE: an existing tag in any casing is reused.
    conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name,))
    tag = conn.execute("SELECT id, name FROM tags WHERE name = ?", (name,)).fetchone()
    cur = conn.execute(
        "INSERT OR IGNORE INTO job_tags (job_id, tag_id) VALUES (?, ?)", (job_id, tag["id"])
    )
    conn.commit()
    if cur.rowcount:
        add_job_event(conn, job_id, "tag_added", f"Tag added: {tag['name']}")
    return {"id": tag["id"], "name": tag["name"]}


def remove_job_tag(conn: sqlite3.Connection, job_id: int, tag_id: int) -> str | None:
    tag = conn.execute("SELECT name FROM tags WHERE id = ?", (tag_id,)).fetchone()
    cur = conn.execute("DELETE FROM job_tags WHERE job_id = ? AND tag_id = ?", (job_id, tag_id))
    if not cur.rowcount:
        conn.commit()
        return None
    conn.execute(
        "DELETE FROM tags WHERE id = ? AND NOT EXISTS (SELECT 1 FROM job_tags WHERE tag_id = ?)",
        (tag_id, tag_id),
    )
    conn.commit()
    add_job_event(conn, job_id, "tag_removed", f"Tag removed: {tag['name']}")
    return tag["name"]


def get_job_tags(conn: sqlite3.Connection, job_id: int) -> list[dict]:
    return _rows_to_dicts(conn.execute(
        "SELECT tags.id, tags.name FROM job_tags JOIN tags ON tags.id = job_tags.tag_id "
        "WHERE job_tags.job_id = ? ORDER BY tags.name COLLATE NOCASE",
        (job_id,),
    ).fetchall())


def get_tag_names_by_usage(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT tags.name FROM tags JOIN job_tags ON job_tags.tag_id = tags.id "
        "GROUP BY tags.id ORDER BY COUNT(*) DESC, tags.name COLLATE NOCASE"
    ).fetchall()
    return [r[0] for r in rows]
```

In `app/template_env.py` (with `import json` at top), after the other filter registrations:

```python
def job_tags(value: str | None) -> list[dict]:
    """Decode a job row's tags_json column (see queries._GATE_SELECT)."""
    return json.loads(value) if value else []


templates.env.filters["job_tags"] = job_tags
```

- [ ] **Step 4: Run** `uv run pytest tests/test_tags.py tests/test_queries.py -q` — PASS.
- [ ] **Step 5: Commit** `feat(tags): tag CRUD queries and tags on job rows`.

---

### Task 3: Tag include/exclude in the job SQL filters

**Files:**
- Modify: `app/db/queries.py` (`_composable_clauses`, `_count_filter_sql`, `search_jobs`, `get_jobs_for_tabs`, `get_jobs`, `get_job_counts`)
- Test: `tests/test_tags.py`

**Interfaces:**
- Produces: keyword args `tags: Sequence[str] = ()` and `not_tags: Sequence[str] = ()` on `search_jobs`, `get_jobs_for_tabs`, `get_jobs`, `get_job_counts`; `_tag_clauses(tags, not_tags) -> tuple[list[str], list]`.

- [ ] **Step 1: Write failing tests** (append to `tests/test_tags.py`):

```python
def _three_tagged_jobs(conn):
    sid = q.insert_source(conn, "s", "https://s.example", "generic_listing")
    ids = []
    for n, tags in enumerate([["Remote", "English"], ["Remote"], ["English", "Agency"]]):
        jid = q.insert_job(conn, source_id=sid, url=f"https://s.example/{n}", title=f"J{n}", company="", raw_text="r")
        q.update_job_pipeline(conn, jid, simplified_content="rust role", content_type="lead", summary="rust")
        for t in tags:
            q.add_job_tag(conn, jid, t)
        ids.append(jid)
    return ids


def test_tag_include_is_and(conn):
    a, b, c = _three_tagged_jobs(conn)
    got = {j["id"] for j in q.get_jobs_for_tabs(conn, ["lead"], tags=["remote", "English"])}
    assert got == {a}


def test_tag_exclude_is_and_not(conn):
    a, b, c = _three_tagged_jobs(conn)
    got = {j["id"] for j in q.get_jobs_for_tabs(conn, ["lead"], not_tags=["agency"])}
    assert got == {a, b}
    got = {j["id"] for j in q.get_jobs_for_tabs(conn, ["lead"], tags=["English"], not_tags=["Agency"])}
    assert got == {a}


def test_tag_filters_apply_to_get_jobs_search_and_counts(conn):
    a, b, c = _three_tagged_jobs(conn)
    assert {j["id"] for j in q.get_jobs(conn, tags=["Remote"])} == {a, b}
    assert {j["id"] for j in q.search_jobs(conn, "rust", tags=["Remote"], not_tags=["English"])} == {b}
    assert q.get_job_counts(conn, tags=["Remote"])["lead"] == 2
    assert q.get_job_counts(conn, not_tags=["Remote"], q="rust")["lead"] == 1
```

- [ ] **Step 2: Run** `uv run pytest tests/test_tags.py -q -k tag_` — FAIL (`unexpected keyword argument 'tags'`).

- [ ] **Step 3: Implement.** Add above `_composable_clauses`:

```python
_TAG_EXISTS = (
    "EXISTS (SELECT 1 FROM job_tags jt JOIN tags t ON t.id = jt.tag_id "
    "WHERE jt.job_id = jobs.id AND t.name = ?)"
)


def _tag_clauses(tags: Sequence[str] = (), not_tags: Sequence[str] = ()) -> tuple[list[str], list]:
    """One EXISTS per must-have tag, one NOT EXISTS per excluded tag.
    tags.name is COLLATE NOCASE, so `=` matches case-insensitively."""
    clauses = [_TAG_EXISTS for _ in tags] + ["NOT " + _TAG_EXISTS for _ in not_tags]
    return clauses, [*tags, *not_tags]
```

(Add `from typing import Sequence` / `from collections.abc import Sequence` at the top if not already imported.)

`_composable_clauses`: add parameters `tags: Sequence[str] = (), not_tags: Sequence[str] = ()`, and before `return`:

```python
    tc, tp = _tag_clauses(tags, not_tags)
    clauses += tc
    params += tp
```

Watch parameter order: the scenario clause appends its `?` param before the tag block, and the tag block is appended after it, so clause order and param order stay aligned.

`_count_filter_sql`: add trailing params `tags: Sequence[str] = (), not_tags: Sequence[str] = ()` and the same three lines before the final `return`.

Thread `tags` / `not_tags` keyword args (default `()`) through `search_jobs`, `get_jobs_for_tabs`, `get_jobs` (each passes them to `_composable_clauses`) and `get_job_counts` (passes to `_count_filter_sql(scenario_id, scenario_none, source_id, org, org_none, tags, not_tags)`).

- [ ] **Step 4: Run** `uv run pytest tests/test_tags.py tests/test_queries.py -q` — PASS.
- [ ] **Step 5: Commit** `feat(tags): include/exclude tag filters in job queries`.

---

### Task 4: `JobFilter` tags / not_tags

**Files:**
- Modify: `app/job_filter.py`
- Modify: `app/template_env.py` (list-aware `urlencode` filter)
- Test: `tests/test_job_filter.py`

**Interfaces:**
- Produces on `JobFilter`: fields `tags: tuple[str, ...] = ()`, `not_tags: tuple[str, ...] = ()` (declared **after** `solo`, so existing positional construction keeps working); `query_params()` now returns `dict[str, str | list[str]]` with `"tag"` / `"not_tag"` lists only when non-empty; `query_string() -> str`; `with_tag(name)`, `with_not_tag(name)`, `with_tag_flipped(name)`, `without_tag(name)`; `is_narrowed` and `cleared()` account for tags. `from_params` accepts Starlette `QueryParams`/`FormData` (has `getlist`) or a plain dict whose `tag`/`not_tag` values are a str or a list.

- [ ] **Step 1: Write failing tests** (append to `tests/test_job_filter.py`):

```python
from starlette.datastructures import QueryParams
from app.job_filter import JobFilter
from app.template_env import templates


def test_tags_parse_from_query_params_multi():
    f = JobFilter.from_params(QueryParams("tag=Remote&tag=Language:+English&not_tag=Agency"))
    assert f.tags == ("Remote", "Language: English")
    assert f.not_tags == ("Agency",)


def test_tags_parse_from_dict_list_or_str_and_dedupe():
    f = JobFilter.from_params({"tag": ["Remote", "remote", "  "], "not_tag": "Agency"})
    assert f.tags == ("Remote",)
    assert f.not_tags == ("Agency",)


def test_tag_in_both_lists_stays_include_only():
    f = JobFilter.from_params({"tag": ["Remote"], "not_tag": ["remote"]})
    assert f.tags == ("Remote",) and f.not_tags == ()


def test_query_params_roundtrip_and_query_string():
    f = JobFilter(tags=("Remote", "A B"), not_tags=("Agency",))
    p = f.query_params()
    assert p["tag"] == ["Remote", "A B"] and p["not_tag"] == ["Agency"]
    assert "tag=Remote&tag=A+B&not_tag=Agency" in f.query_string()
    assert JobFilter.from_params(p) == f
    assert "tag" not in JobFilter().query_params()


def test_jinja_urlencode_handles_lists():
    out = templates.env.from_string("{{ p | urlencode }}").render(p=JobFilter(tags=("A B", "c")).query_params())
    assert "tag=A+B&tag=c" in out


def test_tag_helpers():
    f = JobFilter()
    f = f.with_tag("Remote").with_tag("remote")
    assert f.tags == ("Remote",)
    f = f.with_tag_flipped("REMOTE")
    assert f.tags == () and f.not_tags == ("Remote",)
    f = f.with_tag_flipped("Remote")
    assert f.tags == ("Remote",) and f.not_tags == ()
    f = f.with_not_tag("Agency").without_tag("remote")
    assert f.tags == () and f.not_tags == ("Agency",)


def test_tags_narrow_and_clear():
    f = JobFilter(not_tags=("X",))
    assert f.is_narrowed
    assert f.cleared().not_tags == ()
```

- [ ] **Step 2: Run** `uv run pytest tests/test_job_filter.py -q` — FAIL.

- [ ] **Step 3: Implement** in `app/job_filter.py`:

```python
from app.tags import normalize_tag_name


def _param_list(params, key: str) -> list[str]:
    if hasattr(params, "getlist"):
        return list(params.getlist(key))
    v = params.get(key)
    if isinstance(v, (list, tuple)):
        return list(v)
    return [v] if v else []


def _unique_names(raw: list[str], skip: tuple[str, ...] = ()) -> tuple[str, ...]:
    seen = {s.casefold() for s in skip}
    out: list[str] = []
    for r in raw:
        name = normalize_tag_name(r)
        if name and name.casefold() not in seen:
            seen.add(name.casefold())
            out.append(name)
    return tuple(out)


def _drop(names: tuple[str, ...], name: str) -> tuple[str, ...]:
    return tuple(n for n in names if n.casefold() != name.casefold())
```

Fields on the dataclass, after `solo`:

```python
    # Free manual tags: every name in `tags` must be on the job, none in
    # `not_tags` may be. Compared case-insensitively.
    tags: tuple[str, ...] = ()
    not_tags: tuple[str, ...] = ()
```

In `from_params`, before `return`:

```python
        tags = _unique_names(_param_list(params, "tag"))
        not_tags = _unique_names(_param_list(params, "not_tag"), skip=tags)
```

and return `cls(statuses, scenario_id, scenario_none, source_id, org, org_none, order_pick, query, solo, tags, not_tags)`.

`is_narrowed`: add `or self.tags or self.not_tags` inside the `bool(...)`.

`query_params` — change the annotation to `dict[str, str | list[str]]` and before `return out` add:

```python
        if self.tags:
            out["tag"] = list(self.tags)
        if self.not_tags:
            out["not_tag"] = list(self.not_tags)
```

Add:

```python
    def query_string(self) -> str:
        return urlencode(self.query_params(), doseq=True)
```

and make `query_suffix` return `"?" + self.query_string()` in the non-detail branch.

Tag helpers (next to `with_org`):

```python
    def with_tag(self, name: str) -> "JobFilter":
        name = normalize_tag_name(name)
        if not name or any(t.casefold() == name.casefold() for t in self.tags):
            return self
        return replace(self, tags=(*self.tags, name), not_tags=_drop(self.not_tags, name))

    def with_not_tag(self, name: str) -> "JobFilter":
        name = normalize_tag_name(name)
        if not name or any(t.casefold() == name.casefold() for t in self.not_tags):
            return self
        return replace(self, not_tags=(*self.not_tags, name), tags=_drop(self.tags, name))

    def with_tag_flipped(self, name: str) -> "JobFilter":
        # Flip using the stored spelling, so "REMOTE" flips "Remote" as-is.
        for t in self.tags:
            if t.casefold() == name.casefold():
                return self.with_not_tag(t)
        for t in self.not_tags:
            if t.casefold() == name.casefold():
                return self.with_tag(t)
        return self.with_tag(name)

    def without_tag(self, name: str) -> "JobFilter":
        return replace(self, tags=_drop(self.tags, name), not_tags=_drop(self.not_tags, name))
```

`cleared()` already builds a fresh `JobFilter(statuses=..., order_pick=...)` so tags drop automatically — keep it; update its docstring to "Drop the query and every scenario/source/org/tag narrowing".



In `app/template_env.py`, replace Jinja's `urlencode` so dict values that are lists become repeated params (for plain-string dicts this is byte-identical to Jinja's own output — both use `quote_plus` with `safe=""`):

```python
from collections.abc import Mapping
from urllib.parse import urlencode as _urlencode

_jinja_urlencode = templates.env.filters["urlencode"]


def urlencode_multi(value):
    """Jinja's urlencode, but list values in a mapping become repeated params
    (JobFilter.query_params() carries tag/not_tag lists)."""
    if isinstance(value, Mapping):
        return _urlencode(value, doseq=True)
    return _jinja_urlencode(value)


templates.env.filters["urlencode"] = urlencode_multi
```

Also fix the one dict `.get` consumer that would now break on lists: none — `_content.html` reads `.get('scenario', '')`, a str. Check `app/routes/jobs.py` `_filter_task_params` (stores `query_params()` in task JSON; lists serialise fine and `from_params` reads them back).

- [ ] **Step 4: Run** `uv run pytest tests/test_job_filter.py tests/test_routes_jobs.py -q` — PASS (the routes suite guards the urlencode swap).
- [ ] **Step 5: Commit** `feat(tags): JobFilter tags / not_tags`.

---

### Task 5: Thread tag filters through the jobs routes

**Files:**
- Modify: `app/routes/jobs.py` (`_jobs_for_filter`, `_counts_for_filter`, `_filter_from_bulk_form` + its two callers ~lines 705–790, new `/jobs/tag-filter` route)
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: `JobFilter.tags/not_tags/with_tag/with_not_tag/query_string`; query kwargs from Task 3.
- Produces: `GET /jobs/tag-filter?name=…&mode=include|exclude&<current filter params>` → `jobs/_content.html` with `HX-Push-Url: /jobs?<new filter>`; bulk forms accept repeated `tag_filter` / `not_tag_filter`.

- [ ] **Step 1: Write failing tests** (append to `tests/test_routes_jobs.py`):

```python
def _seed_tagged(conn):
    sid = q.insert_source(conn, "s", "https://s.example", "generic_listing")
    ids = {}
    for title, tags in [("RemoteJob", ["Remote"]), ("AgencyJob", ["Remote", "Agency"]), ("PlainJob", [])]:
        jid = q.insert_job(conn, source_id=sid, url=f"https://s.example/{title}", title=title, company="", raw_text="r")
        q.update_job_pipeline(conn, jid, simplified_content="c", content_type="lead")
        for t in tags:
            q.add_job_tag(conn, jid, t)
        ids[title] = jid
    return ids


def test_job_list_filters_by_tag_and_not_tag(client, conn):
    _seed_tagged(conn)
    html = client.get("/jobs?status=lead&tag=remote&not_tag=Agency").text
    assert "RemoteJob" in html and "AgencyJob" not in html and "PlainJob" not in html


def test_tag_filter_route_adds_include_and_pushes_url(client, conn):
    _seed_tagged(conn)
    resp = client.get("/jobs/tag-filter", params={"status": "lead", "name": "Agency", "mode": "include"},
                      headers={"HX-Request": "true"})
    assert resp.status_code == 200
    assert "AgencyJob" in resp.text and "RemoteJob" not in resp.text
    assert resp.headers["HX-Push-Url"] == "/jobs?status=lead&tag=Agency"


def test_tag_filter_route_exclude_keeps_existing_tags(client, conn):
    _seed_tagged(conn)
    resp = client.get("/jobs/tag-filter", params={"status": "lead", "tag": "Remote", "name": "Agency", "mode": "exclude"},
                      headers={"HX-Request": "true"})
    assert "RemoteJob" in resp.text and "AgencyJob" not in resp.text
    assert resp.headers["HX-Push-Url"] == "/jobs?status=lead&tag=Remote&not_tag=Agency"
```

Also add a bulk-form test: find an existing bulk-feedback test in `tests/test_routes_jobs.py` (search `bulk-feedback`), copy its setup, and post with `data={..., "status_filter": "lead", "tag_filter": ["Remote"], "not_tag_filter": ["Agency"]}`; assert the re-rendered content lists only jobs matching that filter (e.g. accept `PlainJob` via `job_ids` and check the response still contains `RemoteJob` but not `AgencyJob`). Adapt the exact assertion to what that endpoint returns — read the endpoint (~line 705) first.

- [ ] **Step 2: Run** `uv run pytest tests/test_routes_jobs.py -q -k "tag"` — FAIL.

- [ ] **Step 3: Implement.**

`_jobs_for_filter`: pass `tags=f.tags, not_tags=f.not_tags` to both `q.search_jobs` and `q.get_jobs_for_tabs`. `_counts_for_filter`: pass `tags=f.tags, not_tags=f.not_tags`.

`_filter_from_bulk_form`: add params `tag_filter: list[str] | None = None, not_tag_filter: list[str] | None = None` and include `"tag": tag_filter or [], "not_tag": not_tag_filter or []` in the dict. In the two bulk route signatures that call it (~lines 714 and 775) add

```python
    tag_filter: list[str] = Form([]),
    not_tag_filter: list[str] = Form([]),
```

and pass them through by keyword.

New route — **register it directly after `job_list` and before `@router.get("/jobs/{job_id}")`**, otherwise `/jobs/{job_id}` captures `tag-filter` and returns 422:

```python
@router.get("/jobs/tag-filter", response_class=HTMLResponse)
def job_list_tag_filter(
    request: Request, name: str = "", mode: str = "include",
    conn: sqlite3.Connection = Depends(get_db),
):
    """Filter-panel 'Must have' / 'Exclude' buttons: add one tag to the
    current filter, then behave like /jobs with the resulting URL pushed."""
    f = _filter_from_request(request)
    f = f.with_not_tag(name) if mode == "exclude" else f.with_tag(name)
    resp = templates.TemplateResponse(request, "jobs/_content.html", _content_context(conn, f))
    resp.headers["HX-Push-Url"] = "/jobs?" + f.query_string()
    return resp
```

(`_filter_from_request` ignores the unknown `name` / `mode` params.)

- [ ] **Step 4: Run** `uv run pytest tests/test_routes_jobs.py tests/test_job_filter.py -q` — PASS.
- [ ] **Step 5: Commit** `feat(tags): tag filters in job list, counts and bulk actions`.

---

### Task 6: Edit routes + datalist option routes

**Files:**
- Create: `app/routes/tags.py`, `app/templates/jobs/_meta_tags_fragment.html`, `app/templates/tags/_options.html`
- Modify: `app/main.py` (import + `app.include_router(tags.router)`)
- Test: `tests/test_routes_tags.py`

**Interfaces:**
- Consumes: `q.add_job_tag`, `q.remove_job_tag`, `q.set_company_override`, `q.get_job_with_source_name`, `q.get_tag_names_by_usage`, `q.get_distinct_companies`, `JobFilter.from_params`; macro `meta_tags(job, filter=none, open_tag_input=false)` (signature finalised in Task 7 — this task's fragment template already calls it with `open_tag_input`, and Task 7 adds the parameter; until then Jinja ignores nothing, so **add the `open_tag_input=false` parameter to the macro signature in this task** with no other macro change).
- Produces:
  - `POST /jobs/{job_id}/tags` (form `name`) → fragment, header `HX-Trigger: tags-changed`; fragment rendered with the add-input open.
  - `DELETE /jobs/{job_id}/tags/{tag_id}` → fragment, `HX-Trigger: tags-changed`.
  - `POST /jobs/{job_id}/organization` (form `name`) → fragment, `HX-Trigger: orgs-changed`.
  - `GET /tags/options`, `GET /orgs/options` → `<option value="…">` lines.
  - All job routes 404 on unknown job. `?detail=1` → render with `filter=None`; otherwise filter parsed from the query string.

- [ ] **Step 1: Write failing tests** `tests/test_routes_tags.py`:

```python
from app.db import queries as q


def _job(conn, company="Acme"):
    sid = q.insert_source(conn, "s", "https://s.example", "generic_listing")
    return q.insert_job(conn, source_id=sid, url="https://s.example/1", title="T", company=company, raw_text="r")


def test_add_tag_returns_fragment_with_tag_and_open_input(client, conn):
    jid = _job(conn)
    resp = client.post(f"/jobs/{jid}/tags?status=new", data={"name": "Language: English"})
    assert resp.status_code == 200
    assert resp.headers["HX-Trigger"] == "tags-changed"
    assert "Language: English" in resp.text
    assert 'class="job-tags' in resp.text
    assert "autofocus" in resp.text
    assert [t["name"] for t in q.get_job_tags(conn, jid)] == ["Language: English"]


def test_add_tag_unknown_job_404(client, conn):
    assert client.post("/jobs/999/tags", data={"name": "x"}).status_code == 404


def test_remove_tag(client, conn):
    jid = _job(conn)
    t = q.add_job_tag(conn, jid, "Remote")
    resp = client.delete(f"/jobs/{jid}/tags/{t['id']}?detail=1")
    assert resp.status_code == 200
    assert "Remote" not in resp.text
    assert q.get_job_tags(conn, jid) == []


def test_set_and_clear_organization(client, conn):
    jid = _job(conn, company="")
    resp = client.post(f"/jobs/{jid}/organization?detail=1", data={"name": "Brand New Org"})
    assert resp.status_code == 200
    assert resp.headers["HX-Trigger"] == "orgs-changed"
    assert "Brand New Org" in resp.text
    resp = client.post(f"/jobs/{jid}/organization?detail=1", data={"name": ""})
    assert "Brand New Org" not in resp.text
    assert "+ organization" in resp.text


def test_option_routes(client, conn):
    jid = _job(conn)
    q.add_job_tag(conn, jid, 'Say "hi"')
    tags_html = client.get("/tags/options").text
    assert '<option value="Say &#34;hi&#34;">' in tags_html
    assert '<option value="Acme">' in client.get("/orgs/options").text
```

- [ ] **Step 2: Run** `uv run pytest tests/test_routes_tags.py -q` — FAIL (404s).

- [ ] **Step 3: Implement.**

`app/templates/tags/_options.html`:

```jinja
{% for v in values %}<option value="{{ v }}">
{% endfor %}
```

(Jinja autoescape renders `"` as `&#34;`.)

`app/templates/jobs/_meta_tags_fragment.html`:

```jinja
{% import "jobs/_macros.html" as macros %}
{{ macros.meta_tags(job, filter=filter, open_tag_input=open_tag_input|default(false)) }}
```

`app/routes/tags.py`:

```python
from __future__ import annotations
import sqlite3
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from app.db import queries as q
from app.deps import get_db
from app.job_filter import JobFilter
from app.template_env import templates

router = APIRouter()


def _fragment(request: Request, conn: sqlite3.Connection, job_id: int, *,
              trigger: str, open_tag_input: bool = False) -> HTMLResponse:
    job = q.get_job_with_source_name(conn, job_id)
    detail = request.query_params.get("detail") == "1"
    f = None if detail else JobFilter.from_params(request.query_params)
    resp = templates.TemplateResponse(
        request, "jobs/_meta_tags_fragment.html",
        {"job": job, "filter": f, "open_tag_input": open_tag_input},
    )
    resp.headers["HX-Trigger"] = trigger
    return resp


def _require_job(conn: sqlite3.Connection, job_id: int) -> None:
    if q.get_job(conn, job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")


@router.post("/jobs/{job_id}/tags", response_class=HTMLResponse)
def add_tag(job_id: int, request: Request, name: str = Form(""),
            conn: sqlite3.Connection = Depends(get_db)):
    _require_job(conn, job_id)
    q.add_job_tag(conn, job_id, name)
    return _fragment(request, conn, job_id, trigger="tags-changed", open_tag_input=True)


@router.delete("/jobs/{job_id}/tags/{tag_id}", response_class=HTMLResponse)
def remove_tag(job_id: int, tag_id: int, request: Request,
               conn: sqlite3.Connection = Depends(get_db)):
    _require_job(conn, job_id)
    q.remove_job_tag(conn, job_id, tag_id)
    return _fragment(request, conn, job_id, trigger="tags-changed")


@router.post("/jobs/{job_id}/organization", response_class=HTMLResponse)
def set_organization(job_id: int, request: Request, name: str = Form(""),
                     conn: sqlite3.Connection = Depends(get_db)):
    _require_job(conn, job_id)
    q.set_company_override(conn, job_id, name)
    return _fragment(request, conn, job_id, trigger="orgs-changed")


@router.get("/tags/options", response_class=HTMLResponse)
def tag_options(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    return templates.TemplateResponse(
        request, "tags/_options.html", {"values": q.get_tag_names_by_usage(conn)})


@router.get("/orgs/options", response_class=HTMLResponse)
def org_options(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    return templates.TemplateResponse(
        request, "tags/_options.html", {"values": q.get_distinct_companies(conn)})
```

`app/main.py`: add `tags` to the `from app.routes import …` import and `app.include_router(tags.router)` after `cv_versions`.

Minimal macro changes so the tests can pass now (Task 7 replaces the org/tag markup fully): in `meta_tags`, add the `open_tag_input=false` parameter, switch the org block from `job.company` to `job.org` (including `filter.with_org(job.org)`), render a muted `+ organization` span when `job.org` is empty, render each `job.tags_json | job_tags` as `<dd><span class="tag tag-manual">{{ t.name }}</span></dd>`, and when `open_tag_input` emit `<input name="name" autofocus>` inside a form. Task 7 replaces this with the final markup.

- [ ] **Step 4: Run** `uv run pytest tests/test_routes_tags.py tests/test_routes_jobs.py -q` — PASS.
- [ ] **Step 5: Commit** `feat(tags): edit and option routes`.

---

### Task 7: Inline editing UI in the pill row

Use the `frontend-design` skill for this task.

**Files:**
- Modify: `app/templates/jobs/_macros.html` (`meta_tags`), `app/templates/base.html` (CSS + datalists + small JS), `app/templates/jobs/_job_header.html`, `app/templates/jobs/_feedback.html`, `app/routes/cv.py:37`
- Test: `tests/test_routes_tags.py`, `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: routes from Task 6, `job.org`, `job.company`, `job.company_override`, `job.tags_json | job_tags`, `JobFilter.with_tag`, macro `qsuffix`.
- Produces: final `meta_tags(job, filter=none, open_tag_input=false)` markup; global JS `openInlineEdit(btn)` / `closeInlineEdit(form)`; `<datalist id="tag-options">` and `<datalist id="org-options">` in `base.html`.

Behaviour to implement (spec "Editing UI"):

- Edit URLs use suffix `{{ qsuffix(filter, detail=filter is none) }}` so the route knows whether to render filter links.
- Every edit control targets `hx-target="closest dl.job-tags" hx-swap="outerHTML"` (no ids — the same job can appear in header and card on one page).
- Every editable `<dd>` carries `onclick="event.stopPropagation()" onkeyup="event.stopPropagation()" onkeydown="event.stopPropagation()"` — the list row (`_row.html`) expands on `click` and `keyup[key=='Enter']`.
- **Org dd:** if `job.org`: the existing link/span pill (now `job.org`, `filter.with_org(job.org)`), with extra class `tag-org-manual` and `title="Set manually{% if job.company %} — extracted: {{ job.company }}{% endif %}"` when `job.company_override`; then a `<button type="button" class="tag-edit-btn" aria-label="Edit organization" onclick="openInlineEdit(this)">✎</button>`. Else: `<button type="button" class="tag tag-add" onclick="openInlineEdit(this)">+ organization</button>`. Followed by

  ```jinja
  <form class="tag-inline-form" hidden
        hx-post="/jobs/{{ job.id }}/organization{{ sfx }}"
        hx-target="closest dl.job-tags" hx-swap="outerHTML">
    <input name="name" list="org-options" value="{{ job.org or '' }}"
           aria-label="Organization" autocomplete="off" placeholder="Organization"
           onkeydown="if (event.key === 'Escape') closeInlineEdit(this.form)"
           onblur="closeInlineEdit(this.form)">
  </form>
  ```
- **Manual tag dds** (after the source pill), one per `job.tags_json | job_tags`: an `<a class="tag tag-manual">` with `hx-get="/jobs?{{ filter.with_tag(t.name).query_params() | urlencode }}" hx-target="#jobs-content" hx-swap="innerHTML" hx-push-url="true"` when `filter` is set, else `<span class="tag tag-manual">`; then `<button type="button" class="tag-remove" hx-delete="/jobs/{{ job.id }}/tags/{{ t.id }}{{ sfx }}" hx-target="closest dl.job-tags" hx-swap="outerHTML" aria-label="Remove tag {{ t.name }}">×</button>`. Render `Key: Value` names with the part before the first `: ` muted (`<span class="tag-key">Language:</span> English`) — display only.
- **Add dd:** `<button type="button" class="tag tag-add" onclick="openInlineEdit(this)"{% if open_tag_input %} hidden{% endif %}>+ tag</button>` and a form like the org form posting to `/jobs/{{ job.id }}/tags{{ sfx }}`, input `list="tag-options"`, `placeholder="Tag"`, empty value, `{% if not open_tag_input %}hidden{% endif %}` on the form and `{% if open_tag_input %}autofocus{% endif %}` on the input.
- In `base.html`, inside `<body>` but **outside** any `hx-history-elt` element:

  ```html
  <datalist id="tag-options" hx-get="/tags/options" hx-trigger="load, tags-changed from:body"></datalist>
  <datalist id="org-options" hx-get="/orgs/options" hx-trigger="load, orgs-changed from:body"></datalist>
  ```

  and in the main script block:

  ```js
  function openInlineEdit(btn) {
    var form = btn.parentElement.querySelector(".tag-inline-form");
    btn.hidden = true;
    form.hidden = false;
    var input = form.querySelector("input");
    input.focus();
    input.select();
  }
  function closeInlineEdit(form) {
    // Defer so a click on a datalist suggestion / Enter submit isn't cut off.
    setTimeout(function () {
      if (!form.isConnected || form.contains(document.activeElement)) return;
      form.hidden = true;
      form.parentElement.querySelectorAll("[onclick^='openInlineEdit']").forEach(function (b) { b.hidden = false; });
    }, 150);
  }
  ```
- CSS (next to `.tag-org` ~line 275): `.tag-manual` (neutral but distinct from system pills, e.g. outlined `border: 1px solid var(--border)` on transparent bg), `.tag-key` (muted `var(--text-secondary)`), `.tag-add` (dashed outline, muted, `cursor:pointer`), `.tag-edit-btn` / `.tag-remove` (small, borderless, muted until hover), `.tag-org-manual` (subtle marker such as a dotted underline), `.tag-inline-form input` (compact, same height as a pill). Check both light and dark themes.
- Switch remaining org display to the effective value: `_job_header.html` line 5 and `_feedback.html` lines 17 and 48 use `job.org`; `app/routes/cv.py:37` uses `job.get('org', '')`.

- [ ] **Step 1: Write failing tests** (append to `tests/test_routes_tags.py`):

```python
def test_list_row_shows_manual_tag_as_filter_link_with_remove(client, conn):
    jid = _job(conn)
    q.update_job_pipeline(conn, jid, simplified_content="c", content_type="lead")
    q.add_job_tag(conn, jid, "Language: English")
    html = client.get("/jobs?status=lead").text
    assert 'class="tag tag-manual"' in html
    assert "tag=Language%3A+English" in html
    assert f'hx-delete="/jobs/{jid}/tags/' in html
    assert '<span class="tag-key">Language:</span>' in html


def test_detail_page_has_edit_controls_and_datalists(client, conn):
    jid = _job(conn, company="")
    html = client.get(f"/jobs/{jid}").text
    assert "+ organization" in html and "+ tag" in html
    assert f'hx-post="/jobs/{jid}/tags?detail=1"' in html
    assert 'id="tag-options"' in html and 'id="org-options"' in html


def test_override_marker_and_effective_org_in_header(client, conn):
    jid = _job(conn, company="Acme")
    q.set_company_override(conn, jid, "Acme Robotics")
    html = client.get(f"/jobs/{jid}").text
    assert "tag-org-manual" in html
    assert "extracted: Acme" in html
    assert '<p class="job-header-org">Acme Robotics</p>' in html
```

- [ ] **Step 2: Run** — FAIL. **Step 3: Implement** as described. **Step 4: Run** `uv run pytest -q` (full suite — the macro is shared by many templates) — PASS.
- [ ] **Step 5: Manual check** with the `run-dev-server` skill (throwaway DB copy): add/remove tags in a list row (row must not expand), Enter keeps the input open for the next tag, Escape closes, suggestions appear, org set/clear on a job with and without an LLM org, detail page. Stop the server afterwards.
- [ ] **Step 6: Commit** `feat(tags): inline tag and organization editing`.

---

### Task 8: Tag filter UI (chips + filter panel)

Use the `frontend-design` skill for this task.

**Files:**
- Modify: `app/templates/jobs/_content.html`, `app/templates/jobs/list.html`, `app/templates/base.html` (CSS)
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: `filter.tags`, `filter.not_tags`, `with_tag_flipped`, `without_tag`, `/jobs/tag-filter`, `#tag-options` datalist.

Behaviour to implement (spec "Filtering"):

- Bulk-form hidden inputs (next to `org_filter`, `_content.html` ~line 21):

  ```jinja
  {% for t in filter.tags %}<input type="hidden" name="tag_filter" value="{{ t }}" form="bulk-form">{% endfor %}
  {% for t in filter.not_tags %}<input type="hidden" name="not_tag_filter" value="{{ t }}" form="bulk-form">{% endfor %}
  ```
- Filter-state hidden inputs inside the Filters panel, so the selects keep tags when they fire:

  ```jinja
  {% for t in filter.tags %}<input type="hidden" name="tag" value="{{ t }}">{% endfor %}
  {% for t in filter.not_tags %}<input type="hidden" name="not_tag" value="{{ t }}">{% endfor %}
  ```

  and append `,[name='tag'],[name='not_tag']` to the `hx-include` of the scenario, source, org and order selects in `_content.html`, and to the search input's `hx-include` in `list.html` (line 31).
- `active_filter_count` (line 34): add `+ filter.tags | length + filter.not_tags | length`.
- Chips row directly under `.tb-toolbar`, only when `filter.tags or filter.not_tags`:

  ```jinja
  <div class="tb-tag-chips" aria-label="Tag filters">
    {% for t in filter.tags %}
    <span class="tag-chip tag-chip-include">
      <a hx-get="/jobs?{{ filter.with_tag_flipped(t).query_params() | urlencode }}" hx-target="#jobs-content"
         hx-swap="innerHTML" hx-push-url="true" title="Must have — click to exclude instead">+ {{ t }}</a>
      <a hx-get="/jobs?{{ filter.without_tag(t).query_params() | urlencode }}" hx-target="#jobs-content"
         hx-swap="innerHTML" hx-push-url="true" class="tag-chip-x" aria-label="Remove tag filter {{ t }}">×</a>
    </span>
    {% endfor %}
    {% for t in filter.not_tags %}
    … same with class tag-chip-exclude, label "− {{ t }}", title "Excluded — click to require instead" …
    {% endfor %}
  </div>
  ```
- Filters panel, after the Organization select:

  ```jinja
  <label class="tb-panel-label" for="tb-tag">Tags</label>
  <form class="tb-tag-form" hx-get="/jobs/tag-filter" hx-target="#jobs-content" hx-swap="innerHTML"
        hx-vals='{"status": "{{ filter.statuses | join(',') }}"}'
        hx-include="[name='scenario'],[name='source_id'],[name='org'],[name='order'],[name='q'],[name='tag'],[name='not_tag']">
    <input id="tb-tag" name="name" list="tag-options" autocomplete="off" placeholder="Tag" required>
    <button type="submit" name="mode" value="include" class="btn">Must have</button>
    <button type="submit" name="mode" value="exclude" class="btn">Exclude</button>
  </form>
  ```

  (No `hx-push-url` here — the route sends `HX-Push-Url`.)
- CSS: `.tb-tag-chips` (flex, wrap, small gap, aligned with toolbar padding), `.tag-chip-include` (accent tint), `.tag-chip-exclude` (danger/warning tint with the label struck or prefixed `−`), `.tag-chip-x`, `.tb-tag-form` (input + two buttons on one line, wrapping on mobile).

- [ ] **Step 1: Write failing tests** (append to `tests/test_routes_jobs.py`, reusing `_seed_tagged` from Task 5):

```python
def test_tag_filter_chips_and_hidden_inputs(client, conn):
    _seed_tagged(conn)
    html = client.get("/jobs?status=lead&tag=Remote&not_tag=Agency").text
    assert "tag-chip-include" in html and "tag-chip-exclude" in html
    assert '<input type="hidden" name="tag" value="Remote">' in html
    assert '<input type="hidden" name="not_tag_filter" value="Agency" form="bulk-form">' in html
    # flip link turns Remote into an exclusion and keeps Agency excluded
    assert "not_tag=Agency&amp;not_tag=Remote" in html or "not_tag=Agency&not_tag=Remote" in html
    assert 'hx-get="/jobs/tag-filter"' in html


def test_filter_badge_counts_tag_filters(client, conn):
    _seed_tagged(conn)
    html = client.get("/jobs?status=lead&tag=Remote&not_tag=Agency").text
    assert '<span class="tb-badge">2</span>' in html
```

- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** `uv run pytest -q` — PASS.
- [ ] **Step 5: Manual check** with the dev server: click a tag pill → chip appears; flip chip → becomes exclusion; `×` removes; panel Must have / Exclude with autocomplete; scenario/source/org selects and search keep tag filters; tab counts update; bulk action on a tag-filtered view keeps the filter. Stop the server.
- [ ] **Step 6: Commit** `feat(tags): tag filter chips and filter-panel controls`.

---

### Task 9: Wrap-up

- [ ] Run the full suite: `uv run pytest -q` — all green.
- [ ] Remove the backlog entry from `BACKLOG.md` (the "Allow manual tags …" line under Medium) and commit `docs(backlog): drop manual tags item`.
- [ ] Leave the dev server running against the throwaway DB and report the URL — the user tests the UI before merging.
