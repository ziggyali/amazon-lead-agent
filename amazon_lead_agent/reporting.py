from __future__ import annotations

from pathlib import Path
import json
import sqlite3

from amazon_lead_agent.tools.sqlite_store import get_connection


def _latest_report(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        """
        SELECT *
        FROM daily_reports
        ORDER BY date(report_date) DESC, datetime(updated_at) DESC
        LIMIT 1
        """
    ).fetchone()
    return dict(row) if row else {}


def _top_leads(conn: sqlite3.Connection, limit: int = 5) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, company_name, website, score, tier, extraction_method, status, send_status
        FROM leads
        ORDER BY score DESC, datetime(updated_at) DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def _parse_notes(notes: str | None) -> dict:
    if not notes:
        return {}
    try:
        parsed = json.loads(notes)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _coerce_summary(summary: dict | None) -> dict:
    return summary or {}


def write_campaign_report(db_path: str | Path | None, output_path: str | Path = "campaign_report.md", summary: dict | None = None) -> dict:
    conn = get_connection(db_path) if db_path and Path(db_path).exists() else None
    try:
        report = _coerce_summary(summary)
        if not report and conn is not None:
            report = _latest_report(conn)
        notes = _parse_notes(report.get("notes_json") or report.get("notes", ""))
        top = report.get("top_5_leads") or (_top_leads(conn) if conn is not None else [])
        body = [
            "# Campaign Report",
            "",
            f"- discovered_count: {report.get('discovered_count', report.get('discovery_count', 0))}",
            f"- enriched_count: {report.get('enriched_count', report.get('enrichment_count', 0))}",
            f"- scored_count: {report.get('scored_count', report.get('scoring_count', 0))}",
            f"- approved_count: {report.get('approved_count', 0)}",
            f"- rejected_count: {report.get('rejected_count', 0)}",
            f"- drafts_created: {report.get('drafts_created', report.get('draft_count', 0))}",
            f"- contact_form_queue_count: {report.get('contact_form_queue_count', 0)}",
            f"- extraction_fallback_count: {report.get('extraction_fallback_count', 0)}",
            f"- errors: {report.get('errors', 0)}",
            f"- sheet_mirror_status: {report.get('sheet_mirror_status', notes.get('sheet_mirror_status', ''))}",
            f"- sheet_mirror_error_count: {report.get('sheet_mirror_error_count', notes.get('sheet_mirror_error_count', 0))}",
            f"- sheet_read_error_count: {report.get('sheet_read_error_count', notes.get('sheet_read_error_count', 0))}",
            f"- sheet_read_retry_count: {report.get('sheet_read_retry_count', notes.get('sheet_read_retry_count', 0))}",
            f"- sheet_connection_error_count: {report.get('sheet_connection_error_count', notes.get('sheet_connection_error_count', 0))}",
            f"- failed_sheet_rows: {json.dumps(report.get('failed_sheet_rows', notes.get('failed_sheet_rows', [])), ensure_ascii=False)}",
            f"- failed_sheet_reads: {json.dumps(report.get('failed_sheet_reads', notes.get('failed_sheet_reads', [])), ensure_ascii=False)}",
            f"- sheet_flush_errors: {json.dumps(report.get('sheet_flush_errors', notes.get('sheet_flush_errors', [])), ensure_ascii=False)}",
            f"- llm_attempted_providers: {json.dumps(report.get('llm_attempted_providers', notes.get('llm_attempted_providers', [])), sort_keys=True)}",
            f"- search_provider_counts: {json.dumps(report.get('search_provider_counts', notes.get('search_provider_counts', {})), sort_keys=True)}",
            f"- provider_blocked_counts: {json.dumps(report.get('provider_blocked_counts', notes.get('provider_blocked_counts', {})), sort_keys=True)}",
            f"- queries_attempted_by_provider: {json.dumps(report.get('queries_attempted_by_provider', notes.get('queries_attempted_by_provider', {})), sort_keys=True)}",
            f"- query_budget_used: {report.get('query_budget_used', notes.get('query_budget_used', 0))}",
            f"- query_budget_remaining: {report.get('query_budget_remaining', notes.get('query_budget_remaining', 0))}",
            f"- discovery_runtime_seconds: {report.get('discovery_runtime_seconds', notes.get('discovery_runtime_seconds', 0.0))}",
            f"- stopped_reason: {report.get('stopped_reason', notes.get('stopped_reason', ''))}",
            f"- seed_lines_processed: {report.get('seed_lines_processed', notes.get('seed_lines_processed', 0))}",
            f"- seed_pages_fetched: {report.get('seed_pages_fetched', notes.get('seed_pages_fetched', 0))}",
            f"- seed_brand_domains_extracted: {report.get('seed_brand_domains_extracted', notes.get('seed_brand_domains_extracted', 0))}",
            f"- direct_seed_candidates: {report.get('direct_seed_candidates', notes.get('direct_seed_candidates', 0))}",
            f"- seed_candidates_accepted: {report.get('seed_candidates_accepted', notes.get('seed_candidates_accepted', 0))}",
            f"- seed_candidates_rejected: {report.get('seed_candidates_rejected', notes.get('seed_candidates_rejected', 0))}",
            f"- rejected_content_domain_count: {report.get('rejected_content_domains_count', notes.get('rejected_content_domain_count', 0))}",
            f"- hard_rejected_junk_count: {report.get('hard_rejected_junk_count', notes.get('hard_rejected_junk_count', 0))}",
            f"- soft_pass_needs_enrichment_count: {report.get('soft_pass_needs_enrichment_count', notes.get('soft_pass_needs_enrichment_count', 0))}",
            f"- rejected_likely_brand_filter_count: {report.get('rejected_likely_brand_filter_count', notes.get('rejected_likely_brand_filter_count', 0))}",
            f"- rejected_due_to_no_amazon_evidence_count: {report.get('rejected_due_to_no_amazon_evidence_count', notes.get('rejected_due_to_no_amazon_evidence_count', 0))}",
            f"- discovered_count_by_category: {json.dumps(report.get('discovered_count_by_category', notes.get('discovered_count_by_category', {})), sort_keys=True)}",
            f"- cleaned_redirect_count: {report.get('cleaned_redirect_count', notes.get('cleaned_redirect_count', 0))}",
            f"- rejected_redirect_count: {report.get('rejected_redirect_count', notes.get('rejected_redirect_count', 0))}",
            f"- discovered_persisted_count: {report.get('discovered_persisted_count', notes.get('discovered_persisted_count', 0))}",
            f"- discovered_persist_failed_count: {report.get('discovered_persist_failed_count', notes.get('discovered_persist_failed_count', 0))}",
            f"- lead_queue_rows_queued: {report.get('lead_queue_rows_queued', notes.get('lead_queue_rows_queued', 0))}",
            f"- lead_queue_rows_attempted: {report.get('lead_queue_rows_attempted', notes.get('lead_queue_rows_attempted', 0))}",
            f"- lead_queue_rows_written: {report.get('lead_queue_rows_written', notes.get('lead_queue_rows_written', 0))}",
            f"- lead_queue_rows_failed: {report.get('lead_queue_rows_failed', notes.get('lead_queue_rows_failed', 0))}",
            f"- lead_queue_verified_count: {report.get('lead_queue_verified_count', notes.get('lead_queue_verified_count', 0))}",
            f"- lead_queue_missing_after_write: {report.get('lead_queue_missing_after_write', notes.get('lead_queue_missing_after_write', 0))}",
            f"- lead_queue_verification_status: {report.get('lead_queue_verification_status', notes.get('lead_queue_verification_status', ''))}",
            f"- dedupe_cache_unavailable: {report.get('dedupe_cache_unavailable', notes.get('dedupe_cache_unavailable', False))}",
            f"- storage_flush_status: {report.get('storage_flush_status', notes.get('storage_flush_status', ''))}",
            f"- storage_mode_used: {report.get('storage_mode_used', notes.get('storage_mode_used', ''))}",
            f"- extraction_method_counts: {json.dumps(report.get('extraction_method_counts', notes.get('extraction_method_counts', {})), sort_keys=True)}",
            f"- llm_provider_counts: {json.dumps(report.get('llm_provider_counts', notes.get('llm_provider_counts', {})), sort_keys=True)}",
            f"- llm_model_counts: {json.dumps(report.get('llm_model_counts', notes.get('llm_model_counts', {})), sort_keys=True)}",
            f"- tracer_brands_processed: {report.get('tracer_brands_processed', notes.get('tracer_brands_processed', report.get('brands_processed', 0)))}",
            f"- tracer_brands_with_verified_amazon_evidence: {report.get('tracer_brands_with_verified_amazon_evidence', notes.get('tracer_brands_with_verified_amazon_evidence', 0))}",
            f"- tracer_brands_without_evidence: {report.get('tracer_brands_without_evidence', notes.get('tracer_brands_without_evidence', 0))}",
            f"- tracer_contact_paths_found: {report.get('tracer_contact_paths_found', notes.get('tracer_contact_paths_found', 0))}",
            f"- tracer_decision_makers_found: {report.get('tracer_decision_makers_found', notes.get('tracer_decision_makers_found', 0))}",
            f"- tracer_drafts_generated: {report.get('tracer_drafts_generated', notes.get('tracer_drafts_generated', 0))}",
            f"- tracer_drafts_blocked_due_missing_evidence: {report.get('tracer_drafts_blocked_due_missing_evidence', notes.get('tracer_drafts_blocked_due_missing_evidence', 0))}",
            "",
            "## Top 5 Leads",
            "",
        ]
        for lead in top:
            body.append(
                f"- {lead.get('company_name', '')} | score={lead.get('score', 0)} | tier={lead.get('tier', '')} | status={lead.get('status', '')} | method={lead.get('extraction_method', '')}"
            )
        rendered = "\n".join(body) + "\n"
        Path(output_path).write_text(rendered, encoding="utf-8")
        return {"report": report, "top_leads": top, "path": str(output_path)}
    finally:
        if conn is not None:
            conn.close()
