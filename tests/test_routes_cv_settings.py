from app.db import queries as q


def test_cv_page_renders_base_cv_and_save_preview_button(client):
    r = client.get("/cv")
    assert r.status_code == 200
    assert "base_cv" in r.text
    assert "Save &amp; preview" in r.text  # one button does both
    assert "Advanced" in r.text  # link to /cv/advanced


def test_cv_page_shows_download_pdf_when_doc_write_available(client):
    from unittest.mock import patch
    with patch("app.routes.cv.doc_write_available", return_value=True):
        r = client.get("/cv")
    assert 'href="/cv.pdf"' in r.text and "Download PDF" in r.text
    with patch("app.routes.cv.doc_write_available", return_value=False):
        r = client.get("/cv")
    assert 'href="/cv.pdf"' not in r.text


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


def test_cv_form_posts_and_swaps_cv_page(client):
    r = client.get("/cv")
    assert 'hx-post="/cv"' in r.text
    assert 'hx-select="#cv-page"' in r.text


def test_cv_page_base_cv_uses_ink_editor(client, conn):
    r = client.get("/cv")
    assert 'name="base_cv"' in r.text and "data-ink" in r.text


def test_cv_page_base_cv_still_saves(client, conn):
    r = client.post("/cv", data={"base_cv": "# New base\n"})
    assert r.status_code == 200
    assert q.get_cv_settings(conn)["base_cv"] == "# New base\n"


def test_cv_save_persists_base_cv_only(client, conn):
    q.save_cv_settings(conn, base_cv="old", base_instruction="keep me",
                       base_guardrails="keep me too", css="keep", default_scope=[1])
    r = client.post("/cv", data={"base_cv": "# New CV\n\n- thing\n"})
    assert r.status_code == 200
    s = q.get_cv_settings(conn)
    assert s["base_cv"] == "# New CV\n\n- thing\n"
    assert s["base_instruction"] == "keep me"
    assert s["base_guardrails"] == "keep me too"
    assert s["css"] == "keep"


def test_cv_save_shows_preview_iframe(client, conn):
    from unittest.mock import patch
    with patch("app.routes.cv.doc_write_available", return_value=True):
        r = client.post("/cv", data={"base_cv": "# Me\n\n- x\n"})
    assert r.status_code == 200
    assert "<iframe" in r.text
    assert "/cv/preview.html" in r.text
    assert 'class="cv-preview-stage"' in r.text
    assert "cv-preview-fs" in r.text  # fullscreen button
    assert q.get_cv_settings(conn)["base_cv"] == "# Me\n\n- x\n"  # saved, then previewed


def test_cv_preview_html_renders_base_cv(client, conn):
    from unittest.mock import patch
    q.save_cv_settings(conn, base_cv="# Marker CV\n", base_instruction="", base_guardrails="",
                       css="", default_scope=[1])
    with patch("app.routes.cv.doc_write_available", return_value=True), \
         patch("app.routes.cv.render_preview_html", side_effect=lambda md, css: f"<!DOCTYPE html>\n{md}"):
        r = client.get("/cv/preview.html")
    assert r.status_code == 200
    assert "Marker CV" in r.text


def test_cv_save_preview_reports_missing_doc_write(client, conn):
    from unittest.mock import patch
    with patch("app.routes.cv.doc_write_available", return_value=False):
        r = client.post("/cv", data={"base_cv": "# Me"})
    assert r.status_code == 200
    assert "not installed" in r.text


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
