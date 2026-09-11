from app.routes.cv import _guardrail_status

_SETTINGS = {"base_cv": "# Me", "base_instruction": "", "base_guardrails": "no lies"}
_FINDINGS = [{"rule": "no lies", "verdict": "ok", "explanation": ""}]


def _base_hash_of(settings):
    from app.routes.cv import _base_hash
    return _base_hash(settings)


def test_guardrail_status_stale_when_edited_after_check():
    jc = {
        "tailored_cv": "# Draft", "guardrail_findings": _FINDINGS,
        "base_hash": _base_hash_of(_SETTINGS),
        "generated_at": "2026-09-10 10:00:00",
        "guardrails_checked_at": "2026-09-10 10:00:00",
        "edited_at": "2026-09-10 11:00:00",
    }
    assert _guardrail_status(jc, _SETTINGS, running=False) == "stale"


def test_guardrail_status_fresh_when_check_after_edit():
    jc = {
        "tailored_cv": "# Draft", "guardrail_findings": _FINDINGS,
        "base_hash": _base_hash_of(_SETTINGS),
        "generated_at": "2026-09-10 10:00:00",
        "edited_at": "2026-09-10 11:00:00",
        "guardrails_checked_at": "2026-09-10 11:30:00",
    }
    assert _guardrail_status(jc, _SETTINGS, running=False) == "fresh"


def test_guardrail_status_fresh_when_never_edited():
    jc = {
        "tailored_cv": "# Draft", "guardrail_findings": _FINDINGS,
        "base_hash": _base_hash_of(_SETTINGS),
        "generated_at": "2026-09-10 10:00:00",
        "guardrails_checked_at": "2026-09-10 10:00:00",
        "edited_at": None,
    }
    assert _guardrail_status(jc, _SETTINGS, running=False) == "fresh"
