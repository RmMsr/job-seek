from __future__ import annotations
import httpx
from bs4 import BeautifulSoup

MIN_CONTENT_LENGTH = 200  # below this, treat as no real content (e.g. a JS-only page's noscript shell)


class FetchError(Exception):
    pass


class NoContentError(FetchError):
    pass


def extract_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    return soup.get_text(separator="\n")


def has_enough_content(html: str) -> bool:
    return has_enough_text(extract_text(html))


def has_enough_text(text: str) -> bool:
    return len(text.strip()) >= MIN_CONTENT_LENGTH


def fetch_url_html(url: str) -> str:
    try:
        resp = httpx.get(url, timeout=30, follow_redirects=True)
    except httpx.HTTPError as exc:
        raise FetchError(str(exc)) from exc
    if resp.status_code != 200:
        raise FetchError(f"HTTP {resp.status_code}")
    return resp.text


def extract_text_or_raise(html: str) -> str:
    if not has_enough_content(html):
        raise NoContentError("page had little to no extractable text")
    return extract_text(html)


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
