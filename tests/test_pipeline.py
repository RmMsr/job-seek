import sqlite3
import pytest
from unittest.mock import MagicMock, patch
from app.db.schema import init_db
from app.db import queries as q
from app.pipeline import run_fetch, FetchResult, _make_fetcher
from app.fetchers.finn import FinnListingFetcher
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
    q.upsert_profile(conn, "I am an ML engineer.")
    return q.get_source(conn, sid)


def _drain(gen):
    messages = []
    try:
        while True:
            messages.append(next(gen))
    except StopIteration as stop:
        return messages, stop.value


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
    summarize_choice.message.content = '{"title": "ML Engineer - Remote @ Acme", "headline": "Great remote ML role", "summary": "Good ML role"}'
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
        messages, result = _drain(run_fetch(source, conn, client, "llama3.2", "browser-profile"))

    assert isinstance(result, FetchResult)
    assert result.jobs_new == 1
    assert result.error is None
    assert any("job_posting" in m for m in messages)
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
        messages, result = _drain(run_fetch(source, conn, client, "llama3.2", "browser-profile"))

    assert result.jobs_found == 1
    assert result.jobs_new == 0
    assert any("Skipping duplicate" in m for m in messages)


def test_run_fetch_records_fetch_run(conn, source):
    with patch("app.pipeline.HttpFetcher") as MockFetcher:
        MockFetcher.return_value.fetch.return_value = []
        _drain(run_fetch(source, conn, client=MagicMock(), model="llama3.2", profile_dir="bp"))

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
        _drain(run_fetch(source, conn, client, "llama3.2", "browser-profile"))

    jobs = q.get_jobs(conn)
    assert jobs[0]["content_type"] == "irrelevant"
    assert jobs[0]["summary"] == ""
    assert client.chat.completions.create.call_count == 1


def test_run_fetch_yields_progress_and_logs_each_line(conn, source, caplog):
    raw_jobs = [RawJob(url="http://example.com/job/1", title="ML Eng", company="Acme", raw_text="<p>We are hiring</p>")]
    client = _mock_client(
        '{"type": "job_posting", "reason": "full description"}',
        '{"title": "ML Engineer - Remote @ Acme", "headline": "Great remote ML role", "summary": "Good ML role"}',
        '{"score": 0.9, "reasoning": "Great match"}',
    )

    with patch("app.pipeline.HttpFetcher") as MockFetcher, caplog.at_level("INFO", logger="job_seek"):
        MockFetcher.return_value.fetch.return_value = raw_jobs
        messages, result = _drain(run_fetch(source, conn, client, "llama3.2", "browser-profile"))

    assert any("Starting fetch" in m for m in messages)
    assert any("job_posting" in m for m in messages)
    assert any("Fetch complete" in m for m in messages)
    assert messages == [r.message for r in caplog.records]


def test_make_fetcher_dispatches_finn_listing():
    source = {"id": 1, "name": "finn.no", "url": "http://x", "fetcher_type": "finn_listing"}
    assert isinstance(_make_fetcher(source, "browser-profile"), FinnListingFetcher)


def test_run_fetch_scores_against_every_scenario(conn, source):
    first_id = q.get_scenarios(conn)[0]["id"]
    second_id = q.insert_scenario(conn, "Robotics", "")
    q.insert_criterion(conn, second_id, "Must involve embedded systems", "must")

    raw_jobs = [RawJob(url="http://example.com/job/1", title="ML Eng", company="Acme", raw_text="<p>We are hiring</p>")]
    client = _mock_client(
        '{"type": "job_posting", "reason": "full description"}',
        '{"title": "ML Engineer - Remote @ Acme", "headline": "Great remote ML role", "summary": "Good ML role"}',
        '{"score": 0.9, "reasoning": "Great match"}',
    )

    with patch("app.pipeline.HttpFetcher") as MockFetcher:
        MockFetcher.return_value.fetch.return_value = raw_jobs
        messages, result = _drain(run_fetch(source, conn, client, "llama3.2", "browser-profile"))

    job_id = q.get_jobs(conn)[0]["id"]
    assert q.get_job_score(conn, job_id, first_id) is not None
    assert q.get_job_score(conn, job_id, second_id) is not None
    assert sum("Scored" in m for m in messages) == 2


def test_run_fetch_with_no_scenarios_still_summarizes(conn):
    source_id = q.insert_source(conn, "test", "http://example.com", "http")
    source_dict = q.get_source(conn, source_id)
    raw_jobs = [RawJob(url="http://example.com/job/1", title="ML Eng", company="Acme", raw_text="<p>We are hiring</p>")]
    client = _mock_client(
        '{"type": "job_posting", "reason": "full description"}',
        '{"title": "ML Engineer - Remote @ Acme", "headline": "Great remote ML role", "summary": "Good ML role"}',
        '{"score": 0.9, "reasoning": "Great match"}',
    )

    with patch("app.pipeline.HttpFetcher") as MockFetcher:
        MockFetcher.return_value.fetch.return_value = raw_jobs
        messages, result = _drain(run_fetch(source_dict, conn, client, "llama3.2", "browser-profile"))

    job = q.get_jobs(conn)[0]
    assert job["content_type"] == "job_posting"
    assert job["summary"] == "Good ML role"
    assert job["best_score"] is None
    assert not any("Scored" in m for m in messages)


def test_run_fetch_stores_ai_title_and_headline(conn, source):
    raw_jobs = [RawJob(url="http://example.com/job/1", title="scraped title", company="Acme", raw_text="<p>hi</p>")]
    client = _mock_client(
        '{"type": "job_posting", "reason": "full description"}',
        '{"title": "ML Engineer - Remote @ Acme", "headline": "Great remote ML role", "summary": "Good ML role"}',
        '{"score": 0.9, "reasoning": "Great match"}',
    )
    with patch("app.pipeline.HttpFetcher") as MockFetcher:
        MockFetcher.return_value.fetch.return_value = raw_jobs
        _drain(run_fetch(source, conn, client, "llama3.2", "browser-profile"))

    job = q.get_jobs(conn)[0]
    assert job["title"] == "ML Engineer - Remote @ Acme"
    assert job["headline"] == "Great remote ML role"


def test_run_fetch_keeps_scraped_title_when_ai_title_empty(conn, source):
    raw_jobs = [RawJob(url="http://example.com/job/1", title="scraped title", company="Acme", raw_text="<p>hi</p>")]
    client = _mock_client(
        '{"type": "job_posting", "reason": "full description"}',
        '{"title": "", "headline": "", "summary": "Good ML role"}',
        '{"score": 0.9, "reasoning": "Great match"}',
    )
    with patch("app.pipeline.HttpFetcher") as MockFetcher:
        MockFetcher.return_value.fetch.return_value = raw_jobs
        _drain(run_fetch(source, conn, client, "llama3.2", "browser-profile"))

    job = q.get_jobs(conn)[0]
    assert job["title"] == "scraped title"
    assert job["headline"] == ""
