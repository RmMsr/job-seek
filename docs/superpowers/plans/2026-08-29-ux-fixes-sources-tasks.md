# UX fixes: sources add-flow, status bar, task links, tasks list — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship five independent backlog UX fixes to the sources add-flow, the bottom status bar, single-job task links, the fetch page, and the home Tasks list.

**Architecture:** All server-side changes are small edits to existing FastAPI route modules (`app/routes/*.py`) and Jinja templates (`app/templates/**`). The app renders HTML fragments swapped in by a hand-rolled `data-progress-*` polling layer and by htmx; there is no build step and no JS bundler. `app/templates/base.html` holds all global CSS and JS inline.

**Tech Stack:** Python 3.14, FastAPI, Jinja2, SQLite (`app/db/queries.py`), pytest. Run tests with `uv run pytest`. Run the dev server with the `run-dev-server` skill.

## Global Constraints

- Every change happens in the existing worktree at
  `.claude/worktrees/ux-fixes-sources-tasks` on branch
  `worktree-ux-fixes-sources-tasks`. Verify `git branch --show-current` before
  committing.
- One commit per task. Commit message prefix: `fix:` or `ux:` as noted per task.
- TDD: write the failing test first, see it fail, implement, see it pass, commit.
- Tests: `uv run pytest <path> -v`. Full suite (`uv run pytest`) must be green
  before the final task's commit.
- Do not touch `job-seek.db`; tests use an in-memory DB via the `conn` fixture.
- The five tasks are independent and may be implemented in any order; the plan
  lists them 1–5.
- Never hardcode real Slack/community IDs (not relevant to these tasks, but the
  project rule stands).

---

### Task 1: Sources add-flow — full-width result container + auto-clear

**Files:**
- Modify: `app/templates/sources/index.html`
- Modify: `app/templates/sources/_add_form.html`
- Modify: `app/templates/sources/_detect_confirm.html`
- Modify: `app/templates/_rewrite_panel.html`
- Modify: `app/routes/sources.py` (`_task_source_confirm`, and the
  `_rewrite_panel.html` render call in `_task_source_detect`)
- Modify: `app/routes/jobs.py` (the `_rewrite_panel.html` render call — add
  `target=`)
- Modify: `app/templates/base.html` (the `.add-panel-trigger` click handler)
- Test: `tests/test_routes_sources.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: nothing other tasks depend on.
- `_rewrite_panel.html` gains a required `target` template variable. Both call
  sites must pass it:
  - `app/routes/sources.py` → `target="#sources-add-result"`
  - `app/routes/jobs.py` → `target="#rewrite-panel"`

- [ ] **Step 1: Write the failing tests**

In `tests/test_routes_sources.py`, add these tests. Reuse the existing
`_run_detect(conn, url)` helper and the `_SLACK_URL` / `detect_listing_page`
mock patterns already in the file.

```python
def test_add_form_targets_dedicated_result_container(client):
    html = client.get("/sources").text
    assert 'id="sources-add-result"' in html
    assert 'data-progress-target="#sources-add-result"' in html
    # the old in-form target is gone
    assert 'data-progress-target="#add-source-panel"' not in html


def test_detect_confirm_panel_has_flex_wrapper(conn):
    with patch("app.routes.sources.detect_listing_page") as mock_fetch:
        fetched = _run_detect(conn, _SLACK_URL)
        mock_fetch.assert_not_called()
    html = fetched["result"]["html_chunks"][0]
    assert 'id="detect-confirm"' in html
    assert "flex-wrap:wrap" in html


def test_rewrite_panel_from_sources_targets_result_container(conn):
    # a logged-in LinkedIn search URL triggers suggest_rewrite
    fetched = _run_detect(
        conn,
        "https://www.linkedin.com/jobs/search-results/?keywords=robotics&currentJobId=1",
    )
    panel = fetched["result"]["html_chunks"][0]
    assert 'data-progress-target="#sources-add-result"' in panel
    assert 'data-progress-target="#rewrite-panel"' not in panel


