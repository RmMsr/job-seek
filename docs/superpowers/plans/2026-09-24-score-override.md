# Score Override Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user override a job's computed fit score with a 0–100 value picked on a floating dial.

**Architecture:** New nullable `jobs.fit_score_override` column (0–1). Effective score = `COALESCE(fit_score_override, fit_score)`, used for score sort and display. One POST route sets/clears it and re-renders the job like the feedback form does. The dial is vanilla JS + inline SVG in `base.html` (where all app CSS/JS lives), driven by `data-*` attributes on the override button and posting via `htmx.ajax`.

**Tech Stack:** FastAPI, Jinja2, htmx 1.9, SQLite, pytest.

Spec: `docs/superpowers/specs/2026-09-24-score-override-design.md`

## Global Constraints

- Run tests with `uv run pytest` (needs `dangerouslyDisableSandbox: true` for the uv cache lock). Never use a bare `python -m pytest`.
- Use `/usr/bin/git`, not `git` (rtk hook rewrites `git`). Stage explicit paths, never `git add -A`/`.`.
- Commit after each task. End commit messages with:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01ArysrC2rUeKtvkibg8EsfE
  ```
- Work only in `/home/roman/projects/job-seek/.claude/worktrees/score-override` (branch `worktree-score-override`). Verify every commit lands on that branch.
- Override values are integers 0–100 in the API, stored as `score / 100`.
- "Identical" = `score == round(fit_score * 100)` when `fit_score` is not NULL → store NULL (clears override).

---

### Task 1: DB column, set/clear query, reset, sort

**Files:**
- Modify: `app/db/schema.py` (jobs `CREATE TABLE` ~line 122; new migration fn; call it at end of the migration list after `_migrate_job_cv_add_base_cv_id(conn)`)
- Modify: `app/db/queries.py` (`reset_job` ~1143, `_ORDER_BY` ~1341, new `set_fit_score_override`)
- Test: `tests/test_schema.py`, `tests/test_queries.py`

**Interfaces:**
- Produces: `q.set_fit_score_override(conn, job_id: int, score: int) -> None` — stores `score/100` or NULL if identical to computed; logs a `"score"` job event. `get_job`/`get_jobs` rows gain key `fit_score_override` (via `jobs.*`).

- [ ] **Step 1: Failing tests**

`tests/test_schema.py`:
```python
def test_jobs_table_has_fit_score_override_column(conn):
    init_db(conn)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()]
    assert cols.count("fit_score_override") == 1


def test_init_db_migrates_jobs_adds_fit_score_override(conn):
    init_db(conn)
    conn.execute("ALTER TABLE jobs DROP COLUMN fit_score_override")
    conn.commit()
    init_db(conn)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()]
    assert cols.count("fit_score_override") == 1
```
(If `init_db` is guarded so a second call skips migrations — see commit 94c1e75 "stop re-running schema migrations on every request" — call the migration function `_migrate_jobs_add_fit_score_override(conn)` directly instead of the second `init_db`.)

`tests/test_queries.py` (reuse the existing `_mk_job(conn, url, created=..., fit=...)` helper; check its signature first):
```python
def _fit_job(conn, fit=0.58):
    sid = q.insert_source(conn, "s", "http://x", "generic_listing")
    jid = q.insert_job(conn, source_id=sid, url="http://job/o", title="T", company="C", raw_text="r")
    if fit is not None:
        q.update_job_fit(conn, jid, fit, "i", fit, "a", "ph")
    return jid


def test_set_fit_score_override_stores_fraction(conn):
    jid = _fit_job(conn)
    q.set_fit_score_override(conn, jid, 73)
    assert q.get_job(conn, jid)["fit_score_override"] == pytest.approx(0.73)


def test_set_fit_score_override_identical_to_computed_clears(conn):
    jid = _fit_job(conn, fit=0.58)
    q.set_fit_score_override(conn, jid, 73)
    q.set_fit_score_override(conn, jid, 58)
    assert q.get_job(conn, jid)["fit_score_override"] is None


