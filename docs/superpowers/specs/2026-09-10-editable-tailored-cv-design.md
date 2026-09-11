# Editable tailored CV (ink-mde)

## Problem

The tailored CV markdown (`job_cv.tailored_cv`) can only be changed by the LLM —
the `cv_tailor` `generate` task overwrites it wholesale; there is no way to
hand-edit a word. The base CV and profile are editable but through bare monospace
`<textarea>`s.

We want a light, pleasant markdown editor (Obsidian/StackEdit feel — inline live
rendering, CodeMirror 6 under the hood) on the tailored CV, the base CV, and the
profile, with the tailored CV also autosaving.

**Explicitly deferred to a later change:** the manual-edit ↔ LLM-`Update`
collision and the continued-iteration loop. `Update` keeps overwriting
`tailored_cv` silently, exactly as today. Also deferred: ink on the
directives / guardrails / CSS textareas.

## Editor library

[ink-mde](https://github.com/davidmyersdev/ink-mde) `0.34.0` — a thin wrapper
over CodeMirror 6 with inline markdown rendering.

Loaded at runtime from esm.sh's fully-bundled single file (matches how
`base.html` already pulls htmx and Google Fonts from CDNs; the app has no
static-file mount and this change does not add one):

```html
<script type="module">
  import { ink } from 'https://esm.sh/ink-mde@0.34.0?bundle';
  window.__ink = ink;
  window.initInkEditors(document);
</script>
```

`?bundle` yields one self-contained ~2 MB file (~500 KB gzip; includes
CodeMirror `language-data` for fenced-code highlighting). ink-mde self-injects
its stylesheet.

## Changes

### 1. Shared integration

**`window.initInkEditors(root)`** (new `<script type="module">` in
`app/templates/base.html`, plus a plain `<script>` helper it calls):

- Scans `root` for `textarea[data-ink]:not([data-ink-ready])`.
- For each: hide the textarea, insert a sibling `<div class="ink-mount">`, mount
  `ink(mountDiv, { doc: textarea.value, hooks: { afterUpdate } })`, mark the
  textarea `data-ink-ready`.
- `afterUpdate(doc)`: write `doc` into the hidden textarea **and dispatch a
  synthetic `input` event on it** (keeps existing dirty-checks / form posts
  working). Then, if `data-ink-autosave-url` is set, debounce ~1 s and
  `htmx.ajax('POST', url, { values: { markdown: doc }, swap: 'none' })`.
- Stash the instance and a `setReadonly(bool)` helper on the mount element as
  `mountEl._ink` (the profile editor locks its field during refine).
- Idempotent — safe to call repeatedly on HTMX-swapped subtrees.

**`htmx:afterSwap` hook** (`base.html`): `window.initInkEditors(evt.detail.target)`
so ink re-mounts on content HTMX swaps in — the profile "accept proposals"
partial and the CV workbench pane re-renders.

**Jinja macro** `app/templates/_ink_editor.html`:

```jinja
{% macro ink_editor(name, value, min_height="300px", autosave_url=None) %}
```

Renders the hidden `<textarea name=name data-ink
data-ink-min-height=min_height [data-ink-autosave-url=autosave_url]>value</textarea>`
plus an empty `.ink-mount` sibling.

**Theming** (`base.html` CSS): map ink-mde's public `--ink-*` custom properties
(color, font family, block/code background, syntax heading/emphasis/link colors)
onto the app's warm palette tokens. Light-only.

### 2. Tailored CV — Edit tab + autosave

**Schema** (`app/db/schema.py`): two nullable columns on `job_cv`, each with a
one-line `ALTER TABLE ADD COLUMN` migration following the existing
`_migrate_job_cv_add_*` pattern (no backfill — NULL is the "never happened"
state):

- `edited_at TEXT` — last manual edit.
- `guardrails_checked_at TEXT` — last time `guardrail_findings` was written.

**Queries** (`app/db/queries.py`):

- `set_job_cv_tailored(conn, job_id, markdown)` — `INSERT OR IGNORE` then
  `UPDATE job_cv SET tailored_cv = ?, edited_at = datetime('now'),
  updated_at = datetime('now')`.