def test_source_confirm_clears_result_container(conn):
    q.insert_source(conn, "Existing", "https://careers.example.com/jobs", "generic_listing")
    # confirming an already-tracked URL hits the early return path
    task = q.enqueue_task(
        conn, kind="source_confirm",
        params={"url": "https://careers.example.com/jobs", "name": "Dup", "fetcher_type": "generic_listing"},
    )
    execute_task(conn, None, None, _config(), task)
    chunks = q.get_task(conn, task["id"])["result"]["html_chunks"]
    assert any(c.strip() == '<div id="sources-add-result"></div>' for c in chunks)
```

Check the top of `tests/test_routes_sources.py` for the existing imports
(`execute_task`, a config helper, `q`). If a `_config()` helper is not already
present, follow whatever pattern the other `execute_task` call sites in that file
use (grep for `execute_task(` in the test file).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_routes_sources.py -k "result_container or flex_wrapper" -v`
Expected: FAIL — `id="sources-add-result"` not found, etc.

- [ ] **Step 3: Add the result container to the sources page**

In `app/templates/sources/index.html`, add the container as a sibling right after
the `{% call add_panel(...) %}…{% endcall %}` block:

```html
{% extends "base.html" %}
{% block title %}Sources — Job Seek{% endblock %}
{% block content %}
{% include "setup/_subnav.html" %}
<h1>Sources</h1>
{% from "_add_panel.html" import add_panel %}
{% call add_panel("Add") %}
{% include "sources/_add_form.html" %}
{% endcall %}
<div id="sources-add-result"></div>
{% include "sources/_table.html" %}
{% endblock %}
```

- [ ] **Step 4: Retarget the Add button**

In `app/templates/sources/_add_form.html`, change the primary button's
`data-progress-target` and keep everything else:

```html
<form id="add-source-panel" style="display:flex; gap:0.5rem; align-items:center; flex-wrap:wrap;">
  <input type="url" id="add-source-url" name="url" placeholder="Paste a listing page URL…" required style="flex:1; min-width:200px;">
  <button type="button" class="btn btn-subtle add-panel-cancel">Cancel</button>
  <button type="button" class="btn btn-primary"
    data-progress-url="/sources/detect"
    data-progress-body-url="#add-source-url"
    data-progress-target="#sources-add-result"
    data-progress-display="#add-source-progress">Add</button>
  <span id="add-source-progress" class="reset-progress" aria-live="polite"></span>
</form>
```

- [ ] **Step 5: Wrap `_detect_confirm.html` in its own flex row**

Rewrite `app/templates/sources/_detect_confirm.html` so its fields sit in a
self-contained flex wrapper (it previously relied on being injected into the flex
form):

```html
<div id="detect-confirm" style="display:flex; gap:0.5rem; align-items:center; flex-wrap:wrap;">
  <span style="margin:0;">Detected type: <strong>{{ fetcher_type }}</strong></span>
  <input type="hidden" id="detect-url" value="{{ url }}">
  <input type="hidden" id="detect-fetcher-type" value="{{ fetcher_type }}">
  <label for="detect-name" style="font-size:0.9em; color:#495057;">Name:</label>
  <input type="text" id="detect-name" value="{{ name }}" class="text-input" style="flex:1; min-width:160px;">
  <a href="/sources" class="btn btn-subtle">Cancel</a>
  <button type="button" class="btn btn-primary"
    data-progress-url="/sources/detect/confirm"
    data-progress-body-url="#detect-url"
    data-progress-body-name="#detect-name"
    data-progress-body-fetcher_type="#detect-fetcher-type"
    data-progress-oob
    data-progress-display="#detect-confirm-progress">Add source</button>
  <span id="detect-confirm-progress" class="reset-progress" aria-live="polite"></span>
</div>
```

- [ ] **Step 6: Parametrise `_rewrite_panel.html`'s target**

In `app/templates/_rewrite_panel.html`, replace the two hardcoded
`data-progress-target="#rewrite-panel"` with `data-progress-target="{{ target }}"`:

```html
    <button type="button" class="btn btn-subtle"
      data-progress-url="{{ detect_url }}"
      data-progress-body-url="#rw-original"
      data-progress-body-skip_rewrite="#rw-skip"
      data-progress-target="{{ target }}"
      data-progress-display="#rewrite-progress">Add original anyway</button>
    <button type="button" class="btn btn-primary"
      data-progress-url="{{ detect_url }}"
      data-progress-body-url="#rw-suggested"
      data-progress-target="{{ target }}"
      data-progress-display="#rewrite-progress">Use suggested URL</button>
```

- [ ] **Step 7: Pass `target` from both call sites**

In `app/routes/sources.py` `_task_source_detect`, the `_rewrite_panel.html`
render call gains `target="#sources-add-result"`:

```python
            panel = templates.get_template("_rewrite_panel.html").render(
                request=None, original_url=url, suggested_url=suggestion.url,
                reason=suggestion.reason, detect_url="/sources/detect", cancel_url="/sources",
                target="#sources-add-result",
            )
```

In `app/routes/jobs.py`, find the `_rewrite_panel.html` render call (around line
619) and add `target="#rewrite-panel"` to keep the Jobs flow unchanged:

```python
                    panel = templates.get_template("_rewrite_panel.html").render(
                        request=None, original_url=url, suggested_url=suggestion.url,
                        reason=suggestion.reason, detect_url="/jobs/add-by-url", cancel_url="/jobs",
                        target="#rewrite-panel",
                    )
```

- [ ] **Step 8: Clear the result container on confirm**

In `app/routes/sources.py`, add a helper near `_sources_context` and use it in
`_task_source_confirm`'s two return paths:

```python
def _post_confirm_chunks(conn: sqlite3.Connection) -> list[str]:
    """The OOB chunk set every source_confirm outcome ends with: refreshed table,
    a fresh empty add form, and an emptied result container so the confirm panel
    disappears once the source is in."""
    return [
        templates.get_template("sources/_table.html").render(request=None, **_sources_context(conn)),
        templates.get_template("sources/_add_form.html").render(request=None),
        '<div id="sources-add-result"></div>',
    ]
```

Then in `_task_source_confirm`, replace both

```python
    table = templates.get_template("sources/_table.html").render(request=None, **_sources_context(conn))
    add_form = templates.get_template("sources/_add_form.html").render(request=None)
    return {"notices": [...], "html_chunks": [table, add_form]}
```

blocks with

```python
    return {"notices": [...], "html_chunks": _post_confirm_chunks(conn)}
```

keeping each path's own `notices` list (the `already_tracked` path keeps
`[already_tracked]`; the success path keeps its `notices` built from
`fetch_result`).

