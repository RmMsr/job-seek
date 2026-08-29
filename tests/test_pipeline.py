import sqlite3
import pytest
from unittest.mock import MagicMock, patch
from app.db.schema import init_db
from app.db import queries as q
from app.pipeline import run_fetch, run_reprocess_job, FetchResult, _make_fetcher
from app.pipeline import run_add_job, run_reevaluate_job
from app.fetchers.finn import FinnListingFetcher
from app.fetchers.generic_listing import GenericListingFetcher
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
    sid = q.insert_source(conn, "test", "http://example.com", "generic_listing")
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


def _mock_client(classify_resp, summarize_resp, evaluate_resp,
                  assess_fit_resp='{"interest": 0.8, "interest_reasoning": "Good fit", '
                                  '"attainability": 0.7, "attainability_reasoning": "Close match"}'):
    client = MagicMock()
    def create(**kwargs):
        system = kwargs["messages"][0]["content"].lower()
        choice = MagicMock()
        if "you classify" in system:
            choice.message.content = classify_resp
        elif "you screen" in system:
            choice.message.content = evaluate_resp
        elif "you assess" in system:
            choice.message.content = assess_fit_resp
        else:
            choice.message.content = summarize_resp
        return MagicMock(choices=[choice])
    client.chat.completions.create.side_effect = create
    return client


def test_run_add_job_stores_job_against_given_source(conn, source):
    client = _mock_client(
        '{"type": "job_posting", "reason": "full description"}',
        '{"title": "ML Engineer", "headline": "Great role", "summary": "Good ML role"}',
        '{"score": 0.9, "reasoning": "Great match"}',
    )
    messages, _ = _drain(
        run_add_job(conn, client, "llama3.2", source["id"], "http://example.com/job/1", "<p>We are hiring</p>")
    )
    jobs = q.get_jobs(conn)
    assert len(jobs) == 1
    assert jobs[0]["url"] == "http://example.com/job/1"
    assert jobs[0]["source_id"] == source["id"]
    assert jobs[0]["content_type"] == "job_posting"
    assert any("Classified as job_posting" in m for m in messages)


def test_run_add_job_marks_evaluation_complete(conn, source):
    client = _mock_client(
        '{"type": "job_posting", "reason": "full description"}',
        '{"title": "ML Engineer", "headline": "Great role", "summary": "Good ML role"}',
        '{"score": 0.9, "reasoning": "Great match"}',
    )
    _drain(
        run_add_job(conn, client, "llama3.2", source["id"], "http://example.com/job/1", "<p>We are hiring</p>")
    )
    job = q.get_jobs(conn)[0]
    assert job["evaluation_completed_at"] is not None


def test_run_add_job_irrelevant_content_not_persisted(conn, source):
    client = _mock_client(
        '{"type": "irrelevant", "reason": "not a job"}', "{}", "{}",
    )
    _drain(run_add_job(conn, client, "llama3.2", source["id"], "http://example.com/job/2", "<p>Buy socks now</p>"))
    assert q.get_jobs(conn) == []


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

    with patch("app.pipeline.GenericListingFetcher") as MockFetcher:
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


def test_run_fetch_lead_uses_organization_focused_summary(conn, source):
    raw_jobs = [RawJob(url="http://example.com/job/1", title="fallback", company="", raw_text="anyone know if Acme is hiring?")]
    client = _mock_client(
        classify_resp='{"type": "lead", "reason": "vague mention, no full posting"}',
        summarize_resp='{"organizations": ["Acme", "Globex"], "headline": "A couple of leads"}',
        evaluate_resp='{"score": 0.5, "reasoning": "Some relevance"}',
    )

    with patch("app.pipeline.GenericListingFetcher") as MockFetcher:
        MockFetcher.return_value.fetch.return_value = raw_jobs
        _drain(run_fetch(source, conn, client, "llama3.2", "browser-profile"))

    job = q.get_jobs(conn)[0]
    assert job["content_type"] == "lead"
    assert job["title"] == "Acme, Globex"
    # Leads retain the original message verbatim as their summary rather
    # than an AI-compressed rewrite.
    assert job["summary"] == "anyone know if Acme is hiring?"


