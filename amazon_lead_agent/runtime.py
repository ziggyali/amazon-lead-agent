from __future__ import annotations

from datetime import datetime, timezone
import logging
import json
from pathlib import Path
import sqlite3
from typing import Any

from amazon_lead_agent.agents.discovery_agent import run_discovery
from amazon_lead_agent.agents.extraction_agent import run_extraction
from amazon_lead_agent.agents.outreach_agent import run_outreach
from amazon_lead_agent.agents.scoring_agent import run_scoring
from amazon_lead_agent.reporting import write_campaign_report
from amazon_lead_agent.tools.google_sheets import append_daily_report, append_or_update_lead, append_outreach_log


LOGGER = logging.getLogger(__name__)


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


def _sheet_error_status(exc: Exception) -> int | None:
    resp = getattr(exc, "resp", None)
    status = getattr(resp, "status", None) or getattr(exc, "status_code", None)
    try:
        return int(status) if status is not None else None
    except Exception:  # noqa: BLE001
        return None


def _is_fatal_sheet_error(exc: Exception) -> bool:
    status = _sheet_error_status(exc)
    if status in {401, 403}:
        return True
    lowered = str(exc).lower()
    return any(token in lowered for token in ("unauthorized", "forbidden", "invalid_grant", "invalid_client"))


def _append_failed_sheet_row(report: dict[str, Any], context: dict[str, Any]) -> None:
    failed = report.setdefault("failed_sheet_rows", [])
    if len(failed) < 50:
        failed.append(context)


def _safe_mirror_call(report: dict[str, Any], *, tab: str, lead_id: str = "", action: str = "", payload: dict | None = None, writer=None) -> None:
    if not writer:
        return
    try:
        writer()
    except Exception as exc:  # noqa: BLE001
        status = _sheet_error_status(exc)
        context = {
            "tab": tab,
            "lead_id": lead_id,
            "action": action,
            "status": status,
            "error": str(exc),
        }
        if payload is not None:
            context["fields"] = list(payload.keys())
        LOGGER.warning("sheet mirror failed: %s", context)
        report["sheet_mirror_error_count"] = int(report.get("sheet_mirror_error_count", 0)) + 1
        _append_failed_sheet_row(report, context)
        if _is_fatal_sheet_error(exc):
            raise


