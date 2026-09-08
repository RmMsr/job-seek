# CV Differences tab

Replace the "What tailoring changed" block with a third preview tab that renders
the diff *as the CV document*: removed blocks struck through in place, moved
blocks annotated with where they came from, remaining edits shown as precise
word-level redline. Goal: a small number of meaningful facts, not a raw diff.

Deterministic throughout — no LLM involvement.

## Rendering approach

Build one **annotated markdown** string: the tailored CV with diff markup woven
in as inline HTML (`<del>`, `<ins>`, `<span class="cvd-move">`). Render it through
doc-write `--html` (continuous, no page breaks) with the user's CSS plus an
appended diff stylesheet.

doc-write does the layout; we only inject marks into the markdown stream, so
there is never any need to correlate diff results against a rendered DOM.

Verified: `sanitize_cv_markdown` preserves `<del>/<ins>/<span>` with their
`class` and `data-*` attributes (it strips only `style`, `on*`, and the
script/style/link/iframe/… tag set); doc-write `--html` passes the inline HTML
through and applies classes from `--css`.

## Diff engine — `app/cv/cv_diff.py` (new)

Input: `(base_cv, tailored_cv)` markdown. Output: the annotated markdown string
plus a `digest` dict for the summary line and the added-content callout.

Reuses `change_report._blocks(md)` → `list[(section_path, text)]`, one entry per
bullet / non-empty paragraph line. Each block is single-line, so no multi-line
wrapping concerns.

`_blocks` is extended (or wrapped) to also carry, per block: `kind`
(`bullet` | `para` | — headings already consumed), `list_id` (a counter bumped
on each blank line or non-bullet line, so a run of consecutive bullets shares
one id), and `rank` (1-based index within that `list_id`). Needed for §3.
Move the extended parser into `cv_diff.py`.

### 1. Match

Same matching `change_report` already does:

- For each base block, best char-ratio (`difflib.SequenceMatcher` on normalised
  text) match among unclaimed tailored blocks; accept at ratio ≥ 0.6.
- Second pass re-pairs leftover dropped/added blocks by word-ratio ≥ 0.2
  (`_rematch_dropped_added`).

Extract these helpers from `change_report.py` into `cv_diff.py` (or a shared
`app/cv/_block_match.py`) rather than duplicating; `change_report.py` is being
retired (see below) so moving them is fine.

### 2. Classify each matched pair — content axis

Independent of any position change. Compare normalised `before` / `after`:

| case | test | rendering |
|---|---|---|
| identical | `norm(b) == norm(a)` and `norm(b, keep_fmt) == norm(a, keep_fmt)` | unchanged; counted only |
| formatting-only | `norm(b) == norm(a)` but formatting differs | unchanged; counted only, **not** surfaced |
| typo fix | word-ratio ≥ 0.95 | precise word-level redline |
| light reword | 0.5 ≤ word-ratio < 0.95 | precise word-level redline |
| heavy rewrite | word-ratio < 0.5 | whole `before` struck, whole `after` after it (block swap) |

Word-level redline: `difflib.SequenceMatcher` on the two token lists (split on
whitespace, punctuation kept attached), emit `equal` runs plain, `delete` runs
wrapped in `<del class="cvd-del">`, `insert` runs in `<ins class="cvd-ins">`,
`replace` as del-then-ins. Re-join with single spaces.

**Content changes are always rendered**, regardless of any move badge on the
same block or its heading. A typo fix inside a bullet that reordered inside a
section that moved still shows its `<del>/<ins>` redline. The axes are
orthogonal: the matcher pairs blocks by text similarity irrespective of
position, so the content signal is never consumed by a structural change.

**Matching hint:** when two candidate pairs score equally, prefer the one whose
base and tailored section-leaf match. This keeps a heavily-reworded *and* moved
block from failing to pair (its fallback is drop + add, which is acceptable but
less legible).

### 3. Structural changes — position axis

Computed from precomputed per-block positions: `section_path`,
`container` (the section, or a specific list within it), `rank` (1-based index
among base blocks in that container).

Three structural deltas, deduplicated **hierarchically** — a parent move is not
repeated on its children:

1. **Section reordered** — every heading whose 1-based position **among its
   siblings** (same level + parent) differs from the base gets
   `## Experience <span class="cvd-move">↑ from #2</span>` — arrow up if it moved
   earlier, down if later; `#N` is its base sibling ordinal. Every changed
   heading is badged (no LIS "winner" — deterministic, every badge is a fact).
   Child blocks are only suppressed when a section genuinely re-nested (its
   parent path changed), not on a plain sibling shuffle.
