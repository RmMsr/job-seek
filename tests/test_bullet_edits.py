from app.bullet_edits import (
    find_bullet, format_bullet, replace_bullet, remove_bullet, insert_bullet_under_heading,
)


def test_insert_appends_as_last_bullet_of_the_section():
    lines = "## A\n- one\n\n## B\n- two".split("\n")
    insert_bullet_under_heading(lines, "A", "one-and-a-half")
    assert "\n".join(lines) == "## A\n- one\n- one-and-a-half\n\n## B\n- two"


def test_insert_creates_missing_heading_at_end():
    lines = "## A\n- one".split("\n")
    insert_bullet_under_heading(lines, "C", "new")
    assert "\n".join(lines) == "## A\n- one\n\n## C\n\n- new"


def test_insert_into_empty_document():
    lines = [""]
    insert_bullet_under_heading(lines, "A", "new")
    assert "\n".join(lines).strip() == "## A\n\n- new"


def test_insert_first_bullet_keeps_blank_line_after_heading():
    lines = "## A\n## B\n- two".split("\n")
    insert_bullet_under_heading(lines, "A", "first")
    assert "\n".join(lines) == "## A\n\n- first\n## B\n- two"


def test_insert_first_bullet_does_not_double_the_blank_line():
    lines = "## A\n\n## B".split("\n")
    insert_bullet_under_heading(lines, "A", "first")
    assert "\n".join(lines) == "## A\n\n- first\n\n## B"


def test_insert_second_bullet_appends_tight_to_the_list():
    lines = "## A\n\n- one\n\n## B".split("\n")
    insert_bullet_under_heading(lines, "A", "two")
    assert "\n".join(lines) == "## A\n\n- one\n- two\n\n## B"


def test_find_bullet_matches_exact_text_case_insensitive():
    lines = ["- Python", "- Go"]
    assert find_bullet(lines, "go") == 1


def test_find_bullet_returns_none_when_absent():
    assert find_bullet(["- Python"], "Rust") is None


def test_find_bullet_ignores_non_bullet_lines():
    assert find_bullet(["Go", "- Python"], "Go") is None


def test_format_bullet_adds_prefix():
    assert format_bullet("Rust") == "- Rust"


def test_format_bullet_strips_existing_dash_prefix():
    assert format_bullet("- Rust") == "- Rust"
    assert format_bullet("-Rust") == "- Rust"


def test_replace_bullet_updates_matching_line():
    lines = ["- Go", "- Python"]
    assert replace_bullet(lines, "Go", "Golang") is True
    assert lines == ["- Golang", "- Python"]


def test_replace_bullet_returns_false_when_target_missing():
    lines = ["- Python"]
    assert replace_bullet(lines, "Rust", "Rust lang") is False
    assert lines == ["- Python"]


def test_remove_bullet_deletes_matching_line():
    lines = ["- Go", "- Python"]
    assert remove_bullet(lines, "Go") is True
    assert lines == ["- Python"]


def test_remove_bullet_returns_false_when_target_missing():
    lines = ["- Python"]
    assert remove_bullet(lines, "Rust") is False
    assert lines == ["- Python"]
