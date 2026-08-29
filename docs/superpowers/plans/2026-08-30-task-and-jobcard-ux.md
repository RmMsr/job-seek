# Task & job-card UX pass — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make task pages state task age, give the action-needed page an honest Dismiss and actions that navigate away, reorganise the task log page, restructure the four resume panels, and fix the job-card permalink button.

**Architecture:** Pure server-rendered Jinja + htmx + a small vanilla-JS layer in `base.html`. New `age` Jinja filter; `_task_label` gains an explicit kind→label map; one new POST route (`/tasks/{id}/dismiss`); CSS classes replace inline styles in the resume panels; the job-card permalink becomes an absolutely-positioned click-to-copy control.

**Tech Stack:** Python 3.14, FastAPI, Jinja2, sqlite3, pytest + FastAPI `TestClient`. Run tests with `uv run pytest`.

## Global Constraints

- Every change happens in this worktree (`add-result-spacing` branch), never on `main`.
- TDD: failing test first, watch it fail, minimal code, watch it pass, commit.
- Commit after each task.
- Relative time strings are lowercase, space before "ago": `just now`, `5m ago`, `3h ago`, `2d ago`.
- The existing `time_ago` filter (day granularity: `today` / `N days ago`) is NOT modified — job-row ages must be unchanged.
- CSS colours use the existing custom properties (`--surface`, `--ground`, `--border`, `--text-secondary`, `--text-muted`, `--accent`); never hard-code hex.
- The four resume partials (`_rewrite_panel.html`, `jobs/_listing_confirm.html`, `sources/_detect_confirm.html`, `sources/_detect_mismatch.html`) render both inline (Jobs/Sources add-flow, in `#jobs-add-result` / `#sources-add-result`) and standalone on `/tasks/{id}/resume` — every change must work in both, and every existing `id=` and `data-progress-*` attribute must be preserved.

---

### Task 1: `age` Jinja filter

**Files:**
- Modify: `app/dates.py`
- Modify: `app/template_env.py:5` (import) and `:31` (filter registration)
- Test: `tests/test_dates.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `app.dates.age(ts: str | None) -> str`; Jinja filter `age`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_dates.py`:

```python
from app.dates import age


def test_age_none_and_empty_return_empty_string():
    assert age(None) == ""
    assert age("") == ""


def test_age_under_a_minute_is_just_now():
    ts = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    assert age(ts) == "just now"


def test_age_minutes():
    ts = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    assert age(ts) == "5m ago"


def test_age_hours():
    ts = (datetime.now(timezone.utc) - timedelta(hours=3, minutes=10)).isoformat()
    assert age(ts) == "3h ago"


def test_age_days():
    ts = (datetime.now(timezone.utc) - timedelta(hours=50)).isoformat()
    assert age(ts) == "2d ago"


def test_age_future_timestamp_is_just_now():
    ts = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    assert age(ts) == "just now"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_dates.py -q`
Expected: FAIL — `ImportError: cannot import name 'age'`.

- [ ] **Step 3: Implement**

Append to `app/dates.py`:

```python
def age(ts: str | None) -> str:
    """Render an ISO-8601 timestamp as a short coarse relative string:
    'just now', '5m ago', '3h ago', '2d ago'. Finer-grained than time_ago,
    for task ages that are usually minutes or hours old."""
    if not ts:
        return ""
    parsed = datetime.fromisoformat(ts)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    seconds = (datetime.now(timezone.utc) - parsed).total_seconds()
    if seconds < 60:
        return "just now"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"
```

- [ ] **Step 4: Register the filter**

In `app/template_env.py` change the import line:

```python
from app.dates import time_ago, age
```

and add after the `time_ago` registration:

```python
templates.env.filters["age"] = age
```

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest tests/test_dates.py -q`
Expected: PASS (all).

- [ ] **Step 6: Commit**

```bash
git add app/dates.py app/template_env.py tests/test_dates.py
git commit -m "feat: add fine-grained 'age' relative-time filter"
```

---

### Task 2: Task age on the start-page list

**Files:**
- Modify: `app/templates/home/_task_item.html`
- Modify: `app/templates/base.html` (add `.task-age` CSS near `.task-completed`, ~line 387)
- Test: `tests/test_routes_home.py`

**Interfaces:**
- Consumes: `age` filter (Task 1). `item.created_at` is present on every inbox-item dict (`SELECT *` in `q.get_unresolved_inbox_items` / `q.get_recent_resolved_inbox_items` / `q.get_inbox_item`).
- Produces: `<span class="task-age">` in both branches of `_task_item.html`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_routes_home.py`:

```python
def test_home_pending_task_shows_age(client, conn):
    _use_test_db(conn)
    q.create_inbox_item(conn, kind="task_followup", message="decide me", link="/x")
    resp = client.get("/")
    assert 'class="task-age"' in resp.text
    assert "just now" in resp.text
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_routes_home.py::test_home_pending_task_shows_age -q`
Expected: FAIL — `class="task-age"` not found.

