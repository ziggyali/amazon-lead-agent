from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from html import unescape
from html.parser import HTMLParser
import json
import logging
import os
import base64
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse, urlunparse

import requests

from amazon_lead_agent.normalization import normalize_company_name, normalize_domain
from amazon_lead_agent.lead_filters import (
    BLOCKED_DOMAIN_KEYWORDS,
    BLOCKED_ROOT_DOMAINS,
    BLOCKED_TITLE_KEYWORDS,
    PREFERRED_PATH_HINTS,
    is_hard_junk_result,
    is_junk_company_name,
    is_likely_brand_domain,
    is_soft_brand_candidate,
)


LOGGER = logging.getLogger(__name__)
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AmazonLeadAgent/0.1"
DISCOVERY_DEBUG_PATH = Path("logs/discovery_debug.jsonl")
DEFAULT_MAX_SEARCH_QUERIES_PER_RUN = 16
DEFAULT_MAX_SEARCH_QUERIES_PER_CATEGORY = 4
DEFAULT_MAX_RESULTS_PER_QUERY = 10
DEFAULT_MAX_DISCOVERY_RUNTIME_SECONDS = 300

CONTENT_DOMAIN_BLOCKLIST = BLOCKED_ROOT_DOMAINS
CONTENT_DOMAIN_KEYWORDS = BLOCKED_DOMAIN_KEYWORDS
CONTENT_TITLE_KEYWORDS = BLOCKED_TITLE_KEYWORDS
AGENCY_DOMAIN_KEYWORDS = ("agency", "marketing", "consulting", "services", "service", "pr")

QUERY_TEMPLATES: dict[str, list[str]] = {
    "beauty": [
        '"available on Amazon" "skincare" "official"',
        '"buy on Amazon" "skincare" "official site"',
        '"Amazon storefront" "skincare brand"',
        '"where to buy" "skincare" "Amazon"',
        'inurl:where-to-buy skincare Amazon',
        'inurl:retailers skincare Amazon',
        'inurl:stockists skincare Amazon',
    ],
    "pet": [
        '"available on Amazon" "pet supplies" "official"',
        '"buy on Amazon" "dog treats" "official site"',
        '"Amazon storefront" "pet brand"',
        '"where to buy" "pet supplies" "Amazon"',
        'inurl:where-to-buy pet Amazon',
        'inurl:retailers pet Amazon',
    ],
    "home": [
        '"available on Amazon" "home goods" "official"',
        '"buy on Amazon" "home decor" "official site"',
        '"Amazon storefront" "home brand"',
        '"where to buy" "home goods" "Amazon"',
        'inurl:where-to-buy home Amazon',
        'inurl:retailers home Amazon',
    ],
    "supplements": [
        '"available on Amazon" "supplements" "official"',
        '"buy on Amazon" "vitamins" "official site"',
        '"Amazon storefront" "supplement brand"',
        '"where to buy" "supplements" "Amazon"',
        'inurl:where-to-buy supplements Amazon',
        'inurl:retailers supplements Amazon',
    ],
}

SEED_SITE_FILES = {
    "beauty": Path("data/seeds/beauty_seed_sites.txt"),
    "pet": Path("data/seeds/pet_seed_sites.txt"),
    "home": Path("data/seeds/home_seed_sites.txt"),
    "supplements": Path("data/seeds/supplements_seed_sites.txt"),
}

TRACKING_DOMAINS = {
    "bing.com",
    "www.bing.com",
    "duckduckgo.com",
    "www.duckduckgo.com",
    "search.yahoo.com",
    "www.search.yahoo.com",
}

_LAST_SEARCH_STATS: dict = {}


def _new_stats() -> dict:
    return {
        "search_provider_mode": os.environ.get("SEARCH_PROVIDER", "multi").strip().lower() or "multi",
        "discovery_mode": os.environ.get("DISCOVERY_MODE", "hybrid").strip().lower() or "hybrid",
        "queries_total": 0,
        "query_budget_used": 0,
        "query_budget_remaining": 0,
        "discovery_runtime_seconds": 0.0,
        "stopped_reason": "",
        "seed_lines_processed": 0,
        "seed_pages_fetched": 0,
        "seed_brand_domains_extracted": 0,
        "direct_seed_candidates": 0,
        "seed_candidates_accepted": 0,
        "seed_candidates_rejected": 0,
        "queries_attempted_by_provider": defaultdict(int),
        "provider_counts": defaultdict(int),
        "provider_blocked_counts": defaultdict(int),
        "blocked_query_counts": defaultdict(int),
        "rate_limited_query_counts": defaultdict(int),
        "rejected_content_domain_count": 0,
        "rejected_listicle_domains_count": 0,
        "rejected_likely_brand_filter_count": 0,
        "hard_rejected_junk_count": 0,
        "soft_pass_needs_enrichment_count": 0,
        "rejected_due_to_no_amazon_evidence_count": 0,
        "discovered_count_by_category": defaultdict(int),
        "cleaned_redirect_count": 0,
        "rejected_redirect_count": 0,
    }


