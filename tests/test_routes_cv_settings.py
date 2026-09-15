from app.db import queries as q


def test_cv_page_renders_base_cv_and_autosaves(client):
    r = client.get("/cv")
    assert r.status_code == 200
    assert 'data-md-editor-autosave-url="/cv/save-base"' in r.text
    assert "Save &amp; preview" not in r.text  # autosave replaced the submit button
    assert "Advanced" in r.text  # link to /cv/advanced


def test_cv_page_advanced_link_sits_beside_the_heading(client):
    r = client.get("/cv")
    text = r.text
    heading_start = text.index("<h1")
    advanced_start = text.index('href="/cv/advanced"')
    h1_close = text.index("</h1>", heading_start)
    # the link comes right after the h1, in the same flex row, not at the page's end
    assert heading_start < h1_close < advanced_start
    assert advanced_start < text.index("Your base CV")


def test_cv_page_shows_download_pdf_regardless_of_doc_write(client):
    # Download PDF (like the tailored CV's export controls) is always offered —
    # the PDF route itself 503s if doc-write-cli turns out to be missing.
    from unittest.mock import patch
    with patch("app.routes.cv.doc_write_available", return_value=True):
        r = client.get("/cv")
    assert 'href="/cv.pdf"' in r.text and "Download PDF" in r.text
    with patch("app.routes.cv.doc_write_available", return_value=False):
        r = client.get("/cv")
    assert 'href="/cv.pdf"' in r.text


def test_cv_base_pdf_renders(client, conn):
    from unittest.mock import patch
    q.save_cv_settings(conn, base_cv="# Me\n\n- x\n", base_instruction="", base_guardrails="",
                       css="", default_scope=[])
    with patch("app.routes.cv.render_pdf", return_value=b"%PDF-1.7 fake") as rp:
        r = client.get("/cv.pdf")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF")
    rp.assert_called_once()


def test_cv_base_pdf_404_without_base_cv(client, conn):
    q.save_cv_settings(conn, base_cv="   ", base_instruction="", base_guardrails="",
                       css="", default_scope=[])
    assert client.get("/cv.pdf").status_code == 404


def test_cv_base_pdf_render_error_returns_503(client, conn):
    from unittest.mock import patch
    from app.cv.render import CvRenderError
    q.save_cv_settings(conn, base_cv="# Me", base_instruction="", base_guardrails="",
                       css="", default_scope=[])
    with patch("app.routes.cv.render_pdf", side_effect=CvRenderError("doc-write-cli is not installed")):
        assert client.get("/cv.pdf").status_code == 503


def test_cv_page_has_no_guardrails_field(client):
    r = client.get("/cv")
    assert "base_guardrails" not in r.text


def test_cv_page_base_cv_uses_markdown_editor(client, conn):
    r = client.get("/cv")
    assert 'name="markdown"' in r.text and "data-md-editor" in r.text


def test_cv_page_shows_preview_edit_diff_tabs(client, conn):
    from unittest.mock import patch
    with patch("app.routes.cv.doc_write_available", return_value=True):
        r = client.get("/cv")
    assert 'class="cv-preview-tabs"' in r.text
    assert 'data-variant="preview"' in r.text
    assert 'data-variant="edit"' in r.text
    assert 'data-variant="diff"' in r.text
    assert 'class="cv-preview-stage"' in r.text
    assert "cv-preview-fs" in r.text  # fullscreen button


def test_cv_page_diff_tab_disabled_with_only_one_version(client, conn):
    from unittest.mock import patch
    with patch("app.routes.cv.doc_write_available", return_value=True):
        r = client.get("/cv")
    tab = r.text[r.text.index('data-variant="diff"'):]
    assert "disabled" in tab[:200]


