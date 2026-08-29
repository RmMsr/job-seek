# LinkedIn detail-page fetch via guest posting endpoint — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fetch LinkedIn job detail pages via the logged-out `jobs-guest/jobs/api/jobPosting/<id>` endpoint instead of the `/jobs/view/` page, and stop dumping raw scraped text into non-Slack "lead" summaries.

**Architecture:** A pure helper in `app/url_rewrite.py` maps any LinkedIn job-detail URL to the guest posting endpoint. The two HTTP entry points (`HttpFetcher.fetch`, `fetch_url_html`) call it to rewrite only the fetch target — stored URLs, `RawJob.url`, dedup and the "↗ original" link are untouched. Separately, `summarize()` gains a `raw_passthrough` flag so only Slack leads keep their verbatim text.

**Tech Stack:** Python 3, httpx, respx (HTTP mocking in tests), pytest, BeautifulSoup.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-29-linkedin-detail-fetch-design.md`.
- Run tests with `uv run pytest` (the uv cache may need `--no-sandbox`-style network; the executor already runs outside the sandbox).
- Commit after every task (project rule: commit after each completed increment).
- No change to `canonicalize_url`, stored job URLs, or dedup behaviour.
- The guest endpoint host is always `https://www.linkedin.com` (not the regional `fr.`/`no.` subdomain).
- Do NOT clean up already-ingested jobs; do NOT extract posting dates. Out of scope.

---

### Task 1: `linkedin_guest_posting_url` helper

**Files:**
- Modify: `app/url_rewrite.py` (add helper + module constant near the existing `_GUEST_SEARCH`)
- Test: `tests/test_url_rewrite.py`

**Interfaces:**
- Consumes: nothing new (`re`, `urlsplit`, `parse_qs` are already imported in `app/url_rewrite.py`).
- Produces: `linkedin_guest_posting_url(url: str) -> str | None` — returns
  `https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/<digits>` for a
  LinkedIn `/jobs/view/<slug>-<id>` or `?currentJobId=<id>` URL, else `None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_url_rewrite.py`:

```python
from app.url_rewrite import linkedin_guest_posting_url

_GUEST_POSTING = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/"


def test_guest_posting_url_from_slug_and_id():
    assert linkedin_guest_posting_url(
        "https://fr.linkedin.com/jobs/view/senior-ml-engineer-x-f-m-at-doctolib-4421669844"
        "?position=1&pageNum=0&refId=abc"
    ) == _GUEST_POSTING + "4421669844"


def test_guest_posting_url_from_bare_id():
    assert linkedin_guest_posting_url(
        "https://www.linkedin.com/jobs/view/4421669844"
    ) == _GUEST_POSTING + "4421669844"


def test_guest_posting_url_from_current_job_id():
    assert linkedin_guest_posting_url(
        "https://www.linkedin.com/jobs/search-results/?currentJobId=4435382222&keywords=x"
    ) == _GUEST_POSTING + "4435382222"


def test_guest_posting_url_none_for_non_linkedin():
    assert linkedin_guest_posting_url("https://example.com/jobs/view/some-job-123") is None


def test_guest_posting_url_none_without_id():
    assert linkedin_guest_posting_url("https://www.linkedin.com/jobs/view/") is None
    assert linkedin_guest_posting_url("https://www.linkedin.com/jobs/search?keywords=ai") is None


def test_guest_posting_url_none_for_already_guest_url():
    assert linkedin_guest_posting_url(_GUEST_POSTING + "4421669844") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_url_rewrite.py -q`
Expected: FAIL — `ImportError: cannot import name 'linkedin_guest_posting_url'`

- [ ] **Step 3: Implement the helper**

In `app/url_rewrite.py`, add below the existing `_GUEST_SEARCH` constant:

```python
_GUEST_POSTING = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/"
```

and add this function (e.g. just above `suggest_rewrite`):

```python
def linkedin_guest_posting_url(url: str) -> str | None:
    """For a LinkedIn job-detail URL, the logged-out guest endpoint that returns
    just the posting fragment (description first, ~8k chars) instead of the full
    /jobs/view/ page (~300k chars, mostly login/cookie chrome that buries the
    description past our text-truncation limits). Returns None for anything that
    isn't a LinkedIn job-detail URL."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    host = parts.netloc.lower()
    if host != "linkedin.com" and not host.endswith(".linkedin.com"):
        return None
    m = re.search(r"/jobs/view/(?:[^/?#]*-)?(\d+)", parts.path)
    if m:
        job_id = m.group(1)
    else:
        job_id = (parse_qs(parts.query).get("currentJobId") or [""])[0]
    if not job_id.isdigit():
        return None
    return _GUEST_POSTING + job_id
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_url_rewrite.py -q`
Expected: PASS (all, including the pre-existing tests)