- [ ] **Step 9: Clear a stale result block when the panel reopens**

In `app/templates/base.html`, in the `.add-panel-trigger` click handler, clear a
sibling `#sources-add-result` when the panel is toggled:

```javascript
  var trigger = e.target.closest('.add-panel-trigger');
  if (trigger) {
    var panel = trigger.closest('.add-panel');
    panel.classList.toggle('add-panel-open');
    var sib = panel.nextElementSibling;
    if (sib && sib.id === 'sources-add-result') sib.innerHTML = '';
    return;
  }
```

- [ ] **Step 10: Run the new tests + the existing sources suite**

Run: `uv run pytest tests/test_routes_sources.py -v`
Expected: PASS (all, including the pre-existing detect/rewrite tests).

- [ ] **Step 11: Commit**

```bash
git add app/templates/sources/index.html app/templates/sources/_add_form.html \
  app/templates/sources/_detect_confirm.html app/templates/_rewrite_panel.html \
  app/routes/sources.py app/routes/jobs.py app/templates/base.html \
  tests/test_routes_sources.py
git commit -m "fix: sources add-flow — full-width result panel that clears after add"
```

---

### Task 2: Status bar — no overlap with the bulk bar + dismissable

**Files:**
- Modify: `app/templates/base.html` (CSS block near line 385; the ambient-poll
  IIFE `renderStatusBar` near line 693; the bulk-selection IIFE near line 736)
- Test: `tests/test_routes_jobs.py` (one render assertion) — plus manual check

**Interfaces:**
- Consumes: nothing.
- Produces: nothing.

- [ ] **Step 1: Write the failing test**

