# Backlog: Combine apply, compact suggestions, move prompt

Three BACKLOG items (2 quick, 1 medium).

## 1. Combine menu: multi-check + Apply

Today every Combine checkbox fires its own `hx-get`, which re-renders `#jobs-content` and closes the menu.

- Checkboxes don't send requests anymore. The panel is a `<form>` (GET `/jobs`, target `#jobs-content`, push URL), with an **Apply** button that sends the checked statuses (`solo=1`) plus the current scenario/source/org/order/q/tag/not_tag state.
- `JobFilter.from_params` accepts `status` repeated as well as comma-joined (it joins all `status` values).
- Apply is disabled while nothing is checked. Closing the menu without Apply changes nothing.

## 2. Compact, keyboard-friendly tag/org suggestions

The native `<datalist>` popup renders huge. Replace it with a small custom dropdown on every input that uses `list="tag-options"` / `list="org-options"` (the inline org/tag editors in `jobs/_macros.html` and the toolbar tag input `#tb-tag`).

- The inputs drop `list=` and get `data-suggest="tag-options|org-options"`. The existing `<datalist>` elements in `base.html` stay as the data source (they already refresh on `tags-changed` / `orgs-changed`).
- On focus and input: case-insensitive matching, prefix matches first, then substring matches, at most 8 items. The list sits absolutely under the input, uses small type and tokens, and has a light border and shadow.
- Keys: ↓/↑ move the highlight; Enter on a highlighted item fills it and closes the list (no submit; a second Enter submits as before); Tab on a highlighted item fills it; Esc closes the list when it's open (the existing Esc cancel only runs when the list is closed). Clicking an item fills it, and `mousedown` is prevented so the inline editor's blur-close doesn't fire.
- Works for inputs added by htmx swaps (event delegation).

## 3. Confirm a job move after a single-job action

After **Reset to new**, **Re-evaluate** or **Revisit** on a single job, if the job's tab changed (e.g. Accepted → New, New → Not relevant, → Trash), the re-rendered job shows a prompt:

> Reset moved this job from **Accepted** to **New**. [Keep in Accepted] [Move] [Move & go to New →]

- Before running, the task snapshots `prev_tab`, `status`, `content_type`. Afterwards it computes the new tab with the same predicates as the list (`_TAB_PREDICATE`). Pass-as-new is explicit intent, so it gets no prompt.
- **Keep in X**: `POST /jobs/{id}/keep-tab` (with the prev fields + filter suffix) restores `status` and `content_type`, and sets `gate_override=1` if `prev_tab == "new"`. It verifies that the job lands in `prev_tab` again, otherwise it rolls back and returns 409. It logs a job event and returns the re-rendered job plus counts OOB, like the feedback route.
- **Keep** is offered only when the restore would actually work (a dry run that rolls back). The case where it can't: Not relevant → New because gates now pass.
- **Move**: dismisses the prompt client-side. The row stays as today's "Moved to X →" stub, or as the expanded panel if the job is still in view.
- **Move & go to X**: links to `/jobs?status=<tab>#job-<id>`, the same as the stale badge link. Not shown on the detail page.
- Shown in the list (stub and expanded) and on the detail page.
- Refresh scope: the row and counts only (already the case). No full list re-render.
