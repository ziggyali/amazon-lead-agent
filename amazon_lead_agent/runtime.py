from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
import json
from typing import Any

from amazon_lead_agent.agents.discovery_agent import run_discovery
from amazon_lead_agent.agents.extraction_agent import run_extraction
from amazon_lead_agent.agents.outreach_agent import run_outreach
from amazon_lead_agent.agents.scoring_agent import run_scoring
from amazon_lead_agent.reporting import write_campaign_report
from amazon_lead_agent.tools.google_sheets import append_daily_report, append_or_update_lead, append_outreach_log


def _sheet_id(config: dict[str, Any]) -> str:
    storage_sheet = config.get("storage", {}).get("google_sheet_id", "")
    if storage_sheet and storage_sheet != "REPLACE_ME":
        return storage_sheet
    return ""


def _mirror_lead(sheet_id: str, tab: str, lead: dict) -> None:
    if not sheet_id:
        return
    append_or_update_lead(sheet_id, tab, lead)


def _mirror_outreach(sheet_id: str, event: dict) -> None:
    if not sheet_id:
        return
    append_outreach_log(sheet_id, event)


def _mirror_daily_report(sheet_id: str, report: dict) -> None:
    if not sheet_id:
        return
    append_daily_report(sheet_id, report)


def run_campaign(config: dict[str, Any], db_path: Path, mode: str = "full", dry_run: bool = False) -> dict[str, Any]:
    sheet_id = _sheet_id(config)
    report: dict[str, Any] = {
        "mode": mode,
        "dry_run": dry_run,
        "discovered_count": 0,
        "enriched_count": 0,
        "scored_count": 0,
        "approved_count": 0,
        "rejected_count": 0,
        "drafts_created": 0,
        "contact_form_queue_count": 0,
        "extraction_fallback_count": 0,
        "errors": 0,
        "search_provider_counts": {},
        "search_blocked_query_counts": {},
        "search_rate_limited_query_counts": {},
        "rejected_content_domains_count": 0,
        "rejected_listicle_domains_count": 0,
        "llm_provider_counts": {},
        "llm_model_counts": {},
        "sheet_mirror_status": "disabled" if not sheet_id else "enabled",
        "top_5_leads": [],
    }

    if mode in {"full", "discover"}:
        discovery_result = run_discovery(config, db_path)
        discovered = discovery_result.get("leads", [])
        search_stats = discovery_result.get("search_stats", {})
        report["discovered_count"] = len(discovered)
        report["search_provider_counts"] = search_stats.get("provider_counts", {})
        report["search_blocked_query_counts"] = search_stats.get("blocked_query_counts", {})
        report["search_rate_limited_query_counts"] = search_stats.get("rate_limited_query_counts", {})
        report["rejected_content_domains_count"] = int(search_stats.get("rejected_content_domains_count", 0))
        report["rejected_listicle_domains_count"] = int(search_stats.get("rejected_listicle_domains_count", 0))
        for lead in discovered:
            _mirror_lead(sheet_id, "Lead Queue", lead)
            _mirror_outreach(sheet_id, {"lead_id": lead["id"], "event_type": "discovered", "metadata": {"status": lead.get("status", "")}})

    if mode in {"full", "enrich"}:
        enriched = run_extraction(config, db_path)
        report["enriched_count"] = len(enriched)
        for lead in enriched:
            _mirror_lead(sheet_id, "Lead Queue", lead)
            if lead.get("extraction_fallback"):
                report["extraction_fallback_count"] += 1
                _mirror_outreach(sheet_id, {"lead_id": lead.get("id", ""), "event_type": "extraction_fallback", "metadata": {"method": lead.get("extraction_method", "")}})
            if lead.get("status") in {"extraction_error", "blocked_or_error"}:
                report["errors"] += 1
                _mirror_outreach(sheet_id, {"lead_id": lead.get("id", ""), "event_type": "error", "metadata": {"status": lead.get("status", "")}})
            provider = str(lead.get("llm_provider_used", "") or "").strip()
            model = str(lead.get("llm_model_used", "") or "").strip()
            if provider:
                report["llm_provider_counts"][provider] = report["llm_provider_counts"].get(provider, 0) + 1
            if model:
                report["llm_model_counts"][model] = report["llm_model_counts"].get(model, 0) + 1

    if mode in {"full", "score"}:
        scored = run_scoring(config, db_path)
        report["scored_count"] = len(scored)
        min_score = int(config["campaign"]["minimum_score_for_draft"])
        for lead in scored:
            status = lead.get("status", "")
            if status == "approved":
                report["approved_count"] += 1
                _mirror_lead(sheet_id, "Approved Leads", lead)
            elif status == "contact_form_queue":
                report["contact_form_queue_count"] += 1
                _mirror_lead(sheet_id, "Contact Form Queue", lead)
            elif status == "rejected":
                report["rejected_count"] += 1
                _mirror_lead(sheet_id, "Rejected Leads", lead)
            else:
                _mirror_lead(sheet_id, "Lead Queue", lead)
            if lead.get("score", 0) >= min_score and status not in {"rejected"}:
                _mirror_outreach(sheet_id, {"lead_id": lead.get("id", ""), "event_type": status or "scored", "metadata": {"score": lead.get("score", 0), "tier": lead.get("tier", "")}})

    if mode in {"full", "draft"}:
        drafted = run_outreach(config, db_path, dry_run=dry_run)
        report["drafts_created"] = len([lead for lead in drafted if lead.get("draft_id")])

    report_path = write_campaign_report(db_path)
    report["campaign_report_path"] = report_path["path"]
    report["top_5_leads"] = report_path["top_leads"]
    report["notes_json"] = json.dumps(
        {
            "mode": mode,
            "dry_run": dry_run,
            "sheet_mirror_status": report["sheet_mirror_status"],
            "search_provider_counts": report["search_provider_counts"],
            "search_blocked_query_counts": report["search_blocked_query_counts"],
            "search_rate_limited_query_counts": report["search_rate_limited_query_counts"],
            "rejected_content_domains_count": report["rejected_content_domains_count"],
            "rejected_listicle_domains_count": report["rejected_listicle_domains_count"],
            "extraction_fallback_count": report["extraction_fallback_count"],
            "llm_provider_counts": report["llm_provider_counts"],
            "llm_model_counts": report["llm_model_counts"],
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    _mirror_daily_report(
        sheet_id,
        {
            "report_date": datetime.now(timezone.utc).date().isoformat(),
            "campaign": "Amazon Lead Agent",
            "discovered_count": report["discovered_count"],
            "enriched_count": report["enriched_count"],
            "scored_count": report["scored_count"],
            "scoring_count": report["scored_count"],
            "approved_count": report["approved_count"],
            "rejected_count": report["rejected_count"],
            "drafts_created": report["drafts_created"],
            "draft_count": report["drafts_created"],
            "contact_form_queue_count": report["contact_form_queue_count"],
            "extraction_fallback_count": report["extraction_fallback_count"],
            "errors": report["errors"],
            "notes": report["notes_json"],
        },
    )
    return report
