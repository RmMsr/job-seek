from __future__ import annotations
import logging
from fastapi import FastAPI
from app.routes import home, jobs, fetch, profile, scenarios, sources, setup

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="Job Seek")
app.include_router(home.router)
app.include_router(jobs.router)
app.include_router(fetch.router)
app.include_router(profile.router)
app.include_router(scenarios.router)
app.include_router(sources.router)
app.include_router(setup.router)
