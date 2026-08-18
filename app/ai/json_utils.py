from __future__ import annotations
import re

_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL)


def extract_json(text: str) -> str:
    """Strip a surrounding markdown code fence (```json ... ``` or ``` ... ```),
    which some models emit around JSON responses despite being asked not to.

    Some models still emit visible self-correction ("Wait, that's wrong...")
    between two fenced blocks despite being told not to reason aloud — the
    last block is the corrected, final answer, so when there's more than one,
    that's the one to use."""
    text = text.strip()
    matches = _FENCE_RE.findall(text)
    if matches:
        return matches[-1].strip()
    return text
