from __future__ import annotations
import re
from bs4 import BeautifulSoup, Comment

_DROP_TAGS = {"script", "style", "link", "iframe", "object", "embed", "meta", "base"}
# Resources WeasyPrint fetches (images, src attributes).
_SAFE_URL_RE = re.compile(r"^(#|/(?!/)|mailto:|data:image/(png|jpeg|jpg|gif|webp)(?=[;,]|$))", re.IGNORECASE)
# Hyperlink targets are never fetched by the renderer, only written into the
# PDF annotation, so http(s) is safe. file:, javascript:, protocol-relative
# //host etc. stay blocked.
_SAFE_LINK_RE = re.compile(r"^(#|/(?!/)|mailto:|https?://)", re.IGNORECASE)
_FRONTMATTER_RE = re.compile(r"\A(---\n.*?\n---\n)", re.DOTALL)

# Markdown-native links/images that BeautifulSoup never sees. DEST is either an
# angle-bracketed <url> or a bare token, optionally followed by a "title".
_MD_IMAGE_RE = re.compile(r'!\[([^\]]*)\]\(\s*(<[^>\s]*>|[^)\s]+)(\s+"[^"]*")?\s*\)')
_MD_LINK_RE = re.compile(r'(?<!!)\[([^\]]*)\]\(\s*(<[^>\s]*>|[^)\s]+)(\s+"[^"]*")?\s*\)')
_BARE_AUTOLINK_RE = re.compile(r'<((?:https?|ftp|file)://[^>\s]+)>', re.IGNORECASE)
# Markdown reference-style link/image definitions: [ref]: url or [ref]: <url> with optional title
_MD_REF_DEF_RE = re.compile(r'^([ ]{0,3}\[[^\]]+\]:\s*)(\S+)(.*)$', re.MULTILINE)
# A destination worth neutralising only if it actually looks like a fetchable
# URL — a protocol-relative //host or an explicit scheme. This keeps prose lines
# such as "[Note]: I prefer remote work" untouched (a bare word is not a URL a
# renderer would fetch).
_FETCHABLE_URL_RE = re.compile(r'^<?(//|[a-z][a-z0-9+.\-]*:)', re.IGNORECASE)
# Reference-style image usages: ![alt][label], collapsed ![label][] and
# shortcut ![label]. Their labels' definitions are fetched, so stay strict.
_MD_REF_IMAGE_RE = re.compile(r'!\[([^\]]*)\](?:\[([^\]]*)\]|(?![(\[]))')


def _ref_label(label: str) -> str:
    # Markdown matches reference labels case-insensitively, whitespace-collapsed.
    return " ".join(label.split()).casefold()


def _neutralise_markdown_urls(body: str) -> str:
    """Rewrite markdown image/link destinations and bare autolinks that aren't
    on the allow-list to a harmless '#', so WeasyPrint never fetches them.
    Images must match _SAFE_URL_RE; hyperlinks (never fetched) may also keep
    http(s) targets (_SAFE_LINK_RE)."""
    def _rewrite(prefix: str, safe_re: re.Pattern):
        def _sub(m: re.Match) -> str:
            dest = m.group(2).strip().strip("<>")
            if safe_re.match(dest):
                return m.group(0)
            return f"{prefix}[{m.group(1)}](#)"
        return _sub

    def _rewrite_autolink(m: re.Match) -> str:
        url = m.group(1)
        if _SAFE_LINK_RE.match(url):
            # Re-express as an inline link: BeautifulSoup would swallow the
            # bare <https://...> as a bogus tag.
            return f"[{url}]({url})"
        return "#"

    body = _MD_IMAGE_RE.sub(_rewrite("!", _SAFE_URL_RE), body)
    body = _MD_LINK_RE.sub(_rewrite("", _SAFE_LINK_RE), body)
    body = _BARE_AUTOLINK_RE.sub(_rewrite_autolink, body)

    image_labels = {
        _ref_label(m.group(2) or m.group(1)) for m in _MD_REF_IMAGE_RE.finditer(body)
    }

    # Handle reference-style link/image definitions: [ref]: url ...
    # Only act when the destination both looks like a fetchable URL and is not
    # on the safe allow-list — so ordinary prose ("[Note]: I prefer ...") and
    # relative paths are left alone. A label used by any image gets the strict
    # image allow-list; one used only by links may keep http(s).
    def _rewrite_ref_def(m: re.Match) -> str:
        prefix = m.group(1)  # "[ref]: "
        dest = m.group(2).strip().strip("<>")  # the URL, possibly in angle brackets
        suffix = m.group(3)  # optional title and whitespace
        label = _ref_label(prefix.strip()[1:-2])
        safe_re = _SAFE_URL_RE if label in image_labels else _SAFE_LINK_RE
        if _FETCHABLE_URL_RE.match(m.group(2).strip()) and not safe_re.match(dest):
            return f"{prefix}#{suffix}"
        return m.group(0)

    body = _MD_REF_DEF_RE.sub(_rewrite_ref_def, body)
    return body


