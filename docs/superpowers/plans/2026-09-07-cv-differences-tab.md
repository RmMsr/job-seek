# CV Differences Tab — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the "What tailoring changed" block with a third preview tab that renders the base→tailored diff *as the CV document* — struck-through removals in place, move badges, `was #N` rank markers, word-level redline — deterministically.

**Architecture:** A new pure module `app/cv/cv_diff.py` turns `(base_cv, tailored_cv)` markdown into one *annotated markdown* string (tailored CV with `<del>/<ins>/<span>` marks woven in, plus a digest paragraph and an added-content callout). A new route renders it through doc-write `--html` (continuous) with an appended trusted diff stylesheet, served into a lazily-loaded third `<iframe>` tab. The base CV at generation time is snapshotted onto `job_cv` so the diff always reflects what that generation did.

**Tech Stack:** Python 3, `difflib`, FastAPI, Jinja2, SQLite, doc-write-cli.

## Global Constraints

- Deterministic only — no LLM calls in the diff path.
- Diff markup is inline HTML with `class`/`data-*` only (never inline `style`) — `sanitize_cv_markdown` strips `style`/`on*` but preserves `class`/`data-*`.
- All diff styling ships as a Python constant `_DIFF_CSS`, appended to the user's CSS at render time; it is trusted and bypasses `validate_css`.
- doc-write format flags are explicit: `_run(..., fmt)` — diff uses `"html"`.
- Migrations are hard-downtime `ALTER TABLE ADD COLUMN`, guarded by `PRAGMA table_info`, no backfill (personal single-instance app).
- Commit after each task (project rule "Commit frequently"). Commit trailers:
  ```
  Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01YAUuWYfmrM6HK7gL7xmnUm
  ```
- Do **not** stage `.superpowers/sdd/progress.md` or `a.out`. Stage explicit paths, never `git add -A`.
- Run tests with `python -m pytest` (never `uv run`).
- Spec: `docs/superpowers/specs/2026-09-07-cv-differences-tab-design.md`.

---

## Task 1: Block parser with positions + matching + content classification

**Files:**
- Create: `app/cv/cv_diff.py`
- Test: `tests/test_cv_diff.py`

**Interfaces:**
- Consumes from `app.cv.change_report`: `_norm`, `_word_ratio`, `_rematch_dropped_added`, `_REMATCH_THRESHOLD`.
- Produces:
  - `Block` dataclass: `line_no: int`, `section_path: tuple[str, ...]`, `list_id: int`, `rank: int`, `kind: str` (`"bullet"` | `"para"`), `text: str`.
  - `Heading` dataclass: `line_no: int`, `level: int`, `path: tuple[str, ...]`.
  - `parse_blocks(md: str) -> tuple[list[Block], list[Heading]]`.
  - `match_blocks(base: list[Block], new: list[Block]) -> tuple[list[tuple[Block, Block]], list[Block], list[Block]]` — returns `(pairs, dropped, added)`. `pairs` ordered by `new` block position.
  - `classify(before: str, after: str) -> str` — one of `"identical"`, `"formatting"`, `"typo"`, `"reword"`, `"rewrite"`.
  - `redline(before: str, after: str) -> str` — inline HTML, `<del class="cvd-del">` / `<ins class="cvd-ins">` runs.

- [ ] **Step 1: Write failing tests for `parse_blocks`**

```python
# tests/test_cv_diff.py
from app.cv.cv_diff import parse_blocks, match_blocks, classify, redline

MD = """# CV

## Experience

- Built the ingestion pipeline.
- Mentored engineers.

## Skills

- Python, Go
"""

def test_parse_blocks_tracks_line_section_list_rank():
    blocks, headings = parse_blocks(MD)
    texts = [b.text for b in blocks]
    assert texts == ["Built the ingestion pipeline.", "Mentored engineers.", "Python, Go"]
    b0, b1, b2 = blocks
    assert b0.line_no == 4 and MD.splitlines()[b0.line_no] == "- Built the ingestion pipeline."
    assert b0.section_path == ("Experience",) and b2.section_path == ("Skills",)
    assert b0.list_id == b1.list_id and b0.list_id != b2.list_id
    assert (b0.rank, b1.rank, b2.rank) == (1, 2, 1)
    assert b0.kind == "bullet"

def test_parse_blocks_headings():
    _, headings = parse_blocks(MD)
    assert [(h.level, h.path) for h in headings] == [
        (1, ("CV",)), (2, ("Experience",)), (2, ("Skills",)),
    ]
    assert MD.splitlines()[headings[1].line_no] == "## Experience"

def test_parse_blocks_paragraph_is_its_own_list():
    blocks, _ = parse_blocks("## S\n\nA paragraph line.\n\n- a\n- b\n")
    assert blocks[0].kind == "para"
    assert blocks[0].list_id != blocks[1].list_id
```

- [ ] **Step 2: Run — expect ImportError / failure**

Run: `python -m pytest tests/test_cv_diff.py -x -q`
Expected: FAIL (module/functions missing).

- [ ] **Step 3: Implement `parse_blocks`**

Adapt `change_report._blocks` logic (comment/fence/`<!-- -->`/`---`/bare-HTML skipping is identical) but:
- iterate with `enumerate(md.splitlines())` to keep `line_no`.
- maintain `section: list[str]`; on a heading, update it and append a `Heading(line_no, level, tuple(section))`; `continue`.
- `list_id`: an int counter; increment it whenever the current line is blank / a heading / a non-bullet after a bullet run — i.e. a bullet gets the *current* `list_id`; a paragraph line gets its own fresh `list_id` and then the counter increments again so the next thing is separate.
- `rank`: 1-based position within the current `list_id`.
- `kind`: `"bullet"` if `stripped[:2] in ("- ", "* ")` else `"para"`.
- `text`: `stripped[2:].strip()` for a bullet, else `stripped`.
- Return `(blocks, headings)`.

