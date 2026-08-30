from __future__ import annotations
import logging
import threading
from typing import Callable, Generator
import openai
from app.config import Config, load_config
from app.deps import _open_db, get_ai_client, get_model
from app.db import queries as q
from app.fetchers.playwright_pool import browser_install_missing

logger = logging.getLogger("job_seek")

TaskKindFn = Callable[..., Generator[str, None, dict]]
TASK_KINDS: dict[str, TaskKindFn] = {}

_BROWSER_MISSING_INBOX_MESSAGE = (
    "The headless browser needed to read JavaScript-rendered pages isn't installed. "
    "Run `playwright install chromium` on the server, then re-run the fetch."
)


def _sync_browser_missing_inbox(conn) -> None:
    """Keep a single unresolved 'browser_missing' inbox item in step with whether
    the last headless-browser launch failed for lack of the binary. Runs after
    every task so it appears once a render is actually attempted and clears
    itself once the browser works again."""
    open_item = conn.execute(
        "SELECT id FROM inbox_items WHERE kind = 'browser_missing' AND resolved_at IS NULL"
    ).fetchone()
    if browser_install_missing():
        if open_item is None:
            q.create_inbox_item(
                conn, kind="browser_missing", message=_BROWSER_MISSING_INBOX_MESSAGE, link="/sources",
            )
    elif open_item is not None:
        q.resolve_inbox_item(conn, open_item["id"])


def register_task_kind(kind: str) -> Callable[[TaskKindFn], TaskKindFn]:
    def decorator(fn: TaskKindFn) -> TaskKindFn:
        TASK_KINDS[kind] = fn
        return fn
    return decorator


def execute_task(
    conn, client: openai.OpenAI | None, model: str | None, config: Config | None, task: dict
) -> None:
    kind_fn = TASK_KINDS.get(task["kind"])
    if kind_fn is None:
        q.fail_task(conn, task["id"], f"Unknown task kind: {task['kind']!r}")
        return
    # Kind fns that spawn child steps (e.g. fetch_all) need the running task's
    # own id; pass it (and the parent) alongside the persisted params without
    # mutating the stored row.
    call_params = {
        **task["params"],
        "_task_id": task["id"],
        "_parent_task_id": task.get("parent_task_id"),
    }
    try:
        gen = kind_fn(conn, client, model, config, call_params)
        result: dict = {}
        try:
            while True:
                line = next(gen)
                q.append_task_log(conn, task["id"], line)
        except StopIteration as stop:
            result = stop.value or {}
    except Exception as exc:
        logger.exception("Task %s (%s) failed", task["id"], task["kind"])
        q.fail_task(conn, task["id"], str(exc))
        return
    finally:
        _sync_browser_missing_inbox(conn)
    # Sentinel substitution: a needs_action panel rendered by a kind fn can't
    # know its task's id, so it embeds "__ORIGIN_TASK__" in a hidden
    # origin_task_id field; swap in this action's *root* id (its own id when it
    # has no parent) so a spawned follow-up step attaches to the right root.
    _root = str(task.get("parent_task_id") or task["id"])
    if result.get("resume_html"):
        result["resume_html"] = result["resume_html"].replace("__ORIGIN_TASK__", _root)
    if result.get("html_chunks"):
        result["html_chunks"] = [
            c.replace("__ORIGIN_TASK__", _root) if isinstance(c, str) else c
            for c in result["html_chunks"]
        ]
    q.complete_task(conn, task["id"], result)      # persist result JSON
    if result.get("needs_action"):
        q.set_task_needs_action(conn, task["id"])  # then flip status off 'done'


def run_worker_forever(stop_event: threading.Event, poll_interval: float = 1.0) -> None:
    conn = _open_db(load_config())
    try:
        recovered = q.recover_interrupted_tasks(conn)
        if recovered:
            logger.info("Marked %d interrupted task(s) as failed on startup", recovered)
        while not stop_event.is_set():
            task = q.claim_next_task(conn)
            if task is None:
                stop_event.wait(poll_interval)
                continue
            execute_task(conn, get_ai_client(), get_model(), load_config(), task)
    finally:
        conn.close()
