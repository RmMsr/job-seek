from __future__ import annotations
import httpx
from bs4 import BeautifulSoup
from app.url_rewrite import linkedin_guest_posting_url

MIN_CONTENT_LENGTH = 200  # below this, treat as no real content (e.g. a JS-only page's noscript shell)
MIN_ARTICLE_LENGTH = 1200  # a real job posting's extracted text clears this; a JS nav-shell
                           # ("<site> needs JavaScript" + menu) usually does not


class FetchError(Exception):
    pass


class NoContentError(FetchError):
    pass


def extract_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    return soup.get_text(separator="\n")


_BOILERPLATE_SELECTORS = ",".join((
    "script", "style", "nav", "header", "footer",
    "[id*=cookie i]", "[class*=cookie i]",
    "[id*=consent i]", "[class*=consent i]",
    "#CybotCookiebotDialog", "#onetrust-consent-sdk", "#didomi-host", ".osano-cm-window",
))


def extract_readable_text(html: str) -> str:
    """``extract_text`` with site chrome removed — scripts/styles, nav/header/footer,
    and cookie-consent / CMP banners (CookieInformation, Cookiebot, OneTrust,
    Didomi, Osano, and homegrown ``*cookie*`` / ``*consent*`` containers). Used for
    the text that becomes a job's ``raw_text``: a rendered page often leads with a
    multi-thousand-character consent notice that would otherwise bury the posting
    past the classifier's input window. ``extract_text`` itself stays pure."""
    soup = BeautifulSoup(html, "html.parser")
    for el in soup.select(_BOILERPLATE_SELECTORS):
        el.decompose()
    return soup.get_text(separator="\n")


def has_enough_content(html: str) -> bool:
    return has_enough_text(extract_text(html))


def has_enough_text(text: str) -> bool:
    return len(text.strip()) >= MIN_CONTENT_LENGTH


def text_length(html: str) -> int:
    return len(extract_text(html).strip())


def is_substantially_richer(candidate_html: str, baseline_html: str) -> bool:
    """True if ``candidate_html`` yields at least ``MIN_CONTENT_LENGTH`` more
    characters of extractable text than ``baseline_html`` — used to decide
    whether a headless render is worth keeping over the raw HTML for a page that
    is not a job listing (a JS-rendered single posting whose raw HTML is a
    nav-only shell)."""
    return text_length(candidate_html) >= text_length(baseline_html) + MIN_CONTENT_LENGTH


def fetch_url_html(url: str) -> str:
    fetch_url = linkedin_guest_posting_url(url) or url
    try:
        resp = httpx.get(fetch_url, timeout=30, follow_redirects=True)
    except httpx.HTTPError as exc:
        raise FetchError(str(exc)) from exc
    if resp.status_code != 200:
        raise FetchError(f"HTTP {resp.status_code}")
    return resp.text


def extract_text_or_raise(html: str) -> str:
    if not has_enough_content(html):
        raise NoContentError("page had little to no extractable text")
    return extract_readable_text(html)


def extract_page_title(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
        if title:
            return title
    h1 = soup.find("h1")
    if h1:
        text = h1.get_text(strip=True)
        if text:
            return text
    return None
