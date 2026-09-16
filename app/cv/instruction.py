from __future__ import annotations

import difflib
from pathlib import Path

from app.ai.tailor_cv import DirectiveProposal

# The base CV a fresh database starts from. Editable in full on the /cv page —
# this is only the seed. Kept as a sibling file rather than an inline constant
# because it's a whole document, not a short default string.
DEFAULT_BASE_CV = (Path(__file__).resolve().parent / "default_base_cv.md").read_text()
from app.bullet_edits import (
    find_bullet, format_bullet, replace_bullet, remove_bullet, insert_bullet_under_heading,
)

# Each rule is phrased as a self-contained prohibition so it reads cleanly as
# one line in the settings textarea and drops straight into the guardrail
# check without re-numbering or re-framing. This is the *default* content of
# the user-editable guardrails field, not a hardcoded, unbreakable floor —
# the user has full control to edit or remove any of it; "Reset to defaults"
# in CV settings restores exactly this list.
DEFAULT_GUARDRAILS_RULES: list[str] = [
    "The frontmatter contains metadata and must not be removed.",
    "The <aside> HTML tag must not be removed unless it contains no elements.",
    "Do not add a degree, certification, school, or field of study that is not in the base CV.",
    "Do not add an employer, client or project not in the base CV.",
    "Do not use job titles that significantly differ from the base CV or tuning direction.",
    "Do not add employment dates or lengthen a tenure.",
    "Do not invent a quantified metric — a number, percentage, team size, revenue figure, or duration.",
    "Do not be more specific than the base CV or tuning directions imply. Generalization is okay.",
    "Do not address the job offering or organization directly unless explicitly asked. This is not a cover letter.",
]

DEFAULT_GUARDRAILS = "\n".join(DEFAULT_GUARDRAILS_RULES)

# The default headed outline a new job's tuning-directives editor starts from.
# The evaluation pass walks these headings and proposes directives under each.
DEFAULT_DIRECTIVES_TEMPLATE = "\n\n".join(
    "## " + h for h in [
        "Role relevance",
        "Skills match",
        "Content hierarchy and placement",
        "Requirement coverage and gaps",
        "Achievement evidence",
        "Experience and seniority",
        "Personal traits and transparency",
        "Wording and typography",
    ]
) + "\n"

# Replaces the old SCOPE_LINES/SCOPE_ORDER dict — these are now the *default*
# seed content for the user-editable cv_scope_options table (app/db/schema.py's
# _DDL + queries.py's _seed_default_scope_options), not a fixed enum.
# default_enabled=True means a new job starts with this scope checked.
DEFAULT_SCOPE_OPTIONS: list[dict] = [
    {"name": "correct", "default_enabled": True,
     "description": "Fix spelling errors, incorrect grammar, inconsistent naming or typography."},
    {"name": "choose", "default_enabled": True,
     "description": "Include or omit existing bullets and whole sections by relevance to this job."},
    {"name": "organize", "default_enabled": True,
     "description": "Reorder bullets and sections to emphasize key skills and requirements for this job."},
    {"name": "rephrase", "default_enabled": False,
     "description": "Reword existing bullets toward the job's terminology, without introducing a "
                     "claim the base CV does not already support or upgrading the scope or seniority of one."},
    {"name": "introduce", "default_enabled": False,
     "description": "Write a leading paragraph or professional summary conveying the personal alignment "
                     "and mutual interests relevant to both applicant and organisation — without directly "
                     "addressing the opportunity; leave that for a cover letter."},
    {"name": "wildcard", "default_enabled": False,
     "description": "Freely write a new CV ignoring existing hierarchy, sections and content. Reuse factual information. Aim for industry standard expected CV style, language and fit."}
]


def scope_line(opt: dict) -> str:
    name = (opt.get("name") or "").strip()
    return f"- **{name}**: {opt['description']}" if name else f"- {opt['description']}"


def compose_instruction(
    *, base_instruction: str, scope: list[int], scope_options: list[dict],
    guardrails: str, tuning_directives: str,
) -> str:
    scope_set = set(scope)
    enabled = [o for o in scope_options if o["id"] in scope_set]
    parts: list[str] = []
    if base_instruction.strip():
        parts.append(base_instruction.strip())
        parts.append("")
    parts.append(
        "Permitted edits — the ONLY kinds of change you may make to the CV. This is "
        "a hard boundary: any other kind of change is off-limits, even when a tuning "
        "directive below calls for it."
    )
    parts.extend(scope_line(o) for o in enabled)
    parts.append("")
    parts.append(
        "Each permitted edit applies in full and independently — none is optional and none "
        "is traded off against another or against a tuning directive. In particular, if "
        "correcting spelling, grammar, naming and typography is permitted, apply it "
        "everywhere it applies, every time: it is mechanical and always safe, and no "
        "directive or priority can outweigh or skip it."
    )
    parts.append("")
    parts.append("Hard limits — never break these:")
    parts.append(guardrails.strip() or "(none)")
    parts.append("")
    directives = tuning_directives.strip() or "(none)"
    parts.append(
        "Tuning directives — your priorities for this job. Pursue each one only as far as "
        "the permitted edits above allow; where a directive would need an edit that is not "
        "permitted, apply what you can within the permitted edits and otherwise leave that "
        "content as it stands in the CV."
    )
    parts.append(directives)
    return "\n".join(parts)


def _near_duplicate(text: str, of: list[str], ratio: float = 0.82) -> bool:
    t = (text or "").strip().casefold()
    if not t:
        return False
    return any(
        difflib.SequenceMatcher(None, t, (o or "").strip().casefold()).ratio() >= ratio
        for o in of
    )


def resolve_directive_proposals(
    proposals: list[DirectiveProposal], tuning_directives: str,
    handled: list[dict] | None = None,
) -> list[dict]:
    lines = tuning_directives.splitlines()
    # The plan prompt is the real defence against re-proposing handled ideas;
    # this only catches near-verbatim repeats the model slipped through.
    handled_add = [h.get("line") for h in (handled or []) if h.get("action") != "remove"]
    handled_rm = [h.get("target") for h in (handled or []) if h.get("action") == "remove"]
    resolved = []
    for p in proposals:
        if p.action == "add":
            if find_bullet(lines, p.line) is not None:
                continue
            if _near_duplicate(p.line, handled_add):
                continue
            resolved.append({
                "action": "add", "section": p.section, "rationale": p.rationale,
                "line": p.line, "target": None,
            })
        else:
            if find_bullet(lines, p.target) is None:
                continue
            if p.action == "remove" and _near_duplicate(p.target, handled_rm):
                continue
            if p.action == "replace" and _near_duplicate(p.line, handled_add):
                continue
            resolved.append({
                "action": p.action, "section": p.section, "rationale": p.rationale,
                "line": p.line, "target": p.target,
            })
    return resolved


def apply_directive_proposals(tuning_directives: str, resolved: list[dict]) -> str:
    lines = tuning_directives.splitlines()
    for r in resolved:
        if r["action"] == "remove":
            remove_bullet(lines, r["target"])
        elif r["action"] == "replace":
            replace_bullet(lines, r["target"], r["line"])
        elif r["action"] == "add":
            insert_bullet_under_heading(lines, r.get("section") or "", r["line"])
    result = "\n".join(lines)
    if tuning_directives.endswith("\n"):
        result += "\n"
    return result
