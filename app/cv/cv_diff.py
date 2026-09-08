"""Deterministic base -> tailored CV diff, rendered *as the CV document*.

Turns ``(base_cv, tailored_cv)`` markdown into one annotated-markdown string: the
tailored CV with ``<del>/<ins>/<span>`` marks woven in, a digest paragraph, and an
added-content callout. No LLM involvement.
"""
from __future__ import annotations

import bisect
import difflib
import html
import re
from dataclasses import dataclass, field

from app.cv._mdtext import (
    _HEADING_RE,
    _HTML_TAG_ONLY_RE,
    _REMATCH_THRESHOLD,
    _norm,
    _word_ratio,
)

_TOK_RE = re.compile(r"\S+\s*")
_MATCH_THRESHOLD = 0.6
_QUOTE_RE = re.compile(r"(?:>\s?)+")


def _quote_prefix(stripped: str) -> tuple[str, str]:
    """``('> ' marker, remaining text)`` for a blockquote line — the marker
    keeps its nesting depth (``>> `` etc.) and always ends in one space so it
    can be re-prepended when the line is rebuilt. ``('', line)`` otherwise."""
    if not stripped.startswith(">"):
        return "", stripped
    m = _QUOTE_RE.match(stripped)
    return m.group(0).rstrip() + " ", stripped[m.end():]


# --------------------------------------------------------------------------- #
# Task 1: parsing, matching, content classification
# --------------------------------------------------------------------------- #

@dataclass
class Block:
    line_no: int
    section_path: tuple[str, ...]
    list_id: int
    rank: int
    kind: str  # "bullet" | "para" | "quote"
    text: str


@dataclass
class Heading:
    line_no: int
    level: int
    path: tuple[str, ...]


def _heading_path(stack: list[tuple[int, str]]) -> tuple[str, ...]:
    """Path from the heading stack: every heading at level >= 2, or — when the
    only thing on the stack is the document title (an ``#`` h1) — that title."""
    deep = tuple(text for level, text in stack if level >= 2)
    if deep:
        return deep
    return tuple(text for _, text in stack)


def parse_blocks(md: str) -> tuple[list[Block], list[Heading]]:
    """(blocks, headings) for a CV markdown string.

    A *block* is one bullet or one non-empty paragraph line. Comment blocks,
    fenced code, bare HTML tag lines and ``---`` rules are skipped — identical to
    ``change_report._blocks``, but here every block keeps its source ``line_no``,
    its ``list_id`` (a run of consecutive bullets shares one) and its 1-based
    ``rank`` within that list.
    """
    stack: list[tuple[int, str]] = []
    blocks: list[Block] = []
    headings: list[Heading] = []
    in_comment = in_fence = False
    list_counter = 0
    cur_list = 0
    rank = 0
    need_new_list = True

    for line_no, raw in enumerate(md.splitlines()):
        line = raw.rstrip()
        stripped = line.strip()
        if in_comment:
            if "-->" in line:
                in_comment = False
            need_new_list = True
            continue
        if stripped.startswith("```"):
            in_fence = not in_fence
            need_new_list = True
            continue
        if in_fence:
            continue
        if stripped.startswith("<!--"):
            in_comment = "-->" not in stripped
            need_new_list = True
            continue
        h = _HEADING_RE.match(line)
        if h:
            text = h.group(2).strip()
            if not text:
                need_new_list = True
                continue  # bare "##" with no title — noise
            level = len(h.group(1))
            stack = [e for e in stack if e[0] < level] + [(level, text)]
            headings.append(Heading(line_no, level, _heading_path(stack)))
            need_new_list = True
            continue
        if not stripped or stripped == "---" or _HTML_TAG_ONLY_RE.match(stripped):
            need_new_list = True
            continue

        is_bullet = stripped[:2] in ("- ", "* ")
        quote_marker, quote_body = ("", stripped) if is_bullet else _quote_prefix(stripped)
        if is_bullet:
            text = stripped[2:].strip()
        elif quote_marker:
            text = quote_body.strip()
        else:
            text = stripped
        if not re.search(r"\w", text):
            # a line with no letters or digits — a stray "#", "***", "•" — carries
            # nothing reviewable; skip it so it never shows up as a diff block.
            if not is_bullet:
                need_new_list = True
            continue
        if is_bullet:
            if need_new_list:
                list_counter += 1
                cur_list = list_counter
                rank = 0
            rank += 1
            need_new_list = False
            kind = "bullet"
        else:
            list_counter += 1
            cur_list = list_counter
            rank = 1
            need_new_list = True
            kind = "quote" if quote_marker else "para"
        blocks.append(Block(line_no, _heading_path(stack), cur_list, rank, kind, text))

    return blocks, headings


