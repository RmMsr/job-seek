from datetime import datetime, timezone, timedelta
from app.fetchers.base import RawJob, is_recent


def test_raw_job_published_at_defaults_to_none():
    job = RawJob(url="http://x", title="T", company="C", raw_text="r")
    assert job.published_at is None


def test_raw_job_published_at_can_be_set():
    job = RawJob(url="http://x", title="T", company="C", raw_text="r", published_at="2026-07-01T00:00:00+00:00")
    assert job.published_at == "2026-07-01T00:00:00+00:00"


def test_is_recent_none_is_always_recent():
    assert is_recent(None, max_age_days=30) is True


def test_is_recent_within_cutoff():
    published = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    assert is_recent(published, max_age_days=30) is True


def test_is_recent_older_than_cutoff():
    published = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
    assert is_recent(published, max_age_days=30) is False


def test_is_recent_at_exact_boundary_is_recent():
    # A few seconds of slack accounts for the real (non-zero) time between
    # constructing `published` here and `is_recent()` calling `datetime.now()`
    # internally — without it this test is flaky by construction, since two
    # separate `now()` calls always diverge by at least a little.
    published = (datetime.now(timezone.utc) - timedelta(days=30) + timedelta(seconds=5)).isoformat()
    assert is_recent(published, max_age_days=30) is True


def test_is_recent_handles_z_suffix():
    published = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    assert is_recent(published, max_age_days=30) is True


def test_is_recent_handles_naive_datetime():
    published = (datetime.now(timezone.utc) - timedelta(days=1)).replace(tzinfo=None).isoformat()
    assert is_recent(published, max_age_days=30) is True
