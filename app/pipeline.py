from __future__ import annotations
import logging
import sqlite3
from dataclasses import dataclass
from typing import Generator
import openai
from app.db import queries as q
from app.ai.simplify import simplify
from app.ai.classify import classify
from app.ai.summarize import summarize
from app.ai.evaluate import evaluate
from app.ai.assess_fit import assess_fit
from app.fetchers.base import RawJob
from app.fetchers.slack import SlackFetcher, SlackAuthRequired
from app.fetchers.finn import FinnListingFetcher
from app.fetchers.generic_listing import GenericListingFetcher
from app.scenario_version import compute_version_hash, compute_profile_hash

logger = logging.getLogger("job_seek")


@dataclass
class FetchResult:
    source_id: int
    run_id: int
    jobs_found: int
    jobs_new: int
    error: str | None


def _make_fetcher(
    source: dict,
    profile_dir: str,
    conn: sqlite3.Connection,
    client: openai.OpenAI | None = None,
    model: str | None = None,
):
    ft = source["fetcher_type"]
    if ft == "slack":
        return SlackFetcher(source, known_urls=q.get_all_job_urls(conn))
    if ft == "finn_listing":
        return FinnListingFetcher(source, known_urls=q.get_all_job_urls(conn))
    if ft == "generic_listing":
        return GenericListingFetcher(source, client, model, known_urls=q.get_all_job_urls(conn))
    raise ValueError(f"Unsupported fetcher_type: {ft!r}")


def _progress(msg: str) -> str:
    logger.info(msg)
    return msg


def _ingest_posting(
    conn: sqlite3.Connection,
    client: openai.OpenAI,
    model: str,
    job_id: int,
    raw_text: str,
    fallback_title: str,
    is_slack: bool,
    profile: str,
    scenarios: list[dict],
    *,
    url: str,
    progress_prefix: str = "",
) -> Generator[str, None, None]:
    simplified = simplify(raw_text)
    content_type, _ = classify(client, model, simplified, is_slack=is_slack)
    yield _progress(f"{progress_prefix}Classified as {content_type}: {url}")

    if content_type in ("job_posting", "lead"):
        ai_title, headline, job_summary = summarize(client, model, simplified, content_type=content_type)
        q.update_job_pipeline(
            conn, job_id,
            simplified_content=simplified,
            content_type=content_type,
            title=ai_title or fallback_title,
            headline=headline,
            summary=job_summary,
        )
        passed_gate = False
        for scenario in scenarios:
            criteria = q.get_criteria(conn, scenario["id"])
            score, reasoning = evaluate(client, model, scenario, criteria, job_summary)
            version_hash = compute_version_hash(scenario, criteria)
            q.upsert_job_score(conn, job_id, scenario["id"], score, reasoning, version_hash)
            if score >= scenario["gate_threshold"]:
                passed_gate = True
            yield _progress(f"{progress_prefix}Scored {score} for '{scenario['name']}': {url}")
        if passed_gate:
            result = assess_fit(client, model, profile, job_summary)
            q.update_job_fit(
                conn, job_id,
                result["interest"], result["interest_reasoning"],
                result["attainability"], result["attainability_reasoning"],
                compute_profile_hash(profile),
            )
            yield _progress(
                f"{progress_prefix}Fit {result['interest']:.2f}/{result['attainability']:.2f}: {url}"
            )
    elif content_type == "irrelevant":
        q.delete_job(conn, job_id)
    else:
        q.update_job_pipeline(conn, job_id, simplified_content=simplified, content_type=content_type)


