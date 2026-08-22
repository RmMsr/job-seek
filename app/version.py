from __future__ import annotations
import os
import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_git(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args], cwd=_REPO_ROOT, capture_output=True, text=True, timeout=2,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def get_app_version() -> str:
    """The running build's version — APP_VERSION is baked into the
    container image as the git short SHA at build time (see Containerfile
    and .gitlab-ci.yml). Outside a container, fall back to reading it
    straight from git for local dev."""
    return os.environ.get("APP_VERSION") or _run_git("rev-parse", "--short", "HEAD") or "dev"


def get_build_date() -> str:
    """Same idea as get_app_version, but the date the running build was
    made — APP_BUILD_DATE is baked in at container build time; outside a
    container, falls back to the current commit's date."""
    return os.environ.get("APP_BUILD_DATE") or _run_git("log", "-1", "--format=%cd", "--date=short") or "unknown"
