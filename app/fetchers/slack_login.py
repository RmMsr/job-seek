from __future__ import annotations
import logging
from typing import Generator
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright
from app.fetchers.playwright_base import PlaywrightFetcher
from app.fetchers.slack import _resolve_target

logger = logging.getLogger("job_seek")


class SlackCookieLogin(PlaywrightFetcher):
    """Optional, display-dependent tool: opens Slack in a headful browser so the
    user can log in, then extracts the `d` session cookie. Not used on the fetch
    path — only to mint the cookie SlackFetcher later consumes."""

    def __init__(self, source: dict, profile_dir: str) -> None:
        super().__init__(source, profile_dir)
        self._resolved_url, self._workspace, self._channel_id_value = _resolve_target(source["url"])

    def _target_url(self) -> str:
        return self._resolved_url

    def _channel_id(self) -> str:
        return self._channel_id_value

    def _needs_login(self, page) -> bool:
        return self._channel_id() not in urlparse(page.url).path

    def login(self) -> Generator[str, None, str | None]:
        with sync_playwright() as pw:
            cookie = yield from self._login_and_extract(pw)
            return cookie

    def _login_and_extract(self, playwright) -> Generator[str, None, str | None]:
        ctx = self._launch(playwright, headless=True)
        try:
            page = ctx.new_page()
            page.goto(self._target_url(), wait_until="domcontentloaded", timeout=30000)
            if self._needs_login(page):
                ctx.close()
                ctx = self._launch(playwright, headless=False)
                page = ctx.new_page()
                page.goto(self._target_url(), wait_until="domcontentloaded", timeout=30000)
                yield f"Log in to {self._source['name']} in the browser window that just opened..."
                try:
                    yield from self._wait_for_login_with_progress(page)
                except RuntimeError as exc:
                    yield str(exc)
                    return None
            cookie = next((c["value"] for c in ctx.cookies() if c["name"] == "d"), None)
            if cookie:
                yield "Login successful; captured session cookie."
            else:
                yield "Login finished but no `d` session cookie was found."
            return cookie
        finally:
            ctx.close()
