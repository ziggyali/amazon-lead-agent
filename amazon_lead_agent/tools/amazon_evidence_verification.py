from __future__ import annotations

from dataclasses import dataclass
from html import unescape
from urllib.parse import urlparse, urlunparse
import re

from amazon_lead_agent.normalization import infer_brand_name_from_domain, normalize_company_name, normalize_domain
from amazon_lead_agent.tools.amazon_backlink_discovery import is_valid_amazon_url
from amazon_lead_agent.tools.search import clean_search_result_url, get_last_search_stats, search_web


STRUCTURED_EVIDENCE_TYPES = {
    "official_site_amazon_backlink",
    "amazon_storefront_search_result",
    "amazon_product_search_result",
    "amazon_brand_page_search_result",
    "manual_verified_amazon_url",
}

WEAK_EVIDENCE_TYPES = {"weak_text_signal"}


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
    if not url:
        return ""
    cleaned = clean_search_result_url(url) or url
    parsed = urlparse(cleaned)
    if not parsed.scheme or not parsed.netloc:
        return ""
    if not is_valid_amazon_url(cleaned):
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
    best_evidence_confidence: str
    best_evidence_type: str
    best_evidence_reason: str
    evidence_items: list[dict]
    search_stats: dict

    def to_dict(self) -> dict:
        return {
            "canonical_brand_name": self.canonical_brand_name,
            "website_title": self.website_title,
            "amazon_queries_run": list(self.amazon_queries_run),
            "amazon_search_results_seen": self.amazon_search_results_seen,
            "amazon_results_rejected_count": self.amazon_results_rejected_count,
            "amazon_results_rejected_reasons": list(self.amazon_results_rejected_reasons),
            "structured_evidence_found": self.structured_evidence_found,
            "weak_text_signal_found": self.weak_text_signal_found,
            "best_evidence_url": self.best_evidence_url,
            "best_evidence_confidence": self.best_evidence_confidence,
            "best_evidence_type": self.best_evidence_type,
            "best_evidence_reason": self.best_evidence_reason,
            "amazon_evidence_items": list(self.evidence_items),
            "amazon_backlink_found": self.structured_evidence_found,
            "amazon_evidence_summary": self._summary(),
            "search_stats": self.search_stats,
        }

    def _summary(self) -> str:
        if self.best_evidence_url:
            return f"{self.best_evidence_type}: {self.best_evidence_url}"
        if self.weak_text_signal_found:
            return "weak Amazon text signal only"
        return ""


def _manual_override_item(lead: dict, canonical_brand_name: str) -> dict | None:
    manual_url = lead.get("manual_amazon_evidence_url") or ""
    if not is_valid_amazon_url(manual_url):
        return None
    return {
        "evidence_type": "manual_verified_amazon_url",
        "evidence_url": _normalize_amazon_url(manual_url),
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
        items.append(
            {
                "evidence_type": "official_site_amazon_backlink",
                "evidence_url": normalized,
                "evidence_title": lead.get("website_title", ""),
                "evidence_snippet": lead.get("amazon_evidence_summary", ""),
                "evidence_source": lead.get("website") or lead.get("source_url") or "official_site",
                "confidence": "high",
                "reason": f"official website backlink for {canonical_brand_name}",
                "structured": True,
            }
        )
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
    raw_url = result.get("url") or ""
    title = str(result.get("title") or "")
    snippet = str(result.get("snippet") or "")
    cleaned_url = _normalize_amazon_url(raw_url)
    evidence_type, confidence, reason = _evidence_type_from_url(cleaned_url or raw_url, title, snippet) if cleaned_url else ("weak_text_signal", "low", "Non-Amazon or unverified URL")
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
        "rejected": False,
        "text_match": text_match,
    }


def verify_amazon_evidence(lead: dict, search_limit: int = 8) -> dict:
    canonical_brand_name = str(lead.get("canonical_brand_name") or lead.get("seed_label") or lead.get("brand_name") or lead.get("company_name") or "").strip()
    website = str(lead.get("website") or "").strip()
    root_domain = normalize_domain(website)
    website_title = str(lead.get("website_title") or lead.get("title") or "").strip()
    if not canonical_brand_name:
        canonical_brand_name = infer_brand_name_from_domain(root_domain or website)
    queries = _brand_search_queries(canonical_brand_name, root_domain)
    evidence_items: list[dict] = []
    rejected_reasons: list[str] = []
    if manual := _manual_override_item(lead, canonical_brand_name):
        evidence_items.append(manual)
    evidence_items.extend(_official_site_backlink_items(lead, canonical_brand_name))

    search_results_seen = 0
    for query in queries:
        results = search_web(query, limit=search_limit)
        search_results_seen += len(results)
        for result in results:
            item = _classify_search_result(canonical_brand_name, result, query)
            if item.get("rejected"):
                if item.get("rejected_reason"):
                    rejected_reasons.append(str(item["rejected_reason"]))
                continue
            if item.get("structured"):
                evidence_items.append(item)
            else:
                evidence_items.append(item)

    structured_items = [item for item in evidence_items if item.get("structured")]
    weak_items = [item for item in evidence_items if item.get("evidence_type") == "weak_text_signal"]
    best_item = None
    best_rank = (-1, -1)
    confidence_rank = {"high": 3, "medium": 2, "low": 1}
    for item in evidence_items:
        if not item.get("structured"):
            continue
        rank = (confidence_rank.get(str(item.get("confidence")), 0), 1 if item.get("evidence_type") == "official_site_amazon_backlink" else 0)
        if rank > best_rank:
            best_rank = rank
            best_item = item
    if best_item is None and weak_items:
        best_item = weak_items[0]

    amazon_queries_run = list(queries)
    rejected_count = max(0, search_results_seen - len(structured_items) - len(weak_items))
    result = AmazonEvidenceVerificationResult(
        canonical_brand_name=canonical_brand_name,
        website_title=website_title,
        amazon_queries_run=amazon_queries_run,
        amazon_search_results_seen=search_results_seen,
        amazon_results_rejected_count=rejected_count,
        amazon_results_rejected_reasons=sorted(set(rejected_reasons)),
        structured_evidence_found=bool(structured_items),
        weak_text_signal_found=bool(weak_items),
        best_evidence_url=str(best_item.get("evidence_url") if best_item else ""),
        best_evidence_confidence=str(best_item.get("confidence") if best_item else ""),
        best_evidence_type=str(best_item.get("evidence_type") if best_item else ""),
        best_evidence_reason=str(best_item.get("reason") if best_item else ""),
        evidence_items=evidence_items,
        search_stats=get_last_search_stats(),
    )
    return result.to_dict()


def evidence_is_structured(item: dict | None) -> bool:
    if not item:
        return False
    if item.get("structured"):
        return True
    evidence_type = str(item.get("evidence_type") or "").strip().lower()
    return evidence_type in STRUCTURED_EVIDENCE_TYPES

