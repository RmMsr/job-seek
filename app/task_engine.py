from __future__ import annotations
import logging
import threading
from typing import Callable, Generator
import openai
from app.config import Config, load_config
from app.deps import _open_db, get_ai_client, get_model
from app.db import queries as q

logger = logging.getLogger("job_seek")

TaskKindFn = Callable[..., Generator[str, None, dict]]
TASK_KINDS: dict[str, TaskKindFn] = {}


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
    try:
        gen = kind_fn(conn, client, model, config, task["params"])
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
    q.complete_task(conn, task["id"], result)
    if result.get("needs_action"):
        q.create_inbox_item(
            conn,
            kind="task_followup",
            message=result.get("action_message", "A task needs your attention"),
            link=result.get("action_link") or f"/tasks/{task['id']}/resume",
            task_id=task["id"],
        )


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
