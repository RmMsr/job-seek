import time
from unittest.mock import MagicMock, patch
from app.fetchers import playwright_pool as pp
from app.fetchers.playwright_pool import BrowserPool, browser_install_missing


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
    page.goto.assert_called_once_with(
        "https://example.com", wait_until="domcontentloaded", timeout=30000
    )
    page.wait_for_timeout.assert_called_once_with(2000)


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


def test_render_logs_escalation_to_headless_browser(caplog):
    sync_playwright_mock, pw, browser, page = _mock_playwright()
    pool = BrowserPool(idle_timeout_seconds=1.0)
    with caplog.at_level("INFO", logger="job_seek"), \
         patch("app.fetchers.playwright_pool.sync_playwright", sync_playwright_mock):
        pool.render("https://example.com/js-app")
    assert any(
        "escalat" in r.message.lower() and "https://example.com/js-app" in r.message
        for r in caplog.records
    )


def test_pool_logs_active_instance_count_on_launch_and_idle_close(caplog):
    sync_playwright_mock, pw, browser, page = _mock_playwright()
    pool = BrowserPool(idle_timeout_seconds=0.05)
    with caplog.at_level("INFO", logger="job_seek"), \
         patch("app.fetchers.playwright_pool.sync_playwright", sync_playwright_mock):
        pool.render("https://example.com")
        assert any("active=1/1" in r.message for r in caplog.records)
        time.sleep(0.2)
        assert any("active=0/1" in r.message for r in caplog.records)


_BROWSER_MISSING_ERROR = (
    "BrowserType.launch: Executable doesn't exist at "
    "/home/x/.cache/ms-playwright/chromium_headless_shell-1228/chrome-headless-shell\n"
    "Please run the following command to download new browsers: playwright install"
)


def test_render_flags_browser_missing_and_logs_actionable_warning(caplog):
    sync_playwright_mock, pw, browser, page = _mock_playwright()
    pw.chromium.launch.side_effect = RuntimeError(_BROWSER_MISSING_ERROR)
    pool = BrowserPool(idle_timeout_seconds=1.0)
    with caplog.at_level("INFO", logger="job_seek"), \
         patch("app.fetchers.playwright_pool.sync_playwright", sync_playwright_mock):
        html = pool.render("https://example.com/js-app")
    assert html is None
    assert pool.browser_missing is True
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert any("playwright install" in r.message for r in warnings)
    # actionable warning, not a raw traceback dump
    assert not any(r.exc_info for r in warnings)


def test_browser_missing_flag_clears_after_successful_launch():
    sync_playwright_mock, pw, browser, page = _mock_playwright()
    pool = BrowserPool(idle_timeout_seconds=1.0)
    pool.browser_missing = True
    with patch("app.fetchers.playwright_pool.sync_playwright", sync_playwright_mock):
        pool.render("https://example.com")
    assert pool.browser_missing is False


def test_browser_install_missing_reflects_pool_state():
    original = pp._pool.browser_missing
    try:
        pp._pool.browser_missing = True
        assert browser_install_missing() is True
        pp._pool.browser_missing = False
        assert browser_install_missing() is False
    finally:
        pp._pool.browser_missing = original


def test_render_logs_failure_with_exception_on_navigation_error(caplog):
    sync_playwright_mock, pw, browser, page = _mock_playwright()
    page.goto.side_effect = RuntimeError("nav timeout")
    pool = BrowserPool(idle_timeout_seconds=1.0)
    with caplog.at_level("INFO", logger="job_seek"), \
         patch("app.fetchers.playwright_pool.sync_playwright", sync_playwright_mock):
        html = pool.render("https://example.com/broken")
    assert html is None
    failure_records = [
        r for r in caplog.records
        if "https://example.com/broken" in r.message and r.levelname != "INFO"
    ]
    assert failure_records
    assert any(r.exc_info for r in failure_records)
