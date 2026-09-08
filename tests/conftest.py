import importlib
import sqlite3
import pytest
from fastapi.testclient import TestClient
from app.db.schema import init_db
from app.deps import get_db
from app.main import app

# The CV feature is off by default in config.toml ([cv].enabled is unset),
# but most route surface this page family should be exercised as if it were
# on. Patch the feature flag at every import site so the TestClient behaves
# as though [cv].enabled = true unless an individual test explicitly flips
# it back to False. Each module imports `cv_enabled` eagerly (`from app.config
# import cv_enabled`), so patching only app.config would leave those bound
# references returning the real (disabled) value.
_CV_ENABLED_MODULES = (
    "app.config",
    "app.deps",
    "app.routes.jobs",
    "app.routes.sources",
    "app.routes.setup",
)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:", check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    init_db(c)
    yield c
    c.close()


@pytest.fixture
def client(conn, monkeypatch):
    for name in _CV_ENABLED_MODULES:
        monkeypatch.setattr(importlib.import_module(name), "cv_enabled", lambda *_a, **_k: True)
    app.dependency_overrides[get_db] = lambda: conn
    yield TestClient(app)
    app.dependency_overrides.clear()
