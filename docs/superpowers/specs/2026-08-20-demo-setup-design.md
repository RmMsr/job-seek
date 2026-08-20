# Demo Setup with Real Norwegian Job Data — Design Spec

**Date:** 2026-08-20
**Status:** Approved

## Overview

A jumpstart script that seeds `job-seek.db` with realistic Norwegian job-market data: one profile, four search scenarios, real job sources (Finn, Kode24, career pages), and a curated set of real job postings across those scenarios. The database starts in a "fresh user" state — jobs are inserted raw (URL, title, company) without pre-computed scores, summaries, or feedback. This lets someone run the app and see the full pipeline: fetch → simplify → classify → evaluate → rank.

**Purpose:** Public showcase (README, portfolio, demo video). Should look authentic, demonstrate multi-scenario filtering and scoring, and be immediately graspable to a stranger.

---

## User Profile

**Name:** (anonymous; profile is the focus, not a person)

**Career stage:** Mid-career backend-leaning generalist (5–8 years)
- Primary: backend systems, APIs, databases
- Secondary: modern frontend (React, Vue), some UX/design thinking
- Tertiary: DevOps/infrastructure, Docker, deployment pipelines
- Interests: sustainable systems, mentorship, open-source, small focused teams
- Geography: Based in Norway, open to EU relocation for right opportunity

**Compensation expectations:** Varies by scenario (€65k–€90k+ NOK equivalent)

**Profile content:** (will be written as natural markdown)
```
Mid-career backend engineer with full-stack capabilities. 
Strong foundation in Python, SQL, distributed systems. 
Recent work: API design, database optimization, deployment automation.
Some React/Vue experience, enjoy user-facing problem-solving.
Value: clear code, sustainable pace, learning from teammates, mentorship opportunities.
Willing to do full-stack but prefer depth in backend.
Remote-friendly but not remote-only.
```

---

## Four Scenarios

### Scenario 1: Senior Fullstack — Established Company
**Gate threshold:** 0.75 (high bar; only excellent matches)

**Description:**
Seeking a senior fullstack role at an established Norwegian tech company or scale-up (Oslo/Bergen region preferred). Emphasis on career growth, structured teams, and strong technical culture. Role involves both backend rigor and modern frontend. Stability and long-term opportunity matter.

**Criteria:**
- **Must:** Established company (5+ years, stable revenue or strong funding)
- **Must:** Backend or fullstack focus (not pure frontend)
- **Must:** Norway-based or EU-based (relocation cost/logistics)
- **Prefer:** Senior title (tech lead, staff engineer, or equivalent)
- **Prefer:** Mentorship/growth path
- **Prefer:** Python, Go, or Rust backend
- **Avoid:** Startup pre-seed stage (too unstable)
- **Avoid:** Mandatory C++ or legacy monolith

---

### Scenario 2: Dev/Sec/Ops — Startup / Scale-up
**Gate threshold:** 0.55 (lower bar; accepts promising candidates)

**Description:**
Looking for a dev/sec/ops or infrastructure engineer role at a small, fast-moving company (10–100 people). Remote-friendly. Company offers equity/share options and accepts overtime during critical pushes. Emphasis on autonomy, learning new domains, and direct business impact.

**Criteria:**
- **Must:** Remote or hybrid (not office-only)
- **Must:** Small company (< 150 people)
- **Prefer:** Equity/share options or profit-sharing
- **Prefer:** DevOps, security, infrastructure focus
- **Prefer:** Modern cloud stack (AWS, GCP, Kubernetes optional but welcome)
- **Avoid:** Consulting-heavy (too much client work)
- **Avoid:** Rigid process (waterfall, heavy approval chains)

---

### Scenario 3: Founding Engineer — Digital Twin / Simulation
**Gate threshold:** 0.35 (very low bar; exploratory)

**Description:**
Dream role: early-stage founding engineer (ideally CTO track) at a digital twin, simulation, or hardware-adjacent startup. Willing to relocate internationally. High equity stake, greenfield technical architecture, direct impact on product direction. Accepts lower short-term salary for upside and autonomy.

**Criteria:**
- **Must:** Founding/early stage (series A or pre-seed, < 20 people)
- **Must:** Digital twin, simulation, IoT, or hardware-adjacent domain
- **Prefer:** Co-founder or CTO track (equity-heavy, strategic role)
- **Prefer:** Greenfield or significant architectural freedom
- **Prefer:** International team (English-speaking)
- **Avoid:** Saturated markets (pure web SaaS)
- **Avoid:** Founder team without technical credibility

---

### Scenario 4: Tech Lead / EM — AI-Forward Consultancy / Finance / Insurance / Health
**Gate threshold:** 0.65 (medium-high bar; selective)

**Description:**
Mid-level leadership role (tech lead IC or engineering manager) at a larger organization building AI/ML initiatives. Consultancy, finance, insurance, or health sector. Norway-based or EU-based. High compensation expected. Role involves both technical depth and strategic influence.

**Criteria:**
- **Must:** Mid-level leadership (tech lead, principal engineer, or EM)
- **Must:** Active AI/ML or data strategy (not just "we're exploring AI")
- **Must:** Sector: consultancy, financial services, insurance, or health
- **Must:** Europe-based
- **Prefer:** High compensation (€85k+ EUR or NOK equivalent)
- **Prefer:** Budget ownership or hiring responsibility
- **Prefer:** Influence on technical direction
- **Avoid:** Pure ops/SRE roles (too far from strategic AI work)
- **Avoid:** Startup (want org maturity + resources for AI)

---

## Real Norwegian Job Sources

