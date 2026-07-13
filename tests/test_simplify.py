from app.ai.simplify import simplify


def test_strips_html_tags():
    html = "<div><h1>Senior ML Engineer</h1><p>We are looking for...</p></div>"
    result = simplify(html)
    assert "<div>" not in result
    assert "Senior ML Engineer" in result
    assert "We are looking for" in result


def test_collapses_whitespace():
    raw = "  lots   of   spaces\n\n\n\nmany newlines  "
    result = simplify(raw)
    assert "   " not in result
    assert result == result.strip()


def test_removes_script_and_style():
    html = "<style>body{color:red}</style><script>alert(1)</script><p>Job description</p>"
    result = simplify(html)
    assert "color:red" not in result
    assert "alert(1)" not in result
    assert "Job description" in result


def test_plain_text_passes_through():
    text = "Senior Engineer at Acme Corp. Remote. €80k."
    result = simplify(text)
    assert "Senior Engineer" in result
    assert "Acme Corp" in result


def test_empty_input():
    assert simplify("") == ""
