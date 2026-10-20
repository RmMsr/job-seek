# Per-job CSS override

Optional CSS for a single job's CV, appended after the global CSS from
Advanced CV settings.

## Storage

New column `job_cv.css_override TEXT NOT NULL DEFAULT ''` (ALTER TABLE
migration, same pattern as `_migrate_job_cv_add_*`). Not versioned — it's
styling, not CV content.

## Rendering

`_job_css(conn, job_id)` returns `settings.css + "\n" + css_override`. Every
job render uses it instead of `settings["css"]`:

- `/jobs/{id}/cv/preview.html` — base and tailored variants, incl. `?version=`
- `/jobs/{id}/cv/diff.html` — `_DIFF_CSS` still appended last
- `/jobs/{id}/cv.pdf`

Base-CV pages (`/cv/{id}`) are unaffected.

## UI

A folded `<details class="cv-css-override">` on the job preview page,
directly below the Help block. Collapsed by default. Summary "Custom CSS for
this job", plus an "active" badge when the override is non-empty.

Inside: monospace textarea, one hint line, and a **Reload preview** button.
Reload POSTs to `/jobs/{id}/cv/save-css`:

- `validate_css` fails → inline error by the button, nothing saved or reloaded.
- Success → upsert the column; client moves every stage iframe's `src` to
  `data-src` (Base included) and re-activates the current tab, reusing the
  existing lazy-load + "Loading…" notice.
- Empty textarea clears the override. Reload always redraws, even unchanged.

## Tests

Migration adds the column; save route persists / rejects invalid CSS; each
job render route passes global + override CSS to `render_*`; panel and badge
render in the preview page.
