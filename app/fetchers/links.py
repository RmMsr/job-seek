from __future__ import annotations
from urllib.parse import urljoin
from bs4 import BeautifulSoup

_MAX_LINKS = 200


def extract_links(html: str, base_url: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    links: list[tuple[str, str]] = []
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a["href"])
        if href in seen:
            continue
        seen.add(href)
        links.append((href, a.get_text(strip=True)))
        if len(links) >= _MAX_LINKS:
            break
    return links
