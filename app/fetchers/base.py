from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol


@dataclass
class RawJob:
    url: str
    title: str
    company: str
    raw_text: str


class Fetcher(Protocol):
    def fetch(self) -> list[RawJob]: ...
