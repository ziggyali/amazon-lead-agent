from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from html.parser import HTMLParser
import logging
import os
import re
import time
from urllib.parse import parse_qs, quote_plus, unquote, urlparse, urlunparse

import requests

from amazon_lead_agent.normalization import normalize_company_name, normalize_domain


LOGGER = logging.getLogger(__name__)
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AmazonLeadAgent/0.1"

CONTENT_DOMAIN_BLOCKLIST = {
    "popsugar.com",
    "glamour.com",
    "womenshealthmag.com",
    "headtopics.com",
    "buzzfeed.com",
    "instyle.com",
    "marieclaire.com",
    "people.com",
    "today.com",
    "goodhousekeeping.com",
}

CONTENT_TITLE_KEYWORDS = ("best", "top", "award winners", "list", "review")
AGENCY_DOMAIN_KEYWORDS = ("agency", "marketing", "consulting", "services", "service", "pr")
PREFERRED_PATH_HINTS = ("/pages/where-to-buy", "/retailers", "/amazon", "/store-locator", "/contact", "/about")

QUERY_TEMPLATES = [
    '"available on Amazon" "{category}" brand',
    '"shop our Amazon store" "{category}"',
    '"buy on Amazon" "{category}" "official site"',
    '"Amazon storefront" "{category}" brand',
    '"where to buy" "{category}" "Amazon"',
    '"retailers" "{category}" "Amazon"',
    'site:*.com "available on Amazon" "{category}"',
    'site:*.com "shop on Amazon" "{category}"',
]

_LAST_SEARCH_STATS: dict = {}


def _new_stats() -> dict:
    return {
        "search_provider_mode": os.environ.get("SEARCH_PROVIDER", "multi").strip().lower() or "multi",
        "queries_total": 0,
        "provider_counts": defaultdict(int),
        "blocked_query_counts": defaultdict(int),
        "rate_limited_query_counts": defaultdict(int),
        "rejected_content_domains_count": 0,
        "rejected_listicle_domains_count": 0,
    }


def reset_search_stats() -> None:
    global _LAST_SEARCH_STATS
    _LAST_SEARCH_STATS = _new_stats()


def get_last_search_stats() -> dict:
    if not _LAST_SEARCH_STATS:
        reset_search_stats()
    stats = deepcopy(_LAST_SEARCH_STATS)
    stats["provider_counts"] = dict(stats["provider_counts"])
    stats["blocked_query_counts"] = dict(stats["blocked_query_counts"])
    stats["rate_limited_query_counts"] = dict(stats["rate_limited_query_counts"])
    return stats


def _stat_inc(name: str, provider: str, amount: int = 1) -> None:
    if not _LAST_SEARCH_STATS:
        reset_search_stats()
    _LAST_SEARCH_STATS[name][provider] += amount


def _search_provider_order() -> list[str]:
    mode = os.environ.get("SEARCH_PROVIDER", "multi").strip().lower() or "multi"
    if mode == "duckduckgo":
        return ["duckduckgo"]
    if mode == "bing_html":
        return ["bing_html"]
    if mode == "playwright_search":
        return ["playwright_search"]
    return ["duckduckgo", "bing_html", "playwright_search"]


def clean_search_result_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    if "uddg" in query and query["uddg"]:
        return unquote(query["uddg"][0])
    if parsed.scheme in {"http", "https"}:
        return urlunparse(parsed._replace(fragment=""))
    return url