def _leaf(path: tuple[str, ...]) -> tuple[str, ...]:
    return path[-1:] if path else ()


def _rematch(
    dropped: list[Block], added: list[Block], threshold: float = _REMATCH_THRESHOLD,
) -> tuple[list[tuple[Block, Block]], list[Block], list[Block]]:
    """Second pass: re-pair leftover dropped/added blocks that are really the
    same content reworded, globally best-matching pairs first (word-ratio)."""
    candidates: list[tuple[float, int, int]] = []
    for di, d in enumerate(dropped):
        for ai, a in enumerate(added):
            r = _word_ratio(d.text, a.text)
            if r >= threshold:
                candidates.append((r, di, ai))
    candidates.sort(key=lambda c: c[0], reverse=True)
    used_d: set[int] = set()
    used_a: set[int] = set()
    pairs: list[tuple[Block, Block]] = []
    for _r, di, ai in candidates:
        if di in used_d or ai in used_a:
            continue
        used_d.add(di)
        used_a.add(ai)
        pairs.append((dropped[di], added[ai]))
    rem_d = [d for i, d in enumerate(dropped) if i not in used_d]
    rem_a = [a for i, a in enumerate(added) if i not in used_a]
    return pairs, rem_d, rem_a


def match_blocks(
    base: list[Block], new: list[Block],
) -> tuple[list[tuple[Block, Block]], list[Block], list[Block]]:
    """Pair base blocks to new blocks by text similarity, irrespective of
    position. Returns ``(pairs, dropped, added)`` — ``pairs`` ordered by the new
    block's position."""
    free = list(range(len(new)))
    pairs: list[tuple[Block, Block]] = []
    dropped: list[Block] = []

    for b in base:
        scored: list[tuple[float, bool, int]] = []
        for i in free:
            r = difflib.SequenceMatcher(None, _norm(b.text), _norm(new[i].text)).ratio()
            scored.append((r, _leaf(new[i].section_path) == _leaf(b.section_path), i))
        if not scored:
            dropped.append(b)
            continue
        scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
        best_r = scored[0][0]
        within = [t for t in scored if best_r - t[0] <= 0.05]
        within.sort(key=lambda t: (t[1], t[0]), reverse=True)
        pick_r, _leaf_match, pick_i = within[0]
        if pick_r < _MATCH_THRESHOLD:
            dropped.append(b)
            continue
        free.remove(pick_i)
        pairs.append((b, new[pick_i]))

    added = [new[i] for i in free]

    extra_pairs, dropped, added = _rematch(dropped, added)
    pairs.extend(extra_pairs)

    new_index = {id(n): k for k, n in enumerate(new)}
    pairs.sort(key=lambda pn: new_index[id(pn[1])])
    return pairs, dropped, added


# --------------------------------------------------------------------------- #
# Task 2: structural analysis — moves, ranks, section changes
# --------------------------------------------------------------------------- #

_MOVE_CAP = 4


@dataclass
class Annotations:
    heading_notes: dict[int, str] = field(default_factory=dict)
    block_moves: dict[int, str] = field(default_factory=dict)
    block_ranks: dict[int, str] = field(default_factory=dict)
    list_reordered: set[int] = field(default_factory=set)
    renames: list[tuple[str, str]] = field(default_factory=list)
    matched_base_headings: set[int] = field(default_factory=set)
    restructured: bool = False
    sections_reordered: int = 0
    blocks_moved: int = 0
    lists_reordered: int = 0