- [ ] **Step 3: Implement the template**

Replace `app/templates/home/_task_item.html` with:

```html
{% if done %}
<li class="task-completed">
  <input type="checkbox" checked disabled>
  {{ item.message }}
  <span class="task-age">{{ item.created_at | age }}</span>
</li>
{% else %}
<li id="inbox-item-{{ item.id }}">
  <input type="checkbox" hx-post="/inbox/{{ item.id }}/resolve"
    hx-target="#inbox-item-{{ item.id }}" hx-swap="outerHTML"
    aria-label="Mark done">
  <a href="{{ item.link }}">{{ item.message }}</a>
  <span class="task-age">{{ item.created_at | age }}</span>
</li>
{% endif %}
```

- [ ] **Step 4: Add CSS**

In `app/templates/base.html`, immediately after the `.task-completed` rule (~line 387):

```css
    .task-age { margin-left: 0.5rem; font-size: 0.8rem; color: var(--text-muted); }
```

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest tests/test_routes_home.py tests/test_routes_inbox.py -q`
Expected: PASS (existing task tests still green — `_task_item.html` structure otherwise unchanged).

- [ ] **Step 6: Commit**

```bash
git add app/templates/home/_task_item.html app/templates/base.html tests/test_routes_home.py
git commit -m "feat: show task age in the start-page task list"
```

---

### Task 3: Human task labels that name the entity

**Files:**
- Modify: `app/routes/tasks.py` (`_task_label`, ~lines 25-30; add a `_job_title` helper)
- Test: `tests/test_routes_tasks.py`

**Interfaces:**
- Consumes: `q.get_source`, `q.get_job` (both `(conn, id) -> dict | None`).
- Produces: `_task_label(conn, task) -> str` with the expanded map below. Used by `_task_summary` (so `/tasks/active` + the status bar) and by Tasks 4 & 5.

- [ ] **Step 1: Update the tests**

In `tests/test_routes_tasks.py`, delete `test_tasks_active_labels_other_kinds_by_name`
(it asserted `job_reset` → `"job reset"`, which this task changes) and add:

```python
def test_task_label_job_reset_names_the_job(client, conn):
    src = q.insert_source(conn, "S", "https://e.com", "generic_listing")
    job_id = q.insert_job(conn, source_id=src, url="https://e.com/j", title="Backend Engineer", company="", raw_text="")
    q.enqueue_task(conn, kind="job_reset", params={"job_id": job_id})
    resp = client.get("/tasks/active")
    assert resp.json()["tasks"][0]["label"] == "Reset job: Backend Engineer"


def test_task_label_job_reset_without_job_falls_back(client, conn):
    q.enqueue_task(conn, kind="job_reset", params={})
    resp = client.get("/tasks/active")
    assert resp.json()["tasks"][0]["label"] == "Reset job"


def test_task_label_add_listing_source_names_it(client, conn):
    q.enqueue_task(conn, kind="job_add_listing_source",
                   params={"url": "https://x.com", "name": "Example Board", "fetcher_type": "generic_listing"})
    resp = client.get("/tasks/active")
    assert resp.json()["tasks"][0]["label"] == "Add listing source: Example Board"


def test_task_label_source_confirm_names_it(client, conn):
    q.enqueue_task(conn, kind="source_confirm",
                   params={"url": "https://x.com", "name": "Careers X", "fetcher_type": "generic_listing"})
    resp = client.get("/tasks/active")
    assert resp.json()["tasks"][0]["label"] == "Add source: Careers X"
```

Leave `test_tasks_active_labels_fetch_source_with_source_name` and
`test_tasks_active_falls_back_to_kind_when_source_missing` unchanged — the new
code must keep `"Fetch: finn.no"` and the `"fetch source"` fallback.

- [ ] **Step 2: Run to verify the new tests fail**

Run: `uv run pytest tests/test_routes_tasks.py -q -k "label"`
Expected: the four new tests FAIL (labels come back as `"job reset"` etc.); the two kept ones PASS.

- [ ] **Step 3: Implement**

In `app/routes/tasks.py`, replace `_task_label` and add `_job_title` above it:

```python
def _job_title(conn: sqlite3.Connection, job_id) -> str | None:
    if job_id is None:
        return None
    job = q.get_job(conn, job_id)
    return job["title"] if job else None


_JOB_ACTION_LABELS = {
    "job_reset": "Reset job",
    "job_reevaluate": "Re-evaluate job",
    "job_pass_as_new": "Pass job as new",
}