- [ ] **Step 5: Commit**

```bash
git add app/url_rewrite.py tests/test_url_rewrite.py
git commit -m "feat: linkedin_guest_posting_url — map job-detail URLs to the guest endpoint"
```

---

### Task 2: `HttpFetcher` fetches the rewritten target

**Files:**
- Modify: `app/fetchers/http.py` (import + `fetch()` body)
- Test: `tests/test_fetcher_http.py`

**Interfaces:**
- Consumes: `linkedin_guest_posting_url` from Task 1.
- Produces: no signature change. `HttpFetcher.fetch()` GETs
  `linkedin_guest_posting_url(url) or url`; `RawJob.url` stays `self._source["url"]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_fetcher_http.py`:

```python
_LI_VIEW = "https://fr.linkedin.com/jobs/view/ml-engineer-at-acme-4421669844?position=1"
_LI_GUEST = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/4421669844"
_LI_BODY = "<html><body><p>" + ("word " * 60) + "</p></body></html>"


@respx.mock
def test_http_fetcher_rewrites_linkedin_view_url_to_guest_endpoint():
    route = respx.get(_LI_GUEST).mock(return_value=httpx.Response(200, text=_LI_BODY))
    jobs = HttpFetcher({"url": _LI_VIEW}).fetch()
    assert route.called
    assert jobs and jobs[0].url == _LI_VIEW  # stored/display URL is unchanged


@respx.mock
def test_http_fetcher_leaves_non_linkedin_url_untouched():
    route = respx.get("https://example.com/jobs/1").mock(return_value=httpx.Response(200, text=_LI_BODY))
    HttpFetcher({"url": "https://example.com/jobs/1"}).fetch()
    assert route.called
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_fetcher_http.py -q`
Expected: FAIL — `test_http_fetcher_rewrites_linkedin_view_url_to_guest_endpoint` errors because respx has no route for `_LI_VIEW` (the fetcher currently GETs the view URL).

- [ ] **Step 3: Implement**

In `app/fetchers/http.py`:

Add the import near the top:

```python
from app.url_rewrite import linkedin_guest_posting_url
```

Change the first lines of `fetch()` from:

```python
        try:
            resp = httpx.get(self._source["url"], timeout=30, follow_redirects=True)
```

to:

```python
        try:
            fetch_url = linkedin_guest_posting_url(self._source["url"]) or self._source["url"]
            resp = httpx.get(fetch_url, timeout=30, follow_redirects=True)
```

Leave the rest of `fetch()` and `_parse()` unchanged — `_parse` already builds
`RawJob(url=self._source["url"], ...)`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_fetcher_http.py -q`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add app/fetchers/http.py tests/test_fetcher_http.py
git commit -m "feat: HttpFetcher fetches LinkedIn jobs via the guest posting endpoint"
```

---

### Task 3: `fetch_url_html` fetches the rewritten target

**Files:**
- Modify: `app/fetchers/content.py` (import + `fetch_url_html`)
- Test: `tests/test_fetcher_content.py`

**Interfaces:**
- Consumes: `linkedin_guest_posting_url` from Task 1.
- Produces: no signature change. `fetch_url_html(url)` GETs
  `linkedin_guest_posting_url(url) or url`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_fetcher_content.py`:

```python
@respx.mock
def test_fetch_url_html_rewrites_linkedin_view_url_to_guest_endpoint():
    guest = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/4421669844"
    route = respx.get(guest).mock(return_value=httpx.Response(200, text="<p>posting</p>"))
    body = fetch_url_html(
        "https://fr.linkedin.com/jobs/view/ml-engineer-at-acme-4421669844?position=1"
    )
    assert route.called
    assert body == "<p>posting</p>"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_fetcher_content.py::test_fetch_url_html_rewrites_linkedin_view_url_to_guest_endpoint -q`
Expected: FAIL — no respx route for the view URL.

- [ ] **Step 3: Implement**

In `app/fetchers/content.py`:

Add the import near the top:

```python
from app.url_rewrite import linkedin_guest_posting_url
```

Change `fetch_url_html`:

```python
def fetch_url_html(url: str) -> str:
    try:
        resp = httpx.get(url, timeout=30, follow_redirects=True)
```

to:

```python
def fetch_url_html(url: str) -> str:
    fetch_url = linkedin_guest_posting_url(url) or url
    try:
        resp = httpx.get(fetch_url, timeout=30, follow_redirects=True)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_fetcher_content.py -q`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add app/fetchers/content.py tests/test_fetcher_content.py
git commit -m "feat: fetch_url_html fetches LinkedIn jobs via the guest posting endpoint"
```

---

### Task 4: `summarize()` gains a `raw_passthrough` flag

