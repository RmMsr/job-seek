from __future__ import annotations
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from app.routes import jobs

app = FastAPI(title="Job Seek")
app.include_router(jobs.router)
