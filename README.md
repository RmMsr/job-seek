# job-seek

A single-user local job research tool. Fetches job postings from curated sources, evaluates them against your profile and search criteria using a local LLM, and surfaces well-matching opportunities for triage in a browser UI.

## What it does

- Fetches from public job boards (HTTP) and login-gated sources like Slack communities (Playwright with a persistent browser session — log in once, reused automatically)
- Classifies each post as a full job posting, a lead, irrelevant, or an error
- Summarises and scores each job against your profile and active search scenario
- Presents results as a filterable triage list — accept, reject, or mark as invalid with a note
- Refines your search criteria over time based on your feedback

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- A running [Ollama](https://ollama.com/) instance (or any OpenAI-compatible endpoint)

## Setup

```bash
# Install dependencies
uv sync

# Install the Playwright browser (needed for auth-gated sources)
uv run playwright install chromium
```

Edit `config.toml` to point at your LLM endpoint and model:

```toml
[llm]
endpoint = "http://localhost:11434/v1"
model = "llama3.2"

[database]
path = "job-seek.db"

[browser]
profile_dir = "browser-profile"
```

## Usage

```bash
uv run uvicorn app.main:app --reload --port 8000
```

Open [http://localhost:8000](http://localhost:8000) in your browser.

### First-time setup

1. **Profile** — Go to `/profile` and describe your skills, experience, interests, and constraints (plain text or markdown).
2. **Scenarios** — Go to `/scenarios`, create a search scenario (e.g. "Remote senior ML role") and add criteria with weights (`must` / `prefer` / `avoid`).
3. **Sources** — Seed the database with your sources (one-time, see below).

### Seeding sources

Run once in a Python REPL with the app stopped:

```python
import sqlite3
from app.db.schema import init_db
from app.db import queries as q

conn = sqlite3.connect("job-seek.db")
conn.row_factory = sqlite3.Row
init_db(conn)

q.insert_source(conn, "finn.no",     "https://www.finn.no/job/browse.html",                    "http")
q.insert_source(conn, "Example Jobs",  "https://example.com/jobs",                          "http")
q.insert_source(conn, "Example Slack", "https://example-workspace.slack.com/archives/C0EXAMPLE1",     "slack")
```

### Authenticating login-gated sources

The first time you fetch a source that requires login (e.g. Slack), a browser window opens. Log in normally, then press **Enter** in the terminal. The session is saved to `browser-profile/` and reused on all future fetches.

### Fetching jobs

Go to `/fetch` and click **Fetch** next to a source (or **Fetch all**). Progress is shown inline. New jobs appear on the main list at `/` once the run completes.

### Triaging

The main list (`/`) shows unreviewed jobs sorted by relevance score. Click a row to expand it, read the summary and score reasoning, then choose:

- **Accept** — worth following up
- **Reject** — not a fit
- **Invalid** — extraction error or not actually a job

Each action requires a short note. Notes accumulate and feed the **Refine criteria** flow on the Scenarios page, which proposes updates to your criteria based on what you've been accepting and rejecting.

## Running tests

```bash
uv run pytest
```
