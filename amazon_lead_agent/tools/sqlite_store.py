from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
import json
import sqlite3

from amazon_lead_agent.normalization import make_lead_id, normalize_company_name, normalize_domain


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_connection(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(path: str | Path) -> None:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS leads (
                id TEXT PRIMARY KEY,
                company_name TEXT,
                brand_name TEXT,
                normalized_company_name TEXT,
                website TEXT,
                normalized_domain TEXT,
                category TEXT,
                country TEXT,
                description TEXT,
                amazon_links_json TEXT DEFAULT '[]',
                amazon_evidence_summary TEXT,
                amazon_backlink_found INTEGER DEFAULT 0,
                founder_or_executive_names_json TEXT DEFAULT '[]',
                ecommerce_or_marketplace_people_json TEXT DEFAULT '[]',
                public_emails_json TEXT DEFAULT '[]',
                contact_page_url TEXT,
                decision_maker_source_url TEXT,
                pain_points_json TEXT DEFAULT '[]',
                confidence REAL DEFAULT 0,
                score INTEGER DEFAULT 0,
                tier TEXT DEFAULT 'Unscored',
                source_quotes_json TEXT DEFAULT '[]',
                source_urls_json TEXT DEFAULT '[]',
                primary_source_url TEXT,
                contact_path_exists INTEGER DEFAULT 0,
                has_email INTEGER DEFAULT 0,
                status TEXT DEFAULT 'new',
                draft_id TEXT,
                drafted INTEGER DEFAULT 0,
                raw_json TEXT DEFAULT '{}',
                notes TEXT,
                created_at TEXT,
                updated_at TEXT,
                last_enriched_at TEXT,
                last_scored_at TEXT
            );

            CREATE TABLE IF NOT EXISTS source_urls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lead_id TEXT NOT NULL,
                url TEXT NOT NULL,
                kind TEXT DEFAULT 'source',
                created_at TEXT,
                UNIQUE(lead_id, url),
                FOREIGN KEY(lead_id) REFERENCES leads(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS outreach_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lead_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                subject TEXT,
                body TEXT,
                draft_id TEXT,
                metadata_json TEXT DEFAULT '{}',
                created_at TEXT,
                FOREIGN KEY(lead_id) REFERENCES leads(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS daily_reports (
                report_date TEXT PRIMARY KEY,
                discovery_count INTEGER DEFAULT 0,
                enrichment_count INTEGER DEFAULT 0,
                scoring_count INTEGER DEFAULT 0,
                draft_count INTEGER DEFAULT 0,
                notes TEXT,
                created_at TEXT,
                updated_at TEXT
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


def _json_list(value: object) -> str:
    if isinstance(value, str):
        try:
            json.loads(value)
            return value
        except json.JSONDecodeError:
            return json.dumps([value], ensure_ascii=False)
    if value is None:
        return "[]"
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, tuple | set):
        return json.dumps(list(value), ensure_ascii=False)
    return json.dumps([value], ensure_ascii=False)


def _lead_row(lead: dict) -> dict:
    website = lead.get("website") or ""
    company_name = lead.get("company_name") or lead.get("brand_name") or website
    amazon_links = lead.get("amazon_links") or []
    source_urls = lead.get("source_urls") or []
    row = {
        "id": lead.get("id") or make_lead_id(company_name, website, (amazon_links or [None])[0]),
        "company_name": company_name,
        "brand_name": lead.get("brand_name") or company_name,
        "normalized_company_name": lead.get("normalized_company_name") or normalize_company_name(company_name),
        "website": website,
        "normalized_domain": lead.get("normalized_domain") or normalize_domain(website),
        "category": lead.get("category", ""),
        "country": lead.get("country", ""),
        "description": lead.get("description", ""),
        "amazon_links_json": _json_list(amazon_links),
        "amazon_evidence_summary": lead.get("amazon_evidence_summary", ""),
        "amazon_backlink_found": int(bool(lead.get("amazon_backlink_found"))),
        "founder_or_executive_names_json": _json_list(lead.get("founder_or_executive_names", [])),
        "ecommerce_or_marketplace_people_json": _json_list(lead.get("ecommerce_or_marketplace_people", [])),
        "public_emails_json": _json_list(lead.get("public_emails", [])),
        "contact_page_url": lead.get("contact_page_url", ""),
        "decision_maker_source_url": lead.get("decision_maker_source_url", ""),
        "pain_points_json": _json_list(lead.get("pain_points", [])),
        "confidence": float(lead.get("confidence") or 0),
        "score": int(lead.get("score") or 0),
        "tier": lead.get("tier", "Unscored"),
        "source_quotes_json": _json_list(lead.get("source_quotes", [])),
        "source_urls_json": _json_list(source_urls),
        "primary_source_url": lead.get("primary_source_url", source_urls[0] if source_urls else ""),
        "contact_path_exists": int(bool(lead.get("contact_page_url") or lead.get("public_emails"))),
        "has_email": int(bool(lead.get("public_emails"))),
        "status": lead.get("status", "new"),
        "draft_id": lead.get("draft_id", ""),
        "drafted": int(bool(lead.get("drafted"))),
        "raw_json": json.dumps(lead, ensure_ascii=False, sort_keys=True),
        "notes": lead.get("notes", ""),
        "created_at": lead.get("created_at", _utc_now()),
        "updated_at": _utc_now(),
        "last_enriched_at": lead.get("last_enriched_at", ""),
        "last_scored_at": lead.get("last_scored_at", ""),
    }
    return row


def upsert_lead(conn: sqlite3.Connection, lead: dict) -> str:
    row = _lead_row(lead)
    existing = conn.execute(
        "SELECT id FROM leads WHERE id = ? OR (normalized_domain != '' AND normalized_domain = ?)",
        (row["id"], row["normalized_domain"]),
    ).fetchone()
    if existing:
        row["id"] = existing["id"]
        assignments = ", ".join(f"{column}=excluded.{column}" for column in row.keys() if column != "id")
        columns = ", ".join(row.keys())
        placeholders = ", ".join("?" for _ in row)
        conn.execute(
            f"INSERT INTO leads ({columns}) VALUES ({placeholders}) ON CONFLICT(id) DO UPDATE SET {assignments}",
            tuple(row.values()),
        )
    else:
        columns = ", ".join(row.keys())
        placeholders = ", ".join("?" for _ in row)
        conn.execute(f"INSERT INTO leads ({columns}) VALUES ({placeholders})", tuple(row.values()))
    for url in json.loads(row["source_urls_json"]):
        if url:
            conn.execute(
                """
                INSERT OR IGNORE INTO source_urls (lead_id, url, kind, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (row["id"], url, "source", _utc_now()),
            )
    return row["id"]


def _row_to_dict(row: sqlite3.Row) -> dict:
    data = dict(row)
    for key in (
        "amazon_links_json",
        "founder_or_executive_names_json",
        "ecommerce_or_marketplace_people_json",
        "public_emails_json",
        "pain_points_json",
        "source_quotes_json",
        "source_urls_json",
    ):
        data[key[:-5] if key.endswith("_json") else key] = json.loads(data[key] or "[]")
    return data


def _select_leads(conn: sqlite3.Connection, query: str, params: tuple[object, ...]) -> list[dict]:
    rows = conn.execute(query, params).fetchall()
    return [_row_to_dict(row) for row in rows]


def get_leads_for_enrichment(conn: sqlite3.Connection, limit: int) -> list[dict]:
    return _select_leads(
        conn,
        """
        SELECT * FROM leads
        WHERE status IN ('new', 'discovered', 'extraction_error')
        ORDER BY datetime(created_at) ASC
        LIMIT ?
        """,
        (limit,),
    )


def get_leads_for_scoring(conn: sqlite3.Connection, limit: int) -> list[dict]:
    return _select_leads(
        conn,
        """
        SELECT * FROM leads
        WHERE status IN ('enriched', 'extraction_error', 'discovered', 'scoring_error')
        ORDER BY datetime(updated_at) ASC
        LIMIT ?
        """,
        (limit,),
    )


def get_leads_for_drafting(conn: sqlite3.Connection, min_score: int, limit: int) -> list[dict]:
    return _select_leads(
        conn,
        """
        SELECT * FROM leads
        WHERE drafted = 0
          AND score >= ?
          AND has_email = 1
          AND contact_path_exists = 1
          AND amazon_backlink_found = 1
          AND status IN ('scored', 'enriched')
        ORDER BY score DESC, datetime(updated_at) ASC
        LIMIT ?
        """,
        (min_score, limit),
    )


def mark_draft_created(conn: sqlite3.Connection, lead_id: str, draft_id: str) -> None:
    conn.execute(
        """
        UPDATE leads
        SET drafted = 1,
            draft_id = ?,
            status = 'drafted',
            updated_at = ? 
        WHERE id = ?
        """,
        (draft_id, _utc_now(), lead_id),
    )


def record_outreach_event(conn: sqlite3.Connection, event: dict) -> None:
    metadata = event.get("metadata", {})
    if not isinstance(metadata, str):
        metadata = json.dumps(metadata, ensure_ascii=False, sort_keys=True)
    created_at = event.get("created_at", _utc_now())
    conn.execute(
        """
        INSERT INTO outreach_events (lead_id, event_type, subject, body, draft_id, metadata_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event.get("lead_id", ""),
            event.get("event_type", ""),
            event.get("subject", ""),
            event.get("body", ""),
            event.get("draft_id", ""),
            metadata,
            created_at,
        ),
    )
    report_date = created_at[:10]
    column = {
        "discovered": "discovery_count",
        "enriched": "enrichment_count",
        "scored": "scoring_count",
        "draft_created": "draft_count",
    }.get(event.get("event_type"), None)
    conn.execute(
        """
        INSERT INTO daily_reports (report_date, discovery_count, enrichment_count, scoring_count, draft_count, created_at, updated_at)
        VALUES (?, 0, 0, 0, 0, ?, ?)
        ON CONFLICT(report_date) DO NOTHING
        """,
        (report_date, created_at, created_at),
    )
    if column:
        conn.execute(
            f"""
            UPDATE daily_reports
            SET {column} = {column} + 1,
                updated_at = ?
            WHERE report_date = ?
            """,
            (created_at, report_date),
        )
