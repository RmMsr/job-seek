from __future__ import annotations
import queue
import threading
from playwright.sync_api import sync_playwright

DEFAULT_IDLE_TIMEOUT_SECONDS = 10.0
DEFAULT_TIMEOUT_MS = 30000


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

    def render(self, url: str, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> str | None:
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

    def _worker_loop(self) -> None:
        pw = None
        browser = None
        while True:
            try:
                url, timeout_ms, result_q = self._jobs.get(timeout=self._idle_timeout_seconds)
            except queue.Empty:
                if browser is not None:
                    browser.close()
                    pw.stop()
                    browser = None
                    pw = None
                continue
            try:
                if browser is None:
                    pw = sync_playwright().start()
                    browser = pw.chromium.launch(headless=True)
                html = self._render_one(browser, url, timeout_ms)
                result_q.put(("ok", html))
            except Exception:
                result_q.put(("error", None))

    def _render_one(self, browser, url: str, timeout_ms: int) -> str:
        ctx = browser.new_context()
        try:
            page = ctx.new_page()
            page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            return page.content()
        finally:
            ctx.close()


_pool = BrowserPool()


def render_html(url: str, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> str | None:
    return _pool.render(url, timeout_ms)
