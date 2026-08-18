from app.ai.refine_profile import ProfileProposal
from app.profile_apply import resolve_proposals, apply_profile_proposals, group_proposals_by_section

_PROFILE = (
    "# About me\n"
    "\n"
    "## Technologies\n"
    "\n"
    "- Python\n"
    "- Go\n"
    "\n"
    "## Methodologies\n"
    "\n"
    "- Agile principles\n"
)


_MULTI_LIST_PROFILE = (
    "## Team Setup\n"
    "\n"
    "Tools\n"
    "\n"
    "- Slack\n"
    "- Jira\n"
    "\n"
    "Timezone: UTC+1.\n"
    "\n"
    "## Compensation\n"
    "\n"
    "- Base salary negotiable\n"
)


def test_resolve_add_to_existing_section():
    proposals = [ProfileProposal(section="Technologies", action="add", text="Rust", target=None)]
    resolved = resolve_proposals(proposals, _PROFILE)
    assert resolved == [{"action": "add", "section": "Technologies", "text": "Rust", "target": None}]


def test_resolve_add_with_anchor_carries_anchor_through():
    proposals = [ProfileProposal(section="Team Setup", action="add", text="Linear", target=None, anchor="Tools")]
    resolved = resolve_proposals(proposals, _MULTI_LIST_PROFILE)
    assert resolved == [
        {"action": "add", "section": "Team Setup", "text": "Linear", "target": None, "anchor": "Tools"}
    ]


def test_resolve_add_to_new_section_is_kept():
    proposals = [ProfileProposal(section="New Section", action="add", text="Something", target=None)]
    resolved = resolve_proposals(proposals, _PROFILE)
    assert resolved == [{"action": "add", "section": "New Section", "text": "Something", "target": None}]


def test_resolve_add_duplicating_existing_bullet_is_omitted():
    proposals = [ProfileProposal(section="Technologies", action="add", text="  python  ", target=None)]
    assert resolve_proposals(proposals, _PROFILE) == []


def test_resolve_remove_matching_bullet_is_kept():
    proposals = [ProfileProposal(section="Technologies", action="remove", text=None, target="Go")]
    resolved = resolve_proposals(proposals, _PROFILE)
    assert resolved == [{"action": "remove", "section": "Technologies", "text": None, "target": "Go"}]


def test_resolve_remove_unmatched_bullet_is_omitted():
    proposals = [ProfileProposal(section="Technologies", action="remove", text=None, target="Rust")]
    assert resolve_proposals(proposals, _PROFILE) == []


def test_resolve_replace_matching_bullet_is_kept():
    proposals = [ProfileProposal(section="Technologies", action="replace", text="Golang", target="Go")]
    resolved = resolve_proposals(proposals, _PROFILE)
    assert resolved == [{"action": "replace", "section": "Technologies", "text": "Golang", "target": "Go"}]


def test_resolve_replace_unmatched_target_is_omitted():
    proposals = [ProfileProposal(section="Technologies", action="replace", text="X", target="Rust")]
    assert resolve_proposals(proposals, _PROFILE) == []


def test_apply_add_appends_bullet_to_existing_section():
    resolved = [{"action": "add", "section": "Technologies", "text": "Rust", "target": None}]
    result = apply_profile_proposals(_PROFILE, resolved)
    lines = result.splitlines()
    tech_idx = lines.index("## Technologies")
    method_idx = lines.index("## Methodologies")
    assert "- Rust" in lines[tech_idx:method_idx]


def test_apply_add_with_anchor_inserts_into_correct_list():
    resolved = [
        {"action": "add", "section": "Team Setup", "text": "Linear", "target": None, "anchor": "Tools"}
    ]
    result = apply_profile_proposals(_MULTI_LIST_PROFILE, resolved)
    lines = result.splitlines()
    jira_idx = lines.index("- Jira")
    assert lines[jira_idx + 1] == "- Linear"
    assert lines[jira_idx + 2] == ""
    assert lines[jira_idx + 3] == "Timezone: UTC+1."


def test_apply_add_with_unmatched_anchor_falls_back_to_section_end():
    resolved = [
        {"action": "add", "section": "Team Setup", "text": "Linear", "target": None, "anchor": "Nonexistent"}
    ]
    result = apply_profile_proposals(_MULTI_LIST_PROFILE, resolved)
    lines = result.splitlines()
    tz_idx = lines.index("Timezone: UTC+1.")
    assert lines[tz_idx + 1] == "- Linear"


def test_apply_add_without_anchor_still_appends_at_section_end():
    resolved = [{"action": "add", "section": "Team Setup", "text": "Linear", "target": None}]
    result = apply_profile_proposals(_MULTI_LIST_PROFILE, resolved)
    lines = result.splitlines()
    tz_idx = lines.index("Timezone: UTC+1.")
    assert lines[tz_idx + 1] == "- Linear"