- [ ] **Step 4: Run parse tests — expect PASS**

Run: `python -m pytest tests/test_cv_diff.py -x -q`

- [ ] **Step 5: Write failing tests for `match_blocks`, `classify`, `redline`**

```python
def test_match_pairs_by_similarity_regardless_of_position():
    base, _ = parse_blocks("## A\n\n- alpha bullet here\n- beta bullet here\n")
    new, _ = parse_blocks("## A\n\n- beta bullet here\n- alpha bullet here now\n")
    pairs, dropped, added = match_blocks(base, new)
    assert not dropped and not added
    got = {(b.text, n.text) for b, n in pairs}
    assert ("beta bullet here", "beta bullet here") in got
    assert ("alpha bullet here", "alpha bullet here now") in got

def test_match_reports_dropped_and_added():
    base, _ = parse_blocks("## A\n\n- kept line\n- goes away entirely\n")
    new, _ = parse_blocks("## A\n\n- kept line\n- brand new unrelated content\n")
    pairs, dropped, added = match_blocks(base, new)
    assert [d.text for d in dropped] == ["goes away entirely"]
    assert [a.text for a in added] == ["brand new unrelated content"]

def test_classify():
    assert classify("Led the team.", "Led the team.") == "identical"
    assert classify("Led the team.", "**Led** the team.") == "formatting"
    assert classify("Managed teh cluster daily.", "Managed the cluster daily.") == "typo"
    assert classify("Managed the ingestion cluster.", "Ran the ingestion pipeline cluster.") == "reword"
    assert classify("Managed the ingestion cluster.", "Wrote onboarding docs for new hires.") == "rewrite"

def test_redline_marks_word_changes():
    out = redline("Managed teh cluster", "Managed the cluster")
    assert '<del class="cvd-del">teh</del>' in out
    assert '<ins class="cvd-ins">the</ins>' in out
    assert out.startswith("Managed ")
```

- [ ] **Step 6: Run — expect FAIL**

- [ ] **Step 7: Implement `match_blocks`, `classify`, `redline`**

`match_blocks`:
- Mirror `change_report.change_report`'s matching: for each base block, best `difflib.SequenceMatcher(None, _norm(b.text), _norm(n.text)).ratio()` over unclaimed new blocks; accept ≥ 0.6.
- Leftovers → second pass: reuse `_rematch_dropped_added` shape but keep `Block` objects (adapt: it currently takes dicts with `text`/`section`; write a small local `_rematch(dropped, added)` on `Block` lists using `_word_ratio` ≥ `_REMATCH_THRESHOLD`, globally-best-first, same as the original).
- **Section-leaf tiebreaker:** when two candidate new blocks are within 0.05 ratio of each other, prefer the one whose `section_path[-1:] == b.section_path[-1:]`.
- Return `pairs` sorted by the new block's index in `new`, plus `dropped` (base order), `added` (new order).

`classify(before, after)`:
- `nb, na = _norm(before), _norm(after)`
- if `nb == na`: return `"identical"` if `_norm(before, keep_fmt=True) == _norm(after, keep_fmt=True)` else `"formatting"`.
- `r = _word_ratio(before, after)` (reuse `change_report._word_ratio`).
- `r >= 0.95` → `"typo"`; `r >= 0.5` → `"reword"`; else `"rewrite"`.

`redline(before, after)`:
- Tokenise: `re.findall(r"\S+\s*", s)` on each (keeps trailing space with the word).
- `difflib.SequenceMatcher(None, btok, atok).get_opcodes()`:
  - `equal` → emit `after` tokens joined.
  - `delete` → `<del class="cvd-del">{joined base tokens, rstripped}</del> `
  - `insert` → `<ins class="cvd-ins">{joined new tokens, rstripped}</ins> `
  - `replace` → del then ins.
- Collapse doubled spaces; `strip()` the result. Escape `<`/`>`/`&` in the literal text runs (`html.escape`) — the tailored CV text can contain `<` (e.g. "<10ms").

- [ ] **Step 8: Run all Task 1 tests — expect PASS**

Run: `python -m pytest tests/test_cv_diff.py -q`

- [ ] **Step 9: Commit**

```bash
git add app/cv/cv_diff.py tests/test_cv_diff.py
git commit -m "feat: cv_diff block parser, matcher, content classification"
```

---

## Task 2: Structural analysis — moves, ranks, section changes

**Files:**
- Modify: `app/cv/cv_diff.py`
- Test: `tests/test_cv_diff.py`

**Interfaces:**
- Consumes: `Block`, `Heading`, `parse_blocks`, `match_blocks` (Task 1); `change_report._section_diff`.
- Produces:
  - `Annotations` dataclass:
    - `heading_notes: dict[int, str]` — heading `line_no` → badge HTML (section moved / renamed).
    - `block_moves: dict[int, str]` — new-block `line_no` → `moved from "X"` badge HTML.
    - `block_ranks: dict[int, str]` — new-block `line_no` → `was #N` badge HTML.
    - `list_reordered: set[int]` — new-block `line_no`s that should carry a single `list reordered` badge (first item of a wholly-reshuffled list).
    - `restructured: bool`.
    - counts: `sections_reordered`, `blocks_moved`, `lists_reordered`.
  - `analyse_structure(base_blocks, base_headings, new_blocks, new_headings, pairs) -> Annotations`.

- [ ] **Step 1: Write failing tests**