The behaviour is JS/CSS, but assert the static markup/style is present. In
`tests/test_routes_jobs.py` add:

```python
def test_status_bar_has_dismiss_and_bulk_offset(client, conn):
    html = client.get("/jobs").text
    assert "--bulk-bar-h" in html
    assert "status-bar-dismiss" in html
```

(The `client`/`conn` fixtures are already used throughout this file.)

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_routes_jobs.py::test_status_bar_has_dismiss_and_bulk_offset -v`
Expected: FAIL — strings not found.

- [ ] **Step 3: CSS — offset the fixed bottom elements**

In `app/templates/base.html`, update the `#status-bar` and `#notice-stack`
rules and add the dismiss-button rule:

```css
    #notice-stack:empty { display: none; }
    #notice-stack { position: fixed; bottom: calc(3rem + var(--bulk-bar-h, 0px)); left: 50%; transform: translateX(-50%);
      width: 100%; max-width: 960px; box-sizing: border-box; padding: 0 1rem; z-index: 25;
      transition: bottom 0.15s ease; }
```

```css
    #status-bar:empty { display: none; }
    #status-bar { position: fixed; bottom: var(--bulk-bar-h, 0px); left: 50%; transform: translateX(-50%);
      width: 100%; max-width: 960px; box-sizing: border-box; z-index: 20;
      display: flex; align-items: center; gap: 0.75rem; flex-wrap: wrap;
      background: #212529; color: #f8f9fa; font-size: 0.85em;
      padding: 0.5rem 1rem; border-radius: 6px 6px 0 0; box-shadow: 0 -2px 8px rgba(0,0,0,0.15);
      transition: bottom 0.15s ease; }
    #status-bar a { color: #9ec5fe; }
    .status-bar-dismiss { margin-left: auto; background: none; border: none; color: inherit;
      opacity: 0.6; cursor: pointer; font-size: 1rem; line-height: 1; padding: 0 0.15rem; }
    .status-bar-dismiss:hover { opacity: 1; }
```

- [ ] **Step 4: JS — keep `--bulk-bar-h` in sync**

In `app/templates/base.html`, inside the bulk-selection IIFE (the one starting
`function jobCheckboxes()`), add a measurement function and call it from the
existing listeners:

```javascript
      function syncBulkBarHeight() {
        var bar = document.querySelector(".bulk-bar");
        var h = (bar && bar.offsetParent !== null) ? bar.getBoundingClientRect().height : 0;
        document.body.style.setProperty("--bulk-bar-h", h + "px");
      }
      var bulkBarObserver = new ResizeObserver(syncBulkBarHeight);
      function observeBulkBar() {
        var bar = document.querySelector(".bulk-bar");
        if (bar) bulkBarObserver.observe(bar);
        syncBulkBarHeight();
      }
      window.addEventListener("resize", syncBulkBarHeight);
      document.body.addEventListener("htmx:afterSwap", observeBulkBar);
      observeBulkBar();
```

Then in the same IIFE's existing `updateCount()` function, add a call to
`syncBulkBarHeight()` at the end (it already runs on every `job_ids` /
`select-all` change):

```javascript
      function updateCount() {
        var checked = document.querySelectorAll('input[name="job_ids"]:checked');
        document.querySelectorAll(".bulk-count").forEach(function (el) {
          el.textContent = checked.length + " selected";
        });
        syncSelectAll();
        syncBulkBarHeight();
      }
```

- [ ] **Step 5: JS — dismissable status bar**

In `app/templates/base.html`, in the ambient-poll IIFE, add a module-level
`dismissedSignature` and update `renderStatusBar`:

