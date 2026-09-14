# Pending Job Status Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `pending` as a new `jobs.status` value (jobs the user has applied to), with a "Mark pending" organize action that's a full peer to Accept/Reject/Trash everywhere those appear.

**Architecture:** Widen the existing `jobs.status` CHECK constraint via the repo's established drop/rebuild migration pattern, then thread `"pending"` through every place `"accepted"`/`"rejected"`/`"trash"` already appear as a first-class status: tab list, counts, predicates, stale badges, status pill, organize-action buttons (single + bulk). One deliberate divergence: pending jobs are excluded from background re-evaluation/revisit sweeps, same as rejected/trash — this only requires *not* adding `"pending"` to two existing status allowlists in `app/pipeline.py`/`app/db/queries.py`, plus explicitly adding it to one exclusion tuple in `run_reevaluate_job`.

**Tech Stack:** Python 3.14, FastAPI, sqlite3 (stdlib), Jinja2 templates, pytest + httpx `TestClient`.

## Global Constraints

- Migration philosophy (CLAUDE.md): prefer a simple hard-downtime drop/rebuild over compatibility shims. This is a pure CHECK-constraint widen — no data transform.
- Any migration that rebuilds `jobs` drops the `jobs_fts` triggers with it — must follow with `INSERT INTO jobs_fts(jobs_fts) VALUES('rebuild')` and recreate the three triggers (see `_migrate_add_jobs_fts` in `app/db/schema.py`).
- Commit after each task goes green (per CLAUDE.md "Commit frequently").
- Button label is exactly **"Mark pending"**. Button order is **Accept → Mark pending → Reject → Trash**.
- Pending jobs are frozen for background processing: excluded from `get_revisitable_jobs`, from the two `pipeline.py` re-evaluation-candidate queries, and skipped by `run_reevaluate_job` — same treatment as rejected/trash.

---

### Task 1: Widen `jobs.status` to allow `'pending'`

