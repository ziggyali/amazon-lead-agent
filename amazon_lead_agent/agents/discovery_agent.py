from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from amazon_lead_agent.lead_filters import is_junk_company_name, is_blocked_domain, is_tracking_or_search_domain, is_likely_brand_domain, is_soft_brand_candidate
from amazon_lead_agent.normalization import make_lead_id, normalize_company_name
from amazon_lead_agent.tools.amazon_backlink_discovery import contains_amazon_buying_signal
from amazon_lead_agent.tools.search import discover_candidates, get_last_search_stats
from amazon_lead_agent.tools.storage_router import StorageRouter, get_storage_router


def _candidate_from_result(result: dict[str, str], category: str) -> dict[str, object]:
    title = result.get("title") or ""
    website = result.get("url") or ""
    company = title.split("|")[0].strip() or website
    source_url = result.get("source_url") or website
    snippet = result.get("snippet") or ""
    return {
        "id": make_lead_id(company, website, source_url),
        "company_name": company,
        "brand_name": company,
        "normalized_company_name": normalize_company_name(company),
        "website": website,
        "category": category,
        "primary_source_url": source_url,
        "source_urls": [source_url] if source_url else [],
        "amazon_evidence_summary": snippet if contains_amazon_buying_signal(snippet) else "",
        "amazon_backlink_found": int(contains_amazon_buying_signal(snippet)),
        "status": "discovered",
    }


def _hard_reject_candidate(lead: dict[str, object]) -> bool:
    company_name = str(lead.get("company_name") or lead.get("brand_name") or "").strip().lower()
    normalized_company = normalize_company_name(company_name)
    if is_junk_company_name(company_name) or normalized_company == "available":
        return True
    website = str(lead.get("website") or "").strip().lower()
    if is_blocked_domain(website) or is_tracking_or_search_domain(website):
        return True
    return False


def _candidate_status(lead: dict[str, object]) -> str:
    website = str(lead.get("website") or "").strip()
    title = str(lead.get("company_name") or lead.get("brand_name") or "")
    snippet = str(lead.get("amazon_evidence_summary") or "")
    category = str(lead.get("category") or "")
    if is_likely_brand_domain(website, title, snippet, category):
        path = urlparse(website).path.lower()
        signal_text = f"{title} {snippet}".lower()
        if any(hint in path for hint in ("/retailers", "/where-to-buy", "/amazon", "/store-locator")):
            return "discovered"
        if any(signal in signal_text for signal in ("official site", "official website", "amazon store", "shop our", "where to buy")):
            return "discovered"
        return "needs_enrichment"
    if is_soft_brand_candidate(website, title, snippet, category):
        return "needs_enrichment"
    return "needs_enrichment"


def _storage(config: dict, storage_or_path: Path | StorageRouter) -> StorageRouter:
    if isinstance(storage_or_path, StorageRouter) or hasattr(storage_or_path, "upsert_lead"):
        return storage_or_path
    return get_storage_router(config, storage_or_path)


def run_discovery(config: dict, db_path: Path | StorageRouter) -> dict:
    storage = _storage(config, db_path)
    discovered: list[dict] = []
    local_stats = {
        "hard_rejected_junk_count": 0,
        "soft_pass_needs_enrichment_count": 0,
        "rejected_due_to_no_amazon_evidence_count": 0,
        "discovered_count_by_category": {},
    }
    try:
        categories = config["campaign"]["categories"]
        limit = int(config["campaign"]["daily_discovery_limit"])
        results = discover_candidates(categories, limit)
        for result in results:
            lead = _candidate_from_result(result, result.get("category", ""))
            if _hard_reject_candidate(lead):
                local_stats["hard_rejected_junk_count"] += 1
                continue
            status = _candidate_status(lead)
            lead["status"] = status
            category = str(lead.get("category") or "").strip().lower()
            if status == "needs_enrichment":
                local_stats["soft_pass_needs_enrichment_count"] += 1
            if status != "discovered" and not contains_amazon_buying_signal(str(lead.get("amazon_evidence_summary") or "")):
                local_stats["rejected_due_to_no_amazon_evidence_count"] += 1
            if category:
                category_counts = local_stats["discovered_count_by_category"]
                category_counts[category] = int(category_counts.get(category, 0)) + 1
            lead_id = storage.upsert_lead(lead, tab="Lead Queue")
            storage.record_outreach_event(
                {
                    "lead_id": lead_id,
                    "event_type": "discovered",
                    "metadata": {"source_url": lead.get("primary_source_url")},
                },
            )
            discovered.append({**lead, "id": lead_id})
        search_stats = get_last_search_stats()
        search_stats.update(local_stats)
        storage.commit()
        return {"leads": discovered, "search_stats": search_stats}
    finally:
        storage.close()