def test_run_fetch_stores_published_at(conn, source):
    raw_jobs = [RawJob(
        url="http://example.com/job/1", title="T", company="C", raw_text="r",
        published_at="2026-07-01T00:00:00+00:00",
    )]
    client = _mock_client(
        classify_resp='{"type": "lead", "reason": "vague mention, no full posting"}',
        summarize_resp='{"organizations": ["Acme"], "headline": "A lead"}',
        evaluate_resp='{"score": 0.5, "reasoning": "Some relevance"}',
    )

    with patch("app.pipeline.GenericListingFetcher") as MockFetcher:
        MockFetcher.return_value.fetch.return_value = raw_jobs
        _drain(run_fetch(source, conn, client, "llama3.2", "browser-profile"))

    job = q.get_jobs(conn)[0]
    assert job["published_at"] == "2026-07-01T00:00:00+00:00"


def test_run_fetch_skips_existing_url(conn, source):
    q.insert_job(conn, source_id=source["id"], url="http://example.com/job/1", title="T", company="C", raw_text="r")
    raw_jobs = [RawJob(url="http://example.com/job/1", title="ML Eng", company="Acme", raw_text="text")]
    client = MagicMock()

    with patch("app.pipeline.GenericListingFetcher") as MockFetcher:
        MockFetcher.return_value.fetch.return_value = raw_jobs
        messages, result = _drain(run_fetch(source, conn, client, "llama3.2", "browser-profile"))

    assert result.jobs_found == 1
    assert result.jobs_new == 0
    assert any("Skipping duplicate" in m for m in messages)


def test_run_fetch_records_fetch_run(conn, source):
    with patch("app.pipeline.GenericListingFetcher") as MockFetcher:
        MockFetcher.return_value.fetch.return_value = []
        _drain(run_fetch(source, conn, client=MagicMock(), model="llama3.2", profile_dir="bp"))

    runs = q.get_recent_fetch_runs(conn)
    assert len(runs) == 1
    assert runs[0]["completed_at"] is not None


def test_run_fetch_sets_auth_error_on_slack_auth_required(conn, source):
    from app.fetchers.slack import SlackAuthRequired

    with patch("app.pipeline.GenericListingFetcher") as MockFetcher:
        MockFetcher.return_value.fetch.side_effect = SlackAuthRequired("no valid session")
        _drain(run_fetch(source, conn, client=MagicMock(), model="llama3.2", profile_dir="bp"))

    run = q.get_recent_fetch_runs(conn)[0]
    assert run["auth_error"] == 1
    assert run["error"] == "no valid session"


def test_run_fetch_does_not_set_auth_error_on_other_exceptions(conn, source):
    with patch("app.pipeline.GenericListingFetcher") as MockFetcher:
        MockFetcher.return_value.fetch.side_effect = RuntimeError("timeout")
        _drain(run_fetch(source, conn, client=MagicMock(), model="llama3.2", profile_dir="bp"))

    run = q.get_recent_fetch_runs(conn)[0]
    assert run["auth_error"] == 0
    assert run["error"] == "timeout"


def test_run_fetch_irrelevant_not_persisted(conn, source):
    raw_jobs = [RawJob(url="http://example.com/job/1", title="", company="", raw_text="meetup next week")]
    client = MagicMock()
    choice = MagicMock()
    choice.message.content = '{"type": "irrelevant", "reason": "not a job"}'
    client.chat.completions.create.return_value = MagicMock(choices=[choice])

    with patch("app.pipeline.GenericListingFetcher") as MockFetcher:
        MockFetcher.return_value.fetch.return_value = raw_jobs
        messages, result = _drain(run_fetch(source, conn, client, "llama3.2", "browser-profile"))

    assert q.get_jobs(conn) == []
    assert result.jobs_new == 0
    assert client.chat.completions.create.call_count == 1
    assert any("Classified as irrelevant" in m for m in messages)


