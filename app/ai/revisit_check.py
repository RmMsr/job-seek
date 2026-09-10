from __future__ import annotations
import json
import openai
from app.ai._client import complete
from app.ai.json_utils import extract_json

_STATES = ("gone", "closed", "unchanged", "changed")

_SYSTEM = (
    "You are given (A) a summary of a job opening recorded earlier and (B) the "
    "current text of the web page at that job's URL. Decide the page's state "
    "relative to the opening in (A). Respond with exactly: "
    '{"state": "<label>", "reason": "<one short sentence>"}. Labels: '
    '"gone" — the page no longer shows this job (removed, redirected to a '
    "listing or home page, an error or login wall, or unrelated content); "
    '"closed" — still this job, but no longer open to applicants (position '
    "filled, applications closed, deadline passed); "
    '"unchanged" — the same opening, still open, with no material change to the '
    "role, responsibilities, requirements, location, or compensation; "
    '"changed" — the same opening, still open, but the role, requirements, '
    "location, compensation, or other substantive details have materially "
    "changed. Ignore differences in unrelated page content — other job "
    "listings, view or applicant counts, ads, navigation, cookie notices, or "
    "how long ago it says the job was posted."
)


def revisit_check(
    client: openai.OpenAI, model: str, page_text: str, known_summary: str
) -> tuple[str, str]:
    """Compare a live page against the summary we stored for that job.

    Returns ``(state, reason)`` where state is one of gone/closed/unchanged/
    changed. An unparseable response returns ``("unchanged", <detail>)`` — an
    unreliable judgement must never be the thing that trashes a job; a transport
    / API failure raises and fails the task.
    """
    content = complete(
        client,
        model,
        [
            {"role": "system", "content": _SYSTEM},
            {
                "role": "user",
                "content": (
                    f"(A) Recorded summary:\n{known_summary[:2000]}\n\n"
                    f"(B) Current page text:\n{page_text[:4000]}"
                ),
            },
        ],
        temperature=0,
        max_tokens=120,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    try:
        data = json.loads(extract_json(content))
        state = data.get("state", "unchanged")
        if state not in _STATES:
            state = "unchanged"
        return state, data.get("reason", "")
    except Exception as exc:  # noqa: BLE001 — an unparseable response is a non-verdict
        return "unchanged", str(exc)
