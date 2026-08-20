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
Seed a fresh `job-seek.db` with realistic Norwegian job data at "day 1" state: profile, scenarios, sources, and real job URLs with minimal extracted metadata. No pre-computed scores, summaries, or feedback.

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
    url: "..."
    fetcher_type: generic_listing
  - ...

jobs:
  - url: "https://..."
    title: "..."
    company: "..."
    source_name: "Finn.no"
  - ...
```

### Processing
1. Insert profile
2. Insert scenarios + criteria for each
3. Insert sources
4. For each job:
   - Check if URL already exists (skip if so)
   - Insert minimal row: `url`, `title`, `company`, `source_id`, `content_type=NULL`, all LLM fields empty
   - Leave `raw_text`, `summary`, scores for pipeline to fill

### Output
A populated SQLite database ready to use. User can:
- View the raw job list (all marked as `new`, no scores)
- Run a fetch pass to pull down content and compute scores
- See the full pipeline in action

### Non-Goals
- Pre-evaluate jobs with mock scores
- Generate fake LLM summaries or reasoning
- Provide "expected" feedback or notes
- Hide the pipeline from the user

---

## Database State After Jumpstart

| Table | State |
|-------|-------|
| `profile` | 1 row, complete profile text |
| `scenarios` | 4 rows, all created, none marked active yet |
| `criteria` | ~20 rows (5 per scenario), all `source='manual'` |
| `sources` | 3–5 rows (Finn, Kode24, manual career pages) |
| `jobs` | 8–15 rows, all `status='new'`, `content_type=NULL`, minimal metadata |
| `job_scores` | Empty (no evaluation yet) |
| `fetch_runs` | Empty (no fetch runs yet) |
| `scenario_feedback` | Empty (no feedback yet) |

---

## User Flow After Jumpstart

1. Run jumpstart script → fresh DB seeded
2. Start dev server (or container)
3. Visit app → sees 8–15 "new" jobs, no scores yet
4. Navigate to Profile/Scenarios → sees all 4 scenarios + criteria
5. Click "Fetch all" or manually fetch → pulls job content, classifies, scores
6. Watch scores populate in real-time
7. Use feedback panel to accept/reject → see criteria refinement suggestions
8. Switch between scenarios → see different jobs ranked differently per gate threshold

This showcases the full product loop and feels real (not pre-baked).

---

## Implementation

### Phase 1: Collect Real Job URLs
- Manually browse Finn.no (using scenario filters), Kode24, and major career pages
- Collect 2–4 URLs per scenario (total 8–16 jobs)
- Verify URLs are accessible and not behind login walls (generic_listing assumes public)

### Phase 2: Write Jumpstart Script
- Python script: `scripts/jumpstart_demo.py`
- Reads manifest (YAML), inserts profile/scenarios/sources/jobs into DB
- Idempotent (safe to re-run)
- Includes built-in manifest with Norwegian data

### Phase 3: Test & Document
- Test script with fresh DB (`:memory:` or temp file)
- Write README in `scripts/` explaining how to use
- Confirm all jobs actually load and score without errors

---

## Success Criteria

- [ ] Jumpstart script runs without errors
- [ ] All 4 scenarios are created with proper criteria
- [ ] All jobs insert as `new` with no content_type
- [ ] User can manually select a scenario and see filtered job list
- [ ] Running a fetch pass populates scores without crashing
- [ ] Screenshot shows a realistic mix of high/medium/low-scoring jobs
- [ ] No fake/pre-baked data is visible to the user (all processing is transparent)

