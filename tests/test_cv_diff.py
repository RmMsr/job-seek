from app.cv.cv_diff import (
    analyse_structure, classify, match_blocks, parse_blocks, redline,
)


def _prep(base_md, new_md):
    bb, bh = parse_blocks(base_md)
    nb, nh = parse_blocks(new_md)
    pairs, dropped, added = match_blocks(bb, nb)
    return bb, bh, nb, nh, pairs

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


def test_parse_blocks_skips_content_free_lines():
    # a stray "#" / "***" / bare "-" the LLM sometimes trails carries nothing
    blocks, headings = parse_blocks("## Real\n\n- kept bullet\n\n#\n\n***\n")
    assert [b.text for b in blocks] == ["kept bullet"]
    assert [h.path for h in headings] == [("Real",)]


def test_build_ignores_trailing_stray_hash():
    from app.cv.cv_diff import build
    base = "# CV\n\n## Skills\n\n- Python\n"
    new = "# CV\n\n## Skills\n\n- Python\n\n#\n"
    r = build(base, new)
    assert r.is_empty
    assert not r.added_items
    assert "cvd-added" not in r.annotated_markdown


def test_parse_blocks_paragraph_is_its_own_list():
    blocks, _ = parse_blocks("## S\n\nA paragraph line.\n\n- a\n- b\n")
    assert blocks[0].kind == "para"
    assert blocks[0].list_id != blocks[1].list_id


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


def test_section_reorder_annotates_the_mover_relative_to_a_sibling():
    base = "## Summary\n\ntext\n\n## Experience\n\n- x work\n\n## Skills\n\n- y skill\n"
    new = "## Skills\n\n- y skill\n\n## Summary\n\ntext\n\n## Experience\n\n- x work\n"
    bb, bh, nb, nh, pairs = _prep(base, new)
    a = analyse_structure(bb, bh, nb, nh, pairs)
    skills_h = next(h for h in nh if h.path == ("Skills",))
    exp_h = next(h for h in nh if h.path == ("Experience",))
    badge = a.heading_notes[skills_h.line_no]
    assert badge.startswith("<div class=") and "cvd-hnote cvd-up" in badge  # own-line block
    assert ">&#8593; moved up</div>" in badge                              # visible label
    assert "Summary" in badge and "&#10;" in badge                         # multi-line tip
    assert "from #" not in badge and " #2" not in badge                    # no index number
    assert exp_h.line_no not in a.heading_notes  # Experience only shifted -> unbadged
    assert not a.block_moves


def test_section_majority_reshuffle_collapses_to_one_mark():
    base = "".join(f"## S{i}\n\ntext {i}\n\n" for i in range(4))
    new = "".join(f"## S{i}\n\ntext {i}\n\n" for i in (3, 2, 1, 0))
    bb, bh, nb, nh, pairs = _prep(base, new)
    a = analyse_structure(bb, bh, nb, nh, pairs)
    marks = [v for v in a.heading_notes.values()]
    assert len(marks) == 1 and "&#8645;" in marks[0]  # one "reordered" mark


def test_within_list_reorder_sets_rank_badges():
    base = "## S\n\n- first alpha\n- second beta\n- third gamma\n- fourth delta\n"
    new = "## S\n\n- third gamma\n- first alpha\n- second beta\n- fourth delta\n"
    bb, bh, nb, nh, pairs = _prep(base, new)
    a = analyse_structure(bb, bh, nb, nh, pairs)
    gamma = next(n for n in nb if n.text == "third gamma")
    badge = a.block_ranks[gamma.line_no]
    assert badge.endswith(">&#8593;</span>") and "cvd-up" in badge   # arrow only, up = blue
    assert 'title="up 2 positions"' in badge   # line items: just how far it moved
    assert a.lists_reordered >= 1


def test_majority_reshuffle_collapses_to_list_reordered():
    base = "## S\n\n- a one\n- b two\n- c three\n- d four\n- e five\n"
    new = "## S\n\n- e five\n- d four\n- c three\n- b two\n- a one\n"  # reversed
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
    badge = a.block_moves[moved.line_no]
    assert "moved here from &quot;Experience&quot;" in badge and "cvd-move" in badge


