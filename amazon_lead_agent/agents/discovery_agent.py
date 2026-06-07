from __future__ import annotations

from pathlib import Path

from amazon_lead_agent.normalization import make_lead_id, normalize_company_name
from amazon_lead_agent.tools.amazon_backlink_discovery import contains_amazon_buying_signal
from amazon_lead_agent.tools.search import discover_candidates
from amazon_lead_agent.tools.sqlite_store import get_connection, upsert_lead, record_outreach_event


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


def run_discovery(config: dict, db_path: Path) -> list[dict]:
    conn = get_connection(db_path)
    discovered: list[dict] = []
    try:
        categories = config["campaign"]["categories"]
        limit = int(config["campaign"]["daily_discovery_limit"])
        results = discover_candidates(categories, limit)
        for result in results:
            lead = _candidate_from_result(result, result.get("category", ""))
            lead_id = upsert_lead(conn, lead)
            record_outreach_event(
                conn,
                {
                    "lead_id": lead_id,
                    "event_type": "discovered",
                    "metadata": {"source_url": lead.get("primary_source_url")},
                },
            )
            discovered.append({**lead, "id": lead_id})
        conn.commit()
        return discovered
    finally:
        conn.close()

