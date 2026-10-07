from __future__ import annotations
from datetime import date, datetime, timezone


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


def duration(start: str | None, end: str | None) -> str:
    """Compact elapsed span between two ISO-8601 timestamps: '8s', '2m 30s',
    '1h 4m'. Empty string if either side is missing or end precedes start."""
    if not start or not end:
        return ""
    a = datetime.fromisoformat(start)
    b = datetime.fromisoformat(end)
    if a.tzinfo is None:
        a = a.replace(tzinfo=timezone.utc)
    if b.tzinfo is None:
        b = b.replace(tzinfo=timezone.utc)
    total = int((b - a).total_seconds())
    if total < 0:
        return ""
    if total < 60:
        return f"{total}s"
    minutes, seconds = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m {seconds}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


def age(ts: str | None) -> str:
    """Render an ISO-8601 timestamp as a short coarse relative string:
    'just now', '5m ago', '3h ago', '2d ago', '3w ago', '5mo ago', '2y ago'.
    Finer-grained near zero than time_ago; used for task and last-fetch ages."""
    if not ts:
        return ""
    parsed = datetime.fromisoformat(ts)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    seconds = (datetime.now(timezone.utc) - parsed).total_seconds()
    if seconds < 60:
        return "just now"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    if days < 7:
        return f"{days}d ago"
    if days < 30:
        return f"{days // 7}w ago"
    if days < 365:
        return f"{days // 30}mo ago"
    return f"{days // 365}y ago"


def _apply_by_date(apply_by: str | None) -> date | None:
    if not apply_by or apply_by == "rolling":
        return None
    try:
        return date.fromisoformat(apply_by)
    except ValueError:
        return None


def closing(apply_by: str | None, today: date | None = None) -> str:
    """Friendly label for a job's application closing date ('closes in 5 days',
    'rolling', 'apply soonest' when none is known). Never says 'deadline'."""
    if apply_by == "rolling":
        return "rolling"
    d = _apply_by_date(apply_by)
    if d is None:
        return "apply soonest"
    today = today or datetime.now(timezone.utc).date()
    days = (d - today).days
    if days == 0:
        return "closes today"
    if days == 1:
        return "closes tomorrow"
    if 1 < days <= 14:
        return f"closes in {days} days"
    if days > 14:
        return f"closes {d.day} {d.strftime('%b')}"
    if days == -1:
        return "closed yesterday"
    return f"closed {-days} days ago"


def closing_tone(apply_by: str | None, today: date | None = None) -> str:
    """'soon' within 3 days, 'closed' once past, else ''."""
    d = _apply_by_date(apply_by)
    if d is None:
        return ""
    today = today or datetime.now(timezone.utc).date()
    days = (d - today).days
    if days < 0:
        return "closed"
    return "soon" if days <= 3 else ""
