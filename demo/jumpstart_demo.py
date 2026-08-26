#!/usr/bin/env python3
"""
Jumpstart demo database with Norwegian job market scenarios and sources.

This script seeds a fresh job-seek.db with:
- Profile (mid-career backend/fullstack generalist)
- 4 scenarios with realistic criteria
- 9 real Norwegian job sources (Finn.no, Kode24, DNB, Telenor, Accenture, etc.)
- One fabricated "Dream Job" hand-scored at 99%, for screenshots

Does NOT fetch or evaluate real jobs—user does that manually via the UI.
Idempotent: safe to re-run (scenarios/sources/dream job won't duplicate if already present).

Usage:
    python demo/jumpstart_demo.py [--db /path/to/job-seek.db]

Default database: ./job-seek.db
"""

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.db import schema, queries
from app.scenario_version import compute_profile_hash, compute_version_hash


PROFILE_CONTENT = """Mid-career backend engineer with full-stack capabilities.

## Background

Strong foundation in Python, SQL, distributed systems. 5–8 years experience building APIs, optimizing databases, and deploying production systems. Recent work: API design, database optimization, deployment automation.

## Skills

Backend
- Python
- SQL
- distributed systems
- API design

Frontend
- React
- Vue (some production experience)

DevOps
- Docker
- Kubernetes basics
- CI/CD pipelines (JetBrains)

## What I value

- Clear code and sustainable pace
- Learning from teammates
- Mentorship opportunities (both giving and receiving)
- Transparent culture, autonomy in technical decisions
- Remote-friendly or based in Norway/EU

## What I'm open to

- Full-stack roles (but prefer backend depth)
- Startups with strong founders and product-market clarity
- Established companies with strong engineering culture
- Leadership roles (tech lead, EM) at scale


### Geography

- Based in Norway
- Open to EU relocation for the right opportunity
"""

SCENARIOS_AND_CRITERIA = [
    {
        "name": "Senior Fullstack",
        "description": "Seeking senior fullstack role at established Norwegian tech company (Oslo/Bergen). Emphasis on career growth, structured teams, strong technical culture. Both backend rigor and modern frontend.",
        "gate_threshold": 0.75,
        "criteria": [
            ("Established company (5+ years, stable revenue or strong funding)", "must"),
            ("Backend or fullstack focus (not pure frontend)", "must"),
            ("Norway-based or EU-based", "must"),
            ("Senior title (tech lead, staff engineer, or equivalent)", "prefer"),
            ("Mentorship and growth path", "prefer"),
            ("Python, Go, or Rust backend", "prefer"),
            ("Avoid: startup pre-seed stage", "avoid"),
            ("Avoid: mandatory C++ or legacy monolith", "avoid"),
        ],
    },
    {
        "name": "Dev/Sec/Ops @ Startup/Scale-up",
        "description": "Infrastructure or dev/sec/ops role at small, fast-moving company (10–100 people). Remote-friendly, equity/shares offered. Autonomy, learning new domains, direct business impact.",
        "gate_threshold": 0.55,
        "criteria": [
            ("Remote or hybrid (not office-only)", "must"),
            ("Small company (< 150 people)", "must"),
            ("Equity/share options or profit-sharing", "prefer"),
            ("DevOps, security, or infrastructure focus", "prefer"),
            ("Modern cloud stack (AWS, GCP, Kubernetes welcome)", "prefer"),
            ("Avoid: consulting-heavy", "avoid"),
            ("Avoid: rigid process (waterfall, heavy approval)", "avoid"),
        ],
    },
    {
        "name": "Founding Engineer - Maritim",
        "description": "Dream role: founding engineer (CTO track) at early-stage digital twin, simulation, or hardware startup. Willing to relocate internationally. High equity, greenfield architecture, direct product impact.",
        "gate_threshold": 0.35,
        "criteria": [
            ("Founding/early stage (series A or pre-seed, < 20 people)", "must"),
            ("Digital twin, simulation, IoT, or hardware-adjacent domain", "must"),
            ("Co-founder or CTO track (equity-heavy)", "prefer"),
            ("Greenfield or significant architectural freedom", "prefer"),
            ("International team (English-speaking)", "prefer"),
            ("Avoid: saturated markets (pure web SaaS)", "avoid"),
            ("Avoid: founder team without technical credibility", "avoid"),
        ],
    },
    {
        "name": "AI Lead",
        "description": "Mid-level leadership (tech lead IC or engineering manager) at larger org building AI/ML initiatives. Consultancy, finance, insurance, or health. Norway/EU-based. High compensation expected.",
        "gate_threshold": 0.65,
        "criteria": [
            ("Mid-level leadership (tech lead, principal engineer, or EM)", "must"),
            ("Active AI/ML or data strategy (not just 'exploring AI')", "must"),
            ("Sector: consultancy, financial services, insurance, or health", "must"),
            ("Europe-based", "must"),
            ("High compensation (€85k+ EUR or NOK equivalent)", "prefer"),
            ("Budget ownership or hiring responsibility", "prefer"),
            ("Influence on technical direction", "prefer"),
            ("Avoid: pure ops/SRE roles", "avoid"),
            ("Avoid: startup (want org maturity + resources)", "avoid"),
        ],
    },
]