def _task_label(conn: sqlite3.Connection, task: dict) -> str:
    kind = task["kind"]
    params = task["params"]
    if kind == "fetch_source":
        source = q.get_source(conn, params.get("source_id"))
        return f"Fetch: {source['name']}" if source else "fetch source"
    if kind == "job_add_by_url":
        return "Add job by URL"
    if kind == "job_add_listing_source":
        name = params.get("name")
        return f"Add listing source: {name}" if name else "Add listing source"
    if kind == "source_detect":
        return "Detect source"
    if kind == "source_confirm":
        name = params.get("name")
        return f"Add source: {name}" if name else "Add source"
    if kind in _JOB_ACTION_LABELS:
        base = _JOB_ACTION_LABELS[kind]
        title = _job_title(conn, params.get("job_id"))
        return f"{base}: {title}" if title else base
    if kind in ("jobs_bulk_reset", "jobs_bulk_reevaluate"):
        n = len(params.get("job_ids") or [])
        verb = "Reset" if kind == "jobs_bulk_reset" else "Re-evaluate"
        return f"{verb} {n} job{'s' if n != 1 else ''}"
    return kind.replace("_", " ")
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_routes_tasks.py -q`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add app/routes/tasks.py tests/test_routes_tasks.py
git commit -m "feat: task labels name the source or job explicitly"
```

---

### Task 4: Reorganise the task log page

**Files:**
- Modify: `app/routes/tasks.py` (`task_log` route, ~lines 77-85 — pass `label`)
- Modify: `app/templates/tasks/log.html`
- Modify: `app/templates/base.html` (add `.task-meta` + `.task-log` CSS near `.task-age`)
- Test: `tests/test_routes_tasks.py`

**Interfaces:**
- Consumes: `_task_label` (Task 3), `age` filter (Task 1). `task` dict has `status`, `error`, `created_at`, `started_at`, `finished_at`.
- Produces: log page with `<h1>{{ label }}</h1>`, a `.task-meta` `<dl>`, and `<pre class="task-log" id="task-log-lines">`. `id="task-log-status"` retained.

- [ ] **Step 1: Update the existing test + add one**

In `tests/test_routes_tasks.py`, in `test_task_log_page_renders_lines_and_status` replace:

```python
    assert "<h1>Task: fetch source</h1>" in resp.text
```

with:

```python
    assert "<h1>fetch source</h1>" in resp.text
    assert 'class="task-meta"' in resp.text
```

Add:

```python
def test_task_log_page_title_names_the_job(client, conn):
    src = q.insert_source(conn, "S", "https://e.com", "generic_listing")
    job_id = q.insert_job(conn, source_id=src, url="https://e.com/j", title="Data Lead", company="", raw_text="")
    task = q.enqueue_task(conn, kind="job_reevaluate", params={"job_id": job_id, "filter_ctx": {}})
    resp = client.get(f"/tasks/{task['id']}/log")
    assert "<h1>Re-evaluate job: Data Lead</h1>" in resp.text
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_routes_tasks.py -q -k "log_page"`
Expected: FAIL — old `<h1>` text gone / `task-meta` missing.

- [ ] **Step 3: Pass `label` from the route**

In `app/routes/tasks.py`, `task_log`:

```python
    return templates.TemplateResponse(
        request, "tasks/log.html",
        {"task": task, "lines": lines, "label": _task_label(conn, task), "job_link": _task_link(task)},
    )
```

- [ ] **Step 4: Rewrite `app/templates/tasks/log.html`**

```html
{% extends "base.html" %}
{% block title %}{{ label }} — Job Seek{% endblock %}
{% block content %}
<h1>{{ label }}</h1>
<dl class="task-meta">
  <dt>Status</dt>
  <dd><strong id="task-log-status">{{ task.status }}</strong>{% if task.error %} — {{ task.error }}{% endif %}</dd>
  <dt>Age</dt>
  <dd>{{ task.created_at | age }}</dd>
  {% if task.started_at %}<dt>Started</dt><dd>{{ task.started_at | age }}</dd>{% endif %}
  {% if task.finished_at %}<dt>Finished</dt><dd>{{ task.finished_at | age }}</dd>{% endif %}
</dl>
<pre id="task-log-lines" class="task-log">{% for line in lines %}{{ line }}
{% endfor %}</pre>
{% if task.status in ("queued", "running") %}
<script>
  (function () {
    var pre = document.getElementById("task-log-lines");
    var statusEl = document.getElementById("task-log-status");
    function refresh() {
      fetch("/tasks/{{ task.id }}").then(function (r) { return r.json(); }).then(function (t) {
        pre.textContent = (t.log || "").trim();
        statusEl.textContent = t.status;
        if (t.status === "done" || t.status === "failed") location.reload();
      });
    }
    setInterval(refresh, 2000);
  })();
</script>
{% endif %}
{% if job_link %}
<p><a href="{{ job_link }}">← Back to the job</a></p>
{% endif %}
<p><a href="/">Back to Start</a></p>
{% endblock %}
```

- [ ] **Step 5: Add CSS**

