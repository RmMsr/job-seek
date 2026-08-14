from app.fetchers.content import MIN_CONTENT_LENGTH, extract_text, has_enough_content, has_enough_text


def test_has_enough_content_true_for_long_text():
    html = "<html><body><p>" + ("word " * 50) + "</p></body></html>"
    assert has_enough_content(html)


def test_has_enough_content_false_for_short_text():
    html = "<html><body><noscript>Enable JavaScript</noscript></body></html>"
    assert not has_enough_content(html)


def test_has_enough_content_boundary_at_min_length():
    text = "a" * MIN_CONTENT_LENGTH
    assert has_enough_content(f"<html><body><p>{text}</p></body></html>")
    assert not has_enough_content(f"<html><body><p>{text[:-1]}</p></body></html>")


def test_extract_text_strips_tags():
    html = "<html><body><h1>Title</h1><p>Body text</p></body></html>"
    text = extract_text(html)
    assert "Title" in text
    assert "Body text" in text
    assert "<h1>" not in text


def test_has_enough_text_true_for_long_plain_text():
    assert has_enough_text("word " * 50)


def test_has_enough_text_false_for_short_plain_text():
    assert not has_enough_text("You need to enable JavaScript to run this app.")
