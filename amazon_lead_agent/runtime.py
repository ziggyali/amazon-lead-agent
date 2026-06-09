from __future__ import annotations

from datetime import datetime, timezone
import logging
import json
from pathlib import Path
from typing import Any

from amazon_lead_agent.agents.discovery_agent import run_discovery
from amazon_lead_agent.agents.extraction_agent import run_extraction
from amazon_lead_agent.agents.outreach_agent import run_outreach
from amazon_lead_agent.agents.scoring_agent import run_scoring
from amazon_lead_agent.reporting import write_campaign_report
from amazon_lead_agent.tools.storage_router import get_storage_router


LOGGER = logging.getLogger(__name__)


def run_campaign(config: dict[str, Any], db_path: Path, mode: str = "full", dry_run: bool = False) -> dict[str, Any]:
    storage = get_storage_router(config, db_path)
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
        "provider_blocked_counts": {},
        "queries_attempted_by_provider": {},
        "search_blocked_query_counts": {},
        "search_rate_limited_query_counts": {},
        "rejected_content_domains_count": 0,
        "rejected_content_domain_count": 0,
        "rejected_listicle_domains_count": 0,
        "cleaned_redirect_count": 0,
        "rejected_redirect_count": 0,
        "llm_provider_counts": {},
        "llm_model_counts": {},
        "extraction_method_counts": {},
        "sheet_mirror_error_count": 0,
        "failed_sheet_rows": [],
        "sheet_mirror_status": "enabled" if storage.uses_sheets else "disabled",
        "top_5_leads": [],
    }

    fatal_error: Exception | None = None
    try:
        if mode in {"full", "discover"}:
            discovery_result = run_discovery(config, storage)
            discovered = discovery_result.get("leads", [])
            search_stats = discovery_result.get("search_stats", {})
            report["discovered_count"] = len(discovered)
            report["search_provider_counts"] = search_stats.get("provider_counts", {})
            report["provider_blocked_counts"] = search_stats.get("provider_blocked_counts", search_stats.get("blocked_query_counts", {}))
            report["queries_attempted_by_provider"] = search_stats.get("queries_attempted_by_provider", search_stats.get("provider_counts", {}))
            report["search_blocked_query_counts"] = search_stats.get("blocked_query_counts", {})
            report["search_rate_limited_query_counts"] = search_stats.get("rate_limited_query_counts", {})
            report["rejected_content_domains_count"] = int(search_stats.get("rejected_content_domain_count", 0))
            report["rejected_content_domain_count"] = int(search_stats.get("rejected_content_domain_count", 0))
            report["rejected_listicle_domains_count"] = int(search_stats.get("rejected_listicle_domains_count", 0))
            report["cleaned_redirect_count"] = int(search_stats.get("cleaned_redirect_count", 0))
            report["rejected_redirect_count"] = int(search_stats.get("rejected_redirect_count", 0))

        if mode in {"full", "enrich"}:
            enriched = run_extraction(config, storage)
            report["enriched_count"] = len(enriched)
            for lead in enriched:
                if lead.get("extraction_fallback"):
                    report["extraction_fallback_count"] += 1
                if lead.get("status") in {"extraction_error", "blocked_or_error"}:
                    report["errors"] += 1
                provider = str(lead.get("llm_provider_used", "") or "").strip()
                model = str(lead.get("llm_model_used", "") or "").strip()
                method = str(lead.get("extraction_method", "") or "").strip()
                if provider:
                    report["llm_provider_counts"][provider] = report["llm_provider_counts"].get(provider, 0) + 1
                if model:
                    report["llm_model_counts"][model] = report["llm_model_counts"].get(model, 0) + 1
                if method:
                    report["extraction_method_counts"][method] = report["extraction_method_counts"].get(method, 0) + 1

        if mode in {"full", "score"}:
            scored = run_scoring(config, storage)
            report["scored_count"] = len(scored)
            top_candidates = sorted(scored, key=lambda lead: int(lead.get("score", 0) or 0), reverse=True)[:5]
            report["top_5_leads"] = [
                {
                    "id": lead.get("id", ""),
                    "company_name": lead.get("company_name", ""),
                    "website": lead.get("website", ""),
                    "score": lead.get("score", 0),
                    "tier": lead.get("tier", ""),
                    "extraction_method": lead.get("extraction_method", ""),
                    "status": lead.get("status", ""),
                    "send_status": lead.get("send_status", ""),
                }
                for lead in top_candidates
            ]
            for lead in scored:
                status = lead.get("status", "")
                if status == "approved":
                    report["approved_count"] += 1
                elif status == "contact_form_queue":
                    report["contact_form_queue_count"] += 1
                elif status == "rejected":
                    report["rejected_count"] += 1

        if mode in {"full", "draft"}:
            drafted = run_outreach(config, storage, dry_run=dry_run)
            report["drafts_created"] = len([lead for lead in drafted if lead.get("draft_id")])

    except Exception as exc:  # noqa: BLE001
        fatal_error = exc
        report["run_error"] = str(exc)
        report["errors"] += 1
        LOGGER.exception("campaign run failed: %s", exc)
    finally:
        report["llm_provider_counts"] = report.get("llm_provider_counts", {})
        report["llm_model_counts"] = report.get("llm_model_counts", {})
        report["notes_json"] = json.dumps(
            {
                "mode": mode,
                "dry_run": dry_run,
                "sheet_mirror_status": report["sheet_mirror_status"],
                "sheet_mirror_error_count": report["sheet_mirror_error_count"],
                "failed_sheet_rows": report["failed_sheet_rows"],
                "search_provider_counts": report["search_provider_counts"],
                "provider_blocked_counts": report["provider_blocked_counts"],
                "queries_attempted_by_provider": report["queries_attempted_by_provider"],
                "rejected_content_domain_count": report["rejected_content_domain_count"],
                "rejected_content_domains_count": report["rejected_content_domains_count"],
                "cleaned_redirect_count": report["cleaned_redirect_count"],
                "rejected_redirect_count": report["rejected_redirect_count"],
                "extraction_method_counts": report["extraction_method_counts"],
                "llm_provider_counts": report["llm_provider_counts"],
                "llm_model_counts": report["llm_model_counts"],
                "top_5_leads": report["top_5_leads"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        report_path = write_campaign_report(db_path, summary=report)
        report["campaign_report_path"] = report_path["path"]
        report["top_5_leads"] = report_path["top_leads"]
        try:
            storage.append_daily_report(
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
                    "sheet_mirror_status": report["sheet_mirror_status"],
                    "sheet_mirror_error_count": report["sheet_mirror_error_count"],
                    "failed_sheet_rows": report["failed_sheet_rows"],
                    "search_provider_counts": report["search_provider_counts"],
                    "provider_blocked_counts": report["provider_blocked_counts"],
                    "queries_attempted_by_provider": report["queries_attempted_by_provider"],
                    "rejected_content_domain_count": report["rejected_content_domain_count"],
                    "rejected_content_domains_count": report["rejected_content_domains_count"],
                    "cleaned_redirect_count": report["cleaned_redirect_count"],
                    "rejected_redirect_count": report["rejected_redirect_count"],
                    "extraction_method_counts": report["extraction_method_counts"],
                    "llm_provider_counts": report["llm_provider_counts"],
                    "llm_model_counts": report["llm_model_counts"],
                    "notes": report["notes_json"],
                }
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("daily report mirror failed: %s", exc)
            report["sheet_mirror_error_count"] = int(report.get("sheet_mirror_error_count", 0)) + 1
        snapshot = storage.snapshot()
        report["sheet_mirror_error_count"] = int(snapshot.get("sheet_mirror_error_count", report["sheet_mirror_error_count"]))
        report["failed_sheet_rows"] = snapshot.get("failed_sheet_rows", report["failed_sheet_rows"])
        report["sheet_mirror_status"] = "enabled" if snapshot.get("uses_sheets") else "disabled"
        storage.commit()
        storage.close()
    if fatal_error:
        raise fatal_error
    return report
