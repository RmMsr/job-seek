# Intro / landing page

## Problem

Job Seek has no orientation point. `/` currently renders the jobs triage list
directly, with no explanation of how the pipeline is staged, what data feeds
what, or what to do first on a fresh install. A new (or returning-after-a-
while) user has to read the README to understand the workflow.

## Goal

Add a brief, high-level intro page that:
- Guides first-time setup with a checklist that reflects real state (not a
  separate onboarding flow to maintain).
- Once set up, gets out of the way and shows something immediately useful
  (jobs awaiting review, last fetch time).
- Explains the pipeline stages and what information is used where, briefly.
- Surfaces a missing/incomplete `config.toml` clearly, since that silently
  breaks nearly every other route.

## Non-goals

- No persisted "dismissed"/"seen" onboarding state — the checklist is fully
  derived from current DB/config state on every load.
- No change to the pipeline itself, scoring logic, or other pages beyond the
  route rename below.
- No multi-step wizard or forms on this page — it's read-only, links out to
  the existing Profile/Scenarios/Sources/Fetch/Jobs pages.

## Routing changes

- New `app/routes/home.py`, registered in `app/main.py`, owns `GET /`.
- The jobs triage list moves from `GET /` to `GET /jobs` (edit the route
  decorator in `app/routes/jobs.py`; no other change to that route's logic).
- `base.html` nav: "Jobs" link points to `/jobs`; active-state check on that
  link updates accordingly. `/` is not added to the nav (it's a landing page,
  not a section — same as the existing implicit pattern where the current
  jobs-list page's own nav item is the entry point).
- `tests/test_routes_jobs.py` references to `client.get("/")` (~19 call
  sites) update to `client.get("/jobs")`.

## Config self-check

`get_db()` (in `app/deps.py`) calls `load_config()`, which raises
`FileNotFoundError` if `config.toml` is absent, or `KeyError` if a required
key is missing. Because nearly every route depends on `get_db`, a broken or
missing config currently 500s the whole app. The home route must not depend
on `get_db` up front, or it inherits the same failure with no chance to show
a friendly message.

Add to `app/config.py`:

```python
@dataclass
class ConfigStatus:
    exists: bool
    has_llm_endpoint: bool
    has_llm_model: bool

    @property
    def ok(self) -> bool:
        return self.exists and self.has_llm_endpoint and self.has_llm_model


def check_config_status(path: str = "config.toml") -> ConfigStatus:
    """Non-raising config check. Never throws; used by the home route before
    anything else touches the DB, since a missing/broken config.toml means
    get_db() (which also loads config.toml, for db_path) would 500 first."""
    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    except (FileNotFoundError, tomllib.TOMLDecodeError):
        return ConfigStatus(exists=False, has_llm_endpoint=False, has_llm_model=False)
    llm = raw.get("llm", {})
    return ConfigStatus(
        exists=True,
        has_llm_endpoint=bool(llm.get("endpoint")),
        has_llm_model=bool(llm.get("model")),
    )
```

`home.py`'s route calls `check_config_status()` directly (not through
`Depends`) to decide what banner, if any, to show. For the DB connection, it
uses a new `get_db_optional()` dependency in `app/deps.py` — like `get_db()`
but yields `None` instead of raising when config is missing/invalid, so it's
safe to wire up via ordinary `Depends(get_db_optional)` without crashing
before the route body runs. `get_db()` and `get_db_optional()` share a small
`_open_db(config)` helper to avoid duplicating the connection-setup logic.
Using a real dependency (rather than calling `get_db()` manually) matters
for testability too: `tests/conftest.py`'s `client` fixture swaps in the
in-memory test DB via `app.dependency_overrides`, which only intercepts
parameters resolved through `Depends()`.

If `conn is None` (config invalid), skip the DB-dependent sections
(onboarding checklist / actionable block) entirely — only the banner and the
static "How it works" section render.

Banner copy, one line each depending on which check failed:
- Not `exists`: "No `config.toml` found. Copy `config-template.toml` to
  `config.toml` and edit it before using Job Seek."
- `exists` but endpoint/model missing: "`config.toml` has no inference
  provider configured. Set `llm.endpoint` and `llm.model`."

The banner must be visually prominent and impossible to scroll past
unnoticed: full-width, bold text, generous padding, a solid red left border
or background (deeper/more saturated than the existing pale `.score-low`
badge color, since this needs to read as urgent, not just informational),
placed at the very top of `<main>`, above everything else on the page. It
only renders when a check fails; nothing shows when config is fine.

## Onboarding checklist

Shown only when `check_config_status().ok` is true. Five conditions, each
rendered as a checklist row (done/not-done, not dismissible) linking to the
relevant page:

| # | Condition | Query | Links to |
|---|-----------|-------|----------|
| 1 | Profile filled in | `q.get_profile(conn)` non-empty (stripped) | `/profile` |
| 2 | Scenario created | `q.get_scenarios(conn)` non-empty | `/scenarios` |
| 3 | Source added | `q.get_sources(conn)` non-empty | `/sources` |
| 4 | First fetch completed | new query, see below | `/fetch` |
| 5 | First job reviewed | `q.get_job_counts(conn)`: `accepted + rejected + invalid > 0` | `/jobs` |

New query in `app/db/queries.py`:

```python
def has_completed_fetch_run(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM fetch_runs WHERE completed_at IS NOT NULL LIMIT 1"
    ).fetchone()
    return row is not None
```

While any condition is false, all five rows render (done ones showing as
checked, not hidden) so the user sees overall progress, not just what's left.

## Actionable block

Once all five conditions are true, the checklist is replaced by a compact
block with two links:

- "**N jobs awaiting review**" → `/jobs`, using
  `q.get_job_counts(conn)["new"]`
- "**Last fetch: X ago**" → `/fetch`, using a new query below, rendered
  through the existing `time_ago` Jinja filter (already registered in
  `app/template_env.py`)

New query in `app/db/queries.py`:

```python
def get_last_fetch_completed_at(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT MAX(completed_at) FROM fetch_runs WHERE completed_at IS NOT NULL"
    ).fetchone()
    return row[0] if row and row[0] else None
```

## Pipeline explanation (static)

Always rendered (even when config is broken, since it needs no DB/config).
A compact ordered list — bold stage name + one sentence each, not prose
paragraphs — under a "How it works" heading:

1. **Sources** — Job boards, Slack communities, and listing pages you
   register on the Sources page.
2. **Fetch** — Pulls new postings from each source, skipping URLs already
   seen.
3. **Classify** — An LLM tags each posting as a job posting, a lead,
   irrelevant, or an error.
4. **Summarize** — Job postings and leads get an AI-written title, headline,
   and summary.
5. **Score** — For each active scenario, the summary is evaluated against
   your **Profile** (who you are — skills, interests, constraints, set once)
   and that scenario's **Criteria** (what you're looking for right now —
   must/prefer/avoid, refined over time) to produce a relevance score and
   reasoning.