def test_whole_removed_section_is_one_unit_heading_before_content():
    from app.cv.cv_diff import build
    base = ("# CV\n\n## Keep\n\n- kept bullet\n\n## Gone\n\n"
            "A removed paragraph.\n\n- removed bullet one\n- removed bullet two\n")
    new = "# CV\n\n## Keep\n\n- kept bullet\n"
    r = build(base, new)
    am = r.annotated_markdown
    assert '<div class="cvd-gone">' in am
    assert '<div class="cvd-gone-h cvd-gone-h2"><del>Gone</del></div>' in am
    assert "<details" in am and "3 lines removed" in am
    # the struck heading sits before its removed content, not after
    gi = am.index('<del>Gone</del>')
    assert gi < am.index("A removed paragraph.")
    assert r.digest["sections_removed"] == 1
    assert "1 section removed" in r.summary_line
    # those lines are folded into the unit, not also woven individually
    assert am.count('cvd-del cvd-block">A removed paragraph') == 0


def test_partially_removed_section_strikes_only_the_heading_in_place():
    from app.cv.cv_diff import build
    base = "# CV\n\n## Trim\n\n- keep this bullet\n- drop this bullet\n"
    new = "# CV\n\n## Trim\n\n- keep this bullet\n"
    r = build(base, new)
    assert '<div class="cvd-gone">' not in r.annotated_markdown  # heading survives
    assert '<del class="cvd-del cvd-block">drop this bullet</del>' in r.annotated_markdown


def test_heading_typo_is_shown_as_an_inline_redline():
    from app.cv.cv_diff import analyse_structure
    base = "## Summary\n\ntext\n\n### Senior Core Team Engier\n\n- did work\n"
    new = "## Summary\n\ntext\n\n### Senior Core Team Engineer\n\n- did work\n"
    bb, bh, nb, nh, pairs = _prep(base, new)
    a = analyse_structure(bb, bh, nb, nh, pairs)
    fixed = next(h for h in nh if h.path[-1] == "Senior Core Team Engineer")
    note = a.heading_notes[fixed.line_no]
    assert "<span class=\"cvd-hnote-edit\"" in note
    assert '<del class="cvd-del">Engier</del>' in note
    assert '<ins class="cvd-ins">Engineer</ins>' in note
    body = note.split(">", 2)[2]  # skip the <span ... title="...">
    assert "Senior Core Team" not in body  # unchanged prefix dropped, only the fix shown


def test_parse_blocks_strips_blockquote_marker_from_text():
    blocks, _ = parse_blocks("## T\n\n> She ships fast. — a manager\n")
    assert len(blocks) == 1
    assert blocks[0].kind == "quote"
    assert blocks[0].text == "She ships fast. — a manager"  # no leading ">"


def test_reworded_blockquote_line_keeps_its_marker():
    from app.cv.cv_diff import build
    base = "# CV\n\n## T\n\n> She ships fast. — a manager\n\n- b\n"
    new = "# CV\n\n## T\n\n> She ships remarkably fast. — a manager\n\n- b\n"
    r = build(base, new)
    line = next(ln for ln in r.annotated_markdown.splitlines() if "ships" in ln)
    assert line.startswith("> ")                       # still a blockquote line
    assert '<ins class="cvd-ins">remarkably</ins>' in line
    assert "&gt;" not in line


def test_section_move_note_gets_its_own_line_not_the_heading_line():
    from app.cv.cv_diff import build
    base = "# CV\n\n## Skills\n\n- a\n\n## Experience\n\n- x\n\n## Education\n\n- y\n"
    new = "# CV\n\n## Experience\n\n- x\n\n## Skills\n\n- a\n\n## Education\n\n- y\n"
    r = build(base, new)
    lines = r.annotated_markdown.splitlines()
    # the block-level move <div> must not ride on the ATX heading line (the
    # renderer escapes inline HTML mid-heading -> it shows as raw markup)
    heading = next(ln for ln in lines if ln.startswith("## Experience"))
    assert heading == "## Experience"
    assert '<div class="cvd-hnote cvd-up"' in r.annotated_markdown
    div_line = next(ln for ln in lines if ln.startswith('<div class="cvd-hnote'))
    assert "&#8593; moved up" in div_line