def run_fetch(
    source: dict,
    conn: sqlite3.Connection,
    client: openai.OpenAI,
    model: str,
    profile_dir: str,
) -> Generator[str, None, FetchResult]:
    run_id = q.start_fetch_run(conn, source["id"])
    yield _progress(f"Starting fetch for '{source['name']}' ({source['fetcher_type']})")
    try:
        fetcher = _make_fetcher(source, profile_dir, conn, client, model)
        raw_jobs: list[RawJob] = fetcher.fetch()
        jobs_found = len(raw_jobs)
        jobs_new = 0

        yield _progress(f"Fetched {jobs_found} raw posting(s) from '{source['name']}'")

        profile = q.get_profile(conn)
        scenarios = q.get_scenarios(conn)

        for i, raw in enumerate(raw_jobs, start=1):
            if q.url_exists(conn, raw.url):
                yield _progress(f"[{i}/{jobs_found}] Skipping duplicate: {raw.url}")
                continue
            job_id = q.insert_job(
                conn,
                source_id=source["id"],
                url=raw.url,
                title=raw.title,
                company=raw.company,
                raw_text=raw.raw_text,
                published_at=raw.published_at,
            )
            jobs_new += 1
            is_slack = source["fetcher_type"] == "slack"
            yield from _ingest_posting(
                conn, client, model, job_id, raw.raw_text, raw.title, is_slack, profile, scenarios,
                url=raw.url, progress_prefix=f"[{i}/{jobs_found}] ",
            )
            if not q.job_exists(conn, job_id):
                jobs_new -= 1

        q.complete_fetch_run(conn, run_id, jobs_found=jobs_found, jobs_new=jobs_new)
        yield _progress(f"Fetch complete for '{source['name']}': {jobs_new} new / {jobs_found} found")
        return FetchResult(source_id=source["id"], run_id=run_id, jobs_found=jobs_found, jobs_new=jobs_new, error=None)
    except Exception as exc:
        q.complete_fetch_run(
            conn, run_id, jobs_found=0, jobs_new=0, error=str(exc),
            auth_error=isinstance(exc, SlackAuthRequired),
        )
        yield _progress(f"Fetch failed for '{source['name']}': {exc}")
        return FetchResult(source_id=source["id"], run_id=run_id, jobs_found=0, jobs_new=0, error=str(exc))


def run_add_job(
    conn: sqlite3.Connection,
    client: openai.OpenAI,
    model: str,
    source_id: int,
    url: str,
    raw_text: str,
) -> Generator[str, None, None]:
    job_id = q.insert_job(conn, source_id=source_id, url=url, title="", company="", raw_text=raw_text)
    profile = q.get_profile(conn)
    scenarios = q.get_scenarios(conn)
    yield from _ingest_posting(
        conn, client, model, job_id, raw_text, "", False, profile, scenarios, url=url,
    )


def run_reprocess_job(
    conn: sqlite3.Connection,
    client: openai.OpenAI,
    model: str,
    job: dict,
    scenarios: list[dict],
    profile: str,
    *,
    progress_prefix: str = "",
) -> Generator[str, None, None]:
    source = q.get_source(conn, job["source_id"])
    is_slack = bool(source and source["fetcher_type"] == "slack")
    q.reset_job(conn, job["id"])
    yield _progress(f"{progress_prefix}Reprocessing: {job['url']}")
    yield from _ingest_posting(
        conn, client, model, job["id"], job["raw_text"], job["title"], is_slack, profile, scenarios,
        url=job["url"], progress_prefix=progress_prefix,
    )
    if q.job_exists(conn, job["id"]):
        yield _progress(f"{progress_prefix}Reset complete: {job['url']}")
    else:
        yield _progress(f"{progress_prefix}Removed as not job-related: {job['url']}")


def run_pass_as_new(
    conn: sqlite3.Connection,
    client: openai.OpenAI,
    model: str,
    job: dict,
    profile: str,
) -> Generator[str, None, None]:
    q.mark_job_gate_override(conn, job["id"])
    yield _progress(f"Bypassing gate threshold: {job['url']}")
    result = assess_fit(client, model, profile, job["summary"])
    q.update_job_fit(
        conn, job["id"],
        result["interest"], result["interest_reasoning"],
        result["attainability"], result["attainability_reasoning"],
        compute_profile_hash(profile),
    )
    yield _progress(
        f"Fit {result['interest']:.2f}/{result['attainability']:.2f}: {job['url']}"
    )