def test_run_fetch_yields_progress_and_logs_each_line(conn, source, caplog):
    raw_jobs = [RawJob(url="http://example.com/job/1", title="ML Eng", company="Acme", raw_text="<p>We are hiring</p>")]
    client = _mock_client(
        '{"type": "job_posting", "reason": "full description"}',
        '{"title": "ML Engineer - Remote @ Acme", "headline": "Great remote ML role", "summary": "Good ML role"}',
        '{"score": 0.9, "reasoning": "Great match"}',
    )

    with patch("app.pipeline.GenericListingFetcher") as MockFetcher, caplog.at_level("INFO", logger="job_seek"):
        MockFetcher.return_value.fetch.return_value = raw_jobs
        messages, result = _drain(run_fetch(source, conn, client, "llama3.2", "browser-profile"))

    assert any("Starting fetch" in m for m in messages)
    assert any("job_posting" in m for m in messages)
    assert any("Fetch complete" in m for m in messages)
    assert messages == [r.message for r in caplog.records]


def test_make_fetcher_dispatches_finn_listing(conn):
    source = {"id": 1, "name": "finn.no", "url": "http://x", "fetcher_type": "finn_listing"}
    fetcher = _make_fetcher(source, "browser-profile", conn)
    assert isinstance(fetcher, FinnListingFetcher)


def test_make_fetcher_finn_listing_passes_known_urls(conn):
    sid = q.insert_source(conn, "test", "http://example.com", "generic_listing")
    q.insert_job(conn, source_id=sid, url="http://known/1", title="T", company="C", raw_text="r")
    source = {"id": 1, "name": "finn.no", "url": "http://x", "fetcher_type": "finn_listing"}

    fetcher = _make_fetcher(source, "browser-profile", conn)

    assert fetcher._known_urls == frozenset({"http://known/1"})


def test_make_fetcher_slack_passes_known_urls(conn):
    sid = q.insert_source(conn, "test", "http://example.com", "generic_listing")
    q.insert_job(conn, source_id=sid, url="http://known/1", title="T", company="C", raw_text="r")
    source = {
        "id": 1,
        "name": "Example Slack",
        "url": "https://example-workspace.slack.com/archives/C0EXAMPLE1",
        "fetcher_type": "slack",
    }

    fetcher = _make_fetcher(source, "browser-profile", conn)

    assert fetcher._known_urls == frozenset({"http://known/1"})


def test_make_fetcher_raises_for_unsupported_fetcher_type(conn):
    source = {"id": 1, "name": "test", "url": "http://x", "fetcher_type": "manual"}
    with pytest.raises(ValueError):
        _make_fetcher(source, "browser-profile", conn)


def test_make_fetcher_dispatches_generic_listing(conn):
    source = {"id": 1, "name": "Careers", "url": "http://x", "fetcher_type": "generic_listing"}
    client = MagicMock()
    fetcher = _make_fetcher(source, "browser-profile", conn, client, "llama3.2")
    assert isinstance(fetcher, GenericListingFetcher)


def test_make_fetcher_generic_listing_passes_known_urls_and_client(conn):
    sid = q.insert_source(conn, "test", "http://example.com", "generic_listing")
    q.insert_job(conn, source_id=sid, url="http://known/1", title="T", company="C", raw_text="r")
    source = {"id": 1, "name": "Careers", "url": "http://x", "fetcher_type": "generic_listing"}
    client = MagicMock()

    fetcher = _make_fetcher(source, "browser-profile", conn, client, "llama3.2")

    assert fetcher._known_urls == frozenset({"http://known/1"})
    assert fetcher._client is client
    assert fetcher._model == "llama3.2"


def test_make_fetcher_slack_still_works_without_client_or_model(conn):
    source = {
        "id": 1,
        "name": "Example Slack",
        "url": "https://example-workspace.slack.com/archives/C0EXAMPLE1",
        "fetcher_type": "slack",
    }
    fetcher = _make_fetcher(source, "browser-profile", conn)
    assert type(fetcher).__name__ == "SlackFetcher"


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

    with patch("app.pipeline.GenericListingFetcher") as MockFetcher:
        MockFetcher.return_value.fetch.return_value = raw_jobs
        messages, result = _drain(run_fetch(source, conn, client, "llama3.2", "browser-profile"))

    job_id = q.get_jobs(conn)[0]["id"]
    assert q.get_job_score(conn, job_id, first_id) is not None
    assert q.get_job_score(conn, job_id, second_id) is not None
    assert sum("Scored" in m for m in messages) == 2


