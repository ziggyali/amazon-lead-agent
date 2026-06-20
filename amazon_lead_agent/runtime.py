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
from amazon_lead_agent.agents.scoring_agent import classify_scored_lead, score_lead
from amazon_lead_agent.tools.amazon_backlink_discovery import has_verified_amazon_evidence
from amazon_lead_agent.reporting import write_campaign_report
from amazon_lead_agent.tools.scrapegraph_runner import extract_brand_profile
from amazon_lead_agent.tools.storage_router import get_storage_router
from amazon_lead_agent.normalization import ensure_lead_identity


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


def _tracer_draft_body(lead: dict[str, Any], evidence_url: str) -> str:
    brand = lead.get("brand_name") or lead.get("company_name") or "your team"
    category = lead.get("category") or "your category"
    amazon_summary = lead.get("amazon_evidence_summary") or "a public Amazon presence"
    contact_path = lead.get("contact_page_url") or lead.get("contact_url") or ""
    pains = ", ".join((lead.get("pain_points") or [])[:3]) or "Amazon operations"
    offer = lead.get("sender_offer") or "We take the messy, time-consuming operational work off your plate and keep your Amazon account clean, compliant, and conversion-ready."
    parts = [
        f"Hi {brand} team,",
        "",
        f"I found a public Amazon signal on your site: {amazon_summary}.",
        f"Evidence: {evidence_url}",
        f"That makes me think there may be an opportunity around {pains} for your {category} brand.",
    ]
    if contact_path:
        parts.append(f"I also found a public contact path here: {contact_path}.")
    parts.extend(
        [
            "",
            offer,
            "",
            "If I am off base, feel free to ignore this.",
            "",
            "Best,",
            lead.get("sender_name") or "Zaigham Ali",
        ]
    )
    return "\n".join(parts)