def _lis_indices(seq: list[int]) -> set[int]:
    """Indices of one longest strictly-increasing subsequence (patience sorting).
    Ties resolve toward the smaller values, so of a reordered run the items that
    jumped *up* end up as the odd-ones-out rather than the ones they displaced."""
    n = len(seq)
    if n == 0:
        return set()
    piles: list[int] = []       # piles[k] = seq-index of the current top of pile k
    pile_vals: list[int] = []
    prev = [-1] * n
    for i, v in enumerate(seq):
        pos = bisect.bisect_left(pile_vals, v)
        if pos == len(piles):
            piles.append(i)
            pile_vals.append(v)
        else:
            piles[pos] = i
            pile_vals[pos] = v
        prev[i] = piles[pos - 1] if pos > 0 else -1
    out: set[int] = set()
    k = piles[-1]
    while k != -1:
        out.add(k)
        k = prev[k]
    return out


def _short(s: str, limit: int = 60) -> str:
    s = " ".join(s.split())
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


_UP, _DOWN, _BOTH = "&#8593;", "&#8595;", "&#8645;"


def _dir_class(base: str, arrow: str) -> str:
    if arrow == _UP:
        return base + " cvd-up"
    if arrow == _DOWN:
        return base + " cvd-down"
    return base


def _tip_attr(tip: str) -> str:
    # newlines -> &#10; : a literal newline inside the tag breaks its markdown line
    return html.escape(tip, quote=True).replace("\n", "&#10;")


def _move_badge(arrow: str, tip: str) -> str:
    """Inline arrow-only badge for a bullet — direction tints it (up blue,
    down brown), the intent rides in the hover title."""
    return f'<span class="{_dir_class("cvd-move", arrow)}" title="{_tip_attr(tip)}">{arrow}</span>'


def _hnote(arrow: str, label: str, tip: str) -> str:
    """Block-level move note that sits on its own line under a heading — inline
    HTML inside an ATX heading gets escaped by the renderer, so it can't ride on
    the ``##`` line itself."""
    return (
        f'<div class="{_dir_class("cvd-hnote", arrow)}" title="{_tip_attr(tip)}">'
        f"{arrow} {_esc(label)}</div>"
    )


def _reorder_arrows(
    items: list, base_pos, name, *, kind: str,
) -> tuple[dict[int, tuple[str, str]], bool, int]:
    """`items` are the group's members in NEW order. ``base_pos(x)`` gives an
    item's base rank; ``name(x)`` its display text. ``kind`` is ``"section"``
    (tip names the siblings it moved past) or ``"line"`` (tip just says how far).

    Returns ``(movers, collapsed, n_movers)`` where ``movers`` maps an item
    index to ``(arrow, tip)`` — the caller decides the markup. Only genuine
    movers (outside the longest kept run, and actually at a new ordinal) are
    listed. ``collapsed`` is True when a strict majority moved.
    """
    n = len(items)
    if n < 2:
        return {}, False, 0
    seq = [base_pos(x) for x in items]
    base_order = sorted(range(n), key=lambda k: seq[k])  # item indices, base order
    rank_in_base = {k: r for r, k in enumerate(base_order)}
    stay = _lis_indices(seq)
    # a genuine mover is outside the kept run *and* actually at a different
    # ordinal now — LIS can route around an item that never really moved.
    movers = [i for i in range(n) if i not in stay and i != rank_in_base[i]]
    if not movers:
        return {}, False, 0
    if len(movers) * 2 > n:
        return {}, True, len(movers)
    out: dict[int, tuple[str, str]] = {}
    for i in movers:
        nxt = next((items[j] for j in range(i + 1, n) if j in stay), None)
        prv = next((items[j] for j in range(i - 1, -1, -1) if j in stay), None)
        moved_up = nxt is not None and base_pos(nxt) < seq[i]
        arrow = _UP if (moved_up or (nxt is not None and prv is None)) else _DOWN
        if kind == "line":
            delta = abs(i - rank_in_base[i])
            tip = f"{'up' if arrow == _UP else 'down'} {delta} position{'s' if delta != 1 else ''}"
        else:
            br = rank_in_base[i]
            if arrow == _UP:
                tip = _reorder_tip("up", items[base_order[br - 1]] if br > 0 else None, nxt, name)
            else:
                tip = _reorder_tip(
                    "down", items[base_order[br + 1]] if br + 1 < n else None, prv, name
                )
        out[i] = (arrow, tip)
    return out, False, len(movers)


