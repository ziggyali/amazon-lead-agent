from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
import json
import logging
from pathlib import Path
from urllib.parse import urlparse, urlunparse
import re

from amazon_lead_agent.normalization import infer_brand_name_from_domain, normalize_company_name, normalize_domain, resolve_canonical_brand_name
from amazon_lead_agent.tools.amazon_backlink_discovery import is_valid_amazon_url
from amazon_lead_agent.tools.search import clean_search_result_url, get_last_search_stats, search_web_with_metadata


LOGGER = logging.getLogger(__name__)
DEBUG_PATH = Path("logs/amazon_verification_debug.jsonl")
CACHE_PATH = Path("data/amazon_verification_cache.json")


STRUCTURED_EVIDENCE_TYPES = {
    "official_site_amazon_backlink",
    "amazon_storefront_search_result",
    "amazon_product_search_result",
    "amazon_brand_page_search_result",
    "manual_verified_amazon_url",
    "cached_verified_amazon_url",
}

WEAK_EVIDENCE_TYPES = {"weak_text_signal"}


def _debug_path() -> Path:
    return DEBUG_PATH.resolve()


def _cache_path() -> Path:
    return CACHE_PATH.resolve()


def _brand_tokens(brand_name: str) -> list[str]:
    normalized = normalize_company_name(brand_name)
    tokens = [token for token in re.split(r"[\s\-_.]+", normalized) if token]
    return [token for token in tokens if len(token) > 1]