def _tracer_contact_fields(lead: dict[str, Any]) -> dict[str, Any]:
    public_emails = lead.get("public_emails") or []
    contact_page_url = lead.get("contact_page_url") or ""
    names = lead.get("founder_or_executive_names") or lead.get("ecommerce_or_marketplace_people") or []
    decision_maker_name = names[0] if names else ""
    title = lead.get("decision_maker_title") or ""
    if public_emails:
        contact_method = "public_email"
        contact_url = contact_page_url or ""
        contact_email = public_emails[0]
    elif contact_page_url:
        contact_method = "contact_page"
        contact_url = contact_page_url
        contact_email = ""
    elif decision_maker_name:
        contact_method = "team_or_about"
        contact_url = lead.get("decision_maker_source_url") or lead.get("website") or ""
        contact_email = ""
    else:
        contact_method = "unknown"
        contact_url = ""
        contact_email = ""
    return {
        "contact_method": contact_method,
        "contact_url": contact_url,
        "contact_email": contact_email,
        "decision_maker_name": decision_maker_name,
        "decision_maker_title": title,
        "confidence": lead.get("confidence", 0),
    }


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def _write_tracer_summary(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Tracer Bullet Summary",
        "",
        f"- brands processed: {report.get('tracer_brands_processed', 0)}",
        f"- brands with verified Amazon evidence: {report.get('tracer_brands_with_verified_amazon_evidence', 0)}",
        f"- brands without evidence: {report.get('tracer_brands_without_evidence', 0)}",
        f"- drafts generated: {report.get('tracer_drafts_generated', 0)}",
        f"- drafts blocked due missing evidence: {report.get('tracer_drafts_blocked_due_missing_evidence', 0)}",
        f"- usable contact paths: {report.get('tracer_contact_paths_found', 0)}",
        f"- decision makers found: {report.get('tracer_decision_makers_found', 0)}",
        f"- LLM providers used: {', '.join(report.get('llm_providers_used', [])) or 'none'}",
        f"- errors: {report.get('errors', 0)}",
        "",
        "## Manual Review Rubric",
        "",
        "| Brand | Evidence Accuracy | Contact Usefulness | Draft Quality | Sendable? |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in report.get("manual_review_table", []):
        lines.append(
            f"| {item.get('brand_name', '')} | {item.get('evidence_accuracy', '')} | {item.get('contact_usefulness', '')} | {item.get('draft_quality', '')} | {item.get('sendable', '')} |",
        )
    lines.extend(
        [
            "",
            "## Evidence Details",
            "",
            "| Brand | Evidence URL(s) | Contact Path | Decision Maker | Draft Preview |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for item in report.get("tracer_results", []):
        lines.append(
            f"| {item.get('brand_name', '')} | {', '.join(item.get('amazon_evidence_urls', [])) or 'none'} | {item.get('contact_url', '') or 'none'} | {item.get('decision_maker_name', '') or 'none'} | {item.get('draft_preview_subject', '') or 'blocked'} |",
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_tracer_bullet(config: dict[str, Any], db_path: Path, dry_run: bool = True) -> dict[str, Any]:
    storage = get_storage_router(config, db_path)
    tracer_dir = Path("logs")
    tracer_jsonl = tracer_dir / "tracer_bullet_report.jsonl"
    tracer_summary = tracer_dir / "tracer_bullet_summary.md"
    report: dict[str, Any] = {
        "mode": "tracer",
        "dry_run": dry_run,
        "brands_processed": 0,
        "tracer_brands_processed": 0,
        "tracer_brands_with_verified_amazon_evidence": 0,
        "tracer_brands_without_evidence": 0,
        "tracer_contact_paths_found": 0,
        "tracer_decision_makers_found": 0,
        "tracer_drafts_generated": 0,
        "tracer_drafts_blocked_due_missing_evidence": 0,
        "tracer_llm_attempted_providers": [],
        "llm_providers_used": [],
        "storage_flush_status": "pending",
        "errors": 0,
        "manual_review_table": [],
        "tracer_results": [],
        "tracer_report_path": str(tracer_jsonl),
        "tracer_summary_path": str(tracer_summary),
    }
    allowed_categories = {str(item).strip().lower() for item in config.get("campaign", {}).get("categories", []) if str(item).strip()}
    pending_updates: list[dict[str, Any]] = []
    try:
        candidates = storage.get_leads_for_enrichment(8)
        for lead in candidates[:8]:
            lead = ensure_lead_identity(lead)
            report["brands_processed"] += 1
            report["tracer_brands_processed"] += 1
            try:
                profile = extract_brand_profile(lead.get("website", ""), None, config.get("llm", {}))
            except Exception as exc:  # noqa: BLE001
                report["errors"] += 1
                profile = {"extraction_method": "blocked_or_error", "extraction_error": str(exc), "notes": str(exc)}
            merged = {**lead, **profile}
            merged = ensure_lead_identity(merged)
            structured_evidence = has_verified_amazon_evidence(merged)
            if structured_evidence:
                report["tracer_brands_with_verified_amazon_evidence"] += 1
            else:
                report["tracer_brands_without_evidence"] += 1
            contact_fields = _tracer_contact_fields(merged)
            if contact_fields.get("contact_method") != "unknown":
                report["tracer_contact_paths_found"] += 1
            if contact_fields.get("decision_maker_name"):
                report["tracer_decision_makers_found"] += 1
            scored = {**merged, **score_lead(merged), **contact_fields}
            classified = classify_scored_lead(scored, int(config["campaign"]["minimum_score_for_draft"]), allowed_categories=allowed_categories or None)
            final = {**scored, **classified}
            final["sender_name"] = config.get("sender", {}).get("name", "Zaigham Ali")
            final["sender_offer"] = config.get("sender", {}).get("offer", "")
            final["status"] = final.get("status") or "needs_enrichment"
            final["send_status"] = final.get("send_status") or "not_eligible"
            final["review_status"] = final.get("review_status") or "needs_enrichment"
            final["lead_id"] = final.get("lead_id") or final.get("id") or ""
            final["id"] = final.get("id") or final["lead_id"]
            final["lead_id"] = final.get("lead_id") or final["id"]
            if structured_evidence:
                evidence_urls = merged.get("amazon_evidence_urls") or merged.get("amazon_links") or []
                evidence_url = str((evidence_urls or [merged.get("website", "")])[0] or "")
                final["draft_preview_subject"] = f"Quick idea for {final.get('brand_name') or final.get('company_name')}'s Amazon growth"
                final["draft_preview_body"] = _tracer_draft_body(final, evidence_url)
                final["draft_subject"] = final["draft_preview_subject"]
                final["draft_body"] = final["draft_preview_body"]
                final["status"] = "draft_preview"
                final["review_status"] = "previewed"
                final["send_status"] = "draft_preview"
                report["tracer_drafts_generated"] += 1
            else:
                final["status"] = "needs_enrichment"
                final["review_status"] = "needs_enrichment"
                final["send_status"] = "not_eligible"
                report["tracer_drafts_blocked_due_missing_evidence"] += 1
            pending_updates.append(final)
            report["llm_providers_used"].append(str(final.get("llm_provider_used") or ""))
            attempted = final.get("llm_attempted_providers") or []
            if isinstance(attempted, list):
                for item in attempted:
                    provider_name = item.get("provider") if isinstance(item, dict) else str(item)
                    if provider_name:
                        report["tracer_llm_attempted_providers"].append(str(provider_name))
            _append_jsonl(
                tracer_jsonl,
                {
                    "lead_id": final.get("lead_id"),
                    "brand_name": final.get("brand_name"),
                    "website": final.get("website"),
                    "status": final.get("status"),
                    "review_status": final.get("review_status"),
                    "send_status": final.get("send_status"),
                    "score": final.get("score"),
                    "tier": final.get("tier"),
                    "extraction_method": final.get("extraction_method"),
                    "llm_provider_used": final.get("llm_provider_used"),
                    "llm_model_used": final.get("llm_model_used"),
                    "llm_attempted_providers": final.get("llm_attempted_providers", []),
                    "structured_amazon_evidence": structured_evidence,
                    "amazon_evidence_urls": final.get("amazon_evidence_urls", []),
                    "contact_method": contact_fields.get("contact_method"),
                    "contact_url": contact_fields.get("contact_url"),
                    "contact_email": contact_fields.get("contact_email"),
                    "decision_maker_name": contact_fields.get("decision_maker_name"),
                    "decision_maker_title": contact_fields.get("decision_maker_title"),
                },
            )
            report["manual_review_table"].append(
                {
                    "brand_name": final.get("brand_name", ""),
                    "evidence_accuracy": 5 if structured_evidence else 2,
                    "contact_usefulness": 4 if contact_fields.get("contact_method") != "unknown" else 1,
                    "draft_quality": 4 if structured_evidence else 1,
                    "sendable": "yes" if structured_evidence and final.get("contact_method") != "unknown" else "no",
                }
            )
            report["tracer_results"].append(
                {
                    "brand_name": final.get("brand_name", ""),
                    "amazon_evidence_urls": final.get("amazon_evidence_urls", []),
                    "contact_url": contact_fields.get("contact_url"),
                    "decision_maker_name": contact_fields.get("decision_maker_name"),
                    "draft_preview_subject": final.get("draft_preview_subject", ""),
                }
            )
            storage.upsert_lead(final, tab="Lead Queue")
            storage.record_outreach_event(
                {
                    "lead_id": final["lead_id"],
                    "event_type": "tracer_review",
                    "subject": final.get("draft_preview_subject", ""),
                    "body": final.get("draft_preview_body", ""),
                    "metadata": {
                        "structured_amazon_evidence": structured_evidence,
                        "llm_attempted_providers": final.get("llm_attempted_providers", []),
                    },
                },
            )
        storage.append_daily_report(
            {
                "report_date": datetime.now(timezone.utc).date().isoformat(),
                "campaign": "Amazon Lead Agent",
                "discovery_count": report["brands_processed"],
                "enrichment_count": report["brands_processed"],
                "scoring_count": report["brands_processed"],
                "approved_count": 0,
                "rejected_count": 0,
                "draft_count": report["tracer_drafts_generated"],
                "contact_form_queue_count": 0,
                "extraction_fallback_count": 0,
                "errors": report["errors"],
                "notes": json.dumps({"mode": "tracer", "drafts_generated": report["tracer_drafts_generated"]}, ensure_ascii=False),
            }
        )
        _write_tracer_summary(tracer_summary, report)
        try:
            storage.commit()
            report["storage_flush_status"] = "ok"
        except Exception as exc:  # noqa: BLE001
            report["errors"] += 1
            report["storage_flush_status"] = f"failed: {exc}"
            LOGGER.warning("tracer flush failed: %s", exc)
        report_path = write_campaign_report(db_path, summary=report)
        report["campaign_report_path"] = report_path["path"]
        report["llm_providers_used"] = sorted({provider for provider in report["llm_providers_used"] if provider})
        report["llm_attempted_providers"] = sorted({provider for provider in report["tracer_llm_attempted_providers"] if provider})
        report["drafts_generated"] = report["tracer_drafts_generated"]
        report["drafts_blocked_due_missing_evidence"] = report["tracer_drafts_blocked_due_missing_evidence"]
        report["brands_with_verified_amazon_evidence"] = report["tracer_brands_with_verified_amazon_evidence"]
        report["brands_without_evidence"] = report["tracer_brands_without_evidence"]
        report["contact_paths_found"] = report["tracer_contact_paths_found"]
        report["decision_makers_found"] = report["tracer_decision_makers_found"]
        report["tracer_success"] = (
            report["tracer_brands_with_verified_amazon_evidence"] >= 3
            and report["tracer_brands_without_evidence"] >= 0
            and report["tracer_contact_paths_found"] >= 2
            and report["tracer_drafts_generated"] >= 2
            and report["errors"] == 0
        )
        return report
    finally:
        storage.close()


def run_campaign(config: dict[str, Any], db_path: Path, mode: str = "full", dry_run: bool = False) -> dict[str, Any]:
    if mode == "tracer":
        return run_tracer_bullet(config, db_path, dry_run=dry_run)
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
        "llm_attempted_providers": [],
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
                attempted = lead.get("llm_attempted_providers") or []
                if provider:
                    report["llm_provider_counts"][provider] = report["llm_provider_counts"].get(provider, 0) + 1
                if model:
                    report["llm_model_counts"][model] = report["llm_model_counts"].get(model, 0) + 1
                if method:
                    report["extraction_method_counts"][method] = report["extraction_method_counts"].get(method, 0) + 1
                if isinstance(attempted, list):
                    for item in attempted:
                        provider_name = item.get("provider") if isinstance(item, dict) else str(item)
                        if provider_name:
                            report["llm_attempted_providers"].append(str(provider_name))

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
        report["llm_attempted_providers"] = sorted({provider for provider in report.get("llm_attempted_providers", []) if provider})
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
                "llm_attempted_providers": report["llm_attempted_providers"],
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
                    "llm_attempted_providers": report["llm_attempted_providers"],
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