SOURCES = [
    {
        "name": "Finn.no",
        "url": "https://www.finn.no/job/search?occupation=1.22.380&occupation=1.23.246&occupation=1.23.2047&occupation=1.23.2049&occupation=1.23.244&occupation=1.23.384&occupation=1.23.385&occupation=1.23.2052&occupation=1.23.86",
        "fetcher_type": "generic_listing",
    },
    {
        "name": "Kode24",
        "url": "https://kodejobb.no/stillinger",
        "fetcher_type": "generic_listing",
    },
    {
        "name": "DNB Teknologi",
        "url": "https://jobb.dnb.no/go/Teknologi/4224301/",
        "fetcher_type": "generic_listing",
    },
    {
        "name": "Telenor",
        "url": "https://www.telenor.com/career/open-positions/",
        "fetcher_type": "generic_listing",
    },
    {
        "name": "Accenture",
        "url": "https://www.accenture.com/no-en/careers/jobsearch?jt=Mid-Level",
        "fetcher_type": "generic_listing",
    },
    {
        "name": "Sopra Steria",
        "url": "https://careers.soprasteria.no/jobs",
        "fetcher_type": "generic_listing",
    },
    {
        "name": "Tieto",
        "url": "https://careers.tieto.com/jobs?options=320%2C193%2C197%2C213&page=1",
        "fetcher_type": "generic_listing",
    },
    {
        "name": "Bouvet",
        "url": "https://www.bouvet.no/ledige-stillinger",
        "fetcher_type": "generic_listing",
    },
    {
        "name": "Itera",
        "url": "https://careers-no.itera.com/#jobs",
        "fetcher_type": "generic_listing",
    },
]

# A fabricated posting, hand-scored at 99%, so a fresh demo has one
# obviously-glowing entry to screenshot. Targets "Founding Engineer -
# Maritim" since its scenario description already calls it the dream role.
DREAM_JOB_SOURCE = {
    "name": "Dream Job (demo)",
    "url": "https://dreamjob.demo.invalid/careers",
    "fetcher_type": "manual",
}