- Stamp `guardrails_checked_at = datetime('now')` wherever `guardrail_findings`
  is written: `_persist_draft` (`app/routes/cv.py`) and the new `recheck` task
  branch.

**`_preview_tabs.html`**: a fourth tab `data-variant="edit"` (enabled whenever
`has_draft`). In the stage, a non-iframe sibling:

```html
<div class="cv-preview-doc cv-preview-editor" data-variant="edit">
  {{ ink_editor("markdown", job_cv.tailored_cv,
                autosave_url="/jobs/%d/cv/save-tailored"|format(job.id)) }}
</div>
```

The `cv-preview-doc` class means the existing `activate(stage, variant)` tab JS
toggles `.is-active` for free; no `src`/`data-src` ⇒ the iframe-load path skips
it. **Lazy mount**: ink is mounted on first activation of the Edit tab (avoids
CodeMirror's `display:none` measurement problem, skips the cost for
non-editors). Default active tab stays `tailored`.

**Route** `POST /jobs/{job_id}/cv/save-tailored` (`app/routes/cv.py`), form field
`markdown`:

- `_require_editable(conn, job_id)` — 404 missing job, 409 if `finalized_at`.
- `q.set_job_cv_tailored(conn, job_id, markdown)`.
- Return an HTML fragment of **out-of-band** pieces:
  - `<div id="cv-diff-summary" hx-swap-oob="true">` — re-rendered
    `_cv_diff_summary.html` (`_cv_diff_view` is cheap pure-Python: recomputes
    `base_cv_snapshot` vs the new markdown).
  - `<div id="cv-findings" hx-swap-oob="true">` — re-rendered `_findings.html`;
    `guardrail_status` now computes to `"stale"` because `edited_at` just moved
    past `guardrails_checked_at`.
  - `<span id="cv-editor-status" hx-swap-oob="true">Saved</span>`.

**Client** (shared module): status span cycles `Saving…` → `Saved` /
`Save failed — will retry`; on failure the text stays in the editor and the next
edit retries. On a successful save, **invalidate the styled iframes** — clear
`src`, restore `data-src` on the `tailored` and `diff` iframes so switching back
re-fetches instead of showing a stale doc-write render.

**`_preview_pane.html`**: add the `#cv-editor-status` span near the tab bar. In
the `not has_doc_write` branch, replace the read-only `{{ … | markdown }}` div
with the same `ink_editor(... autosave_url=...)` — editing must not require
doc-write.

**Wrap `#cv-diff-summary`**: `_cv_diff_summary.html`'s content is currently
inlined in `_preview_pane.html` under `.cv-diff-summary`; give the include a
stable `<div id="cv-diff-summary">` wrapper so the OOB swap has a target.

### 3. Guardrail staleness + standalone re-check

**`_guardrail_status`** (`app/routes/cv.py`): one extra clause — findings are
`"stale"` when

```python
job_cv.get("edited_at") and (
    not job_cv.get("guardrails_checked_at")
    or job_cv["guardrails_checked_at"] < job_cv["edited_at"]
)
```

composed with the existing `_draft_stale(...)` check. Existing rows: both columns
NULL ⇒ not stale, unchanged.

**`_findings.html`**: in the `<h3>` header, when `job_cv.tailored_cv` and
`settings.base_guardrails` are both non-empty, a re-check button (reuses the
`data-progress-url` + task-poll + OOB infrastructure, no new JS):

```html
<button type="button" class="btn btn-subtle"
        data-progress-url="/jobs/{{ job.id }}/cv/recheck-guardrails"
        data-progress-label="Re-checking guardrails" data-progress-oob>Re-check</button>
```

Repoint the existing `"stale"` copy ("Re-check with Update") at this button.
`job` is already in the findings render context via `_workbench_ctx`.

**Route** `POST /jobs/{job_id}/cv/recheck-guardrails`: `_require_editable` →
`q.enqueue_task(kind="cv_tailor", params={"job_id", "mode": "recheck",
"render": "findings"})` → `{task_id, already_active}`.

