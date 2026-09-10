from __future__ import annotations
import logging
from app.config import Config

logger = logging.getLogger("job_seek")
_initialized = False


def _setup(config: Config) -> None:
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from openinference.instrumentation.openai import OpenAIInstrumentor

    provider = TracerProvider(
        resource=Resource.create({"service.name": config.tracing_project})
    )
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=config.tracing_endpoint))
    )
    trace.set_tracer_provider(provider)
    OpenAIInstrumentor().instrument()


def init_tracing(config: Config) -> None:
    """Enable OpenInference tracing of LLM calls when [tracing].endpoint is set.
    No-op (and imports nothing) otherwise. Safe to call more than once."""
    global _initialized
    if _initialized or not config.tracing_endpoint:
        return
    try:
        _setup(config)
        _initialized = True
        logger.info("LLM tracing enabled -> %s", config.tracing_endpoint)
    except Exception:
        logger.warning("LLM tracing setup failed; continuing without it", exc_info=True)
