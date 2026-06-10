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


def _storage_mode_label(storage: Any) -> str:
    mode = str(getattr(storage, "mode", "") or "").strip().lower()
    if mode:
        return mode
    if bool(getattr(storage, "uses_sheets", False)):
        return "sheets"
    if bool(getattr(storage, "uses_sqlite", False)):
        return "sqlite"
    return ""


def _safe_commit(storage: Any, report: dict[str, Any], *, label: str) -> str:
    try:
        storage.commit()
        return "ok"
    except Exception as exc:  # noqa: BLE001
        report["errors"] += 1
        LOGGER.warning("%s storage flush failed: %s", label, exc)
        return f"failed: {exc}"


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
        "rejected_content_domains_count": 0,
        "rejected_content_domain_count": 0,
        "rejected_listicle_domains_count": 0,
        "hard_rejected_junk_count": 0,
        "soft_pass_needs_enrichment_count": 0,
        "rejected_likely_brand_filter_count": 0,
        "rejected_due_to_no_amazon_evidence_count": 0,
        "discovered_count_by_category": {},
        "cleaned_redirect_count": 0,
        "rejected_redirect_count": 0,
        "discovered_persisted_count": 0,
        "discovered_persist_failed_count": 0,
        "lead_queue_rows_queued": 0,
        "lead_queue_rows_attempted": 0,
        "lead_queue_rows_written": 0,
        "lead_queue_rows_failed": 0,
        "lead_queue_verified_count": 0,
        "lead_queue_missing_after_write": 0,
        "lead_queue_verification_status": "not_attempted",
        "dedupe_cache_unavailable": False,
        "storage_flush_status": "pending",
        "storage_mode_used": _storage_mode_label(storage),
        "llm_provider_counts": {},
        "llm_model_counts": {},
        "extraction_method_counts": {},
        "sheet_mirror_error_count": 0,
        "sheet_read_error_count": 0,
        "sheet_read_retry_count": 0,
        "sheet_connection_error_count": 0,
        "failed_sheet_rows": [],
        "failed_sheet_reads": [],
        "sheet_flush_errors": [],
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
            report["storage_mode_used"] = _storage_mode_label(storage) or report["storage_mode_used"]
            report["search_provider_counts"] = search_stats.get("provider_counts", {})
            report["provider_blocked_counts"] = search_stats.get("provider_blocked_counts", search_stats.get("blocked_query_counts", {}))
            report["queries_attempted_by_provider"] = search_stats.get("queries_attempted_by_provider", search_stats.get("provider_counts", {}))
            report["search_blocked_query_counts"] = search_stats.get("blocked_query_counts", {})
            report["search_rate_limited_query_counts"] = search_stats.get("rate_limited_query_counts", {})
            report["query_budget_used"] = int(search_stats.get("query_budget_used", 0))
            report["query_budget_remaining"] = int(search_stats.get("query_budget_remaining", 0))
            report["discovery_runtime_seconds"] = float(search_stats.get("discovery_runtime_seconds", 0.0))
            report["stopped_reason"] = str(search_stats.get("stopped_reason", ""))
            report["seed_lines_processed"] = int(search_stats.get("seed_lines_processed", 0))
            report["seed_pages_fetched"] = int(search_stats.get("seed_pages_fetched", 0))
            report["seed_brand_domains_extracted"] = int(search_stats.get("seed_brand_domains_extracted", 0))
            report["direct_seed_candidates"] = int(search_stats.get("direct_seed_candidates", 0))
            report["seed_candidates_accepted"] = int(search_stats.get("seed_candidates_accepted", 0))
            report["seed_candidates_rejected"] = int(search_stats.get("seed_candidates_rejected", 0))
            report["rejected_content_domains_count"] = int(search_stats.get("rejected_content_domain_count", 0))
            report["rejected_content_domain_count"] = int(search_stats.get("rejected_content_domain_count", 0))
            report["rejected_listicle_domains_count"] = int(search_stats.get("rejected_listicle_domains_count", 0))
            report["hard_rejected_junk_count"] = int(search_stats.get("hard_rejected_junk_count", 0))
            report["soft_pass_needs_enrichment_count"] = int(search_stats.get("soft_pass_needs_enrichment_count", 0))
            report["rejected_likely_brand_filter_count"] = int(search_stats.get("rejected_likely_brand_filter_count", 0))
            report["rejected_due_to_no_amazon_evidence_count"] = int(search_stats.get("rejected_due_to_no_amazon_evidence_count", 0))
            report["discovered_count_by_category"] = search_stats.get("discovered_count_by_category", {})
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
        pre_report_flush_status = report["storage_flush_status"]
        if report["storage_flush_status"] == "pending":
            report["storage_flush_status"] = _safe_commit(storage, report, label="pre_report")
        snapshot = storage.snapshot()
        sheet_store_snapshot = snapshot.get("sheet_store", {})
        if not isinstance(sheet_store_snapshot, dict):
            sheet_store_snapshot = {}
        report["sheet_mirror_error_count"] = int(snapshot.get("sheet_mirror_error_count", report["sheet_mirror_error_count"]))
        report["failed_sheet_rows"] = snapshot.get("failed_sheet_rows", report["failed_sheet_rows"])
        report["sheet_read_error_count"] = int(snapshot.get("sheet_read_error_count", report["sheet_read_error_count"]))
        report["sheet_read_retry_count"] = int(snapshot.get("sheet_read_retry_count", report["sheet_read_retry_count"]))
        report["sheet_connection_error_count"] = int(snapshot.get("sheet_connection_error_count", report["sheet_connection_error_count"]))
        report["failed_sheet_reads"] = snapshot.get("failed_sheet_reads", report["failed_sheet_reads"])
        report["sheet_flush_errors"] = snapshot.get("sheet_flush_errors", report["sheet_flush_errors"])
        report["sheet_mirror_status"] = "enabled" if snapshot.get("uses_sheets") else "disabled"
        report["lead_queue_rows_queued"] = int(sheet_store_snapshot.get("lead_queue_rows_queued", report["lead_queue_rows_queued"]))
        report["lead_queue_rows_attempted"] = int(sheet_store_snapshot.get("lead_queue_rows_attempted", report["lead_queue_rows_attempted"]))
        report["lead_queue_rows_written"] = int(sheet_store_snapshot.get("lead_queue_rows_written", report["lead_queue_rows_written"]))
        report["lead_queue_rows_failed"] = int(sheet_store_snapshot.get("lead_queue_rows_failed", report["lead_queue_rows_failed"]))
        report["lead_queue_verified_count"] = int(sheet_store_snapshot.get("lead_queue_verified_count", report["lead_queue_verified_count"]))
        report["lead_queue_missing_after_write"] = int(sheet_store_snapshot.get("lead_queue_missing_after_write", report["lead_queue_missing_after_write"]))
        report["lead_queue_verification_status"] = str(sheet_store_snapshot.get("lead_queue_verification_status", report["lead_queue_verification_status"]))
        report["dedupe_cache_unavailable"] = bool(sheet_store_snapshot.get("dedupe_cache_unavailable", report["dedupe_cache_unavailable"]))
        report["discovered_persisted_count"] = report["lead_queue_rows_written"]
        report["discovered_persist_failed_count"] = report["lead_queue_rows_failed"]
        sheet_flush_status = str(sheet_store_snapshot.get("storage_flush_status", "") or "").strip()
        if sheet_flush_status:
            report["storage_flush_status"] = sheet_flush_status
        elif report["storage_flush_status"] == "pending":
            report["storage_flush_status"] = pre_report_flush_status
        report["llm_provider_counts"] = report.get("llm_provider_counts", {})
        report["llm_model_counts"] = report.get("llm_model_counts", {})
        report["notes_json"] = json.dumps(
            {
                "mode": mode,
                "dry_run": dry_run,
                "sheet_mirror_status": report["sheet_mirror_status"],
                "sheet_mirror_error_count": report["sheet_mirror_error_count"],
                "sheet_read_error_count": report["sheet_read_error_count"],
                "sheet_read_retry_count": report["sheet_read_retry_count"],
                "sheet_connection_error_count": report["sheet_connection_error_count"],
                "failed_sheet_rows": report["failed_sheet_rows"],
                "failed_sheet_reads": report["failed_sheet_reads"],
                "sheet_flush_errors": report["sheet_flush_errors"],
                "lead_queue_rows_queued": report["lead_queue_rows_queued"],
                "lead_queue_rows_attempted": report["lead_queue_rows_attempted"],
                "lead_queue_rows_written": report["lead_queue_rows_written"],
                "lead_queue_rows_failed": report["lead_queue_rows_failed"],
                "lead_queue_verified_count": report["lead_queue_verified_count"],
                "lead_queue_missing_after_write": report["lead_queue_missing_after_write"],
                "lead_queue_verification_status": report["lead_queue_verification_status"],
                "dedupe_cache_unavailable": report["dedupe_cache_unavailable"],
                "search_provider_counts": report["search_provider_counts"],
                "provider_blocked_counts": report["provider_blocked_counts"],
                "queries_attempted_by_provider": report["queries_attempted_by_provider"],
                "query_budget_used": report["query_budget_used"],
                "query_budget_remaining": report["query_budget_remaining"],
                "discovery_runtime_seconds": report["discovery_runtime_seconds"],
                "stopped_reason": report["stopped_reason"],
                "seed_lines_processed": report["seed_lines_processed"],
                "seed_pages_fetched": report["seed_pages_fetched"],
                "seed_brand_domains_extracted": report["seed_brand_domains_extracted"],
                "direct_seed_candidates": report["direct_seed_candidates"],
                "seed_candidates_accepted": report["seed_candidates_accepted"],
                "seed_candidates_rejected": report["seed_candidates_rejected"],
                "rejected_content_domain_count": report["rejected_content_domain_count"],
                "rejected_content_domains_count": report["rejected_content_domains_count"],
                "hard_rejected_junk_count": report["hard_rejected_junk_count"],
                "soft_pass_needs_enrichment_count": report["soft_pass_needs_enrichment_count"],
                "rejected_likely_brand_filter_count": report["rejected_likely_brand_filter_count"],
                "rejected_due_to_no_amazon_evidence_count": report["rejected_due_to_no_amazon_evidence_count"],
                "discovered_count_by_category": report["discovered_count_by_category"],
                "cleaned_redirect_count": report["cleaned_redirect_count"],
                "rejected_redirect_count": report["rejected_redirect_count"],
                "discovered_persisted_count": report["discovered_persisted_count"],
                "discovered_persist_failed_count": report["discovered_persist_failed_count"],
                "lead_queue_rows_queued": report["lead_queue_rows_queued"],
                "lead_queue_rows_attempted": report["lead_queue_rows_attempted"],
                "lead_queue_rows_written": report["lead_queue_rows_written"],
                "lead_queue_rows_failed": report["lead_queue_rows_failed"],
                "lead_queue_verified_count": report["lead_queue_verified_count"],
                "lead_queue_missing_after_write": report["lead_queue_missing_after_write"],
                "lead_queue_verification_status": report["lead_queue_verification_status"],
                "dedupe_cache_unavailable": report["dedupe_cache_unavailable"],
                "storage_flush_status": report["storage_flush_status"],
                "storage_mode_used": report["storage_mode_used"],
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
                    "sheet_read_error_count": report["sheet_read_error_count"],
                    "sheet_read_retry_count": report["sheet_read_retry_count"],
                    "sheet_connection_error_count": report["sheet_connection_error_count"],
                    "failed_sheet_rows": report["failed_sheet_rows"],
                    "failed_sheet_reads": report["failed_sheet_reads"],
                    "sheet_flush_errors": report["sheet_flush_errors"],
                    "search_provider_counts": report["search_provider_counts"],
                    "provider_blocked_counts": report["provider_blocked_counts"],
                    "queries_attempted_by_provider": report["queries_attempted_by_provider"],
                    "query_budget_used": report["query_budget_used"],
                    "query_budget_remaining": report["query_budget_remaining"],
                    "discovery_runtime_seconds": report["discovery_runtime_seconds"],
                    "stopped_reason": report["stopped_reason"],
                    "seed_lines_processed": report["seed_lines_processed"],
                    "seed_pages_fetched": report["seed_pages_fetched"],
                    "seed_brand_domains_extracted": report["seed_brand_domains_extracted"],
                    "direct_seed_candidates": report["direct_seed_candidates"],
                    "seed_candidates_accepted": report["seed_candidates_accepted"],
                    "seed_candidates_rejected": report["seed_candidates_rejected"],
                    "rejected_content_domain_count": report["rejected_content_domain_count"],
                    "rejected_content_domains_count": report["rejected_content_domains_count"],
                    "hard_rejected_junk_count": report["hard_rejected_junk_count"],
                    "soft_pass_needs_enrichment_count": report["soft_pass_needs_enrichment_count"],
                    "rejected_likely_brand_filter_count": report["rejected_likely_brand_filter_count"],
                    "rejected_due_to_no_amazon_evidence_count": report["rejected_due_to_no_amazon_evidence_count"],
                    "discovered_count_by_category": report["discovered_count_by_category"],
                    "cleaned_redirect_count": report["cleaned_redirect_count"],
                    "rejected_redirect_count": report["rejected_redirect_count"],
                    "discovered_persisted_count": report["discovered_persisted_count"],
                    "discovered_persist_failed_count": report["discovered_persist_failed_count"],
                    "lead_queue_rows_queued": report["lead_queue_rows_queued"],
                    "lead_queue_rows_attempted": report["lead_queue_rows_attempted"],
                    "lead_queue_rows_written": report["lead_queue_rows_written"],
                    "lead_queue_rows_failed": report["lead_queue_rows_failed"],
                    "lead_queue_verified_count": report["lead_queue_verified_count"],
                    "lead_queue_missing_after_write": report["lead_queue_missing_after_write"],
                    "lead_queue_verification_status": report["lead_queue_verification_status"],
                    "dedupe_cache_unavailable": report["dedupe_cache_unavailable"],
                    "storage_flush_status": report["storage_flush_status"],
                    "storage_mode_used": report["storage_mode_used"],
                    "extraction_method_counts": report["extraction_method_counts"],
                    "llm_provider_counts": report["llm_provider_counts"],
                    "llm_model_counts": report["llm_model_counts"],
                    "notes": report["notes_json"],
                }
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("daily report mirror failed: %s", exc)
            report["sheet_mirror_error_count"] = int(report.get("sheet_mirror_error_count", 0)) + 1
        _safe_commit(storage, report, label="final")
        storage.close()
    if fatal_error:
        raise fatal_error
    return report