In `app/templates/base.html`, after the `.task-age` rule:

```css
    .task-meta { display: grid; grid-template-columns: max-content 1fr; gap: 0.2rem 0.9rem; margin: 0 0 1.25rem; }
    .task-meta dt { color: var(--text-muted); }
    .task-meta dd { margin: 0; }
    .task-log { background: var(--ground); border: 1px solid var(--border); border-radius: 6px;
      padding: 0.75rem 1rem; white-space: pre-wrap; overflow-x: auto; }
```

- [ ] **Step 6: Run to verify pass**

Run: `uv run pytest tests/test_routes_tasks.py -q`
Expected: PASS (all).

- [ ] **Step 7: Commit**

```bash
git add app/routes/tasks.py app/templates/tasks/log.html app/templates/base.html tests/test_routes_tasks.py
git commit -m "feat: reorganise the task log page — human title, structured metadata"
```

---

### Task 5: Dismiss route + action-needed page structure

**Files:**
- Modify: `app/routes/tasks.py` (import `RedirectResponse`; new `task_dismiss` route; `task_resume` passes `task` + `label`)
- Modify: `app/templates/tasks/resume.html`
- Modify: `app/templates/base.html` (CSS: hide panel Cancel on the resume page; `.resume-subtitle`, `.resume-dismiss`)
- Test: `tests/test_routes_tasks.py`

