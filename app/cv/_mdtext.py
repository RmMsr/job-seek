"""Small shared helpers for reading CV markdown as plain text — heading/HTML
recognisers and text normalisation used by the diff engine."""
from __future__ import annotations

import difflib
import re

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)")
_HTML_TAG_ONLY_RE = re.compile(r"^</?[a-zA-Z][^>]*>$")
_EMPHASIS_RE = re.compile(r"[*_`#>]+")
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")

# 0.2: a leftover dropped/added pair sharing at least a fifth of its words is
# really the same content reworded, not two unrelated lines — see the rematch
# pass in cv_diff.
_REMATCH_THRESHOLD = 0.2


def _norm(t: str, *, keep_fmt: bool = False) -> str:
    s = t.lower()
    if not keep_fmt:
        s = _LINK_RE.sub(r"\1", s)
        s = _EMPHASIS_RE.sub("", s)
    return re.sub(r"\s+", " ", s).strip()


def _word_ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _norm(a).split(), _norm(b).split()).ratio()
