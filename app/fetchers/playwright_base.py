from __future__ import annotations
import logging
import time
from pathlib import Path
from typing import Generator
from playwright.sync_api import sync_playwright, BrowserContext
from app.fetchers.base import RawJob

logger = logging.getLogger("job_seek")


class PlaywrightFetcher:
    LOGIN_TIMEOUT_SECONDS = 300
    LOGIN_POLL_SECONDS = 2
    LOGIN_PROGRESS_EVERY_SECONDS = 10

    def __init__(self, source: dict, profile_dir: str) -> None:
        self._source = source
        self._profile_dir = profile_dir

    def _profile_path(self) -> str:
        source_slug = self._source["name"].lower().replace(" ", "_")
        path = Path(self._profile_dir) / source_slug
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    def _launch(self, playwright, *, headless: bool) -> BrowserContext:
        return playwright.chromium.launch_persistent_context(
            self._profile_path(),
            headless=headless,
        )

    def _needs_login(self, page) -> bool:
        return False

    def _target_url(self) -> str:
        return self._source["url"]

    def fetch(self) -> list[RawJob]:
        with sync_playwright() as pw:
            return self._fetch_with(pw)

    def _fetch_with(self, playwright) -> list[RawJob]:
        ctx = self._launch(playwright, headless=True)
        try:
            page = ctx.new_page()
            page.goto(self._target_url(), wait_until="domcontentloaded", timeout=30000)
            if self._needs_login(page):
                ctx.close()
                ctx = self._launch(playwright, headless=False)
                page = ctx.new_page()
                page.goto(self._target_url(), wait_until="domcontentloaded", timeout=30000)
                logger.info(
                    "Log in to %s in the browser window; waiting up to %ds",
                    self._source["name"], self.LOGIN_TIMEOUT_SECONDS,
                )
                self._wait_for_login(page)
            return self._extract(page)
        finally:
            ctx.close()

    def _wait_for_login(self, page) -> None:
        for _ in self._wait_for_login_with_progress(page):
            pass

    def _wait_for_login_with_progress(self, page) -> Generator[str, None, None]:
        deadline = time.monotonic() + self.LOGIN_TIMEOUT_SECONDS
        last_update = time.monotonic()
        while self._needs_login(page):
            now = time.monotonic()
            if now >= deadline:
                raise RuntimeError(
                    f"Timed out waiting for login to {self._source['name']}"
                )
            if now - last_update >= self.LOGIN_PROGRESS_EVERY_SECONDS:
                yield f"Still waiting for login to {self._source['name']}... ({int(deadline - now)}s remaining)"
                last_update = now
            page.wait_for_timeout(self.LOGIN_POLL_SECONDS * 1000)

    def check_needs_login(self) -> bool:
        with sync_playwright() as pw:
            return self._check_needs_login_with(pw)

    def _check_needs_login_with(self, playwright) -> bool:
        ctx = self._launch(playwright, headless=True)
        try:
            page = ctx.new_page()
            page.goto(self._target_url(), wait_until="domcontentloaded", timeout=30000)
            return self._needs_login(page)
        finally:
            ctx.close()

    def login(self) -> Generator[str, None, bool]:
        with sync_playwright() as pw:
            result = yield from self._login_with(pw)
            return result

    def _login_with(self, playwright) -> Generator[str, None, bool]:
        ctx = self._launch(playwright, headless=True)
        try:
            page = ctx.new_page()
            page.goto(self._target_url(), wait_until="domcontentloaded", timeout=30000)
            if not self._needs_login(page):
                yield f"Already logged in to {self._source['name']}."
                return True
            ctx.close()
            ctx = self._launch(playwright, headless=False)
            page = ctx.new_page()
            page.goto(self._target_url(), wait_until="domcontentloaded", timeout=30000)
            yield f"Log in to {self._source['name']} in the browser window that just opened..."
            try:
                yield from self._wait_for_login_with_progress(page)
            except RuntimeError as exc:
                yield str(exc)
                return False
            yield "Login successful."
            return True
        finally:
            ctx.close()

    def _extract(self, page) -> list[RawJob]:
        content = page.content()
        return [RawJob(url=self._source["url"], title="", company="", raw_text=content)]
