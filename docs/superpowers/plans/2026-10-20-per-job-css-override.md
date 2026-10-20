# Per-job CSS Override Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Optional per-job CSS appended after the global CV CSS, edited in a folded panel on the job preview page with a Reload button that saves and redraws the preview.

**Architecture:** New `job_cv.css_override` column. A `_job_css(conn, job_id)` helper in `app/routes/cv.py` combines global + override CSS and replaces `settings["css"]` in the three job render routes. A `POST /jobs/{id}/cv/save-css` route validates/saves; a delegated JS handler in `base.html` posts the form and resets the stage iframes.

**Tech Stack:** FastAPI, Jinja2, SQLite, vanilla JS (delegated listeners in `app/templates/base.html`), pytest.

Spec: `docs/superpowers/specs/2026-10-20-per-job-css-override-design.md`

## Global Constraints

- Run tests with `uv run pytest` (needs `dangerouslyDisableSandbox: true`), never bare `python -m pytest`.
- Use `/usr/bin/git`, stage explicit paths (no `git add -A`).
- Base-CV routes (`/cv/{id}/preview.html`, `/cv/{id}/diff.html`, `/cv/{id}.pdf`) are NOT changed.
- Override goes *after* global CSS; `_DIFF_CSS` still goes after both (already handled inside `render_diff_html`).
- Commit trailer on every commit:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01Bwkk6DBq4gseEsbBDShQPr
  ```

---

### Task 1: Storage + render wiring

**Files:**
- Modify: `app/db/schema.py` (CREATE TABLE job_cv ~line 52; new migration fn after `_migrate_job_cv_add_base_cv_snapshot`; call it at the end of the migration list in `init_db`, after `_migrate_add_manual_tags(conn)`)
- Modify: `app/routes/cv.py` (`cv_preview_html` ~line 415, `cv_diff_html` ~line 456, `cv_pdf` ~line 1106)
- Test: `tests/test_schema.py`, `tests/test_routes_cv_preview.py`

**Interfaces:**
- Produces: column `job_cv.css_override TEXT NOT NULL DEFAULT ''` (exposed via `q.get_job_cv(...)["css_override"]`, settable via `q.upsert_job_cv(conn, job_id, css_override=...)`); `app.routes.cv._job_css(conn, job_id) -> str`.

- [ ] **Step 1: Write failing tests**

In `tests/test_schema.py`, add `"css_override"` to the expected `job_cv` column set in the existing exact-columns test (~line 1363), and add:

```python
def test_init_db_adds_job_cv_css_override_defaulting_empty(conn):
    conn.executescript(
        """
        CREATE TABLE job_cv (
            job_id INTEGER PRIMARY KEY,
            scope TEXT NOT NULL DEFAULT '[]',
            plan TEXT NOT NULL DEFAULT '[]',
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    conn.execute("INSERT INTO job_cv (job_id) VALUES (1)")
    conn.commit()
    init_db(conn)
    assert conn.execute("SELECT css_override FROM job_cv WHERE job_id = 1").fetchone()[0] == ""
    init_db(conn)  # idempotent
```

(Use the same `conn` fixture the neighbouring `test_init_db_adds_job_cv_handled_suggestions_defaulting_empty` uses — check its definition at the top of the file; it's a bare connection without `init_db`.)

In `tests/test_routes_cv_preview.py` add:

```python
def _capture_css(target):
    """Patch app.routes.cv.<target> and return the list of css args it got."""
    from unittest.mock import patch
    seen = []
    def fake(markdown, css):
        seen.append(css)
        return b"%PDF" if target == "render_pdf" else "<html></html>"
    return seen, patch(f"app.routes.cv.{target}", side_effect=fake)


def test_job_renders_append_css_override(client, conn):
    from unittest.mock import patch
    jid = _job(conn)
    q.save_cv_settings(conn, base_instruction="", base_guardrails="",
                       css="h1{color:red}", default_scope=["select", "reorder"])
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft\n", css_override="h1{color:blue}",
                    base_cv_snapshot="# Me")
    expected = "h1{color:red}\nh1{color:blue}"
    with patch("app.routes.cv.doc_write_available", return_value=True):
        for target, url in [
            ("render_preview_html", f"/jobs/{jid}/cv/preview.html?variant=base"),
            ("render_preview_html", f"/jobs/{jid}/cv/preview.html?variant=tailored"),
            ("render_diff_html", f"/jobs/{jid}/cv/diff.html"),
            ("render_pdf", f"/jobs/{jid}/cv.pdf"),
        ]:
            seen, p = _capture_css(target)
            with p:
                assert client.get(url).status_code == 200, url
            assert seen == [expected], url


def test_job_renders_use_global_css_without_override(client, conn):
    from unittest.mock import patch
    jid = _job(conn)
    q.save_cv_settings(conn, base_instruction="", base_guardrails="",
                       css="h1{color:red}", default_scope=["select", "reorder"])
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft\n")
    seen, p = _capture_css("render_preview_html")
    with patch("app.routes.cv.doc_write_available", return_value=True), p:
        client.get(f"/jobs/{jid}/cv/preview.html?variant=tailored")
    assert seen == ["h1{color:red}"]
```

If `cv_diff_html` takes the `_resolve_diff_target` path and needs a version to exist, adjust the diff URL/fixture so it reaches `render_diff_html` (the `base_cv_snapshot` fallback is used when `_resolve_diff_target` returns None). Don't weaken the CSS assertion.

- [ ] **Step 2: Run tests, verify they fail**

Run: `uv run pytest tests/test_schema.py tests/test_routes_cv_preview.py -q`
Expected: FAIL (no `css_override` column).

- [ ] **Step 3: Implement**

`app/db/schema.py` — add to the `job_cv` CREATE TABLE, before `updated_at`:

```sql
    css_override TEXT NOT NULL DEFAULT '',
```

New migration (next to the other `_migrate_job_cv_add_*`):

```python
def _migrate_job_cv_add_css_override(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(job_cv)")}
    if "css_override" not in cols:
        conn.execute("ALTER TABLE job_cv ADD COLUMN css_override TEXT NOT NULL DEFAULT ''")
        conn.commit()
```

Call `_migrate_job_cv_add_css_override(conn)` last in `init_db`'s migration list. Check that no later rebuild migration (`CREATE TABLE job_cv_new ...` ~line 1091) runs after it and drops the column — it runs earlier in the list, so appending at the end is safe; verify.

`app/routes/cv.py` — next to `_job_base_cv_id`:

```python
def _job_css(conn: sqlite3.Connection, job_id: int) -> str:
    """Global CV CSS plus this job's optional override, appended so it wins."""
    css = q.get_cv_settings(conn)["css"]
    row = q.get_job_cv(conn, job_id)
    override = row["css_override"] if row else ""
    return f"{css}\n{override}" if override.strip() else css
```

Replace `settings["css"]` with `_job_css(conn, job_id)` in `cv_preview_html`, `cv_diff_html`, `cv_pdf`. Remove the now-unused `settings = q.get_cv_settings(conn)` lines in those functions if nothing else uses them.

- [ ] **Step 4: Run tests, verify they pass**

Run: `uv run pytest tests/test_schema.py tests/test_routes_cv_preview.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
/usr/bin/git add app/db/schema.py app/routes/cv.py tests/test_schema.py tests/test_routes_cv_preview.py
/usr/bin/git commit -m "feat(cv): per-job css_override column applied to job renders"
```

---

### Task 2: Save route

**Files:**
- Modify: `app/routes/cv.py` (new route next to `cv_save_scope` ~line 1002)
- Test: `tests/test_routes_cv_preview.py`

**Interfaces:**
- Consumes: `css_override` column via `q.upsert_job_cv`; `validate_css` (already imported).
- Produces: `POST /jobs/{job_id}/cv/save-css`, form field `css`. 204 on success; 400 with plain-text error body on invalid CSS; 404 for missing job (via `_require_job`).

- [ ] **Step 1: Write failing tests**

```python
def test_save_css_override_persists(client, conn):
    jid = _job(conn)
    r = client.post(f"/jobs/{jid}/cv/save-css", data={"css": "h1{color:blue}"})
    assert r.status_code == 204
    assert q.get_job_cv(conn, jid)["css_override"] == "h1{color:blue}"
    r = client.post(f"/jobs/{jid}/cv/save-css", data={"css": ""})
    assert r.status_code == 204
    assert q.get_job_cv(conn, jid)["css_override"] == ""


def test_save_css_override_rejects_unsafe_css(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, css_override="h1{color:blue}")
    r = client.post(f"/jobs/{jid}/cv/save-css", data={"css": "@import 'x.css';"})
    assert r.status_code == 400
    assert "@import" in r.text
    assert q.get_job_cv(conn, jid)["css_override"] == "h1{color:blue}"


def test_save_css_override_404_for_missing_job(client):
    assert client.post("/jobs/999/cv/save-css", data={"css": ""}).status_code == 404
```

- [ ] **Step 2: Run, verify fail**

Run: `uv run pytest tests/test_routes_cv_preview.py -q -k save_css`
Expected: FAIL (404/405).

- [ ] **Step 3: Implement**

```python
@router.post("/jobs/{job_id}/cv/save-css")
async def cv_save_job_css(job_id: int, request: Request,
                          conn: sqlite3.Connection = Depends(get_db)):
    _require_job(conn, job_id)
    form = await request.form()
    css = form.get("css", "")
    err = validate_css(css)
    if err:
        return PlainTextResponse(err, status_code=400)
    q.upsert_job_cv(conn, job_id, css_override=css)
    return Response(status_code=204)
```

Import `PlainTextResponse` from `fastapi.responses` if not already imported (`Response` already is — check the imports at top).

- [ ] **Step 4: Run, verify pass**

Run: `uv run pytest tests/test_routes_cv_preview.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
/usr/bin/git add app/routes/cv.py tests/test_routes_cv_preview.py
/usr/bin/git commit -m "feat(cv): save-css route for per-job override"
```

---

### Task 3: Preview-page panel + Reload JS

**Files:**
- Create: `app/templates/cv/_css_override.html`
- Modify: `app/templates/cv/_preview_pane.html:72` (include right after `_frontmatter_hint.html`)
- Modify: `app/templates/base.html` (styles near `.cv-hint` ~line 492; JS inside the CV preview-stage IIFE that starts ~line 1907)
- Test: `tests/test_routes_cv_preview.py`

**Interfaces:**
- Consumes: `job`, `job_cv` (may be None) from `_workbench_ctx`; `POST /jobs/{id}/cv/save-css` from Task 2.

- [ ] **Step 1: Write failing tests**

```python
def test_preview_pane_has_css_override_panel_below_help(client, conn):
    jid = _job(conn)
    text = client.get(f"/jobs/{jid}/cv/preview").text
    assert '<details class="cv-css-override"' in text
    assert text.index('class="cv-help"') < text.index('class="cv-css-override"')
    assert f'data-save-url="/jobs/{jid}/cv/save-css"' in text
    assert 'class="btn btn-subtle cv-css-reload"' in text
    panel = text[text.index('class="cv-css-override"'):]
    panel = panel[:panel.index("</details>")]
    assert '<span class="cv-css-active" hidden>' in panel


def test_preview_pane_css_override_shows_value_and_active_badge(client, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, css_override="h1{color:blue}")
    text = client.get(f"/jobs/{jid}/cv/preview").text
    panel = text[text.index('class="cv-css-override"'):]
    panel = panel[:panel.index("</details>")]
    assert "h1{color:blue}</textarea>" in panel
    assert '<span class="cv-css-active">active</span>' in panel
```

- [ ] **Step 2: Run, verify fail**

Run: `uv run pytest tests/test_routes_cv_preview.py -q -k 'css_override_panel or css_override_shows'`
Expected: FAIL.

- [ ] **Step 3: Template** `app/templates/cv/_css_override.html`:

```html
{# Per-job CSS override, appended after the global CSS (Advanced CV
   settings) for every render of this job. Reload saves via
   /jobs/{id}/cv/save-css and redraws the preview stage — see the
   .cv-css-reload handler in base.html. #}
{% set _css = job_cv.css_override if job_cv else '' %}
<details class="cv-css-override">
  <summary>Custom CSS for this job
    <span class="cv-css-active"{% if not _css.strip() %} hidden{% endif %}>active</span></summary>
  <p class="muted">Appended after the global CSS from
    <a href="/cv/advanced">Advanced CV settings</a>; applies to this job's previews and PDF.</p>
  <textarea name="css" spellcheck="false">{{ _css }}</textarea>
  <div class="cv-css-actions">
    <button type="button" class="btn btn-subtle cv-css-reload"
            data-save-url="/jobs/{{ job.id }}/cv/save-css">&#8635; Reload preview</button>
    <span class="cv-css-status" aria-live="polite"></span>
  </div>
</details>
```

Note: the second test's exact `<span class="cv-css-active">active</span>` string requires the badge to render with no `hidden` attribute and no whitespace inside — keep the Jinja on one line as shown so the non-hidden output is exactly that.

Include it in `_preview_pane.html` directly after `{% include "cv/_frontmatter_hint.html" %}`.

- [ ] **Step 4: Styles** in `base.html` next to `.cv-hint` rules, using existing tokens (`--border`, `--text-muted`, `--alert`):

```css
    .cv-css-override { font-size: 0.85em; margin: 0.4rem 0; }
    .cv-css-override summary { cursor: pointer; color: var(--text-muted); }
    .cv-css-override p { margin: 0.4rem 0; }
    .cv-css-override textarea { width: 100%; min-height: 8rem; box-sizing: border-box;
      font-family: monospace; font-size: 0.95em; }
    .cv-css-actions { display: flex; gap: 0.6rem; align-items: center; margin-top: 0.4rem; }
    .cv-css-active { font-size: 0.8em; padding: 0 0.45em; border-radius: 0.5em;
      border: 1px solid var(--border); margin-left: 0.3em; }
    .cv-css-status[data-state="error"] { color: var(--alert); }
```

Check these tokens exist in `:root` in `base.html`; use the nearest existing equivalents if named differently.

- [ ] **Step 5: JS** — inside the CV preview-stage IIFE (the one defining `activate`, `stageOf`), add a delegated click handler. Place it as a separate `document.body.addEventListener('click', ...)` inside that IIFE so it can call `activate`:

```js
  // Per-job CSS override: save, then drop every iframe's src back to data-src
  // and re-activate the visible tab, so it re-renders now and the others
  // re-render lazily on their next activation.
  document.body.addEventListener('click', function (e) {
    var btn = e.target.closest('.cv-css-reload');
    if (!btn) return;
    var panel = btn.closest('.cv-css-override');
    var status = panel.querySelector('.cv-css-status');
    var css = panel.querySelector('textarea[name="css"]').value;
    var body = new FormData();
    body.append('css', css);
    btn.disabled = true;
    status.dataset.state = '';
    status.textContent = 'Saving…';
    fetch(btn.dataset.saveUrl, { method: 'POST', body: body })
      .then(function (r) {
        if (r.ok) return '';
        return r.text().then(function (t) { throw new Error(t || 'Save failed'); });
      })
      .then(function () {
        status.textContent = '';
        panel.querySelector('.cv-css-active').hidden = !css.trim();
        var stage = (panel.closest('#cv-preview-pane') || document).querySelector('.cv-preview-stage');
        if (!stage) return;
        stage.querySelectorAll('iframe.cv-preview-doc').forEach(function (f) {
          var src = f.getAttribute('src');
          if (src) { f.setAttribute('data-src', src); f.removeAttribute('src'); }
        });
        var active = stage.querySelector('.cv-preview-doc.is-active');
        if (active) activate(stage, active.dataset.variant);
      })
      .catch(function (err) {
        status.dataset.state = 'error';
        status.textContent = err.message;
      })
      .finally(function () { btn.disabled = false; });
  });
```

Note: if the active tab is the Edit tab (not an iframe), `activate` just keeps it; the iframes reload when next shown. That's fine.

- [ ] **Step 6: Run full suite**

Run: `uv run pytest -q`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
/usr/bin/git add app/templates/cv/_css_override.html app/templates/cv/_preview_pane.html app/templates/base.html tests/test_routes_cv_preview.py
/usr/bin/git commit -m "feat(ui): per-job CSS override panel with reload"
```

- [ ] **Step 8: Manual check** — use the `run-dev-server` skill; open a job's preview page, set `h1{color:blue}`, hit Reload, confirm the visible preview redraws blue, the badge appears, `@import 'x';` shows an inline error, and clearing it removes the badge. Leave the server running and report the URL.