def test_global_move_cap_sets_restructured():
    secs = [f"## S{i}\n\n- item {i}\n" for i in range(6)]
    base = "\n".join(secs)
    new = "\n".join(reversed(secs))
    bb, bh, nb, nh, pairs = _prep(base, new)
    a = analyse_structure(bb, bh, nb, nh, pairs)
    assert a.restructured
    assert not a.heading_notes and not a.block_moves


BASE = """# Jane Doe

## Experience

- Managed teh ingestion cluster.
- Ran the payments migration.
- Legacy Perl maintenance.

## Skills

- Python, Go, Rust
"""


def test_dropped_block_struck_in_place():
    from app.cv.cv_diff import build
    new = BASE.replace("- Legacy Perl maintenance.\n", "")
    r = build(BASE, new)
    assert '<del class="cvd-del cvd-block">Legacy Perl maintenance.</del>' in r.annotated_markdown
    assert r.digest["dropped"] == 1


def test_struck_bullet_joins_the_list_tightly_no_blank_lines():
    from app.cv.cv_diff import build
    base = "# CV\n\n## Roles\n\n- Core Team Backend\n- Infra: DevOps, SRE\n- Architect\n"
    new = "# CV\n\n## Roles\n\n- Core Team Backend\n- Architect\n"
    r = build(base, new)
    lines = r.annotated_markdown.splitlines()
    i = next(k for k, ln in enumerate(lines) if "Infra: DevOps, SRE" in ln)
    # struck bullet sits directly between its siblings — no blank line either
    # side, or markdown makes the whole list loose (<p>-wrapped <li>s)
    assert lines[i].startswith('- <del class="cvd-del cvd-block">')
    assert lines[i - 1] == "- Core Team Backend"
    assert lines[i + 1] == "- Architect"


def test_typo_shows_redline_even_when_bullet_reordered():
    from app.cv.cv_diff import build
    # "Managed …" both moves (to the end of the list) and gets its typo fixed
    new = """# Jane Doe

## Experience

- Ran the payments migration.
- Legacy Perl maintenance.
- Managed the ingestion cluster.

## Skills

- Python, Go, Rust
"""
    r = build(BASE, new)
    managed_line = next(
        ln for ln in r.annotated_markdown.splitlines() if "ingestion cluster" in ln
    )
    assert '<del class="cvd-del">teh</del>' in managed_line
    assert '<ins class="cvd-ins">the</ins>' in managed_line
    assert "cvd-move" in managed_line  # the move arrow rides alongside the redline
    assert r.digest["reworded"] == 1


def test_added_block_higher_attention_and_listed():
    from app.cv.cv_diff import build
    new = BASE.replace("## Experience\n", "## Summary\n\nPassionate about payments infra.\n\n## Experience\n")
    r = build(BASE, new)
    assert "cvd-added" in r.annotated_markdown
    assert '<span class="cvd-new">new</span>' in r.annotated_markdown
    assert any("Passionate about payments infra." in t for _, t in r.added_items)
    # digest + added-content list are surfaced by the pane, not baked into the doc
    assert "cvd-callout" not in r.annotated_markdown
    assert "cvd-digest" not in r.annotated_markdown
    assert not r.annotated_markdown.lstrip().startswith("<p")


def test_heavy_rewrite_is_block_swap():
    from app.cv.cv_diff import build
    new = BASE.replace("- Ran the payments migration.",
                       "- Owned quarterly OKR planning for a nine-person platform group.")
    r = build(BASE, new)
    am = r.annotated_markdown
    assert '<del class="cvd-del cvd-block">Ran the payments migration.</del>' in am
    assert "Owned quarterly OKR planning" in am


def test_identical_is_empty():
    from app.cv.cv_diff import build
    r = build(BASE, BASE)
    assert r.is_empty
    assert r.summary_line == "No changes from your base CV."


def test_summary_line_omits_zero_terms():
    from app.cv.cv_diff import build
    new = BASE.replace("- Legacy Perl maintenance.\n", "")
    r = build(BASE, new)
    assert r.summary_line == "1 bullet dropped"