def test_cv_page_diff_tab_activates_once_a_second_version_exists(client, conn):
    # No explicit accept — merely editing past the 1h stacking window (so a
    # genuine second version opens) is enough to have something to diff.
    from unittest.mock import patch
    q.get_cv_settings(conn)  # seeds version 1
    first_id = q.get_cv_settings(conn)["current_version_id"]
    conn.execute("UPDATE cv_versions SET updated_at = datetime('now', '-2 hours') WHERE id = ?", (first_id,))
    conn.commit()
    q.save_cv_settings(conn, base_cv="v2", base_instruction="", base_guardrails="",
                       css="", default_scope=[])

    with patch("app.routes.cv.doc_write_available", return_value=True):
        r = client.get("/cv")
    tab = r.text[r.text.index('data-variant="diff"'):r.text.index("</button>", r.text.index('data-variant="diff"'))]
    assert "disabled" not in tab
    assert tab.rstrip().endswith(">Differences to")
    # Diffs against the previous version, not anything explicitly accepted.
    assert f'data-src="/cv/diff.html?against={first_id}"' in r.text


def test_cv_page_diff_tab_shows_accepted_badge_once_accepted(client, conn):
    from unittest.mock import patch
    q.get_cv_settings(conn)  # seeds version 1 so /cv/accept has a current_version_id to act on
    client.post("/cv/accept")
    with patch("app.routes.cv.doc_write_available", return_value=True):
        r = client.get("/cv")
    tab = r.text[r.text.index('data-variant="diff"'):r.text.index("</button>", r.text.index('data-variant="diff"'))]
    assert "disabled" not in tab
    # "to <target>" isn't repeated on the tab itself — the adjacent picker
    # trigger already names the target, right next to it.
    assert tab.rstrip().endswith(">Differences to")
    assert 'cv-version-badge-accepted">&#9733; Accepted' in r.text[r.text.index('class="cv-version-trigger cv-version-trigger-sm"'):]


def test_cv_page_diff_picker_lists_all_versions_and_switches_target(client, conn):
    from unittest.mock import patch
    q.save_cv_settings(conn, base_cv="v1", base_instruction="", base_guardrails="",
                       css="", default_scope=[])
    first_id = q.get_cv_settings(conn)["current_version_id"]
    conn.execute("UPDATE cv_versions SET updated_at = datetime('now', '-2 hours') WHERE id = ?", (first_id,))
    conn.commit()
    q.accept_base_cv(conn)
    q.save_cv_settings(conn, base_cv="v2", base_instruction="", base_guardrails="",
                       css="", default_scope=[])
    current_id = q.get_cv_settings(conn)["current_version_id"]

    with patch("app.routes.cv.doc_write_available", return_value=True):
        r = client.get("/cv")
    menu = r.text[r.text.index('class="cv-version-menu"'):]
    # current_id is excluded from the picker menu (self-diff is a no-op) —
    # it remains selectable via an explicit ?against=, exercised below.
    assert f"against={first_id}" in menu
    assert f"against={current_id}" not in menu

    with patch("app.routes.cv.doc_write_available", return_value=True):
        r = client.get(f"/cv?against={current_id}")
    # "current" now shows on the adjacent picker trigger, not repeated on
    # the tab label itself.
    trigger = r.text[r.text.index('class="cv-version-trigger cv-version-trigger-sm"'):]
    assert 'cv-version-badge-current">Current' in trigger
    assert f'data-src="/cv/diff.html?against={current_id}"' in r.text

    assert client.get("/cv?against=99999").status_code == 404


def test_cv_page_diff_picker_excludes_the_version_being_viewed(client, conn):
    from unittest.mock import patch
    q.save_cv_settings(conn, base_cv="v1", base_instruction="", base_guardrails="",
                       css="", default_scope=[])
    first_id = q.get_cv_settings(conn)["current_version_id"]
    conn.execute("UPDATE cv_versions SET updated_at = datetime('now', '-2 hours') WHERE id = ?", (first_id,))
    conn.commit()
    q.accept_base_cv(conn)
    q.save_cv_settings(conn, base_cv="v2", base_instruction="", base_guardrails="",
                       css="", default_scope=[])
    second_id = q.get_cv_settings(conn)["current_version_id"]

    # Viewing v1 (first_id) read-only: its own diff-target picker must not
    # offer v1 itself as a diff target (a no-op self-diff) — only v2 (or
    # whatever else exists) should be selectable.
    with patch("app.routes.cv.doc_write_available", return_value=True):
        r = client.get(f"/cv?version={first_id}")
    menu_start = r.text.index('class="cv-version-menu"')
    menu = r.text[menu_start:r.text.index("</div>\n    </div>", menu_start)]
    assert f"against={second_id}" in menu
    assert f"version={first_id}&against={first_id}" not in menu

    # On the plain editable page (not viewing any specific historic
    # version), the live current version is excluded too — diffing it
    # against itself would be a no-op.
    with patch("app.routes.cv.doc_write_available", return_value=True):
        r = client.get("/cv")
    menu = r.text[r.text.index('class="cv-version-menu"'):]
    assert f"against={first_id}" in menu
    assert f"against={second_id}" not in menu


