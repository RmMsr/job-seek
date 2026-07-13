from __future__ import annotations
from pathlib import Path
from playwright.sync_api import sync_playwright, BrowserContext
from app.fetchers.base import RawJob

_SESSION_MARKER = "session_established"


class PlaywrightFetcher:
    def __init__(self, source: dict, profile_dir: str) -> None:
        self._source = source
        self._profile_dir = profile_dir

    def _profile_path(self) -> str:
        source_slug = self._source["name"].lower().replace(" ", "_")
        path = Path(self._profile_dir) / source_slug
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    def _is_session_established(self) -> bool:
        return (Path(self._profile_path()) / _SESSION_MARKER).exists()

    def _mark_session_established(self) -> None:
        (Path(self._profile_path()) / _SESSION_MARKER).touch()

    def _get_context(self, playwright) -> BrowserContext:
        headless = self._is_session_established()
        return playwright.chromium.launch_persistent_context(
            self._profile_path(),
            headless=headless,
        )

    def fetch(self) -> list[RawJob]:
        with sync_playwright() as pw:
            ctx = self._get_context(pw)
            try:
                page = ctx.new_page()
                page.goto(self._source["url"], wait_until="networkidle", timeout=30000)
                if not self._is_session_established():
                    input(
                        f"\n[job-seek] Log in to {self._source['name']} in the browser window, "
                        "then press ENTER here to continue..."
                    )
                    self._mark_session_established()
                    page.reload(wait_until="networkidle")
                jobs = self._extract(page)
                return jobs
            finally:
                ctx.close()

    def _extract(self, page) -> list[RawJob]:
        content = page.content()
        return [RawJob(url=self._source["url"], title="", company="", raw_text=content)]
