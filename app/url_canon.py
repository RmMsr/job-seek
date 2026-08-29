from __future__ import annotations
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

_STRIP_EXACT = frozenset({
    "gclid", "fbclid", "msclkid", "mc_cid", "mc_eid", "_hsenc", "_hsmi",
    "trk", "trackingid", "refid", "position", "pagenum",
    "origintolandingjobpostings", "lipi", "licu", "recommendedflavor",
})


def _keep(name: str) -> bool:
    lower = name.lower()
    return not lower.startswith("utm_") and lower not in _STRIP_EXACT


def canonicalize_url(url: str) -> str:
    """Return a stable form of a job URL for dedup and storage: lowercased host,
    tracking query params removed, other params kept in order. The fragment is
    preserved — Slack permalinks (`.../archives/C123#<ts>`) and SPA hash routes
    carry identity there, and tracking junk never lives in the fragment.
    Non-http(s) or netloc-less input is returned unchanged. Idempotent."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return url
    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if _keep(k)]
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path, urlencode(kept), parts.fragment))
