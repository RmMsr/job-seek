from app.ai.json_utils import extract_json


def test_extract_json_passes_through_plain_json():
    assert extract_json('{"score": 0.5}') == '{"score": 0.5}'


def test_extract_json_strips_json_fenced_code_block():
    text = '```json\n{"score": 0.5, "reasoning": "ok"}\n```'
    assert extract_json(text) == '{"score": 0.5, "reasoning": "ok"}'


def test_extract_json_strips_bare_fenced_code_block():
    text = '```\n{"score": 0.5}\n```'
    assert extract_json(text) == '{"score": 0.5}'


def test_extract_json_strips_surrounding_whitespace():
    text = '  \n{"score": 0.5}\n  '
    assert extract_json(text) == '{"score": 0.5}'
