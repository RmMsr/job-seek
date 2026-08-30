from __future__ import annotations
from urllib.parse import urlsplit
from app.fetchers.slack import SLACK_URL_RE

DETECTABLE_FETCHER_TYPES = {"slack", "finn_listing", "generic_listing", "eawork_listing"}

_EAWORK_HOSTS = {"jobs.80000hours.org", "eawork.org", "www.eawork.org"}


def classify_known_source(url: str) -> str | None:
    if SLACK_URL_RE.match(url):
        return "slack"
    host = urlsplit(url).netloc.lower()
    if host == "finn.no" or host.endswith(".finn.no"):
        return "finn_listing"
    if host in _EAWORK_HOSTS:
        return "eawork_listing"
    return None