def test_cv_save_base_persists_base_cv_only(client, conn):
    q.save_cv_settings(conn, base_cv="old", base_instruction="keep me",
                       base_guardrails="keep me too", css="keep", default_scope=[1])
    r = client.post("/cv/save-base", data={"markdown": "# New CV\n\n- thing\n"})
    assert r.status_code == 200
    s = q.get_cv_settings(conn)
    assert s["base_cv"] == "# New CV\n\n- thing\n"
    assert s["base_instruction"] == "keep me"
    assert s["base_guardrails"] == "keep me too"
    assert s["css"] == "keep"


def test_cv_save_base_returns_oob_version_list_and_status(client, conn):
    r = client.post("/cv/save-base", data={"markdown": "# New CV\n"})
    assert r.status_code == 200
    assert 'id="cv-version-list"' in r.text and 'hx-swap-oob="true"' in r.text
    assert 'id="cv-editor-status"' in r.text and "Saved" in r.text


def test_cv_preview_html_renders_base_cv(client, conn):
    from unittest.mock import patch
    q.save_cv_settings(conn, base_cv="# Marker CV\n", base_instruction="", base_guardrails="",
                       css="", default_scope=[1])
    with patch("app.routes.cv.doc_write_available", return_value=True), \
         patch("app.routes.cv.render_preview_html", side_effect=lambda md, css: f"<!DOCTYPE html>\n{md}"):
        r = client.get("/cv/preview.html")
    assert r.status_code == 200
    assert "Marker CV" in r.text


def test_cv_page_reports_missing_doc_write(client, conn):
    from unittest.mock import patch
    with patch("app.routes.cv.doc_write_available", return_value=False):
        r = client.get("/cv")
    assert r.status_code == 200
    assert "not installed" in r.text
    assert 'data-md-editor-autosave-url="/cv/save-base"' in r.text  # still autosaves


def test_cv_diff_html_shows_placeholder_with_only_one_version(client, conn):
    r = client.get("/cv/diff.html")
    assert r.status_code == 200
    assert "Nothing to compare against yet" in r.text


def test_cv_diff_html_diffs_current_against_previous_version_without_accepting(client, conn):
    from unittest.mock import patch
    q.get_cv_settings(conn)  # seeds version 1
    first_id = q.get_cv_settings(conn)["current_version_id"]
    conn.execute("UPDATE cv_versions SET updated_at = datetime('now', '-2 hours') WHERE id = ?", (first_id,))
    conn.commit()
    q.save_cv_settings(conn, base_cv="# Accepted content\n\n- new bullet\n", base_instruction="",
                       base_guardrails="", css="", default_scope=[])
    with patch("app.routes.cv.doc_write_available", return_value=True), \
         patch("app.routes.cv.render_diff_html", side_effect=lambda md, css: f"<!DOCTYPE html>\n{md}"):
        r = client.get("/cv/diff.html")
    assert r.status_code == 200
    assert "new bullet" in r.text


