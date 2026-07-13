from __future__ import annotations
import re
from bs4 import BeautifulSoup


def simplify(raw_text: str) -> str:
    if not raw_text.strip():
        return ""
    soup = BeautifulSoup(raw_text, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