**Files:**
- Modify: `app/db/schema.py` (the `_DDL` constant's `jobs` table CHECK, and a new migration function + its call in `init_db`)
- Test: `tests/test_schema.py`

**Interfaces:**
- Produces: `jobs.status` CHECK now includes `'pending'`, for both fresh installs (via `_DDL`) and existing DBs (via the new `_migrate_jobs_add_pending_status` migration). No new Python symbols consumed by later tasks — later tasks just rely on the DB accepting `status='pending'`.

- [ ] **Step 1: Write the failing migration test**

Add to `tests/test_schema.py` (near the existing `test_jobs_status_check_allows_trash_not_invalid` / `test_init_db_migrates_jobs_status_invalid_to_trash` tests, e.g. right after line 889):

```python
def test_jobs_status_check_allows_pending(conn):
    init_db(conn)
    conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s', 'http://x', 'slack')")
    conn.execute(
        "INSERT INTO jobs (source_id, url, title, status) VALUES (1, 'http://job/1', 'Title', 'pending')"
    )
    conn.commit()
    row = conn.execute("SELECT status FROM jobs WHERE url = 'http://job/1'").fetchone()
    assert row["status"] == "pending"


def test_init_db_migrates_jobs_add_pending_status(conn):
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
            headline TEXT NOT NULL DEFAULT '',
            published_at TEXT,
            content_type TEXT CHECK(content_type IN ('job_posting', 'lead', 'irrelevant', 'error')),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
            status TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('new', 'accepted', 'rejected', 'trash')),
            feedback_note TEXT,
            feedback_handled_at TEXT,
            interest_score REAL,
            interest_reasoning TEXT,
            attainability_score REAL,
            attainability_reasoning TEXT,
            fit_score REAL,
            profile_version_hash TEXT,
            gate_override INTEGER NOT NULL DEFAULT 0,
            evaluation_completed_at TEXT,
            status_changed_at TEXT
        );
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
        """
    )
    conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s', 'http://x', 'slack')")
    conn.execute(
        "INSERT INTO jobs (source_id, url, title, status, summary) "
        "VALUES (1, 'http://job/1', 'Kubernetes Engineer', 'accepted', 'runs Kubernetes clusters')"
    )
    conn.commit()

    init_db(conn)

    # existing data preserved
    row = conn.execute("SELECT title, status FROM jobs WHERE url = 'http://job/1'").fetchone()
    assert row["title"] == "Kubernetes Engineer"
    assert row["status"] == "accepted"

    # widened CHECK now accepts 'pending'
    conn.execute(
        "INSERT INTO jobs (source_id, url, title, status) VALUES (1, 'http://job/2', 'New Title', 'pending')"
    )
    row2 = conn.execute("SELECT status FROM jobs WHERE url = 'http://job/2'").fetchone()
    assert row2["status"] == "pending"

    # still rejects genuinely invalid values
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO jobs (source_id, url, title, status) VALUES (1, 'http://job/3', 'Title', 'bogus')"
        )

    # FTS search still works after the rebuild (triggers recreated, content reindexed)
    hits = conn.execute(
        "SELECT rowid FROM jobs_fts WHERE jobs_fts MATCH ?", ('"Kubernetes"',)
    ).fetchall()
    assert {r["rowid"] for r in hits} == {1}

    # Idempotent: running init_db again doesn't error or lose data.
    init_db(conn)
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 2
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_schema.py -k pending -v`
Expected: both FAIL — `test_jobs_status_check_allows_pending` fails the `assert row["status"] == "pending"` (or raises `IntegrityError` from the INSERT since `_DDL` doesn't allow `'pending'` yet); `test_init_db_migrates_jobs_add_pending_status` fails the `'pending'` INSERT the same way.

- [ ] **Step 3: Widen the canonical `_DDL` CHECK constraint**

In `app/db/schema.py`, find the `jobs` table definition inside the `_DDL` string constant (currently `status TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('new', 'accepted', 'rejected', 'trash')),`) and widen it:

```python
    status TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('new', 'accepted', 'rejected', 'trash', 'pending')),
```

- [ ] **Step 4: Add the migration function**

In `app/db/schema.py`, add this function right after `_migrate_cv_scope_options_drop_is_baseline` (the last migration function, immediately before `def init_db`):

```python
def _migrate_jobs_add_pending_status(conn: sqlite3.Connection) -> None:
    # Widens jobs.status to allow 'pending' (jobs the user has applied to).
    # Pure widen, not a rename, so no data transform is needed -- but SQLite
    # can't alter a CHECK constraint in place, so this still needs the same
    # drop/rebuild as _migrate_jobs_status_invalid_to_trash above.
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='jobs'"
    ).fetchone()
    if row is None or "'pending'" in row[0]:
        return
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript(
        """
        CREATE TABLE jobs_new (
            id INTEGER PRIMARY KEY,
            source_id INTEGER NOT NULL REFERENCES sources(id),
            url TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL DEFAULT '',
            company TEXT NOT NULL DEFAULT '',
            raw_text TEXT NOT NULL DEFAULT '',
            simplified_content TEXT NOT NULL DEFAULT '',
            summary TEXT NOT NULL DEFAULT '',
            headline TEXT NOT NULL DEFAULT '',
            published_at TEXT,
            content_type TEXT CHECK(content_type IN ('job_posting', 'lead', 'irrelevant', 'error')),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
            status TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('new', 'accepted', 'rejected', 'trash', 'pending')),
            feedback_note TEXT,
            feedback_handled_at TEXT,
            interest_score REAL,
            interest_reasoning TEXT,
            attainability_score REAL,
            attainability_reasoning TEXT,
            fit_score REAL,
            profile_version_hash TEXT,
            gate_override INTEGER NOT NULL DEFAULT 0,
            evaluation_completed_at TEXT,
            status_changed_at TEXT
        );
        """
    )
    conn.execute(
        """
        INSERT INTO jobs_new (
            id, source_id, url, title, company, raw_text, simplified_content, summary,
            headline, published_at, content_type, created_at, fetched_at, status, feedback_note,
            feedback_handled_at, interest_score, interest_reasoning, attainability_score,
            attainability_reasoning, fit_score, profile_version_hash, gate_override,
            evaluation_completed_at, status_changed_at
        )
        SELECT
            id, source_id, url, title, company, raw_text, simplified_content, summary,
            headline, published_at, content_type, created_at, fetched_at, status, feedback_note,
            feedback_handled_at, interest_score, interest_reasoning, attainability_score,
            attainability_reasoning, fit_score, profile_version_hash, gate_override,
            evaluation_completed_at, status_changed_at
        FROM jobs
        """
    )
    conn.execute("DROP TABLE jobs")
    conn.execute("ALTER TABLE jobs_new RENAME TO jobs")
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")

    # Rebuilding jobs dropped the jobs_fts triggers (they're defined on
    # jobs) -- recreate them and reindex, per the note in _migrate_add_jobs_fts.
    conn.executescript(
        """
        INSERT INTO jobs_fts(jobs_fts) VALUES('rebuild');

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
        """
    )
    conn.commit()
```

- [ ] **Step 5: Register the migration**

In `app/db/schema.py`, in `init_db`, add the call as the new last line (right after `_migrate_cv_scope_options_drop_is_baseline(conn)`):

```python
    _migrate_jobs_add_pending_status(conn)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest tests/test_schema.py -k pending -v`
Expected: both PASS.

- [ ] **Step 7: Run the full schema test file to check for regressions**

Run: `python -m pytest tests/test_schema.py -v`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add app/db/schema.py tests/test_schema.py
git commit -m "feat: widen jobs.status to allow pending"
```

---

### Task 2: Add `pending` to the jobs tab list

**Files:**
- Modify: `app/job_filter.py`
- Test: `tests/test_job_filter.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `VALID_TABS = ("new", "lead", "accepted", "pending", "rejected", "not_relevant", "trash")` — later tasks (`_TAB_PREDICATE` in queries.py, `_SEARCH_SEED_TABS` in routes/jobs.py, the tab list in `_content.html`) all key off tab names being valid here.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_job_filter.py` (near the other `VALID_TABS`-order tests, e.g. after `test_statuses_comma_list_kept_in_order_and_deduped` around line 135):

```python
def test_pending_is_a_valid_tab_ordered_after_accepted():
    f = JobFilter.from_params({"status": "trash,pending,new"})
    assert f.statuses == ("new", "pending", "trash")


def test_with_status_toggled_adds_pending_after_accepted():
    f = JobFilter.from_params({"status": "accepted"}).with_status_toggled("pending")
    assert f.statuses == ("accepted", "pending")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_job_filter.py -k pending -v`
Expected: FAIL — `"pending"` isn't in `VALID_TABS`, so `from_params` drops it and `with_status_toggled` doesn't add it.

- [ ] **Step 3: Add `pending` to `VALID_TABS`**

In `app/job_filter.py`, change:

```python
VALID_TABS = ("new", "lead", "accepted", "rejected", "not_relevant", "trash")
```

to:

```python
VALID_TABS = ("new", "lead", "accepted", "pending", "rejected", "not_relevant", "trash")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_job_filter.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/job_filter.py tests/test_job_filter.py
git commit -m "feat: add pending to the valid job tabs"
```

---

### Task 3: `pending` in tab predicates and counts (queries.py)

**Files:**
- Modify: `app/db/queries.py`
- Test: `tests/test_queries.py`

**Interfaces:**
- Consumes: `jobs.status` accepts `'pending'` (Task 1); `VALID_TABS` includes `"pending"` (Task 2).
- Produces: `_TAB_PREDICATE["pending"]`, `get_job_counts(...)["pending"]`. `get_revisitable_jobs` explicitly stays scoped to `new`/`lead`/`accepted` only — pending jobs must NOT appear in its result (frozen, same as rejected/trash).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_queries.py` right after `test_get_job_counts` (around line 547):

```python
def test_get_job_counts_includes_pending(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_feedback(conn, jid, "pending", "applied")
    counts = q.get_job_counts(conn)
    assert counts["pending"] == 1
```

And a `pending` case in the revisitable-scope test. Modify `test_get_revisitable_jobs_scope` (around line 1859) to add a pending job to the excluded set:

```python
def test_get_revisitable_jobs_scope(conn):
    gid = q.insert_source(conn, "board", "http://example.com", "generic_listing")
    slk = q.insert_source(conn, "slk", "http://x.slack.com/c", "slack")

    gate_passed = _revisit_job(conn, gid, "http://example.com/passed")
    lead = _revisit_job(conn, gid, "http://example.com/lead", content_type="lead")
    accepted = _revisit_job(conn, gid, "http://example.com/acc", status="accepted")

    # Excluded: gate-failed "Not relevant", error rows, rejected, trash, pending, slack, unevaluated.
    _revisit_job(conn, gid, "http://example.com/notrel", evaluated=False)
    _revisit_job(conn, gid, "http://example.com/err", content_type="error")
    _revisit_job(conn, gid, "http://example.com/rej", status="rejected")
    _revisit_job(conn, gid, "http://example.com/trash", status="trash")
    _revisit_job(conn, gid, "http://example.com/pending", status="pending")
    _revisit_job(conn, slk, "http://x.slack.com/c#1")

    ids = {j["id"] for j in q.get_revisitable_jobs(conn)}
    assert ids == {gate_passed, lead, accepted}
```

(This is a one-line addition to the existing test, not a new test — it locks in that pending stays excluded without needing a code change, since `get_revisitable_jobs`'s SQL only ever whitelists `new`/`lead`/`accepted`.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_queries.py -k "pending or revisitable_jobs_scope" -v`
Expected: `test_get_job_counts_includes_pending` FAILs with `KeyError: 'pending'` (not yet in the counts dict). `test_get_revisitable_jobs_scope` PASSes already (no code change needed there) — confirming the freeze behavior is already correct by construction.

- [ ] **Step 3: Add the `pending` tab predicate**

In `app/db/queries.py`, in `_TAB_PREDICATE` (around line 924), add:

```python
_TAB_PREDICATE: dict[str, str] = {
    "new": f"(jobs.status = 'new' AND jobs.content_type = 'job_posting' AND ({_GATE_PASSED_CLAUSE}))",
    "lead": "(jobs.status = 'new' AND jobs.content_type = 'lead')",
    "accepted": "(jobs.status = 'accepted')",
    "pending": "(jobs.status = 'pending')",
    "rejected": "(jobs.status = 'rejected')",
    "not_relevant": f"(jobs.status = 'new' AND jobs.content_type = 'job_posting' AND ({_GATE_FAILED_CLAUSE}))",
    "trash": "(jobs.status = 'trash')",
}
```

- [ ] **Step 4: Add `pending` to `get_job_counts`**

In `app/db/queries.py`, in `get_job_counts` (around line 1010), update the initial dict and the direct-count loop:

```python
    counts = {"new": 0, "accepted": 0, "pending": 0, "rejected": 0, "trash": 0, "lead": 0, "not_relevant": 0}

    for key in ("accepted", "pending", "rejected", "trash"):
```

(leave the rest of the function body unchanged — the loop body already generalizes over `key`.)

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_queries.py -v`
Expected: all PASS. (Existing `test_get_job_counts` at line 536 already asserts the full counts dict with an explicit `==` comparison — verify it still passes; it doesn't create a pending job, so it needs `"pending": 0` added to its expected dict.)

- [ ] **Step 5b: Update `test_get_job_counts`'s expected dict**

In `tests/test_queries.py`, change:

```python
    assert counts == {"new": 0, "accepted": 1, "rejected": 1, "trash": 0, "lead": 1, "not_relevant": 0}
```

to:

```python
    assert counts == {"new": 0, "accepted": 1, "pending": 0, "rejected": 1, "trash": 0, "lead": 1, "not_relevant": 0}
```

Then re-run: `python -m pytest tests/test_queries.py -v` — expect all PASS.

- [ ] **Step 6: Commit**

```bash
git add app/db/queries.py tests/test_queries.py
git commit -m "feat: add pending tab predicate and job count"
```

---

### Task 4: Freeze `pending` jobs out of per-job re-evaluation

**Files:**
- Modify: `app/pipeline.py:405`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `jobs.status` accepts `'pending'` (Task 1).
- Produces: `run_reevaluate_job` now skips `status == "pending"` the same way it already skips `"rejected"`/`"trash"`. No signature change.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_pipeline.py` right after `test_run_reevaluate_job_skips_trash_job` (around line 735):

```python
def test_run_reevaluate_job_skips_pending_job(conn, source):
    jid = q.insert_job(
        conn, source_id=source["id"], url="http://example.com/job/1",
        title="T", company="C", raw_text="raw",
    )
    q.update_job_pipeline(conn, jid, simplified_content="clean text", content_type="job_posting", summary="s")
    q.update_job_feedback(conn, jid, "pending", "applied")
    job = q.get_job(conn, jid)
    scenarios = q.get_scenarios(conn)
    profile = q.get_profile(conn)

    with patch("app.pipeline.summarize") as mock_summarize:
        messages, _ = _drain(run_reevaluate_job(conn, MagicMock(), "llama3.2", job, scenarios, profile))

    mock_summarize.assert_not_called()
    assert any("Skipped" in m for m in messages)
    assert q.get_job(conn, jid)["status"] == "pending"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_pipeline.py -k skips_pending_job -v`
Expected: FAIL — `mock_summarize.assert_not_called()` fails because `run_reevaluate_job` doesn't currently skip `pending`, so it proceeds to call `summarize`.

- [ ] **Step 3: Add `pending` to the skip guard**

In `app/pipeline.py`, in `run_reevaluate_job` (line 405), change:

```python
    if job["status"] in ("rejected", "trash") or job["content_type"] not in ("job_posting", "lead"):
```

to:

```python
    if job["status"] in ("rejected", "trash", "pending") or job["content_type"] not in ("job_posting", "lead"):
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_pipeline.py -k skips_pending_job -v`
Expected: PASS.

- [ ] **Step 5: Run the full pipeline test file to check for regressions**

Run: `python -m pytest tests/test_pipeline.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add app/pipeline.py tests/test_pipeline.py
git commit -m "feat: freeze pending jobs out of per-job re-evaluation"
```

---

### Task 5: `pending` in stale badges, search seeding, and the status-change revisit trigger (routes/jobs.py)

**Files:**
- Modify: `app/routes/jobs.py`
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: `_TAB_PREDICATE["pending"]` and `get_job_counts(...)["pending"]` (Task 3); `VALID_TABS` includes `"pending"` (Task 2).
- Produces: `_stale_badge` returns a `"Moved to Pending"` badge for pending jobs; `_SEARCH_SEED_TABS` includes `"pending"`; `POST /jobs/{id}/feedback` and `POST /jobs/bulk-feedback` enqueue the `jobs_revisit` status-change task for `status="pending"` too. No template changes yet (Task 7) — these tests exercise the JSON/badge/task side of the routes, not button HTML.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_routes_jobs.py` right after `test_job_feedback_without_filter_query_defaults_to_new_tab_badge` (around line 1662):

```python
def test_job_feedback_pending_shows_moved_to_pending_badge(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.post(f"/jobs/{jid}/feedback", data={"status": "pending", "note": ""})
    assert resp.status_code == 200
    assert "Moved to Pending" in resp.text
```

Add right after `test_run_reevaluate_job_skips_pending_job`-style tests aren't here — instead, add right after `test_accept_enqueues_status_change_revisit` (around line 2761):

```python
def test_mark_pending_enqueues_status_change_revisit(client, conn):
    sid = q.insert_source(conn, "board", "http://example.com", "generic_listing")
    jid = q.insert_job(conn, source_id=sid, url="http://example.com/j", title="J",
                       company="", raw_text="b")
    client.post(f"/jobs/{jid}/feedback", data={"status": "pending", "note": ""})
    tasks = [t for t in q.get_active_tasks(conn) if t["kind"] == "jobs_revisit"]
    assert len(tasks) == 1
    assert tasks[0]["params"] == {"job_ids": [jid], "trigger": "status_change"}
```

Add a bulk-feedback counterpart right after `test_job_bulk_feedback_note_is_optional` (around line 1041):

```python
def test_job_bulk_feedback_pending_enqueues_status_change_revisit(client, conn):
    sid = q.insert_source(conn, "board", "http://example.com", "generic_listing")
    j1 = q.insert_job(conn, source_id=sid, url="http://example.com/1", title="J1", company="", raw_text="b")
    j2 = q.insert_job(conn, source_id=sid, url="http://example.com/2", title="J2", company="", raw_text="b")
    client.post("/jobs/bulk-feedback", data={"job_ids": [j1, j2], "status": "pending", "note": ""})
    tasks = [t for t in q.get_active_tasks(conn) if t["kind"] == "jobs_revisit"]
    assert len(tasks) == 1
    assert set(tasks[0]["params"]["job_ids"]) == {j1, j2}
    assert tasks[0]["params"]["trigger"] == "status_change"
```

Add a search-seed test right after `test_job_list_filter_accepted` (around line 71):

```python
def test_search_surfaces_pending_jobs_by_default(client, conn):
    sid = q.insert_source(conn, "s", "https://s", "generic_listing")
    jid = q.insert_job(conn, source_id=sid, url="http://s/1", title="Rust Engineer", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="c", content_type="job_posting", summary="s")
    q.mark_job_evaluation_complete(conn, jid)
    q.update_job_feedback(conn, jid, "pending", "applied")
    resp = client.get("/jobs?q=rust")
    assert "Rust Engineer" in resp.text
```

**Pre-existing test needs updating too:** `test_search_tab_bar_shows_seeded_scope` (line 2612-2621) asserts an exact count of active seeded tabs and an exact toggle URL, both of which are built from `_SEARCH_SEED_TABS` via `_effective_tabs`. Adding `"pending"` to `_SEARCH_SEED_TABS` changes the seeded set from 4 tabs to 5, and shifts the toggle URL (tabs render in `VALID_TABS` order: `new, lead, accepted, pending, rejected, ...`). Update it:

```python
def test_search_tab_bar_shows_seeded_scope(client, conn):
    sid = q.insert_source(conn, "s", "https://s", "generic_listing")
    _seed_searchable(conn, sid, "http://s/1", "Kappa Engineer")
    html = client.get("/jobs?q=kappa").text
    # the seeded buckets (new/lead/accepted/pending/rejected) render active
    assert html.count('class="tab-item active"') == 5
    # a checkbox toggle to add Trash carries the whole seeded scope + the query
    assert ("status=new%2Clead%2Caccepted%2Cpending%2Crejected%2Ctrash" in html
            or "status=new,lead,accepted,pending,rejected,trash" in html)
    assert "q=kappa" in html
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_routes_jobs.py -k "pending or seeded_scope" -v`
Expected: `test_job_feedback_pending_shows_moved_to_pending_badge` FAILs (`_stale_badge` doesn't recognize `"pending"`, falls through to the generic `"No longer shown in this view"` badge or none). `test_mark_pending_enqueues_status_change_revisit` and the bulk counterpart FAIL (`status in ("accepted", "rejected")` doesn't include `"pending"`, so no task is enqueued). `test_search_surfaces_pending_jobs_by_default` FAILs (pending isn't in `_SEARCH_SEED_TABS` yet, so an unscoped search doesn't seed it in). `test_search_tab_bar_shows_seeded_scope` FAILs too, since it was just edited to expect 5 active tabs but `_SEARCH_SEED_TABS` still only produces 4 — this clears once Step 4 lands.

- [ ] **Step 3: Add the `pending` stale badge branch**

In `app/routes/jobs.py`, in `_stale_badge` (around line 135), add a branch right after the `trash` check:

```python
    if job["status"] == "accepted":
        return {"label": "Moved to Accepted", "href": f"/jobs?status=accepted{anchor}"}
    if job["status"] == "pending":
        return {"label": "Moved to Pending", "href": f"/jobs?status=pending{anchor}"}
    if job["status"] == "rejected":
        return {"label": "Moved to Rejected", "href": f"/jobs?status=rejected{anchor}"}
    if job["status"] == "trash":
        return {"label": "Moved to Trash", "href": f"/jobs?status=trash{anchor}"}
```

- [ ] **Step 4: Add `pending` to the search seed tabs**

In `app/routes/jobs.py` (around line 49), change:

```python
_SEARCH_SEED_TABS = ("new", "lead", "accepted", "rejected")
```

to:

```python
_SEARCH_SEED_TABS = ("new", "lead", "accepted", "pending", "rejected")
```

- [ ] **Step 5: Add `pending` to the status-change revisit trigger, single and bulk**

In `app/routes/jobs.py`, in `job_feedback` (around line 333):

```python
    if status in ("accepted", "pending", "rejected"):
```

And in `job_bulk_feedback` (around line 676):

```python
    if status in ("accepted", "pending", "rejected"):
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest tests/test_routes_jobs.py -k "pending" -v`
Expected: all PASS.

- [ ] **Step 7: Run the full routes test file to check for regressions**

Run: `python -m pytest tests/test_routes_jobs.py -v`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add app/routes/jobs.py tests/test_routes_jobs.py
git commit -m "feat: recognize pending status in stale badges, search, and revisit trigger"
```

---

### Task 6: `pending` in the onboarding checklist (routes/home.py)

**Files:**
- Modify: `app/routes/home.py:31`
- Test: `tests/test_routes_home.py`

**Interfaces:**
- Consumes: `get_job_counts(...)["pending"]` (Task 3).
- Produces: the "Review your first job" checklist item is `done` when `counts["pending"] > 0` too, even with `accepted`/`rejected`/`trash` all still zero.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_routes_home.py` right after `test_home_full_setup_shows_actionable_block` (around line 100):

```python
def test_home_checklist_review_job_done_via_pending_only(client, conn, monkeypatch, tmp_path):
    _use_test_db(conn)
    _use_valid_config(monkeypatch, tmp_path)
    q.upsert_profile(conn, "Python engineer")
    q.insert_scenario(conn, "Remote ML", "")
    source_id = q.insert_source(conn, "finn.no", "https://finn.no", "generic_listing")

    job_id = q.insert_job(conn, source_id=source_id, url="http://finn.no/1", title="ML Eng", company="Acme", raw_text="r")
    q.update_job_pipeline(conn, job_id, simplified_content="c", content_type="job_posting", summary="s")
    q.update_job_feedback(conn, job_id, "pending", "applied, waiting to hear back")

    resp = client.get("/")
    assert resp.status_code == 200
    assert '<a href="/jobs">✓ Review your first job</a>' in resp.text
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_routes_home.py -k pending_only -v`
Expected: FAIL — the checklist item still shows `○` since `counts["pending"]` isn't part of the done-check.

- [ ] **Step 3: Add `pending` to the done-check**

In `app/routes/home.py` (line 31), change:

```python
                "done": (counts["accepted"] + counts["rejected"] + counts["trash"]) > 0,
```

to:

```python
                "done": (counts["accepted"] + counts["pending"] + counts["rejected"] + counts["trash"]) > 0,
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_routes_home.py -k pending_only -v`
Expected: PASS.

- [ ] **Step 5: Run the full home test file to check for regressions**

Run: `python -m pytest tests/test_routes_home.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add app/routes/home.py tests/test_routes_home.py
git commit -m "feat: count pending jobs toward the onboarding checklist"
```

---

### Task 7: "Mark pending" button, status pill, and tab in the UI

**Files:**
- Modify: `app/templates/jobs/_feedback.html`, `app/templates/jobs/_bulk_actions.html`, `app/templates/jobs/_macros.html`, `app/templates/jobs/_content.html`, `app/templates/base.html`
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: `get_job_counts(...)["pending"]` (Task 3), `job["status"] == "pending"` (Task 1).
- Produces: rendered "Mark pending" buttons (single-job form and bulk bar), a `status-pill-pending` pill, and a "Pending" tab with count — nothing downstream depends on these beyond the browser/tests.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_routes_jobs.py` right after `test_job_list_bulk_bar_has_actions_and_no_scenario_select` (around line 1315):

```python
def test_job_list_bulk_bar_has_mark_pending_button(client, conn):
    _seed(conn)
    resp = client.get("/jobs")
    assert resp.status_code == 200
    assert 'form="bulk-form" name="status" value="pending"' in resp.text
    assert ">Mark pending<" in resp.text
```

Add right after `test_job_list_shows_status_badge_for_accepted_rejected_trash` (around line 1454):

```python
def test_job_list_shows_status_badge_for_pending(client, conn):
    sid = q.insert_source(conn, "finn.no", "https://finn.no", "generic_listing")
    jid_pending = q.insert_job(conn, source_id=sid, url="http://finn.no/job/1", title="Pending Job", company="Acme", raw_text="r")
    q.update_job_feedback(conn, jid_pending, "pending", "")

    resp = client.get("/jobs?status=pending")
    assert '<span class="status-pill status-pill-pending">Pending</span>' in resp.text
```

Add right after `test_job_list_filter_bar_shows_counts` (around line 82):

```python
def test_job_list_filter_bar_shows_pending_count(client, conn):
    _seed(conn)
    resp = client.get("/jobs")
    assert resp.status_code == 200
    assert '<span class="tab-count" id="count-pending">0</span>' in resp.text
```

Add a single-job organize-actions button test right after `test_job_expand_feedback_form_has_no_redirect_field` (line 788-792):

```python
def test_job_expand_organize_actions_include_mark_pending(client, conn):
    sid, jid, scenario_id = _seed(conn)
    resp = client.get(f"/jobs/{jid}/expand")
    assert resp.status_code == 200
    assert 'name="status" value="pending"' in resp.text
    assert ">Mark pending<" in resp.text
```

**Pre-existing test needs updating too:** `test_tab_checkbox_link_toggles_one_status` (around line 2594-2600) asserts an exact count of rendered tab markers — `html.count('class="tab-check"') == 6` — one per entry in `_content.html`'s `tab_defs`. Adding the "Pending" tab in Step 6 below makes this 7:

```python
def test_tab_checkbox_link_toggles_one_status(client, conn):
    _seed(conn)
    html = client.get("/jobs?status=new").text
    # every tab renders its marker, even in single-status mode (no hover reveal)
    assert html.count('class="tab-check"') == 7
    # an "add Accepted to the view" control pointing at status=new,accepted
    assert "status=new%2Caccepted" in html or "status=new,accepted" in html
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_routes_jobs.py -k "pending or toggled_one_status or seeded_scope" -v`
Expected: the four new tests FAIL — no "Mark pending" button, no `status-pill-pending` class, no `count-pending` tab-count span exist yet. `test_tab_checkbox_link_toggles_one_status` also FAILs (it now expects 7 tab markers but the template still renders 6). `test_search_tab_bar_shows_seeded_scope` also FAILs (it was just bumped to expect 5 active tab-items, but `tab_defs` still has no `"pending"` entry so it's still 4). Both clear once Step 6 lands.

- [ ] **Step 3: Add the "Mark pending" button to the single-job organize actions**

In `app/templates/jobs/_feedback.html` (around line 127), change:

```html
        <button type="submit" name="status" value="accepted" class="btn btn-accept">Accept</button>
        <button type="submit" name="status" value="rejected" class="btn btn-reject" title="Does not match your criteria.">Reject</button>
```

to:

```html
        <button type="submit" name="status" value="accepted" class="btn btn-accept">Accept</button>
        <button type="submit" name="status" value="pending" class="btn">Mark pending</button>
        <button type="submit" name="status" value="rejected" class="btn btn-reject" title="Does not match your criteria.">Reject</button>
```

- [ ] **Step 4: Add the "Mark pending" button to the bulk actions bar**

In `app/templates/jobs/_bulk_actions.html` (lines 2-3), change:

```html
  <button type="submit" form="bulk-form" name="status" value="accepted" class="btn btn-accept">Accept</button>
  <button type="submit" form="bulk-form" name="status" value="rejected" class="btn btn-reject" title="Does not match your criteria.">Reject</button>
```

to:

```html
  <button type="submit" form="bulk-form" name="status" value="accepted" class="btn btn-accept">Accept</button>
  <button type="submit" form="bulk-form" name="status" value="pending" class="btn">Mark pending</button>
  <button type="submit" form="bulk-form" name="status" value="rejected" class="btn btn-reject" title="Does not match your criteria.">Reject</button>
```

- [ ] **Step 5: Add the `pending` status pill**

In `app/templates/jobs/_macros.html` (around line 13), change:

```jinja
{% if job.status == "accepted" %}<span class="status-pill status-pill-accepted">Accepted</span>
{% elif job.status == "rejected" %}<span class="status-pill status-pill-rejected">Rejected</span>
```

to:

```jinja
{% if job.status == "accepted" %}<span class="status-pill status-pill-accepted">Accepted</span>
{% elif job.status == "pending" %}<span class="status-pill status-pill-pending">Pending</span>
{% elif job.status == "rejected" %}<span class="status-pill status-pill-rejected">Rejected</span>
```

- [ ] **Step 6: Add the "Pending" tab**

In `app/templates/jobs/_content.html` (lines 2-9), change:

```jinja
{% set tab_defs = [
  ("new", "New Jobs", "count-new", counts.new, "Job postings awaiting your decision — passed at least one scenario's relevance gate."),
  ("lead", "New Leads", "count-lead", counts.lead, "Leads awaiting your decision — mentions of a possible opportunity, not full postings."),
  ("accepted", "Accepted", "count-accepted", counts.accepted, "Jobs and leads you've accepted."),
  ("rejected", "Rejected", "count-rejected", counts.rejected, "Jobs and leads you've rejected."),
  ("not_relevant", "Not relevant", "count-not_relevant", counts.not_relevant, "Real job postings that didn't pass any scenario's relevance gate — not a match for your current scenarios."),
  ("trash", "Trash", "count-trash", counts.trash, "Unusable postings (expired, spam, duplicate, wrong content) — kept temporarily."),
] %}
```

to:

```jinja
{% set tab_defs = [
  ("new", "New Jobs", "count-new", counts.new, "Job postings awaiting your decision — passed at least one scenario's relevance gate."),
  ("lead", "New Leads", "count-lead", counts.lead, "Leads awaiting your decision — mentions of a possible opportunity, not full postings."),
  ("accepted", "Accepted", "count-accepted", counts.accepted, "Jobs and leads you've accepted."),
  ("pending", "Pending", "count-pending", counts.pending, "Jobs you've applied to and are waiting to hear back on."),
  ("rejected", "Rejected", "count-rejected", counts.rejected, "Jobs and leads you've rejected."),
  ("not_relevant", "Not relevant", "count-not_relevant", counts.not_relevant, "Real job postings that didn't pass any scenario's relevance gate — not a match for your current scenarios."),
  ("trash", "Trash", "count-trash", counts.trash, "Unusable postings (expired, spam, duplicate, wrong content) — kept temporarily."),
] %}
```

**Pre-existing test needs updating too (correction from Task 5):** Task 5 found that `test_search_tab_bar_shows_seeded_scope` (`tests/test_routes_jobs.py`, around line 2640) could NOT be bumped from 4 to 5 active tab-items at that point, because the `active` class in `_content.html`'s loop (`{% for tab, label, count_id, count, tip in tab_defs %}` / `{% set active = tab in scope_tabs %}`) only ever considers tabs that have an entry in `tab_defs` — and before this step, `tab_defs` had no `"pending"` entry, so "pending" being in `scope_tabs` was inert (no tab-item rendered for it at all). Task 5 correctly left that test's active-count at `4` with an explanatory comment. Now that this step adds the `"pending"` entry to `tab_defs`, "pending" gets an actual tab-item, and it IS in `scope_tabs` (via `_SEARCH_SEED_TABS`, which Task 5 already updated) — so the count becomes 5. Update the test:

```python
def test_search_tab_bar_shows_seeded_scope(client, conn):
    sid = q.insert_source(conn, "s", "https://s", "generic_listing")
    _seed_searchable(conn, sid, "http://s/1", "Kappa Engineer")
    html = client.get("/jobs?q=kappa").text
    # the seeded buckets (new/lead/accepted/pending/rejected) render active --
    # "pending" now has a tab_defs entry (this step) and was already in
    # _SEARCH_SEED_TABS (Task 5), so it finally renders as an active tab-item.
    assert html.count('class="tab-item active"') == 5
    # a checkbox toggle to add Trash carries the whole seeded scope + the query
    assert ("status=new%2Clead%2Caccepted%2Cpending%2Crejected%2Ctrash" in html
            or "status=new,lead,accepted,pending,rejected,trash" in html)
    assert "q=kappa" in html
```

- [ ] **Step 7: Add the `pending` status-pill CSS**

In `app/templates/base.html` (around line 573), change:

```css
    .status-pill-accepted { background: var(--success-tint); color: var(--success-strong); }
    .status-pill-rejected { background: var(--alert-tint); color: var(--alert); }
```

to:

```css
    .status-pill-accepted { background: var(--success-tint); color: var(--success-strong); }
    .status-pill-pending { background: var(--warning-tint); color: var(--warning); }
    .status-pill-rejected { background: var(--alert-tint); color: var(--alert); }
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `python -m pytest tests/test_routes_jobs.py -k "pending or toggled_one_status or seeded_scope" -v`
Expected: all PASS.

- [ ] **Step 9: Run the full test suite to check for regressions**

Run: `python -m pytest -v`
Expected: all PASS, no failures anywhere in the suite.

- [ ] **Step 10: Commit**

```bash
git add app/templates/jobs/_feedback.html app/templates/jobs/_bulk_actions.html \
        app/templates/jobs/_macros.html app/templates/jobs/_content.html app/templates/base.html \
        tests/test_routes_jobs.py
git commit -m "feat: add Mark pending button, status pill, and tab to the jobs UI"
```

---

## Manual verification (UI-facing — do this after Task 7)

This feature is UI-facing, so per CLAUDE.md, after Task 7 lands: start the dev server against a throwaway copy of `job-seek.db` (use the `run-dev-server` skill), open `/jobs`, and check:

1. The tab bar shows a "Pending" tab between "Accepted" and "Rejected", with the right count.
2. On any job's organize actions (single-job expanded view, and via bulk-select), the button order reads Accept → Mark pending → Reject → Trash.
3. Clicking "Mark pending" moves the job to the Pending tab and shows the amber "Pending" status pill.
4. From a non-Pending tab, marking a job pending shows a "Moved to Pending" stale badge with a working link to the Pending tab.

Leave the dev server running and hand the URL to the user for their own check before offering to merge.
