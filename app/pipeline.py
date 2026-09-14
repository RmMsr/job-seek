from __future__ import annotations
import logging
import sqlite3
import time
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Generator
import openai
from app.db import queries as q
from app.ai.simplify import simplify
from app.ai.classify import classify
from app.ai.revisit_check import revisit_check
from app.ai.summarize import summarize
from app.ai.evaluate import evaluate
from app.ai.assess_fit import assess_fit
from app.fetchers.content import (
    fetch_url_html, extract_text, has_enough_text, FetchError,
)
from app.fetchers.playwright_pool import render_html
from app.fetchers.base import RawJob
from app.fetchers.slack import SlackFetcher, SlackAuthRequired
from app.fetchers.finn import FinnListingFetcher
from app.fetchers.eawork import EaworkListingFetcher
from app.fetchers.generic_listing import GenericListingFetcher
from app.scenario_version import compute_version_hash, compute_profile_hash
from app.url_canon import canonicalize_url

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
    if ft == "eawork_listing":
        return EaworkListingFetcher(source, known_urls=q.get_all_job_urls(conn))
    if ft == "generic_listing":
        return GenericListingFetcher(source, client, model, known_urls=q.get_all_job_urls(conn))
    raise ValueError(f"Unsupported fetcher_type: {ft!r}")


def _progress(msg: str) -> str:
    logger.info(msg)
    return msg


def _evaluate_posting(
    conn: sqlite3.Connection,
    client: openai.OpenAI,
    model: str,
    job_id: int,
    *,
    simplified: str,
    content_type: str,
    fallback_title: str,
    is_slack: bool,
    profile: str,
    scenarios: list[dict],
    url: str,
    progress_prefix: str = "",
    preserve_existing_metadata: bool = False,
) -> Generator[str, None, None]:
    job_summary = summarize(
        client, model, simplified, content_type=content_type, raw_passthrough=is_slack,
        today=datetime.now(timezone.utc).date(),
    )
    # `preserve_existing_metadata` guards the *date*: a fetcher-supplied
    # published_at is authoritative and relative phrases must not be re-resolved
    # against a fresh "now". Company is separate — only eawork supplies one, so
    # finn/slack/generic jobs depend entirely on LLM extraction. Write the
    # extracted company whenever we don't already have one, but never let it
    # overwrite an existing value.
    has_company = bool((q.get_job(conn, job_id) or {}).get("company"))
    q.update_job_pipeline(
        conn, job_id,
        simplified_content=simplified,
        content_type=content_type,
        title=job_summary.title or fallback_title,
        headline=job_summary.headline,
        summary=job_summary.summary,
        company="" if has_company else job_summary.company,
        published_at="" if preserve_existing_metadata else job_summary.posted_date,
    )
    passed_gate = False
    for scenario in scenarios:
        criteria = q.get_criteria(conn, scenario["id"])
        score, reasoning = evaluate(client, model, scenario, criteria, job_summary.summary)
        version_hash = compute_version_hash(scenario, criteria)
        q.upsert_job_score(conn, job_id, scenario["id"], score, reasoning, version_hash)
        if score >= scenario["gate_threshold"]:
            passed_gate = True
        yield _progress(f"{progress_prefix}Scored {score} for '{scenario['name']}': {url}")
    if passed_gate:
        result = assess_fit(client, model, profile, job_summary.summary)
        q.update_job_fit(
            conn, job_id,
            result["interest"], result["interest_reasoning"],
            result["attainability"], result["attainability_reasoning"],
            compute_profile_hash(profile),
        )
        yield _progress(
            f"{progress_prefix}Fit {result['interest']:.2f}/{result['attainability']:.2f}: {url}"
        )
    q.mark_job_evaluation_complete(conn, job_id)


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
    preserve_existing_metadata: bool = False,
) -> Generator[str, None, None]:
    simplified = simplify(raw_text)
    content_type, _ = classify(client, model, simplified, is_slack=is_slack)
    yield _progress(f"{progress_prefix}Classified as {content_type}: {url}")

    if content_type in ("job_posting", "lead"):
        yield from _evaluate_posting(
            conn, client, model, job_id,
            simplified=simplified, content_type=content_type,
            fallback_title=fallback_title, is_slack=is_slack,
            profile=profile, scenarios=scenarios, url=url,
            progress_prefix=progress_prefix,
            preserve_existing_metadata=preserve_existing_metadata,
        )
    elif content_type == "irrelevant":
        q.delete_job(conn, job_id)
    else:
        q.update_job_pipeline(conn, job_id, simplified_content=simplified, content_type=content_type)


