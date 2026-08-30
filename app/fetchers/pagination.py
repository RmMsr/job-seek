from __future__ import annotations
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_OFFSET_PARAMS = ("start", "offset", "from")  # next value = current + page_size
_INDEX_PARAMS = ("page",)                     # next value = current + 1


def next_page_url(url: str, page_size: int) -> str | None:
    """Return ``url`` with its one recognized pagination param advanced to the
    next page, or ``None`` when there is nothing to page: no known param,
    ``page_size == 0``, or a non-int param value. Every other query param and
    the fragment is preserved."""
    if page_size == 0:
        return None
    parts = urlsplit(url)
    params = parse_qsl(parts.query, keep_blank_values=True)
    names = [k for k, _ in params]
    for name in _OFFSET_PARAMS + _INDEX_PARAMS:
        if name not in names:
            continue
        idx = names.index(name)
        try:
            current = int(params[idx][1])
        except ValueError:
            return None
        step = 1 if name in _INDEX_PARAMS else page_size
        params[idx] = (name, str(current + step))
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(params), parts.fragment))
    return None