**Interfaces:**
- Consumes: `q.get_inbox_item_by_task_id(conn, task_id) -> dict | None` (dict has `id`, `resolved_at`), `q.resolve_inbox_item(conn, item_id)`, `_task_label` (Task 3), `age` (Task 1).
- Produces: `POST /tasks/{task_id}/dismiss` → `303` to `/`. `resume.html` renders `<form ... action="/tasks/{id}/dismiss">` and a `.resume-subtitle` with `.task-age`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_routes_tasks.py`:

```python
def test_task_dismiss_resolves_inbox_item_and_redirects_to_start(client, conn):
    task = q.enqueue_task(conn, kind="fetch_source", params={})
    q.complete_task(conn, task["id"], {"resume_html": "<p>x</p>"})
    q.create_inbox_item(conn, kind="task_followup", message="x", link="/y", task_id=task["id"])
    resp = client.post(f"/tasks/{task['id']}/dismiss", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    assert q.count_unresolved_inbox_items(conn) == 0


def test_task_dismiss_without_inbox_item_still_redirects(client, conn):
    task = q.enqueue_task(conn, kind="fetch_source", params={})
    resp = client.post(f"/tasks/{task['id']}/dismiss", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"


def test_task_resume_shows_age_and_dismiss_form(client, conn):
    task = q.enqueue_task(conn, kind="fetch_source", params={})
    q.complete_task(conn, task["id"], {"resume_html": "<p>confirm me</p>"})
    resp = client.get(f"/tasks/{task['id']}/resume")
    assert f'action="/tasks/{task["id"]}/dismiss"' in resp.text
    assert 'class="task-age"' in resp.text
    assert ">Dismiss</button>" in resp.text
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_routes_tasks.py -q -k "dismiss or resume_shows"`
Expected: FAIL — route 405/404, template lacks the form.

- [ ] **Step 3: Implement the route**

In `app/routes/tasks.py`, extend the import:

```python
from fastapi.responses import HTMLResponse, RedirectResponse
```

Add the route (after `task_resume`):

```python
@router.post("/tasks/{task_id}/dismiss")
def task_dismiss(task_id: int, conn: sqlite3.Connection = Depends(get_db)):
    item = q.get_inbox_item_by_task_id(conn, task_id)
    if item is not None and item["resolved_at"] is None:
        q.resolve_inbox_item(conn, item["id"])
    return RedirectResponse("/", status_code=303)
```

Update `task_resume` to pass `task` and `label`:

```python
    return templates.TemplateResponse(
        request, "tasks/resume.html", {
            "task": task,
            "label": _task_label(conn, task),
            "resume_html": task["result"]["resume_html"],
            "action_message": task["result"].get("action_message"),
            "inbox_item_id": inbox_item["id"] if inbox_item else None,
        }
    )
```

- [ ] **Step 4: Rewrite `app/templates/tasks/resume.html`**

```html
{% extends "base.html" %}
{% block title %}Action needed — Job Seek{% endblock %}
{% block content %}
<h1>Action needed</h1>
<p class="resume-subtitle">{{ label }} &middot; <span class="task-age">{{ task.created_at | age }}</span></p>
{% if action_message %}
<p class="resume-message">{{ action_message }}</p>
{% endif %}
<div id="resume-page" style="margin-top:1.25rem;"
     {% if inbox_item_id %}data-inbox-item-id="{{ inbox_item_id }}"{% endif %}>
  {{ resume_html | safe }}
</div>
<form method="post" action="/tasks/{{ task.id }}/dismiss" class="resume-dismiss">
  <button type="submit" class="btn btn-subtle">Dismiss</button>
</form>
<p style="margin-top:1.5rem;"><a href="/">Back to Start</a></p>
{% endblock %}
```

- [ ] **Step 5: Add CSS**

In `app/templates/base.html`, after the `.task-log` rule:

```css
    .resume-subtitle { margin: -0.5rem 0 0; color: var(--text-muted); font-size: 0.9rem; }
    .resume-subtitle .task-age { margin-left: 0; }
    .resume-message { color: var(--text-secondary); }
    .resume-dismiss { margin-top: 1rem; }
    /* On the standalone action-needed page the panel's own inline Cancel link
       is replaced by the page's Dismiss button. */
    #resume-page a.btn-subtle { display: none; }
```

- [ ] **Step 6: Run to verify pass**

Run: `uv run pytest tests/test_routes_tasks.py -q`
Expected: PASS (all).

- [ ] **Step 7: Commit**

```bash
git add app/routes/tasks.py app/templates/tasks/resume.html app/templates/base.html tests/test_routes_tasks.py
git commit -m "feat: action-needed page — task age subtitle + honest Dismiss"
```

---

### Task 6: Action-needed page — pressing an action navigates away

**Files:**
- Modify: `app/templates/base.html` (`finishWatched`, ~lines 532-588)

**Interfaces:**
- Consumes: `#resume-page` element with optional `data-inbox-item-id` (Task 5). The outer-closure vars `resumePage` (~line 527) and `resumeInboxItemId` (~line 528) already exist.
- Produces: on the resume page, a completed action resolves the follow-up item and hard-navigates back (referrer, same-origin, different path) or to `/`.

- [ ] **Step 1: Restructure `finishWatched`**

In `app/templates/base.html`, in `finishWatched(taskId, task)`, immediately after the existing `if (task.status === "failed") { ... return; }` block, insert:

```javascript
        // The standalone action-needed page has no in-page target to swap into.
        // The action succeeded, so leave: clear the follow-up item and go back
        // where we came from, reloaded.
        if (resumePage) {
          var leave = function () {
            var dest = "/";
            var ref = document.referrer;
            if (ref) {
              try {
                var u = new URL(ref);
                if (u.origin === window.location.origin && u.pathname !== window.location.pathname) {
                  dest = ref;
                }
              } catch (e) {}
            }
            window.location.assign(dest);
          };
          if (resumeInboxItemId) {
            fetch("/inbox/" + resumeInboxItemId + "/resolve", { method: "POST" }).then(leave, leave);
          } else {
            leave();
          }
          return;
        }
```

- [ ] **Step 2: Remove the now-dead trailing resolve**

Delete the block at the end of `finishWatched` (~lines 585-587):

```javascript
        if (resumeInboxItemId) {
          fetch("/inbox/" + resumeInboxItemId + "/resolve", { method: "POST" });
        }
```

- [ ] **Step 3: Manual verification (no JS test harness)**

Start the dev server (see `run-dev-server` skill). Then:
1. On `/jobs`, click **+ Add**, paste `https://www.linkedin.com/jobs/search/?keywords=engineer`, click **Add**.
2. Follow the inbox item from `/` to `/tasks/{id}/resume`.
3. Click **Use suggested URL** → the browser navigates to `/` (reloaded), and the follow-up item is resolved or replaced by the next step's item.
4. Click **Dismiss** on another such task → lands on `/`, task gone from the list.
5. Confirm the inline Jobs/Sources add-flow still swaps in place (no regression): the same panel used inline still works.

- [ ] **Step 4: Run the full suite (regression check)**

Run: `uv run pytest -q`
Expected: PASS (JS not covered, but nothing server-side regressed).

- [ ] **Step 5: Commit**

```bash
git add app/templates/base.html
git commit -m "feat: action-needed page actions navigate back with reload"
```

---

### Task 7: Restructure the four resume panels

**Files:**
- Modify: `app/templates/_rewrite_panel.html`
- Modify: `app/templates/jobs/_listing_confirm.html`
- Modify: `app/templates/sources/_detect_confirm.html`
- Modify: `app/templates/sources/_detect_mismatch.html`
- Modify: `app/templates/base.html` (add `.resume-panel*` CSS after `.resume-dismiss`)
- Test: `tests/test_routes_sources.py`, `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: each panel wrapped in `.resume-panel` with `.resume-panel-field` groups and a `.resume-panel-actions` row. Every existing `id=`, `class` hook (`listing-confirm`, `add-job-by-url`, etc.), and `data-progress-*` attribute preserved. `#rewrite-panel` id retained on the rewrite panel root.

- [ ] **Step 1: Update the structure tests**

In `tests/test_routes_sources.py`, `test_detect_confirm_panel_has_flex_wrapper` — rename and change the assertion:

```python
def test_detect_confirm_panel_uses_resume_panel_structure(conn):
    with patch("app.routes.sources.detect_listing_page") as mock_fetch:
        fetched = _run_detect(conn, _SLACK_URL)
        mock_fetch.assert_not_called()
    html = fetched["result"]["html_chunks"][0]
    assert 'id="detect-confirm"' in html
    assert 'class="resume-panel"' in html
    assert 'class="resume-panel-actions"' in html
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_routes_sources.py -q -k "resume_panel_structure"`
Expected: FAIL — `resume-panel` class not present.

- [ ] **Step 3: Add CSS**

In `app/templates/base.html`, after `.resume-dismiss`:

```css
    .resume-panel { display: flex; flex-direction: column; gap: 1rem; max-width: 640px;
      padding: 1rem 1.25rem; border: 1px solid var(--border); border-radius: 8px; background: var(--surface); }
    .resume-panel > p { margin: 0; line-height: 1.5; }
    .resume-panel-field { display: flex; flex-direction: column; gap: 0.3rem; }
    .resume-panel-field > label { font-size: 0.85em; color: var(--text-secondary); }
    .resume-panel-field input[type="text"], .resume-panel-field input[type="url"] { width: 100%; }
    .resume-panel-note { font-size: 0.8em; text-transform: uppercase; letter-spacing: 0.04em; color: var(--text-muted); }
    .resume-panel-actions { display: flex; gap: 0.5rem; flex-wrap: wrap; align-items: center; }
    .resume-panel-warn { color: var(--reject, #dc3545); }
```

- [ ] **Step 4: Rewrite `app/templates/_rewrite_panel.html`**

```html
<div id="rewrite-panel" class="resume-panel">
  <p>{{ reason }}</p>
  <div class="resume-panel-field">
    <span class="resume-panel-note">Suggested source</span>
    <code style="font-size:0.85em; overflow-wrap:anywhere; color:var(--text-secondary);">{{ suggested_url }}</code>
  </div>
  <input type="hidden" id="rw-suggested" value="{{ suggested_url }}">
  <input type="hidden" id="rw-original" value="{{ original_url }}">
  <input type="hidden" id="rw-skip" value="1">
  <div class="resume-panel-actions">
    <a href="{{ cancel_url }}" class="btn btn-subtle">Cancel</a>
    <button type="button" class="btn btn-subtle"
      data-progress-url="{{ detect_url }}"
      data-progress-body-url="#rw-original"
      data-progress-body-skip_rewrite="#rw-skip"
      data-progress-target="{{ target }}"
      {% if action_target %}data-progress-action-target="{{ action_target }}"{% endif %}
      data-progress-display="#rewrite-progress">Add original anyway</button>
    <button type="button" class="btn btn-primary"
      data-progress-url="{{ detect_url }}"
      data-progress-body-url="#rw-suggested"
      data-progress-target="{{ target }}"
      {% if action_target %}data-progress-action-target="{{ action_target }}"{% endif %}
      data-progress-display="#rewrite-progress">Use suggested URL</button>
  </div>
  <span id="rewrite-progress" class="reset-progress" aria-live="polite"></span>
</div>
```

- [ ] **Step 5: Rewrite `app/templates/jobs/_listing_confirm.html`**

```html
<form class="listing-confirm resume-panel">
  <p>Detected {{ link_count }} job posting link{{ 's' if link_count != 1 else '' }} at <strong>{{ domain }}</strong>.</p>
  <input type="hidden" id="listing-url" value="{{ url }}">
  <input type="hidden" id="listing-fetcher-type" value="{{ fetcher_type }}">
  <div class="resume-panel-field">
    <label for="listing-name">Name</label>
    <input type="text" id="listing-name" value="{{ default_name }}" class="text-input">
  </div>
  <div class="resume-panel-actions">
    <a href="/jobs" class="btn btn-subtle">Cancel</a>
    <button type="button" class="btn btn-primary"
      data-progress-url="/jobs/add-listing-source{% if filter_status is defined %}?status={{ filter_status or '' }}&content_type={{ filter_content_type or '' }}{% endif %}"
      data-progress-body-url="#listing-url"
      data-progress-body-name="#listing-name"
      data-progress-body-fetcher_type="#listing-fetcher-type"
      data-progress-target="#jobs-content"
      data-progress-action-target="#jobs-add-result"
      data-progress-display="#listing-confirm-progress">Keep as source &amp; fetch</button>
  </div>
  <span id="listing-confirm-progress" class="reset-progress" aria-live="polite"></span>
</form>
```

- [ ] **Step 6: Rewrite `app/templates/sources/_detect_confirm.html`**

```html
<div id="detect-confirm" class="resume-panel">
  <p>Detected type: <strong>{{ fetcher_type }}</strong></p>
  <input type="hidden" id="detect-url" value="{{ url }}">
  <input type="hidden" id="detect-fetcher-type" value="{{ fetcher_type }}">
  <div class="resume-panel-field">
    <label for="detect-name">Name</label>
    <input type="text" id="detect-name" value="{{ name }}" class="text-input">
  </div>
  <div class="resume-panel-actions">
    <a href="/sources" class="btn btn-subtle">Cancel</a>
    <button type="button" class="btn btn-primary"
      data-progress-url="/sources/detect/confirm"
      data-progress-body-url="#detect-url"
      data-progress-body-name="#detect-name"
      data-progress-body-fetcher_type="#detect-fetcher-type"
      data-progress-oob
      data-progress-display="#detect-confirm-progress">Add source</button>
  </div>
  <span id="detect-confirm-progress" class="reset-progress" aria-live="polite"></span>
</div>
```

- [ ] **Step 7: Rewrite `app/templates/sources/_detect_mismatch.html`**

```html
<div class="resume-panel">
  <p class="resume-panel-warn">This page looks like a single job posting, not a listing of multiple jobs.</p>
  <input type="hidden" id="detect-url" value="{{ url }}">
  <input type="hidden" id="detect-name" value="{{ name }}">
  <input type="hidden" id="detect-fetcher-type" value="generic_listing">
  <div class="resume-panel-actions">
    <a href="/sources" class="btn btn-subtle">Cancel</a>
    <button type="button" class="btn"
      data-progress-url="/jobs/add-by-url"
      data-progress-body-url="#detect-url"
      data-progress-display="#detect-mismatch-progress">Add as job instead</button>
    <button type="button" class="btn"
      data-progress-url="/sources/detect/confirm"
      data-progress-body-url="#detect-url"
      data-progress-body-name="#detect-name"
      data-progress-body-fetcher_type="#detect-fetcher-type"
      data-progress-oob
      data-progress-display="#detect-mismatch-progress">Add as source anyway</button>
  </div>
  <span id="detect-mismatch-progress" class="reset-progress" aria-live="polite"></span>
</div>
```

- [ ] **Step 8: Run to verify pass + full regression**

Run: `uv run pytest tests/test_routes_sources.py tests/test_routes_jobs.py -q`
Expected: PASS. If any test asserted an old inline style string (e.g. `flex-wrap:wrap`, `display:flex`) on these panels, update that assertion to the corresponding class (`resume-panel-actions` / `resume-panel`) — do not weaken coverage, just retarget it.

- [ ] **Step 9: Manual check**

With the dev server: trigger each panel (LinkedIn URL → rewrite; a multi-posting listing URL on `/jobs` → listing-confirm; a new URL on `/sources` → detect-confirm; a single-posting URL on `/sources` → detect-mismatch). Verify each reads well both inline and at `/tasks/{id}/resume`, and the resume page shows exactly one Dismiss (no leftover Cancel).

- [ ] **Step 10: Commit**

```bash
git add app/templates/_rewrite_panel.html app/templates/jobs/_listing_confirm.html \
  app/templates/sources/_detect_confirm.html app/templates/sources/_detect_mismatch.html \
  app/templates/base.html tests/test_routes_sources.py
git commit -m "feat: give the resume panels a vertical, labelled structure"
```

---

### Task 8: Job-card permalink button — fixed position + click-to-copy

**Files:**
- Modify: `app/templates/jobs/_row.html:16-21`
- Modify: `app/templates/jobs/_feedback.html:15-19`
- Modify: `app/templates/base.html` (`.job-link-icon` CSS ~line 212; mobile override ~line 418; new JS handler)
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `.job-link-icon` absolutely positioned top-right in both collapsed and expanded cards; `.job-card` gets `position: relative` context (it already is on `.job-row`). New delegated click handler copies `location.origin + href` to the clipboard, flashing `.job-link-icon--copied` for ~1.2s, falling back to navigation.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_routes_jobs.py` (near the other `_row` render tests):

```python
def test_job_row_permalink_is_absolute_positioned_copy_control(client, conn):
    src = q.insert_source(conn, "S", "https://e.com", "generic_listing")
    job_id = q.insert_job(conn, source_id=src, url="https://e.com/j", title="Role", company="", raw_text="")
    resp = client.get("/jobs")
    assert f'href="/jobs/{job_id}"' in resp.text
    assert 'class="job-link-icon"' in resp.text
    assert 'data-permalink' in resp.text
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_routes_jobs.py -q -k "permalink_is_absolute"`
Expected: FAIL — `data-permalink` not present.

- [ ] **Step 3: `_row.html` — mark the link, drop it from header flow**

In `app/templates/jobs/_row.html`, replace the `.job-row-header` block (lines 16-21) with:

```html
    <div class="job-row-header">
      <h3 class="job-title">{{ job.title or "(no title)" }}</h3>
      {% if job.company %}<span class="job-company">{{ job.company }}</span>{% endif %}
      {% if job.published_at %}<span class="job-age">{{ job.published_at | time_ago }}</span>{% endif %}
    </div>
    <a href="/jobs/{{ job.id }}" class="job-link-icon" data-permalink
      title="Copy link to this job" aria-label="Copy link to this job"
      onclick="event.stopPropagation()">&#128279;</a>
```

(The `<a>` moves out of `.job-row-header` to be a direct child of `.job-row-content`, which is the positioned-context ancestor via `.job-row`.)

- [ ] **Step 4: `_feedback.html` — same treatment**

In `app/templates/jobs/_feedback.html`, replace the `.job-detail-title-row` block (lines 15-19) with:

```html
    <div class="job-detail-title-row">
      <h2 class="job-detail-title">{{ job.title or "(no title)" }}</h2>
      {% if job.published_at %}<span class="job-age">{{ job.published_at | time_ago }}</span>{% endif %}
    </div>
```

and add, as a direct child of the `<article>` (right after the `{% endif %}` that closes the `job-select-wrap`, before `<div class="job-detail-header" ...>`):

```html
  <a href="/jobs/{{ job.id }}" class="job-link-icon" data-permalink
    title="Copy link to this job" aria-label="Copy link to this job"
    onclick="event.stopPropagation()">&#128279;</a>
```

- [ ] **Step 5: CSS**

In `app/templates/base.html`, replace the `.job-link-icon` / `.job-link-icon:hover` rules (~lines 212-213) with:

```css
    .job-link-icon { position: absolute; top: 0.35rem; right: 0.5rem; z-index: 3;
      text-decoration: none; font-size: 0.85em; padding: 0.2rem 0.35rem; line-height: 1;
      border-radius: 4px; color: var(--text-muted); }
    .job-row-content:has(.job-select-wrap) .job-link-icon,
    .job-row-expanded:has(.job-select-wrap) .job-link-icon { right: 2.1rem; }
    .job-link-icon:hover { background: var(--ground); color: var(--accent); }
    .job-link-icon--copied { color: var(--accent); }
```

Remove the `padding-right` reservation that made room for the inline icon:
`.job-row-header` (line 208) → `padding-right: 0;` (or drop the property);
`.job-detail-title-row` (line 221) → same.

In the mobile block (~line 418), remove `.job-row-header .job-link-icon { float: right; margin: 0 0 0.3rem 0.5rem; }` — the icon is now absolute in every viewport.

- [ ] **Step 6: JS handler**

In `app/templates/base.html`, inside the existing top-level `document.body.addEventListener("click", ...)` handler (the same one that handles `.add-panel-trigger`, ~line 996), add at the top of the callback:

```javascript
        var permalink = e.target.closest("a.job-link-icon[data-permalink]");
        if (permalink) {
          e.preventDefault();
          e.stopPropagation();
          var full = new URL(permalink.getAttribute("href"), window.location.origin).href;
          var flash = function () {
            permalink.classList.add("job-link-icon--copied");
            setTimeout(function () { permalink.classList.remove("job-link-icon--copied"); }, 1200);
          };
          if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(full).then(flash, function () { window.location.assign(full); });
          } else {
            window.location.assign(full);
          }
          return;
        }
```

- [ ] **Step 7: Run to verify pass + regression**

Run: `uv run pytest tests/test_routes_jobs.py -q`
Expected: PASS. Update any test that asserted the old inline position / `title="Direct link to this job"` text.

- [ ] **Step 8: Manual check**

Dev server → `/jobs`: the 🔗 sits top-right, just left of the checkbox, and stays put when a card is expanded/collapsed. Click it → brief highlight, link is in the clipboard (paste to confirm). On `/jobs/{id}` (detail, no checkbox) it sits at the edge.

- [ ] **Step 9: Commit**

```bash
git add app/templates/jobs/_row.html app/templates/jobs/_feedback.html app/templates/base.html tests/test_routes_jobs.py
git commit -m "feat: job-card permalink — fixed top-right position, click-to-copy"
```

---

## Final verification

- [ ] `uv run pytest -q` — whole suite green.
- [ ] Manual pass covering Tasks 6, 7, 8 against a throwaway-DB dev server.
- [ ] Update `BACKLOG.md`: remove the "task detail page needs to be reorganised" and "permalink button of a job card" lines. Commit.
- [ ] Hand the running dev server URL to the user for a UI review before merging (per `CLAUDE.md`).

## Self-review notes

- Spec §1-§6 each map to Tasks 1-8 (§2 spans Tasks 2/4/5; §3 spans Tasks 5/6). §3a redirect target simplified to `/` (the Dismiss form's own `Referer` is the resume page, so honouring it would loop) — the "back, with reload" behaviour lives in the JS action path (Task 6), which is the case the user asked about.
- `time_ago` untouched; `age` is additive.
- No new dependencies.