```python
from app.cv.cv_diff import analyse_structure

def _prep(base_md, new_md):
    bb, bh = parse_blocks(base_md)
    nb, nh = parse_blocks(new_md)
    pairs, dropped, added = match_blocks(bb, nb)
    return bb, bh, nb, nh, pairs

def test_section_move_annotates_heading_not_children():
    base = "## Experience\n\n- x work\n\n## Skills\n\n- y skill\n"
    new = "## Skills\n\n- y skill\n\n## Experience\n\n- x work\n"
    bb, bh, nb, nh, pairs = _prep(base, new)
    a = analyse_structure(bb, bh, nb, nh, pairs)
    skills_h = next(h for h in nh if h.path == ("Skills",))
    assert skills_h.line_no in a.heading_notes
    assert "moved" in a.heading_notes[skills_h.line_no]
    assert not a.block_moves  # children rode along -> no per-block badge
    assert a.sections_reordered == 1

def test_within_list_reorder_sets_rank_badges():
    base = "## S\n\n- first alpha\n- second beta\n- third gamma\n- fourth delta\n"
    new = "## S\n\n- third gamma\n- first alpha\n- second beta\n- fourth delta\n"
    bb, bh, nb, nh, pairs = _prep(base, new)
    a = analyse_structure(bb, bh, nb, nh, pairs)
    gamma = next(n for n in nb if n.text == "third gamma")
    assert "was #3" in a.block_ranks[gamma.line_no]
    assert a.lists_reordered >= 1

def test_majority_reshuffle_collapses_to_list_reordered():
    base = "## S\n\n- a one\n- b two\n- c three\n- d four\n- e five\n"
    new = "## S\n\n- e five\n- c three\n- a one\n- d four\n- b two\n"
    bb, bh, nb, nh, pairs = _prep(base, new)
    a = analyse_structure(bb, bh, nb, nh, pairs)
    assert a.list_reordered
    assert not a.block_ranks  # per-bullet markers suppressed

def test_container_change_badges_the_block():
    base = "## Experience\n\n- portable line here\n\n## Skills\n\n- s\n"
    new = "## Experience\n\n- e\n\n## Skills\n\n- s\n- portable line here\n"
    bb, bh, nb, nh, pairs = _prep(base, new)
    a = analyse_structure(bb, bh, nb, nh, pairs)
    moved = next(n for n in nb if n.text == "portable line here")
    assert 'moved from "Experience"' in a.block_moves[moved.line_no]

def test_global_move_cap_sets_restructured():
    # 6 sections whose order is fully reversed
    secs = [f"## S{i}\n\n- item {i}\n" for i in range(6)]
    base = "\n".join(secs)
    new = "\n".join(reversed(secs))
    bb, bh, nb, nh, pairs = _prep(base, new)
    a = analyse_structure(bb, bh, nb, nh, pairs)
    assert a.restructured
    assert not a.heading_notes and not a.block_moves
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement `analyse_structure`**

1. **Section moves (LIS on headings).** Build the sequence of base-heading-index for each new heading matched by identical `path` (or, if not found, by best `_norm` ratio ≥ 0.6 — that pairing also yields renames). Compute the longest increasing subsequence; new headings *not* in it are "moved". Direction arrow: compare fractional document position (new heading index / len vs base heading index / len). Badge: `<span class="cvd-move">↑ moved above "<next kept heading leaf>"</span>` — simplest: `↑ moved` / `↓ moved` with no anchor is acceptable if "above X" is fiddly; keep `↑ moved earlier` / `↓ moved later`. Record in `heading_notes`. Count into `sections_reordered`.
2. **Section renames.** A new heading paired to a base heading with a different leaf and *same* LIS position → `heading_notes[line] = '<span class="cvd-move">renamed from "OLD"</span>'`; count into `sections_reordered`.
3. **Moved sections suppress child annotations.** Collect the set of `new` section paths whose heading is in `heading_notes` for a move. Any `pair` whose new block sits under such a path and whose base block sat under the *matching* base path is "rode along" → no block move/rank badge.
4. **Container change.** For a remaining `pair`, if `base_block.section_path[-1] != new_block.section_path[-1]`: `block_moves[new_block.line_no] = '<span class="cvd-move">↑/↓ moved from "<base leaf>"</span>'`. Count `blocks_moved`.
5. **Within-list reorder.** Group remaining pairs by `new_block.list_id`. Within a group, compare each block's `new rank` to its `base rank`. Let `changed = [pairs whose rank differs]`. If `len(changed) > len(group) / 2`: `list_reordered.add(first group member's line_no)`, `lists_reordered += 1`. Else: for each changed pair `block_ranks[new.line_no] = f'<span class="cvd-rank">was #{base_rank}</span>'`; `lists_reordered += 1` if any.
6. **Global cap.** If `blocks_moved + (# moved headings)` > 4: clear `heading_notes` move entries + `block_moves`, set `restructured = True`. Keep renames? Drop them too for simplicity. Keep `block_ranks` and `list_reordered`.

LIS helper: standard `O(n log n)` with `bisect`; return the set of indices in one longest subsequence.

- [ ] **Step 4: Run Task 2 tests — expect PASS**

Run: `python -m pytest tests/test_cv_diff.py -q`

- [ ] **Step 5: Commit**

```bash
git add app/cv/cv_diff.py tests/test_cv_diff.py
git commit -m "feat: cv_diff structural analysis — section/block moves, rank markers, caps"
```

---

## Task 3: Assembly — annotated markdown, digest, public `build`

**Files:**
- Modify: `app/cv/cv_diff.py`
- Test: `tests/test_cv_diff.py`

**Interfaces:**
- Consumes: everything from Tasks 1–2; `change_report._section_diff` for removed/added section counts.
- Produces:
  ```python
  @dataclass
  class DiffResult:
      annotated_markdown: str      # digest <p> + callout <div> + tailored CV with marks
      digest: dict                 # the §7 dict from the spec
      summary_line: str
      added_items: list[tuple[str, str]]   # (section leaf, text)
      is_empty: bool               # no meaningful change at all
  def build(base_cv: str, tailored_cv: str) -> DiffResult
  ```

- [ ] **Step 1: Write failing tests**

```python
from app.cv.cv_diff import build

BASE = """# Jane Doe

## Experience

- Managed teh ingestion cluster.
- Ran the payments migration.
- Legacy Perl maintenance.

## Skills

- Python, Go, Rust
"""

def test_dropped_block_struck_in_place():
    new = BASE.replace("- Legacy Perl maintenance.\n", "")
    r = build(BASE, new)
    assert '<del class="cvd-del cvd-block">Legacy Perl maintenance.</del>' in r.annotated_markdown
    assert r.digest["dropped"] == 1

def test_typo_shows_redline_even_when_bullet_reordered():
    new = """# Jane Doe

## Experience

- Ran the payments migration.
- Managed the ingestion cluster.
- Legacy Perl maintenance.

## Skills

- Python, Go, Rust
"""
    r = build(BASE, new)
    am = r.annotated_markdown
    assert '<del class="cvd-del">teh</del>' in am and '<ins class="cvd-ins">the</ins>' in am
    assert "was #1" in am  # the managed-cluster bullet fell from #1 to #2 -> its base rank was 1
    assert r.digest["reworded"] == 1

def test_added_block_higher_attention_and_callout():
    new = BASE.replace("## Experience\n", "## Summary\n\nPassionate about payments infra.\n\n## Experience\n")
    r = build(BASE, new)
    assert "cvd-added" in r.annotated_markdown
    assert '<span class="cvd-new">new</span>' in r.annotated_markdown
    assert any("Passionate about payments infra." in t for _, t in r.added_items)
    assert "cvd-callout" in r.annotated_markdown

def test_heavy_rewrite_is_block_swap():
    new = BASE.replace("- Ran the payments migration.",
                       "- Owned quarterly OKR planning for a nine-person platform group.")
    r = build(BASE, new)
    am = r.annotated_markdown
    assert '<del class="cvd-del cvd-block">Ran the payments migration.</del>' in am
    assert "Owned quarterly OKR planning" in am

def test_identical_is_empty():
    r = build(BASE, BASE)
    assert r.is_empty
    assert r.summary_line == "No changes from your base CV."

def test_summary_line_omits_zero_terms():
    new = BASE.replace("- Legacy Perl maintenance.\n", "")
    r = build(BASE, new)
    assert r.summary_line == "1 bullet dropped"
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement `build`**

1. `base_blocks, base_headings = parse_blocks(base_cv)`; same for tailored.
2. `pairs, dropped, added = match_blocks(base_blocks, tailored_blocks)`.
3. `ann = analyse_structure(...)`.
4. `lines = tailored_cv.splitlines()`.
5. **Annotate matched-pair lines.** For each `(b, n)` in `pairs`:
   - `cls = classify(b.text, n.text)`.
   - Determine the inline body:
     - `identical` / `formatting` → keep `n`'s original text (no change).
     - `typo` / `reword` → `redline(b.text, n.text)`.
     - `rewrite` → handled as a *removed + added* pair instead: add `b` to a local `swaps` list (render `b.text` struck as a `cvd-block` immediately before `lines[n.line_no]`), keep `n.text` plain but wrapped `<ins class="cvd-ins cvd-block">…</ins>`. Count once under `reworded`.
   - Suffix badges in order: `ann.block_moves.get(n.line_no)`, `ann.block_ranks.get(n.line_no)`, or `list reordered` span if `n.line_no in ann.list_reordered`.
   - Rebuild the line preserving the leading `- ` / `* ` marker when `n.kind == "bullet"`: `f"{marker}{body} {badges}"`.
6. **Weave dropped blocks.** For each `d` in `dropped`: find its preceding surviving base block (previous base block that is in `pairs`); its insertion point is right after that block's tailored counterpart line (or at the top of the tailored counterpart of `d`'s section heading if none). Render:
   - bullet → `- <del class="cvd-del cvd-block">{escape(d.text)}</del>`
   - para → `<del class="cvd-del cvd-block">{escape(d.text)}</del>`
   Collect `(insert_after_line_index, rendered)` then splice in descending index order.
   Dropped **headings** (a base heading whose `path` has no tailored match): render `<del class="cvd-del cvd-block">**{leaf}**</del>` at the mapped position.
7. **Annotate headings.** For `line_no, note` in `ann.heading_notes`: append ` {note}` to `lines[line_no]`.
8. **Added blocks.** For each `a` in `added`: wrap `lines[a.line_no]` body as `<ins class="cvd-ins cvd-block cvd-added">{body}</ins> <span class="cvd-new">new</span>` (keep bullet marker). Append `(a.section_path[-1] if a.section_path else "", a.text)` to `added_items`.
9. **Digest** (spec §7): `blocks_moved`, `lists_reordered`, `sections_reordered` from `ann`; `dropped = len(dropped headings+blocks)`; `reworded = count of typo+reword+rewrite pairs`; `added = len(added)`; `renamed_sections` from ann; `restructured = ann.restructured`; `added_items`.
10. **`summary_line`**: join non-zero terms with ` · `. Terms & pluralisation:
    - `sections_reordered` → `"{n} section(s) reordered"`
    - `blocks_moved` → `"{n} block(s) moved"`
    - `lists_reordered` → `"{n} list(s) reordered"`
    - `dropped` → `"{n} bullet(s) dropped"`
    - `reworded` → `"{n} reworded"`
    - `added` → `"{n} new paragraph(s) added"`
    If `restructured`: first term is literal `"The document was substantially reordered"`, followed only by dropped/reworded/added terms.
    If every term is zero: `summary_line = "No changes from your base CV."`, `is_empty = True`.
11. **Prepend digest + callout** to the joined lines:
    - `<p class="cvd-digest">{summary_line}</p>`
    - if `added_items`: `<div class="cvd-callout"><strong>New — not from your base CV, review these</strong><ul>` + `<li>[{sec}] {escape(text)}</li>` … `</ul></div>`
    - blank line, then the annotated document.
12. `return DiffResult(annotated_markdown=…, digest=…, summary_line=…, added_items=…, is_empty=…)`.

Keep helpers private (`_…`). `html.escape` every raw text run that isn't already going through `redline`.

- [ ] **Step 4: Run all `tests/test_cv_diff.py` — expect PASS**

Run: `python -m pytest tests/test_cv_diff.py -q`

- [ ] **Step 5: Full suite sanity**

Run: `python -m pytest -q`
Expected: no new failures (change_report still present and used at this point).

- [ ] **Step 6: Commit**

```bash
git add app/cv/cv_diff.py tests/test_cv_diff.py
git commit -m "feat: cv_diff.build — annotated markdown, digest, summary line"
```

---

## Task 4: `render_diff_html` + `_DIFF_CSS`

**Files:**
- Modify: `app/cv/render.py`
- Test: `tests/test_cv_render.py`

**Interfaces:**
- Consumes: `_run(markdown, css, out_name, work, fmt)` (already has `fmt`).
- Produces: `render_diff_html(markdown: str, css: str) -> str`; module constant `_DIFF_CSS: str`.

- [ ] **Step 1: Write failing test**

```python
# tests/test_cv_render.py
from app.cv.render import render_diff_html  # add to imports

@needs_docwrite
def test_render_diff_html_is_continuous_and_styled():
    md = ('<p class="cvd-digest">1 bullet dropped</p>\n\n'
          '# CV\n\n- kept\n- <del class="cvd-del cvd-block">gone</del>\n')
    html = render_diff_html(md, "body{font-family:serif}")
    assert "<!DOCTYPE html" in html or "<!doctype html" in html.lower()
    assert ".cvd-del" in html          # _DIFF_CSS injected
    assert "cvd-digest" in html
    assert 'class="page"' not in html  # --html is continuous, not paginated
```

- [ ] **Step 2: Run — expect FAIL (ImportError)**

Run: `python -m pytest tests/test_cv_render.py -q -k diff`

- [ ] **Step 3: Implement**

```python
_DIFF_CSS = """
.cvd-del { color: #b3261e; text-decoration: line-through; }
.cvd-ins { color: #0a7d33; text-decoration: none; background: #e9f6ec; }
.cvd-block { display: block; padding: 0.1em 0.4em; border-left: 3px solid transparent; }
del.cvd-block { border-left-color: #b3261e; }
ins.cvd-block { border-left-color: #0a7d33; }
.cvd-added { background: #fff3d6; border-left-color: #d9a400; }
.cvd-move, .cvd-rank, .cvd-new {
  font-size: 0.75em; font-weight: 600; padding: 0 0.35em; margin-left: 0.4em;
  border-radius: 0.5em; vertical-align: 0.1em; white-space: nowrap;
}
.cvd-move { color: #444; background: #ececec; border: 1px solid #ccc; }
.cvd-rank { color: #666; background: #f3f3f3; border: 1px solid #ddd; font-weight: 500; }
.cvd-new  { color: #7a5c00; background: #ffe9a8; border: 1px solid #e0c366; }
.cvd-digest { font-size: 0.95em; color: #555; margin: 0 0 1em; padding-bottom: 0.6em; border-bottom: 1px solid #ddd; }
.cvd-callout { background: #fff3d6; border: 1px solid #d9a400; border-radius: 6px; padding: 0.6em 0.9em; margin: 0 0 1.5em; }
.cvd-callout ul { margin: 0.4em 0 0; padding-left: 1.2em; }
"""

def render_diff_html(markdown: str, css: str) -> str:
    """Continuous single-page HTML for the Differences tab — the tailored CV
    with base->tailored diff marks woven in."""
    with tempfile.TemporaryDirectory(prefix="cv-diff-") as work:
        out = _run(markdown, (css or "") + "\n" + _DIFF_CSS, "cv.html", work, "html")
        with open(out) as f:
            return f.read()
```

- [ ] **Step 4: Run — expect PASS**

Run: `python -m pytest tests/test_cv_render.py -q`

- [ ] **Step 5: Commit**

```bash
git add app/cv/render.py tests/test_cv_render.py
git commit -m "feat: render_diff_html + _DIFF_CSS for the Differences tab"
```

---

## Task 5: Schema — `base_cv_snapshot`; persist it; retire `change_report`

**Files:**
- Modify: `app/db/schema.py` (DDL + migration + `init_db` registration)
- Modify: `app/routes/cv.py` (`_persist_draft`, `_task_cv_tailor` both branches, imports)
- Modify: `app/cv/change_report.py` (drop the public `change_report()` fn + `_group_by_section`)
- Delete: `tests/test_cv_change_report.py`
- Test: `tests/test_schema.py` (`test_job_cv_table_columns_and_cascade` — add `base_cv_snapshot` to the expected set; add a new `test_init_db_adds_job_cv_base_cv_snapshot_defaulting_empty` copied from the `handled_suggestions` one right below it at ~line 1308), `tests/test_cv_task.py` (for `_persist_draft`)

**Interfaces:**
- Consumes: `q.upsert_job_cv` (generic `**fields`), `q.get_job_cv` (`SELECT *`, so the new column is returned automatically — no queries.py change).
- Produces: `job_cv.base_cv_snapshot TEXT NOT NULL DEFAULT ''`, written by `_persist_draft`.

- [ ] **Step 1: Write failing migration + persist tests**

In `tests/test_schema.py`: extend the expected column set in
`test_job_cv_table_columns_and_cascade` with `"base_cv_snapshot"`, and add a
migration test copied from `test_init_db_adds_job_cv_handled_suggestions_defaulting_empty`
(~line 1308) — legacy `job_cv` DDL *without* `base_cv_snapshot` and *with* a
`handled_suggestions` column, insert a row, `init_db(conn)`, assert the column
now exists and defaults `''`, and idempotency.

In `tests/test_cv_task.py` (uses real `conn` + job fixtures — match its style):

```python
def test_persist_draft_snapshots_the_base_cv(conn):
    from app.routes.cv import _persist_draft
    jid = <make a job with the module's existing helper>
    settings = {"base_cv": "# Base v1\n\n- one\n", "base_guardrails": ""}
    _persist_draft(conn, jid, draft="# Tailored\n\n- one\n", findings=[], settings=settings)
    assert q.get_job_cv(conn, jid)["base_cv_snapshot"] == "# Base v1\n\n- one\n"
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Schema changes**

- Add to `job_cv` DDL after `base_hash`: `base_cv_snapshot TEXT NOT NULL DEFAULT '',`
- Add migration (place next to `_migrate_job_cv_add_handled_suggestions`):
  ```python
  def _migrate_job_cv_add_base_cv_snapshot(conn: sqlite3.Connection) -> None:
      cols = {r[1] for r in conn.execute("PRAGMA table_info(job_cv)")}
      if "base_cv_snapshot" not in cols:
          conn.execute("ALTER TABLE job_cv ADD COLUMN base_cv_snapshot TEXT NOT NULL DEFAULT ''")
          conn.commit()
  ```
- Register in `init_db` after `_migrate_job_cv_add_handled_suggestions(conn)`.

- [ ] **Step 4: `_persist_draft` — snapshot + drop change_report**

- `fields` becomes: `{"tailored_cv": draft, "base_hash": _base_hash(settings), "base_cv_snapshot": settings.get("base_cv", "")}`.
- Drop `"change_report": rep`.
- Keep the `rep` parameter for now **or** remove it and update both call sites — remove it: change signature to `_persist_draft(conn, job_id, *, draft, findings, settings)`.

- [ ] **Step 5: `_task_cv_tailor` — drop `change_report` calls**

- Remove `from app.cv.change_report import change_report` (line 18).
- Baseline branch (~line 445): delete `rep = change_report(settings["base_cv"], draft)`; call `_persist_draft(conn, job_id, draft=draft, findings=findings, settings=settings)`.
- Generate branch (~line 481): same deletion; `_persist_draft(conn, job_id, draft=draft, findings=findings, settings=settings)`.

- [ ] **Step 6: Trim `change_report.py`**

- Delete the public `def change_report(...)` function and `def _group_by_section(...)`.
- Keep `_blocks`, `_norm`, `_section_diff`, `_word_ratio`, `_rematch_dropped_added`, `_REMATCH_THRESHOLD`, regexes, `_HEADING_RE` etc. — `cv_diff` imports them.
- `git rm tests/test_cv_change_report.py`.

- [ ] **Step 7: Run**

Run: `python -m pytest -q`
Expected: failures only in tests that assert on the removed "What tailoring changed" UI (fixed in Task 7) and any that import `change_report` the function. Fix the latter now (they should move to `test_cv_diff.py` concepts or be deleted). Migration + persist tests PASS.

- [ ] **Step 8: Commit**

```bash
git add app/db/schema.py app/routes/cv.py app/cv/change_report.py tests/
git rm tests/test_cv_change_report.py
git commit -m "feat: snapshot base_cv onto job_cv at generation; retire change_report()"
```

---

## Task 6: Route `GET /jobs/{job_id}/cv/diff.html` + fallback template

**Files:**
- Modify: `app/routes/cv.py`
- Create: `app/templates/cv/_cv_diff_fallback.html`
- Test: `tests/test_routes_cv_workbench.py`

**Interfaces:**
- Consumes: `cv_diff.build`, `render_diff_html`, `doc_write_available`, `CvRenderError`, `q.get_job`, `q.get_job_cv`, `q.get_cv_settings`.
- Produces: route `cv_diff_html(job_id, conn)` → `HTMLResponse`.

- [ ] **Step 1: Write failing tests**

```python
def test_diff_html_renders_with_a_draft(client, cv_on, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# CV\n\n- kept\n",
                    base_cv_snapshot="# CV\n\n- kept\n- dropped\n")
    with patch("app.routes.cv.doc_write_available", return_value=True), \
         patch("app.routes.cv.render_diff_html", side_effect=lambda md, css: f"<!doctype html>\n{md}"):
        r = client.get(f"/jobs/{jid}/cv/diff.html")
    assert r.status_code == 200
    assert "cvd-del" in r.text and "dropped" in r.text

def test_diff_html_404_without_draft(client, cv_on, conn):
    jid = _job(conn)
    assert client.get(f"/jobs/{jid}/cv/diff.html").status_code == 404

def test_diff_html_predates_tracking_when_snapshot_empty(client, cv_on, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# CV\n", base_cv_snapshot="")
    r = client.get(f"/jobs/{jid}/cv/diff.html")
    assert r.status_code == 200
    assert "predates change tracking" in r.text

def test_diff_html_fallback_without_doc_write(client, cv_on, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# CV\n\n- a\n", base_cv_snapshot="# CV\n\n- a\n- b\n")
    with patch("app.routes.cv.doc_write_available", return_value=False):
        r = client.get(f"/jobs/{jid}/cv/diff.html")
    assert r.status_code == 200
    assert "<del" in r.text  # markdown filter rendered the annotated md
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement the route** (place after `cv_preview_html`, ~line 186)

```python
@router.get(
    "/jobs/{job_id}/cv/diff.html", response_class=HTMLResponse,
    dependencies=[Depends(require_cv_enabled)],
)
def cv_diff_html(job_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    job = q.get_job(conn, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    row = q.get_job_cv(conn, job_id)
    if row is None or not row["tailored_cv"]:
        raise HTTPException(status_code=404, detail="No tailored CV")
    if not row["base_cv_snapshot"]:
        return templates.TemplateResponse(
            request, "cv/_cv_diff_fallback.html",
            {"predates": True, "body_html": ""},
        )
    settings = q.get_cv_settings(conn)
    result = build(row["base_cv_snapshot"], row["tailored_cv"])
    if doc_write_available():
        try:
            return HTMLResponse(render_diff_html(result.annotated_markdown, settings["css"]))
        except CvRenderError as exc:
            raise HTTPException(status_code=503, detail=str(exc))
    return templates.TemplateResponse(
        request, "cv/_cv_diff_fallback.html",
        {"predates": False, "body_html": md_filter(result.annotated_markdown)},
    )
```

- `from app.cv.cv_diff import build` at top.
- `render_diff_html` into the existing `from app.cv.render import …` line.
- For `md_filter`: reuse whatever the Jinja `markdown` filter calls (grep `template_env` for `"markdown"`); call that function directly, or just render the fallback template with `{{ body_md | markdown }}` and pass `body_md` instead of pre-rendering.

- [ ] **Step 4: Fallback template** — `app/templates/cv/_cv_diff_fallback.html`

A **standalone** HTML document (it is an iframe body):

```html
<!doctype html>
<html><head><meta charset="utf-8"><style>
  body { font: 14px/1.5 -apple-system, system-ui, sans-serif; margin: 2rem; color: #222; }
  del { color: #b3261e; } ins { color: #0a7d33; text-decoration: none; background: #e9f6ec; }
  .cvd-move, .cvd-rank, .cvd-new { font-size: .8em; background: #eee; border-radius: .4em; padding: 0 .3em; }
  .cvd-callout { background: #fff3d6; border: 1px solid #d9a400; padding: .6rem .9rem; border-radius: 6px; }
</style></head><body>
{% if predates %}
  <p class="muted">This draft predates change tracking — regenerate to see the differences.</p>
{% else %}
  {{ body_md | markdown }}
{% endif %}
</body></html>
```

(Pass `body_md=result.annotated_markdown` from the route instead of pre-rendering `body_html`.)

- [ ] **Step 5: Run — expect PASS**

Run: `python -m pytest tests/test_routes_cv_workbench.py -q -k diff`

- [ ] **Step 6: Commit**

```bash
git add app/routes/cv.py app/templates/cv/_cv_diff_fallback.html tests/test_routes_cv_workbench.py
git commit -m "feat: /jobs/{id}/cv/diff.html route + no-doc-write fallback"
```

---

## Task 7: UI — Differences tab, shared tab partial, delete change-report block

**Files:**
- Create: `app/templates/cv/_preview_tabs.html`
- Modify: `app/templates/cv/_preview_pane.html`
- Modify: `app/templates/cv/_accepted.html`
- Delete: `app/templates/cv/_change_report.html`
- Modify: `app/templates/base.html` (only if a CSS/JS gap shows up — expected: none)
- Test: `tests/test_routes_cv_workbench.py`, `tests/test_routes_cv_actions.py`

**Interfaces:**
- `_preview_tabs.html` params: `job`, `active` (`"base"`|`"tailored"`|`"diff"`), `mid_label` (`"Tailored"` or `"Accepted"`), `has_draft`, `title_prefix`.
- The generic tab JS in `base.html` (`data-variant` + lazy `data-src`) already drives 3 tabs — no JS change.

- [ ] **Step 1: Write failing tests**

```python
def test_preview_pane_has_differences_tab(client, cv_on, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Draft\n", base_cv_snapshot="# Base\n")
    with patch("app.routes.cv.doc_write_available", return_value=True):
        r = client.get(f"/jobs/{jid}/cv")
    assert 'data-variant="diff"' in r.text
    assert f'data-src="/jobs/{jid}/cv/diff.html"' in r.text
    assert "What tailoring changed" not in r.text
    assert 'id="cv-change-report"' not in r.text

def test_differences_tab_disabled_without_draft(client, cv_on, conn):
    jid = _job(conn)
    with patch("app.routes.cv.doc_write_available", return_value=True):
        r = client.get(f"/jobs/{jid}/cv")
    # tab present but disabled, no iframe
    assert 'data-variant="diff"' in r.text
    assert f'/jobs/{jid}/cv/diff.html' not in r.text

def test_accepted_view_has_three_tabs(client, cv_on, conn):
    jid = _job(conn)
    q.upsert_job_cv(conn, jid, tailored_cv="# Final\n", base_cv_snapshot="# Base\n")
    q.finalize_job_cv(conn, jid)
    with patch("app.routes.cv.doc_write_available", return_value=True):
        r = client.get(f"/jobs/{jid}/cv")
    assert r.text.count('class="cv-preview-tab"') == 3
    assert 'data-variant="diff"' in r.text
```

Also: grep existing tests for `"What tailoring changed"`, `_change_report`, `cv-change-report`, `rep.summary` and fix/remove them.

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: `_preview_tabs.html`** — extract from current `_preview_pane.html` lines 16–39

```html
{% set active = active or ('tailored' if has_draft else 'base') %}
<div class="cv-preview-bar">
  <div class="cv-preview-tabs" role="tablist" aria-label="CV preview">
    <button type="button" class="cv-preview-tab" role="tab" data-variant="base"
            aria-selected="{{ 'true' if active == 'base' else 'false' }}">Base</button>
    <button type="button" class="cv-preview-tab" role="tab" data-variant="tailored"
            aria-selected="{{ 'true' if active == 'tailored' else 'false' }}"
            {% if not has_draft %}disabled title="Update to see the tailored version"{% endif %}>{{ mid_label or 'Tailored' }}</button>
    <button type="button" class="cv-preview-tab" role="tab" data-variant="diff"
            aria-selected="{{ 'true' if active == 'diff' else 'false' }}"
            {% if not has_draft %}disabled title="Update to see the differences"{% endif %}>Differences</button>
  </div>
  <button type="button" class="btn btn-subtle cv-preview-fs">&#9974; Fullscreen</button>
</div>
<div class="cv-preview-stage">
  <iframe class="cv-preview-doc{{ ' is-active' if active == 'base' }}" data-variant="base" title="{{ title_prefix }}Base CV"
          {{ 'src' if active == 'base' else 'data-src' }}="/jobs/{{ job.id }}/cv/preview.html?variant=base"
          sandbox="allow-scripts allow-same-origin"></iframe>
  {% if has_draft %}
  <iframe class="cv-preview-doc{{ ' is-active' if active == 'tailored' }}" data-variant="tailored" title="{{ title_prefix }}{{ mid_label or 'Tailored' }} CV"
          {{ 'src' if active == 'tailored' else 'data-src' }}="/jobs/{{ job.id }}/cv/preview.html?variant=tailored"
          sandbox="allow-scripts allow-same-origin"></iframe>
  <iframe class="cv-preview-doc{{ ' is-active' if active == 'diff' }}" data-variant="diff" title="{{ title_prefix }}Differences"
          {{ 'src' if active == 'diff' else 'data-src' }}="/jobs/{{ job.id }}/cv/diff.html"
          sandbox="allow-scripts allow-same-origin"></iframe>
  {% endif %}
  <div class="cv-preview-notice" aria-live="polite">
    <p class="n-loading">&#8635; Loading preview&hellip;</p>
  </div>
</div>
```

- [ ] **Step 4: Rewire `_preview_pane.html`**

- Replace the hand-written `.cv-preview-bar` + `.cv-preview-stage` block (lines 16–39) with:
  `{% include "cv/_preview_tabs.html" with context %}` after setting nothing extra (it reads `job`, `has_draft`; pass `mid_label='Tailored'`, `title_prefix=''`, `active=''`). Use `{% set title_prefix = '' %}{% set mid_label = 'Tailored' %}` before the include, or `{% with mid_label='Tailored', title_prefix='' %}…{% endwith %}`.
- Keep the `{% if has_draft %}` decision block (Accept + `_cv_export`).
- **Delete** the `<div id="cv-change-report">{% include "cv/_change_report.html" %}</div>` line.
- Keep `<div id="cv-findings">`.
- Keep the `{% else %}` no-doc-write branch (raw markdown) as-is.

- [ ] **Step 5: Rewire `_accepted.html`**

- Replace the single-iframe block (lines 15–26) with `{% include "cv/_preview_tabs.html" %}` using `active='tailored'`, `mid_label='Accepted'`, `has_draft=true`, `title_prefix='Accepted '`.
- Keep the `.cv-preview-actions` (export + Start over) above it and the `{% else %}` raw-markdown branch.

- [ ] **Step 6: Delete `_change_report.html`**

```bash
git rm app/templates/cv/_change_report.html
```

- [ ] **Step 7: Run**

Run: `python -m pytest -q`
Fix any remaining assertions on the old change-report markup. Expected green.

- [ ] **Step 8: Commit**

```bash
git add app/templates/cv/ tests/
git rm app/templates/cv/_change_report.html
git commit -m "feat: Differences preview tab; shared _preview_tabs partial; drop change-report block"
```

---

## Task 8: Manual-test pass + full suite + docs

**Files:**
- Modify: `docs/superpowers/specs/2026-09-07-cv-differences-tab-design.md` (only if implementation diverged — note deltas at the bottom)

- [ ] **Step 1: Full suite**

Run: `python -m pytest -q`
Expected: all green (baseline was 1621; net change from deleted `test_cv_change_report.py` ≈ −10, plus new `test_cv_diff.py`).

- [ ] **Step 2: Dev server (throwaway DB)**

Use the `run-dev-server` recipe (copy `config.toml` + `sqlite3 <live> ".backup ./job-seek.db"`, append `[cv]\nenabled=true` if missing, `python -m uvicorn app.main:app --reload --port <free>`). Pick a port not in use by another worktree.

- [ ] **Step 3: Exercise a job with an existing draft**

- `GET /jobs/<id>/cv` for a job that already has a tailored CV. Its `base_cv_snapshot` is empty (pre-migration) → Differences tab shows "predates change tracking".
- Hit **Update**, wait for regen. Now the snapshot is populated.
- Open **Differences**: confirm struck removals in place, `was #N` on reordered bullets, redline on reworded lines, the higher-attention background on any added paragraph, digest line at top, callout for added content.
- Check the accepted flow: Accept → Base/Accepted/Differences all present and switchable.

- [ ] **Step 4: Leave the server running, hand the URL to the user**

Per CLAUDE.md: report `http://127.0.0.1:<port>/jobs/<id>/cv` and wait for their go-ahead before any merge/cleanup. Do **not** proceed to finish the branch.

- [ ] **Step 5: Commit any spec deltas**

```bash
git add docs/superpowers/specs/2026-09-07-cv-differences-tab-design.md
git commit -m "docs: note Differences-tab implementation deltas"
```

---

## Self-Review

**Spec coverage:** rendering approach (Task 4/6), diff engine incl. orthogonal axes & compound changes (Tasks 1–3), reordered-list `was #N` + majority collapse (Task 2), two-tier inserts (Task 3/4), base snapshot (Task 5), route + fallback + predates-tracking (Task 6), three-tab UI incl. accepted view (Task 7), retire change_report (Task 5/7), digest + callout inside the tab (Task 3), tests + manual pass (all tasks + Task 8). Global reorder cap (Task 2). Section-leaf matching hint (Task 1).

**Placeholder scan:** every code step carries real code or a concrete adaptation instruction against a named existing function. `md_filter` in Task 6 is the one loose end — Step 3 note says resolve it by rendering the fallback template with `{{ body_md | markdown }}` (the existing Jinja filter), so no new helper is needed.

**Type consistency:** `Block`/`Heading`/`Annotations`/`DiffResult` dataclasses defined in Tasks 1–3 with stable field names; `parse_blocks` → `(list[Block], list[Heading])` used identically in Tasks 2 & 3; `build` signature fixed in Task 3 and consumed in Task 6; `render_diff_html(markdown, css)` fixed in Task 4, called in Task 6; `_persist_draft` new signature (`draft, findings, settings` — no `rep`) set in Task 5 and both call sites updated in the same task.
