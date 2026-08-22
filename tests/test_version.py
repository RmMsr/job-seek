import re
from app.version import get_app_version, get_build_date


def test_uses_app_version_env_var(monkeypatch):
    monkeypatch.setenv("APP_VERSION", "abc1234")
    assert get_app_version() == "abc1234"


def test_falls_back_to_git_sha_when_env_var_unset(monkeypatch):
    monkeypatch.delenv("APP_VERSION", raising=False)
    version = get_app_version()
    assert version == "dev" or len(version) >= 7


def test_uses_app_build_date_env_var(monkeypatch):
    monkeypatch.setenv("APP_BUILD_DATE", "2026-08-20")
    assert get_build_date() == "2026-08-20"


def test_falls_back_to_git_commit_date_when_env_var_unset(monkeypatch):
    monkeypatch.delenv("APP_BUILD_DATE", raising=False)
    build_date = get_build_date()
    assert build_date == "unknown" or re.match(r"^\d{4}-\d{2}-\d{2}$", build_date)