def reset_search_stats() -> None:
    global _LAST_SEARCH_STATS
    _LAST_SEARCH_STATS = _new_stats()


def get_last_search_stats() -> dict:
    if not _LAST_SEARCH_STATS:
        reset_search_stats()
    stats = deepcopy(_LAST_SEARCH_STATS)
    stats["queries_attempted_by_provider"] = dict(stats["queries_attempted_by_provider"])
    stats["provider_counts"] = dict(stats["provider_counts"])
    stats["provider_blocked_counts"] = dict(stats["provider_blocked_counts"])
    stats["blocked_query_counts"] = dict(stats["blocked_query_counts"])
    stats["rate_limited_query_counts"] = dict(stats["rate_limited_query_counts"])
    stats["discovered_count_by_category"] = dict(stats["discovered_count_by_category"])
    return stats


def _stat_inc(name: str, provider: str, amount: int = 1) -> None:
    if not _LAST_SEARCH_STATS:
        reset_search_stats()
    _LAST_SEARCH_STATS[name][provider] += amount


def _config_section(config: dict | None) -> dict[str, object]:
    if not isinstance(config, dict):
        return {}
    merged: dict[str, object] = {}
    for key in ("discovery", "campaign"):
        section = config.get(key, {})
        if isinstance(section, dict):
            merged.update(section)
    return merged


def _env_or_config_text(config: dict | None, env_name: str, config_keys: tuple[str, ...], default: str) -> str:
    raw = os.environ.get(env_name, "").strip()
    if raw:
        return raw
    section = _config_section(config)
    for key in config_keys:
        value = section.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return default


def _env_or_config_int(config: dict | None, env_name: str, config_keys: tuple[str, ...], default: int) -> int:
    raw = os.environ.get(env_name, "").strip()
    if raw:
        try:
            return int(raw)
        except ValueError:
            LOGGER.info("invalid %s=%s, using default=%s", env_name, raw, default)
    section = _config_section(config)
    for key in config_keys:
        value = section.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return default


def _discovery_mode(config: dict | None = None) -> str:
    return _env_or_config_text(config, "DISCOVERY_MODE", ("discovery_mode", "mode"), "hybrid").lower()


def _discovery_limits(config: dict | None = None) -> dict[str, int]:
    return {
        "max_search_queries_per_run": _env_or_config_int(config, "MAX_SEARCH_QUERIES_PER_RUN", ("max_search_queries_per_run",), DEFAULT_MAX_SEARCH_QUERIES_PER_RUN),
        "max_search_queries_per_category": _env_or_config_int(config, "MAX_SEARCH_QUERIES_PER_CATEGORY", ("max_search_queries_per_category",), DEFAULT_MAX_SEARCH_QUERIES_PER_CATEGORY),
        "max_results_per_query": _env_or_config_int(config, "MAX_RESULTS_PER_QUERY", ("max_results_per_query",), DEFAULT_MAX_RESULTS_PER_QUERY),
        "max_discovery_runtime_seconds": _env_or_config_int(config, "MAX_DISCOVERY_RUNTIME_SECONDS", ("max_discovery_runtime_seconds",), DEFAULT_MAX_DISCOVERY_RUNTIME_SECONDS),
    }


def _seed_file_for_category(category: str) -> Path | None:
    return SEED_SITE_FILES.get(category.strip().lower())


def _parse_seed_line(line: str) -> dict[str, str] | None:
    raw = unescape((line or "").strip())
    if not raw or raw.startswith("#"):
        return None
    lowered = raw.lower()
    kind = "direct"
    for prefix in ("seed:", "list:", "directory:"):
        if lowered.startswith(prefix):
            kind = "page"
            raw = raw.split(":", 1)[1].strip()
            break
    label = ""
    if "|" in raw:
        url_part, label_part = raw.split("|", 1)
        raw = url_part.strip()
        label = label_part.strip()
    cleaned = clean_search_result_url(raw) or raw
    if not cleaned:
        return None
    return {"kind": kind, "url": cleaned, "label": label}