def test_apply_add_with_anchor_on_empty_list_inserts_right_after_anchor():
    profile = "## Perks\n\nSnacks\n\nSchedule: Flexible hours.\n"
    resolved = [
        {"action": "add", "section": "Perks", "text": "Coffee", "target": None, "anchor": "Snacks"}
    ]
    result = apply_profile_proposals(profile, resolved)
    lines = result.splitlines()
    anchor_idx = lines.index("Snacks")
    assert lines[anchor_idx + 1] == "- Coffee"


def test_apply_add_inserts_before_trailing_blank_line_not_after():
    # _PROFILE's "Technologies" section ends with a blank separator line
    # before the next "##" heading. The new bullet must land right after
    # the last existing bullet, leaving that blank line as the separator
    # to the next section — not after the blank line (which would put a
    # blank line above the new bullet and none below it).
    resolved = [{"action": "add", "section": "Technologies", "text": "Rust", "target": None}]
    result = apply_profile_proposals(_PROFILE, resolved)
    lines = result.splitlines()
    go_idx = lines.index("- Go")
    assert lines[go_idx + 1] == "- Rust"
    assert lines[go_idx + 2] == ""
    assert lines[go_idx + 3] == "## Methodologies"


def test_apply_add_strips_leading_dash_from_text_to_avoid_double_dash():
    resolved = [{"action": "add", "section": "Technologies", "text": "- Rust", "target": None}]
    result = apply_profile_proposals(_PROFILE, resolved)
    assert "- Rust" in result.splitlines()
    assert "- - Rust" not in result


def test_apply_replace_strips_leading_dash_from_text_to_avoid_double_dash():
    resolved = [{"action": "replace", "section": "Technologies", "text": "- Golang", "target": "Go"}]
    result = apply_profile_proposals(_PROFILE, resolved)
    assert "- Golang" in result.splitlines()
    assert "- - Golang" not in result


def test_apply_add_creates_new_section_at_end():
    resolved = [{"action": "add", "section": "New Section", "text": "Something", "target": None}]
    result = apply_profile_proposals(_PROFILE, resolved)
    assert "## New Section" in result
    lines = result.splitlines()
    new_idx = lines.index("## New Section")
    assert lines[new_idx + 1] == "- Something"


def test_apply_remove_deletes_matching_bullet():
    resolved = [{"action": "remove", "section": "Technologies", "text": None, "target": "Go"}]
    result = apply_profile_proposals(_PROFILE, resolved)
    assert "- Go" not in result.splitlines()
    assert "- Python" in result.splitlines()


def test_apply_replace_updates_bullet_text():
    resolved = [{"action": "replace", "section": "Technologies", "text": "Golang", "target": "Go"}]
    result = apply_profile_proposals(_PROFILE, resolved)
    assert "- Golang" in result.splitlines()
    assert "- Go" not in result.splitlines()


def test_apply_multiple_adds_to_same_new_section():
    resolved = [
        {"action": "add", "section": "New Section", "text": "First", "target": None},
        {"action": "add", "section": "New Section", "text": "Second", "target": None},
    ]
    result = apply_profile_proposals(_PROFILE, resolved)
    assert result.count("## New Section") == 1
    assert "- First" in result.splitlines()
    assert "- Second" in result.splitlines()


def test_group_proposals_by_section_groups_same_section_together():
    resolved = [
        {"action": "add", "section": "Technologies", "text": "Rust", "target": None},
        {"action": "remove", "section": "Technologies", "text": None, "target": "Go"},
    ]
    grouped = group_proposals_by_section(resolved)
    assert grouped == [
        {
            "section": "Technologies",
            "rows": [
                {"index": 0, "action": "add", "section": "Technologies", "text": "Rust", "target": None},
                {"index": 1, "action": "remove", "section": "Technologies", "text": None, "target": "Go"},
            ],
        }
    ]


def test_group_proposals_by_section_preserves_first_appearance_order():
    resolved = [
        {"action": "add", "section": "Methodologies", "text": "Shift-left", "target": None},
        {"action": "add", "section": "Technologies", "text": "Rust", "target": None},
        {"action": "add", "section": "Methodologies", "text": "TDD", "target": None},
    ]
    grouped = group_proposals_by_section(resolved)
    assert [g["section"] for g in grouped] == ["Methodologies", "Technologies"]
    assert [item["index"] for item in grouped[0]["rows"]] == [0, 2]


def test_group_proposals_by_section_empty_list():
    assert group_proposals_by_section([]) == []
