# Backlog

Known gaps, not yet scheduled.

- ux: Add a save button for job notes.
- fix: a status change does not need to repeat the note content into history
- fix: /fetch page action buttons should be in line with the title
- ux: a finished task should primarily state the duration after that it finished, not the time stamp
- observability: Add tracing of llm calls
- change: a task on a single job ot source like "Add job by URL" should lik to the final result, single job revisit should link to that. Multiple jobs revisits should lik to them also stating the change. Log lines should include job ids.
- fetch: **`generic_listing` has no pagination.** Sopra Steria and Tieto both cap
  at exactly 10 postings (their page-1 size) — later pages are never fetched.
  (JS-driven pagination, not hyperlink pagers — needs Playwright click/scroll)
- integration: mcp server
- doc: Create a list of supported job board examples for user documentation and regression testing.

## Per-job CV generation — deferred (from the whole-branch review, non-blocking)

- cv: **Re-plan doesn't clear the "base CV changed" badge.** `_staleness` `stale_plan`
  keys off `base_hash`, which plan mode only refreshes when it renders a baseline draft.
  Re-planning an already-drafted job leaves the badge lit until Generate. Decide: refresh
  `base_hash` on every plan run, or split the badge (plan-stale vs draft-stale).
- cv: **First-visit spurious "base CV changed" badge** if the baseline draft fails
  (`base_hash` stays `''` → mismatch). Harmless, cosmetic.
- cv: **Regenerate leaves orphan `page-N.png`** only within a job's own preview dir when a
  re-render is shorter — now cleared by `render_preview_pngs`, but a stale-page flash is
  still possible in the ~ms window between delete and re-copy (single-threaded worker).
- cv: **`_neutralise_markdown_urls` also rewrites example links inside code spans /
  fenced blocks** (e.g. a CV literally showing `[x](https://example.com)`). Fails safe
  (link neutralised, markup intact); unlikely in real CV content.
- cv: **`scope_labels` context key** built by `_workbench_ctx` but unused (workbench
  renders raw scope keys as checkbox labels); either use the full sentences or drop the key.
- cv: **Workbench templates use `class="muted"`; `cv/settings.html` uses inline
  `color:var(--text-muted)`** — cosmetic inconsistency (a `.muted` rule was added to
  base.html so both render; settings.html could switch to the class).
- cv: **doc-write `pip` in the Containerfile is not build-tested** here — `uv pip install`
  + `git` added per review, but a real `podman build` should confirm before relying on it.
- cv/schema (pre-existing, unrelated): the copied live `job-seek.db` was found with an
  orphan `tasks_new` table and NULL `created_at` rows — an interrupted
  `_migrate_tasks_add_cancelled` can wedge startup. Worth a guard that drops `*_new`
  tables before a rebuild migration.
- cv: **Directives not starting with `- ` are invisible to replace/remove proposals**
  (`find_bullet` only matches `- ` lines; the textarea's help text doesn't require a
  dash). System-seeded directives always use `- `, so only matters for hand-typed lines
  without it — silently dropped from the suggestion list, no error shown.
- cv: **Pre-existing `job_cv.plan` rows predate the directive-evaluation rework** and
  lack `action`/`target` — `d.action` renders as `""`, falls to the `add` branch, Apply
  is a silent no-op. Re-running "Evaluate directives" refreshes to the new shape; no
  migration needed per project philosophy, just worth knowing before first post-merge use.
- cv: **No confirmation after a fully-accepted directive-proposal apply** (Profile shows
  "✓ N changes applied"; the CV accept route doesn't set an equivalent summary).
- cv: **No dismiss path for a pending directive-proposal review** other than unchecking
  everything and Apply, or re-evaluating. Profile has a Cancel button; the CV plan is
  persisted (not ephemeral) so the tradeoff differs, but worth a deliberate call.
- cv: **Directive-proposal Apply button is `btn-primary` ("Apply")**, Profile's
  equivalent is `btn-accept` ("Apply changes") — cosmetic drift from the mirrored pattern.
- cv: **Stale-plan banner still reads "base CV changed since this plan"** next to a
  button now labelled "Evaluate directives" — same wording drift the rename was meant to
  clean up, missed in the directive-evaluation rework.
- cv: **Race between directives autosave (600ms debounce) and Apply on the suggestion
  form** — accepting proposals reads `tuning_directives` fresh from the DB, so a hand
  edit followed by a fast Apply click can have the late-landing autosave overwrite the
  merged result, or Apply merge against pre-edit text. Fix: flush/cancel the pending
  autosave before the Apply request fires (htmx `htmx:confirm` + a synchronous save,
  or `hx-include` the live textarea value instead of reading from the DB).
- profile: **`accept_profile_proposals`/`apply_profile_proposals` share the same
  crash-on-blank-field shape** fixed in the CV accept route (`format_bullet(None)` →
  `AttributeError` if a proposal's editable text input is cleared before Apply) — found
  while reviewing the CV directive-evaluation work, not fixed here since it's a
  different, untouched feature.
- cv: **Reloading `/jobs/{id}/cv` mid-first-plan enqueues a duplicate plan task**
  (a wasted LLM call) — `job_cv` isn't persisted until `plan_tailoring` returns, so the
  `{% if job_cv is none %}` autostart re-fires. Pre-existing; the plan-only-first-run
  change removed the in-pane "update in progress" hint that used to mask it. Guard the
  autostart on a queued/running plan task, or persist a stub `job_cv` row before the call.