def _read_seed_sites(category: str) -> list[dict[str, str]]:
    path = _seed_file_for_category(category)
    if not path or not path.exists():
        return []
    seeds: list[dict[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        parsed = _parse_seed_line(line)
        if parsed:
            seeds.append(parsed)
    return seeds


def _query_items_for_category(category: str) -> list[dict[str, str]]:
    templates = QUERY_TEMPLATES.get(category.strip().lower(), [])
    return [{"category": category.strip().lower(), "query": template} for template in templates]


def _seed_items_for_category(category: str) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for seed in _read_seed_sites(category):
        items.append(
            {
                "category": category.strip().lower(),
                "query": seed["url"],
                "source_url": seed["url"],
                "provider": "seeded",
                "kind": seed["kind"],
                "label": seed.get("label", ""),
            }
        )
    return items


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
    url = unescape(url).strip()
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    candidate_keys = ("uddg", "u", "url", "r", "target", "q", "dest", "destination", "href", "p", "adurl", "targeturl", "redirect", "redir")
    candidate_values: list[str] = []
    for key in candidate_keys:
        candidate_values.extend(query.get(key, []))
    if parsed.fragment:
        candidate_values.extend(parse_qs(parsed.fragment).get("u", []))
    candidate_values.append(url)
    tracking_domain = _is_tracking_or_search_domain(url)
    for candidate in candidate_values:
        resolved = _resolve_target_url(candidate)
        if resolved:
            if _is_tracking_or_search_domain(resolved):
                continue
            return resolved
    if tracking_domain:
        return ""
    if parsed.scheme in {"http", "https"}:
        final = urlunparse(parsed._replace(fragment=""))
        if _is_tracking_or_search_domain(final):
            return ""
        return final
    return ""


def _is_tracking_or_search_domain(url: str) -> bool:
    parsed = urlparse(url)
    domain = normalize_domain(parsed.netloc or parsed.path)
    if domain in TRACKING_DOMAINS:
        return True
    if parsed.netloc.endswith("bing.com") and parsed.path.startswith("/ck/a"):
        return True
    if parsed.netloc.endswith("bing.com") and "/ck/" in parsed.path:
        return True
    return False


def _decode_base64ish(value: str) -> str:
    cleaned = re.sub(r"^[a-zA-Z]\d+", "", value.strip())
    cleaned = cleaned.replace("-", "+").replace("_", "/")
    padding = "=" * (-len(cleaned) % 4)
    try:
        decoded = base64.b64decode(cleaned + padding, validate=False)
        return decoded.decode("utf-8", errors="ignore")
    except Exception:  # noqa: BLE001
        return ""


def _resolve_target_url(candidate: str) -> str:
    if not candidate:
        return ""
    candidate = unescape(candidate).strip()
    for _ in range(2):
        candidate = unquote(candidate)
    if candidate.startswith("http://") or candidate.startswith("https://"):
        if _is_tracking_or_search_domain(candidate):
            return ""
        return candidate
    if "http" in candidate:
        match = re.search(r"https?://[^\s\"'<>]+", candidate)
        if match:
            resolved = match.group(0).rstrip(").,;")
            if not _is_tracking_or_search_domain(resolved):
                return resolved
    decoded = _decode_base64ish(candidate)
    if decoded and ("http://" in decoded or "https://" in decoded):
        match = re.search(r"https?://[^\s\"'<>]+", decoded)
        if match:
            resolved = match.group(0).rstrip(").,;")
            if not _is_tracking_or_search_domain(resolved):
                return resolved
    if "://" not in candidate and candidate.startswith("www."):
        resolved = f"https://{candidate}"
        if not _is_tracking_or_search_domain(resolved):
            return resolved
    return ""


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
        _stat_inc("provider_blocked_counts", provider)
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
        _stat_inc("provider_blocked_counts", provider)
        _stat_inc("blocked_query_counts", provider)
        LOGGER.info("provider_blocked provider=%s query=%s", provider, query)
        return [], "blocked"
    results = _extract_results_from_html(html, "bing_html" if "bing" in provider else "duckduckgo")[:limit]
    return results, "ok"


def _search_web_with_provider(query: str, limit: int = 10, provider: str | None = None) -> tuple[list[dict], str | None, str]:
    providers = [provider] if provider else _search_provider_order()
    last_provider = provider or ""
    for current in providers:
        if not current:
            continue
        _stat_inc("queries_attempted_by_provider", current)
        _stat_inc("provider_counts", current)
        last_provider = current
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
            return results, current, "ok"
    return [], last_provider, "empty"


def search_web(query: str, limit: int = 10, provider: str | None = None) -> list[dict]:
    results, provider_used, _ = _search_web_with_provider(query, limit=limit, provider=provider)
    if results:
        provider_label = provider_used or provider or _search_provider_order()[0]
        for result in results:
            result.setdefault("provider", provider_label)
    return results


def search_web_with_metadata(query: str, limit: int = 10, provider: str | None = None) -> tuple[list[dict], str | None, str]:
    results, provider_used, status = _search_web_with_provider(query, limit=limit, provider=provider)
    if results:
        provider_label = provider_used or provider or _search_provider_order()[0]
        for result in results:
            result.setdefault("provider", provider_label)
    return results, provider_used, status


def generate_queries(categories: list[str]) -> list[dict[str, str]]:
    queries: list[dict[str, str]] = []
    for category in categories:
        category = category.strip().lower()
        if not category:
            continue
        queries.extend(_query_items_for_category(category))
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
    title = (result.get("title") or "").lower()
    url = result.get("url") or ""
    snippet = (result.get("snippet") or "").lower()
    if is_hard_junk_result(url, title, snippet):
        return True
    if any(keyword in domain for keyword in AGENCY_DOMAIN_KEYWORDS) and not _has_preferred_path(url):
        if not is_soft_brand_candidate(url, title, snippet):
            return True
    if any(re.search(rf"\b{re.escape(keyword)}\b", title) for keyword in ("list", "review", "best", "top")) and not is_soft_brand_candidate(url, title, snippet):
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


class _AnchorLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[dict[str, str]] = []
        self._current_href: str = ""
        self._current_text: list[str] = []
        self._in_anchor = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        attr_map = {key.lower(): (value or "") for key, value in attrs}
        href = attr_map.get("href", "")
        if not href:
            return
        self._current_href = href
        self._current_text = []
        self._in_anchor = True

    def handle_data(self, data: str) -> None:
        if self._in_anchor:
            text = data.strip()
            if text:
                self._current_text.append(text)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or not self._in_anchor:
            return
        self.links.append({"href": self._current_href, "text": " ".join(self._current_text).strip()})
        self._current_href = ""
        self._current_text = []
        self._in_anchor = False


def _append_discovery_debug(entry: dict[str, object]) -> None:
    try:
        debug_path = Path.cwd() / DISCOVERY_DEBUG_PATH
        debug_path.parent.mkdir(parents=True, exist_ok=True)
        with debug_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception as exc:  # noqa: BLE001
        LOGGER.info("failed to write discovery debug entry: %s", exc)


def _fetch_public_page(url: str) -> str:
    try:
        response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
    except Exception as exc:  # noqa: BLE001
        LOGGER.info("seed page fetch failed url=%s error=%s", url, exc)
        return ""
    if response.status_code != 200:
        return ""
    return response.text or ""


def _extract_links_from_html(html: str, base_url: str) -> list[dict[str, str]]:
    parser = _AnchorLinkParser()
    parser.feed(html or "")
    extracted: list[dict[str, str]] = []
    for item in parser.links:
        href = item.get("href", "")
        absolute = href if urlparse(href).scheme else urljoin(base_url, href)
        cleaned = clean_search_result_url(absolute)
        if not cleaned:
            continue
        extracted.append({"url": cleaned, "title": item.get("text", ""), "snippet": ""})
    return extracted


def _normalize_query_item(item: object, default_category: str = "") -> dict[str, str]:
    if isinstance(item, dict):
        query = str(item.get("query") or item.get("q") or "").strip()
        category = str(item.get("category") or default_category or "").strip().lower()
        provider = str(item.get("provider") or "").strip().lower()
        kind = str(item.get("kind") or "search").strip().lower()
        source_url = str(item.get("source_url") or "").strip()
        label = str(item.get("label") or "").strip()
        return {"query": query, "category": category, "provider": provider, "kind": kind, "source_url": source_url, "label": label}
    query = str(item or "").strip()
    return {"query": query, "category": default_category.strip().lower(), "provider": "", "kind": "search", "source_url": "", "label": ""}


def _unique_nonempty(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        value = str(value or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _brand_candidate_from_url(url: str, title: str, snippet: str, category: str, source_url: str, raw_url: str | None = None) -> dict[str, str]:
    cleaned_url = clean_search_result_url(url)
    return {
        "url": cleaned_url or url,
        "raw_url": raw_url or source_url or url,
        "source_url": source_url or raw_url or url,
        "source_urls": _unique_nonempty([source_url or raw_url or url, cleaned_url or url]),
        "title": title,
        "snippet": snippet,
        "category": category,
        "extracted_brand_domain": cleaned_url or url,
    }


def _direct_seed_candidate(seed_url: str, label: str, category: str) -> dict[str, str]:
    cleaned_url = clean_search_result_url(seed_url)
    brand_url = cleaned_url or seed_url
    domain = normalize_domain(brand_url)
    company_name = normalize_company_name(label or domain or brand_url)
    return {
        "url": brand_url,
        "raw_url": seed_url,
        "source_url": seed_url,
        "source_urls": _unique_nonempty([seed_url, brand_url]),
        "title": label or company_name or domain or brand_url,
        "snippet": "",
        "category": category,
        "company_name": company_name,
        "brand_name": company_name,
        "normalized_company_name": normalize_company_name(company_name),
        "extracted_brand_domain": brand_url,
        "status_hint": "needs_enrichment",
        "seed_url": seed_url,
    }


def _expand_official_brand_domains_from_page(page_url: str, category: str, source_url: str, title: str = "", snippet: str = "") -> list[dict[str, str]]:
    html = _fetch_public_page(page_url)
    if not html:
        return []
    links = _extract_links_from_html(html, page_url)
    candidates: list[dict[str, str]] = []
    seen: set[str] = set()
    for link in links:
        link_url = link.get("url", "")
        if not link_url:
            continue
        domain = normalize_domain(link_url)
        if not domain or domain in seen:
            continue
        if is_hard_junk_result(link_url, link.get("title", ""), link.get("snippet", ""), category):
            _LAST_SEARCH_STATS["seed_candidates_rejected"] += 1
            _append_discovery_debug(
                {
                    "provider": "seeded",
                    "category": category,
                    "seed_url": source_url,
                    "raw_url": page_url,
                    "cleaned_url": link_url,
                    "extracted_brand_domain": "",
                    "title_or_anchor": link.get("title", "") or title,
                    "decision": "rejected",
                    "reason": "hard_junk_outbound_link",
                }
            )
            continue
        if is_likely_brand_domain(link_url, link.get("title", ""), link.get("snippet", ""), category) or is_soft_brand_candidate(link_url, link.get("title", ""), link.get("snippet", ""), category):
            seen.add(domain)
            candidates.append(_brand_candidate_from_url(link_url, link.get("title", ""), link.get("snippet", ""), category, source_url, raw_url=page_url))
            _append_discovery_debug(
                {
                    "provider": "seeded",
                    "category": category,
                    "seed_url": source_url,
                    "raw_url": page_url,
                    "cleaned_url": link_url,
                    "extracted_brand_domain": link_url,
                    "title_or_anchor": link.get("title", "") or title,
                    "decision": "accepted",
                    "reason": "seed_page_expanded_brand_domain",
                }
            )
        else:
            _LAST_SEARCH_STATS["seed_candidates_rejected"] += 1
            _append_discovery_debug(
                {
                    "provider": "seeded",
                    "category": category,
                    "seed_url": source_url,
                    "raw_url": page_url,
                    "cleaned_url": link_url,
                    "extracted_brand_domain": "",
                    "title_or_anchor": link.get("title", "") or title,
                    "decision": "rejected",
                    "reason": "seed_page_non_brand_link",
                }
            )
    return candidates


def discover_candidates(categories: list[str], limit: int = 50, config: dict | None = None) -> list[dict]:
    reset_search_stats()
    limits = _discovery_limits(config)
    mode = _discovery_mode(config)
    _LAST_SEARCH_STATS["discovery_mode"] = mode
    start = time.monotonic()
    candidates: list[dict] = []
    seen_domains: set[str] = set()
    per_category_query_counts: defaultdict[str, int] = defaultdict(int)
    query_plan: list[dict[str, str]] = []
    normalized_categories = [category.strip().lower() for category in categories if str(category).strip()]
    for category in normalized_categories:
        if mode in {"seeded", "hybrid"}:
            query_plan.extend(_seed_items_for_category(category))
        if mode in {"search", "hybrid"}:
            query_plan.extend(generate_queries([category]))

    stopped_reason = "exhausted_sources"

    for item in query_plan:
        if len(candidates) >= limit:
            stopped_reason = "accepted_limit_reached"
            break
        elapsed = time.monotonic() - start
        if elapsed >= limits["max_discovery_runtime_seconds"]:
            stopped_reason = "runtime_budget_exhausted"
            break
        if _LAST_SEARCH_STATS["query_budget_used"] >= limits["max_search_queries_per_run"]:
            stopped_reason = "query_budget_exhausted"
            break

        query_data = _normalize_query_item(item, default_category=normalized_categories[0] if len(normalized_categories) == 1 else "")
        query = query_data["query"]
        category = query_data["category"] or (normalized_categories[0] if len(normalized_categories) == 1 else "")
        kind = query_data["kind"]
        if not query or not category:
            continue
        if per_category_query_counts[category] >= limits["max_search_queries_per_category"]:
            continue

        per_category_query_counts[category] += 1
        _LAST_SEARCH_STATS["queries_total"] += 1
        _LAST_SEARCH_STATS["query_budget_used"] += 1
        _LAST_SEARCH_STATS["query_budget_remaining"] = max(0, limits["max_search_queries_per_run"] - int(_LAST_SEARCH_STATS["query_budget_used"]))

        query_provider = query_data["provider"] or None
        raw_provider = query_provider or "multi"
        provider_results: list[dict] = []
        provider_used = raw_provider
        seed_label = query_data.get("label", "")

        if kind == "direct":
            _LAST_SEARCH_STATS["seed_lines_processed"] += 1
            _LAST_SEARCH_STATS["direct_seed_candidates"] += 1
            candidate = _direct_seed_candidate(query, seed_label, category)
            candidate_url = candidate.get("url") or ""
            domain = normalize_domain(candidate_url)
            title_or_anchor = str(candidate.get("title") or seed_label or candidate_url)
            if not domain or is_hard_junk_result(candidate_url, title_or_anchor, "", category) or is_junk_company_name(candidate.get("company_name")):
                _LAST_SEARCH_STATS["seed_candidates_rejected"] += 1
                _LAST_SEARCH_STATS["hard_rejected_junk_count"] += 1
                _append_discovery_debug(
                    {
                        "provider": "seeded",
                        "category": category,
                        "seed_url": query,
                        "raw_url": query,
                        "cleaned_url": candidate_url,
                        "extracted_brand_domain": "",
                        "title_or_anchor": title_or_anchor,
                        "decision": "rejected",
                        "reason": "direct_seed_hard_junk",
                    }
                )
                continue
            if domain in seen_domains:
                _LAST_SEARCH_STATS["seed_candidates_rejected"] += 1
                _append_discovery_debug(
                    {
                        "provider": "seeded",
                        "category": category,
                        "seed_url": query,
                        "raw_url": query,
                        "cleaned_url": candidate_url,
                        "extracted_brand_domain": candidate_url,
                        "title_or_anchor": title_or_anchor,
                        "decision": "rejected",
                        "reason": "duplicate_seed_domain",
                    }
                )
                continue
            candidate.update(
                {
                    "provider": "seeded",
                    "search_query": "",
                    "status_hint": "needs_enrichment",
                    "source_url": query,
                    "source_urls": _unique_nonempty([query, candidate_url]),
                }
            )
            seen_domains.add(domain)
            candidates.append(candidate)
            _LAST_SEARCH_STATS["soft_pass_needs_enrichment_count"] += 1
            _LAST_SEARCH_STATS["seed_candidates_accepted"] += 1
            _LAST_SEARCH_STATS["seed_lines_processed"] += 1
            _LAST_SEARCH_STATS["seed_brand_domains_extracted"] += 1
            _append_discovery_debug(
                {
                    "provider": "seeded",
                    "category": category,
                    "seed_url": query,
                    "raw_url": query,
                    "cleaned_url": candidate_url,
                    "extracted_brand_domain": candidate_url,
                    "title_or_anchor": title_or_anchor,
                    "decision": "soft_pass",
                    "reason": "direct_seed_brand_domain",
                }
            )
            continue

        if kind == "page":
            _LAST_SEARCH_STATS["seed_lines_processed"] += 1
            _LAST_SEARCH_STATS["seed_pages_fetched"] += 1
            expanded_candidates = _expand_official_brand_domains_from_page(query, category, source_url=query, title=seed_label or "", snippet="")
            if expanded_candidates:
                _LAST_SEARCH_STATS["seed_brand_domains_extracted"] += len(expanded_candidates)
                for candidate in expanded_candidates:
                    if len(candidates) >= limit:
                        stopped_reason = "accepted_limit_reached"
                        break
                    candidate_url = candidate.get("url") or ""
                    domain = normalize_domain(candidate_url)
                    if not domain or domain in seen_domains:
                        _LAST_SEARCH_STATS["seed_candidates_rejected"] += 1
                        continue
                    candidate_company = normalize_company_name(candidate.get("title") or domain)
                    if is_junk_company_name(candidate_company):
                        _LAST_SEARCH_STATS["seed_candidates_rejected"] += 1
                        _LAST_SEARCH_STATS["hard_rejected_junk_count"] += 1
                        _append_discovery_debug(
                            {
                                "provider": "seeded",
                                "category": category,
                                "seed_url": query,
                                "raw_url": query,
                                "cleaned_url": candidate_url,
                                "extracted_brand_domain": "",
                                "title_or_anchor": candidate.get("title") or seed_label or candidate_url,
                                "decision": "rejected",
                                "reason": "seed_page_brand_company_junk",
                            }
                        )
                        continue
                    candidate.update(
                        {
                            "category": category,
                            "company_name": candidate_company,
                            "brand_name": candidate_company,
                            "normalized_company_name": normalize_company_name(candidate_company),
                            "search_query": query,
                            "provider": "seeded",
                            "status_hint": "needs_enrichment",
                        }
                    )
                    seen_domains.add(domain)
                    candidates.append(candidate)
                    _LAST_SEARCH_STATS["soft_pass_needs_enrichment_count"] += 1
                    _LAST_SEARCH_STATS["seed_candidates_accepted"] += 1
                continue
            _LAST_SEARCH_STATS["seed_candidates_rejected"] += 1
            _append_discovery_debug(
                {
                    "provider": "seeded",
                    "category": category,
                    "seed_url": query,
                    "raw_url": query,
                    "cleaned_url": "",
                    "extracted_brand_domain": "",
                    "title_or_anchor": seed_label or query,
                    "decision": "rejected",
                    "reason": "seed_page_no_brand_domain",
                }
            )
            continue

        provider_results, provider_used, _ = _search_web_with_provider(query, limit=limits["max_results_per_query"], provider=query_provider or None)
        sorted_results = sorted(provider_results, key=_result_score, reverse=True)
        for result in sorted_results:
            if len(candidates) >= limit:
                stopped_reason = "accepted_limit_reached"
                break
            raw_url = result.get("raw_url") or result.get("url") or ""
            cleaned_url = clean_search_result_url(raw_url or result.get("url") or "")
            title = result.get("title") or ""
            snippet = result.get("snippet") or ""
            decision = "rejected"
            reason = ""
            extracted_brand_domain = ""
            if kind == "seed":
                expanded_candidates = _expand_official_brand_domains_from_page(raw_url, category, source_url=query, title=title, snippet=snippet)
                if not expanded_candidates:
                    reason = "seed_page_no_brand_domain"
                    _append_discovery_debug(
                        {
                            "query": query,
                            "provider": provider_used,
                            "category": category,
                            "raw_url": raw_url,
                            "cleaned_url": cleaned_url,
                            "title": title,
                            "snippet": snippet,
                            "decision": decision,
                            "reason": reason,
                            "extracted_brand_domain": "",
                        }
                    )
                    continue
                for candidate in expanded_candidates:
                    if len(candidates) >= limit:
                        stopped_reason = "accepted_limit_reached"
                        break
                    candidate_url = candidate.get("url") or ""
                    domain = normalize_domain(candidate_url)
                    if not domain or domain in seen_domains:
                        continue
                    candidate_company = normalize_company_name(candidate.get("title") or domain)
                    if is_junk_company_name(candidate_company):
                        _LAST_SEARCH_STATS["hard_rejected_junk_count"] += 1
                        _append_discovery_debug(
                            {
                                "query": query,
                                "provider": provider_used,
                                "category": category,
                                "raw_url": raw_url,
                                "cleaned_url": candidate_url,
                                "title": candidate.get("title") or title,
                                "snippet": candidate.get("snippet") or snippet,
                                "decision": "rejected",
                                "reason": "junk_company_name",
                                "extracted_brand_domain": candidate_url,
                            }
                        )
                        continue
                    candidate.update(
                        {
                            "category": category,
                            "company_name": candidate_company,
                            "brand_name": candidate_company,
                            "normalized_company_name": normalize_company_name(candidate_company),
                            "search_query": query,
                            "provider": provider_used,
                            "status_hint": "needs_enrichment",
                        }
                    )
                    seen_domains.add(domain)
                    candidates.append(candidate)
                    decision = "soft_pass"
                    reason = "seed_expanded_official_brand_domain"
                    extracted_brand_domain = candidate_url
                    _LAST_SEARCH_STATS["soft_pass_needs_enrichment_count"] += 1
                    _append_discovery_debug(
                        {
                            "query": query,
                            "provider": provider_used,
                            "category": category,
                            "raw_url": raw_url,
                            "cleaned_url": candidate_url,
                            "title": candidate.get("title") or title,
                            "snippet": candidate.get("snippet") or snippet,
                            "decision": decision,
                            "reason": reason,
                            "extracted_brand_domain": extracted_brand_domain,
                        }
                    )
                continue

            if _is_rejectable_content_result(result):
                _LAST_SEARCH_STATS["rejected_content_domain_count"] += 1
                if any(keyword in (title or "").lower() for keyword in CONTENT_TITLE_KEYWORDS):
                    _LAST_SEARCH_STATS["rejected_listicle_domains_count"] += 1
                _append_discovery_debug(
                    {
                        "query": query,
                        "provider": provider_used,
                        "category": category,
                        "raw_url": raw_url,
                        "cleaned_url": cleaned_url,
                        "title": title,
                        "snippet": snippet,
                        "decision": "rejected",
                        "reason": "hard_junk_result",
                        "extracted_brand_domain": "",
                    }
                )
                continue

            if raw_url and cleaned_url and cleaned_url != raw_url:
                _LAST_SEARCH_STATS["cleaned_redirect_count"] += 1
            if raw_url and not cleaned_url:
                _LAST_SEARCH_STATS["rejected_redirect_count"] += 1
                _append_discovery_debug(
                    {
                        "query": query,
                        "provider": provider_used,
                        "category": category,
                        "raw_url": raw_url,
                        "cleaned_url": "",
                        "title": title,
                        "snippet": snippet,
                        "decision": "rejected",
                        "reason": "unresolvable_redirect",
                        "extracted_brand_domain": "",
                    }
                )
                continue

            final_url = cleaned_url or result.get("url") or ""
            domain = normalize_domain(final_url)
            if not domain or domain in seen_domains:
                continue

            if is_likely_brand_domain(final_url, title, snippet, category):
                decision = "accepted"
                reason = "brand_domain"
                candidate = dict(result)
                candidate.update(
                    {
                        "url": final_url,
                        "raw_url": raw_url or final_url,
                        "source_url": result.get("source_url") or raw_url or final_url,
                        "category": category,
                        "company_name": normalize_company_name(title or domain),
                        "normalized_company_name": normalize_company_name(title or domain),
                        "search_query": query,
                        "provider": provider_used,
                    }
                )
                if is_junk_company_name(candidate["company_name"]):
                    _LAST_SEARCH_STATS["hard_rejected_junk_count"] += 1
                    _append_discovery_debug(
                        {
                            "query": query,
                            "provider": provider_used,
                            "category": category,
                            "raw_url": raw_url,
                            "cleaned_url": final_url,
                            "title": title,
                            "snippet": snippet,
                            "decision": "rejected",
                            "reason": "junk_company_name",
                            "extracted_brand_domain": "",
                        }
                    )
                    continue
                seen_domains.add(domain)
                candidates.append(candidate)
                _append_discovery_debug(
                    {
                        "query": query,
                        "provider": provider_used,
                        "category": category,
                        "raw_url": raw_url,
                        "cleaned_url": final_url,
                        "title": title,
                        "snippet": snippet,
                        "decision": decision,
                        "reason": reason,
                        "extracted_brand_domain": "",
                    }
                )
                continue

            if is_soft_brand_candidate(final_url, title, snippet, category):
                decision = "soft_pass"
                reason = "brand_like_domain"
                candidate = dict(result)
                candidate.update(
                    {
                        "url": final_url,
                        "raw_url": raw_url or final_url,
                        "source_url": result.get("source_url") or raw_url or final_url,
                        "category": category,
                        "company_name": normalize_company_name(title or domain),
                        "normalized_company_name": normalize_company_name(title or domain),
                        "search_query": query,
                        "provider": provider_used,
                        "status_hint": "needs_enrichment",
                    }
                )
                if is_junk_company_name(candidate["company_name"]):
                    _LAST_SEARCH_STATS["hard_rejected_junk_count"] += 1
                    _append_discovery_debug(
                        {
                            "query": query,
                            "provider": provider_used,
                            "category": category,
                            "raw_url": raw_url,
                            "cleaned_url": final_url,
                            "title": title,
                            "snippet": snippet,
                            "decision": "rejected",
                            "reason": "junk_company_name",
                            "extracted_brand_domain": "",
                        }
                    )
                    continue
                seen_domains.add(domain)
                candidates.append(candidate)
                _LAST_SEARCH_STATS["soft_pass_needs_enrichment_count"] += 1
                _LAST_SEARCH_STATS["rejected_likely_brand_filter_count"] += 1
                _append_discovery_debug(
                    {
                        "query": query,
                        "provider": provider_used,
                        "category": category,
                        "raw_url": raw_url,
                        "cleaned_url": final_url,
                        "title": title,
                        "snippet": snippet,
                        "decision": decision,
                        "reason": reason,
                        "extracted_brand_domain": "",
                    }
                )
                continue

            expanded_candidates = _expand_official_brand_domains_from_page(final_url, category, source_url=raw_url or final_url, title=title, snippet=snippet)
            if expanded_candidates:
                for candidate in expanded_candidates:
                    if len(candidates) >= limit:
                        stopped_reason = "accepted_limit_reached"
                        break
                    candidate_url = candidate.get("url") or ""
                    candidate_domain = normalize_domain(candidate_url)
                    if not candidate_domain or candidate_domain in seen_domains:
                        continue
                    candidate_company = normalize_company_name(candidate.get("title") or candidate_domain)
                    if is_junk_company_name(candidate_company):
                        _LAST_SEARCH_STATS["hard_rejected_junk_count"] += 1
                        _append_discovery_debug(
                            {
                                "query": query,
                                "provider": provider_used,
                                "category": category,
                                "raw_url": raw_url,
                                "cleaned_url": candidate_url,
                                "title": candidate.get("title") or title,
                                "snippet": candidate.get("snippet") or snippet,
                                "decision": "rejected",
                                "reason": "junk_company_name",
                                "extracted_brand_domain": candidate_url,
                            }
                        )
                        continue
                    candidate.update(
                        {
                            "category": category,
                            "company_name": candidate_company,
                            "brand_name": candidate_company,
                            "normalized_company_name": normalize_company_name(candidate_company),
                            "search_query": query,
                            "provider": provider_used,
                            "status_hint": "needs_enrichment",
                        }
                    )
                    seen_domains.add(candidate_domain)
                    candidates.append(candidate)
                    _LAST_SEARCH_STATS["soft_pass_needs_enrichment_count"] += 1
                    _append_discovery_debug(
                        {
                            "query": query,
                            "provider": provider_used,
                            "category": category,
                            "raw_url": raw_url,
                            "cleaned_url": candidate_url,
                            "title": candidate.get("title") or title,
                            "snippet": candidate.get("snippet") or snippet,
                            "decision": "soft_pass",
                            "reason": "expanded_official_brand_domain",
                            "extracted_brand_domain": candidate_url,
                        }
                    )
                continue

            _LAST_SEARCH_STATS["rejected_likely_brand_filter_count"] += 1
            _append_discovery_debug(
                {
                    "query": query,
                    "provider": provider_used,
                    "category": category,
                    "raw_url": raw_url,
                    "cleaned_url": final_url,
                    "title": title,
                    "snippet": snippet,
                    "decision": "rejected",
                    "reason": "no_official_brand_domain_found",
                    "extracted_brand_domain": "",
                }
            )

    _LAST_SEARCH_STATS["query_budget_remaining"] = max(0, limits["max_search_queries_per_run"] - int(_LAST_SEARCH_STATS["query_budget_used"]))
    _LAST_SEARCH_STATS["discovery_runtime_seconds"] = round(time.monotonic() - start, 3)
    _LAST_SEARCH_STATS["stopped_reason"] = stopped_reason
    return candidates
