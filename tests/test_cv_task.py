from unittest.mock import MagicMock, patch
import pytest
from app.db import queries as q
from app.task_engine import execute_task
from app.config import Config
from app.ai.tailor_cv import DirectiveProposal


@pytest.fixture
def cfg(tmp_path):
    db = tmp_path / "job-seek.db"
    return Config(llm_endpoint="http://x/v1", llm_model="m", llm_api_key="",
                  browser_profile_dir="b", db_path=str(db))


def _seed(conn):
    conn.execute("INSERT INTO sources (name, url, fetcher_type) VALUES ('s','http://x','manual')")
    conn.execute(
        "INSERT INTO jobs (source_id, url, title, company, simplified_content) "
        "VALUES (1,'http://x/1','Platform Engineer','Acme','We need Kafka and Terraform.')"
    )
    conn.commit()
    q.save_cv_settings(conn, base_cv="# Me\n\n- Kafka work\n", base_instruction="",
                       base_guardrails="", css="", default_scope=[1, 2])
    return 1


def _run(conn, cfg, job_id, mode):
    task = q.enqueue_task(conn, kind="cv_tailor", params={"job_id": job_id, "mode": mode})
    execute_task(conn, MagicMock(), "m", cfg, task)
    return q.get_task(conn, task["id"])


def test_persist_draft_snapshots_the_base_cv(conn):
    from app.routes.cv import _persist_draft
    jid = _seed(conn)
    settings = {"base_cv": "# Base v1\n\n- one\n", "base_guardrails": ""}
    _persist_draft(conn, jid, draft="# Tailored\n\n- one\n", findings=[], settings=settings)
    assert q.get_job_cv(conn, jid)["base_cv_snapshot"] == "# Base v1\n\n- one\n"


def test_plan_mode_first_visit_is_plan_only_no_draft(conn, cfg):
    jid = _seed(conn)
    q.save_cv_settings(conn, base_cv="# Me\n\n- Kafka work\n", base_instruction="",
                       base_guardrails="", css="", default_scope=[1, 2],
                       directives_template="## Skills match\n## Wording and typography")
    with patch("app.routes.cv.plan_tailoring", return_value={"directives": [
            DirectiveProposal(action="add", section="Skills match", rationale="r",
                              line="foreground Kafka", target=None)]}), \
         patch("app.routes.cv.tailor_cv") as mock_tailor, \
         patch("app.routes.cv.check_guardrails") as mock_check:
        task = _run(conn, cfg, jid, "plan")
    assert task["status"] == "done"
    row = q.get_job_cv(conn, jid)
    assert row["tuning_directives"].strip() == "## Skills match\n## Wording and typography"
    assert row["plan"][0]["line"] == "foreground Kafka"
    assert row["plan"][0]["section"] == "Skills match"
    assert row["plan_generated_at"] is not None
    assert row["generated_at"] is None       # no auto baseline draft
    assert not row["tailored_cv"]
    mock_tailor.assert_not_called()
    mock_check.assert_not_called()
    assert "cv" in [e["kind"] for e in q.get_job_events(conn, jid)]


def test_plan_mode_first_visit_empty_template_still_queues_proposals(conn, cfg):
    jid = _seed(conn)  # _seed saves settings without a template -> ''
    with patch("app.routes.cv.plan_tailoring", return_value={"directives": [
            DirectiveProposal(action="add", section="General", rationale="r",
                              line="foreground Kafka", target=None)]}):
        task = _run(conn, cfg, jid, "plan")
    row = q.get_job_cv(conn, jid)
    assert row["tuning_directives"] == ""          # nothing seeded, nothing auto-applied
    assert row["plan"][0]["line"] == "foreground Kafka"