def test_run_fetch_with_no_scenarios_still_summarizes(conn):
    source_id = q.insert_source(conn, "test", "http://example.com", "generic_listing")
    source_dict = q.get_source(conn, source_id)
    raw_jobs = [RawJob(url="http://example.com/job/1", title="ML Eng", company="Acme", raw_text="<p>We are hiring</p>")]
    client = _mock_client(
        '{"type": "job_posting", "reason": "full description"}',
        '{"title": "ML Engineer - Remote @ Acme", "headline": "Great remote ML role", "summary": "Good ML role"}',
        '{"score": 0.9, "reasoning": "Great match"}',
    )

    with patch("app.pipeline.GenericListingFetcher") as MockFetcher:
        MockFetcher.return_value.fetch.return_value = raw_jobs
        messages, result = _drain(run_fetch(source_dict, conn, client, "llama3.2", "browser-profile"))

    job = q.get_jobs(conn)[0]
    assert job["content_type"] == "job_posting"
    assert job["summary"] == "Good ML role"
    assert job["fit_score"] is None
    assert not any("Scored" in m for m in messages)


def test_run_fetch_runs_stage_two_once_when_gate_passes(conn, source):
    raw_jobs = [RawJob(url="http://example.com/job/1", title="ML Eng", company="Acme", raw_text="<p>hi</p>")]
    client = _mock_client(
        '{"type": "job_posting", "reason": "full description"}',
        '{"title": "ML Engineer - Remote @ Acme", "headline": "Great remote ML role", "summary": "Good ML role"}',
        '{"score": 0.9, "reasoning": "Great match"}',  # 0.9 >= default gate_threshold 0.7
    )

    with patch("app.pipeline.GenericListingFetcher") as MockFetcher:
        MockFetcher.return_value.fetch.return_value = raw_jobs
        messages, _ = _drain(run_fetch(source, conn, client, "llama3.2", "browser-profile"))

    job = q.get_jobs(conn)[0]
    assert job["interest_score"] == pytest.approx(0.8)
    assert job["attainability_score"] == pytest.approx(0.7)
    assert job["fit_score"] == pytest.approx(0.75)
    assert job["profile_version_hash"]
    assert sum("Fit" in m for m in messages) == 1


def test_run_fetch_skips_stage_two_when_gate_not_passed(conn, source):
    q.update_scenario(conn, q.get_scenarios(conn)[0]["id"], name="Remote ML", description="", gate_threshold=0.95)
    raw_jobs = [RawJob(url="http://example.com/job/1", title="ML Eng", company="Acme", raw_text="<p>hi</p>")]
    client = _mock_client(
        '{"type": "job_posting", "reason": "full description"}',
        '{"title": "ML Engineer - Remote @ Acme", "headline": "Great remote ML role", "summary": "Good ML role"}',
        '{"score": 0.9, "reasoning": "Great match"}',  # 0.9 < 0.95, gate fails
    )

    with patch("app.pipeline.GenericListingFetcher") as MockFetcher:
        MockFetcher.return_value.fetch.return_value = raw_jobs
        messages, _ = _drain(run_fetch(source, conn, client, "llama3.2", "browser-profile"))

    job = q.get_jobs(conn)[0]
    assert job["fit_score"] is None
    assert not any("Fit" in m for m in messages)


def test_run_fetch_stores_ai_title_and_headline(conn, source):
    raw_jobs = [RawJob(url="http://example.com/job/1", title="scraped title", company="Acme", raw_text="<p>hi</p>")]
    client = _mock_client(
        '{"type": "job_posting", "reason": "full description"}',
        '{"title": "ML Engineer - Remote @ Acme", "headline": "Great remote ML role", "summary": "Good ML role"}',
        '{"score": 0.9, "reasoning": "Great match"}',
    )
    with patch("app.pipeline.GenericListingFetcher") as MockFetcher:
        MockFetcher.return_value.fetch.return_value = raw_jobs
        _drain(run_fetch(source, conn, client, "llama3.2", "browser-profile"))

    job = q.get_jobs(conn)[0]
    assert job["title"] == "ML Engineer - Remote @ Acme"
    assert job["headline"] == "Great remote ML role"


