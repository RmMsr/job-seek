import sqlite3
import threading
import time
from unittest.mock import patch
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


def test_cancel_task_sets_cancelled_and_finished(conn):
    t = q.enqueue_task(conn, kind="fetch_all", params={})
    q.cancel_task(conn, t["id"])
    got = q.get_task(conn, t["id"])
    assert got["status"] == "cancelled"
    assert got["finished_at"] is not None


def test_execute_task_stops_when_cancel_requested(conn):
    @te.register_task_kind("test_execute_cancel")
    def fn(conn, client, model, config, params):
        for i in range(10):
            yield f"step {i}"
        return {"html_chunks": []}

    task = q.enqueue_task(conn, kind="test_execute_cancel", params={})
    q.claim_next_task(conn)  # mark running, like the worker would

    real_append = q.append_task_log

    def append_and_maybe_cancel(c, tid, line):
        real_append(c, tid, line)
        if line == "step 0":
            te.request_cancel(tid)

    with patch("app.task_engine.q.append_task_log", side_effect=append_and_maybe_cancel):
        te.execute_task(conn, None, None, None, q.get_task(conn, task["id"]))

    got = q.get_task(conn, task["id"])
    assert got["status"] == "cancelled"
    assert got["error"] is None
    assert "step 0" in got["log"]
    assert "step 9" not in got["log"]
    assert not te.cancel_requested(task["id"])  # cleared in finally
    del te.TASK_KINDS["test_execute_cancel"]


def test_execute_task_honors_pre_run_cancel_flag(conn):
    @te.register_task_kind("test_execute_cancel_pre")
    def fn(conn, client, model, config, params):
        yield "only step"
        return {}

    task = q.enqueue_task(conn, kind="test_execute_cancel_pre", params={})
    te.request_cancel(task["id"])  # request lands before the worker starts the run
    te.execute_task(conn, None, None, None, q.get_task(conn, task["id"]))
    assert q.get_task(conn, task["id"])["status"] == "cancelled"
    assert not te.cancel_requested(task["id"])  # finally clears it
    del te.TASK_KINDS["test_execute_cancel_pre"]


def test_execute_task_clears_cancel_flag_on_normal_completion(conn):
    @te.register_task_kind("test_execute_normal_clear")
    def fn(conn, client, model, config, params):
        yield "step"
        return {}

    task = q.enqueue_task(conn, kind="test_execute_normal_clear", params={})
    te.execute_task(conn, None, None, None, task)
    assert q.get_task(conn, task["id"])["status"] == "done"
    assert not te.cancel_requested(task["id"])
    del te.TASK_KINDS["test_execute_normal_clear"]


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


def test_execute_task_creates_one_browser_missing_inbox_item(conn):
    @te.register_task_kind("test_browser_missing")
    def fn(conn, client, model, config, params):
        return {}
        yield  # pragma: no cover

    with patch("app.task_engine.browser_install_missing", return_value=True):
        for _ in range(2):
            task = q.enqueue_task(conn, kind="test_browser_missing", params={})
            te.execute_task(conn, None, None, None, task)

    items = [i for i in q.get_unresolved_inbox_items(conn) if i["kind"] == "browser_missing"]
    assert len(items) == 1
    assert "playwright install" in items[0]["message"]
    del te.TASK_KINDS["test_browser_missing"]


def test_execute_task_resolves_browser_missing_inbox_when_recovered(conn):
    q.create_inbox_item(conn, kind="browser_missing", message="stale", link="/sources")

    @te.register_task_kind("test_browser_ok")
    def fn(conn, client, model, config, params):
        return {}
        yield  # pragma: no cover

    task = q.enqueue_task(conn, kind="test_browser_ok", params={})
    with patch("app.task_engine.browser_install_missing", return_value=False):
        te.execute_task(conn, None, None, None, task)

    assert not [i for i in q.get_unresolved_inbox_items(conn) if i["kind"] == "browser_missing"]
    del te.TASK_KINDS["test_browser_ok"]


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


def test_needs_action_result_sets_needs_action_status(conn):
    @te.register_task_kind("_t_needs")
    def _gen(conn, client, model, config, params):
        yield "working"
        return {"needs_action": True, "resume_html": "<p>confirm</p>"}

    t = q.enqueue_task(conn, kind="_t_needs", params={})
    te.execute_task(conn, None, None, None, q.get_task(conn, t["id"]))
    row = q.get_task(conn, t["id"])
    assert row["status"] == "needs_action"
    assert row["finished_at"] is not None
    assert conn.execute("SELECT COUNT(*) FROM inbox_items").fetchone()[0] == 0
    del te.TASK_KINDS["_t_needs"]


def test_plain_result_still_completes(conn):
    @te.register_task_kind("_t_plain")
    def _gen(conn, client, model, config, params):
        yield "x"
        return {"html_chunks": []}

    t = q.enqueue_task(conn, kind="_t_plain", params={})
    te.execute_task(conn, None, None, None, q.get_task(conn, t["id"]))
    assert q.get_task(conn, t["id"])["status"] == "done"
    del te.TASK_KINDS["_t_plain"]


def test_origin_task_sentinel_gets_root_id(conn):
    # A root task (no parent): the sentinel resolves to its own id.
    @te.register_task_kind("_t_root")
    def _gen(conn, client, model, config, params):
        yield "x"
        return {"needs_action": True,
                "resume_html": '<input value="__ORIGIN_TASK__">',
                "html_chunks": ['<form data-x="__ORIGIN_TASK__">']}

    t = q.enqueue_task(conn, kind="_t_root", params={})
    te.execute_task(conn, None, None, None, q.get_task(conn, t["id"]))
    row = q.get_task(conn, t["id"])
    assert row["result"]["resume_html"] == f'<input value="{t["id"]}">'
    assert row["result"]["html_chunks"] == [f'<form data-x="{t["id"]}">']
    del te.TASK_KINDS["_t_root"]


def test_origin_task_sentinel_flattens_to_root_for_a_child(conn):
    root = q.enqueue_task(conn, kind="fetch_all", params={})

    @te.register_task_kind("_t_child")
    def _gen(conn, client, model, config, params):
        yield "x"
        return {"needs_action": True, "resume_html": '<input value="__ORIGIN_TASK__">'}

    c = q.enqueue_task(conn, kind="_t_child", params={}, parent_task_id=root["id"])
    te.execute_task(conn, None, None, None, q.get_task(conn, c["id"]))
    assert q.get_task(conn, c["id"])["result"]["resume_html"] == f'<input value="{root["id"]}">'
    del te.TASK_KINDS["_t_child"]


def test_kind_fn_receives_own_task_id(conn):
    seen = {}

    @te.register_task_kind("_t_selfid")
    def _gen(conn, client, model, config, params):
        seen["id"] = params["_task_id"]
        return {}
        yield  # pragma: no cover

    t = q.enqueue_task(conn, kind="_t_selfid", params={})
    te.execute_task(conn, None, None, None, q.get_task(conn, t["id"]))
    assert seen["id"] == t["id"]
    # not persisted onto the stored params
    assert "_task_id" not in q.get_task(conn, t["id"])["params"]
    del te.TASK_KINDS["_t_selfid"]


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
