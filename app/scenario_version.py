from __future__ import annotations
import hashlib


def compute_version_hash(scenario: dict, criteria: list[dict]) -> str:
    parts = [scenario.get("name", ""), scenario.get("description", "")]
    for c in sorted(criteria, key=lambda c: (c["weight"], c["text"])):
        parts.append(f"{c['weight']}:{c['text']}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
