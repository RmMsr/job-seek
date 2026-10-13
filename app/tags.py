from __future__ import annotations
import re

_WS_RE = re.compile(r"\s+")


def normalize_tag_name(raw: str | None) -> str:
    """Trim and collapse inner whitespace. Used for tag names and manual org names."""
    return _WS_RE.sub(" ", (raw or "").strip())
