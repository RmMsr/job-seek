from __future__ import annotations
import logging
from app.config import Config

logger = logging.getLogger("job_seek")
_initialized = False


def _setup(config: Config) -> None:
    import openlit

    # openlit probes ~60 libraries and logs an INFO line for each one missing.
    logging.getLogger("openlit").setLevel(logging.WARNING)
    openlit.init(
        otlp_endpoint=config.tracing_endpoint,
        application_name=config.tracing_project,
        environment="production",
        disable_batch=False,
        capture_message_content=True,
        # Only LLM calls are of interest; per-request HTTP spans (incl. the
        # 2s /tasks/active poll) would bury them.
        disabled_instrumentors=["fastapi", "starlette", "httpx", "requests", "urllib", "urllib3"],
    )


def init_tracing(config: Config) -> None:
    """Enable OpenLit tracing of LLM calls when [tracing].endpoint is set.
    No-op (and imports nothing) otherwise. Safe to call more than once."""
    global _initialized
    if _initialized or not config.tracing_endpoint:
        return
    try:
        _setup(config)
        _initialized = True
        logger.info("LLM tracing enabled via OpenLit -> %s", config.tracing_endpoint)
    except Exception:
        logger.warning("LLM tracing setup failed; continuing without it", exc_info=True)