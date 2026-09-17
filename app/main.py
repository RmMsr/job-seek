from __future__ import annotations
import logging
import threading
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.routes import home, jobs, fetch, profile, scenarios, sources, setup, tasks, inbox, cv, cv_versions
from app.task_engine import run_worker_forever

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


class _ExcludeTasksActiveFilter(logging.Filter):
    """The status bar polls /tasks/active every 2s from every open tab —
    without this it drowns out everything else in the access log."""

    def filter(self, record: logging.LogRecord) -> bool:
        return "/tasks/active" not in record.getMessage()


logging.getLogger("uvicorn.access").addFilter(_ExcludeTasksActiveFilter())


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.config import check_config_status, load_config
    if check_config_status().ok:
        config = load_config()
        try:
            from app.tracing import init_tracing
            init_tracing(config)
        except Exception:
            logging.getLogger("job_seek").warning("tracing init skipped", exc_info=True)
        # Migrate once, synchronously, before the app (or the worker thread)
        # opens any other connection — request handlers use the lighter
        # app.deps._connect() and no longer run migrations themselves.
        from app.deps import _open_db
        _open_db(config).close()
    stop_event = threading.Event()
    worker_thread = threading.Thread(target=run_worker_forever, args=(stop_event,), daemon=True)
    worker_thread.start()
    yield
    stop_event.set()
    worker_thread.join(timeout=5)


app = FastAPI(title="Job Seek", lifespan=lifespan)
app.include_router(home.router)
app.include_router(jobs.router)
app.include_router(fetch.router)
app.include_router(profile.router)
app.include_router(scenarios.router)
app.include_router(sources.router)
app.include_router(setup.router)
app.include_router(tasks.router)
app.include_router(inbox.router)
app.include_router(cv.router)
app.include_router(cv_versions.router)
