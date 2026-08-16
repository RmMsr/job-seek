# Surface Embedded Application/Original-Posting Links in Job Summaries Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a `job_posting`-type job's content contains a link to the original job description / application form / hiring organization, surface that link as a trailing line in the AI-generated `summary`.

**Architecture:** Extend the `_SYSTEM` prompt in `app/ai/summarize.py` to ask the model for an optional `source_link` field alongside `title`/`headline`/`summary`. After parsing the JSON response, validate the returned link is an `http(s)://` URL that appears verbatim in the input `simplified_content` (guards against hallucination); if valid, append it to the returned summary as `\n\n**Original posting:** <url>`.

**Tech Stack:** Python, pytest, unittest.mock (existing patterns in `tests/test_summarize.py`).

## Global Constraints

- Change is scoped to `app/ai/summarize.py` and its tests only — no DB, schema, template, or fetcher changes (see spec's "Scope" section).
- The `lead` content-type path (`_LEAD_SYSTEM` branch) must remain unchanged — its `summary` stays the verbatim original message.
- `source_link` must never be trusted as-is: only accepted if `http(s)://`-prefixed AND a verbatim substring of the input `simplified_content`.

---

### Task 1: Add `source_link` extraction to the job_posting summarizer

**Files:**
- Modify: `app/ai/summarize.py`
- Test: `tests/test_summarize.py`

**Interfaces:**
- Consumes: existing `summarize(client, model, simplified_content, content_type="job_posting")` signature — unchanged. Existing `extract_json` from `app/ai/json_utils.py` — unchanged.
- Produces: `summarize()` still returns `tuple[str, str, str]` (`title, headline, summary`) — unchanged signature, but for `job_posting` content, `summary` may now have a trailing `\n\n**Original posting:** <url>` line.

- [ ] **Step 1: Write the failing tests**

Add these test cases to `tests/test_summarize.py` (append after the existing `test_summarize_defaults_to_job_posting_prompt` test, before the lead-content-type tests):

```python
def test_summarize_appends_valid_source_link_to_summary():
    response = (
        '{"title": "T", "headline": "H", "summary": "**Role:** ML Engineer", '
        '"source_link": "https://example.com/apply"}'
    )
    client = _mock_client(response)
    content = "Some job posting text.\nApply here: https://example.com/apply\nMore text."
    _, _, summary = summarize(client, "llama3.2", content)
    assert summary == "**Role:** ML Engineer\n\n**Original posting:** https://example.com/apply"


def test_summarize_rejects_source_link_not_present_in_content():
    response = (
        '{"title": "T", "headline": "H", "summary": "**Role:** ML Engineer", '
        '"source_link": "https://hallucinated.example.com/made-up"}'
    )
    client = _mock_client(response)
    content = "Some job posting text with no links at all."
    _, _, summary = summarize(client, "llama3.2", content)
    assert summary == "**Role:** ML Engineer"


def test_summarize_rejects_non_http_source_link():
    response = (
        '{"title": "T", "headline": "H", "summary": "**Role:** ML Engineer", '
        '"source_link": "javascript:alert(1)"}'
    )
    client = _mock_client(response)
    content = "Some text.\njavascript:alert(1)\nMore text."
    _, _, summary = summarize(client, "llama3.2", content)
    assert summary == "**Role:** ML Engineer"


def test_summarize_leaves_summary_unchanged_when_no_source_link():
    response = '{"title": "T", "headline": "H", "summary": "**Role:** ML Engineer", "source_link": ""}'
    client = _mock_client(response)
    _, _, summary = summarize(client, "llama3.2", "content with no links")
    assert summary == "**Role:** ML Engineer"


def test_summarize_leaves_summary_unchanged_when_source_link_field_missing():
    # Model may omit the field entirely (e.g. older prompt caching, non-compliant model).
    response = '{"title": "T", "headline": "H", "summary": "**Role:** ML Engineer"}'
    client = _mock_client(response)
    _, _, summary = summarize(client, "llama3.2", "content with no links")
    assert summary == "**Role:** ML Engineer"


def test_summarize_lead_path_unaffected_by_source_link():
    response = '{"organizations": ["Acme"], "headline": "H", "source_link": "https://example.com/apply"}'
    client = _mock_client(response)
    original = "Posted by U123:\n\nAcme is hiring, apply at https://example.com/apply"
    _, _, summary = summarize(client, "llama3.2", original, content_type="lead")
    assert summary == original
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_summarize.py -v`
Expected: the 6 new tests FAIL (existing tests still PASS). Failures should show the summary missing the `**Original posting:**` line (or, for the rejection tests, the assertions currently trivially pass since no injection happens yet — check that `test_summarize_appends_valid_source_link_to_summary` is the one that fails with a clear mismatch; the "rejects"/"unchanged" tests may pass by coincidence before the feature exists, which is fine since they lock in the no-op baseline).

- [ ] **Step 3: Update the `_SYSTEM` prompt**

In `app/ai/summarize.py`, replace the `_SYSTEM` constant:

```python
_SYSTEM = (
    "Summarize this job posting. Respond with exactly this JSON shape: "
    '{"title": "<Role - Location (remote/hybrid/onsite) @ Organization>", '
    '"headline": "<one punchy sentence on the most compelling or notable detail>", '
    '"summary": "<concise markdown covering role, company, location/remote status, '
    'key requirements, compensation if mentioned, notable perks or red flags>", '
    '"source_link": "<a URL copied verbatim from the text below that points to the '
    'original job description, application form, or the hiring organization/job page, '
    'or empty string if none is present>"}. '
    "For title: use the role as given in the posting, or a concise generated one if "
    "unclear; include location with remote/hybrid/onsite status; include the "
    "organization name. Be factual and brief. No invented details. "
    "For summary: keep it well-structured and easy to scan, and don't drop any of the "
    "standard fields listed above. Within that, favor concrete specifics over generic "
    "advertised-sounding phrasing (e.g. 'competitive salary', 'fast-paced environment', "
    "'collaborative team') — call out what's actually distinctive about this posting, "
    "such as unusual scope or impact, concrete technical/domain details, or notable team "
    "or organization context. "
    "For source_link: only return a URL that appears verbatim in the text below — never "
    "construct, guess, or modify one. If several links are present, prefer the most direct "
    "application link or the original detailed posting over generic organization/social links."
)
```

- [ ] **Step 4: Add link validation and summary-appending logic**

In `app/ai/summarize.py`, add a helper function above `summarize()`:

```python
def _valid_source_link(link: str, simplified_content: str) -> str:
    if not link.startswith(("http://", "https://")):
        return ""
    if link not in simplified_content:
        return ""
    return link
```

Then update the `job_posting` return branch inside `summarize()` (the code after the `if is_lead:` block) from:

```python
        title = data.get("title", "")
        return title, data.get("headline", ""), data.get("summary", "")
```

to:

```python
        title = data.get("title", "")
        summary = data.get("summary", "")
        source_link = _valid_source_link(data.get("source_link", ""), simplified_content)
        if source_link:
            summary = f"{summary}\n\n**Original posting:** {source_link}"
        return title, data.get("headline", ""), summary
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_summarize.py -v`
Expected: all tests PASS (existing + 6 new).

- [ ] **Step 6: Run the full test suite**

Run: `uv run pytest -q`
Expected: all tests pass (no regressions in `test_pipeline.py` or elsewhere that exercise `summarize()`).

- [ ] **Step 7: Commit**

```bash
git add app/ai/summarize.py tests/test_summarize.py
git commit -m "feat: surface embedded application links in job summaries"
```