def _reorder_tip(direction: str, base_anchor, new_anchor, name) -> str:
    """A tiny 1-3 line tooltip: which sibling it left, which it now sits by."""
    up = direction == "up"
    lines = ["UP" if up else "DOWN"]
    if base_anchor is not None:
        lines.append(f'from after "{_short(name(base_anchor))}"' if up
                     else f'from before "{_short(name(base_anchor))}"')
    else:
        lines.append("from the top" if up else "from the end")
    if new_anchor is not None:
        lines.append(f'to before "{_short(name(new_anchor))}"' if up
                     else f'to after "{_short(name(new_anchor))}"')
    else:
        lines.append("to the top" if up else "to the end")
    return "\n".join(lines)


def _match_headings(
    base_headings: list[Heading], new_headings: list[Heading],
) -> list[tuple[int, int]]:
    """(base_index, new_index) for every new heading that pairs with a base
    heading — exact path first, then best leaf-ratio >= 0.6. Each base heading is
    claimed at most once."""
    used_base: set[int] = set()
    out: list[tuple[int, int]] = []
    for ni, nh in enumerate(new_headings):
        exact = next(
            (bi for bi, bh in enumerate(base_headings)
             if bi not in used_base and bh.path == nh.path),
            None,
        )
        if exact is not None:
            used_base.add(exact)
            out.append((exact, ni))
            continue
        best_bi, best_r = None, 0.0
        nleaf = _norm(nh.path[-1] if nh.path else "")
        for bi, bh in enumerate(base_headings):
            if bi in used_base:
                continue
            r = difflib.SequenceMatcher(
                None, _norm(bh.path[-1] if bh.path else ""), nleaf
            ).ratio()
            if r > best_r:
                best_bi, best_r = bi, r
        if best_bi is not None and best_r >= _MATCH_THRESHOLD:
            used_base.add(best_bi)
            out.append((best_bi, ni))
    return out


