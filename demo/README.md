# Demo

Scripts for seeding and validating a Norway-focused demo dataset. Assumes you've already done the main setup (see repo root `README.md` — `uv sync`, `config.toml`).

Run these from a separate demo data directory (own `config.toml`/`job-seek.db`, keeps it out of your real one) with `uv run --project`, which resolves the job-seek project/venv without changing your cwd. Use the *absolute* script path too — it's resolved against your cwd, not `--project`:

```bash
# 1. Seed profile, scenarios, and sources (creates job-seek.db if needed)
uv run --project /path/to/job-seek python /path/to/job-seek/demo/jumpstart_demo.py

# 2. Populate real jobs — either start the app and click "Fetch All", or:
uv run --project /path/to/job-seek python /path/to/job-seek/demo/qa_fetch_sources.py
```

## `jumpstart_demo.py` — Demo Database Bootstrap

Seeds `job-seek.db` with a demo profile, 4 scenarios (with criteria), 9 real Norwegian job sources, and one fabricated "Dream Job" hand-scored at 99% match (for screenshots). Does not fetch or fabricate real jobs — run a fetch afterwards (via the UI or `qa_fetch_sources.py`) to populate real postings.

Safe to re-run: scenarios/sources/dream job are skipped if already present by name/URL, and an existing profile is never overwritten.

```bash
python demo/jumpstart_demo.py [--db /path/to/job-seek.db]
```

## `qa_fetch_sources.py` — Source Validation

Runs the real fetch pipeline (fetch → classify → summarize → evaluate) against one or more configured sources, without needing the web server running. Use it to check a source actually returns postings before relying on it.

```bash
python demo/qa_fetch_sources.py [--only "Finn.no,Kode24"] [--max-per-source 3] [--db ...] [--config ...]
```

Requires a working LLM endpoint in `config.toml`. Adds real jobs/scores to the target DB (not destructive to existing data, but not a dry run either). `--max-per-source` (default 3, 0 = no limit) caps how many jobs get classified per source before moving on — keeps a full QA run fast.
