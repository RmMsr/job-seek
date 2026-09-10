from app.config import Config
import app.tracing as tr


def _cfg(**kw):
    base = dict(llm_endpoint="http://x", llm_model="m", llm_api_key="k",
                browser_profile_dir="j", db_path="j.db")
    base.update(kw)
    return Config(**base)


def test_init_tracing_noop_without_endpoint(monkeypatch):
    tr._initialized = False
    called = []
    monkeypatch.setattr(tr, "_setup", lambda cfg: called.append(cfg))
    tr.init_tracing(_cfg(tracing_endpoint=None))
    assert called == []


def test_init_tracing_sets_up_with_endpoint(monkeypatch):
    tr._initialized = False
    called = []
    monkeypatch.setattr(tr, "_setup", lambda cfg: called.append(cfg))
    tr.init_tracing(_cfg(tracing_endpoint="http://localhost:6006/v1/traces"))
    assert len(called) == 1


def test_init_tracing_idempotent(monkeypatch):
    tr._initialized = False
    called = []
    monkeypatch.setattr(tr, "_setup", lambda cfg: called.append(cfg))
    cfg = _cfg(tracing_endpoint="http://x/v1/traces")
    tr.init_tracing(cfg)
    tr.init_tracing(cfg)
    assert len(called) == 1
