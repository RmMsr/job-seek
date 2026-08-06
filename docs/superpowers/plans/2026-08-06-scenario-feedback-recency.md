# Scenario Feedback Recency & Handled/Unhandled Counts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bound `propose_criteria`'s feedback input to a 30-day/20-row recency window, replace job-id-based handled-marking with a timestamp-anchor sweep, and surface unhandled/handled vote counts on the scenarios page.

**Architecture:** Pure additive/behavioral change to the existing `scenario_feedback` table (no schema migration). `app/db/queries.py` gains a `days` window on the existing recency query, a new anchor-lookup function, a rewritten `mark_feedback_handled`, and a new counts function. `app/routes/scenarios.py` swaps job-id plumbing for anchor plumbing between the refine and accept routes. Two templates gain a hidden field rename and two count badges.

**Tech Stack:** Python, FastAPI, sqlite3 (stdlib), pytest, httpx `TestClient`, Jinja2.

## Global Constraints

- Recency window: 30 days, row cap: 20 (both hardcoded defaults, no UI to configure — per spec §"Out of scope").
- No schema migration — `scenario_feedback.created_at`/`direction`/`handled_at` already exist.
- `get_recent_feedback_job_ids` is deleted entirely (spec §1); nothing should reference it after this plan.
- Follow existing project conventions: `_rows_to_dicts`/`_row_to_dict` helpers, `conn.commit()` after writes, HTMX partial-swap routes, inline `<span class="tag">` styling already used elsewhere in `scenarios/index.html` and `_header.html`.

Reference spec: `docs/superpowers/specs/2026-08-06-scenario-feedback-recency-design.md`

---

### Task 1: Recency window on `_recent_scenario_feedback_rows` + `get_recent_feedback_notes`

**Files:**
- Modify: `app/db/queries.py:400-410` (`_recent_scenario_feedback_rows`), `app/db/queries.py:431-435` (`get_recent_feedback_notes`)
- Test: `tests/test_queries.py`

