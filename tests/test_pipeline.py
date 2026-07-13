import sqlite3
import pytest
from unittest.mock import MagicMock, patch
from app.db.schema import init_db
from app.db import queries as q
from app.pipeline import run_fetch, FetchResult
from app.fetchers.base import RawJob


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_db(c)
    yield c
    c.close()


@pytest.fixture
def source(conn):
    sid = q.insert_source(conn, "test", "http://example.com", "http")
    q.insert_scenario(conn, "Remote ML", "")
    scenario = q.get_scenarios(conn)[0]
    q.set_active_scenario(conn, scenario["id"])
    q.upsert_profile(conn, "I am an ML engineer.")
    return q.get_source(conn, sid)


def _mock_client(classify_resp, summarize_resp, evaluate_resp):
    client = MagicMock()
    def create(**kwargs):
        system = kwargs["messages"][0]["content"]
        choice = MagicMock()
        if "classify" in system.lower() or "job_posting" in system.lower() or "lead" in system.lower():
            choice.message.content = classify_resp
        elif "summarize" in system.lower() or "summary" in system.lower():
            choice.message.content = summarize_resp
        else:
            choice.message.content = evaluate_resp
        return MagicMock(choices=[choice])
    client.chat.completions.create.side_effect = create
    return client


def test_run_fetch_new_job_stored(conn, source):
    raw_jobs = [RawJob(url="http://example.com/job/1", title="ML Eng", company="Acme", raw_text="<p>We are hiring</p>")]
    client = MagicMock()
    choice = MagicMock()
    choice.message.content = '{"type": "job_posting", "reason": "full description"}'
    summarize_choice = MagicMock()
    summarize_choice.message.content = "Good ML role"
    evaluate_choice = MagicMock()
    evaluate_choice.message.content = '{"score": 0.9, "reasoning": "Great match"}'
    call_count = [0]
    def create(**kwargs):
        call_count[0] += 1
        m = MagicMock()
        if call_count[0] == 1:
            m.choices = [choice]
        elif call_count[0] == 2:
            m.choices = [summarize_choice]
        else:
            m.choices = [evaluate_choice]
        return m
    client.chat.completions.create.side_effect = create

    with patch("app.pipeline.HttpFetcher") as MockFetcher:
        MockFetcher.return_value.fetch.return_value = raw_jobs
        result = run_fetch(source, conn, client, "llama3.2", "browser-profile")

    assert isinstance(result, FetchResult)
    assert result.jobs_new == 1
    assert result.error is None
    jobs = q.get_jobs(conn)
    assert len(jobs) == 1
    assert jobs[0]["url"] == "http://example.com/job/1"
    assert jobs[0]["content_type"] == "job_posting"


def test_run_fetch_skips_existing_url(conn, source):
    q.insert_job(conn, source_id=source["id"], url="http://example.com/job/1", title="T", company="C", raw_text="r")
    raw_jobs = [RawJob(url="http://example.com/job/1", title="ML Eng", company="Acme", raw_text="text")]
    client = MagicMock()

    with patch("app.pipeline.HttpFetcher") as MockFetcher:
        MockFetcher.return_value.fetch.return_value = raw_jobs
        result = run_fetch(source, conn, client, "llama3.2", "browser-profile")

    assert result.jobs_found == 1
    assert result.jobs_new == 0


def test_run_fetch_records_fetch_run(conn, source):
    with patch("app.pipeline.HttpFetcher") as MockFetcher:
        MockFetcher.return_value.fetch.return_value = []
        run_fetch(source, conn, client=MagicMock(), model="llama3.2", profile_dir="bp")

    runs = q.get_recent_fetch_runs(conn)
    assert len(runs) == 1
    assert runs[0]["completed_at"] is not None


def test_run_fetch_irrelevant_not_evaluated(conn, source):
    raw_jobs = [RawJob(url="http://example.com/job/1", title="", company="", raw_text="meetup next week")]
    client = MagicMock()
    choice = MagicMock()
    choice.message.content = '{"type": "irrelevant", "reason": "not a job"}'
    client.chat.completions.create.return_value = MagicMock(choices=[choice])

    with patch("app.pipeline.HttpFetcher") as MockFetcher:
        MockFetcher.return_value.fetch.return_value = raw_jobs
        run_fetch(source, conn, client, "llama3.2", "browser-profile")

    jobs = q.get_jobs(conn)
    assert jobs[0]["content_type"] == "irrelevant"
    assert jobs[0]["summary"] == ""
    assert client.chat.completions.create.call_count == 1
