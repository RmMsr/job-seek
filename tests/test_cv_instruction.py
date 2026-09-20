from app.ai.tailor_cv import DirectiveProposal
from app.cv.instruction import (
    DEFAULT_GUARDRAILS, DEFAULT_GUARDRAILS_RULES, DEFAULT_SCOPE_OPTIONS, compose_instruction,
    resolve_directive_proposals, apply_directive_proposals,
)

_SCOPE_OPTIONS = [
    {"id": 1, "name": "choose", "description": "Select bullets by relevance."},
    {"id": 2, "name": "organize", "description": "Reorder to foreground what matters."},
    {"id": 3, "name": "rephrase", "description": "Reword toward the job's terms."},
]


def test_compose_prefixes_scope_name():
    out = compose_instruction(base_instruction="", scope=[1, 2], scope_options=_SCOPE_OPTIONS,
                              guardrails="", tuning_directives="")
    assert "- **choose**: Select bullets by relevance." in out
    assert "- **organize**: Reorder to foreground what matters." in out


def test_compose_scope_without_name_has_no_prefix():
    opts = [{"id": 1, "description": "No name here."}]
    out = compose_instruction(base_instruction="", scope=[1], scope_options=opts,
                              guardrails="", tuning_directives="")
    assert "- No name here." in out
    assert "**" not in out.split("Permitted edits")[1].split("Hard limits")[0]


def test_default_scope_options_has_five_named_entries():
    assert [o["name"] for o in DEFAULT_SCOPE_OPTIONS] == [
        "correct", "choose", "organize", "rephrase", "introduce", "wildcard"]
    assert sum(o["default_enabled"] for o in DEFAULT_SCOPE_OPTIONS) == 3


def test_default_directives_template_is_headings_only():
    from app.cv.instruction import DEFAULT_DIRECTIVES_TEMPLATE
    lines = [l for l in DEFAULT_DIRECTIVES_TEMPLATE.splitlines() if l.strip()]
    assert len(lines) == 8
    assert all(l.startswith("## ") for l in lines)
    assert "## Role relevance" in lines
    assert "## Wording and typography" in lines
    # one blank line between each heading
    assert "\n\n## Skills match\n\n## Content hierarchy" in DEFAULT_DIRECTIVES_TEMPLATE


def test_compose_makes_mechanical_correction_non_negotiable():
    out = compose_instruction(
        base_instruction="", scope=[1], scope_options=_SCOPE_OPTIONS,
        guardrails="", tuning_directives="- foreground Kafka",
    )
    assert "no directive or priority can outweigh or skip it" in out


def test_compose_makes_scope_a_hard_boundary_over_directives():
    out = compose_instruction(
        base_instruction="", scope=[1, 2], scope_options=_SCOPE_OPTIONS,
        guardrails="", tuning_directives="- rewrite the opening paragraph",
    )
    low = out.lower()
    # scope is stated as the hard boundary, and directives are subordinate to it
    assert "hard boundary" in low
    assert "off-limits" in low and "tuning directive" in low
    assert "only as far as" in low  # directives pursued within permitted edits


def test_default_guardrails_lists_the_prohibitions():
    assert len(DEFAULT_GUARDRAILS_RULES) == 9
    for token in ("frontmatter", "<aside>", "degree", "employer", "job title",
                  "dates", "metric", "more specific", "cover letter"):
        assert token in DEFAULT_GUARDRAILS.lower()


def test_default_guardrails_text_built_from_rules_one_per_line():
    assert DEFAULT_GUARDRAILS.splitlines() == DEFAULT_GUARDRAILS_RULES


def test_default_base_cv_is_a_non_empty_document():
    from app.cv.instruction import DEFAULT_BASE_CV
    assert DEFAULT_BASE_CV.startswith("---")  # frontmatter
    assert '<aside class="sidebar"' in DEFAULT_BASE_CV
    assert len(DEFAULT_BASE_CV.splitlines()) > 20


def test_fresh_cv_settings_seed_the_default_base_cv(conn):
    from app.cv.instruction import DEFAULT_BASE_CV
    from app.db import queries as q

    base_cv_id = q.list_base_cvs(conn)[0]["id"]
    assert q.get_base_cv(conn, base_cv_id)["base_cv"] == DEFAULT_BASE_CV


def test_compose_orders_sections_and_includes_guardrails():
    out = compose_instruction(
        base_instruction="British English.",
        scope=[2, 1],  # unordered on purpose
        scope_options=_SCOPE_OPTIONS,
        guardrails="Keep it to two pages.",
        tuning_directives="- foreground platform work, keep mentoring line",
    )
    assert out.index("British English.") < out.index("Permitted edits")
    assert out.index("Permitted edits") < out.index("Hard limits — never break these:")
    assert out.index("Hard limits — never break these:") < out.index("Tuning directives")
    # canonical (scope_options) order regardless of input `scope` order
    assert out.index("Select bullets") < out.index("Reorder to foreground")
    assert "Keep it to two pages." in out
    assert "foreground platform work" in out


def test_compose_handles_empty_guardrails_and_directives():
    out = compose_instruction(
        base_instruction="", scope=[1], scope_options=_SCOPE_OPTIONS,
        guardrails="", tuning_directives="",
    )
    assert "Hard limits — never break these:\n(none)" in out
    assert "Tuning directives" in out and "(none)" in out


