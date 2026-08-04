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
from app.fetchers.base import RawJob
from app.fetchers.http import HttpFetcher
from app.fetchers.playwright_base import PlaywrightFetcher
from app.fetchers.slack import SlackFetcher
from app.fetchers.finn import FinnListingFetcher
from app.scenario_version import compute_version_hash

logger = logging.getLogger("job_seek")


@dataclass
class FetchResult:
    source_id: int
    run_id: int
    jobs_found: int
    jobs_new: int
    error: str | None


def _make_fetcher(source: dict, profile_dir: str, conn: sqlite3.Connection):
    ft = source["fetcher_type"]
    if ft == "http":
        return HttpFetcher(source)
    if ft == "slack":
        return SlackFetcher(source, profile_dir)
    if ft == "finn_listing":
        return FinnListingFetcher(source, known_urls=q.get_all_job_urls(conn))
    return PlaywrightFetcher(source, profile_dir)


def _progress(msg: str) -> str:
    logger.info(msg)
    return msg


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
        fetcher = _make_fetcher(source, profile_dir, conn)
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
            simplified = simplify(raw.raw_text)
            is_slack = source["fetcher_type"] == "slack"
            content_type, _ = classify(client, model, simplified, is_slack=is_slack)
            yield _progress(f"[{i}/{jobs_found}] Classified as {content_type}: {raw.url}")

            if content_type in ("job_posting", "lead"):
                ai_title, headline, job_summary = summarize(client, model, simplified)
                q.update_job_pipeline(
                    conn, job_id,
                    simplified_content=simplified,
                    content_type=content_type,
                    title=ai_title or raw.title,
                    headline=headline,
                    summary=job_summary,
                )
                for scenario in scenarios:
                    criteria = q.get_criteria(conn, scenario["id"])
                    score, reasoning = evaluate(client, model, profile, scenario, criteria, job_summary)
                    version_hash = compute_version_hash(scenario, criteria)
                    q.upsert_job_score(conn, job_id, scenario["id"], score, reasoning, version_hash)
                    yield _progress(f"[{i}/{jobs_found}] Scored {score} for '{scenario['name']}': {raw.url}")
            else:
                q.update_job_pipeline(
                    conn, job_id,
                    simplified_content=simplified,
                    content_type=content_type,
                )

        q.complete_fetch_run(conn, run_id, jobs_found=jobs_found, jobs_new=jobs_new)
        yield _progress(f"Fetch complete for '{source['name']}': {jobs_new} new / {jobs_found} found")
        return FetchResult(source_id=source["id"], run_id=run_id, jobs_found=jobs_found, jobs_new=jobs_new, error=None)
    except Exception as exc:
        q.complete_fetch_run(conn, run_id, jobs_found=0, jobs_new=0, error=str(exc))
        yield _progress(f"Fetch failed for '{source['name']}': {exc}")
        return FetchResult(source_id=source["id"], run_id=run_id, jobs_found=0, jobs_new=0, error=str(exc))


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
    profile = q.get_profile(conn)
    to_evaluate, skipped, criteria, current_hash = _eligible_for_reevaluation(conn, scenario)
    total = job_total if job_total is not None else len(to_evaluate)

    msg = f"Re-evaluating {len(to_evaluate)} job(s) for scenario '{scenario['name']}'"
    if skipped:
        msg += f", skipping {skipped} already current"
    yield _progress(scenario_label + msg)

    for i, job in enumerate(to_evaluate, start=1):
        if job["simplified_content"]:
            ai_title, headline, new_summary = summarize(client, model, job["simplified_content"])
        else:
            ai_title, headline, new_summary = job["title"], job["headline"], job["summary"]
        score, reasoning = evaluate(client, model, profile, scenario, criteria, new_summary)
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
