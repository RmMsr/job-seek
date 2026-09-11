from __future__ import annotations
import logging
import queue
import re
import threading
from playwright.sync_api import sync_playwright

logger = logging.getLogger("job_seek")

DEFAULT_IDLE_TIMEOUT_SECONDS = 10.0
DEFAULT_TIMEOUT_MS = 30000
SETTLE_MS = 2000  # fixed pause after domcontentloaded for client-side rendering to finish;
                  # networkidle never settles on pages with analytics/consent beacons
MAX_BROWSER_INSTANCES = 1  # single dedicated worker thread owns at most one browser at a time

# Playwright's "the browser binary isn't downloaded" launch error — an operator
# setup problem fixable with `playwright install`, distinct from a page that just
# won't render.
_BROWSER_MISSING_RE = re.compile(r"Executable doesn't exist|playwright install", re.IGNORECASE)

# Sentinel queue item telling the worker thread to exit its loop, distinct from
# a real (url, timeout_ms, result_q) render job.
_STOP = object()


class BrowserPool:
    """Owns a single headless Chromium instance on a dedicated background
    thread. Playwright's sync API is thread-affine — a browser/page object
    may only be touched from the thread that started it — so this is the one
    thread that ever calls into Playwright; callers on any other thread
    submit a render job and block for the result. The browser is closed
    after `idle_timeout_seconds` with no jobs and relaunched lazily on the
    next call.
    """

    def __init__(self, idle_timeout_seconds: float = DEFAULT_IDLE_TIMEOUT_SECONDS) -> None:
        self._idle_timeout_seconds = idle_timeout_seconds
        self._jobs: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        # Set when a launch fails for lack of the browser binary; cleared on the
        # next successful launch. Read via browser_install_missing().
        self.browser_missing = False

    def render(self, url: str, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> str | None:
        logger.info("Escalating to headless-browser render: %s", url)
        self._ensure_thread()
        result_q: queue.Queue = queue.Queue(maxsize=1)
        self._jobs.put((url, timeout_ms, result_q))
        status, payload = result_q.get()
        return payload if status == "ok" else None

    def _ensure_thread(self) -> None:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._worker_loop, daemon=True)
                self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Signal the worker thread to exit and wait for it. A no-op if no
        thread has been started (nothing has called render() yet)."""
        with self._lock:
            thread = self._thread
        if thread is None or not thread.is_alive():
            return
        self._jobs.put(_STOP)
        thread.join(timeout=timeout)

    def _worker_loop(self) -> None:
        pw = None
        browser = None
        while True:
            try:
                item = self._jobs.get(timeout=self._idle_timeout_seconds)
            except queue.Empty:
                if browser is not None:
                    browser.close()
                    pw.stop()
                    browser = None
                    pw = None
                    logger.info(
                        "Playwright browser pool: closed idle browser (active=0/%d)",
                        MAX_BROWSER_INSTANCES,
                    )
                continue
            if item is _STOP:
                if browser is not None:
                    browser.close()
                    pw.stop()
                return
            url, timeout_ms, result_q = item
            try:
                if browser is None:
                    pw = sync_playwright().start()
                    browser = pw.chromium.launch(headless=True)
                    self.browser_missing = False
                    logger.info(
                        "Playwright browser pool: launched browser (active=1/%d)",
                        MAX_BROWSER_INSTANCES,
                    )
                html = self._render_one(browser, url, timeout_ms)
                result_q.put(("ok", html))
            except Exception as exc:
                if _BROWSER_MISSING_RE.search(str(exc)):
                    self.browser_missing = True
                    logger.warning(
                        "Playwright browser is not installed — JavaScript-rendered pages "
                        "cannot be fetched. Run `playwright install chromium` on the server. "
                        "(while rendering %s)", url,
                    )
                else:
                    logger.exception("Playwright render failed for %s", url)
                result_q.put(("error", None))
                if browser is None and pw is not None:
                    # launch failed mid-init; drop the half-started Playwright so
                    # the next job retries from a clean state
                    try:
                        pw.stop()
                    except Exception:
                        pass
                    pw = None

    def _render_one(self, browser, url: str, timeout_ms: int) -> str:
        ctx = browser.new_context()
        try:
            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(SETTLE_MS)
            return page.content()
        finally:
            ctx.close()


_pool = BrowserPool()


def render_html(url: str, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> str | None:
    return _pool.render(url, timeout_ms)


def browser_install_missing() -> bool:
    """True if a headless-browser launch has failed because the browser binary
    is not installed. Cleared on the next successful launch."""
    return _pool.browser_missing
