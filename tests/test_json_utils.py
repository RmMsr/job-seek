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


def test_extract_json_uses_last_block_when_model_self_corrects():
    # Some models still emit visible reasoning between fenced blocks despite
    # being asked not to (e.g. "Wait, I made a mistake... let me redo this")
    # followed by a second, corrected block. The final block is the answer.
    text = (
        '```json\n[{"a": 1}]\n```\n\n'
        "Wait, that's wrong, let me reconsider.\n\n"
        '```json\n[{"a": 2}]\n```'
    )
    assert extract_json(text) == '[{"a": 2}]'
