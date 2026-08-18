# Dismissable status notice after "Fetch all"

## Problem

On `/fetch`, the "Fetch all active sources" button streams live per-source
progress, then — because it sets neither `data-progress-target` nor
`data-progress-oob` — falls through to `location.reload()` in
`base.html`'s progress-button handler. The final summary line
(`"All sources fetched: N new / M found across K source(s)"`) is only ever
shown transiently in the inline progress span; it's gone the instant the
page reloads, and there was never a way to dismiss or re-read it.

## Scope

`/fetch` (`app/templates/fetch/panel.html`) and its `POST /fetch/all`
handler (`app/routes/fetch.py`) only. The per-row single-source "Fetch"
button keeps its current full-reload behavior — unchanged.

## Design

Reuse the notice system already used on `/jobs` and `/sources`
(`#notice-stack` + `NOTICE:`-prefixed stream lines, rendered/dismissed by
the existing `showNotice()` in `base.html`) instead of building anything
new.

1. **`fetch/panel.html`**
   - Add `<div id="notice-stack" aria-live="polite"></div>` above the
     table, matching `sources/index.html` and `jobs/list.html`.
   - Wrap the existing `{% if sources %}...{% endif %}` table markup in
     `<div id="fetch-content">` so it can be targeted for an in-place swap.
   - Give the button `data-progress-target="#fetch-content"` (in addition
     to its existing `data-progress-url="/fetch/all"`) and rename its label
     from "Fetch all active sources" to "Fetch all".

   Because `#notice-stack` sits outside `#fetch-content`, swapping the
   latter's `innerHTML` on completion leaves the notice untouched — this is
   the same mechanism `/jobs/add-by-url` already relies on.

2. **`app/routes/fetch.py` (`trigger_fetch_all`)**
   - Add a local `_notice_line(template_name, *, level="info", **context)`
     helper, matching the one already duplicated in `routes/sources.py` and
     `routes/jobs.py` (same signature, same body — following existing
     precedent rather than extracting a shared module).
   - Add a template `app/templates/fetch/_fetch_all_summary_notice.html`
     rendering the aggregate line, e.g.:
     `<p>Fetch all: <strong>{{ total_new }}</strong> new /
     {{ total_found }} found across {{ source_count }} source(s).</p>`
   - Replace the stream's final plain-text line
     (`yield f"All sources fetched: ..."`) with:
     - `yield _notice_line("fetch/_fetch_all_summary_notice.html", total_new=total_new, total_found=total_found, source_count=len(sources))`
     - followed by a final `HTML:` line re-rendering the sources table body
       (extract the existing `{% for source in sources %}...{% endfor %}`
       table into `app/templates/fetch/_table.html`, included from both
       `panel.html` and rendered standalone here via
       `templates.get_template("fetch/_table.html").render(...)`) so the
       per-source "New / Found" and "Lifetime" columns reflect the just-run
       fetch without a full page reload. Recompute `runs_by_source` and
       `stats_by_source` after the loop (same queries `fetch_panel` already
       uses) before rendering.
   - If `sources` is empty (no enabled sources), skip the `HTML:` line
     (nothing changed) but still emit the notice — `0 new / 0 found across
     0 source(s)`.

### Why this shape

- No new frontend mechanism — `data-progress-target` + `NOTICE:` lines are
  an established pattern (`/jobs/add-by-url`, `/scenarios/refine`,
  `sources/_add_form.html`), so this is pure wiring, not new plumbing.
- Extracting `fetch/_table.html` mirrors `sources/_table.html` /
  `jobs/_content.html`, which already split "content that both the initial
  page render and a later streamed swap need" into its own partial.
- Notice content is the aggregate line only (confirmed with user) — no
  per-source breakdown in the notice; the swapped table already carries
  per-source detail.

## Testing

Extend `tests/test_routes_fetch.py`:
- `POST /fetch/all` response stream contains a `NOTICE:` line with the
  correct aggregate new/found/source counts (mock `run_fetch` to yield a
  known result).
- Response stream contains a final `HTML:` line containing the updated
  table markup (spot-check a source's new/found figures appear).
- `POST /fetch/all` with zero enabled sources still emits a `NOTICE:` line
  (`0 new / 0 found across 0 source(s)`) and no `HTML:` line.
- `GET /fetch` still renders `#notice-stack` and `#fetch-content` wrapping
  the table, and the button reads "Fetch all" with
  `data-progress-target="#fetch-content"`.
