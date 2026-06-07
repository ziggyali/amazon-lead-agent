from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
import logging
import re
from typing import Iterable

import requests


LOGGER = logging.getLogger(__name__)
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AmazonLeadAgent/0.1"

BAD_EMAIL_SUFFIXES = (".jpg", ".png", ".svg", ".webp", ".gif", ".js", ".css")
BAD_EMAIL_TOKENS = ("img_", "u003e", "layout.", "theme.js", "不确定")
BAD_EMAIL_PATTERNS = {
    "jane smith",
}
BAD_NAME_PATTERNS = {
    "jane smith",
}


class LinkTextParser(HTMLParser):
    def __init__(self, base_url: str | None = None) -> None:
        super().__init__()
        self.base_url = base_url
        self.links: list[str] = []
        self.text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        attr_map = {key.lower(): value for key, value in attrs}
        href = attr_map.get("href") or ""
        if href:
            if self.base_url:
                href = urljoin(self.base_url, href)
            self.links.append(href)

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text:
            self.text_parts.append(text)

    @property
    def text(self) -> str:
        return " ".join(self.text_parts)


def fetch_html(url: str, timeout: int = 25, use_playwright: bool = True) -> tuple[str, str]:
    headers = {"User-Agent": USER_AGENT}
    if use_playwright:
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(user_agent=USER_AGENT)
                page.goto(url, wait_until="networkidle", timeout=timeout * 1000)
                content = page.content()
                current_url = page.url
                browser.close()
                return content, current_url
        except Exception as exc:  # noqa: BLE001
            LOGGER.info("playwright fetch failed for %s: %s", url, exc)
    try:
        response = requests.get(url, headers=headers, timeout=timeout)
        response.raise_for_status()
        return response.text, response.url
    except Exception as exc:  # noqa: BLE001
        LOGGER.info("requests fetch failed for %s: %s", url, exc)
        req = Request(url, headers=headers)
        with urlopen(req, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            return body, response.geturl()


def extract_text_and_links(html: str, base_url: str | None = None) -> tuple[str, list[str]]:
    parser = LinkTextParser(base_url=base_url)
    parser.feed(html or "")
    return parser.text, parser.links


def extract_public_emails(html: str, text: str | None = None) -> list[str]:
    candidates = set()
    haystack = f"{html or ''}\n{text or ''}"
    for match in re.findall(r"[\w.+-]+@[\w.-]+\.[a-z]{2,}", haystack, flags=re.IGNORECASE):
        normalized = match.strip().strip(".,;:()[]<>\"'")
        lowered = normalized.lower()
        if any(token in lowered for token in BAD_EMAIL_TOKENS):
            continue
        if any(lowered.endswith(suffix) for suffix in BAD_EMAIL_SUFFIXES):
            continue
        if any(pattern in lowered for pattern in BAD_EMAIL_PATTERNS):
            continue
        candidates.add(normalized)
    return sorted(candidates)


def filter_public_names(names: Iterable[str]) -> list[str]:
    if isinstance(names, str):
        names = re.split(r"[\n,;]+", names)
    cleaned: list[str] = []
    for name in names:
        normalized = re.sub(r"\s+", " ", (name or "").strip())
        if not normalized:
            continue
        lowered = normalized.lower()
        if any(pattern in lowered for pattern in BAD_NAME_PATTERNS):
            continue
        if lowered in {"unknown", "n/a", "na", "not sure", "not certain"}:
            continue
        cleaned.append(normalized)
    return sorted(dict.fromkeys(cleaned))


def discover_contact_like_urls(links: Iterable[str]) -> list[str]:
    matches: list[str] = []
    for link in links:
        lowered = link.lower()
        if any(token in lowered for token in ("contact", "about", "team", "support", "help")):
            matches.append(link)
    return sorted(set(matches))


def get_visible_title(html: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group(1)).strip()


def looks_blocked(html: str) -> bool:
    lowered = (html or "").lower()
    return any(
        phrase in lowered
        for phrase in (
            "access denied",
            "captcha",
            "robot check",
            "verify you are human",
            "blocked",
            "unusual traffic",
        )
    )
