from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import quote_plus, urlparse
import logging
import time

import requests

from amazon_lead_agent.normalization import normalize_company_name, normalize_domain


LOGGER = logging.getLogger(__name__)
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AmazonLeadAgent/0.1"


class _DuckDuckGoParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None
        self._in_title = False
        self._in_snippet = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {key: value for key, value in attrs}
        if tag == "a" and "result__a" in (attr_map.get("class") or ""):
            href = attr_map.get("href") or ""
            self._current = {"url": href, "title": "", "snippet": ""}
            self._in_title = True
        elif tag in {"a", "div", "span"} and self._current is not None:
            cls = attr_map.get("class") or ""
            if "result__snippet" in cls:
                self._in_snippet = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_title:
            self._in_title = False
        if tag in {"div", "span"} and self._in_snippet:
            self._in_snippet = False
        if tag == "a" and self._current is not None and self._current.get("url"):
            if self._current.get("title") or self._current.get("snippet"):
                self.results.append(self._current)
            self._current = None

    def handle_data(self, data: str) -> None:
        if self._current is None:
            return
        if self._in_title:
            self._current["title"] += data.strip()
        elif self._in_snippet:
            text = data.strip()
            if text:
                self._current["snippet"] += (text + " ")


def generate_queries(categories: list[str]) -> list[str]:
    queries: list[str] = []
    for category in categories:
        category = category.strip().lower()
        if not category:
            continue
        queries.extend(
            [
                f'"{category}" "available on Amazon" brand',
                f'"{category}" "Amazon storefront" DTC',
                f'"{category}" "shop our Amazon store"',
                f'"{category}" "buy on Amazon" brand',
            ]
        )
    return queries


def search_web(query: str, limit: int = 10) -> list[dict]:
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    headers = {"User-Agent": USER_AGENT}
    try:
        response = requests.get(url, headers=headers, timeout=20)
        if response.status_code != 200:
            LOGGER.warning("search blocked for query=%s status=%s", query, response.status_code)
            return []
        parser = _DuckDuckGoParser()
        parser.feed(response.text)
        results: list[dict] = []
        for item in parser.results[:limit]:
            parsed = urlparse(item["url"])
            results.append(
                {
                    "title": item.get("title", "").strip(),
                    "url": item.get("url", "").strip(),
                    "snippet": item.get("snippet", "").strip(),
                    "domain": normalize_domain(parsed.netloc or parsed.path),
                    "source": "duckduckgo",
                    "query": query,
                }
            )
        time.sleep(0.25)
        return results
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("search failed for query=%s error=%s", query, exc)
        return []


def discover_candidates(categories: list[str], limit: int = 50) -> list[dict]:
    queries = generate_queries(categories)
    candidates: list[dict] = []
    seen_domains: set[str] = set()
    for query in queries:
        for result in search_web(query, limit=limit):
            domain = normalize_domain(result.get("url"))
            if not domain or domain in seen_domains:
                continue
            seen_domains.add(domain)
            result["category"] = next((category for category in categories if category.lower() in query.lower()), "")
            result["company_name"] = normalize_company_name(result.get("title") or domain)
            candidates.append(result)
            if len(candidates) >= limit:
                return candidates
    return candidates

