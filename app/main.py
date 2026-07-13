from __future__ import annotations
from fastapi import FastAPI
from app.routes import jobs, fetch, profile, scenarios

app = FastAPI(title="Job Seek")
app.include_router(jobs.router)
app.include_router(fetch.router)
app.include_router(profile.router)
app.include_router(scenarios.router)