def _brand_matches_text(brand_name: str, text: str) -> bool:
    lowered = f" {unescape(text or '').lower()} "
    brand = normalize_company_name(brand_name)
    if not brand:
        return False
    if brand in lowered:
        return True
    tokens = _brand_tokens(brand)
    if not tokens:
        return False
    hits = sum(1 for token in tokens if re.search(rf"\b{re.escape(token)}\b", lowered))
    return hits >= max(1, len(tokens) // 2)


def _normalize_amazon_url(url: str | None) -> str:
    if url is None:
        LOGGER.info("amazon url rejected: empty value")
        return ""
    if not isinstance(url, str):
        LOGGER.info("amazon url rejected: non-string value=%r", url)
        return ""
    raw = url.strip()
    if any(ch in raw for ch in "[]{}") and not raw.startswith(("http://", "https://", "www.")):
        LOGGER.info("amazon url rejected: malformed bracketed value=%r", url)
        return ""
    cleaned = ""
    try:
        cleaned = clean_search_result_url(raw) or raw
    except Exception as exc:  # noqa: BLE001
        LOGGER.info("amazon url rejected: clean failed value=%r error=%s", url, exc)
        return ""
    cleaned = str(cleaned or "").strip()
    if not cleaned or cleaned in {"[]", "{}", "None", "none"}:
        LOGGER.info("amazon url rejected: empty or malformed value=%r", url)
        return ""
    try:
        parsed = urlparse(cleaned)
    except Exception as exc:  # noqa: BLE001
        LOGGER.info("amazon url rejected: parse failed value=%r cleaned=%r error=%s", url, cleaned, exc)
        return ""
    if not parsed.scheme or not parsed.netloc:
        LOGGER.info("amazon url rejected: missing scheme/netloc value=%r cleaned=%r", url, cleaned)
        return ""
    if not is_valid_amazon_url(cleaned):
        LOGGER.info("amazon url rejected: non-amazon value=%r cleaned=%r", url, cleaned)
        return ""
    normalized = parsed._replace(query="", fragment="")
    return urlunparse(normalized)


def _evidence_type_from_url(url: str, title: str, snippet: str) -> tuple[str, str, str]:
    parsed = urlparse(url)
    path = parsed.path.lower()
    text = f"{title} {snippet}".lower()
    if "/stores/" in path or "visit the" in text and "store" in text:
        return "amazon_storefront_search_result", "high", "Amazon storefront/store page matched"
    if any(token in path for token in ("/dp/", "/gp/product/", "/product/")):
        return "amazon_product_search_result", "medium", "Amazon product page matched"
    if any(token in path for token in ("/brand/", "/gp/brand/", "/stores")) or "brand" in text and "amazon" in text:
        return "amazon_brand_page_search_result", "medium", "Amazon brand page matched"
    if any(token in path for token in ("/s", "/search")):
        return "weak_text_signal", "low", "Generic Amazon search result page"
    return "weak_text_signal", "low", "Amazon URL without a specific storefront/product/brand signal"


@dataclass
class AmazonEvidenceVerificationResult:
    canonical_brand_name: str
    website_title: str
    amazon_queries_run: list[str]
    amazon_search_results_seen: int
    amazon_results_rejected_count: int
    amazon_results_rejected_reasons: list[str]
    structured_evidence_found: bool
    weak_text_signal_found: bool
    best_evidence_url: str
    best_evidence_title: str
    best_evidence_snippet: str
    best_evidence_source: str
    best_evidence_confidence: str
    best_evidence_type: str
    best_evidence_reason: str
    evidence_last_verified_at: str
    evidence_items: list[dict]
    search_stats: dict

    def to_dict(self) -> dict:
        amazon_evidence_urls = [
            item.get("evidence_url")
            for item in self.evidence_items
            if item.get("structured") and item.get("evidence_url") and is_valid_amazon_url(str(item.get("evidence_url")))
        ]
        best_url = self.best_evidence_url if is_valid_amazon_url(self.best_evidence_url) else ""
        if self.best_evidence_type in STRUCTURED_EVIDENCE_TYPES and best_url and best_url not in amazon_evidence_urls:
            amazon_evidence_urls.insert(0, best_url)
        return {
            "canonical_brand_name": self.canonical_brand_name,
            "website_title": self.website_title,
            "amazon_queries_run": list(self.amazon_queries_run),
            "amazon_search_results_seen": self.amazon_search_results_seen,
            "amazon_results_rejected_count": self.amazon_results_rejected_count,
            "amazon_results_rejected_reasons": list(self.amazon_results_rejected_reasons),
            "structured_evidence_found": self.structured_evidence_found,
            "weak_text_signal_found": self.weak_text_signal_found,
            "best_evidence_url": best_url,
            "best_evidence_title": self.best_evidence_title,
            "best_evidence_snippet": self.best_evidence_snippet,
            "best_evidence_source": self.best_evidence_source,
            "best_evidence_confidence": self.best_evidence_confidence,
            "best_evidence_type": self.best_evidence_type,
            "best_evidence_reason": self.best_evidence_reason,
            "evidence_last_verified_at": self.evidence_last_verified_at,
            "amazon_evidence_items": list(self.evidence_items),
            "amazon_evidence_urls": amazon_evidence_urls,
            "amazon_backlink_found": self.structured_evidence_found,
            "amazon_evidence_summary": self._summary(),
            "cached_verified_amazon_url": best_url if self.best_evidence_type == "cached_verified_amazon_url" else "",
            "search_stats": self.search_stats,
        }

    def _summary(self) -> str:
        if self.best_evidence_url:
            return f"{self.best_evidence_type}: {self.best_evidence_url}"
        if self.weak_text_signal_found:
            return "weak Amazon text signal only"
        return ""


def _append_debug_record(payload: dict) -> None:
    try:
        _debug_path().parent.mkdir(parents=True, exist_ok=True)
        with _debug_path().open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception as exc:  # noqa: BLE001
        LOGGER.info("amazon verification debug write failed: %s", exc)


def _load_cache() -> dict:
    try:
        path = _cache_path()
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        LOGGER.info("amazon verification cache load failed: %s", exc)
    return {}


def _save_cache(cache: dict) -> None:
    try:
        path = _cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        LOGGER.info("amazon verification cache save failed: %s", exc)


def _verification_cache_key(canonical_brand_name: str, root_domain: str) -> str:
    brand = normalize_company_name(canonical_brand_name)
    domain = normalize_domain(root_domain)
    return "|".join(part for part in (brand, domain) if part) or "unknown"


def _cache_verified_evidence(canonical_brand_name: str, root_domain: str, item: dict) -> None:
    url = _normalize_amazon_url(item.get("evidence_url"))
    if not url:
        return
    cache = _load_cache()
    cache[_verification_cache_key(canonical_brand_name, root_domain)] = {
        "canonical_brand_name": canonical_brand_name,
        "root_domain": normalize_domain(root_domain),
        "best_evidence_url": url,
        "best_evidence_type": str(item.get("evidence_type") or ""),
        "best_evidence_confidence": str(item.get("confidence") or ""),
        "best_evidence_title": str(item.get("evidence_title") or ""),
        "best_evidence_snippet": str(item.get("evidence_snippet") or ""),
        "best_evidence_source": str(item.get("evidence_source") or ""),
        "evidence_last_verified_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_cache(cache)


def _load_verified_cache(canonical_brand_name: str, root_domain: str) -> dict:
    cache = _load_cache()
    cached = cache.get(_verification_cache_key(canonical_brand_name, root_domain)) or {}
    if not isinstance(cached, dict):
        return {}
    url = _normalize_amazon_url(cached.get("best_evidence_url"))
    if not url:
        return {}
    cached["best_evidence_url"] = url
    cached["best_evidence_type"] = "cached_verified_amazon_url"
    cached["best_evidence_confidence"] = "medium"
    cached["best_evidence_source"] = "cached_prior_verified"
    cached["evidence_last_verified_at"] = str(cached.get("evidence_last_verified_at") or "")
    return cached


def _manual_override_item(lead: dict, canonical_brand_name: str) -> dict | None:
    manual_url = lead.get("manual_amazon_evidence_url") or ""
    normalized = _normalize_amazon_url(manual_url)
    if not normalized:
        return None
    return {
        "evidence_type": "manual_verified_amazon_url",
        "evidence_url": normalized,
        "evidence_title": "",
        "evidence_snippet": lead.get("manual_amazon_evidence_notes", ""),
        "evidence_source": "manual_sheet_override",
        "confidence": "high",
        "reason": f"manual override for {canonical_brand_name}",
        "structured": True,
    }


def _official_site_backlink_items(lead: dict, canonical_brand_name: str) -> list[dict]:
    items: list[dict] = []
    amazon_urls: list[str] = []
    for field in ("amazon_evidence_url", "amazon_evidence_urls", "amazon_links", "amazon_links_json"):
        value = lead.get(field)
        if not value:
            continue
        if isinstance(value, str):
            amazon_urls.append(value)
        elif isinstance(value, list):
            amazon_urls.extend(str(item) for item in value if str(item).strip())
        else:
            amazon_urls.append(str(value))
    for url in amazon_urls:
        normalized = _normalize_amazon_url(url)
        if not normalized:
            continue
        item = {
            "evidence_type": "official_site_amazon_backlink",
            "evidence_url": normalized,
            "evidence_title": lead.get("website_title", ""),
            "evidence_snippet": lead.get("amazon_evidence_summary", ""),
            "evidence_source": lead.get("website") or lead.get("source_url") or "official_site",
            "confidence": "high",
            "reason": f"official website backlink for {canonical_brand_name}",
            "structured": True,
        }
        items.append(item)
        _cache_verified_evidence(canonical_brand_name, lead.get("website") or lead.get("source_url") or "", item)
        break
    return items


def _brand_search_queries(canonical_brand_name: str, root_domain: str) -> list[str]:
    brand = canonical_brand_name.strip()
    domain = root_domain.strip()
    if not brand:
        return []
    return [
        f'site:amazon.com/stores "{brand}"',
        f'site:amazon.com "{brand}" "Visit the {brand} Store"',
        f'site:amazon.com "{brand}" "Amazon.com"',
        f'site:amazon.com "{brand}" "{domain}"' if domain else f'site:amazon.com "{brand}"',
        f'"{brand}" "Amazon Storefront"',
        f'"{brand}" "Amazon.com" "Store"',
    ]


def _classify_search_result(brand: str, result: dict, query: str) -> dict:
    raw_url = str(result.get("url") or "")
    title = str(result.get("title") or "")
    snippet = str(result.get("snippet") or "")
    cleaned_url = _normalize_amazon_url(raw_url)
    evidence_type, confidence, reason = _evidence_type_from_url(cleaned_url or raw_url, title, snippet) if cleaned_url else ("weak_text_signal", "low", "Non-Amazon or unverified URL")
    provider = str(result.get("provider") or "")
    if not cleaned_url:
        return {
            "evidence_type": evidence_type,
            "evidence_url": "",
            "evidence_title": title,
            "evidence_snippet": snippet,
            "evidence_source": "search_index",
            "confidence": "low",
            "reason": reason,
            "structured": False,
            "query": query,
            "provider": provider,
            "raw_url": raw_url,
            "cleaned_url": "",
            "decision": "rejected",
            "rejected": True,
            "rejected_reason": reason,
        }

    text_match = _brand_matches_text(brand, f"{title} {snippet}")
    if evidence_type == "weak_text_signal":
        return {
            "evidence_type": "weak_text_signal",
            "evidence_url": cleaned_url,
            "evidence_title": title,
            "evidence_snippet": snippet,
            "evidence_source": "search_index",
            "confidence": "low",
            "reason": reason,
            "structured": False,
            "query": query,
            "provider": provider,
            "raw_url": raw_url,
            "cleaned_url": cleaned_url,
            "decision": "accepted",
            "rejected": False,
            "text_match": text_match,
        }

    structured = evidence_type in STRUCTURED_EVIDENCE_TYPES
    if structured and not text_match and evidence_type != "official_site_amazon_backlink":
        confidence = "medium"
    return {
        "evidence_type": evidence_type,
        "evidence_url": cleaned_url,
        "evidence_title": title,
        "evidence_snippet": snippet,
        "evidence_source": "search_index",
        "confidence": confidence,
        "reason": reason,
        "structured": structured,
        "query": query,
        "provider": provider,
        "raw_url": raw_url,
        "cleaned_url": cleaned_url,
        "decision": "accepted",
        "rejected": False,
        "text_match": text_match,
    }


def verify_amazon_evidence(lead: dict, search_limit: int = 8) -> dict:
    canonical_brand_name = resolve_canonical_brand_name(lead)
    website = str(lead.get("website") or "").strip()
    root_domain = normalize_domain(website)
    website_title = str(lead.get("website_title") or lead.get("title") or "").strip()
    if not canonical_brand_name:
        canonical_brand_name = infer_brand_name_from_domain(root_domain or website)
    queries = _brand_search_queries(canonical_brand_name, root_domain)
    evidence_items: list[dict] = []
    rejected_reasons: list[str] = []
    debug_records: list[dict] = []

    manual = _manual_override_item(lead, canonical_brand_name)
    if manual:
        evidence_items.append(manual)
        _cache_verified_evidence(canonical_brand_name, root_domain or website, manual)
        debug_records.append(
            {
                "brand": canonical_brand_name,
                "canonical_brand_name": canonical_brand_name,
                "query": "manual_amazon_evidence_url",
                "provider": "manual_sheet_override",
                "raw_url": lead.get("manual_amazon_evidence_url") or "",
                "cleaned_url": manual.get("evidence_url", ""),
                "title": manual.get("evidence_title", ""),
                "snippet": manual.get("evidence_snippet", ""),
                "decision": "accepted",
                "evidence_type": manual.get("evidence_type", ""),
                "confidence": manual.get("confidence", ""),
                "reason": manual.get("reason", ""),
            }
        )

    evidence_items.extend(_official_site_backlink_items(lead, canonical_brand_name))

    search_results_seen = 0
    for query in queries:
        results, provider_used, status = search_web_with_metadata(query, limit=search_limit)
        provider_label = provider_used or str(get_last_search_stats().get("search_provider_mode") or "search")
        search_results_seen += len(results)
        if not results:
            reason = "provider_blocked" if status == "blocked" else "no_results"
            debug_records.append(
                {
                    "brand": canonical_brand_name,
                    "canonical_brand_name": canonical_brand_name,
                    "query": query,
                    "provider": provider_label,
                    "raw_url": "",
                    "cleaned_url": "",
                    "title": "",
                    "snippet": "",
                    "decision": "rejected",
                    "reason": reason,
                }
            )
            if status == "blocked":
                rejected_reasons.append(reason)
            continue
        for result in results:
            if not result.get("provider"):
                result["provider"] = provider_label
            item = _classify_search_result(canonical_brand_name, result, query)
            debug_records.append(
                {
                    "brand": canonical_brand_name,
                    "canonical_brand_name": canonical_brand_name,
                    "query": query,
                    "provider": item.get("provider", provider_label),
                    "raw_url": item.get("raw_url", ""),
                    "cleaned_url": item.get("cleaned_url", ""),
                    "title": item.get("evidence_title", ""),
                    "snippet": item.get("evidence_snippet", ""),
                    "decision": item.get("decision", "rejected"),
                    "evidence_type": item.get("evidence_type", "") if not item.get("rejected") else "",
                    "confidence": item.get("confidence", "") if not item.get("rejected") else "",
                    "reason": item.get("reason", "") if not item.get("rejected") else item.get("rejected_reason", ""),
                }
            )
            if item.get("rejected"):
                if item.get("rejected_reason"):
                    rejected_reasons.append(str(item["rejected_reason"]))
                continue
            evidence_items.append(item)
            if item.get("structured"):
                _cache_verified_evidence(canonical_brand_name, root_domain or website, item)

    structured_items = [item for item in evidence_items if item.get("structured") and is_valid_amazon_url(str(item.get("evidence_url") or ""))]
    weak_items = [item for item in evidence_items if item.get("evidence_type") == "weak_text_signal"]
    best_item = None
    best_rank = (-1, -1)
    confidence_rank = {"high": 3, "medium": 2, "low": 1}
    for item in structured_items:
        rank = (confidence_rank.get(str(item.get("confidence")), 0), 1 if item.get("evidence_type") == "official_site_amazon_backlink" else 0)
        if rank > best_rank:
            best_rank = rank
            best_item = item

    evidence_last_verified_at = ""
    if best_item and best_item.get("structured") and best_item.get("evidence_type") != "cached_verified_amazon_url":
        evidence_last_verified_at = datetime.now(timezone.utc).isoformat()

    if not structured_items:
        cached = _load_verified_cache(canonical_brand_name, root_domain or website)
        if cached:
            best_item = {
                "evidence_type": "cached_verified_amazon_url",
                "evidence_url": cached.get("best_evidence_url", ""),
                "evidence_title": cached.get("best_evidence_title", ""),
                "evidence_snippet": cached.get("best_evidence_snippet", ""),
                "evidence_source": "cached_prior_verified",
                "confidence": "medium",
                "reason": "cached prior verified Amazon evidence",
                "structured": True,
            }
            best_url = str(best_item.get("evidence_url") or "")
            evidence_last_verified_at = str(cached.get("evidence_last_verified_at") or "")
            structured_items = [best_item]
        elif weak_items:
            best_item = weak_items[0]
            best_url = str(best_item.get("evidence_url") or "").strip()
        else:
            best_item = None
            best_url = ""
    else:
        best_url = str(best_item.get("evidence_url") if best_item else "").strip()

    best_title = str(best_item.get("evidence_title") if best_item else "")
    best_snippet = str(best_item.get("evidence_snippet") if best_item else "")
    best_source = str(best_item.get("evidence_source") if best_item else "")
    amazon_evidence_urls: list[str] = []
    for item in structured_items:
        url = str(item.get("evidence_url") or "").strip()
        if is_valid_amazon_url(url) and url not in amazon_evidence_urls:
            amazon_evidence_urls.append(url)
    if best_url and is_valid_amazon_url(best_url) and best_url not in amazon_evidence_urls:
        amazon_evidence_urls.insert(0, best_url)

    amazon_queries_run = list(queries)
    rejected_count = max(0, search_results_seen - len(structured_items) - len(weak_items))
    structured_evidence_found = bool(best_item and best_item.get("structured") and best_url and is_valid_amazon_url(best_url))
    result = AmazonEvidenceVerificationResult(
        canonical_brand_name=canonical_brand_name,
        website_title=website_title,
        amazon_queries_run=amazon_queries_run,
        amazon_search_results_seen=search_results_seen,
        amazon_results_rejected_count=rejected_count,
        amazon_results_rejected_reasons=sorted(set(rejected_reasons)),
        structured_evidence_found=structured_evidence_found,
        weak_text_signal_found=bool(weak_items),
        best_evidence_url=best_url if is_valid_amazon_url(best_url) else "",
        best_evidence_title=best_title,
        best_evidence_snippet=best_snippet,
        best_evidence_source=best_source,
        best_evidence_confidence=str(best_item.get("confidence") if best_item else ""),
        best_evidence_type=str(best_item.get("evidence_type") if best_item else ""),
        best_evidence_reason=str(best_item.get("reason") if best_item else ""),
        evidence_last_verified_at=evidence_last_verified_at,
        evidence_items=evidence_items,
        search_stats=get_last_search_stats(),
    )
    output = result.to_dict()
    output["amazon_evidence_urls"] = amazon_evidence_urls
    if output["structured_evidence_found"] and not output["best_evidence_url"]:
        output["structured_evidence_found"] = False
    if output["best_evidence_url"] and not is_valid_amazon_url(output["best_evidence_url"]):
        output["structured_evidence_found"] = False
        output["best_evidence_url"] = ""
    _append_debug_record(
        {
            "brand": canonical_brand_name,
            "canonical_brand_name": canonical_brand_name,
            "query": "",
            "provider": "summary",
            "raw_url": "",
            "cleaned_url": "",
            "title": output.get("best_evidence_title", ""),
            "snippet": output.get("best_evidence_snippet", ""),
            "decision": "accepted" if output.get("structured_evidence_found") else "rejected",
            "evidence_type": output.get("best_evidence_type", ""),
            "confidence": output.get("best_evidence_confidence", ""),
            "reason": output.get("best_evidence_reason", "") or ("cached prior verified Amazon evidence" if output.get("best_evidence_type") == "cached_verified_amazon_url" else ""),
        },
    )
    for record in debug_records:
        _append_debug_record(record)
    return output


def evidence_is_structured(item: dict | None) -> bool:
    if not item:
        return False
    if item.get("structured"):
        return True
    evidence_type = str(item.get("evidence_type") or "").strip().lower()
    return evidence_type in STRUCTURED_EVIDENCE_TYPES
