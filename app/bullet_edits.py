from __future__ import annotations


def find_bullet(lines: list[str], text: str) -> int | None:
    needle = text.strip().casefold()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("- ") and stripped[2:].strip().casefold() == needle:
            return i
    return None


def format_bullet(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("- "):
        stripped = stripped[2:].strip()
    elif stripped.startswith("-"):
        stripped = stripped[1:].strip()
    return f"- {stripped}"


def replace_bullet(lines: list[str], target: str, text: str) -> bool:
    idx = find_bullet(lines, target)
    if idx is None:
        return False
    lines[idx] = format_bullet(text)
    return True


def remove_bullet(lines: list[str], target: str) -> bool:
    idx = find_bullet(lines, target)
    if idx is None:
        return False
    del lines[idx]
    return True


def _heading_index(lines: list[str], section: str) -> int | None:
    needle = section.strip().casefold()
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("#") and s.lstrip("#").strip().casefold() == needle:
            return i
    return None


def insert_bullet_under_heading(lines: list[str], section: str, text: str) -> None:
    """Append `text` as the last bullet of `section` (matched by heading text,
    case-insensitively), mutating `lines` in place. Keeps exactly one blank line
    between the heading and its first bullet. If the heading is absent, create it
    at the end of the document."""
    bullet = format_bullet(text)
    hi = _heading_index(lines, section)
    if hi is None:
        while lines and not lines[-1].strip():
            lines.pop()
        if lines:
            lines.append("")
        lines.append(f"## {section.strip()}")
        lines.append("")
        lines.append(bullet)
        return
    end = len(lines)
    for j in range(hi + 1, len(lines)):
        if lines[j].strip().startswith("#"):
            end = j
            break
    insert_at = end
    while insert_at - 1 > hi and not lines[insert_at - 1].strip():
        insert_at -= 1
    if insert_at == hi + 1:            # this is the section's first bullet
        lines.insert(insert_at, "")
        insert_at += 1
    lines.insert(insert_at, bullet)