class _SearchParser(HTMLParser):
    def __init__(self, provider: str) -> None:
        super().__init__()
        self.provider = provider
        self.results: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None
        self._in_title = False
        self._in_snippet = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {key.lower(): (value or "") for key, value in attrs}
        if self.provider == "duckduckgo" and tag == "a" and "result__a" in attr_map.get("class", ""):
            self._current = {"url": attr_map.get("href", ""), "title": "", "snippet": ""}
            self._in_title = True
        elif self.provider == "bing_html" and tag == "h2":
            self._in_title = True
        elif tag == "a" and self._current is not None and self.provider == "bing_html":
            href = attr_map.get("href", "")
            if href and not self._current.get("url"):
                self._current["url"] = href
        elif self._current is not None:
            cls = attr_map.get("class", "")
            if "result__snippet" in cls or "b_caption" in cls:
                self._in_snippet = True

    def handle_endtag(self, tag: str) -> None:
        if self.provider == "duckduckgo" and tag == "a" and self._in_title:
            self._in_title = False
            if self._current is not None and self._current.get("url"):
                if self._current.get("title") or self._current.get("snippet"):
                    self.results.append(self._current)
                self._current = None
        elif self.provider == "bing_html" and tag == "h2":
            self._in_title = False
        elif tag in {"div", "span", "p"} and self._in_snippet:
            self._in_snippet = False

    def handle_data(self, data: str) -> None:
        if self._current is None:
            return
        text = data.strip()
        if not text:
            return
        if self._in_title:
            if self.provider == "bing_html":
                self._current.setdefault("title", "")
                self._current["title"] += text
            else:
                self._current["title"] += text
        elif self._in_snippet:
            self._current["snippet"] += text + " "


def _extract_results_from_html(html: str, provider: str) -> list[dict[str, str]]:
    if provider == "bing_html":
        results: list[dict[str, str]] = []
        for block in re.findall(r"<li[^>]*class=[\"'][^\"']*b_algo[^\"']*[\"'][^>]*>(.*?)</li>", html or "", flags=re.IGNORECASE | re.DOTALL):
            anchor = re.search(r"<h2[^>]*>\s*<a[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", block, flags=re.IGNORECASE | re.DOTALL)
            if not anchor:
                continue
            href = anchor.group(1).strip()
            title = re.sub(r"<[^>]+>", " ", anchor.group(2))
            title = re.sub(r"\s+", " ", title).strip()
            snippet_match = re.search(r"<div[^>]*class=[\"'][^\"']*b_caption[^\"']*[\"'][^>]*>.*?<p[^>]*>(.*?)</p>", block, flags=re.IGNORECASE | re.DOTALL)
            snippet = re.sub(r"<[^>]+>", " ", snippet_match.group(1)) if snippet_match else ""
            snippet = re.sub(r"\s+", " ", snippet).strip()
            if href:
                results.append({"url": href, "title": title, "snippet": snippet})
        return results
    parser = _SearchParser(provider)
    parser.feed(html or "")
    return parser.results


def _search_url(provider: str, query: str) -> str:
    encoded = quote_plus(query)
    if provider == "bing_html":
        return f"https://www.bing.com/search?q={encoded}"
    return f"https://html.duckduckgo.com/html/?q={encoded}"


def _blocked_response(status_code: int, body: str) -> bool:
    if status_code in {202, 429, 403}:
        return True
    lowered = (body or "").lower()
    return any(phrase in lowered for phrase in ("captcha", "verify you are human", "robot check", "blocked", "unusual traffic"))


def _search_with_requests(provider: str, query: str, limit: int = 10) -> tuple[list[dict], str]:
    url = _search_url(provider, query)
    headers = {"User-Agent": USER_AGENT}
    response = requests.get(url, headers=headers, timeout=20)
    if _blocked_response(response.status_code, response.text):
        _stat_inc("blocked_query_counts", provider)
        if response.status_code in {429, 202}:
            _stat_inc("rate_limited_query_counts", provider)
        LOGGER.info("provider_blocked provider=%s status=%s query=%s", provider, response.status_code, query)
        return [], "blocked"
    if response.status_code != 200:
        LOGGER.info("search provider returned status=%s provider=%s query=%s", response.status_code, provider, query)
        return [], "empty"
    results = _extract_results_from_html(response.text, provider)[:limit]
    return results, "ok"


