# Profile Improvement Suggestions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user turn accumulated job accept/reject/trash notes into LLM-proposed profile edits, reviewed and applied as checkboxes — the same pattern `app/ai/refine.py` already applies to scenario criteria, applied to the profile document instead.

**Architecture:** A new query pulls unhandled `jobs.feedback_note` rows (gated by the already-existing-but-unused `jobs.feedback_handled_at`). A new `app/ai/refine_profile.py` asks the LLM to propose `add`/`replace`/`remove` bullet-point edits scoped to a `##` section. A new `app/profile_apply.py` resolves those proposals against the current profile text (locating target bullets/sections) and applies the checked subset by mutating the markdown. Two new routes render/accept the proposals, mirroring `/scenarios/{id}/refine` and `/scenarios/{id}/refine/accept`.

**Tech Stack:** FastAPI, Jinja2, sqlite3, openai client, pytest + `TestClient`.

## Global Constraints

- No new DB tables/columns — reuse `jobs.feedback_note` and `jobs.feedback_handled_at` (per this project's migration philosophy: prefer reusing what's there over schema churn).
- Only `- ` bullet lines are valid `add`/`replace`/`remove` targets; profile prose paragraphs are untouched by this feature.
- Marking feedback handled happens by job ID (via a `job_ids` hidden field), not a time anchor.
- Follow existing code style: no docstrings/comments except where behavior is genuinely non-obvious, `from __future__ import annotations` at the top of new modules, dataclasses for proposal shapes, `_SYSTEM` prompt as a module-level constant, `temperature=0` and the `enable_thinking: False` `extra_body` on every LLM call (matches `app/ai/refine.py` and `app/ai/evaluate.py`).

Full design context: `docs/superpowers/specs/2026-08-18-profile-suggestions-design.md`.

---

### Task 1: DB queries for unhandled profile feedback

**Files:**
- Modify: `app/db/queries.py` (add two functions, near `update_job_feedback` at line ~361)
- Test: `tests/test_queries.py` (add tests near the existing `get_recent_feedback_notes` tests, ~line 538)

