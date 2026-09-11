from app.template_env import templates


def _render(**kw):
    tmpl = templates.env.from_string(
        '{% from "_ink_editor.html" import ink_editor %}'
        '{{ ink_editor(name, value, min_height, autosave_url) }}'
    )
    return tmpl.render(**kw)


def test_macro_emits_hidden_textarea_with_data_ink():
    html = _render(name="base_cv", value="# Hi", min_height="420px", autosave_url=None)
    assert 'name="base_cv"' in html
    assert "data-ink" in html
    assert 'data-ink-min-height="420px"' in html
    assert "# Hi" in html
    assert "ink-mount" in html
    assert "data-ink-autosave-url" not in html


def test_macro_includes_autosave_url_when_given():
    html = _render(name="markdown", value="x", min_height="300px",
                   autosave_url="/jobs/7/cv/save-tailored")
    assert 'data-ink-autosave-url="/jobs/7/cv/save-tailored"' in html


def test_macro_escapes_value():
    html = _render(name="c", value="<script>alert(1)</script>", min_height="300px", autosave_url=None)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