def _search_with_playwright(provider: str, query: str, limit: int = 10) -> tuple[list[dict], str]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # noqa: BLE001
        LOGGER.info("playwright search unavailable: %s", exc)
        return [], "blocked"

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent=USER_AGENT)
            page.goto(_search_url(provider if provider != "playwright_search" else "duckduckgo", query), wait_until="networkidle", timeout=30000)
            html = page.content()
            browser.close()
    except Exception as exc:  # noqa: BLE001
        LOGGER.info("playwright search failed provider=%s query=%s error=%s", provider, query, exc)
        return [], "blocked"

    if _blocked_response(200, html):
        _stat_inc("blocked_query_counts", provider)
        LOGGER.info("provider_blocked provider=%s query=%s", provider, query)
        return [], "blocked"
    results = _extract_results_from_html(html, "bing_html" if "bing" in provider else "duckduckgo")[:limit]
    return results, "ok"


def search_web(query: str, limit: int = 10, provider: str | None = None) -> list[dict]:
    providers = [provider] if provider else _search_provider_order()
    for current in providers:
        if not current:
            continue
        _stat_inc("provider_counts", current)
        try:
            if current == "playwright_search":
                results, status = _search_with_playwright(current, query, limit)
            else:
                results, status = _search_with_requests(current, query, limit)
        except Exception as exc:  # noqa: BLE001
            LOGGER.info("search failed provider=%s query=%s error=%s", current, query, exc)
            continue
        if status == "blocked":
            time.sleep(0.5)
            continue
        if results:
            return results
    return []


def generate_queries(categories: list[str]) -> list[str]:
    queries: list[str] = []
    for category in categories:
        category = category.strip().lower()
        if not category:
            continue
        for template in QUERY_TEMPLATES:
            queries.append(template.format(category=category))
    return queries


def _result_domain(result: dict) -> str:
    url = result.get("url") or ""
    return normalize_domain(urlparse(url).netloc or urlparse(url).path)


def _has_preferred_path(url: str) -> bool:
    path = urlparse(url or "").path.lower()
    return any(hint in path for hint in PREFERRED_PATH_HINTS)


def _is_rejectable_content_result(result: dict) -> bool:
    domain = _result_domain(result)
    if not domain:
        return True
    if domain in CONTENT_DOMAIN_BLOCKLIST:
        return True
    title = (result.get("title") or "").lower()
    url = result.get("url") or ""
    if any(keyword in title for keyword in CONTENT_TITLE_KEYWORDS) and not _has_preferred_path(url):
        return True
    if any(keyword in domain for keyword in AGENCY_DOMAIN_KEYWORDS) and not _has_preferred_path(url):
        return True
    return False


def _result_score(result: dict) -> int:
    score = 0
    url = result.get("url") or ""
    domain = _result_domain(result)
    title = (result.get("title") or "").lower()
    if _has_preferred_path(url):
        score += 30
    if domain and domain not in CONTENT_DOMAIN_BLOCKLIST:
        score += 10
    if any(keyword in title for keyword in CONTENT_TITLE_KEYWORDS):
        score -= 40
    if domain in CONTENT_DOMAIN_BLOCKLIST:
        score -= 80
    if any(keyword in domain for keyword in AGENCY_DOMAIN_KEYWORDS):
        score -= 50
    return score


def discover_candidates(categories: list[str], limit: int = 50) -> list[dict]:
    reset_search_stats()
    queries = generate_queries(categories)
    candidates: list[dict] = []
    seen_domains: set[str] = set()
    for query in queries:
        _LAST_SEARCH_STATS["queries_total"] += 1
        provider_results = search_web(query, limit=limit)
        sorted_results = sorted(provider_results, key=_result_score, reverse=True)
        for result in sorted_results:
            if _is_rejectable_content_result(result):
                _LAST_SEARCH_STATS["rejected_content_domains_count"] += 1
                if any(keyword in (result.get("title") or "").lower() for keyword in CONTENT_TITLE_KEYWORDS):
                    _LAST_SEARCH_STATS["rejected_listicle_domains_count"] += 1
                continue
            domain = normalize_domain(result.get("url"))
            if not domain or domain in seen_domains:
                continue
            seen_domains.add(domain)
            result["category"] = next((category for category in categories if category.lower() in query.lower()), "")
            result["company_name"] = normalize_company_name(result.get("title") or domain)
            result["search_query"] = query
            candidates.append(result)
            if len(candidates) >= limit:
                return candidates
    return candidates
