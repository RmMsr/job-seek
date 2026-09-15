# Jobs Toolbar Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the jobs page's wrapping, cluttered filter/tab toolbar with a single-row desktop layout (4 primary status tabs + More/Combine/Filters popovers) and a two-row mobile layout (native status `<select>` + compact utility row).

**Architecture:** Template + CSS only. `app/job_filter.py`'s `JobFilter` already supports everything needed (`for_status` for solo-select, `with_status_toggled` for multi-select) — no route or query logic changes. The toolbar becomes one flex container (`app/templates/jobs/_content.html`) whose children are shown/hidden by a single CSS breakpoint (`app/templates/base.html`), reusing the exact same DOM nodes at every viewport width rather than duplicating markup per breakpoint.

**Tech Stack:** FastAPI + Jinja2 templates, htmx (no JS framework), plain CSS custom properties already defined in `base.html`.

## Global Constraints

- No backend/route/`JobFilter` changes — this is presentational only (per the spec).
- No new JS — use native `<details>`/`<summary>` and `<select>`; the existing long-press-to-toggle JS becomes dead code and is deleted, not adapted.
- Breakpoint is `max-width: 600px`, matching the existing convention already used for `.job-row-content` in `base.html`.
- Every named form control (`scenario`, `source_id`, `org`, `order`) must remain a single DOM node — no per-breakpoint duplicate markup for these (avoids `hx-include="[name='...']"` collisions elsewhere on the page).
- All three popovers (More, Combine, Filters) are plain `<details>` with no `open` attribute — they close after every action. Do not add `hx-preserve` or an auto-`open` heuristic (both were considered in the design spec and rejected — see `docs/superpowers/specs/2026-09-14-jobs-toolbar-redesign-design.md`, "Why these specific trade-offs").
- Six of the seven per-status `id="count-*"` spans are unchanged (just relocated in the DOM); the Combine panel's per-status counts are plain (non-`id`, non-oob) text — do not add oob ids there.

---

### Task 1: Toolbar markup + counts + test updates

