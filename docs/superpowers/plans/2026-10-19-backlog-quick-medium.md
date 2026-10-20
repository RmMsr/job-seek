# Plan: Combine apply, compact suggestions, move prompt

Spec: `docs/superpowers/specs/2026-10-19-backlog-quick-medium-design.md`. TDD where server-side, commit after each task.

## Task 1 — Combine Apply
- Test (`tests/test_job_filter.py`): `from_params` with `status` repeated (multi-dict `getlist`) and mixed comma/repeated → merged, deduped, VALID_TABS order preserved by later helpers as today.
- Test (`tests/test_routes_jobs.py`): rendered Combine panel has a form with checkboxes `name="status"` and no `hx-get` on the checkboxes, plus an Apply button; GET `/jobs?status=new&status=lead&solo=1` lists both tabs.
- Implement in `app/job_filter.py` + `app/templates/jobs/_content.html`; small JS (in base.html) disables Apply when nothing is checked.

## Task 2 — Compact suggestions
- Template test: inline org/tag inputs and `#tb-tag` carry `data-suggest=...` and no `list=` attribute.
- Implement the delegated autocomplete JS + CSS in `app/templates/base.html` (next to `openInlineEdit`). Use the `frontend-design` skill for styling — match existing tokens.
- Verify by hand in a browser against the dev server (keyboard ↓/↑/Enter/Tab/Esc, click, inline-editor blur).

## Task 3 — Move prompt (server)
- `app/db/queries.py`: `get_job_tab(conn, job_id) -> str | None` using `_TAB_PREDICATE`; `restore_job_tab(conn, job_id, prev_tab, prev_status, prev_content_type, *, dry_run=False) -> bool` (no commit until verified; rollback on dry run / mismatch; job event on success).
- `app/routes/jobs.py`: snapshot in `job_reset`, `job_reevaluate`, single-job `jobs_revisit`; build `move_prompt` dict (from/to tab + labels, can_keep, prev fields, action label, href) and pass through `_render_updated_job_html`; new `POST /jobs/{id}/keep-tab`.
- Tests: tab helper per tab; restore for accepted→new (reset), new→not_relevant (gate_override), not_relevant→new not revertible; task result HTML contains prompt only when tab changed; keep-tab route restores and returns 409 when impossible.

## Task 4 — Move prompt (UI)
- `app/templates/jobs/_move_prompt.html`, included in `_row.html` (in place of the stale-note when a prompt exists) and `_feedback.html` (top, list + detail). Move = client-side dismiss; Keep = `hx-post` to keep-tab targeting `#job-{id}` outerHTML with counts OOB; go-to link hidden on detail page.
- Route/template tests for the rendered prompt; manual check on dev server.

## Task 5 — Wrap up
- Full `uv run pytest`; leave the dev server running (run-dev-server skill, throwaway DB copy) and report the URL.