def analyse_structure(
    base_blocks: list[Block],
    base_headings: list[Heading],
    new_blocks: list[Block],
    new_headings: list[Heading],
    pairs: list[tuple[Block, Block]],
) -> Annotations:
    ann = Annotations()
    _REORDERED = f'<div class="cvd-hnote" title="sibling sections reordered">{_BOTH} reordered</div>'

    # 1 + 2. Section reorders and renames. Headings are grouped by their siblings
    # (same level + parent); within each group only the genuine movers get an
    # arrow, and the arrow's `title` says "moved before/after X" — a relative,
    # intentional description rather than an absolute index. A group where at
    # least half moved collapses to one "reversed" mark on its first heading.
    matched = _match_headings(base_headings, new_headings)
    b_of_n = {ni: bi for bi, ni in matched}
    ann.matched_base_headings = {bi for bi, _ni in matched}
    moved_new_paths: set[tuple[str, ...]] = set()
    rode_base_path: dict[tuple[str, ...], tuple[str, ...]] = {}
    moved_headings = 0

    # A heading only takes part in its sibling group's reorder analysis when its
    # base match sat in the *same* group (same level + parent). One that changed
    # level or parent is a re-nesting — handled separately below — and would
    # otherwise inject a bogus base index into the group's ordering.
    sib_groups: dict[tuple[int, tuple[str, ...]], list[int]] = {}
    for ni, nh in enumerate(new_headings):
        bi = b_of_n.get(ni)
        if bi is None:
            continue
        bh = base_headings[bi]
        if bh.level == nh.level and bh.path[:-1] == nh.path[:-1]:
            sib_groups.setdefault((nh.level, nh.path[:-1]), []).append(ni)

    for nis in sib_groups.values():
        hs = [new_headings[ni] for ni in nis]
        bpos = {id(new_headings[ni]): b_of_n[ni] for ni in nis}
        badges, collapsed, n_movers = _reorder_arrows(
            hs,
            base_pos=lambda h: bpos[id(h)],
            name=lambda h: h.path[-1] if h.path else "",
            kind="section",
        )
        if collapsed:
            ann.heading_notes[hs[0].line_no] = _REORDERED
            ann.sections_reordered += n_movers
            moved_headings += n_movers
            continue
        for i, (arrow, tip) in badges.items():
            label = "moved up" if arrow == _UP else "moved down"
            ann.heading_notes[hs[i].line_no] = _hnote(arrow, label, tip)
            ann.sections_reordered += 1
            moved_headings += 1

    # renames + re-nesting (heading matched, but leaf text or parent changed)
    for ni, nh in enumerate(new_headings):
        bi = b_of_n.get(ni)
        if bi is None or nh.line_no in ann.heading_notes:
            continue
        bh = base_headings[bi]
        if bh.path[:-1] != nh.path[:-1]:
            was_under = bh.path[-2] if len(bh.path) > 1 else None
            now_under = nh.path[-2] if len(nh.path) > 1 else None
            if was_under and not now_under:
                tip = f'promoted out of "{_short(was_under)}"'
            elif now_under and not was_under:
                tip = f'moved under "{_short(now_under)}"'
            elif was_under and now_under:
                tip = f'moved from "{_short(was_under)}" to "{_short(now_under)}"'
            else:
                tip = "moved to a different section"
            ann.heading_notes[nh.line_no] = _hnote("&#8597;", "moved section", tip)
            ann.sections_reordered += 1
            moved_headings += 1
            moved_new_paths.add(nh.path)
            rode_base_path[nh.path] = bh.path
        elif nh.path and bh.path and nh.path[-1] != bh.path[-1]:
            # matched heading with changed text — show the correction inline as
            # a redline span appended to the heading.
            old, new = bh.path[-1], nh.path[-1]
            ann.heading_notes[nh.line_no] = (
                f' <span class="cvd-hnote-edit" title="was &quot;{_esc(old)}&quot;">'
                f"{redline_terse(old, new)}</span>"
            )
            ann.renames.append((old, new))
            ann.sections_reordered += 1

    base_idx = {id(b): k for k, b in enumerate(base_blocks)}
    new_idx = {id(n): k for k, n in enumerate(new_blocks)}
    bb_blocks = max(len(base_blocks), 1)
    nn_blocks = max(len(new_blocks), 1)

    # 3 + 4. Rode-along suppression, then container changes.
    list_groups: dict[int, list[tuple[Block, Block]]] = {}
    for b, n in pairs:
        if n.section_path in moved_new_paths and b.section_path == rode_base_path.get(n.section_path):
            continue  # rode along with a re-nested section — no per-block badge
        b_leaf = b.section_path[-1] if b.section_path else ""
        n_leaf = n.section_path[-1] if n.section_path else ""
        if b_leaf != n_leaf:
            earlier = (new_idx[id(n)] / nn_blocks) < (base_idx[id(b)] / bb_blocks)
            ann.block_moves[n.line_no] = _move_badge(
                _UP if earlier else _DOWN, f'moved here from "{b_leaf}"'
            )
            ann.blocks_moved += 1
            continue
        list_groups.setdefault(n.list_id, []).append((b, n))

    # 5. Within-list reorder — same arrow + intent as headings.
    for group in list_groups.values():
        group.sort(key=lambda pn: pn[1].rank)
        badges, collapsed, _n = _reorder_arrows(
            group,
            base_pos=lambda pn: pn[0].rank,
            name=lambda pn: pn[1].text,
            kind="line",
        )
        if collapsed:
            ann.list_reordered.add(group[0][1].line_no)
            ann.lists_reordered += 1
        elif badges:
            ann.lists_reordered += 1
            for i, (arrow, tip) in badges.items():
                ann.block_ranks[group[i][1].line_no] = _move_badge(arrow, tip)

    # 6. Global move cap.
    if ann.blocks_moved + moved_headings > _MOVE_CAP:
        ann.restructured = True
        ann.heading_notes = {}
        ann.block_moves = {}
        ann.renames = []
        ann.sections_reordered = 0
        ann.blocks_moved = 0

    return ann


def classify(before: str, after: str) -> str:
    """One of ``identical`` / ``formatting`` / ``typo`` / ``reword`` / ``rewrite``."""
    nb, na = _norm(before), _norm(after)
    if nb == na:
        if _norm(before, keep_fmt=True) == _norm(after, keep_fmt=True):
            return "identical"
        return "formatting"
    char_ratio = difflib.SequenceMatcher(None, nb, na).ratio()
    if char_ratio >= 0.9:
        return "typo"
    if _word_ratio(before, after) >= 0.5:
        return "reword"
    return "rewrite"


