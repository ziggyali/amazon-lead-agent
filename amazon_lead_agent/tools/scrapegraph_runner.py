from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse
import json
import logging
import re

import requests

from amazon_lead_agent.prompts import load_prompt
from amazon_lead_agent.tools.amazon_backlink_discovery import extract_amazon_links, contains_amazon_buying_signal, summarize_amazon_evidence


LOGGER = logging.getLogger(__name__)


class _EmailAndLinkParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.text_parts: list[str] = []
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {key: value for key, value in attrs}
        if tag == "a":
            href = attr_map.get("href") or ""
            if href:
                self.links.append(urljoin(self.base_url, href))

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text:
            self.text_parts.append(text)

    @property
    def text(self) -> str:
        return " ".join(self.text_parts)


def _heuristic_extract(url: str) -> dict:
    response = requests.get(url, timeout=25, headers={"User-Agent": "Mozilla/5.0 AmazonLeadAgent/0.1"})
    response.raise_for_status()
    parser = _EmailAndLinkParser(url)
    parser.feed(response.text)
    html = response.text
    text = parser.text
    emails = sorted(set(re.findall(r"[\w.+-]+@[\w.-]+\.[a-z]{2,}", html, flags=re.IGNORECASE)))
    amazon_links = extract_amazon_links(html, url)
    contact_links = [link for link in parser.links if any(token in link.lower() for token in ("contact", "about", "team"))]
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
    title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else ""
    company_name = title.split("|")[0].split("-")[0].strip() or urlparse(url).netloc
    summary = summarize_amazon_evidence(amazon_links, text)
    pain_points = []
    for token in ("inventory", "ads", "marketplace", "operations", "amazon", "fulfillment"):
        if token in text.lower():
            pain_points.append(token)
    return {
        "company_name": company_name,
        "brand_name": company_name,
        "website": url,
        "category": "",
        "country": "",
        "description": text[:500],
        "amazon_links": amazon_links,
        "amazon_evidence_summary": summary,
        "amazon_backlink_found": bool(amazon_links or contains_amazon_buying_signal(text)),
        "founder_or_executive_names": [],
        "ecommerce_or_marketplace_people": [],
        "public_emails": emails,
        "contact_page_url": contact_links[0] if contact_links else "",
        "decision_maker_source_url": url,
        "pain_points": pain_points,
        "confidence": 0.55 if amazon_links else 0.35,
        "source_quotes": [title] if title else [],
        "source_urls": [url],
    }


def extract_brand_profile(url: str, minimax_api_key: str) -> dict:
    prompt = load_prompt("extract_brand.md")
    try:
        from scrapegraphai.graphs import SmartScraperGraph
    except Exception:  # noqa: BLE001
        LOGGER.info("scrapegraphai unavailable, falling back to heuristic extraction")
        return _heuristic_extract(url)

    try:
        graph = SmartScraperGraph(
            prompt=prompt,
            source=url,
            config={
                "llm": {
                    "api_key": minimax_api_key,
                    "model": "MiniMax-Text-01",
                    "api_base": "https://api.minimax.chat/v1",
                },
                "verbose": False,
                "headless": True,
            },
        )
        result = graph.run()
        if isinstance(result, str):
            parsed = json.loads(result)
        else:
            parsed = result
        parsed.setdefault("website", url)
        parsed.setdefault("source_urls", [url])
        return parsed
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("scrapegraphai extraction failed, using heuristic fallback: %s", exc)
        return _heuristic_extract(url)