def test_compose_ignores_scope_ids_not_in_options():
    out = compose_instruction(
        base_instruction="", scope=[1, 999], scope_options=_SCOPE_OPTIONS,
        guardrails="", tuning_directives="",
    )
    assert "Select bullets" in out
    assert "999" not in out


def test_resolve_add_kept_when_not_duplicate():
    proposals = [DirectiveProposal(action="add", section="Skills match", rationale="r",
                                    line="foreground platform work", target=None)]
    resolved = resolve_directive_proposals(proposals, "- keep mentoring line")
    assert resolved == [{"action": "add", "section": "Skills match", "rationale": "r",
                          "line": "foreground platform work", "target": None}]


def test_resolve_add_duplicate_is_omitted():
    proposals = [DirectiveProposal(action="add", section="Skills match", rationale="r",
                                    line="foreground platform work", target=None)]
    resolved = resolve_directive_proposals(proposals, "- foreground platform work")
    assert resolved == []


def test_resolve_drops_add_that_near_repeats_a_handled_suggestion():
    proposals = [DirectiveProposal(action="add", section="Skills match", rationale="r",
                                    line="Name C++ prominently in the skills list", target=None)]
    handled = [{"action": "add", "line": "name C++ prominently in the skills list"}]
    assert resolve_directive_proposals(proposals, "", handled=handled) == []
    # a genuinely different proposal still gets through
    other = [DirectiveProposal(action="add", section="Skills match", rationale="r",
                                line="foreground the Kubernetes platform work", target=None)]
    assert len(resolve_directive_proposals(other, "", handled=handled)) == 1


def test_resolve_replace_matching_target_is_kept():
    proposals = [DirectiveProposal(action="replace", section="Role relevance", rationale="r",
                                    line="tighten the wording", target="mention platform work")]
    resolved = resolve_directive_proposals(proposals, "- mention platform work")
    assert resolved == [{"action": "replace", "section": "Role relevance", "rationale": "r",
                          "line": "tighten the wording", "target": "mention platform work"}]


def test_resolve_replace_unmatched_target_is_omitted():
    proposals = [DirectiveProposal(action="replace", section="Role relevance", rationale="r",
                                    line="x", target="nonexistent")]
    assert resolve_directive_proposals(proposals, "- mention platform work") == []


def test_resolve_remove_matching_target_is_kept():
    proposals = [DirectiveProposal(action="remove", section="Wording and typography", rationale="r",
                                    line=None, target="drop the essay section")]
    resolved = resolve_directive_proposals(proposals, "- drop the essay section")
    assert resolved == [{"action": "remove", "section": "Wording and typography", "rationale": "r",
                          "line": None, "target": "drop the essay section"}]


def test_resolve_remove_unmatched_target_is_omitted():
    proposals = [DirectiveProposal(action="remove", section="Wording and typography", rationale="r",
                                    line=None, target="nonexistent")]
    assert resolve_directive_proposals(proposals, "- keep this") == []


def test_apply_add_inserts_under_named_section():
    resolved = [{"action": "add", "section": "Skills match", "rationale": "r",
                 "line": "name Kubernetes explicitly", "target": None}]
    out = apply_directive_proposals("## Role relevance\n- lead with platform\n\n## Skills match", resolved)
    assert out.splitlines() == [
        "## Role relevance", "- lead with platform", "", "## Skills match",
        "", "- name Kubernetes explicitly"]


def test_apply_add_with_unknown_section_appends_heading():
    resolved = [{"action": "add", "section": "New area", "rationale": "r",
                 "line": "do the thing", "target": None}]
    out = apply_directive_proposals("## Role relevance\n- x", resolved)
    assert out.endswith("## New area\n\n- do the thing")


def test_apply_add_to_empty_directives():
    resolved = [{"action": "add", "section": "Skills match", "rationale": "r",
                 "line": "foreground platform work", "target": None}]
    result = apply_directive_proposals("", resolved)
    assert result.strip() == "## Skills match\n\n- foreground platform work"


def test_apply_replace_updates_matching_line():
    resolved = [{"action": "replace", "section": "Role relevance", "rationale": "r",
                 "line": "tighten the wording", "target": "mention platform work"}]
    result = apply_directive_proposals("- mention platform work\n- keep this", resolved)
    assert result.splitlines() == ["- tighten the wording", "- keep this"]


def test_apply_remove_deletes_matching_line():
    resolved = [{"action": "remove", "section": "Wording and typography", "rationale": "r",
                 "line": None, "target": "drop the essay section"}]
    result = apply_directive_proposals("- drop the essay section\n- keep this", resolved)
    assert result.splitlines() == ["- keep this"]


def test_apply_multiple_proposals_in_one_pass():
    resolved = [
        {"action": "remove", "section": "Wording and typography", "rationale": "r",
         "line": None, "target": "old one"},
        {"action": "add", "section": "Skills match", "rationale": "r", "line": "new one", "target": None},
    ]
    result = apply_directive_proposals("## Skills match\n- old one\n- keep this", resolved)
    assert result.splitlines() == ["## Skills match", "- keep this", "- new one"]


def test_apply_stale_target_is_silently_skipped():
    resolved = [{"action": "replace", "section": "Role relevance", "rationale": "r",
                 "line": "new wording", "target": "no longer present"}]
    result = apply_directive_proposals("- something else", resolved)
    assert result == "- something else"