_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_MD_EMPH_RE = re.compile(r"\*\*|__|(?<!\w)[*_](?=\S)|(?<=\S)[*_](?!\w)|`|~~")


def _plain(s: str) -> str:
    """Strip markdown emphasis / link syntax so a diff fragment renders as prose
    — inline ``**``/``*``/`` ` `` inside a woven ``<del>``/``<ins>`` would other-
    wise be re-parsed by the renderer and produce mismatched tags."""
    return _MD_EMPH_RE.sub("", _MD_LINK_RE.sub(r"\1", s))


def _esc(s: str) -> str:
    return html.escape(_plain(s), quote=False)


def redline(before: str, after: str) -> str:
    """Inline word-level redline: ``<del class="cvd-del">`` / ``<ins class="cvd-ins">``."""
    btok = _TOK_RE.findall(_plain(before))
    atok = _TOK_RE.findall(_plain(after))
    out: list[str] = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, btok, atok).get_opcodes():
        if op == "equal":
            out.append(_esc("".join(atok[j1:j2])))
        elif op == "delete":
            out.append(f'<del class="cvd-del">{_esc("".join(btok[i1:i2]).rstrip())}</del> ')
        elif op == "insert":
            out.append(f'<ins class="cvd-ins">{_esc("".join(atok[j1:j2]).rstrip())}</ins> ')
        elif op == "replace":
            out.append(f'<del class="cvd-del">{_esc("".join(btok[i1:i2]).rstrip())}</del> ')
            out.append(f'<ins class="cvd-ins">{_esc("".join(atok[j1:j2]).rstrip())}</ins> ')
    s = re.sub(r"[ \t]{2,}", " ", "".join(out))
    return s.strip()


def redline_terse(before: str, after: str, ctx: int = 2) -> str:
    """Redline showing only the changed spans — leading/trailing unchanged words
    are dropped, interior unchanged runs longer than ``ctx`` collapse to ``…``.
    For a heading correction, where the corrected text is already shown above."""
    btok = _TOK_RE.findall(_plain(before))
    atok = _TOK_RE.findall(_plain(after))
    ops = difflib.SequenceMatcher(None, btok, atok).get_opcodes()
    changed = [k for k, o in enumerate(ops) if o[0] != "equal"]
    if not changed:
        return _esc(after)
    out: list[str] = []
    for k, (op, i1, i2, j1, j2) in enumerate(ops):
        if op == "equal":
            words = atok[j1:j2]
            lead = k < changed[0]
            trail = k > changed[-1]
            if lead or trail:
                continue
            if len(words) > ctx:
                out.append("… ")
            else:
                out.append(_esc("".join(words)))
            continue
        if op in ("delete", "replace"):
            out.append(f'<del class="cvd-del">{_esc("".join(btok[i1:i2]).rstrip())}</del> ')
        if op in ("insert", "replace"):
            out.append(f'<ins class="cvd-ins">{_esc("".join(atok[j1:j2]).rstrip())}</ins> ')
    return re.sub(r"[ \t]{2,}", " ", "".join(out)).strip()


# --------------------------------------------------------------------------- #
# Task 3: assembly — annotated markdown, digest, public build()
# --------------------------------------------------------------------------- #

@dataclass
class DiffResult:
    annotated_markdown: str
    digest: dict
    summary_line: str
    added_items: list[tuple[str, str]]
    is_empty: bool


def _line_marker(raw: str, kind: str) -> tuple[str, str, str]:
    """(indent, marker, body-text) for a source line. The marker — a bullet
    ``- ``/``* `` or a blockquote ``> `` — is carried through so it can be
    re-prepended when the line is rewritten (otherwise a touched quote line
    loses its ``>`` and renders as a bare paragraph)."""
    stripped = raw.lstrip()
    indent = raw[: len(raw) - len(stripped)]
    if kind == "bullet" and stripped[:2] in ("- ", "* "):
        return indent, stripped[:2], stripped[2:].strip()
    if kind == "quote":
        marker, body = _quote_prefix(stripped)
        if marker:
            return indent, marker, body.strip()
    return indent, "", stripped