def test_generate_keeps_prior_findings_when_guardrail_check_returns_empty(conn, cfg):
    jid = _seed(conn)
    q.save_cv_settings(conn, base_cv="# Me\n\n- Kafka work\n", base_instruction="",
                       base_guardrails="Do not invent dates.", css="", default_scope=[1])
    q.upsert_job_cv(conn, jid, scope=[1], tailored_cv="# old",
                    guardrail_findings=[{"rule": "Do not invent dates.", "verdict": "ok", "explanation": ""}])
    q.set_job_cv_directives(conn, jid, "- foreground Kafka")
    with patch("app.routes.cv.tailor_cv", return_value={"markdown": "# Me\n- new\n"}), \
         patch("app.routes.cv.check_guardrails", return_value={"findings": []}):  # transient failure
        _run(conn, cfg, jid, "generate")
    row = q.get_job_cv(conn, jid)
    assert "new" in row["tailored_cv"]                       # draft still updated
    assert row["guardrail_findings"][0]["rule"] == "Do not invent dates."  # findings NOT blanked


def test_plan_mode_does_not_overwrite_edited_directives(conn, cfg):
    jid = _seed(conn)
    q.upsert_job_cv(conn, jid, scope=[1, 2])
    q.set_job_cv_directives(conn, jid, "- my own directive")
    with patch("app.routes.cv.plan_tailoring", return_value={"directives": [
            DirectiveProposal(action="add", section="Wording and typography", rationale="r",
                               line="drop the essay section", target=None)]}), \
         patch("app.routes.cv.tailor_cv", return_value={"markdown": "# Me\n\n- Kafka work\n"}), \
         patch("app.routes.cv.check_guardrails", return_value={"findings": []}):
        task = _run(conn, cfg, jid, "plan")
    assert task["status"] == "done"
    row = q.get_job_cv(conn, jid)
    assert row["tuning_directives"] == "- my own directive"       # untouched
    assert row["plan"][0]["line"] == "drop the essay section"     # suggestion pending review


def test_generate_mode_stores_draft(conn, cfg):
    jid = _seed(conn)
    q.upsert_job_cv(conn, jid, scope=[1, 2, 3])
    q.set_job_cv_directives(conn, jid, "- foreground Kafka")
    with patch("app.routes.cv.tailor_cv", return_value={"markdown": "# Me\n\n- Kafka, Kafka, Kafka\n"}) as tc, \
         patch("app.routes.cv.check_guardrails", return_value={"findings": [{"rule": "x", "verdict": "ok", "explanation": ""}]}):
        _run(conn, cfg, jid, "generate")
    row = q.get_job_cv(conn, jid)
    assert "Kafka, Kafka" in row["tailored_cv"]
    assert row["base_hash"] != ""
    # instruction passed to tailor_cv contained the floor and the directive
    instr = tc.call_args.args[3] if len(tc.call_args.args) > 3 else tc.call_args.kwargs["instruction"]
    assert "Hard limits" in instr and "foreground Kafka" in instr


def test_generate_is_a_no_op_for_a_finalized_cv(conn, cfg):
    jid = _seed(conn)
    q.upsert_job_cv(conn, jid, scope=[1], tailored_cv="# frozen draft")
    q.finalize_job_cv(conn, jid)
    with patch("app.routes.cv.tailor_cv", return_value={"markdown": "# regenerated"}) as tc, \
         patch("app.routes.cv.check_guardrails", return_value={"findings": []}):
        _run(conn, cfg, jid, "generate")
    row = q.get_job_cv(conn, jid)
    assert row["tailored_cv"] == "# frozen draft"   # untouched
    assert row["finalized_at"] is not None          # still accepted
    tc.assert_not_called()


def test_job_context_is_plain_prose_and_capped(conn, cfg):
    from app.routes.cv import _job_context
    job = {"title": "Role", "company": "Acme",
           "summary": "**bold** summary", "simplified_content": "x " * 8000}
    ctx = _job_context(job)
    assert "**" not in ctx
    assert len(ctx) <= 9200  # 9000 cap + title/company header slack