def test_set_fit_score_override_on_unscored_job_stores(conn):
    jid = _fit_job(conn, fit=None)
    q.set_fit_score_override(conn, jid, 0)
    assert q.get_job(conn, jid)["fit_score_override"] == 0


def test_set_fit_score_override_logs_event(conn):
    jid = _fit_job(conn, fit=0.58)
    q.set_fit_score_override(conn, jid, 73)
    q.set_fit_score_override(conn, jid, 58)
    msgs = [e["message"] for e in q.get_job_events(conn, jid)]
    assert any("Score overridden" in m and "73" in m for m in msgs)
    assert any("Score override cleared" in m for m in msgs)


def test_reset_job_clears_fit_score_override(conn):
    jid = _fit_job(conn)
    q.set_fit_score_override(conn, jid, 73)
    q.reset_job(conn, jid)
    assert q.get_job(conn, jid)["fit_score_override"] is None


def test_update_job_fit_keeps_fit_score_override(conn):
    jid = _fit_job(conn)
    q.set_fit_score_override(conn, jid, 73)
    q.update_job_fit(conn, jid, 0.2, "i", 0.2, "a", "ph2")
    assert q.get_job(conn, jid)["fit_score_override"] == pytest.approx(0.73)


def test_get_jobs_order_score_uses_override(conn):
    lo = _mk_job(conn, "https://x.test/lo", created="2024-01-01T00:00:00", fit=0.2)
    hi = _mk_job(conn, "https://x.test/hi", created="2024-01-01T00:00:00", fit=0.9)
    q.set_fit_score_override(conn, lo, 95)
    ids = [j["id"] for j in q.get_jobs(conn, status="new", order="score")]
    assert ids.index(lo) < ids.index(hi)
```

- [ ] **Step 2: Run, expect FAIL**

`uv run pytest tests/test_schema.py tests/test_queries.py -k "fit_score_override or order_score" -v`

- [ ] **Step 3: Implement**

`app/db/schema.py` — add `fit_score_override REAL,` after `fit_score REAL,` in the jobs `CREATE TABLE`, plus:
```python
def _migrate_jobs_add_fit_score_override(conn: sqlite3.Connection) -> None:
    # Purely additive column, same shape as the gate_override migration.
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='jobs'"
    ).fetchone()
    if row is None or "fit_score_override" in row[0]:
        return
    conn.execute("ALTER TABLE jobs ADD COLUMN fit_score_override REAL")
    conn.commit()