**Task** — new `mode == "recheck"` branch in `_task_cv_tailor`:

1. Load job / settings / row. Bail (`return {"job_id": job_id}`) if row is
   finalized, has no `tailored_cv`, or `settings["base_guardrails"]` is blank.
2. `yield "Checking against your guardrails… (LLM call: check_guardrails)"`
3. `findings = check_guardrails(client, model, settings["base_guardrails"],
   settings["base_cv"], row["tailored_cv"])["findings"]`
4. Same empty-result guard as `_persist_draft`: blank `findings` while guardrails
   *are* configured ⇒ keep prior findings, log a warning.
5. `q.upsert_job_cv(conn, job_id, guardrail_findings=findings)` + stamp
   `guardrails_checked_at`.
6. `q.add_job_event(conn, job_id, "cv", "Guardrails re-checked")`
7. `return _tailor_result(conn, job_id, params)`.

**`_rendered_chunk`** (`app/routes/cv.py`): a `which == "findings"` case →
renders `cv/_findings.html` wrapped as `<div id="cv-findings">…</div>`. So the
re-check OOB chunk replaces **only** `#cv-findings`, never the whole preview
pane — this is what stops a background re-check from wiping an editor mid-type.
`_tailor_result` maps `render == "findings"` to `panes = ["findings"]`.

### 4. Base CV and profile editors

**Base CV** (`app/templates/cv/index.html`): the `base_cv` textarea →
`{{ ink_editor("base_cv", settings.base_cv, min_height="420px") }}`. Form-backed,
no autosave — the Save button and `cv_save` route are untouched.

**Profile** (`app/templates/profile/_editor.html`): the `content` textarea →
`{{ ink_editor("content", content, min_height="300px") }}`. Form-backed. Adapt
the bespoke inline `<script>`:

- `isDirty()` unchanged — compares the hidden textarea's `value` to
  `originalValue`; works via the mirrored value + synthetic `input` event.
- `setLocked(locked)` also calls `textarea.nextElementSibling._ink.setReadonly(locked)`
  (or looks up the `.ink-mount`) so the field is genuinely locked while refine
  proposals are pending.
- The `htmx:afterSwap` hook (§1) re-mounts ink after "accept proposals" swaps the
  partial back with the rewritten markdown.

The suggestion machinery (`apply_profile_proposals` — line-based
add-bullet / `replace_bullet` / `remove_bullet` on the markdown string) is
untouched; the editor is stateless w.r.t. it and just re-seeds from the new text.

## Testing

- **Routes**: `save-tailored` persists markdown + stamps `edited_at` + returns
  OOB `#cv-diff-summary` / `#cv-findings`; 409 finalized; 404 missing job.
  `recheck-guardrails` enqueues the task; 409 finalized.
- **Task**: `mode == "recheck"` updates `guardrail_findings` +
  `guardrails_checked_at`; empty-result guard keeps prior findings;
  `_tailor_result` returns a `#cv-findings` chunk and not a `preview_pane` one.
- **Unit**: `_guardrail_status` → `"stale"` when `edited_at > guardrails_checked_at`,
  `"fresh"` after a re-check; `_draft_stale` unchanged.
- **Migration**: both columns added; existing rows read NULL / not-stale.
- **Templates**: base CV / profile / workbench render the `[data-ink]` mount
  markup; existing form-POST tests for `/cv` and `/profile` still pass (textarea
  `name`s unchanged).
- **JS**: no JS test infra — the editor UX (typing, autosave, tab switch,
  lock-during-refine, afterSwap re-mount) is covered by manual dev-server
  testing, then handed to the user per the project's UI convention.

## Files

**New**: `app/templates/_ink_editor.html`.

**Modified**: `app/db/schema.py` · `app/db/queries.py` · `app/routes/cv.py` ·
`app/templates/base.html` · `app/templates/cv/_preview_tabs.html` ·
`app/templates/cv/_preview_pane.html` · `app/templates/cv/_cv_diff_summary.html`
· `app/templates/cv/_findings.html` · `app/templates/cv/index.html` ·
`app/templates/profile/_editor.html`.
