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
            f"- failed_sheet_rows: {json.dumps(report.get('failed_sheet_rows', notes.get('failed_sheet_rows', [])), ensure_ascii=False)}",
            f"- sheet_flush_errors: {json.dumps(report.get('sheet_flush_errors', notes.get('sheet_flush_errors', [])), ensure_ascii=False)}",
            f"- search_provider_counts: {json.dumps(report.get('search_provider_counts', notes.get('search_provider_counts', {})), sort_keys=True)}",
            f"- provider_blocked_counts: {json.dumps(report.get('provider_blocked_counts', notes.get('provider_blocked_counts', {})), sort_keys=True)}",
            f"- queries_attempted_by_provider: {json.dumps(report.get('queries_attempted_by_provider', notes.get('queries_attempted_by_provider', {})), sort_keys=True)}",
            f"- rejected_content_domain_count: {report.get('rejected_content_domains_count', notes.get('rejected_content_domain_count', 0))}",
            f"- hard_rejected_junk_count: {report.get('hard_rejected_junk_count', notes.get('hard_rejected_junk_count', 0))}",
            f"- soft_pass_needs_enrichment_count: {report.get('soft_pass_needs_enrichment_count', notes.get('soft_pass_needs_enrichment_count', 0))}",
            f"- rejected_due_to_no_amazon_evidence_count: {report.get('rejected_due_to_no_amazon_evidence_count', notes.get('rejected_due_to_no_amazon_evidence_count', 0))}",
            f"- discovered_count_by_category: {json.dumps(report.get('discovered_count_by_category', notes.get('discovered_count_by_category', {})), sort_keys=True)}",
            f"- cleaned_redirect_count: {report.get('cleaned_redirect_count', notes.get('cleaned_redirect_count', 0))}",
            f"- rejected_redirect_count: {report.get('rejected_redirect_count', notes.get('rejected_redirect_count', 0))}",
            f"- extraction_method_counts: {json.dumps(report.get('extraction_method_counts', notes.get('extraction_method_counts', {})), sort_keys=True)}",
            f"- llm_provider_counts: {json.dumps(report.get('llm_provider_counts', notes.get('llm_provider_counts', {})), sort_keys=True)}",
            f"- llm_model_counts: {json.dumps(report.get('llm_model_counts', notes.get('llm_model_counts', {})), sort_keys=True)}",
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
