# CV settings & configurability — design

## Goal

The base CV is core content the user maintains regularly; it's currently
buried under Setup alongside advanced/experimental knobs (guardrails, edit
scopes, house-style instruction, CSS). Give it a first-class page, make the
advanced knobs individually resettable, and turn the fixed 4-item edit-scope
checkbox list into a user-editable list like guardrails already are.

## 1. Navigation and page split

- Main nav (`base.html`) gets a new **CV** link, placed after **Profile**.
  Gated on the CV feature flag via a new Jinja global (below), not
  route-supplied context — nav renders on every page, and per-route context
  threading is exactly what caused today's "Actions group missing" bugs.
- `app/template_env.py` registers `app.config.cv_enabled` as a Jinja
  global: `templates.env.globals["cv_enabled"] = cv_enabled`. Templates call
  it as `{% if cv_enabled() %}`. Existing per-route `context["cv_enabled"]`
  values are unaffected (a route-supplied variable shadows the global of the
  same name) — this only adds the capability where no route context exists,
  i.e. `base.html`'s nav.
- **`/cv`** becomes the new, primary page: base CV (markdown) editor, a
  **Preview** button, and a link to **Advanced CV settings** (→
  `/cv/advanced`). This is what "CV" in the main nav points to.
- **`/cv/advanced`** takes over today's `/cv` settings content minus the
  base CV field: guardrails, editing scopes, writing style, CSS — each in
  its own headed section with its own **Reset to defaults** button. Reached
  from the Setup subnav's "CV" tab (updated to point here) and from the
  link on `/cv`.
- Base CV has no reset button — there is no factory default for someone's
  résumé.

## 2. Base CV preview

`/cv` gets a **Preview** button next to the base-CV textarea. It reuses the
tailored-CV render pipeline exactly (`render_preview_pngs`,
`doc_write_available`, `CvRenderError` from `app/cv/render.py`) but stays
simple: no caching, no new DB column, no new file-serving route. `POST
/cv/preview` renders to a throwaway temp directory, base64-encodes each
page's PNG bytes into `data:` URIs, and returns them inline in an htmx
partial. Same "doc-write-cli not installed" fallback message as the
tailored-CV preview.

## 3. Renamed and headed sections on `/cv/advanced`

Plain-language section headings, each with its own Reset-to-defaults button
next to the heading:

- **Guardrails** (already exists as of the previous mechanics work — no
  functional change, just placement under this heading).
- **Editing scopes** (see §4).
- **Writing style** — renamed from "House-style instruction"; same field
  (`base_instruction`), same help text. Reset restores it to `""`.
- **Appearance (CSS)** — renamed from "Global CSS"; same field (`css`), same
  help text. Reset restores it to `""`.

## 4. Editing scopes become a user-editable list

Today `SCOPE_ORDER`/`SCOPE_LINES` in `app/cv/instruction.py` are a hardcoded
4-entry dict (select/reorder/rephrase/summary) baked into
`compose_instruction()`, the plan-pane checkboxes, and `BASELINE_SCOPE`.
Move this into a real table, `cv_scope_options`, following the same
inline-edit CRUD pattern this codebase already uses for scenario criteria
(`app/routes/scenarios.py`'s `/criteria/{id}` routes +
`scenarios/_criterion*.html`):

```sql
CREATE TABLE IF NOT EXISTS cv_scope_options (
    id INTEGER PRIMARY KEY,
    description TEXT NOT NULL,
    default_enabled INTEGER NOT NULL DEFAULT 0,
    is_baseline INTEGER NOT NULL DEFAULT 0,
    sort_order INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

- `description` is the one editable field — the full sentence, e.g. "Reorder
  bullets and sections… to foreground the experience this job values most."
  There is no separate short "key" shown anywhere; the checkbox label *is*
  the description, matching today's behavior exactly (today's checkbox
  label is already the full `SCOPE_LINES` sentence, not the short word).
- `default_enabled` — whether a new job starts with this scope checked
  (today's `cv_settings.default_scope`).
- `is_baseline` — whether the very first, most-conservative auto-generated
  draft (on first workbench visit) includes this scope. Today this is the
  hardcoded `BASELINE_SCOPE = ["select", "reorder"]`; it becomes "whichever
  rows have `is_baseline = 1`" — editable through the same reset, not
  exposed as a separate checkbox in this iteration (YAGNI — nobody has
  asked to tune baseline conservativeness independently of "reorder"/"select"
  specifically).
- `sort_order` — display and `compose_instruction` iteration order.
- **Reset to defaults** deletes all rows and reseeds the four defaults.

**Reference change:** `job_cv.scope` and `cv_settings.default_scope` (both
existing JSON columns) switch from arrays of string keys (`["select",
"reorder"]`) to arrays of `cv_scope_options.id` integers. `compose_instruction`
looks up enabled descriptions by id against the *current* `cv_scope_options`
table instead of a static dict — a job whose stored scope references a
since-deleted row just silently omits that line, the same way a since-edited
guardrail doesn't retroactively change past checks. No special-casing needed.

**Migration** (hard downtime, per project convention — personal
single-instance app):
1. Create `cv_scope_options`, seed the four defaults in a fixed order so
   their ids are deterministic (1=select, 2=reorder, 3=rephrase,
   4=summary), `default_enabled=1` for ids 1-2 (matches today's default),
   `is_baseline=1` for ids 1-2 (matches today's `BASELINE_SCOPE`).
2. Rewrite `cv_settings.default_scope` and every `job_cv.scope` JSON array,
   mapping each old string key to its seeded id via that fixed table.

## 5. UI for the editable scope list

Mirrors `scenarios/_criteria.html` + `_criterion_edit.html`:
- A list of rows, each showing the description text, its default-enabled
  checkbox, **Edit**, and **Delete**.
- **Edit** swaps a row into an inline form (description textarea +
  default-enabled checkbox + Save/Cancel), same GET-edit-form /
  POST-save shape as criteria.
- An **Add a scope type** mini-form at the bottom (description + checkbox +
  Add).
- The per-job plan pane's scope checkboxes (`cv/_plan_pane.html`) and the
  settings-page "default scope" checkboxes both iterate the same live
  `cv_scope_options` rows instead of the static `SCOPE_ORDER`/`SCOPE_LINES`.

## Out of scope

- Reordering scope rows via drag-and-drop — `sort_order` exists in the
  schema for `compose_instruction`'s output order but there's no UI to
  change it yet; new rows append at the end. Add later if it's ever
  actually needed.
- Any change to guardrails beyond the heading/placement already shipped.

## Testing

Extend rather than replace: `tests/test_cv_instruction.py` (new
`compose_instruction` scope-lookup shape), `tests/test_schema.py` (new
table + migration + idempotency), `tests/test_routes_cv_settings.py` (page
split, per-field resets, scope CRUD), `tests/test_routes_cv_workbench.py`
and `tests/test_routes_cv_actions.py` (plan pane still renders/saves scope
correctly against ids instead of string keys), `tests/test_cv_task.py`
(baseline draft still uses `is_baseline` rows).
