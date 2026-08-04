from datetime import datetime, timezone, timedelta
from app.dates import time_ago


def test_time_ago_none_returns_empty_string():
    assert time_ago(None) == ""


def test_time_ago_today():
    published = datetime.now(timezone.utc).isoformat()
    assert time_ago(published) == "today"


def test_time_ago_one_day():
    published = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    assert time_ago(published) == "1 day ago"


def test_time_ago_multiple_days():
    published = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    assert time_ago(published) == "5 days ago"


def test_time_ago_handles_z_suffix():
    published = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    assert time_ago(published) == "2 days ago"