@dataclass
class RevisitOutcome:
    verdict: str          # "closed" | "changed" | "unchanged" | "skipped"
    reason: str | None = None


_REVISIT_RETRY_DELAY_SECONDS = 3.0

_REVISIT_CLOSED_REASON = {
    "gone": "this job posting is no longer available",
    "closed": "this job is no longer accepting applications",
}


def _closed_reason_for_fetch_error(exc: FetchError) -> str:
    msg = str(exc)
    if "404" in msg or "410" in msg:
        return "this job can no longer be found"
    return "this job posting could no longer be reached"


def run_revisit_job(
    conn: sqlite3.Connection,
    client: openai.OpenAI,
    model: str,
    job: dict,
    scenarios: list[dict],
    profile: str,
    *,
    progress_prefix: str = "",
) -> Generator[str, None, RevisitOutcome]:
    url = job["url"]
    if job.get("source_fetcher_type") == "slack":
        yield _progress(f"{progress_prefix}Skipping (Slack post, not re-fetchable): {url}")
        return RevisitOutcome("skipped")

    yield _progress(f"{progress_prefix}Revisiting: {url}")
    # Stamp now, before the outcome is known: every real revisit attempt advances
    # fetched_at so a capped sweep rotates on, even past a job that keeps erroring.
    q.mark_job_revisited(conn, job["id"])

    try:
        html = fetch_url_html(url)
    except FetchError:
        time.sleep(_REVISIT_RETRY_DELAY_SECONDS)
        try:
            html = fetch_url_html(url)
        except FetchError as exc:
            reason = _closed_reason_for_fetch_error(exc)
            q.mark_job_closed(conn, job["id"], reason)
            yield _progress(f"{progress_prefix}Closed ({exc}) — moved to Trash: {url}")
            return RevisitOutcome("closed", reason)

    text = extract_text(html)
    if not has_enough_text(text):
        rendered = render_html(url)
        if rendered:
            text = extract_text(rendered)
        if not has_enough_text(text):
            reason = "this job posting no longer shows any content"
            q.mark_job_closed(conn, job["id"], reason)
            yield _progress(f"{progress_prefix}Closed (no content) — moved to Trash: {url}")
            return RevisitOutcome("closed", reason)

    simplified = simplify(text)
    reference = (job["summary"] or job["simplified_content"] or "").strip()
    if not reference:
        # Nothing on file to compare against (an unprocessed / error job):
        # the page is reachable and has content, so treat it as still there.
        yield _progress(f"{progress_prefix}Still reachable (nothing on file to compare): {url}")
        return RevisitOutcome("unchanged")

    state, reason_detail = revisit_check(client, model, simplified, reference)

    if state in ("gone", "closed"):
        reason = _REVISIT_CLOSED_REASON[state]
        q.mark_job_closed(conn, job["id"], reason)
        yield _progress(
            f"{progress_prefix}Closed ({state}: {reason_detail or 'no detail'}) — moved to Trash: {url}"
        )
        return RevisitOutcome("closed", reason)

    if state == "changed":
        yield _progress(
            f"{progress_prefix}Still open, posting changed ({reason_detail or 'no detail'}) — re-evaluating: {url}"
        )
        detail = f" — {reason_detail}" if reason_detail else ""
        q.add_job_event(conn, job["id"], "revisit", f"Revisit: posting changed{detail}")
        q.update_job_raw_text(conn, job["id"], text)
        content_type = job["content_type"] if job["content_type"] in ("job_posting", "lead") else "job_posting"
        yield from _evaluate_posting(
            conn, client, model, job["id"],
            simplified=simplified, content_type=content_type,
            fallback_title=job["title"], is_slack=False,
            profile=profile, scenarios=scenarios, url=url,
            progress_prefix=progress_prefix, preserve_existing_metadata=True,
        )
        return RevisitOutcome("changed")

    yield _progress(f"{progress_prefix}Still open, unchanged: {url}")
    return RevisitOutcome("unchanged")


