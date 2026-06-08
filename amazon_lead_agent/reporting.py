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


def write_campaign_report(db_path: str | Path, output_path: str | Path = "campaign_report.md") -> dict:
    conn = get_connection(db_path)
    try:
        report = _latest_report(conn)
        notes = _parse_notes(report.get("notes", ""))
        top = _top_leads(conn)
        body = [
            "# Campaign Report",
            "",
            f"- discovered_count: {report.get('discovery_count', 0)}",
            f"- enriched_count: {report.get('enrichment_count', 0)}",
            f"- scored_count: {report.get('scoring_count', 0)}",
            f"- approved_count: {report.get('approved_count', 0)}",
            f"- rejected_count: {report.get('rejected_count', 0)}",
            f"- drafts_created: {report.get('draft_count', 0)}",
            f"- contact_form_queue_count: {report.get('contact_form_queue_count', 0)}",
            f"- extraction_fallback_count: {report.get('extraction_fallback_count', 0)}",
            f"- errors: {report.get('errors', 0)}",
            f"- sheet_mirror_status: {notes.get('sheet_mirror_status', '')}",
            f"- search_provider_counts: {json.dumps(notes.get('search_provider_counts', {}), sort_keys=True)}",
            f"- search_blocked_query_counts: {json.dumps(notes.get('search_blocked_query_counts', {}), sort_keys=True)}",
            f"- search_rate_limited_query_counts: {json.dumps(notes.get('search_rate_limited_query_counts', {}), sort_keys=True)}",
            f"- rejected_content_domains_count: {notes.get('rejected_content_domains_count', 0)}",
            f"- rejected_listicle_domains_count: {notes.get('rejected_listicle_domains_count', 0)}",
            f"- llm_provider_counts: {json.dumps(notes.get('llm_provider_counts', {}), sort_keys=True)}",
            f"- llm_model_counts: {json.dumps(notes.get('llm_model_counts', {}), sort_keys=True)}",
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
        conn.close()