6. **Triage** — The Jobs list surfaces everything sorted by score. You
   accept, reject, or mark invalid, each with a short note.
7. **Refine** — Your notes accumulate as feedback. The Scenarios page can
   propose criteria updates based on what you've been accepting and
   rejecting — closing the loop back into Score.

## Template structure

New `app/templates/home/index.html` (extends `base.html`), roughly:

```
{% if not config_status.ok %}
  <banner>
{% endif %}

{% if config_status.ok %}
  {% if onboarding_complete %}
    <actionable block>
  {% else %}
    <checklist>
  {% endif %}
{% endif %}

<how-it-works section>
```

Minimal new CSS in `base.html`'s existing `<style>` block: a `.config-banner`
rule (reusing the red palette already used by `.score-low`) and a
`.checklist` rule for the done/not-done rows. No new JS, no htmx endpoints —
this page is fully static per request, no partial-swap interactions needed.

## Testing

- New `tests/test_routes_home.py`:
  - Config missing → banner renders, no checklist/actionable block, "How it
    works" still renders.
  - Config present but no `llm.endpoint`/`model` → same banner behavior
    (different message).
  - Config valid, nothing set up → all 5 checklist rows show not-done.
  - Config valid, partial setup (e.g. profile + scenario, no source) → mixed
    done/not-done rows.
  - Config valid, all 5 conditions met → actionable block renders with
    correct job count and last-fetch time, checklist does not render.
- Update `tests/test_routes_jobs.py`: `client.get("/")` → `client.get("/jobs")`
  throughout.