def test_run_reprocess_job_reruns_full_pipeline(conn, source):
    jid = q.insert_job(
        conn, source_id=source["id"], url="http://example.com/job/1",
        title="stale title", company="Acme", raw_text="<p>We are hiring</p>",
    )
    q.update_job_pipeline(
        conn, jid, simplified_content="stale simplified", content_type="job_posting",
        headline="stale headline", summary="stale summary",
    )
    scenario_id = q.get_scenarios(conn)[0]["id"]
    q.upsert_job_score(conn, jid, scenario_id, 0.1, "stale reasoning", "stale-hash")
    q.update_job_feedback(conn, jid, "rejected", "not a fit")

    client = _mock_client(
        '{"type": "job_posting", "reason": "full description"}',
        '{"title": "ML Engineer - Remote @ Acme", "headline": "Great remote ML role", "summary": "Good ML role"}',
        '{"score": 0.9, "reasoning": "Great match"}',
    )
    job = q.get_job(conn, jid)
    scenarios = q.get_scenarios(conn)
    profile = q.get_profile(conn)

    messages, _ = _drain(run_reprocess_job(conn, client, "llama3.2", job, scenarios, profile))

    updated = q.get_job(conn, jid)
    assert updated["status"] == "new"
    assert updated["feedback_note"] == "not a fit"
    assert updated["simplified_content"] != "stale simplified"
    assert updated["summary"] == "Good ML role"
    assert updated["headline"] == "Great remote ML role"
    assert updated["title"] == "ML Engineer - Remote @ Acme"
    score = q.get_job_score(conn, jid, scenario_id)
    assert score["relevance_score"] == 0.9
    assert any("Reprocessing" in m for m in messages)
    assert any("Scored 0.9" in m for m in messages)


def test_run_reprocess_job_irrelevant_deletes_job(conn, source):
    jid = q.insert_job(
        conn, source_id=source["id"], url="http://example.com/job/1",
        title="T", company="C", raw_text="meetup announcement",
    )
    client = MagicMock()
    choice = MagicMock()
    choice.message.content = '{"type": "irrelevant", "reason": "not a job"}'
    client.chat.completions.create.return_value = MagicMock(choices=[choice])
    job = q.get_job(conn, jid)
    scenarios = q.get_scenarios(conn)
    profile = q.get_profile(conn)

    messages, _ = _drain(run_reprocess_job(conn, client, "llama3.2", job, scenarios, profile))

    assert q.get_job(conn, jid) is None
    assert q.get_job_scores(conn, jid) == []
    assert any("Removed as not job-related" in m for m in messages)


def test_run_reevaluate_job_rescopes_and_reassesses_without_moving_status(conn, source):
    jid = q.insert_job(
        conn, source_id=source["id"], url="http://example.com/job/1",
        title="Old Title", company="Acme", raw_text="raw text",
    )
    q.update_job_pipeline(
        conn, jid, simplified_content="clean text", content_type="job_posting",
        title="Old Title", headline="old hook", summary="old summary",
    )
    scenario_id = q.get_scenarios(conn)[0]["id"]
    q.upsert_job_score(conn, jid, scenario_id, 0.3, "old reasoning", "old-hash")
    q.update_job_fit(conn, jid, 0.2, "old interest", 0.2, "old attainability", "old-phash")
    q.update_job_feedback(conn, jid, "accepted", "great fit")
    q.mark_job_gate_override(conn, jid)
    q.upsert_scenario_feedback(conn, jid, scenario_id, "great candidate", "higher")

    job = q.get_job(conn, jid)
    scenarios = q.get_scenarios(conn)
    profile = q.get_profile(conn)

    with patch("app.pipeline.summarize", return_value=("New Title", "new hook", "new summary")), \
         patch("app.pipeline.evaluate", return_value=(0.9, "now a great match")), \
         patch("app.pipeline.assess_fit", return_value={
             "interest": 0.8, "interest_reasoning": "strong interest",
             "attainability": 0.7, "attainability_reasoning": "reachable",
         }):
        messages, _ = _drain(run_reevaluate_job(conn, MagicMock(), "llama3.2", job, scenarios, profile))

    updated = q.get_job(conn, jid)
    assert updated["status"] == "accepted"
    assert updated["title"] == "New Title"
    assert updated["headline"] == "new hook"
    assert updated["summary"] == "new summary"
    assert updated["interest_score"] == pytest.approx(0.8)
    assert updated["attainability_score"] == pytest.approx(0.7)
    assert updated["gate_override"] == 1
    assert updated["evaluation_completed_at"] is not None
    score = q.get_job_score(conn, jid, scenario_id)
    assert score["relevance_score"] == pytest.approx(0.9)
    scores = q.get_job_scores(conn, jid)
    scenario_feedback_row = next(s for s in scores if s["scenario_id"] == scenario_id)
    assert scenario_feedback_row["feedback_note"] == "great candidate"
    assert scenario_feedback_row["feedback_direction"] == "higher"
    assert any("Scored 0.9" in m for m in messages)
    assert any("Fit 0.80/0.70" in m for m in messages)