def test_cv_diff_html_diffs_current_against_accepted(client, conn):
    from unittest.mock import patch
    q.save_cv_settings(conn, base_cv="# Accepted content\n", base_instruction="", base_guardrails="",
                       css="", default_scope=[])
    client.post("/cv/accept")
    conn.execute("UPDATE cv_versions SET updated_at = datetime('now', '-2 hours') "
                "WHERE entity_type = 'base' AND entity_id = 1")
    conn.commit()
    q.save_cv_settings(conn, base_cv="# Accepted content\n\n- new bullet\n", base_instruction="",
                       base_guardrails="", css="", default_scope=[])
    with patch("app.routes.cv.doc_write_available", return_value=True), \
         patch("app.routes.cv.render_diff_html", side_effect=lambda md, css: f"<!DOCTYPE html>\n{md}"):
        r = client.get("/cv/diff.html")
    assert r.status_code == 200
    assert "new bullet" in r.text


def test_cv_advanced_page_has_headed_sections(client):
    r = client.get("/cv/advanced")
    assert r.status_code == 200
    assert "Guardrails" in r.text
    assert "Editing scopes" in r.text
    assert "Writing style" in r.text
    assert "Appearance" in r.text
    assert "House-style" not in r.text  # renamed
    assert "base_cv" not in r.text  # lives on the primary page now


def test_cv_save_style_roundtrip(client, conn):
    r = client.post("/cv/save-style", data={"base_instruction": "British English"})
    assert r.status_code == 200
    assert "Saved" in r.text
    assert q.get_cv_settings(conn)["base_instruction"] == "British English"


def test_cv_save_guardrails_roundtrip(client, conn):
    r = client.post("/cv/save-guardrails", data={"base_guardrails": "No invented dates"})
    assert r.status_code == 200
    assert "Saved" in r.text
    assert q.get_cv_settings(conn)["base_guardrails"] == "No invented dates"


def test_save_confirmation_is_scoped_to_the_saved_section(client):
    # one "Saved." only, and it sits inside the guardrails form, not the others
    r = client.post("/cv/save-guardrails", data={"base_guardrails": "x"})
    assert r.text.count(">Saved.</div>") == 1
    guardrails_form = r.text.split('action="/cv/save-guardrails"')[1].split("</form>")[0]
    assert "Saved." in guardrails_form