### Source 1: Finn.no
**URL:** https://www.finn.no/job/search?occupation=1.22.380&occupation=1.23.246&occupation=1.23.2047&occupation=1.23.2049&occupation=1.23.244&occupation=1.23.384&occupation=1.23.385&occupation=1.23.2052&occupation=1.23.86
**Fetcher type:** generic_listing
**Description:** Norway's largest classified job board. Occupation codes filter for software development, IT, and related roles.

### Source 2: Kode24
**URL:** https://www.kode24.no/
**Fetcher type:** generic_listing
**Description:** Norwegian tech-focused job board. Higher signal-to-noise for developer roles.

### Source 3: Career Pages (DNB, Visma, Equinor, Bekk, Kantega)
**Fetcher type:** manual (user adds specific URLs as needed)
**Description:** Direct career pages of major Norwegian employers. Provided as examples; user can add more.

---

## Jumpstart Script

### Purpose
Bootstrap a fresh `job-seek.db` with infrastructure only: profile, scenarios + criteria, and job sources. Does **not** fetch jobs—user manually triggers fetch after jumpstart via the UI. This keeps the setup lightweight and lets the user see the full pipeline in action.

### Input
A YAML or JSON manifest defining:
```
profile:
  content: "..."

scenarios:
  - name: "Senior Fullstack — Established Company"
    description: "..."
    gate_threshold: 0.75
    criteria:
      - text: "..."
        weight: must
        source: manual
      - ...
  - ...

sources:
  - name: "Finn.no"
    url: "https://www.finn.no/job/search?occupation=..."
    fetcher_type: generic_listing
  - name: "Kode24"
    url: "https://www.kode24.no/"
    fetcher_type: generic_listing
  - ...
```

### Processing
1. Insert profile (markdown text)
2. Insert 4 scenarios + criteria for each
3. Insert sources (Finn, Kode24, career pages)
4. Done — no fetch or evaluation

### Output
An empty (job-free) SQLite database with full infrastructure: profile, scenarios, sources. Ready for user to start app and manually fetch.

### Benefits
- **Lightweight:** Quick to run, no network/LLM latency
- **Transparent pipeline:** User sees jobs arrive and get scored in real-time
- **Source validation:** Fetch step validates all sources work (QA happens during fetch, not bootstrap)
- **Flexible:** User controls when to fetch, can test individual sources

---

## Database State After Jumpstart

| Table | State |
|-------|-------|
| `profile` | 1 row, complete profile text |
| `scenarios` | 4 rows, all created (none marked active yet—user activates from UI) |
| `criteria` | ~20 rows (5 per scenario), all `source='manual'` |
| `sources` | 3–5 rows (Finn, Kode24, career pages), all enabled |
| `jobs` | Empty (user fetches after jumpstart) |
| `job_scores` | Empty (will populate after fetch + evaluate) |
| `fetch_runs` | Empty (will log after first fetch) |
| `scenario_feedback` | Empty (will populate after user reviews jobs) |

---

## User Flow After Jumpstart

1. Run jumpstart script → sets up infrastructure (1 min)
2. Start dev server
3. Visit app → sees empty job list, all 4 scenarios available
4. Navigate to Profile → sees the generated profile text
5. Switch to Scenarios tab → sees all 4 scenarios + their criteria
6. (Optional) Pick preferred scenario to activate
7. Click "Fetch all" → starts fetching real jobs from Finn, Kode24, etc.
8. Watch progress stream in → jobs arrive, get classified, summarized, scored
9. Once fetch completes → job list populates with real, scored jobs
10. Click a job → sees LLM summary, score reasoning per active scenario
11. Leave feedback (accept/reject + notes) → starts training criteria refinement
12. (Optional) Switch scenarios → see same jobs ranked differently per scenario's gate

This showcases the complete product loop transparently: from empty DB → real fetch → real evaluation → user feedback.

---

## Implementation

### Phase 1: Write Jumpstart Script
- Python script: `scripts/jumpstart_demo.py`
- Reads manifest (YAML), inserts profile/scenarios/sources
- Does not fetch or evaluate (user does this manually via UI)
- Includes built-in manifest with Norwegian sources + scenario definitions
- Idempotent for scenarios/sources (safe to re-run)

### Phase 2: Test & Validate
- Run script on fresh DB
- Verify all 4 scenarios created with correct criteria
- Verify all 3+ sources configured and enabled
- Verify database is empty (no jobs)
- Spot-check manifest has correct occupation codes for Finn.no and URLs for Kode24

### Phase 3: Document
- README in `scripts/` with:
  - How to run jumpstart
  - What to do next (start dev server, click Fetch All)
  - Expected fetch duration (~5–15 min depending on sources + LLM latency)
  - Troubleshooting (what to do if fetch fails on a source)
- Example manifest included in script

---

## Success Criteria

- [ ] Jumpstart script runs quickly (~30 sec, no network/LLM calls)
- [ ] All 4 scenarios created with proper criteria
- [ ] All 3+ sources configured and enabled
- [ ] Database starts empty (no jobs pre-seeded)
- [ ] User can start app and see empty job list + populated scenarios
- [ ] User can click "Fetch all" to populate jobs from real sources
- [ ] Fetch succeeds (all sources return jobs, errors gracefully handled)
- [ ] All jobs classified (content_type filled: job_posting/lead/irrelevant)
- [ ] All jobs have LLM-generated summaries + headlines
- [ ] All jobs scored against all 4 scenarios (job_scores table populated)
- [ ] Switching scenarios in UI shows different rankings per gate_threshold
- [ ] Screenshots show realistic mix of high/medium/low-scoring jobs across scenarios