def test_generate_task_returns_oob_preview_chunk(conn, cfg):
    jid = _seed(conn)
    q.upsert_job_cv(conn, jid, scope=[1])
    q.set_job_cv_directives(conn, jid, "- foreground Kafka")
    with patch("app.routes.cv.tailor_cv", return_value={"markdown": "# Me\n- x\n"}), \
         patch("app.routes.cv.check_guardrails", return_value={"findings": []}):
        task = q.enqueue_task(conn, kind="cv_tailor",
                              params={"job_id": jid, "mode": "generate", "render": "preview_pane"})
        execute_task(conn, MagicMock(), "m", cfg, task)
    res = q.get_task(conn, task["id"])["result"]
    assert any('id="cv-preview-pane"' in c for c in res["html_chunks"])
    assert any('id="cv-plan-pane"' in c for c in res["html_chunks"])
    # the finishing task is still 'running' in the DB, but its own result render
    # must not paint the preview as still-updating
    preview = next(c for c in res["html_chunks"] if 'id="cv-preview-pane"' in c)
    assert 'class="cv-preview-progress" aria-live="polite" hidden' in preview
    assert 'class="cv-findings-stale" aria-live="polite" hidden' in preview
    # the stage badges must not stick on "running" once the task's own render lands
    assert 'data-state="running"' not in preview


def test_plan_task_result_chunk_does_not_stick_the_plan_badge_on_running(conn, cfg):
    jid = _seed(conn)
    with patch("app.routes.cv.plan_tailoring", return_value={"directives": []}):
        task = q.enqueue_task(conn, kind="cv_tailor",
                              params={"job_id": jid, "mode": "plan", "render": "plan_pane"})
        execute_task(conn, MagicMock(), "m", cfg, task)
    chunk = q.get_task(conn, task["id"])["result"]["html_chunks"][0]
    assert 'id="cv-plan-pane"' in chunk
    assert 'data-state="running"' not in chunk
    assert 'data-state="fresh"' in chunk  # plan just ran against the current inputs


def test_plan_mode_passes_handled_suggestions(conn, cfg):
    jid = _seed(conn)
    q.upsert_job_cv(conn, jid, scope=[1])
    q.add_handled_suggestions(conn, jid, [
        {"action": "add", "section": "Skills match", "line": "name C++ prominently", "rationale": "r"}])
    with patch("app.routes.cv.plan_tailoring", return_value={"directives": []}) as pt, \
         patch("app.routes.cv.tailor_cv", return_value={"markdown": "# x"}), \
         patch("app.routes.cv.check_guardrails", return_value={"findings": []}):
        _run(conn, cfg, jid, "plan")
    handled_arg = pt.call_args.kwargs["handled"]
    assert handled_arg[0]["line"] == "name C++ prominently"


def test_plan_mode_passes_job_notes(conn, cfg):
    jid = _seed(conn)
    conn.execute("UPDATE jobs SET feedback_note = ?, status = 'accepted' WHERE id = ?",
                 ("I want the platform work, not the GenAI angle.", jid))
    conn.commit()
    with patch("app.routes.cv.plan_tailoring", return_value={"directives": []}) as pt, \
         patch("app.routes.cv.tailor_cv", return_value={"markdown": "# x"}), \
         patch("app.routes.cv.check_guardrails", return_value={"findings": []}):
        _run(conn, cfg, jid, "plan")
    notes_arg = pt.call_args.args[4]
    assert "platform work" in notes_arg
    assert notes_arg.startswith("[ACCEPTED]")


def test_plan_mode_only_uses_directed_unhandled_scenario_feedback(conn, cfg):
    jid = _seed(conn)
    conn.execute("INSERT INTO scenarios (name) VALUES ('s1'), ('s2'), ('s3')")
    conn.execute(
        "INSERT INTO scenario_feedback (job_id, scenario_id, note, direction, handled_at) VALUES "
        "(?, 1, 'directed and open', 'higher', NULL), "
        "(?, 2, 'no direction', NULL, NULL), "
        "(?, 3, 'directed but handled', 'lower', datetime('now'))",
        (jid, jid, jid),
    )
    conn.commit()
    with patch("app.routes.cv.plan_tailoring", return_value={"directives": []}) as pt, \
         patch("app.routes.cv.tailor_cv", return_value={"markdown": "# x"}), \
         patch("app.routes.cv.check_guardrails", return_value={"findings": []}):
        _run(conn, cfg, jid, "plan")
    notes_arg = pt.call_args.args[4]
    assert "directed and open" in notes_arg
    assert "no direction" not in notes_arg
    assert "directed but handled" not in notes_arg