**Interfaces:**
- Produces: `get_unhandled_profile_notes(conn: sqlite3.Connection, limit: int = 30) -> list[dict]` — each dict is `{"id": int, "status": str, "feedback_note": str}`, most recent job (highest id) first.
- Produces: `mark_profile_feedback_handled(conn: sqlite3.Connection, job_ids: list[int]) -> None`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_queries.py`:

```python
def test_get_unhandled_profile_notes_returns_notes(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_feedback(conn, j1, "rejected", "No AI focus")
    notes = q.get_unhandled_profile_notes(conn)
    assert notes == [{"id": j1, "status": "rejected", "feedback_note": "No AI focus"}]


def test_get_unhandled_profile_notes_excludes_empty_notes(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_feedback(conn, j1, "accepted", "")
    assert q.get_unhandled_profile_notes(conn) == []


def test_get_unhandled_profile_notes_excludes_handled(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_feedback(conn, j1, "rejected", "No AI focus")
    q.mark_profile_feedback_handled(conn, [j1])
    assert q.get_unhandled_profile_notes(conn) == []


def test_get_unhandled_profile_notes_orders_most_recent_first(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T1", company="C", raw_text="r")
    j2 = q.insert_job(conn, source_id=source_id, url="http://job/2", title="T2", company="C", raw_text="r")
    q.update_job_feedback(conn, j1, "rejected", "first")
    q.update_job_feedback(conn, j2, "accepted", "second")
    notes = q.get_unhandled_profile_notes(conn)
    assert [n["id"] for n in notes] == [j2, j1]


def test_get_unhandled_profile_notes_respects_limit(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    for i in range(3):
        jid = q.insert_job(conn, source_id=source_id, url=f"http://job/{i}", title="T", company="C", raw_text="r")
        q.update_job_feedback(conn, jid, "rejected", f"note {i}")
    assert len(q.get_unhandled_profile_notes(conn, limit=2)) == 2


def test_mark_profile_feedback_handled_only_marks_given_jobs(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T1", company="C", raw_text="r")
    j2 = q.insert_job(conn, source_id=source_id, url="http://job/2", title="T2", company="C", raw_text="r")
    q.update_job_feedback(conn, j1, "rejected", "one")
    q.update_job_feedback(conn, j2, "rejected", "two")

    q.mark_profile_feedback_handled(conn, [j1])

    remaining = q.get_unhandled_profile_notes(conn)
    assert [n["id"] for n in remaining] == [j2]


def test_mark_profile_feedback_handled_empty_list_is_noop(conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    j1 = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_feedback(conn, j1, "rejected", "note")
    q.mark_profile_feedback_handled(conn, [])
    assert len(q.get_unhandled_profile_notes(conn)) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_queries.py -k unhandled_profile_notes or mark_profile_feedback_handled -v`
Expected: FAIL with `AttributeError: module 'app.db.queries' has no attribute 'get_unhandled_profile_notes'`

- [ ] **Step 3: Implement the queries**

Add to `app/db/queries.py`, right after `update_job_feedback` (currently ending at line ~366):

```python
def get_unhandled_profile_notes(conn: sqlite3.Connection, limit: int = 30) -> list[dict]:
    return _rows_to_dicts(
        conn.execute(
            """
            SELECT id, status, feedback_note FROM jobs
            WHERE feedback_note IS NOT NULL AND feedback_note != ''
              AND feedback_handled_at IS NULL
            ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    )


def mark_profile_feedback_handled(conn: sqlite3.Connection, job_ids: list[int]) -> None:
    if not job_ids:
        return
    placeholders = ",".join("?" for _ in job_ids)
    conn.execute(
        f"UPDATE jobs SET feedback_handled_at = datetime('now') WHERE id IN ({placeholders})",
        job_ids,
    )
    conn.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_queries.py -k "unhandled_profile_notes or mark_profile_feedback_handled" -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add app/db/queries.py tests/test_queries.py
git commit -m "feat: add queries for unhandled profile feedback notes"
```

---

### Task 2: LLM proposal generation

**Files:**
- Create: `app/ai/refine_profile.py`
- Test: `tests/test_refine_profile.py`

**Interfaces:**
- Consumes: nothing from Task 1 directly (route wiring happens in Task 4); this task is self-contained.
- Produces: `ProfileProposal` dataclass with fields `section: str`, `action: str`, `text: str | None`, `target: str | None`.
- Produces: `propose_profile_changes(client: openai.OpenAI, model: str, profile_text: str, feedback_notes: list[dict]) -> list[ProfileProposal]`, where `feedback_notes` items are `{"status": str, "feedback_note": str}` (the exact shape `get_unhandled_profile_notes` returns).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_refine_profile.py`:

```python
from unittest.mock import MagicMock
from app.ai.refine_profile import propose_profile_changes, ProfileProposal


def _mock_client(response_text: str) -> MagicMock:
    client = MagicMock()
    choice = MagicMock()
    choice.message.content = response_text
    client.chat.completions.create.return_value = MagicMock(choices=[choice])
    return client


_PROFILE = "# About me\n\n## Technologies\n\n- Python\n- Go\n"
_NOTES = [
    {"status": "rejected", "feedback_note": "No AI focus"},
    {"status": "accepted", "feedback_note": "Great AI engineering role"},
]


def test_propose_profile_changes_returns_proposals():
    response = '[{"section": "Technologies", "action": "add", "text": "AI engineering", "target": null}]'
    client = _mock_client(response)
    proposals = propose_profile_changes(client, "llama3.2", _PROFILE, _NOTES)
    assert len(proposals) == 1
    assert isinstance(proposals[0], ProfileProposal)
    assert proposals[0].section == "Technologies"
    assert proposals[0].action == "add"
    assert proposals[0].text == "AI engineering"
    assert proposals[0].target is None


def test_propose_profile_changes_invalid_json_returns_empty():
    client = _mock_client("not json")
    assert propose_profile_changes(client, "llama3.2", _PROFILE, _NOTES) == []


def test_propose_profile_changes_strips_markdown_code_fence():
    response = '```json\n[{"section": "Technologies", "action": "add", "text": "AI engineering", "target": null}]\n```'
    client = _mock_client(response)
    proposals = propose_profile_changes(client, "llama3.2", _PROFILE, _NOTES)
    assert len(proposals) == 1
    assert proposals[0].text == "AI engineering"


def test_propose_profile_changes_filters_invalid_action():
    response = '[{"section": "Technologies", "action": "modify", "text": "x", "target": null}]'
    client = _mock_client(response)
    assert propose_profile_changes(client, "llama3.2", _PROFILE, _NOTES) == []


def test_propose_profile_changes_filters_missing_section():
    response = '[{"action": "add", "text": "x", "target": null}]'
    client = _mock_client(response)
    assert propose_profile_changes(client, "llama3.2", _PROFILE, _NOTES) == []


def test_propose_profile_changes_filters_add_without_text():
    response = '[{"section": "Technologies", "action": "add", "text": null, "target": null}]'
    client = _mock_client(response)
    assert propose_profile_changes(client, "llama3.2", _PROFILE, _NOTES) == []


def test_propose_profile_changes_filters_remove_without_target():
    response = '[{"section": "Technologies", "action": "remove", "text": null, "target": null}]'
    client = _mock_client(response)
    assert propose_profile_changes(client, "llama3.2", _PROFILE, _NOTES) == []


def test_propose_profile_changes_filters_replace_without_target():
    response = '[{"section": "Technologies", "action": "replace", "text": "new", "target": null}]'
    client = _mock_client(response)
    assert propose_profile_changes(client, "llama3.2", _PROFILE, _NOTES) == []


def test_propose_profile_changes_accepts_remove():
    response = '[{"section": "Technologies", "action": "remove", "text": null, "target": "Go"}]'
    client = _mock_client(response)
    proposals = propose_profile_changes(client, "llama3.2", _PROFILE, _NOTES)
    assert len(proposals) == 1
    assert proposals[0].action == "remove"
    assert proposals[0].target == "Go"


def test_propose_profile_changes_accepts_replace():
    response = '[{"section": "Technologies", "action": "replace", "text": "Golang", "target": "Go"}]'
    client = _mock_client(response)
    proposals = propose_profile_changes(client, "llama3.2", _PROFILE, _NOTES)
    assert len(proposals) == 1
    assert proposals[0].action == "replace"
    assert proposals[0].text == "Golang"
    assert proposals[0].target == "Go"


def test_propose_profile_changes_tags_notes_with_status():
    client = _mock_client("[]")
    propose_profile_changes(client, "llama3.2", _PROFILE, _NOTES)
    call_args = client.chat.completions.create.call_args
    user_content = call_args.kwargs["messages"][1]["content"]
    assert "[REJECTED] No AI focus" in user_content
    assert "[ACCEPTED] Great AI engineering role" in user_content


def test_propose_profile_changes_uses_zero_temperature_and_disables_thinking():
    client = _mock_client("[]")
    propose_profile_changes(client, "llama3.2", _PROFILE, _NOTES)
    call_args = client.chat.completions.create.call_args
    assert call_args.kwargs["temperature"] == 0
    assert call_args.kwargs["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_refine_profile.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.ai.refine_profile'`

- [ ] **Step 3: Implement `app/ai/refine_profile.py`**

```python
from __future__ import annotations
import json
from dataclasses import dataclass
import openai
from app.ai.json_utils import extract_json

_SYSTEM = """You propose improvements to a candidate's job-search profile based on feedback
notes left when accepting, rejecting, or trashing specific jobs.

The profile is a markdown document organized into "##" sections (e.g. "Technologies",
"Ideal Employment"). Each bullet point ("- ...") under a section is a discrete fact,
skill, or preference.

Each feedback note carries the job's outcome:
- "[ACCEPTED] <note>": the job was a good fit — a reason to reinforce a matching trait,
  or to note something the profile currently discourages that shouldn't be.
- "[REJECTED] <note>": the job was a mismatch — a reason to add or strengthen a
  disqualifying trait, or to soften a requirement stated too strictly.
- "[TRASH] <note>": trashed jobs are often removed for administrative reasons
  (duplicate, expired, fetch error) with no bearing on fit — ignore these unless the
  note itself clearly expresses a genuine preference.

Given the current profile and feedback notes, propose changes as a JSON array. Each item:
{"section": "<## heading text, existing or new>", "action": "add|replace|remove",
"text": "<new bullet text, for add/replace>", "target": "<exact existing bullet text,
for replace/remove>"}.
Only propose changes to bullet points, and only propose changes clearly supported by
the feedback. Return [] if no changes needed.
Respond with a valid JSON array only."""


@dataclass
class ProfileProposal:
    section: str
    action: str
    text: str | None
    target: str | None


def propose_profile_changes(
    client: openai.OpenAI,
    model: str,
    profile_text: str,
    feedback_notes: list[dict],
) -> list[ProfileProposal]:
    notes_text = "\n".join(f"[{n['status'].upper()}] {n['feedback_note']}" for n in feedback_notes)
    user_content = f"## Current Profile\n{profile_text}\n\n## Job Feedback Notes\n{notes_text}"
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user_content},
            ],
            temperature=0,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        items = json.loads(extract_json(resp.choices[0].message.content))
        proposals = []
        for item in items:
            action = item.get("action")
            if action not in ("add", "replace", "remove"):
                continue
            section = item.get("section")
            if not section:
                continue
            text = item.get("text")
            target = item.get("target")
            if action == "add" and not text:
                continue
            if action == "remove" and not target:
                continue
            if action == "replace" and not (text and target):
                continue
            proposals.append(ProfileProposal(section=section, action=action, text=text, target=target))
        return proposals
    except Exception:
        return []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_refine_profile.py -v`
Expected: PASS (12 tests)

- [ ] **Step 5: Commit**

```bash
git add app/ai/refine_profile.py tests/test_refine_profile.py
git commit -m "feat: add LLM proposal generation for profile improvements"
```

---

### Task 3: Resolve and apply proposals against profile text

**Files:**
- Create: `app/profile_apply.py`
- Test: `tests/test_profile_apply.py`

**Interfaces:**
- Consumes: `ProfileProposal` from Task 2 (`app/ai/refine_profile.ProfileProposal`).
- Produces: `resolve_proposals(proposals: list[ProfileProposal], profile_text: str) -> list[dict]`, each resolved dict: `{"action": str, "section": str, "text": str | None, "target": str | None}`.
- Produces: `apply_profile_proposals(profile_text: str, resolved: list[dict]) -> str`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_profile_apply.py`:

```python
from app.ai.refine_profile import ProfileProposal
from app.profile_apply import resolve_proposals, apply_profile_proposals

_PROFILE = (
    "# About me\n"
    "\n"
    "## Technologies\n"
    "\n"
    "- Python\n"
    "- Go\n"
    "\n"
    "## Methodologies\n"
    "\n"
    "- Agile principles\n"
)


def test_resolve_add_to_existing_section():
    proposals = [ProfileProposal(section="Technologies", action="add", text="Rust", target=None)]
    resolved = resolve_proposals(proposals, _PROFILE)
    assert resolved == [{"action": "add", "section": "Technologies", "text": "Rust", "target": None}]


def test_resolve_add_to_new_section_is_kept():
    proposals = [ProfileProposal(section="New Section", action="add", text="Something", target=None)]
    resolved = resolve_proposals(proposals, _PROFILE)
    assert resolved == [{"action": "add", "section": "New Section", "text": "Something", "target": None}]


def test_resolve_add_duplicating_existing_bullet_is_omitted():
    proposals = [ProfileProposal(section="Technologies", action="add", text="  python  ", target=None)]
    assert resolve_proposals(proposals, _PROFILE) == []


def test_resolve_remove_matching_bullet_is_kept():
    proposals = [ProfileProposal(section="Technologies", action="remove", text=None, target="Go")]
    resolved = resolve_proposals(proposals, _PROFILE)
    assert resolved == [{"action": "remove", "section": "Technologies", "text": None, "target": "Go"}]


def test_resolve_remove_unmatched_bullet_is_omitted():
    proposals = [ProfileProposal(section="Technologies", action="remove", text=None, target="Rust")]
    assert resolve_proposals(proposals, _PROFILE) == []


def test_resolve_replace_matching_bullet_is_kept():
    proposals = [ProfileProposal(section="Technologies", action="replace", text="Golang", target="Go")]
    resolved = resolve_proposals(proposals, _PROFILE)
    assert resolved == [{"action": "replace", "section": "Technologies", "text": "Golang", "target": "Go"}]


def test_resolve_replace_unmatched_target_is_omitted():
    proposals = [ProfileProposal(section="Technologies", action="replace", text="X", target="Rust")]
    assert resolve_proposals(proposals, _PROFILE) == []


def test_apply_add_appends_bullet_to_existing_section():
    resolved = [{"action": "add", "section": "Technologies", "text": "Rust", "target": None}]
    result = apply_profile_proposals(_PROFILE, resolved)
    lines = result.splitlines()
    tech_idx = lines.index("## Technologies")
    method_idx = lines.index("## Methodologies")
    assert "- Rust" in lines[tech_idx:method_idx]


def test_apply_add_creates_new_section_at_end():
    resolved = [{"action": "add", "section": "New Section", "text": "Something", "target": None}]
    result = apply_profile_proposals(_PROFILE, resolved)
    assert "## New Section" in result
    lines = result.splitlines()
    new_idx = lines.index("## New Section")
    assert lines[new_idx + 1] == "- Something"


def test_apply_remove_deletes_matching_bullet():
    resolved = [{"action": "remove", "section": "Technologies", "text": None, "target": "Go"}]
    result = apply_profile_proposals(_PROFILE, resolved)
    assert "- Go" not in result.splitlines()
    assert "- Python" in result.splitlines()


def test_apply_replace_updates_bullet_text():
    resolved = [{"action": "replace", "section": "Technologies", "text": "Golang", "target": "Go"}]
    result = apply_profile_proposals(_PROFILE, resolved)
    assert "- Golang" in result.splitlines()
    assert "- Go" not in result.splitlines()


def test_apply_multiple_adds_to_same_new_section():
    resolved = [
        {"action": "add", "section": "New Section", "text": "First", "target": None},
        {"action": "add", "section": "New Section", "text": "Second", "target": None},
    ]
    result = apply_profile_proposals(_PROFILE, resolved)
    assert result.count("## New Section") == 1
    assert "- First" in result.splitlines()
    assert "- Second" in result.splitlines()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_profile_apply.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.profile_apply'`

- [ ] **Step 3: Implement `app/profile_apply.py`**

```python
from __future__ import annotations
from app.ai.refine_profile import ProfileProposal


def _find_bullet_line(lines: list[str], text: str) -> int | None:
    needle = text.strip().casefold()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("- ") and stripped[2:].strip().casefold() == needle:
            return i
    return None


def _find_section_bounds(lines: list[str], section: str) -> tuple[int, int] | None:
    needle = section.strip().casefold()
    for i, line in enumerate(lines):
        if line.startswith("## ") and line[3:].strip().casefold() == needle:
            j = i + 1
            while j < len(lines) and not lines[j].startswith("#"):
                j += 1
            return (i, j)
    return None


def resolve_proposals(proposals: list[ProfileProposal], profile_text: str) -> list[dict]:
    lines = profile_text.splitlines()
    resolved = []
    for p in proposals:
        if p.action == "add":
            if _find_bullet_line(lines, p.text) is not None:
                continue
            resolved.append({"action": "add", "section": p.section, "text": p.text, "target": None})
        else:
            if _find_bullet_line(lines, p.target) is None:
                continue
            resolved.append({"action": p.action, "section": p.section, "text": p.text, "target": p.target})
    return resolved


def apply_profile_proposals(profile_text: str, resolved: list[dict]) -> str:
    lines = profile_text.splitlines()
    for r in resolved:
        if r["action"] == "remove":
            idx = _find_bullet_line(lines, r["target"])
            if idx is not None:
                del lines[idx]
        elif r["action"] == "replace":
            idx = _find_bullet_line(lines, r["target"])
            if idx is not None:
                lines[idx] = f"- {r['text']}"
        elif r["action"] == "add":
            bounds = _find_section_bounds(lines, r["section"])
            if bounds is not None:
                _, end = bounds
                lines.insert(end, f"- {r['text']}")
            else:
                if lines and lines[-1].strip() != "":
                    lines.append("")
                lines.append(f"## {r['section']}")
                lines.append(f"- {r['text']}")
    result = "\n".join(lines)
    if profile_text.endswith("\n"):
        result += "\n"
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_profile_apply.py -v`
Expected: PASS (12 tests)

- [ ] **Step 5: Commit**

```bash
git add app/profile_apply.py tests/test_profile_apply.py
git commit -m "feat: add resolve/apply logic for profile improvement proposals"
```

---

### Task 4: Routes and UI

**Files:**
- Modify: `app/routes/profile.py`
- Modify: `app/templates/profile/index.html`
- Create: `app/templates/profile/_editor.html`
- Create: `app/templates/profile/_proposals.html`
- Test: `tests/test_routes_profile.py`

**Interfaces:**
- Consumes: `q.get_unhandled_profile_notes`, `q.mark_profile_feedback_handled` (Task 1); `propose_profile_changes`, `ProfileProposal` (Task 2); `resolve_proposals`, `apply_profile_proposals` (Task 3).
- Produces: `POST /profile/refine` (streams proposals HTML), `POST /profile/refine/accept` (applies checked proposals).

- [ ] **Step 1: Extract the editor partial (refactor, no behavior change)**

Read current `app/templates/profile/index.html`:

```html
{% extends "base.html" %}
{% block title %}Profile — Job Seek{% endblock %}
{% block content %}
<h1>Profile</h1>
{% if saved %}<div class="save-confirmation" aria-live="polite">Saved.</div>{% endif %}
<form method="post" action="/profile">
  <label>Your skills, interests, and constraints (markdown supported):<br>
    <textarea name="content" style="width:100%; min-height:300px; font-family:monospace;">{{ content }}</textarea>
  </label>
  <br>
  <button type="submit" class="btn">Save</button>
</form>
<div style="margin-top:1.5rem; padding-top:1rem; border-top:1px solid #dee2e6;">
  <button class="btn" data-progress-url="/scenarios/reevaluate"
    title="Re-score every new/accepted job against every scenario and refresh its profile fit.">
    Re-evaluate everything
  </button>
</div>
{% endblock %}
```

Create `app/templates/profile/_editor.html`:

```html
<div id="profile-editor">
  {% if saved %}<div class="save-confirmation" aria-live="polite">Saved.</div>{% endif %}
  <form method="post" action="/profile">
    <label>Your skills, interests, and constraints (markdown supported):<br>
      <textarea name="content" style="width:100%; min-height:300px; font-family:monospace;">{{ content }}</textarea>
    </label>
    <br>
    <button type="submit" class="btn">Save</button>
  </form>
  <div style="margin-top:1rem;">
    <button class="btn" data-progress-url="/profile/refine" data-progress-target="#profile-proposals-area"
      title="Ask the LLM to propose profile edits based on unhandled job accept/reject/trash notes.">
      Suggest profile improvements
    </button>
    {% if unhandled_notes_count %}
    <span style="margin-left:0.5rem; font-size:0.85em; color:#666;">
      {{ unhandled_notes_count }} note{{ 's' if unhandled_notes_count != 1 else '' }} not yet reviewed
    </span>
    {% endif %}
  </div>
  <div id="profile-proposals-area" style="margin-top:0.75rem;"></div>
</div>
```

Replace `app/templates/profile/index.html` with:

```html
{% extends "base.html" %}
{% block title %}Profile — Job Seek{% endblock %}
{% block content %}
<h1>Profile</h1>
{% include "profile/_editor.html" %}
<div style="margin-top:1.5rem; padding-top:1rem; border-top:1px solid #dee2e6;">
  <button class="btn" data-progress-url="/scenarios/reevaluate"
    title="Re-score every new/accepted job against every scenario and refresh its profile fit.">
    Re-evaluate everything
  </button>
</div>
{% endblock %}
```

- [ ] **Step 2: Run existing profile tests to confirm the refactor is behavior-preserving**

Run: `uv run pytest tests/test_routes_profile.py -v`
Expected: PASS (existing 4 tests still pass — page still renders the same form/button markup)

- [ ] **Step 3: Write the failing tests for the new routes**

Add to `tests/test_routes_profile.py` (add `from unittest.mock import patch` and `from app.ai.refine_profile import ProfileProposal` to the imports):

```python
from unittest.mock import patch
from app.ai.refine_profile import ProfileProposal


def test_profile_page_shows_unhandled_notes_count(client, conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_feedback(conn, jid, "rejected", "No AI focus")
    resp = client.get("/profile")
    assert "1 note not yet reviewed" in resp.text


def test_refine_profile_returns_proposals(client, conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_feedback(conn, jid, "rejected", "No AI focus")
    q.upsert_profile(conn, "## Technologies\n\n- Python\n")
    proposals = [ProfileProposal(section="Technologies", action="add", text="AI/ML", target=None)]
    with patch("app.routes.profile.propose_profile_changes", return_value=proposals):
        resp = client.post("/profile/refine")
    assert resp.status_code == 200
    assert "HTML:" in resp.text
    assert "AI/ML" in resp.text


def test_refine_profile_add_proposal_has_editable_input(client, conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_feedback(conn, jid, "rejected", "No AI focus")
    q.upsert_profile(conn, "## Technologies\n\n- Python\n")
    proposals = [ProfileProposal(section="Technologies", action="add", text="AI/ML", target=None)]
    with patch("app.routes.profile.propose_profile_changes", return_value=proposals):
        resp = client.post("/profile/refine")
    assert 'name="text_0" value="AI/ML"' in resp.text
    assert 'name="kind_0" value="add"' in resp.text
    assert 'name="section_0" value="Technologies"' in resp.text
    assert f'name="job_ids" value="{jid}"' in resp.text


def test_refine_profile_remove_proposal_is_struck_through(client, conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_feedback(conn, jid, "rejected", "No AI focus")
    q.upsert_profile(conn, "## Technologies\n\n- Python\n")
    proposals = [ProfileProposal(section="Technologies", action="remove", text=None, target="Python")]
    with patch("app.routes.profile.propose_profile_changes", return_value=proposals):
        resp = client.post("/profile/refine")
    assert "text-decoration:line-through" in resp.text
    assert 'name="target_0" value="Python"' in resp.text


def test_refine_profile_no_proposals_shows_message(client, conn):
    q.upsert_profile(conn, "## Technologies\n\n- Python\n")
    with patch("app.routes.profile.propose_profile_changes", return_value=[]):
        resp = client.post("/profile/refine")
    assert "No changes proposed" in resp.text


def test_refine_profile_alone_does_not_mark_feedback_handled(client, conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_feedback(conn, jid, "rejected", "No AI focus")
    q.upsert_profile(conn, "## Technologies\n\n- Python\n")
    proposals = [ProfileProposal(section="Technologies", action="add", text="AI/ML", target=None)]
    with patch("app.routes.profile.propose_profile_changes", return_value=proposals):
        client.post("/profile/refine")
    assert len(q.get_unhandled_profile_notes(conn)) == 1


def test_accept_profile_proposals_applies_checked_add(client, conn):
    q.upsert_profile(conn, "## Technologies\n\n- Python\n")
    resp = client.post(
        "/profile/refine/accept",
        data={
            "kind_0": "add", "section_0": "Technologies", "text_0": "AI/ML", "apply_0": "on",
        },
    )
    assert resp.status_code == 200
    assert "- AI/ML" in q.get_profile(conn)


def test_accept_profile_proposals_skips_unchecked(client, conn):
    q.upsert_profile(conn, "## Technologies\n\n- Python\n")
    client.post(
        "/profile/refine/accept",
        data={"kind_0": "add", "section_0": "Technologies", "text_0": "AI/ML"},
    )
    assert "AI/ML" not in q.get_profile(conn)


def test_accept_profile_proposals_applies_checked_remove(client, conn):
    q.upsert_profile(conn, "## Technologies\n\n- Python\n- Go\n")
    client.post(
        "/profile/refine/accept",
        data={
            "kind_0": "remove", "section_0": "Technologies", "target_0": "Go", "apply_0": "on",
        },
    )
    assert "- Go" not in q.get_profile(conn).splitlines()


def test_accept_profile_proposals_marks_submitted_job_ids_handled(client, conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_feedback(conn, jid, "rejected", "No AI focus")
    q.upsert_profile(conn, "## Technologies\n\n- Python\n")

    client.post(
        "/profile/refine/accept",
        data={
            "kind_0": "add", "section_0": "Technologies", "text_0": "AI/ML", "apply_0": "on",
            "job_ids": str(jid),
        },
    )

    assert q.get_unhandled_profile_notes(conn) == []


def test_accept_profile_proposals_leaves_unhandled_when_not_submitted(client, conn):
    source_id = q.insert_source(conn, "s", "http://x", "generic_listing")
    jid = q.insert_job(conn, source_id=source_id, url="http://job/1", title="T", company="C", raw_text="r")
    q.update_job_feedback(conn, jid, "rejected", "No AI focus")
    q.upsert_profile(conn, "## Technologies\n\n- Python\n")

    client.post(
        "/profile/refine/accept",
        data={"kind_0": "add", "section_0": "Technologies", "text_0": "AI/ML", "apply_0": "on"},
    )

    assert len(q.get_unhandled_profile_notes(conn)) == 1
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `uv run pytest tests/test_routes_profile.py -v`
Expected: FAIL — `404 Not Found` for `/profile/refine` and `/profile/refine/accept` (routes don't exist yet), and `AttributeError`/`ImportError` for `app.routes.profile.propose_profile_changes` in the `patch()` calls.

- [ ] **Step 5: Implement the routes**

Replace `app/routes/profile.py` with:

```python
from __future__ import annotations
import sqlite3
import openai
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from app.deps import get_db, get_ai_client, get_model
from app.db import queries as q
from app.ai.refine_profile import propose_profile_changes
from app.profile_apply import resolve_proposals, apply_profile_proposals
from app.template_env import templates

router = APIRouter()


def _profile_context(conn: sqlite3.Connection) -> dict:
    return {
        "content": q.get_profile(conn),
        "unhandled_notes_count": len(q.get_unhandled_profile_notes(conn)),
    }


@router.get("/profile", response_class=HTMLResponse)
def profile_page(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    return templates.TemplateResponse(request, "profile/index.html", _profile_context(conn))


@router.post("/profile", response_class=HTMLResponse)
def profile_save(
    request: Request,
    content: str = Form(...),
    conn: sqlite3.Connection = Depends(get_db),
):
    q.upsert_profile(conn, content)
    ctx = _profile_context(conn)
    ctx["saved"] = True
    return templates.TemplateResponse(request, "profile/index.html", ctx)


@router.post("/profile/refine")
def refine_profile(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    client: openai.OpenAI = Depends(get_ai_client),
    model: str = Depends(get_model),
):
    profile_text = q.get_profile(conn)
    notes = q.get_unhandled_profile_notes(conn)

    def stream():
        yield "Requesting profile improvement suggestions from LLM\n"
        proposals = propose_profile_changes(client, model, profile_text, notes)
        resolved = resolve_proposals(proposals, profile_text)
        yield f"Received {len(resolved)} proposal(s)\n"
        html = templates.get_template("profile/_proposals.html").render(
            request=request,
            proposals=resolved,
            job_ids=[n["id"] for n in notes],
        )
        yield "HTML:" + html.replace("\n", "")

    return StreamingResponse(stream(), media_type="text/plain")


@router.post("/profile/refine/accept", response_class=HTMLResponse)
async def accept_profile_proposals(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
):
    form = await request.form()
    resolved = []
    i = 0
    while f"kind_{i}" in form:
        if f"apply_{i}" in form:
            resolved.append({
                "action": form[f"kind_{i}"],
                "section": form[f"section_{i}"],
                "text": form.get(f"text_{i}") or None,
                "target": form.get(f"target_{i}") or None,
            })
        i += 1
    profile_text = q.get_profile(conn)
    new_text = apply_profile_proposals(profile_text, resolved)
    q.upsert_profile(conn, new_text)
    job_ids = [int(v) for v in form.getlist("job_ids")]
    q.mark_profile_feedback_handled(conn, job_ids)
    ctx = _profile_context(conn)
    ctx["saved"] = True
    return templates.TemplateResponse(request, "profile/_editor.html", ctx)
```

- [ ] **Step 6: Create `app/templates/profile/_proposals.html`**

```html
{% if proposals %}
<form hx-post="/profile/refine/accept" hx-target="#profile-editor" hx-swap="outerHTML">
  {% for jid in job_ids %}
  <input type="hidden" name="job_ids" value="{{ jid }}">
  {% endfor %}
  <p><strong>Proposed profile changes.</strong> Uncheck any you don't want, then apply.</p>
  {% for p in proposals %}
  <div style="display:flex; gap:0.5rem; align-items:center; margin-bottom:0.5rem; padding:0.4rem; background:#f8f9fa; border-radius:4px;">
    <input type="checkbox" name="apply_{{ loop.index0 }}" checked>
    <input type="hidden" name="kind_{{ loop.index0 }}" value="{{ p.action }}">
    <input type="hidden" name="section_{{ loop.index0 }}" value="{{ p.section }}">
    <span class="tag">{{ p.section }}</span>
    {% if p.action == "remove" %}
    <input type="hidden" name="target_{{ loop.index0 }}" value="{{ p.target }}">
    <span style="flex:1; text-decoration:line-through; color:#888;">{{ p.target | markdown_inline }}</span>
    {% elif p.action == "replace" %}
    <input type="hidden" name="target_{{ loop.index0 }}" value="{{ p.target }}">
    <input type="hidden" name="text_{{ loop.index0 }}" value="{{ p.text }}">
    <span style="flex:1;"><span style="text-decoration:line-through; color:#888;">{{ p.target | markdown_inline }}</span> &rarr; {{ p.text | markdown_inline }}</span>
    {% else %}
    <input type="hidden" name="text_{{ loop.index0 }}" value="{{ p.text }}">
    <span style="flex:1;">{{ p.text | markdown_inline }}</span>
    {% endif %}
  </div>
  {% endfor %}
  <button type="submit" class="btn btn-accept">Apply changes</button>
</form>
{% else %}
<p>No changes proposed based on current feedback.</p>
{% endif %}
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/test_routes_profile.py -v`
Expected: PASS (all tests, existing + new)

- [ ] **Step 8: Run the full test suite**

Run: `uv run pytest -q`
Expected: PASS, no regressions elsewhere (particularly `tests/test_queries.py`, `tests/test_refine_profile.py`, `tests/test_profile_apply.py`, `tests/test_routes_scenarios.py`)

- [ ] **Step 9: Commit**

```bash
git add app/routes/profile.py app/templates/profile/index.html app/templates/profile/_editor.html app/templates/profile/_proposals.html tests/test_routes_profile.py
git commit -m "feat: add profile improvement suggestions UI and routes"
```

---

## Self-Review Notes

- **Spec coverage:** query layer (Task 1), LLM proposal shape incl. `add`/`replace`/`remove` (Task 2), resolve/apply against bullet lines and sections incl. new-section creation (Task 3), routes + UI incl. unhandled-notes count and job-ID-based handled-marking (Task 4) — all five spec sections have a covering task.
- **Type consistency:** `ProfileProposal` fields (`section`, `action`, `text`, `target`) are identical across Task 2's definition, Task 3's `resolve_proposals`/`apply_profile_proposals` signatures, and Task 4's route usage. Resolved-dict shape (`action`/`section`/`text`/`target`) is consistent between Task 3's output and Task 4's form field naming (`kind_i`/`section_i`/`text_i`/`target_i`) and template rendering.
- **Manual verification suggestion (not part of automated tests):** after Task 4, run the app via the `run-dev-server` skill and click through "Suggest profile improvements" against the real accumulated job notes to sanity-check the LLM's actual proposal quality — the automated tests only cover the mechanical plumbing, not real model output.