**Files:**
- Modify: `app/templates/jobs/_content.html` (lines 19-98 — the `filter-row`/`filter-bar` block; everything else in the file is unchanged)
- Modify: `app/templates/jobs/_counts_oob.html` (add one line)
- Modify: `tests/test_routes_jobs.py` (update assertions that reference the old markup's class names)

**Interfaces:**
- Consumes (unchanged, already exist): `JobFilter.for_status(tab)`, `.with_status_toggled(tab)`, `.with_statuses(tabs)`, `.is_multi`, `.is_narrowed`, `.searching`, `.scenario_id`/`.scenario_none`/`.source_id`/`.org`/`.org_none`/`.order`/`.statuses`, `.cleared()`, `.query_params()` — all in `app/job_filter.py`, none of it changes. Context vars already passed by `app/routes/jobs.py`: `filter`, `counts`, `scenarios`, `sources`, `companies`, `jobs`, `stale_jobs`, `active_tabs` (optional).
- Produces (new, consumed by Task 2's CSS): class names `tb-toolbar`, `tb-tabs`, `tb-tab` (+ `.active`), `tb-count`, `tb-trigger` (+ `.active`), `tb-chev`, `tb-badge`, `tb-panel` (+ `.tb-panel-more`), `tb-panel-label`, `tb-check-row`, `tb-count-plain`, `tb-more-link` (+ `.active`), `tb-clear-link`, `tb-status-select-wrap`, `tb-status-select`, `tb-utility`, `tb-select-all`. New element id `count-more-badge`.

- [ ] **Step 1: Update the test assertions to describe the new markup**

  These are existing tests in `tests/test_routes_jobs.py` that currently assert on the old `.filter-row`/`.filter-bar`/`.tab-item`/`.tab-check`/`.tab-count`/`.filter-links` markup. Updating them now (before touching the template) means they'll fail for the *expected* reason — old markup still present — which we verify in Step 2.

  **1a. Global rename `tab-count` → `tb-count`** (23 occurrences — the `id="count-*"` spans keep their ids, only the class name changes). Run:

  ```bash
  sed -i 's/tab-count/tb-count/g' tests/test_routes_jobs.py
  ```

  **1b. Global rename `filter-bar` → `tb-toolbar`** (2 occurrences, both `<div class="filter-bar">` presence checks used only as "the toolbar/counts are in this chunk" markers). Run:

  ```bash
  sed -i 's/filter-bar/tb-toolbar/g' tests/test_routes_jobs.py
  ```

  **1c. `test_search_returns_matches_across_statuses`** — find:

  ```python
    assert 'class="filter-links' in html                # status tabs stay visible during search
  ```

  replace with:

  ```python
    assert 'class="tb-tabs"' in html                     # status tabs stay visible during search
  ```

  **1d. `test_search_blank_query_is_normal_tabbed_view`** — find:

  ```python
    assert 'class="filter-links"' in html
  ```

  replace with:

  ```python
    assert 'class="tb-tabs"' in html
  ```

  **1e. `test_tab_checkbox_link_toggles_one_status`** — find:

  ```python
  def test_tab_checkbox_link_toggles_one_status(client, conn):
      _seed(conn)
      html = client.get("/jobs?status=new").text
      # every tab renders its marker, even in single-status mode (no hover reveal)
      assert html.count('class="tab-check"') == 7
      # an "add Accepted to the view" control pointing at status=new,accepted
      assert "status=new%2Caccepted" in html or "status=new,accepted" in html
  ```

  replace with:

  ```python
  def test_tab_checkbox_link_toggles_one_status(client, conn):
      _seed(conn)
      html = client.get("/jobs?status=new").text
      # the Combine panel always renders a toggle checkbox for all 7 statuses,
      # even in single-status mode (multi-select is secondary, not hidden)
      assert html.count('class="tb-check-row"') == 7
      # an "add Accepted to the view" checkbox pointing at status=new,accepted
      assert "status=new%2Caccepted" in html or "status=new,accepted" in html
  ```

  **1f. `test_search_view_keeps_tab_bar`** — find:

  ```python
  def test_search_view_keeps_tab_bar(client, conn):
      _seed_searchable(conn, q.insert_source(conn, "s", "https://s", "generic_listing"), "http://s/1", "Delta Engineer")
      html = client.get("/jobs?q=delta").text
      # tabs stay visible during search, and render with checkboxes (is-multi)
      # so the seeded scope is visible and toggleable
      assert 'class="filter-links is-multi"' in html
      assert 'name="order"' not in html                # sort still hidden while searching
  ```

  replace with:

  ```python
  def test_search_view_keeps_tab_bar(client, conn):
      _seed_searchable(conn, q.insert_source(conn, "s", "https://s", "generic_listing"), "http://s/1", "Delta Engineer")
      html = client.get("/jobs?q=delta").text
      # tabs stay visible during search; the Combine panel (always rendered,
      # not search-specific) keeps the seeded scope toggleable
      assert 'class="tb-tabs"' in html
      assert 'name="order"' not in html                # sort still hidden while searching
  ```

  **1g. `test_search_tab_bar_shows_seeded_scope`** — find:

  ```python
  def test_search_tab_bar_shows_seeded_scope(client, conn):
      sid = q.insert_source(conn, "s", "https://s", "generic_listing")
      _seed_searchable(conn, sid, "http://s/1", "Kappa Engineer")
      html = client.get("/jobs?q=kappa").text
      # the seeded buckets (new/lead/accepted/pending/rejected) render active --
      # "pending" now has a tab_defs entry (this step) and was already in
      # _SEARCH_SEED_TABS (Task 5), so it finally renders as an active tab-item.
      assert html.count('class="tab-item active"') == 5
      # a checkbox toggle to add Trash carries the whole seeded scope (pending included) + the query
      assert ("status=new%2Clead%2Caccepted%2Cpending%2Crejected%2Ctrash" in html
              or "status=new,lead,accepted,pending,rejected,trash" in html)
      assert "q=kappa" in html
  ```

  replace with:

  ```python
  def test_search_tab_bar_shows_seeded_scope(client, conn):
      sid = q.insert_source(conn, "s", "https://s", "generic_listing")
      _seed_searchable(conn, sid, "http://s/1", "Kappa Engineer")
      html = client.get("/jobs?q=kappa").text
      # the seeded buckets (new/lead/accepted/pending/rejected) render active --
      # the first four are primary tabs, "rejected" is archived (shown active
      # inside the More panel).
      assert html.count('class="tb-tab active"') == 4
      assert 'class="tb-more-link active"' in html
      # a checkbox toggle to add Trash carries the whole seeded scope (pending included) + the query
      assert ("status=new%2Clead%2Caccepted%2Cpending%2Crejected%2Ctrash" in html
              or "status=new,lead,accepted,pending,rejected,trash" in html)
      assert "q=kappa" in html
  ```

  **1h. `test_job_feedback_updates_counts_oob`** — Step 1a's `sed` rename already turned this test's four assertions from `tab-count` to `tb-count`; it needs one more assertion appended so the new `count-more-badge` oob span is covered too. Find:

  ```python
  def test_job_feedback_updates_counts_oob(client, conn):
      sid, jid, scenario_id = _seed(conn)
      resp = client.post(f"/jobs/{jid}/feedback", data={"status": "accepted", "note": ""})
      assert resp.status_code == 200
      assert '<span class="tb-count" id="count-new" hx-swap-oob="true">0</span>' in resp.text
      assert '<span class="tb-count" id="count-accepted" hx-swap-oob="true">1</span>' in resp.text
      assert '<span class="tb-count" id="count-rejected" hx-swap-oob="true">0</span>' in resp.text
      assert '<span class="tb-count" id="count-pending" hx-swap-oob="true">0</span>' in resp.text
  ```

  replace with:

  ```python
  def test_job_feedback_updates_counts_oob(client, conn):
      sid, jid, scenario_id = _seed(conn)
      resp = client.post(f"/jobs/{jid}/feedback", data={"status": "accepted", "note": ""})
      assert resp.status_code == 200
      assert '<span class="tb-count" id="count-new" hx-swap-oob="true">0</span>' in resp.text
      assert '<span class="tb-count" id="count-accepted" hx-swap-oob="true">1</span>' in resp.text
      assert '<span class="tb-count" id="count-rejected" hx-swap-oob="true">0</span>' in resp.text
      assert '<span class="tb-count" id="count-pending" hx-swap-oob="true">0</span>' in resp.text
      assert '<span class="tb-badge" id="count-more-badge" hx-swap-oob="true">0</span>' in resp.text
  ```

- [ ] **Step 2: Run the full jobs test suite and confirm the expected failures**

  Run: `python -m pytest tests/test_routes_jobs.py -v`

  Expected: multiple FAILs — the assertions now look for `tb-count`/`tb-toolbar`/`tb-tabs`/`tb-check-row`/`tb-tab active`/`tb-more-link active`, none of which exist yet in the still-unmodified `_content.html`. Confirm the failures are exactly these (old class names still present, new ones absent) and not something unrelated — if any *other* test fails, stop and investigate before proceeding.

- [ ] **Step 3: Rewrite `app/templates/jobs/_content.html`**

  Replace lines 19-98 (the `<input type="hidden" ...status-marker...>` through the closing `</div>` of `filter-bar` — i.e. everything between the existing hidden `*_filter` inputs block and the `<div class="bulk-bar decision-panel">` line) with:

  ```jinja
{% set primary_tabs = tab_defs[:4] %}   {# new, lead, accepted, pending #}
{% set archive_tabs = tab_defs[4:] %}   {# rejected, not_relevant, trash #}
{% set scope_tabs = active_tabs if active_tabs is defined else filter.statuses %}
{% set scope_filter = filter.with_statuses(scope_tabs) if filter.searching else filter %}
{% set more_active = archive_tabs | selectattr(0, 'in', scope_tabs) | list | length > 0 %}
{% set archive_total = archive_tabs | map(attribute=3) | sum %}
{% set active_filter_count =
     (1 if (filter.scenario_id is not none or filter.scenario_none) else 0)
   + (1 if filter.source_id is not none else 0)
   + (1 if (filter.org is not none or filter.org_none) else 0) %}

<div class="tb-toolbar">
  <nav class="tb-tabs">
    {% for tab, label, count_id, count, tip in primary_tabs %}
    <a class="tb-tab{% if tab in scope_tabs %} active{% endif %}"
       href="/jobs?{{ filter.for_status(tab).query_params() | urlencode }}"
       hx-get="/jobs?{{ filter.for_status(tab).query_params() | urlencode }}"
       hx-target="#jobs-content" hx-swap="innerHTML" hx-push-url="true"
       title="{{ tip | safe }}">{{ label }}<span class="tb-count" id="{{ count_id }}">{{ count }}</span></a>
    {% endfor %}
    <details>
      <summary class="tb-trigger{% if more_active %} active{% endif %}">More <span class="tb-badge" id="count-more-badge">{{ archive_total }}</span> <span class="tb-chev">▾</span></summary>
      <div class="tb-panel tb-panel-more">
        {% for tab, label, count_id, count, tip in archive_tabs %}
        <a class="tb-more-link{% if tab in scope_tabs %} active{% endif %}"
           href="/jobs?{{ filter.for_status(tab).query_params() | urlencode }}"
           hx-get="/jobs?{{ filter.for_status(tab).query_params() | urlencode }}"
           hx-target="#jobs-content" hx-swap="innerHTML" hx-push-url="true"
           title="{{ tip | safe }}">{{ label }}<span class="tb-count" id="{{ count_id }}">{{ count }}</span></a>
        {% endfor %}
      </div>
    </details>
  </nav>

  <div class="tb-status-select-wrap">
    <select class="tb-status-select" name="status"
            hx-get="/jobs" hx-target="#jobs-content" hx-swap="innerHTML" hx-push-url="true"
            hx-include="[name='scenario'],[name='source_id'],[name='org'],[name='order'],[name='q']">
      {% if filter.is_multi %}
      <option value="" selected disabled>Multiple statuses</option>
      {% endif %}
      {% for tab, label, count_id, count, tip in tab_defs %}
      <option value="{{ tab }}" {% if not filter.is_multi and filter.statuses == (tab,) %}selected{% endif %}>{{ label }} · {{ count }}</option>
      {% endfor %}
    </select>
  </div>

  <div class="tb-utility">
    <details>
      <summary class="tb-trigger{% if filter.is_multi %} active{% endif %}">Combine <span class="tb-chev">▾</span></summary>
      <div class="tb-panel">
        <span class="tb-panel-label">Add statuses to this view</span>
        {% for tab, label, count_id, count, tip in tab_defs %}
        {% set toggle = scope_filter.with_status_toggled(tab).query_params() | urlencode %}
        <label class="tb-check-row">
          <input type="checkbox" {% if tab in scope_tabs %}checked{% endif %}
                 hx-get="/jobs?{{ toggle }}" hx-target="#jobs-content" hx-swap="innerHTML" hx-push-url="true">
          {{ label }} <span class="tb-count-plain">{{ count }}</span>
        </label>
        {% endfor %}
      </div>
    </details>

    <details>
      <summary class="tb-trigger{% if active_filter_count %} active{% endif %}">Filters {% if active_filter_count %}<span class="tb-badge">{{ active_filter_count }}</span>{% endif %} <span class="tb-chev">▾</span></summary>
      <div class="tb-panel">
        <label class="tb-panel-label" for="tb-scenario">Scenario</label>
        <select id="tb-scenario" name="scenario" hx-get="/jobs" hx-target="#jobs-content" hx-swap="innerHTML"
                hx-push-url="true" hx-vals='{"status": "{{ filter.statuses | join(',') }}"}'
                hx-include="[name='source_id'],[name='org'],[name='order'],[name='q']">
          <option value="">(All)</option>
          <option value="none" {% if filter.scenario_none %}selected{% endif %}>(None)</option>
          {% for s in scenarios %}
          <option value="{{ s.id }}" {% if filter.scenario_id == s.id %}selected{% endif %}>{{ s.name }}</option>
          {% endfor %}
        </select>
        <label class="tb-panel-label" for="tb-source">Source</label>
        <select id="tb-source" name="source_id" hx-get="/jobs" hx-target="#jobs-content" hx-swap="innerHTML"
                hx-push-url="true" hx-vals='{"status": "{{ filter.statuses | join(',') }}"}'
                hx-include="[name='scenario'],[name='org'],[name='order'],[name='q']">
          <option value="">(All)</option>
          {% for s in sources %}
          <option value="{{ s.id }}" {% if filter.source_id == s.id %}selected{% endif %}>{{ "(Single / None)" if s.fetcher_type == "manual" else s.name }}</option>
          {% endfor %}
        </select>
        <label class="tb-panel-label" for="tb-org">Organization</label>
        <select id="tb-org" name="org" hx-get="/jobs" hx-target="#jobs-content" hx-swap="innerHTML"
                hx-push-url="true" hx-vals='{"status": "{{ filter.statuses | join(',') }}"}'
                hx-include="[name='scenario'],[name='source_id'],[name='order'],[name='q']">
          <option value="">(All)</option>
          <option value="none" {% if filter.org_none %}selected{% endif %}>(None)</option>
          {% for c in companies %}
          <option value="{{ c }}" {% if filter.org == c %}selected{% endif %}>{{ c | truncate(40, True, '…') }}</option>
          {% endfor %}
        </select>
        {% if not filter.searching %}
        <label class="tb-panel-label" for="tb-order">Sort</label>
        <select id="tb-order" name="order" hx-get="/jobs" hx-target="#jobs-content" hx-swap="innerHTML"
                hx-push-url="true" hx-vals='{"status": "{{ filter.statuses | join(',') }}"}'
                hx-include="[name='scenario'],[name='source_id'],[name='org']">
          <option value="change" {% if filter.order == 'change' %}selected{% endif %}>Newest changes</option>
          <option value="score" {% if filter.order == 'score' %}selected{% endif %}>Fit score</option>
          <option value="age" {% if filter.order == 'age' %}selected{% endif %}>Posting age</option>
        </select>
        {% endif %}
        {% if filter.is_narrowed %}
        <a class="tb-clear-link" href="/jobs?{{ filter.cleared().query_params() | urlencode }}">Clear all filters</a>
        {% endif %}
      </div>
    </details>

    {% if jobs %}
    <label class="tb-select-all">
      <input type="checkbox" class="select-all-checkbox" aria-label="Select all">
      Select all
    </label>
    {% endif %}
  </div>
</div>
  ```

  Leave everything above line 19 (the `{% import %}`, `tab_defs` list, and the six hidden `*_filter`/`jobs-status-marker` inputs) and everything from the old line 99 onward (`<div class="bulk-bar decision-panel">` through the end of the file) exactly as they are.

- [ ] **Step 4: Add the More-panel's oob counter to `app/templates/jobs/_counts_oob.html`**

  The file currently ends with:

  ```jinja
  <span class="tab-count" id="count-trash" hx-swap-oob="true">{{ counts.trash }}</span>
  ```

  Add one line after it (and rename the class on all seven existing lines from `tab-count` to `tb-count`, matching Step 3's markup):

  ```jinja
  <span class="tb-count" id="count-new" hx-swap-oob="true">{{ counts.new }}</span>
  <span class="tb-count" id="count-accepted" hx-swap-oob="true">{{ counts.accepted }}</span>
  <span class="tb-count" id="count-pending" hx-swap-oob="true">{{ counts.pending }}</span>
  <span class="tb-count" id="count-rejected" hx-swap-oob="true">{{ counts.rejected }}</span>
  <span class="tb-count" id="count-lead" hx-swap-oob="true">{{ counts.lead }}</span>
  <span class="tb-count" id="count-not_relevant" hx-swap-oob="true">{{ counts.not_relevant }}</span>
  <span class="tb-count" id="count-trash" hx-swap-oob="true">{{ counts.trash }}</span>
  <span class="tb-badge" id="count-more-badge" hx-swap-oob="true">{{ counts.rejected + counts.not_relevant + counts.trash }}</span>
  ```

  (i.e. the whole file becomes exactly these 8 lines.)

- [ ] **Step 5: Run the full jobs test suite and confirm it's green**

  Run: `python -m pytest tests/test_routes_jobs.py -v`

  Expected: all PASS. If anything still fails, read the failure carefully — it's almost certainly a markup detail (missing space, wrong attribute order affecting a substring match) rather than a logic bug, since no Python code changed.

- [ ] **Step 6: Run the full test suite (not just jobs) to catch any unrelated regression**

  Run: `python -m pytest`

  Expected: all PASS (same pass/fail counts as before this task, modulo the jobs-file assertions just fixed).

- [ ] **Step 7: Commit**

  ```bash
  git add app/templates/jobs/_content.html app/templates/jobs/_counts_oob.html tests/test_routes_jobs.py
  git commit -m "$(cat <<'EOF'
  refactor(jobs): collapse toolbar into tab row + More/Combine/Filters popovers

  Replaces the always-visible 7-tab strip and Scenario/Source/Org/Sort
  selects with 4 primary tabs, a More popover for the archival statuses,
  a Combine popover for multi-status views, and a Filters popover for
  the dropdown filters. No JobFilter/route changes — purely template.

  Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
  EOF
  )"
  ```

---

### Task 2: CSS — new toolbar styles, responsive layout, delete dead rules

**Files:**
- Modify: `app/templates/base.html` (CSS only, inside the existing `<style>` block)

**Interfaces:**
- Consumes: the class names Task 1 introduced (listed in Task 1's "Produces").
- Produces: nothing consumed by later tasks — this is a leaf/presentational task.

There is no automated test for CSS content — the existing tests only assert on HTML class *names* being present (already covered by Task 1), not on how they're styled. This task's own verification is Step 3 below (regression-run the test suite, which must stay green since no markup changes) plus the manual visual check in Task 4.

- [ ] **Step 1: Replace the old toolbar CSS block**

  In `app/templates/base.html`, find the block starting at the comment `/* ---- Jobs page toolbar ------------------------------------------------ */` and ending at the line `.select-all input { width: 1rem; height: 1rem; margin: 0; cursor: pointer; accent-color: var(--accent); }` (this is the entire `.jobs-search`/`.filter-row`/`.filter-selects`/`.filter-tools`/`.filter-bar`/`.filter-links`/`.tab-item`/`.tab-label`/`.tab-count`/`.tab-check`/`.filter-clear`/`.select-all` block). Replace it with:

  ```css
    /* ---- Jobs page toolbar ------------------------------------------------ */
    /* Every band above the first job row — heading, Add, search, toolbar —
       is separated by roughly one text line so nothing reads as a heavy
       panel. */

    /* Search is a light filter zone, NOT a raised card: a faint tint + a
       single hairline under it. The empty #jobs-add-result between it and
       the heading stays at zero height. */
    .jobs-search {
      margin: 0; padding: 0.3rem 0.5rem;
      background: var(--ground);
      border: none; border-bottom: 1px solid var(--border);
      border-radius: 6px 6px 0 0;
    }
    .jobs-search:focus-within { border-bottom-color: var(--accent); }
    .jobs-search input[type="search"] {
      display: block; width: 100%; max-width: none;
      border: none; background-color: transparent;
      padding: 0.2rem 0.2rem 0.2rem 1.7rem;
      background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='%238b8680' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Ccircle cx='11' cy='11' r='7'/%3E%3Cline x1='21' y1='21' x2='16.65' y2='16.65'/%3E%3C/svg%3E");
      background-repeat: no-repeat; background-position: left center; background-size: 0.95rem;
    }
    .jobs-search input[type="search"]:focus { outline: none; border: none; }

    /* One toolbar row: primary tabs (or, on mobile, the status select) on the
       left, utility controls (Combine/Filters/Select-all) pushed right. Never
       wraps to a second row above 600px; below that it stacks to exactly two
       rows (status select, then utility row) — see the media query below. */
    .tb-toolbar {
      display: flex; align-items: center; flex-wrap: nowrap; gap: 1rem;
      margin: 0 0 0.6rem; padding: 0.5rem 0.5rem 0.6rem;
      background: var(--ground); border-radius: 0 0 6px 6px;
      border-bottom: 1px solid var(--border);
    }
    .tb-tabs { display: flex; align-items: center; gap: 1.3rem; min-width: 0; flex: 1 1 auto; }
    .tb-status-select-wrap { display: none; flex: 1 1 auto; position: relative; }

    .tb-tab {
      position: relative; display: inline-flex; align-items: baseline; gap: 0.3rem;
      white-space: nowrap; padding: 0.3rem 0.1rem 0.5rem; margin-bottom: -1px;
      border-bottom: 2px solid transparent; color: var(--text-secondary);
      text-decoration: none; font-size: 0.9rem; font-weight: 500;
      transition: color 0.12s, border-color 0.12s; -webkit-tap-highlight-color: transparent;
    }
    .tb-tab:hover { color: var(--accent); }
    .tb-tab.active { color: var(--text-primary); font-weight: 600; border-bottom-color: var(--accent); }
    .tb-count { font-size: 0.8em; font-weight: 500; color: var(--text-muted); font-variant-numeric: tabular-nums; }
    .tb-tab.active .tb-count { color: var(--accent); }

    /* Trigger for the More / Combine / Filters popovers. Suppress the native
       disclosure marker (both browsers' mechanisms) in favor of our own
       rotating chevron. */
    .tb-trigger {
      position: relative; display: inline-flex; align-items: center; gap: 0.4rem;
      cursor: pointer; list-style: none; -webkit-tap-highlight-color: transparent;
      background: transparent; border: 1px solid var(--border); border-radius: 7px;
      color: var(--text-secondary); font-family: var(--font-sans); font-size: 0.82rem;
      font-weight: 500; padding: 0.5rem 0.65rem;
      transition: border-color 0.12s, color 0.12s, background 0.12s;
    }
    .tb-trigger::-webkit-details-marker { display: none; }
    .tb-trigger:hover { border-color: var(--accent); color: var(--accent); }
    .tb-trigger.active { border-color: var(--accent); color: var(--accent); background: var(--accent-tint); }
    .tb-chev { font-size: 0.65rem; color: var(--text-muted); transition: transform 0.15s cubic-bezier(.23,1,.32,1); }
    details[open] > .tb-trigger .tb-chev { transform: rotate(180deg); }
    .tb-badge {
      background: var(--accent); color: #fff; font-size: 0.68rem; font-weight: 700;
      border-radius: 999px; padding: 0 6px; line-height: 1.5; font-variant-numeric: tabular-nums;
    }

    /* Popover panel shared by More / Combine / Filters — one elevation step
       above --ground (i.e. --surface), matching .job-row:hover's shadow
       weight plus a softer resting shadow for the extra lift of an overlay. */
    .tb-tabs details, .tb-utility details { position: relative; }
    .tb-panel {
      position: absolute; top: calc(100% + 8px); right: 0; z-index: 20;
      background: var(--surface); border: 1px solid var(--border); border-radius: 9px;
      padding: 0.85rem; min-width: 230px;
      box-shadow: 0 2px 8px rgba(0,0,0,.04), 0 8px 24px rgba(0,0,0,.08);
      transform-origin: top right;
      animation: tb-panel-in 0.16s cubic-bezier(.23,1,.32,1);
    }
    @keyframes tb-panel-in {
      from { opacity: 0; transform: scale(0.96) translateY(-2px); }
      to { opacity: 1; transform: scale(1) translateY(0); }
    }
    .tb-panel-label {
      display: block; font-size: 0.68rem; font-weight: 600; text-transform: uppercase;
      letter-spacing: 0.05em; color: var(--text-muted); margin: 0.6rem 0 0.2rem;
    }
    .tb-panel-label:first-child { margin-top: 0; }
    .tb-panel select {
      width: 100%; font-family: var(--font-sans); font-size: 0.88rem; padding: 0.4rem 0.5rem;
    }
    .tb-check-row {
      display: flex; align-items: center; gap: 0.55rem; padding: 0.4rem 0.1rem;
      font-size: 0.88rem; color: var(--text-primary); cursor: pointer; border-radius: 6px;
    }
    .tb-check-row:hover { background: var(--ground); }
    .tb-check-row input { width: 1rem; height: 1rem; accent-color: var(--accent); margin: 0; }
    .tb-count-plain { margin-left: auto; color: var(--text-muted); font-size: 0.82em; font-variant-numeric: tabular-nums; }
    .tb-more-link {
      display: flex; align-items: center; gap: 0.55rem; padding: 0.45rem 0.1rem;
      font-size: 0.88rem; color: var(--text-primary); text-decoration: none; border-radius: 6px;
    }
    .tb-more-link:hover { background: var(--ground); color: var(--accent); }
    .tb-more-link.active { color: var(--accent); font-weight: 600; }
    .tb-more-link .tb-count { margin-left: auto; }
    .tb-clear-link { display: inline-block; margin-top: 0.7rem; font-size: 0.8rem; color: var(--text-secondary); text-decoration: none; }
    .tb-clear-link:hover { color: var(--accent); }

    /* Mobile status picker: a native select styled as the primary control,
       with its own dropdown-caret background image (same technique as the
       search icon above) since a styled native <select> can't have a real
       child element for the arrow. */
    .tb-status-select-wrap::after {
      content: "▾"; position: absolute; right: 0.8rem; top: 50%; transform: translateY(-50%);
      color: var(--text-muted); font-size: 0.75rem; pointer-events: none;
    }
    .tb-status-select {
      -webkit-appearance: none; appearance: none; width: 100%;
      font-family: var(--font-sans); font-size: 0.98rem; font-weight: 600;
      padding: 0.65rem 2rem 0.65rem 0.7rem;
    }

    .tb-utility { display: flex; align-items: center; gap: 0.5rem; flex: none; margin-left: auto; }
    .tb-select-all {
      white-space: nowrap; cursor: pointer; display: inline-flex; align-items: center;
      gap: var(--space-2); font-size: 0.7rem; font-weight: 600; text-transform: uppercase;
      letter-spacing: 0.05em; color: var(--text-muted);
    }
    .tb-select-all input { width: 1rem; height: 1rem; margin: 0; cursor: pointer; accent-color: var(--accent); }

    @media (max-width: 600px) {
      .tb-toolbar { flex-direction: column; align-items: stretch; gap: 0.55rem; }
      .tb-tabs { display: none; }
      .tb-status-select-wrap { display: block; order: 1; }
      .tb-utility { order: 2; width: 100%; }
      .tb-utility .tb-panel { right: auto; left: 0; transform-origin: top left; }
    }
  ```

- [ ] **Step 2: Exempt the new popover animation from reduced motion**

  Find (in the existing `@media (prefers-reduced-motion: reduce)` block):

  ```css
    @media (prefers-reduced-motion: reduce) {
      ::view-transition-group(*), ::view-transition-old(*), ::view-transition-new(*) { animation: none !important; }
      .job-row-expanded.htmx-swapping { animation: none; }
    }
  ```

  Replace with:

  ```css
    @media (prefers-reduced-motion: reduce) {
      ::view-transition-group(*), ::view-transition-old(*), ::view-transition-new(*) { animation: none !important; }
      .job-row-expanded.htmx-swapping { animation: none; }
      .tb-panel { animation: none; }
    }
  ```

- [ ] **Step 3: Run the full test suite to confirm no regression**

  Run: `python -m pytest`

  Expected: all PASS, same counts as after Task 1 (CSS changes don't affect any Python-level or HTML-string assertion).

- [ ] **Step 4: Commit**

  ```bash
  git add app/templates/base.html
  git commit -m "$(cat <<'EOF'
  style(jobs): style the new toolbar, delete the old filter-bar CSS

  Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
  EOF
  )"
  ```

---

### Task 3: Delete the now-dead long-press-to-toggle JS

**Files:**
- Modify: `app/templates/base.html` (delete one `<script>` block)

**Interfaces:**
- Consumes: nothing (this is pure deletion).
- Produces: nothing consumed by later tasks.

**Why this is safe to delete:** this IIFE exists only to make the old always-visible `.tab-check` chip reachable via a touch long-press (so touch users didn't need a separate, tiny tap target). Task 1 removed `.tab-check` entirely — multi-select now lives in the openly-tappable Combine panel checkboxes, which need no special touch gesture. The block has no other purpose and nothing else references it.

- [ ] **Step 1: Delete the block**

  In `app/templates/base.html`, find and delete this entire `<script>...</script>` block verbatim:

  ```html
    <script>
  (function () {
    // Touch/pen only: long-press a status tab = toggle that bucket in/out of the
    // view (same effect as tapping the tab's marker). Extra path for small
    // screens; the always-visible +/✓ markers keep it discoverable. Mouse users
    // just click the marker. A short tap is unchanged: label = exclusive nav,
    // marker = toggle. The move-threshold guard keeps scrolling / quick taps clear.
    var LONG_PRESS_MS = 500;
    var MOVE_CANCEL_PX = 10;
    var timer = null, startX = 0, startY = 0, activeItem = null, fired = false;

    function cancel() {
      // Only clear `fired` when a pending press is aborted before it fires — a
      // press that already fired is waiting for its one trailing click to be
      // swallowed (see the capture-phase 'click' listener below).
      if (timer) { clearTimeout(timer); timer = null; fired = false; }
      activeItem = null;
    }

    document.body.addEventListener('pointerdown', function (e) {
      fired = false;  // every fresh interaction starts clean (a prior fire whose
                      // trailing click never arrived must not swallow this tap)
      if (e.pointerType === 'mouse') return;  // touch/pen only — mouse users tap the marker
      var item = e.target.closest('.tab-item');
      if (!item || !item.querySelector('.tab-check')) return;
      if (e.target.closest('.tab-check')) return;  // tapping the marker already toggles
      activeItem = item;
      startX = e.clientX; startY = e.clientY;
      timer = setTimeout(function () {
        timer = null;
        var check = activeItem && activeItem.querySelector('.tab-check');
        if (!check) return;
        if (navigator.vibrate) { try { navigator.vibrate(15); } catch (err) {} }
        var pulsed = activeItem;
        pulsed.classList.add('tab-item-pulse');
        setTimeout(function () { pulsed.classList.remove('tab-item-pulse'); }, 170);
        check.click();   // synthetic click — htmx does the swap (fired still false, so
        fired = true;    // the suppressor ignores it); now swallow the REAL release click
      }, LONG_PRESS_MS);
    });

    document.body.addEventListener('pointermove', function (e) {
      if (!timer) return;
      if (Math.abs(e.clientX - startX) > MOVE_CANCEL_PX ||
          Math.abs(e.clientY - startY) > MOVE_CANCEL_PX) cancel();
    });
    document.body.addEventListener('pointerup', cancel);
    document.body.addEventListener('pointercancel', cancel);

    // Swallow the click that fires on release after a long-press fired, so the
    // label's exclusive-nav doesn't run on top of the toggle.
    document.body.addEventListener('click', function (e) {
      if (!fired) return;
      fired = false;
      if (e.target.closest('.tab-item')) { e.preventDefault(); e.stopPropagation(); }
    }, true);
  })();
    </script>
  ```

  (The `<script>` block immediately before it, ending `panel.classList...}); </script>`, and the `<script>` block immediately after it, starting `(function () { function highlightFromHash()...`, are both unrelated — leave them untouched. Deleting the block above them just leaves those two adjacent.)

- [ ] **Step 2: Run the full test suite to confirm no regression**

  Run: `python -m pytest`

  Expected: all PASS (this JS has no server-side/Python test coverage; deleting it can't change any assertion outcome).

- [ ] **Step 3: Commit**

  ```bash
  git add app/templates/base.html
  git commit -m "$(cat <<'EOF'
  chore(jobs): remove dead long-press-to-toggle JS

  Workaround for the old always-visible .tab-check chip, which no longer
  exists — multi-select now lives in the Combine popover's checkboxes.

  Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
  EOF
  )"
  ```

---

### Task 4: Manual verification and handoff

**Files:** none (no code changes — this task drives the dev server and reports back).

This is a UI-facing change, so per this repo's process it needs a human look before merging — don't proceed to "finishing a development branch" after this task on your own; hand the running server to the user and wait for their go-ahead.

- [ ] **Step 1: Start the dev server against a throwaway DB**

  Use the `run-dev-server` skill (it copies `job-seek.db` into this worktree and starts the server with `--reload` against the copy — never the live DB). Also copy `config.toml` from the main checkout into this worktree first if it isn't already there (it's gitignored, so a fresh worktree won't have it).

- [ ] **Step 2: Walk the checklist from the design spec**

  Open the jobs page in a browser at both a desktop width and a ~375px mobile width (browser devtools device toolbar), in both light and dark mode, and check:

  - Toolbar never wraps to more than the two documented lines (one desktop, two mobile), tab counts included, at any width down to 320px.
  - Solo tab click (primary label, More-panel link, or mobile `<select>`) lands on the same filtered view as today.
  - Combine checkbox toggling builds/removes a multi-status view; the panel closes after each toggle (expected, documented trade-off) and its trigger shows the accent `active` state whenever a multi-status view is active.
  - Filters popover: each of Scenario/Source/Org/Sort still filters correctly; the badge count matches the number of active dropdown filters (0-3, not counting Sort or the search box); "Clear all filters" still does a full-page navigation and empties the search box.
  - Accepting/rejecting a job from its row still live-decrements the visible primary-tab or More-panel count (via the oob swap) without a full page reload.
  - Keyboard: each `<summary>` trigger is reachable via Tab and toggles with Enter/Space (native `<details>` behavior — no custom JS to verify here, just confirm nothing else swallows focus).

- [ ] **Step 3: Report back**

  Leave the dev server running. Tell the user the URL and that it's running against a throwaway DB copy, and ask them to try it before you offer to merge or clean up the worktree. Do not proceed to squash-merge or remove the worktree until they give the go-ahead — per this repo's process, UI-facing changes get a human look first.