def test_generate_mode_empty_draft_fails_task(conn, cfg):
    jid = _seed(conn)
    q.upsert_job_cv(conn, jid, scope=[1])
    q.set_job_cv_directives(conn, jid, "- foreground Kafka")
    with patch("app.routes.cv.tailor_cv", return_value={"markdown": ""}):
        task = _run(conn, cfg, jid, "generate")
    assert task["status"] == "failed"
    assert "empty" in (task["error"] or "").lower()


def test_plan_first_visit_returns_only_plan_chunk(conn, cfg):
    jid = _seed(conn)
    with patch("app.routes.cv.plan_tailoring", return_value={"directives": [
            DirectiveProposal(action="add", section="Skills match", rationale="r",
                               line="foreground Kafka", target=None)]}):
        task = q.enqueue_task(conn, kind="cv_tailor",
                              params={"job_id": jid, "mode": "plan", "render": "plan_pane"})
        execute_task(conn, MagicMock(), "m", cfg, task)
    res = q.get_task(conn, task["id"])["result"]
    assert len(res["html_chunks"]) == 1
    assert 'id="cv-plan-pane"' in res["html_chunks"][0]


def test_replan_existing_draft_returns_only_plan_chunk(conn, cfg):
    jid = _seed(conn)
    # Seed a job_cv with an existing tailored_cv
    q.upsert_job_cv(conn, jid, tailored_cv="# Me\n\n- old content\n", scope=[1, 2])
    with patch("app.routes.cv.plan_tailoring", return_value={"directives": [
            DirectiveProposal(action="add", section="Content hierarchy and placement", rationale="r",
                               line="reorder sections", target=None)]}), \
         patch("app.routes.cv.tailor_cv", return_value={"markdown": "# Me\n\n- Kafka work\n"}), \
         patch("app.routes.cv.check_guardrails", return_value={"findings": []}):
        task = q.enqueue_task(conn, kind="cv_tailor",
                              params={"job_id": jid, "mode": "plan", "render": "plan_pane"})
        execute_task(conn, MagicMock(), "m", cfg, task)
    res = q.get_task(conn, task["id"])["result"]
    chunks = res["html_chunks"]
    assert len(chunks) == 1
    assert 'id="cv-plan-pane"' in chunks[0]


def test_plan_run_does_not_overwrite_existing_scope(conn, cfg):
    jid = _seed(conn)
    q.upsert_job_cv(conn, jid, scope=[2])          # the user's deliberate choice
    with patch("app.routes.cv.plan_tailoring", return_value={"directives": []}):
        _run(conn, cfg, jid, "plan")
    assert q.get_job_cv(conn, jid)["scope"] == [2]


def test_plan_run_seeds_default_scope_on_a_fresh_row(conn, cfg):
    # No job_cv yet — the run seeds scope from cv_scope_options.default_enabled
    # (the seeded defaults are ids 1,2,3 == correct/choose/organize), exactly as
    # a first generate would.
    jid = _seed(conn)
    with patch("app.routes.cv.plan_tailoring", return_value={"directives": []}):
        _run(conn, cfg, jid, "plan")
    assert q.get_job_cv(conn, jid)["scope"] == [1, 2, 3]


def test_plan_run_stamps_plan_context_hash(conn, cfg):
    jid = _seed(conn)
    with patch("app.routes.cv.plan_tailoring", return_value={"directives": []}):
        _run(conn, cfg, jid, "plan")
    row = q.get_job_cv(conn, jid)
    assert row["plan_context_hash"]
    # must be a 64-char hex hash (sha256)
    assert len(row["plan_context_hash"]) == 64
    assert all(c in "0123456789abcdef" for c in row["plan_context_hash"])


def test_plan_mode_logs_timed_steps(conn, cfg, caplog):
    import logging
    jid = _seed(conn)
    with patch("app.routes.cv.plan_tailoring", return_value={"directives": [
            DirectiveProposal(action="add", section="Skills match", rationale="r",
                               line="foreground Kafka", target=None)]}):
        with caplog.at_level(logging.INFO, logger="job_seek"):
            _run(conn, cfg, jid, "plan")
    messages = [r.getMessage() for r in caplog.records]
    assert any("plan_tailoring" in m and str(jid) in m for m in messages)