DREAM_JOB = {
    "url": "https://dreamjob.demo.invalid/careers/dream-job",
    "title": "Dream Job",
    "company": "Fjordlight Robotics",
    "headline": "Founding engineer, CTO track, greenfield digital-twin platform — remote-first, internationally distributed crew.",
    "summary": (
        "Fjordlight Robotics is building a digital-twin platform for autonomous maritime "
        "vessels. Series A, 14 people, and looking for a founding engineer to take the "
        "CTO track: greenfield architecture, meaningful equity, and a small international "
        "team that already trusts you with the hard calls."
    ),
    "raw_text": (
        "Dream Job — Founding Engineer (CTO track)\n"
        "Fjordlight Robotics — remote-first, international team\n\n"
        "We're a Series A startup (14 people) building a digital-twin simulation platform "
        "for autonomous maritime vessels. We're looking for a founding engineer to help "
        "shape the architecture from the ground up and grow into the CTO role as we scale.\n\n"
        "What you'd own:\n"
        "- Greenfield architecture for our simulation and telemetry stack\n"
        "- Technical direction as we go from 14 to 40 people\n"
        "- A founding team with deep maritime and robotics credibility\n\n"
        "What we offer:\n"
        "- Meaningful equity (co-founder-level)\n"
        "- Full architectural freedom — no legacy to untangle\n"
        "- A distributed, English-speaking team across Norway and the EU\n\n"
        "---\n"
        "This is a fantasy demo posting seeded by jumpstart_demo.py — not a real listing. "
        "It exists so a fresh demo has one obviously top-scoring job to screenshot."
    ),
    "target_scenario": "Founding Engineer - Maritim",
    "score_reasoning": (
        "Hand-set to 99% — this is a fabricated demo posting seeded by jumpstart_demo.py "
        "for screenshots, not a real evaluation."
    ),
}


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="job-seek.db", help="Path to job-seek.db")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    schema.init_db(conn)

    try:
        print("Seeding demo database...")

        # 1. Profile (never overwrite an existing one — this seeds a fresh
        # demo, not a reset of a real user's data)
        if queries.get_profile(conn).strip():
            print("  → Profile (already set, left untouched)")
        else:
            print("  → Profile")
            queries.upsert_profile(conn, PROFILE_CONTENT)

        # 2. Scenarios + criteria
        existing_scenarios = {s["name"]: s["id"] for s in queries.get_scenarios(conn)}
        for scenario_def in SCENARIOS_AND_CRITERIA:
            if scenario_def["name"] in existing_scenarios:
                print(f"  → Scenario: {scenario_def['name']} (already exists)")
                continue

            print(f"  → Scenario: {scenario_def['name']}")
            scenario_id = queries.insert_scenario(
                conn,
                name=scenario_def["name"],
                description=scenario_def["description"],
            )

            # Set gate threshold (insert_scenario doesn't take it)
            queries.update_scenario(
                conn,
                scenario_id,
                name=scenario_def["name"],
                description=scenario_def["description"],
                gate_threshold=scenario_def["gate_threshold"],
            )

            # Insert criteria
            for criterion_text, weight in scenario_def["criteria"]:
                queries.insert_criterion(
                    conn,
                    scenario_id=scenario_id,
                    text=criterion_text,
                    weight=weight,
                    source="manual",
                )

        # 3. Sources
        for source_def in SOURCES:
            existing = queries.get_source_by_url(conn, source_def["url"])
            if existing:
                print(f"  → Source: {source_def['name']} (already exists)")
            else:
                print(f"  → Source: {source_def['name']}")
                queries.insert_source(
                    conn,
                    name=source_def["name"],
                    url=source_def["url"],
                    fetcher_type=source_def["fetcher_type"],
                )

        # 4. Dream Job — fabricated, hand-scored at 99%, for screenshots
        if queries.get_job_by_url(conn, DREAM_JOB["url"]):
            print("  → Dream Job (already exists)")
        else:
            print("  → Dream Job (fantasy 99% match, for screenshots)")

            demo_source = queries.get_source_by_url(conn, DREAM_JOB_SOURCE["url"])
            demo_source_id = demo_source["id"] if demo_source else queries.insert_source(
                conn,
                name=DREAM_JOB_SOURCE["name"],
                url=DREAM_JOB_SOURCE["url"],
                fetcher_type=DREAM_JOB_SOURCE["fetcher_type"],
            )

            job_id = queries.insert_job(
                conn,
                source_id=demo_source_id,
                url=DREAM_JOB["url"],
                title=DREAM_JOB["title"],
                company=DREAM_JOB["company"],
                raw_text=DREAM_JOB["raw_text"],
                published_at=datetime.now(timezone.utc).isoformat(),
            )
            queries.update_job_pipeline(
                conn,
                job_id,
                simplified_content=DREAM_JOB["raw_text"],
                content_type="job_posting",
                summary=DREAM_JOB["summary"],
                headline=DREAM_JOB["headline"],
            )

            profile_hash = compute_profile_hash(queries.get_profile(conn))
            queries.update_job_fit(
                conn,
                job_id,
                interest_score=0.99,
                interest_reasoning=DREAM_JOB["score_reasoning"],
                attainability_score=0.99,
                attainability_reasoning=DREAM_JOB["score_reasoning"],
                profile_version_hash=profile_hash,
            )

            target_scenario = next(
                s for s in queries.get_scenarios(conn) if s["name"] == DREAM_JOB["target_scenario"]
            )
            target_criteria = queries.get_criteria(conn, target_scenario["id"])
            queries.upsert_job_score(
                conn,
                job_id,
                target_scenario["id"],
                0.99,
                DREAM_JOB["score_reasoning"],
                compute_version_hash(target_scenario, target_criteria),
            )
            queries.mark_job_evaluation_complete(conn, job_id)

        print("\n✓ Demo database seeded successfully!")
        print(f"  Profile: 1 entry")
        print(f"  Scenarios: {len(SCENARIOS_AND_CRITERIA)}")
        print(f"  Sources: {len(SOURCES)}")
        print(f"  Dream Job: 1 fantasy entry at 99% match")
        print(f"\nNext steps:")
        print(f"  1. Start the dev server: uv run uvicorn app.main:app --port 8000")
        print(f"  2. Open http://localhost:8000 in your browser")
        print(f"  3. Click 'Fetch All' to populate jobs from real sources")
        print(f"  4. Watch scores populate as jobs are evaluated")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