def test_run_reevaluate_job_always_recomputes_even_when_unchanged(conn, source):
    jid = q.insert_job(
        conn, source_id=source["id"], url="http://example.com/job/1",
        title="T", company="C", raw_text="raw",
    )
    q.update_job_pipeline(conn, jid, simplified_content="clean text", content_type="job_posting", summary="s")
    scenarios = q.get_scenarios(conn)
    profile = q.get_profile(conn)

    with patch("app.pipeline.summarize", return_value=("T", "h", "s")), \
         patch("app.pipeline.evaluate", return_value=(0.5, "reason")) as mock_evaluate, \
         patch("app.pipeline.assess_fit", return_value={
             "interest": 0.5, "interest_reasoning": "r",
             "attainability": 0.5, "attainability_reasoning": "r",
         }) as mock_assess:
        _drain(run_reevaluate_job(conn, MagicMock(), "llama3.2", q.get_job(conn, jid), scenarios, profile))
        _drain(run_reevaluate_job(conn, MagicMock(), "llama3.2", q.get_job(conn, jid), scenarios, profile))

    assert mock_evaluate.call_count == 2
    assert mock_assess.call_count == 2


def test_run_reevaluate_job_skips_rejected_job(conn, source):
    jid = q.insert_job(
        conn, source_id=source["id"], url="http://example.com/job/1",
        title="T", company="C", raw_text="raw",
    )
    q.update_job_pipeline(conn, jid, simplified_content="clean text", content_type="job_posting", summary="s")
    q.update_job_feedback(conn, jid, "rejected", "no")
    job = q.get_job(conn, jid)
    scenarios = q.get_scenarios(conn)
    profile = q.get_profile(conn)

    with patch("app.pipeline.summarize") as mock_summarize, \
         patch("app.pipeline.evaluate") as mock_evaluate, \
         patch("app.pipeline.assess_fit") as mock_assess_fit:
        messages, _ = _drain(run_reevaluate_job(conn, MagicMock(), "llama3.2", job, scenarios, profile))

    mock_summarize.assert_not_called()
    mock_evaluate.assert_not_called()
    mock_assess_fit.assert_not_called()
    assert any("Skipped" in m for m in messages)
    assert q.get_job(conn, jid)["status"] == "rejected"


def test_run_reevaluate_job_skips_trash_job(conn, source):
    jid = q.insert_job(
        conn, source_id=source["id"], url="http://example.com/job/1",
        title="T", company="C", raw_text="raw",
    )
    q.update_job_pipeline(conn, jid, simplified_content="clean text", content_type="job_posting", summary="s")
    q.update_job_feedback(conn, jid, "trash", "no")
    job = q.get_job(conn, jid)
    scenarios = q.get_scenarios(conn)
    profile = q.get_profile(conn)

    with patch("app.pipeline.summarize") as mock_summarize:
        messages, _ = _drain(run_reevaluate_job(conn, MagicMock(), "llama3.2", job, scenarios, profile))

    mock_summarize.assert_not_called()
    assert any("Skipped" in m for m in messages)
    assert q.get_job(conn, jid)["status"] == "trash"