**Files:**
- Modify: `app/ai/summarize.py` (`summarize` signature + body)
- Test: `tests/test_summarize.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `summarize(client, model, simplified_content, content_type="job_posting", raw_passthrough=True) -> tuple[str, str, str]`.
  With `raw_passthrough=True` (default) behaviour is exactly as today. With
  `raw_passthrough=False`, a `lead` is summarised through the `_SYSTEM`
  (job-posting) path — title/headline/summary all AI-generated, no verbatim body.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_summarize.py`:

```python
def test_summarize_non_slack_lead_gets_ai_summary_not_raw_body():
    response = '{"title": "ML Engineer - Paris @ Acme", "headline": "H", "summary": "**Role:** ML Engineer"}'
    client = _mock_client(response)
    title, headline, summary = summarize(
        client, "llama3.2", "a wall of scraped page chrome",
        content_type="lead", raw_passthrough=False,
    )
    assert title == "ML Engineer - Paris @ Acme"
    assert summary == "**Role:** ML Engineer"


def test_summarize_non_slack_lead_uses_job_posting_prompt():
    client = _mock_client('{"title": "T", "headline": "H", "summary": "S"}')
    summarize(client, "llama3.2", "content", content_type="lead", raw_passthrough=False)
    system = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert "job posting" in system.lower()


def test_summarize_non_slack_lead_empty_summary_on_api_error():
    client = MagicMock()
    client.chat.completions.create.side_effect = Exception("boom")
    result = summarize(client, "llama3.2", "raw", content_type="lead", raw_passthrough=False)
    assert result == ("", "", "")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_summarize.py -q`
Expected: FAIL — `test_summarize_non_slack_lead_*` fail (the lead path still returns the raw body / uses the lead prompt; `summarize` has no `raw_passthrough` kwarg).

- [ ] **Step 3: Implement**

In `app/ai/summarize.py`, replace the `summarize` function body's lead handling.

Change the signature line:

```python
def summarize(
    client: openai.OpenAI,
    model: str,
    simplified_content: str,
    content_type: str = "job_posting",
) -> tuple[str, str, str]:
    is_lead = content_type == "lead"
```

to:

```python
def summarize(
    client: openai.OpenAI,
    model: str,
    simplified_content: str,
    content_type: str = "job_posting",
    raw_passthrough: bool = True,
) -> tuple[str, str, str]:
    # A "lead" keeps its source text verbatim as the summary only when that text
    # is a short human message worth preserving (Slack). For scraped web leads
    # (raw_passthrough=False) the lead is summarised like a posting, so the job
    # body is never a dump of page chrome.
    raw_lead = content_type == "lead" and raw_passthrough
```

Then replace every remaining use of `is_lead` in the function with `raw_lead`:

```python
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _LEAD_SYSTEM if raw_lead else _SYSTEM},
                {"role": "user", "content": simplified_content[:6000]},
            ],
            temperature=0.3,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        data = json.loads(extract_json(resp.choices[0].message.content))
        if raw_lead:
            # Leads are short/vague by nature, so the retained original message
            # (already including its author, see SlackFetcher) is more useful
            # than an AI-compressed rewrite — only title/headline come from AI.
            title = ", ".join(data.get("organizations", []))
            return title, data.get("headline", ""), simplified_content
        title = data.get("title", "")
        summary = data.get("summary", "")
        source_link = _valid_source_link(data.get("source_link", ""), simplified_content)
        if source_link:
            summary = f"{summary}\n\n**Original posting:** [{source_link}]({source_link})"
        return title, data.get("headline", ""), summary
    except Exception:
        return "", "", simplified_content if raw_lead else ""
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_summarize.py -q`
Expected: PASS (all — the pre-existing lead tests call `summarize` without
`raw_passthrough`, so they get the default `True` and are unaffected).

- [ ] **Step 5: Commit**

```bash
git add app/ai/summarize.py tests/test_summarize.py
git commit -m "feat: summarize() raw_passthrough flag — only Slack leads keep verbatim text"
```

---

### Task 5: `_ingest_posting` passes `raw_passthrough=is_slack`

**Files:**
- Modify: `app/pipeline.py` (`_ingest_posting` — the `summarize(...)` call ~line 73)
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `summarize(..., raw_passthrough=...)` from Task 4; `_ingest_posting`
  already has an `is_slack: bool` parameter.
- Produces: no signature change.

- [ ] **Step 1: Update the existing lead test + add a Slack-lead test**

In `tests/test_pipeline.py`, the existing `test_run_fetch_lead_uses_organization_focused_summary`
uses a `generic_listing` `source` fixture, so the lead is now summarised, not
retained. Replace that test with these two:

```python
def test_run_fetch_non_slack_lead_gets_ai_summary(conn, source):
    raw_jobs = [RawJob(url="http://example.com/job/1", title="fallback", company="", raw_text="scraped page chrome")]
    client = _mock_client(
        classify_resp='{"type": "lead", "reason": "thin page, no full posting"}',
        summarize_resp='{"title": "ML Engineer - Paris @ Acme", "headline": "A lead", "summary": "AI summary body"}',
        evaluate_resp='{"score": 0.5, "reasoning": "Some relevance"}',
    )

    with patch("app.pipeline.GenericListingFetcher") as MockFetcher:
        MockFetcher.return_value.fetch.return_value = raw_jobs
        _drain(run_fetch(source, conn, client, "llama3.2", "browser-profile"))

    job = q.get_jobs(conn)[0]
    assert job["content_type"] == "lead"
    # A non-Slack lead is summarised like a posting — never the raw scrape.
    assert job["summary"] == "AI summary body"
    assert job["title"] == "ML Engineer - Paris @ Acme"


def test_run_fetch_slack_lead_retains_raw_message(conn):
    q.insert_scenario(conn, "Remote ML", "")
    q.upsert_profile(conn, "I am an ML engineer.")
    sid = q.insert_source(conn, "Community", "https://community.slack.com", "slack")
    slack_source = q.get_source(conn, sid)
    raw_jobs = [RawJob(url="http://example.com/job/1", title="", company="", raw_text="anyone know if Acme is hiring?")]
    client = _mock_client(
        classify_resp='{"type": "lead", "reason": "vague mention"}',
        summarize_resp='{"organizations": ["Acme", "Globex"], "headline": "A couple of leads"}',
        evaluate_resp='{"score": 0.5, "reasoning": "Some relevance"}',
    )

    with patch("app.pipeline.SlackFetcher") as MockFetcher:
        MockFetcher.return_value.fetch.return_value = raw_jobs
        _drain(run_fetch(slack_source, conn, client, "llama3.2", "browser-profile"))

    job = q.get_jobs(conn)[0]
    assert job["content_type"] == "lead"
    assert job["title"] == "Acme, Globex"
    assert job["summary"] == "anyone know if Acme is hiring?"
```

- [ ] **Step 2: Run the tests to verify the new state**

Run: `uv run pytest tests/test_pipeline.py -q`
Expected: FAIL — `test_run_fetch_non_slack_lead_gets_ai_summary` fails
(`job["summary"]` is still the raw text `"scraped page chrome"` because
`_ingest_posting` calls `summarize` without `raw_passthrough`).
`test_run_fetch_slack_lead_retains_raw_message` should already PASS.

- [ ] **Step 3: Implement**

In `app/pipeline.py`, in `_ingest_posting`, change:

```python
    if content_type in ("job_posting", "lead"):
        ai_title, headline, job_summary = summarize(client, model, simplified, content_type=content_type)
```

to:

```python
    if content_type in ("job_posting", "lead"):
        ai_title, headline, job_summary = summarize(
            client, model, simplified, content_type=content_type, raw_passthrough=is_slack,
        )
```

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS (all ~1000 tests). If `test_run_fetch_stores_published_at` fails,
its lead `summarize_resp` is org-shaped while the non-Slack path now expects
posting-shaped JSON — it still only asserts `published_at`, which is set from
`RawJob.published_at` regardless, so it should pass; if not, change that test's
`summarize_resp` to `'{"title": "T", "headline": "h", "summary": "s"}'`.

- [ ] **Step 5: Commit**

```bash
git add app/pipeline.py tests/test_pipeline.py
git commit -m "feat: only Slack leads keep verbatim body; web-scraped leads are summarised"
```

---

## Self-Review

**Spec coverage:**
- "Fetch-time LinkedIn detail-URL rewrite" → Tasks 1–3.
- `linkedin_guest_posting_url` helper (slug+id, currentJobId, None cases) → Task 1.
- `HttpFetcher` rewrites fetch target, `RawJob.url` unchanged → Task 2.
- `fetch_url_html` rewrites fetch target → Task 3.
- "Lead raw-passthrough guard" (`raw_passthrough` param from `is_slack`) → Tasks 4–5.
- "No change to canonicalize_url / stored URLs / dedup" → honoured (no task touches them).
- Out-of-scope items (cleanup, published_at extraction) → not planned. Correct.

**Placeholder scan:** none — every code step has full code.

**Type consistency:** `linkedin_guest_posting_url(url: str) -> str | None` used
identically in Tasks 2 and 3 (`linkedin_guest_posting_url(x) or x`).
`summarize(..., raw_passthrough=True)` defined in Task 4, called with
`raw_passthrough=is_slack` in Task 5. `raw_lead` naming consistent within Task 4.
