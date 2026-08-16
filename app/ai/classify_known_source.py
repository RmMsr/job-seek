from __future__ import annotations
from urllib.parse import urlsplit
from app.fetchers.slack import SLACK_URL_RE

DETECTABLE_FETCHER_TYPES = {"slack", "finn_listing", "generic_listing"}


def classify_known_source(url: str) -> str | None:
    if SLACK_URL_RE.match(url):
        return "slack"
    host = urlsplit(url).netloc.lower()
    if host == "finn.no" or host.endswith(".finn.no"):
        return "finn_listing"
    return None
