#!/usr/bin/env python3
"""
Jumpstart demo database with Norwegian job market scenarios and sources.

This script seeds a fresh job-seek.db with:
- Profile (mid-career backend/fullstack generalist)
- 4 scenarios with realistic criteria
- 9 real Norwegian job sources (Finn.no, Kode24, DNB, Telenor, Accenture, etc.)

Does NOT fetch or evaluate jobs—user does that manually via the UI.
Idempotent: safe to re-run (scenarios/sources won't duplicate if already present).

Usage:
    python demo/jumpstart_demo.py [--db /path/to/job-seek.db]

Default database: ./job-seek.db
"""

import sqlite3
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.db import schema, queries


PROFILE_CONTENT = """Mid-career backend engineer with full-stack capabilities.

**Background:**
Strong foundation in Python, SQL, distributed systems. 5–8 years experience building APIs, optimizing databases, and deploying production systems. Recent work: API design, database optimization, deployment automation.

**Skills:**
- Backend: Python, SQL, distributed systems, API design
- Frontend: React, Vue (some production experience)
- DevOps: Docker, Kubernetes basics, CI/CD pipelines

**What I value:**
- Clear code and sustainable pace
- Learning from teammates
- Mentorship opportunities (both giving and receiving)
- Transparent culture, autonomy in technical decisions
- Remote-friendly or based in Norway/EU

**What I'm open to:**
- Full-stack roles (but prefer backend depth)
- Startups with strong founders and product-market clarity
- Established companies with strong engineering culture
- Leadership roles (tech lead, EM) at scale

**Geography:**
Based in Norway. Open to EU relocation for the right opportunity.
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

        print("\n✓ Demo database seeded successfully!")
        print(f"  Profile: 1 entry")
        print(f"  Scenarios: {len(SCENARIOS_AND_CRITERIA)}")
        print(f"  Sources: {len(SOURCES)}")
        print(f"\nNext steps:")
        print(f"  1. Start the dev server: uv run uvicorn app.main:app --port 8000")
        print(f"  2. Open http://localhost:8000 in your browser")
        print(f"  3. Click 'Fetch All' to populate jobs from real sources")
        print(f"  4. Watch scores populate as jobs are evaluated")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
