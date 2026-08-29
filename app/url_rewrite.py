from __future__ import annotations
import re
from dataclasses import dataclass
from urllib.parse import urlsplit, parse_qs, urlencode

_GUEST_SEARCH = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"


@dataclass(frozen=True)
class RewriteSuggestion:
    url: str
    reason: str


def _linkedin_search_terms(parts) -> tuple[str, str] | None:
    """(keywords, location) for any LinkedIn job-search URL — the logged-in
    search page or the rewritten guest endpoint — else None."""
    host = parts.netloc.lower()
    if host != "linkedin.com" and not host.endswith(".linkedin.com"):
        return None
    if "/jobs" not in parts.path:
        return None
    params = parse_qs(parts.query, keep_blank_values=False)
    keywords = (params.get("keywords") or [""])[0].strip()
    if not keywords:
        return None
    return keywords, (params.get("location") or [""])[0].strip()


def _linkedin_job_search(parts) -> RewriteSuggestion | None:
    if parts.path.startswith("/jobs-guest/"):
        return None
    terms = _linkedin_search_terms(parts)
    if terms is None:
        return None
    keywords, location = terms
    query = [("keywords", keywords)]
    if location:
        query.append(("location", location))
    query.append(("start", "0"))
    return RewriteSuggestion(
        url=f"{_GUEST_SEARCH}?{urlencode(query)}",
        reason=(
            "This URL looks like a LinkedIn job search, which needs a login to fetch. "
            "This public search URL can be fetched instead."
        ),
    )


_RULES = [_linkedin_job_search]


def suggest_rewrite(url: str) -> RewriteSuggestion | None:
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    for rule in _RULES:
        hit = rule(parts)
        if hit is not None:
            return hit
    return None


def suggest_source_name(url: str) -> str | None:
    """A human-friendly default source name for URLs whose host tells us more
    than the netloc would — e.g. a LinkedIn search URL carries its query terms.
    Returns None when there's nothing better to offer than the host."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https"):
        return None
    terms = _linkedin_search_terms(parts)
    if terms is not None:
        keywords, location = (re.sub(r"\s+", "-", t) for t in terms)
        return "linkedin/" + keywords + (f"/{location}" if location else "")
    return None