2. **Container change** — a block left its list/section for another. Badge on the
   block: `<span class="cvd-move">↑ moved from "<base section leaf>"</span>`
   (arrow by document position). Shown even if its old section also moved.
3. **Within-list reorder** — same blocks, new order. Per bullet whose `rank`
   changed: `<span class="cvd-rank">↑ from #3</span>` (arrow + base ordinal,
   same convention as headings). If > half a list's bullets changed rank, drop
   the per-bullet markers and put one `<span class="cvd-move">list reordered</span>`
   on the list's first item.

**Section rename** (heading text changed, same sibling position) →
`## Experience <span class="cvd-move">renamed from "Work history"</span>`.

**Document-global cap:** if section + container move badges would exceed 4, drop
them all, set `digest["restructured"] = True`, show one line: *"The document was
substantially reordered."* Within-list `↑ from #N` markers and all content
redline are **kept**.

**Content-free lines** (a stray `#`, `***`, bare `-`) never become diff blocks —
`parse_blocks` skips any line with no letters or digits, and empty headings.

### 4. Removed blocks

Blocks in base with no match. Weave each back into the tailored block sequence
at the position of its nearest surviving base neighbour (the previous matched
base block → insert after that block's tailored counterpart; if it was first,
insert at top of that section). Render struck through:

- bullet → `- <del class="cvd-del cvd-block">…</del>`
- paragraph → `<del class="cvd-del cvd-block">…</del>`
- heading → `<del class="cvd-del cvd-block">**…**</del>` on its own line (never a
  live `#`, which would restructure the outline)

### 5. Added blocks

Blocks in tailored with no match. Render in place as
`<ins class="cvd-ins cvd-block cvd-added">…</ins>` with a trailing
`<span class="cvd-new">new</span>` marker, and collect `(section, text)` into
`digest["added"]` for the callout.

`.cvd-added` carries the **higher-attention** background (see `_DIFF_CSS`) — a
whole generated block is the thing most worth reviewing. Inline `.cvd-ins` runs
*inside* a reworded block keep the subtle treatment.

### 6. Section-level diff

Reuse `change_report._section_diff(base_paths, new_paths)` for
removed / added sections and to feed the digest counts. Section *moves* and
*renames* are annotated on the headings per §3.

### 7. Digest

```python
{
  "sections_reordered": int,   # section moves + renames
  "blocks_moved": int,         # container changes
  "lists_reordered": int,      # within-list reorders (per list, not per bullet)
  "dropped": int,
  "reworded": int,             # typo + light + heavy content changes
  "added": int,
  "renamed_sections": [(old, new), …],
  "restructured": bool,
  "added_items": [(section, text), …],
}
```

Summary line, omitting any zero term, joined with ` · `:

> `1 section reordered · 2 blocks moved · 1 list reordered · 3 bullets dropped · 5 reworded · 1 new paragraph added`

Wording: "section(s) reordered", "block(s) moved", "list(s) reordered",
"bullet(s) dropped", "reworded", "new paragraph(s) added". `restructured` →
the summary line is just *"The document was substantially reordered"* followed by
the non-structural terms (dropped / reworded / added). If everything is zero:
*"No changes from your base CV."*

## Rendering — `app/cv/render.py`

`_run(markdown, css, out_name, work, fmt)` already takes `fmt` (added earlier).
Add:

```python
def render_diff_html(markdown: str, css: str) -> str:
    """Continuous single-page HTML for the Differences tab."""
    with tempfile.TemporaryDirectory(prefix="cv-diff-") as work:
        out = _run(markdown, css + "\n" + _DIFF_CSS, "cv.html", work, "html")
        with open(out) as f:
            return f.read()
```

`_DIFF_CSS` constant in `render.py` (or `cv_diff.py`), trusted (constant, not
user input) so it bypasses `validate_css`:

| class | style |
|---|---|
| `.cvd-del` | red, `line-through` |
| `.cvd-ins` | green text, no underline, faint green ground — inline insertions only |
| `.cvd-block` | `display:block`, padding, left border |
| `.cvd-added` | **higher-attention** ground (`--alert-tint`-equivalent), stronger left border — whole added blocks |
| `.cvd-move` | small muted bordered pill, sits after the text/heading |
| `.cvd-rank` | smaller, fainter than `.cvd-move` (`was #3`) |
| `.cvd-new` | small accent pill (`new`) |

## Route — `app/routes/cv.py`

```
GET /jobs/{job_id}/cv/diff.html   → HTMLResponse
```

- 404 if job missing or no `tailored_cv`.
- Build annotated markdown from `row["base_cv_snapshot"]` and `row["tailored_cv"]`
  via `cv_diff.build(...)`.