```
Call it as the **last** migration (after the rebuild migrations, so they can't drop it).

`app/db/queries.py`:
- In `reset_job`'s UPDATE add `fit_score_override = NULL,` next to `fit_score = NULL,`.
- `_ORDER_BY["score"] = "COALESCE(jobs.fit_score_override, jobs.fit_score) DESC NULLS LAST, jobs.created_at DESC"`.
- New function (near `log_score_change`):
```python
def set_fit_score_override(conn: sqlite3.Connection, job_id: int, score: int) -> None:
    """Store a user override (0..100) of the fit score. Picking the computed
    score itself clears the override."""
    row = conn.execute("SELECT fit_score FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        return
    fit = row["fit_score"]
    value = None if fit is not None and score == round(fit * 100) else score / 100
    conn.execute("UPDATE jobs SET fit_score_override = ? WHERE id = ?", (value, job_id))
    if value is None:
        add_job_event(conn, job_id, "score", "Score override cleared")
    else:
        add_job_event(conn, job_id, "score", f"Score overridden → {score}%")
    conn.commit()
```
(Check whether `add_job_event` commits itself; don't double up needlessly but a trailing commit is harmless.)

- [ ] **Step 4: Run, expect PASS**, then full `uv run pytest` green.
- [ ] **Step 5: Commit** `feat(jobs): fit_score_override column and set/clear query`

---

### Task 2: Route + in-Python sort key

**Files:**
- Modify: `app/routes/jobs.py` (`_sort_key` ~75; new route after `job_save_note` ~372)
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: `q.set_fit_score_override`.
- Produces: `POST /jobs/{job_id}/score-override` form field `score: int` (0–100, else 422). Accepts the same query string as `/feedback` (filter params, or `?detail=1`). Returns HTML from `_render_updated_job_html(conn, request, job_id, f, detail=detail)`.

- [ ] **Step 1: Failing tests** (use the file's `_seed(conn)`, which gives fit_score 0.75):
```python
def test_score_override_sets_and_renders(client, conn):
    _, jid, _ = _seed(conn)
    resp = client.post(f"/jobs/{jid}/score-override", data={"score": "90"})
    assert resp.status_code == 200
    assert q.get_job(conn, jid)["fit_score_override"] == pytest.approx(0.90)
    assert f'id="job-{jid}"' in resp.text


def test_score_override_identical_clears(client, conn):
    _, jid, _ = _seed(conn)
    client.post(f"/jobs/{jid}/score-override", data={"score": "90"})
    client.post(f"/jobs/{jid}/score-override", data={"score": "75"})
    assert q.get_job(conn, jid)["fit_score_override"] is None


@pytest.mark.parametrize("bad", ["-1", "101", "abc"])
def test_score_override_rejects_out_of_range(client, conn, bad):
    _, jid, _ = _seed(conn)
    resp = client.post(f"/jobs/{jid}/score-override", data={"score": bad})
    assert resp.status_code == 422


def test_score_override_detail_renders_detail_variant(client, conn):
    _, jid, _ = _seed(conn)
    resp = client.post(f"/jobs/{jid}/score-override?detail=1", data={"score": "90"})
    assert resp.status_code == 200
    assert "job-detail-extra" in resp.text
```
And a `_sort_key` unit test:
```python
def test_sort_key_score_uses_override():
    from app.routes.jobs import _sort_key
    a = {"fit_score": 0.9, "fit_score_override": None, "created_at": "1"}
    b = {"fit_score": 0.2, "fit_score_override": 0.95, "created_at": "1"}
    assert sorted([a, b], key=_sort_key("score"), reverse=True)[0] is b
```
(Check how `_sort_key` is used — ascending with `reverse=True`? Match the existing call.)

- [ ] **Step 2: Run, expect FAIL**
- [ ] **Step 3: Implement**

```python
def _effective_fit(j) -> float | None:
    return j["fit_score_override"] if j.get("fit_score_override") is not None else j["fit_score"]
```
Use it in `_sort_key("score")` in place of `j["fit_score"]`.

Route:
```python
@router.post("/jobs/{job_id}/score-override", response_class=HTMLResponse)
def job_score_override(
    job_id: int,
    request: Request,
    score: int = Form(..., ge=0, le=100),
    conn: sqlite3.Connection = Depends(get_db),
):
    q.set_fit_score_override(conn, job_id, score)
    f = _filter_from_request(request)
    detail = _is_detail_page_request(request)
    return HTMLResponse(_render_updated_job_html(conn, request, job_id, f, detail=detail))
```

- [ ] **Step 4: Run, expect PASS**; full suite green.
- [ ] **Step 5: Commit** `feat(jobs): score-override route`

---

### Task 3: Score display (macro, row, feedback) + override button markup

**Files:**
- Modify: `app/templates/jobs/_macros.html` (`fit_score_badge`)
- Modify: `app/templates/jobs/_row.html` (score column)
- Modify: `app/templates/jobs/_feedback.html` (two meta lines + Organize group)
- Modify: `app/templates/base.html` (CSS only, next to `.score-badge` ~165 and `.job-row-score` ~202)
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Produces (consumed by Task 4's JS): the override button
  ```html
  <button type="button" class="btn score-override-btn score-badge score-fluid" style="{{ eff | score_style }}"
    data-score-override-url="/jobs/{{ job.id }}/score-override{{ macros.qsuffix(filter, detail=is_detail_page|default(false)) }}"
    data-score-override-target="#job-{{ job.id }}"
    data-computed="{{ (job.fit_score * 100)|round|int if job.fit_score is not none else '' }}"
    data-override="{{ (job.fit_score_override * 100)|round|int if job.fit_score_override is not none else '' }}"
    title="Override the fit score" aria-haspopup="dialog">73%</button>
  ```
  Label is the effective score `NN%`, or `–` when none (then omit `style`). It is the first child of the Organize `.actions` group, before Accept. `type="button"` is required — it sits inside the feedback `<form>`.

- [ ] **Step 1: Failing tests**
```python
def test_row_shows_struck_original_and_override(client, conn):
    _, jid, _ = _seed(conn)
    q.set_fit_score_override(conn, jid, 90)
    html = client.get("/jobs").text
    assert "score-original" in html and "75%" in html and "90%" in html


def test_expanded_shows_override_button_before_accept(client, conn):
    _, jid, _ = _seed(conn)
    html = client.get(f"/jobs/{jid}/expand").text
    assert 'data-score-override-url="/jobs/%d/score-override' % jid in html
    assert html.index("score-override-btn") < html.index('value="accepted"')


def test_override_button_shows_dash_for_unscored(client, conn):
    sid = q.insert_source(conn, "s", "http://x", "generic_listing")
    jid = q.insert_job(conn, source_id=sid, url="http://job/u", title="U", company="C", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="c", content_type="job_posting", summary="s")
    html = client.get(f"/jobs/{jid}/expand").text
    start = html.index("score-override-btn")
    assert "–" in html[start:html.index("</button>", start)]


def test_no_struck_original_without_override(client, conn):
    _seed(conn)
    assert "score-original" not in client.get("/jobs").text
```
- [ ] **Step 2: Run, expect FAIL**
- [ ] **Step 3: Implement**

`_macros.html` — replace `fit_score_badge`:
```jinja
{% macro fit_score_badge(job, size="", layout="inline") %}
{% set ov = job.fit_score_override %}
{% set eff = ov if ov is not none else job.fit_score %}
{% if eff is not none %}
{% if job.fit_score is not none %}
{% set fit_title = "Profile fit: average of Interest (%.0f%%) and Attainability (%.0f%%) against your profile"|format(job.interest_score * 100, job.attainability_score * 100) %}
{% endif %}
{% if ov is not none %}
<span class="score-overridden score-overridden-{{ layout }}">
  <span class="score-badge score-fluid{% if size %} {{ size }}{% endif %}" style="{{ ov | score_style }}" title="Your override">{{ "%.0f"|format(ov * 100) }}%</span>
  {% if job.fit_score is not none %}
  <span class="score-arrow" aria-hidden="true">{{ "&#9652;"|safe if layout == "stacked" else "&#9656;"|safe }}</span>
  <s class="score-original" title="{{ fit_title }}"><span class="sr-only">computed </span>{{ "%.0f"|format(job.fit_score * 100) }}%</s>
  {% endif %}
</span>
{% else %}
<span class="score-badge score-fluid{% if size %} {{ size }}{% endif %}" style="{{ eff | score_style }}" title="{{ fit_title }}">{{ "%.0f"|format(eff * 100) }}%</span>
{% endif %}
{% endif %}
{% endmacro %}
```
Visual order: inline must read `~~58%~~ ▸ 73%` and stacked must be override on top, `▴` then struck original below. Achieve inline order with CSS (`.score-overridden-inline { display:inline-flex; flex-direction: row-reverse; align-items:center; gap:.3em }`) or reorder the markup per layout — whichever is simpler; keep the macro readable. Stacked: `.score-overridden-stacked { display:flex; flex-direction:column; align-items:center; gap:0 }`. `.score-original { color: var(--text-muted); font-size: .8em; }`. `.score-arrow { color: var(--text-muted); font-size: .75em; line-height: 1; }`.

`_row.html`: change the score cell condition to `{% if job.fit_score is not none or job.fit_score_override is not none %}` and call `macros.fit_score_badge(job, layout="stacked")`. Check `.job-row-score .score-badge` CSS still looks right with the wrapper.

`_feedback.html`: the two `job-detail-meta` lines use the same widened condition (inline layout, the default). Add the override button (Interfaces above) as the first element inside the Organize `.actions` div. Button styling: it's a `.btn` that also carries the score colour; add minimal CSS `.score-override-btn { font-variant-numeric: tabular-nums; min-width: 3.5em; }` and make sure `.btn` doesn't wipe the `.score-fluid` background — adjust specificity if needed.

- [ ] **Step 4: Run, expect PASS**; full suite green.
- [ ] **Step 5: Commit** `feat(jobs): show score override on cards and override button`

---

### Task 4: The dial (JS + CSS in base.html)

Use the `frontend-design` skill for this task.

**Files:**
- Modify: `app/templates/base.html` (new `<style>` rules near the score CSS; new `<script>` block alongside the existing ones near the end)
- Test: `tests/test_routes_jobs.py` (smoke: dial script present)

**Interfaces:**
- Consumes: button `data-*` from Task 3; route from Task 2.

Behaviour (from the spec):
- Delegated `click` listener on `document` for `.score-override-btn` (buttons are re-rendered by htmx, so no per-element binding). Stop propagation so the expanded-card header toggle doesn't fire.
- Opens a fixed-position overlay (backdrop + centred panel), large: ~min(80vw, 420px) square SVG.
- Gauge arc: 300°, gap at the bottom; 0 at bottom-left → 100 at bottom-right, clockwise. Ticks every 1 (tiny) / 5 (small) / 10 (labelled 0…100). Arc stroke coloured with the score scale (use a `conicGradient`-free approach: e.g. 20 short arc segments each styled with the nearest `--fit-*` var, or an SVG `linearGradient` approximation — keep it simple).
- Markers on the arc: ▲ at computed (`data-computed`, if non-empty), ● at current override (`data-override`, if non-empty).
- Pointer move over the panel → angle from centre → value 0–100 (clamp; in the bottom gap snap to the nearest end). Centre shows the hovered value large (`73%`) and a needle/handle at that angle. Before any hover, show the effective current value.
- Click on the arc/dial area → `htmx.ajax('POST', url, {target, swap: 'outerHTML', values: {score: value}})`, close.
- Cancel button under the centre, `Esc`, and backdrop click → close without saving.
- Keyboard: the dial panel is focusable (`role="dialog" aria-modal="true" aria-label="Override fit score"`), arrows ±1 (Shift ±10), Enter saves. Return focus to the button on close.
- Must work in light and dark themes (use existing CSS vars: `--text-muted`, `--border`, surface vars — look up the real names in `:root`).

- [ ] **Step 1:** Smoke test: `GET /jobs` HTML contains `score-override-dial` (the script/style identifier). Run, expect FAIL.
- [ ] **Step 2:** Implement script + styles.
- [ ] **Step 3:** Run full suite green.
- [ ] **Step 4:** Manual check via the `run-dev-server` skill (throwaway DB copy, never the live one): open `/jobs`, expand a job, open dial, hover, click to save, confirm card shows `~~old~~ ▸ new`, folded card stacked, pick computed value → override cleared, Cancel/Esc do nothing, detail page `/jobs/{id}` works too. Check no console errors (claude-in-chrome if available).
- [ ] **Step 5: Commit** `feat(jobs): score override dial`
- [ ] **Step 6:** Leave the dev server running and report its URL.