```javascript
      var dismissedSignature = null;

      function renderStatusBar(data) {
        var bar = document.getElementById("status-bar");
        if (!bar) return;
        var tasks = data.tasks || [];
        var signature = tasks.map(function (t) { return t.id; }).join(",");
        if (signature && signature === dismissedSignature && !data.inbox_count) {
          bar.innerHTML = "";
          return;
        }
        var current = tasks.find(function (t) { return t.status === "running"; }) || tasks[0];
        var parts = [];
        if (current) {
          var queuedCount = tasks.length - 1;
          var label = current.label || current.kind.replace(/_/g, " ");
          parts.push('<span><strong>' + escapeHtml(label) + ':</strong></span>');
          if (current.progress) {
            parts.push(
              '<span class="status-bar-progress-track"><span class="status-bar-progress-fill" style="width:' +
              current.progress.percent + '%"></span></span>' +
              '<span>' + current.progress.current + '/' + current.progress.total +
              ' (' + current.progress.percent + '%)</span>'
            );
          } else {
            var statusWord = current.status === "running" ? "started" : current.status;
            parts.push('<span>' + escapeHtml(statusWord) + '</span>');
          }
          parts.push('<a href="/tasks/' + current.id + '/log">details</a>');
          if (queuedCount > 0) parts.push('<span>+' + queuedCount + ' queued</span>');
        }
        if (data.inbox_count) {
          parts.push('<a href="/">' + data.inbox_count + ' need attention</a>');
        }
        if (parts.length) {
          parts.push('<button type="button" class="status-bar-dismiss" aria-label="Dismiss" data-status-signature="' +
            escapeHtml(signature) + '">×</button>');
        }
        bar.innerHTML = parts.join("");
      }
```

Add a delegated click handler at the end of the same IIFE (near
`document.body.addEventListener("click", onClick);`):

```javascript
      document.body.addEventListener("click", function (evt) {
        var dismiss = evt.target.closest(".status-bar-dismiss");
        if (!dismiss) return;
        dismissedSignature = dismiss.getAttribute("data-status-signature") || "";
        var bar = document.getElementById("status-bar");
        if (bar) bar.innerHTML = "";
      });
```

- [ ] **Step 6: Run the test + full jobs suite**

Run: `uv run pytest tests/test_routes_jobs.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/templates/base.html tests/test_routes_jobs.py
git commit -m "fix: status bar sits above the bulk bar and can be dismissed"
```

---

### Task 3: Single-job task links back to the job

**Files:**
- Modify: `app/routes/tasks.py` (`_task_summary`, `task_log`)
- Modify: `app/templates/tasks/log.html`
- Modify: `app/templates/base.html` (`renderStatusBar` — add the job link)
- Test: `tests/test_routes_tasks.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `_task_summary` dict gains an optional `"link"` key (str or absent).
  `/tasks/active` and `/tasks/{id}` both expose it.
- If Task 2 is done first, the `renderStatusBar` edit here adds one line to the
  version Task 2 produced; if Task 3 is done first, Task 2 rebases its
  `renderStatusBar` rewrite over it. Either order is fine — the two edits touch
  different lines (Task 2: signature + dismiss button; Task 3: the "view job"
  anchor next to "details").

- [ ] **Step 1: Write the failing tests**

In `tests/test_routes_tasks.py`:

```python
_JOB_LINK_KINDS = ("job_reset", "job_pass_as_new", "job_reevaluate")


def test_task_detail_includes_job_link_for_single_job_kinds(client, conn):
    job_id = q.insert_job(conn, source_id=None, url="https://e.com/j", title="J", company="", raw_text="")
    task = q.enqueue_task(conn, kind="job_reevaluate", params={"job_id": job_id, "filter_ctx": {}})
    resp = client.get(f"/tasks/{task['id']}")
    assert resp.json()["link"] == f"/jobs/{job_id}"


def test_task_detail_no_link_for_fetch_source(client, conn):
    src_id = q.insert_source(conn, "S", "https://e.com", "generic_listing")
    task = q.enqueue_task(conn, kind="fetch_source", params={"source_id": src_id})
    assert resp_link(client, task["id"]) is None


def resp_link(client, task_id):
    return client.get(f"/tasks/{task_id}").json().get("link")


def test_task_log_page_shows_job_backlink(client, conn):
    job_id = q.insert_job(conn, source_id=None, url="https://e.com/j", title="J", company="", raw_text="")
    task = q.enqueue_task(conn, kind="job_reset", params={"job_id": job_id, "filter_ctx": {}})
    html = client.get(f"/tasks/{task['id']}/log").text
    assert f'href="/jobs/{job_id}"' in html
