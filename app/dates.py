from __future__ import annotations
from datetime import datetime, timezone


def time_ago(published_at: str | None) -> str:
    """Render an ISO-8601 timestamp as a short relative string, e.g. '5 days ago'."""
    if not published_at:
        return ""
    parsed = datetime.fromisoformat(published_at)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    days = (datetime.now(timezone.utc) - parsed).days
    if days <= 0:
        return "today"
    if days == 1:
        return "1 day ago"
    return f"{days} days ago"
