#!/usr/bin/env python3
"""
QA script: runs the fetch pipeline directly (no HTTP server) against every
enabled source in the DB, printing progress unbuffered as it goes.

Used to validate that all jumpstart-configured sources actually work
end-to-end (fetch -> classify -> summarize -> evaluate).

Usage:
    python demo/qa_fetch_sources.py [--db job-seek.db] [--config config.toml] [--only "Finn.no,Kode24"] [--max-per-source 3]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import sqlite3
import argparse
import openai
from app.config import load_config
from app.db import queries as q
from app.db.schema import init_db
from app.pipeline import run_fetch


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="job-seek.db")
    parser.add_argument("--config", default="config.toml")
    parser.add_argument("--only", default=None, help="Comma-separated source names to limit to")
    parser.add_argument(
        "--max-per-source", type=int, default=3,
        help="Stop after this many jobs are classified per source (0 = no limit)",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)

    client = openai.OpenAI(base_url=config.llm_endpoint, api_key="not-needed")
    model = config.llm_model

    sources = [s for s in q.get_sources(conn) if s["fetcher_type"] != "manual" and s["enabled"]]
    if args.only:
        wanted = {n.strip() for n in args.only.split(",")}
        sources = [s for s in sources if s["name"] in wanted]

    print(f"Running fetch QA against {len(sources)} source(s)", flush=True)
    results = []
    for idx, source in enumerate(sources, start=1):
        label = f"[{idx}/{len(sources)}: {source['name']}]"
        print(f"{label} starting...", flush=True)
        gen = run_fetch(source, conn, client, model, config.browser_profile_dir)
        classified = 0
        try:
            try:
                while True:
                    msg = next(gen)
                    print(f"{label} {msg}", flush=True)
                    if " Classified as " in msg:
                        classified += 1
                        if args.max_per_source and classified >= args.max_per_source:
                            print(f"{label} reached --max-per-source={args.max_per_source}, stopping this source", flush=True)
                            gen.close()  # cuts the fetch_runs row short — expected, this is a capped QA run
                            break
            except StopIteration as stop:
                result = stop.value
                print(f"{label} DONE found={result.jobs_found} new={result.jobs_new}", flush=True)
                results.append((source["name"], "ok", result.jobs_found, result.jobs_new, None))
            else:
                results.append((source["name"], "capped", None, classified, None))
        except Exception as e:
            print(f"{label} FAILED: {e!r}", flush=True)
            results.append((source["name"], "error", 0, 0, repr(e)))

    print("\n=== QA SUMMARY ===", flush=True)
    for name, status, found, new, err in results:
        if status == "ok":
            print(f"  OK     {name}: found={found} new={new}", flush=True)
        elif status == "capped":
            print(f"  CAPPED {name}: classified {new} (stopped at --max-per-source)", flush=True)
        else:
            print(f"  FAIL   {name}: {err}", flush=True)

    conn.close()


if __name__ == "__main__":
    main()