- If `doc_write_available()`: `render_diff_html(annotated, settings["css"])`;
  wrap a `CvRenderError` as 503.
- Else: render `cv/_cv_diff.html` — a minimal standalone HTML page that runs the
  annotated markdown through the Jinja `markdown` filter. Returned as the iframe
  body, so it must be a full `<html>` document, not a fragment.
- Empty `base_cv_snapshot` → return the "predates change tracking" page (also a
  full HTML document) without invoking the diff engine.

## Storage — base snapshot

`job_cv` gains `base_cv_snapshot TEXT NOT NULL DEFAULT ''`.

- Schema DDL + `_migrate_job_cv_add_base_cv_snapshot` (guard on
  `PRAGMA table_info`, `ALTER TABLE … ADD COLUMN`). Hard-downtime, no backfill —
  existing drafts get an empty snapshot and their Differences tab shows
  "regenerate for an accurate diff" until the next Generate. (Acceptable: this
  is a personal single-instance app.)
- `_persist_draft` writes `base_cv_snapshot = settings["base_cv"]` alongside the
  existing `tailored_cv` / `base_hash` fields.
- `queries.get_job_cv` already returns all columns; add `base_cv_snapshot` to
  any explicit column list.

If `base_cv_snapshot` is empty (pre-migration draft), the tab renders a single
muted line: *"This draft predates change tracking — regenerate to see the
differences."*

## Retire `change_report`

- Delete `app/templates/cv/_change_report.html`.
- Remove the `_change_report.html` include from `_preview_pane.html`.
- Stop calling `change_report(...)` in `cv.py` (`_task_cv_tailor` generate + plan
  branches) and drop `change_report` from `_persist_draft`'s field dict.
- Leave the `job_cv.change_report` column in place (empty going forward) — no
  migration to drop it; harmless.
- `app/cv/change_report.py` stays only if `cv_diff.py` imports helpers from it;
  otherwise move the shared helpers and delete it. Decide during implementation.
- Delete / rewrite `tests/test_cv_change_report.py` accordingly.

## UI

### Workbench preview pane — `_preview_pane.html`

Third tab in `.cv-preview-tabs`:

```html
<button type="button" class="cv-preview-tab" role="tab" data-variant="diff"
        {% if not has_draft %}disabled …{% endif %}>Differences</button>
```

Third iframe in `.cv-preview-stage`, lazy (`data-src`, like the base tab):

```html
{% if has_draft %}
<iframe class="cv-preview-doc" data-variant="diff" title="Differences"
        data-src="/jobs/{{ job.id }}/cv/diff.html"
        sandbox="allow-scripts allow-same-origin"></iframe>
{% endif %}
```

The generic tab handler in `base.html` already activates by `data-variant` and
lazy-loads `data-src` — no JS change needed. When a regen finishes,
`__cvPreviewResync` already re-syncs; the diff iframe reloads on next activation
because the pane is re-rendered (fresh `data-src`, no `src`).

Remove the `#cv-change-report` include from the decision block.

### Digest + added-content list

Rendered in the **preview pane**, between the Update button (`.cv-preview-actions`)
and the tab bar, so they show on any tab — not baked into the diff document.
Shared partial `cv/_cv_diff_summary.html`, driven by `cv_diff_view` from
`_workbench_ctx` (`{summary_line, added_items, is_empty}`, or `None` when there
is no draft / no base snapshot). Included by both `_preview_pane.html` and
`_accepted.html`.

- `<p class="cv-diff-summary-line">` — the digest sentence.
- `<div class="cv-diff-added">` (when `added_items`) — heading *"New — not from
  your base CV, review these"* + `<ul>` of the items, on an `--alert-tint` ground.

`build()` therefore returns `annotated_markdown` as the document only;
`summary_line` / `added_items` / `digest` stay as structured fields. `_DIFF_CSS`
has no `.cvd-digest` / `.cvd-callout` rules.

### Accepted view — `_accepted.html`

Gains the same three-tab structure (Base / Accepted / Differences) in place of
its current single iframe. `base_cv_snapshot` is stored, so the diff route works
unchanged. Extract the tab + stage markup shared by `_preview_pane.html` and
`_accepted.html` into `cv/_preview_tabs.html` (params: `job`, `variants`,
`active`, `has_draft`) to avoid divergence.

### `base.html`

No JS changes. `_DIFF_CSS` lives in `render.py` (injected into the rendered
document), not `base.html` — the marks only ever appear inside the doc-write
iframe.

## Edge cases

- Draft identical to base → digest line *"No changes from your base CV."*, empty
  document body.
