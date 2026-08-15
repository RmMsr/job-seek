# job-seeker: Find best job matches for you

You need:

1. **Job sources**: URLs where job offers are published. Like job boards or Slack channel.
2. **Your portfolio**: A text describing your skills, experience expectations and dislikes.
3. **Work scenarios**: A set of definitions and rules what you are looking for.
4. **GenAI LLM API key**: Credentials for an Open AI copatible chat completions API. Either a local LLM (ollama, llama.cpp, LM-Studio, ...) or one of the public providers.

You get:

- **Filtered list of conrete jobs** matching your scenarios.
- **Easy to read job summary**: See all relevant facts at once.
- **Prioritized ranking ob opportunities** evaluated against your skills, career stage and preferences.

Daily workflow:

1. Fetch newly published jobs
2. Check the findings. Leave feedback to finetune scenario specifications.
3. Approve or reject jobs. Leave notes to improve your profile.
4. Apply (not part of the app yet)

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- OpenAI chat-completion compatible GenAI endpoint

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

### Running in a container

All per-instance state — `config.toml`, the sqlite database, and
`browser-profile/` — lives under a single `data/` directory inside the
container, so one volume mount covers everything. On first run, the
container populates `data/` with a default `config.toml` that points at an
inference provider running on the container host itself (e.g. a local Ollama
on port 11434); edit the mounted `data/config.toml` to change the endpoint,
model, or anything else. The image runs as a fixed non-root uid/gid (1000)
for hardening.

The Slack login flow still runs on the host (`uv run python -m
app.cli.slack_login ...`), not inside the container: it needs a real,
visible browser window.

#### Podman (primary)

```bash
podman build --file Containerfile --tag job-seek .
mkdir --parents data
podman run --detach --publish 8000:8000 \
  --userns=keep-id:uid=1000,gid=1000 \
  --volume "$(pwd)/data:/app/data" \
  job-seek
```

`--userns=keep-id` maps the container's uid 1000 back to your own host uid,
so files Podman writes into `data/` (the config, db, browser profile) stay
owned by you rather than an arbitrary container uid. Podman resolves
`host.containers.internal` (the default endpoint's hostname) to the host
automatically — no extra flags needed.

#### Docker

```bash
docker build --file Containerfile --tag job-seek .
mkdir --parents data
docker run --detach --publish 8000:8000 \
  --add-host=host.containers.internal:host-gateway \
  --volume "$(pwd)/data:/app/data" \
  job-seek
```

Docker has no `--userns=keep-id` equivalent, so make sure `data/` is
writable by uid 1000 on the host before starting the container (it already
will be if your own user is uid 1000, the common default for a first Linux
user account) — otherwise `chown --recursive 1000:1000 data` first. The
`--add-host` flag is required on Docker (unlike Podman) to resolve
`host.containers.internal` to the host machine.

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

Go to `/fetch` and click **Fetch** next to a source (or **Fetch all**). Progress is shown inline. New jobs appear on the main list at `/jobs` once the run completes.

### Triaging

The main list (`/jobs`) shows unreviewed jobs sorted by relevance score. Click a row to expand it, read the summary and score reasoning, then choose:

- **Accept** — worth following up
- **Reject** — not a fit
- **Invalid** — extraction error or not actually a job

Each action requires a short note. Notes accumulate and feed the **Refine criteria** flow on the Scenarios page, which proposes updates to your criteria based on what you've been accepting and rejecting.

## Running tests

```bash
uv run pytest
```
