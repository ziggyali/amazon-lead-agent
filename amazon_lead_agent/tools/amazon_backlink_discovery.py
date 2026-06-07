from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import urljoin
import re


AMAZON_SIGNALS = (
    "amazon.com",
    "/stores/",
    "available on amazon",
    "shop our amazon store",
    "buy on amazon",
    "amazon storefront",
    "amazon store",
)


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        for key, value in attrs:
            if key.lower() == "href" and value:
                self.links.append(value)


def extract_amazon_links(html: str, base_url: str) -> list[str]:
    parser = _LinkParser()
    parser.feed(html or "")
    links: list[str] = []
    for href in parser.links:
        absolute = urljoin(base_url, href)
        lowered = absolute.lower()
        if "amazon." in lowered or "/stores/" in lowered:
            links.append(absolute)
    return sorted(set(links))


def contains_amazon_buying_signal(text: str) -> bool:
    haystack = (text or "").lower()
    return any(signal in haystack for signal in AMAZON_SIGNALS)


def summarize_amazon_evidence(links: list[str], text: str) -> str:
    snippets: list[str] = []
    if links:
        snippets.append(f"Amazon links: {', '.join(sorted(set(links))[:3])}")
    text_lower = (text or "").lower()
    matched = [signal for signal in AMAZON_SIGNALS if signal in text_lower]
    if matched:
        snippets.append(f"Signals: {', '.join(matched[:4])}")
    return " | ".join(snippets)
