from __future__ import annotations

from pathlib import Path

from amazon_lead_agent.tools.scrapegraph_runner import extract_brand_profile
from amazon_lead_agent.tools.storage_router import StorageRouter, get_storage_router


def _storage(config: dict, storage_or_path: Path | StorageRouter) -> StorageRouter:
    if isinstance(storage_or_path, StorageRouter) or hasattr(storage_or_path, "upsert_lead"):
        return storage_or_path
    return get_storage_router(config, storage_or_path)


def run_extraction(config: dict, db_path: Path | StorageRouter) -> list[dict]:
    storage = _storage(config, db_path)
    enriched: list[dict] = []
    try:
        candidates = storage.get_leads_for_enrichment(int(config["campaign"]["daily_discovery_limit"]))
        minimax_key = __import__("os").environ.get("MINIMAX_API_KEY", "")
        for lead in candidates:
            try:
                profile = extract_brand_profile(lead.get("website", ""), minimax_key, config.get("llm", {}))
                merged = {
                    **lead,
                    **profile,
                    "status": "enriched",
                    "extraction_fallback": int(profile.get("extraction_method") != "scrapegraphai_other"),
                    "blocked_or_error": int(profile.get("extraction_method") == "blocked_or_error"),
                }
                storage.upsert_lead(merged, tab="Lead Queue")
                storage.record_outreach_event({"lead_id": lead["id"], "event_type": "enriched", "metadata": profile})
                if profile.get("extraction_method") and profile.get("extraction_method") != "scrapegraphai_other":
                    storage.record_outreach_event(
                        {
                            "lead_id": lead["id"],
                            "event_type": "extraction_fallback",
                            "metadata": {"method": profile.get("extraction_method"), "notes": profile.get("notes", "")},
                        },
                    )
                enriched.append(merged)
            except Exception as exc:  # noqa: BLE001
                merged = {**lead, "status": "extraction_error", "blocked_or_error": 1, "notes": str(exc), "extraction_method": "blocked_or_error"}
                storage.upsert_lead(merged, tab="Lead Queue")
                storage.record_outreach_event({"lead_id": lead["id"], "event_type": "error", "metadata": {"error": str(exc)}})
                enriched.append(merged)
        storage.commit()
        return enriched
    finally:
        storage.close()