def _struck(text: str, kind: str) -> str:
    body = f'<del class="cvd-del cvd-block">{_esc(text)}</del>'
    if kind == "bullet":
        return f"- {body}"
    if kind == "quote":
        return f"> {body}"
    return body


def _term(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def build(base_cv: str, tailored_cv: str) -> DiffResult:
    """Annotated markdown (+ digest, summary, added-content list) for the diff."""
    base_blocks, base_headings = parse_blocks(base_cv)
    new_blocks, new_headings = parse_blocks(tailored_cv)
    pairs, dropped, added = match_blocks(base_blocks, new_blocks)
    ann = analyse_structure(base_blocks, base_headings, new_blocks, new_headings, pairs)

    lines = tailored_cv.splitlines()
    inserts: list[tuple[int, int, str]] = []  # (pos, seq, text)
    seq_counter = 0
    reworded = 0

    paired_base_ln = {id(b): n.line_no for b, n in pairs}
    matched_base_h = ann.matched_base_headings

    def badges_for(ln: int) -> str:
        bs: list[str] = []
        if ln in ann.block_moves:
            bs.append(ann.block_moves[ln])
        if ln in ann.block_ranks:
            bs.append(ann.block_ranks[ln])
        if ln in ann.list_reordered:
            bs.append(f'<span class="cvd-move" title="list reordered">{_BOTH}</span>')
        return "".join(bs)

    # Matched pairs — content redline / block swap, plus structural badges.
    for b, n in pairs:
        cls = classify(b.text, n.text)
        indent, marker, orig = _line_marker(lines[n.line_no], n.kind)
        badge = badges_for(n.line_no)
        if cls in ("identical", "formatting"):
            if not badge:
                continue
            body = orig
        elif cls in ("typo", "reword"):
            body = redline(b.text, n.text)
            reworded += 1
        else:  # rewrite -> struck original + fresh block
            reworded += 1
            seq_counter += 1
            inserts.append((n.line_no, seq_counter, _struck(b.text, b.kind)))
            body = f'<ins class="cvd-ins cvd-block">{_esc(n.text)}</ins>'
        line = f"{indent}{marker}{body}"
        lines[n.line_no] = f"{line} {badge}" if badge else line

    # Heading notes. An inline correction <span> rides on the heading line
    # itself; a block-level move/reorder <div> can't (inline HTML mid-ATX-heading
    # gets escaped by the renderer) so it's woven in on its own line right under.
    for ln, note in ann.heading_notes.items():
        if note.lstrip().startswith("<div"):
            seq_counter += 1
            inserts.append((ln + 1, seq_counter, note))
        else:
            lines[ln] = lines[ln] + note

    # Added blocks — higher-attention ground + "new" marker.
    added_items: list[tuple[str, str]] = []
    for a in added:
        indent, marker, _orig = _line_marker(lines[a.line_no], a.kind)
        body = (f'<ins class="cvd-ins cvd-block cvd-added">{_esc(a.text)}</ins> '
                f'<span class="cvd-new">new</span>')
        lines[a.line_no] = f"{indent}{marker}{body}"
        added_items.append((a.section_path[-1] if a.section_path else "", a.text))

    dropped_set = {id(d) for d in dropped}

    def _weave_pos(base_line: int) -> int:
        """Insert position for something that sat at base_line: right after the
        nearest surviving base block before it, else right before the nearest
        surviving one after it, else the end."""
        before = [b for b in base_blocks if b.line_no < base_line and id(b) in paired_base_ln]
        after = [b for b in base_blocks if b.line_no > base_line and id(b) in paired_base_ln]
        if before:
            return paired_base_ln[id(before[-1])] + 1
        if after:
            return paired_base_ln[id(after[0])]
        return len(lines)

    # Whole removed sections: dropped heading whose every child block is also
    # dropped. Rendered as one unit — struck heading in the heading font + a
    # folded block of its removed lines — so the heading keeps its place above
    # its content instead of the two weaving in independently.
    consumed: set[int] = set()
    dropped_headings = 0
    removed_sections = 0
    for bi, bh in enumerate(base_headings):
        if bi in matched_base_h:
            continue
        kids = [b for b in base_blocks if b.section_path == bh.path]
        if kids and not all(id(b) in dropped_set for b in kids):
            # section partially survived — just strike the heading in place
            dropped_headings += 1
            leaf = bh.path[-1] if bh.path else ""
            seq_counter += 1
            inserts.append((_weave_pos(bh.line_no), seq_counter,
                            f'<div class="cvd-gone-h cvd-gone-h{bh.level}">'
                            f'<del>{_esc(leaf)}</del></div>'))
            continue
        removed_sections += 1
        for b in kids:
            consumed.add(id(b))
        body = "".join(
            f'<div class="cvd-gone-line"><del>{_esc(b.text)}</del></div>' for b in kids
        )
        n = len(kids)
        fold = (f'<details class="cvd-gone-d"><summary>{n} line{"s" if n != 1 else ""} '
                f'removed</summary>{body}</details>') if body else ""
        leaf = bh.path[-1] if bh.path else ""
        seq_counter += 1
        inserts.append((_weave_pos(bh.line_no), seq_counter,
                        f'<div class="cvd-gone"><div class="cvd-gone-h cvd-gone-h{bh.level}">'
                        f'<del>{_esc(leaf)}</del></div>{fold}</div>'))

    # Remaining dropped blocks — woven in at their nearest surviving neighbour.
    for d in dropped:
        if id(d) in consumed:
            continue
        seq_counter += 1
        inserts.append((_weave_pos(d.line_no), seq_counter, _struck(d.text, d.kind)))

    # Blank lines around every insert: block-level HTML (<div>, <details>, a
    # standalone struck block) only renders raw when it's blank-line-delimited —
    # doc-write is lenient, python-markdown (the no-doc-write fallback) is not.
    # A struck *bullet* landing next to a surviving list item is the exception:
    # blank lines there would make the whole list "loose" (every sibling <li>
    # gains a <p> wrapper and its spacing), so it joins the list tightly instead.
    def _is_bullet(s: str) -> bool:
        return s.lstrip()[:2] in ("- ", "* ")

    for pos, _s, text in sorted(inserts, key=lambda t: (t[0], t[1]), reverse=True):
        at = max(0, min(pos, len(lines)))
        tight = _is_bullet(text) and (
            (at < len(lines) and _is_bullet(lines[at]))
            or (at > 0 and _is_bullet(lines[at - 1]))
        )
        lines.insert(at, text if tight else f"\n{text}\n")

    dropped_loose = len([d for d in dropped if id(d) not in consumed]) + dropped_headings
    digest = {
        "sections_reordered": ann.sections_reordered,
        "blocks_moved": ann.blocks_moved,
        "lists_reordered": ann.lists_reordered,
        "sections_removed": removed_sections,
        "dropped": dropped_loose,
        "reworded": reworded,
        "added": len(added),
        "renamed_sections": ann.renames,
        "restructured": ann.restructured,
        "added_items": added_items,
    }

    terms: list[str] = []
    if not ann.restructured:
        # cleared by the move cap; the "substantially reordered" line stands in
        if ann.sections_reordered:
            terms.append(_term(ann.sections_reordered, "section reordered", "sections reordered"))
        if ann.blocks_moved:
            terms.append(_term(ann.blocks_moved, "block moved", "blocks moved"))
    # kept even when restructured — the per-bullet ↑/↓ markers still render
    if ann.lists_reordered:
        terms.append(_term(ann.lists_reordered, "list reordered", "lists reordered"))
    if removed_sections:
        terms.append(_term(removed_sections, "section removed", "sections removed"))
    if dropped_loose:
        terms.append(_term(dropped_loose, "bullet dropped", "bullets dropped"))
    if reworded:
        terms.append(f"{reworded} reworded")
    if added:
        terms.append(_term(len(added), "new paragraph added", "new paragraphs added"))

    if ann.restructured:
        summary_line = " · ".join(["The document was substantially reordered", *terms])
        is_empty = False
    elif terms:
        summary_line = " · ".join(terms)
        is_empty = False
    else:
        summary_line = "No changes from your base CV."
        is_empty = True

    # The document only — the summary line and the added-content list are surfaced
    # in the workbench pane above the tabs (see _cv_diff_summary.html), not baked
    # into the rendered doc, so they show whichever preview tab is active.
    annotated = "\n".join(lines)

    return DiffResult(annotated, digest, summary_line, added_items, is_empty)