**Interfaces:**
- Produces: `_recent_scenario_feedback_rows(conn, scenario_id: int, limit: int, days: int = 30) -> list[dict]` — each row now also has a `created_at` key (str, sqlite `datetime('now')` format e.g. `"2026-08-06 12:00:00"`). `get_recent_feedback_notes(conn, scenario_id, limit: int = 20, days: int = 30) -> list[dict]` — return shape unchanged (`{"direction": ..., "note": ...}`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_queries.py` (near the existing `test_get_recent_feedback_notes_excludes_handled` at line 363):

```python
def test_get_recent_feedback_notes_excludes_feedback_older_than_window(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T1", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, j1, scenario_id, "too junior", "lower")
    conn.execute(
        "UPDATE scenario_feedback SET created_at = datetime('now', '-40 days') WHERE job_id = ? AND scenario_id = ?",
        (j1, scenario_id),
    )
    conn.commit()
    assert q.get_recent_feedback_notes(conn, scenario_id) == []


def test_get_recent_feedback_notes_includes_feedback_within_window(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T1", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, j1, scenario_id, "too junior", "lower")
    conn.execute(
        "UPDATE scenario_feedback SET created_at = datetime('now', '-20 days') WHERE job_id = ? AND scenario_id = ?",
        (j1, scenario_id),
    )
    conn.commit()
    assert q.get_recent_feedback_notes(conn, scenario_id) == [{"direction": "lower", "note": "too junior"}]


def test_get_recent_feedback_notes_row_cap_applies_within_window(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")
    for i in range(3):
        j = q.insert_job(conn, source_id=source_id, url=f"http://job/{i}", title="T", company="C", raw_text="r")
        q.upsert_scenario_feedback(conn, j, scenario_id, f"note {i}", "lower")
    notes = q.get_recent_feedback_notes(conn, scenario_id, limit=2)
    assert len(notes) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_queries.py -k "excludes_feedback_older_than_window or includes_feedback_within_window or row_cap_applies_within_window" -v`
Expected: first test FAILs (`created_at` filter doesn't exist yet — the old row is still returned), other two PASS already (they don't yet depend on new behavior). Confirm the first one fails before proceeding.

- [ ] **Step 3: Implement the recency window**

In `app/db/queries.py`, replace `_recent_scenario_feedback_rows` and `get_recent_feedback_notes`:

```python
def _recent_scenario_feedback_rows(conn: sqlite3.Connection, scenario_id: int, limit: int, days: int = 30) -> list[dict]:
    return _rows_to_dicts(
        conn.execute(
            """
            SELECT job_id, note, direction, created_at FROM scenario_feedback
            WHERE scenario_id = ? AND direction IS NOT NULL AND handled_at IS NULL
              AND created_at >= datetime('now', ? || ' days')
            ORDER BY created_at DESC LIMIT ?
            """,
            (scenario_id, f'-{days}', limit),
        ).fetchall()
    )


def get_recent_feedback_notes(conn: sqlite3.Connection, scenario_id: int, limit: int = 20, days: int = 30) -> list[dict]:
    return [
        {"direction": r["direction"], "note": r["note"]}
        for r in _recent_scenario_feedback_rows(conn, scenario_id, limit, days)
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_queries.py -k "recent_feedback_notes" -v`
Expected: PASS (all `get_recent_feedback_notes*` tests, including the pre-existing ones from before this plan).

- [ ] **Step 5: Commit**

```bash
git add app/db/queries.py tests/test_queries.py
git commit -m "feat: bound scenario feedback recency to a 30-day window"
```

---

### Task 2: Replace `get_recent_feedback_job_ids` with `get_recent_feedback_anchor`

**Files:**
- Modify: `app/db/queries.py:438-439` (delete `get_recent_feedback_job_ids`, add `get_recent_feedback_anchor`)
- Test: `tests/test_queries.py`

**Interfaces:**
- Consumes: `_recent_scenario_feedback_rows(conn, scenario_id, limit, days=30) -> list[dict]` (Task 1, now includes `created_at`).
- Produces: `get_recent_feedback_anchor(conn, scenario_id: int, limit: int = 20, days: int = 30) -> str | None` — the newest `created_at` among the in-window/in-cap rows, or `None` if there are none.

- [ ] **Step 1: Write the failing tests**

Replace the existing `test_get_recent_feedback_job_ids` test (line 372-377 of `tests/test_queries.py`) with:

```python
def test_get_recent_feedback_anchor_is_newest_created_at(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T1", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, j1, scenario_id, "too junior", "lower")
    row = conn.execute(
        "SELECT created_at FROM scenario_feedback WHERE job_id = ? AND scenario_id = ?", (j1, scenario_id)
    ).fetchone()
    assert q.get_recent_feedback_anchor(conn, scenario_id) == row["created_at"]


def test_get_recent_feedback_anchor_none_when_no_recent_feedback(conn):
    scenario_id = q.insert_scenario(conn, "A", "")
    assert q.get_recent_feedback_anchor(conn, scenario_id) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_queries.py -k "recent_feedback_anchor" -v`
Expected: FAIL with `AttributeError: module 'app.db.queries' has no attribute 'get_recent_feedback_anchor'`.

- [ ] **Step 3: Implement**

In `app/db/queries.py`, delete `get_recent_feedback_job_ids` and add:

```python
def get_recent_feedback_anchor(conn: sqlite3.Connection, scenario_id: int, limit: int = 20, days: int = 30) -> str | None:
    rows = _recent_scenario_feedback_rows(conn, scenario_id, limit, days)
    return rows[0]["created_at"] if rows else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_queries.py -k "recent_feedback_anchor" -v`
Expected: PASS.

Note: `tests/test_queries.py` and `app/routes/scenarios.py` still reference the now-deleted `get_recent_feedback_job_ids` at this point (line 302, 330, 388, 391, 406-407 of `tests/test_queries.py` / `tests/test_routes_scenarios.py`, and lines 117, 235 of `app/routes/scenarios.py`) — this is expected; those call sites are rewritten in Tasks 3-5. Do not attempt to fix them in this task; running the *full* suite now will show failures elsewhere, which is fine. Only the `-k "recent_feedback_anchor"` subset needs to pass here.

- [ ] **Step 5: Commit**

```bash
git add app/db/queries.py tests/test_queries.py
git commit -m "feat: add get_recent_feedback_anchor, remove get_recent_feedback_job_ids"
```

---

### Task 3: Anchor-based `mark_feedback_handled` sweep

**Files:**
- Modify: `app/db/queries.py:442-451`
- Test: `tests/test_queries.py`

**Interfaces:**
- Produces: `mark_feedback_handled(conn: sqlite3.Connection, scenario_id: int, anchor: str | None) -> None` — replaces the old `(conn, scenario_id, job_ids: list[int])` signature entirely. No-op when `anchor` is falsy.

- [ ] **Step 1: Write the failing tests**

Replace `test_get_recent_feedback_notes_excludes_handled` (line 363-369), `test_mark_feedback_handled_scoped_to_one_scenario` (line 380-391), and `test_upsert_scenario_feedback_resets_handled_state`'s call to `mark_feedback_handled` (search for `mark_feedback_handled` in the file and update every call site to the new signature). Concretely:

```python
def test_get_recent_feedback_notes_excludes_handled(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T1", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, j1, scenario_id, "too junior", "lower")
    anchor = q.get_recent_feedback_anchor(conn, scenario_id)
    q.mark_feedback_handled(conn, scenario_id, anchor)
    assert q.get_recent_feedback_notes(conn, scenario_id) == []


def test_mark_feedback_handled_scoped_to_one_scenario(conn):
    # A job can carry independent gate feedback for two scenarios — handling
    # one scenario's proposals must not clear the other's pending feedback.
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_a = q.insert_scenario(conn, "A", "")
    scenario_b = q.insert_scenario(conn, "B", "")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T1", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, j1, scenario_a, "too junior", "lower")
    q.upsert_scenario_feedback(conn, j1, scenario_b, "should count here too", "higher")
    anchor_a = q.get_recent_feedback_anchor(conn, scenario_a)
    q.mark_feedback_handled(conn, scenario_a, anchor_a)
    assert q.get_recent_feedback_notes(conn, scenario_a) == []
    assert q.get_recent_feedback_notes(conn, scenario_b) == [{"direction": "higher", "note": "should count here too"}]


def test_mark_feedback_handled_sweeps_rows_beyond_the_cap(conn):
    # The LLM only ever sees the 20 most recent rows, but accepting that
    # batch's proposals should clear every unhandled row up to that point,
    # not just the 20 that were sampled — otherwise stragglers beyond the
    # cap can never be marked handled.
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")
    job_ids = []
    for i in range(3):
        j = q.insert_job(conn, source_id=source_id, url=f"http://job/{i}", title="T", company="C", raw_text="r")
        q.upsert_scenario_feedback(conn, j, scenario_id, f"note {i}", "lower")
        job_ids.append(j)

    anchor = q.get_recent_feedback_anchor(conn, scenario_id, limit=1)  # only the newest row is "in the batch"
    q.mark_feedback_handled(conn, scenario_id, anchor)

    assert q.get_recent_feedback_notes(conn, scenario_id, limit=10) == []


def test_mark_feedback_handled_leaves_rows_created_after_anchor(conn):
    # A vote cast after refine was triggered (but before its proposals were
    # accepted) wasn't part of what the LLM saw, so it must stay unhandled.
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T1", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, j1, scenario_id, "too junior", "lower")
    anchor = q.get_recent_feedback_anchor(conn, scenario_id)

    j2 = q.insert_job(conn, source_id=source_id, url="http://job/2", title="T2", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, j2, scenario_id, "should count higher", "higher")

    q.mark_feedback_handled(conn, scenario_id, anchor)

    assert q.get_recent_feedback_notes(conn, scenario_id) == [{"direction": "higher", "note": "should count higher"}]


def test_mark_feedback_handled_none_anchor_is_noop(conn):
    scenario_id = q.insert_scenario(conn, "A", "")
    q.mark_feedback_handled(conn, scenario_id, None)  # must not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_queries.py -k "mark_feedback_handled" -v`
Expected: FAIL — `mark_feedback_handled` still expects a `list[int]`, so passing a string anchor either raises (iterating over string as `job_ids`, building malformed placeholders) or silently does the wrong thing. Confirm at least one assertion fails.

- [ ] **Step 3: Implement**

In `app/db/queries.py`, replace `mark_feedback_handled`:

```python
def mark_feedback_handled(conn: sqlite3.Connection, scenario_id: int, anchor: str | None) -> None:
    if not anchor:
        return
    conn.execute(
        """UPDATE scenario_feedback SET handled_at = datetime('now')
        WHERE scenario_id = ? AND direction IS NOT NULL AND handled_at IS NULL AND created_at <= ?""",
        (scenario_id, anchor),
    )
    conn.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_queries.py -k "mark_feedback_handled or recent_feedback_notes or recent_feedback_anchor" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/db/queries.py tests/test_queries.py
git commit -m "feat: mark_feedback_handled sweeps by timestamp anchor instead of job-id list"
```

---

### Task 4: `get_recent_feedback_counts`

**Files:**
- Modify: `app/db/queries.py` (add function near `get_recent_feedback_notes`)
- Test: `tests/test_queries.py`

**Interfaces:**
- Consumes: `_recent_scenario_feedback_rows` (Task 1).
- Produces: `get_recent_feedback_counts(conn, scenario_id: int, limit: int = 20, days: int = 30) -> dict` with exactly the keys `unhandled_higher`, `unhandled_lower`, `handled_higher`, `handled_lower` (all `int`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_queries.py`:

```python
def test_get_recent_feedback_counts_splits_unhandled_by_direction(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T1", company="C", raw_text="r")
    j2 = q.insert_job(conn, source_id=source_id, url="http://job/2", title="T2", company="C", raw_text="r")
    j3 = q.insert_job(conn, source_id=source_id, url="http://job/3", title="T3", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, j1, scenario_id, "a", "higher")
    q.upsert_scenario_feedback(conn, j2, scenario_id, "b", "higher")
    q.upsert_scenario_feedback(conn, j3, scenario_id, "c", "lower")

    counts = q.get_recent_feedback_counts(conn, scenario_id)

    assert counts == {"unhandled_higher": 2, "unhandled_lower": 1, "handled_higher": 0, "handled_lower": 0}


def test_get_recent_feedback_counts_splits_handled_by_direction(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T1", company="C", raw_text="r")
    j2 = q.insert_job(conn, source_id=source_id, url="http://job/2", title="T2", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, j1, scenario_id, "a", "higher")
    q.upsert_scenario_feedback(conn, j2, scenario_id, "b", "lower")
    anchor = q.get_recent_feedback_anchor(conn, scenario_id)
    q.mark_feedback_handled(conn, scenario_id, anchor)

    counts = q.get_recent_feedback_counts(conn, scenario_id)

    assert counts == {"unhandled_higher": 0, "unhandled_lower": 0, "handled_higher": 1, "handled_lower": 1}


def test_get_recent_feedback_counts_excludes_rows_older_than_window(conn):
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T1", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, j1, scenario_id, "a", "higher")
    conn.execute(
        "UPDATE scenario_feedback SET created_at = datetime('now', '-40 days') WHERE job_id = ? AND scenario_id = ?",
        (j1, scenario_id),
    )
    conn.commit()

    counts = q.get_recent_feedback_counts(conn, scenario_id)

    assert counts == {"unhandled_higher": 0, "unhandled_lower": 0, "handled_higher": 0, "handled_lower": 0}


def test_get_recent_feedback_counts_unhandled_matches_notes_row_count(conn):
    # The displayed unhandled count must be mechanically identical to what
    # propose_criteria actually receives.
    source_id = q.insert_source(conn, "s", "http://x", "http")
    scenario_id = q.insert_scenario(conn, "A", "")
    for i in range(25):
        j = q.insert_job(conn, source_id=source_id, url=f"http://job/{i}", title="T", company="C", raw_text="r")
        q.upsert_scenario_feedback(conn, j, scenario_id, f"note {i}", "higher")

    counts = q.get_recent_feedback_counts(conn, scenario_id)
    notes = q.get_recent_feedback_notes(conn, scenario_id)

    assert counts["unhandled_higher"] == len(notes) == 20  # capped
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_queries.py -k "get_recent_feedback_counts" -v`
Expected: FAIL with `AttributeError: module 'app.db.queries' has no attribute 'get_recent_feedback_counts'`.

- [ ] **Step 3: Implement**

In `app/db/queries.py`, add near `get_recent_feedback_notes`:

```python
def get_recent_feedback_counts(conn: sqlite3.Connection, scenario_id: int, limit: int = 20, days: int = 30) -> dict:
    unhandled = _recent_scenario_feedback_rows(conn, scenario_id, limit, days)
    handled = _rows_to_dicts(
        conn.execute(
            """
            SELECT direction FROM scenario_feedback
            WHERE scenario_id = ? AND direction IS NOT NULL AND handled_at IS NOT NULL
              AND created_at >= datetime('now', ? || ' days')
            """,
            (scenario_id, f'-{days}'),
        ).fetchall()
    )
    return {
        "unhandled_higher": sum(1 for r in unhandled if r["direction"] == "higher"),
        "unhandled_lower": sum(1 for r in unhandled if r["direction"] == "lower"),
        "handled_higher": sum(1 for r in handled if r["direction"] == "higher"),
        "handled_lower": sum(1 for r in handled if r["direction"] == "lower"),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_queries.py -k "get_recent_feedback_counts" -v`
Expected: PASS.

- [ ] **Step 5: Run the full query test file**

Run: `pytest tests/test_queries.py -v`
Expected: PASS. (This confirms Tasks 1-4 haven't broken any other query test; `app/routes/scenarios.py` still uses the old `get_recent_feedback_job_ids`/job-id `mark_feedback_handled` calls, but that file isn't touched or tested by `test_queries.py`.)

- [ ] **Step 6: Commit**

```bash
git add app/db/queries.py tests/test_queries.py
git commit -m "feat: add get_recent_feedback_counts split by handled/unhandled and direction"
```

---

### Task 5: Route + template wiring for the anchor hand-off (refine → accept)

**Files:**
- Modify: `app/routes/scenarios.py:20-23` (delete `_parse_job_ids`), `app/routes/scenarios.py:102-132` (`refine_all_scenarios`), `app/routes/scenarios.py:225-255` (`refine_criteria`), `app/routes/scenarios.py:258-284` (`accept_proposals`)
- Modify: `app/templates/scenarios/_proposals.html:5`
- Test: `tests/test_routes_scenarios.py`

**Interfaces:**
- Consumes: `q.get_recent_feedback_anchor(conn, scenario_id, limit=20, days=30) -> str | None` (Task 2), `q.mark_feedback_handled(conn, scenario_id, anchor: str | None) -> None` (Task 3).
- Produces: `_proposals.html` template now expects a `feedback_anchor: str | None` render variable instead of `feedback_job_ids: str`; the rendered hidden input is `name="feedback_anchor"`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_routes_scenarios.py`, replace every test that references `get_recent_feedback_job_ids` or `feedback_job_ids`:

Replace `test_refine_alone_does_not_mark_feedback_handled` (line 287-302):

```python
def test_refine_alone_does_not_mark_feedback_handled(client, conn):
    # Feedback stays "live" until you actually act on a proposal derived
    # from it — merely running refine and looking at the suggestions
    # shouldn't consume it.
    sid = q.insert_scenario(conn, "Remote ML", "")
    q.insert_criterion(conn, sid, "Must be remote", "must")
    source_id = q.insert_source(conn, "s", "http://x", "http")
    job_id = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_pipeline(conn, job_id, simplified_content="", content_type="job_posting")
    q.upsert_scenario_feedback(conn, job_id, sid, "too junior", "lower")

    proposals = [CriterionProposal(text="Must be senior", weight="must", action="add")]
    with patch("app.routes.scenarios.propose_criteria", return_value=proposals):
        client.post(f"/scenarios/{sid}/refine")

    assert q.get_recent_feedback_notes(conn, sid) == [{"direction": "lower", "note": "too junior"}]
```

Replace `test_refine_embeds_feedback_job_ids_in_apply_form` (line 305-315):

```python
def test_refine_embeds_feedback_anchor_in_apply_form(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    source_id = q.insert_source(conn, "s", "http://x", "http")
    job_id = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_pipeline(conn, job_id, simplified_content="", content_type="job_posting")
    q.upsert_scenario_feedback(conn, job_id, sid, "too junior", "lower")
    anchor = q.get_recent_feedback_anchor(conn, sid)

    proposals = [CriterionProposal(text="Must be senior", weight="must", action="add")]
    with patch("app.routes.scenarios.propose_criteria", return_value=proposals):
        resp = client.post(f"/scenarios/{sid}/refine")
    assert f'name="feedback_anchor" value="{anchor}"' in resp.text
```

Replace `test_manual_add_criterion_does_not_mark_feedback_handled` (line 318-330):

```python
def test_manual_add_criterion_does_not_mark_feedback_handled(client, conn):
    # The always-instant manual add-form at the bottom of the criteria list
    # is unrelated to the LLM-suggestion batch-apply flow and carries no
    # feedback_anchor — it must never mark anything handled.
    sid = q.insert_scenario(conn, "Remote ML", "")
    source_id = q.insert_source(conn, "s", "http://x", "http")
    job_id = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_pipeline(conn, job_id, simplified_content="", content_type="job_posting")
    q.upsert_scenario_feedback(conn, job_id, sid, "too junior", "lower")

    resp = client.post(f"/scenarios/{sid}/criteria", data={"text": "Must be senior", "weight": "must"})
    assert resp.status_code == 200
    assert q.get_recent_feedback_notes(conn, sid) == [{"direction": "lower", "note": "too junior"}]
```

Replace `test_apply_batch_marks_feedback_handled_even_when_all_rows_skipped` (line 373-389):

```python
def test_apply_batch_marks_feedback_handled_even_when_all_rows_skipped(client, conn):
    # One atomic submit reviews the whole batch, regardless of which
    # individual rows were applied — even an all-skip submission means the
    # batch was looked at and consciously rejected.
    sid = q.insert_scenario(conn, "Remote ML", "")
    source_id = q.insert_source(conn, "s", "http://x", "http")
    job_id = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_pipeline(conn, job_id, simplified_content="", content_type="job_posting")
    q.upsert_scenario_feedback(conn, job_id, sid, "too junior", "lower")
    anchor = q.get_recent_feedback_anchor(conn, sid)

    resp = client.post(
        f"/scenarios/{sid}/refine/accept",
        data={"kind_0": "add", "text_0": "Must be senior", "weight_0": "must", "feedback_anchor": anchor},
    )
    assert resp.status_code == 200
    assert q.get_recent_feedback_notes(conn, sid) == []
    assert q.get_criteria(conn, sid) == []
```

Replace `test_apply_batch_only_marks_this_scenarios_feedback_handled` (line 392-407):

```python
def test_apply_batch_only_marks_this_scenarios_feedback_handled(client, conn):
    sid_a = q.insert_scenario(conn, "Remote ML", "")
    sid_b = q.insert_scenario(conn, "Robotics", "")
    source_id = q.insert_source(conn, "s", "http://x", "http")
    job_id = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_pipeline(conn, job_id, simplified_content="", content_type="job_posting")
    q.upsert_scenario_feedback(conn, job_id, sid_a, "too junior", "lower")
    q.upsert_scenario_feedback(conn, job_id, sid_b, "should count here too", "higher")
    anchor_a = q.get_recent_feedback_anchor(conn, sid_a)

    client.post(
        f"/scenarios/{sid_a}/refine/accept",
        data={"feedback_anchor": anchor_a},
    )

    assert q.get_recent_feedback_notes(conn, sid_a) == []
    assert q.get_recent_feedback_notes(conn, sid_b) == [{"direction": "higher", "note": "should count here too"}]
```

Replace `test_apply_batch_ignores_malformed_feedback_job_ids` (line 410-416):

```python
def test_apply_batch_ignores_malformed_feedback_anchor(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    resp = client.post(
        f"/scenarios/{sid}/refine/accept",
        data={"feedback_anchor": "1; DROP TABLE jobs"},
    )
    assert resp.status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_routes_scenarios.py -v`
Expected: multiple FAILs — routes still send/expect `feedback_job_ids`, `accept_proposals` still calls the old `mark_feedback_handled(conn, scenario_id, list[int])` signature (which will now raise or behave incorrectly against the Task 3 implementation), and `_proposals.html` still renders `feedback_job_ids`.

- [ ] **Step 3: Implement route + template changes**

In `app/routes/scenarios.py`:

1. Delete `_parse_job_ids` (lines 20-23).
2. In `refine_all_scenarios` (around line 117-126), replace:
   ```python
   job_ids = q.get_recent_feedback_job_ids(conn, scenario["id"])
   notes = q.get_recent_feedback_notes(conn, scenario["id"])
   ```
   with:
   ```python
   anchor = q.get_recent_feedback_anchor(conn, scenario["id"])
   notes = q.get_recent_feedback_notes(conn, scenario["id"])
   ```
   and the template render call's `feedback_job_ids=",".join(str(i) for i in job_ids)` becomes `feedback_anchor=anchor`.
3. In `refine_criteria` (around line 235-251), same substitution: `job_ids = q.get_recent_feedback_job_ids(conn, scenario_id)` → `anchor = q.get_recent_feedback_anchor(conn, scenario_id)`, and the render call's `feedback_job_ids=",".join(str(i) for i in job_ids)` → `feedback_anchor=anchor`.
4. In `accept_proposals` (line 281), replace:
   ```python
   q.mark_feedback_handled(conn, scenario_id, _parse_job_ids(form.get("feedback_job_ids")))
   ```
   with:
   ```python
   q.mark_feedback_handled(conn, scenario_id, form.get("feedback_anchor") or None)
   ```

In `app/templates/scenarios/_proposals.html`, replace line 5:
```html
<input type="hidden" name="feedback_job_ids" value="{{ feedback_job_ids | default('') }}">
```
with:
```html
<input type="hidden" name="feedback_anchor" value="{{ feedback_anchor or '' }}">
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_routes_scenarios.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full test suite**

Run: `pytest -v`
Expected: PASS. No remaining references to `get_recent_feedback_job_ids`, `_parse_job_ids`, or `feedback_job_ids` anywhere (confirm with `grep -rn "get_recent_feedback_job_ids\|_parse_job_ids\|feedback_job_ids" app/ tests/` — expect no output).

- [ ] **Step 6: Commit**

```bash
git add app/routes/scenarios.py app/templates/scenarios/_proposals.html tests/test_routes_scenarios.py
git commit -m "feat: hand off feedback-handled anchor between refine and accept routes"
```

---

### Task 6: Unhandled/handled vote-count badges on the scenarios page

**Files:**
- Modify: `app/routes/scenarios.py:43-46` (`_scenarios_context`)
- Modify: `app/templates/scenarios/index.html:27-35`
- Test: `tests/test_routes_scenarios.py`

**Interfaces:**
- Consumes: `q.get_recent_feedback_counts(conn, scenario_id) -> dict` (Task 4).
- Produces: `scenarios/index.html` template context gains `feedback_counts_by_scenario: dict[int, dict]`, keyed by scenario id, same shape as `criteria_by_scenario`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_routes_scenarios.py`:

```python
def test_scenarios_page_shows_unhandled_feedback_badge(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    source_id = q.insert_source(conn, "s", "http://x", "http")
    job_id = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, job_id, sid, "too junior", "lower")

    resp = client.get("/scenarios")

    assert "▲0 · ▼1 new" in resp.text


def test_scenarios_page_shows_handled_feedback_badge(client, conn):
    sid = q.insert_scenario(conn, "Remote ML", "")
    source_id = q.insert_source(conn, "s", "http://x", "http")
    job_id = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.upsert_scenario_feedback(conn, job_id, sid, "worth it", "higher")
    anchor = q.get_recent_feedback_anchor(conn, sid)
    q.mark_feedback_handled(conn, sid, anchor)

    resp = client.get("/scenarios")

    assert "▲1 · ▼0 handled" in resp.text


def test_scenarios_page_omits_badges_when_no_feedback(client, conn):
    q.insert_scenario(conn, "Remote ML", "")

    resp = client.get("/scenarios")

    assert "new</span>" not in resp.text
    assert "handled</span>" not in resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_routes_scenarios.py -k "badge" -v`
Expected: FAIL — the badges don't exist in the template yet.

- [ ] **Step 3: Implement**

In `app/routes/scenarios.py`, modify `_scenarios_context`:

```python
def _scenarios_context(conn: sqlite3.Connection) -> dict:
    scenarios = q.get_scenarios(conn)
    criteria_by_scenario = {s["id"]: q.get_criteria(conn, s["id"]) for s in scenarios}
    feedback_counts_by_scenario = {s["id"]: q.get_recent_feedback_counts(conn, s["id"]) for s in scenarios}
    return {
        "scenarios": scenarios,
        "criteria_by_scenario": criteria_by_scenario,
        "feedback_counts_by_scenario": feedback_counts_by_scenario,
    }
```

In `app/templates/scenarios/index.html`, in the per-scenario button row (around line 27-35), add the badges before the closing `</div>`:

```html
  <div style="margin-top:1rem; display:flex; gap:0.5rem; flex-wrap:wrap; align-items:center;">
    <button class="btn"
      data-progress-url="/scenarios/{{ scenario.id }}/refine"
      data-progress-target="#proposals-area-{{ scenario.id }}">
      Refine criteria from feedback
    </button>
    <button class="btn" data-progress-url="/scenarios/{{ scenario.id }}/reevaluate">Re-evaluate jobs</button>
    {% set fc = feedback_counts_by_scenario[scenario.id] %}
    {% if fc.unhandled_higher or fc.unhandled_lower %}
    <span class="tag" title="Since last adjustment — will feed the next refine">▲{{ fc.unhandled_higher }} · ▼{{ fc.unhandled_lower }} new</span>
    {% endif %}
    {% if fc.handled_higher or fc.handled_lower %}
    <span class="tag" title="Already incorporated, last 30 days">▲{{ fc.handled_higher }} · ▼{{ fc.handled_lower }} handled</span>
    {% endif %}
    <div id="proposals-area-{{ scenario.id }}" style="margin-top:0.75rem; width:100%;"></div>
  </div>
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_routes_scenarios.py -k "badge" -v`
Expected: PASS.

- [ ] **Step 5: Run the full test suite**

Run: `pytest -v`
Expected: PASS, all tests green.

- [ ] **Step 6: Commit**

```bash
git add app/routes/scenarios.py app/templates/scenarios/index.html tests/test_routes_scenarios.py
git commit -m "feat: show unhandled/handled feedback vote counts on scenarios page"
```

---

## Manual verification (after all tasks complete)

Use the `run-dev-server` skill to start the app against a throwaway DB copy, then:
1. Go to `/scenarios`, create a scenario if none exist.
2. Fetch/create a job, give it "should score lower" gate feedback on that scenario's score tab, reload `/scenarios` — confirm the "▲0 · ▼1 new" badge appears.
3. Click "Refine criteria from feedback", accept a proposal (or apply with all rows unchecked) — reload `/scenarios`, confirm the badge flips to "▲0 · ▼1 handled" and the "new" badge disappears.
4. Give feedback on a second job, confirm only that one shows as "new" while the first stays "handled".