_BLOCKQUOTE_MARK_RE = re.compile(r"(?m)^([ \t]{0,3})((?:&gt;[ \t]?)+)")


def _restore_blockquote_markers(body: str) -> str:
    """BeautifulSoup round-tripping escapes a line-leading ``>`` to ``&gt;``,
    which stops the markdown renderer from seeing the blockquote. Put it back —
    a ``>`` at the start of a line is markdown syntax, never a character that
    needs escaping. Inline ``>`` elsewhere stays escaped (correct, and safe)."""
    return _BLOCKQUOTE_MARK_RE.sub(
        lambda m: m.group(1) + m.group(2).replace("&gt;", ">"), body
    )


def _split_frontmatter(md: str) -> tuple[str, str]:
    m = _FRONTMATTER_RE.match(md)
    if m:
        return m.group(1), md[m.end():]
    return "", md


def sanitize_cv_markdown(md: str) -> str:
    """Sanitise markdown for safe rendering via doc-write (WeasyPrint PDF/PNG output).

    Removes script/style tags, event handlers, inline styles, and neutralises remote/file URLs,
    protocol-relative URLs, and SVG/non-raster data URIs in fetched resources. Hyperlinks
    (markdown links, <a href>) keep http(s) targets, which are never fetched. Output is safe for render-time
    resource fetches but is NOT HTML-escaped for browser display (use separate HTML escaping).
    """
    front, body = _split_frontmatter(md)
    # Neutralise markdown-native links/images on the raw body first: BeautifulSoup
    # only sees HTML tags, and a bare <scheme://...> autolink can be swallowed as
    # a bogus tag before the regex ever runs.
    body = _neutralise_markdown_urls(body)
    soup = BeautifulSoup(body, "html.parser")

    for c in soup.find_all(string=lambda s: isinstance(s, Comment)):
        c.extract()
    for tag in soup.find_all(list(_DROP_TAGS)):
        tag.decompose()
    for tag in soup.find_all(True):
        for attr in list(tag.attrs):
            low = attr.lower()
            if low.startswith("on") or low == "style":
                del tag.attrs[attr]
            elif low in ("href", "src"):
                val = str(tag.attrs[attr]).strip()
                safe_re = _SAFE_LINK_RE if tag.name == "a" and low == "href" else _SAFE_URL_RE
                if not safe_re.match(val):
                    tag.attrs[attr] = "#"
    return front + _restore_blockquote_markers(str(soup))


_CSS_IMPORT_RE = re.compile(r"@import\b", re.IGNORECASE)
_CSS_URL_RE = re.compile(r"url\(\s*['\"]?([^'\")]+)['\"]?\s*\)", re.IGNORECASE)
_CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def validate_css(css: str) -> str | None:
    """Validate CSS for safe rendering via doc-write (WeasyPrint PDF/PNG output).

    Rejects @import rules and url() references to non-data URIs. Returns error string if
    invalid, None if safe. Intended for render-time safety; not a general-purpose CSS sanitiser.
    """
    css = _CSS_COMMENT_RE.sub("", css)
    if _CSS_IMPORT_RE.search(css):
        return "CSS may not use @import."
    for m in _CSS_URL_RE.finditer(css):
        target = m.group(1).strip().lower()
        if not target.startswith("data:"):
            return "CSS url(...) may only reference data: URIs."
    return None
