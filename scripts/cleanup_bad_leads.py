from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
from urllib.parse import urlparse

from amazon_lead_agent.tools.search import clean_search_result_url
from amazon_lead_agent.tools.sqlite_store import get_connection, init_db, upsert_lead


CONTENT_DOMAIN_BLOCKLIST = {
    "popsugar.com",
    "glamour.com",
    "womenshealthmag.com",
    "headtopics.com",
    "buzzfeed.com",
    "instyle.com",
    "marieclaire.com",
    "people.com",
    "today.com",
    "goodhousekeeping.com",
}

LISTICLE_TITLE_KEYWORDS = ("best", "top", "award winners", "list", "review")


def _normalize_path(path: str) -> str:
    return Path(path or "data/leads.db").as_posix()


def _lead_domains(lead: sqlite3.Row) -> list[str]:
    lead = dict(lead)
    values: list[str] = []
    for key in ("website", "primary_source_url", "contact_page_url", "decision_maker_source_url"):
        value = str(lead.get(key) or "").strip()
        if value:
            values.append(value)
    try:
        source_urls = json.loads(lead.get("source_urls_json") or "[]")
        if isinstance(source_urls, list):
            values.extend([str(item) for item in source_urls if item])
    except json.JSONDecodeError:
        pass
    domains: list[str] = []
    for value in values:
        domains.append((urlparse(value).netloc or "").lower())
    return domains


def _is_bing_redirect(value: str) -> bool:
    lowered = (value or "").lower()
    return "bing.com/ck/a" in lowered or "bing.com/ck/" in lowered or "bing.com/aclick" in lowered


def _classify_lead(lead: sqlite3.Row) -> dict[str, str] | None:
    lead = dict(lead)
    website = str(lead["website"] or "").strip()
    title = str(lead["company_name"] or lead["brand_name"] or "").lower()
    domains = _lead_domains(lead)

    if website and _is_bing_redirect(website):
        return {"status": "rejected", "review_status": "rejected", "send_status": "not_eligible", "reason": "bing redirect website"}
    if any(domain in CONTENT_DOMAIN_BLOCKLIST for domain in domains):
        return {"status": "rejected", "review_status": "rejected", "send_status": "not_eligible", "reason": "content/listicle domain"}
    if any(keyword in title for keyword in LISTICLE_TITLE_KEYWORDS) and not any(domain and domain not in CONTENT_DOMAIN_BLOCKLIST for domain in domains):
        return {"status": "rejected", "review_status": "rejected", "send_status": "not_eligible", "reason": "listicle title"}

    extraction_method = str(lead["extraction_method"] or "").strip().lower()
    score = int(lead["score"] or 0)
    if extraction_method == "blocked_or_error" and score >= 75:
        has_contact = bool((lead.get("public_emails_json") or "[]") != "[]" or (lead.get("contact_page_url") or ""))
        has_amazon = bool(lead["amazon_backlink_found"])
        if not (has_contact or has_amazon):
            return {"status": "needs_enrichment", "review_status": "needs_enrichment", "send_status": "not_eligible", "reason": "blocked_or_error high score without verified signals"}
    return None


def find_cleanup_actions(conn: sqlite3.Connection) -> list[dict[str, str]]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM leads").fetchall()
    actions: list[dict[str, str]] = []
    for lead in rows:
        action = _classify_lead(lead)
        if action:
            actions.append(
                {
                    "id": lead["id"],
                    "company_name": lead["company_name"] or "",
                    "current_status": lead["status"] or "",
                    "current_score": str(lead["score"] or 0),
                    **action,
                }
            )
    return actions


def apply_cleanup(conn: sqlite3.Connection, actions: list[dict[str, str]]) -> int:
    updated = 0
    for action in actions:
        row = conn.execute("SELECT * FROM leads WHERE id = ?", (action["id"],)).fetchone()
        if not row:
            continue
        payload = dict(row)
        payload.update(
            {
                "status": action["status"],
                "review_status": action["review_status"],
                "send_status": action["send_status"],
                "notes": (payload.get("notes") or "") + f" [cleanup: {action['reason']}]",
            }
        )
        upsert_lead(conn, payload)
        updated += 1
    conn.commit()
    return updated


def main() -> int:
    parser = argparse.ArgumentParser(description="Preview or apply cleanup for bad lead rows.")
    parser.add_argument("--db", default="data/leads.db", help="Path to the SQLite database.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Print what would be changed without writing.")
    mode.add_argument("--apply", action="store_true", help="Apply cleanup changes.")
    args = parser.parse_args()

    db_path = Path(_normalize_path(args.db))
    if not db_path.exists():
        print(f"Database not found: {db_path}")
        return 1

    conn = get_connection(db_path)
    try:
        actions = find_cleanup_actions(conn)
        if not actions:
            print("No cleanup candidates found.")
            return 0
        if args.apply:
            updated = apply_cleanup(conn, actions)
            print(f"Applied cleanup to {updated} leads.")
            return 0
        print(json.dumps(actions, indent=2, ensure_ascii=False))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
