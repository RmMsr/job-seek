from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol


@dataclass
class RawJob:
    url: str
    title: str
    company: str
    raw_text: str
    published_at: str | None = None


class Fetcher(Protocol):
    def fetch(self) -> list[RawJob]: ...


def is_recent(published_at: str | None, max_age_days: int) -> bool:
    """True if published_at (ISO-8601) is within max_age_days of now, or unknown."""
    if published_at is None:
        return True
    parsed = datetime.fromisoformat(published_at)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    return parsed >= cutoff
