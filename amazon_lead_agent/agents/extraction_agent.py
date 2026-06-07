from __future__ import annotations

from pathlib import Path

from amazon_lead_agent.tools.scrapegraph_runner import extract_brand_profile
from amazon_lead_agent.tools.sqlite_store import get_connection, get_leads_for_enrichment, upsert_lead, record_outreach_event


def run_extraction(config: dict, db_path: Path) -> list[dict]:
    conn = get_connection(db_path)
    enriched: list[dict] = []
    try:
        candidates = get_leads_for_enrichment(conn, int(config["campaign"]["daily_discovery_limit"]))
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
                upsert_lead(conn, merged)
                record_outreach_event(conn, {"lead_id": lead["id"], "event_type": "enriched", "metadata": profile})
                if profile.get("extraction_method") and profile.get("extraction_method") != "scrapegraphai_other":
                    record_outreach_event(
                        conn,
                        {
                            "lead_id": lead["id"],
                            "event_type": "extraction_fallback",
                            "metadata": {"method": profile.get("extraction_method"), "notes": profile.get("notes", "")},
                        },
                    )
                enriched.append(merged)
            except Exception as exc:  # noqa: BLE001
                merged = {**lead, "status": "extraction_error", "blocked_or_error": 1, "notes": str(exc), "extraction_method": "blocked_or_error"}
                upsert_lead(conn, merged)
                record_outreach_event(conn, {"lead_id": lead["id"], "event_type": "error", "metadata": {"error": str(exc)}})
                enriched.append(merged)
        conn.commit()
        return enriched
    finally:
        conn.close()

