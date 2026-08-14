import time
from unittest.mock import MagicMock, patch
from app.fetchers.playwright_pool import BrowserPool


def _mock_playwright():
    """Returns (sync_playwright_mock, pw, browser, page). Mirrors how a real
    `sync_playwright().start()` -> `pw.chromium.launch()` -> `browser.new_context()`
    -> `ctx.new_page()` chain is shaped, minus the `with` block since BrowserPool
    calls `.start()`/`.stop()` directly instead of using `sync_playwright()` as a
    context manager (it needs to hold the Playwright instance open across calls)."""
    sync_playwright_mock = MagicMock()
    pw = MagicMock()
    sync_playwright_mock.return_value.start.return_value = pw
    browser = MagicMock()
    pw.chromium.launch.return_value = browser
    ctx = MagicMock()
    browser.new_context.return_value = ctx
    page = MagicMock()
    ctx.new_page.return_value = page
    page.content.return_value = "<html>rendered</html>"
    return sync_playwright_mock, pw, browser, page


def test_render_returns_page_content():
    sync_playwright_mock, pw, browser, page = _mock_playwright()
    pool = BrowserPool(idle_timeout_seconds=1.0)
    with patch("app.fetchers.playwright_pool.sync_playwright", sync_playwright_mock):
        html = pool.render("https://example.com")
    assert html == "<html>rendered</html>"
    page.goto.assert_called_once_with("https://example.com", wait_until="networkidle", timeout=30000)


def test_render_reuses_browser_across_calls_within_idle_window():
    sync_playwright_mock, pw, browser, page = _mock_playwright()
    pool = BrowserPool(idle_timeout_seconds=1.0)
    with patch("app.fetchers.playwright_pool.sync_playwright", sync_playwright_mock):
        pool.render("https://example.com/a")
        pool.render("https://example.com/b")
    assert pw.chromium.launch.call_count == 1
    assert browser.new_context.call_count == 2


def test_render_closes_browser_after_idle_timeout_and_relaunches():
    sync_playwright_mock, pw, browser, page = _mock_playwright()
    pool = BrowserPool(idle_timeout_seconds=0.05)
    with patch("app.fetchers.playwright_pool.sync_playwright", sync_playwright_mock):
        pool.render("https://example.com")
        time.sleep(0.2)
        assert browser.close.call_count == 1
        assert pw.stop.call_count == 1
        pool.render("https://example.com/again")
    assert pw.chromium.launch.call_count == 2


def test_render_returns_none_on_navigation_error():
    sync_playwright_mock, pw, browser, page = _mock_playwright()
    page.goto.side_effect = RuntimeError("timeout")
    pool = BrowserPool(idle_timeout_seconds=1.0)
    with patch("app.fetchers.playwright_pool.sync_playwright", sync_playwright_mock):
        html = pool.render("https://example.com")
    assert html is None
