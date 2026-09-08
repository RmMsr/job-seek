# CV HTML preview + Base/Tailored tabs — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the CV preview PNG pipeline with `doc-write-cli --html` rendered on demand into an `<iframe>`, and give the workbench preview pane Base/Tailored tabs.

**Architecture:** `doc-write-cli` writes a self-contained paginated HTML document when the output path ends `.html` (no flag needed — the CLI infers format from the suffix). It renders in ~0.5s with no WeasyPrint/browser pass, carries its own CSP, and is embedded with `<iframe sandbox="allow-scripts allow-same-origin">`. Previews become stateless GET routes; the `cv_tailor` task stops rendering anything; `job_cv.preview_pages` is dropped.

**Tech Stack:** FastAPI, Jinja2, htmx, SQLite, pytest. `doc-write-cli` (external binary, already a dependency).

## Global Constraints

- Personal single-instance app: hard-downtime migrations are fine; no backwards-compat shims.
- Commit after each task (each task ends green).
- TDD: failing test first, then implementation.
- Run the suite with `python -m pytest` (not `uv run` — read-only cache in sandbox).
- Do **not** `git add -A` / `git add .` — the repo root has untracked special files that break it. Stage explicit paths. Use `/usr/bin/git` (a shell `git` function is shadowed in this environment).
- The built-in doc-write prev/next control pill is kept — the app never drives pagination itself. Do not pass `--no-controls`.
- `cv.pdf` download and `render_pdf` are untouched.

---

### Task 1: `render_preview_html` + drop task-time preview rendering

**Files:**
- Modify: `app/cv/render.py`
- Modify: `app/routes/cv.py` (`_task_cv_tailor` and its helpers)
- Test: `tests/test_cv_render.py`, `tests/test_cv_task.py`

**Interfaces:**
- Produces: `render_preview_html(markdown: str, css: str) -> str` in `app/cv/render.py` — returns the full HTML document text; raises `CvRenderError` if `doc-write-cli` is missing or fails.
- Removes: `render_preview_pngs`, `app.routes.cv._render_previews`, `app.routes.cv._render_previews_step`, `app.routes.cv._preview_dir`.

- [ ] **Step 1: Rewrite the render.py tests**

In `tests/test_cv_render.py`, change the import line (line 4-6) to:

```python
from app.cv.render import (
    doc_write_available, render_pdf, render_preview_html, CvRenderError,
)
```

Delete `test_render_preview_pngs` and `test_render_preview_pngs_clears_stale_pages` entirely. Add:

```python
def test_render_preview_html_raises_when_binary_missing(monkeypatch):
    monkeypatch.setattr("app.cv.render.doc_write_available", lambda: False)
    with pytest.raises(CvRenderError):
        render_preview_html("# CV\n\n- x\n", "")


@needs_docwrite
def test_render_preview_html_is_self_contained_doc():
    html = render_preview_html("# CV\n\n- Did the thing.\n", "p { color: #333; }")
    assert html.lstrip().startswith("<!DOCTYPE html>")
    assert "Did the thing." in html
    assert "paged" in html.lower()  # inlined pagination engine
```

- [ ] **Step 2: Run the render tests, expect failure**

Run: `python -m pytest tests/test_cv_render.py -q`
Expected: ImportError / `render_preview_html` not defined.

- [ ] **Step 3: Implement `render_preview_html` in `app/cv/render.py`**

Remove the `import glob` line. Delete `render_preview_pngs` (lines ~50-66). Add, after `render_pdf`:

```python
def render_preview_html(markdown: str, css: str) -> str:
    """A self-contained, paginated HTML preview document for iframe embedding.
    doc-write-cli infers HTML output from the .html output suffix."""
    with tempfile.TemporaryDirectory(prefix="cv-render-") as work:
        out = _run(markdown, css, "cv.html", work)
        with open(out) as f:
            return f.read()
```

- [ ] **Step 4: Run the render tests, expect pass**

Run: `python -m pytest tests/test_cv_render.py -q`
Expected: PASS (the `@needs_docwrite` one runs here — `doc-write-cli` is installed).

- [ ] **Step 5: Strip preview rendering out of the task**

In `app/routes/cv.py`:

- Line 22 import → `from app.cv.render import render_preview_html, render_pdf, doc_write_available, CvRenderError`
- Delete `_preview_dir` (lines ~66-68), `_render_previews` (~71-83), `_render_previews_step` (~86-101).
- In `_task_cv_tailor` baseline branch: delete the line
  `pngs = yield from _render_previews_step(job_id, draft, settings["css"], config)`
  and change the following `q.upsert_job_cv(...)` call to drop `preview_pages=pngs,`:

```python
                q.upsert_job_cv(
                    conn, job_id, tailored_cv=draft, change_report=rep,
                    guardrail_findings=findings,
                    base_hash=_base_hash(settings),
                )
```

- In the `mode == "generate"` branch: delete
  `pngs = yield from _render_previews_step(job_id, draft, settings["css"], config)`
  and drop `preview_pages=pngs,` from the `q.upsert_job_cv(...)` below it:

```python
    q.upsert_job_cv(
        conn, job_id, tailored_cv=draft, change_report=rep, guardrail_findings=findings,
        base_hash=_base_hash(settings),
    )
```

- [ ] **Step 6: Fix `tests/test_cv_task.py`**

Remove every `patch("app.routes.cv.render_preview_pngs", return_value=...)` context-manager line (9 occurrences — each is the last entry in a `with` block; delete the line and fix the trailing `\` / `:` on the line above). In `test_generate_mode_stores_draft_and_clears_finalized`, also delete the assertion line:

```python
    assert row["preview_pages"] == ["/tmp/p1.png"]
```

- [ ] **Step 7: Run affected suites**

Run: `python -m pytest tests/test_cv_render.py tests/test_cv_task.py -q`
Expected: PASS. (`job_cv.preview_pages` column still exists with its `'[]'` default — harmless; removed in Task 4.)

- [ ] **Step 8: Commit**

```bash
/usr/bin/git add app/cv/render.py app/routes/cv.py tests/test_cv_render.py tests/test_cv_task.py
/usr/bin/git commit -m "feat: render_preview_html; stop rendering previews in the cv_tailor task

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01YAUuWYfmrM6HK7gL7xmnUm"
```

---

### Task 2: On-demand preview routes

**Files:**
- Modify: `app/routes/cv.py`
- Modify: `app/templates/cv/_preview_result.html`
- Test: `tests/test_routes_cv_settings.py`, `tests/test_routes_cv_workbench.py`

**Interfaces:**
- Consumes: `render_preview_html`, `doc_write_available` (Task 1).
- Produces:
  - `GET /jobs/{job_id}/cv/preview.html?variant=base|tailored` → `HTMLResponse`; `variant` defaults to `"tailored"`; 404 when `variant=tailored` and no draft; 404 for a missing job; 503 when doc-write-cli is missing.
  - `GET /cv/preview.html` → `HTMLResponse` of the base CV; 503 when doc-write-cli is missing.
- Removes: `GET /jobs/{job_id}/cv/preview/{page}.png` (`cv_preview_png`), context keys `preview_urls` / `n_pages`.

- [ ] **Step 1: Write the failing route tests**

In `tests/test_routes_cv_workbench.py`, delete `test_preview_png_out_of_range_404` (lines ~51-54). Add:

```python
def test_preview_html_renders_base_and_tailored(client, cv_on, conn):
    from unittest.mock import patch
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Tailored marker\n")
    with patch("app.routes.cv.render_preview_html", side_effect=lambda md, css: f"<!DOCTYPE html>\n{md}"):
        rb = client.get(f"/jobs/{jid}/cv/preview.html?variant=base")
        rt = client.get(f"/jobs/{jid}/cv/preview.html?variant=tailored")
    assert rb.status_code == 200 and "# Me" in rb.text          # base CV from _job()
    assert rt.status_code == 200 and "Tailored marker" in rt.text


def test_preview_html_defaults_to_tailored(client, cv_on, conn):
    from unittest.mock import patch
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# The draft\n")
    with patch("app.routes.cv.render_preview_html", side_effect=lambda md, css: md):
        r = client.get(f"/jobs/{jid}/cv/preview.html")
    assert "The draft" in r.text


def test_preview_html_tailored_404_without_draft(client, cv_on, conn):
    jid = _job(conn)
    assert client.get(f"/jobs/{jid}/cv/preview.html?variant=tailored").status_code == 404


def test_preview_html_503_without_doc_write(client, cv_on, conn):
    from unittest.mock import patch
    jid = _job(conn)
    with patch("app.routes.cv.doc_write_available", return_value=False):
        assert client.get(f"/jobs/{jid}/cv/preview.html?variant=base").status_code == 503
```

In `tests/test_routes_cv_settings.py`, replace `test_cv_preview_renders_pages` (lines ~41-56) with:

```python
def test_cv_preview_shows_iframe(client, cv_on, conn):
    from unittest.mock import patch
    q.save_cv_settings(conn, base_cv="# Me\n\n- x\n", base_instruction="", base_guardrails="",
                       css="", default_scope=[1])
    with patch("app.routes.cv.doc_write_available", return_value=True):
        r = client.post("/cv/preview")
    assert r.status_code == 200
    assert "<iframe" in r.text
    assert "/cv/preview.html" in r.text


def test_cv_preview_html_renders_base_cv(client, cv_on, conn):
    from unittest.mock import patch
    q.save_cv_settings(conn, base_cv="# Marker CV\n", base_instruction="", base_guardrails="",
                       css="", default_scope=[1])
    with patch("app.routes.cv.render_preview_html", side_effect=lambda md, css: f"<!DOCTYPE html>\n{md}"):
        r = client.get("/cv/preview.html")
    assert r.status_code == 200
    assert "Marker CV" in r.text
```

Leave `test_cv_preview_reports_missing_doc_write` unchanged.

- [ ] **Step 2: Run, expect failure**

Run: `python -m pytest tests/test_routes_cv_workbench.py tests/test_routes_cv_settings.py -q`
Expected: FAIL — 404s / missing routes.

- [ ] **Step 3: Add the routes in `app/routes/cv.py`**

Delete `cv_preview_png` (the `@router.get("/jobs/{job_id}/cv/preview/{page}.png")` route, ~lines 187-196). Add next to `cv_workbench`:

```python
@router.get(
    "/jobs/{job_id}/cv/preview.html", response_class=HTMLResponse,
    dependencies=[Depends(require_cv_enabled)],
)
def cv_preview_html(job_id: int, variant: str = "tailored",
                    conn: sqlite3.Connection = Depends(get_db)):
    job = q.get_job(conn, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    settings = q.get_cv_settings(conn)
    if variant == "base":
        markdown = settings["base_cv"]
    else:
        row = q.get_job_cv(conn, job_id)
        if row is None or not row["tailored_cv"]:
            raise HTTPException(status_code=404, detail="No tailored CV")
        markdown = row["tailored_cv"]
    if not doc_write_available():
        raise HTTPException(status_code=503, detail="doc-write-cli is not installed")
    try:
        return HTMLResponse(render_preview_html(markdown, settings["css"]))
    except CvRenderError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
```

Add next to `cv_preview_base`:

```python
@router.get("/cv/preview.html", response_class=HTMLResponse,
            dependencies=[Depends(require_cv_enabled)])
def cv_preview_base_html(conn: sqlite3.Connection = Depends(get_db)):
    settings = q.get_cv_settings(conn)
    if not doc_write_available():
        raise HTTPException(status_code=503, detail="doc-write-cli is not installed")
    try:
        return HTMLResponse(render_preview_html(settings["base_cv"], settings["css"]))
    except CvRenderError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
```

Rewrite `cv_preview_base` (the `POST /cv/preview` handler) — it now just picks between the iframe partial and the not-installed message:

```python
@router.post("/cv/preview", response_class=HTMLResponse, dependencies=[Depends(require_cv_enabled)])
def cv_preview_base(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    return templates.TemplateResponse(
        request, "cv/_preview_result.html", {"has_doc_write": doc_write_available()}
    )
```

In `_workbench_ctx`, delete the `n_pages` line and the `preview_urls` key; keep `has_doc_write`.

Remove imports left unused after the deletions — candidates: `base64`, `tempfile`, `os`, `FileResponse`. Grep each within `app/routes/cv.py` before removing (`cv_pdf` uses `Response`, not `FileResponse`; `_preview_dir` was the last `os` user).

- [ ] **Step 4: Rewrite `app/templates/cv/_preview_result.html`**

```html
{% if has_doc_write %}
<iframe src="/cv/preview.html" title="Base CV preview"
        sandbox="allow-scripts allow-same-origin"
        style="width:100%;aspect-ratio:1 / 1.414;border:1px solid var(--border);background:#fff;"></iframe>
{% else %}
<p class="muted">Preview unavailable: doc-write-cli is not installed.</p>
{% endif %}
```

- [ ] **Step 5: Run, expect pass**

Run: `python -m pytest tests/test_routes_cv_workbench.py tests/test_routes_cv_settings.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
/usr/bin/git add app/routes/cv.py app/templates/cv/_preview_result.html tests/test_routes_cv_workbench.py tests/test_routes_cv_settings.py
/usr/bin/git commit -m "feat: on-demand /cv preview.html routes; drop the PNG page route

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01YAUuWYfmrM6HK7gL7xmnUm"
```

---

### Task 3: Base/Tailored tabs in the workbench preview pane

**Files:**
- Modify: `app/templates/cv/_preview_pane.html`
- Modify: `app/templates/base.html` (CSS)
- Test: `tests/test_routes_cv_workbench.py`

**Interfaces:**
- Consumes: `GET /jobs/{job_id}/cv/preview.html?variant=…` (Task 2), `has_doc_write` / `job_cv` / `job` / `settings` / `stale_directives` / `stale_plan` from `_workbench_ctx`.

**Use the `frontend-design` skill** for the tab and iframe styling before writing markup.

- [ ] **Step 1: Write the failing tests**

Add `import re` at the top of `tests/test_routes_cv_workbench.py` if not present. Add:

```python
def test_preview_pane_has_base_tailored_tabs(client, cv_on, conn):
    from unittest.mock import patch
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft\n")
    with patch("app.routes.cv.doc_write_available", return_value=True):
        r = client.get(f"/jobs/{jid}/cv")
    assert 'data-variant="base"' in r.text
    assert 'data-variant="tailored"' in r.text
    assert 'id="cv-preview-frame"' in r.text
    # a draft exists → tailored tab is the active one
    assert 'src="/jobs/%d/cv/preview.html?variant=tailored"' % jid in r.text


def test_preview_pane_tailored_tab_disabled_without_draft(client, cv_on, conn):
    from unittest.mock import patch
    jid = _job(conn)
    with patch("app.routes.cv.doc_write_available", return_value=True):
        r = client.get(f"/jobs/{jid}/cv")
    m = re.search(r'<button[^>]*data-variant="tailored"[^>]*>', r.text)
    assert m and "disabled" in m.group(0)
    # frame falls back to the base variant
    assert 'variant=base"' in r.text


def test_preview_pane_markdown_fallback_without_doc_write(client, cv_on, conn):
    from unittest.mock import patch
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft body\n")
    with patch("app.routes.cv.doc_write_available", return_value=False):
        r = client.get(f"/jobs/{jid}/cv")
    assert "cv-preview-frame" not in r.text
    assert "Draft body" in r.text  # rendered markdown
```

- [ ] **Step 2: Run, expect failure**

Run: `python -m pytest tests/test_routes_cv_workbench.py -q -k preview_pane`
Expected: FAIL.

- [ ] **Step 3: Rewrite `app/templates/cv/_preview_pane.html`**

Keep the top action block (lines 1-17: Generate / Accept / Download PDF) exactly as it is. Replace everything from line 19 (`{% if job_cv and job_cv.tailored_cv %}`) to the end with:

```html
{% set has_draft = job_cv and job_cv.tailored_cv %}

{% if has_draft and (stale_directives or stale_plan) %}
<p class="muted" style="font-size:.85em;color:var(--warning)">&#9208; Waiting on you — this preview is from before your latest changes. Generate to refresh it.</p>
{% endif %}

{% if has_doc_write %}
  {% set variant = 'tailored' if has_draft else 'base' %}
  <div class="cv-preview-tabs" role="tablist" aria-label="CV preview">
    <button type="button" class="cv-preview-tab" role="tab" data-variant="base"
            aria-selected="{{ 'false' if has_draft else 'true' }}">Base</button>
    <button type="button" class="cv-preview-tab" role="tab" data-variant="tailored"
            aria-selected="{{ 'true' if has_draft else 'false' }}"
            {% if not has_draft %}disabled title="Generate to see the tailored version"{% endif %}>Tailored</button>
  </div>
  <iframe id="cv-preview-frame" class="cv-preview-frame" title="CV preview"
          src="/jobs/{{ job.id }}/cv/preview.html?variant={{ variant }}"
          sandbox="allow-scripts allow-same-origin"></iframe>
  <script>
  (function () {
    var frame = document.getElementById("cv-preview-frame");
    var tabs = document.querySelectorAll(".cv-preview-tabs .cv-preview-tab");
    tabs.forEach(function (t) {
      t.addEventListener("click", function () {
        if (t.disabled) return;
        tabs.forEach(function (o) { o.setAttribute("aria-selected", o === t ? "true" : "false"); });
        frame.src = "/jobs/{{ job.id }}/cv/preview.html?variant=" + t.dataset.variant;
      });
    });
  })();
  </script>
{% else %}
  <h2>Preview</h2>
  <p class="muted">doc-write-cli is not installed — showing the raw markdown.</p>
  <div class="cv-markdown" style="border:1px solid var(--border);padding:1rem;">{{ (job_cv.tailored_cv if has_draft else settings.base_cv) | markdown }}</div>
{% endif %}

{% if has_draft %}
  <div id="cv-change-report">{% include "cv/_change_report.html" %}</div>
  <div id="cv-findings">{% include "cv/_findings.html" %}</div>
{% endif %}
```

- [ ] **Step 4: Add CSS to `app/templates/base.html`**

After the `.scenario-tab-link.has-new-suggestions::after { … }` rule (~line 577), add:

```css
    .cv-preview-tabs { display: flex; gap: 1rem; border-bottom: 1px solid var(--border); margin: 0.5rem 0 0; }
    .cv-preview-tab { background: none; border: none; cursor: pointer; padding: 0.4rem 0; margin-bottom: -1px;
      color: var(--text-secondary); font-weight: 500; font-size: 0.9rem; border-bottom: 2px solid transparent; }
    .cv-preview-tab:hover:not(:disabled) { color: var(--accent); }
    .cv-preview-tab[aria-selected="true"] { color: var(--text-primary); font-weight: 600; border-bottom-color: var(--accent); }
    .cv-preview-tab:disabled { cursor: default; opacity: 0.45; }
    .cv-preview-frame { width: 100%; aspect-ratio: 1 / 1.414; border: 1px solid var(--border);
      border-top: none; background: #fff; display: block; }
```

- [ ] **Step 5: Run, expect pass**

Run: `python -m pytest tests/test_routes_cv_workbench.py -q`
Expected: PASS.

- [ ] **Step 6: Manual check on the dev server**

Use the `run-dev-server` skill (throwaway DB copy). Load a job's Tailor CV page:
- First visit: Base tab active, Tailored disabled, base CV renders in the iframe with the doc-write prev/next pill.
- After Generate: Tailored tab active; clicking Base swaps the iframe; multi-page CVs page via the pill.
- Temporarily rename `doc-write-cli` off `PATH` (or patch) is not needed — just eyeball the markdown fallback branch via a unit test.
Leave the server running for user handoff.

- [ ] **Step 7: Commit**

```bash
/usr/bin/git add app/templates/cv/_preview_pane.html app/templates/base.html tests/test_routes_cv_workbench.py
/usr/bin/git commit -m "feat: Base/Tailored tabs in the CV workbench preview pane

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01YAUuWYfmrM6HK7gL7xmnUm"
```

---

### Task 4: Drop the `job_cv.preview_pages` column

**Files:**
- Modify: `app/db/schema.py`
- Modify: `app/db/queries.py:40`
- Test: `tests/test_schema.py`

**Interfaces:**
- Produces: `_migrate_job_cv_drop_preview_pages(conn)` — no-op unless the live `job_cv` table still has the column.

- [ ] **Step 1: Write the failing migration test**

In `tests/test_schema.py`, in `test_job_cv_table_columns_and_cascade`, remove `"preview_pages",` from the expected `cols` set (line ~1268). Add a new test near it:

```python
def test_init_db_drops_job_cv_preview_pages(conn):
    conn.executescript(
        """
        CREATE TABLE job_cv (
            job_id INTEGER PRIMARY KEY,
            scope TEXT NOT NULL DEFAULT '[]',
            tuning_directives TEXT NOT NULL DEFAULT '',
            plan TEXT NOT NULL DEFAULT '[]',
            tailored_cv TEXT NOT NULL DEFAULT '',
            guardrail_findings TEXT NOT NULL DEFAULT '[]',
            change_report TEXT NOT NULL DEFAULT '{}',
            base_hash TEXT NOT NULL DEFAULT '',
            preview_pages TEXT NOT NULL DEFAULT '[]',
            plan_generated_at TEXT, directives_edited_at TEXT, generated_at TEXT, finalized_at TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    conn.execute("INSERT INTO job_cv (job_id, preview_pages) VALUES (1, '[\"/x/p1.png\"]')")
    conn.commit()

    init_db(conn)

    cols = {r["name"] for r in conn.execute("PRAGMA table_info(job_cv)")}
    assert "preview_pages" not in cols
    assert conn.execute("SELECT COUNT(*) FROM job_cv").fetchone()[0] == 1  # row preserved

    init_db(conn)  # idempotent
    assert "preview_pages" not in {r["name"] for r in conn.execute("PRAGMA table_info(job_cv)")}
```

Also update the schema literal in `test_init_db_remaps_string_keyed_scope_to_ids` — leave its `CREATE TABLE job_cv` with `preview_pages` as-is (it simulates an old DB; the migration will drop it and the test only asserts on `scope`).

- [ ] **Step 2: Run, expect failure**

Run: `python -m pytest tests/test_schema.py -q -k "job_cv"`
Expected: FAIL — column still present.

- [ ] **Step 3: Implement the migration in `app/db/schema.py`**

Remove the `preview_pages TEXT NOT NULL DEFAULT '[]',` line from `_DDL` (line ~39). Add, next to `_migrate_jobs_drop_feedback_scenario_id` (follow that DROP COLUMN pattern):

```python
def _migrate_job_cv_drop_preview_pages(conn: sqlite3.Connection) -> None:
    # Previews are rendered on demand as HTML now (doc-write-cli --html) — the
    # task no longer stores PNG page paths. Direct DROP COLUMN.
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='job_cv'"
    ).fetchone()
    if row is None or "preview_pages" not in row[0]:
        return
    conn.execute("ALTER TABLE job_cv DROP COLUMN preview_pages")
    conn.commit()
```

Append `_migrate_job_cv_drop_preview_pages(conn)` to the migration list at the end of `init_db` (after `_migrate_seed_and_remap_cv_scope_options(conn)`).

- [ ] **Step 4: Update `app/db/queries.py:40`**

```python
_JOB_CV_JSON_COLS = ("scope", "plan", "guardrail_findings", "change_report")
```

- [ ] **Step 5: Run, expect pass**

Run: `python -m pytest tests/test_schema.py -q`
Expected: PASS.

- [ ] **Step 6: Full suite**

Run: `python -m pytest -q`
Expected: PASS (previous baseline: 1518 passed). Investigate any new failure.

- [ ] **Step 7: Commit**

```bash
/usr/bin/git add app/db/schema.py app/db/queries.py tests/test_schema.py
/usr/bin/git commit -m "feat: drop job_cv.preview_pages — previews are on-demand HTML now

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01YAUuWYfmrM6HK7gL7xmnUm"
```

---

## Final check

1. `python -m pytest -q` — all green.
2. `grep -rn "preview_pages\|render_preview_pngs\|preview/.*png\|preview_urls" app/ tests/` — no matches.
3. Dev server (throwaway DB): `/cv` Preview button shows the base CV in an iframe; a job's Tailor CV page shows Base/Tailored tabs that swap the iframe; the doc-write pill paginates multi-page CVs; Download PDF still works. Leave the server running and hand the URL to the user.

## Spec coverage

- render.py `--html` swap → Task 1
- on-demand routes, `.png` route deletion, `POST /cv/preview` rewrite → Task 2
- Base/Tailored tabs, disabled-Tailored state, staleness note position, markdown fallback → Task 3
- task/`_workbench_ctx` cleanup → Tasks 1 & 2
- migration + `_DDL` + `_JOB_CV_JSON_COLS` + schema tests → Task 4
- built-in controls kept, `cv.pdf` untouched → Global Constraints (no code change)
