from __future__ import annotations
from app.ai.refine_profile import ProfileProposal


def _find_bullet_line(lines: list[str], text: str) -> int | None:
    needle = text.strip().casefold()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("- ") and stripped[2:].strip().casefold() == needle:
            return i
    return None


def _find_section_bounds(lines: list[str], section: str) -> tuple[int, int] | None:
    needle = section.strip().casefold()
    for i, line in enumerate(lines):
        if line.startswith("## ") and line[3:].strip().casefold() == needle:
            j = i + 1
            while j < len(lines) and not lines[j].startswith("#"):
                j += 1
            return (i, j)
    return None


def _find_anchor_line(lines: list[str], start: int, end: int, anchor: str) -> int | None:
    needle = anchor.strip().casefold()
    for i in range(start + 1, end):
        stripped = lines[i].strip()
        if stripped and not stripped.startswith("- ") and not stripped.startswith("#") and stripped.casefold() == needle:
            return i
    return None


def _find_anchored_list_end(lines: list[str], search_from: int, end: int) -> int:
    j = search_from
    while j < end and not lines[j].strip().startswith("- "):
        j += 1
    if j >= end:
        return search_from
    while j < end and lines[j].strip().startswith("- "):
        j += 1
    return j


def resolve_proposals(proposals: list[ProfileProposal], profile_text: str) -> list[dict]:
    lines = profile_text.splitlines()
    resolved = []
    for p in proposals:
        if p.action == "add":
            if _find_bullet_line(lines, p.text) is not None:
                continue
            row = {"action": "add", "section": p.section, "text": p.text, "target": None}
            if p.anchor:
                row["anchor"] = p.anchor
            resolved.append(row)
        else:
            if _find_bullet_line(lines, p.target) is None:
                continue
            resolved.append({"action": p.action, "section": p.section, "text": p.text, "target": p.target})
    return resolved


def _format_bullet(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("- "):
        stripped = stripped[2:].strip()
    elif stripped.startswith("-"):
        stripped = stripped[1:].strip()
    return f"- {stripped}"


def group_proposals_by_section(resolved: list[dict]) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for i, r in enumerate(resolved):
        groups.setdefault(r["section"], []).append({**r, "index": i})
    return [{"section": section, "rows": rows} for section, rows in groups.items()]


def apply_profile_proposals(profile_text: str, resolved: list[dict]) -> str:
    lines = profile_text.splitlines()
    for r in resolved:
        if r["action"] == "remove":
            idx = _find_bullet_line(lines, r["target"])
            if idx is not None:
                del lines[idx]
        elif r["action"] == "replace":
            idx = _find_bullet_line(lines, r["target"])
            if idx is not None:
                lines[idx] = _format_bullet(r["text"])
        elif r["action"] == "add":
            bounds = _find_section_bounds(lines, r["section"])
            if bounds is not None:
                start, end = bounds
                insert_at = None
                anchor = r.get("anchor")
                if anchor:
                    anchor_idx = _find_anchor_line(lines, start, end, anchor)
                    if anchor_idx is not None:
                        insert_at = _find_anchored_list_end(lines, anchor_idx + 1, end)
                if insert_at is None:
                    insert_at = end
                    while insert_at > start + 1 and lines[insert_at - 1].strip() == "":
                        insert_at -= 1
                lines.insert(insert_at, _format_bullet(r["text"]))
            else:
                if lines and lines[-1].strip() != "":
                    lines.append("")
                lines.append(f"## {r['section']}")
                lines.append(_format_bullet(r["text"]))
    result = "\n".join(lines)
    if profile_text.endswith("\n"):
        result += "\n"
    return result