def _count_leads_by_field(db_path: Path, field: str) -> dict[str, int]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            f"""
            SELECT COALESCE(NULLIF({field}, ''), '') AS value, COUNT(*) AS count
            FROM leads
            GROUP BY value
            """
        ).fetchall()
        return {str(row["value"]): int(row["count"]) for row in rows if row["value"] is not None}
    finally:
        conn.close()


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
        "sheet_mirror_status": "disabled" if not sheet_id else "enabled",
        "top_5_leads": [],
    }

    fatal_error: Exception | None = None
    try:
        if mode in {"full", "discover"}:
            discovery_result = run_discovery(config, db_path)
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
            for lead in discovered:
                _safe_mirror_call(
                    report,
                    tab="Lead Queue",
                    lead_id=str(lead.get("id", "")),
                    action="lead",
                    payload=lead,
                    writer=lambda lead=lead: _mirror_lead(sheet_id, "Lead Queue", lead),
                )
                _safe_mirror_call(
                    report,
                    tab="Outreach Log",
                    lead_id=str(lead.get("id", "")),
                    action="discovered",
                    payload={"status": lead.get("status", "")},
                    writer=lambda lead=lead: _mirror_outreach(sheet_id, {"lead_id": lead["id"], "event_type": "discovered", "metadata": {"status": lead.get("status", "")}}),
                )

        if mode in {"full", "enrich"}:
            enriched = run_extraction(config, db_path)
            report["enriched_count"] = len(enriched)
            for lead in enriched:
                _safe_mirror_call(
                    report,
                    tab="Lead Queue",
                    lead_id=str(lead.get("id", "")),
                    action="lead",
                    payload=lead,
                    writer=lambda lead=lead: _mirror_lead(sheet_id, "Lead Queue", lead),
                )
                if lead.get("extraction_fallback"):
                    report["extraction_fallback_count"] += 1
                    _safe_mirror_call(
                        report,
                        tab="Outreach Log",
                        lead_id=str(lead.get("id", "")),
                        action="extraction_fallback",
                        payload={"method": lead.get("extraction_method", "")},
                        writer=lambda lead=lead: _mirror_outreach(sheet_id, {"lead_id": lead.get("id", ""), "event_type": "extraction_fallback", "metadata": {"method": lead.get("extraction_method", "")}}),
                    )
                if lead.get("status") in {"extraction_error", "blocked_or_error"}:
                    report["errors"] += 1
                    _safe_mirror_call(
                        report,
                        tab="Outreach Log",
                        lead_id=str(lead.get("id", "")),
                        action="error",
                        payload={"status": lead.get("status", "")},
                        writer=lambda lead=lead: _mirror_outreach(sheet_id, {"lead_id": lead.get("id", ""), "event_type": "error", "metadata": {"status": lead.get("status", "")}}),
                    )
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
                    _safe_mirror_call(report, tab="Approved Leads", lead_id=str(lead.get("id", "")), action="lead", payload=lead, writer=lambda lead=lead: _mirror_lead(sheet_id, "Approved Leads", lead))
                elif status == "contact_form_queue":
                    report["contact_form_queue_count"] += 1
                    _safe_mirror_call(report, tab="Contact Form Queue", lead_id=str(lead.get("id", "")), action="lead", payload=lead, writer=lambda lead=lead: _mirror_lead(sheet_id, "Contact Form Queue", lead))
                elif status == "rejected":
                    report["rejected_count"] += 1
                    _safe_mirror_call(report, tab="Rejected Leads", lead_id=str(lead.get("id", "")), action="lead", payload=lead, writer=lambda lead=lead: _mirror_lead(sheet_id, "Rejected Leads", lead))
                else:
                    _safe_mirror_call(report, tab="Lead Queue", lead_id=str(lead.get("id", "")), action="lead", payload=lead, writer=lambda lead=lead: _mirror_lead(sheet_id, "Lead Queue", lead))
                if lead.get("score", 0) >= min_score and status not in {"rejected"}:
                    _safe_mirror_call(
                        report,
                        tab="Outreach Log",
                        lead_id=str(lead.get("id", "")),
                        action=status or "scored",
                        payload={"score": lead.get("score", 0), "tier": lead.get("tier", "")},
                        writer=lambda lead=lead, status=status: _mirror_outreach(sheet_id, {"lead_id": lead.get("id", ""), "event_type": status or "scored", "metadata": {"score": lead.get("score", 0), "tier": lead.get("tier", "")}}),
                    )

        if mode in {"full", "draft"}:
            drafted = run_outreach(config, db_path, dry_run=dry_run)
            report["drafts_created"] = len([lead for lead in drafted if lead.get("draft_id")])

    except Exception as exc:  # noqa: BLE001
        fatal_error = exc
        report["run_error"] = str(exc)
        report["errors"] += 1
        LOGGER.exception("campaign run failed: %s", exc)
    finally:
        report["extraction_method_counts"] = _count_leads_by_field(db_path, "extraction_method")
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
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        report_path = write_campaign_report(db_path, summary=report)
        report["campaign_report_path"] = report_path["path"]
        report["top_5_leads"] = report_path["top_leads"]
        _safe_mirror_call(
            report,
            tab="Daily Reports",
            action="daily_report",
            payload={
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
                "rejected_content_domain_count": report["rejected_content_domains_count"],
                "cleaned_redirect_count": report["cleaned_redirect_count"],
                "rejected_redirect_count": report["rejected_redirect_count"],
                "extraction_method_counts": report["extraction_method_counts"],
                "llm_provider_counts": report["llm_provider_counts"],
                "llm_model_counts": report["llm_model_counts"],
                "notes": report["notes_json"],
            },
            writer=lambda: _mirror_daily_report(
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
                },
            ),
        )
    if fatal_error:
        raise fatal_error
    return report