def run_fetch(
    source: dict,
    conn: sqlite3.Connection,
    client: openai.OpenAI,
    model: str,
    profile_dir: str,
    task_id: int | None = None,
) -> Generator[str, None, FetchResult]:
    run_id = q.start_fetch_run(conn, source["id"], task_id=task_id)
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
            raw_url = canonicalize_url(raw.url)
            if q.url_exists(conn, raw_url):
                yield _progress(f"[{i}/{jobs_found}] Skipping duplicate: {raw_url}")
                continue
            job_id = q.insert_job(
                conn,
                source_id=source["id"],
                url=raw_url,
                title=raw.title,
                company=raw.company,
                raw_text=raw.raw_text,
                published_at=raw.published_at,
            )
            jobs_new += 1
            is_slack = source["fetcher_type"] == "slack"
            yield from _ingest_posting(
                conn, client, model, job_id, raw.raw_text, raw.title, is_slack, profile, scenarios,
                url=raw_url, progress_prefix=f"[{i}/{jobs_found}] ",
                preserve_existing_metadata=raw.published_at is not None,
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
    url = canonicalize_url(url)
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
    before_scores = {s["scenario_id"]: s["relevance_score"] for s in q.get_job_scores(conn, job["id"])}
    before_fit = job["fit_score"]
    q.reset_job(conn, job["id"])
    yield _progress(f"{progress_prefix}Reprocessing: {job['url']}")
    yield from _ingest_posting(
        conn, client, model, job["id"], job["raw_text"], job["title"], is_slack, profile, scenarios,
        url=job["url"], progress_prefix=progress_prefix,
        preserve_existing_metadata=job["published_at"] is not None,
    )
    if q.job_exists(conn, job["id"]):
        for s in q.get_job_scores(conn, job["id"]):
            q.log_score_change(
                conn, job["id"],
                label=f'Re-scored "{s["scenario_name"]}"',
                old=before_scores.get(s["scenario_id"]),
                new=s["relevance_score"],
                threshold=s["scenario_gate_threshold"],
            )
        q.log_score_change(
            conn, job["id"], label="Fit re-assessed",
            old=before_fit, new=q.get_job(conn, job["id"])["fit_score"],
        )
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


def run_reevaluate_job(
    conn: sqlite3.Connection,
    client: openai.OpenAI,
    model: str,
    job: dict,
    scenarios: list[dict],
    profile: str,
    *,
    progress_prefix: str = "",
) -> Generator[str, None, None]:
    if job["status"] in ("rejected", "trash", "pending") or job["content_type"] not in ("job_posting", "lead"):
        yield _progress(f"{progress_prefix}Skipped (not eligible for re-evaluation): {job['url']}")
        return

    s = summarize(client, model, job["simplified_content"], content_type=job["content_type"])
    new_summary = s.summary
    q.update_job_pipeline(
        conn, job["id"],
        simplified_content=job["simplified_content"],
        content_type=job["content_type"],
        title=s.title or job["title"],
        headline=s.headline,
        summary=new_summary,
    )

    for scenario in scenarios:
        criteria = q.get_criteria(conn, scenario["id"])
        version_hash = compute_version_hash(scenario, criteria)
        score, reasoning = evaluate(client, model, scenario, criteria, new_summary)
        q.upsert_job_score(conn, job["id"], scenario["id"], score, reasoning, version_hash)
        yield _progress(f"{progress_prefix}Scored {score} for '{scenario['name']}': {job['url']}")

    result = assess_fit(client, model, profile, new_summary)
    q.update_job_fit(
        conn, job["id"],
        result["interest"], result["interest_reasoning"],
        result["attainability"], result["attainability_reasoning"],
        compute_profile_hash(profile),
    )
    yield _progress(
        f"{progress_prefix}Fit {result['interest']:.2f}/{result['attainability']:.2f}: {job['url']}"
    )
    q.mark_job_evaluation_complete(conn, job["id"])


def _eligible_for_reevaluation(conn: sqlite3.Connection, scenario: dict) -> tuple[list[dict], int, list[dict], str]:
    criteria = q.get_criteria(conn, scenario["id"])
    current_hash = compute_version_hash(scenario, criteria)
    candidates = q.get_jobs(conn, status="new") + q.get_jobs(conn, status="accepted")
    eligible = [j for j in candidates if j["content_type"] in ("job_posting", "lead")]
    existing_hashes = q.get_job_score_hashes(conn, scenario["id"])
    to_evaluate = [j for j in eligible if existing_hashes.get(j["id"]) != current_hash]
    skipped = len(eligible) - len(to_evaluate)
    return to_evaluate, skipped, criteria, current_hash


def run_reevaluate(
    conn: sqlite3.Connection,
    client: openai.OpenAI,
    model: str,
    scenario: dict,
) -> Generator[str, None, int]:
    to_evaluate, skipped, criteria, current_hash = _eligible_for_reevaluation(conn, scenario)
    total = len(to_evaluate)

    msg = f"Re-evaluating {total} job(s) for scenario '{scenario['name']}'"
    if skipped:
        msg += f", skipping {skipped} already current"
    yield _progress(msg)

    scored = 0
    for i, job in enumerate(to_evaluate, start=1):
        if not job["summary"]:
            # A summary-less job keeps its stale hash and is re-listed (and
            # re-skipped) on every run — refreshing the summary is the job of
            # the per-job "reset to new" action, not this path.
            yield _progress(f"[{i}/{total}] Skipped (no summary on file): {job['title'] or job['url']}")
            continue
        score, reasoning = evaluate(client, model, scenario, criteria, job["summary"])
        q.upsert_job_score(conn, job["id"], scenario["id"], score, reasoning, current_hash)
        scored += 1
        yield _progress(f"[{i}/{total}] Re-scored {score}: {job['title'] or job['url']}")

    yield _progress(f"Re-evaluation complete for '{scenario['name']}': {scored} job(s) updated")
    return scored


def run_reassess_fit(
    conn: sqlite3.Connection,
    client: openai.OpenAI,
    model: str,
) -> Generator[str, None, int]:
    profile = q.get_profile(conn)
    current_hash = compute_profile_hash(profile)
    candidates = q.get_jobs(conn, status="new") + q.get_jobs(conn, status="accepted")
    eligible = [j for j in candidates if j["content_type"] in ("job_posting", "lead")]
    to_assess = [j for j in eligible if j["profile_version_hash"] != current_hash]
    skipped = len(eligible) - len(to_assess)

    msg = f"Recomputing fit scores for {len(to_assess)} job(s)"
    if skipped:
        msg += f", skipping {skipped} already current"
    yield _progress(msg)

    for i, job in enumerate(to_assess, start=1):
        if not job["summary"]:
            # Same as run_reevaluate: never feed an empty summary to the LLM.
            # A summary-less job keeps its stale hash and is re-listed (and
            # re-skipped) on every run — refreshing the summary is the job of
            # the per-job "reset to new" action, not this path.
            yield _progress(f"[{i}/{len(to_assess)}] Skipped (no summary on file): {job['title'] or job['url']}")
            continue
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