def test_save_operations_redirect_back_to_advanced(client):
    # post-redirect-get: the browser lands on /cv/advanced, never on /cv/save-*
    r = client.post("/cv/save-style", data={"base_instruction": "x"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/cv/advanced?saved=style"
    r = client.post("/cv/reset-css", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/cv/advanced?saved=css"


def test_cv_save_style_leaves_other_sections_untouched(client, conn):
    q.save_cv_settings(conn, base_cv="", base_instruction="", base_guardrails="custom",
                        css="p{color:red}", default_scope=[1])
    client.post("/cv/save-style", data={"base_instruction": "British English"})
    s = q.get_cv_settings(conn)
    assert s["base_guardrails"] == "custom"
    assert s["css"] == "p{color:red}"


def test_cv_save_css_roundtrip(client, conn):
    r = client.post("/cv/save-css", data={"css": "p { color: red; }"})
    assert r.status_code == 200
    assert "Saved" in r.text
    assert q.get_cv_settings(conn)["css"] == "p { color: red; }"


def test_cv_save_css_rejects_bad_css(client, conn):
    r = client.post("/cv/save-css", data={"css": "@import url('http://evil/x.css');"})
    assert r.status_code == 200
    assert "@import" in r.text
    assert q.get_cv_settings(conn)["css"] == ""  # not saved


def test_reset_guardrails_restores_defaults(client, conn):
    from app.cv.instruction import DEFAULT_GUARDRAILS
    q.save_cv_settings(conn, base_cv="# Me", base_instruction="", base_guardrails="my custom rule only",
                       css="", default_scope=[1])
    r = client.post("/cv/reset-guardrails")
    assert r.status_code == 200
    assert q.get_cv_settings(conn)["base_guardrails"] == DEFAULT_GUARDRAILS


def test_reset_style_clears_to_empty(client, conn):
    q.save_cv_settings(conn, base_cv="", base_instruction="something custom",
                       base_guardrails="", css="", default_scope=[1])
    r = client.post("/cv/reset-style")
    assert r.status_code == 200
    assert q.get_cv_settings(conn)["base_instruction"] == ""


def test_reset_css_clears_to_empty(client, conn):
    q.save_cv_settings(conn, base_cv="", base_instruction="", base_guardrails="",
                       css="p{color:red}", default_scope=[1])
    r = client.post("/cv/reset-css")
    assert r.status_code == 200
    assert q.get_cv_settings(conn)["css"] == ""


def test_reset_preserves_other_settings(client, conn):
    q.save_cv_settings(conn, base_cv="# Keep me", base_instruction="British English",
                       base_guardrails="custom", css="p{color:red}", default_scope=[1])
    client.post("/cv/reset-guardrails")
    s = q.get_cv_settings(conn)
    assert s["base_cv"] == "# Keep me"
    assert s["base_instruction"] == "British English"
    assert s["css"] == "p{color:red}"


def test_new_cv_settings_seeded_with_default_guardrails(client, conn):
    from app.cv.instruction import DEFAULT_GUARDRAILS_RULES
    s = q.get_cv_settings(conn)
    for rule in DEFAULT_GUARDRAILS_RULES:
        assert rule in s["base_guardrails"]


def test_setup_subnav_cv_tab_points_to_advanced(client):
    r = client.get("/setup")
    assert 'href="/cv/advanced"' in r.text


def test_scope_options_add(client, conn):
    r = client.post("/cv/scope-options", data={"description": "Custom scope.", "default_enabled": "on"})
    assert r.status_code == 200
    assert "Custom scope." in r.text
    opts = q.get_scope_options(conn)
    assert opts[-1]["description"] == "Custom scope."
    assert opts[-1]["default_enabled"] == 1


def test_scope_options_save_all_updates_descriptions_and_defaults(client, conn):
    id_a = q.insert_scope_option(conn, "Original A.", default_enabled=True)
    id_b = q.insert_scope_option(conn, "Original B.", default_enabled=False)
    r = client.post(
        "/cv/scope-options/save-all",
        data={
            "id": [str(id_a), str(id_b)],
            "name": ["", ""],
            "description": ["Edited A.", "Edited B."],
            "default_enabled": [str(id_b)],
        },
    )
    assert r.status_code == 200
    assert "Edited A." in r.text
    assert "Edited B." in r.text
    a, b = q.get_scope_option(conn, id_a), q.get_scope_option(conn, id_b)
    assert a["description"] == "Edited A." and a["default_enabled"] == 0
    assert b["description"] == "Edited B." and b["default_enabled"] == 1


def test_scope_options_save_all_skips_blank_description(client, conn):
    opt_id = q.insert_scope_option(conn, "Keep me.")
    client.post("/cv/scope-options/save-all",
                data={"id": [str(opt_id)], "name": [""], "description": ["  "]})
    assert q.get_scope_option(conn, opt_id)["description"] == "Keep me."


def test_save_all_scope_options_persists_name_and_shows_saved(client, conn):
    q.reset_scope_options(conn)
    opts = q.get_scope_options(conn)
    r = client.post("/cv/scope-options/save-all", data={
        "id": [str(o["id"]) for o in opts],
        "name": ["renamed" if i == 0 else o["name"] for i, o in enumerate(opts)],
        "description": [o["description"] for o in opts],
        "default_enabled": [str(opts[0]["id"])],
    })
    assert r.status_code == 200
    assert "Saved." in r.text
    assert q.get_scope_options(conn)[0]["name"] == "renamed"


def test_add_scope_option_persists_name(client, conn):
    q.reset_scope_options(conn)
    r = client.post("/cv/scope-options", data={"name": "extra", "description": "An extra scope."})
    assert r.status_code == 200
    assert q.get_scope_options(conn)[-1]["name"] == "extra"


def test_scope_editor_renders_name_input_and_autosize(client):
    r = client.get("/cv/advanced")
    assert 'name="name"' in r.text
    assert 'class="autosize"' in r.text


def test_save_directives_template_persists_and_keeps_other_fields(client, conn):
    q.save_cv_settings(conn, base_cv="KEEP", base_instruction="", base_guardrails="G",
                       css="", default_scope=[1], directives_template="## Old")
    r = client.post("/cv/save-directives-template", data={"directives_template": "## New\n## Two"})
    assert r.status_code == 200
    s = q.get_cv_settings(conn)
    assert s["directives_template"] == "## New\n## Two"
    assert s["base_cv"] == "KEEP" and s["base_guardrails"] == "G"


def test_reset_directives_template_restores_default(client, conn):
    from app.cv.instruction import DEFAULT_DIRECTIVES_TEMPLATE
    q.save_cv_settings(conn, base_cv="", base_instruction="", base_guardrails="",
                       css="", default_scope=[1], directives_template="## Mangled")
    r = client.post("/cv/reset-directives-template", data={})
    assert r.status_code == 200
    assert q.get_cv_settings(conn)["directives_template"] == DEFAULT_DIRECTIVES_TEMPLATE


def test_saving_guardrails_preserves_directives_template(client, conn):
    q.save_cv_settings(conn, base_cv="", base_instruction="", base_guardrails="",
                       css="", default_scope=[1], directives_template="## Keep me")
    client.post("/cv/save-guardrails", data={"base_guardrails": "New rule"})
    assert q.get_cv_settings(conn)["directives_template"] == "## Keep me"


def test_advanced_page_renders_directives_template_textarea(client):
    r = client.get("/cv/advanced")
    assert 'name="directives_template"' in r.text


def test_advanced_forms_are_plain_post_no_htmx():
    # saves use post-redirect-get (no JS dependency); no leftover hx-* attrs
    from pathlib import Path
    html = Path("app/templates/cv/advanced.html").read_text()
    assert "hx-post" not in html and "hx-select" not in html
    for action in ("/cv/save-style", "/cv/save-guardrails", "/cv/save-css",
                   "/cv/save-directives-template"):
        assert f'action="{action}"' in html


def test_scope_options_delete(client, conn):
    opt_id = q.insert_scope_option(conn, "Delete me.")
    r = client.delete(f"/cv/scope-options/{opt_id}")
    assert r.status_code == 200
    assert q.get_scope_option(conn, opt_id) is None


def test_scope_options_reset_restores_defaults(client, conn):
    q.reset_scope_options(conn)  # ensure a known baseline (conftest's conn already seeds via init_db)
    opts = q.get_scope_options(conn)
    q.update_scope_option(conn, opts[0]["id"], "Mangled.", default_enabled=False)
    r = client.post("/cv/scope-options/reset")
    assert r.status_code == 200
    fresh = q.get_scope_options(conn)
    assert len(fresh) == 6
    assert "Mangled." not in [o["description"] for o in fresh]


def test_cv_page_shows_read_only_historic_version(client, conn):
    q.save_cv_settings(conn, base_cv="v1", base_instruction="", base_guardrails="",
                       css="", default_scope=[])
    old_id = q.get_cv_settings(conn)["current_version_id"]
    # Past the 1h manual-edit stacking window, so v2 opens a distinct version
    # instead of overwriting v1's row in place (see Task 2's stacking rule).
    conn.execute("UPDATE cv_versions SET updated_at = datetime('now', '-2 hours') WHERE id = ?", (old_id,))
    conn.commit()
    q.save_cv_settings(conn, base_cv="v2", base_instruction="", base_guardrails="",
                       css="", default_scope=[])

    r = client.get(f"/cv?version={old_id}")
    assert r.status_code == 200
    assert "Reopen this version" in r.text
    # No markdown editor while viewing history (the hidden textarea backing
    # "Copy markdown" in the export controls doesn't count as one; base.html's
    # global <script>/<style> blocks also mention these class/attribute names,
    # so check for the actual rendered element rather than a bare substring).
    assert 'class="md-editor-source"' not in r.text
    assert 'data-variant="edit"' not in r.text

    # Reopen sits beside the picker, in the same row — both come before the
    # separate Download PDF/Copy markdown action bar, not inside it.
    row_pos = r.text.index('class="cv-version-row"')
    trigger_pos = r.text.index('class="btn cv-version-trigger"')
    reopen_pos = r.text.index("Reopen this version")
    pdf_pos = r.text.index("Download PDF")
    assert row_pos < trigger_pos < reopen_pos < pdf_pos

    # The menu keeps a static newest-first order — v2 (current, newer) stays
    # ahead of v1 (older, the one actually on screen here), it doesn't jump
    # to the top just because it's what's being viewed.
    new_id = q.get_cv_settings(conn)["current_version_id"]
    new_hash = q.get_version(conn, "base", 1, new_id)["hash"][:6]
    old_hash = q.get_version(conn, "base", 1, old_id)["hash"][:6]
    menu = r.text[r.text.index('class="cv-version-menu"'):]
    assert menu.index(new_hash) < menu.index(old_hash)


def test_cv_page_read_only_version_offers_accept(client, conn):
    q.save_cv_settings(conn, base_cv="v1", base_instruction="", base_guardrails="",
                       css="", default_scope=[])
    old_id = q.get_cv_settings(conn)["current_version_id"]
    conn.execute("UPDATE cv_versions SET updated_at = datetime('now', '-2 hours') WHERE id = ?", (old_id,))
    conn.commit()
    q.save_cv_settings(conn, base_cv="v2", base_instruction="", base_guardrails="",
                       css="", default_scope=[])

    r = client.get(f"/cv?version={old_id}")
    assert "&#9733; Accept this version</button>" in r.text

    r = client.post(f"/cv/versions/{old_id}/accept", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == f"/cv?version={old_id}"
    assert q.get_version(conn, "base", 1, old_id)["accepted_at"] is not None
    assert q.get_cv_settings(conn)["current_version_id"] != old_id   # current untouched

    # Already accepted — nothing more to do from here (accepting a
    # different version replaces the mark; there's no separate "unaccept").
    r = client.get(f"/cv?version={old_id}")
    assert "&#9733; Accept this version</button>" not in r.text

    assert client.post("/cv/versions/99999/accept").status_code == 404


def test_cv_page_shows_when_a_non_current_version_is_accepted(client, conn):
    """Accepting a historic version must not read as "nothing accepted" on
    the editable page — settings.accepted_at only reflects whether *current*
    is accepted, so the page needs the true accepted_version to tell the two
    apart and avoid re-offering an Accept button that would silently steal
    the mark."""
    q.save_cv_settings(conn, base_cv="v1", base_instruction="", base_guardrails="",
                       css="", default_scope=[])
    old_id = q.get_cv_settings(conn)["current_version_id"]
    old_hash = q.get_version(conn, "base", 1, old_id)["hash"]
    conn.execute("UPDATE cv_versions SET updated_at = datetime('now', '-2 hours') WHERE id = ?", (old_id,))
    conn.commit()
    q.save_cv_settings(conn, base_cv="v2", base_instruction="", base_guardrails="",
                       css="", default_scope=[])
    q.accept_base_cv_version(conn, old_id)

    r = client.get("/cv")
    assert f"Version <code>{old_hash[:6]}</code> is accepted" in r.text
    assert f'href="/cv?version={old_id}"' in r.text
    assert "&#9733; Accept this version instead</button>" in r.text
    # not the "nothing accepted yet" wording, and not the plain "Accepted ..."
    # badge either (that's reserved for when *current* is the accepted one).
    assert "Accept this version for tailoring" not in r.text
    assert "— this is what tailoring uses." not in r.text


def test_cv_page_read_only_version_shows_the_accepted_badge(client, conn):
    """Mirrors the tailored-CV version view: the accepted marker belongs to the
    version row, so viewing that historic version must still show it (on the
    version picker's trigger) even after editing has moved current_version_id
    on."""
    q.save_cv_settings(conn, base_cv="v1", base_instruction="", base_guardrails="",
                       css="", default_scope=[])
    accepted_id = q.get_cv_settings(conn)["current_version_id"]
    q.accept_base_cv(conn)
    q.save_cv_settings(conn, base_cv="v2", base_instruction="", base_guardrails="",
                       css="", default_scope=[])   # accepted current -> new version
    assert q.get_cv_settings(conn)["current_version_id"] != accepted_id

    r = client.get(f"/cv?version={accepted_id}")
    assert r.status_code == 200
    trigger_pos = r.text.index('class="btn cv-version-trigger"')
    trigger = r.text[trigger_pos:r.text.index('</button>', trigger_pos)]
    assert 'cv-version-badge-accepted">&#9733; Accepted' in trigger

    # ...and the version that is merely current, not accepted, does not.
    r = client.get(f"/cv?version={q.get_cv_settings(conn)['current_version_id']}")
    trigger_pos = r.text.index('class="btn cv-version-trigger"')
    trigger = r.text[trigger_pos:r.text.index('</button>', trigger_pos)]
    assert 'cv-version-badge-accepted' not in trigger

    # ...and the version that is merely current, not accepted, does not.
    r = client.get(f"/cv?version={q.get_cv_settings(conn)['current_version_id']}")
    assert 'class="cv-version-accepted"' not in r.text


def test_cv_page_404s_for_unknown_version(client, conn):
    assert client.get("/cv?version=99999").status_code == 404


def test_cv_page_version_picker_shows_hash_badges_and_parent(client, conn):
    q.save_cv_settings(conn, base_cv="v1", base_instruction="", base_guardrails="",
                       css="", default_scope=[])
    first_id = q.get_cv_settings(conn)["current_version_id"]
    conn.execute("UPDATE cv_versions SET updated_at = datetime('now', '-2 hours') WHERE id = ?", (first_id,))
    conn.commit()
    q.accept_base_cv(conn)   # accept v1 before moving on, so its star sticks
    q.save_cv_settings(conn, base_cv="v2", base_instruction="", base_guardrails="",
                       css="", default_scope=[])

    r = client.get("/cv")
    assert 'class="cv-section-heading">History' in r.text
    assert 'class="btn cv-version-trigger"' in r.text
    assert 'class="cv-version-menu"' in r.text

    first = q.get_version(conn, "base", 1, first_id)
    second_id = q.get_cv_settings(conn)["current_version_id"]
    second = q.get_version(conn, "base", 1, second_id)
    assert second["parent_hash"] == first["hash"]

    # The trigger always shows the currently-visible version (here: current,
    # v2), badged as such — not just whatever happens to sort first.
    trigger_pos = r.text.index('class="btn cv-version-trigger"')
    trigger = r.text[trigger_pos:r.text.index('</button>', trigger_pos)]
    assert second["hash"][:6] in trigger
    assert 'cv-version-badge-current">Current' in trigger

    # v1 (accepted) and v2 (current, parented on v1) both carry their badges
    # and info inside the menu too, not just on the closed trigger.
    menu = r.text[r.text.index('class="cv-version-menu"'):]
    assert f'cv-version-badge-accepted">&#9733; Accepted' in menu
    assert f'from version {first["hash"][:6]}' in menu
    # The row for whichever version is on screen stays marked once the menu
    # is open (not only via the trigger's closed-state text).
    assert 'is-selected' in menu
    assert 'cv-version-check' in menu


def test_cv_page_lists_history_and_accept_controls(client, conn):
    q.save_cv_settings(conn, base_cv="v1", base_instruction="", base_guardrails="",
                       css="", default_scope=[])
    first_id = q.get_cv_settings(conn)["current_version_id"]
    # Past the 1h stacking window, so the version list below has a non-current
    # entry to show (otherwise both saves collapse into one current version
    # and the list — which excludes current — renders empty).
    conn.execute("UPDATE cv_versions SET updated_at = datetime('now', '-2 hours') WHERE id = ?", (first_id,))
    conn.commit()
    q.save_cv_settings(conn, base_cv="v2", base_instruction="", base_guardrails="",
                       css="", default_scope=[])

    r = client.get("/cv")
    assert 'class="cv-version-list"' in r.text
    assert '/cv/accept' in r.text

    client.post("/cv/accept")
    r = client.get("/cv")
    assert "Accepted" in r.text
    # Already accepted — no action needed (accepting a different version is
    # what replaces it, there's no separate "unaccept").
    assert 'action="/cv/accept"' not in r.text