```

Check `tests/test_routes_tasks.py` / `tests/conftest.py` for the exact
`q.insert_job` signature — match whatever other tests in the repo pass (grep
`insert_job(` across `tests/`). If `source_id` must be a real source, create one
with `q.insert_source` first.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_routes_tasks.py -k "job_link or backlink or fetch_source" -v`
Expected: FAIL — `link` key missing / KeyError.

- [ ] **Step 3: Add `link` to `_task_summary`**

In `app/routes/tasks.py`:

```python
_JOB_LINK_KINDS = {"job_reset", "job_pass_as_new", "job_reevaluate"}


def _task_link(task: dict) -> str | None:
    if task["kind"] in _JOB_LINK_KINDS:
        job_id = task["params"].get("job_id")
        if job_id is not None:
            return f"/jobs/{job_id}"
    return None
```

In `_task_summary`, add the key (only when non-None, to keep existing payloads
tidy):

```python
    summary = {
        "id": task["id"], "kind": task["kind"], "label": _task_label(conn, task), "status": task["status"],
        "last_line": last_line, "error": task["error"],
        "progress": _progress_from_line(last_line),
    }
    link = _task_link(task)
    if link:
        summary["link"] = link
```

The `test_task_detail_no_link_for_fetch_source` test uses `.get("link")` so an
absent key returns `None` — matches.

- [ ] **Step 4: Pass the link to the log template**

In `app/routes/tasks.py` `task_log`:

```python
@router.get("/tasks/{task_id}/log", response_class=HTMLResponse)
def task_log(task_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    task = q.get_task(conn, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    lines = task["log"].strip().split("\n") if task["log"].strip() else []
    return templates.TemplateResponse(
        request, "tasks/log.html", {"task": task, "lines": lines, "job_link": _task_link(task)}
    )
```

- [ ] **Step 5: Render the backlink in `log.html`**

In `app/templates/tasks/log.html`, add above the existing
`<p><a href="/">Back to Start</a></p>`:

```html
{% if job_link %}
<p><a href="{{ job_link }}">← Back to the job</a></p>
{% endif %}
<p><a href="/">Back to Start</a></p>
```

- [ ] **Step 6: Add the job link to the status bar**

In `app/templates/base.html` `renderStatusBar`, right after the `details` link
push:

```javascript
          parts.push('<a href="/tasks/' + current.id + '/log">details</a>');
          if (current.link) parts.push('<a href="' + escapeHtml(current.link) + '">view job</a>');
```

- [ ] **Step 7: Run tests**

Run: `uv run pytest tests/test_routes_tasks.py -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add app/routes/tasks.py app/templates/tasks/log.html app/templates/base.html \
  tests/test_routes_tasks.py
git commit -m "ux: single-job tasks link back to the job from status bar and log"
```

---

### Task 4: Fetch page — Source-name column links to Sources

**Files:**
- Modify: `app/templates/fetch/_table.html`
- Test: `tests/test_routes_fetch.py`

**Interfaces:**
- Consumes: nothing. Produces: nothing.

- [ ] **Step 1: Write the failing test**

In `tests/test_routes_fetch.py`:

```python
def test_fetch_table_links_source_name_to_sources_page(client, conn):
    src_id = q.insert_source(conn, "Acme Board", "https://acme.example/jobs", "generic_listing")
    html = client.get("/fetch").text
    assert f'href="/sources#source-row-{src_id}"' in html
    assert "Acme Board" in html
```

Match `q.insert_source`'s real signature (grep `insert_source(` in `tests/`).

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_routes_fetch.py::test_fetch_table_links_source_name_to_sources_page -v`
Expected: FAIL — href not found.

- [ ] **Step 3: Link the name**

In `app/templates/fetch/_table.html`, change the Source cell:

```html
        <td style="padding:0.5rem;">
          <a href="/sources#source-row-{{ source.id }}"><strong>{{ source.name }}</strong></a>
        </td>
```

- [ ] **Step 4: Run the test + fetch suite**

Run: `uv run pytest tests/test_routes_fetch.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/templates/fetch/_table.html tests/test_routes_fetch.py
git commit -m "ux: link fetch-page source names to the Sources page row"
```

---

### Task 5: Home Tasks list — checkbox checklist instead of Dismiss button

**Files:**
- Create: `app/templates/home/_task_item.html`
- Modify: `app/templates/home/index.html` (Tasks `<section>`)
- Modify: `app/routes/inbox.py` (`inbox_resolve` returns the resolved `<li>`)
- Modify: `app/db/queries.py` (add `get_inbox_item`)
- Modify: `app/templates/base.html` (one CSS line for checkbox spacing)
- Test: `tests/test_routes_home.py`, `tests/test_routes_inbox.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `q.get_inbox_item(conn: sqlite3.Connection, item_id: int) -> dict | None`.
- `home/_task_item.html` takes `item` (dict with `id`, `message`, `link`) and
  `done` (bool).

- [ ] **Step 1: Write the failing tests**

Replace `test_home_shows_pending_task_with_dismiss_button` in
`tests/test_routes_home.py` with:

```python
def test_home_shows_pending_task_with_resolve_checkbox(client, conn):
    _use_test_db(conn)
    item_id = q.create_inbox_item(conn, kind="task_followup", message="please decide", link="/somewhere")
    resp = client.get("/")
    assert "please decide" in resp.text
    assert 'href="/somewhere"' in resp.text
    assert f'hx-post="/inbox/{item_id}/resolve"' in resp.text
    assert 'type="checkbox"' in resp.text
    assert "Dismiss</button>" not in resp.text
```

Add to `tests/test_routes_home.py`:

```python
def test_home_resolved_task_shows_checked_disabled_box(client, conn):
    _use_test_db(conn)
    item_id = q.create_inbox_item(conn, kind="task_followup", message="already handled", link="/x")
    q.resolve_inbox_item(conn, item_id)
    resp = client.get("/")
    assert 'class="task-completed"' in resp.text
    assert "disabled" in resp.text
    assert "checked" in resp.text
```

Replace `test_inbox_resolve_marks_resolved` in `tests/test_routes_inbox.py` with:

```python
def test_inbox_resolve_marks_resolved_and_returns_completed_li(client, conn):
    item_id = q.create_inbox_item(conn, kind="task_followup", message="decide me", link="/y")
    resp = client.post(f"/inbox/{item_id}/resolve")
    assert resp.status_code == 200
    assert q.count_unresolved_inbox_items(conn) == 0
    assert 'class="task-completed"' in resp.text
    assert "decide me" in resp.text
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_routes_home.py tests/test_routes_inbox.py -v`
Expected: FAIL — checkbox / completed-li assertions.

- [ ] **Step 3: Add `get_inbox_item`**

In `app/db/queries.py`, next to `get_inbox_item_by_task_id`:

```python
def get_inbox_item(conn: sqlite3.Connection, item_id: int) -> dict | None:
    return _row_to_dict(
        conn.execute("SELECT * FROM inbox_items WHERE id = ?", (item_id,)).fetchone()
    )
```

- [ ] **Step 4: Create the item partial**

`app/templates/home/_task_item.html`:

```html
{% if done %}
<li class="task-completed">
  <input type="checkbox" checked disabled>
  {{ item.message }}
</li>
{% else %}
<li id="inbox-item-{{ item.id }}">
  <input type="checkbox" hx-post="/inbox/{{ item.id }}/resolve"
    hx-target="#inbox-item-{{ item.id }}" hx-swap="outerHTML"
    aria-label="Mark done">
  <a href="{{ item.link }}">{{ item.message }}</a>
</li>
{% endif %}
```

- [ ] **Step 5: Use the partial on the home page**

In `app/templates/home/index.html`, replace the Tasks `<ul>` body:

```html
{% if pending_tasks or recent_completed_tasks %}
<section class="tasks-section">
  <h2>Tasks</h2>
  <ul class="checklist" id="tasks-list">
    {% for item in pending_tasks %}
      {% include "home/_task_item.html" with context %}
    {% endfor %}
    {% for item in recent_completed_tasks %}
      {% set done = True %}
      {% include "home/_task_item.html" with context %}
    {% endfor %}
  </ul>
</section>
{% endif %}
```

Jinja note: `{% set done = True %}` inside the second loop leaks into later
iterations of that loop only (fine — every item there is done). For the first
loop, `done` is undefined → the `{% if done %}` is falsy → renders the pending
branch. To be explicit, add `{% set done = False %}` inside the first loop.

- [ ] **Step 6: Return the completed `<li>` from the resolve route**

Rewrite `app/routes/inbox.py`:

```python
from __future__ import annotations
import sqlite3
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from app.deps import get_db
from app.db import queries as q
from app.template_env import templates

router = APIRouter()


@router.post("/inbox/{item_id}/resolve", response_class=HTMLResponse)
def inbox_resolve(item_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    q.resolve_inbox_item(conn, item_id)
    item = q.get_inbox_item(conn, item_id)
    if item is None:
        return HTMLResponse(content="")
    return templates.TemplateResponse(request, "home/_task_item.html", {"item": item, "done": True})
```

- [ ] **Step 7: CSS spacing**

In `app/templates/base.html`, next to the `.checklist` rules:

```css
    .checklist li input[type="checkbox"] { margin-right: 0.5rem; vertical-align: middle; }
```

- [ ] **Step 8: Run tests**

Run: `uv run pytest tests/test_routes_home.py tests/test_routes_inbox.py -v`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add app/templates/home/_task_item.html app/templates/home/index.html \
  app/routes/inbox.py app/db/queries.py app/templates/base.html \
  tests/test_routes_home.py tests/test_routes_inbox.py
git commit -m "ux: home Tasks list uses a resolve checkbox instead of a Dismiss button"
```

---

### Task 6: Full-suite green + backlog update

**Files:**
- Modify: `BACKLOG.md`

- [ ] **Step 1: Run the whole suite**

Run: `uv run pytest`
Expected: PASS. Fix any regression before continuing.

- [ ] **Step 2: Trim the delivered items from `BACKLOG.md`**

Remove these lines (now shipped):
- `ux: Rework Tasks. use checkbox list style instead of dismiss.`
- `fix: Bottom Statusbar overlaps with bulk update form. ensure no overlap and make status line dismissable`
- `ux: Link via the name column from fetch page to souces with highlight on target page`
- `ux: A single job task like reevaluate should link back the the job on the status and on the task details page`

Leave `ux: the task detail page needs to be reorganised…` (only the back-link
part was done) and the sources add-flow line does not exist in the backlog (it
came from the user directly) — nothing to remove for Task 1.

- [ ] **Step 3: Commit**

```bash
git add BACKLOG.md
git commit -m "chore: drop shipped UX items from backlog"
```

---

## Self-Review

**Spec coverage:**
- Spec §1 (sources add-flow) → Task 1 — container, retarget, wrapper, rewrite
  target param, confirm clear, panel-reopen clear. ✓
- Spec §2 (status bar) → Task 2 — `--bulk-bar-h` offset, ResizeObserver sync,
  dismiss button + signature. ✓
- Spec §3 (job task links) → Task 3 — `_task_summary` link, status bar anchor,
  log page backlink. ✓
- Spec §4 (fetch name link) → Task 4. ✓
- Spec §5 (tasks checklist) → Task 5 — partial, page, resolve route,
  `get_inbox_item`, CSS. ✓
- Spec "full suite green" + backlog hygiene → Task 6. ✓

**Placeholder scan:** no TBD/TODO; every code step shows the code. Test steps
that depend on repo-specific signatures (`insert_job`, `insert_source`,
`execute_task` config) tell the implementer to grep for the existing pattern
rather than guessing — acceptable, since those are established conventions the
fresh reviewer can read off neighbouring tests.

**Type consistency:** `_task_link(task)` defined once in Task 3, used in both
`_task_summary` and `task_log`. `_post_confirm_chunks(conn)` defined once in
Task 1. `get_inbox_item` signature matches the `get_inbox_item_by_task_id`
sibling. `_rewrite_panel.html` `target` var passed by both call sites. `done`
context var consistent between `_task_item.html` and its two include sites.