- doc-write missing → annotated markdown through the Jinja `markdown` filter in
  a bordered block; `<del>/<ins>` render natively, `.cvd-*` unstyled but
  legible (`<del>` strikes, `<ins>` underlines by default).
- Reorder cap exceeded → single "substantially reordered" line, no move badges;
  `was #N` markers and content redline still shown.
- Compound change on one block (section moved + bullet reordered within it +
  typo fixed): heading gets one `moved` badge, the bullet gets `was #N` and its
  `<del>/<ins>` typo redline. Three distinct facts, three distinct annotations —
  the structural dedup is hierarchical (parent move not repeated on children),
  content is always shown.
- Block moved container *and* heavily reworded, similarity below matching
  threshold → falls back to struck drop + green add. Acceptable; the
  section-leaf matching hint reduces how often this happens.

## Testing

`tests/test_cv_diff.py`:

- classification: identical / formatting-only (suppressed) / typo / light reword
  / heavy rewrite → expected markup.
- structural: section move → heading badge, child blocks unbadged; container
  change → block badge; within-list reorder → `was #N` per moved bullet;
  > half a list moved → single `list reordered`; global cap → `restructured`
  with `was #N` and redline retained.
- **compound**: section move + within-list reorder + typo on one bullet → all
  three annotations present, independently.
- matching hint: equal-scoring pairs disambiguated by section leaf.
- removed-block weaving: dropped bullet appears struck at its original neighbour
  position; numbering of surviving `was #N` stays coherent.
- added block: `.cvd-added` higher-attention ground + `new` marker, appears in
  `digest["added_items"]`; inline `.cvd-ins` stays subtle.
- section rename annotation + digest counts.
- digest string: zero-term omission, all-zero → "No changes".

`tests/test_routes_cv_*`:

- `GET /cv/diff.html` → 200 with a draft, contains `cvd-` markup; 404 without a
  draft; pre-migration empty-snapshot → the muted "predates change tracking"
  line.
- `_preview_pane.html` renders a Differences tab (disabled without a draft);
  `#cv-change-report` / "What tailoring changed" gone.
- accepted view has three tabs.

`tests/test_db_*`: `_migrate_job_cv_add_base_cv_snapshot` adds the column;
`_persist_draft` writes the snapshot.

Update existing tests that assert on the change-report block.

## Files

| file | change |
|---|---|
| `app/cv/cv_diff.py` | **new** — diff engine |
| `app/cv/change_report.py` | retire; move shared match helpers out or delete |
| `app/cv/render.py` | `+render_diff_html`, `+_DIFF_CSS` |
| `app/routes/cv.py` | `+cv_diff_html` route; snapshot in `_persist_draft`; drop `change_report` wiring |
| `app/db/schema.py` | `+base_cv_snapshot` column `+_migrate_job_cv_add_base_cv_snapshot` |
| `app/db/queries.py` | `base_cv_snapshot` in `get_job_cv` / `_persist_draft` write |
| `app/templates/cv/_preview_pane.html` | `+diff` tab/iframe; `−_change_report` include |
| `app/templates/cv/_preview_tabs.html` | **new** — shared tab/stage markup |
| `app/templates/cv/_accepted.html` | three-tab structure |
| `app/templates/cv/_cv_diff.html` | **new** — no-doc-write fallback wrapper |
| `app/templates/cv/_change_report.html` | **delete** |
| `tests/test_cv_diff.py` | **new** |
| `tests/test_cv_change_report.py` | delete / fold into `test_cv_diff.py` |

## Implementation deltas (2026-09-07)

- **Typo classification** compares *characters* (`SequenceMatcher` ratio ≥ 0.9),
  not word-ratio ≥ 0.95. A single-letter fix like `teh`→`the` only scores ~0.75
  on words but ~0.96 on characters; the char measure is what actually separates
  "typo" from "light reword".
- **Within-list "odd one out"** uses a leftmost-preferring longest-increasing
  subsequence, so of a straight swap the *demoted* bullet carries `was #N`
  (the one now lower than it was), matching how a reader scans the new order.
- **`_section_diff` is not wired into the digest.** A removed/added section's
  bullets are already counted as dropped/added blocks and its heading as a
  dropped heading, so a separate section tally would double-count.
- **No-doc-write fallback** renders the annotated markdown with the `markdown`
  package directly in the route (the Jinja `markdown` filter HTML-escapes its
  input, which would neuter the `<del>/<ins>` marks). Fallback template is
  `cv/_cv_diff_fallback.html` (not `_cv_diff.html`).
- `_persist_draft` lost its `rep` parameter entirely (signature is now
  `draft, findings, settings`); both `_task_cv_tailor` call sites updated.
- No `queries.py` change was needed — `get_job_cv` is `SELECT *`.
