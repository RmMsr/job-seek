from __future__ import annotations
import sqlite3
from dataclasses import dataclass
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


@dataclass
class FetchResult:
    source_id: int
    run_id: int
    jobs_found: int
    jobs_new: int
    error: str | None


def _make_fetcher(source: dict, profile_dir: str):
    ft = source["fetcher_type"]
    if ft == "http":
        return HttpFetcher(source)
    if ft == "slack":
        return SlackFetcher(source, profile_dir)
    return PlaywrightFetcher(source, profile_dir)


def run_fetch(
    source: dict,
    conn: sqlite3.Connection,
    client: openai.OpenAI,
    model: str,
    profile_dir: str,
) -> FetchResult:
    run_id = q.start_fetch_run(conn, source["id"])
    try:
        fetcher = _make_fetcher(source, profile_dir)
        raw_jobs: list[RawJob] = fetcher.fetch()
        jobs_found = len(raw_jobs)
        jobs_new = 0

        profile = q.get_profile(conn)
        scenario = q.get_active_scenario(conn)
        criteria = q.get_criteria(conn, scenario["id"]) if scenario else []

        for raw in raw_jobs:
            if q.url_exists(conn, raw.url):
                continue
            job_id = q.insert_job(
                conn,
                source_id=source["id"],
                url=raw.url,
                title=raw.title,
                company=raw.company,
                raw_text=raw.raw_text,
            )
            jobs_new += 1
            simplified = simplify(raw.raw_text)
            is_slack = source["fetcher_type"] == "slack"
            content_type, _ = classify(client, model, simplified, is_slack=is_slack)

            if content_type in ("job_posting", "lead") and scenario:
                job_summary = summarize(client, model, simplified)
                score, reasoning = evaluate(client, model, profile, scenario, criteria, job_summary)
                q.update_job_pipeline(
                    conn, job_id,
                    simplified_content=simplified,
                    content_type=content_type,
                    summary=job_summary,
                    relevance_score=score,
                    score_reasoning=reasoning,
                    scenario_id=scenario["id"],
                )
            else:
                q.update_job_pipeline(
                    conn, job_id,
                    simplified_content=simplified,
                    content_type=content_type,
                )

        q.complete_fetch_run(conn, run_id, jobs_found=jobs_found, jobs_new=jobs_new)
        return FetchResult(source_id=source["id"], run_id=run_id, jobs_found=jobs_found, jobs_new=jobs_new, error=None)
    except Exception as exc:
        q.complete_fetch_run(conn, run_id, jobs_found=0, jobs_new=0, error=str(exc))
        return FetchResult(source_id=source["id"], run_id=run_id, jobs_found=0, jobs_new=0, error=str(exc))
