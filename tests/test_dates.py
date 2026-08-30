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


from app.dates import age


def test_age_none_and_empty_return_empty_string():
    assert age(None) == ""
    assert age("") == ""


def test_age_under_a_minute_is_just_now():
    ts = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    assert age(ts) == "just now"


def test_age_minutes():
    ts = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    assert age(ts) == "5m ago"


def test_age_hours():
    ts = (datetime.now(timezone.utc) - timedelta(hours=3, minutes=10)).isoformat()
    assert age(ts) == "3h ago"


def test_age_days():
    ts = (datetime.now(timezone.utc) - timedelta(hours=50)).isoformat()
    assert age(ts) == "2d ago"


def test_age_future_timestamp_is_just_now():
    ts = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    assert age(ts) == "just now"