def test_run_reevaluate_job_skips_non_scoreable_content_type(conn, source):
    jid = q.insert_job(
        conn, source_id=source["id"], url="http://example.com/job/1",
        title="T", company="C", raw_text="raw",
    )
    q.update_job_pipeline(conn, jid, simplified_content="", content_type="error")
    job = q.get_job(conn, jid)
    scenarios = q.get_scenarios(conn)
    profile = q.get_profile(conn)

    with patch("app.pipeline.summarize") as mock_summarize:
        messages, _ = _drain(run_reevaluate_job(conn, MagicMock(), "llama3.2", job, scenarios, profile))

    mock_summarize.assert_not_called()
    assert any("Skipped" in m for m in messages)


def test_run_fetch_keeps_scraped_title_when_ai_title_empty(conn, source):
    raw_jobs = [RawJob(url="http://example.com/job/1", title="scraped title", company="Acme", raw_text="<p>hi</p>")]
    client = _mock_client(
        '{"type": "job_posting", "reason": "full description"}',
        '{"title": "", "headline": "", "summary": "Good ML role"}',
        '{"score": 0.9, "reasoning": "Great match"}',
    )
    with patch("app.pipeline.GenericListingFetcher") as MockFetcher:
        MockFetcher.return_value.fetch.return_value = raw_jobs
        _drain(run_fetch(source, conn, client, "llama3.2", "browser-profile"))

    job = q.get_jobs(conn)[0]
    assert job["title"] == "scraped title"
    assert job["headline"] == ""


from app.pipeline import run_pass_as_new


def test_run_pass_as_new_marks_override_and_assesses_fit(conn, source):
    scenario_id = q.get_scenarios(conn)[0]["id"]
    jid = q.insert_job(conn, source_id=source["id"], url="http://example.com/job/1", title="T", company="C", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="job_posting", summary="Good role")
    q.upsert_job_score(conn, jid, scenario_id, 0.2, "weak", "hash1")  # below default 0.7 gate

    client = MagicMock()
    choice = MagicMock()
    choice.message.content = '{"interest": 0.8, "interest_reasoning": "a", "attainability": 0.6, "attainability_reasoning": "b"}'
    client.chat.completions.create.return_value = MagicMock(choices=[choice])
    profile = q.get_profile(conn)
    job = q.get_job(conn, jid)

    messages, _ = _drain(run_pass_as_new(conn, client, "llama3.2", job, profile))

    updated = q.get_job(conn, jid)
    assert updated["gate_override"] == 1
    assert updated["interest_score"] == pytest.approx(0.8)
    assert updated["fit_score"] == pytest.approx(0.7)
    # Original gate score/reasoning must survive untouched.
    score = q.get_job_score(conn, jid, scenario_id)
    assert score["relevance_score"] == pytest.approx(0.2)
    assert score["score_reasoning"] == "weak"
    assert any("Fit 0.80/0.60" in m for m in messages)


from app.pipeline import run_reassess_fit


def test_run_reassess_fit_updates_gate_passed_jobs(conn, source):
    scenario_id = q.get_scenarios(conn)[0]["id"]
    jid = q.insert_job(conn, source_id=source["id"], url="http://example.com/job/1", title="T", company="C", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="job_posting", summary="Good role")
    q.upsert_job_score(conn, jid, scenario_id, 0.9, "great", "hash1")  # passes default 0.7 gate

    client = MagicMock()
    choice = MagicMock()
    choice.message.content = '{"interest": 0.8, "interest_reasoning": "a", "attainability": 0.6, "attainability_reasoning": "b"}'
    client.chat.completions.create.return_value = MagicMock(choices=[choice])

    messages, updated_count = _drain(run_reassess_fit(conn, client, "llama3.2"))

    assert updated_count == 1
    job = q.get_job(conn, jid)
    assert job["interest_score"] == pytest.approx(0.8)
    assert job["fit_score"] == pytest.approx(0.7)
    assert any("Recomputing fit scores for 1 job" in m for m in messages)


