from __future__ import annotations
from bs4 import BeautifulSoup

MIN_CONTENT_LENGTH = 200  # below this, treat as no real content (e.g. a JS-only page's noscript shell)


def extract_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    return soup.get_text(separator="\n")


def has_enough_content(html: str) -> bool:
    return has_enough_text(extract_text(html))


def has_enough_text(text: str) -> bool:
    return len(text.strip()) >= MIN_CONTENT_LENGTH
