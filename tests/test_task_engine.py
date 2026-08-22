import sqlite3
import threading
import time
import pytest
from app.db.schema import init_db
from app.db import queries as q
from app import task_engine as te


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:", check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    init_db(c)
    yield c
    c.close()


def test_register_task_kind_adds_to_registry():
    @te.register_task_kind("test_kind_registration")
    def fn(conn, client, model, config, params):
        return {}
        yield  # pragma: no cover
    assert te.TASK_KINDS["test_kind_registration"] is fn
    del te.TASK_KINDS["test_kind_registration"]


def test_execute_task_runs_generator_and_stores_log_and_result(conn):
    @te.register_task_kind("test_execute_success")
    def fn(conn, client, model, config, params):
        yield "step one"
        yield "step two"
        return {"html_chunks": ["<p>done</p>"]}

    task = q.enqueue_task(conn, kind="test_execute_success", params={})
    te.execute_task(conn, None, None, None, task)
    fetched = q.get_task(conn, task["id"])
    assert fetched["status"] == "done"
    assert fetched["log"] == "step one\nstep two\n"
    assert fetched["result"] == {"html_chunks": ["<p>done</p>"]}
    del te.TASK_KINDS["test_execute_success"]


def test_execute_task_marks_failed_on_exception(conn):
    @te.register_task_kind("test_execute_failure")
    def fn(conn, client, model, config, params):
        yield "about to fail"
        raise ValueError("boom")

    task = q.enqueue_task(conn, kind="test_execute_failure", params={})
    te.execute_task(conn, None, None, None, task)
    fetched = q.get_task(conn, task["id"])
    assert fetched["status"] == "failed"
    assert fetched["error"] == "boom"
    del te.TASK_KINDS["test_execute_failure"]


def test_execute_task_unknown_kind_fails_immediately(conn):
    task = q.enqueue_task(conn, kind="no_such_kind", params={})
    te.execute_task(conn, None, None, None, task)
    fetched = q.get_task(conn, task["id"])
    assert fetched["status"] == "failed"
    assert "no_such_kind" in fetched["error"]


def test_execute_task_creates_inbox_item_when_needs_action(conn):
    @te.register_task_kind("test_execute_needs_action")
    def fn(conn, client, model, config, params):
        yield "checking"
        return {"needs_action": True, "action_message": "please decide", "action_link": "/somewhere"}

    task = q.enqueue_task(conn, kind="test_execute_needs_action", params={})
    te.execute_task(conn, None, None, None, task)
    items = q.get_unresolved_inbox_items(conn)
    assert len(items) == 1
    assert items[0]["message"] == "please decide"
    assert items[0]["link"] == "/somewhere"
    assert q.get_inbox_item_by_task_id(conn, task["id"])["id"] == items[0]["id"]
    del te.TASK_KINDS["test_execute_needs_action"]


def test_execute_task_needs_action_defaults_link_to_resume(conn):
    @te.register_task_kind("test_execute_needs_action_resume")
    def fn(conn, client, model, config, params):
        yield "checking"
        return {"needs_action": True, "action_message": "decide", "resume_html": "<p>panel</p>"}

    task = q.enqueue_task(conn, kind="test_execute_needs_action_resume", params={})
    te.execute_task(conn, None, None, None, task)
    items = q.get_unresolved_inbox_items(conn)
    assert items[0]["link"] == f"/tasks/{task['id']}/resume"
    del te.TASK_KINDS["test_execute_needs_action_resume"]


def test_run_worker_forever_processes_queued_task_then_stops(conn, monkeypatch):
    processed = []

    @te.register_task_kind("test_worker_loop")
    def fn(conn, client, model, config, params):
        processed.append(params["n"])
        return {}
        yield  # pragma: no cover

    monkeypatch.setattr(te, "_open_db", lambda config: conn)
    monkeypatch.setattr(te, "load_config", lambda: object())
    monkeypatch.setattr(te, "get_ai_client", lambda: None)
    monkeypatch.setattr(te, "get_model", lambda: None)

    q.enqueue_task(conn, kind="test_worker_loop", params={"n": 1})
    stop_event = threading.Event()
    thread = threading.Thread(target=te.run_worker_forever, args=(stop_event, 0.05))
    thread.start()
    for _ in range(40):
        if processed:
            break
        time.sleep(0.05)
    stop_event.set()
    thread.join(timeout=2)
    assert processed == [1]
    del te.TASK_KINDS["test_worker_loop"]
