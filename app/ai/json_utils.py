from __future__ import annotations
import re

_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?```$", re.DOTALL)


def extract_json(text: str) -> str:
    """Strip a surrounding markdown code fence (```json ... ``` or ``` ... ```),
    which some models emit around JSON responses despite being asked not to."""
    text = text.strip()
    match = _FENCE_RE.match(text)
    if match:
        return match.group(1).strip()
    return text
