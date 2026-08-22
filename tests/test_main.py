import logging
from app.main import _ExcludeTasksActiveFilter


def _record(message: str) -> logging.LogRecord:
    return logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, message, (), None)


def test_filters_out_tasks_active_requests():
    assert _ExcludeTasksActiveFilter().filter(_record('127.0.0.1 - "GET /tasks/active HTTP/1.1" 200')) is False


def test_keeps_other_requests():
    assert _ExcludeTasksActiveFilter().filter(_record('127.0.0.1 - "GET /jobs HTTP/1.1" 200')) is True
