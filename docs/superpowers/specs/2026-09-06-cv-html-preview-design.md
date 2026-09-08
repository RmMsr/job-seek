# CV preview: doc-write-cli `--html` + Base/Tailored tabs

## Why

`doc-write-cli` gained a `--html` mode: a self-contained, paginated HTML
document meant to be embedded in an `<iframe>`. It renders in ~0.5s (no
WeasyPrint, no headless-browser screenshot — markdown → HTML plus an inlined
client-side pagination engine), carries its own restrictive CSP, and shows one
page at a time scaled to fill whatever box the iframe gives it, with a built-in
prev/next control pill.

That replaces the current PNG pipeline, which renders pages in the `cv_tailor`
task, stores file paths in `job_cv.preview_pages`, and serves each page as an
image. Because `--html` is cheap, previews move to **on-demand rendering** — no
task-time work, no stored artifacts, no stale-file cleanup.

Second change: the workbench preview pane gets **Base | Tailored tabs** so the
user can flip between the untailored CV and the tailored draft in the same
rendered form.

## Rendering — `app/cv/render.py`

Replace:

```python
def render_preview_pngs(markdown: str, css: str, out_dir: str) -> list[str]
```

with:

```python
def render_preview_html(markdown: str, css: str) -> str
```

- Sanitises the markdown (`sanitize_cv_markdown`, as `_run` already does), writes
  it and the optional CSS to a temp dir, runs `doc-write-cli <md> <out.html>
  --html`, returns the file's text.
- Built-in prev/next pill kept (the default; **no** `--no-controls` — the app
  does not drive pagination itself).
- `render_pdf` and `doc_write_available` unchanged.
- Drop `render_preview_pngs`, its stale-page cleanup, and the now-unused `glob`
  import.

## Serving — on-demand routes, nothing stored

| Route | Renders |
|---|---|
| `GET /jobs/{job_id}/cv/preview.html?variant=base\|tailored` (default `tailored`) | `base` → `settings.base_cv`; `tailored` → `job_cv.tailored_cv`, **404** if absent |
| `GET /cv/preview.html` | `settings.base_cv` |

- CSS is always `settings.css`.
- `HTMLResponse` with the document as body.
- doc-write-cli missing → **503** (the iframe then shows the browser's own error;
  the pane also has a markdown fallback, see below).
- **Delete** `GET /jobs/{job_id}/cv/preview/{page}.png`.
- `POST /cv/preview` (the htmx button on `/cv`) stays, but its partial
  (`cv/_preview_result.html`) now contains `<iframe src="/cv/preview.html">` — or
  the "doc-write-cli is not installed" message.

## Workbench preview pane — `cv/_preview_pane.html`

The `<img>` loop / markdown block becomes a tab strip plus **one** iframe:

- Two tab buttons: **Base**, **Tailored**. Styled with `frontend-design`,
  consistent with the existing underline tab look (`.tab-item` / `.scenario-tab`
  family in `base.html`).
- A short inline script swaps the single iframe's `src` between `?variant=base`
  and `?variant=tailored` and toggles `aria-selected` on the buttons. One iframe,
  so only the visible variant is ever rendered.
- Default selected tab: **Tailored** when `job_cv.tailored_cv` exists, else
  **Base**.
- **Tailored** tab is `disabled` with `title="Generate to see the tailored
  version"` until a draft exists.
- iframe: `sandbox="allow-scripts allow-same-origin"` (as the `--html` docstring
  recommends), `width: 100%`, fixed A4-aspect box (`aspect-ratio: 1 / 1.414`),
  `1px solid var(--border)`.
- The existing "Waiting on you — this preview is from before your latest changes"
  staleness note stays **above** the tabs.
- `#cv-change-report` and `#cv-findings` stay **below**, unchanged.

### Fallbacks

- doc-write-cli absent (`has_doc_write` is already in the workbench context): the
  pane shows the current `{{ … | markdown }}` block plus "(doc-write-cli not
  installed)" instead of the tabs + iframe.
- No draft yet: Base tab active, Tailored tab disabled (above).

## DB + task cleanup

- Migration `_migrate_job_cv_drop_preview_pages`: `ALTER TABLE job_cv DROP COLUMN
  preview_pages`, guarded on the `sqlite_master` table text, appended to
  `init_db`. Hard-downtime is fine (personal single-instance app).
- Remove `preview_pages` from `_DDL` and from `_JOB_CV_JSON_COLS` in
  `queries.py`.
- `_task_cv_tailor`: delete `_render_previews`, `_render_previews_step`, every
  `pngs = yield from _render_previews_step(...)` line, and the `preview_pages=`
  kwargs on `upsert_job_cv`. The task no longer renders previews at all. The task
  log loses its "Rendered PDF/PNG preview — took Xs" line — acceptable, since
  rendering is now instant on view.
- `_workbench_ctx`: drop `preview_urls` / `n_pages`; keep `has_doc_write`.
- Drop imports that fall out (`base64`, `tempfile` if unused after the base
  preview route switches to `render_preview_html`).

## Tests

- `test_cv_render.py`: PNG tests → `render_preview_html` returns a document
  starting `<!DOCTYPE html>`, containing the CV text and a pagination-engine
  marker; missing binary raises `CvRenderError`.
- `test_routes_cv_workbench.py`: drop `test_preview_png_out_of_range_404`; add
  `preview.html` route tests (base renders, tailored renders, tailored 404s with
  no draft), tab strip present, Tailored tab disabled without a draft.
- `test_routes_cv_settings.py`: `test_cv_preview_renders_pages` → asserts
  `<iframe`; keep `test_cv_preview_reports_missing_doc_write`.
- `test_cv_task.py` / `test_routes_cv_actions.py`: drop `preview_pages` /
  `_render_previews` assertions.
- `test_schema.py`: assert `preview_pages` is gone from `job_cv` after `init_db`.

## Out of scope

- Driving pagination from app chrome (custom buttons via the postMessage bridge)
  — the built-in pill is kept.
- Any change to `cv.pdf` download or `render_pdf`.
- Caching the on-demand renders.
