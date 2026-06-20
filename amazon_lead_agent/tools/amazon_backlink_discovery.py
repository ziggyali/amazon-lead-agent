from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from amazon_lead_agent.normalization import normalize_domain


AMAZON_SIGNALS = (
    "amazon.com",
    "/stores/",
    "available on amazon",
    "shop our amazon store",
    "buy on amazon",
    "amazon storefront",
    "amazon store",
)

AMAZON_ROOT_DOMAINS = {
    "amazon.com",
    "amazon.ca",
    "amazon.co.uk",
    "amazon.de",
    "amazon.fr",
    "amazon.it",
    "amazon.es",
    "amazon.in",
    "amazon.co.jp",
    "amazon.com.au",
    "amazon.nl",
    "amazon.sg",
    "amazon.ae",
    "amazon.sa",
    "amazon.se",
    "amazon.pl",
    "amazon.com.mx",
    "amazon.com.br",
    "amazon.com.tr",
    "amazon.com.be",
    "amazon.com.eg",
    "smile.amazon.com",
    "amzn.to",
}


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
        domain = normalize_domain(absolute)
        if domain in AMAZON_ROOT_DOMAINS or any(domain.endswith(f".{root}") for root in AMAZON_ROOT_DOMAINS if root.startswith("amazon.")):
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


def has_verified_amazon_evidence(lead: dict) -> bool:
    evidence_items = lead.get("amazon_evidence_items") or []
    if isinstance(evidence_items, list):
        for item in evidence_items:
            if isinstance(item, dict):
                evidence_type = str(item.get("evidence_type") or "").strip().lower()
                if evidence_type in {"official_site_amazon_backlink", "amazon_storefront_search_result", "amazon_product_search_result", "amazon_brand_page_search_result", "manual_verified_amazon_url"} and is_valid_amazon_url(item.get("evidence_url")):
                    return True
    evidence_urls: list[str] = []
    for field in ("amazon_links", "amazon_links_json", "amazon_evidence_url", "amazon_evidence_urls"):
        value = lead.get(field)
        if not value:
            continue
        if isinstance(value, str):
            evidence_urls.append(value)
        elif isinstance(value, list):
            evidence_urls.extend(str(item) for item in value if str(item).strip())
        else:
            evidence_urls.append(str(value))
    if any(is_valid_amazon_url(url) for url in evidence_urls):
        return bool(_truthy(lead.get("amazon_backlink_found")) or evidence_urls)
    return False


def is_valid_amazon_url(url: str | None) -> bool:
    if not url:
        return False
    domain = normalize_domain(url)
    if not domain:
        return False
    if domain in AMAZON_ROOT_DOMAINS or any(domain.endswith(f".{root}") for root in AMAZON_ROOT_DOMAINS if root.startswith("amazon.")):
        return True
    lowered = url.lower()
    return bool(re.search(r"amazon\.(?:com|ca|co\.uk|de|fr|it|es|in|co\.jp|com\.au|nl|sg|ae|sa|se|pl|com\.mx|com\.br|com\.tr|com\.be|com\.eg)", lowered))


def _truthy(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)
