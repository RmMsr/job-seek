from __future__ import annotations
from fastapi import FastAPI
from app.routes import jobs, fetch

app = FastAPI(title="Job Seek")
app.include_router(jobs.router)
app.include_router(fetch.router)