def test_run_reassess_fit_includes_accepted_and_gate_failed_jobs(conn, source):
    scenario_id = q.get_scenarios(conn)[0]["id"]
    accepted_id = q.insert_job(conn, source_id=source["id"], url="http://example.com/job/1", title="T", company="C", raw_text="r")
    q.update_job_pipeline(conn, accepted_id, simplified_content="clean", content_type="job_posting", summary="Good role")
    q.upsert_job_score(conn, accepted_id, scenario_id, 0.9, "great", "hash1")
    q.update_job_feedback(conn, accepted_id, "accepted", "")

    gate_failed_id = q.insert_job(conn, source_id=source["id"], url="http://example.com/job/2", title="U", company="C", raw_text="r")
    q.update_job_pipeline(conn, gate_failed_id, simplified_content="clean", content_type="job_posting", summary="Weak role")
    q.upsert_job_score(conn, gate_failed_id, scenario_id, 0.3, "weak", "hash1")  # below default 0.7 gate

    client = MagicMock()
    choice = MagicMock()
    choice.message.content = '{"interest": 0.8, "interest_reasoning": "a", "attainability": 0.6, "attainability_reasoning": "b"}'
    client.chat.completions.create.return_value = MagicMock(choices=[choice])

    messages, updated_count = _drain(run_reassess_fit(conn, client, "llama3.2"))

    assert updated_count == 2
    assert q.get_job(conn, accepted_id)["interest_score"] == pytest.approx(0.8)
    assert q.get_job(conn, gate_failed_id)["interest_score"] == pytest.approx(0.8)


def test_run_reassess_fit_skips_already_current_profile_hash(conn, source):
    scenario_id = q.get_scenarios(conn)[0]["id"]
    jid = q.insert_job(conn, source_id=source["id"], url="http://example.com/job/1", title="T", company="C", raw_text="r")
    q.update_job_pipeline(conn, jid, simplified_content="clean", content_type="job_posting", summary="Good role")
    q.upsert_job_score(conn, jid, scenario_id, 0.9, "great", "hash1")

    client = MagicMock()
    choice = MagicMock()
    choice.message.content = '{"interest": 0.8, "interest_reasoning": "a", "attainability": 0.6, "attainability_reasoning": "b"}'
    client.chat.completions.create.return_value = MagicMock(choices=[choice])

    _drain(run_reassess_fit(conn, client, "llama3.2"))  # first pass: assesses
    messages, updated_count = _drain(run_reassess_fit(conn, client, "llama3.2"))  # second pass: profile unchanged

    assert updated_count == 0
    assert "skipping 1 already current" in "".join(messages)


def test_run_fetch_stores_canonical_job_urls(conn):
    from app import pipeline

    sid = q.insert_source(conn, "S", "https://ex.com", "generic_listing")
    src = q.get_source(conn, sid)

    class _F:
        def fetch(self):
            return [RawJob(url="https://ex.com/jobs/9?refId=abc&utm_source=x",
                           title="", company="", raw_text="x" * 300)]

    with patch.object(pipeline, "_make_fetcher", return_value=_F()), \
         patch.object(pipeline, "_ingest_posting", return_value=iter(())):
        _drain(pipeline.run_fetch(src, conn, MagicMock(), "m", "/tmp"))

    assert q.get_all_job_urls(conn) == frozenset({"https://ex.com/jobs/9"})


def test_run_fetch_dedups_against_canonical_known_url(conn):
    from app import pipeline

    sid = q.insert_source(conn, "S", "https://ex.com", "generic_listing")
    src = q.get_source(conn, sid)
    q.insert_job(conn, source_id=sid, url="https://ex.com/jobs/9", title="", company="", raw_text="x" * 300)

    class _F:
        def fetch(self):
            return [RawJob(url="https://ex.com/jobs/9?trackingId=zzz",
                           title="", company="", raw_text="x" * 300)]

    with patch.object(pipeline, "_make_fetcher", return_value=_F()), \
         patch.object(pipeline, "_ingest_posting", return_value=iter(())) as ingest:
        _drain(pipeline.run_fetch(src, conn, MagicMock(), "m", "/tmp"))

    ingest.assert_not_called()