def _eligible_for_reevaluation(conn: sqlite3.Connection, scenario: dict) -> tuple[list[dict], int, list[dict], str]:
    criteria = q.get_criteria(conn, scenario["id"])
    current_hash = compute_version_hash(scenario, criteria)
    eligible = [j for j in q.get_jobs(conn, status="new") if j["content_type"] in ("job_posting", "lead")]
    existing_hashes = q.get_job_score_hashes(conn, scenario["id"])
    to_evaluate = [j for j in eligible if existing_hashes.get(j["id"]) != current_hash]
    skipped = len(eligible) - len(to_evaluate)
    return to_evaluate, skipped, criteria, current_hash


def count_jobs_needing_reevaluation(conn: sqlite3.Connection, scenario: dict) -> int:
    to_evaluate, _, _, _ = _eligible_for_reevaluation(conn, scenario)
    return len(to_evaluate)


def run_reevaluate(
    conn: sqlite3.Connection,
    client: openai.OpenAI,
    model: str,
    scenario: dict,
    *,
    job_offset: int = 0,
    job_total: int | None = None,
    scenario_label: str = "",
) -> Generator[str, None, int]:
    to_evaluate, skipped, criteria, current_hash = _eligible_for_reevaluation(conn, scenario)
    total = job_total if job_total is not None else len(to_evaluate)

    msg = f"Re-evaluating {len(to_evaluate)} job(s) for scenario '{scenario['name']}'"
    if skipped:
        msg += f", skipping {skipped} already current"
    yield _progress(scenario_label + msg)

    for i, job in enumerate(to_evaluate, start=1):
        if job["simplified_content"]:
            ai_title, headline, new_summary = summarize(
                client, model, job["simplified_content"], content_type=job["content_type"]
            )
        else:
            ai_title, headline, new_summary = job["title"], job["headline"], job["summary"]
        score, reasoning = evaluate(client, model, scenario, criteria, new_summary)
        q.update_job_pipeline(
            conn, job["id"],
            simplified_content=job["simplified_content"],
            content_type=job["content_type"],
            title=ai_title or job["title"],
            headline=headline,
            summary=new_summary,
        )
        q.upsert_job_score(conn, job["id"], scenario["id"], score, reasoning, current_hash)
        yield _progress(f"{scenario_label}[{job_offset + i}/{total}] Re-scored {score}: {job['title'] or job['url']}")

    yield _progress(f"{scenario_label}Re-evaluation complete for '{scenario['name']}': {len(to_evaluate)} job(s) updated")
    return len(to_evaluate)


def run_reassess_fit(
    conn: sqlite3.Connection,
    client: openai.OpenAI,
    model: str,
) -> Generator[str, None, int]:
    profile = q.get_profile(conn)
    current_hash = compute_profile_hash(profile)
    eligible = [
        j for j in q.get_jobs(conn, status="new", gate_status="passed")
        if j["content_type"] in ("job_posting", "lead")
    ]
    to_assess = [j for j in eligible if j["profile_version_hash"] != current_hash]
    skipped = len(eligible) - len(to_assess)

    msg = f"Recomputing fit scores for {len(to_assess)} job(s)"
    if skipped:
        msg += f", skipping {skipped} already current"
    yield _progress(msg)

    for i, job in enumerate(to_assess, start=1):
        result = assess_fit(client, model, profile, job["summary"])
        q.update_job_fit(
            conn, job["id"],
            result["interest"], result["interest_reasoning"],
            result["attainability"], result["attainability_reasoning"],
            current_hash,
        )
        yield _progress(
            f"[{i}/{len(to_assess)}] Fit {result['interest']:.2f}/{result['attainability']:.2f}: "
            f"{job['title'] or job['url']}"
        )

    yield _progress(f"Fit recompute complete: {len(to_assess)} job(s) updated")
    return len(to_assess)
